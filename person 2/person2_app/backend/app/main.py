"""
app/main.py
───────────
FastAPI application factory.

Start the server:
    uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import settings
from app.database import Base, engine
import app.models  # noqa: F401 — registers ORM models with Base.metadata before create_all
from app.migrate import run_migrations
from app.routes import cases, health, evidence, analysis, decision, checklist

logger = logging.getLogger(__name__)

# ── Application lifespan ──────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ANN001
    """
    Startup: create DB tables + upload directory.
    Shutdown: nothing special required for SQLite.
    """
    # Ensure upload directory exists (never stored in git).
    upload_path = Path(settings.upload_dir)
    upload_path.mkdir(parents=True, exist_ok=True)

    # Apply schema migrations BEFORE create_all.
    # create_all creates missing tables but does NOT add columns to existing ones.
    # run_migrations handles ALTER TABLE for columns added after initial deployment.
    run_migrations(settings.database_url)

    # Create any tables that are entirely new (idempotent for existing tables).
    Base.metadata.create_all(bind=engine)

    logger.info("SatyaLens backend started (env=%s)", settings.app_env)
    yield
    logger.info("SatyaLens backend stopped.")


# ── App factory ───────────────────────────────────────────────────────────────

app = FastAPI(
    title="SatyaLens API",
    description="Backend API for the SatyaLens suspicious-communication detection prototype.",
    version="0.2.0",
    docs_url="/docs" if settings.debug else None,
    redoc_url="/redoc" if settings.debug else None,
    lifespan=lifespan,
)

# ── CORS middleware ───────────────────────────────────────────────────────────

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Consistent error response handlers ───────────────────────────────────────

def _error_body(code: str, message: str) -> dict[str, Any]:
    return {"error": {"code": code, "message": message}}


@app.exception_handler(404)
async def not_found_handler(request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_404_NOT_FOUND,
        content=_error_body("NOT_FOUND", f"The path {request.url.path!r} does not exist."),
    )


@app.exception_handler(405)
async def method_not_allowed_handler(request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_405_METHOD_NOT_ALLOWED,
        content=_error_body("METHOD_NOT_ALLOWED", "HTTP method not allowed on this endpoint."),
    )


@app.exception_handler(500)
async def internal_error_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled server error: %s", exc)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=_error_body("INTERNAL_ERROR", "An unexpected error occurred. Please try again."),
    )


# ── Route registration ────────────────────────────────────────────────────────

app.include_router(health.router, prefix="/api")
app.include_router(cases.router, prefix="/api/cases", tags=["Cases"])
app.include_router(
    evidence.router,
    prefix="/api/cases/{case_id}/evidence",
    tags=["Evidence"],
)
app.include_router(
    analysis.router,
    prefix="/api/cases/{case_id}/evidence/{evidence_id}",
    tags=["Analysis"],
)
app.include_router(
    decision.router,
    prefix="/api/cases/{case_id}/decision",
    tags=["Decision"],
)
app.include_router(
    checklist.router,
    prefix="/api/cases/{case_id}",
    tags=["Checklist", "Feedback"],
)
