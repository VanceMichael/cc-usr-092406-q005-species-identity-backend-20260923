from sqlalchemy import Column, Integer, String, Float, Date, DateTime, ForeignKey, Text, Index, text
from sqlalchemy.orm import relationship
from datetime import datetime
from .database import Base

# ---------------------------------------------------------------------------
# 品种身份链路
#
# Species          规范品种：稳定标识 code + 当前标准名 current_name，code 终身不变。
# SpeciesName      名称版本：canonical（标准名）/ alias（别名），带 proposed/active/
#                  superseded/rejected 生命周期。归一化键只允许有一个 active 版本，
#                  并发裁定时由部分唯一索引兜底。
# SpeciesMapping   旧文本/新异写文本的候选映射与未决队列，裁定后保留依据。
# SpeciesEvent     全部身份调整的审计流水（创建、别名、更名、合并、拆分、撤回…）。
# CycleSignature   已签署周期分析的口径快照，更名/合并/拆分后仍按原口径重现。
# ---------------------------------------------------------------------------

class Species(Base):
    __tablename__ = "species"

    id = Column(Integer, primary_key=True, index=True)
    code = Column(String(64), unique=True, index=True, nullable=False, comment="稳定品种标识")
    current_name = Column(String(100), nullable=False, comment="当前标准名(有效名称)")
    scientific_name = Column(String(200), comment="学名")
    description = Column(Text, comment="说明")
    status = Column(String(20), default="active", comment="状态: active, inactive")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    names = relationship("SpeciesName", back_populates="species")
    mappings = relationship("SpeciesMapping", foreign_keys="SpeciesMapping.target_species_id", back_populates="target_species")
    events = relationship("SpeciesEvent", foreign_keys="SpeciesEvent.species_id", back_populates="species")

class SpeciesName(Base):
    __tablename__ = "species_names"
    __table_args__ = (
        # 同一归一化写法全局只能有一个生效版本，并发别名裁定时只有一个胜出
        Index("ix_species_names_active_key", "normalized_key", unique=True,
              sqlite_where=text("status = 'active'")),
        Index("ix_species_names_species_status", "species_id", "status"),
    )

    id = Column(Integer, primary_key=True, index=True)
    species_id = Column(Integer, ForeignKey("species.id"), nullable=False)
    name = Column(String(100), nullable=False, comment="名称原文")
    normalized_key = Column(String(100), nullable=True, index=True, comment="归一化键；非active版本可腾空")
    name_type = Column(String(20), nullable=False, comment="canonical / alias")
    status = Column(String(20), default="proposed", nullable=False,
                    comment="proposed / active / superseded / rejected")
    note = Column(Text, comment="备注/裁定依据")
    actor = Column(String(100), comment="操作人")
    created_at = Column(DateTime, default=datetime.utcnow)
    decided_at = Column(DateTime, comment="裁定时间")

    species = relationship("Species", back_populates="names")

class SpeciesMapping(Base):
    __tablename__ = "species_mappings"
    __table_args__ = (
        # 同一旧文本在未决队列中只允许出现一次
        Index("ix_species_mappings_pending_key", "normalized_key", unique=True,
              sqlite_where=text("status = 'pending'")),
        Index("ix_species_mappings_status", "status"),
    )

    id = Column(Integer, primary_key=True, index=True)
    raw_text = Column(String(100), nullable=False, comment="原始品种文本")
    normalized_key = Column(String(100), nullable=False, index=True, comment="归一化键")
    candidate_species_id = Column(Integer, ForeignKey("species.id"), comment="系统建议的候选身份")
    confidence = Column(String(20), default="low", comment="high / low：仅用于提示，不自动合并")
    status = Column(String(20), default="pending", nullable=False,
                    comment="pending / approved / rejected / withdrawn / resolved_auto")
    target_species_id = Column(Integer, ForeignKey("species.id"), comment="裁定后的目标身份")
    source = Column(String(20), default="entry", comment="entry / scan")
    occurrence_count = Column(Integer, default=1, comment="该文本在业务数据中的出现次数")
    reason = Column(Text, comment="裁定/撤回依据")
    actor = Column(String(100), comment="操作人")
    created_at = Column(DateTime, default=datetime.utcnow)
    decided_at = Column(DateTime, comment="裁定时间")

    candidate_species = relationship("Species", foreign_keys=[candidate_species_id])
    target_species = relationship("Species", foreign_keys=[target_species_id])

class SpeciesEvent(Base):
    __tablename__ = "species_events"

    id = Column(Integer, primary_key=True, index=True)
    species_id = Column(Integer, ForeignKey("species.id"), nullable=False, index=True)
    event_type = Column(String(40), nullable=False,
                        comment="created / alias_proposed / alias_approved / alias_rejected / "
                                "renamed / mapping_approved(merge) / mapping_rejected / "
                                "mapping_withdrawn / mapping_resolved_auto / mapping_proposed / split")
    from_value = Column(Text, comment="变更前写法/身份")
    to_value = Column(Text, comment="变更后写法/身份")
    related_species_id = Column(Integer, ForeignKey("species.id"), nullable=True, comment="拆分/合并关联身份")
    mapping_id = Column(Integer, ForeignKey("species_mappings.id"), nullable=True)
    reason = Column(Text)
    actor = Column(String(100))
    created_at = Column(DateTime, default=datetime.utcnow)

    species = relationship("Species", foreign_keys=[species_id], back_populates="events")

class CycleSignature(Base):
    __tablename__ = "cycle_signatures"

    id = Column(Integer, primary_key=True, index=True)
    batch_id = Column(Integer, ForeignKey("batches.id"), unique=True, nullable=False, index=True)
    signed_batch_number = Column(String(50), nullable=False)
    signed_species_code = Column(String(64), comment="签署时品种标识")
    signed_species_name = Column(String(100), nullable=False, comment="签署时品种口径名")
    snapshot = Column(Text, nullable=False, comment="签署时周期分析完整快照(JSON)")
    signer = Column(String(100), comment="签署人")
    signed_at = Column(DateTime, default=datetime.utcnow)

class Pond(Base):
    __tablename__ = "ponds"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), unique=True, index=True, nullable=False)
    area = Column(Float, nullable=False, comment="面积(亩)")
    water_depth = Column(Float, nullable=False, comment="水深(米)")
    species = Column(String(100), comment="养殖品种(原文)")
    # 塘口允许轮养：species_code 仅表示当前/最近一轮的品种身份，历史看批次
    species_code = Column(String(64), comment="当前品种稳定标识")
    status = Column(String(20), default="active", comment="状态: active, inactive")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    batches = relationship("Batch", back_populates="pond")

class Batch(Base):
    __tablename__ = "batches"

    id = Column(Integer, primary_key=True, index=True)
    batch_number = Column(String(50), unique=True, index=True, nullable=False, comment="批次号")
    pond_id = Column(Integer, ForeignKey("ponds.id"), nullable=False)
    species = Column(String(100), nullable=False, comment="养殖品种(创建时原文, 永久保留)")
    species_code = Column(String(64), index=True, comment="创建时采用的品种版本标识, 永久保留")
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
    species = Column(String(100), nullable=False, comment="品种(投苗时原文, 永久保留)")
    species_code = Column(String(64), index=True, comment="投苗时采用的品种版本标识, 永久保留")
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
