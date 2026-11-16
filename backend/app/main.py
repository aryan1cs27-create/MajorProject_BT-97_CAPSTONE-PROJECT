"""MatchWise API — application entry point.

Run locally (PowerShell, from backend/):

    uvicorn app.main:app --reload --port 8000
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import health_router, jobs_router, matches_router, resumes_router
from app.core.config import get_settings
from app.db.init import init_db
from app.services import storage as storage_service

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("matchwise")

settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI):
    logger.info("Starting %s v%s (%s)", settings.app_name, settings.app_version, settings.environment)
    if not settings.api_token:
        logger.warning(
            "API_TOKEN is not set: the resume/job/match routes are UNAUTHENTICATED. "
            "Set API_TOKEN in backend/.env before exposing this server."
        )
    storage_service.upload_dir()          # ensure backend/uploads exists
    init_db()                             # idempotent: create missing tables
    yield
    logger.info("Shutting down %s", settings.app_name)


app = FastAPI(
    title=settings.app_name,
    description="Expandable job and resume matching recommendation system.",
    version=settings.app_version,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    # Explicit origins from ALLOWED_ORIGINS — never "*" (and never "*" together
    # with credentials), so the browser only accepts these dev frontends.
    allow_origins=settings.allowed_origins,
    # The token auth uses headers (not cookies), so credentials stay off and
    # the origin allow-list must remain explicit.
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Accept", "Content-Type", "Authorization"],
)


@app.exception_handler(RequestValidationError)
async def validation_error_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={
            "error": {
                "code": "validation_error",
                "message": "Invalid request.",
                "details": _json_safe_errors(exc.errors()),
            }
        },
    )


def _json_safe_errors(errors: list[dict]) -> list[dict]:
    """Pydantic puts the raw exception object in ``ctx`` for validator errors,
    which is not JSON serialisable — convert non-scalar values to strings."""
    safe: list[dict] = []
    for error in errors:
        item = dict(error)
        ctx = item.get("ctx")
        if isinstance(ctx, dict):
            item["ctx"] = {
                key: value if isinstance(value, (str, int, float, bool, type(None))) else str(value)
                for key, value in ctx.items()
            }
        safe.append(item)
    return safe


@app.exception_handler(HTTPException)
async def http_exception_handler(_: Request, exc: HTTPException) -> JSONResponse:
    """Render every HTTPException as {"error": {code, status, message}}."""
    detail = exc.detail
    if isinstance(detail, dict) and "message" in detail:
        code = str(detail.get("code", "http_error"))
        message = str(detail["message"])
        body = {"error": {"code": code, "status": exc.status_code, "message": message}}
    elif isinstance(detail, str):
        body = {"error": {"code": "http_error", "status": exc.status_code, "message": detail}}
    else:
        body = {
            "error": {
                "code": "http_error",
                "status": exc.status_code,
                "message": "Request failed.",
                "details": detail,
            }
        }
    return JSONResponse(status_code=exc.status_code, content=body, headers=exc.headers)


@app.exception_handler(Exception)
async def unhandled_error_handler(_: Request, exc: Exception) -> JSONResponse:
    # Full details go to the server log only; the client gets a safe message.
    logger.exception("Unhandled error")
    return JSONResponse(
        status_code=500,
        content={"error": {"code": "internal_error", "message": f"An unexpected error occurred ({exc.__class__.__name__})."}},
    )


app.include_router(health_router)
app.include_router(resumes_router)
app.include_router(jobs_router)
app.include_router(matches_router)


@app.get("/", include_in_schema=False)
def root() -> dict[str, str]:
    return {"app": settings.app_name, "docs": "/docs"}
