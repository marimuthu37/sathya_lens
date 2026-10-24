"""
payment_ocr.py -- Payment Screenshot OCR and Field Reconciliation
================================================================
Extracts structured fields from payment screenshots using Tesseract OCR
(via pytesseract) and compares them against caller-supplied expected values.

Design constraints
------------------
* Falls back gracefully if Tesseract is not installed (status: unavailable).
* OCR is imperfect; every result carries a limitations list.
* This component does NOT confirm payment settlement. A screenshot is
  evidence of an image; it cannot prove a real-time bank transaction.
* Never claims that a matching screenshot proves the payment cleared.

Comparison policy (per field type)
-----------------------------------
amount
    Uses Python Decimal after stripping all currency symbols, commas and
    whitespace. "5000", "5,000", "5000.00", "Rs. 5,000.00" and
    "5000.0" all compare equal.  The original extracted string is always
    preserved in extracted_value for display.
    Amounts that cannot be parsed as Decimal fall back to case-insensitive
    string comparison.

transaction_id
    Compared case-insensitively after stripping leading keyword prefixes
    (UTR, TXN, REF, NEFT, IMPS, RTGS) and non-alphanumeric separators.
    "UTR123456789" and "123456789" compare equal if the numeric body
    matches.  The canonical form is the stripped alphanumeric body.
    The original extracted string is always preserved in extracted_value.

All other fields (date, time, payee_name, payer_name, upi_id, status_text,
    payment_method, bank_name)
    Case-insensitive string match after stripping leading/trailing whitespace.
    No currency or ID normalisation is applied.

Extracted fields (best-effort; field present only when text is found)
---------------------------------------------------------------------
  amount         -- Monetary amount (with currency symbol if present)
  transaction_id -- UTR / Ref / Transaction ID
  date           -- Transaction date
  time           -- Transaction time
  payee_name     -- Recipient name
  payer_name     -- Sender name
  upi_id         -- UPI ID or VPA
  bank_name      -- Issuing bank name
  status_text    -- Payment status text (e.g. "Success", "Pending")
  payment_method -- Card / UPI / NEFT / IMPS etc.
"""

from __future__ import annotations

import logging
import os
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from satyalens_analysis.schema import (
    ComponentStatus,
    PaymentField,
    PaymentResult,
    payment_failed,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Dependency check + auto-configuration
# ---------------------------------------------------------------------------

def _auto_configure_tesseract() -> None:
    """
    Auto-detect Tesseract binary and set pytesseract.tesseract_cmd if not
    already pointing to a valid executable.  Checks (in order):
      1. Scoop user install  (HOME/scoop/apps/tesseract/current/tesseract.exe)
      2. Program Files       (C:/Program Files/Tesseract-OCR/tesseract.exe)
      3. Program Files x86   (C:/Program Files (x86)/Tesseract-OCR/tesseract.exe)
      4. System PATH         (via shutil.which)
    Also sets TESSDATA_PREFIX for Scoop installs if not already set.
    """
    import shutil
    try:
        import pytesseract
    except ImportError:
        return

    current_cmd = pytesseract.pytesseract.tesseract_cmd
    if current_cmd and current_cmd != "tesseract" and Path(current_cmd).exists():
        return

    candidates = [
        Path.home() / "scoop" / "apps" / "tesseract" / "current" / "tesseract.exe",
        Path("C:/Program Files/Tesseract-OCR/tesseract.exe"),
        Path("C:/Program Files (x86)/Tesseract-OCR/tesseract.exe"),
    ]
    for c in candidates:
        if c.is_file():
            pytesseract.pytesseract.tesseract_cmd = str(c)
            scoop_tessdata = Path.home() / "scoop" / "persist" / "tesseract" / "tessdata"
            if scoop_tessdata.is_dir() and not os.environ.get("TESSDATA_PREFIX"):
                os.environ["TESSDATA_PREFIX"] = str(scoop_tessdata)
            logger.debug("payment_ocr: Tesseract auto-configured at %s", c)
            return

    tess_in_path = shutil.which("tesseract")
    if tess_in_path:
        pytesseract.pytesseract.tesseract_cmd = tess_in_path
        logger.debug("payment_ocr: Tesseract found in PATH at %s", tess_in_path)


def _check_dependencies() -> Tuple[bool, str]:
    """Return (available: bool, reason: str)."""
    try:
        import pytesseract  # noqa: F401
        from PIL import Image  # noqa: F401
    except ImportError as exc:
        return False, (
            f"Missing Python dependency: {exc}. "
            "Install with: pip install pytesseract Pillow opencv-python"
        )

    _auto_configure_tesseract()

    try:
        import pytesseract
        pytesseract.get_tesseract_version()
        return True, ""
    except pytesseract.TesseractNotFoundError:
        return False, (
            "Tesseract OCR binary not found. "
            "Install via Scoop (no admin): scoop install tesseract, then "
            "download eng.traineddata from https://github.com/tesseract-ocr/tessdata_fast. "
            "Or: https://github.com/UB-Mannheim/tesseract/wiki (Windows installer). "
            "Or: sudo apt-get install tesseract-ocr (Linux/macOS). "
            "Set pytesseract.pytesseract.tesseract_cmd if installed in a non-default path."
        )
    except Exception as exc:  # noqa: BLE001
        return False, f"Tesseract check failed: {exc}"


_DEPENDENCIES_OK, _DEPENDENCY_REASON = _check_dependencies()

# ---------------------------------------------------------------------------
# Regex patterns for field extraction
# ---------------------------------------------------------------------------

_FIELD_PATTERNS: Dict[str, List[str]] = {
    "amount": [
        # keyword then value
        r"(?:amount|paid|total|payment|rs\.?|inr|usd|eur|gbp)"
        r"\s*[:\-]?\s*([₹\$€£]?\s*[\d,]+(?:\.\d{1,2})?)",
        # leading currency symbol
        r"([₹\$€£]\s*[\d,]+(?:\.\d{1,2})?)",
        # value then keyword
        r"([\d,]+(?:\.\d{2})?)\s*(?:rs\.?|₹|inr|usd|\$)",
    ],
    "transaction_id": [
        # Explicit label then alphanumeric body (6-30 chars)
        # Separators between keyword and value: colon, dash, space, comma, period
        r"(?:utr|transaction\s*(?:id|no|number|ref)|ref(?:erence)?\s*(?:id|no|number)?"
        r"|txn\s*(?:id|no)|order\s*(?:id|no)|neft\s*ref|imps\s*ref|rtgs\s*ref)"
        r"\s*[:\-,\.]?\s*([A-Za-z0-9]{6,30})",
        # Short keyword + optional # separator
        r"(?:ref|txn|utr)\s*#?\s*([A-Za-z0-9]{6,30})",
    ],
    "date": [
        r"\b(\d{1,2}[\/\-\.]\d{1,2}[\/\-\.]\d{2,4})\b",
        r"\b(\d{1,2}\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*\s+\d{2,4})\b",
        r"\b((?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*\s+\d{1,2},?\s+\d{2,4})\b",
    ],
    "time": [
        r"\b(\d{1,2}:\d{2}(?::\d{2})?\s*(?:am|pm)?)\b",
        r"\b(\d{2}:\d{2}(?::\d{2})?)\b",
    ],
    "payee_name": [
        r"(?:to|paid\s+to|recipient|payee|beneficiary)\s*[:\-]?\s*([A-Za-z][A-Za-z\s\.]{2,40})",
    ],
    "payer_name": [
        r"(?:from|paid\s+by|sender|payer)\s*[:\-]?\s*([A-Za-z][A-Za-z\s\.]{2,40})",
    ],
    "upi_id": [
        r"([a-z0-9._\-]+@[a-z]{2,20})",
    ],
    "bank_name": [
        r"(?:bank|issued\s+by|via)\s*[:\-]?\s*([A-Za-z][A-Za-z\s]{3,40}(?:bank|pay|payments))",
        r"\b(hdfc|sbi|icici|axis|kotak|pnb|canara|union|yes\s+bank|paytm|phonepe|gpay)\b",
    ],
    "status_text": [
        r"\b(payment\s+(?:successful|success|completed|failed|pending|processing))\b",
        r"\b(transaction\s+(?:successful|failed|pending))\b",
        r"\b(successfully\s+(?:paid|transferred|sent))\b",
        r"\b(success|successful|completed|failed|pending|processing)\b",
    ],
    "payment_method": [
        r"\b(upi|neft|rtgs|imps|net\s+banking|credit\s+card|debit\s+card"
        r"|wallet|phonepe|gpay|google\s+pay|paytm)\b",
    ],
}

# Fields that use amount-specific Decimal comparison
_AMOUNT_FIELDS = frozenset({"amount"})

# Fields that use transaction-ID normalisation (strip keyword prefix)
_TXNID_FIELDS = frozenset({"transaction_id"})

# Keyword prefixes OCR may include in a captured transaction-ID group
_TXNID_PREFIX_RE = re.compile(
    r"^(?:utr|txn|ref|neft|imps|rtgs|transaction|reference|order)\s*",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Image preprocessing
# ---------------------------------------------------------------------------

def _preprocess_image(image_path: str):
    """Return a PIL Image, preprocessed for better OCR accuracy."""
    import cv2  # type: ignore
    import numpy as np
    from PIL import Image

    img = cv2.imread(str(image_path))
    if img is None:
        raise ValueError(f"Could not read image at path: {image_path!r}")

    h, w = img.shape[:2]
    if h < 800 or w < 400:
        scale = max(800 / h, 400 / w, 2.0)
        img = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    processed = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 2
    )
    return Image.fromarray(processed)


# ---------------------------------------------------------------------------
# Field extraction helpers
# ---------------------------------------------------------------------------

def _extract_fields(raw_text: str) -> Dict[str, str]:
    """Apply regex patterns to raw OCR text and return best match per field."""
    normalised = raw_text.lower()
    results: Dict[str, str] = {}

    for field_name, patterns in _FIELD_PATTERNS.items():
        for pattern in patterns:
            match = re.search(pattern, normalised, re.IGNORECASE)
            if match:
                value = (
                    match.group(1).strip()
                    if match.lastindex and match.lastindex >= 1
                    else match.group(0).strip()
                )
                results[field_name] = value
                break

    return results


# ---------------------------------------------------------------------------
# Comparison helpers
# ---------------------------------------------------------------------------

def _normalise_amount(value: str) -> str:
    """
    Strip currency symbols, commas, whitespace and return a canonical decimal
    string suitable for Decimal() parsing.

    Examples
    --------
    "Rs. 5,000.00" -> "5000.00"
    "₹5000"        -> "5000"
    "$100.5"       -> "100.5"

    NOTE: Do NOT use this function on transaction IDs, dates, UPI IDs or names.
    """
    # Remove known currency symbols and words
    cleaned = re.sub(r"[₹\$€£]", "", value)
    cleaned = re.sub(r"\b(?:rs\.?|inr|usd|eur|gbp)\b", "", cleaned, flags=re.IGNORECASE)
    # Remove commas and whitespace
    cleaned = re.sub(r"[,\s]", "", cleaned)
    return cleaned.strip()


def _amounts_equal(extracted: str, expected: str) -> bool:
    """
    Compare two monetary strings using Python Decimal arithmetic.

    "5000", "5000.00", "5,000.00", "Rs. 5,000" all compare equal.
    Falls back to case-insensitive string comparison if Decimal parsing fails.

    The original extracted string is never mutated; comparison is performed on
    normalised copies only.
    """
    norm_extracted = _normalise_amount(extracted)
    norm_expected = _normalise_amount(expected)
    try:
        return Decimal(norm_extracted) == Decimal(norm_expected)
    except InvalidOperation:
        # Non-numeric amounts: fall back to string comparison
        return norm_extracted.lower() == norm_expected.lower()


def _normalise_txnid(value: str) -> str:
    """
    Strip leading keyword prefixes and non-alphanumeric separators from a
    transaction ID so that "UTR123456789", "utr 123456789", and "123456789"
    all produce the same canonical body for comparison.

    Only alphanumeric characters are retained after prefix removal.
    The original extracted string is never modified; this operates on a copy.

    Examples
    --------
    "UTR123456789"   -> "123456789"  (prefix stripped)
    "utr 123456789"  -> "123456789"
    "TXN-ABCD1234"   -> "ABCD1234"
    "123456789"      -> "123456789"  (no change)
    """
    stripped = _TXNID_PREFIX_RE.sub("", value.strip())
    # Keep only alphanumeric characters
    return re.sub(r"[^A-Za-z0-9]", "", stripped)


def _txnids_equal(extracted: str, expected: str) -> bool:
    """
    Compare transaction IDs after canonical normalisation.
    Case-insensitive; leading keyword prefixes and separators ignored.
    """
    return _normalise_txnid(extracted).lower() == _normalise_txnid(expected).lower()


def _fuzzy_match(a: str, b: str) -> bool:
    """
    Default case-insensitive string match for fields that are not amounts or
    transaction IDs (dates, names, UPI IDs, status text, etc.).
    """
    return a.strip().lower() == b.strip().lower()


def _compare_field(field_name: str, extracted: str, expected: str) -> Tuple[bool, str]:
    """
    Apply the correct comparison policy for the given field type.

    Returns
    -------
    (match: bool, comparison_note: str)
        comparison_note is empty when the default policy is used; non-empty
        when a specialised normaliser was applied so callers can surface it.
    """
    if field_name in _AMOUNT_FIELDS:
        match = _amounts_equal(extracted, expected)
        note = (
            f"Decimal comparison: normalised extracted={_normalise_amount(extracted)!r}, "
            f"normalised expected={_normalise_amount(expected)!r}"
        )
        return match, note

    if field_name in _TXNID_FIELDS:
        match = _txnids_equal(extracted, expected)
        note = (
            f"Transaction-ID comparison: canonical extracted={_normalise_txnid(extracted)!r}, "
            f"canonical expected={_normalise_txnid(expected)!r}"
        )
        return match, note

    # Default: case-insensitive string match
    return _fuzzy_match(extracted, expected), ""


# ---------------------------------------------------------------------------
# Public function
# ---------------------------------------------------------------------------

_STATIC_LIMITATIONS = [
    "OCR may produce errors on stylised fonts, low-resolution or compressed screenshots.",
    "This component cannot verify that a payment transaction actually cleared in real time.",
    "A matching screenshot does NOT prove payment settlement; screenshots can be edited.",
    "Field extraction uses regex patterns; unusual layouts may produce missed or incorrect fields.",
    "Amount comparison uses Decimal arithmetic; transaction IDs are compared after stripping "
    "keyword prefixes (UTR, TXN, REF). Original extracted values are preserved for display.",
    "Confidence ratings reflect OCR quality heuristics, not ground-truth accuracy.",
]


def analyse_payment(
    image_path: str,
    expected_fields: Optional[Dict[str, str]] = None,
) -> PaymentResult:
    """
    Run OCR on a payment screenshot and compare extracted fields against
    expected values supplied by the caller.

    Parameters
    ----------
    image_path : str
        Absolute or relative path to the payment screenshot image.
        Supported formats: PNG, JPEG, BMP, TIFF, WEBP (Pillow-supported).
    expected_fields : dict, optional
        Mapping of field_name -> expected_value strings.
        Supported keys: 'amount', 'transaction_id', 'date', 'payee_name',
        'payer_name', 'upi_id', 'status_text', 'payment_method'.

        Comparison policy per field type
        ---------------------------------
        amount         : Decimal comparison after stripping currency symbols and
                         commas. "5000", "5000.00", "5,000.00" compare equal.
        transaction_id : Case-insensitive after stripping keyword prefixes (UTR,
                         TXN, REF) and non-alphanumeric chars. "UTR123" == "123".
        all others     : Case-insensitive string match.

        The original extracted_value is always preserved for display; only the
        normalised copies are used for match determination.

    Returns
    -------
    PaymentResult
        status='analysed'       -- OCR ran and produced output.
        status='unavailable'    -- pytesseract or Tesseract binary missing.
        status='failed'         -- Runtime error during analysis.
        status='not_applicable' -- No image_path provided.
    """
    if not image_path:
        return PaymentResult(
            status=ComponentStatus.NOT_APPLICABLE,
            limitations=["No payment screenshot path provided."],
        )

    if not _DEPENDENCIES_OK:
        return PaymentResult(
            status=ComponentStatus.UNAVAILABLE,
            limitations=[_DEPENDENCY_REASON] + list(_STATIC_LIMITATIONS),
        )

    path = Path(image_path)
    if not path.exists():
        return payment_failed(f"Image file not found: {image_path!r}")
    if not path.is_file():
        return payment_failed(f"Path is not a file: {image_path!r}")

    try:
        import pytesseract

        try:
            pil_image = _preprocess_image(str(path))
        except Exception as exc:  # noqa: BLE001
            logger.warning("payment_ocr: preprocessing failed (%s), using raw image.", exc)
            from PIL import Image
            pil_image = Image.open(str(path))

        custom_config = r"--oem 3 --psm 6"
        raw_text: str = pytesseract.image_to_string(pil_image, config=custom_config)
        logger.info("payment_ocr: OCR complete, %d chars extracted.", len(raw_text))

        extracted = _extract_fields(raw_text)
        expected = expected_fields or {}

        payment_fields: List[PaymentField] = []
        mismatches: List[str] = []

        all_field_names = set(extracted.keys()) | set(expected.keys())
        for fname in sorted(all_field_names):
            extracted_val = extracted.get(fname)
            expected_val = expected.get(fname)

            match_result: Optional[bool] = None
            comparison_note = ""

            if expected_val is not None and extracted_val is not None:
                match_result, comparison_note = _compare_field(fname, extracted_val, expected_val)
                if not match_result:
                    detail = f" [{comparison_note}]" if comparison_note else ""
                    mismatches.append(
                        f"{fname}: expected={expected_val!r}, "
                        f"extracted={extracted_val!r}{detail}"
                    )
            elif expected_val is not None and extracted_val is None:
                match_result = False
                mismatches.append(
                    f"{fname}: expected={expected_val!r}, "
                    "but field was not found in screenshot."
                )

            confidence = "medium" if extracted_val else "low"

            payment_fields.append(PaymentField(
                field_name=fname,
                extracted_value=extracted_val,
                expected_value=expected_val,
                match=match_result,
                confidence=confidence,
            ))

        return PaymentResult(
            status=ComponentStatus.ANALYSED,
            raw_text=raw_text[:4000],
            fields=payment_fields,
            mismatches=mismatches,
            limitations=list(_STATIC_LIMITATIONS),
        )

    except Exception as exc:  # noqa: BLE001
        logger.exception("payment_ocr: unexpected error.")
        return payment_failed(f"OCR analysis error: {exc}")
