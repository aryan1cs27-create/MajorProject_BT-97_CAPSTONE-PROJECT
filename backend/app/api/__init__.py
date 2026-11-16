from app.api.health import router as health_router
from app.api.jobs import router as jobs_router
from app.api.matches import router as matches_router
from app.api.resumes import router as resumes_router

__all__ = ["health_router", "resumes_router", "jobs_router", "matches_router"]
