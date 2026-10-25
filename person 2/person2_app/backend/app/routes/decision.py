"""
app/routes/decision.py
──────────────────────
Segment 6: Decision engine endpoints.

Endpoints
─────────
POST  /api/cases/{case_id}/decision
    — (Re-)compute the guidance decision for the case.
    — Reads all evidence and their analysis results from the DB.
    — Returns DecisionSummaryRead.  Decision is NOT persisted as a separate
      table — it is always recomputed from the current state of the evidence
      and analysis results, making it deterministic for unchanged inputs.
    — Why not persist? The existing Case.decision column already stores the
      category string. We update that column after each computation so the
      case list reflects the latest decision without a new table.

GET   /api/cases/{case_id}/decision
    — Returns the most-recently computed decision for this case.
    — Returns 404 if no decision has been computed yet.
    — The full DecisionSummaryRead is recomputed from current DB state and
      returned (not read from a cache) so it is always fresh.

Safety guarantees
─────────────────
- No numeric scores are invented or returned.
- No findings are fabricated.
- INSUFFICIENT_EVIDENCE is returned when analysis is unavailable.
- The category is advisory only; the API never blocks payments.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import (
    AnalysisResultStatus,
    Case,
    ConsentStatus,
    Evidence,
)
from app.routes.analysis import _payment_from_json
from app.schemas import DecisionSummaryRead, EvidenceReferenceRead
from app.services.analysis import findings_from_json
from app.services.decision import (
    CaseContext,
    DecisionSummary,
    EvidenceInput,
    GuidanceCategory,
    compute_decision,
)

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


def _build_evidence_inputs(case: Case) -> list[EvidenceInput]:
    """Convert ORM evidence rows (with analysis_result) into EvidenceInput list."""
    inputs: list[EvidenceInput] = []
    for ev in case.evidence_items:
        ar = ev.analysis_result
        payment: Optional[dict] = None
        if ar is None:
            analysis_status = "not_requested"
            findings: list[dict] = []
        else:
            analysis_status = ar.status
            if analysis_status == AnalysisResultStatus.COMPLETED:
                findings = findings_from_json(ar.findings_json)
                payment = _payment_from_json(ar.payment_json)
            else:
                findings = []

        inputs.append(EvidenceInput(
            evidence_id=ev.id,
            filename=ev.original_filename,
            media_type=ev.media_type,
            analysis_status=analysis_status,
            findings=findings,
            payment=payment,
        ))
    return inputs


def _summary_to_schema(summary: DecisionSummary) -> DecisionSummaryRead:
    refs = [
        EvidenceReferenceRead(
            evidence_id=r.evidence_id,
            filename=r.filename,
            finding_type=r.finding_type,
            finding_label=r.finding_label,
        )
        for r in summary.supporting_refs
    ]
    return DecisionSummaryRead(
        category=summary.category.value,
        short_explanation=summary.short_explanation,
        factors_of_concern=summary.factors_of_concern,
        factors_unknown=summary.factors_unknown,
        conflicts=summary.conflicts,
        supporting_refs=refs,
        limitations=summary.limitations,
        next_steps=summary.next_steps,
        generated_at=summary.generated_at,
        rule_version=summary.rule_version,
    )


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post(
    "",
    response_model=DecisionSummaryRead,
    status_code=status.HTTP_200_OK,
    summary="Compute the guidance decision for a case",
    description=(
        "Applies the deterministic decision rules to all available evidence "
        "and analysis results for the case. "
        "Returns a DecisionSummaryRead. The guidance category (LOW_CONCERN, "
        "VERIFY_FIRST, HIGH_RISK, or INSUFFICIENT_EVIDENCE) is advisory only. "
        "Consent must be granted before a decision can be requested. "
        "The Case.decision column is updated to reflect the latest category. "
        "No numeric risk score is ever computed or returned."
    ),
    tags=["Decision"],
)
def compute_case_decision(
    case_id: str,
    db: Session = Depends(get_db),
) -> DecisionSummaryRead:
    case = _get_case_or_404(case_id, db)

    if case.consent_status != ConsentStatus.GRANTED:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "CONSENT_REQUIRED",
                "message": "Consent must be granted before a decision can be requested.",
            },
        )

    evidence_inputs = _build_evidence_inputs(case)
    context = CaseContext(description=case.description)

    summary = compute_decision(evidence_inputs, context)

    # Persist the category to the Case row for the case list view.
    # Map guidance category → Decision enum value where possible.
    from app.models import Decision
    CATEGORY_TO_DECISION = {
        GuidanceCategory.LOW_CONCERN:           Decision.LOW_CONCERN,
        GuidanceCategory.VERIFY_FIRST:          Decision.VERIFY_FIRST,
        GuidanceCategory.HIGH_RISK:             Decision.HIGH_RISK,
        GuidanceCategory.INSUFFICIENT_EVIDENCE: None,  # no matching Decision enum value
    }
    case.decision = CATEGORY_TO_DECISION.get(summary.category)
    # FIX (Issue 3): mark that a decision was computed so GET can return it
    # even when category == INSUFFICIENT_EVIDENCE (decision column stays None).
    case.decision_computed = True
    db.commit()

    logger.info(
        "Decision computed: case=%s category=%s rule_version=%s",
        case_id, summary.category.value, summary.rule_version,
    )
    return _summary_to_schema(summary)


@router.get(
    "",
    response_model=DecisionSummaryRead,
    status_code=status.HTTP_200_OK,
    summary="Get the current decision for a case",
    description=(
        "Recomputes and returns the current decision from the live database state. "
        "Returns 404 if no decision has been requested yet (Case.decision is null). "
        "Consent must be granted."
    ),
    tags=["Decision"],
)
def get_case_decision(
    case_id: str,
    db: Session = Depends(get_db),
) -> DecisionSummaryRead:
    case = _get_case_or_404(case_id, db)

    if case.consent_status != ConsentStatus.GRANTED:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "CONSENT_REQUIRED",
                "message": "Consent must be granted before viewing a decision.",
            },
        )

    # FIX (Issue 3): use decision_computed flag instead of case.decision is None.
    # INSUFFICIENT_EVIDENCE stores decision=None but decision_computed=True.
    if not case.decision_computed:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "DECISION_NOT_FOUND",
                "message": (
                    "No decision has been computed for this case. "
                    "POST /api/cases/{case_id}/decision to request one."
                ),
            },
        )

    # Recompute from live state so the result is always fresh.
    evidence_inputs = _build_evidence_inputs(case)
    context = CaseContext(description=case.description)
    summary = compute_decision(evidence_inputs, context)
    return _summary_to_schema(summary)
