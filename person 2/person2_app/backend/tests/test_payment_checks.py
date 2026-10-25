"""
tests/test_payment_checks.py
────────────────────────────
Payment screenshot observations (app/services/payment_checks.py) and their
effect through the adapter, route and decision engine.

Coverage:
  1. Matching payment fields → no finding, "does NOT prove settlement" note
  2. Inconsistent transaction reference and description → one ocr_mismatch
  3. Generic / missing recipient → finding / note only
  4. Missing expected comparison values → note, no invented comparison
  5. OCR failure / empty extraction → FAILED / no findings, explicit note
  6. Low-confidence OCR → note + confidence carried into the finding
  7. Unavailable payment analysis → stub (status=unavailable)
  8. Zero findings never become LOW_CONCERN
  9. API response schemas unchanged
  + Real OCR on a synthetic screenshot (skipped without Tesseract / a TTF font)

All fixtures are synthetic; no real financial data is used.
"""

from __future__ import annotations

import copy
import io
from pathlib import Path

import pytest

from app.services.analysis import AnalysisModuleError, findings_from_json, findings_to_json
from app.services.analysis_person1 import Person1Adapter
from app.services.payment_checks import check_payment

from tests.test_analysis_person1 import _BASE_RESULT, _setup_case


# ── Synthetic payment objects ─────────────────────────────────────────────────

def _field(name, value, conf="high", expected=None, match=None):
    return {"field_name": name, "extracted_value": value, "expected_value": expected,
            "match": match, "confidence": conf}


def _payment(fields, raw_text="Payment Successful\nsynthetic receipt", mismatches=(), status="analysed"):
    return {"status": status, "raw_text": raw_text, "fields": list(fields),
            "mismatches": list(mismatches), "limitations": ["OCR may produce errors."]}


_CLEAN = [
    _field("amount", "₹500.00"), _field("payee_name", "Riya Demo Stores"),
    _field("transaction_id", "UTR123456789012"), _field("status_text", "Payment Successful"),
]

# Mirrors the structure of the reported issue with synthetic values.
_PLACEHOLDER_AND_REF = [
    _field("amount", "€1.00"), _field("payee_name", "Merchant Name"),
    _field("payer_name", "Examplebank"), _field("transaction_id", "TID-241--", conf="low"),
    _field("description", "TID-241"),
]
_PROMPT_TEXT = "Payment confirmation\nPlease confirm the following payment details\nAccept\nDecline"


def _types(findings):
    return [f.finding_type for f in findings]


# ── 1. Matching fields ────────────────────────────────────────────────────────

class TestMatchingFields:

    def test_all_matched_has_no_findings_and_no_settlement_claim(self):
        fields = [_field("amount", "₹500.00", expected="500", match=True),
                  _field("transaction_id", "UTR123456789012", expected="123456789012", match=True),
                  _field("payee_name", "Riya Demo Stores"), _field("status_text", "Payment Successful")]
        findings, notes = check_payment(_payment(fields))
        assert findings == []
        joined = "\n".join(notes)
        assert "All compared fields matched" in joined
        assert "does NOT prove payment settlement" in joined

    def test_clean_receipt_without_expected_values_has_no_findings(self):
        findings, _ = check_payment(_payment(_CLEAN))
        assert findings == []

    def test_unrelated_reference_and_description_are_normal(self):
        fields = _CLEAN + [_field("description", "INV-2026-0042")]
        findings, _ = check_payment(_payment(fields))
        assert findings == []

    def test_free_text_description_is_not_compared(self):
        fields = _CLEAN + [_field("description", "Rent for October")]
        assert check_payment(_payment(fields))[0] == []


# ── 2. Reference / description inconsistency ─────────────────────────────────

class TestReferenceConsistency:

    def test_near_duplicate_disagreement_is_one_finding(self):
        findings, _ = check_payment(_payment(_PLACEHOLDER_AND_REF, raw_text=_PROMPT_TEXT))
        ref = [f for f in findings if "reference" in f.label.lower()]
        assert len(ref) == 1, "malformation and disagreement must not double-count"
        f = ref[0]
        assert f.finding_type == "ocr_mismatch" and f.is_interpretation is True
        assert "'TID-241--'" in f.detail and "'TID-241'" in f.detail
        assert "ends with a separator" in f.detail
        assert "fraud" not in f.detail.lower()
        assert "not proof of fraud" in f.reliability_note

    def test_truncated_reference_is_flagged(self):
        fields = _CLEAN[:2] + [_field("transaction_id", "TID-2419"), _field("description", "TID-241")]
        findings, _ = check_payment(_payment(fields))
        assert any("truncated" in f.detail for f in findings)

    def test_malformed_reference_alone(self):
        fields = _CLEAN[:2] + [_field("transaction_id", "UTR--123456")]
        findings, _ = check_payment(_payment(fields))
        assert [f.label for f in findings] == ["Transaction reference appears malformed"]
        assert "repeated separators" in findings[0].detail

    def test_raw_values_are_not_normalised_away(self):
        findings, notes = check_payment(_payment(_PLACEHOLDER_AND_REF))
        assert any("Extracted transaction_id: 'TID-241--'" in n for n in notes)


# ── 3. Recipient identity ─────────────────────────────────────────────────────

class TestRecipient:

    @pytest.mark.parametrize("name", ["Merchant Name", "payee", "RECIPIENT NAME", "XXXX", "N/A", "Test"])
    def test_placeholder_recipient_is_finding(self, name):
        fields = [_field("amount", "₹500"), _field("payee_name", name)]
        findings, _ = check_payment(_payment(fields))
        assert _types(findings) == ["ocr_mismatch"]
        assert findings[0].label == "Recipient name looks like a template placeholder"

    @pytest.mark.parametrize("name", ["Riya Demo Stores", "Name Brand Outlet", "Merchant Services Ltd"])
    def test_real_looking_names_are_not_flagged(self, name):
        fields = [_field("amount", "₹500"), _field("payee_name", name)]
        assert check_payment(_payment(fields))[0] == []

    def test_missing_recipient_is_note_not_finding(self):
        findings, notes = check_payment(_payment([_field("amount", "₹500")]))
        assert findings == []
        assert any("No recipient name could be read" in n for n in notes)


# ── 4. Expected values ────────────────────────────────────────────────────────

class TestExpectedValues:

    def test_no_expected_values_means_no_comparison(self):
        findings, notes = check_payment(_payment(_CLEAN))
        assert findings == []
        assert any("no expected values were supplied" in n for n in notes)

    def test_supplied_mismatch_is_direct_payment_mismatch(self):
        fields = [_field("amount", "€1.00", expected="€10.00", match=False)]
        p = _payment(fields, mismatches=["amount: expected='€10.00', extracted='€1.00'"])
        findings, _ = check_payment(p)
        assert _types(findings) == ["payment_mismatch"]
        assert findings[0].is_interpretation is False

    def test_conflicting_currency_markers(self):
        findings, _ = check_payment(_payment([_field("amount", "€500 USD"), _field("payee_name", "Riya Demo")]))
        assert [f.label for f in findings] == ["Amount shows conflicting currency markers"]


# ── 5. OCR failure / empty extraction ────────────────────────────────────────

class TestEmptyOrFailed:

    def test_empty_ocr_text_has_no_findings_and_says_so(self):
        findings, notes = check_payment(_payment([], raw_text="   \n"))
        assert findings == []
        assert "OCR produced no text" in notes[0]
        assert "does NOT mean the payment is genuine" in notes[0]

    def test_text_without_fields(self):
        findings, notes = check_payment(_payment([], raw_text="hello world"))
        assert findings == []
        assert any("recognised no payment fields" in n for n in notes)

    def test_payment_component_failed_raises(self, tmp_path):
        failed = {"status": "failed", "raw_text": "", "fields": [], "mismatches": [],
                  "limitations": ["OCR analysis error: engine crashed"]}
        adapter = Person1Adapter(lambda **kw: dict(copy.deepcopy(_BASE_RESULT), payment=failed), "t")
        (tmp_path / "a.png").write_bytes(b"x")
        with pytest.raises(AnalysisModuleError):
            adapter.analyse(tmp_path / "a.png", "image/png", "e")


# ── 6. Low-confidence OCR ─────────────────────────────────────────────────────

class TestLowConfidence:

    def test_low_confidence_values_are_listed(self):
        _, notes = check_payment(_payment(_PLACEHOLDER_AND_REF))
        low = [n for n in notes if n.startswith("[payment] Low OCR confidence")]
        assert len(low) == 1 and "transaction_id ('TID-241--')" in low[0]

    def test_finding_carries_confidence(self):
        findings, _ = check_payment(_payment(_PLACEHOLDER_AND_REF))
        ref = next(f for f in findings if "reference" in f.label.lower())
        assert "reference low" in ref.reliability_note


# ── Approval prompt wording ───────────────────────────────────────────────────

class TestApprovalPrompt:

    def test_prompt_without_status_is_noted_not_flagged(self):
        fields = [_field("amount", "€1.00"), _field("payee_name", "Riya Demo")]
        findings, notes = check_payment(_payment(fields, raw_text=_PROMPT_TEXT))
        assert findings == []
        assert any("awaiting approval" in n for n in notes)

    def test_status_text_suppresses_prompt_note(self):
        _, notes = check_payment(_payment(_CLEAN, raw_text="Payment Successful\nAccept"))
        assert not any("awaiting approval" in n for n in notes)


# ── Findings survive Person 2's validation ───────────────────────────────────

def test_findings_pass_person2_validator():
    findings, _ = check_payment(_payment(_PLACEHOLDER_AND_REF))
    assert len(findings_from_json(findings_to_json(findings))) == len(findings) == 2


# ── 7-9. Route, decision and schema ──────────────────────────────────────────

def _image_adapter(payment):
    return Person1Adapter(lambda **kw: dict(copy.deepcopy(_BASE_RESULT), payment=copy.deepcopy(payment)),
                          "1.0.0+schema1.0")


JPEG = b"\xff\xd8\xff" + b"\x00" * 64
# "payment" is an additive key (2026-10-25); every original key is still present.
SCHEMA_ANALYSIS = {"id", "evidence_id", "status", "requested_at", "completed_at", "module_name",
                   "module_version", "findings", "limitations", "error_message", "is_stub",
                   "module_available", "payment"}


class TestRouteAndDecision:

    def test_observations_give_verify_first_not_high_risk(self, client, tmp_path, monkeypatch):
        adapter = _image_adapter(_payment(_PLACEHOLDER_AND_REF, raw_text=_PROMPT_TEXT))
        cid, eid = _setup_case(client, tmp_path, monkeypatch, adapter, "shot.jpg", JPEG, "image/jpeg")
        r = client.post(f"/api/cases/{cid}/evidence/{eid}/analyse")
        assert r.status_code == 202 and r.json()["status"] == "completed"
        res = client.get(f"/api/cases/{cid}/evidence/{eid}/analysis").json()
        assert set(res) == SCHEMA_ANALYSIS
        assert sorted(f["finding_type"] for f in res["findings"]) == ["ocr_mismatch", "ocr_mismatch"]
        d = client.post(f"/api/cases/{cid}/decision").json()
        assert d["category"] == "verify_first"
        assert d["conflicts"] == []

    def test_zero_findings_is_insufficient_not_low_concern(self, client, tmp_path, monkeypatch):
        adapter = _image_adapter(_payment(_CLEAN))
        cid, eid = _setup_case(client, tmp_path, monkeypatch, adapter, "shot.jpg", JPEG, "image/jpeg")
        assert client.post(f"/api/cases/{cid}/evidence/{eid}/analyse").json()["status"] == "completed"
        res = client.get(f"/api/cases/{cid}/evidence/{eid}/analysis").json()
        assert res["findings"] == [] and res["is_stub"] is False
        d = client.post(f"/api/cases/{cid}/decision").json()
        assert d["category"] == "insufficient_evidence"
        assert "Analysis ran but returned no specific findings" in d["short_explanation"]
        assert "not evidence" in d["short_explanation"]

    def test_unavailable_payment_is_stub(self, client, tmp_path, monkeypatch):
        unavailable = {"status": "unavailable", "raw_text": "", "fields": [], "mismatches": [],
                       "limitations": ["Tesseract OCR binary not found."]}
        adapter = _image_adapter(unavailable)
        cid, eid = _setup_case(client, tmp_path, monkeypatch, adapter, "shot.jpg", JPEG, "image/jpeg")
        assert client.post(f"/api/cases/{cid}/evidence/{eid}/analyse").json()["status"] == "unavailable"
        res = client.get(f"/api/cases/{cid}/evidence/{eid}/analysis").json()
        assert res["is_stub"] is True and res["findings"] == []
        assert client.post(f"/api/cases/{cid}/decision").json()["category"] == "insufficient_evidence"


# ── Real OCR on a synthetic screenshot ───────────────────────────────────────

_FONT = Path("C:/Windows/Fonts/arial.ttf")


def _draw_screenshot(path: Path) -> None:
    from PIL import Image, ImageDraw, ImageFont
    font = ImageFont.truetype(str(_FONT), 30)
    img = Image.new("RGB", (900, 620), "white")
    draw = ImageDraw.Draw(img)
    rows = [("Payment confirmation", ""), ("Please confirm the following payment details", ""),
            ("One-time payment of", "€7.50"), ("To", "Merchant Name"), ("From", "Examplebank"),
            ("Transaction Ref No.", "QX-518--"), ("Description", "QX-518")]
    for i, (label, value) in enumerate(rows):
        draw.text((40, 30 + i * 80), label, fill="black", font=font)
        if value:
            draw.text((560, 30 + i * 80), value, fill="black", font=font)
    img.save(path)


def test_real_ocr_synthetic_screenshot(tmp_path):
    pytest.importorskip("satyalens_analysis")
    import satyalens_analysis.payment_ocr as po
    if not po._DEPENDENCIES_OK or not _FONT.exists():
        pytest.skip("Tesseract or Arial font not available")
    from app.services.analysis_person1 import get_adapter

    shot = tmp_path / "synthetic.png"
    _draw_screenshot(shot)
    res = get_adapter().analyse(shot, "image/png", "synthetic")
    assert res.is_stub is False
    labels = sorted(f.label for f in res.findings)
    assert labels == ["Recipient name looks like a template placeholder",
                      "Transaction reference and description disagree"]
    assert "Extracted amount: '€7.50'" in res.limitations
    assert "Extracted transaction_id: 'QX-518--'" in res.limitations
