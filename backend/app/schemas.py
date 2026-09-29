from pydantic import BaseModel
from typing import Optional, List, Any, Dict
from datetime import date, datetime

# ---------------------------------------------------------------------------
# 品种身份
# ---------------------------------------------------------------------------

class SpeciesIdentity(BaseModel):
    """列表、周期分析、追溯共用的统一身份视图。"""
    code: Optional[str] = None
    current_name: Optional[str] = None
    canonical_name: Optional[str] = None
    aliases: List[str] = []
    historical_names: List[str] = []
    pending: bool = False
    pending_mapping_count: Optional[int] = None
    status: Optional[str] = None
    mapping_id: Optional[int] = None
    candidates: List[Dict[str, Any]] = []

class SpeciesCreate(BaseModel):
    name: str
    code: Optional[str] = None
    scientific_name: Optional[str] = None
    description: Optional[str] = None
    actor: Optional[str] = None

class SpeciesUpdate(BaseModel):
    scientific_name: Optional[str] = None
    description: Optional[str] = None
    status: Optional[str] = None

class SpeciesRename(BaseModel):
    new_name: str
    reason: Optional[str] = None
    actor: Optional[str] = None

class SpeciesResponse(BaseModel):
    id: int
    code: str
    current_name: str
    scientific_name: Optional[str] = None
    description: Optional[str] = None
    status: str
    aliases: List[str] = []
    historical_names: List[str] = []
    pending_mapping_count: int = 0
    identity: Optional[SpeciesIdentity] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True

class SpeciesNameResponse(BaseModel):
    id: int
    species_id: int
    name: str
    name_type: str
    status: str
    note: Optional[str] = None
    actor: Optional[str] = None
    created_at: datetime
    decided_at: Optional[datetime] = None

    class Config:
        from_attributes = True

class AliasPropose(BaseModel):
    alias: str
    note: Optional[str] = None
    actor: Optional[str] = None

class AliasDecision(BaseModel):
    reason: Optional[str] = None
    actor: Optional[str] = None

class SpeciesMappingResponse(BaseModel):
    id: int
    raw_text: str
    normalized_key: str
    candidate_species_id: Optional[int] = None
    candidate_code: Optional[str] = None
    candidate_name: Optional[str] = None
    target_species_id: Optional[int] = None
    target_code: Optional[str] = None
    confidence: str
    status: str
    source: str
    occurrence_count: int
    reason: Optional[str] = None
    actor: Optional[str] = None
    created_at: datetime
    decided_at: Optional[datetime] = None

class MappingApprove(BaseModel):
    target_species_id: int
    reason: Optional[str] = None
    actor: Optional[str] = None

class MappingDecision(BaseModel):
    reason: Optional[str] = None
    actor: Optional[str] = None

class SpeciesSplitRequest(BaseModel):
    new_species_name: str
    move_aliases: List[str]
    reason: Optional[str] = None
    actor: Optional[str] = None

class SpeciesMergeRequest(BaseModel):
    reason: Optional[str] = None
    actor: Optional[str] = None

class SpeciesEventResponse(BaseModel):
    id: int
    species_id: int
    event_type: str
    from_value: Optional[str] = None
    to_value: Optional[str] = None
    related_species_id: Optional[int] = None
    mapping_id: Optional[int] = None
    reason: Optional[str] = None
    actor: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True

class SuggestResponse(BaseModel):
    input: str
    match: Optional[Dict[str, Any]] = None
    candidates: List[Dict[str, Any]] = []

class SpeciesOverviewItem(BaseModel):
    code: Optional[str] = None
    current_name: str
    status: str
    aliases: List[str] = []
    historical_names: List[str] = []
    batch_count: int = 0
    pond_count: int = 0
    stocking_quantity: int = 0
    occurrence_count: Optional[int] = None

class EntryResolveResult(BaseModel):
    """创建业务记录时返回的身份解析结果（同时附在响应头式字段里）。"""
    status: str
    species_code: Optional[str] = None
    mapping_id: Optional[int] = None
    candidates: List[Dict[str, Any]] = []

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
    species_code: Optional[str] = None
    species_identity: Optional[SpeciesIdentity] = None
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
    species_code: Optional[str] = None
    species_identity: Optional[SpeciesIdentity] = None
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
    species_code: Optional[str] = None
    species_identity: Optional[SpeciesIdentity] = None
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
    species_code: Optional[str] = None
    recorded_species_name: Optional[str] = None
    species_identity: Optional[SpeciesIdentity] = None
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
    signed: Optional[bool] = None
    signed_at: Optional[datetime] = None
    signer: Optional[str] = None
    signed_species_name: Optional[str] = None

class SignCycleRequest(BaseModel):
    signer: Optional[str] = None

class StockingRecordTrace(BaseModel):
    species: str
    species_code: Optional[str] = None
    species_identity: Optional[SpeciesIdentity] = None
    recorded_species_name: Optional[str] = None
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
    species_code: Optional[str] = None
    species_identity: Optional[SpeciesIdentity] = None
    recorded_species_name: Optional[str] = None
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
