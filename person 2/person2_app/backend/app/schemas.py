"""
app/schemas.py
──────────────
Pydantic request / response schemas shared across routes.

Segment 1: error envelope + health.
Segment 2: Case and Evidence schemas added below.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, field_validator

# payment_inputs (not payment_checks): importing payment_checks here would cycle
# through app.services.analysis's import-time adapter loading.
from app.services.payment_inputs import normalise_amount, normalise_currency


# ── Generic error envelope ────────────────────────────────────────────────────

class ErrorDetail(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    error: ErrorDetail


# ── Health ────────────────────────────────────────────────────────────────────

class HealthResponse(BaseModel):
    status: str
    version: str
    environment: str


# ── Evidence schemas ──────────────────────────────────────────────────────────

class EvidenceRead(BaseModel):
    """Evidence metadata returned to the client."""
    id: str
    case_id: str
    original_filename: str
    media_type: str
    file_size_bytes: int
    sha256_hash: str
    created_at: datetime
    processing_status: str

    model_config = {"from_attributes": True}


# ── Case schemas ──────────────────────────────────────────────────────────────

class CaseCreate(BaseModel):
    """
    Body accepted by POST /api/cases.

    Only a free-text description is required at creation time.
    All status fields are initialised by the server.
    """
    description: Optional[str] = Field(
        default=None,
        max_length=2000,
        description="Optional human-readable description of the suspicious communication.",
        examples=["Received a call claiming to be from my bank asking me to transfer funds."],
    )

    @field_validator("description", mode="before")
    @classmethod
    def strip_description(cls, v: object) -> object:
        """Strip whitespace from string descriptions; pass non-strings through unchanged."""
        if isinstance(v, str):
            v = v.strip()
            return v if v else None
        return v  # let Pydantic handle type coercion / rejection


class CaseRead(BaseModel):
    """Full case representation returned to the client."""
    id: str
    created_at: datetime
    updated_at: datetime
    status: str
    consent_status: str
    consent_at: Optional[datetime]
    analysis_status: str
    decision: Optional[str]
    description: Optional[str]
    evidence_items: list[EvidenceRead] = []

    model_config = {"from_attributes": True}


class CaseList(BaseModel):
    """Paginated list of cases (evidence items omitted for brevity)."""
    id: str
    created_at: datetime
    updated_at: datetime
    status: str
    consent_status: str
    analysis_status: str
    decision: Optional[str]
    description: Optional[str]
    evidence_count: int = 0

    model_config = {"from_attributes": True}


class CaseListResponse(BaseModel):
    """Wrapper for the case list endpoint."""
    total: int
    items: list[CaseList]


# ── Consent schema ────────────────────────────────────────────────────────────

CONSENT_POLICY_VERSION = "1.0"

class ConsentRequest(BaseModel):
    """
    Body required by POST /api/cases/{case_id}/consent.

    FIX (Issue 2): The backend now requires an explicit acknowledgement field.
    Omitting it, or sending acknowledged=False, results in a 400 error.
    This prevents a frontend-only checkbox from being bypassed by a direct
    API call without the acknowledgement.

    What is stored:
      - Case ID (already in the URL)
      - Server-generated UTC timestamp
      - Consent policy version (server-side constant)

    What is NOT stored:
      - User identity, name, email, IP address
      - Any payment credentials or OTPs

    DEMO NOTE: No user identity is verified. This is a local/demo prototype.
    """
    acknowledged: bool = Field(
        ...,
        description=(
            "Must be true. The caller confirms they have read and understood "
            "the data-processing notice and give explicit consent to upload "
            "evidence for this case."
        ),
    )

    @field_validator("acknowledged")
    @classmethod
    def must_be_true(cls, v: bool) -> bool:
        if not v:
            raise ValueError(
                "Consent acknowledgement must be explicitly true. "
                "Set acknowledged=true after reading the consent notice."
            )
        return v


class ConsentResponse(BaseModel):
    """Response returned after recording (or confirming) consent."""
    case_id: str
    consent_status: str
    consent_at: Optional[datetime]
    consent_policy_version: str
    message: str


# ── Analysis schemas (Segment 5) ──────────────────────────────────────────────

class FindingRead(BaseModel):
    """
    A single finding from Person 1's analysis module.

    is_interpretation — True when this is an inferred conclusion rather than a
                        direct measurement.  The UI must label these clearly.
    reliability_note  — Module-supplied caveat on finding reliability.
    """
    finding_type: str
    label: str
    detail: str
    evidence_source: str
    is_interpretation: bool = False
    reliability_note: Optional[str] = None
    # Findings sharing a group come from one reading (e.g. one OCR pass) and are
    # NOT independent confirmations. None for ungrouped / older findings.
    correlation_group: Optional[str] = None


# ── Expected payment details (request) ────────────────────────────────────────

_CONTROL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f]")
_REFERENCE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 #\-_/.:]*$")


class ExpectedPaymentIn(BaseModel):
    """
    What the user expected to pay, supplied optionally alongside a payment
    screenshot.  Every field is optional; blank strings become null.  A field
    that is null is never compared and is never treated as a mismatch.

    amount    — string, Decimal-parsed ("100", "100.50", "1,000.00"). JSON
                numbers are rejected so no binary float is ever involved.
    currency  — ISO code (EUR, USD, GBP, INR, …) or €, £, ₹. Shared symbols
                ($, ¥, kr, Rs) are rejected as ambiguous.
    recipient — expected payee / merchant name.
    reference — expected transaction reference.
    """
    model_config = {"extra": "forbid"}

    amount: Optional[str] = Field(default=None, max_length=32, examples=["25.00"])
    currency: Optional[str] = Field(default=None, max_length=8, examples=["EUR"])
    recipient: Optional[str] = Field(default=None, max_length=200, examples=["Amazon Europe"])
    reference: Optional[str] = Field(default=None, max_length=64, examples=["TID-7823419"])

    @field_validator("amount", "currency", "recipient", "reference", mode="before")
    @classmethod
    def blank_to_none(cls, v: object) -> object:
        if isinstance(v, str):
            v = " ".join(v.split())
            return v or None
        return v  # non-strings (e.g. JSON numbers) are rejected by the str type

    @field_validator("amount")
    @classmethod
    def valid_amount(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        return format(normalise_amount(v), "f")

    @field_validator("currency")
    @classmethod
    def valid_currency(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        return normalise_currency(v)

    @field_validator("recipient")
    @classmethod
    def valid_recipient(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        if _CONTROL_CHARS_RE.search(v) or not any(c.isalnum() for c in v):
            raise ValueError("Recipient must contain letters or digits and no control characters.")
        return v

    @field_validator("reference")
    @classmethod
    def valid_reference(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        if not _REFERENCE_RE.match(v):
            raise ValueError(
                "Reference may contain only letters, digits, spaces and # - _ / . : "
                "and must start with a letter or digit."
            )
        return v

    def supplied(self) -> Optional[dict]:
        """Normalised dict of the supplied values, or None if nothing was supplied."""
        data = self.model_dump()
        return data if any(v is not None for v in data.values()) else None


class AnalysisRequestBody(BaseModel):
    """Optional body for POST .../analyse. Omitting it keeps the original behaviour."""
    model_config = {"extra": "forbid"}

    expected_payment: Optional[ExpectedPaymentIn] = None


# ── Payment summary (response) ────────────────────────────────────────────────

class PaymentExtractedFieldRead(BaseModel):
    field_name: str
    extracted_value: Optional[str]   # raw OCR text — never normalised
    confidence: str                  # Tesseract-derived tier, NOT a probability of correctness


class PaymentComparisonRead(BaseModel):
    field: str                       # amount | currency | recipient | reference
    expected: Optional[str]
    extracted: Optional[str]
    extracted_confidence: Optional[str]
    status: str                      # not_supplied | not_compared | match | format_difference | partial_match | mismatch
    ran: bool                        # True only when a comparison was actually performed
    detail: str


class QualityWarningRead(BaseModel):
    code: str
    severity: str                    # caution | severe
    message: str


class PaymentQualityRead(BaseModel):
    assessment: str                  # no_issues_detected | limited | unreadable
    metrics: dict[str, Optional[float]] = {}
    warnings: list[QualityWarningRead] = []
    note: Optional[str] = None


class PaymentSummaryRead(BaseModel):
    """
    Structured payment-screenshot record.  settlement_status is always
    'not_determinable': a screenshot cannot show that a payment settled.
    Image-quality warnings are NOT fraud findings.
    """
    extraction_status: str           # fields_extracted | no_fields_recognised | no_text
    settlement_status: str
    settlement_note: str
    extracted_fields: list[PaymentExtractedFieldRead] = []
    expected_supplied: dict[str, Optional[str]] = {}
    comparisons: list[PaymentComparisonRead] = []
    quality: Optional[PaymentQualityRead] = None
    correlation_group: Optional[str] = None


class AnalysisResultRead(BaseModel):
    """
    Full analysis result for one evidence item.

    Honesty constraints
    ────────────────────
    • findings is an empty list when status is unavailable, pending, running, or failed.
    • module_name = "unavailable" when the analysis module is not installed.
    • is_stub = True when no real analysis has occurred.
    • The UI must never present stub results as completed analysis.
    • SHA-256 does not prove authenticity; analysis does not prove authenticity.
    """
    id: str
    evidence_id: str
    status: str                         # AnalysisResultStatus value
    requested_at: datetime
    completed_at: Optional[datetime]
    module_name: Optional[str]
    module_version: Optional[str]
    findings: list[FindingRead] = []
    limitations: Optional[str]
    error_message: Optional[str]
    is_stub: bool = True                # True until a real module is installed
    module_available: bool = False      # reflects settings.analysis_module_enabled
    # Additive (2026-10-25): present only for analysed payment screenshots.
    payment: Optional[PaymentSummaryRead] = None

    model_config = {"from_attributes": True}


class AnalysisRequestResponse(BaseModel):
    """Response from POST .../analyse — confirms the request was accepted."""
    evidence_id: str
    result_id: str
    status: str
    message: str
    module_available: bool


# ── Decision schemas (Segment 6) ──────────────────────────────────────────────

class EvidenceReferenceRead(BaseModel):
    """Links a decision factor to its source evidence item."""
    evidence_id: str
    filename: str
    finding_type: Optional[str] = None
    finding_label: Optional[str] = None


class DecisionSummaryRead(BaseModel):
    """
    Full decision output for a case.

    Honesty constraints
    ────────────────────
    • category is advisory only — not proof of fraud or authenticity.
    • factors_of_concern list every finding that contributed to a raised level.
    • factors_unknown lists items that could NOT be assessed.
    • conflicts surfaces contradictory findings.
    • limitations explains what the system could not determine.
    • next_steps are independent-verification suggestions only.
    • No numeric risk score is ever present.
    • rule_version identifies which rule set produced this decision.
    """
    category: str                        # GuidanceCategory value
    short_explanation: str
    factors_of_concern: list[str] = []
    factors_unknown: list[str] = []
    conflicts: list[str] = []
    supporting_refs: list[EvidenceReferenceRead] = []
    limitations: list[str] = []
    next_steps: list[str] = []
    generated_at: datetime
    rule_version: str

# ── Checklist schemas (Segment 7) ─────────────────────────────────────────────

class ChecklistItemDef(BaseModel):
    """Static definition of one checklist item (server-side catalogue)."""
    item_id: str
    category: str
    text: str
    detail: Optional[str] = None
    india_only: bool = False


class ChecklistProgressRead(BaseModel):
    """Current checked/unchecked state of one item for a case."""
    item_id: str
    checked: bool
    checked_at: Optional[datetime] = None


class ChecklistStateRead(BaseModel):
    """
    Full checklist state for a case: canonical items + per-item progress.

    disclaimer: always present — completion does not prove safety.
    """
    case_id: str
    items: list[ChecklistItemDef]
    progress: list[ChecklistProgressRead]
    checklist_version: str
    disclaimer: str


class ChecklistUpdateRequest(BaseModel):
    """Body for PATCH /checklist/{item_id}."""
    checked: bool


# ── Feedback schemas (Segment 7) ──────────────────────────────────────────────

class FeedbackSubmit(BaseModel):
    """
    Body for POST/PUT /feedback.

    All boolean fields are nullable (tri-state: yes/no/not answered).
    Note is optional and capped at 500 characters.
    Fields NOT accepted: name, phone, email, credentials, payment info.
    """
    verified_independently: Optional[bool] = Field(
        default=None,
        description="Did you complete independent verification steps?",
    )
    still_has_concerns: Optional[bool] = Field(
        default=None,
        description="Do you still have concerns about this communication?",
    )
    result_useful: Optional[bool] = Field(
        default=None,
        description="Did you find the guidance result useful?",
    )
    result_clear: Optional[bool] = Field(
        default=None,
        description="Was the guidance result clearly explained?",
    )
    note: Optional[str] = Field(
        default=None,
        max_length=500,
        description="Optional short note (max 500 characters). Do not include credentials or personal data.",
    )

    @field_validator("note", mode="before")
    @classmethod
    def strip_note(cls, v: object) -> object:
        if isinstance(v, str):
            v = v.strip()
            return v if v else None
        return v


class FeedbackRead(BaseModel):
    """Feedback record returned to the client."""
    id: str
    case_id: str
    verified_independently: Optional[bool]
    still_has_concerns: Optional[bool]
    result_useful: Optional[bool]
    result_clear: Optional[bool]
    note: Optional[str]
    submitted_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
