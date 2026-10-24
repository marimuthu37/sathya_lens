"""
test_audio.py — Unit tests for audio_detector.py

Reflects verified state (2026-10-23):
  - Audio model integration is incomplete (AASIST requires custom loader).
  - All audio analysis returns status='unavailable' or 'not_applicable' or 'failed'.
  - Tests verify the contract is upheld and results are never falsely reassuring.
  - The 5 inference tests (TestAnalyseAudioWithDeps) remain and skip automatically
    until deps + a working model loader are both present.
"""
import json
import pytest
from satyalens_analysis.audio_detector import (
    analyse_audio,
    _check_audio_deps,
    _AUDIO_DEPS_OK,
    _VERIFIED_MODEL_ID,
    _MODEL_LICENCE,
)
from satyalens_analysis.schema import ComponentStatus, AudioFinding, AudioReliability


class TestAudioDependencyCheck:
    def test_returns_tuple(self):
        ok, reason = _check_audio_deps()
        assert isinstance(ok, bool)
        assert isinstance(reason, str)

    def test_reason_empty_when_ok(self):
        ok, reason = _check_audio_deps()
        if ok:
            assert reason == ""

    def test_reason_populated_when_not_ok(self):
        ok, reason = _check_audio_deps()
        if not ok:
            assert len(reason) > 0


class TestModelMetadata:
    """Verify the model ID and licence strings are populated and honest."""

    def test_verified_model_id_is_set(self):
        assert len(_VERIFIED_MODEL_ID) > 0
        assert "/" in _VERIFIED_MODEL_ID  # namespace/model-name format

    def test_model_licence_string_populated(self):
        assert len(_MODEL_LICENCE) > 0

    def test_model_id_is_not_unverified_id(self):
        # Confirm the previously unverifiable ID is no longer used
        assert "Jenrola" not in _VERIFIED_MODEL_ID


class TestAnalyseAudioNoInput:
    def test_empty_path_returns_not_applicable(self):
        result = analyse_audio("")
        assert result.status == ComponentStatus.NOT_APPLICABLE

    def test_not_applicable_has_reason(self):
        result = analyse_audio("")
        assert len(result.reason) > 0

    def test_not_applicable_no_finding(self):
        result = analyse_audio("")
        assert result.finding is None


class TestAnalyseAudioMissingFile:
    def test_missing_file_returns_failed(self):
        result = analyse_audio("/nonexistent/audio.wav")
        assert result.status == ComponentStatus.FAILED

    def test_missing_file_has_reason(self):
        result = analyse_audio("/nonexistent/audio.wav")
        assert len(result.reason) > 0

    def test_missing_file_has_limitations(self):
        result = analyse_audio("/nonexistent/audio.wav")
        assert isinstance(result.limitations, list)
        assert len(result.limitations) > 0


class TestAnalyseAudioExistingFile:
    """For files that exist, model integration is still incomplete → unavailable."""

    @pytest.fixture()
    def real_wav(self, tmp_path):
        """Create a minimal valid WAV file using only stdlib (no torch)."""
        import wave, struct
        path = tmp_path / "silence.wav"
        with wave.open(str(path), "w") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(16000)
            # 1 second of silence (16000 frames × 2 bytes, all zeros)
            wf.writeframes(struct.pack("<" + "h" * 16000, *([0] * 16000)))
        return str(path)

    def test_existing_file_returns_unavailable(self, real_wav):
        result = analyse_audio(real_wav)
        assert result.status == ComponentStatus.UNAVAILABLE

    def test_unavailable_has_reason(self, real_wav):
        result = analyse_audio(real_wav)
        assert len(result.reason) > 0

    def test_unavailable_does_not_claim_genuine(self, real_wav):
        result = analyse_audio(real_wav)
        combined = (result.reason + " ".join(result.limitations)).upper()
        assert "NOT" in combined or "NEVER" in combined

    def test_unavailable_has_no_finding(self, real_wav):
        # finding must be absent when unavailable
        result = analyse_audio(real_wav)
        assert result.finding is None

    def test_unavailable_result_json_serialisable(self, real_wav):
        result = analyse_audio(real_wav)
        d = result.to_dict()
        json_str = json.dumps(d)
        loaded = json.loads(json_str)
        assert loaded["status"] == "unavailable"
        assert "finding" not in loaded


class TestAnalyseAudioUnavailableDeps:
    """
    When ML dependencies (torch/torchaudio/huggingface_hub) are missing,
    the result must be 'unavailable' and must not imply genuine.
    """
    def test_unavailable_status_when_deps_missing(self):
        if _AUDIO_DEPS_OK:
            pytest.skip("All audio deps are present; skipping unavailable-deps test")
        result = analyse_audio("/any/existing-or-not.wav")
        # Must be unavailable (deps missing takes priority over file existence)
        assert result.status in (
            ComponentStatus.UNAVAILABLE,
            ComponentStatus.FAILED,
        )

    def test_unavailable_limitations_present(self):
        if _AUDIO_DEPS_OK:
            pytest.skip("Deps present")
        result = analyse_audio("/any/path.wav")
        assert isinstance(result.limitations, list)
        combined = " ".join(result.limitations)
        assert "not" in combined.lower() or "never" in combined.lower()


class TestAnalyseAudioOutputSchema:
    def test_result_json_serialisable_empty(self):
        result = analyse_audio("")
        d = result.to_dict()
        json_str = json.dumps(d)
        loaded = json.loads(json_str)
        assert "status" in loaded
        assert "reason" in loaded

    def test_status_is_valid_enum_value(self):
        result = analyse_audio("")
        valid = {s.value for s in ComponentStatus}
        assert result.status.value in valid

    def test_finding_absent_when_not_analysed(self):
        result = analyse_audio("")
        d = result.to_dict()
        assert "finding" not in d


@pytest.mark.skipif(
    not _AUDIO_DEPS_OK,
    reason="Audio ML dependencies (torch/torchaudio/huggingface_hub) not installed"
)
class TestAnalyseAudioWithDeps:
    """
    These tests only run when torch/torchaudio/huggingface_hub are ALL available.
    Even then, they expect 'unavailable' because the custom AASIST loader is not
    yet implemented. They will be updated once the custom loader is built.
    """

    @pytest.fixture()
    def synthetic_wav(self, tmp_path):
        """Create a minimal valid WAV file using torchaudio."""
        import torch
        import torchaudio
        waveform = torch.zeros(1, 16000)
        path = tmp_path / "test_silence.wav"
        torchaudio.save(str(path), waveform, sample_rate=16000)
        return str(path)

    def test_with_deps_still_returns_unavailable(self, synthetic_wav):
        """Model custom loader is not yet built — even with deps, unavailable expected."""
        result = analyse_audio(synthetic_wav)
        assert result.status == ComponentStatus.UNAVAILABLE

    def test_result_has_limitations(self, synthetic_wav):
        result = analyse_audio(synthetic_wav)
        assert len(result.limitations) > 0

    def test_result_is_json_serialisable(self, synthetic_wav):
        result = analyse_audio(synthetic_wav)
        json.dumps(result.to_dict())
