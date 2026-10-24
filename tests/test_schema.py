"""
test_schema.py — Unit tests for schema.py
"""
import pytest
from satyalens_analysis.schema import (
    AnalysisResult,
    AudioFinding,
    AudioReliability,
    AudioResult,
    ComponentStatus,
    ContextFlag,
    ContextResult,
    PaymentField,
    PaymentResult,
    audio_failed,
    audio_unavailable,
    build_empty_result,
    context_failed,
    payment_failed,
)


class TestComponentStatus:
    def test_all_values_present(self):
        expected = {"analysed", "inconclusive", "failed", "not_applicable", "unavailable"}
        assert {s.value for s in ComponentStatus} == expected


class TestAudioResult:
    def test_not_applicable_dict(self):
        r = AudioResult(status=ComponentStatus.NOT_APPLICABLE, reason="No input.")
        d = r.to_dict()
        assert d["status"] == "not_applicable"
        assert "finding" not in d
        assert "reliability" not in d
        assert d["reason"] == "No input."

    def test_analysed_dict_includes_finding(self):
        r = AudioResult(
            status=ComponentStatus.ANALYSED,
            finding=AudioFinding.SPOOF_LIKELY,
            reliability=AudioReliability.LIMITED,
            reason="Score above threshold.",
            raw_score=0.82,
        )
        d = r.to_dict()
        assert d["status"] == "analysed"
        assert d["finding"] == "spoof_likely"
        assert d["reliability"] == "limited"
        assert d["raw_score"] == pytest.approx(0.82, abs=1e-5)

    def test_raw_score_rounded(self):
        r = AudioResult(status=ComponentStatus.ANALYSED, raw_score=0.123456789)
        d = r.to_dict()
        assert d["raw_score"] == 0.123457

    def test_audio_failed_helper(self):
        r = audio_failed("boom")
        assert r.status == ComponentStatus.FAILED
        # Limitation must clearly state failure ≠ genuine
        combined = " ".join(r.limitations).upper()
        assert "NOT" in combined or "NEVER" in combined

    def test_audio_unavailable_helper(self):
        r = audio_unavailable("missing torch", ["limitation 1"])
        assert r.status == ComponentStatus.UNAVAILABLE
        assert r.limitations == ["limitation 1"]


class TestPaymentResult:
    def test_empty_payment_result(self):
        r = PaymentResult(status=ComponentStatus.NOT_APPLICABLE)
        d = r.to_dict()
        assert d["status"] == "not_applicable"
        assert d["fields"] == []
        assert d["mismatches"] == []

    def test_payment_field_match_true(self):
        f = PaymentField(
            field_name="amount",
            extracted_value="5000",
            expected_value="5000",
            match=True,
        )
        d = f.to_dict()
        assert d["match"] is True

    def test_payment_field_match_false(self):
        f = PaymentField(
            field_name="amount",
            extracted_value="4000",
            expected_value="5000",
            match=False,
        )
        d = f.to_dict()
        assert d["match"] is False

    def test_payment_field_no_expected(self):
        f = PaymentField(
            field_name="date",
            extracted_value="01/01/2024",
            expected_value=None,
            match=None,
        )
        d = f.to_dict()
        assert d["match"] is None
        assert d["expected_value"] is None

    def test_payment_failed_helper(self):
        r = payment_failed("OCR crash")
        assert r.status == ComponentStatus.FAILED
        assert any("NOT confirm" in l for l in r.limitations)


class TestContextResult:
    def test_empty_context(self):
        r = ContextResult(status=ComponentStatus.NOT_APPLICABLE)
        d = r.to_dict()
        assert d["flags"] == []
        assert d["findings"] == []

    def test_context_flag_dict(self):
        f = ContextFlag(
            rule_id="URGENCY_PRESSURE",
            label="Urgency",
            severity="high",
            explanation="phrase causes urgency",
        )
        d = f.to_dict()
        assert d["rule_id"] == "URGENCY_PRESSURE"
        assert d["severity"] == "high"

    def test_context_failed_helper(self):
        r = context_failed("text parsing error")
        assert r.status == ComponentStatus.FAILED


class TestAnalysisResult:
    def test_build_empty(self):
        r = build_empty_result("CASE-999")
        assert r.case_id == "CASE-999"
        assert r.schema_version == "1.0"
        assert r.audio.status == ComponentStatus.NOT_APPLICABLE
        assert r.payment.status == ComponentStatus.NOT_APPLICABLE
        assert r.context.status == ComponentStatus.NOT_APPLICABLE
        assert r.errors == []

    def test_to_dict_schema_version(self):
        r = build_empty_result("X")
        d = r.to_dict()
        assert d["schema_version"] == "1.0"
        assert "audio" in d
        assert "payment" in d
        assert "context" in d
        assert "errors" in d

    def test_to_dict_is_json_serialisable(self):
        import json
        r = build_empty_result("CASE-001")
        json_str = json.dumps(r.to_dict())
        loaded = json.loads(json_str)
        assert loaded["case_id"] == "CASE-001"
