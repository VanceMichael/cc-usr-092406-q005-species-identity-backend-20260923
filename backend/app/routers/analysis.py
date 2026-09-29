from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func
from typing import List, Optional
from datetime import date
from ..database import get_db
from ..models import (
    Batch, Pond, StockingRecord, FeedingRecord, CostRecord, HarvestSale,
    WaterQualityRecord, MedicationRecord, SignedAnalysis, SpeciesIdentity,
)
from ..schemas import (
    CultureCycleAnalysis, BatchTraceability, BatchInfo, PondInfo,
    SignAnalysisRequest,
)
from .. import species_service as svc

router = APIRouter(
    prefix="/api/analysis",
    tags=["养殖周期分析"]
)


def _build_cycle_payload(db, batch: Batch) -> dict:
    pond = db.query(Pond).filter(Pond.id == batch.pond_id).first()

    initial_quantity = db.query(func.sum(StockingRecord.quantity)).filter(
        StockingRecord.batch_id == batch.id
    ).scalar() or 0

    harvest_weight = db.query(func.sum(HarvestSale.weight)).filter(
        HarvestSale.batch_id == batch.id
    ).scalar() or 0

    feed_total = db.query(func.sum(FeedingRecord.feed_quantity)).filter(
        FeedingRecord.batch_id == batch.id
    ).scalar() or 0

    total_cost = db.query(func.sum(CostRecord.amount)).filter(
        CostRecord.batch_id == batch.id
    ).scalar() or 0

    total_revenue = db.query(func.sum(HarvestSale.total_amount)).filter(
        HarvestSale.batch_id == batch.id
    ).scalar() or 0

    harvest_date = batch.actual_harvest_date
    days_cultured = None
    if harvest_date:
        days_cultured = (harvest_date - batch.stocking_date).days

    survival_rate = 0
    if initial_quantity > 0 and harvest_weight > 0:
        avg_weight_per_fish = 0.5
        estimated_survival = harvest_weight / avg_weight_per_fish
        survival_rate = (estimated_survival / initial_quantity) * 100

    feed_conversion_ratio = 0
    if harvest_weight > 0 and feed_total > 0:
        feed_conversion_ratio = feed_total / harvest_weight

    yield_per_mu = 0
    if pond and pond.area > 0:
        yield_per_mu = harvest_weight / pond.area

    profit = total_revenue - total_cost

    costs = db.query(
        CostRecord.cost_type,
        func.sum(CostRecord.amount).label('total')
    ).filter(
        CostRecord.batch_id == batch.id
    ).group_by(CostRecord.cost_type).all()

    cost_breakdown = {c.cost_type: c.total for c in costs}

    known_types = ['feed', 'medicine', 'labor', 'electricity']
    other_cost = sum(
        amount for cost_type, amount in cost_breakdown.items()
        if cost_type not in known_types
    )

    cost_summary_dict = {
        "feed_cost": cost_breakdown.get('feed', 0),
        "medicine_cost": cost_breakdown.get('medicine', 0),
        "labor_cost": cost_breakdown.get('labor', 0),
        "electricity_cost": cost_breakdown.get('electricity', 0),
        "other_cost": other_cost,
        "total_cost": total_cost
    }

    feeding_summary_dict = db.query(
        FeedingRecord.feed_type,
        func.sum(FeedingRecord.feed_quantity).label('total_quantity'),
        func.count(FeedingRecord.id).label('feeding_count')
    ).filter(
        FeedingRecord.batch_id == batch.id
    ).group_by(FeedingRecord.feed_type).all()

    total_feed_weight = feed_total
    feeding_count = sum(f.feeding_count for f in feeding_summary_dict)
    avg_daily_feed = 0
    if days_cultured and days_cultured > 0:
        avg_daily_feed = total_feed_weight / days_cultured

    feeding_summary_result = {
        "total_feed_weight": total_feed_weight,
        "feeding_count": feeding_count,
        "avg_daily_feed": avg_daily_feed
    }

    return {
        "batch_number": batch.batch_number,
        "pond_name": pond.name if pond else "未知",
        # 周期分析沿用批次当时采用的名称(签署口径), 同时附统一身份视图
        "species": batch.species,
        "species_identity": svc.species_view(
            db,
            identity_id=batch.species_identity_id,
            version_id=batch.species_version_id,
            raw_text=batch.species,
        ),
        "stocking_date": batch.stocking_date,
        "harvest_date": harvest_date,
        "days_cultured": days_cultured,
        "initial_quantity": initial_quantity,
        "harvest_weight": harvest_weight,
        "survival_rate": round(survival_rate, 2),
        "feed_total": feed_total,
        "feed_conversion_ratio": round(feed_conversion_ratio, 2),
        "area": pond.area if pond else 0,
        "yield_per_mu": round(yield_per_mu, 2),
        "total_cost": total_cost,
        "total_revenue": total_revenue,
        "profit": profit,
        "cost_summary": cost_summary_dict,
        "feeding_summary": feeding_summary_result,
    }


@router.get("/cycle/{batch_id}/", response_model=CultureCycleAnalysis)
def analyze_cycle(batch_id: int, db: Session = Depends(get_db)):
    batch = db.query(Batch).filter(Batch.id == batch_id).first()
    if not batch:
        raise HTTPException(status_code=404, detail="批次不存在")
    return CultureCycleAnalysis(**_build_cycle_payload(db, batch))


@router.post("/cycle/{batch_id}/sign/")
def sign_cycle_analysis(batch_id: int, payload: SignAnalysisRequest, db: Session = Depends(get_db)):
    """签署当前周期分析。名称更正后仍可用重现接口按签署时原口径查看。"""
    batch = db.query(Batch).filter(Batch.id == batch_id).first()
    if not batch:
        raise HTTPException(status_code=404, detail="批次不存在")

    snapshot = svc.sign_analysis(
        db, batch, _build_cycle_payload(db, batch),
        signed_by=payload.signed_by, sign_reason=payload.sign_reason,
    )
    db.commit()
    db.refresh(snapshot)
    return svc.reproduce_signed_analysis(db, snapshot)


@router.get("/cycle/signed/{signed_id}/")
def reproduce_cycle_analysis(signed_id: int, db: Session = Depends(get_db)):
    """按签署时的原口径重现已签署分析(不受后续名称更正影响)。"""
    snapshot = db.get(SignedAnalysis, signed_id)
    if not snapshot:
        raise HTTPException(status_code=404, detail="已签署分析不存在")
    return svc.reproduce_signed_analysis(db, snapshot)


@router.get("/traceability/{batch_id}/", response_model=BatchTraceability)
def batch_traceability(batch_id: int, db: Session = Depends(get_db)):
    batch = db.query(Batch).filter(Batch.id == batch_id).first()
    if not batch:
        raise HTTPException(status_code=404, detail="批次不存在")

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

    def _view(row):
        return svc.species_view(
            db,
            identity_id=row.species_identity_id,
            version_id=row.species_version_id,
            raw_text=row.species,
        )

    return BatchTraceability(
        batch=BatchInfo(
            batch_number=batch.batch_number,
            species=batch.species,
            species_identity=svc.species_view(
                db,
                identity_id=batch.species_identity_id,
                version_id=batch.species_version_id,
                raw_text=batch.species,
            ),
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
                "species": r.species,
                # 追溯接口此前引用投苗名称; 现在与批次通过同一身份归并
                "species_identity": _view(r),
                "quantity": r.quantity,
                "source": r.source,
                "batch_number": r.batch_number,
                "stocking_date": r.created_at.date() if hasattr(r, 'created_at') else None
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


# ---------------------------------------------------------------------------
# 跨场区(跨塘口)比较: 按稳定品种身份聚合, 同义写法不再拆成多个品种
# ---------------------------------------------------------------------------

@router.get("/compare-by-species/")
def compare_by_species(db: Session = Depends(get_db)):
    result = []
    identities = db.query(SpeciesIdentity).filter(SpeciesIdentity.status == "active").all()

    # 解析每条批次记录到存续身份(跟随合并链)
    def resolved_identity_id(batch):
        if not batch.species_identity_id:
            return None
        root = svc.root_identity(db, batch.species_identity_id)
        return root.id if root else None

    grouped = {}
    unresolved = []
    for batch in db.query(Batch).all():
        identity_id = resolved_identity_id(batch)
        if identity_id is None:
            unresolved.append({
                "batch_number": batch.batch_number,
                "raw_species": batch.species,
                "resolution_status": svc.species_view(
                    db, identity_id=None, version_id=None, raw_text=batch.species
                )["resolution_status"],
            })
            continue
        bucket = grouped.setdefault(identity_id, {"batch_count": 0, "total_area": 0.0})
        bucket["batch_count"] += 1
        pond = db.query(Pond).filter(Pond.id == batch.pond_id).first()
        if pond:
            bucket["total_area"] += pond.area or 0

    for identity in identities:
        if identity.id not in grouped:
            continue
        version = svc.current_version(db, identity)
        result.append({
            "identity_id": identity.id,
            "identity_code": identity.code,
            "current_standard_name": version.canonical_name if version else None,
            "historical_names": svc.identity_summary(db, identity)["historical_names"],
            **grouped[identity.id],
        })

    return {"groups": result, "unresolved": unresolved}
