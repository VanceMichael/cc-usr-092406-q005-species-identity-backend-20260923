"""品种身份领域服务。

职责：
* 名称归一化与稳定标识(code)管理；
* 录入时的身份解析：精确/空白大小写变体自动采信，模糊相似只给候选、绝不自动合并；
* 候选映射与未决队列(pending)的生成、裁定(合并)、驳回、撤回、拆分，全部留审计事件；
* 业务表(ponds/batches/stocking_records)的 species_code 快照回填；
* 对外统一的身份视图(当前标准名/历史名/别名/未决状态)。

并发正确性依赖 species_names 的部分唯一索引：同一归一化写法至多一个 active 名称。
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime
from difflib import SequenceMatcher
from typing import Optional

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

from ..models import (
    Batch,
    Pond,
    Species,
    SpeciesEvent,
    SpeciesMapping,
    SpeciesName,
    StockingRecord,
)

# 模糊候选的最低相似度。仅用于提示，永远不触发自动合并。
FUZZY_THRESHOLD = 0.45
MAX_CANDIDATES = 5


class SpeciesError(Exception):
    """品种领域规则冲突（路由层转成对应 HTTP 状态码）。"""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class SpeciesConflict(SpeciesError):
    def __init__(self, message: str):
        super().__init__(message, status_code=409)


def _commit(db) -> None:
    """提交并发敏感写入：唯一索引冲突统一转成 409 语义错误。"""
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise SpeciesConflict("并发裁定冲突：该写法已有另一版本生效，本次操作未生效（仅一个版本可生效）")


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------

def normalize_name(text: Optional[str]) -> str:
    """归一化品种写法：去首尾及内部空白、统一标点、转小写。

    " 罗氏 沼虾 "、"罗氏沼虾"、"罗氏·沼虾" 归一到同一键；
    而 "罗氏沼虾" 与 "罗氏虾"(缺字) 是不同键，必须走人工裁定。
    """
    if not text:
        return ""
    text = str(text).strip().lower()
    text = re.sub(r"\s+", "", text)
    text = re.sub(r"[·・,，.。\-—_()（）\[\]【】'\"“”‘’/\\]+", "", text)
    return text


def _generate_code(db, normalized_key: str) -> str:
    """由归一化名生成稳定可读候选 code；冲突时追加序号。code 一经启用永不改变。"""
    digest = hashlib.md5(normalized_key.encode("utf-8")).digest()
    base = "SP" + hex(int.from_bytes(digest[:5], "big"))[2:].zfill(10)[:8].upper()
    code = base
    n = 1
    while db.query(Species).filter(Species.code == code).first():
        code = f"{base}-{n}"
        n += 1
    return code


def _now() -> datetime:
    return datetime.utcnow()


def _log(db, species_id: int, event_type: str, *, from_value=None, to_value=None,
         related_species_id=None, mapping_id=None, reason=None, actor=None) -> SpeciesEvent:
    event = SpeciesEvent(
        species_id=species_id,
        event_type=event_type,
        from_value=from_value,
        to_value=to_value,
        related_species_id=related_species_id,
        mapping_id=mapping_id,
        reason=reason,
        actor=actor,
    )
    db.add(event)
    return event


def _active_name_by_key(db, key: str) -> Optional[SpeciesName]:
    if not key:
        return None
    return db.query(SpeciesName).filter(
        SpeciesName.normalized_key == key,
        SpeciesName.status == "active",
    ).first()


def _active_names_map(db):
    """返回 {normalized_key: SpeciesName}，供扫描/批量解析使用。"""
    rows = db.query(SpeciesName).filter(SpeciesName.status == "active").all()
    return {r.normalized_key: r for r in rows}


def get_species(db, species_id: int) -> Species:
    obj = db.query(Species).filter(Species.id == species_id).first()
    if not obj:
        raise SpeciesError("品种不存在", 404)
    return obj


# ---------------------------------------------------------------------------
# 目录维护
# ---------------------------------------------------------------------------

def create_species(db, name: str, *, code: Optional[str] = None, scientific_name: Optional[str] = None,
                   description: Optional[str] = None, actor: Optional[str] = None) -> Species:
    key = normalize_name(name)
    if not key:
        raise SpeciesError("品种名称不能为空")
    owner = _active_name_by_key(db, key)
    if owner:
        raise SpeciesConflict(f"该写法已是品种「{owner.species.current_name}」的生效名称，不能重复建档")
    if code:
        code = code.strip().upper()
        if db.query(Species).filter(Species.code == code).first():
            raise SpeciesConflict(f"品种标识 {code} 已存在")
    else:
        code = _generate_code(db, key)

    species = Species(code=code, current_name=name.strip(), scientific_name=scientific_name,
                      description=description, status="active")
    db.add(species)
    db.flush()
    db.add(SpeciesName(species_id=species.id, name=name.strip(), normalized_key=key,
                       name_type="canonical", status="active", actor=actor,
                       decided_at=_now(), note="建档标准名"))
    _log(db, species.id, "created", to_value=name.strip(), actor=actor)
    # 若该写法此前在未决队列，建档即视为人工裁定为独立品种：了结映射并回填记录
    _resolve_pending_for_key(db, key, species, reason=f"品种「{name.strip()}」建档，同键文本自动归属", actor=actor)
    _backfill_unresolved(db, species)
    db.commit()
    db.refresh(species)
    return species


def rename_canonical(db, species_id: int, new_name: str, *, reason: Optional[str] = None,
                     actor: Optional[str] = None) -> Species:
    """标准名更正：只改变后续展示规则；旧标准名转为历史名并保留为可解析别名。

    已签署分析快照不受影响，仍按签署时口径重现。
    """
    species = get_species(db, species_id)
    new_name = (new_name or "").strip()
    key = normalize_name(new_name)
    if not key:
        raise SpeciesError("新品种名不能为空")
    owner = _active_name_by_key(db, key)
    if owner and owner.species_id != species.id:
        raise SpeciesConflict(f"该写法已是品种「{owner.species.current_name}」的生效名称")

    old_canonical = db.query(SpeciesName).filter(
        SpeciesName.species_id == species.id,
        SpeciesName.name_type == "canonical",
        SpeciesName.status == "active",
    ).first()
    old_name = old_canonical.name if old_canonical else species.current_name

    if owner and owner.species_id == species.id and owner.name_type == "canonical":
        # 仅空白/大小写差异，原 canonical 行直接改名即可
        old_canonical.name = new_name
    else:
        if old_canonical:
            # 旧标准名降级为别名（仍然可解析），并在历史名中留痕
            old_canonical.name_type = "alias"
            old_canonical.note = (old_canonical.note or "") + "；原标准名，更名后转别名"
        if owner and owner.species_id == species.id:
            # key 已被本品种别名占用：该别名升级为标准名
            owner.name = new_name
            owner.name_type = "canonical"
            owner.note = (owner.note or "") + "；别名升级为标准名"
            canonical_row = owner
        else:
            canonical_row = SpeciesName(species_id=species.id, name=new_name, normalized_key=key,
                                        name_type="canonical", status="active", actor=actor,
                                        decided_at=_now(), note="名称更正后的标准名")
            db.add(canonical_row)

    species.current_name = new_name
    _log(db, species.id, "renamed", from_value=old_name, to_value=new_name,
         reason=reason, actor=actor)
    db.commit()
    db.refresh(species)
    return species


def propose_alias(db, species_id: int, alias: str, *, note: Optional[str] = None,
                  actor: Optional[str] = None) -> SpeciesName:
    """登记别名提议（未裁定）。不会影响任何解析结果。"""
    species = get_species(db, species_id)
    alias = (alias or "").strip()
    key = normalize_name(alias)
    if not key:
        raise SpeciesError("别名不能为空")
    owner = _active_name_by_key(db, key)
    if owner:
        if owner.species_id == species.id:
            raise SpeciesConflict("该写法已是本品种的生效名称")
        raise SpeciesConflict(f"该写法已是品种「{owner.species.current_name}」的生效名称，"
                              "如确属同义，请走映射合并；如属不同品系，请拆分")
    row = SpeciesName(species_id=species.id, name=alias, normalized_key=key,
                      name_type="alias", status="proposed", note=note, actor=actor)
    db.add(row)
    db.flush()
    _log(db, species.id, "alias_proposed", to_value=alias, reason=note, actor=actor)
    db.commit()
    db.refresh(row)
    return row


def approve_alias(db, alias_name_id: int, *, actor: Optional[str] = None) -> SpeciesName:
    """批准别名生效。并发批同一写法时，部分唯一索引保证只有一个版本生效。"""
    row = db.query(SpeciesName).filter(SpeciesName.id == alias_name_id).first()
    if not row:
        raise SpeciesError("别名记录不存在", 404)
    if row.status == "active":
        return row
    if row.status in ("superseded", "rejected"):
        raise SpeciesError(f"该别名已{ '撤回' if row.status == 'superseded' else '驳回'}，"
                           "请重新提议后再裁定")
    species = get_species(db, row.species_id)
    holder = _active_name_by_key(db, row.normalized_key)
    if holder and holder.id != row.id:
        raise SpeciesConflict(f"该写法已是品种「{holder.species.current_name}」的生效名称，"
                              "如需调整请先合并或拆分")
    row.status = "active"
    row.decided_at = _now()
    row.actor = actor or row.actor
    _log(db, species.id, "alias_approved", to_value=row.name, actor=actor)
    _resolve_pending_for_key(db, row.normalized_key, species, reason=f"别名「{row.name}」生效", actor=actor)
    _backfill_unresolved(db, species)
    _commit(db)
    db.refresh(row)
    return row


def reject_alias(db, alias_name_id: int, *, reason: Optional[str] = None,
                 actor: Optional[str] = None) -> SpeciesName:
    row = db.query(SpeciesName).filter(SpeciesName.id == alias_name_id).first()
    if not row:
        raise SpeciesError("别名记录不存在", 404)
    if row.status == "active":
        raise SpeciesConflict("生效中的别名不能直接驳回，请先撤回")
    row.status = "rejected"
    row.decided_at = _now()
    row.normalized_key = None  # 释放键位，原文保留在 name 中供审阅
    _log(db, row.species_id, "alias_rejected", from_value=row.name, reason=reason, actor=actor)
    db.commit()
    db.refresh(row)
    return row


def withdraw_alias(db, alias_name_id: int, *, reason: Optional[str] = None,
                   actor: Optional[str] = None) -> SpeciesName:
    """撤回生效别名（并发调整时的下线手段）。"""
    row = db.query(SpeciesName).filter(SpeciesName.id == alias_name_id).first()
    if not row:
        raise SpeciesError("别名记录不存在", 404)
    if row.status != "active" or row.name_type != "alias":
        raise SpeciesError("仅生效中的别名可以撤回")
    row.status = "superseded"
    row.decided_at = _now()
    row.normalized_key = None
    _log(db, row.species_id, "alias_withdrawn", from_value=row.name, reason=reason, actor=actor)
    db.commit()
    db.refresh(row)
    return row


# ---------------------------------------------------------------------------
# 录入解析与同义提示
# ---------------------------------------------------------------------------

def suggest(db, text: str) -> dict:
    """只给同义写法提示，不做任何落库。精确命中时给出唯一身份。"""
    key = normalize_name(text)
    if not key:
        return {"input": text, "match": None, "candidates": []}
    active = _active_name_by_key(db, key)
    if active:
        return {
            "input": text,
            "match": {"code": active.species.code, "current_name": active.species.current_name,
                      "match_type": active.name_type},
            "candidates": [],
        }
    return {"input": text, "match": None, "candidates": _fuzzy_candidates(db, key)}


def _fuzzy_candidates(db, key: str) -> list:
    names = db.query(SpeciesName).filter(SpeciesName.status == "active").all()
    scored = {}
    for n in names:
        ratio = SequenceMatcher(None, key, n.normalized_key).ratio()
        contained = 1.0 if (key and (key in n.normalized_key or n.normalized_key in key)) else 0.0
        score = max(ratio, contained * 0.8)
        if score < FUZZY_THRESHOLD:
            continue
        prev = scored.get(n.species_id)
        if not prev or score > prev["score"]:
            scored[n.species_id] = {
                "species_id": n.species_id,
                "code": n.species.code,
                "current_name": n.species.current_name,
                "matched_name": n.name,
                "match_type": "fuzzy",
                "score": round(score, 3),
            }
    return sorted(scored.values(), key=lambda c: c["score"], reverse=True)[:MAX_CANDIDATES]


def _queue_mapping(db, raw_text: str, key: str, candidates: list, *, source: str,
                   confidence: str) -> SpeciesMapping:
    """登记/复用未决映射。已驳回或已撤回的文本再次出现时重新入队（允许翻案）。"""
    pending = db.query(SpeciesMapping).filter(
        SpeciesMapping.normalized_key == key,
        SpeciesMapping.status == "pending",
    ).first()
    if pending:
        pending.occurrence_count = (pending.occurrence_count or 1) + 1
        if not pending.candidate_species_id and candidates:
            pending.candidate_species_id = candidates[0]["species_id"]
            pending.confidence = "low"
        return pending
    candidate_id = candidates[0]["species_id"] if candidates else None
    mapping = SpeciesMapping(
        raw_text=raw_text.strip(), normalized_key=key,
        candidate_species_id=candidate_id,
        confidence=confidence if candidate_id else "low",
        status="pending", source=source, occurrence_count=1,
    )
    db.add(mapping)
    db.flush()
    _log(db, candidate_id or _placeholder_species(db), "mapping_proposed",
         to_value=raw_text.strip(), mapping_id=mapping.id,
         reason="自动候选，等待人工裁定" if candidate_id else "无候选，等待人工裁定")
    return mapping


def _placeholder_species(db) -> int:
    """没有候选身份时，审计事件挂在一个稳定的占位主体下，避免事件无所属。"""
    placeholder = db.query(Species).filter(Species.code == "SP-UNRESOLVED").first()
    if placeholder:
        return placeholder.id
    placeholder = Species(code="SP-UNRESOLVED", current_name="（未裁定文本队列）",
                          status="inactive",
                          description="系统占位：承载尚未映射到任何品种的文本提议事件，不参与业务统计")
    db.add(placeholder)
    db.flush()
    return placeholder.id


def resolve_on_entry(db, raw_text: Optional[str], *, source: str = "entry",
                     actor: Optional[str] = None, commit: bool = True) -> dict:
    """录入/扫描时解析品种文本。

    返回:
      status=matched        精确命中生效名称（含空白/标点/大小写变体）
      status=pending        无身份：已入未决队列，candidates 仅为提示
    真正不同品系（仅模糊相似或毫无相似）永远不会自动合并。
    """
    key = normalize_name(raw_text)
    if not key:
        return {"status": "empty", "species_code": None,
                "candidates": [], "mapping_id": None}
    active = _active_name_by_key(db, key)
    if active:
        result = {"status": "matched", "species_code": active.species.code,
                  "species_id": active.species_id, "match_type": active.name_type,
                  "candidates": [], "mapping_id": None}
        if (raw_text or "").strip() != active.name:
            # 空白/标点/大小写变体：复用同一键的既有采信依据并累加出现次数；
            # 没有依据时才新增一条 resolved_auto 记录。不新增名称行（键已被占用）。
            mapping = db.query(SpeciesMapping).filter(
                SpeciesMapping.normalized_key == key,
                SpeciesMapping.status.in_(["resolved_auto", "approved"]),
                SpeciesMapping.target_species_id == active.species_id,
            ).order_by(SpeciesMapping.id.desc()).first()
            if mapping:
                mapping.occurrence_count = (mapping.occurrence_count or 1) + 1
            else:
                mapping = SpeciesMapping(
                    raw_text=(raw_text or "").strip(), normalized_key=key,
                    candidate_species_id=active.species_id, target_species_id=active.species_id,
                    confidence="high", status="resolved_auto", source=source, occurrence_count=1,
                    reason=f"与生效名「{active.name}」仅空白/标点/大小写差异，自动采信",
                )
                db.add(mapping)
            db.flush()
            result["mapping_id"] = mapping.id
            result["status"] = "resolved_auto"
            if mapping.reason and "自动采信" in mapping.reason:
                _log(db, active.species_id, "mapping_resolved_auto",
                     to_value=(raw_text or "").strip(), mapping_id=mapping.id, actor=actor,
                     reason=mapping.reason)
            if commit:
                db.commit()
        return result

    candidates = _fuzzy_candidates(db, key)
    confidence = "low"
    mapping = _queue_mapping(db, raw_text, key, candidates, source=source, confidence=confidence)
    if commit:
        db.commit()
    db.refresh(mapping)
    return {"status": "pending", "species_code": None, "mapping_id": mapping.id,
            "candidates": candidates}


# ---------------------------------------------------------------------------
# 旧文本扫描 / 未决队列
# ---------------------------------------------------------------------------

def _iter_business_rows(db):
    yield "pond", db.query(Pond).filter(Pond.species.isnot(None)).all()
    yield "batch", db.query(Batch).filter(Batch.species.isnot(None)).all()
    yield "stocking", db.query(StockingRecord).filter(StockingRecord.species.isnot(None)).all()


def scan_legacy_texts(db, *, actor: Optional[str] = None) -> dict:
    """扫描 ponds/batches/stocking_records 中的旧文本：

    * 命中生效键（含空白变体）→ 立即回填 species_code 并留 resolved_auto 依据；
    * 其余文本 → 生成候选映射进入未决队列（每个归一化键仅一条 pending）。
    """
    name_map = _active_names_map(db)
    # key -> {"raw": set, "rows": [(kind,row)], "count"}
    buckets: dict = {}
    for kind, rows in _iter_business_rows(db):
        for row in rows:
            key = normalize_name(row.species)
            if not key:
                continue
            bucket = buckets.setdefault(key, {"raws": set(), "rows": []})
            bucket["raws"].add((row.species or "").strip())
            bucket["rows"].append((kind, row))

    auto_resolved, queued = [], []
    for key, bucket in buckets.items():
        raw_sample = sorted(bucket["raws"], key=len)[0]
        active = name_map.get(key)
        terminal = db.query(SpeciesMapping).filter(
            SpeciesMapping.normalized_key == key,
            SpeciesMapping.status.in_(["approved", "resolved_auto"]),
        ).order_by(SpeciesMapping.decided_at.desc()).first()
        if active or terminal:
            target_id = active.species_id if active else terminal.target_species_id
            target = active.species if active else terminal.target_species
            changed = 0
            for kind, row in bucket["rows"]:
                if getattr(row, "species_code", None) is None and target:
                    row.species_code = target.code
                    changed += 1
            # 幂等：记录均已回填且已有终态依据时，不再重复造映射
            if changed == 0 and terminal:
                continue
            mapping = SpeciesMapping(
                raw_text=raw_sample, normalized_key=key,
                candidate_species_id=target_id, target_species_id=target_id,
                confidence="high", status="resolved_auto", source="scan",
                occurrence_count=len(bucket["rows"]),
                reason="旧文本扫描：命中生效名称键，自动回填" if active
                       else f"旧文本扫描：沿用既有{terminal.status}映射回填",
            )
            db.add(mapping)
            db.flush()
            _log(db, target_id, "mapping_resolved_auto", to_value=raw_sample,
                 mapping_id=mapping.id, reason=f"扫描回填 {changed} 条业务记录", actor=actor)
            auto_resolved.append({"key": key, "raw_text": raw_sample,
                                  "code": target.code if target else None, "backfilled": changed})
            continue

        pending = db.query(SpeciesMapping).filter(
            SpeciesMapping.normalized_key == key,
            SpeciesMapping.status == "pending",
        ).first()
        candidates = _fuzzy_candidates(db, key)
        if pending:
            pending.occurrence_count = len(bucket["rows"])
            target = pending
            created = False
        else:
            target = SpeciesMapping(
                raw_text=raw_sample, normalized_key=key,
                candidate_species_id=candidates[0]["species_id"] if candidates else None,
                confidence="low", status="pending", source="scan",
                occurrence_count=len(bucket["rows"]),
            )
            db.add(target)
            db.flush()
            created = True
        if created:
            placeholder = candidates[0]["species_id"] if candidates else _placeholder_species(db)
            _log(db, placeholder, "mapping_proposed", to_value=raw_sample,
                 mapping_id=target.id, reason="旧文本扫描入队", actor=actor)
        queued.append({"key": key, "raw_text": raw_sample,
                       "occurrence_count": target.occurrence_count,
                       "candidate": candidates[0] if candidates else None,
                       "mapping_id": target.id})

    db.commit()
    return {"scanned_keys": len(buckets), "auto_resolved": auto_resolved, "queued": queued}


def list_mappings(db, status: Optional[str] = None) -> list:
    q = db.query(SpeciesMapping)
    if status:
        q = q.filter(SpeciesMapping.status == status)
    return q.order_by(SpeciesMapping.status, SpeciesMapping.created_at.desc()).all()


def _resolve_pending_for_key(db, key: str, species: Species, *, reason: str, actor=None,
                             exclude_id: Optional[int] = None):
    db.flush()  # 调用方可能已在会话中改动映射状态，先落库再查（会话 autoflush=False）
    q = db.query(SpeciesMapping).filter(
        SpeciesMapping.normalized_key == key,
        SpeciesMapping.status == "pending",
    )
    if exclude_id is not None:
        q = q.filter(SpeciesMapping.id != exclude_id)
    for m in q.all():
        m.status = "resolved_auto"
        m.target_species_id = species.id
        m.decided_at = _now()
        m.reason = reason
        _log(db, species.id, "mapping_resolved_auto", to_value=m.raw_text,
             mapping_id=m.id, reason=reason, actor=actor)


def _backfill_unresolved(db, species: Species):
    """把本品种所有生效键对应的、尚无 code 快照的业务记录指向该品种。"""
    keys = {n.normalized_key for n in species.names if n.status == "active"}
    if not keys:
        return 0
    changed = 0
    for kind, rows in _iter_business_rows(db):
        for row in rows:
            if getattr(row, "species_code", None) is not None:
                continue
            if normalize_name(row.species) in keys:
                row.species_code = species.code
                changed += 1
    return changed


# ---------------------------------------------------------------------------
# 裁定：合并 / 驳回 / 撤回 / 拆分
# ---------------------------------------------------------------------------

def approve_mapping(db, mapping_id: int, target_species_id: int, *, reason: Optional[str] = None,
                    actor: Optional[str] = None) -> SpeciesMapping:
    """批准候选映射：仅把**这一条旧文本**并入目标品种（文本级合并）。

    * 旧文本成为目标品种的生效别名（若该写法已是另一品种的生效名称则拒绝——
      整体合并两个已建档品种必须走显式的 merge_species，不能由模糊候选自动触发）；
    * 同键业务记录回填目标 code；同键其他未决映射了结；全程审计留痕。
    """
    mapping = db.query(SpeciesMapping).filter(SpeciesMapping.id == mapping_id).first()
    if not mapping:
        raise SpeciesError("映射不存在", 404)
    if mapping.status != "pending":
        raise SpeciesConflict(f"该映射已裁定为 {mapping.status}，不能重复裁定")
    target = get_species(db, target_species_id)
    source = mapping.candidate_species

    holder = _active_name_by_key(db, mapping.normalized_key)
    if holder and holder.species_id != target.id:
        raise SpeciesConflict(
            f"该写法目前是品种「{holder.species.current_name}」的生效名称；"
            "确认两个品种是同一物种时请先调用品种合并接口，如系不同品系请使用拆分")

    # 旧文本成为目标品种的生效别名（键可能已属于目标 canonical，则无需新增）
    if not holder:
        db.add(SpeciesName(species_id=target.id, name=mapping.raw_text,
                           normalized_key=mapping.normalized_key, name_type="alias",
                           status="active", decided_at=_now(), actor=actor,
                           note=f"映射 #{mapping.id} 批准合并"))

    # 只回填本条映射写法对应的业务记录；其他写法由各自的映射/别名分别裁定
    changed = 0
    for kind, rows in _iter_business_rows(db):
        for row in rows:
            if (getattr(row, "species_code", None) is None
                    and normalize_name(row.species) == mapping.normalized_key):
                row.species_code = target.code
                changed += 1

    mapping.status = "approved"
    mapping.target_species_id = target.id
    mapping.decided_at = _now()
    mapping.reason = reason
    mapping.actor = actor
    _log(db, target.id, "mapping_approved", from_value=mapping.raw_text,
         to_value=target.current_name, related_species_id=source.id if source else None,
         mapping_id=mapping.id,
         reason=(reason or "") + f"；回填 {changed} 条", actor=actor)

    # 同键其余未决项了结（排除刚被批准的本映射）
    _resolve_pending_for_key(db, mapping.normalized_key, target,
                             reason=f"映射 #{mapping.id} 已批准合并", actor=actor,
                             exclude_id=mapping.id)
    _commit(db)
    db.refresh(mapping)
    return mapping


def reject_mapping(db, mapping_id: int, *, reason: Optional[str] = None,
                   actor: Optional[str] = None) -> SpeciesMapping:
    """驳回候选映射：不建立身份关系，文本保持未裁定；以后再次出现会重新入队。"""
    mapping = db.query(SpeciesMapping).filter(SpeciesMapping.id == mapping_id).first()
    if not mapping:
        raise SpeciesError("映射不存在", 404)
    if mapping.status != "pending":
        raise SpeciesConflict(f"该映射已裁定为 {mapping.status}")
    mapping.status = "rejected"
    mapping.decided_at = _now()
    mapping.reason = reason
    mapping.actor = actor
    owner_id = mapping.candidate_species_id or _placeholder_species(db)
    _log(db, owner_id, "mapping_rejected", from_value=mapping.raw_text,
         mapping_id=mapping.id, reason=reason, actor=actor)
    db.commit()
    db.refresh(mapping)
    return mapping


def withdraw_mapping(db, mapping_id: int, *, reason: Optional[str] = None,
                     actor: Optional[str] = None) -> SpeciesMapping:
    """撤回已批准的映射：别名下线、身份脱钩，但历史 code 快照与审计依据保留。"""
    mapping = db.query(SpeciesMapping).filter(SpeciesMapping.id == mapping_id).first()
    if not mapping:
        raise SpeciesError("映射不存在", 404)
    if mapping.status != "approved":
        raise SpeciesConflict("仅已批准(合并)的映射可以撤回")
    target = mapping.target_species
    name_row = None
    if target:
        name_row = db.query(SpeciesName).filter(
            SpeciesName.species_id == target.id,
            SpeciesName.normalized_key == mapping.normalized_key,
            SpeciesName.status == "active",
        ).first()
    if name_row and name_row.name_type == "alias":
        name_row.status = "superseded"
        name_row.normalized_key = None
        name_row.decided_at = _now()
    mapping.status = "withdrawn"
    mapping.decided_at = _now()
    mapping.reason = reason
    mapping.actor = actor
    if target:
        _log(db, target.id, "mapping_withdrawn", from_value=mapping.raw_text,
             mapping_id=mapping.id, reason=reason, actor=actor)
    db.commit()
    db.refresh(mapping)
    return mapping


def split_species(db, source_species_id: int, new_species_name: str, move_aliases: list,
                  *, reason: Optional[str] = None, actor: Optional[str] = None) -> dict:
    """拆分：认定某个/某些别名其实是不同品系，为其建立独立新品种。

    * 选中的生效别名迁移到新品种（其中一个升为新标准名）；
    * 依据业务记录原文判定归属：原文命中被拆出写法的记录改挂新品种 code，
      其余记录保留源品种 code，不做臆断合并；
    * 源品种与新品种各留一条 split 审计。
    """
    source = get_species(db, source_species_id)
    new_name = (new_species_name or "").strip()
    if not new_name:
        raise SpeciesError("新品种标准名不能为空")
    move_keys = {normalize_name(x) for x in move_aliases if normalize_name(x)}
    if not move_keys:
        raise SpeciesError("拆分至少要指定一个被误并的别名写法")

    rows = db.query(SpeciesName).filter(
        SpeciesName.species_id == source.id,
        SpeciesName.status == "active",
        SpeciesName.name_type == "alias",
    ).all()
    selected = [r for r in rows if r.normalized_key in move_keys]
    if len(selected) != len(move_keys):
        found = {r.normalized_key for r in selected}
        missing = [k for k in move_keys if k not in found]
        raise SpeciesError(f"以下写法不是该品种的生效别名，无法拆出：{missing}")

    new_key = normalize_name(new_name)
    if _active_name_by_key(db, new_key) and new_key not in move_keys:
        raise SpeciesConflict("新品种标准名与其它品种的生效写法冲突")
    new_species = Species(code=_generate_code(db, new_key), current_name=new_name, status="active")
    db.add(new_species)
    db.flush()

    promoted = False
    for r in selected:
        r.species_id = new_species.id
        if not promoted and (r.normalized_key == new_key or normalize_name(r.name) == new_key):
            r.name = new_name
            r.name_type = "canonical"
            promoted = True
    if not promoted:
        db.add(SpeciesName(species_id=new_species.id, name=new_name, normalized_key=new_key,
                           name_type="canonical", status="active", decided_at=_now(),
                           actor=actor, note="拆分建立的新品种标准名"))

    # 依据原文重新判定业务记录归属
    reassigned = 0
    for kind, biz_rows in _iter_business_rows(db):
        for row in biz_rows:
            raw_key = normalize_name(row.species)
            if raw_key in move_keys and row.species_code in (source.code, None):
                row.species_code = new_species.code
                reassigned += 1

    _log(db, source.id, "split", from_value=source.current_name, to_value=new_name,
         related_species_id=new_species.id,
         reason=(reason or "") + f"；拆出写法 {sorted(move_keys)}；{reassigned} 条记录改挂新品种",
         actor=actor)
    _log(db, new_species.id, "created", to_value=new_name,
         related_species_id=source.id, reason=f"从品种 {source.code} 拆分", actor=actor)
    _commit(db)
    db.refresh(new_species)
    return {"new_species_id": new_species.id, "code": new_species.code,
            "current_name": new_species.current_name, "reassigned_records": reassigned}


def merge_species(db, source_species_id: int, target_species_id: int, *,
                  reason: Optional[str] = None, actor: Optional[str] = None) -> dict:
    """显式整体合并两个**已建档**品种（人工确认同物种时使用）。

    与 approve_mapping 的区别：后者只裁定一条文本；本操作把源品种的全部生效
    名称、业务身份、未决候选整体迁入目标品种，源品种停用。任何一步名称冲突
    都需人工先拆分/更名，绝不静默强合。
    """
    source = get_species(db, source_species_id)
    target = get_species(db, target_species_id)
    if source.id == target.id:
        raise SpeciesError("不能合并品种自身")

    target_keys = {n.normalized_key for n in target.names if n.status == "active"}
    superseded_names, moved_names = [], []
    for n in db.query(SpeciesName).filter(
        SpeciesName.species_id == source.id,
        SpeciesName.status == "active",
    ).all():
        if n.normalized_key in target_keys:
            # 两个品种持有同一写法：以目标为准，源侧重复名称下线留痕
            n.status = "superseded"
            n.normalized_key = None
            n.decided_at = _now()
            n.note = (n.note or "") + f"；整体合并到 {target.code} 时与目标写法重复，下线"
            superseded_names.append(n.name)
            continue
        n.species_id = target.id
        if n.name_type == "canonical":
            n.name_type = "alias"
            n.note = (n.note or "") + f"；由品种 {source.code} 整体合并转入"
        moved_names.append(n.name)

    migrated = 0
    for model in (Pond, Batch, StockingRecord):
        rows = db.query(model).filter(model.species_code == source.code).all()
        for row in rows:
            row.species_code = target.code
            migrated += 1

    for other in db.query(SpeciesMapping).filter(
        SpeciesMapping.candidate_species_id == source.id,
        SpeciesMapping.status == "pending",
    ).all():
        other.candidate_species_id = target.id

    source.status = "inactive"
    _log(db, target.id, "species_merged", from_value=source.current_name,
         to_value=target.current_name, related_species_id=source.id,
         reason=(reason or "") + f"；迁入名称 {sorted(moved_names)}，下线重名 {sorted(superseded_names)}，"
                f"{migrated} 条业务记录改挂", actor=actor)
    _commit(db)
    return {"source_code": source.code, "target_code": target.code,
            "moved_names": moved_names, "superseded_names": superseded_names,
            "migrated_records": migrated}


# ---------------------------------------------------------------------------
# 统一身份视图
# ---------------------------------------------------------------------------

def identity_for_code(db, code: Optional[str]) -> Optional[dict]:
    if not code:
        return None
    species = db.query(Species).filter(Species.code == code).first()
    if not species:
        return {"code": code, "current_name": None, "canonical_name": None,
                "aliases": [], "historical_names": [], "pending": False,
                "status": "unknown"}
    active_aliases, historical = [], []
    for n in species.names:
        if n.status == "active" and n.name_type == "alias":
            active_aliases.append(n.name)
        elif n.status in ("superseded", "rejected"):
            historical.append(n.name)
    # 更名前的旧标准名仍是可解析别名，同时也是历史名称
    historical.extend(
        e.from_value for e in species.events
        if e.event_type == "renamed" and e.from_value
    )
    pending_count = db.query(SpeciesMapping).filter(
        SpeciesMapping.candidate_species_id == species.id,
        SpeciesMapping.status == "pending",
    ).count()
    return {
        "code": species.code,
        "current_name": species.current_name,
        "canonical_name": species.current_name,
        "aliases": sorted(set(active_aliases)),
        "historical_names": sorted(set(historical)),
        "pending": pending_count > 0,
        "pending_mapping_count": pending_count,
        "status": species.status,
    }


def identity_for_text(db, raw_text: Optional[str]) -> dict:
    """无 code 快照的旧记录用：明确返回未裁定状态与候选。"""
    key = normalize_name(raw_text)
    mapping = db.query(SpeciesMapping).filter(
        SpeciesMapping.normalized_key == key,
        SpeciesMapping.status == "pending",
    ).first() if key else None
    candidates = _fuzzy_candidates(db, key) if key and not _active_name_by_key(db, key) else []
    return {
        "code": None,
        "current_name": (raw_text or "").strip() or None,
        "canonical_name": None,
        "aliases": [],
        "historical_names": [],
        "status": "pending" if mapping else "unmapped",
        "pending": mapping is not None,
        "mapping_id": mapping.id if mapping else None,
        "candidates": candidates,
    }


def resolve_record_identity(db, code: Optional[str], raw_text: Optional[str]) -> dict:
    return identity_for_code(db, code) or identity_for_text(db, raw_text)


def overview_by_species(db) -> list:
    """跨塘口按同一身份聚合，品种不再因文本写法被拆成多行。"""
    species_rows = db.query(Species).filter(Species.code != "SP-UNRESOLVED").all()
    result = []
    for sp in species_rows:
        batch_count = db.query(Batch).filter(Batch.species_code == sp.code).count()
        pond_count = db.query(Pond).filter(Pond.species_code == sp.code).count()
        # 投苗以投苗当时冻结的 species_code 为准，与批次身份可能不同（混养补苗）
        stocking_total = db.query(func.sum(StockingRecord.quantity)).filter(
            StockingRecord.species_code == sp.code
        ).scalar() or 0
        result.append({
            "code": sp.code,
            "current_name": sp.current_name,
            "status": sp.status,
            "aliases": sorted({n.name for n in sp.names
                               if n.status == "active" and n.name_type == "alias"}),
            "historical_names": sorted({n.name for n in sp.names
                                        if n.status in ("superseded", "rejected")}),
            "batch_count": batch_count,
            "pond_count": pond_count,
            "stocking_quantity": int(stocking_total),
        })
    # 未裁定文本分组单列，避免被误算成任何品种
    unresolved = {}
    for kind, rows in _iter_business_rows(db):
        for row in rows:
            if row.species_code is not None:
                continue
            key = normalize_name(row.species)
            if not key:
                continue
            unresolved.setdefault(key, {"raw_text": (row.species or "").strip(), "count": 0})
            unresolved[key]["count"] += 1
    return result + [
        {"code": None, "current_name": v["raw_text"], "status": "pending",
         "aliases": [], "historical_names": [], "batch_count": 0, "pond_count": 0,
         "stocking_quantity": 0, "occurrence_count": v["count"]}
        for v in unresolved.values()
    ]
