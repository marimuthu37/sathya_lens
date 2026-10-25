"""
app/services/analysis_person1.py
────────────────────────────────
Adapter between the application and Person 1's `satyalens_analysis` package
(see INTEGRATION_CONTRACT.md, schema version 1.0).

Loaded by `app.services.analysis._load_adapter()` only when
ANALYSIS_MODULE_ENABLED=true.  Disabled by default.

Evidence routing (one validated evidence file per call)
───────────────────────────────────────────────────────
  image/*     → run_analysis(screenshot_path=...)   payment OCR
  audio/*     → run_analysis(audio_path=...)        audio (currently always
                                                    'unavailable' upstream)
  text/plain  → run_analysis(message_text=...)      context rules + ML text
                                                    classifier (UTF-8 only)
  anything else (PDF, video) → no component applies → stub result

Finding policy
──────────────
Only genuine concern signals become AnalysisFinding objects, because the
decision engine treats ANY finding as usable evidence (a lone non-concern
finding yields LOW_CONCERN).  Benign predictions, field matches, unavailable
or not-applicable components are written to `limitations` instead.

  payment.mismatches[]                    → payment_mismatch
  user-supplied expected payment details  → payment_mismatch (compared here, in
                                            payment_checks.py — they are NOT
                                            passed to run_analysis, whose own
                                            comparison treats an unread field
                                            as a mismatch and ignores OCR
                                            reliability)
  payment consistency observations        → ocr_mismatch   (interpretation;
                                            see app/services/payment_checks.py)
  payment.quality warnings                → notes only, never findings
  context.flags[]                         → script_match   (interpretation)
  text_classifier predicted 'suspicious'  → script_match   (interpretation)
  audio finding 'spoof_likely'            → deepfake_score (interpretation)

Context flags are keyword rules and the text classifier has NOT passed
real-world evaluation, so both map to `script_match` (VERIFY_FIRST), never to
the HIGH_RISK `scam_pattern` type.  Both analyse the same text, so they share
one finding type and cannot escalate each other.

Status policy
─────────────
- No relevant component ran (all 'unavailable' / 'not_applicable')
    → ModuleResult(is_stub=True); the route persists status=UNAVAILABLE.
- Every relevant component that was attempted 'failed'
    → AnalysisModuleError; the route persists status=FAILED.
- Otherwise → completed result (possibly with zero findings, which the
  decision engine treats as inconclusive, not reassuring).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable, Optional

from app.services.analysis import (
    AnalysisAdapter,
    AnalysisFinding,
    AnalysisModuleError,
    ModuleResult,
)
from app.services.payment_checks import evaluate_payment

logger = logging.getLogger(__name__)

MODULE_NAME = "satyalens_analysis"
SUPPORTED_SCHEMA_VERSION = "1.0"

# Upper bound on text evidence passed to the rule engine / classifier.
MAX_MESSAGE_CHARS = 20_000

# Keys the contract guarantees are always present.
_REQUIRED_TOP_LEVEL_KEYS = frozenset({
    "schema_version", "case_id", "audio", "payment", "context",
    "text_classifier", "errors",
})
_REQUIRED_PAYMENT_KEYS = frozenset({
    "status", "raw_text", "fields", "mismatches", "limitations",
})

# Component statuses meaning "no analysis was performed".
_NOT_RUN_STATUSES = frozenset({"unavailable", "not_applicable"})

_TEXT_MEDIA_TYPES = frozenset({"text/plain"})

_RunAnalysisFn = Callable[..., dict]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _normalise_media_type(media_type: str) -> str:
    return (media_type or "").split(";")[0].strip().lower()


def _components_for(media_type: str) -> tuple[str, ...]:
    """Return the Person 1 result keys relevant to this media type."""
    if media_type.startswith("image/"):
        return ("payment",)
    if media_type.startswith("audio/"):
        return ("audio",)
    if media_type in _TEXT_MEDIA_TYPES:
        return ("context", "text_classifier")
    return ()


def _read_message_text(file_path: Path) -> str:
    """Read a text evidence file as strict UTF-8 (BOM tolerated)."""
    try:
        text = file_path.read_bytes().decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise AnalysisModuleError(
            "Text evidence is not valid UTF-8; it was not analysed."
        ) from exc
    except OSError as exc:
        raise AnalysisModuleError("Text evidence could not be read from storage.") from exc
    if len(text) > MAX_MESSAGE_CHARS:
        raise AnalysisModuleError(
            f"Text evidence exceeds {MAX_MESSAGE_CHARS} characters; it was not analysed."
        )
    return text


def _validate_contract(result: Any) -> dict:
    """Reject results that do not satisfy the schema 1.0 contract."""
    if not isinstance(result, dict):
        raise AnalysisModuleError("Analysis package returned a non-dict result.")
    missing = _REQUIRED_TOP_LEVEL_KEYS - result.keys()
    if missing:
        raise AnalysisModuleError(
            f"Analysis result is missing contract keys: {', '.join(sorted(missing))}."
        )
    if result["schema_version"] != SUPPORTED_SCHEMA_VERSION:
        raise AnalysisModuleError(
            f"Unsupported analysis schema_version {result['schema_version']!r} "
            f"(expected {SUPPORTED_SCHEMA_VERSION!r})."
        )
    payment = result["payment"]
    if not isinstance(payment, dict) or _REQUIRED_PAYMENT_KEYS - payment.keys():
        raise AnalysisModuleError("Analysis result 'payment' object does not match the contract.")
    for key in ("audio", "context", "text_classifier"):
        if not isinstance(result[key], dict) or "status" not in result[key]:
            raise AnalysisModuleError(f"Analysis result '{key}' object does not match the contract.")
    return result


def _component_limitations(name: str, component: dict) -> list[str]:
    lines = [f"[{name}] status: {component.get('status')}."]
    reason = component.get("reason")
    if isinstance(reason, str) and reason:
        lines.append(f"[{name}] {reason}")
    for item in component.get("limitations") or []:
        if isinstance(item, str) and item:
            lines.append(f"[{name}] {item}")
    return lines


# ── Per-component finding extraction ──────────────────────────────────────────

def _quality_notes(quality: Optional[dict]) -> list[str]:
    """Image-quality warnings as notes — kept apart from (fraud-related) findings."""
    if not isinstance(quality, dict):
        return ["[quality] Image quality was not assessed by this package version."]
    lines = [f"[quality] Image quality assessment: {quality.get('assessment', 'unknown')}."]
    for w in quality.get("warnings") or []:
        if isinstance(w, dict) and isinstance(w.get("message"), str):
            lines.append(f"[quality] ({w.get('severity', '?')}) {w['message']}")
    return lines


def _payment_findings(
    payment: dict, notes: list[str], expected_payment: Optional[dict] = None,
) -> tuple[list[AnalysisFinding], Optional[dict]]:
    if payment["status"] != "analysed":
        return [], None
    findings, payment_notes, summary = evaluate_payment(payment, expected_payment)
    notes.extend(payment_notes)
    notes.extend(_quality_notes(summary["quality"]))
    return findings, summary


def _context_findings(context: dict, notes: list[str]) -> list[AnalysisFinding]:
    if context.get("status") != "analysed":
        return []
    findings: list[AnalysisFinding] = []
    for flag in context.get("flags") or []:
        if not isinstance(flag, dict):
            continue
        label = flag.get("label") or flag.get("rule_id")
        detail = flag.get("explanation") or label
        if not (isinstance(label, str) and label and isinstance(detail, str) and detail):
            continue
        findings.append(AnalysisFinding(
            finding_type="script_match",
            label=f"Scam-context rule: {label}",
            detail=detail,
            evidence_source="message_text",
            is_interpretation=True,
            reliability_note=(
                f"Deterministic keyword/pattern rule {flag.get('rule_id', '?')} "
                f"(severity: {flag.get('severity', 'unknown')}). Not proof of fraud."
            ),
        ))
    if not findings:
        notes.append(
            "[context] No scam-context rules fired. This does NOT confirm the "
            "message is legitimate."
        )
    return findings


def _text_classifier_findings(tc: dict, notes: list[str]) -> list[AnalysisFinding]:
    if tc.get("status") != "analysed":
        return []
    predicted = tc.get("predicted_class")
    model_version = tc.get("model_version") or "unknown"
    if predicted != "suspicious":
        notes.append(
            f"[text_classifier] Predicted '{predicted}' (model {model_version}). "
            "A 'legitimate' prediction does NOT mean the message is safe."
        )
        return []
    terms = [
        t["term"] for t in (tc.get("top_terms") or [])
        if isinstance(t, dict) and isinstance(t.get("term"), str) and (t.get("weight") or 0) > 0
    ]
    detail = f"Model {model_version} classified the message as resembling suspicious examples."
    if terms:
        detail += f" Most influential terms: {', '.join(terms)}."
    return [AnalysisFinding(
        finding_type="script_match",
        label="ML text classifier: message resembles suspicious examples",
        detail=detail,
        evidence_source="message_text",
        is_interpretation=True,
        reliability_note=(
            "Trained on synthetic data and has not passed real-world evaluation; "
            "score is uncalibrated. Correlated with the scam-context rules (same "
            "text) — not an independent confirmation. Requires human review."
        ),
    )]


def _audio_findings(audio: dict, notes: list[str]) -> list[AnalysisFinding]:
    if audio.get("status") != "analysed":
        return []
    if audio.get("finding") != "spoof_likely":
        notes.append(
            f"[audio] Model finding '{audio.get('finding')}'. This does NOT prove "
            "the audio is genuine."
        )
        return []
    return [AnalysisFinding(
        finding_type="deepfake_score",
        label="Audio model output favours spoofed / synthetic speech",
        detail=audio.get("reason") or "Audio anti-spoofing model returned spoof_likely.",
        evidence_source="audio_track",
        is_interpretation=True,
        reliability_note=f"Model reliability: {audio.get('reliability', 'unknown')}.",
    )]


_EXTRACTORS = {
    "context": _context_findings,
    "text_classifier": _text_classifier_findings,
    "audio": _audio_findings,
}


# ── Adapter ───────────────────────────────────────────────────────────────────

class Person1Adapter(AnalysisAdapter):
    """Runs `satyalens_analysis.run_analysis` on one validated evidence file."""

    is_available: bool = True

    def __init__(self, run_analysis_fn: _RunAnalysisFn, module_version: str) -> None:
        self._run_analysis = run_analysis_fn
        self._module_version = module_version

    def _stub(self, limitations: list[str]) -> ModuleResult:
        return ModuleResult(
            module_name=MODULE_NAME,
            module_version=self._module_version,
            findings=[],
            limitations="\n".join(limitations),
            is_stub=True,
        )

    def analyse(
        self, file_path: Path, media_type: str, evidence_id: str,
        expected_payment: Optional[dict] = None,
    ) -> ModuleResult:
        mtype = _normalise_media_type(media_type)
        components = _components_for(mtype)
        if not components:
            return self._stub([
                f"No SatyaLens analysis component supports media type '{mtype}'. "
                "No analysis was performed. This does NOT mean the evidence is safe."
            ])

        if not file_path.is_file():
            raise AnalysisModuleError("Evidence file is not available for analysis.")

        kwargs: dict[str, Optional[str]] = {}
        if components == ("payment",):
            kwargs["screenshot_path"] = str(file_path)
        elif components == ("audio",):
            kwargs["audio_path"] = str(file_path)
        else:
            kwargs["message_text"] = _read_message_text(file_path)

        result = _validate_contract(self._run_analysis(case_id=evidence_id, **kwargs))

        findings: list[AnalysisFinding] = []
        notes: list[str] = []
        payment_summary: Optional[dict] = None
        statuses = {name: result[name].get("status") for name in components}
        for name in components:
            notes.extend(_component_limitations(name, result[name]))
            if name == "payment":
                found, payment_summary = _payment_findings(result[name], notes, expected_payment)
                findings.extend(found)
            else:
                findings.extend(_EXTRACTORS[name](result[name], notes))
        for err in result["errors"] or []:
            notes.append(f"[errors] {err}")

        attempted = [s for s in statuses.values() if s not in _NOT_RUN_STATUSES]
        if not attempted:
            notes.insert(0, (
                "No SatyaLens component could analyse this evidence (unavailable or "
                "not applicable). This does NOT mean the evidence is genuine or safe."
            ))
            return self._stub(notes)
        if all(s == "failed" for s in attempted):
            raise AnalysisModuleError(
                "SatyaLens analysis failed for this evidence: "
                + "; ".join(f"{k}={v}" for k, v in statuses.items())
            )

        logger.info(
            "Person 1 analysis: evidence=%s components=%s findings=%d",
            evidence_id, statuses, len(findings),
        )
        return ModuleResult(
            module_name=MODULE_NAME,
            module_version=self._module_version,
            findings=findings,
            limitations="\n".join(notes),
            is_stub=False,
            payment=payment_summary,
        )


def get_adapter() -> Person1Adapter:
    """
    Entry point used by `app.services.analysis._load_adapter()`.

    Raises RuntimeError (not ImportError) when the package is missing so the
    loader logs the real cause instead of "analysis_person1.py was not found".
    """
    try:
        import satyalens_analysis
        from satyalens_analysis import run_analysis
    except ImportError as exc:
        raise RuntimeError(
            "satyalens_analysis is not installed in this environment; "
            "install it with: pip install -e \"<repo root>[ml]\""
        ) from exc
    version = f"{getattr(satyalens_analysis, '__version__', 'unknown')}+schema{SUPPORTED_SCHEMA_VERSION}"
    return Person1Adapter(run_analysis, version)
