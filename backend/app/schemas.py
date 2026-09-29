from pydantic import BaseModel
from typing import Optional, List
from datetime import date, datetime

class PondBase(BaseModel):
    name: str
    area: float
    water_depth: float
    species: Optional[str] = None
    status: Optional[str] = "active"

class PondCreate(PondBase):
    pass

class PondUpdate(BaseModel):
    name: Optional[str] = None
    area: Optional[float] = None
    water_depth: Optional[float] = None
    species: Optional[str] = None
    status: Optional[str] = None

class PondResponse(PondBase):
    id: int
    species_identity_id: Optional[int] = None
    species_version_id: Optional[int] = None
    species_identity: Optional[dict] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True

class BatchBase(BaseModel):
    batch_number: str
    pond_id: int
    species: str
    stocking_date: date
    estimated_harvest_date: Optional[date] = None
    actual_harvest_date: Optional[date] = None
    status: Optional[str] = "active"

class BatchCreate(BatchBase):
    pass

class BatchUpdate(BaseModel):
    batch_number: Optional[str] = None
    pond_id: Optional[int] = None
    species: Optional[str] = None
    stocking_date: Optional[date] = None
    estimated_harvest_date: Optional[date] = None
    actual_harvest_date: Optional[date] = None
    status: Optional[str] = None

class BatchResponse(BatchBase):
    id: int
    species_identity_id: Optional[int] = None
    species_version_id: Optional[int] = None
    species_identity: Optional[dict] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True

class StockingRecordBase(BaseModel):
    batch_id: int
    species: str
    quantity: int
    source: Optional[str] = None
    batch_number: Optional[str] = None
    weight_per_unit: Optional[float] = None
    total_weight: Optional[float] = None
    notes: Optional[str] = None

class StockingRecordCreate(StockingRecordBase):
    pass

class StockingRecordUpdate(BaseModel):
    batch_id: Optional[int] = None
    species: Optional[str] = None
    quantity: Optional[int] = None
    source: Optional[str] = None
    batch_number: Optional[str] = None
    weight_per_unit: Optional[float] = None
    total_weight: Optional[float] = None
    notes: Optional[str] = None

class StockingRecordResponse(StockingRecordBase):
    id: int
    species_identity_id: Optional[int] = None
    species_version_id: Optional[int] = None
    species_identity: Optional[dict] = None
    created_at: datetime

    class Config:
        from_attributes = True

class FeedingRecordBase(BaseModel):
    batch_id: int
    feeding_date: date
    feed_type: str
    feed_quantity: float
    feeding_time: Optional[str] = None
    weather: Optional[str] = None
    water_temperature: Optional[float] = None
    notes: Optional[str] = None

class FeedingRecordCreate(FeedingRecordBase):
    pass

class FeedingRecordUpdate(BaseModel):
    batch_id: Optional[int] = None
    feeding_date: Optional[date] = None
    feed_type: Optional[str] = None
    feed_quantity: Optional[float] = None
    feeding_time: Optional[str] = None
    weather: Optional[str] = None
    water_temperature: Optional[float] = None
    notes: Optional[str] = None

class FeedingRecordResponse(FeedingRecordBase):
    id: int
    created_at: datetime

    class Config:
        from_attributes = True

class WaterQualityRecordBase(BaseModel):
    batch_id: int
    record_date: date
    record_time: Optional[str] = None
    water_temperature: Optional[float] = None
    ph_value: Optional[float] = None
    dissolved_oxygen: Optional[float] = None
    ammonia_nitrogen: Optional[float] = None
    nitrite: Optional[float] = None
    transparency: Optional[float] = None
    notes: Optional[str] = None

class WaterQualityRecordCreate(WaterQualityRecordBase):
    pass

class WaterQualityRecordUpdate(BaseModel):
    batch_id: Optional[int] = None
    record_date: Optional[date] = None
    record_time: Optional[str] = None
    water_temperature: Optional[float] = None
    ph_value: Optional[float] = None
    dissolved_oxygen: Optional[float] = None
    ammonia_nitrogen: Optional[float] = None
    nitrite: Optional[float] = None
    transparency: Optional[float] = None
    notes: Optional[str] = None

class WaterQualityRecordResponse(WaterQualityRecordBase):
    id: int
    created_at: datetime

    class Config:
        from_attributes = True

class MedicationRecordBase(BaseModel):
    batch_id: int
    medication_date: date
    drug_name: str
    drug_type: Optional[str] = None
    dosage: Optional[float] = None
    dosage_unit: Optional[str] = "kg"
    administration_method: Optional[str] = None
    purpose: Optional[str] = None
    manufacturer: Optional[str] = None
    batch_number: Optional[str] = None
    notes: Optional[str] = None

class MedicationRecordCreate(MedicationRecordBase):
    pass

class MedicationRecordUpdate(BaseModel):
    batch_id: Optional[int] = None
    medication_date: Optional[date] = None
    drug_name: Optional[str] = None
    drug_type: Optional[str] = None
    dosage: Optional[float] = None
    dosage_unit: Optional[str] = None
    administration_method: Optional[str] = None
    purpose: Optional[str] = None
    manufacturer: Optional[str] = None
    batch_number: Optional[str] = None
    notes: Optional[str] = None

class MedicationRecordResponse(MedicationRecordBase):
    id: int
    created_at: datetime

    class Config:
        from_attributes = True

class CostRecordBase(BaseModel):
    batch_id: int
    cost_date: date
    cost_type: str
    amount: float
    description: Optional[str] = None
    quantity: Optional[float] = None
    unit: Optional[str] = None
    unit_price: Optional[float] = None
    notes: Optional[str] = None

class CostRecordCreate(CostRecordBase):
    pass

class CostRecordUpdate(BaseModel):
    batch_id: Optional[int] = None
    cost_date: Optional[date] = None
    cost_type: Optional[str] = None
    amount: Optional[float] = None
    description: Optional[str] = None
    quantity: Optional[float] = None
    unit: Optional[str] = None
    unit_price: Optional[float] = None
    notes: Optional[str] = None

class CostRecordResponse(CostRecordBase):
    id: int
    created_at: datetime

    class Config:
        from_attributes = True

class HarvestSaleBase(BaseModel):
    batch_id: int
    sale_date: date
    weight: float
    unit_price: float
    total_amount: Optional[float] = None
    buyer: Optional[str] = None
    batch_number: Optional[str] = None
    quality_grade: Optional[str] = None
    notes: Optional[str] = None

class HarvestSaleCreate(HarvestSaleBase):
    pass

class HarvestSaleUpdate(BaseModel):
    batch_id: Optional[int] = None
    sale_date: Optional[date] = None
    weight: Optional[float] = None
    unit_price: Optional[float] = None
    total_amount: Optional[float] = None
    buyer: Optional[str] = None
    batch_number: Optional[str] = None
    quality_grade: Optional[str] = None
    notes: Optional[str] = None

class HarvestSaleResponse(HarvestSaleBase):
    id: int
    created_at: datetime

    class Config:
        from_attributes = True

class CostSummaryItem(BaseModel):
    type: str
    amount: float

class FeedingSummaryItem(BaseModel):
    feed_type: str
    total_quantity: float
    feeding_count: int

class CultureCycleAnalysis(BaseModel):
    batch_number: str
    pond_name: str
    species: str
    species_identity: Optional[dict] = None
    stocking_date: date
    harvest_date: Optional[date] = None
    days_cultured: Optional[int] = None
    initial_quantity: int
    harvest_weight: float
    survival_rate: float
    feed_total: float
    feed_conversion_ratio: float
    area: float
    yield_per_mu: float
    total_cost: float
    total_revenue: float
    profit: float
    cost_summary: Optional[dict] = None
    feeding_summary: Optional[dict] = None

class StockingRecordTrace(BaseModel):
    species: str
    species_identity: Optional[dict] = None
    quantity: int
    source: Optional[str] = None
    batch_number: Optional[str] = None
    stocking_date: Optional[date] = None

class FeedingRecordTrace(BaseModel):
    feeding_date: date
    feed_type: str
    quantity: float
    unit: Optional[str] = None

class WaterQualityRecordTrace(BaseModel):
    record_date: date
    water_temperature: Optional[float] = None
    ph_value: Optional[float] = None
    dissolved_oxygen: Optional[float] = None

class MedicationRecordTrace(BaseModel):
    medication_date: date
    medication_name: str
    dosage: Optional[float] = None
    unit: Optional[str] = None

class CostRecordTrace(BaseModel):
    cost_date: date
    cost_type: str
    amount: float
    description: Optional[str] = None

class HarvestSaleTrace(BaseModel):
    sale_date: date
    weight: float
    unit_price: float
    total_amount: Optional[float] = None
    buyer: Optional[str] = None

class BatchInfo(BaseModel):
    batch_number: str
    species: str
    species_identity: Optional[dict] = None
    stocking_date: date
    harvest_date: Optional[date] = None
    status: str
    pond_id: Optional[int] = None

class PondInfo(BaseModel):
    name: Optional[str] = None
    area: Optional[float] = None
    water_depth: Optional[float] = None

class BatchTraceability(BaseModel):
    batch: BatchInfo
    pond_info: PondInfo
    stocking_records: List[StockingRecordTrace] = []
    feeding_records: List[FeedingRecordTrace] = []
    water_quality_records: List[WaterQualityRecordTrace] = []
    medication_records: List[MedicationRecordTrace] = []
    cost_records: List[CostRecordTrace] = []
    harvest_sales: List[HarvestSaleTrace] = []


# ---------------------------------------------------------------------------
# 品种身份域
# ---------------------------------------------------------------------------

class SpeciesIdentityCreate(BaseModel):
    canonical_name: str
    created_by: Optional[str] = None
    reason: Optional[str] = None


class SpeciesNameCorrect(BaseModel):
    new_name: str
    reason: str
    changed_by: Optional[str] = None


class SpeciesAliasPropose(BaseModel):
    alias_text: str
    proposed_by: Optional[str] = None
    reason: Optional[str] = None


class SpeciesAliasAdjudicate(BaseModel):
    action: str  # approve / reject / withdraw
    reviewed_by: Optional[str] = None
    reason: Optional[str] = None
    expected_lock_version: Optional[int] = None


class SpeciesMergeRequest(BaseModel):
    source_identity_id: int
    target_identity_id: int
    rationale: str
    decided_by: Optional[str] = None


class SpeciesSplitRequest(BaseModel):
    from_identity_id: int
    new_canonical_name: str
    move_alias_texts: List[str]
    rationale: str
    decided_by: Optional[str] = None


class SpeciesLegacyDecision(BaseModel):
    action: str  # map / reject
    target_identity_id: Optional[int] = None
    decided_by: Optional[str] = None
    rationale: Optional[str] = None


class SpeciesLegacyWithdraw(BaseModel):
    decided_by: Optional[str] = None
    rationale: Optional[str] = None


class SpeciesResolveRequest(BaseModel):
    text: str
    source_table: str = "manual"


class SpeciesCandidateOut(BaseModel):
    identity_id: int
    identity_code: str
    canonical_name: str
    score: float
    matched_alias: Optional[str] = None


class SpeciesResolutionOut(BaseModel):
    raw_text: str
    normalized_text: str
    status: str
    matched: bool
    identity_id: Optional[int] = None
    identity_code: Optional[str] = None
    canonical_name: Optional[str] = None
    legacy_text_id: Optional[int] = None
    candidates: List[SpeciesCandidateOut] = []


class SpeciesNameVersionOut(BaseModel):
    id: int
    version_no: int
    canonical_name: str
    status: str
    change_reason: Optional[str] = None
    created_by: Optional[str] = None
    created_at: datetime
    superseded_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class SpeciesAliasOut(BaseModel):
    id: int
    identity_id: int
    alias_text: str
    normalized_text: str
    status: str
    lock_version: int
    proposed_by: Optional[str] = None
    reviewed_by: Optional[str] = None
    reason: Optional[str] = None
    created_at: datetime
    reviewed_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class SpeciesIdentityOut(BaseModel):
    id: int
    code: str
    status: str
    current_version_id: Optional[int] = None
    merged_into_id: Optional[int] = None
    created_at: datetime
    current_standard_name: Optional[str] = None
    historical_names: List[dict] = []
    aliases: List[str] = []

    class Config:
        from_attributes = True


class SpeciesDecisionOut(BaseModel):
    id: int
    action: str
    legacy_text_id: Optional[int] = None
    source_identity_id: Optional[int] = None
    target_identity_id: Optional[int] = None
    source_text: Optional[str] = None
    rationale: str
    decided_by: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True


class SpeciesLegacyTextOut(BaseModel):
    id: int
    raw_text: str
    normalized_text: str
    source_table: str
    occurrence_count: int
    status: str
    mapped_identity_id: Optional[int] = None
    suggested_identity_id: Optional[int] = None
    similarity: Optional[float] = None
    decided_by: Optional[str] = None
    decided_at: Optional[datetime] = None
    created_at: datetime
    candidates: List[SpeciesCandidateOut] = []

    class Config:
        from_attributes = True


class SpeciesView(BaseModel):
    """列表/周期分析/追溯统一返回的品种身份视图。"""
    identity_id: Optional[int] = None
    identity_code: Optional[str] = None
    current_standard_name: Optional[str] = None
    historical_name: Optional[str] = None
    is_historical_name: bool = False
    unresolved: bool = False
    raw_name: Optional[str] = None
    resolution_status: str
    candidates: Optional[List[dict]] = None


class SignAnalysisRequest(BaseModel):
    signed_by: str
    sign_reason: Optional[str] = None
