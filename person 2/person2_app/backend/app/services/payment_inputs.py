"""
app/services/payment_inputs.py
──────────────────────────────
Normalisation of user-supplied expected payment details (amount, currency).

Kept free of imports from app.services.analysis: schemas.py imports this
module, and app.services.analysis loads the Person 1 adapter at import time,
which imports payment_checks.  Importing payment_checks from schemas would
therefore create an import cycle when ANALYSIS_MODULE_ENABLED=true.
"""

from __future__ import annotations

import re
from decimal import Decimal

SUPPORTED_CURRENCIES = frozenset({
    "EUR", "USD", "GBP", "INR", "CHF", "JPY", "CNY", "CAD", "AUD", "NZD",
    "SGD", "HKD", "SEK", "NOK", "DKK", "PLN", "AED", "ZAR", "BRL", "MXN",
})
# Symbols that name exactly one supported currency.
UNAMBIGUOUS_SYMBOLS = {"€": "EUR", "£": "GBP", "₹": "INR"}
# Symbols shared by several currencies → which of ours they could mean.
AMBIGUOUS_SYMBOLS = {
    "$": frozenset({"USD", "CAD", "AUD", "NZD", "SGD", "HKD", "MXN"}),
    "¥": frozenset({"JPY", "CNY"}),
    "RS": frozenset({"INR"}),   # also PKR, LKR, NPR (not supported here)
    "KR": frozenset({"SEK", "NOK", "DKK"}),
    "R$": frozenset({"BRL"}),
}

PLAIN_AMOUNT_RE = re.compile(r"^\d+(?:\.\d{1,2})?$")
GROUPED_AMOUNT_RE = re.compile(r"^\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?$")
MAX_EXPECTED_AMOUNT = Decimal("1000000000000")


def normalise_currency(value: str) -> str:
    """
    Return the ISO code for a user-supplied currency, or raise ValueError.

    Accepts a supported ISO 4217 code (any case) or an unambiguous symbol
    (€, £, ₹).  Shared symbols such as $ or ¥ are rejected because the
    currency they stand for cannot be known.
    """
    v = value.strip()
    if v in UNAMBIGUOUS_SYMBOLS:
        return UNAMBIGUOUS_SYMBOLS[v]
    key = v.upper().rstrip(".")
    if key in AMBIGUOUS_SYMBOLS:
        raise ValueError(
            f"'{v}' is used by more than one currency; enter the 3-letter code "
            f"instead (e.g. {', '.join(sorted(AMBIGUOUS_SYMBOLS[key]))})."
        )
    if key in SUPPORTED_CURRENCIES:
        return key
    raise ValueError(
        f"'{v}' is not a supported currency. Use a 3-letter code such as "
        "EUR, USD, GBP or INR, or the symbol €, £ or ₹."
    )


def normalise_amount(value: str) -> Decimal:
    """
    Parse a user-supplied amount with Decimal arithmetic, or raise ValueError.

    Accepts "100", "100.5", "100.50" and "1,000.00".  Rejects formats whose
    meaning is ambiguous ("1.000,00", "12,50"), signs, exponents and currency
    symbols (the currency has its own field).
    """
    v = value.strip().replace(" ", "")
    if not (PLAIN_AMOUNT_RE.match(v) or GROUPED_AMOUNT_RE.match(v)):
        raise ValueError(
            f"'{value.strip()}' is not a valid amount. Use digits with an optional "
            "'.' and up to two decimals, e.g. 100 or 1,000.50. Enter the currency "
            "separately."
        )
    amount = Decimal(v.replace(",", ""))
    if amount <= 0 or amount >= MAX_EXPECTED_AMOUNT:
        raise ValueError("Amount must be greater than 0 and less than 1,000,000,000,000.")
    return amount
