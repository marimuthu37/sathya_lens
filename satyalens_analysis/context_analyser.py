"""
context_analyser.py — Financial Scam-Context Rule Analysis
===========================================================
Applies transparent, keyword-and-pattern-based rules to message/scenario
text supplied by the caller.  Rules are deterministic: every flag
includes the rule ID, a human-readable label, a severity tier and the
exact explanation of why it fired.

Design constraints
------------------
* Pure Python — no external ML dependencies.
* Rules cover: urgency pressure, secrecy/confidentiality demands,
  credential or OTP requests, suspicious payment patterns (gift cards,
  crypto, wire to new payee) and impersonation cues.
* Urgency alone is NOT treated as proof of fraud; each rule is labelled
  with its reasoning.
* This component does NOT issue a final risk verdict.

Rule catalogue (rule_id → description)
---------------------------------------
URGENCY_PRESSURE        — Extreme time pressure phrases.
SECRECY_DEMAND          — Requests to keep transaction secret.
CREDENTIAL_REQUEST      — Requests for OTP, PIN, password, CVV.
GIFT_CARD_PAYMENT       — Gift card as payment method.
CRYPTO_PAYMENT          — Crypto wallet / coin transfer requests.
NEW_PAYEE_PRESSURE      — Unsolicited request to pay a previously unknown account.
IMPERSONATION_CUE       — Phrases mimicking bank/gov/tech-support authority.
REFUND_OVERPAYMENT_SCAM — Classic advance-fee / overpayment setup.
REMOTE_ACCESS_REQUEST   — Request to install remote-access software.
THREAT_OR_COERCION      — Threatening language linked to payment.
"""

from __future__ import annotations

import re
import logging
from dataclasses import dataclass, field
from typing import List, Tuple

from satyalens_analysis.schema import (
    ComponentStatus,
    ContextFlag,
    ContextResult,
    context_failed,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Rule definitions
# ---------------------------------------------------------------------------

@dataclass
class _Rule:
    rule_id: str
    label: str
    severity: str   # 'low' | 'medium' | 'high'
    patterns: List[str]
    explanation_template: str   # Use {match} as placeholder for matched text


_RULES: List[_Rule] = [
    _Rule(
        rule_id="URGENCY_PRESSURE",
        label="Extreme Urgency / Time Pressure",
        severity="high",
        patterns=[
            r"\b(act\s+now|immediately|urgent|urgently|right\s+now|within\s+\d+\s+(minute|hour|second|min|hr)s?|last\s+chance|deadline\s+today|expires?\s+(today|tonight|in\s+\d+)|do\s+it\s+now|no\s+time\s+to\s+wait)\b",
        ],
        explanation_template=(
            'Phrase "{match}" creates artificial urgency. Legitimate '
            "institutions rarely demand immediate financial action."
        ),
    ),
    _Rule(
        rule_id="SECRECY_DEMAND",
        label="Secrecy / Confidentiality Demand",
        severity="high",
        patterns=[
            r"\b(don['\u2019]?t\s+tell|keep\s+(it|this)\s+(secret|between\s+us|confidential|quiet)|do\s+not\s+inform|don['\u2019]?t\s+inform|tell\s+no\s+one|nobody\s+should\s+know|don['\u2019]?t\s+share\s+this)\b",
        ],
        explanation_template=(
            'Instruction "{match}" asks the target to hide the '
            "transaction. Financial scams frequently rely on secrecy to "
            "prevent victims from seeking help."
        ),
    ),
    _Rule(
        rule_id="CREDENTIAL_REQUEST",
        label="Credential / OTP / PIN Request",
        severity="high",
        patterns=[
            r"\b(otp|one[\s-]time\s+pass(word|code)?|share\s+(your\s+)?(pin|password|cvv|cvc|security\s+code)|send\s+(me\s+)?(the\s+)?(otp|code|pin|password)|give\s+(me\s+)?(your\s+)?(otp|pin|password|cvv))\b",
        ],
        explanation_template=(
            '"{match}" requests sensitive authentication credentials. '
            "No legitimate bank, payment provider or government body "
            "will ever ask for OTPs, PINs or passwords."
        ),
    ),
    _Rule(
        rule_id="GIFT_CARD_PAYMENT",
        label="Gift Card as Payment",
        severity="high",
        patterns=[
            r"\b(gift\s+card|amazon\s+card|itunes\s+card|google\s+play\s+card|steam\s+card|vanilla\s+card|prepaid\s+card|scratch\s+card)\b",
        ],
        explanation_template=(
            '"{match}" requests payment via gift card. Gift cards are '
            "irreversible and untraceable — a hallmark of financial scams."
        ),
    ),
    _Rule(
        rule_id="CRYPTO_PAYMENT",
        label="Cryptocurrency Transfer Request",
        severity="medium",
        patterns=[
            r"\b(bitcoin|btc|ethereum|eth|usdt|tether|crypto|cryptocurrency|wallet\s+address|send\s+to\s+(my\s+)?wallet|blockchain\s+transfer)\b",
        ],
        explanation_template=(
            '"{match}" involves a cryptocurrency payment. While crypto '
            "is legitimate, it is commonly exploited in scams due to "
            "irreversibility."
        ),
    ),
    _Rule(
        rule_id="NEW_PAYEE_PRESSURE",
        label="Unsolicited New Payee / Account",
        severity="medium",
        patterns=[
            r"\b(new\s+(account|payee|bank\s+account|upi\s+id)|transfer\s+to\s+a\s+different|pay\s+to\s+a\s+new|use\s+this\s+(account|upi)|my\s+(new\s+)?(account|bank)\s+(is|number|details))\b",
        ],
        explanation_template=(
            '"{match}" requests payment to an unfamiliar account. '
            "Unexpected changes in payee details are a common social-"
            "engineering tactic."
        ),
    ),
    _Rule(
        rule_id="IMPERSONATION_CUE",
        label="Authority / Impersonation Cue",
        severity="medium",
        patterns=[
            r"\b(i\s+am\s+(from\s+)?(the\s+)?(bank|rbi|irs|tax\s+department|police|cbi|fbi|microsoft|apple\s+support|google\s+support|customer\s+care|helpdesk)|calling\s+from\s+(the\s+)?(bank|rbi|microsoft|government|tax|it\s+department)|verified\s+bank\s+official|government\s+agent)\b",
        ],
        explanation_template=(
            '"{match}" makes an unsolicited authority claim. Scammers '
            "impersonate banks, regulators and tech companies to build "
            "false trust."
        ),
    ),
    _Rule(
        rule_id="REFUND_OVERPAYMENT_SCAM",
        label="Refund / Overpayment Setup",
        severity="high",
        patterns=[
            r"\b(we\s+(accidentally\s+)?(over)?paid\s+you|you\s+received\s+extra|send\s+back\s+the\s+(excess|extra|difference|amount)|refund\s+(the\s+)?(excess|difference|extra)|accidental\s+(transfer|payment)|wrong\s+transfer)\b",
        ],
        explanation_template=(
            '"{match}" matches a classic advance-fee or overpayment '
            "scam pattern where the victim is tricked into returning "
            "funds that were never actually deposited."
        ),
    ),
    _Rule(
        rule_id="REMOTE_ACCESS_REQUEST",
        label="Remote Access Software Request",
        severity="high",
        patterns=[
            r"\b(anydesk|teamviewer|rustdesk|ultraviewer|remote\s+(desktop|access|control)\s+(software|app|tool)|install\s+.{0,30}(remote|desktop|viewer|connect))\b",
        ],
        explanation_template=(
            '"{match}" requests installation of remote-access software. '
            "This allows a scammer to directly control the victim's "
            "device and authorise transactions."
        ),
    ),
    _Rule(
        rule_id="THREAT_OR_COERCION",
        label="Threat or Coercion Linked to Payment",
        severity="high",
        patterns=[
            r"\b(will\s+(arrest|sue|report|block)\s+you|your\s+account\s+(will\s+be\s+)?(blocked|frozen|suspended|closed)|legal\s+action\s+(will\s+be\s+taken|against\s+you)|police\s+will\s+(come|arrest)|face\s+(consequences|arrest|penalty)|pay\s+or\s+(we\s+will|else))\b",
        ],
        explanation_template=(
            '"{match}" uses threats to pressure a payment. Coercive '
            "language is a strong indicator of fraud."
        ),
    ),
]


# ---------------------------------------------------------------------------
# Public function
# ---------------------------------------------------------------------------

_STATIC_LIMITATIONS = [
    "Rules are keyword/pattern-based and may produce false positives or miss novel phrasing.",
    "A high flag count raises concern but is not proof of fraud.",
    "Absence of flags does not confirm the request is legitimate.",
    "This component does not issue a final risk verdict.",
]


def analyse_context(text: str) -> ContextResult:
    """
    Apply scam-context rules to *text*.

    Parameters
    ----------
    text : str
        The message, scenario description or conversation excerpt to analyse.
        Must be non-empty.

    Returns
    -------
    ContextResult
        status='analysed' with zero or more flags on success.
        status='failed' with an error message if the function raises.
        status='not_applicable' if text is empty/None.
    """
    if not text or not text.strip():
        return ContextResult(
            status=ComponentStatus.NOT_APPLICABLE,
            limitations=["No message text was provided."],
        )

    try:
        normalised = text.lower()
        flags: List[ContextFlag] = []

        for rule in _RULES:
            for pattern in rule.patterns:
                match = re.search(pattern, normalised, re.IGNORECASE)
                if match:
                    matched_text = match.group(0).strip()
                    explanation = rule.explanation_template.format(match=matched_text)
                    flags.append(
                        ContextFlag(
                            rule_id=rule.rule_id,
                            label=rule.label,
                            severity=rule.severity,
                            explanation=explanation,
                        )
                    )
                    break   # One flag per rule per text; don't repeat same rule.

        # Deduplicate by rule_id (defensive, shouldn't be needed).
        seen: set = set()
        unique_flags: List[ContextFlag] = []
        for f in flags:
            if f.rule_id not in seen:
                seen.add(f.rule_id)
                unique_flags.append(f)

        findings = _build_findings(unique_flags)

        logger.info("context_analyser: %d flag(s) raised.", len(unique_flags))
        return ContextResult(
            status=ComponentStatus.ANALYSED,
            flags=unique_flags,
            findings=findings,
            limitations=list(_STATIC_LIMITATIONS),
        )

    except Exception as exc:  # noqa: BLE001
        logger.exception("context_analyser: unexpected error.")
        return context_failed(f"Context analysis error: {exc}")


def _build_findings(flags: List[ContextFlag]) -> List[str]:
    """Convert flags into narrative summary sentences."""
    if not flags:
        return ["No scam-context patterns detected in the provided text."]

    high   = [f for f in flags if f.severity == "high"]
    medium = [f for f in flags if f.severity == "medium"]
    low    = [f for f in flags if f.severity == "low"]

    parts: List[str] = []
    if high:
        labels = ", ".join(f.label for f in high)
        parts.append(
            f"{len(high)} high-severity pattern(s) detected: {labels}."
        )
    if medium:
        labels = ", ".join(f.label for f in medium)
        parts.append(
            f"{len(medium)} medium-severity pattern(s) detected: {labels}."
        )
    if low:
        labels = ", ".join(f.label for f in low)
        parts.append(
            f"{len(low)} low-severity pattern(s) detected: {labels}."
        )

    parts.append(
        "These patterns are context signals. A final risk determination "
        "must be made by the case review process, not by this component."
    )
    return parts
