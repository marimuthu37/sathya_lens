"""
app/services/payment_checks.py
──────────────────────────────
Evidence-based observations on one payment screenshot, computed from the
`payment` object of Person 1's result (INTEGRATION_CONTRACT.md, schema 1.0).

The package extracts fields; this module decides which extracted values
warrant human review.  Only discrepancies VISIBLE in the screenshot become
findings; absences (a field OCR could not read) and benign results become
notes, because OCR frequently misses fields and absence is weak evidence.

  Finding (ocr_mismatch, interpretation → VERIFY_FIRST at most)
    - recipient name is a template placeholder ("Merchant Name", "Payee", …)
    - transaction reference and description are near-duplicates that disagree
    - transaction reference is malformed (leading/trailing/repeated separators)
    - amount shows conflicting currency markers
  Finding (payment_mismatch, direct comparison)
    - extracted value differs from a caller-supplied expected value
    - extracted value differs from a user-supplied expected amount, currency,
      recipient or reference (see "Expected payment details" below)
  Note only
    - extracted values with their OCR confidence
    - missing amount / recipient, low OCR confidence, no text at all
    - approval-prompt wording with no completion status
    - expected values not supplied / all compared fields matched
    - formatting-only differences from an expected value

Expected payment details
────────────────────────
The user may supply an expected amount, currency, recipient and reference.
Each is compared only when the user supplied it AND the screenshot value was
read with usable reliability: never when the value is missing, has low OCR
confidence, or the image-quality assessment has a 'severe' warning.  Skipped
comparisons are reported as `not_compared` with the reason, never as matches
or mismatches.  Comparison outcomes:

  match              same value (Decimal equality for amounts)
  format_difference  same value written differently (case, spacing,
                     punctuation, separators or an ID prefix) → note only
  partial_match      recipient names share words but one has extra words
                     → interpretation finding
  mismatch           different value → payment_mismatch finding

All findings come from the same OCR text, so they are correlated and never
count as independent confirmations; none of them claims fraud or settlement.
"""

from __future__ import annotations

import re
import unicodedata
from decimal import Decimal, InvalidOperation
from typing import Optional

from app.services.analysis import AnalysisFinding
from app.services.payment_inputs import (  # noqa: F401  (normalisers re-exported)
    AMBIGUOUS_SYMBOLS as _AMBIGUOUS_SYMBOLS,
    GROUPED_AMOUNT_RE as _GROUPED_AMOUNT_RE,
    PLAIN_AMOUNT_RE as _PLAIN_AMOUNT_RE,
    SUPPORTED_CURRENCIES,
    UNAMBIGUOUS_SYMBOLS as _UNAMBIGUOUS_SYMBOLS,
    normalise_amount,
    normalise_currency,
)

# Recipient values that are template placeholders rather than identities.
_PLACEHOLDER_NAMES = frozenset({
    "merchant", "merchant name", "recipient", "recipient name", "payee", "payee name",
    "beneficiary", "beneficiary name", "name", "full name", "your name", "customer name",
    "business name", "company name", "account name", "account holder", "store name",
    "shop name", "test", "test merchant", "sample", "example", "demo", "unknown",
    "n a", "na", "none", "null", "undefined", "lorem ipsum",
})
_PLACEHOLDER_RE = re.compile(r"^(?:x{3,}|\*{3,}|_{3,}|\.{3,})$", re.IGNORECASE)

# A single reference-like token: letters/digits with - _ / separators, at least one digit.
_ID_TOKEN_RE = re.compile(r"^(?=[A-Za-z0-9\-_/]*\d)[A-Za-z0-9\-_/]{3,}$")
_SEPARATORS = "-_/"

_CURRENCY_MARKERS = (
    (re.compile(r"₹|\brs\.?|\binr\b", re.IGNORECASE), "INR"),
    (re.compile(r"\$|\busd\b", re.IGNORECASE), "USD"),
    (re.compile(r"€|\beur\b", re.IGNORECASE), "EUR"),
    (re.compile(r"£|\bgbp\b", re.IGNORECASE), "GBP"),
)

_APPROVAL_PROMPT_RE = re.compile(
    r"\b(please confirm|confirm (?:the )?(?:following )?payment|accept|decline|approve|authori[sz]e)\b",
    re.IGNORECASE,
)

_CORRELATION_NOTE = (
    "Derived from the same OCR text as any other payment observation on this "
    "screenshot — not an independent confirmation. A discrepancy warrants "
    "independent verification; it is not proof of fraud."
)

_SOURCE = "payment_screenshot_ocr"

# Every payment finding on one screenshot shares this group: they all derive
# from one OCR reading and must not be counted as independent confirmations.
CORRELATION_GROUP = "payment_screenshot_ocr"

_SETTLEMENT_NOTE = (
    "A screenshot cannot show whether a payment was made, received or settled. "
    "Check the payment in your own bank or payment app."
)

# Currency markers that may appear in an OCR amount string.
_EXTRACTED_MARKER_RE = re.compile(
    r"R\$|[€£₹$¥]|\bRs\b\.?|\b(" + "|".join(sorted(SUPPORTED_CURRENCIES)) + r")\b",
    re.IGNORECASE,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _fields_by_name(payment: dict) -> dict[str, dict]:
    fields: dict[str, dict] = {}
    for f in payment.get("fields") or []:
        if isinstance(f, dict) and isinstance(f.get("field_name"), str):
            fields.setdefault(f["field_name"], f)
    return fields


def _value(fields: dict[str, dict], name: str) -> Optional[str]:
    v = (fields.get(name) or {}).get("extracted_value")
    return v.strip() if isinstance(v, str) and v.strip() else None


def _confidence(fields: dict[str, dict], name: str) -> str:
    return str((fields.get(name) or {}).get("confidence") or "unknown")


def _alnum(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", value).lower()


def _reliability(conf_text: str) -> str:
    return f"OCR confidence: {conf_text}. {_CORRELATION_NOTE}"


def _malformation(ref: str) -> Optional[str]:
    """Describe how a reference looks malformed, or None."""
    problems = []
    if ref[0] in _SEPARATORS:
        problems.append("starts with a separator")
    if ref[-1] in _SEPARATORS:
        problems.append("ends with a separator")
    if re.search(r"[-_/]{2,}", ref):
        problems.append("contains repeated separators")
    if not problems:
        return None
    return problems[0] if len(problems) == 1 else ", ".join(problems[:-1]) + " and " + problems[-1]


def _currency_codes(value: str) -> set[str]:
    return {code for rx, code in _CURRENCY_MARKERS if rx.search(value)}


# ── Individual checks ─────────────────────────────────────────────────────────

def _placeholder_recipient(fields: dict[str, dict]) -> list[AnalysisFinding]:
    payee = _value(fields, "payee_name")
    if not payee:
        return []
    norm = re.sub(r"[^a-z0-9]+", " ", payee.lower()).strip()
    if norm not in _PLACEHOLDER_NAMES and not _PLACEHOLDER_RE.match(payee):
        return []
    return [AnalysisFinding(
        finding_type="ocr_mismatch",
        label="Recipient name looks like a template placeholder",
        detail=(
            f"The screenshot names the recipient as {payee!r}, a generic placeholder "
            "rather than an identifiable person or business, so the payee cannot be "
            "identified from this screenshot. Placeholder text also appears on "
            "legitimate test or demo screens."
        ),
        evidence_source=_SOURCE,
        is_interpretation=True,
        reliability_note=_reliability(_confidence(fields, "payee_name")),
    )]


def _reference_findings(fields: dict[str, dict]) -> list[AnalysisFinding]:
    """Reference/description disagreement, else reference malformation (never both)."""
    ref = _value(fields, "transaction_id")
    if not ref:
        return []
    ref_conf = _confidence(fields, "transaction_id")
    malformed = _malformation(ref)
    desc = _value(fields, "description")

    if desc and _ID_TOKEN_RE.match(desc) and desc.lower() != ref.lower():
        a, b = _alnum(ref), _alnum(desc)
        if a and b and (a == b or a.startswith(b) or b.startswith(a)):
            relation = (
                "identical once punctuation is removed" if a == b
                else "one is a truncated form of the other"
            )
            detail = (
                f"Transaction reference {ref!r} and description {desc!r} refer to the "
                f"same identifier ({relation}) but are not written the same way."
            )
            if malformed:
                detail += f" The reference also {malformed}."
            detail += (
                " This can come from a formatting or truncation error, an edited "
                "screenshot, or OCR misreading characters."
            )
            conf = f"reference {ref_conf}, description {_confidence(fields, 'description')}"
            return [AnalysisFinding(
                finding_type="ocr_mismatch",
                label="Transaction reference and description disagree",
                detail=detail,
                evidence_source=_SOURCE,
                is_interpretation=True,
                reliability_note=_reliability(conf),
            )]

    if malformed:
        return [AnalysisFinding(
            finding_type="ocr_mismatch",
            label="Transaction reference appears malformed",
            detail=(
                f"Transaction reference {ref!r} {malformed}. Genuine references rarely "
                "do; this can also be an OCR artifact from an adjacent icon or line."
            ),
            evidence_source=_SOURCE,
            is_interpretation=True,
            reliability_note=_reliability(ref_conf),
        )]
    return []


def _amount_findings(fields: dict[str, dict]) -> list[AnalysisFinding]:
    amount = _value(fields, "amount")
    if not amount or len(_currency_codes(amount)) < 2:
        return []
    return [AnalysisFinding(
        finding_type="ocr_mismatch",
        label="Amount shows conflicting currency markers",
        detail=(
            f"The amount {amount!r} carries more than one currency marker "
            f"({', '.join(sorted(_currency_codes(amount)))}), so the currency of the "
            "payment cannot be determined from the screenshot."
        ),
        evidence_source=_SOURCE,
        is_interpretation=True,
        reliability_note=_reliability(_confidence(fields, "amount")),
    )]


def _mismatch_findings(payment: dict) -> list[AnalysisFinding]:
    return [
        AnalysisFinding(
            finding_type="payment_mismatch",
            label="Payment screenshot field does not match the expected value",
            detail=mismatch,
            evidence_source=_SOURCE,
            is_interpretation=False,
            reliability_note=(
                "OCR may misread stylised or low-resolution text. A screenshot "
                "cannot prove that a payment was or was not settled."
            ),
        )
        for mismatch in payment.get("mismatches") or []
        if isinstance(mismatch, str) and mismatch
    ]


def _notes(payment: dict, fields: dict[str, dict], user_expectations: bool = False) -> list[str]:
    notes: list[str] = []
    raw_text = payment.get("raw_text") or ""
    if not raw_text.strip():
        notes.append(
            "[payment] OCR produced no text. The image may be unreadable or not a "
            "payment screenshot; no observations could be made. This does NOT mean "
            "the payment is genuine."
        )
        return notes

    extracted = [(n, f) for n, f in fields.items() if _value(fields, n)]
    if not extracted:
        notes.append("[payment] OCR read text but recognised no payment fields.")
    for name, f in sorted(extracted):
        notes.append(
            f"[payment] Extracted {name}: {_value(fields, name)!r} "
            f"(OCR confidence: {f.get('confidence', 'unknown')})"
        )

    low = [f"{n} ({_value(fields, n)!r})" for n, f in sorted(extracted) if f.get("confidence") == "low"]
    if low:
        notes.append(
            f"[payment] Low OCR confidence for: {', '.join(low)}. These values may be "
            "misread; compare them with the original screenshot."
        )
    if extracted and not _value(fields, "amount"):
        notes.append("[payment] No payment amount could be read from the screenshot.")
    if extracted and not _value(fields, "payee_name"):
        notes.append("[payment] No recipient name could be read from the screenshot.")

    if not _value(fields, "status_text"):
        prompt = sorted({m.group(1).lower() for m in _APPROVAL_PROMPT_RE.finditer(raw_text)})
        if prompt:
            notes.append(
                "[payment] No completion status (e.g. 'successful') was found, and the "
                f"screenshot contains approval-prompt wording ({', '.join(prompt)}). It may "
                "show a payment request awaiting approval rather than a completed payment."
            )
        else:
            notes.append("[payment] No payment status text (e.g. 'successful') was found.")

    if user_expectations:
        return notes  # comparison notes are added by evaluate_payment
    compared = [f for f in fields.values() if f.get("match") is not None]
    if not compared:
        notes.append(
            f"[payment] {len(fields)} field(s) extracted by OCR; no expected values "
            "were supplied, so no comparison was made."
        )
    elif not payment.get("mismatches"):
        notes.append(
            "[payment] All compared fields matched. A matching screenshot does NOT "
            "prove payment settlement; screenshots can be edited."
        )
    return notes


# ── Expected-value comparisons ────────────────────────────────────────────────

_EXPECTED_KEYS = ("amount", "currency", "recipient", "reference")
_SOURCE_FIELD = {"amount": "amount", "currency": "amount",
                 "recipient": "payee_name", "reference": "transaction_id"}
_RAN = frozenset({"match", "format_difference", "partial_match", "mismatch"})
_ID_PREFIX_RE = re.compile(r"^(?:utr|txn|ref|tid|id|neft|imps|rtgs|order|no)+", re.IGNORECASE)


def _comparison(key: str, expected: Optional[str], extracted: Optional[str],
                confidence: Optional[str], status: str, detail: str) -> dict:
    return {
        "field": key, "expected": expected, "extracted": extracted,
        "extracted_confidence": confidence, "status": status,
        "ran": status in _RAN, "detail": detail,
    }


def _parse_extracted_amount(raw: str) -> Optional[Decimal]:
    """Decimal value of an OCR amount string, or None if it is not unambiguous."""
    body = _EXTRACTED_MARKER_RE.sub("", raw).replace(" ", "").lstrip(":-")  # "INR: 500"
    if not (_PLAIN_AMOUNT_RE.match(body) or _GROUPED_AMOUNT_RE.match(body)):
        return None
    try:
        return Decimal(body.replace(",", ""))
    except InvalidOperation:
        return None


def _extracted_currencies(raw: str) -> list[frozenset]:
    """Candidate currency sets, one per distinct currency marker in `raw`."""
    found: dict[str, frozenset] = {}
    for m in _EXTRACTED_MARKER_RE.finditer(raw):
        token = m.group(0).upper().rstrip(".")
        if m.group(0) in _UNAMBIGUOUS_SYMBOLS:
            found[token] = frozenset({_UNAMBIGUOUS_SYMBOLS[m.group(0)]})
        elif token in _AMBIGUOUS_SYMBOLS:
            found[token] = _AMBIGUOUS_SYMBOLS[token]
        elif token in SUPPORTED_CURRENCIES:
            found[token] = frozenset({token})
    # "€25 EUR" states one currency twice — collapse identical candidate sets.
    return list(set(found.values()))


def _name_tokens(value: str) -> list[str]:
    folded = unicodedata.normalize("NFKD", value)
    folded = "".join(c for c in folded if not unicodedata.combining(c)).casefold()
    return re.sub(r"[^a-z0-9]+", " ", folded).split()


def _compare_amount(expected: str, extracted: str) -> tuple[str, str]:
    value = _parse_extracted_amount(extracted)
    if value is None:
        return "not_compared", (
            f"The screenshot amount {extracted!r} could not be read as a single "
            "unambiguous number."
        )
    if value == Decimal(expected):
        return "match", f"The screenshot amount {extracted!r} equals the expected {expected}."
    detail = f"You expected {expected}; the screenshot shows {extracted!r} (read as {value})."
    # Tesseract's English model cannot read '₹' and often turns it into a
    # leading digit ('₹1,463.12' → '21,463.12'). Kept as a mismatch — the
    # difference may be real — but the likely OCR cause is stated.
    exp_digits, got_digits = _alnum(format(Decimal(expected), "f")), _alnum(format(value, "f"))
    if (not _extracted_currencies(extracted) and len(got_digits) == len(exp_digits) + 1
            and got_digits.endswith(exp_digits)):
        detail += (
            f" The extra leading '{got_digits[0]}' may be a currency symbol such as ₹ that "
            "OCR misread as a digit; compare with the screenshot."
        )
    return "mismatch", detail


def _compare_currency(expected: str, extracted: str) -> tuple[str, str]:
    candidates = _extracted_currencies(extracted)
    if not candidates:
        return "not_compared", (
            f"The screenshot amount {extracted!r} shows no currency symbol or code."
        )
    if len(candidates) > 1:
        return "not_compared", (
            f"The screenshot amount {extracted!r} shows conflicting currency markers."
        )
    (codes,) = candidates
    if len(codes) == 1:
        (code,) = codes
        if code == expected:
            return "match", f"The screenshot amount {extracted!r} is in {code}, as expected."
        return "mismatch", f"You expected {expected}; the screenshot amount {extracted!r} is in {code}."
    if expected in codes:
        return "not_compared", (
            f"The symbol in {extracted!r} is used by several currencies "
            f"({', '.join(sorted(codes))}), so it cannot confirm {expected}."
        )
    return "mismatch", (
        f"You expected {expected}; the symbol in {extracted!r} is used for "
        f"{', '.join(sorted(codes))}, not {expected}."
    )


def _compare_recipient(expected: str, extracted: str) -> tuple[str, str]:
    if expected.casefold() == extracted.casefold():
        return "match", f"The screenshot recipient {extracted!r} matches."
    exp_t, ext_t = _name_tokens(expected), _name_tokens(extracted)
    if exp_t and "".join(exp_t) == "".join(ext_t):
        return "format_difference", (
            f"{extracted!r} and the expected {expected!r} differ only in case, "
            "spacing, accents or punctuation."
        )
    common = set(exp_t) & set(ext_t)
    if common and (set(exp_t) <= set(ext_t) or set(ext_t) <= set(exp_t)):
        return "partial_match", (
            f"The screenshot recipient {extracted!r} and the expected {expected!r} "
            "share words but one has words the other lacks. This may be a longer "
            "legal name, or a different payee with a similar name."
        )
    return "mismatch", f"You expected {expected!r}; the screenshot names {extracted!r}."


def _compare_reference(expected: str, extracted: str) -> tuple[str, str]:
    if expected.casefold() == extracted.casefold():
        return "match", f"The screenshot reference {extracted!r} matches."
    a, b = _alnum(expected), _alnum(extracted)
    if a and a == b:
        return "format_difference", (
            f"{extracted!r} and the expected {expected!r} differ only in separators "
            "or case."
        )
    pa, pb = _ID_PREFIX_RE.sub("", a), _ID_PREFIX_RE.sub("", b)
    if pa and pa == pb:
        return "format_difference", (
            f"{extracted!r} and the expected {expected!r} differ only by a reference "
            "prefix (e.g. UTR, TXN, TID)."
        )
    if a and _deconfuse(a) == _deconfuse(b):
        return "format_difference", (
            f"{extracted!r} and the expected {expected!r} differ only in characters OCR "
            "often confuses (O/0, I/1, S/5, B/8); check the screenshot by eye."
        )
    return "mismatch", f"You expected {expected!r}; the screenshot shows {extracted!r}."


_CONFUSABLE = str.maketrans({"o": "0", "i": "1", "l": "1", "s": "5", "b": "8"})


def _deconfuse(alnum_lower: str) -> str:
    """Map characters Tesseract commonly swaps onto one representative."""
    return alnum_lower.translate(_CONFUSABLE)


_COMPARATORS = {
    "amount": _compare_amount, "currency": _compare_currency,
    "recipient": _compare_recipient, "reference": _compare_reference,
}


def _severe_quality_warnings(payment: dict) -> list[str]:
    quality = payment.get("quality")
    if not isinstance(quality, dict):
        return []
    return [
        w.get("code", "unknown") for w in quality.get("warnings") or []
        if isinstance(w, dict) and w.get("severity") == "severe"
    ]


def compare_expected(payment: dict, expected: Optional[dict]) -> list[dict]:
    """One comparison record per expected key (amount, currency, recipient, reference)."""
    fields = _fields_by_name(payment)
    expected = expected or {}
    severe = _severe_quality_warnings(payment)
    out = []
    for key in _EXPECTED_KEYS:
        exp = expected.get(key)
        src = _SOURCE_FIELD[key]
        ext = _value(fields, src)
        conf = (fields.get(src) or {}).get("confidence") if ext else None
        if exp is None:
            status, detail = "not_supplied", "No expected value was supplied, so no comparison was made."
        elif severe:
            status, detail = "not_compared", (
                f"Not compared: image quality is too poor for a reliable reading "
                f"({', '.join(severe)}). Check the screenshot yourself."
            )
        elif ext is None:
            status, detail = "not_compared", (
                "Not compared: this value could not be read from the screenshot. "
                "A missing value is not treated as a mismatch."
            )
        elif conf == "low":
            status, detail = "not_compared", (
                f"Not compared: the screenshot value {ext!r} has low OCR confidence "
                "and may be misread. Compare it with the screenshot yourself."
            )
        else:
            status, detail = _COMPARATORS[key](exp, ext)
        out.append(_comparison(key, exp, ext, conf, status, detail))
    return out


_COMPARISON_LABELS = {
    "amount": "Amount differs from the amount you expected",
    "currency": "Currency differs from the currency you expected",
    "recipient": "Recipient differs from the recipient you expected",
    "reference": "Transaction reference differs from the reference you expected",
}


def _comparison_findings(comparisons: list[dict]) -> list[AnalysisFinding]:
    findings = []
    for c in comparisons:
        if c["status"] not in ("mismatch", "partial_match"):
            continue
        partial = c["status"] == "partial_match"
        findings.append(AnalysisFinding(
            finding_type="payment_mismatch",
            label=("Recipient only partly matches the recipient you expected" if partial
                   else _COMPARISON_LABELS[c["field"]]),
            detail=c["detail"] + " This needs human review; it is not proof of fraud.",
            evidence_source=_SOURCE,
            is_interpretation=partial,
            reliability_note=(
                f"OCR confidence of the screenshot value: {c['extracted_confidence']}. "
                f"{_CORRELATION_NOTE} {_SETTLEMENT_NOTE}"
            ),
        ))
    return findings


def _comparison_notes(comparisons: list[dict]) -> list[str]:
    supplied = [c for c in comparisons if c["status"] != "not_supplied"]
    if not supplied:
        return [
            "[payment] No expected payment details were supplied, so the screenshot "
            "was not compared with what you expected."
        ]
    notes = [f"[payment] Expected {c['field']}: {c['status'].replace('_', ' ')} — {c['detail']}"
             for c in supplied]
    if all(c["status"] in ("match", "format_difference") for c in supplied):
        notes.append(
            "[payment] Every supplied expected value matched the screenshot. This does "
            "NOT prove payment settlement; screenshots can be edited."
        )
    return notes


def _extraction_status(payment: dict, fields: dict[str, dict]) -> str:
    if not (payment.get("raw_text") or "").strip():
        return "no_text"
    if not any(_value(fields, n) for n in fields):
        return "no_fields_recognised"
    return "fields_extracted"


def evaluate_payment(
    payment: dict, expected: Optional[dict] = None,
) -> tuple[list[AnalysisFinding], list[str], dict]:
    """
    Return (findings, notes, summary) for an analysed payment object.

    `expected` holds normalised user expectations (amount as a Decimal string,
    ISO currency, recipient, reference); any key may be absent or None.
    `summary` is the structured, JSON-serialisable payment record stored with
    the analysis result.
    """
    fields = _fields_by_name(payment)
    comparisons = compare_expected(payment, expected)
    findings = (
        _mismatch_findings(payment)
        + _comparison_findings(comparisons)
        + _placeholder_recipient(fields)
        + _reference_findings(fields)
        + _amount_findings(fields)
    )
    for f in findings:
        f.correlation_group = CORRELATION_GROUP
    notes = _notes(payment, fields, user_expectations=expected is not None)
    if expected is not None:
        notes.extend(_comparison_notes(comparisons))
    summary = {
        "extraction_status": _extraction_status(payment, fields),
        "settlement_status": "not_determinable",
        "settlement_note": _SETTLEMENT_NOTE,
        "extracted_fields": [
            {"field_name": n, "extracted_value": f.get("extracted_value"),
             "confidence": f.get("confidence") or "unknown"}
            for n, f in sorted(fields.items())
        ],
        "expected_supplied": {k: (expected or {}).get(k) for k in _EXPECTED_KEYS},
        "comparisons": comparisons,
        "quality": payment.get("quality") if isinstance(payment.get("quality"), dict) else None,
        "correlation_group": CORRELATION_GROUP,
    }
    return findings, notes, summary


# ── Public entry point ────────────────────────────────────────────────────────

def check_payment(
    payment: dict, expected: Optional[dict] = None,
) -> tuple[list[AnalysisFinding], list[str]]:
    """Return (findings, notes) for an analysed payment object."""
    findings, notes, _ = evaluate_payment(payment, expected)
    return findings, notes
