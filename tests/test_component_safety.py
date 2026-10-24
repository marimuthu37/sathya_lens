"""
test_component_safety.py — audit safeguards for the non-classifier components and the
Person 2 contract: audio never fabricates a detection, payment OCR is extracted text (never
proof of settlement) with explicit mismatch reporting, and run_analysis() keeps its
seven-key, JSON-serialisable contract without logging message text.
"""
import json
import logging

import pytest

from satyalens_analysis import audio_detector as ad
from satyalens_analysis import payment_ocr as po
from satyalens_analysis.schema import ComponentStatus
from satyalens_analysis.unified import run_analysis

STATUS_VALUES = {s.value for s in ComponentStatus}
TOP_KEYS = {"schema_version", "case_id", "audio", "payment", "context", "text_classifier", "errors"}


# ---------------------------------------------------------------------------
# Audio: no fabricated prediction on any path
# ---------------------------------------------------------------------------

class TestAudioNeverFabricates:
    @pytest.fixture()
    def wav(self, tmp_path):
        p = tmp_path / "a.wav"
        p.write_bytes(b"RIFF\x24\x00\x00\x00WAVEfmt ")
        return p

    def _assert_no_detection(self, res):
        d = res.to_dict()
        assert res.finding is None and res.reliability is None and res.raw_score is None
        assert not {"finding", "reliability", "raw_score"} & set(d)
        assert d["status"] in STATUS_VALUES and d["status"] != "analysed"

    @pytest.mark.parametrize("kind", ["empty", "missing", "directory", "file"])
    def test_every_path(self, kind, wav, tmp_path):
        arg = {"empty": "", "missing": str(tmp_path / "none.wav"), "directory": str(tmp_path), "file": str(wav)}[kind]
        res = ad.analyse_audio(arg)
        self._assert_no_detection(res)
        expected = {"empty": "not_applicable", "missing": "failed", "directory": "failed", "file": "unavailable"}
        assert res.status.value == expected[kind]

    def test_unavailable_even_if_dependencies_reported_present(self, wav, monkeypatch):
        monkeypatch.setattr(ad, "_check_audio_deps", lambda: (True, ""))
        res = ad.analyse_audio(str(wav))
        self._assert_no_detection(res)
        assert res.status.value == "unavailable"
        assert any("genuine" in s.lower() for s in res.limitations + [res.reason])


# ---------------------------------------------------------------------------
# Payment OCR: comparison policy and "extracted text, not settlement proof"
# ---------------------------------------------------------------------------

class TestPaymentComparison:
    @pytest.mark.parametrize("extracted, expected", [("₹500", "$500"), ("INR 500", "USD 500"),
                                                     ("500 EUR", "500 GBP"), ("Rs 1,250", "€1250")])
    def test_explicitly_different_currencies_never_match(self, extracted, expected):
        match, note = po._compare_field("amount", extracted, expected)
        assert match is False and "currency differs" in note

    @pytest.mark.parametrize("extracted, expected", [("Rs. 5,000.00", "5000"), ("₹500", "Rs 500"),
                                                     ("Rs. 5,000", "INR 5000.00"), ("$100.50", "USD 100.5")])
    def test_same_currency_matches(self, extracted, expected):
        match, note = po._compare_field("amount", extracted, expected)
        assert match is True and "currency" not in note.split(";", 1)[-1]

    @pytest.mark.parametrize("extracted, expected", [("500", "₹500"), ("$500", "500"), ("INR 500", "500")])
    def test_documented_one_sided_currency_is_ignored(self, extracted, expected):
        # Current, documented contract: a currency on ONE side only does not block a match.
        # Changing this is a product decision (see the audit report), not a silent fix.
        assert po._compare_field("amount", extracted, expected)[0] is True

    @pytest.mark.parametrize("extracted, expected", [("₹500 USD", "500"), ("₹500 USD", "$500"),
                                                     ("₹500 USD", "₹500"), ("Rs 500 $", "₹500 USD"),
                                                     ("500", "INR 500 USD")])
    def test_conflicting_currency_markers_never_match(self, extracted, expected):
        match, note = po._compare_field("amount", extracted, expected)
        assert match is False and "conflicting currency markers" in note

    @pytest.mark.parametrize("extracted", ["", "five hundred", "5O0", "500.0.0", "Rs.", "-"])
    def test_missing_malformed_or_ambiguous_amounts_never_match(self, extracted):
        assert po._compare_field("amount", extracted, "500")[0] is False

    @pytest.mark.parametrize("field, extracted, expected", [
        ("amount", "499", "500"), ("amount", "5O0", "500"),                 # wrong digits / OCR-malformed
        ("transaction_id", "UTR123456789", "UTR123456780"),               # reference mismatch
        ("payee_name", "Riya Demo", "Kabir Demo"),                         # recipient mismatch
        ("upi_id", "riya.demo@examplepay", "neel.demo@examplepay"),
    ])
    def test_mismatches_detected(self, field, extracted, expected):
        assert po._compare_field(field, extracted, expected)[0] is False


@pytest.mark.skipif(not po._DEPENDENCIES_OK, reason="OCR dependencies not installed")
class TestPaymentResultSemantics:
    @pytest.fixture()
    def image(self, tmp_path):
        from PIL import Image
        p = tmp_path / "receipt.png"
        Image.new("RGB", (40, 20), "white").save(p)
        return p

    def _run(self, monkeypatch, image, extracted, expected):
        import pytesseract
        monkeypatch.setattr(pytesseract, "image_to_string", lambda *a, **k: "synthetic OCR text")
        monkeypatch.setattr(po, "_extract_fields", lambda raw: dict(extracted))
        return po.analyse_payment(str(image), expected).to_dict()

    def test_mismatch_missing_and_unexpected_fields(self, monkeypatch, image):
        d = self._run(monkeypatch, image,
                      {"amount": "₹500", "transaction_id": "UTR123456789", "date": "24/10/2026"},
                      {"amount": "$500", "transaction_id": "UTR123456789", "payee_name": "Riya Demo"})
        assert d["status"] == "analysed"
        fields = {f["field_name"]: f for f in d["fields"]}
        assert fields["amount"]["match"] is False
        assert fields["transaction_id"]["match"] is True
        assert fields["payee_name"]["match"] is False and fields["payee_name"]["extracted_value"] is None
        assert fields["payee_name"]["confidence"] == "low"
        assert fields["date"]["match"] is None                   # extracted but nothing to compare against
        assert any(m.startswith("amount:") and "currency differs" in m for m in d["mismatches"])
        assert any(m.startswith("payee_name:") and "not found" in m for m in d["mismatches"])

    def test_full_match_is_still_not_verification(self, monkeypatch, image):
        d = self._run(monkeypatch, image, {"amount": "Rs 500", "transaction_id": "UTR123456789"},
                      {"amount": "500", "transaction_id": "123456789"})
        assert d["status"] == "analysed" and d["mismatches"] == []
        assert any("does NOT prove payment settlement" in s for s in d["limitations"])
        assert set(d) == {"status", "raw_text", "fields", "mismatches", "limitations"}   # no 'verified' key

    def test_ocr_runtime_error_is_failed_not_analysed(self, monkeypatch, image):
        import pytesseract

        def boom(*a, **k):
            raise RuntimeError("ocr engine crashed")

        monkeypatch.setattr(pytesseract, "image_to_string", boom)
        d = po.analyse_payment(str(image), {"amount": "500"}).to_dict()
        assert d["status"] == "failed" and not d.get("fields")


# ---------------------------------------------------------------------------
# Person 2 contract
# ---------------------------------------------------------------------------

class TestIntegrationContract:
    @pytest.mark.parametrize("kwargs", [
        {},
        {"message_text": "Your OTP is XXXXXX. Do not share it."},
        {"message_text": "   "},
        {"audio_path": "missing.wav", "screenshot_path": "missing.png", "message_text": "hello"},
    ])
    def test_seven_keys_json_and_status_values(self, kwargs):
        out = run_analysis(**kwargs)
        assert set(out) == TOP_KEYS
        again = json.loads(json.dumps(out))
        assert again == out
        for comp in ("audio", "payment", "context", "text_classifier"):
            assert out[comp]["status"] in STATUS_VALUES, comp
        tc_out = out["text_classifier"]
        assert ("predicted_class" in tc_out) == (tc_out["status"] == "analysed")
        assert isinstance(out["errors"], list)

    def test_message_text_is_never_logged(self, caplog):
        sentinel = "SENTINEL-7Q2K transfer to demo.payee@examplepay immediately"
        with caplog.at_level(logging.DEBUG, logger="satyalens_analysis"):
            run_analysis(message_text=sentinel)
        assert any(r.name.startswith("satyalens_analysis") for r in caplog.records)   # logging did happen
        assert "SENTINEL-7Q2K" not in caplog.text and "examplepay" not in caplog.text
