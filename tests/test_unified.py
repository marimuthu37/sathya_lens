"""
test_unified.py — Integration tests for unified.py run_analysis()
"""
import json
import pytest
from satyalens_analysis.unified import run_analysis
from satyalens_analysis.schema import ComponentStatus


class TestRunAnalysisNoInputs:
    def test_all_not_applicable_when_no_inputs(self):
        result = run_analysis(case_id="TEST-001")
        assert result["audio"]["status"] == "not_applicable"
        assert result["payment"]["status"] == "not_applicable"
        assert result["context"]["status"] == "not_applicable"

    def test_schema_version_is_1_0(self):
        result = run_analysis()
        assert result["schema_version"] == "1.0"

    def test_case_id_echoed(self):
        result = run_analysis(case_id="CASE-XYZ")
        assert result["case_id"] == "CASE-XYZ"

    def test_errors_list_is_empty(self):
        result = run_analysis()
        assert result["errors"] == []

    def test_result_is_json_serialisable(self):
        result = run_analysis(case_id="CASE-001")
        json_str = json.dumps(result)
        loaded = json.loads(json_str)
        assert loaded["schema_version"] == "1.0"

    def test_all_required_top_level_keys(self):
        result = run_analysis()
        required_keys = {"schema_version", "case_id", "audio", "payment", "context", "errors"}
        assert required_keys == set(result.keys())


class TestRunAnalysisWithContext:
    def test_context_analysed_when_text_provided(self):
        result = run_analysis(
            case_id="CASE-002",
            message_text="Act now or you will be arrested!",
        )
        assert result["context"]["status"] == "analysed"
        assert len(result["context"]["flags"]) > 0

    def test_context_not_applicable_when_no_text(self):
        result = run_analysis(case_id="CASE-003")
        assert result["context"]["status"] == "not_applicable"

    def test_audio_not_applicable_when_no_audio(self):
        result = run_analysis(message_text="Some text")
        assert result["audio"]["status"] == "not_applicable"


class TestRunAnalysisWithAudio:
    def test_audio_failed_for_nonexistent_file(self):
        result = run_analysis(audio_path="/nonexistent/audio.wav")
        assert result["audio"]["status"] in ("failed", "unavailable")

    def test_failed_audio_does_not_impact_context(self):
        result = run_analysis(
            audio_path="/nonexistent/audio.wav",
            message_text="Pay using a gift card immediately.",
        )
        # Context should still run independently
        assert result["context"]["status"] == "analysed"


class TestRunAnalysisWithPayment:
    def test_payment_failed_for_nonexistent_image(self):
        result = run_analysis(screenshot_path="/nonexistent/screenshot.png")
        assert result["payment"]["status"] in ("failed", "unavailable")

    def test_failed_payment_does_not_impact_context(self):
        result = run_analysis(
            screenshot_path="/nonexistent/screenshot.png",
            message_text="Install AnyDesk right now.",
        )
        assert result["context"]["status"] == "analysed"


class TestRunAnalysisFullScenario:
    def test_all_components_with_partial_valid_inputs(self):
        """Context is valid; audio and payment point to nonexistent files."""
        result = run_analysis(
            case_id="CASE-FULL",
            audio_path="/nonexistent/voice.wav",
            screenshot_path="/nonexistent/payment.png",
            message_text=(
                "Urgent! Send ₹50,000 now. Don't tell anyone. "
                "Share your OTP immediately or your account will be blocked."
            ),
        )
        assert result["schema_version"] == "1.0"
        assert result["context"]["status"] == "analysed"
        assert len(result["context"]["flags"]) >= 3
        # Failed components recorded in errors or status
        assert result["audio"]["status"] in ("failed", "unavailable")
        assert result["payment"]["status"] in ("failed", "unavailable")
