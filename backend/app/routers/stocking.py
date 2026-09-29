from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List
from ..database import get_db
from ..models import StockingRecord, Batch
from ..schemas import StockingRecordCreate, StockingRecordUpdate, StockingRecordResponse
from .. import species_service as svc

router = APIRouter(
    prefix="/api/stocking-records",
    tags=["投苗记录"]
)

@router.post("/", response_model=StockingRecordResponse)
def create_stocking_record(record: StockingRecordCreate, db: Session = Depends(get_db)):
    db_batch = db.query(Batch).filter(Batch.id == record.batch_id).first()
    if not db_batch:
        raise HTTPException(status_code=404, detail="批次不存在")

    data = record.dict()
    raw_species = data.pop("species")

    # 投苗名称与批次名可能不一致, 各自保留当时采用的品种版本,
    # 但二者最终通过同一身份归并
    resolution = svc.resolve_species_text(db, raw_species, "stocking_records", register=True)
    new_record = StockingRecord(
        **data,
        species=raw_species,
        species_identity_id=resolution.identity.id if resolution.identity else None,
        species_version_id=resolution.version.id if resolution.version else None,
    )
    db.add(new_record)
    db.commit()
    db.refresh(new_record)
    svc.attach_species_view(db, new_record)
    return new_record

@router.get("/", response_model=List[StockingRecordResponse])
def get_stocking_records(skip: int = 0, limit: int = 100, batch_id: int = None, db: Session = Depends(get_db)):
    query = db.query(StockingRecord)
    if batch_id:
        query = query.filter(StockingRecord.batch_id == batch_id)
    records = query.offset(skip).limit(limit).all()
    for record in records:
        svc.attach_species_view(db, record)
    return records

@router.get("/{record_id}/", response_model=StockingRecordResponse)
def get_stocking_record(record_id: int, db: Session = Depends(get_db)):
    record = db.query(StockingRecord).filter(StockingRecord.id == record_id).first()
    if not record:
        raise HTTPException(status_code=404, detail="投苗记录不存在")
    svc.attach_species_view(db, record)
    return record

@router.put("/{record_id}/", response_model=StockingRecordResponse)
def update_stocking_record(record_id: int, record: StockingRecordUpdate, db: Session = Depends(get_db)):
    db_record = db.query(StockingRecord).filter(StockingRecord.id == record_id).first()
    if not db_record:
        raise HTTPException(status_code=404, detail="投苗记录不存在")

    update_data = record.dict(exclude_unset=True)

    # 已发生的投苗必须保留当时采用的品种版本, 更正请走目录名称更正或未决裁定
    if "species" in update_data:
        raise HTTPException(
            status_code=409,
            detail="投苗记录品种在创建后冻结以保留当时口径; 如需更正请使用品种目录的名称更正或未决队列裁定",
        )

    for key, value in update_data.items():
        setattr(db_record, key, value)

    db.commit()
    db.refresh(db_record)
    svc.attach_species_view(db, db_record)
    return db_record

@router.delete("/{record_id}/")
def delete_stocking_record(record_id: int, db: Session = Depends(get_db)):
    db_record = db.query(StockingRecord).filter(StockingRecord.id == record_id).first()
    if not db_record:
        raise HTTPException(status_code=404, detail="投苗记录不存在")

    db.delete(db_record)
    db.commit()
    return {"message": "投苗记录删除成功"}
