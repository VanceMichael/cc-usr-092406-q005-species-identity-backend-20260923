from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import inspect, text
from .database import engine, SessionLocal, Base
from .routers import ponds, batches, stocking, feeding, water_quality, medication, costs, harvest, analysis, species
from . import species_service

Base.metadata.create_all(bind=engine)


def _ensure_column(inspector, table_name, column_name, ddl):
    """SQLite 旧库轻量迁移: 表已存在但缺少新列时补齐, 保证幂等。"""
    if table_name not in inspector.get_table_names():
        return
    columns = {col["name"] for col in inspector.get_columns(table_name)}
    if column_name not in columns:
        with engine.begin() as conn:
            conn.execute(text(ddl))


def _migrate_and_backfill():
    """建表 + 给已有 ponds/batches/stocking_records 补身份列; 旧文本精确命中目录即安全回填, 否则入未决队列。"""
    inspector = inspect(engine)

    _ensure_column(
        inspector, "ponds", "species_identity_id",
        "ALTER TABLE ponds ADD COLUMN species_identity_id INTEGER",
    )
    _ensure_column(
        inspector, "ponds", "species_version_id",
        "ALTER TABLE ponds ADD COLUMN species_version_id INTEGER",
    )
    _ensure_column(
        inspector, "batches", "species_identity_id",
        "ALTER TABLE batches ADD COLUMN species_identity_id INTEGER",
    )
    _ensure_column(
        inspector, "batches", "species_version_id",
        "ALTER TABLE batches ADD COLUMN species_version_id INTEGER",
    )
    _ensure_column(
        inspector, "stocking_records", "species_identity_id",
        "ALTER TABLE stocking_records ADD COLUMN species_identity_id INTEGER",
    )
    _ensure_column(
        inspector, "stocking_records", "species_version_id",
        "ALTER TABLE stocking_records ADD COLUMN species_version_id INTEGER",
    )

    # 部分唯一索引在全新建表时由模型建立; 旧库在此补建(IF NOT EXISTS 保证幂等)
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_name_version_one_effective "
            "ON species_name_versions (identity_id) WHERE status = 'effective'"
        ))

    # 旧数据: 精确命中目录则回填身份, 不认识的文本生成候选并入未决队列
    db = SessionLocal()
    try:
        species_service.scan_legacy_texts(db)
        db.commit()
    finally:
        db.close()


_migrate_and_backfill()

app = FastAPI(
    title="水产养殖管理系统",
    description="水产养殖管理系统: 塘口、批次、投苗、投喂、水质、用药、成本、销售、周期分析与品种身份目录",
    version="2.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(species.router)
app.include_router(ponds.router)
app.include_router(batches.router)
app.include_router(stocking.router)
app.include_router(feeding.router)
app.include_router(water_quality.router)
app.include_router(medication.router)
app.include_router(costs.router)
app.include_router(harvest.router)
app.include_router(analysis.router)

@app.get("/")
def root():
    return {
        "message": "欢迎使用水产养殖管理系统API",
        "docs": "/docs",
        "version": "2.0.0"
    }

@app.get("/health")
def health_check():
    return {"status": "healthy"}
