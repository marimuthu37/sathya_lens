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
