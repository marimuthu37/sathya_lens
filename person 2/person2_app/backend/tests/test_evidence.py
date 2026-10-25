"""
tests/test_evidence.py
──────────────────────
Segment 4 test suite covering:
  - Consent workflow (missing case, idempotency, state transitions)
  - Evidence upload (consent check, MIME allow-list, size limit, magic bytes,
    SHA-256 correctness, metadata persistence, path traversal, duplicate filenames)
  - Evidence deletion (found, not found, file removal)
  - Storage error simulation
  - Regression: all existing case CRUD and health tests still pass
    (run test_cases.py and test_health.py separately)

All tests use the in-memory SQLite database from conftest.py and a
temporary upload directory — no real filesystem writes to production paths.
"""

from __future__ import annotations

import hashlib
import io
import os
import tempfile
import unittest.mock as mock
from pathlib import Path

import pytest

from app.models import Case, ConsentStatus


# ── Helpers ───────────────────────────────────────────────────────────────────

JPEG_MAGIC = b"\xff\xd8\xff" + b"\x00" * 100   # minimal valid JPEG header
PNG_MAGIC  = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
PDF_MAGIC  = b"%PDF-1.4\n" + b"\x00" * 100
TXT_BYTES  = b"Hello, SatyaLens evidence test.\n"


def _make_case(client) -> str:
    """Create a case and return its ID."""
    resp = client.post("/api/cases", json={})
    assert resp.status_code == 201
    return resp.json()["id"]


def _grant_consent(client, case_id: str) -> None:
    resp = client.post(f"/api/cases/{case_id}/evidence/consent", json={"acknowledged": True})
    assert resp.status_code == 200


def _upload(client, case_id: str, content: bytes, filename: str, mime: str):
    return client.post(
        f"/api/cases/{case_id}/evidence",
        files={"file": (filename, io.BytesIO(content), mime)},
    )


# ── Consent endpoint ──────────────────────────────────────────────────────────

class TestConsent:

    def test_consent_nonexistent_case_returns_404(self, client):
        resp = client.post("/api/cases/00000000-0000-0000-0000-000000000000/evidence/consent", json={"acknowledged": True})
        assert resp.status_code == 404
        data = resp.json()
        # Global 404 handler OR case-specific HTTPException — both are valid 404s
        assert "error" in data or "detail" in data

    def test_consent_returns_200(self, client):
        cid = _make_case(client)
        resp = client.post(f"/api/cases/{cid}/evidence/consent", json={"acknowledged": True})
        assert resp.status_code == 200

    def test_consent_response_schema(self, client):
        cid = _make_case(client)
        data = client.post(f"/api/cases/{cid}/evidence/consent", json={"acknowledged": True}).json()
        assert data["case_id"] == cid
        assert data["consent_status"] == "granted"
        assert data["consent_at"] is not None
        assert "message" in data

    def test_consent_updates_case_consent_status(self, client):
        cid = _make_case(client)
        client.post(f"/api/cases/{cid}/evidence/consent", json={"acknowledged": True})
        case_data = client.get(f"/api/cases/{cid}").json()
        assert case_data["consent_status"] == "granted"
        assert case_data["consent_at"] is not None

    def test_consent_is_idempotent(self, client):
        """Calling consent twice should not error and should remain granted."""
        cid = _make_case(client)
        client.post(f"/api/cases/{cid}/evidence/consent", json={"acknowledged": True})
        resp2 = client.post(f"/api/cases/{cid}/evidence/consent", json={"acknowledged": True})
        assert resp2.status_code == 200
        assert resp2.json()["consent_status"] == "granted"

    def test_consent_starts_as_pending(self, client):
        cid = _make_case(client)
        case_data = client.get(f"/api/cases/{cid}").json()
        assert case_data["consent_status"] == "pending"


# ── Upload endpoint ───────────────────────────────────────────────────────────

class TestUpload:

    # Fixtures: each test uses a fresh db and client from conftest

    def test_upload_without_consent_returns_403(self, client, tmp_path, monkeypatch):
        monkeypatch.setattr("app.routes.evidence._storage_path", lambda: tmp_path)
        cid = _make_case(client)
        resp = _upload(client, cid, JPEG_MAGIC, "test.jpg", "image/jpeg")
        assert resp.status_code == 403
        assert resp.json()["detail"]["code"] == "CONSENT_REQUIRED"

    def test_upload_nonexistent_case_returns_404(self, client, tmp_path, monkeypatch):
        monkeypatch.setattr("app.routes.evidence._storage_path", lambda: tmp_path)
        resp = _upload(client, "00000000-0000-0000-0000-000000000000",
                       JPEG_MAGIC, "test.jpg", "image/jpeg")
        assert resp.status_code == 404

    def test_upload_valid_jpeg_returns_201(self, client, tmp_path, monkeypatch):
        monkeypatch.setattr("app.routes.evidence._storage_path", lambda: tmp_path)
        cid = _make_case(client)
        _grant_consent(client, cid)
        resp = _upload(client, cid, JPEG_MAGIC, "photo.jpg", "image/jpeg")
        assert resp.status_code == 201

    def test_upload_response_schema(self, client, tmp_path, monkeypatch):
        monkeypatch.setattr("app.routes.evidence._storage_path", lambda: tmp_path)
        cid = _make_case(client)
        _grant_consent(client, cid)
        data = _upload(client, cid, JPEG_MAGIC, "photo.jpg", "image/jpeg").json()
        assert "id" in data
        assert data["case_id"] == cid
        assert data["original_filename"] == "photo.jpg"
        assert data["media_type"] == "image/jpeg"
        assert data["file_size_bytes"] == len(JPEG_MAGIC)
        assert len(data["sha256_hash"]) == 64
        assert data["processing_status"] == "pending"
        assert "created_at" in data

    def test_upload_sha256_correctness(self, client, tmp_path, monkeypatch):
        monkeypatch.setattr("app.routes.evidence._storage_path", lambda: tmp_path)
        cid = _make_case(client)
        _grant_consent(client, cid)
        content = JPEG_MAGIC + b"unique-bytes-for-sha-test"
        expected = hashlib.sha256(content).hexdigest()
        data = _upload(client, cid, content, "hash_test.jpg", "image/jpeg").json()
        assert data["sha256_hash"] == expected

    def test_upload_disallowed_mime_returns_415(self, client, tmp_path, monkeypatch):
        monkeypatch.setattr("app.routes.evidence._storage_path", lambda: tmp_path)
        cid = _make_case(client)
        _grant_consent(client, cid)
        resp = _upload(client, cid, b"#!/bin/sh\nrm -rf /", "evil.sh", "application/x-sh")
        assert resp.status_code == 415
        assert resp.json()["detail"]["code"] == "UNSUPPORTED_MEDIA_TYPE"

    def test_upload_wrong_magic_bytes_returns_415(self, client, tmp_path, monkeypatch):
        monkeypatch.setattr("app.routes.evidence._storage_path", lambda: tmp_path)
        cid = _make_case(client)
        _grant_consent(client, cid)
        # Send text bytes but claim it's a JPEG
        resp = _upload(client, cid, b"not a jpeg at all", "fake.jpg", "image/jpeg")
        assert resp.status_code == 415
        assert resp.json()["detail"]["code"] == "INVALID_FILE_SIGNATURE"

    def test_upload_empty_file_returns_400(self, client, tmp_path, monkeypatch):
        monkeypatch.setattr("app.routes.evidence._storage_path", lambda: tmp_path)
        cid = _make_case(client)
        _grant_consent(client, cid)
        resp = _upload(client, cid, b"", "empty.jpg", "image/jpeg")
        assert resp.status_code == 400
        assert resp.json()["detail"]["code"] == "EMPTY_FILE"

    def test_upload_oversized_file_returns_413(self, client, tmp_path, monkeypatch):
        monkeypatch.setattr("app.routes.evidence._storage_path", lambda: tmp_path)
        # Lower the limit to 200 bytes for the test
        monkeypatch.setattr("app.routes.evidence.settings.max_upload_bytes", 200)
        cid = _make_case(client)
        _grant_consent(client, cid)
        big_content = JPEG_MAGIC + b"\x00" * 300
        resp = _upload(client, cid, big_content, "big.jpg", "image/jpeg")
        assert resp.status_code == 413
        assert resp.json()["detail"]["code"] == "FILE_TOO_LARGE"

    def test_upload_pdf_valid(self, client, tmp_path, monkeypatch):
        monkeypatch.setattr("app.routes.evidence._storage_path", lambda: tmp_path)
        cid = _make_case(client)
        _grant_consent(client, cid)
        resp = _upload(client, cid, PDF_MAGIC, "doc.pdf", "application/pdf")
        assert resp.status_code == 201

    def test_upload_text_plain_valid(self, client, tmp_path, monkeypatch):
        monkeypatch.setattr("app.routes.evidence._storage_path", lambda: tmp_path)
        cid = _make_case(client)
        _grant_consent(client, cid)
        resp = _upload(client, cid, TXT_BYTES, "notes.txt", "text/plain")
        assert resp.status_code == 201

    def test_upload_evidence_appears_in_case_detail(self, client, tmp_path, monkeypatch):
        monkeypatch.setattr("app.routes.evidence._storage_path", lambda: tmp_path)
        cid = _make_case(client)
        _grant_consent(client, cid)
        _upload(client, cid, JPEG_MAGIC, "screen.jpg", "image/jpeg")
        case_data = client.get(f"/api/cases/{cid}").json()
        assert len(case_data["evidence_items"]) == 1
        assert case_data["evidence_items"][0]["original_filename"] == "screen.jpg"

    def test_upload_duplicate_filename_both_accepted(self, client, tmp_path, monkeypatch):
        """Server generates different UUIDs so duplicate filenames are allowed."""
        monkeypatch.setattr("app.routes.evidence._storage_path", lambda: tmp_path)
        cid = _make_case(client)
        _grant_consent(client, cid)
        r1 = _upload(client, cid, JPEG_MAGIC, "dup.jpg", "image/jpeg")
        r2 = _upload(client, cid, JPEG_MAGIC + b"x", "dup.jpg", "image/jpeg")
        assert r1.status_code == 201
        assert r2.status_code == 201
        assert r1.json()["id"] != r2.json()["id"]

    def test_upload_path_traversal_filename_sanitised(self, client, tmp_path, monkeypatch):
        monkeypatch.setattr("app.routes.evidence._storage_path", lambda: tmp_path)
        cid = _make_case(client)
        _grant_consent(client, cid)
        # Attempt path traversal in filename
        resp = _upload(client, cid, JPEG_MAGIC, "../../etc/passwd.jpg", "image/jpeg")
        # Should succeed but stored safely
        assert resp.status_code == 201
        # Filename must NOT contain '..'
        assert ".." not in resp.json()["original_filename"]

    def test_upload_file_written_to_tmp_dir(self, client, tmp_path, monkeypatch):
        monkeypatch.setattr("app.routes.evidence._storage_path", lambda: tmp_path)
        cid = _make_case(client)
        _grant_consent(client, cid)
        _upload(client, cid, JPEG_MAGIC, "save_check.jpg", "image/jpeg")
        files = list(tmp_path.iterdir())
        assert len(files) == 1

    def test_upload_storage_error_returns_500(self, client, tmp_path, monkeypatch):
        """Simulate an OS write error on final rename; endpoint must return 500 and not save DB record."""
        monkeypatch.setattr("app.routes.evidence._storage_path", lambda: tmp_path)

        # FIX: we now stream to temp file then rename; patch rename to simulate disk failure
        original_rename = Path.rename
        def _fail_rename(self, target):
            raise OSError("Disk full")
        monkeypatch.setattr(Path, "rename", _fail_rename)

        cid = _make_case(client)
        _grant_consent(client, cid)
        resp = _upload(client, cid, JPEG_MAGIC, "fail.jpg", "image/jpeg")
        assert resp.status_code == 500
        assert resp.json()["detail"]["code"] == "STORAGE_ERROR"

        # DB must have no evidence row
        case_data = client.get(f"/api/cases/{cid}").json()
        assert len(case_data["evidence_items"]) == 0


# ── Delete evidence endpoint ──────────────────────────────────────────────────

class TestDeleteEvidence:

    def test_delete_evidence_returns_204(self, client, tmp_path, monkeypatch):
        monkeypatch.setattr("app.routes.evidence._storage_path", lambda: tmp_path)
        cid = _make_case(client)
        _grant_consent(client, cid)
        ev_id = _upload(client, cid, JPEG_MAGIC, "del.jpg", "image/jpeg").json()["id"]
        resp = client.delete(f"/api/cases/{cid}/evidence/{ev_id}")
        assert resp.status_code == 204

    def test_delete_evidence_removes_metadata(self, client, tmp_path, monkeypatch):
        monkeypatch.setattr("app.routes.evidence._storage_path", lambda: tmp_path)
        cid = _make_case(client)
        _grant_consent(client, cid)
        ev_id = _upload(client, cid, JPEG_MAGIC, "gone.jpg", "image/jpeg").json()["id"]
        client.delete(f"/api/cases/{cid}/evidence/{ev_id}")
        case_data = client.get(f"/api/cases/{cid}").json()
        assert all(e["id"] != ev_id for e in case_data["evidence_items"])

    def test_delete_evidence_removes_file(self, client, tmp_path, monkeypatch):
        monkeypatch.setattr("app.routes.evidence._storage_path", lambda: tmp_path)
        cid = _make_case(client)
        _grant_consent(client, cid)
        _upload(client, cid, JPEG_MAGIC, "rm.jpg", "image/jpeg")
        assert len(list(tmp_path.iterdir())) == 1
        ev_id = client.get(f"/api/cases/{cid}").json()["evidence_items"][0]["id"]
        client.delete(f"/api/cases/{cid}/evidence/{ev_id}")
        assert len(list(tmp_path.iterdir())) == 0

    def test_delete_nonexistent_evidence_returns_404(self, client, tmp_path, monkeypatch):
        monkeypatch.setattr("app.routes.evidence._storage_path", lambda: tmp_path)
        cid = _make_case(client)
        resp = client.delete(f"/api/cases/{cid}/evidence/00000000-0000-0000-0000-000000000000")
        assert resp.status_code == 404
        data = resp.json()
        # Global 404 handler returns {"error": ...}; HTTPException returns {"detail": ...}
        assert "error" in data or "detail" in data

    def test_delete_evidence_wrong_case_returns_404(self, client, tmp_path, monkeypatch):
        """Evidence from case A cannot be deleted via case B's URL."""
        monkeypatch.setattr("app.routes.evidence._storage_path", lambda: tmp_path)
        cid_a = _make_case(client)
        _grant_consent(client, cid_a)
        ev_id = _upload(client, cid_a, JPEG_MAGIC, "a.jpg", "image/jpeg").json()["id"]
        cid_b = _make_case(client)
        resp = client.delete(f"/api/cases/{cid_b}/evidence/{ev_id}")
        assert resp.status_code == 404

    def test_delete_missing_file_still_removes_metadata(self, client, tmp_path, monkeypatch):
        """If the stored file is already gone, metadata must still be removed cleanly."""
        monkeypatch.setattr("app.routes.evidence._storage_path", lambda: tmp_path)
        cid = _make_case(client)
        _grant_consent(client, cid)
        ev_id = _upload(client, cid, JPEG_MAGIC, "vanished.jpg", "image/jpeg").json()["id"]
        # Manually remove the stored file before calling the endpoint
        for f in tmp_path.iterdir():
            f.unlink()
        resp = client.delete(f"/api/cases/{cid}/evidence/{ev_id}")
        assert resp.status_code == 204
        case_data = client.get(f"/api/cases/{cid}").json()
        assert len(case_data["evidence_items"]) == 0


# ── PNG upload ────────────────────────────────────────────────────────────────

class TestPngUpload:

    def test_valid_png_accepted(self, client, tmp_path, monkeypatch):
        monkeypatch.setattr("app.routes.evidence._storage_path", lambda: tmp_path)
        cid = _make_case(client)
        _grant_consent(client, cid)
        resp = _upload(client, cid, PNG_MAGIC, "screenshot.png", "image/png")
        assert resp.status_code == 201
        assert resp.json()["media_type"] == "image/png"

    def test_png_magic_mismatch_rejected(self, client, tmp_path, monkeypatch):
        monkeypatch.setattr("app.routes.evidence._storage_path", lambda: tmp_path)
        cid = _make_case(client)
        _grant_consent(client, cid)
        resp = _upload(client, cid, JPEG_MAGIC, "trick.png", "image/png")
        assert resp.status_code == 415
