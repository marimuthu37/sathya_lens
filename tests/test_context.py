"""
test_context.py — Unit tests for context_analyser.py
"""
import pytest
from satyalens_analysis.context_analyser import analyse_context
from satyalens_analysis.schema import ComponentStatus


class TestContextAnalyser:

    # ------------------------------------------------------------------
    # Empty / None input
    # ------------------------------------------------------------------
    def test_empty_string_returns_not_applicable(self):
        result = analyse_context("")
        assert result.status == ComponentStatus.NOT_APPLICABLE

    def test_whitespace_only_returns_not_applicable(self):
        result = analyse_context("   \n\t  ")
        assert result.status == ComponentStatus.NOT_APPLICABLE

    def test_none_like_empty_string_is_safe(self):
        result = analyse_context("")
        assert result.status == ComponentStatus.NOT_APPLICABLE

    # ------------------------------------------------------------------
    # Clean text — no flags
    # ------------------------------------------------------------------
    def test_clean_text_no_flags(self):
        result = analyse_context(
            "Please transfer the rent payment to the usual account by end of month."
        )
        assert result.status == ComponentStatus.ANALYSED
        assert len(result.flags) == 0
        assert any("No scam-context" in f for f in result.findings)

    # ------------------------------------------------------------------
    # Individual rule triggers
    # ------------------------------------------------------------------
    def test_urgency_pressure_detected(self):
        result = analyse_context("You must act now or lose your account.")
        assert result.status == ComponentStatus.ANALYSED
        rule_ids = [f.rule_id for f in result.flags]
        assert "URGENCY_PRESSURE" in rule_ids

    def test_secrecy_demand_detected(self):
        result = analyse_context("Don't tell anyone about this transfer, keep it secret.")
        rule_ids = [f.rule_id for f in result.flags]
        assert "SECRECY_DEMAND" in rule_ids

    def test_credential_request_detected(self):
        result = analyse_context("Please share your OTP with me to complete the process.")
        rule_ids = [f.rule_id for f in result.flags]
        assert "CREDENTIAL_REQUEST" in rule_ids

    def test_gift_card_detected(self):
        result = analyse_context("Pay us using an Amazon gift card immediately.")
        rule_ids = [f.rule_id for f in result.flags]
        assert "GIFT_CARD_PAYMENT" in rule_ids

    def test_crypto_payment_detected(self):
        result = analyse_context("Send 0.5 bitcoin to my wallet address to settle the debt.")
        rule_ids = [f.rule_id for f in result.flags]
        assert "CRYPTO_PAYMENT" in rule_ids

    def test_new_payee_pressure_detected(self):
        result = analyse_context("Please pay to a new account, my bank account is different now.")
        rule_ids = [f.rule_id for f in result.flags]
        assert "NEW_PAYEE_PRESSURE" in rule_ids

    def test_impersonation_cue_detected(self):
        result = analyse_context("I am from the bank and need you to transfer funds.")
        rule_ids = [f.rule_id for f in result.flags]
        assert "IMPERSONATION_CUE" in rule_ids

    def test_refund_overpayment_detected(self):
        result = analyse_context("We accidentally overpaid you; please send back the excess amount.")
        rule_ids = [f.rule_id for f in result.flags]
        assert "REFUND_OVERPAYMENT_SCAM" in rule_ids

    def test_remote_access_request_detected(self):
        result = analyse_context("Please install AnyDesk so I can help you remotely.")
        rule_ids = [f.rule_id for f in result.flags]
        assert "REMOTE_ACCESS_REQUEST" in rule_ids

    def test_threat_or_coercion_detected(self):
        result = analyse_context("Pay now or we will arrest you tomorrow.")
        rule_ids = [f.rule_id for f in result.flags]
        assert "THREAT_OR_COERCION" in rule_ids

    # ------------------------------------------------------------------
    # Multiple rules in one text
    # ------------------------------------------------------------------
    def test_multiple_flags_in_one_message(self):
        text = (
            "Act now! I am from the bank. "
            "Don't tell anyone. "
            "Send the OTP and pay using a gift card."
        )
        result = analyse_context(text)
        assert result.status == ComponentStatus.ANALYSED
        assert len(result.flags) >= 4

    def test_no_duplicate_rule_ids(self):
        text = "Act now, act now, act immediately, right now, within 5 minutes!"
        result = analyse_context(text)
        rule_ids = [f.rule_id for f in result.flags]
        assert len(rule_ids) == len(set(rule_ids)), "Duplicate rule IDs found"

    # ------------------------------------------------------------------
    # Severity
    # ------------------------------------------------------------------
    def test_high_severity_flags_present_for_scam_text(self):
        result = analyse_context(
            "Share your OTP immediately or your account will be blocked."
        )
        severities = {f.severity for f in result.flags}
        assert "high" in severities

    # ------------------------------------------------------------------
    # Limitations always populated
    # ------------------------------------------------------------------
    def test_limitations_always_present_on_success(self):
        result = analyse_context("Normal message here.")
        assert len(result.limitations) > 0

    # ------------------------------------------------------------------
    # Output is JSON-serialisable
    # ------------------------------------------------------------------
    def test_output_json_serialisable(self):
        import json
        result = analyse_context("Act now or we will arrest you!")
        json_str = json.dumps(result.to_dict())
        loaded = json.loads(json_str)
        assert "status" in loaded
        assert "flags" in loaded

    # ------------------------------------------------------------------
    # Explanation contains matched text
    # ------------------------------------------------------------------
    def test_flag_explanation_is_non_empty(self):
        result = analyse_context("Act now before your account is blocked!")
        for flag in result.flags:
            assert len(flag.explanation) > 0

    # ------------------------------------------------------------------
    # Findings narrative
    # ------------------------------------------------------------------
    def test_findings_narrative_present(self):
        result = analyse_context("Install AnyDesk please and share your OTP.")
        assert len(result.findings) > 0

    def test_final_verdict_disclaimer_in_findings(self):
        result = analyse_context("Install AnyDesk please and share your OTP.")
        combined = " ".join(result.findings)
        assert "final" in combined.lower() or "verdict" in combined.lower() or "determination" in combined.lower()
