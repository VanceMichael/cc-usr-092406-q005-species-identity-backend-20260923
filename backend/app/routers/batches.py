from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List
from ..database import get_db
from ..models import Batch, Pond
from ..schemas import BatchCreate, BatchUpdate, BatchResponse
from ..services import species as species_svc

router = APIRouter(
    prefix="/api/batches",
    tags=["批次管理"]
)


def _attach_identity(db: Session, batch: Batch) -> Batch:
    """批次冻结创建时品种版本：身份解析以 species_code 快照为准。"""
    batch.species_identity = species_svc.resolve_record_identity(db, batch.species_code, batch.species)
    return batch


@router.post("/", response_model=BatchResponse)
def create_batch(batch: BatchCreate, db: Session = Depends(get_db)):
    db_pond = db.query(Pond).filter(Pond.id == batch.pond_id).first()
    if not db_pond:
        raise HTTPException(status_code=404, detail="塘口不存在")

    db_batch = db.query(Batch).filter(Batch.batch_number == batch.batch_number).first()
    if db_batch:
        raise HTTPException(status_code=400, detail="批次号已存在")

    data = batch.model_dump()
    species_text = data.pop("species")
    result = species_svc.resolve_on_entry(db, species_text, source="entry")
    new_batch = Batch(**data, species=species_text, species_code=result.get("species_code"))
    db.add(new_batch)
    db.commit()
    db.refresh(new_batch)
    return _attach_identity(db, new_batch)


@router.get("/", response_model=List[BatchResponse])
def get_batches(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    batches = db.query(Batch).offset(skip).limit(limit).all()
    return [_attach_identity(db, b) for b in batches]


@router.get("/{batch_id}/", response_model=BatchResponse)
def get_batch(batch_id: int, db: Session = Depends(get_db)):
    batch = db.query(Batch).filter(Batch.id == batch_id).first()
    if not batch:
        raise HTTPException(status_code=404, detail="批次不存在")
    return _attach_identity(db, batch)


@router.get("/by-number/{batch_number}/", response_model=BatchResponse)
def get_batch_by_number(batch_number: str, db: Session = Depends(get_db)):
    batch = db.query(Batch).filter(Batch.batch_number == batch_number).first()
    if not batch:
        raise HTTPException(status_code=404, detail="批次不存在")
    return _attach_identity(db, batch)


@router.put("/{batch_id}/", response_model=BatchResponse)
def update_batch(batch_id: int, batch: BatchUpdate, db: Session = Depends(get_db)):
    db_batch = db.query(Batch).filter(Batch.id == batch_id).first()
    if not db_batch:
        raise HTTPException(status_code=404, detail="批次不存在")

    update_data = batch.model_dump(exclude_unset=True)
    # 已创建批次必须保留当时采用的品种版本，品种更正不允许改写历史批次
    if update_data.get("species") not in (None, db_batch.species):
        raise HTTPException(
            status_code=409,
            detail="批次品种版本在创建时已冻结，不能改写；名称展示由品种目录统一裁定"
        )
    update_data.pop("species", None)
    update_data.pop("species_code", None)
    for key, value in update_data.items():
        setattr(db_batch, key, value)

    db.commit()
    db.refresh(db_batch)
    return _attach_identity(db, db_batch)


@router.delete("/{batch_id}/")
def delete_batch(batch_id: int, db: Session = Depends(get_db)):
    db_batch = db.query(Batch).filter(Batch.id == batch_id).first()
    if not db_batch:
        raise HTTPException(status_code=404, detail="批次不存在")

    db.delete(db_batch)
    db.commit()
    return {"message": "批次删除成功"}
