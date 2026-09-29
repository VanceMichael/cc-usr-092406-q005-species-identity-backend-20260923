from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List
from ..database import get_db
from ..models import Batch, Pond
from ..schemas import BatchCreate, BatchUpdate, BatchResponse
from .. import species_service as svc

router = APIRouter(
    prefix="/api/batches",
    tags=["批次管理"]
)

@router.post("/", response_model=BatchResponse)
def create_batch(batch: BatchCreate, db: Session = Depends(get_db)):
    db_pond = db.query(Pond).filter(Pond.id == batch.pond_id).first()
    if not db_pond:
        raise HTTPException(status_code=404, detail="塘口不存在")

    db_batch = db.query(Batch).filter(Batch.batch_number == batch.batch_number).first()
    if db_batch:
        raise HTTPException(status_code=400, detail="批次号已存在")

    data = batch.dict()
    raw_species = data.pop("species")

    # 先解析品种文本(命中即关联, 未命中入未决队列), 再落库; 快照当时采用的名称版本,
    # 后续名称更正不影响该批次的原口径
    resolution = svc.resolve_species_text(db, raw_species, "batches", register=True)
    new_batch = Batch(
        **data,
        species=raw_species,
        species_identity_id=resolution.identity.id if resolution.identity else None,
        species_version_id=resolution.version.id if resolution.version else None,
    )
    db.add(new_batch)
    db.commit()
    db.refresh(new_batch)
    svc.attach_species_view(db, new_batch)
    return new_batch

@router.get("/", response_model=List[BatchResponse])
def get_batches(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    batches = db.query(Batch).offset(skip).limit(limit).all()
    for batch in batches:
        svc.attach_species_view(db, batch)
    return batches

@router.get("/{batch_id}/", response_model=BatchResponse)
def get_batch(batch_id: int, db: Session = Depends(get_db)):
    batch = db.query(Batch).filter(Batch.id == batch_id).first()
    if not batch:
        raise HTTPException(status_code=404, detail="批次不存在")
    svc.attach_species_view(db, batch)
    return batch

@router.get("/by-number/{batch_number}/", response_model=BatchResponse)
def get_batch_by_number(batch_number: str, db: Session = Depends(get_db)):
    batch = db.query(Batch).filter(Batch.batch_number == batch_number).first()
    if not batch:
        raise HTTPException(status_code=404, detail="批次不存在")
    svc.attach_species_view(db, batch)
    return batch

@router.put("/{batch_id}/", response_model=BatchResponse)
def update_batch(batch_id: int, batch: BatchUpdate, db: Session = Depends(get_db)):
    db_batch = db.query(Batch).filter(Batch.id == batch_id).first()
    if not db_batch:
        raise HTTPException(status_code=404, detail="批次不存在")

    update_data = batch.dict(exclude_unset=True)

    # 批次必须保留创建时采用的品种版本; 展示名称更正请走品种目录,
    # 错误文本请在未决队列中裁定, 而不是改写批次
    if "species" in update_data:
        raise HTTPException(
            status_code=409,
            detail="批次品种在创建后冻结以保留当时口径; 如需更正名称请使用品种目录的名称更正或未决队列裁定",
        )

    for key, value in update_data.items():
        setattr(db_batch, key, value)

    db.commit()
    db.refresh(db_batch)
    svc.attach_species_view(db, db_batch)
    return db_batch

@router.delete("/{batch_id}/")
def delete_batch(batch_id: int, db: Session = Depends(get_db)):
    db_batch = db.query(Batch).filter(Batch.id == batch_id).first()
    if not db_batch:
        raise HTTPException(status_code=404, detail="批次不存在")

    db.delete(db_batch)
    db.commit()
    return {"message": "批次删除成功"}
