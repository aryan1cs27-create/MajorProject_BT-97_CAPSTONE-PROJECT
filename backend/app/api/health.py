"""Health-check API routes."""

from __future__ import annotations

import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import get_settings
from app.db.session import engine
from app.schemas.health import DatabaseHealthResponse, DatabaseStatus, HealthResponse

logger = logging.getLogger("matchwise.health")

router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health", response_model=HealthResponse, summary="Application health check")
def health() -> HealthResponse:
    settings = get_settings()
    return HealthResponse(
        status="ok",
        app=settings.app_name,
        version=settings.app_version,
        environment=settings.environment,
    )


@router.get(
    "/health/db",
    response_model=DatabaseHealthResponse,
    summary="Database connectivity check",
)
def database_health() -> DatabaseHealthResponse | JSONResponse:
    """Performs a real connectivity check.

    Returns 503 when the database is unreachable. The response never contains
    credentials or raw driver errors; details are logged server-side only.
    """
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        logger.error("Database health check failed: %s", exc.__class__.__name__, exc_info=True)
        return JSONResponse(
            status_code=503,
            content={
                "status": "error",
                "database": {
                    "connected": False,
                    "detail": "Could not connect to the database. Check that MySQL is running "
                    "and DATABASE_URL is configured correctly.",
                },
            },
        )

    return DatabaseHealthResponse(
        status="ok",
        database=DatabaseStatus(connected=True, detail="Connection OK"),
    )
