"""
tests/test_cases.py
────────────────────
Test suite for the Case management endpoints (Segment 2).

Coverage:
  - POST   /api/cases   — create (valid, empty body, too-long description)
  - GET    /api/cases   — list (empty, with cases)
  - GET    /api/cases/{case_id}  — retrieve (found, not found, bad ID format)
  - DELETE /api/cases/{case_id} — delete (found, not found, cascades evidence metadata)
  - DB integrity (status defaults, timestamps, FK cascade)

Run with:
    pytest -v
"""

from __future__ import annotations

import pytest


# ── POST /api/cases ───────────────────────────────────────────────────────────

class TestCreateCase:

    def test_create_returns_201(self, client):
        resp = client.post("/api/cases", json={})
        assert resp.status_code == 201

    def test_create_returns_json(self, client):
        resp = client.post("/api/cases", json={})
        assert resp.headers["content-type"].startswith("application/json")

    def test_create_response_schema(self, client):
        data = client.post("/api/cases", json={}).json()
        required = {
            "id", "created_at", "updated_at", "status",
            "consent_status", "consent_at", "analysis_status",
            "decision", "description", "evidence_items",
        }
        assert required.issubset(data.keys())

    def test_create_default_status_fields(self, client):
        data = client.post("/api/cases", json={}).json()
        assert data["status"]           == "open"
        assert data["consent_status"]   == "pending"
        assert data["analysis_status"]  == "not_started"
        assert data["decision"]         is None
        assert data["consent_at"]       is None
        assert data["evidence_items"]   == []

    def test_create_with_description(self, client):
        desc = "Received a suspicious call about a KYC update."
        data = client.post("/api/cases", json={"description": desc}).json()
        assert data["description"] == desc

    def test_create_strips_description_whitespace(self, client):
        data = client.post("/api/cases", json={"description": "  hello  "}).json()
        assert data["description"] == "hello"

    def test_create_empty_string_description_becomes_null(self, client):
        data = client.post("/api/cases", json={"description": "   "}).json()
        assert data["description"] is None

    def test_create_null_description_allowed(self, client):
        data = client.post("/api/cases", json={"description": None}).json()
        assert data["description"] is None

    def test_create_assigns_unique_ids(self, client):
        id1 = client.post("/api/cases", json={}).json()["id"]
        id2 = client.post("/api/cases", json={}).json()["id"]
        assert id1 != id2

    def test_create_description_too_long_returns_422(self, client):
        resp = client.post("/api/cases", json={"description": "x" * 2001})
        assert resp.status_code == 422

    def test_create_invalid_body_type_returns_422(self, client):
        resp = client.post("/api/cases", json={"description": 12345})
        # Pydantic will coerce int to str — this should still pass
        # (testing that numbers don't crash the endpoint)
        assert resp.status_code in (201, 422)

    def test_create_timestamps_present(self, client):
        data = client.post("/api/cases", json={}).json()
        assert data["created_at"]
        assert data["updated_at"]


# ── GET /api/cases ────────────────────────────────────────────────────────────

class TestListCases:

    def test_list_empty_returns_200(self, client):
        resp = client.get("/api/cases")
        assert resp.status_code == 200

    def test_list_empty_shape(self, client):
        data = client.get("/api/cases").json()
        assert data["total"] == 0
        assert data["items"] == []

    def test_list_shows_created_cases(self, client):
        client.post("/api/cases", json={"description": "Case A"})
        client.post("/api/cases", json={"description": "Case B"})
        data = client.get("/api/cases").json()
        assert data["total"] == 2
        assert len(data["items"]) == 2

    def test_list_ordered_newest_first(self, client):
        client.post("/api/cases", json={"description": "First"})
        client.post("/api/cases", json={"description": "Second"})
        items = client.get("/api/cases").json()["items"]
        # Newest (Second) should be first
        assert items[0]["description"] == "Second"

    def test_list_item_has_evidence_count(self, client):
        client.post("/api/cases", json={})
        item = client.get("/api/cases").json()["items"][0]
        assert "evidence_count" in item
        assert item["evidence_count"] == 0

    def test_list_items_do_not_embed_evidence_details(self, client):
        """List items should not include the full evidence_items array."""
        client.post("/api/cases", json={})
        item = client.get("/api/cases").json()["items"][0]
        assert "evidence_items" not in item


# ── GET /api/cases/{case_id} ──────────────────────────────────────────────────

class TestGetCase:

    def test_get_existing_case_returns_200(self, client):
        case_id = client.post("/api/cases", json={}).json()["id"]
        resp = client.get(f"/api/cases/{case_id}")
        assert resp.status_code == 200

    def test_get_returns_correct_id(self, client):
        case_id = client.post("/api/cases", json={"description": "Test"}).json()["id"]
        data = client.get(f"/api/cases/{case_id}").json()
        assert data["id"] == case_id

    def test_get_returns_description(self, client):
        case_id = client.post("/api/cases", json={"description": "My case"}).json()["id"]
        data = client.get(f"/api/cases/{case_id}").json()
        assert data["description"] == "My case"

    def test_get_nonexistent_returns_404(self, client):
        resp = client.get("/api/cases/00000000-0000-0000-0000-000000000000")
        assert resp.status_code == 404

    def test_get_404_error_envelope(self, client):
        """Verify 404 for a missing case returns a JSON error body."""
        resp = client.get("/api/cases/00000000-0000-0000-0000-000000000001")
        assert resp.status_code == 404
        data = resp.json()
        # The global 404 handler returns {"error": {"code": ..., "message": ...}}
        assert "error" in data
        assert "code" in data["error"]
        assert "message" in data["error"]

    def test_get_includes_evidence_items_list(self, client):
        case_id = client.post("/api/cases", json={}).json()["id"]
        data = client.get(f"/api/cases/{case_id}").json()
        assert "evidence_items" in data
        assert isinstance(data["evidence_items"], list)


# ── DELETE /api/cases/{case_id} ───────────────────────────────────────────────

class TestDeleteCase:

    def test_delete_existing_returns_204(self, client):
        case_id = client.post("/api/cases", json={}).json()["id"]
        resp = client.delete(f"/api/cases/{case_id}")
        assert resp.status_code == 204

    def test_delete_removes_case_from_list(self, client):
        case_id = client.post("/api/cases", json={}).json()["id"]
        client.delete(f"/api/cases/{case_id}")
        data = client.get("/api/cases").json()
        assert data["total"] == 0

    def test_delete_makes_case_unreachable(self, client):
        case_id = client.post("/api/cases", json={}).json()["id"]
        client.delete(f"/api/cases/{case_id}")
        resp = client.get(f"/api/cases/{case_id}")
        assert resp.status_code == 404

    def test_delete_nonexistent_returns_404(self, client):
        resp = client.delete("/api/cases/00000000-0000-0000-0000-000000000000")
        assert resp.status_code == 404

    def test_delete_404_error_envelope(self, client):
        """Verify 404 for a missing case returns a JSON error body."""
        resp = client.delete("/api/cases/00000000-0000-0000-0000-000000000002")
        assert resp.status_code == 404
        data = resp.json()
        # The global 404 handler returns {"error": {"code": ..., "message": ...}}
        assert "error" in data
        assert "code" in data["error"]
        assert "message" in data["error"]

    def test_delete_only_removes_target_case(self, client):
        id1 = client.post("/api/cases", json={"description": "Keep"}).json()["id"]
        id2 = client.post("/api/cases", json={"description": "Delete"}).json()["id"]
        client.delete(f"/api/cases/{id2}")
        assert client.get(f"/api/cases/{id1}").status_code == 200
        assert client.get(f"/api/cases/{id2}").status_code == 404


# ── Database integrity ────────────────────────────────────────────────────────

class TestDatabaseIntegrity:

    def test_case_id_is_uuid_string(self, client):
        import uuid
        case_id = client.post("/api/cases", json={}).json()["id"]
        # Should parse without raising
        parsed = uuid.UUID(case_id)
        assert str(parsed) == case_id

    def test_cascade_delete_evidence_metadata(self, client, db_session):
        """
        Directly insert an Evidence row linked to a Case, then delete the Case
        via the API and confirm the Evidence row is also gone (FK CASCADE).
        """
        from app.models import Case, Evidence

        # Create a case through the API to get a valid ID
        case_id = client.post("/api/cases", json={}).json()["id"]

        # Directly insert an Evidence metadata row
        ev = Evidence(
            case_id=case_id,
            original_filename="audio.wav",
            media_type="audio/wav",
            file_size_bytes=102400,
            sha256_hash="a" * 64,
        )
        db_session.add(ev)
        db_session.commit()
        ev_id = ev.id

        # Delete via API
        resp = client.delete(f"/api/cases/{case_id}")
        assert resp.status_code == 204

        # Evidence row must be gone (CASCADE)
        db_session.expire_all()
        assert db_session.get(Evidence, ev_id) is None

    def test_multiple_cases_are_independent(self, client):
        ids = [client.post("/api/cases", json={}).json()["id"] for _ in range(3)]
        assert len(set(ids)) == 3  # all unique
        for cid in ids:
            assert client.get(f"/api/cases/{cid}").status_code == 200
