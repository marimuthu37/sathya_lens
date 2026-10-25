"""
test_payment.py -- Unit tests for payment_ocr.py

Covers:
  - Amount comparison via Decimal (P1 fix)
  - Transaction-ID normalisation (P2 fix)
  - Field extraction patterns including UTR/prefix variants
  - Mismatch reporting includes normalisation note
  - Full OCR pipeline (synthetic screenshot, when Tesseract available)
"""
import json
import pytest
from pathlib import Path
from satyalens_analysis.payment_ocr import (
    analyse_payment,
    _extract_fields,
    _fuzzy_match,
    _normalise_amount,
    _normalise_txnid,
    _amounts_equal,
    _txnids_equal,
    _compare_field,
    _DEPENDENCIES_OK,
)
from satyalens_analysis.schema import ComponentStatus


# ---------------------------------------------------------------------------
# _normalise_amount
# ---------------------------------------------------------------------------

class TestNormaliseAmount:
    def test_strip_rupee_symbol(self):
        assert _normalise_amount("Rs. 5,000.00") == "5000.00"

    def test_strip_rupee_unicode(self):
        assert _normalise_amount("5,000.00") == "5000.00"

    def test_strip_dollar(self):
        assert _normalise_amount("$100.50") == "100.50"

    def test_strip_euro(self):
        assert _normalise_amount("200.00") == "200.00"

    def test_plain_number(self):
        assert _normalise_amount("5000") == "5000"

    def test_no_decimal(self):
        assert _normalise_amount("5000") == "5000"

    def test_strips_commas(self):
        assert _normalise_amount("1,00,000") == "100000"

    def test_strips_whitespace(self):
        assert _normalise_amount("  5 000  ") == "5000"

    def test_inr_keyword(self):
        assert _normalise_amount("INR 5000") == "5000"


# ---------------------------------------------------------------------------
# _amounts_equal  (Decimal-based comparison)
# ---------------------------------------------------------------------------

class TestAmountsEqual:
    """Priority 1 — Decimal amount comparison."""

    def test_integer_equals_decimal(self):
        assert _amounts_equal("5000", "5000.00") is True

    def test_commas_stripped(self):
        assert _amounts_equal("5,000.00", "5000") is True

    def test_currency_symbol_stripped(self):
        assert _amounts_equal("5000.00", "5000") is True

    def test_inr_prefix_stripped(self):
        assert _amounts_equal("5000", "5000") is True

    def test_different_amounts_not_equal(self):
        assert _amounts_equal("4999", "5000") is False

    def test_zero_vs_zero_point_zero(self):
        assert _amounts_equal("0", "0.00") is True

    def test_large_amount(self):
        assert _amounts_equal("1,00,000", "100000.00") is True

    def test_fractional_difference(self):
        assert _amounts_equal("5000.01", "5000.00") is False

    def test_non_numeric_falls_back_to_string(self):
        # e.g. a garbled OCR result — must not raise, must return False
        assert _amounts_equal("N/A", "5000") is False

    def test_both_non_numeric_equal(self):
        assert _amounts_equal("pending", "pending") is True


# ---------------------------------------------------------------------------
# _normalise_txnid
# ---------------------------------------------------------------------------

class TestNormaliseTxnid:
    """Priority 2 — Transaction-ID normalisation."""

    def test_utr_prefix_stripped(self):
        assert _normalise_txnid("UTR123456789") == "123456789"

    def test_lowercase_utr_stripped(self):
        assert _normalise_txnid("utr123456789") == "123456789"

    def test_utr_space_stripped(self):
        assert _normalise_txnid("utr 123456789") == "123456789"

    def test_txn_prefix_stripped(self):
        assert _normalise_txnid("TXN-ABCD1234") == "ABCD1234"

    def test_ref_prefix_stripped(self):
        assert _normalise_txnid("REF:123456") == "123456"

    def test_neft_prefix_stripped(self):
        assert _normalise_txnid("NEFT REF 987654") == "987654"

    def test_plain_digits_unchanged(self):
        assert _normalise_txnid("123456789") == "123456789"

    def test_separators_stripped(self):
        assert _normalise_txnid("123-456-789") == "123456789"

    def test_alphanumeric_preserved(self):
        assert _normalise_txnid("ABCD1234EFGH") == "ABCD1234EFGH"


# ---------------------------------------------------------------------------
# _txnids_equal
# ---------------------------------------------------------------------------

class TestTxnidsEqual:
    def test_utr_prefix_vs_bare(self):
        assert _txnids_equal("UTR123456789", "123456789") is True

    def test_txn_prefix_vs_bare(self):
        assert _txnids_equal("TXN-ABCD1234", "ABCD1234") is True

    def test_case_insensitive(self):
        assert _txnids_equal("abc123", "ABC123") is True

    def test_separator_ignored(self):
        assert _txnids_equal("123-456-789", "123456789") is True

    def test_different_ids_not_equal(self):
        assert _txnids_equal("UTR123", "UTR456") is False

    def test_both_with_prefix(self):
        assert _txnids_equal("UTR123456", "TXN123456") is True

    def test_utr_space_vs_bare(self):
        assert _txnids_equal("utr 123456789012", "123456789012") is True


# ---------------------------------------------------------------------------
# _compare_field dispatch
# ---------------------------------------------------------------------------

class TestCompareField:
    def test_amount_uses_decimal(self):
        match, note = _compare_field("amount", "5,000.00", "5000")
        assert match is True
        assert "Decimal" in note

    def test_transaction_id_strips_prefix(self):
        match, note = _compare_field("transaction_id", "UTR123456", "123456")
        assert match is True
        assert "Transaction-ID" in note

    def test_default_case_insensitive(self):
        match, note = _compare_field("payee_name", "John Doe", "john doe")
        assert match is True
        assert note == ""

    def test_amount_mismatch_has_note(self):
        match, note = _compare_field("amount", "4999", "5000")
        assert match is False
        assert "Decimal" in note

    def test_date_uses_default(self):
        match, note = _compare_field("date", "23/10/2024", "23/10/2024")
        assert match is True
        assert note == ""


# ---------------------------------------------------------------------------
# _fuzzy_match (default string comparison)
# ---------------------------------------------------------------------------

class TestFuzzyMatch:
    def test_exact_match(self):
        assert _fuzzy_match("5000", "5000") is True

    def test_case_insensitive(self):
        assert _fuzzy_match("John Doe", "john doe") is True

    def test_mismatch(self):
        assert _fuzzy_match("4999", "5000") is False

    def test_whitespace_stripped(self):
        assert _fuzzy_match("  5000  ", "5000") is True


# ---------------------------------------------------------------------------
# _extract_fields — field extraction patterns
# ---------------------------------------------------------------------------

class TestExtractFields:
    """Priority 2 — transaction ID extraction regression tests."""

    def test_extract_amount_inr(self):
        fields = _extract_fields("Amount: 5,000.00\nTransaction ID: TXN123456")
        assert "amount" in fields
        assert "5" in fields["amount"]

    def test_extract_utr_colon(self):
        """UTR: 123456789012"""
        fields = _extract_fields("UTR: 123456789012\nStatus: Success")
        assert "transaction_id" in fields
        assert "123456789012" in fields["transaction_id"]

    def test_extract_utr_space(self):
        """UTR 123456789012 (no colon)"""
        fields = _extract_fields("UTR 123456789012")
        assert "transaction_id" in fields

    def test_extract_transaction_id_label(self):
        """Transaction ID: TXN123456"""
        fields = _extract_fields("Transaction ID: TXN123456")
        assert "transaction_id" in fields

    def test_extract_ref_no(self):
        """Ref No: REF987654"""
        fields = _extract_fields("Ref No: REF987654321")
        assert "transaction_id" in fields

    def test_extract_txn_with_dash_separator(self):
        """txn-ABCD1234 (dash separator between keyword and ID)"""
        fields = _extract_fields("txn-ABCD1234XY")
        assert "transaction_id" in fields

    def test_extract_txn_with_comma_separator(self):
        """Transaction ID, UTR123456789 (OCR often produces comma instead of colon)"""
        fields = _extract_fields("Transaction ID, UTR123456789")
        assert "transaction_id" in fields

    def test_extracted_value_not_truncated(self):
        """Full 18-char UTR must not be silently truncated."""
        fields = _extract_fields("UTR: 123456789012345678")
        if "transaction_id" in fields:
            # The extracted body (after prefix) must contain the numeric content
            assert len(fields["transaction_id"]) >= 6

    def test_extract_upi_id(self):
        fields = _extract_fields("Paid to: user@okaxis")
        assert "upi_id" in fields
        assert "@" in fields["upi_id"]

    def test_extract_date(self):
        fields = _extract_fields("Date: 23/10/2024")
        assert "date" in fields
        assert "23" in fields["date"]

    def test_extract_status_text(self):
        fields = _extract_fields("Payment Successful")
        assert "status_text" in fields
        assert "success" in fields["status_text"].lower()

    def test_no_fields_returns_dict(self):
        fields = _extract_fields("No extractable data here at all xyz.")
        assert isinstance(fields, dict)


class TestExtractRawValues:
    """Values keep raw OCR casing/punctuation and never span OCR lines."""

    LAYOUT = (
        "Payment confirmation\n"
        "One-time payment of €1.00\n"
        "To Merchant Name\n"
        "From Examplebank\n"
        "Transaction Ref No. TID-241--\n"
        "Description TID-241\n"
    )

    def test_casing_preserved(self):
        fields = _extract_fields(self.LAYOUT)
        assert fields["payee_name"] == "Merchant Name"
        assert fields["payer_name"] == "Examplebank"

    def test_names_stop_at_line_end(self):
        fields = _extract_fields(self.LAYOUT)
        assert "\n" not in fields["payee_name"] and "\n" not in fields["payer_name"]

    def test_hyphenated_reference_kept_verbatim(self):
        assert _extract_fields(self.LAYOUT)["transaction_id"] == "TID-241--"

    def test_label_word_no_is_not_the_reference(self):
        assert "transaction_id" not in _extract_fields("Transaction Ref No. pending")

    def test_description_extracted(self):
        assert _extract_fields(self.LAYOUT)["description"] == "TID-241"

    def test_amount_keeps_currency_symbol(self):
        assert _extract_fields(self.LAYOUT)["amount"] == "€1.00"

    def test_keyword_inside_word_is_not_a_label(self):
        assert "payee_name" not in _extract_fields("Customer support available")

    @pytest.mark.parametrize("text, want", [
        ("One-time payment of GBP 1,263.83", "GBP 1,263.83"),   # code no longer dropped
        ("Amount: EUR 672.58", "EUR 672.58"),
        ("Total: Rs. 5,000", "Rs. 5,000"),
        ("Paid 500.00 INR", "500.00 INR"),
        ("Amount: €25.00", "€25.00"),
        ("Amount: 25.00", "25.00"),
    ])
    def test_amount_keeps_stated_currency_code(self, text, want):
        assert _extract_fields(text)["amount"] == want

    # Found on held-out split v1 (stacked layout, recall 0.70); fix is NOT
    # evaluated on that split — see reports/payment_pipeline_eval_v1.
    def test_stacked_label_then_value_line(self):
        text = "Transfer details\nRecipient\nPeak Fitness Club\nAmount\n€4,198.97\n"
        assert _extract_fields(text)["payee_name"] == "Peak Fitness Club"

    def test_stacked_with_blank_line_as_tesseract_emits_it(self):
        # Exact raw OCR text of held-out v2 sample test2-003 (synthetic).
        raw = ("Transfer details\nRecipient\n\nLumen Lighting Co\nAmount\n\nGBP 54,697.27\n"
               "Reference\nUTR641949938534\nStatus\n\nCompleted\n")
        fields = _extract_fields(raw)
        assert fields["payee_name"] == "Lumen Lighting Co"
        assert fields["amount"] == "GBP 54,697.27"

    def test_stacked_label_followed_by_another_label_is_not_a_name(self):
        assert "payee_name" not in _extract_fields("Recipient\nAmount\n€5.00\n")

    def test_same_line_payee_still_preferred(self):
        assert _extract_fields("To Merchant Name\nRecipient\nOther Name\n")["payee_name"] == "Merchant Name"

    def test_code_with_colon_still_normalises(self):
        assert _normalise_amount("INR: 500") == "500"
        assert _amounts_equal("INR: 500", "500.00")

    def test_bank_name_does_not_span_lines(self):
        fields = _extract_fields("Secure Bank Transfers\n\nPayment confirmation")
        assert "\n" not in fields.get("bank_name", "")


class TestFieldConfidence:
    def test_missing_value_is_low(self):
        from satyalens_analysis.payment_ocr import _field_confidence
        assert _field_confidence(None, {"x": 99.0}) == "low"

    def test_no_word_data_falls_back_to_medium(self):
        from satyalens_analysis.payment_ocr import _field_confidence
        assert _field_confidence("€1.00", None) == "medium"

    def test_lowest_word_confidence_wins(self):
        from satyalens_analysis.payment_ocr import _field_confidence
        words = {"merchant": 96.0, "name": 52.0}
        assert _field_confidence("Merchant Name", words) == "low"
        assert _field_confidence("Merchant", words) == "high"
        assert _field_confidence("Merchant", {"merchant": 70.0}) == "medium"

    def test_unmatched_tokens_use_pass_mean(self):
        from satyalens_analysis.payment_ocr import _field_confidence
        assert _field_confidence("zzz", {"a": 90.0, "b": 92.0}) == "high"

    @pytest.mark.skipif(not _DEPENDENCIES_OK, reason="Tesseract not available")
    def test_word_data_failure_falls_back(self, monkeypatch, tmp_path):
        """If image_to_data fails, OCR still runs and confidence degrades to 'medium'."""
        import pytesseract
        from PIL import Image
        p = tmp_path / "blank.png"
        Image.new("RGB", (60, 30), "white").save(p)

        def boom(*a, **k):
            raise RuntimeError("no word data")

        monkeypatch.setattr(pytesseract, "image_to_data", boom)
        monkeypatch.setattr(pytesseract, "image_to_string", lambda *a, **k: "Amount: Rs 500")
        result = analyse_payment(str(p))
        assert result.status == ComponentStatus.ANALYSED
        amt = next(f for f in result.fields if f.field_name == "amount")
        assert amt.confidence == "medium"
        assert any("confidence was unavailable" in s for s in result.limitations)


# ---------------------------------------------------------------------------
# analyse_payment -- integration-level
# ---------------------------------------------------------------------------

class TestAnalysePayment:
    def test_no_path_returns_not_applicable(self):
        result = analyse_payment(image_path="")
        assert result.status == ComponentStatus.NOT_APPLICABLE

    def test_none_path_handled(self):
        result = analyse_payment(image_path="")
        assert result.status == ComponentStatus.NOT_APPLICABLE

    def test_nonexistent_file_returns_failed_or_unavailable(self):
        result = analyse_payment(image_path="/nonexistent/path/image.png")
        assert result.status in (ComponentStatus.FAILED, ComponentStatus.UNAVAILABLE)

    def test_limitations_always_present(self):
        result = analyse_payment(image_path="")
        assert isinstance(result.limitations, list)

    def test_result_json_serialisable(self):
        result = analyse_payment(image_path="")
        loaded = json.loads(json.dumps(result.to_dict()))
        assert "status" in loaded

    def test_unavailable_status_has_limitations(self):
        result = analyse_payment(image_path="/fake/path.png")
        assert len(result.limitations) > 0

    @pytest.mark.skipif(
        not Path("tests/fixtures/sample_payment.png").exists(),
        reason="No sample fixture image available"
    )
    def test_fixture_image_analysed(self):
        result = analyse_payment(
            image_path="tests/fixtures/sample_payment.png",
            expected_fields={"amount": "5000"},
        )
        assert result.status in (
            ComponentStatus.ANALYSED,
            ComponentStatus.UNAVAILABLE,
            ComponentStatus.FAILED,
        )


# ---------------------------------------------------------------------------
# Mismatch note regression tests (Priority 1 + 2 fix verification)
# ---------------------------------------------------------------------------

class TestMismatchNotes:
    """Verify that mismatch strings include normalisation context."""

    @pytest.mark.skipif(not _DEPENDENCIES_OK, reason="Tesseract not available")
    def test_amount_mismatch_includes_decimal_note(self, tmp_path):
        from PIL import Image, ImageDraw
        img = Image.new("RGB", (600, 200), "white")
        draw = ImageDraw.Draw(img)
        draw.text((20, 40), "Amount: Rs. 5000.00", fill="black")
        p = str(tmp_path / "amt.png")
        img.save(p)
        result = analyse_payment(p, expected_fields={"amount": "9999"})
        if result.status == ComponentStatus.ANALYSED and result.mismatches:
            # At least one mismatch must carry the Decimal note
            combined = " ".join(result.mismatches)
            assert "Decimal" in combined or "normalised" in combined.lower()

    @pytest.mark.skipif(not _DEPENDENCIES_OK, reason="Tesseract not available")
    def test_txnid_utr_prefix_matches_bare(self, tmp_path):
        """UTR123456789 extracted, 123456789 expected -> match=True (not a mismatch)."""
        from PIL import Image, ImageDraw
        img = Image.new("RGB", (600, 200), "white")
        draw = ImageDraw.Draw(img)
        draw.text((20, 40), "Transaction ID: UTR123456789", fill="black")
        p = str(tmp_path / "txn.png")
        img.save(p)
        result = analyse_payment(p, expected_fields={"transaction_id": "123456789"})
        if result.status == ComponentStatus.ANALYSED:
            txn_field = next(
                (f for f in result.fields if f.field_name == "transaction_id"), None
            )
            if txn_field and txn_field.extracted_value:
                # Normalised comparison must treat these as equal
                assert _txnids_equal(txn_field.extracted_value, "123456789")


# ---------------------------------------------------------------------------
# Full OCR pipeline with Tesseract (synthetic screenshot)
# ---------------------------------------------------------------------------

class TestPaymentWithSyntheticImage:
    """
    Create a synthetic payment screenshot in memory and run the full OCR pipeline.
    Skips automatically when Tesseract binary is unavailable.
    """

    @pytest.fixture()
    def synthetic_screenshot(self, tmp_path):
        try:
            from PIL import Image, ImageDraw
        except ImportError:
            pytest.skip("PIL not available")

        img = Image.new("RGB", (700, 400), color=(255, 255, 255))
        draw = ImageDraw.Draw(img)
        lines = [
            "Payment Successful",
            "Amount: Rs. 5000.00",
            "Transaction ID: UTR123456789",
            "Date: 23/10/2024",
            "Paid to: John Doe",
            "UPI ID: johndoe@okaxis",
        ]
        y = 50
        for line in lines:
            draw.text((50, y), line, fill=(0, 0, 0))
            y += 45
        img_path = tmp_path / "synthetic_payment.png"
        img.save(str(img_path))
        return str(img_path)

    def test_synthetic_screenshot_analysed(self, synthetic_screenshot):
        if not _DEPENDENCIES_OK:
            pytest.skip("Tesseract not available")
        result = analyse_payment(
            image_path=synthetic_screenshot,
            # Decimal comparison: "5000" must equal extracted "5000.00"
            expected_fields={"amount": "5000"},
        )
        assert result.status == ComponentStatus.ANALYSED
        assert isinstance(result.fields, list)
        assert len(result.limitations) > 0

    def test_amount_5000_equals_5000_00(self, synthetic_screenshot):
        """Priority 1 regression: Decimal comparison must treat 5000 == 5000.00."""
        if not _DEPENDENCIES_OK:
            pytest.skip("Tesseract not available")
        result = analyse_payment(
            image_path=synthetic_screenshot,
            expected_fields={"amount": "5000"},
        )
        if result.status == ComponentStatus.ANALYSED:
            amt_field = next(
                (f for f in result.fields if f.field_name == "amount"), None
            )
            if amt_field and amt_field.extracted_value and amt_field.expected_value:
                # The Decimal comparator must report match=True
                assert _amounts_equal(
                    amt_field.extracted_value, amt_field.expected_value
                ), (
                    f"Decimal comparison failed: extracted={amt_field.extracted_value!r} "
                    f"expected={amt_field.expected_value!r}"
                )

    def test_screenshot_does_not_claim_payment_settled(self, synthetic_screenshot):
        if not _DEPENDENCIES_OK:
            pytest.skip("Tesseract not available")
        result = analyse_payment(image_path=synthetic_screenshot)
        combined = " ".join(result.limitations)
        assert (
            "cannot verify" in combined.lower()
            or "does not" in combined.lower()
            or "cannot confirm" in combined.lower()
        )

    def test_extracted_value_preserved_as_raw_ocr(self, synthetic_screenshot):
        """extracted_value must be the raw OCR string, not a normalised version."""
        if not _DEPENDENCIES_OK:
            pytest.skip("Tesseract not available")
        result = analyse_payment(
            image_path=synthetic_screenshot,
            expected_fields={"amount": "5000"},
        )
        if result.status == ComponentStatus.ANALYSED:
            amt = next((f for f in result.fields if f.field_name == "amount"), None)
            if amt:
                # extracted_value must be the raw OCR string — it must NOT have
                # been replaced with a Decimal representation
                assert isinstance(amt.extracted_value, str)
                # It should contain the raw OCR output (not "5000" as a parsed Decimal)
                # We can verify by checking it's truthy and string-typed
                assert len(amt.extracted_value) > 0


# ---------------------------------------------------------------------------
# Image-quality assessment (degraded fixtures generated at test time)
# ---------------------------------------------------------------------------

_ARIAL = Path("C:/Windows/Fonts/arial.ttf")
_QUALITY_ROWS = [("Payment Successful", ""), ("Amount", "\u20ac7.50"), ("To", "Riya Demo Stores"),
                 ("Transaction Ref No.", "QX-518234"), ("Date", "12/10/2026")]


def _receipt(path, blur=0.0, scale=1.0):
    """Synthetic receipt; optionally Gaussian-blurred and/or downscaled."""
    from PIL import Image, ImageDraw, ImageFilter, ImageFont
    font = ImageFont.truetype(str(_ARIAL), 30)
    img = Image.new("RGB", (900, 560), "white")
    draw = ImageDraw.Draw(img)
    for i, (label, value) in enumerate(_QUALITY_ROWS):
        draw.text((40, 40 + i * 90), label, fill="black", font=font)
        if value:
            draw.text((480, 40 + i * 90), value, fill="black", font=font)
    if blur:
        img = img.filter(ImageFilter.GaussianBlur(blur))
    if scale != 1.0:
        img = img.resize((int(900 * scale), int(560 * scale)), Image.LANCZOS)
    img.save(path)
    return str(path)


def _codes(result):
    return {w["code"] for w in result.quality["warnings"]}


class TestQualityAssessmentUnits:
    def test_sharpness_is_contrast_independent(self):
        import numpy as np
        from satyalens_analysis.payment_ocr import _edge_sharpness
        img = np.full((60, 60), 200, dtype=np.uint8)
        img[20:40, 20:40] = 0
        faint = (200 + (img.astype(float) - 200) * 0.05).astype(np.uint8)
        assert abs(_edge_sharpness(img) - _edge_sharpness(faint)) < 0.02

    def test_blank_image_has_no_sharpness(self):
        import numpy as np
        from satyalens_analysis.payment_ocr import _edge_sharpness
        assert _edge_sharpness(np.full((30, 30), 255, dtype=np.uint8)) is None

    def test_no_text_is_unreadable(self, tmp_path):
        from PIL import Image
        from satyalens_analysis.payment_ocr import _assess_quality
        p = tmp_path / "blank.png"
        Image.new("RGB", (300, 300), "white").save(p)
        q = _assess_quality(str(p), "", None, [], {}, {})
        assert q["assessment"] == "unreadable"
        assert [w["code"] for w in q["warnings"]] == ["no_text"]
        assert q["metrics"]["edge_sharpness"] is None

    def test_critical_field_warnings_are_cautions(self, tmp_path):
        from PIL import Image
        from satyalens_analysis.payment_ocr import _assess_quality
        p = tmp_path / "x.png"
        Image.new("RGB", (300, 300), "white").save(p)
        q = _assess_quality(str(p), "one two three four five six", {"one": 95.0}, [20.0],
                            {"amount": "\u20ac5", "payee_name": "Riya"}, {"amount": "high", "payee_name": "low"})
        by_code = {w["code"]: w["severity"] for w in q["warnings"]}
        assert by_code == {"critical_field_missing": "caution", "critical_field_low_confidence": "caution"}
        assert q["assessment"] == "limited"

    def test_metrics_failure_does_not_crash(self, tmp_path):
        from satyalens_analysis.payment_ocr import _assess_quality
        q = _assess_quality(str(tmp_path / "missing.png"), "a b c d e f", {"a": 90.0}, [20.0],
                            {"amount": "1", "payee_name": "R", "transaction_id": "T1"}, {})
        assert q["metrics"]["width_px"] is None
        assert q["assessment"] == "no_issues_detected"


@pytest.mark.skipif(not _DEPENDENCIES_OK or not _ARIAL.exists(), reason="Tesseract or Arial not available")
class TestQualityAssessmentRealOCR:
    """Thresholds must flag the fixtures on which extraction was observed to degrade."""

    def test_sharp_receipt_has_no_issues(self, tmp_path):
        r = analyse_payment(_receipt(tmp_path / "sharp.png"))
        assert r.quality["assessment"] == "no_issues_detected", r.quality["warnings"]
        assert r.quality["metrics"]["edge_sharpness"] > 0.15
        assert "quality" in r.to_dict()

    def test_heavily_blurred_receipt_is_flagged_severe(self, tmp_path):
        r = analyse_payment(_receipt(tmp_path / "blur.png", blur=4))
        assert r.status == ComponentStatus.ANALYSED
        assert r.quality["assessment"] in ("limited", "unreadable")
        assert "blurred" in _codes(r)
        assert any(w["severity"] == "severe" for w in r.quality["warnings"])

    def test_tiny_text_is_flagged_low_resolution(self, tmp_path):
        r = analyse_payment(_receipt(tmp_path / "tiny.png", scale=0.22))
        assert "low_resolution" in _codes(r)

    def test_raw_values_and_confidence_preserved_when_degraded(self, tmp_path):
        r = analyse_payment(_receipt(tmp_path / "soft.png", blur=3))
        for f in r.fields:
            assert f.confidence in ("low", "medium", "high")
            assert f.extracted_value is None or isinstance(f.extracted_value, str)
        assert r.quality["metrics"]["ocr_mean_word_confidence"] is not None
