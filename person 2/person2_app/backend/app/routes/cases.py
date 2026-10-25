"""
app/routes/cases.py
───────────────────
Case management endpoints.

Segment 2 implements:
    POST   /api/cases               — create a new case
    GET    /api/cases               — list all cases
    GET    /api/cases/{case_id}     — retrieve a single case with evidence metadata
    DELETE /api/cases/{case_id}     — delete a case, its evidence metadata, and stored files

Security fix (Segment 8 / Issue 1):
    Case deletion now also removes all associated evidence files from disk.
    Strategy:
      1. Collect all server-side file paths before touching the DB.
      2. Attempt to delete the DB record (cascade handles evidence metadata).
      3. After a successful DB commit, attempt to remove each file.
      4. File-removal failures are logged with paths for operator reconciliation
         but do NOT falsely report failure after a committed DB delete.
         (The metadata is gone — reporting an error would mislead callers.)
      5. Files are resolved inside the upload directory; paths outside it are
         never deleted.

    If the DB delete fails, no files are touched.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import Case
from app.schemas import CaseCreate, CaseList, CaseListResponse, CaseRead

logger = logging.getLogger(__name__)

router = APIRouter()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_case_or_404(case_id: str, db: Session) -> Case:
    """Fetch a Case by ID or raise a 404 HTTPException."""
    case = db.get(Case, case_id)
    if case is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "CASE_NOT_FOUND", "message": f"Case '{case_id}' does not exist."},
        )
    return case


def _case_to_list_item(case: Case) -> dict[str, Any]:
    """Convert a Case ORM object to the CaseList schema dict."""
    return {
        "id":               case.id,
        "created_at":       case.created_at,
        "updated_at":       case.updated_at,
        "status":           case.status,
        "consent_status":   case.consent_status,
        "analysis_status":  case.analysis_status,
        "decision":         case.decision,
        "description":      case.description,
        "evidence_count":   len(case.evidence_items),
    }


def _collect_evidence_paths(case: Case) -> list[Path]:
    """
    Return a list of expected on-disk paths for all evidence attached to this case.

    Uses the same server-filename derivation as evidence.py so paths are consistent.
    Resolves each path inside the configured upload directory; skips anything that
    resolves outside it (path-traversal guard).
    """
    import re
    upload_dir = Path(settings.upload_dir).resolve()
    paths: list[Path] = []
    for ev in case.evidence_items:
        suffix = Path(ev.original_filename).suffix.lower()
        suffix = re.sub(r"[^\w.]", "", suffix)[:16]
        server_name = f"{ev.id}{suffix}"
        candidate = (upload_dir / server_name).resolve()
        try:
            candidate.relative_to(upload_dir)
            paths.append(candidate)
        except ValueError:
            logger.warning(
                "Skipping out-of-bounds evidence path during case deletion: %s", candidate
            )
    return paths


def _delete_evidence_files(paths: list[Path], case_id: str) -> list[Path]:
    """
    Remove evidence files from disk after a successful DB delete.

    Returns a list of paths that could NOT be deleted (for operator reconciliation).
    Never raises — deletion failures are always logged.
    """
    orphaned: list[Path] = []
    for path in paths:
        try:
            path.unlink(missing_ok=True)
            logger.debug("Removed evidence file %s (case=%s)", path.name, case_id)
        except OSError as exc:
            logger.error(
                "Could not remove evidence file %s for case=%s: %s — "
                "file may need manual cleanup.",
                path, case_id, exc,
            )
            orphaned.append(path)
    return orphaned


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post(
    "",
    response_model=CaseRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new case",
    description=(
        "Opens a new verification case. All status fields are server-initialised. "
        "An optional description of the suspicious communication may be provided."
    ),
    tags=["Cases"],
)
def create_case(
    body: CaseCreate,
    db: Session = Depends(get_db),
) -> CaseRead:
    case = Case(description=body.description)
    db.add(case)
    try:
        db.commit()
        db.refresh(case)
    except IntegrityError:
        db.rollback()
        logger.exception("IntegrityError creating case")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "DB_ERROR", "message": "Could not create case due to a database error."},
        )
    logger.info("Created case id=%s", case.id)
    return CaseRead.model_validate(case)


@router.get(
    "",
    response_model=CaseListResponse,
    summary="List all cases",
    description="Returns a paginated list of all cases, ordered newest first.",
    tags=["Cases"],
)
def list_cases(db: Session = Depends(get_db)) -> CaseListResponse:
    cases = db.query(Case).order_by(Case.created_at.desc()).all()
    items = [CaseList.model_validate(_case_to_list_item(c)) for c in cases]
    return CaseListResponse(total=len(items), items=items)


@router.get(
    "/{case_id}",
    response_model=CaseRead,
    summary="Get a single case",
    description="Returns full case details including attached evidence metadata.",
    tags=["Cases"],
)
def get_case(
    case_id: str,
    db: Session = Depends(get_db),
) -> CaseRead:
    case = _get_case_or_404(case_id, db)
    return CaseRead.model_validate(case)


@router.delete(
    "/{case_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="Delete a case and all its evidence",
    description=(
        "Permanently deletes a case, all associated evidence metadata, "
        "and all stored evidence files. "
        "File paths are resolved safely within the configured upload directory. "
        "No files outside the upload directory are ever deleted. "
        "If the database delete succeeds but some files cannot be removed, "
        "the orphaned paths are logged for operator reconciliation. "
        "NOTE: This application has no authentication. Do not expose publicly."
    ),
    tags=["Cases"],
)
def delete_case(
    case_id: str,
    db: Session = Depends(get_db),
) -> Response:
    case = _get_case_or_404(case_id, db)

    # Collect file paths BEFORE deleting DB records (evidence IDs gone after commit)
    evidence_paths = _collect_evidence_paths(case)

    try:
        db.delete(case)
        db.commit()
    except IntegrityError:
        db.rollback()
        logger.exception("IntegrityError deleting case id=%s", case_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "DB_ERROR", "message": "Could not delete case due to a database error."},
        )

    # DB is committed — now clean up files (non-fatal if some files are missing)
    orphaned = _delete_evidence_files(evidence_paths, case_id)
    if orphaned:
        logger.error(
            "Case %s deleted from DB but %d file(s) could not be removed: %s",
            case_id, len(orphaned), [str(p) for p in orphaned],
        )

    logger.info("Deleted case id=%s (removed %d of %d evidence files)",
                case_id, len(evidence_paths) - len(orphaned), len(evidence_paths))
    return Response(status_code=status.HTTP_204_NO_CONTENT)
