"""
app/routes/checklist.py
───────────────────────
Segment 7: Verification checklist and user feedback endpoints.

Endpoints
─────────
GET    /api/cases/{case_id}/checklist
    — Returns the canonical checklist items plus per-case progress.
    — Always includes a disclaimer that completion ≠ proof of safety.

PATCH  /api/cases/{case_id}/checklist/{item_id}
    — Marks an item as checked or unchecked (upsert).
    — Validates item_id against server-side catalogue; rejects unknown IDs.

GET    /api/cases/{case_id}/feedback
    — Returns existing feedback for the case, or 404 if none submitted yet.

PUT    /api/cases/{case_id}/feedback
    — Creates or replaces feedback for the case (upsert — one record per case).
    — Validates and limits note to 500 chars.
    — Does NOT collect identity, credentials, or payment information.

DELETE /api/cases/{case_id}/feedback
    — Deletes the feedback record for this case.

Safety guarantees
─────────────────
- item_id is validated against VALID_ITEM_IDS; unknown IDs → 422.
- No user identity, phone, email, or credential data is accepted.
- All timestamps are server-side.
- Data is scoped to the case; cross-case access is not possible.
- All rows are deleted when their parent Case is deleted (CASCADE FK).

Privacy note
────────────
This application is a LOCAL/DEMO PROTOTYPE. It must NOT be exposed publicly
with real sensitive cases unless authentication and encryption at rest are added.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Case, CaseFeedback, ChecklistProgress
from app.schemas import (
    ChecklistItemDef,
    ChecklistProgressRead,
    ChecklistStateRead,
    ChecklistUpdateRequest,
    FeedbackRead,
    FeedbackSubmit,
)
from app.services.checklist import (
    CHECKLIST_ITEMS,
    CHECKLIST_VERSION,
    VALID_ITEM_IDS,
    get_item,
)

logger = logging.getLogger(__name__)
router = APIRouter()

CHECKLIST_DISCLAIMER = (
    "Completing checklist items records your verification steps. "
    "It does not prove a payment, requester, or communication is legitimate or safe. "
    "Always exercise independent judgement."
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_case_or_404(case_id: str, db: Session) -> Case:
    case = db.get(Case, case_id)
    if case is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "CASE_NOT_FOUND", "message": f"Case '{case_id}' does not exist."},
        )
    return case


def _get_progress_map(case_id: str, db: Session) -> dict[str, ChecklistProgress]:
    """Return a dict of item_id → ChecklistProgress rows for this case."""
    rows = (
        db.query(ChecklistProgress)
        .filter(ChecklistProgress.case_id == case_id)
        .all()
    )
    return {row.item_id: row for row in rows}


# ── Checklist endpoints ───────────────────────────────────────────────────────

@router.get(
    "/checklist",
    response_model=ChecklistStateRead,
    status_code=status.HTTP_200_OK,
    summary="Get checklist items and per-case progress",
    tags=["Checklist"],
)
def get_checklist(case_id: str, db: Session = Depends(get_db)) -> ChecklistStateRead:
    _get_case_or_404(case_id, db)
    progress_map = _get_progress_map(case_id, db)

    items = [
        ChecklistItemDef(
            item_id=item.item_id,
            category=item.category,
            text=item.text,
            detail=item.detail,
            india_only=item.india_only,
        )
        for item in CHECKLIST_ITEMS
    ]

    progress = [
        ChecklistProgressRead(
            item_id=item.item_id,
            checked=progress_map[item.item_id].checked if item.item_id in progress_map else False,
            checked_at=(
                progress_map[item.item_id].checked_at if item.item_id in progress_map else None
            ),
        )
        for item in CHECKLIST_ITEMS
    ]

    return ChecklistStateRead(
        case_id=case_id,
        items=items,
        progress=progress,
        checklist_version=CHECKLIST_VERSION,
        disclaimer=CHECKLIST_DISCLAIMER,
    )


@router.patch(
    "/checklist/{item_id}",
    response_model=ChecklistProgressRead,
    status_code=status.HTTP_200_OK,
    summary="Mark a checklist item as checked or unchecked",
    description=(
        "Upserts the checked state for one checklist item. "
        "item_id must be a valid server-side catalogue ID. "
        "Checking an item records only that the user marked it — "
        "it does not independently verify the action."
    ),
    tags=["Checklist"],
)
def update_checklist_item(
    case_id: str,
    item_id: str,
    body: ChecklistUpdateRequest,
    db: Session = Depends(get_db),
) -> ChecklistProgressRead:
    _get_case_or_404(case_id, db)

    if item_id not in VALID_ITEM_IDS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "INVALID_ITEM_ID",
                "message": f"'{item_id}' is not a valid checklist item ID.",
            },
        )

    # Upsert
    existing = (
        db.query(ChecklistProgress)
        .filter(
            ChecklistProgress.case_id == case_id,
            ChecklistProgress.item_id == item_id,
        )
        .first()
    )

    if existing is None:
        row = ChecklistProgress(
            case_id=case_id,
            item_id=item_id,
            checked=body.checked,
        )
        db.add(row)
    else:
        existing.checked = body.checked
        existing.checked_at = datetime.now(timezone.utc)
        row = existing

    db.commit()
    db.refresh(row)

    logger.info("Checklist item updated: case=%s item=%s checked=%s", case_id, item_id, body.checked)
    return ChecklistProgressRead(
        item_id=row.item_id,
        checked=row.checked,
        checked_at=row.checked_at,
    )


# ── Feedback endpoints ────────────────────────────────────────────────────────

@router.get(
    "/feedback",
    response_model=FeedbackRead,
    status_code=status.HTTP_200_OK,
    summary="Get feedback for a case",
    tags=["Feedback"],
)
def get_feedback(case_id: str, db: Session = Depends(get_db)) -> FeedbackRead:
    _get_case_or_404(case_id, db)
    row = db.query(CaseFeedback).filter(CaseFeedback.case_id == case_id).first()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "FEEDBACK_NOT_FOUND",
                "message": "No feedback has been submitted for this case yet.",
            },
        )
    return FeedbackRead.model_validate(row)


@router.put(
    "/feedback",
    response_model=FeedbackRead,
    status_code=status.HTTP_200_OK,
    summary="Submit or update feedback for a case",
    description=(
        "Creates or replaces the feedback record for this case. "
        "All fields are optional. Note is limited to 500 characters. "
        "Do not include credentials, payment details, or personal identity data."
    ),
    tags=["Feedback"],
)
def submit_feedback(
    case_id: str,
    body: FeedbackSubmit,
    db: Session = Depends(get_db),
) -> FeedbackRead:
    _get_case_or_404(case_id, db)

    existing = db.query(CaseFeedback).filter(CaseFeedback.case_id == case_id).first()

    if existing is None:
        row = CaseFeedback(
            case_id=case_id,
            verified_independently=body.verified_independently,
            still_has_concerns=body.still_has_concerns,
            result_useful=body.result_useful,
            result_clear=body.result_clear,
            note=body.note,
        )
        db.add(row)
    else:
        existing.verified_independently = body.verified_independently
        existing.still_has_concerns = body.still_has_concerns
        existing.result_useful = body.result_useful
        existing.result_clear = body.result_clear
        existing.note = body.note
        existing.updated_at = datetime.now(timezone.utc)
        row = existing

    db.commit()
    db.refresh(row)
    logger.info("Feedback submitted/updated: case=%s", case_id)
    return FeedbackRead.model_validate(row)


@router.delete(
    "/feedback",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete feedback for a case",
    tags=["Feedback"],
)
def delete_feedback(case_id: str, db: Session = Depends(get_db)):
    _get_case_or_404(case_id, db)
    row = db.query(CaseFeedback).filter(CaseFeedback.case_id == case_id).first()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "FEEDBACK_NOT_FOUND",
                "message": "No feedback record found for this case.",
            },
        )
    db.delete(row)
    db.commit()
    logger.info("Feedback deleted: case=%s", case_id)
