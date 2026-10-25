"""
schema.py — Common response schema for SatyaLens Analysis Package
=================================================================

Defines the canonical AnalysisResult structure, all status/finding
enums, and helper constructors.  Every component (audio, payment,
context) builds a partial dict that is validated here before the
unified runner assembles the final response.

Status values (used consistently across all components)
-------------------------------------------------------
  analysed       — Component ran and produced a result.
  inconclusive   — Component ran but could not make a determination.
  failed         — Component raised an error during analysis.
  not_applicable — Input was not provided or not relevant.
  unavailable    — Required dependency / model is not installed.

Finding values (audio only)
---------------------------
  genuine_likely        — Model output favours genuine speech.
  spoof_likely          — Model output favours spoofed / synthetic speech.
  inconclusive          — Score within uncertainty band; no determination.
  (field absent when status is not_applicable/unavailable/failed)

Reliability values (audio only)
--------------------------------
  limited   — Model has known generalisation gaps; treat as signal only.
  moderate  — Standard benchmark quality; interpret with caution.
  (field absent when no model ran)

Predicted class values (text_classifier only)
---------------------------------------------
  suspicious  — Classifier output favours the 'suspicious' training label.
  legitimate  — Classifier output favours the 'legitimate' training label.
  (field absent unless status is 'analysed')

IMPORTANT: A status of 'failed' or 'unavailable' NEVER implies the
media is genuine or the request is safe.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class ComponentStatus(str, Enum):
    ANALYSED       = "analysed"
    INCONCLUSIVE   = "inconclusive"
    FAILED         = "failed"
    NOT_APPLICABLE = "not_applicable"
    UNAVAILABLE    = "unavailable"


class AudioFinding(str, Enum):
    GENUINE_LIKELY = "genuine_likely"
    SPOOF_LIKELY   = "spoof_likely"
    INCONCLUSIVE   = "inconclusive"


class AudioReliability(str, Enum):
    LIMITED  = "limited"
    MODERATE = "moderate"


# ---------------------------------------------------------------------------
# Typed sub-result dataclasses
# ---------------------------------------------------------------------------

@dataclass
class AudioResult:
    """Result returned by the audio detector."""
    status: ComponentStatus
    finding: Optional[AudioFinding] = None
    reliability: Optional[AudioReliability] = None
    reason: str = ""
    # Raw model score (logit or probability) — NOT a calibrated confidence.
    # Present only when status == ANALYSED.
    raw_score: Optional[float] = None
    # Input quality notes (e.g. duration, sample-rate issues).
    input_quality: List[str] = field(default_factory=list)
    limitations: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {"status": self.status.value}
        if self.finding is not None:
            d["finding"] = self.finding.value
        if self.reliability is not None:
            d["reliability"] = self.reliability.value
        d["reason"] = self.reason
        if self.raw_score is not None:
            d["raw_score"] = round(self.raw_score, 6)
        if self.input_quality:
            d["input_quality"] = self.input_quality
        if self.limitations:
            d["limitations"] = self.limitations
        return d


@dataclass
class PaymentField:
    """A single extracted field from a payment screenshot."""
    field_name: str
    extracted_value: Optional[str]
    expected_value: Optional[str]
    match: Optional[bool]      # None if expected_value was not supplied.
    confidence: str = "low"    # 'low' | 'medium' | 'high' — OCR confidence tier

    def to_dict(self) -> Dict[str, Any]:
        return {
            "field_name": self.field_name,
            "extracted_value": self.extracted_value,
            "expected_value": self.expected_value,
            "match": self.match,
            "confidence": self.confidence,
        }


@dataclass
class PaymentResult:
    """Result returned by the payment OCR component."""
    status: ComponentStatus
    raw_text: str = ""
    fields: List[PaymentField] = field(default_factory=list)
    mismatches: List[str] = field(default_factory=list)
    # Always populated: OCR, library and disclaimer caveats.
    limitations: List[str] = field(default_factory=list)
    # Image-quality assessment; present only when OCR ran (status 'analysed').
    quality: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        d = {
            "status": self.status.value,
            "raw_text": self.raw_text,
            "fields": [f.to_dict() for f in self.fields],
            "mismatches": self.mismatches,
            "limitations": self.limitations,
        }
        if self.quality is not None:
            d["quality"] = self.quality
        return d


@dataclass
class ContextFlag:
    """A single triggered scam-context rule."""
    rule_id: str       # Machine-readable identifier, e.g. "URGENCY_PRESSURE"
    label: str         # Human-readable short label
    severity: str      # 'low' | 'medium' | 'high'
    explanation: str   # Why this rule fired, based only on input text.

    def to_dict(self) -> Dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "label": self.label,
            "severity": self.severity,
            "explanation": self.explanation,
        }


@dataclass
class ContextResult:
    """Result returned by the financial scam-context analyser."""
    status: ComponentStatus
    flags: List[ContextFlag] = field(default_factory=list)
    findings: List[str] = field(default_factory=list)   # Narrative summaries
    limitations: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status.value,
            "flags": [f.to_dict() for f in self.flags],
            "findings": self.findings,
            "limitations": self.limitations,
        }


@dataclass
class TextClassifierResult:
    """Result returned by the ML text classifier (TF-IDF + Logistic Regression)."""
    status: ComponentStatus
    # 'suspicious' | 'legitimate' — present only when status == ANALYSED.
    predicted_class: Optional[str] = None
    # LogisticRegression.predict_proba() for the 'suspicious' class.
    # UNCALIBRATED — NOT a fraud probability. Present only when status == ANALYSED.
    raw_score: Optional[float] = None
    model_version: Optional[str] = None
    training_data: Optional[str] = None
    reason: str = ""
    # Terms that most influenced the score: {"term": str, "weight": float};
    # positive weight pushes toward 'suspicious', negative toward 'legitimate'.
    top_terms: List[Dict[str, Any]] = field(default_factory=list)
    limitations: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {"status": self.status.value}
        if self.predicted_class is not None:
            d["predicted_class"] = self.predicted_class
        if self.raw_score is not None:
            d["raw_score"] = round(self.raw_score, 6)
        if self.model_version is not None:
            d["model_version"] = self.model_version
        if self.training_data is not None:
            d["training_data"] = self.training_data
        d["reason"] = self.reason
        d["top_terms"] = self.top_terms
        d["limitations"] = self.limitations
        return d


# ---------------------------------------------------------------------------
# Top-level result
# ---------------------------------------------------------------------------

@dataclass
class AnalysisResult:
    """
    The complete, schema-v1.0 response returned by run_analysis().

    Fields
    ------
    schema_version : Always "1.0".
    case_id        : Caller-supplied identifier; never inspected here.
    audio          : AudioResult
    payment        : PaymentResult
    context        : ContextResult
    text_classifier: TextClassifierResult (additive key; same message text
                     as context, so the two findings are correlated)
    errors         : List of error strings that did NOT abort the run.
    """
    schema_version: str = "1.0"
    case_id: str = ""
    audio: AudioResult = field(default_factory=lambda: AudioResult(
        status=ComponentStatus.NOT_APPLICABLE,
        reason="No audio input provided.",
    ))
    payment: PaymentResult = field(default_factory=lambda: PaymentResult(
        status=ComponentStatus.NOT_APPLICABLE,
        limitations=["No screenshot provided."],
    ))
    context: ContextResult = field(default_factory=lambda: ContextResult(
        status=ComponentStatus.NOT_APPLICABLE,
        limitations=["No message text provided."],
    ))
    text_classifier: TextClassifierResult = field(default_factory=lambda: TextClassifierResult(
        status=ComponentStatus.NOT_APPLICABLE,
        reason="No message text provided.",
    ))
    errors: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "case_id": self.case_id,
            "audio": self.audio.to_dict(),
            "payment": self.payment.to_dict(),
            "context": self.context.to_dict(),
            "text_classifier": self.text_classifier.to_dict(),
            "errors": self.errors,
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def build_empty_result(case_id: str = "") -> AnalysisResult:
    """Return a fully populated AnalysisResult with all fields NOT_APPLICABLE."""
    return AnalysisResult(case_id=case_id)


def audio_unavailable(reason: str, limitations: Optional[List[str]] = None) -> AudioResult:
    """Convenience: build an AudioResult for when the model is unavailable."""
    return AudioResult(
        status=ComponentStatus.UNAVAILABLE,
        reason=reason,
        limitations=limitations or [],
    )


def audio_failed(reason: str) -> AudioResult:
    """Convenience: build an AudioResult for a runtime failure."""
    return AudioResult(
        status=ComponentStatus.FAILED,
        reason=reason,
        limitations=[
            "Analysis failed. This does NOT indicate the audio is genuine."
        ],
    )


def payment_failed(reason: str) -> PaymentResult:
    """Convenience: build a PaymentResult for a runtime failure."""
    return PaymentResult(
        status=ComponentStatus.FAILED,
        limitations=[
            reason,
            "Analysis failed. This does NOT confirm or deny payment settlement.",
        ],
    )


def context_failed(reason: str) -> ContextResult:
    """Convenience: build a ContextResult for a runtime failure."""
    return ContextResult(
        status=ComponentStatus.FAILED,
        findings=[],
        limitations=[reason, "Context analysis could not complete."],
    )


def text_classifier_failed(reason: str) -> TextClassifierResult:
    """Convenience: build a TextClassifierResult for a runtime failure."""
    return TextClassifierResult(
        status=ComponentStatus.FAILED,
        reason=reason,
        limitations=[
            "Classification failed. This does NOT indicate the message is legitimate."
        ],
    )
