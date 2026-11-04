"""Alembic 运行迁移时执行的文件：告诉它连哪个数据库、对照哪些表。"""

import logging

from alembic import context
from sqlalchemy import create_engine, pool

import jacara.models  # noqa: F401  导入所有表，autogenerate 才能看到它们
from jacara.core.config import get_settings
from jacara.core.db import Base

target_metadata = Base.metadata

# 在终端里显示 Alembic 做了什么（例如 Running upgrade  -> fb6c95503174）
logging.basicConfig(format="%(message)s", level=logging.WARNING)
logging.getLogger("alembic").setLevel(logging.INFO)


def run_migrations_offline() -> None:
    """只生成 SQL、不连数据库（uv run alembic upgrade head --sql）。"""
    context.configure(
        url=get_settings().database_url,
        target_metadata=target_metadata,
        literal_binds=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """连上数据库，真正执行迁移。"""
    engine = create_engine(get_settings().database_url, poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
