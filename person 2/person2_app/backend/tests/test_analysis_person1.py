"""
tests/test_analysis_person1.py
──────────────────────────────
Person 1 adapter (app/services/analysis_person1.py) and route integration.

Coverage:
  - Media routing: image → screenshot_path, audio → audio_path, text → message_text
  - Unsupported media type → stub, package not called
  - UTF-8 handling: BOM accepted, invalid bytes / oversize text rejected
  - Unavailable audio → stub (never a finding, never COMPLETED)
  - Only concern signals become findings; benign results go to limitations
  - Contract validation (seven top-level keys, five payment keys, schema 1.0)
  - All relevant components failed → AnalysisModuleError
  - Findings survive Person 2's findings_to_json / findings_from_json validation
  - Route: stub result persisted as UNAVAILABLE; decision stays INSUFFICIENT_EVIDENCE
  - Route: concern findings persisted as COMPLETED; decision is VERIFY_FIRST
  - Real package smoke test (skipped if satyalens_analysis is not installed)
"""

from __future__ import annotations

import copy
import io
from pathlib import Path

import pytest

from app.services.analysis import (
    AnalysisModuleError,
    findings_from_json,
    findings_to_json,
)
from app.services.analysis_person1 import MAX_MESSAGE_CHARS, Person1Adapter


# ── Fake Person 1 results ─────────────────────────────────────────────────────

_BASE_RESULT = {
    "schema_version": "1.0",
    "case_id": "",
    "audio": {"status": "not_applicable", "reason": "No audio path provided."},
    "payment": {
        "status": "not_applicable", "raw_text": "", "fields": [],
        "mismatches": [], "limitations": ["No screenshot path provided."],
    },
    "context": {"status": "not_applicable", "flags": [], "findings": [],
                "limitations": ["No message text provided."]},
    "text_classifier": {"status": "not_applicable", "reason": "No message text provided.",
                        "top_terms": [], "limitations": []},
    "errors": [],
}

_AUDIO_UNAVAILABLE = {
    "status": "unavailable",
    "reason": "Audio deepfake detection is not yet available.",
    "limitations": ["No compatible audio model loader is implemented."],
}

_CONTEXT_FLAGGED = {
    "status": "analysed",
    "flags": [{
        "rule_id": "CREDENTIAL_REQUEST", "label": "Credential / OTP Request",
        "severity": "high", "explanation": 'Phrase "OTP" requests a credential.',
    }],
    "findings": ["1 rule fired."],
    "limitations": ["Keyword rules can miss paraphrased scams."],
}

_CONTEXT_CLEAN = {"status": "analysed", "flags": [], "findings": [],
                  "limitations": ["Keyword rules can miss paraphrased scams."]}

_TC_SUSPICIOUS = {
    "status": "analysed", "predicted_class": "suspicious", "raw_score": 0.91,
    "model_version": "tfidf-logreg-v1-test", "training_data": "synthetic",
    "reason": "Inference ran.", "limitations": ["Synthetic training data."],
    "top_terms": [{"term": "otp", "weight": 1.2}, {"term": "hello", "weight": -0.3}],
}

_TC_LEGITIMATE = dict(_TC_SUSPICIOUS, predicted_class="legitimate", raw_score=0.12)

_TC_UNAVAILABLE = {"status": "unavailable", "reason": "Model artifact not found.",
                   "top_terms": [], "limitations": []}


def _result(**overrides) -> dict:
    r = copy.deepcopy(_BASE_RESULT)
    r.update(copy.deepcopy(overrides))
    return r


class _FakeRun:
    """Records the kwargs passed to run_analysis and returns a canned result."""

    def __init__(self, result: dict) -> None:
        self.result = result
        self.calls: list[dict] = []

    def __call__(self, **kwargs) -> dict:
        self.calls.append(kwargs)
        return self.result


def _adapter(result: dict) -> tuple[Person1Adapter, _FakeRun]:
    fake = _FakeRun(result)
    return Person1Adapter(fake, "1.0.0+schema1.0"), fake


def _write(tmp_path: Path, name: str, data: bytes) -> Path:
    p = tmp_path / name
    p.write_bytes(data)
    return p


# ── Media routing ─────────────────────────────────────────────────────────────

class TestMediaRouting:

    def test_image_maps_to_screenshot_path(self, tmp_path):
        adapter, fake = _adapter(_result(payment={
            "status": "analysed", "raw_text": "x", "fields": [], "mismatches": [], "limitations": ["l"],
        }))
        f = _write(tmp_path, "a.png", b"\x89PNG\r\n\x1a\n")
        adapter.analyse(f, "image/png", "ev-1")
        assert fake.calls == [{"case_id": "ev-1", "screenshot_path": str(f)}]

    def test_audio_maps_to_audio_path(self, tmp_path):
        adapter, fake = _adapter(_result(audio=_AUDIO_UNAVAILABLE))
        f = _write(tmp_path, "a.wav", b"RIFF")
        adapter.analyse(f, "audio/wav", "ev-2")
        assert fake.calls == [{"case_id": "ev-2", "audio_path": str(f)}]

    def test_text_maps_to_message_text(self, tmp_path):
        adapter, fake = _adapter(_result(context=_CONTEXT_CLEAN, text_classifier=_TC_UNAVAILABLE))
        f = _write(tmp_path, "m.txt", "﻿Pay now ₹5000".encode("utf-8"))
        adapter.analyse(f, "text/plain; charset=utf-8", "ev-3")
        assert fake.calls == [{"case_id": "ev-3", "message_text": "Pay now ₹5000"}]

    def test_unsupported_media_type_is_stub_without_calling_package(self, tmp_path):
        adapter, fake = _adapter(_result())
        f = _write(tmp_path, "d.pdf", b"%PDF-1.4")
        res = adapter.analyse(f, "application/pdf", "ev-4")
        assert res.is_stub is True
        assert res.findings == []
        assert "application/pdf" in res.limitations
        assert fake.calls == []

    def test_invalid_utf8_raises(self, tmp_path):
        adapter, fake = _adapter(_result())
        f = _write(tmp_path, "m.txt", b"\xff\xfe\x00bad")
        with pytest.raises(AnalysisModuleError):
            adapter.analyse(f, "text/plain", "ev-5")
        assert fake.calls == []

    def test_oversize_text_raises(self, tmp_path):
        adapter, fake = _adapter(_result())
        f = _write(tmp_path, "m.txt", b"a" * (MAX_MESSAGE_CHARS + 1))
        with pytest.raises(AnalysisModuleError):
            adapter.analyse(f, "text/plain", "ev-6")
        assert fake.calls == []

    def test_missing_file_raises(self, tmp_path):
        adapter, _ = _adapter(_result())
        with pytest.raises(AnalysisModuleError):
            adapter.analyse(tmp_path / "gone.png", "image/png", "ev-7")


# ── Finding / limitation policy ──────────────────────────────────────────────

class TestFindingPolicy:

    def test_unavailable_audio_is_stub_with_no_findings(self, tmp_path):
        adapter, _ = _adapter(_result(audio=_AUDIO_UNAVAILABLE))
        res = adapter.analyse(_write(tmp_path, "a.wav", b"RIFF"), "audio/wav", "e")
        assert res.is_stub is True
        assert res.findings == []
        assert "unavailable" in res.limitations
        assert "does NOT mean the evidence is genuine" in res.limitations

    def test_audio_genuine_likely_is_not_a_finding(self, tmp_path):
        audio = {"status": "analysed", "finding": "genuine_likely", "reliability": "limited",
                 "reason": "r", "raw_score": 0.1, "limitations": []}
        adapter, _ = _adapter(_result(audio=audio))
        res = adapter.analyse(_write(tmp_path, "a.wav", b"RIFF"), "audio/wav", "e")
        assert res.is_stub is False
        assert res.findings == []

    def test_payment_mismatch_becomes_finding(self, tmp_path):
        payment = {
            "status": "analysed", "raw_text": "Rs 500",
            "fields": [{"field_name": "amount", "extracted_value": "500", "expected_value": "5000",
                        "match": False, "confidence": "medium"}],
            "mismatches": ["amount: expected='5000', extracted='500'"],
            "limitations": ["OCR may produce errors."],
        }
        adapter, _ = _adapter(_result(payment=payment))
        res = adapter.analyse(_write(tmp_path, "a.png", b"x"), "image/png", "e")
        assert [f.finding_type for f in res.findings] == ["payment_mismatch"]
        assert res.findings[0].detail == "amount: expected='5000', extracted='500'"

    def test_payment_without_mismatches_has_no_findings(self, tmp_path):
        payment = {
            "status": "analysed", "raw_text": "Rs 5000",
            "fields": [{"field_name": "amount", "extracted_value": "5000", "expected_value": None,
                        "match": None, "confidence": "medium"}],
            "mismatches": [], "limitations": ["OCR may produce errors."],
        }
        adapter, _ = _adapter(_result(payment=payment))
        res = adapter.analyse(_write(tmp_path, "a.png", b"x"), "image/png", "e")
        assert res.is_stub is False
        assert res.findings == []
        assert "no comparison was made" in res.limitations

    def test_context_flag_becomes_script_match_interpretation(self, tmp_path):
        adapter, _ = _adapter(_result(context=_CONTEXT_FLAGGED, text_classifier=_TC_UNAVAILABLE))
        res = adapter.analyse(_write(tmp_path, "m.txt", b"Send OTP"), "text/plain", "e")
        assert len(res.findings) == 1
        f = res.findings[0]
        assert f.finding_type == "script_match"
        assert f.is_interpretation is True
        assert "CREDENTIAL_REQUEST" in f.reliability_note
        assert "[text_classifier] status: unavailable." in res.limitations

    def test_suspicious_classifier_is_interpretation_without_score(self, tmp_path):
        adapter, _ = _adapter(_result(context=_CONTEXT_CLEAN, text_classifier=_TC_SUSPICIOUS))
        res = adapter.analyse(_write(tmp_path, "m.txt", b"Send OTP"), "text/plain", "e")
        assert len(res.findings) == 1
        f = res.findings[0]
        assert f.finding_type == "script_match"
        assert f.is_interpretation is True
        assert "otp" in f.detail and "hello" not in f.detail
        assert "0.91" not in f.detail and "%" not in f.detail

    def test_legitimate_classifier_goes_to_limitations(self, tmp_path):
        adapter, _ = _adapter(_result(context=_CONTEXT_CLEAN, text_classifier=_TC_LEGITIMATE))
        res = adapter.analyse(_write(tmp_path, "m.txt", b"Hi"), "text/plain", "e")
        assert res.is_stub is False
        assert res.findings == []
        assert "does NOT mean the message is safe" in res.limitations

    def test_never_emits_high_risk_types_for_text(self, tmp_path):
        adapter, _ = _adapter(_result(context=_CONTEXT_FLAGGED, text_classifier=_TC_SUSPICIOUS))
        res = adapter.analyse(_write(tmp_path, "m.txt", b"Send OTP"), "text/plain", "e")
        assert {f.finding_type for f in res.findings} == {"script_match"}

    def test_findings_survive_person2_validation(self, tmp_path):
        adapter, _ = _adapter(_result(context=_CONTEXT_FLAGGED, text_classifier=_TC_SUSPICIOUS))
        res = adapter.analyse(_write(tmp_path, "m.txt", b"Send OTP"), "text/plain", "e")
        assert len(findings_from_json(findings_to_json(res.findings))) == len(res.findings) == 2


# ── Status and contract handling ──────────────────────────────────────────────

class TestStatusAndContract:

    def test_all_relevant_components_failed_raises(self, tmp_path):
        failed = {"status": "failed", "reason": "boom", "limitations": []}
        adapter, _ = _adapter(_result(context=dict(failed, flags=[], findings=[]),
                                      text_classifier=dict(failed, top_terms=[]),
                                      errors=["Context component unexpected error: boom"]))
        with pytest.raises(AnalysisModuleError):
            adapter.analyse(_write(tmp_path, "m.txt", b"x"), "text/plain", "e")

    def test_one_failed_one_analysed_completes_and_reports_failure(self, tmp_path):
        failed_tc = {"status": "failed", "reason": "SHA mismatch", "top_terms": [], "limitations": []}
        adapter, _ = _adapter(_result(context=_CONTEXT_FLAGGED, text_classifier=failed_tc))
        res = adapter.analyse(_write(tmp_path, "m.txt", b"OTP"), "text/plain", "e")
        assert res.is_stub is False
        assert len(res.findings) == 1
        assert "[text_classifier] status: failed." in res.limitations

    def test_missing_top_level_key_raises(self, tmp_path):
        bad = _result()
        del bad["text_classifier"]
        adapter, _ = _adapter(bad)
        with pytest.raises(AnalysisModuleError):
            adapter.analyse(_write(tmp_path, "a.png", b"x"), "image/png", "e")

    def test_missing_payment_key_raises(self, tmp_path):
        bad = _result()
        del bad["payment"]["mismatches"]
        adapter, _ = _adapter(bad)
        with pytest.raises(AnalysisModuleError):
            adapter.analyse(_write(tmp_path, "a.png", b"x"), "image/png", "e")

    def test_wrong_schema_version_raises(self, tmp_path):
        adapter, _ = _adapter(_result(schema_version="2.0"))
        with pytest.raises(AnalysisModuleError):
            adapter.analyse(_write(tmp_path, "a.png", b"x"), "image/png", "e")

    def test_additive_keys_are_tolerated(self, tmp_path):
        adapter, _ = _adapter(_result(new_component={"status": "analysed"},
                                      payment=dict(_BASE_RESULT["payment"], status="inconclusive")))
        res = adapter.analyse(_write(tmp_path, "a.png", b"x"), "image/png", "e")
        assert res.is_stub is False


# ── Route integration ────────────────────────────────────────────────────────

def _setup_case(client, tmp_path, monkeypatch, adapter, filename, data, mime) -> tuple[str, str]:
    monkeypatch.setattr("app.routes.evidence._storage_path", lambda: tmp_path)
    monkeypatch.setattr("app.routes.analysis._storage_path", lambda: tmp_path)
    monkeypatch.setattr("app.routes.analysis.module_is_available", lambda: adapter.is_available)
    monkeypatch.setattr("app.routes.analysis.run_analysis",
                        lambda fp, mt, eid: adapter.analyse(fp, mt, eid))
    cid = client.post("/api/cases", json={}).json()["id"]
    client.post(f"/api/cases/{cid}/evidence/consent", json={"acknowledged": True})
    r = client.post(f"/api/cases/{cid}/evidence", files={"file": (filename, io.BytesIO(data), mime)})
    assert r.status_code == 201
    return cid, r.json()["id"]


class TestRouteIntegration:

    def test_stub_result_persisted_as_unavailable(self, client, tmp_path, monkeypatch):
        adapter, _ = _adapter(_result(audio=_AUDIO_UNAVAILABLE))
        cid, eid = _setup_case(client, tmp_path, monkeypatch, adapter, "call.wav", b"RIFF0000", "audio/wav")
        r = client.post(f"/api/cases/{cid}/evidence/{eid}/analyse")
        assert r.status_code == 202
        assert r.json()["status"] == "unavailable"
        assert r.json()["module_available"] is True

        result = client.get(f"/api/cases/{cid}/evidence/{eid}/analysis").json()
        assert result["status"] == "unavailable"
        assert result["is_stub"] is True
        assert result["findings"] == []
        assert result["module_name"] == "satyalens_analysis"

        ev = client.get(f"/api/cases/{cid}").json()["evidence_items"][0]
        assert ev["processing_status"] == "pending"

        decision = client.post(f"/api/cases/{cid}/decision").json()
        assert decision["category"] == "insufficient_evidence"

    def test_concern_findings_completed_and_verify_first(self, client, tmp_path, monkeypatch):
        adapter, fake = _adapter(_result(context=_CONTEXT_FLAGGED, text_classifier=_TC_SUSPICIOUS))
        cid, eid = _setup_case(client, tmp_path, monkeypatch, adapter,
                               "msg.txt", b"Share your OTP now", "text/plain")
        r = client.post(f"/api/cases/{cid}/evidence/{eid}/analyse")
        assert r.status_code == 202
        assert r.json()["status"] == "completed"
        assert fake.calls[0]["message_text"] == "Share your OTP now"

        result = client.get(f"/api/cases/{cid}/evidence/{eid}/analysis").json()
        assert result["is_stub"] is False
        assert [f["finding_type"] for f in result["findings"]] == ["script_match", "script_match"]

        decision = client.post(f"/api/cases/{cid}/decision").json()
        assert decision["category"] == "verify_first"

    def test_benign_only_result_is_not_low_concern(self, client, tmp_path, monkeypatch):
        adapter, _ = _adapter(_result(context=_CONTEXT_CLEAN, text_classifier=_TC_LEGITIMATE))
        cid, eid = _setup_case(client, tmp_path, monkeypatch, adapter, "msg.txt", b"Hello", "text/plain")
        assert client.post(f"/api/cases/{cid}/evidence/{eid}/analyse").json()["status"] == "completed"
        decision = client.post(f"/api/cases/{cid}/decision").json()
        assert decision["category"] == "insufficient_evidence"

    def test_unsupported_media_unavailable(self, client, tmp_path, monkeypatch):
        adapter, fake = _adapter(_result())
        cid, eid = _setup_case(client, tmp_path, monkeypatch, adapter, "doc.pdf", b"%PDF-1.4 x", "application/pdf")
        assert client.post(f"/api/cases/{cid}/evidence/{eid}/analyse").json()["status"] == "unavailable"
        assert fake.calls == []

    def test_invalid_utf8_text_failed_500(self, client, tmp_path, monkeypatch):
        adapter, _ = _adapter(_result())
        cid, eid = _setup_case(client, tmp_path, monkeypatch, adapter, "msg.txt", b"\xff\xfebad", "text/plain")
        r = client.post(f"/api/cases/{cid}/evidence/{eid}/analyse")
        assert r.status_code == 500
        assert r.json()["detail"]["code"] == "ANALYSIS_FAILED"
        assert client.get(f"/api/cases/{cid}/evidence/{eid}/analysis").json()["status"] == "failed"


# ── Default configuration and real package ───────────────────────────────────

def test_module_disabled_by_default():
    from app.config import Settings
    assert Settings(_env_file=None).analysis_module_enabled is False


def test_get_adapter_with_real_package():
    pytest.importorskip("satyalens_analysis")
    from app.services.analysis_person1 import get_adapter
    adapter = get_adapter()
    assert adapter.is_available is True


def test_real_package_text_smoke(tmp_path):
    """Context rules are deterministic; the classifier may be unavailable here."""
    pytest.importorskip("satyalens_analysis")
    from app.services.analysis_person1 import get_adapter
    f = _write(tmp_path, "m.txt",
               b"URGENT: your account will be blocked. Share the OTP immediately and tell no one.")
    res = get_adapter().analyse(f, "text/plain", "smoke")
    assert res.is_stub is False
    assert res.findings, "expected at least one context rule to fire"
    assert {x.finding_type for x in res.findings} == {"script_match"}


def test_real_package_audio_is_unavailable(tmp_path):
    pytest.importorskip("satyalens_analysis")
    from app.services.analysis_person1 import get_adapter
    res = get_adapter().analyse(_write(tmp_path, "a.wav", b"RIFF0000WAVE"), "audio/wav", "smoke")
    assert res.is_stub is True
    assert res.findings == []
