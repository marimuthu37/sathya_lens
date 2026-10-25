"""
app/services/checklist.py
─────────────────────────
Segment 7: Canonical verification checklist item definitions.

These items are defined server-side so:
  - IDs are stable and validatable.
  - Text and category can be updated centrally.
  - The frontend never invents item IDs.

What this service NEVER claims:
  - Completing all items proves a transaction or requester is legitimate.
  - A checked item is independently verified — only the user knows that.
  - Completing items removes risk.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


CHECKLIST_VERSION = "1.0.0"


@dataclass(frozen=True)
class ChecklistItem:
    """
    A single independent-verification step.

    Fields
    ──────
    item_id      — stable slug, used as the DB key.
    category     — grouping: "contact", "payment", "warning_signs", "credentials", "reporting".
    text         — the actionable instruction shown to the user.
    detail       — optional longer explanation.
    india_only   — True for India-specific items (helpline 1930, cybercrime.gov.in).
    """
    item_id:    str
    category:   str
    text:       str
    detail:     Optional[str] = None
    india_only: bool = False


# ── Canonical checklist ───────────────────────────────────────────────────────
# Order matters — displayed in this sequence in the UI.

CHECKLIST_ITEMS: list[ChecklistItem] = [
    ChecklistItem(
        item_id="contact_known_number",
        category="contact",
        text="Contact the requester using a number you already know or found independently "
             "— not the number given in the suspicious message.",
        detail="Look up the number from the organisation's official website, a physical "
               "statement, or a previously saved contact. Do not trust numbers provided "
               "within the suspicious communication itself.",
    ),
    ChecklistItem(
        item_id="verify_official_channel",
        category="contact",
        text="Verify the request through the organisation's official website or in-person "
             "branch — not via a link or number from the suspicious message.",
        detail="Banks and government bodies do not ask for payments or OTPs over "
               "unexpected calls or messages.",
    ),
    ChecklistItem(
        item_id="confirm_payee_amount",
        category="payment",
        text="Confirm the recipient/payee name and payment amount independently before "
             "making any transfer.",
        detail="Cross-check account details with a previous verified payment or "
               "statement. A match with what was provided does not prove the request "
               "is legitimate.",
    ),
    ChecklistItem(
        item_id="check_payment_details",
        category="payment",
        text="Check that account number, IFSC, and UPI handle shown in any screenshot "
             "match what you independently obtained from a trusted source.",
        detail="Altered screenshots can change digits while appearing authentic. "
               "Always verify against a source you control.",
    ),
    ChecklistItem(
        item_id="urgency_pressure_warning",
        category="warning_signs",
        text="Treat urgency, secrecy, or pressure to bypass normal procedures as a "
             "warning sign — pause and verify before acting.",
        detail="Phrases such as 'act immediately', 'do not tell anyone', or "
               "'your account will be frozen' are common social-engineering tactics.",
    ),
    ChecklistItem(
        item_id="unexpected_request_warning",
        category="warning_signs",
        text="Be cautious of unexpected requests for payment, account access, or "
             "personal information — even if the caller appears to know your details.",
        detail="Fraudsters may know partial personal information from data breaches "
               "or public records. Familiarity does not prove legitimacy.",
    ),
    ChecklistItem(
        item_id="no_credentials",
        category="credentials",
        text="Do not share OTPs, UPI PINs, net-banking passwords, debit/credit card "
             "numbers, or CVV with anyone — including callers claiming to be from "
             "your bank or a government body.",
        detail="Legitimate banks and government departments never ask for full "
               "passwords or OTPs over the phone or via messages.",
    ),
    ChecklistItem(
        item_id="trusted_person",
        category="contact",
        text="Discuss the request with a trusted person (family member, colleague, "
             "or advisor) before taking action.",
        detail="A second opinion can help identify red flags that are easy to miss "
               "under pressure.",
    ),
    ChecklistItem(
        item_id="report_cyber_fraud_india",
        category="reporting",
        text=(
            "If money has already been lost to suspected cyber fraud in India: "
            "call the Cyber Crime Helpline 1930 immediately and report online at "
            "https://cybercrime.gov.in — the sooner you report, the higher the "
            "chance of recovery."
        ),
        detail="Verify these contact details from official government sources before "
               "the demo, as they may change. This item is India-specific.",
        india_only=True,
    ),
]

# Stable lookup map: item_id → ChecklistItem
CHECKLIST_MAP: dict[str, ChecklistItem] = {item.item_id: item for item in CHECKLIST_ITEMS}

VALID_ITEM_IDS: frozenset[str] = frozenset(CHECKLIST_MAP.keys())


def get_item(item_id: str) -> Optional[ChecklistItem]:
    """Return the ChecklistItem for item_id, or None if unknown."""
    return CHECKLIST_MAP.get(item_id)
