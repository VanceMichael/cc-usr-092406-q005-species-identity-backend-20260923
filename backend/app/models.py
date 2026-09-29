from sqlalchemy import Column, Integer, String, Float, Date, DateTime, ForeignKey, Text, Index, text
from sqlalchemy.orm import relationship
from datetime import datetime
from .database import Base

class Pond(Base):
    __tablename__ = "ponds"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), unique=True, index=True, nullable=False)
    area = Column(Float, nullable=False, comment="面积(亩)")
    water_depth = Column(Float, nullable=False, comment="水深(米)")
    species = Column(String(100), comment="养殖品种(录入原文)")
    species_identity_id = Column(Integer, ForeignKey("species_identities.id"), nullable=True, comment="品种稳定身份")
    species_version_id = Column(Integer, ForeignKey("species_name_versions.id"), nullable=True, comment="当时采用的品种名称版本")
    status = Column(String(20), default="active", comment="状态: active, inactive")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    batches = relationship("Batch", back_populates="pond")
    species_history = relationship("PondSpeciesHistory", back_populates="pond", order_by="PondSpeciesHistory.id")

class Batch(Base):
    __tablename__ = "batches"

    id = Column(Integer, primary_key=True, index=True)
    batch_number = Column(String(50), unique=True, index=True, nullable=False, comment="批次号")
    pond_id = Column(Integer, ForeignKey("ponds.id"), nullable=False)
    species = Column(String(100), nullable=False, comment="养殖品种(录入原文)")
    species_identity_id = Column(Integer, ForeignKey("species_identities.id"), nullable=True, comment="品种稳定身份")
    species_version_id = Column(Integer, ForeignKey("species_name_versions.id"), nullable=True, comment="当时采用的品种名称版本")
    stocking_date = Column(Date, nullable=False, comment="放苗日期")
    estimated_harvest_date = Column(Date, comment="预计收获日期")
    actual_harvest_date = Column(Date, comment="实际收获日期")
    status = Column(String(20), default="active", comment="状态: active, harvested, closed")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    pond = relationship("Pond", back_populates="batches")
    stocking_records = relationship("StockingRecord", back_populates="batch")
    feeding_records = relationship("FeedingRecord", back_populates="batch")
    water_quality_records = relationship("WaterQualityRecord", back_populates="batch")
    medication_records = relationship("MedicationRecord", back_populates="batch")
    cost_records = relationship("CostRecord", back_populates="batch")
    harvest_sales = relationship("HarvestSale", back_populates="batch")

class StockingRecord(Base):
    __tablename__ = "stocking_records"

    id = Column(Integer, primary_key=True, index=True)
    batch_id = Column(Integer, ForeignKey("batches.id"), nullable=False)
    species = Column(String(100), nullable=False, comment="品种(录入原文)")
    species_identity_id = Column(Integer, ForeignKey("species_identities.id"), nullable=True, comment="品种稳定身份")
    species_version_id = Column(Integer, ForeignKey("species_name_versions.id"), nullable=True, comment="当时采用的品种名称版本")
    quantity = Column(Integer, nullable=False, comment="数量(尾)")
    source = Column(String(200), comment="来源")
    batch_number = Column(String(50), comment="苗种批次号")
    weight_per_unit = Column(Float, comment="单重(克/尾)")
    total_weight = Column(Float, comment="总重量(公斤)")
    notes = Column(Text, comment="备注")
    created_at = Column(DateTime, default=datetime.utcnow)

    batch = relationship("Batch", back_populates="stocking_records")

class FeedingRecord(Base):
    __tablename__ = "feeding_records"

    id = Column(Integer, primary_key=True, index=True)
    batch_id = Column(Integer, ForeignKey("batches.id"), nullable=False)
    feeding_date = Column(Date, nullable=False, comment="投喂日期")
    feed_type = Column(String(100), nullable=False, comment="饲料类型")
    feed_quantity = Column(Float, nullable=False, comment="投喂量(公斤)")
    feeding_time = Column(String(20), comment="投喂时间")
    weather = Column(String(50), comment="天气情况")
    water_temperature = Column(Float, comment="水温(℃)")
    notes = Column(Text, comment="备注")
    created_at = Column(DateTime, default=datetime.utcnow)

    batch = relationship("Batch", back_populates="feeding_records")

class WaterQualityRecord(Base):
    __tablename__ = "water_quality_records"

    id = Column(Integer, primary_key=True, index=True)
    batch_id = Column(Integer, ForeignKey("batches.id"), nullable=False)
    record_date = Column(Date, nullable=False, comment="检测日期")
    record_time = Column(String(20), comment="检测时间")
    water_temperature = Column(Float, comment="水温(℃)")
    ph_value = Column(Float, comment="pH值")
    dissolved_oxygen = Column(Float, comment="溶解氧(mg/L)")
    ammonia_nitrogen = Column(Float, comment="氨氮(mg/L)")
    nitrite = Column(Float, comment="亚硝酸盐(mg/L)")
    transparency = Column(Float, comment="透明度(cm)")
    notes = Column(Text, comment="备注")
    created_at = Column(DateTime, default=datetime.utcnow)

    batch = relationship("Batch", back_populates="water_quality_records")

class MedicationRecord(Base):
    __tablename__ = "medication_records"

    id = Column(Integer, primary_key=True, index=True)
    batch_id = Column(Integer, ForeignKey("batches.id"), nullable=False)
    medication_date = Column(Date, nullable=False, comment="用药日期")
    drug_name = Column(String(200), nullable=False, comment="药品名称")
    drug_type = Column(String(50), comment="药品类型")
    dosage = Column(Float, comment="用量")
    dosage_unit = Column(String(20), default="kg", comment="用量单位")
    administration_method = Column(String(100), comment="施用方法")
    purpose = Column(String(200), comment="用途")
    manufacturer = Column(String(200), comment="生产厂家")
    batch_number = Column(String(50), comment="药品批次号")
    notes = Column(Text, comment="备注")
    created_at = Column(DateTime, default=datetime.utcnow)

    batch = relationship("Batch", back_populates="medication_records")

class CostRecord(Base):
    __tablename__ = "cost_records"

    id = Column(Integer, primary_key=True, index=True)
    batch_id = Column(Integer, ForeignKey("batches.id"), nullable=False)
    cost_date = Column(Date, nullable=False, comment="费用日期")
    cost_type = Column(String(50), nullable=False, comment="费用类型: feed, medicine, labor, electricity, other")
    amount = Column(Float, nullable=False, comment="金额(元)")
    description = Column(String(500), comment="费用描述")
    quantity = Column(Float, comment="数量")
    unit = Column(String(20), comment="单位")
    unit_price = Column(Float, comment="单价")
    notes = Column(Text, comment="备注")
    created_at = Column(DateTime, default=datetime.utcnow)

    batch = relationship("Batch", back_populates="cost_records")

class HarvestSale(Base):
    __tablename__ = "harvest_sales"

    id = Column(Integer, primary_key=True, index=True)
    batch_id = Column(Integer, ForeignKey("batches.id"), nullable=False)
    sale_date = Column(Date, nullable=False, comment="销售日期")
    weight = Column(Float, nullable=False, comment="重量(公斤)")
    unit_price = Column(Float, nullable=False, comment="单价(元/公斤)")
    total_amount = Column(Float, comment="总金额(元)")
    buyer = Column(String(200), comment="买家")
    batch_number = Column(String(50), comment="追溯批次号")
    quality_grade = Column(String(50), comment="质量等级")
    notes = Column(Text, comment="备注")
    created_at = Column(DateTime, default=datetime.utcnow)

    batch = relationship("Batch", back_populates="harvest_sales")


# ---------------------------------------------------------------------------
# 品种身份域：目录、版本化名称、可审阅别名、映射决策、未决队列、轮养史、签署快照
# ---------------------------------------------------------------------------

class SpeciesIdentity(Base):
    """品种的稳定身份。code 一经生成永不改变；跨场区比较、列表、分析、追溯都以它为准。"""
    __tablename__ = "species_identities"

    id = Column(Integer, primary_key=True, index=True)
    code = Column(String(40), unique=True, nullable=False, index=True, comment="稳定标识, 如 SP-0001")
    current_version_id = Column(Integer, ForeignKey("species_name_versions.id"), nullable=True, comment="当前生效名称版本")
    merged_into_id = Column(Integer, ForeignKey("species_identities.id"), nullable=True, comment="合并后指向存续身份")
    status = Column(String(20), default="active", nullable=False, comment="active / merged")
    created_at = Column(DateTime, default=datetime.utcnow)

    merged_into = relationship("SpeciesIdentity", remote_side=[id], backref="merged_from")
    versions = relationship(
        "SpeciesNameVersion", back_populates="identity",
        foreign_keys="SpeciesNameVersion.identity_id", order_by="SpeciesNameVersion.id"
    )
    aliases = relationship("SpeciesAlias", back_populates="identity")


class SpeciesNameVersion(Base):
    """品种名称的不可变版本。同一身份同时只有一个 effective 版本(部分唯一索引)。"""
    __tablename__ = "species_name_versions"

    id = Column(Integer, primary_key=True, index=True)
    identity_id = Column(Integer, ForeignKey("species_identities.id"), nullable=False)
    version_no = Column(Integer, nullable=False, comment="身份内版本序号, 从1开始")
    canonical_name = Column(String(100), nullable=False, comment="该版本的规范名称")
    status = Column(String(20), default="effective", nullable=False, comment="effective / superseded")
    change_reason = Column(String(300), comment="名称更正原因")
    created_by = Column(String(100), comment="操作人")
    created_at = Column(DateTime, default=datetime.utcnow)
    superseded_at = Column(DateTime, nullable=True)

    identity = relationship(
        "SpeciesIdentity", back_populates="versions", foreign_keys=[identity_id]
    )

    __table_args__ = (
        Index(
            "ux_name_version_one_effective",
            "identity_id",
            unique=True,
            sqlite_where=text("status = 'effective'"),
        ),
    )


class SpeciesAlias(Base):
    """可审阅别名。normalized_text 全局唯一, 真正不同的品系不会因此自动合并。"""
    __tablename__ = "species_aliases"

    id = Column(Integer, primary_key=True, index=True)
    identity_id = Column(Integer, ForeignKey("species_identities.id"), nullable=False)
    alias_text = Column(String(100), nullable=False, comment="别名原文")
    normalized_text = Column(String(120), unique=True, nullable=False, index=True, comment="归一化键")
    status = Column(String(20), default="pending", nullable=False, comment="pending/approved/rejected/withdrawn")
    lock_version = Column(Integer, default=1, nullable=False, comment="乐观锁, 并发裁定只一个生效")
    proposed_by = Column(String(100), comment="提议人")
    reviewed_by = Column(String(100), comment="裁定人")
    reason = Column(String(300), comment="提议/裁定依据")
    created_at = Column(DateTime, default=datetime.utcnow)
    reviewed_at = Column(DateTime, nullable=True)

    identity = relationship("SpeciesIdentity", back_populates="aliases")


class SpeciesMappingDecision(Base):
    """旧文本到品种身份的映射裁定流水: 合并、拆分、撤回都留下依据, 只追加不改写。"""
    __tablename__ = "species_mapping_decisions"

    id = Column(Integer, primary_key=True, index=True)
    legacy_text_id = Column(Integer, ForeignKey("legacy_species_texts.id"), nullable=True)
    action = Column(String(20), nullable=False, comment="map/merge/split/withdraw/reject/auto_map")
    source_identity_id = Column(Integer, ForeignKey("species_identities.id"), nullable=True)
    target_identity_id = Column(Integer, ForeignKey("species_identities.id"), nullable=True)
    source_text = Column(String(100), comment="动作发生时的文本快照")
    rationale = Column(String(500), nullable=False, comment="裁定依据")
    decided_by = Column(String(100), comment="裁定人")
    created_at = Column(DateTime, default=datetime.utcnow)


class LegacySpeciesText(Base):
    """历史/未识别品种文本及其映射状态(未决队列)。"""
    __tablename__ = "legacy_species_texts"

    id = Column(Integer, primary_key=True, index=True)
    raw_text = Column(String(100), nullable=False, comment="原始文本")
    normalized_text = Column(String(120), unique=True, nullable=False, index=True)
    source_table = Column(String(200), nullable=False, comment="出现过的表: ponds,batches,stocking_records")
    occurrence_count = Column(Integer, default=1, nullable=False)
    status = Column(String(20), default="pending", nullable=False, comment="pending/mapped/withdrawn/rejected")
    mapped_identity_id = Column(Integer, ForeignKey("species_identities.id"), nullable=True)
    suggested_identity_id = Column(Integer, ForeignKey("species_identities.id"), nullable=True, comment="候选身份")
    similarity = Column(Float, nullable=True, comment="候选相似度")
    decided_by = Column(String(100))
    decided_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class PondSpeciesHistory(Base):
    """塘口轮养史: 每次塘口更换养殖品种追加一条。"""
    __tablename__ = "pond_species_history"

    id = Column(Integer, primary_key=True, index=True)
    pond_id = Column(Integer, ForeignKey("ponds.id"), nullable=False)
    species_text = Column(String(100), nullable=False, comment="当时录入文本")
    species_identity_id = Column(Integer, ForeignKey("species_identities.id"), nullable=True)
    species_version_id = Column(Integer, ForeignKey("species_name_versions.id"), nullable=True)
    change_reason = Column(String(300))
    changed_by = Column(String(100))
    started_at = Column(DateTime, default=datetime.utcnow)

    pond = relationship("Pond", back_populates="species_history")


class SignedAnalysis(Base):
    """已签署的周期分析快照。名称更正后仍按签署时的原口径原样重现。"""
    __tablename__ = "signed_analyses"

    id = Column(Integer, primary_key=True, index=True)
    batch_id = Column(Integer, ForeignKey("batches.id"), nullable=False)
    species_identity_id = Column(Integer, ForeignKey("species_identities.id"), nullable=True)
    species_version_id = Column(Integer, ForeignKey("species_name_versions.id"), nullable=True)
    species_name_at_signing = Column(String(100), nullable=False, comment="签署时口径的品种名称")
    payload_json = Column(Text, nullable=False, comment="完整分析结果快照")
    signed_by = Column(String(100), nullable=False)
    sign_reason = Column(String(300))
    created_at = Column(DateTime, default=datetime.utcnow)
