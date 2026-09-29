from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List
from ..database import get_db
from ..models import (
    Batch, Pond, StockingRecord, FeedingRecord, CostRecord,
    HarvestSale, WaterQualityRecord, MedicationRecord,
)
from ..schemas import (
    CultureCycleAnalysis, BatchTraceability, BatchInfo, PondInfo, SignCycleRequest,
)
from ..services import cycle as cycle_svc
from ..services import species as species_svc

router = APIRouter(
    prefix="/api/analysis",
    tags=["养殖周期分析"]
)


def _get_batch_or_404(db: Session, batch_id: int) -> Batch:
    batch = db.query(Batch).filter(Batch.id == batch_id).first()
    if not batch:
        raise HTTPException(status_code=404, detail="批次不存在")
    return batch


@router.get("/cycle/{batch_id}/", response_model=CultureCycleAnalysis)
def analyze_cycle(batch_id: int, db: Session = Depends(get_db)):
    """按当前数据与品种目录当前标准名展示周期分析。

    批次与投苗的 species_code 快照不变；这里仅替换展示口径，
    并同时回传历史名称、未决状态，供前端明确区分。
    """
    batch = _get_batch_or_404(db, batch_id)
    return cycle_svc.compute_cycle(db, batch)


@router.post("/cycle/{batch_id}/sign", response_model=CultureCycleAnalysis)
def sign_cycle(batch_id: int, payload: SignCycleRequest = None, db: Session = Depends(get_db)):
    """签署当前周期分析：品种口径随快照固化，此后更名/合并/拆分仍按原口径重现。"""
    batch = _get_batch_or_404(db, batch_id)
    signer = payload.signer if payload else None
    return cycle_svc.sign_cycle(db, batch, signer=signer)


@router.get("/cycle/{batch_id}/signed", response_model=CultureCycleAnalysis)
def get_signed_cycle(batch_id: int, db: Session = Depends(get_db)):
    """按签署时的原口径重现已签署分析。"""
    batch = _get_batch_or_404(db, batch_id)
    try:
        return cycle_svc.get_signed(db, batch)
    except cycle_svc.CycleError as e:
        raise HTTPException(status_code=e.status_code, detail=e.message)


@router.get("/traceability/{batch_id}/", response_model=BatchTraceability)
def batch_traceability(batch_id: int, db: Session = Depends(get_db)):
    batch = _get_batch_or_404(db, batch_id)
    pond = db.query(Pond).filter(Pond.id == batch.pond_id).first()

    stocking_records = db.query(StockingRecord).filter(
        StockingRecord.batch_id == batch.id
    ).all()
    feeding_records = db.query(FeedingRecord).filter(
        FeedingRecord.batch_id == batch.id
    ).all()
    water_quality_records = db.query(WaterQualityRecord).filter(
        WaterQualityRecord.batch_id == batch.id
    ).all()
    medication_records = db.query(MedicationRecord).filter(
        MedicationRecord.batch_id == batch.id
    ).all()
    cost_records = db.query(CostRecord).filter(
        CostRecord.batch_id == batch.id
    ).all()
    harvest_sales = db.query(HarvestSale).filter(
        HarvestSale.batch_id == batch.id
    ).all()

    batch_identity = species_svc.resolve_record_identity(db, batch.species_code, batch.species)

    return BatchTraceability(
        batch=BatchInfo(
            batch_number=batch.batch_number,
            species=batch_identity.get("current_name") or batch.species,
            species_code=batch_identity.get("code"),
            species_identity=batch_identity,
            recorded_species_name=batch.species,
            stocking_date=batch.stocking_date,
            harvest_date=batch.actual_harvest_date,
            status=batch.status,
            pond_id=batch.pond_id
        ),
        pond_info=PondInfo(
            name=pond.name if pond else None,
            area=pond.area if pond else None,
            water_depth=pond.water_depth if pond else None
        ),
        stocking_records=[
            {
                "species": (identity := species_svc.resolve_record_identity(
                    db, r.species_code, r.species)).get("current_name") or r.species,
                "species_code": identity.get("code"),
                "species_identity": identity,
                "recorded_species_name": r.species,
                "quantity": r.quantity,
                "source": r.source,
                "batch_number": r.batch_number,
                "stocking_date": r.created_at.date() if r.created_at else None
            } for r in stocking_records
        ],
        feeding_records=[
            {
                "feeding_date": r.feeding_date,
                "feed_type": r.feed_type,
                "quantity": r.feed_quantity,
                "unit": "kg"
            } for r in feeding_records
        ],
        water_quality_records=[
            {
                "record_date": r.record_date,
                "water_temperature": r.water_temperature,
                "ph_value": r.ph_value,
                "dissolved_oxygen": r.dissolved_oxygen
            } for r in water_quality_records
        ],
        medication_records=[
            {
                "medication_date": r.medication_date,
                "medication_name": r.drug_name,
                "dosage": r.dosage,
                "unit": r.dosage_unit
            } for r in medication_records
        ],
        cost_records=[
            {
                "cost_date": r.cost_date,
                "cost_type": r.cost_type,
                "amount": r.amount,
                "description": r.description
            } for r in cost_records
        ],
        harvest_sales=[
            {
                "sale_date": r.sale_date,
                "weight": r.weight,
                "unit_price": r.unit_price,
                "total_amount": r.total_amount,
                "buyer": r.buyer
            } for r in harvest_sales
        ]
    )

@router.get("/trace-by-number/{batch_number}/", response_model=BatchTraceability)
def trace_by_batch_number(batch_number: str, db: Session = Depends(get_db)):
    batch = db.query(Batch).filter(Batch.batch_number == batch_number).first()
    if not batch:
        raise HTTPException(status_code=404, detail=f"批次号 {batch_number} 不存在")
    return batch_traceability(batch.id, db)
