"""
app/services/decision.py
────────────────────────
Segment 6: Transparent, deterministic decision engine.

What this service produces
──────────────────────────
One of four guidance categories for a case:
  LOW_CONCERN         — available evidence shows no specific concern indicators.
  VERIFY_FIRST        — one or more findings warrant independent verification.
  HIGH_RISK           — findings strongly suggest the interaction may be harmful.
  INSUFFICIENT_EVIDENCE — not enough usable analysis to produce a meaningful assessment.

What this service NEVER does
─────────────────────────────
- Invent confidence scores, accuracy values, or numeric risk thresholds.
- Claim that a match proves legitimacy or a mismatch proves fraud.
- Produce LOW_CONCERN when no meaningful analysis has been completed.
- Treat unavailable or failed analysis as negative evidence (absence ≠ proof of safety).
- Automatically block transactions or contact third parties.
- Make assertions about a person's intent or guilt.

Decision rule version
──────────────────────
RULE_VERSION is incremented when rule logic changes, enabling clients to detect
that a previously computed decision was made under different rules.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)

# Bump this whenever rule logic changes so callers can detect stale decisions.
# 1.1.0: payment-screenshot quality, expected-value comparisons and correlated
#        observations are reported (R6–R8); categories are unchanged by them.
RULE_VERSION = "1.1.0"

_SETTLEMENT_LIMITATION = (
    "A payment screenshot cannot confirm that a payment was made, received or "
    "settled. Check the payment in your own bank or payment app."
)

# finding_type values from Person 1's contract that raise concern when present.
# These are cross-referenced against FindingRead.finding_type.
# When Person 1's module delivers real types, extend this set.
CONCERN_FINDING_TYPES: frozenset[str] = frozenset({
    "deepfake_score",
    "audio_manipulation",
    "ocr_mismatch",
    "payment_mismatch",
    "scam_pattern",
    "script_match",
})

HIGH_RISK_FINDING_TYPES: frozenset[str] = frozenset({
    "deepfake_score",
    "audio_manipulation",
    "scam_pattern",
})


# ── Output types ──────────────────────────────────────────────────────────────

class GuidanceCategory(str, Enum):
    """
    The four guidance categories produced by the decision engine.
    These are advisory — they do not constitute proof of fraud or safety.
    """
    LOW_CONCERN          = "low_concern"
    VERIFY_FIRST         = "verify_first"
    HIGH_RISK            = "high_risk"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


@dataclass
class EvidenceReference:
    """Links a factor to its source evidence item."""
    evidence_id: str
    filename: str
    finding_type: Optional[str] = None
    finding_label: Optional[str] = None


@dataclass
class DecisionSummary:
    """
    Full output of the decision engine for a case.

    Fields
    ──────
    category           — the guidance category (GuidanceCategory).
    short_explanation  — one-sentence plain-language summary.
    factors_of_concern — findings that contributed to a raised concern level.
    factors_unknown    — items that could not be assessed (missing, unavailable,
                         failed analysis, or fields not provided).
    conflicts          — contradictory findings across evidence items.
    supporting_refs    — evidence items referenced in the decision.
    limitations        — what the system could not determine.
    next_steps         — practical independent-verification suggestions.
    generated_at       — UTC timestamp of decision generation.
    rule_version       — version of decision rules applied.
    """
    category: GuidanceCategory
    short_explanation: str
    factors_of_concern: list[str] = field(default_factory=list)
    factors_unknown: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    supporting_refs: list[EvidenceReference] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)
    next_steps: list[str] = field(default_factory=list)
    generated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    rule_version: str = RULE_VERSION


# ── Supporting types for calling context ─────────────────────────────────────

@dataclass
class EvidenceInput:
    """
    Flattened representation of one evidence item + its analysis result,
    as passed into the decision engine.
    """
    evidence_id: str
    filename: str
    media_type: str
    analysis_status: str           # AnalysisResultStatus value or "not_requested"
    findings: list[dict]           # list of FindingRead-compatible dicts
    payment: Optional[dict] = None  # PaymentSummaryRead-compatible dict, if any


@dataclass
class CaseContext:
    """
    Optional context from the case that may be compared with evidence findings.
    All fields are optional — missing values must be labelled 'not_provided',
    never assumed to match or mismatch.
    """
    description: Optional[str] = None
    # Future: expected_amount, payee, purpose — only add when the model supports them.


# ── Decision logic ────────────────────────────────────────────────────────────

def _safe_findings(raw: list[dict]) -> list[dict]:
    """Return only findings that have required fields; discard malformed ones."""
    valid = []
    for f in raw:
        if not isinstance(f, dict):
            continue
        if not all(k in f for k in ("finding_type", "label", "detail", "evidence_source")):
            logger.warning("Discarding malformed finding (missing required fields): %s", f)
            continue
        valid.append(f)
    return valid


def compute_decision(
    evidence_inputs: list[EvidenceInput],
    context: Optional[CaseContext] = None,
) -> DecisionSummary:
    """
    Apply deterministic rules to produce a DecisionSummary.

    Rule set (v1.0.0)
    ──────────────────
    R0. No evidence items at all → INSUFFICIENT_EVIDENCE.
    R1. All evidence has analysis status unavailable/failed/not_requested
        → INSUFFICIENT_EVIDENCE.  Never LOW_CONCERN.
    R2. At least one evidence item has usable findings (analysis completed
        with ≥1 finding):
          a. Any finding_type in HIGH_RISK_FINDING_TYPES → HIGH_RISK.
          b. Any finding_type in CONCERN_FINDING_TYPES   → VERIFY_FIRST.
          c. No concern findings                         → LOW_CONCERN
             (only if at least one completed analysis with findings exists).
    R3. Completed analysis but zero findings returned: counts as usable
        but inconclusive — does not alone justify LOW_CONCERN if other
        items are unassessed.
    R4. Conflicts: same finding_type appears with is_interpretation=True
        on one item and is_interpretation=False on another → surfaced as conflict.
    R5. If some items are assessed and some are not → VERIFY_FIRST at minimum
        (we do not know what the unassessed items contain).
    R6. Payment screenshots: image-quality limits, unread fields and expected
        values that were supplied but not compared are listed as unknowns.
        They never raise or lower the category on their own; a limited or
        unreadable image with no findings stays INSUFFICIENT_EVIDENCE.
    R7. Findings sharing a correlation_group on one item come from one
        reading (e.g. one OCR pass). They are reported as correlated; no rule
        counts findings, so several of them cannot escalate past what one
        of them would produce (payment types are never HIGH_RISK).
    R8. Whenever a payment screenshot was assessed, the decision states that a
        screenshot cannot confirm settlement.
    """
    factors_of_concern: list[str] = []
    factors_unknown: list[str] = []
    conflicts: list[str] = []
    supporting_refs: list[EvidenceReference] = []
    limitations: list[str] = []

    # ── R0: No evidence ───────────────────────────────────────────────────────
    if not evidence_inputs:
        return DecisionSummary(
            category=GuidanceCategory.INSUFFICIENT_EVIDENCE,
            short_explanation=(
                "No evidence has been uploaded for this case. "
                "Upload and analyse relevant files before requesting a decision."
            ),
            factors_unknown=["No evidence items attached to this case."],
            limitations=[
                "Without evidence, no assessment is possible.",
                "This system cannot make assumptions about the nature of a "
                "communication without analysed evidence.",
            ],
            next_steps=_standard_next_steps(),
        )

    # ── Classify each evidence item ───────────────────────────────────────────
    USABLE_STATUSES = {"completed"}
    UNUSABLE_STATUSES = {"unavailable", "failed", "pending", "running", "not_requested"}

    any_usable = False
    any_unusable = False
    any_real_findings = False  # True when at least one valid finding was processed
    any_zero_findings = False  # True when completed analysis returned 0 findings
    any_payment = False        # True when a payment screenshot was assessed
    highest_concern = GuidanceCategory.LOW_CONCERN

    # Track finding_type → [is_interpretation values] for conflict detection
    type_interp_map: dict[str, list[bool]] = {}

    for ev in evidence_inputs:
        ev_ref_base = EvidenceReference(evidence_id=ev.evidence_id, filename=ev.filename)

        if ev.analysis_status in UNUSABLE_STATUSES:
            any_unusable = True
            label = {
                "unavailable": "Analysis module unavailable",
                "failed":      "Analysis failed",
                "pending":     "Analysis pending",
                "running":     "Analysis still running",
                "not_requested": "Analysis not requested",
            }.get(ev.analysis_status, f"Analysis status: {ev.analysis_status}")
            factors_unknown.append(f"'{ev.filename}': {label} — content not assessed.")
            continue

        # Analysis completed
        valid_findings = _safe_findings(ev.findings)
        any_usable = True

        if isinstance(ev.payment, dict):
            any_payment = True
            factors_unknown.extend(_payment_unknowns(ev.filename, ev.payment))
        limitations.extend(_correlation_notes(ev.filename, valid_findings))

        if not valid_findings:
            # Completed but no specific findings — inconclusive, not reassuring.
            # We track this as usable=True (analysis ran) but finding_count=0.
            any_usable = True
            any_zero_findings = True
            factors_unknown.append(
                f"'{ev.filename}': Analysis completed but returned no specific findings."
            )
            supporting_refs.append(EvidenceReference(
                evidence_id=ev.evidence_id, filename=ev.filename,
                finding_type=None, finding_label="No findings returned",
            ))
            continue

        for f in valid_findings:
            any_real_findings = True
            ftype = f["finding_type"]
            is_interp = bool(f.get("is_interpretation", False))
            label = f["label"]
            detail = f.get("detail", "")
            reliability = f.get("reliability_note")

            # Track for conflict detection
            if ftype not in type_interp_map:
                type_interp_map[ftype] = []
            type_interp_map[ftype].append(is_interp)

            ref = EvidenceReference(
                evidence_id=ev.evidence_id,
                filename=ev.filename,
                finding_type=ftype,
                finding_label=label,
            )
            supporting_refs.append(ref)

            if ftype in HIGH_RISK_FINDING_TYPES:
                highest_concern = GuidanceCategory.HIGH_RISK
                concern_text = f"'{ev.filename}': {label}"
                if is_interp:
                    concern_text += " (interpretation — requires human review)"
                if reliability:
                    concern_text += f" [reliability: {reliability}]"
                factors_of_concern.append(concern_text)

            elif ftype in CONCERN_FINDING_TYPES:
                if highest_concern == GuidanceCategory.LOW_CONCERN:
                    highest_concern = GuidanceCategory.VERIFY_FIRST
                concern_text = f"'{ev.filename}': {label}"
                if is_interp:
                    concern_text += " (interpretation — requires human review)"
                if reliability:
                    concern_text += f" [reliability: {reliability}]"
                factors_of_concern.append(concern_text)

            else:
                # Non-concern finding — still surfaced for transparency
                factors_unknown.append(
                    f"'{ev.filename}': {label} "
                    f"(type '{ftype}' has no concern rule — check findings detail)"
                )

    # ── R4: Conflict detection ────────────────────────────────────────────────
    for ftype, interp_vals in type_interp_map.items():
        if len(set(interp_vals)) > 1:
            conflicts.append(
                f"Finding type '{ftype}' appears as both a direct measurement and "
                f"an interpretation across evidence items. Human review required."
            )

    # ── R5: Mixed assessed/unassessed ────────────────────────────────────────
    if any_usable and any_unusable:
        if highest_concern == GuidanceCategory.LOW_CONCERN:
            highest_concern = GuidanceCategory.VERIFY_FIRST
        factors_unknown.append(
            "Some evidence items were not analysed. "
            "The decision is based only on assessed items."
        )

    # ── R1: Nothing usable at all, or only zero-findings results → INSUFFICIENT_EVIDENCE
    # Per spec: prefer INSUFFICIENT_EVIDENCE when no meaningful findings are available.
    if not any_usable or (any_usable and not any_real_findings):
        if any_usable:
            # Analysis ran but found nothing specific: say so, not "not analysed".
            explanation = (
                "Analysis ran but returned no specific findings. That is not evidence "
                "that the communication or payment is genuine; there is not enough "
                "to reach an assessment."
            )
        else:
            explanation = (
                "No evidence items have usable analysis results. "
                "Ensure all evidence has been analysed before requesting a decision."
            )
        if any_usable:
            # Analysis ran: do not say it is unavailable or ask for it again.
            insufficient_limitations = [
                "Completed analysis produced no specific findings; that is not "
                "evidence that the communication or payment is genuine or safe.",
                "Analysis can only report what it can read and recognise in the evidence.",
            ]
            next_steps = _standard_next_steps()[1:]
        else:
            insufficient_limitations = [
                "Analysis is unavailable, failed, or not yet requested for all evidence.",
                "This system cannot produce a meaningful assessment without completed analysis.",
                "An unavailable or failed analysis does not mean the evidence is safe.",
            ]
            next_steps = _standard_next_steps()
        if any_payment:
            insufficient_limitations.append(_SETTLEMENT_LIMITATION)
            next_steps.append(_PAYMENT_NEXT_STEP)
        return DecisionSummary(
            category=GuidanceCategory.INSUFFICIENT_EVIDENCE,
            short_explanation=explanation,
            factors_unknown=factors_unknown,
            limitations=insufficient_limitations,
            next_steps=next_steps,
        )

    # ── Assign category and explanation ──────────────────────────────────────
    category = highest_concern
    explanation, next_steps = _explanation_and_steps(category, factors_of_concern, conflicts)
    if any_payment:
        limitations.append(_SETTLEMENT_LIMITATION)
        next_steps.append(_PAYMENT_NEXT_STEP)

    limitations.extend(_standard_limitations())
    if any_unusable:
        limitations.append(
            "Some evidence items were not assessed. The decision reflects only "
            "evidence with completed analysis."
        )

    return DecisionSummary(
        category=category,
        short_explanation=explanation,
        factors_of_concern=factors_of_concern,
        factors_unknown=factors_unknown,
        conflicts=conflicts,
        supporting_refs=supporting_refs,
        limitations=limitations,
        next_steps=next_steps,
    )


# ── Payment-screenshot helpers ────────────────────────────────────────────────

_PAYMENT_NEXT_STEP = (
    "Confirm the payment's status, amount and recipient in your own bank or "
    "payment app — not from the screenshot."
)


def _payment_unknowns(filename: str, payment: dict) -> list[str]:
    """R6: what the payment screenshot could not tell us (never a concern)."""
    out: list[str] = []
    quality = payment.get("quality")
    if isinstance(quality, dict) and quality.get("assessment") in ("limited", "unreadable"):
        codes = [w.get("code") for w in quality.get("warnings") or [] if isinstance(w, dict)]
        out.append(
            f"'{filename}': screenshot quality is {quality['assessment']} "
            f"({', '.join(c for c in codes if c) or 'see analysis'}); extracted values "
            "may be missing or misread."
        )
    elif quality is None:
        out.append(f"'{filename}': screenshot quality was not assessed.")

    comparisons = [c for c in payment.get("comparisons") or [] if isinstance(c, dict)]
    supplied = [c for c in comparisons if c.get("status") != "not_supplied"]
    if not supplied:
        out.append(
            f"'{filename}': no expected payment details were supplied, so the "
            "screenshot was not compared with what you expected."
        )
    for c in supplied:
        if c.get("status") == "not_compared":
            out.append(f"'{filename}': expected {c.get('field')} was not compared — {c.get('detail')}")
    return out


def _correlation_notes(filename: str, findings: list[dict]) -> list[str]:
    """R7: say when several findings are one line of evidence."""
    groups: dict[str, int] = {}
    for f in findings:
        group = f.get("correlation_group")
        if isinstance(group, str) and f.get("finding_type") in CONCERN_FINDING_TYPES:
            groups[group] = groups.get(group, 0) + 1
    return [
        f"'{filename}': {n} observations come from the same reading ({group}). They "
        "are correlated and count as one line of evidence, not as independent "
        "confirmations."
        for group, n in sorted(groups.items()) if n > 1
    ]


# ── Text helpers ──────────────────────────────────────────────────────────────

def _explanation_and_steps(
    category: GuidanceCategory,
    factors: list[str],
    conflicts: list[str],
) -> tuple[str, list[str]]:
    if category == GuidanceCategory.HIGH_RISK:
        return (
            "One or more analysed evidence items contain findings associated with "
            "high-risk patterns. Independent verification is strongly recommended "
            "before taking any action.",
            [
                "Do not transfer money or share personal information until you have "
                "independently verified the request through an official channel.",
                "Contact your bank or the relevant institution directly using a "
                "phone number from their official website — not one provided in the message.",
                "Report the interaction to your local consumer protection or "
                "cybercrime authority.",
                "Consult a trusted person before acting.",
            ],
        )
    if category == GuidanceCategory.VERIFY_FIRST:
        return (
            "Some findings warrant independent verification before acting. "
            "This does not confirm fraudulent activity — further checks are advised.",
            [
                "Verify the request directly with the organisation or person through "
                "an independently obtained contact method.",
                "Do not use contact details (phone, email, website) provided within "
                "the suspicious communication itself.",
                "Check whether the payment details match what you expect through "
                "a separate, trusted channel.",
            ],
        )
    # LOW_CONCERN
    return (
        "No concern-linked patterns were detected in the available evidence. "
        "This does not confirm the interaction is safe — always verify independently.",
        [
            "Even when no specific patterns are detected, independently verify any "
            "unexpected payment or data request before acting.",
            "Never share OTPs, PINs, or account passwords with anyone.",
        ],
    )


def _standard_next_steps() -> list[str]:
    return [
        "Upload evidence and request analysis before seeking a decision.",
        "Independently verify the communication through an official channel.",
        "Do not share payment credentials or personal information.",
    ]


def _standard_limitations() -> list[str]:
    return [
        "This is a decision-support tool, not a legal or financial authority.",
        "Guidance categories are advisory. They do not prove fraud or authenticity.",
        "Analysis accuracy depends on the quality and completeness of the evidence.",
        "A LOW_CONCERN result does not guarantee the interaction is safe.",
        "A HIGH_RISK result does not prove malicious intent.",
    ]
