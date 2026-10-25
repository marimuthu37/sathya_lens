"""
app/routes/health.py
────────────────────
GET /api/health — liveness / readiness probe.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.config import settings
from app.schemas import HealthResponse

router = APIRouter()

# Application version — bump this manually or inject from CI.
_VERSION = "0.1.0"


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Health check",
    description="Returns the current liveness status, version, and runtime environment.",
    tags=["System"],
)
def health_check() -> HealthResponse:
    return HealthResponse(
        status="ok",
        version=_VERSION,
        environment=settings.app_env,
    )
