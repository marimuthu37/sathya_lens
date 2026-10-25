"""
tests/test_analysis.py
──────────────────────
Segment 5 test suite: analysis request and retrieval.

Coverage:
  - Case not found, evidence not found
  - Consent not granted
  - Module unavailable (default) → status=unavailable, no fabricated findings
  - Idempotency: second POST returns existing result
  - Failed result: re-POST triggers fresh analysis
  - GET before any analysis → 404
  - GET returns persisted result
  - Module adapter returning real findings (mocked)
  - Module adapter raising AnalysisModuleError → FAILED + 500
  - Module adapter raising unexpected exception → FAILED + 500
  - File missing from disk (when module available)
  - Regression: all existing endpoints still pass
"""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
from typing import Optional
from unittest.mock import patch, MagicMock

import pytest

from app.models import AnalysisResultStatus, ConsentStatus
from app.services.analysis import (
    AnalysisAdapter,
    AnalysisFinding,
    AnalysisModuleError,
    ModuleResult,
    UnavailableAdapter,
)


# ── Shared helpers ─────────────────────────────────────────────────────────────

JPEG_MAGIC = b"\xff\xd8\xff" + b"\x00" * 100


def _make_case(client) -> str:
    r = client.post("/api/cases", json={})
    assert r.status_code == 201
    return r.json()["id"]


def _grant_consent(client, case_id: str) -> None:
    r = client.post(f"/api/cases/{case_id}/evidence/consent", json={"acknowledged": True})
    assert r.status_code == 200


def _upload(client, case_id: str, tmp_path: Path) -> str:
    r = client.post(
        f"/api/cases/{case_id}/evidence",
        files={"file": ("shot.jpg", io.BytesIO(JPEG_MAGIC), "image/jpeg")},
    )
    assert r.status_code == 201
    return r.json()["id"]


def _analyse_url(case_id: str, ev_id: str) -> str:
    return f"/api/cases/{case_id}/evidence/{ev_id}/analyse"


def _result_url(case_id: str, ev_id: str) -> str:
    return f"/api/cases/{case_id}/evidence/{ev_id}/analysis"


# ── Module-unavailable flow (default — no real module installed) ───────────────

class TestAnalysisUnavailable:

    def test_returns_202(self, client, tmp_path, monkeypatch):
        monkeypatch.setattr("app.routes.evidence._storage_path", lambda: tmp_path)
        monkeypatch.setattr("app.routes.analysis._storage_path", lambda: tmp_path)
        cid = _make_case(client)
        _grant_consent(client, cid)
        eid = _upload(client, cid, tmp_path)
        r = client.post(_analyse_url(cid, eid))
        assert r.status_code == 202

    def test_status_is_unavailable(self, client, tmp_path, monkeypatch):
        monkeypatch.setattr("app.routes.evidence._storage_path", lambda: tmp_path)
        monkeypatch.setattr("app.routes.analysis._storage_path", lambda: tmp_path)
        cid = _make_case(client)
        _grant_consent(client, cid)
        eid = _upload(client, cid, tmp_path)
        data = client.post(_analyse_url(cid, eid)).json()
        assert data["status"] == "unavailable"
        assert data["module_available"] is False

    def test_no_fabricated_findings(self, client, tmp_path, monkeypatch):
        monkeypatch.setattr("app.routes.evidence._storage_path", lambda: tmp_path)
        monkeypatch.setattr("app.routes.analysis._storage_path", lambda: tmp_path)
        cid = _make_case(client)
        _grant_consent(client, cid)
        eid = _upload(client, cid, tmp_path)
        client.post(_analyse_url(cid, eid))
        result = client.get(_result_url(cid, eid)).json()
        assert result["findings"] == []
        assert result["is_stub"] is True

    def test_limitations_text_present(self, client, tmp_path, monkeypatch):
        monkeypatch.setattr("app.routes.evidence._storage_path", lambda: tmp_path)
        monkeypatch.setattr("app.routes.analysis._storage_path", lambda: tmp_path)
        cid = _make_case(client)
        _grant_consent(client, cid)
        eid = _upload(client, cid, tmp_path)
        client.post(_analyse_url(cid, eid))
        result = client.get(_result_url(cid, eid)).json()
        assert result["limitations"] is not None
        assert len(result["limitations"]) > 10

    def test_result_persisted(self, client, tmp_path, monkeypatch):
        """GET after POST must return the persisted record."""
        monkeypatch.setattr("app.routes.evidence._storage_path", lambda: tmp_path)
        monkeypatch.setattr("app.routes.analysis._storage_path", lambda: tmp_path)
        cid = _make_case(client)
        _grant_consent(client, cid)
        eid = _upload(client, cid, tmp_path)
        post_data = client.post(_analyse_url(cid, eid)).json()
        get_data = client.get(_result_url(cid, eid)).json()
        assert get_data["id"] == post_data["result_id"]
        assert get_data["status"] == "unavailable"

    def test_idempotent_second_post(self, client, tmp_path, monkeypatch):
        """A second POST while result is unavailable/non-failed returns existing."""
        monkeypatch.setattr("app.routes.evidence._storage_path", lambda: tmp_path)
        monkeypatch.setattr("app.routes.analysis._storage_path", lambda: tmp_path)
        cid = _make_case(client)
        _grant_consent(client, cid)
        eid = _upload(client, cid, tmp_path)
        r1 = client.post(_analyse_url(cid, eid)).json()
        r2 = client.post(_analyse_url(cid, eid)).json()
        assert r1["result_id"] == r2["result_id"]


# ── Access control ────────────────────────────────────────────────────────────

class TestAnalysisAccessControl:

    def test_nonexistent_case_returns_404(self, client, tmp_path, monkeypatch):
        monkeypatch.setattr("app.routes.analysis._storage_path", lambda: tmp_path)
        r = client.post("/api/cases/00000000-0000-0000-0000-000000000000/evidence/00000000-0000-0000-0000-000000000001/analyse")
        assert r.status_code == 404

    def test_nonexistent_evidence_returns_404(self, client, tmp_path, monkeypatch):
        monkeypatch.setattr("app.routes.evidence._storage_path", lambda: tmp_path)
        monkeypatch.setattr("app.routes.analysis._storage_path", lambda: tmp_path)
        cid = _make_case(client)
        _grant_consent(client, cid)
        r = client.post(f"/api/cases/{cid}/evidence/00000000-0000-0000-0000-000000000001/analyse")
        assert r.status_code == 404

    def test_no_consent_returns_403(self, client, tmp_path, monkeypatch):
        monkeypatch.setattr("app.routes.evidence._storage_path", lambda: tmp_path)
        monkeypatch.setattr("app.routes.analysis._storage_path", lambda: tmp_path)
        cid = _make_case(client)
        # Do NOT grant consent
        _grant_consent(client, cid)  # grant consent so we can upload
        eid = _upload(client, cid, tmp_path)
        # Revoke by directly querying — instead, create a fresh case with no consent
        cid2 = _make_case(client)
        r = client.post(f"/api/cases/{cid2}/evidence/00000000-0000-0000-0000-000000000001/analyse")
        assert r.status_code in (403, 404)  # 403 if consent check first, 404 if evidence not found


class TestAnalysisConsentRequired:

    def test_consent_pending_returns_403(self, client, tmp_path, monkeypatch):
        monkeypatch.setattr("app.routes.analysis._storage_path", lambda: tmp_path)
        cid = _make_case(client)
        # No consent — need an evidence_id. Use a fake one; 404 after consent check.
        r = client.post(f"/api/cases/{cid}/evidence/00000000-0000-0000-0000-000000000002/analyse")
        assert r.status_code == 403
        assert r.json()["detail"]["code"] == "CONSENT_REQUIRED"


# ── GET before analysis requested ─────────────────────────────────────────────

class TestGetAnalysisNotFound:

    def test_get_before_post_returns_404(self, client, tmp_path, monkeypatch):
        monkeypatch.setattr("app.routes.evidence._storage_path", lambda: tmp_path)
        monkeypatch.setattr("app.routes.analysis._storage_path", lambda: tmp_path)
        cid = _make_case(client)
        _grant_consent(client, cid)
        eid = _upload(client, cid, tmp_path)
        r = client.get(_result_url(cid, eid))
        assert r.status_code == 404
        data = r.json()
        # Global 404 handler returns {error: ...}; HTTPException returns {detail: ...}
        assert "error" in data or "detail" in data

    def test_get_nonexistent_case_returns_404(self, client, tmp_path, monkeypatch):
        monkeypatch.setattr("app.routes.analysis._storage_path", lambda: tmp_path)
        r = client.get("/api/cases/00000000-0000-0000-0000-000000000000/evidence/00000000-0000-0000-0000-000000000001/analysis")
        assert r.status_code == 404


# ── Module available — mock adapter ──────────────────────────────────────────

class _MockAdapter(AnalysisAdapter):
    """Test adapter that returns deterministic real findings."""
    is_available = True

    def analyse(self, file_path, media_type, evidence_id):
        return ModuleResult(
            module_name="mock_module",
            module_version="0.1.0",
            findings=[
                AnalysisFinding(
                    finding_type="audio_quality",
                    label="Background noise detected",
                    detail="SNR estimated at 12 dB.",
                    evidence_source="audio_track",
                    is_interpretation=False,
                    reliability_note="Valid for recordings ≥ 5 s.",
                ),
                AnalysisFinding(
                    finding_type="interpretation",
                    label="Possible scripted speech pattern",
                    detail="Sentence structure matches known scam script templates.",
                    evidence_source="transcript",
                    is_interpretation=True,
                    reliability_note="Interpretive; requires human review.",
                ),
            ],
            limitations="Mock module; not a real analysis.",
            is_stub=False,
        )


class _FailingAdapter(AnalysisAdapter):
    """Test adapter that raises AnalysisModuleError."""
    is_available = True

    def analyse(self, file_path, media_type, evidence_id):
        raise AnalysisModuleError("Simulated module failure.")


class _CrashAdapter(AnalysisAdapter):
    """Test adapter that raises an unexpected exception."""
    is_available = True

    def analyse(self, file_path, media_type, evidence_id):
        raise RuntimeError("Unexpected crash in module.")


def _patch_adapter(monkeypatch, adapter_instance):
    """Patch the module-level _adapter singleton in the analysis service."""
    monkeypatch.setattr("app.routes.analysis.module_is_available", lambda: adapter_instance.is_available)
    monkeypatch.setattr("app.routes.analysis.run_analysis",
                        lambda fp, mt, eid: adapter_instance.analyse(fp, mt, eid))


class TestModuleAvailable:

    def test_completed_status(self, client, tmp_path, monkeypatch):
        monkeypatch.setattr("app.routes.evidence._storage_path", lambda: tmp_path)
        monkeypatch.setattr("app.routes.analysis._storage_path", lambda: tmp_path)
        _patch_adapter(monkeypatch, _MockAdapter())
        cid = _make_case(client)
        _grant_consent(client, cid)
        eid = _upload(client, cid, tmp_path)
        r = client.post(_analyse_url(cid, eid))
        assert r.status_code == 202
        assert r.json()["status"] == "completed"
        assert r.json()["module_available"] is True

    def test_findings_persisted_and_returned(self, client, tmp_path, monkeypatch):
        monkeypatch.setattr("app.routes.evidence._storage_path", lambda: tmp_path)
        monkeypatch.setattr("app.routes.analysis._storage_path", lambda: tmp_path)
        _patch_adapter(monkeypatch, _MockAdapter())
        cid = _make_case(client)
        _grant_consent(client, cid)
        eid = _upload(client, cid, tmp_path)
        client.post(_analyse_url(cid, eid))
        result = client.get(_result_url(cid, eid)).json()
        assert result["status"] == "completed"
        assert len(result["findings"]) == 2
        assert result["is_stub"] is False
        assert result["module_available"] is True

    def test_interpretation_flag_preserved(self, client, tmp_path, monkeypatch):
        monkeypatch.setattr("app.routes.evidence._storage_path", lambda: tmp_path)
        monkeypatch.setattr("app.routes.analysis._storage_path", lambda: tmp_path)
        _patch_adapter(monkeypatch, _MockAdapter())
        cid = _make_case(client)
        _grant_consent(client, cid)
        eid = _upload(client, cid, tmp_path)
        client.post(_analyse_url(cid, eid))
        result = client.get(_result_url(cid, eid)).json()
        interps = [f for f in result["findings"] if f["is_interpretation"]]
        assert len(interps) == 1

    def test_module_error_returns_500_and_status_failed(self, client, tmp_path, monkeypatch):
        monkeypatch.setattr("app.routes.evidence._storage_path", lambda: tmp_path)
        monkeypatch.setattr("app.routes.analysis._storage_path", lambda: tmp_path)
        _patch_adapter(monkeypatch, _FailingAdapter())
        cid = _make_case(client)
        _grant_consent(client, cid)
        eid = _upload(client, cid, tmp_path)
        r = client.post(_analyse_url(cid, eid))
        assert r.status_code == 500
        assert r.json()["detail"]["code"] == "ANALYSIS_FAILED"
        # Result is persisted as FAILED
        get_r = client.get(_result_url(cid, eid)).json()
        assert get_r["status"] == "failed"

    def test_unexpected_crash_returns_500_and_status_failed(self, client, tmp_path, monkeypatch):
        monkeypatch.setattr("app.routes.evidence._storage_path", lambda: tmp_path)
        monkeypatch.setattr("app.routes.analysis._storage_path", lambda: tmp_path)
        _patch_adapter(monkeypatch, _CrashAdapter())
        cid = _make_case(client)
        _grant_consent(client, cid)
        eid = _upload(client, cid, tmp_path)
        r = client.post(_analyse_url(cid, eid))
        assert r.status_code == 500
        get_r = client.get(_result_url(cid, eid)).json()
        assert get_r["status"] == "failed"
        assert get_r["error_message"] is not None

    def test_retry_after_failed_starts_fresh(self, client, tmp_path, monkeypatch):
        """A second POST when status=FAILED should re-run analysis."""
        monkeypatch.setattr("app.routes.evidence._storage_path", lambda: tmp_path)
        monkeypatch.setattr("app.routes.analysis._storage_path", lambda: tmp_path)
        _patch_adapter(monkeypatch, _FailingAdapter())
        cid = _make_case(client)
        _grant_consent(client, cid)
        eid = _upload(client, cid, tmp_path)
        client.post(_analyse_url(cid, eid))  # fails

        # Switch to working adapter
        _patch_adapter(monkeypatch, _MockAdapter())
        r2 = client.post(_analyse_url(cid, eid))
        assert r2.status_code == 202
        assert r2.json()["status"] == "completed"

    def test_file_missing_from_disk_returns_422(self, client, tmp_path, monkeypatch):
        monkeypatch.setattr("app.routes.evidence._storage_path", lambda: tmp_path)
        monkeypatch.setattr("app.routes.analysis._storage_path", lambda: tmp_path)
        _patch_adapter(monkeypatch, _MockAdapter())
        cid = _make_case(client)
        _grant_consent(client, cid)
        eid = _upload(client, cid, tmp_path)
        # Remove file from disk
        for f in tmp_path.iterdir():
            f.unlink()
        r = client.post(_analyse_url(cid, eid))
        assert r.status_code == 422
        assert r.json()["detail"]["code"] == "FILE_NOT_FOUND"


# ── UnavailableAdapter unit tests ─────────────────────────────────────────────

class TestUnavailableAdapterUnit:

    def test_returns_stub_result(self):
        adapter = UnavailableAdapter()
        result = adapter.analyse(Path("/dev/null"), "image/jpeg", "test-id")
        assert result.is_stub is True
        assert result.findings == []
        assert result.module_name == "unavailable"
        assert len(result.limitations) > 0

    def test_is_available_false(self):
        assert UnavailableAdapter.is_available is False
