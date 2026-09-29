"""品种身份领域服务。

提供从自由文本到稳定品种身份(SpeciesIdentity)的完整链路:

* 文本归一化与录入解析(命中规范名/已批准别名 -> 直接关联; 否则入未决队列并给出候选)
* 品种目录与版本化名称(名称更正只产生新版本, 历史版本不可变)
* 可审阅别名(提议/批准/驳回/撤回, 乐观锁保证并发下只有一次裁定生效)
* 旧文本候选映射、未决队列, 以及合并/拆分/撤回的决策流水
* 面向列表、周期分析、追溯的统一身份视图
"""

import json
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from difflib import SequenceMatcher
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import update

from . import models

# ---------------------------------------------------------------------------
# 文本归一化
# ---------------------------------------------------------------------------

_WHITESPACE_RE = re.compile(r"\s+", re.UNICODE)


def normalize_name(text: Optional[str]) -> str:
    """归一化品种名称: 全角转半角、去全部空白、忽略大小写。

    因此 "罗氏沼虾"、"罗氏 沼虾"、" 罗氏沼虾 " 与 (拉丁名场景下) 大小写差异
    都视为同一写法; 但字面不同的品系名不会被视作相同。
    """
    if text is None:
        return ""
    normalized = unicodedata.normalize("NFKC", str(text))
    normalized = normalized.replace("　", "")
    normalized = _WHITESPACE_RE.sub("", normalized)
    return normalized.casefold()


# ---------------------------------------------------------------------------
# 录入解析结果
# ---------------------------------------------------------------------------

@dataclass
class Candidate:
    identity_id: int
    identity_code: str
    canonical_name: str
    score: float
    matched_alias: Optional[str] = None


@dataclass
class SpeciesResolution:
    """录入一段品种文本后的解析结论。"""
    raw_text: str
    normalized_text: str
    status: str  # exact_canonical / exact_alias / legacy_mapped / pending / rejected
    identity: Optional[models.SpeciesIdentity] = None
    version: Optional[models.SpeciesNameVersion] = None
    legacy_text: Optional[models.LegacySpeciesText] = None
    candidates: list = field(default_factory=list)

    @property
    def matched(self) -> bool:
        return self.identity is not None


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------

def generate_identity_code(db) -> str:
    count = db.query(models.SpeciesIdentity).count()
    while True:
        candidate = f"SP-{count + 1:04d}"
        exists = db.query(models.SpeciesIdentity.id).filter(
            models.SpeciesIdentity.code == candidate
        ).first()
        if not exists:
            return candidate
        count += 1


def root_identity(db, identity_id: Optional[int]) -> Optional[models.SpeciesIdentity]:
    """沿合并链找到当前存续身份。"""
    if identity_id is None:
        return None
    identity = db.get(models.SpeciesIdentity, identity_id)
    seen = set()
    while identity is not None and identity.merged_into_id and identity.id not in seen:
        seen.add(identity.id)
        identity = db.get(models.SpeciesIdentity, identity.merged_into_id)
    return identity


def current_version(db, identity: models.SpeciesIdentity) -> Optional[models.SpeciesNameVersion]:
    if identity.current_version_id:
        return db.get(models.SpeciesNameVersion, identity.current_version_id)
    return identity.versions[-1] if identity.versions else None


def _approved_alias(db, normalized: str) -> Optional[models.SpeciesAlias]:
    return db.query(models.SpeciesAlias).filter(
        models.SpeciesAlias.normalized_text == normalized,
        models.SpeciesAlias.status == "approved",
    ).first()


def _record_decision(db, *, action, rationale, decided_by=None, legacy_text_id=None,
                     source_identity_id=None, target_identity_id=None, source_text=None):
    decision = models.SpeciesMappingDecision(
        action=action,
        rationale=rationale,
        decided_by=decided_by,
        legacy_text_id=legacy_text_id,
        source_identity_id=source_identity_id,
        target_identity_id=target_identity_id,
        source_text=source_text,
    )
    db.add(decision)
    return decision


# ---------------------------------------------------------------------------
# 品种目录
# ---------------------------------------------------------------------------

def create_identity(db, canonical_name: str, *, created_by=None, reason=None):
    """建立一个新品种身份: 生成稳定 code、第 1 个生效名称版本, 并把规范名登记为已批准别名。"""
    name = (canonical_name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="品种名称不能为空")
    normalized = normalize_name(name)
    existing = _approved_alias(db, normalized)
    if existing:
        owner = root_identity(db, existing.identity_id)
        raise HTTPException(
            status_code=409,
            detail=f"名称 '{name}' 已属于品种 {owner.code}(身份ID={owner.id});"
                   f"真正不同的品系请使用不同名称, 同义写法请走别名提议",
        )

    identity = models.SpeciesIdentity(code=generate_identity_code(db), status="active")
    db.add(identity)
    db.flush()

    version = models.SpeciesNameVersion(
        identity_id=identity.id, version_no=1, canonical_name=name,
        status="effective", change_reason=reason or "建立品种目录", created_by=created_by,
    )
    db.add(version)
    db.flush()
    identity.current_version_id = version.id

    db.add(models.SpeciesAlias(
        identity_id=identity.id, alias_text=name, normalized_text=normalized,
        status="approved", proposed_by=created_by, reviewed_by=created_by,
        reason="规范名(建目)",
    ))
    db.flush()
    return identity


def correct_name(db, identity_id: int, new_name: str, *, reason, changed_by=None):
    """名称更正: 旧版本置为 superseded(不可变保留), 产生唯一的新生效版本。

    只改变后续展示规则; 已保存的批次/投苗/已签署分析仍引用旧版本, 按原口径重现。
    """
    identity = db.get(models.SpeciesIdentity, identity_id)
    identity = root_identity(db, identity_id) if identity is None else identity
    if identity is None or identity.status != "active":
        raise HTTPException(status_code=404, detail="品种身份不存在或已合并")

    new_name = (new_name or "").strip()
    if not new_name:
        raise HTTPException(status_code=400, detail="新名称不能为空")
    normalized = normalize_name(new_name)

    alias = _approved_alias(db, normalized)
    if alias and root_identity(db, alias.identity_id).id != identity.id:
        raise HTTPException(status_code=409, detail="该名称已被另一品种占用, 不能更正为同一名称")

    old_version = current_version(db, identity)
    if old_version and normalize_name(old_version.canonical_name) == normalized:
        raise HTTPException(status_code=400, detail="新名称与当前规范名一致, 无需更正")

    now = datetime.utcnow()
    if old_version is not None:
        old_version.status = "superseded"
        old_version.superseded_at = now

    next_no = (old_version.version_no + 1) if old_version else 1
    new_version = models.SpeciesNameVersion(
        identity_id=identity.id, version_no=next_no, canonical_name=new_name,
        status="effective", change_reason=reason, created_by=changed_by,
    )
    db.add(new_version)
    db.flush()
    identity.current_version_id = new_version.id

    # 新规范名同时登记为别名, 保证全局归一化查找可命中
    if not alias:
        db.add(models.SpeciesAlias(
            identity_id=identity.id, alias_text=new_name, normalized_text=normalized,
            status="approved", proposed_by=changed_by, reviewed_by=changed_by,
            reason=f"名称更正(v{next_no})",
        ))

    _record_decision(
        db, action="rename", rationale=reason or "名称更正", decided_by=changed_by,
        source_identity_id=identity.id, source_text=old_version.canonical_name if old_version else None,
        target_identity_id=identity.id,
    )
    db.flush()
    return identity


# ---------------------------------------------------------------------------
# 别名提议 / 裁定(乐观锁)
# ---------------------------------------------------------------------------

def propose_alias(db, identity_id: int, alias_text: str, *, proposed_by=None, reason=None):
    identity = root_identity(db, identity_id)
    if identity is None or identity.status != "active":
        raise HTTPException(status_code=404, detail="品种身份不存在或已合并")
    alias_text = (alias_text or "").strip()
    if not alias_text:
        raise HTTPException(status_code=400, detail="别名不能为空")
    normalized = normalize_name(alias_text)

    approved = _approved_alias(db, normalized)
    if approved:
        owner = root_identity(db, approved.identity_id)
        if owner.id == identity.id:
            raise HTTPException(status_code=400, detail="该写法已是本品种的有效别名")
        raise HTTPException(
            status_code=409,
            detail=f"该写法已属于品种(身份ID={owner.id}); 如确为同义请走显式合并, 系统不会自动合并不同品系",
        )

    pending = db.query(models.SpeciesAlias).filter(
        models.SpeciesAlias.normalized_text == normalized
    ).first()
    if pending:
        raise HTTPException(status_code=409, detail="该写法已有待裁定/已处理的别名记录")

    alias = models.SpeciesAlias(
        identity_id=identity.id, alias_text=alias_text, normalized_text=normalized,
        status="pending", proposed_by=proposed_by, reason=reason or "别名提议",
    )
    db.add(alias)
    db.flush()
    return alias


def adjudicate_alias(db, alias_id: int, action: str, *, reviewed_by=None,
                     reason=None, expected_lock_version: Optional[int] = None):
    """裁定别名(approve/reject/withdraw)。

    乐观锁: 仅当 lock_version 与调用方持有的一致时更新才生效, 并发裁定只有一个成功。
    """
    if action not in ("approve", "reject", "withdraw"):
        raise HTTPException(status_code=400, detail="不支持的别名裁定动作")

    alias = db.get(models.SpeciesAlias, alias_id)
    if alias is None:
        raise HTTPException(status_code=404, detail="别名不存在")
    if expected_lock_version is not None and alias.lock_version != expected_lock_version:
        raise HTTPException(status_code=409, detail="别名已被他人调整, 请刷新后重试(版本冲突)")
    if alias.status != "pending":
        raise HTTPException(status_code=409, detail=f"别名当前状态为 {alias.status}, 不能重复裁定")

    new_status = {"approve": "approved", "reject": "rejected", "withdraw": "withdrawn"}[action]

    if action == "approve":
        owner = _approved_alias(db, alias.normalized_text)
        if owner is not None and root_identity(db, owner.identity_id).id != root_identity(db, alias.identity_id).id:
            raise HTTPException(status_code=409, detail="该写法已被另一品种占用, 不能批准")

    result = db.execute(
        update(models.SpeciesAlias)
        .where(
            models.SpeciesAlias.id == alias_id,
            models.SpeciesAlias.lock_version == alias.lock_version,
            models.SpeciesAlias.status == "pending",
        )
        .values(
            status=new_status,
            lock_version=models.SpeciesAlias.lock_version + 1,
            reviewed_by=reviewed_by,
            reviewed_at=datetime.utcnow(),
            reason=(reason or alias.reason),
        )
    )
    if result.rowcount == 0:
        db.rollback()
        raise HTTPException(status_code=409, detail="并发裁定冲突, 只有一个版本可以生效")

    _record_decision(
        db, action=f"alias_{action}", rationale=reason or f"别名{action}",
        decided_by=reviewed_by, target_identity_id=alias.identity_id, source_text=alias.alias_text,
    )
    db.flush()
    return db.get(models.SpeciesAlias, alias_id)


# ---------------------------------------------------------------------------
# 显式合并 / 拆分(不自动发生)
# ---------------------------------------------------------------------------

def merge_identities(db, source_id: int, target_id: int, *, rationale, decided_by=None):
    """把 source 合并进 target。历史业务记录的版本快照不动, 身份经合并链解析到 target。"""
    source = db.get(models.SpeciesIdentity, source_id)
    target = root_identity(db, target_id)
    if source is None or target is None:
        raise HTTPException(status_code=404, detail="品种身份不存在")
    source = root_identity(db, source.id)
    if source.id == target.id:
        raise HTTPException(status_code=400, detail="不能合并到自身或已属同一身份")
    if source.status != "active" or target.status != "active":
        raise HTTPException(status_code=400, detail="只有存续中的品种身份可以合并")

    for alias in source.aliases:
        if alias.status != "approved":
            continue
        clash = db.query(models.SpeciesAlias).filter(
            models.SpeciesAlias.normalized_text == alias.normalized_text,
            models.SpeciesAlias.status == "approved",
            models.SpeciesAlias.id != alias.id,
        ).first()
        if clash is None:
            alias.identity_id = target.id
        else:
            alias.status = "withdrawn"
            alias.reason = (alias.reason or "") + f"| 合并至身份{target.id}时写法冲突, 撤回"

    source.status = "merged"
    source.merged_into_id = target.id
    for legacy in db.query(models.LegacySpeciesText).filter(
        models.LegacySpeciesText.mapped_identity_id == source.id,
        models.LegacySpeciesText.status == "mapped",
    ):
        legacy.mapped_identity_id = target.id

    _record_decision(
        db, action="merge", rationale=rationale, decided_by=decided_by,
        source_identity_id=source.id, target_identity_id=target.id,
    )
    db.flush()
    return target


def split_identity(db, from_identity_id: int, new_canonical_name: str, move_alias_texts,
                   *, rationale, decided_by=None):
    """从既有品种中拆出一个真正不同的品系, 把指定已批准写法迁移到新身份。"""
    origin = root_identity(db, from_identity_id)
    if origin is None or origin.status != "active":
        raise HTTPException(status_code=404, detail="品种身份不存在或已合并")

    texts = [t.strip() for t in (move_alias_texts or []) if t and t.strip()]
    if not texts:
        raise HTTPException(status_code=400, detail="拆分至少要迁移一个写法(别名/旧名)到新品系")

    new_identity = create_identity(
        db, new_canonical_name, created_by=decided_by,
        reason=f"从身份{origin.id}拆分: {rationale}",
    )

    moved = []
    for raw in texts:
        normalized = normalize_name(raw)
        alias = db.query(models.SpeciesAlias).filter(
            models.SpeciesAlias.normalized_text == normalized,
            models.SpeciesAlias.status == "approved",
        ).first()
        if alias is None or root_identity(db, alias.identity_id).id != origin.id:
            raise HTTPException(status_code=400, detail=f"写法 '{raw}' 不属于原品种, 无法迁移")
        # 新身份的规范名别名已在 create_identity 中建立
        if normalized != normalize_name(new_canonical_name):
            alias.identity_id = new_identity.id
            moved.append(raw)

    _record_decision(
        db, action="split", rationale=rationale, decided_by=decided_by,
        source_identity_id=origin.id, target_identity_id=new_identity.id,
        source_text=", ".join(moved),
    )
    db.flush()
    return new_identity


# ---------------------------------------------------------------------------
# 旧文本: 候选生成、扫描、未决队列、裁定
# ---------------------------------------------------------------------------

def suggest_candidates(db, normalized: str, limit: int = 5, threshold: float = 0.45):
    candidates = []
    seen_identities = set()
    aliases = db.query(models.SpeciesAlias).filter(
        models.SpeciesAlias.status == "approved"
    ).all()
    for alias in aliases:
        identity = root_identity(db, alias.identity_id)
        if identity is None or identity.id in seen_identities:
            continue
        score = SequenceMatcher(None, normalized, alias.normalized_text).ratio()
        # 子串包含给一点加成, 覆盖简称场景
        if normalized and alias.normalized_text and (
            normalized in alias.normalized_text or alias.normalized_text in normalized
        ):
            score = max(score, 0.75)
        if score >= threshold:
            version = current_version(db, identity)
            candidates.append(Candidate(
                identity_id=identity.id,
                identity_code=identity.code,
                canonical_name=version.canonical_name if version else "",
                score=round(score, 3),
                matched_alias=alias.alias_text,
            ))
            seen_identities.add(identity.id)
    candidates.sort(key=lambda c: c.score, reverse=True)
    return candidates[:limit]


def _upsert_legacy(db, raw_text, source_table, count_hint=1):
    normalized = normalize_name(raw_text)
    legacy = db.query(models.LegacySpeciesText).filter(
        models.LegacySpeciesText.normalized_text == normalized
    ).first()
    if legacy is None:
        legacy = models.LegacySpeciesText(
            raw_text=raw_text, normalized_text=normalized, source_table=source_table,
            occurrence_count=count_hint, status="pending",
        )
        db.add(legacy)
        db.flush()
    else:
        legacy.occurrence_count += count_hint
    return legacy


def resolve_species_text(db, raw_text: str, source_table: str, register: bool = False) -> SpeciesResolution:
    """录入时解析品种文本。

    命中规范名/有效别名 -> 关联到身份(永不自动合并身份);
    否则生成候选。register=True(业务记录落库)时把文本计入未决队列;
    register=False(录入预览)只返回候选, 不污染队列计数。
    """
    normalized = normalize_name(raw_text)
    if not normalized:
        raise HTTPException(status_code=400, detail="品种名称不能为空")

    alias = _approved_alias(db, normalized)
    if alias is not None:
        identity = root_identity(db, alias.identity_id)
        version = current_version(db, identity)
        is_canonical = bool(version and normalize_name(version.canonical_name) == normalized)
        return SpeciesResolution(
            raw_text=raw_text, normalized_text=normalized,
            status="exact_canonical" if is_canonical else "exact_alias",
            identity=identity, version=version,
        )

    legacy = db.query(models.LegacySpeciesText).filter(
        models.LegacySpeciesText.normalized_text == normalized
    ).first()
    if legacy is not None and legacy.status == "mapped" and legacy.mapped_identity_id:
        identity = root_identity(db, legacy.mapped_identity_id)
        return SpeciesResolution(
            raw_text=raw_text, normalized_text=normalized, status="legacy_mapped",
            identity=identity, version=current_version(db, identity), legacy_text=legacy,
        )
    if legacy is not None and legacy.status == "rejected":
        return SpeciesResolution(
            raw_text=raw_text, normalized_text=normalized, status="rejected",
            legacy_text=legacy, candidates=suggest_candidates(db, normalized),
        )

    if legacy is None:
        if register:
            legacy = _upsert_legacy(db, raw_text, source_table)
    elif register and legacy.status == "pending":
        # 每条真实业务记录出现一次, 计数 +1(队列计数可由 scan 随时重算校准)
        legacy.occurrence_count += 1
        if source_table not in legacy.source_table.split(","):
            legacy.source_table = f"{legacy.source_table},{source_table}"

    candidates = suggest_candidates(db, normalized)
    if legacy is not None and register and not legacy.suggested_identity_id and candidates:
        legacy.suggested_identity_id = candidates[0].identity_id
        legacy.similarity = candidates[0].score
    if legacy is not None:
        db.flush()
    return SpeciesResolution(
        raw_text=raw_text, normalized_text=normalized, status="pending",
        legacy_text=legacy, candidates=candidates,
    )


def list_pending_legacy(db):
    return db.query(models.LegacySpeciesText).filter(
        models.LegacySpeciesText.status == "pending"
    ).order_by(models.LegacySpeciesText.occurrence_count.desc(), models.LegacySpeciesText.id).all()


def decide_legacy_mapping(db, legacy_text_id: int, action: str, *,
                          target_identity_id: Optional[int] = None,
                          decided_by=None, rationale=None):
    """人工裁定旧文本: map(映射并回填业务记录) / reject(确认非品种写法)。"""
    legacy = db.get(models.LegacySpeciesText, legacy_text_id)
    if legacy is None:
        raise HTTPException(status_code=404, detail="未决文本不存在")
    if legacy.status not in ("pending", "withdrawn"):
        raise HTTPException(status_code=409, detail=f"该文本状态为 {legacy.status}, 不能裁定")

    if action == "reject":
        legacy.status = "rejected"
        legacy.decided_by = decided_by
        legacy.decided_at = datetime.utcnow()
        _record_decision(
            db, action="reject", rationale=rationale or "确认不是品种写法",
            decided_by=decided_by, legacy_text_id=legacy.id, source_text=legacy.raw_text,
        )
        db.flush()
        return legacy

    if action != "map":
        raise HTTPException(status_code=400, detail="动作只支持 map 或 reject")
    if target_identity_id is None:
        raise HTTPException(status_code=400, detail="映射需要指定目标品种身份")
    target = root_identity(db, target_identity_id)
    if target is None or target.status != "active":
        raise HTTPException(status_code=404, detail="目标品种身份不存在或已合并")

    # 把该写法登记为目标身份的有效别名, 以后录入同样文本即可直接命中
    existing = db.query(models.SpeciesAlias).filter(
        models.SpeciesAlias.normalized_text == legacy.normalized_text
    ).first()
    if existing is None:
        db.add(models.SpeciesAlias(
            identity_id=target.id, alias_text=legacy.raw_text,
            normalized_text=legacy.normalized_text, status="approved",
            proposed_by=decided_by, reviewed_by=decided_by,
            reason=f"未决文本映射(legacy#{legacy.id})",
        ))
    elif existing.status != "approved" or root_identity(db, existing.identity_id).id != target.id:
        existing.identity_id = target.id
        existing.status = "approved"
        existing.reviewed_by = decided_by
        existing.reviewed_at = datetime.utcnow()

    version = current_version(db, target)
    backfilled = _backfill_business_rows(db, legacy.normalized_text, target, version)

    legacy.status = "mapped"
    legacy.mapped_identity_id = target.id
    legacy.decided_by = decided_by
    legacy.decided_at = datetime.utcnow()

    _record_decision(
        db, action="map", rationale=rationale or "人工映射旧文本", decided_by=decided_by,
        legacy_text_id=legacy.id, target_identity_id=target.id, source_text=legacy.raw_text,
    )
    db.flush()
    legacy.backfilled_count = backfilled
    return legacy


def withdraw_legacy_mapping(db, legacy_text_id: int, *, decided_by=None, rationale=None):
    """撤回映射: 文本回到未决队列, 自动回填过的记录解除身份, 决策依据保留。"""
    legacy = db.get(models.LegacySpeciesText, legacy_text_id)
    if legacy is None:
        raise HTTPException(status_code=404, detail="文本不存在")
    if legacy.status != "mapped":
        raise HTTPException(status_code=409, detail="只有已映射文本可以撤回")

    target_id = legacy.mapped_identity_id
    cleared = 0
    for model in (models.Pond, models.Batch, models.StockingRecord):
        rows = db.query(model).filter(
            model.species_identity_id == target_id,
            model.species_version_id.isnot(None),
        ).all()
        for row in rows:
            if normalize_name(row.species) == legacy.normalized_text:
                row.species_identity_id = None
                row.species_version_id = None
                cleared += 1

    # 由映射登记的别名同步撤回(规范名除外)
    alias = db.query(models.SpeciesAlias).filter(
        models.SpeciesAlias.normalized_text == legacy.normalized_text,
        models.SpeciesAlias.identity_id == target_id,
        models.SpeciesAlias.status == "approved",
    ).first()
    version = current_version(db, root_identity(db, target_id))
    if alias and version and alias.normalized_text != normalize_name(version.canonical_name):
        alias.status = "withdrawn"
        alias.reason = (alias.reason or "") + f"| 映射被撤回(legacy#{legacy.id})"

    legacy.status = "pending"
    legacy.mapped_identity_id = None
    legacy.decided_by = decided_by
    legacy.decided_at = datetime.utcnow()

    _record_decision(
        db, action="withdraw", rationale=rationale or "撤回旧文本映射", decided_by=decided_by,
        legacy_text_id=legacy.id, target_identity_id=target_id, source_text=legacy.raw_text,
    )
    db.flush()
    legacy.cleared_count = cleared
    return legacy


def _backfill_business_rows(db, normalized, identity, version):
    """把历史上录入该文本、但尚无身份的塘口/批次/投苗记录关联到身份。"""
    count = 0
    for model in (models.Pond, models.Batch, models.StockingRecord):
        rows = db.query(model).filter(model.species_identity_id.is_(None)).all()
        for row in rows:
            if normalize_name(row.species) == normalized:
                row.species_identity_id = identity.id
                row.species_version_id = version.id if version else None
                count += 1
    return count


def scan_legacy_texts(db):
    """扫描三张业务表, 为没有身份的旧文本生成/刷新未决队列与候选。"""
    stats = {"scanned": 0, "new_texts": 0, "backfilled_exact": 0}

    # 先跨表聚合同一归一化文本, occurrence_count 为全库出现次数
    aggregated = {}  # normalized -> {"raw": str, "sources": set, "rows": list}
    for source_table, model in (
        ("ponds", models.Pond),
        ("batches", models.Batch),
        ("stocking_records", models.StockingRecord),
    ):
        for row in db.query(model).all():
            stats["scanned"] += 1
            if not row.species:
                continue
            normalized = normalize_name(row.species)
            bucket = aggregated.setdefault(
                normalized, {"raw": row.species, "sources": set(), "rows": []}
            )
            bucket["sources"].add(source_table)
            bucket["rows"].append(row)

    for normalized, bucket in aggregated.items():
        rows = bucket["rows"]
        # 已在目录中的写法直接安全回填, 这不是合并, 只是精确命中
        alias = _approved_alias(db, normalized)
        if alias is not None:
            identity = root_identity(db, alias.identity_id)
            version = current_version(db, identity)
            for row in rows:
                if row.species_identity_id is None:
                    row.species_identity_id = identity.id
                    row.species_version_id = version.id if version else None
                    stats["backfilled_exact"] += 1

            # 同步结案从未被人工裁定过的未决条目; 人工驳回/撤回过的保留, 不覆盖人工意图
            legacy = db.query(models.LegacySpeciesText).filter(
                models.LegacySpeciesText.normalized_text == normalized,
                models.LegacySpeciesText.status == "pending",
                models.LegacySpeciesText.decided_at.is_(None),
            ).first()
            if legacy is not None:
                legacy.status = "mapped"
                legacy.mapped_identity_id = identity.id
                legacy.suggested_identity_id = identity.id
                legacy.similarity = 1.0
                legacy.decided_by = "system-scan"
                legacy.decided_at = datetime.utcnow()
                _record_decision(
                    db, action="auto_map",
                    rationale="扫描时发现该文本已精确命中品种目录, 自动结案",
                    decided_by="system-scan", legacy_text_id=legacy.id,
                    target_identity_id=identity.id, source_text=legacy.raw_text,
                )
            continue

        legacy = db.query(models.LegacySpeciesText).filter(
            models.LegacySpeciesText.normalized_text == normalized
        ).first()
        if legacy is None:
            legacy = models.LegacySpeciesText(
                raw_text=bucket["raw"], normalized_text=normalized,
                source_table=",".join(sorted(bucket["sources"])), status="pending",
            )
            db.add(legacy)
            db.flush()
            stats["new_texts"] += 1
        else:
            legacy.source_table = ",".join(sorted(bucket["sources"]))
        legacy.occurrence_count = len(rows)
        candidates = suggest_candidates(db, normalized, limit=1)
        if candidates:
            legacy.suggested_identity_id = candidates[0].identity_id
            legacy.similarity = candidates[0].score
    db.flush()
    return stats


# ---------------------------------------------------------------------------
# 统一身份视图(列表 / 周期分析 / 追溯共用)
# ---------------------------------------------------------------------------

def identity_summary(db, identity: models.SpeciesIdentity) -> dict:
    version = current_version(db, identity)
    historical = [
        {"version_id": v.id, "version_no": v.version_no, "name": v.canonical_name,
         "superseded_at": v.superseded_at}
        for v in identity.versions if v.status == "superseded"
    ]
    alias_texts = sorted({
        a.alias_text for a in db.query(models.SpeciesAlias).filter(
            models.SpeciesAlias.identity_id.in_(
                [identity.id] + [m.id for m in identity.merged_from]
            ),
            models.SpeciesAlias.status == "approved",
        ).all()
    })
    return {
        "identity_id": identity.id,
        "identity_code": identity.code,
        "current_standard_name": version.canonical_name if version else None,
        "status": identity.status,
        "merged_into_code": root_identity(db, identity.id).code if identity.status == "merged" else None,
        "historical_names": historical,
        "aliases": alias_texts,
    }


def species_view(db, *, identity_id, version_id, raw_text) -> dict:
    """生成一条业务记录的品种视图, 明确区分: 未裁定 / 历史名称 / 当前标准名。"""
    base = {
        "identity_id": None,
        "identity_code": None,
        "current_standard_name": None,
        "historical_name": None,
        "is_historical_name": False,
        "unresolved": False,
        "raw_name": raw_text,
        "resolution_status": "unresolved",
    }
    if identity_id is None:
        normalized = normalize_name(raw_text)
        legacy = db.query(models.LegacySpeciesText).filter(
            models.LegacySpeciesText.normalized_text == normalized
        ).first() if normalized else None
        base["unresolved"] = True
        base["resolution_status"] = (
            legacy.status if legacy else "pending"
        )
        if legacy and legacy.status == "pending":
            base["candidates"] = [
                {"identity_id": c.identity_id, "identity_code": c.identity_code,
                 "canonical_name": c.canonical_name, "score": c.score}
                for c in suggest_candidates(db, normalized)
            ]
        return base

    identity = root_identity(db, identity_id)
    if identity is None:
        base["unresolved"] = True
        return base
    version = current_version(db, identity)
    used_version = db.get(models.SpeciesNameVersion, version_id) if version_id else None
    base["identity_id"] = identity.id
    base["identity_code"] = identity.code
    base["current_standard_name"] = version.canonical_name if version else None

    if used_version is not None and version is not None and used_version.id != version.id:
        base["historical_name"] = used_version.canonical_name
        base["is_historical_name"] = True
        base["resolution_status"] = "historical_name"
    else:
        base["resolution_status"] = "current"
    return base


# ---------------------------------------------------------------------------
# 已签署分析快照
# ---------------------------------------------------------------------------

def attach_species_view(db, obj):
    """给塘口/批次/投苗 ORM 对象挂上统一身份视图, 供响应模型序列化。"""
    obj.species_identity = species_view(
        db,
        identity_id=getattr(obj, "species_identity_id", None),
        version_id=getattr(obj, "species_version_id", None),
        raw_text=getattr(obj, "species", None),
    )
    return obj


def sign_analysis(db, batch, payload: dict, *, signed_by, sign_reason=None):
    identity = root_identity(db, batch.species_identity_id) if batch.species_identity_id else None
    used_version = db.get(models.SpeciesNameVersion, batch.species_version_id) if batch.species_version_id else None
    name_at_signing = used_version.canonical_name if used_version else batch.species
    snapshot = models.SignedAnalysis(
        batch_id=batch.id,
        species_identity_id=identity.id if identity else None,
        species_version_id=used_version.id if used_version else None,
        species_name_at_signing=name_at_signing,
        payload_json=json.dumps(payload, ensure_ascii=False, default=str),
        signed_by=signed_by,
        sign_reason=sign_reason,
    )
    db.add(snapshot)
    db.flush()
    return snapshot


def reproduce_signed_analysis(db, snapshot: models.SignedAnalysis) -> dict:
    payload = json.loads(snapshot.payload_json)
    identity = root_identity(db, snapshot.species_identity_id) if snapshot.species_identity_id else None
    payload["signed"] = {
        "signed_at": snapshot.created_at,
        "signed_by": snapshot.signed_by,
        "sign_reason": snapshot.sign_reason,
        "species_name_at_signing": snapshot.species_name_at_signing,
        "species_identity_code": identity.code if identity else None,
    }
    return payload
