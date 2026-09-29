from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List
from ..database import get_db
from ..models import StockingRecord, Batch
from ..schemas import StockingRecordCreate, StockingRecordUpdate, StockingRecordResponse
from ..services import species as species_svc

router = APIRouter(
    prefix="/api/stocking-records",
    tags=["投苗记录"]
)


def _attach_identity(db: Session, record: StockingRecord) -> StockingRecord:
    """投苗记录冻结投苗当时的品种版本。"""
    record.species_identity = species_svc.resolve_record_identity(
        db, record.species_code, record.species
    )
    return record


@router.post("/", response_model=StockingRecordResponse)
def create_stocking_record(record: StockingRecordCreate, db: Session = Depends(get_db)):
    db_batch = db.query(Batch).filter(Batch.id == record.batch_id).first()
    if not db_batch:
        raise HTTPException(status_code=404, detail="批次不存在")

    data = record.model_dump()
    species_text = data.pop("species")
    result = species_svc.resolve_on_entry(db, species_text, source="entry")
    new_record = StockingRecord(**data, species=species_text,
                                species_code=result.get("species_code"))
    db.add(new_record)
    db.commit()
    db.refresh(new_record)
    return _attach_identity(db, new_record)


@router.get("/", response_model=List[StockingRecordResponse])
def get_stocking_records(skip: int = 0, limit: int = 100, batch_id: int = None,
                         db: Session = Depends(get_db)):
    query = db.query(StockingRecord)
    if batch_id:
        query = query.filter(StockingRecord.batch_id == batch_id)
    records = query.offset(skip).limit(limit).all()
    return [_attach_identity(db, r) for r in records]


@router.get("/{record_id}/", response_model=StockingRecordResponse)
def get_stocking_record(record_id: int, db: Session = Depends(get_db)):
    record = db.query(StockingRecord).filter(StockingRecord.id == record_id).first()
    if not record:
        raise HTTPException(status_code=404, detail="投苗记录不存在")
    return _attach_identity(db, record)


@router.put("/{record_id}/", response_model=StockingRecordResponse)
def update_stocking_record(record_id: int, record: StockingRecordUpdate,
                           db: Session = Depends(get_db)):
    db_record = db.query(StockingRecord).filter(StockingRecord.id == record_id).first()
    if not db_record:
        raise HTTPException(status_code=404, detail="投苗记录不存在")

    update_data = record.model_dump(exclude_unset=True)
    # 已发生的投苗必须保留当时采用的品种版本
    if update_data.get("species") not in (None, db_record.species):
        raise HTTPException(
            status_code=409,
            detail="投苗记录的品种版本已冻结，不能改写；如需统一展示请在品种目录裁定别名/映射"
        )
    update_data.pop("species", None)
    update_data.pop("species_code", None)
    for key, value in update_data.items():
        setattr(db_record, key, value)

    db.commit()
    db.refresh(db_record)
    return _attach_identity(db, db_record)


@router.delete("/{record_id}/")
def delete_stocking_record(record_id: int, db: Session = Depends(get_db)):
    db_record = db.query(StockingRecord).filter(StockingRecord.id == record_id).first()
    if not db_record:
        raise HTTPException(status_code=404, detail="投苗记录不存在")

    db.delete(db_record)
    db.commit()
    return {"message": "投苗记录删除成功"}
