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
  description    -- Description / narration / remarks line (extracted only;
                    never compared)

Extracted values keep the original OCR casing and punctuation (matching is
case-insensitive, values are not lowercased), so discrepancies such as a
trailing "--" on a reference stay visible to the caller.

OCR pass selection and confidence
---------------------------------
OCR runs on two renderings of the image (adaptive threshold and plain
grayscale) and keeps the text of the pass with the higher mean Tesseract word
confidence.  Each field's confidence ("low" / "medium" / "high") is the lowest
Tesseract confidence of the words that make up the value.  When word
confidences are unavailable, confidence falls back to "medium" for any
extracted value.  These are Tesseract self-reports, not ground-truth accuracy.
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
        # currency code then value — the code is KEPT in the captured value so
        # the stated currency stays visible ("GBP 1,263.83", not "1,263.83")
        r"\b((?:rs\.?|inr|usd|eur|gbp)\s*[:\-]?\s*[\d,]+(?:\.\d{1,2})?)",
        # keyword then value
        r"(?:amount|paid|total|payment)"
        r"\s*[:\-]?\s*([₹\$€£]?\s*[\d,]+(?:\.\d{1,2})?(?:[ \t]*(?:inr|usd|eur|gbp|rs)\b\.?)?)",
        # leading currency symbol
        r"([₹\$€£]\s*[\d,]+(?:\.\d{1,2})?)",
        # value then currency code (code kept)
        r"([\d,]+(?:\.\d{2})?\s*(?:rs\.?|₹|inr|usd|eur|gbp|\$))",
    ],
    "transaction_id": [
        # Explicit label then ID body. Separators between keyword and value:
        # colon, dash, space, comma, period, hash. The body may contain
        # internal/trailing - _ / (kept as-is) and must contain a digit, so a
        # label word such as "No." is never captured as the ID.
        r"\b(?:utr|transaction\s*(?:ref(?:erence)?|id)?\s*(?:id|no|number)?"
        r"|ref(?:erence)?\s*(?:id|no|number)?"
        r"|txn\s*(?:id|no|number)?|order\s*(?:id|no|number)?|neft\s*ref|imps\s*ref|rtgs\s*ref)"
        r"[\s:\-,\.#]*((?=[A-Za-z0-9\-_/]*\d)[A-Za-z0-9][A-Za-z0-9\-_/]{3,40})",
        # Short keyword + optional separator + body
        r"(?:ref|txn|utr)[\s:\-,\.#]*((?=[A-Za-z0-9\-_/]*\d)[A-Za-z0-9][A-Za-z0-9\-_/]{3,40})",
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
    # Names stop at the end of the OCR line ([ \t], not \s) so a value never
    # absorbs the following label, e.g. "Merchant Name\nFrom Revolut".
    "payee_name": [
        r"\b(?:to|paid\s+to|recipient|payee|beneficiary)\b[ \t]*[:\-]?[ \t]*([A-Za-z][A-Za-z \t\.]{2,40})",
        # Stacked layout: the label alone on its line, the name on the next
        # non-blank line (Tesseract often emits a blank line between them).
        # That line must not itself be another field label.
        r"(?m)^[ \t]*(?:paid\s+to|recipient|payee|beneficiary|to)[ \t]*:?[ \t]*\n(?:[ \t]*\n)?[ \t]*"
        r"(?!(?:amount|reference|ref|status|from|date|time|description|transaction|utr)\b)"
        r"([A-Za-z][A-Za-z \t\.]{2,40})",
    ],
    "payer_name": [
        r"\b(?:from|paid\s+by|sender|payer)\b[ \t]*[:\-]?[ \t]*([A-Za-z][A-Za-z \t\.]{2,40})",
    ],
    "upi_id": [
        r"([a-z0-9._\-]+@[a-z]{2,20})",
    ],
    "bank_name": [
        r"\b(?:bank|issued\s+by|via)\b[ \t]*[:\-]?[ \t]*([A-Za-z][A-Za-z \t]{3,40}(?:bank|pay|payments))\b",
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
    "description": [
        r"\b(?:description|narration|remarks?|purpose|memo)\b[ \t]*[:\-]?[ \t]*([^\n]{1,80})",
    ],
}

# Fields that use amount-specific Decimal comparison
_AMOUNT_FIELDS = frozenset({"amount"})

# Fields that use transaction-ID normalisation (strip keyword prefix)
_TXNID_FIELDS = frozenset({"transaction_id"})

# Keyword prefixes OCR may include in a captured transaction-ID group
_TXNID_PREFIX_RE = re.compile(
    r"^(?:(?:utr|txn|ref(?:erence)?|neft|imps|rtgs|transaction|order)[\s\-_:#]*)+",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Image preprocessing
# ---------------------------------------------------------------------------

def _upscale_factor(h: int, w: int) -> float:
    """Factor by which small images are enlarged before OCR (1.0 = unchanged)."""
    if h < 800 or w < 400:
        return max(800 / h, 400 / w, 2.0)
    return 1.0


def _preprocess_image(image_path: str):
    """Return a PIL Image, preprocessed for better OCR accuracy."""
    import cv2  # type: ignore
    import numpy as np
    from PIL import Image

    img = cv2.imread(str(image_path))
    if img is None:
        raise ValueError(f"Could not read image at path: {image_path!r}")

    scale = _upscale_factor(*img.shape[:2])
    if scale != 1.0:
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
    """
    Apply regex patterns to raw OCR text and return best match per field.

    Matching is case-insensitive but values keep their original OCR casing.
    """
    results: Dict[str, str] = {}

    for field_name, patterns in _FIELD_PATTERNS.items():
        for pattern in patterns:
            match = re.search(pattern, raw_text, re.IGNORECASE)
            if match:
                value = (
                    match.group(1).strip()
                    if match.lastindex and match.lastindex >= 1
                    else match.group(0).strip()
                )
                if value:
                    results[field_name] = value
                    break

    return results


# ---------------------------------------------------------------------------
# OCR pass selection and word confidence
# ---------------------------------------------------------------------------

_OCR_CONFIG = r"--oem 3 --psm 6"

# Tesseract word confidence (0-100) thresholds for field confidence labels.
_HIGH_CONFIDENCE = 85.0
_MEDIUM_CONFIDENCE = 60.0


def _grayscale_image(image_path: str):
    """Plain grayscale rendering, scaled like _preprocess_image (no threshold)."""
    import cv2  # type: ignore
    from PIL import Image

    img = cv2.imread(str(image_path))
    if img is None:
        raise ValueError(f"Could not read image at path: {image_path!r}")
    scale = _upscale_factor(*img.shape[:2])
    if scale != 1.0:
        img = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    return Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY))


def _word_data(pil_image) -> Tuple[Optional[Dict[str, float]], List[float]]:
    """
    Return ({lowercased word: highest confidence} or None, [word box heights])
    for one OCR pass.  The dict is None if Tesseract word data is unavailable
    or contains no words; heights are in the pixel space of `pil_image`.
    """
    import pytesseract

    try:
        data = pytesseract.image_to_data(
            pil_image, config=_OCR_CONFIG, output_type=pytesseract.Output.DICT
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("payment_ocr: word confidence unavailable (%s).", exc)
        return None, []
    words: Dict[str, float] = {}
    heights: List[float] = []
    texts, confs = data.get("text", []), data.get("conf", [])
    box_heights = data.get("height") or [None] * len(texts)
    for text, conf, height in zip(texts, confs, box_heights):
        try:
            conf_f = float(conf)
        except (TypeError, ValueError):
            continue
        word = str(text).strip().lower()
        if word and conf_f >= 0:
            words[word] = max(conf_f, words.get(word, -1.0))
            if isinstance(height, (int, float)) and height > 0:
                heights.append(float(height))
    return (words or None), heights


def _word_confidences(pil_image) -> Optional[Dict[str, float]]:
    """{lowercased word: highest confidence} for one OCR pass, or None."""
    return _word_data(pil_image)[0]


def _mean(values) -> Optional[float]:
    values = list(values)
    return sum(values) / len(values) if values else None


def _select_ocr_pass(image_path: str):
    """
    Return (pil_image, word_confidences or None, word_heights_px) for the OCR
    pass to use.  Word heights are converted back to ORIGINAL-image pixels.

    The adaptive-threshold rendering is the default; the plain grayscale
    rendering replaces it only when its mean word confidence is higher.
    """
    from PIL import Image

    try:
        default = _preprocess_image(image_path)
    except Exception as exc:  # noqa: BLE001
        logger.warning("payment_ocr: preprocessing failed (%s), using raw image.", exc)
        raw = Image.open(image_path)
        words, heights = _word_data(raw)
        return raw, words, heights

    with Image.open(image_path) as original:
        scale = _upscale_factor(original.height, original.width)

    best_img, (best_words, best_heights) = default, _word_data(default)
    try:
        gray = _grayscale_image(image_path)
    except Exception:  # noqa: BLE001
        return best_img, best_words, [h / scale for h in best_heights]
    gray_words, gray_heights = _word_data(gray)
    best_mean = _mean(best_words.values()) if best_words else None
    gray_mean = _mean(gray_words.values()) if gray_words else None
    if gray_mean is not None and (best_mean is None or gray_mean > best_mean):
        best_img, best_words, best_heights = gray, gray_words, gray_heights
    return best_img, best_words, [h / scale for h in best_heights]


def _field_confidence(value: Optional[str], words: Optional[Dict[str, float]]) -> str:
    """Lowest word confidence of the value's tokens, mapped to low/medium/high."""
    if not value:
        return "low"
    if not words:
        return "medium"
    confs = []
    for token in value.split():
        conf = words.get(token.lower())
        if conf is None:
            conf = words.get(token.strip(".,:;!?()[]").lower())
        if conf is not None:
            confs.append(conf)
    if not confs:
        # Value text not found word-for-word in the word data: use the pass mean.
        confs = [_mean(words.values())]
    lowest = min(confs)
    if lowest >= _HIGH_CONFIDENCE:
        return "high"
    if lowest >= _MEDIUM_CONFIDENCE:
        return "medium"
    return "low"


# ---------------------------------------------------------------------------
# Image-quality assessment
# ---------------------------------------------------------------------------
#
# Thresholds were set by measuring each metric against actual extraction on a
# small fixture set: one synthetic receipt (Arial 30px) rendered sharp, Gaussian-
# blurred (sigma 1-7), downscaled (x0.18-x0.5) and contrast-reduced (3-60 grey
# levels), plus five synthetic "banxis" mock-up screenshots (~10px text) blurred
# at sigma 1-3.  They are NOT validated on real-world screenshots; they flag
# conditions under which extraction was observed to degrade on those fixtures.
#
#   edge sharpness  sharp 0.20-0.27; sigma 1 ~0.10-0.12 (occasional misreads);
#                   sigma 1.5 ~0.064-0.07 (no fields on ~10px text)
#   text height     >=8.3px read correctly; 6.5-6.6px dropped fields and misread
#                   "€7.50" as "€750"
#   mean word conf  readable fixtures 85-95; failed or misread fixtures 29-69
#   contrast        reported only: OCR stayed correct down to 3 grey levels, so
#                   no contrast threshold is applied

_SHARPNESS_CAUTION = 0.15
_SHARPNESS_SEVERE = 0.08
_MIN_TEXT_HEIGHT_PX = 8.0
_MIN_MEAN_WORD_CONFIDENCE = 70.0
_MIN_OCR_WORDS = 5
_CRITICAL_FIELDS = ("amount", "payee_name", "transaction_id")

_QUALITY_NOTE = (
    "Quality checks are heuristics calibrated on a small synthetic fixture set, "
    "not on real-world screenshots. Passing them does not mean the extracted "
    "values are correct."
)


def _edge_sharpness(gray) -> Optional[float]:
    """
    Contrast-independent sharpness: mean |Laplacian| / mean |Sobel gradient|
    over strong-edge pixels.  Blur spreads each edge, which lowers the ratio;
    scaling the contrast scales both terms equally.  None if there are no edges.
    """
    import cv2  # type: ignore
    import numpy as np

    g = gray.astype(np.float64)
    mag = np.hypot(cv2.Sobel(g, cv2.CV_64F, 1, 0), cv2.Sobel(g, cv2.CV_64F, 0, 1))
    if mag.max() <= 0:
        return None
    edges = mag > 0.1 * mag.max()
    lap = np.abs(cv2.Laplacian(g, cv2.CV_64F))
    return float(lap[edges].mean() / mag[edges].mean())


def _assess_quality(
    image_path: str,
    raw_text: str,
    words: Optional[Dict[str, float]],
    word_heights: List[float],
    extracted: Dict[str, str],
    confidences: Dict[str, str],
) -> Dict:
    """
    Return an explainable quality assessment of one screenshot.

    Each warning has a code, a severity and a plain-language message.
    'severe' means extracted values should not be compared automatically;
    'caution' means values may be misread and need checking by eye.
    """
    import statistics

    warnings: List[Dict[str, str]] = []

    def warn(code: str, severity: str, message: str) -> None:
        warnings.append({"code": code, "severity": severity, "message": message})

    metrics: Dict[str, Optional[float]] = {
        "width_px": None, "height_px": None, "edge_sharpness": None,
        "contrast_range": None, "median_text_height_px": None,
        "ocr_word_count": len(raw_text.split()),
        "ocr_mean_word_confidence": None,
    }
    try:
        import cv2  # type: ignore
        import numpy as np

        img = cv2.imread(str(image_path), cv2.IMREAD_GRAYSCALE)
        if img is not None:
            metrics["height_px"], metrics["width_px"] = map(int, img.shape[:2])
            sharp = _edge_sharpness(img)
            metrics["edge_sharpness"] = round(sharp, 3) if sharp is not None else None
            p1, p99 = np.percentile(img, [1, 99])
            metrics["contrast_range"] = round(float(p99 - p1), 1)
    except Exception as exc:  # noqa: BLE001
        logger.warning("payment_ocr: image metrics unavailable (%s).", exc)
    if word_heights:
        metrics["median_text_height_px"] = round(statistics.median(word_heights), 1)
    if words:
        metrics["ocr_mean_word_confidence"] = round(_mean(words.values()), 1)

    if not raw_text.strip():
        warn("no_text", "severe",
             "OCR found no readable text. The image may be unreadable, blank or "
             "not a payment screenshot; nothing could be checked.")
    elif metrics["ocr_word_count"] < _MIN_OCR_WORDS:
        warn("insufficient_text", "severe",
             f"OCR read only {metrics['ocr_word_count']} word(s), too little to "
             "check a payment screenshot reliably.")

    sharp = metrics["edge_sharpness"]
    if sharp is not None and sharp < _SHARPNESS_SEVERE:
        warn("blurred", "severe",
             f"The image appears blurred (edge sharpness {sharp:.3f}). On test "
             "fixtures at this level, OCR missed or misread payment fields.")
    elif sharp is not None and sharp < _SHARPNESS_CAUTION:
        warn("possibly_blurred", "caution",
             f"The image may be slightly blurred (edge sharpness {sharp:.3f}); "
             "characters such as digits and separators may be misread.")

    text_h = metrics["median_text_height_px"]
    if text_h is not None and text_h < _MIN_TEXT_HEIGHT_PX:
        warn("low_resolution", "severe",
             f"Text is very small (median height {text_h:.1f}px). At this size "
             "test fixtures produced silently misread amounts (e.g. '€750' for '€7.50').")

    mean_conf = metrics["ocr_mean_word_confidence"]
    if mean_conf is not None and mean_conf < _MIN_MEAN_WORD_CONFIDENCE:
        warn("low_ocr_confidence", "severe",
             f"Tesseract's self-reported mean word confidence is low ({mean_conf:.0f}/100). "
             "This is not a measure of accuracy, but low values coincided with "
             "misreads on test fixtures.")

    if raw_text.strip():
        missing = [f for f in _CRITICAL_FIELDS if f not in extracted]
        if missing:
            warn("critical_field_missing", "caution",
                 f"Could not read: {', '.join(missing)}. These cannot be checked "
                 "from this screenshot.")
        low = [f for f in _CRITICAL_FIELDS if f in extracted and confidences.get(f) == "low"]
        if low:
            warn("critical_field_low_confidence", "caution",
                 f"Low OCR confidence for: {', '.join(low)}. Compare these values "
                 "with the screenshot yourself.")

    if not raw_text.strip():
        assessment = "unreadable"
    elif warnings:
        assessment = "limited"
    else:
        assessment = "no_issues_detected"
    return {"assessment": assessment, "metrics": metrics, "warnings": warnings,
            "note": _QUALITY_NOTE}


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
    cleaned = re.sub(r"(?:\b(?:inr|usd|eur|gbp)\b|\brs\.?)", "", cleaned, flags=re.IGNORECASE)
    # Remove commas, whitespace and a separator after a currency code ("INR: 500")
    cleaned = re.sub(r"[,\s]", "", cleaned).lstrip(":-")
    return cleaned.strip()


_CURRENCY_MARKERS = (
    (re.compile(r"₹|\brs\.?|\binr\b", re.IGNORECASE), "INR"),
    (re.compile(r"\$|\busd\b", re.IGNORECASE), "USD"),
    (re.compile(r"€|\beur\b", re.IGNORECASE), "EUR"),
    (re.compile(r"£|\bgbp\b", re.IGNORECASE), "GBP"),
)


def _currency(value: str) -> Optional[str]:
    """Currency code stated in a monetary string, None if none, 'MIXED' if several."""
    found = {code for rx, code in _CURRENCY_MARKERS if rx.search(value)}
    if len(found) > 1:
        return "MIXED"
    return found.pop() if found else None


def _amounts_equal(extracted: str, expected: str) -> bool:
    """
    Compare two monetary strings using Python Decimal arithmetic.

    "5000", "5000.00", "5,000.00", "Rs. 5,000" all compare equal.
    If BOTH strings state a currency and the currencies differ ("₹500" vs "$500"),
    they are never equal; a string with conflicting markers ("₹500 USD") never
    matches anything; a single currency on one side only is ignored.
    Falls back to case-insensitive string comparison if Decimal parsing fails.

    The original extracted string is never mutated; comparison is performed on
    normalised copies only.
    """
    cur_extracted, cur_expected = _currency(extracted), _currency(expected)
    if "MIXED" in (cur_extracted, cur_expected):
        return False
    if cur_extracted and cur_expected and cur_extracted != cur_expected:
        return False
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
        cur_x, cur_e = _currency(extracted), _currency(expected)
        if "MIXED" in (cur_x, cur_e):
            note += f"; conflicting currency markers: extracted={cur_x}, expected={cur_e}"
        elif cur_x and cur_e and cur_x != cur_e:
            note += f"; currency differs: extracted={cur_x}, expected={cur_e}"
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
    "Confidence ratings come from Tesseract's self-reported word confidence, "
    "not ground-truth accuracy.",
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
                         Explicitly different currencies ("₹500" vs "$500") never match,
                         nor does an amount with conflicting markers ("₹500 USD").
                         A currency on one side only is ignored ("500" == "₹500").
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

        pil_image, word_confs, word_heights = _select_ocr_pass(str(path))
        raw_text: str = pytesseract.image_to_string(pil_image, config=_OCR_CONFIG)
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

            confidence = _field_confidence(extracted_val, word_confs)

            payment_fields.append(PaymentField(
                field_name=fname,
                extracted_value=extracted_val,
                expected_value=expected_val,
                match=match_result,
                confidence=confidence,
            ))

        limitations = list(_STATIC_LIMITATIONS)
        mean_conf = _mean(word_confs.values()) if word_confs else None
        if mean_conf is not None:
            limitations.append(
                f"OCR mean word confidence: {mean_conf:.0f}/100 "
                "(Tesseract self-report, not ground-truth accuracy)."
            )
        else:
            limitations.append("OCR word confidence was unavailable for this image.")

        quality = _assess_quality(
            str(path), raw_text, word_confs, word_heights, extracted,
            {f.field_name: f.confidence for f in payment_fields},
        )

        return PaymentResult(
            status=ComponentStatus.ANALYSED,
            raw_text=raw_text[:4000],
            fields=payment_fields,
            mismatches=mismatches,
            limitations=limitations,
            quality=quality,
        )

    except Exception as exc:  # noqa: BLE001
        logger.exception("payment_ocr: unexpected error.")
        return payment_failed(f"OCR analysis error: {exc}")
