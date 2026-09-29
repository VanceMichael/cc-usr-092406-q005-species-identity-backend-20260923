from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List
from ..database import get_db
from ..models import Pond, PondSpeciesHistory
from ..schemas import PondCreate, PondUpdate, PondResponse
from .. import species_service as svc

router = APIRouter(
    prefix="/api/ponds",
    tags=["塘口管理"]
)

def _apply_species(db, pond, raw_species):
    resolution = svc.resolve_species_text(db, raw_species, "ponds", register=True)
    pond.species = raw_species
    pond.species_identity_id = resolution.identity.id if resolution.identity else None
    pond.species_version_id = resolution.version.id if resolution.version else None
    return resolution

@router.post("/", response_model=PondResponse)
def create_pond(pond: PondCreate, db: Session = Depends(get_db)):
    db_pond = db.query(Pond).filter(Pond.name == pond.name).first()
    if db_pond:
        raise HTTPException(status_code=400, detail="塘口名称已存在")
    data = pond.dict()
    raw_species = data.pop("species", None)
    new_pond = Pond(**data)
    db.add(new_pond)
    db.flush()

    if raw_species:
        resolution = _apply_species(db, new_pond, raw_species)
        db.add(PondSpeciesHistory(
            pond_id=new_pond.id,
            species_text=raw_species,
            species_identity_id=resolution.identity.id if resolution.identity else None,
            species_version_id=resolution.version.id if resolution.version else None,
            change_reason="建塘初始品种",
        ))

    db.commit()
    db.refresh(new_pond)
    svc.attach_species_view(db, new_pond)
    return new_pond

@router.get("/", response_model=List[PondResponse])
def get_ponds(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    ponds = db.query(Pond).offset(skip).limit(limit).all()
    for pond in ponds:
        svc.attach_species_view(db, pond)
    return ponds

@router.get("/{pond_id}/", response_model=PondResponse)
def get_pond(pond_id: int, db: Session = Depends(get_db)):
    pond = db.query(Pond).filter(Pond.id == pond_id).first()
    if not pond:
        raise HTTPException(status_code=404, detail="塘口不存在")
    svc.attach_species_view(db, pond)
    return pond

@router.get("/{pond_id}/species-history/")
def get_pond_species_history(pond_id: int, db: Session = Depends(get_db)):
    pond = db.query(Pond).filter(Pond.id == pond_id).first()
    if not pond:
        raise HTTPException(status_code=404, detail="塘口不存在")
    return [
        {
            "id": h.id,
            "species_text": h.species_text,
            "species_identity_id": h.species_identity_id,
            "species_version_id": h.species_version_id,
            "change_reason": h.change_reason,
            "changed_by": h.changed_by,
            "started_at": h.started_at,
        }
        for h in pond.species_history
    ]

@router.put("/{pond_id}/", response_model=PondResponse)
def update_pond(pond_id: int, pond: PondUpdate, db: Session = Depends(get_db)):
    db_pond = db.query(Pond).filter(Pond.id == pond_id).first()
    if not db_pond:
        raise HTTPException(status_code=404, detail="塘口不存在")

    update_data = pond.dict(exclude_unset=True)
    new_species = update_data.pop("species", None)
    for key, value in update_data.items():
        setattr(db_pond, key, value)

    # 塘口允许轮养: 更换品种追加轮养历史, 不覆盖历史
    if new_species is not None and (
        db_pond.species is None
        or svc.normalize_name(new_species) != svc.normalize_name(db_pond.species)
    ):
        resolution = _apply_species(db, db_pond, new_species)
        db.add(PondSpeciesHistory(
            pond_id=db_pond.id,
            species_text=new_species,
            species_identity_id=resolution.identity.id if resolution.identity else None,
            species_version_id=resolution.version.id if resolution.version else None,
            change_reason="塘口轮养/品种更正",
        ))

    db.commit()
    db.refresh(db_pond)
    svc.attach_species_view(db, db_pond)
    return db_pond

@router.delete("/{pond_id}/")
def delete_pond(pond_id: int, db: Session = Depends(get_db)):
    db_pond = db.query(Pond).filter(Pond.id == pond_id).first()
    if not db_pond:
        raise HTTPException(status_code=404, detail="塘口不存在")

    db.delete(db_pond)
    db.commit()
    return {"message": "塘口删除成功"}
