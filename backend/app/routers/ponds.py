from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List
from ..database import get_db
from ..models import Pond
from ..schemas import PondCreate, PondUpdate, PondResponse
from ..services import species as species_svc

router = APIRouter(
    prefix="/api/ponds",
    tags=["塘口管理"]
)


def _attach_identity(db: Session, pond: Pond) -> Pond:
    """把统一品种身份挂到响应对象上；塘口允许轮养，展示当前/最近一轮身份。"""
    pond.species_identity = species_svc.resolve_record_identity(db, pond.species_code, pond.species)
    return pond


@router.post("/", response_model=PondResponse)
def create_pond(pond: PondCreate, db: Session = Depends(get_db)):
    db_pond = db.query(Pond).filter(Pond.name == pond.name).first()
    if db_pond:
        raise HTTPException(status_code=400, detail="塘口名称已存在")
    data = pond.model_dump()
    species_code = None
    if data.get("species"):
        result = species_svc.resolve_on_entry(db, data["species"], source="entry")
        species_code = result.get("species_code")
    new_pond = Pond(**data, species_code=species_code)
    db.add(new_pond)
    db.commit()
    db.refresh(new_pond)
    return _attach_identity(db, new_pond)


@router.get("/", response_model=List[PondResponse])
def get_ponds(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    ponds = db.query(Pond).offset(skip).limit(limit).all()
    return [_attach_identity(db, p) for p in ponds]


@router.get("/{pond_id}/", response_model=PondResponse)
def get_pond(pond_id: int, db: Session = Depends(get_db)):
    pond = db.query(Pond).filter(Pond.id == pond_id).first()
    if not pond:
        raise HTTPException(status_code=404, detail="塘口不存在")
    return _attach_identity(db, pond)


@router.put("/{pond_id}/", response_model=PondResponse)
def update_pond(pond_id: int, pond: PondUpdate, db: Session = Depends(get_db)):
    db_pond = db.query(Pond).filter(Pond.id == pond_id).first()
    if not db_pond:
        raise HTTPException(status_code=404, detail="塘口不存在")

    update_data = pond.model_dump(exclude_unset=True)
    # 塘口允许轮养：更换品种文本即开启新一轮，按录入解析建立当前身份
    if "species" in update_data:
        new_text = update_data.pop("species")
        db_pond.species = new_text
        if new_text:
            result = species_svc.resolve_on_entry(db, new_text, source="entry")
            db_pond.species_code = result.get("species_code")
        else:
            db_pond.species_code = None
    update_data.pop("species_code", None)  # 身份只能经解析链路写入
    for key, value in update_data.items():
        setattr(db_pond, key, value)

    db.commit()
    db.refresh(db_pond)
    return _attach_identity(db, db_pond)


@router.delete("/{pond_id}/")
def delete_pond(pond_id: int, db: Session = Depends(get_db)):
    db_pond = db.query(Pond).filter(Pond.id == pond_id).first()
    if not db_pond:
        raise HTTPException(status_code=404, detail="塘口不存在")

    db.delete(db_pond)
    db.commit()
    return {"message": "塘口删除成功"}
