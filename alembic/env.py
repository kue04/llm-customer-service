"""Alembic 运行环境。

连接串解析顺序（见 alembic.ini 里的说明）：
1. 环境变量 ``RAG_DATABASE_URL``
2. ``config.get_main_option("sqlalchemy.url")``（测试通过 set_main_option 注入）
3. 无有效配置时拒绝运行；SQLite 仅在显式测试配置中允许。

``target_metadata`` 合并 ingestion/runtime 元数据并保留原约束命名惯例，
这样以后新增模型可以直接用 ``alembic revision --autogenerate`` 生成草稿，
但**手工审查后**再提交，绝不使用运行时 create_all 改表。
"""

from __future__ import annotations

from logging.config import fileConfig
import os
from pathlib import Path
import sys

from alembic import context
from sqlalchemy import MetaData, engine_from_config, pool


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from services.ingestion import models  # noqa: E402
from services.ingestion.db import DATABASE_URL_ENV, get_database_url, validate_database_url  # noqa: E402
from services.runtime_schema import metadata as runtime_metadata  # noqa: E402


config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = MetaData(naming_convention=models.Base.metadata.naming_convention)
for source_metadata in (models.Base.metadata, runtime_metadata):
    for table in source_metadata.sorted_tables:
        table.to_metadata(target_metadata)


def resolve_url() -> str:
    env_url = (os.getenv(DATABASE_URL_ENV) or "").strip()
    if env_url:
        validate_database_url(env_url)
        return env_url
    configured = (config.get_main_option("sqlalchemy.url") or "").strip()
    if configured:
        validate_database_url(configured)
        return configured
    return get_database_url()


def run_migrations_offline() -> None:
    context.configure(
        url=resolve_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section) or {}
    section["sqlalchemy.url"] = resolve_url()
    connectable = engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
