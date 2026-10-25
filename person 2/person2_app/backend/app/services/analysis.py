"""
app/services/analysis.py
────────────────────────
Segment 5: Analysis service — integration layer between the application and
Person 1's AI detection module.

Integration status
──────────────────
Person 1's module (audio deepfake detection, OCR, payment reconciliation,
scam-context rules) is NOT available in this repository at the time of
Segment 5 implementation.

This file defines:
  1. The typed integration CONTRACT that Person 1's module must satisfy.
  2. A safe UNAVAILABLE adapter that returns a clearly-labelled placeholder
     when the module is absent — never fabricated findings.
  3. The MODULE_REGISTRY that switches between the real adapter and the stub.

When Person 1 delivers the module:
  - Create `app/services/analysis_person1.py` implementing `AnalysisAdapter`.
  - Register it in MODULE_REGISTRY below.
  - Set ANALYSIS_MODULE_ENABLED=true in .env.
  - No other application code needs to change.

What this service NEVER does
─────────────────────────────
- Invent confidence scores, deepfake labels, or risk verdicts.
- Execute uploaded files.
- Send evidence to an external API unless explicitly configured and disclosed.
- Report COMPLETED when processing was skipped or failed.
"""

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from app.config import settings

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# §1  Integration contract — Person 1 must satisfy this interface
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class AnalysisFinding:
    """
    A single finding produced by an analysis module.

    Fields
    ──────
    finding_type    — machine-readable type code, e.g. "audio_quality",
                      "deepfake_score", "ocr_field".
    label           — short human-readable description of what was detected.
    detail          — longer explanation (may include observed values).
    evidence_source — which aspect of the file produced this finding,
                      e.g. "audio_track", "page_1_text".
    is_interpretation — True if this is an inferred interpretation rather than
                        a directly observed measurement.  MUST be flagged
                        clearly in the UI.
    reliability_note  — text from the module describing reliability limits,
                        e.g. "Result valid only for ≥ 10 s recordings."
                        None if the module does not supply one.
    correlation_group — findings on one evidence item that share a group were
                        derived from the same reading (e.g. one OCR pass) and
                        are NOT independent confirmations of each other.
                        None if the finding is not grouped.
    """
    finding_type: str
    label: str
    detail: str
    evidence_source: str
    is_interpretation: bool = False
    reliability_note: Optional[str] = None
    correlation_group: Optional[str] = None


@dataclass
class ModuleResult:
    """
    Full result object returned by a conforming analysis module.

    Fields
    ──────
    module_name     — stable identifier for the module, e.g. "satyalens_audio_v1".
    module_version  — semver string, e.g. "1.0.0".
    findings        — list of AnalysisFinding objects (may be empty).
    limitations     — free-text description of what the module cannot determine.
    processed_at    — UTC timestamp of when processing completed.
    is_stub         — MUST be True only for UnavailableAdapter results.
                      Real modules must set this to False.
    payment         — structured payment-screenshot summary (extracted fields,
                      expected-value comparisons, image quality), or None when
                      no payment screenshot was analysed.
    """
    module_name: str
    module_version: str
    findings: list[AnalysisFinding] = field(default_factory=list)
    limitations: str = ""
    processed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    is_stub: bool = False
    payment: Optional[dict] = None


class AnalysisAdapter(ABC):
    """
    Abstract base that every analysis module adapter must implement.

    Person 1: subclass this, implement `analyse`, set is_available = True.
    """

    #: Must be True for real adapters; False for UnavailableAdapter.
    is_available: bool = False

    @abstractmethod
    def analyse(
        self, file_path: Path, media_type: str, evidence_id: str,
        expected_payment: Optional[dict] = None,
    ) -> ModuleResult:
        """
        Run analysis on the evidence file and return a ModuleResult.

        Parameters
        ──────────
        file_path        — absolute path to the evidence file on disk.
        media_type       — client-declared MIME type (already validated upstream).
        evidence_id      — UUID of the Evidence row; for traceability only.
        expected_payment — optional normalised user expectations for a payment
                           screenshot (amount, currency, recipient, reference).

        Raises
        ──────
        AnalysisModuleError — on any failure within the module.
        """


class AnalysisModuleError(Exception):
    """Raised by an adapter when analysis fails."""


# ═══════════════════════════════════════════════════════════════════════════════
# §2  UnavailableAdapter — safe placeholder when Person 1's module is absent
# ═══════════════════════════════════════════════════════════════════════════════

class UnavailableAdapter(AnalysisAdapter):
    """
    Returns a clearly-labelled UNAVAILABLE placeholder.

    This adapter NEVER fabricates findings.  Its result carries is_stub=True
    so the route layer can persist status=UNAVAILABLE.  The UI must display
    this state explicitly and never present it as completed analysis.
    """

    is_available: bool = False

    def analyse(
        self, file_path: Path, media_type: str, evidence_id: str,
        expected_payment: Optional[dict] = None,
    ) -> ModuleResult:
        return ModuleResult(
            module_name="unavailable",
            module_version="0.0.0",
            findings=[],
            limitations=(
                "The analysis module (Person 1) has not been integrated into this "
                "deployment. No analysis has been performed and no findings are "
                "available. Results will show 'Analysis unavailable' until the "
                "module is installed and configured."
            ),
            is_stub=True,
        )


# ═══════════════════════════════════════════════════════════════════════════════
# §3  Module registry — switch real/stub via config
# ═══════════════════════════════════════════════════════════════════════════════

def _load_adapter() -> AnalysisAdapter:
    """
    Return the appropriate adapter based on configuration.

    When ANALYSIS_MODULE_ENABLED=true, attempt to import Person 1's adapter
    from `app.services.analysis_person1`.  If that import fails, log the error
    and fall back to UnavailableAdapter so the rest of the system keeps running.
    """
    if not settings.analysis_module_enabled:
        logger.info("Analysis module disabled by config; using UnavailableAdapter.")
        return UnavailableAdapter()

    try:
        from app.services import analysis_person1  # type: ignore[import]
        adapter = analysis_person1.get_adapter()
        logger.info(
            "Loaded Person 1 analysis adapter: %s",
            getattr(adapter, "__class__", type(adapter)).__name__,
        )
        return adapter
    except ImportError as exc:
        # Log the real cause: an import cycle or a missing dependency also
        # raises ImportError, not only a missing analysis_person1.py.
        logger.warning(
            "ANALYSIS_MODULE_ENABLED=true but app/services/analysis_person1.py "
            "could not be imported (%s).  Falling back to UnavailableAdapter.", exc,
        )
        return UnavailableAdapter()
    except Exception:
        logger.exception("Failed to load Person 1 analysis adapter; using UnavailableAdapter.")
        return UnavailableAdapter()


# Singleton — instantiated once at import time.
_adapter: AnalysisAdapter = _load_adapter()


# ═══════════════════════════════════════════════════════════════════════════════
# §4  Public service function
# ═══════════════════════════════════════════════════════════════════════════════

def run_analysis(
    file_path: Path,
    media_type: str,
    evidence_id: str,
    expected_payment: Optional[dict] = None,
) -> ModuleResult:
    """
    Run analysis via the configured adapter.

    Returns a ModuleResult.  Never raises — failures are captured in the
    ModuleResult.limitations / is_stub fields.

    Safe by design:
    - Never executes the uploaded file.
    - Never sends data externally unless the Person 1 adapter explicitly does so
      with user disclosure.
    - On any unexpected exception, returns a stub result with is_stub=True so
      the caller can persist status=FAILED.
    """
    try:
        if expected_payment is None:
            # Positional-only call keeps adapters written before expected_payment working.
            return _adapter.analyse(file_path, media_type, evidence_id)
        return _adapter.analyse(file_path, media_type, evidence_id, expected_payment=expected_payment)
    except AnalysisModuleError as exc:
        logger.warning("AnalysisModuleError for evidence=%s: %s", evidence_id, exc)
        raise
    except Exception:
        logger.exception("Unexpected error in analysis adapter for evidence=%s", evidence_id)
        raise


def module_is_available() -> bool:
    """Return True if the configured adapter is a real (non-stub) module."""
    return _adapter.is_available


def findings_to_json(findings: list[AnalysisFinding]) -> Optional[str]:
    """Serialise findings to a JSON string, or return None for an empty list."""
    if not findings:
        return None
    return json.dumps(
        [
            {
                "finding_type":     f.finding_type,
                "label":            f.label,
                "detail":           f.detail,
                "evidence_source":  f.evidence_source,
                "is_interpretation": f.is_interpretation,
                "reliability_note": f.reliability_note,
                "correlation_group": f.correlation_group,
            }
            for f in findings
        ],
        ensure_ascii=False,
    )


# ── Validated finding types ───────────────────────────────────────────────────

# Required string fields in a valid finding dict
_REQUIRED_FINDING_FIELDS: frozenset[str] = frozenset({
    "finding_type", "label", "detail", "evidence_source",
})


def _validate_finding_dict(raw: object, index: int) -> Optional[dict]:
    """
    FIX (Issue 5): Strict validation of a single finding dict.

    Rules:
    - Must be a dict.
    - Required string fields must all be present and be actual str values.
    - is_interpretation must be an actual bool; reject strings like "false"
      because bool("false") == True which would silently misclassify findings.
    - reliability_note must be str or None if present.
    - No numeric risk scores are accepted (they are not part of the contract).

    Returns the validated dict, or None to signal the entry should be discarded.
    Discarded entries are logged at WARNING — never silently swallowed.
    """
    if not isinstance(raw, dict):
        logger.warning("Finding[%d]: not a dict (type=%s) — discarded.", index, type(raw).__name__)
        return None

    for field_name in _REQUIRED_FINDING_FIELDS:
        val = raw.get(field_name)
        if not isinstance(val, str) or not val:
            logger.warning(
                "Finding[%d]: field %r is missing or not a non-empty string "
                "(got %r of type %s) — discarded.",
                index, field_name, val, type(val).__name__,
            )
            return None

    # is_interpretation: must be an actual bool, not a string
    is_interp = raw.get("is_interpretation", False)
    if not isinstance(is_interp, bool):
        logger.warning(
            "Finding[%d]: is_interpretation is not a bool (got %r type=%s). "
            "bool() coercion of strings like 'false' produces True. Discarding.",
            index, is_interp, type(is_interp).__name__,
        )
        return None

    # reliability_note: optional, but if present must be str or None
    rel_note = raw.get("reliability_note", None)
    if rel_note is not None and not isinstance(rel_note, str):
        logger.warning(
            "Finding[%d]: reliability_note is not str or None (got %r) — "
            "setting to None.",
            index, rel_note,
        )
        raw = dict(raw)
        raw["reliability_note"] = None

    # correlation_group: optional (absent in rows stored before it existed)
    group = raw.get("correlation_group")
    if group is not None and not isinstance(group, str):
        logger.warning("Finding[%d]: correlation_group is not str or None — setting to None.", index)
        group = None

    # Build a clean, safe copy with only expected fields
    return {
        "finding_type":      raw["finding_type"],
        "label":             raw["label"],
        "detail":            raw["detail"],
        "evidence_source":   raw["evidence_source"],
        "is_interpretation": is_interp,
        "reliability_note":  raw.get("reliability_note"),
        "correlation_group": group,
    }


def findings_from_json(raw: Optional[str]) -> list[dict]:
    """
    Deserialise findings JSON to a validated list of finding dicts.

    FIX (Issue 5): each finding is now run through _validate_finding_dict so
    that type errors (e.g. is_interpretation="false") are caught before the
    decision engine sees them, rather than silently producing wrong results.
    """
    if not raw:
        return []
    try:
        data = json.loads(raw)
        if not isinstance(data, list):
            logger.warning("findings_json is not a JSON array — returning empty list.")
            return []
    except (json.JSONDecodeError, TypeError):
        logger.warning("Could not deserialise findings_json; returning empty list.")
        return []

    validated: list[dict] = []
    for i, entry in enumerate(data):
        result = _validate_finding_dict(entry, i)
        if result is not None:
            validated.append(result)
    return validated
