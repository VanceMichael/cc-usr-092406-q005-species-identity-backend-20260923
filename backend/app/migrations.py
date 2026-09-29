"""轻量结构迁移。

项目使用 SQLite 且未引入 Alembic；这里只做向后兼容的“缺列补列”，
历史业务表保留原数据，品种身份列以可空方式补齐，后续由扫描回填建立映射。
"""
from sqlalchemy import text, inspect

_ADD_COLUMNS = {
    "ponds": [("species_code", "VARCHAR(64)")],
    "batches": [("species_code", "VARCHAR(64)")],
    "stocking_records": [("species_code", "VARCHAR(64)")],
}


def _existing_columns(inspector, table: str):
    if table not in inspector.get_table_names():
        return set()
    return {col["name"] for col in inspector.get_columns(table)}


def run_lightweight_migrations(engine) -> list:
    """补缺失列，返回执行过的迁移描述。重复执行安全。"""
    applied = []
    inspector = inspect(engine)
    with engine.begin() as conn:
        for table, columns in _ADD_COLUMNS.items():
            present = _existing_columns(inspector, table)
            if not present:
                # 建表交给 Base.metadata.create_all，下一轮进程自然生效
                continue
            for name, ddl_type in columns:
                if name not in present:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl_type}"))
                    applied.append(f"{table}.{name}")
    return applied
