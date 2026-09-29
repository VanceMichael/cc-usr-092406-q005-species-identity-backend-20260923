"""品种目录与别名管理 API。

提供：品种建档/查询、别名提议与裁定、标准名更正、旧文本扫描、
未决映射队列的合并/驳回/撤回、品系拆分、审计事件与按身份汇总。

注意：所有静态路径（/mappings/、/alias-names/、/suggest/ 等）必须在
`/{species_id}/` 之前注册，否则会被整型路径参数先匹配。
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List, Optional

from ..database import get_db
from ..models import Species, SpeciesName, SpeciesMapping, SpeciesEvent
from ..schemas import (
    AliasDecision,
    AliasPropose,
    MappingApprove,
    MappingDecision,
    SpeciesCreate,
    SpeciesEventResponse,
    SpeciesMappingResponse,
    SpeciesMergeRequest,
    SpeciesNameResponse,
    SpeciesOverviewItem,
    SpeciesRename,
    SpeciesResponse,
    SpeciesSplitRequest,
    SpeciesUpdate,
    SuggestResponse,
)
from ..services import species as svc

router = APIRouter(
    prefix="/api/species",
    tags=["品种身份目录"]
)


def _species_payload(db: Session, sp: Species) -> dict:
    identity = svc.identity_for_code(db, sp.code)
    return {
        "id": sp.id,
        "code": sp.code,
        "current_name": sp.current_name,
        "scientific_name": sp.scientific_name,
        "description": sp.description,
        "status": sp.status,
        "aliases": identity["aliases"],
        "historical_names": identity["historical_names"],
        "pending_mapping_count": identity.get("pending_mapping_count", 0),
        "identity": identity,
        "created_at": sp.created_at,
        "updated_at": sp.updated_at,
    }


def _mapping_payload(m: SpeciesMapping) -> dict:
    return {
        "id": m.id,
        "raw_text": m.raw_text,
        "normalized_key": m.normalized_key,
        "candidate_species_id": m.candidate_species_id,
        "candidate_code": m.candidate_species.code if m.candidate_species else None,
        "candidate_name": m.candidate_species.current_name if m.candidate_species else None,
        "target_species_id": m.target_species_id,
        "target_code": m.target_species.code if m.target_species else None,
        "confidence": m.confidence,
        "status": m.status,
        "source": m.source,
        "occurrence_count": m.occurrence_count or 1,
        "reason": m.reason,
        "actor": m.actor,
        "created_at": m.created_at,
        "decided_at": m.decided_at,
    }


def _raise(err: svc.SpeciesError):
    raise HTTPException(status_code=err.status_code, detail=err.message)


# ---------------------------------------------------------------------------
# 目录（集合级静态路径）
# ---------------------------------------------------------------------------

@router.post("/", response_model=SpeciesResponse)
def create_species(payload: SpeciesCreate, db: Session = Depends(get_db)):
    try:
        sp = svc.create_species(
            db, payload.name, code=payload.code,
            scientific_name=payload.scientific_name,
            description=payload.description, actor=payload.actor,
        )
    except svc.SpeciesError as e:
        _raise(e)
    return _species_payload(db, sp)


@router.get("/", response_model=List[SpeciesResponse])
def list_species(include_inactive: bool = True, db: Session = Depends(get_db)):
    q = db.query(Species).filter(Species.code != "SP-UNRESOLVED")
    if not include_inactive:
        q = q.filter(Species.status == "active")
    return [_species_payload(db, sp) for sp in q.order_by(Species.id).all()]


@router.get("/overview/", response_model=List[SpeciesOverviewItem])
def species_overview(db: Session = Depends(get_db)):
    """跨塘口按同一品种身份聚合（未裁定文本单列，不并入任何品种）。"""
    return svc.overview_by_species(db)


@router.get("/suggest/", response_model=SuggestResponse)
def suggest_species(text: str, db: Session = Depends(get_db)):
    """录入前的同义写法提示：只提示，不落库、不合并。"""
    return svc.suggest(db, text)


@router.post("/scan/")
def scan_legacy(payload: Optional[MappingDecision] = None, db: Session = Depends(get_db)):
    """扫描塘口/批次/投苗旧文本：生成候选映射与未决队列，并自动回填确定项。"""
    actor = payload.actor if payload else None
    return svc.scan_legacy_texts(db, actor=actor)


# ---------------------------------------------------------------------------
# 未决映射队列（静态路径，置于 /{species_id}/ 之前）
# ---------------------------------------------------------------------------

@router.get("/mappings/", response_model=List[SpeciesMappingResponse])
def list_mappings(status: Optional[str] = None, db: Session = Depends(get_db)):
    return [_mapping_payload(m) for m in svc.list_mappings(db, status)]


@router.post("/mappings/{mapping_id}/approve", response_model=SpeciesMappingResponse)
def approve_mapping(mapping_id: int, payload: MappingApprove, db: Session = Depends(get_db)):
    """裁定合并：旧文本并入目标品种，回填历史记录，全过程留痕。"""
    try:
        m = svc.approve_mapping(db, mapping_id, payload.target_species_id,
                                reason=payload.reason, actor=payload.actor)
    except svc.SpeciesError as e:
        _raise(e)
    return _mapping_payload(m)


@router.post("/mappings/{mapping_id}/reject", response_model=SpeciesMappingResponse)
def reject_mapping(mapping_id: int, payload: Optional[MappingDecision] = None,
                   db: Session = Depends(get_db)):
    try:
        m = svc.reject_mapping(
            db, mapping_id,
            reason=payload.reason if payload else None,
            actor=payload.actor if payload else None,
        )
    except svc.SpeciesError as e:
        _raise(e)
    return _mapping_payload(m)


@router.post("/mappings/{mapping_id}/withdraw", response_model=SpeciesMappingResponse)
def withdraw_mapping(mapping_id: int, payload: Optional[MappingDecision] = None,
                     db: Session = Depends(get_db)):
    """撤回已批准合并：别名下线、身份脱钩，历史快照与审计依据保留。"""
    try:
        m = svc.withdraw_mapping(
            db, mapping_id,
            reason=payload.reason if payload else None,
            actor=payload.actor if payload else None,
        )
    except svc.SpeciesError as e:
        _raise(e)
    return _mapping_payload(m)


# ---------------------------------------------------------------------------
# 别名裁定（静态路径）
# ---------------------------------------------------------------------------

@router.post("/alias-names/{alias_name_id}/approve", response_model=SpeciesNameResponse)
def approve_alias(alias_name_id: int, payload: Optional[AliasDecision] = None,
                  db: Session = Depends(get_db)):
    try:
        row = svc.approve_alias(db, alias_name_id,
                                actor=payload.actor if payload else None)
    except svc.SpeciesError as e:
        _raise(e)
    return row


@router.post("/alias-names/{alias_name_id}/reject", response_model=SpeciesNameResponse)
def reject_alias(alias_name_id: int, payload: Optional[AliasDecision] = None,
                 db: Session = Depends(get_db)):
    try:
        row = svc.reject_alias(
            db, alias_name_id,
            reason=payload.reason if payload else None,
            actor=payload.actor if payload else None,
        )
    except svc.SpeciesError as e:
        _raise(e)
    return row


@router.post("/alias-names/{alias_name_id}/withdraw", response_model=SpeciesNameResponse)
def withdraw_alias(alias_name_id: int, payload: Optional[AliasDecision] = None,
                   db: Session = Depends(get_db)):
    try:
        row = svc.withdraw_alias(
            db, alias_name_id,
            reason=payload.reason if payload else None,
            actor=payload.actor if payload else None,
        )
    except svc.SpeciesError as e:
        _raise(e)
    return row


# ---------------------------------------------------------------------------
# 品种实体（参数路径置于静态路径之后）
# ---------------------------------------------------------------------------

@router.get("/{species_id}/", response_model=SpeciesResponse)
def get_species(species_id: int, db: Session = Depends(get_db)):
    try:
        sp = svc.get_species(db, species_id)
    except svc.SpeciesError as e:
        _raise(e)
    return _species_payload(db, sp)


@router.put("/{species_id}/", response_model=SpeciesResponse)
def update_species(species_id: int, payload: SpeciesUpdate, db: Session = Depends(get_db)):
    try:
        sp = svc.get_species(db, species_id)
    except svc.SpeciesError as e:
        _raise(e)
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(sp, key, value)
    db.commit()
    db.refresh(sp)
    return _species_payload(db, sp)


@router.post("/{species_id}/rename", response_model=SpeciesResponse)
def rename_species(species_id: int, payload: SpeciesRename, db: Session = Depends(get_db)):
    """名称更正：只改后续展示规则，历史名保留，已签署分析不受影响。"""
    try:
        sp = svc.rename_canonical(db, species_id, payload.new_name,
                                  reason=payload.reason, actor=payload.actor)
    except svc.SpeciesError as e:
        _raise(e)
    return _species_payload(db, sp)


@router.post("/{species_id}/split")
def split_species(species_id: int, payload: SpeciesSplitRequest, db: Session = Depends(get_db)):
    """把被误并的别名拆成独立品系，业务记录按原文重新归属。"""
    try:
        result = svc.split_species(
            db, species_id, payload.new_species_name, payload.move_aliases,
            reason=payload.reason, actor=payload.actor,
        )
    except svc.SpeciesError as e:
        _raise(e)
    return result


@router.post("/{source_id}/merge/{target_id}")
def merge_species(source_id: int, target_id: int, payload: Optional[SpeciesMergeRequest] = None,
                  db: Session = Depends(get_db)):
    """显式整体合并两个已建档品种（人工确认同物种）；模糊候选不会触发此操作。"""
    try:
        result = svc.merge_species(
            db, source_id, target_id,
            reason=payload.reason if payload else None,
            actor=payload.actor if payload else None,
        )
    except svc.SpeciesError as e:
        _raise(e)
    return result


@router.get("/{species_id}/events/", response_model=List[SpeciesEventResponse])
def species_events(species_id: int, db: Session = Depends(get_db)):
    try:
        svc.get_species(db, species_id)
    except svc.SpeciesError as e:
        _raise(e)
    return db.query(SpeciesEvent).filter(
        SpeciesEvent.species_id == species_id
    ).order_by(SpeciesEvent.id.desc()).all()


@router.post("/{species_id}/aliases/", response_model=SpeciesNameResponse)
def propose_alias(species_id: int, payload: AliasPropose, db: Session = Depends(get_db)):
    try:
        row = svc.propose_alias(db, species_id, payload.alias,
                                note=payload.note, actor=payload.actor)
    except svc.SpeciesError as e:
        _raise(e)
    return row


@router.get("/{species_id}/aliases/", response_model=List[SpeciesNameResponse])
def list_aliases(species_id: int, status: Optional[str] = None, db: Session = Depends(get_db)):
    q = db.query(SpeciesName).filter(SpeciesName.species_id == species_id)
    if status:
        q = q.filter(SpeciesName.status == status)
    return q.order_by(SpeciesName.id.desc()).all()
