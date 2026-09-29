"""品种目录与别名管理路由。"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from typing import List, Optional

from ..database import get_db
from ..models import (
    SpeciesIdentity, SpeciesNameVersion, SpeciesAlias,
    SpeciesMappingDecision, LegacySpeciesText,
)
from ..schemas import (
    SpeciesIdentityCreate, SpeciesNameCorrect, SpeciesAliasPropose,
    SpeciesAliasAdjudicate, SpeciesMergeRequest, SpeciesSplitRequest,
    SpeciesLegacyDecision, SpeciesLegacyWithdraw, SpeciesResolveRequest,
    SpeciesIdentityOut, SpeciesNameVersionOut, SpeciesAliasOut,
    SpeciesDecisionOut, SpeciesLegacyTextOut, SpeciesResolutionOut,
    SpeciesCandidateOut,
)
from .. import species_service as svc

router = APIRouter(
    prefix="/api/species",
    tags=["品种身份"]
)


# ---------------------------------------------------------------------------
# 录入提示: 不写库地解析一段文本(写库版由塘口/批次/投苗创建接口内部完成)
# ---------------------------------------------------------------------------

@router.post("/resolve/", response_model=SpeciesResolutionOut)
def resolve_text(payload: SpeciesResolveRequest, db: Session = Depends(get_db)):
    """录入时提示同义写法: 命中则返回身份; 未命中返回候选并入未决队列, 绝不自动合并。"""
    result = svc.resolve_species_text(db, payload.text, payload.source_table)
    db.commit()
    version = result.version
    return SpeciesResolutionOut(
        raw_text=result.raw_text,
        normalized_text=result.normalized_text,
        status=result.status,
        matched=result.matched,
        identity_id=result.identity.id if result.identity else None,
        identity_code=result.identity.code if result.identity else None,
        canonical_name=version.canonical_name if version else None,
        legacy_text_id=result.legacy_text.id if result.legacy_text else None,
        candidates=[SpeciesCandidateOut(**c.__dict__) for c in result.candidates],
    )


# ---------------------------------------------------------------------------
# 品种目录
# ---------------------------------------------------------------------------

@router.post("/identities/", response_model=SpeciesIdentityOut)
def create_identity(payload: SpeciesIdentityCreate, db: Session = Depends(get_db)):
    identity = svc.create_identity(
        db, payload.canonical_name, created_by=payload.created_by, reason=payload.reason
    )
    db.commit()
    db.refresh(identity)
    return _identity_out(db, identity)


@router.get("/identities/", response_model=List[SpeciesIdentityOut])
def list_identities(include_merged: bool = False, db: Session = Depends(get_db)):
    query = db.query(SpeciesIdentity)
    if not include_merged:
        query = query.filter(SpeciesIdentity.status == "active")
    identities = query.order_by(SpeciesIdentity.id).all()
    return [_identity_out(db, i) for i in identities]


@router.get("/identities/{identity_id}/", response_model=SpeciesIdentityOut)
def get_identity(identity_id: int, db: Session = Depends(get_db)):
    identity = db.get(SpeciesIdentity, identity_id)
    if identity is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="品种身份不存在")
    return _identity_out(db, identity)


@router.get("/identities/{identity_id}/versions/", response_model=List[SpeciesNameVersionOut])
def list_versions(identity_id: int, db: Session = Depends(get_db)):
    identity = db.get(SpeciesIdentity, identity_id)
    if identity is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="品种身份不存在")
    return (
        db.query(SpeciesNameVersion)
        .filter(SpeciesNameVersion.identity_id == identity_id)
        .order_by(SpeciesNameVersion.version_no)
        .all()
    )


@router.post("/identities/{identity_id}/correct-name/", response_model=SpeciesIdentityOut)
def correct_name(identity_id: int, payload: SpeciesNameCorrect, db: Session = Depends(get_db)):
    """名称更正: 只改变后续展示规则, 历史批次/投苗/已签署分析仍按原版本重现。"""
    identity = svc.correct_name(
        db, identity_id, payload.new_name,
        reason=payload.reason, changed_by=payload.changed_by,
    )
    db.commit()
    db.refresh(identity)
    return _identity_out(db, identity)


# ---------------------------------------------------------------------------
# 别名: 提议 / 列表 / 裁定(乐观锁)
# ---------------------------------------------------------------------------

@router.post("/identities/{identity_id}/aliases/", response_model=SpeciesAliasOut)
def propose_alias(identity_id: int, payload: SpeciesAliasPropose, db: Session = Depends(get_db)):
    alias = svc.propose_alias(
        db, identity_id, payload.alias_text,
        proposed_by=payload.proposed_by, reason=payload.reason,
    )
    db.commit()
    db.refresh(alias)
    return alias


@router.get("/aliases/", response_model=List[SpeciesAliasOut])
def list_aliases(status: Optional[str] = None, identity_id: Optional[int] = None,
                 db: Session = Depends(get_db)):
    query = db.query(SpeciesAlias)
    if status:
        query = query.filter(SpeciesAlias.status == status)
    if identity_id:
        query = query.filter(SpeciesAlias.identity_id == identity_id)
    return query.order_by(SpeciesAlias.id).all()


@router.post("/aliases/{alias_id}/adjudicate/", response_model=SpeciesAliasOut)
def adjudicate_alias(alias_id: int, payload: SpeciesAliasAdjudicate, db: Session = Depends(get_db)):
    """批准/驳回/撤回别名。并发时乐观锁保证只有一个版本生效(冲突返回409)。"""
    alias = svc.adjudicate_alias(
        db, alias_id, payload.action,
        reviewed_by=payload.reviewed_by, reason=payload.reason,
        expected_lock_version=payload.expected_lock_version,
    )
    db.commit()
    db.refresh(alias)
    return alias


# ---------------------------------------------------------------------------
# 显式合并 / 拆分 —— 只有人工显式操作才会改变身份归并
# ---------------------------------------------------------------------------

@router.post("/merge/", response_model=SpeciesIdentityOut)
def merge_identities(payload: SpeciesMergeRequest, db: Session = Depends(get_db)):
    target = svc.merge_identities(
        db, payload.source_identity_id, payload.target_identity_id,
        rationale=payload.rationale, decided_by=payload.decided_by,
    )
    db.commit()
    db.refresh(target)
    return _identity_out(db, target)


@router.post("/split/", response_model=SpeciesIdentityOut)
def split_identity(payload: SpeciesSplitRequest, db: Session = Depends(get_db)):
    identity = svc.split_identity(
        db, payload.from_identity_id, payload.new_canonical_name,
        payload.move_alias_texts, rationale=payload.rationale, decided_by=payload.decided_by,
    )
    db.commit()
    db.refresh(identity)
    return _identity_out(db, identity)


# ---------------------------------------------------------------------------
# 未决队列: 扫描 / 列表 / 裁定映射 / 撤回
# ---------------------------------------------------------------------------

@router.post("/legacy/scan/")
def scan_legacy(db: Session = Depends(get_db)):
    stats = svc.scan_legacy_texts(db)
    db.commit()
    return stats


@router.get("/legacy/", response_model=List[SpeciesLegacyTextOut])
def list_legacy(status: Optional[str] = "pending", db: Session = Depends(get_db)):
    query = db.query(LegacySpeciesText)
    if status and status != "all":
        query = query.filter(LegacySpeciesText.status == status)
    rows = query.order_by(
        LegacySpeciesText.occurrence_count.desc(), LegacySpeciesText.id
    ).all()
    result = []
    for row in rows:
        out = SpeciesLegacyTextOut.model_validate(row).model_copy()
        if row.status == "pending":
            out.candidates = [
                SpeciesCandidateOut(**c.__dict__)
                for c in svc.suggest_candidates(db, row.normalized_text)
            ]
        result.append(out)
    return result


@router.post("/legacy/{legacy_text_id}/decide/", response_model=SpeciesLegacyTextOut)
def decide_legacy(legacy_text_id: int, payload: SpeciesLegacyDecision,
                  db: Session = Depends(get_db)):
    legacy = svc.decide_legacy_mapping(
        db, legacy_text_id, payload.action,
        target_identity_id=payload.target_identity_id,
        decided_by=payload.decided_by, rationale=payload.rationale,
    )
    db.commit()
    db.refresh(legacy)
    return legacy


@router.post("/legacy/{legacy_text_id}/withdraw/", response_model=SpeciesLegacyTextOut)
def withdraw_legacy(legacy_text_id: int, payload: SpeciesLegacyWithdraw,
                    db: Session = Depends(get_db)):
    legacy = svc.withdraw_legacy_mapping(
        db, legacy_text_id, decided_by=payload.decided_by, rationale=payload.rationale
    )
    db.commit()
    db.refresh(legacy)
    return legacy


# ---------------------------------------------------------------------------
# 决策依据流水
# ---------------------------------------------------------------------------

@router.get("/decisions/", response_model=List[SpeciesDecisionOut])
def list_decisions(limit: int = 100, db: Session = Depends(get_db)):
    return (
        db.query(SpeciesMappingDecision)
        .order_by(SpeciesMappingDecision.id.desc())
        .limit(limit)
        .all()
    )


def _identity_out(db, identity: SpeciesIdentity) -> SpeciesIdentityOut:
    summary = svc.identity_summary(db, identity)
    return SpeciesIdentityOut(
        id=identity.id,
        code=identity.code,
        status=identity.status,
        current_version_id=identity.current_version_id,
        merged_into_id=identity.merged_into_id,
        created_at=identity.created_at,
        current_standard_name=summary["current_standard_name"],
        historical_names=summary["historical_names"],
        aliases=summary["aliases"],
    )
