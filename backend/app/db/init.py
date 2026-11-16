"""Database initialization — Alembic-driven (Phase 5.1).

Startup no longer creates tables with ``Base.metadata.create_all`` and no longer
patches columns with hand-written ``ALTER TABLE`` statements: it applies the
Alembic migrations in ``backend/migrations`` (``upgrade head``).

The baseline revision is adoption-safe — it creates only tables that do not
exist and adds only missing columns/indexes — so running this against a database
that was created by the old ``create_all`` path preserves every row.
"""

from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy.exc import SQLAlchemyError

logger = logging.getLogger("matchwise.db")

BACKEND_DIR = Path(__file__).resolve().parents[2]
ALEMBIC_INI = BACKEND_DIR / "alembic.ini"
MIGRATIONS_DIR = BACKEND_DIR / "migrations"


def alembic_config():
    """Alembic config pointing at this project's migrations (URL comes from env)."""
    from alembic.config import Config

    config = Config(str(ALEMBIC_INI))
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    return config


def current_revision() -> str | None:
    """Applied revision on the configured database, or None when unreachable."""
    from alembic.runtime.migration import MigrationContext
    from sqlalchemy import create_engine

    from app.core.config import get_settings

    engine = create_engine(get_settings().database_url, pool_pre_ping=True, future=True)
    try:
        with engine.connect() as connection:
            return MigrationContext.configure(connection).get_current_revision()
    finally:
        engine.dispose()


def init_db() -> None:
    """Apply pending migrations. Idempotent; never drops or recreates data."""
    from alembic import command

    try:
        command.upgrade(alembic_config(), "head")
    except SQLAlchemyError as exc:
        logger.error(
            "Database initialization failed: %s", exc.__class__.__name__, exc_info=True
        )
        raise RuntimeError(
            "Could not connect to the database to apply migrations. Check "
            "DATABASE_URL and make sure the MySQL server is running."
        ) from None
    logger.info("Database schema is up to date (alembic revision: head)")
