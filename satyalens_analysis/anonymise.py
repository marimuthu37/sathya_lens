"""
anonymise.py — Shared anonymiser for training, test and donated messages
=========================================================================
One function, applied identically to EVERY source and BOTH classes (and, for
models trained on anonymised data, at inference), so that masking artefacts
cannot become a label shortcut.

Version: anon-v1

Rules (in order)
----------------
1. IMC25 / Presidio placeholders are normalised to the shared scheme:
     <URL> -> urltoken        <PHONE_NUMBER> -> phonetoken
     <EMAIL_ADDRESS> -> emailtoken    <DATE_TIME> -> datetoken
     <US_BANK_NUMBER>, <US_DRIVER_LICENSE>, <CREDIT_CARD>, <IBAN_CODE>,
     <US_SSN>, <US_PASSPORT>, <US_ITIN>, <CRYPTO> -> numtoken
     any other <UPPER_CASE> placeholder (NAMED_ENTITY, LOCATION, NRP,
     PERSON, ...) -> removed, because the same masking cannot be reproduced
     on legitimate text.
2. E-mail addresses                                          -> emailtoken
3. UPI handles (name@provider, no dot in provider)           -> upitoken
4. URLs (scheme, www., or bare domain with a known TLD)      -> urltoken
5. Phone numbers (10-digit mobile +cc, 1800 toll-free, STD)  -> phonetoken
6. Dates and times (12/10/2026, 12-Oct-26, 3 Nov 2026, 14:05) -> datetoken
7. Masked account fragments (XX1234, XXXX-1234)              -> numtoken
8. Remaining runs of >= 4 digits (OTPs, IDs, years, amounts)  -> numtoken
Whitespace is collapsed. Text is otherwise unchanged (case preserved).

Known residual asymmetry: IMC25 also removed person names, locations and
some relative dates ("today"); this anonymiser cannot remove names from
other sources reliably. Donated messages are reviewed by the donor, who
redacts names manually (see scripts/donate_messages.py).

anon-v2 (PROPOSED for protocol v4 — not used by any pipeline yet)
-----------------------------------------------------------------
anonymise_v2() = anonymise() (anon-v1, unchanged) followed by:
 9. Social-media handles (@ + letter/underscore + up to 29 word chars/dots)
    -> handletoken.  "@ 9.9%" and "Rs@499" are left alone.
10. Remaining e-mail-like fragments with a letter on both sides of '@'
    ("name@bank") and dangling local parts ("rahul@ ") -> emailtoken.
anon-v1 output is frozen by a regression test; protocol v3 stays on anon-v1.
"""

from __future__ import annotations

import re
from typing import List

ANONYMISER_VERSION = "anon-v1"

_PLACEHOLDER_MAP = {
    "URL": "urltoken",
    "PHONE_NUMBER": "phonetoken",
    "EMAIL_ADDRESS": "emailtoken",
    "DATE_TIME": "datetoken",
    "US_BANK_NUMBER": "numtoken",
    "US_DRIVER_LICENSE": "numtoken",
    "CREDIT_CARD": "numtoken",
    "IBAN_CODE": "numtoken",
    "US_SSN": "numtoken",
    "US_PASSPORT": "numtoken",
    "US_ITIN": "numtoken",
    "CRYPTO": "numtoken",
}
_PLACEHOLDER = re.compile(r"<([A-Z][A-Z_]+)>")
_TLDS = r"(?:com|in|net|org|co|io|me|ly|gd|do|info|xyz|link|app|online|site|top|club|live|shop|gov|edu|uk|us|example)"
_URL = re.compile(
    rf"(?:https?://\S+|www\.\S+|\b[a-z0-9][a-z0-9\-]*(?:\.[a-z0-9\-]+)*\.{_TLDS}\b(?:/\S*)?)",
    re.IGNORECASE,
)
_EMAIL = re.compile(r"\b[\w.+\-]+@[\w\-]+\.[\w.\-]+\b")
_UPI = re.compile(r"\b[\w.\-]{2,}@[a-z][a-z0-9]{1,20}\b", re.IGNORECASE)
# 10-digit mobiles (optionally +country code, optionally split 5+5), 1800
# toll-free numbers, and STD landlines (0 + area code + 6-8 digits). Other
# long digit runs (UPI refs, transaction ids) fall through to numtoken.
_PHONE = re.compile(
    r"(?:\+\d{1,3}[\s\-]?)?\b\d{5}[\s\-]?\d{5}\b|\b1800[\s\-]?\d{3}[\s\-]?\d{4}\b|\b0\d{2,4}[\s\-]?\d{6,8}\b"
)
_MONTHS = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*"
_DATE = re.compile(
    rf"\b(?:\d{{1,2}}[/\-.]\d{{1,2}}[/\-.]\d{{2,4}}|\d{{1,2}}[\s\-]{_MONTHS}[\s\-,]*\d{{2,4}}|"
    rf"{_MONTHS}\s+\d{{1,2}},?\s+\d{{2,4}}|\d{{1,2}}:\d{{2}}(?::\d{{2}})?(?:\s*[ap]\.?m\.?)?)\b",
    re.IGNORECASE,
)
_MASKED_ACCT = re.compile(r"\b[xX*]{2,}[\-\s]?\d{2,}\b")
_LONG_DIGITS = re.compile(r"\d{4,}")


def anonymise(text: str) -> str:
    """Apply anon-v1 to *text* (see module docstring)."""
    text = _PLACEHOLDER.sub(lambda m: f" {_PLACEHOLDER_MAP.get(m.group(1), '')} ", text)
    text = _EMAIL.sub(" emailtoken ", text)
    text = _UPI.sub(" upitoken ", text)
    text = _URL.sub(" urltoken ", text)
    text = _DATE.sub(" datetoken ", text)
    text = _PHONE.sub(" phonetoken ", text)
    text = _MASKED_ACCT.sub(" numtoken ", text)
    text = _LONG_DIGITS.sub(" numtoken ", text)
    return re.sub(r"\s+", " ", text).strip()


ANONYMISER_V2_VERSION = "anon-v2"
_HANDLE = re.compile(r"(?<![\w@.])@[A-Za-z_][A-Za-z0-9_.]{0,29}(?<!\.)")
_EMAIL_FRAGMENT = re.compile(r"[\w.+\-]*[A-Za-z][\w.+\-]*@[A-Za-z][\w.\-]*")
_DANGLING_LOCAL = re.compile(r"[\w.+\-]*[A-Za-z][\w.+\-]*@(?![\w@])")


def anonymise_v2(text: str) -> str:
    """anon-v2 (proposed): anon-v1, then social handles and residual e-mail fragments."""
    text = anonymise(text)
    text = _HANDLE.sub(" handletoken ", text)
    text = _EMAIL_FRAGMENT.sub(" emailtoken ", text)
    text = _DANGLING_LOCAL.sub(" emailtoken ", text)
    return re.sub(r"\s+", " ", text).strip()


ANONYMISERS = {ANONYMISER_VERSION: anonymise, ANONYMISER_V2_VERSION: anonymise_v2}


# Words that are commonly capitalised in financial SMS and are not names.
_CAPITALISED_OK = {
    "dear", "customer", "bank", "upi", "otp", "pin", "your", "you", "the", "a", "an", "is", "rs", "inr",
    "a/c", "ac", "acct", "account", "card", "credit", "debit", "debited", "credited", "alert", "thank",
    "thanks", "info", "team", "ltd", "pvt", "limited", "india", "if", "not", "do", "never", "please",
    "call", "sms", "dial", "use", "valid", "ref", "txn", "avl", "bal", "balance", "available", "sent",
    "received", "payment", "paid", "order", "delivered", "delivery", "shipped", "bill", "due", "amount",
    "emi", "loan", "neft", "imps", "rtgs", "upi", "atm", "kyc", "pan", "app", "mobile", "net", "banking",
    "hi", "hello", "regards", "ok", "for", "to", "on", "of", "at", "in", "by", "from", "and", "or", "this",
    "it", "we", "our", "us", "will", "be", "has", "have", "been", "was", "now", "today", "tomorrow",
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
}
_TOKENS = {"urltoken", "emailtoken", "upitoken", "phonetoken", "datetoken", "numtoken", "handletoken"}


def pii_warnings(masked_text: str) -> List[str]:
    """
    Heuristic warnings on ALREADY-MASKED text for the donor review step:
    leftover digit runs, '@', and capitalised words that may be names or
    places. Heuristic only — the donor's review is the safeguard.
    """
    warnings: List[str] = []
    without_amounts = re.sub(r"(?:rs\.?|inr|₹)\s*[\d,]+(?:\.\d+)?", " ", masked_text, flags=re.IGNORECASE)
    if re.search(r"\d{3,}", without_amounts):
        warnings.append("contains a run of 3+ digits")
    if "@" in masked_text:
        warnings.append("contains '@'")
    words = re.findall(r"[A-Za-z][A-Za-z'\-]*", masked_text)
    for i, w in enumerate(words):
        lw = w.lower()
        if lw in _TOKENS or lw in _CAPITALISED_OK or w.isupper():
            continue
        if w[0].isupper() and i > 0 and len(w) > 2:
            warnings.append(f"possible name/place: {w}")
    if re.search(r"\b(?:road|rd|street|st|nagar|colony|sector|flat|house|apartment|apt|lane)\b",
                 masked_text, re.IGNORECASE):
        warnings.append("possible address wording")
    return warnings
