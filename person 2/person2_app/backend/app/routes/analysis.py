"""
app/routes/analysis.py
──────────────────────
Segment 5: Analysis endpoints.

Endpoints
─────────
POST   /api/cases/{case_id}/evidence/{evidence_id}/analyse
    — request analysis of one evidence item.
    — requires: case exists, consent granted, evidence belongs to case.
    — optional JSON body {"expected_payment": {amount, currency, recipient,
      reference}} for image evidence; omitting the body keeps the original
      behaviour.  Expected details on non-image evidence → 422.
    — idempotent: if result already exists and is not FAILED, returns current
      state — unless expected payment details are supplied that differ from
      the ones the stored result was computed with, in which case it re-runs.
    — if module unavailable: creates result with status=UNAVAILABLE immediately.
    — if module available: runs synchronously (async task queue is Segment 6+).

GET    /api/cases/{case_id}/evidence/{evidence_id}/analysis
    — retrieve the current analysis result (or 404 if not yet requested).

Safety guarantees
─────────────────
- Uploaded files are read-only; never executed.
- Absolute file paths are never returned in API responses.
- No findings are fabricated; all findings come from the module or are absent.
- is_stub=True when module is unavailable; UI must present this state honestly.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, status
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import (
    AnalysisResult,
    AnalysisResultStatus,
    Case,
    ConsentStatus,
    Evidence,
    EvidenceStatus,
)
from app.schemas import (
    AnalysisRequestBody,
    AnalysisRequestResponse,
    AnalysisResultRead,
    FindingRead,
    PaymentSummaryRead,
)
from app.services.analysis import (
    AnalysisModuleError,
    findings_from_json,
    findings_to_json,
    module_is_available,
    run_analysis,
)
from app.routes.evidence import _storage_path, _server_filename  # reuse path helpers
from app.services.case_status import refresh_analysis_status

logger = logging.getLogger(__name__)
router = APIRouter()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_case_or_404(case_id: str, db: Session) -> Case:
    case = db.get(Case, case_id)
    if case is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "CASE_NOT_FOUND", "message": f"Case '{case_id}' does not exist."},
        )
    return case


def _get_evidence_or_404(evidence_id: str, case_id: str, db: Session) -> Evidence:
    ev = db.query(Evidence).filter(
        Evidence.id == evidence_id,
        Evidence.case_id == case_id,
    ).first()
    if ev is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "EVIDENCE_NOT_FOUND",
                    "message": f"Evidence '{evidence_id}' not found on case '{case_id}'."},
        )
    return ev


def _commit_with_case_status(db: Session, case: Case) -> None:
    """Commit, keeping Case.analysis_status in step with its evidence."""
    refresh_analysis_status(db, case)
    db.commit()


def _payment_from_json(raw: Optional[str]) -> Optional[dict]:
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        logger.warning("payment_json could not be decoded; omitting payment summary.")
        return None
    return data if isinstance(data, dict) else None


def _stored_expected(result: Optional[AnalysisResult]) -> Optional[dict]:
    """Expected payment details the stored result was computed with, or None."""
    payment = _payment_from_json(result.payment_json) if result else None
    supplied = (payment or {}).get("expected_supplied") or {}
    return supplied if any(v is not None for v in supplied.values()) else None


def _result_to_schema(result: AnalysisResult) -> AnalysisResultRead:
    """Convert ORM AnalysisResult to the API schema, deserialising findings."""
    raw_findings = findings_from_json(result.findings_json)
    findings = [FindingRead(**f) for f in raw_findings]
    payment = None
    raw_payment = _payment_from_json(result.payment_json)
    if raw_payment is not None:
        try:
            payment = PaymentSummaryRead(**raw_payment)
        except ValidationError:
            logger.warning("Stored payment summary for result=%s is malformed; omitted.", result.id)
    return AnalysisResultRead(
        id=result.id,
        evidence_id=result.evidence_id,
        status=result.status,
        requested_at=result.requested_at,
        completed_at=result.completed_at,
        module_name=result.module_name,
        module_version=result.module_version,
        findings=findings,
        limitations=result.limitations,
        error_message=result.error_message,
        is_stub=(result.status == AnalysisResultStatus.UNAVAILABLE or result.module_name == "unavailable"),
        module_available=module_is_available(),
        payment=payment,
    )


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post(
    "/analyse",
    response_model=AnalysisRequestResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Request analysis of an evidence item",
    description=(
        "Queues or immediately performs analysis on an evidence item using Person 1's "
        "detection module. Requires consent to be granted. "
        "If the module is not installed, returns status=unavailable immediately — "
        "no analysis is performed and no findings are fabricated. "
        "If an analysis result already exists and is not in a failed state, the "
        "existing result is returned without re-running analysis (idempotent)."
    ),
    tags=["Analysis"],
)
def request_analysis(
    case_id: str,
    evidence_id: str,
    body: Optional[AnalysisRequestBody] = Body(default=None),
    db: Session = Depends(get_db),
) -> AnalysisRequestResponse:
    # ── 1. Validate case and consent ──────────────────────────────────────────
    case = _get_case_or_404(case_id, db)
    if case.consent_status != ConsentStatus.GRANTED:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "CONSENT_REQUIRED",
                "message": "Consent must be granted before analysis can be requested.",
            },
        )

    # ── 2. Validate evidence ──────────────────────────────────────────────────
    ev = _get_evidence_or_404(evidence_id, case_id, db)

    expected = body.expected_payment.supplied() if body and body.expected_payment else None
    if expected is not None and not ev.media_type.lower().startswith("image/"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "EXPECTED_PAYMENT_NOT_APPLICABLE",
                "message": "Expected payment details can only be supplied for image (screenshot) evidence.",
            },
        )

    # ── 3. Idempotency — return existing non-failed result ────────────────────
    # New, different expected payment details re-run the comparison.
    existing: Optional[AnalysisResult] = ev.analysis_result
    expectations_changed = (
        expected is not None
        and existing is not None
        and existing.status in (AnalysisResultStatus.COMPLETED, AnalysisResultStatus.UNAVAILABLE)
        and expected != _stored_expected(existing)
    )
    if existing and not expectations_changed and existing.status not in (
        AnalysisResultStatus.FAILED,
    ):
        return AnalysisRequestResponse(
            evidence_id=evidence_id,
            result_id=existing.id,
            status=existing.status,
            message=(
                "Analysis result already exists. Use GET .../analysis to retrieve it."
                if existing.status == AnalysisResultStatus.COMPLETED
                else "Analysis is already in progress or has been completed."
            ),
            module_available=module_is_available(),
        )

    # ── 4. Create/reset the result record ─────────────────────────────────────
    if existing:
        # Reset a previously failed result, or one computed with other expectations
        db.delete(existing)
        db.flush()

    result = AnalysisResult(evidence_id=evidence_id)
    db.add(result)
    db.flush()  # assigns result.id

    # ── 5. Run analysis (synchronous for now; async queue is Segment 6+) ──────
    if not module_is_available():
        # Module absent — record unavailable, do not fabricate findings.
        module_result = run_analysis(Path("/dev/null"), ev.media_type, evidence_id)
        result.status = AnalysisResultStatus.UNAVAILABLE
        result.module_name = module_result.module_name
        result.module_version = module_result.module_version
        result.limitations = module_result.limitations
        result.findings_json = None
        result.error_message = None
        result.completed_at = datetime.now(timezone.utc)

        # Evidence status unchanged from PENDING — analysis did not run.
        _commit_with_case_status(db, case)
        db.refresh(result)
        logger.info(
            "Analysis unavailable for evidence=%s case=%s", evidence_id, case_id
        )
        return AnalysisRequestResponse(
            evidence_id=evidence_id,
            result_id=result.id,
            status=result.status,
            message=(
                "The analysis module is not available in this deployment. "
                "No analysis was performed and no findings have been generated. "
                "Results will update when the module is installed."
            ),
            module_available=False,
        )

    # Module present — run actual analysis.
    ev.processing_status = EvidenceStatus.PROCESSING
    result.status = AnalysisResultStatus.RUNNING
    _commit_with_case_status(db, case)

    # Resolve safe file path (same logic as evidence.py _server_filename)
    storage_dir = _storage_path()
    server_name = _server_filename(evidence_id, ev.original_filename)
    file_path = storage_dir / server_name

    if not file_path.exists():
        result.status = AnalysisResultStatus.FAILED
        result.error_message = "Evidence file not found on disk; cannot analyse."
        result.completed_at = datetime.now(timezone.utc)
        ev.processing_status = EvidenceStatus.FAILED
        _commit_with_case_status(db, case)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "FILE_NOT_FOUND", "message": "Evidence file is missing from storage."},
        )

    try:
        args = (file_path, ev.media_type, evidence_id)
        module_result = (
            run_analysis(*args) if expected is None
            else run_analysis(*args, expected_payment=expected)
        )
        result.module_name = module_result.module_name
        result.module_version = module_result.module_version
        result.limitations = module_result.limitations
        result.error_message = None
        result.completed_at = datetime.now(timezone.utc)
        if module_result.is_stub:
            # Module installed, but no component could analyse this evidence
            # (e.g. audio model unavailable) — never report it as COMPLETED.
            result.status = AnalysisResultStatus.UNAVAILABLE
            result.findings_json = None
            result.payment_json = None
            ev.processing_status = EvidenceStatus.PENDING
        else:
            result.status = AnalysisResultStatus.COMPLETED
            result.findings_json = findings_to_json(module_result.findings)
            result.payment_json = (
                json.dumps(module_result.payment, ensure_ascii=False)
                if module_result.payment is not None else None
            )
            ev.processing_status = EvidenceStatus.PROCESSED

    except AnalysisModuleError as exc:
        result.status = AnalysisResultStatus.FAILED
        result.error_message = str(exc)
        result.completed_at = datetime.now(timezone.utc)
        ev.processing_status = EvidenceStatus.FAILED
        _commit_with_case_status(db, case)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "ANALYSIS_FAILED", "message": f"Analysis module error: {exc}"},
        )
    except Exception as exc:
        logger.exception("Unexpected error during analysis of evidence=%s", evidence_id)
        result.status = AnalysisResultStatus.FAILED
        result.error_message = "Unexpected error during analysis."
        result.completed_at = datetime.now(timezone.utc)
        ev.processing_status = EvidenceStatus.FAILED
        _commit_with_case_status(db, case)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "ANALYSIS_ERROR", "message": "An unexpected error occurred during analysis."},
        )

    _commit_with_case_status(db, case)
    db.refresh(result)
    if result.status == AnalysisResultStatus.UNAVAILABLE:
        logger.info(
            "Analysis unavailable for evidence=%s case=%s module=%s",
            evidence_id, case_id, result.module_name,
        )
        return AnalysisRequestResponse(
            evidence_id=evidence_id,
            result_id=result.id,
            status=result.status,
            message=(
                "No analysis component could assess this evidence type in this "
                "deployment. No findings have been generated. This does not mean "
                "the evidence is safe."
            ),
            module_available=True,
        )
    logger.info(
        "Analysis completed: evidence=%s case=%s module=%s",
        evidence_id, case_id, result.module_name,
    )
    return AnalysisRequestResponse(
        evidence_id=evidence_id,
        result_id=result.id,
        status=result.status,
        message="Analysis completed.",
        module_available=True,
    )


@router.get(
    "/analysis",
    response_model=AnalysisResultRead,
    status_code=status.HTTP_200_OK,
    summary="Retrieve the analysis result for an evidence item",
    description=(
        "Returns the current analysis result for the specified evidence item. "
        "Returns 404 if analysis has not been requested yet. "
        "findings will be empty unless status=completed AND the analysis module "
        "is available — never fabricated."
    ),
    tags=["Analysis"],
)
def get_analysis(
    case_id: str,
    evidence_id: str,
    db: Session = Depends(get_db),
) -> AnalysisResultRead:
    _get_case_or_404(case_id, db)
    ev = _get_evidence_or_404(evidence_id, case_id, db)

    result: Optional[AnalysisResult] = ev.analysis_result
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "ANALYSIS_NOT_FOUND",
                "message": (
                    "No analysis has been requested for this evidence item. "
                    "POST .../analyse to request analysis."
                ),
            },
        )

    return _result_to_schema(result)
