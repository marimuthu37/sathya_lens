"""
app/models.py
─────────────
SQLAlchemy ORM model definitions.

Segment 2: Case and Evidence metadata models.
Segment 5: AnalysisResult model added.
Segment 7: ChecklistProgress and CaseFeedback models added.
"""

# NOTE: `from __future__ import annotations` is intentionally omitted here.
# SQLAlchemy 2's Mapped[] annotation resolution requires annotations to be
# evaluated eagerly (not as strings).  We use Optional[X] from typing instead
# of the Python 3.10+ `X | Y` syntax to stay compatible with Python 3.9.

import uuid
from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


# ── Helpers ───────────────────────────────────────────────────────────────────

def _now_utc() -> datetime:
    """Return a timezone-aware UTC datetime (used as column default)."""
    return datetime.now(timezone.utc)


def _new_uuid() -> str:
    """Generate a URL-safe UUID4 string for use as a primary key."""
    return str(uuid.uuid4())


# ── Enumerations ──────────────────────────────────────────────────────────────

import enum  # noqa: E402  (kept after helpers for readability)


class CaseStatus(str, enum.Enum):
    """Lifecycle status of a case."""
    OPEN       = "open"
    PROCESSING = "processing"
    COMPLETE   = "complete"
    ARCHIVED   = "archived"


class ConsentStatus(str, enum.Enum):
    """Whether the user has provided informed consent."""
    PENDING  = "pending"
    GRANTED  = "granted"
    REVOKED  = "revoked"


class AnalysisStatus(str, enum.Enum):
    """State of the analysis pipeline for the case."""
    NOT_STARTED = "not_started"
    QUEUED      = "queued"
    RUNNING     = "running"
    DONE        = "done"
    FAILED      = "failed"


class Decision(str, enum.Enum):
    """Final verdict produced by the decision engine (Segment 6)."""
    LOW_CONCERN  = "low_concern"
    VERIFY_FIRST = "verify_first"
    HIGH_RISK    = "high_risk"


class EvidenceStatus(str, enum.Enum):
    """Processing status of a single evidence item."""
    PENDING    = "pending"
    PROCESSING = "processing"
    PROCESSED  = "processed"
    FAILED     = "failed"


class AnalysisResultStatus(str, enum.Enum):
    """
    Status of a stored AnalysisResult record.

    UNAVAILABLE — Person 1's module is not installed. Result is a documented
                  placeholder, NOT a real model output.
    PENDING     — queued but not yet processed.
    RUNNING     — actively being processed.
    COMPLETED   — module returned a result.
    FAILED      — module raised an exception or returned an invalid response.
    """
    UNAVAILABLE = "unavailable"
    PENDING     = "pending"
    RUNNING     = "running"
    COMPLETED   = "completed"
    FAILED      = "failed"


# ── ORM Models ────────────────────────────────────────────────────────────────

class Case(Base):
    __tablename__ = "cases"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=_new_uuid, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now_utc, nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now_utc, onupdate=_now_utc, nullable=False,
    )
    status: Mapped[str] = mapped_column(
        Enum(CaseStatus, name="case_status"), default=CaseStatus.OPEN, nullable=False,
    )
    consent_status: Mapped[str] = mapped_column(
        Enum(ConsentStatus, name="consent_status"), default=ConsentStatus.PENDING, nullable=False,
    )
    consent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    analysis_status: Mapped[str] = mapped_column(
        Enum(AnalysisStatus, name="analysis_status"), default=AnalysisStatus.NOT_STARTED, nullable=False,
    )
    decision: Mapped[Optional[str]] = mapped_column(
        Enum(Decision, name="decision"), nullable=True, default=None,
    )
    # FIX (Issue 3): Tracks whether a decision has been computed for this case.
    # INSUFFICIENT_EVIDENCE maps to Case.decision = None (no matching enum value),
    # so Case.decision alone cannot distinguish "never computed" from "computed
    # but insufficient evidence". This flag is True after any successful POST /decision.
    decision_computed: Mapped[bool] = mapped_column(default=False, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    evidence_items: Mapped[List["Evidence"]] = relationship(
        "Evidence", back_populates="case",
        cascade="all, delete-orphan", lazy="select",
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Case id={self.id!r} status={self.status!r}>"


class Evidence(Base):
    """
    Metadata for a single piece of evidence attached to a case.
    Raw file bytes are stored on disk; this table stores only metadata.
    """
    __tablename__ = "evidence"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=_new_uuid, index=True
    )
    case_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("cases.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    original_filename: Mapped[str] = mapped_column(String(512), nullable=False)
    media_type: Mapped[str] = mapped_column(String(128), nullable=False)
    file_size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now_utc, nullable=False
    )
    processing_status: Mapped[str] = mapped_column(
        Enum(EvidenceStatus, name="evidence_status"),
        default=EvidenceStatus.PENDING, nullable=False,
    )
    case: Mapped["Case"] = relationship("Case", back_populates="evidence_items")
    analysis_result: Mapped[Optional["AnalysisResult"]] = relationship(
        "AnalysisResult", back_populates="evidence",
        cascade="all, delete-orphan", uselist=False, lazy="select",
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Evidence id={self.id!r} case_id={self.case_id!r}>"


class AnalysisResult(Base):
    """
    Stores the outcome of running Person 1's analysis module against a single
    evidence item.  One-to-one with Evidence.

    IMPORTANT:
    - No fabricated confidence scores or deepfake detections are stored here.
    - When status = UNAVAILABLE, all findings fields are NULL.
    - findings_json is raw JSON from Person 1's module; never invented.
    """
    __tablename__ = "analysis_results"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=_new_uuid, index=True
    )
    evidence_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("evidence.id", ondelete="CASCADE"),
        nullable=False, unique=True, index=True,
    )
    status: Mapped[str] = mapped_column(
        Enum(AnalysisResultStatus, name="analysis_result_status"),
        default=AnalysisResultStatus.PENDING, nullable=False,
    )
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now_utc, nullable=False
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    module_name: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    module_version: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    # Raw JSON from Person 1's module. NULL when unavailable/pending/running/failed.
    findings_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    limitations: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Structured payment-screenshot summary (extracted fields with confidence,
    # user-supplied expectations, comparisons, image quality). NULL when no
    # payment screenshot was analysed, and for rows created before it existed.
    payment_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    evidence: Mapped["Evidence"] = relationship("Evidence", back_populates="analysis_result")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<AnalysisResult id={self.id!r} status={self.status!r}>"


# ── Segment 7 models ──────────────────────────────────────────────────────────

class ChecklistProgress(Base):
    """
    Records whether a user has marked a specific checklist item as completed
    for a given case.

    IMPORTANT:
    - A checked item means the USER marked it as done.
    - It does NOT mean SatyaLens independently verified it.
    - Completion of all items does not prove a payment or requester is safe.
    - No personal information beyond the case ID is stored.

    Retention / deletion:
    - Rows are deleted automatically when the parent Case is deleted (CASCADE).
    """
    __tablename__ = "checklist_progress"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=_new_uuid, index=True
    )
    case_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("cases.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    # item_id must be one of the VALID_ITEM_IDS from services/checklist.py
    item_id: Mapped[str] = mapped_column(String(64), nullable=False)
    checked: Mapped[bool] = mapped_column(default=True, nullable=False)
    checked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now_utc, nullable=False
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<ChecklistProgress case={self.case_id!r} item={self.item_id!r}>"


class CaseFeedback(Base):
    """
    Optional user feedback for a case. One row per case (upsert on re-submit).

    Fields NOT collected: user identity, phone, email, IP, credentials, location.

    Retention: deleted when parent Case is deleted (CASCADE).
    Note field: limited to 500 characters server-side.
    """
    __tablename__ = "case_feedback"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=_new_uuid, index=True
    )
    case_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("cases.id", ondelete="CASCADE"),
        nullable=False, unique=True, index=True,
    )
    verified_independently: Mapped[Optional[bool]] = mapped_column(nullable=True, default=None)
    still_has_concerns: Mapped[Optional[bool]] = mapped_column(nullable=True, default=None)
    result_useful: Mapped[Optional[bool]] = mapped_column(nullable=True, default=None)
    result_clear: Mapped[Optional[bool]] = mapped_column(nullable=True, default=None)
    note: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    submitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now_utc, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now_utc, onupdate=_now_utc, nullable=False
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<CaseFeedback case={self.case_id!r}>"
