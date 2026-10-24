"""
test_component_safety.py — audit safeguards: audio never fabricates a detection, and
run_analysis() keeps its seven-key, JSON-serialisable contract without logging message text.
(Payment-OCR safeguards are added in a separate commit.)
"""
import json
import logging

import pytest

from satyalens_analysis import audio_detector as ad
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
