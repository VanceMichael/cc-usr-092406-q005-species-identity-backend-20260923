"""养殖周期分析的计算与签署。

签署(sign)把当时的计算结果连同品种口径名整体快照化；此后品种更名、合并或拆分
都不会改变已签署快照，GET signed 接口始终按原口径重现。
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..models import (
    Batch,
    CostRecord,
    CycleSignature,
    FeedingRecord,
    HarvestSale,
    Pond,
    StockingRecord,
)
from . import species as species_service


class CycleError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def compute_cycle(db: Session, batch: Batch) -> dict:
    """按当前数据计算周期分析。品种展示名取批次身份的当前标准名。"""
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
    days_cultured = (harvest_date - batch.stocking_date).days if harvest_date else None

    survival_rate = 0.0
    if initial_quantity > 0 and harvest_weight > 0:
        avg_weight_per_fish = 0.5
        estimated_survival = harvest_weight / avg_weight_per_fish
        survival_rate = (estimated_survival / initial_quantity) * 100

    feed_conversion_ratio = feed_total / harvest_weight if harvest_weight > 0 and feed_total > 0 else 0
    yield_per_mu = harvest_weight / pond.area if pond and pond.area > 0 else 0

    costs = db.query(
        CostRecord.cost_type,
        func.sum(CostRecord.amount).label("total"),
    ).filter(CostRecord.batch_id == batch.id).group_by(CostRecord.cost_type).all()
    cost_breakdown = {c.cost_type: c.total for c in costs}
    known_types = ["feed", "medicine", "labor", "electricity"]
    other_cost = sum(amount for cost_type, amount in cost_breakdown.items()
                     if cost_type not in known_types)
    cost_summary = {
        "feed_cost": cost_breakdown.get("feed", 0),
        "medicine_cost": cost_breakdown.get("medicine", 0),
        "labor_cost": cost_breakdown.get("labor", 0),
        "electricity_cost": cost_breakdown.get("electricity", 0),
        "other_cost": other_cost,
        "total_cost": total_cost,
    }

    feeding_rows = db.query(
        FeedingRecord.feed_type,
        func.sum(FeedingRecord.feed_quantity).label("total_quantity"),
        func.count(FeedingRecord.id).label("feeding_count"),
    ).filter(FeedingRecord.batch_id == batch.id).group_by(FeedingRecord.feed_type).all()
    feeding_count = sum(f.feeding_count for f in feeding_rows)
    avg_daily_feed = (feed_total / days_cultured) if days_cultured and days_cultured > 0 else 0
    feeding_summary = {
        "total_feed_weight": feed_total,
        "feeding_count": feeding_count,
        "avg_daily_feed": avg_daily_feed,
    }

    identity = species_service.resolve_record_identity(db, batch.species_code, batch.species)
    display_name = identity.get("current_name") or batch.species

    return {
        "batch_number": batch.batch_number,
        "pond_name": pond.name if pond else "未知",
        "species": display_name,
        "species_code": identity.get("code"),
        "recorded_species_name": batch.species,
        "species_identity": identity,
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
        "profit": total_revenue - total_cost,
        "cost_summary": cost_summary,
        "feeding_summary": feeding_summary,
    }


def sign_cycle(db: Session, batch: Batch, signer: Optional[str] = None) -> dict:
    """签署当前周期分析：把当前口径固化成快照，重复签署返回既有快照。"""
    existing = db.query(CycleSignature).filter(CycleSignature.batch_id == batch.id).first()
    if existing:
        payload = json.loads(existing.snapshot)
        payload["signed"] = True
        payload["signed_at"] = existing.signed_at
        payload["signer"] = existing.signer
        return payload

    analysis = compute_cycle(db, batch)
    signature = CycleSignature(
        batch_id=batch.id,
        signed_batch_number=batch.batch_number,
        signed_species_code=analysis.get("species_code"),
        signed_species_name=analysis["species"],
        snapshot=json.dumps(analysis, ensure_ascii=False, default=_json_default),
        signer=signer,
    )
    db.add(signature)
    db.commit()
    db.refresh(signature)
    payload = json.loads(signature.snapshot)
    payload["signed"] = True
    payload["signed_at"] = signature.signed_at
    payload["signer"] = signature.signer
    return payload


def get_signed(db: Session, batch: Batch) -> dict:
    signature = db.query(CycleSignature).filter(CycleSignature.batch_id == batch.id).first()
    if not signature:
        raise CycleError("该批次尚无已签署分析", 404)
    payload = json.loads(signature.snapshot)
    payload["signed"] = True
    payload["signed_at"] = signature.signed_at
    payload["signer"] = signature.signer
    payload["signed_species_name"] = signature.signed_species_name
    return payload


def _json_default(obj):
    if isinstance(obj, (datetime,)):
        return obj.isoformat()
    if hasattr(obj, "isoformat"):  # date
        return obj.isoformat()
    raise TypeError(f"不可序列化的类型: {type(obj)}")
