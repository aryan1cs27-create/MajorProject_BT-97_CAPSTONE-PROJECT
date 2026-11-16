"""Alembic environment for MatchWise.

The database URL comes from the application settings (``DATABASE_URL`` in
``backend/.env``), with ``ALEMBIC_DATABASE_URL`` as an override for one-off
runs and tests — there is no second copy of the connection string here.
"""

from __future__ import annotations

import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import create_engine, pool

# `prepend_sys_path` in alembic.ini normally handles this; keep it explicit so
# programmatic use (`command.upgrade`) works from any working directory.
BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app import models  # noqa: E402,F401  (registers every table on the metadata)
from app.core.config import get_settings  # noqa: E402
from app.db.session import Base  # noqa: E402

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def database_url() -> str:
    return os.getenv("ALEMBIC_DATABASE_URL") or get_settings().database_url


def run_migrations_offline() -> None:
    context.configure(
        url=database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = create_engine(database_url(), poolclass=pool.NullPool, future=True)
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            # SQLite cannot ALTER most things; batch mode keeps migrations
            # runnable there (the test suite and the verification script use it).
            render_as_batch=connection.dialect.name == "sqlite",
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
