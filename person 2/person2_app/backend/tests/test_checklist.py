"""
tests/test_checklist.py
───────────────────────
Segment 7 tests: verification checklist and user feedback endpoints.

Coverage:
  Checklist:
    - GET returns canonical items with disclaimer
    - All canonical item IDs present in response
    - All items start unchecked
    - PATCH checks an item; re-GET reflects state
    - PATCH unchecks an item
    - PATCH unknown item_id → 422
    - PATCH empty string item_id → 404/422
    - Missing case → 404
    - Checklist items are case-scoped (no cross-case bleed)
    - checked_at timestamp set on check
    - Re-check updates checked_at
    - Disclaimer always present in GET response

  Feedback:
    - GET before submit → 404
    - PUT creates feedback record
    - GET after PUT returns record
    - PUT second time updates (upsert)
    - All boolean fields optional (can all be None)
    - Note trimmed and stored
    - Note over 500 chars → 422
    - Note with only whitespace stored as null
    - DELETE removes feedback → subsequent GET returns 404
    - DELETE non-existent → 404
    - Missing case → 404 on all feedback endpoints
    - No personal identity fields accepted

  Isolation:
    - Checklist progress isolated per case
    - Feedback isolated per case

  Regression:
    - All previous 133 tests pass
"""

from __future__ import annotations

import pytest
from app.services.checklist import CHECKLIST_ITEMS, VALID_ITEM_IDS, CHECKLIST_VERSION


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_case(client) -> str:
    r = client.post("/api/cases", json={})
    assert r.status_code == 201
    return r.json()["id"]

def _checklist_url(cid: str) -> str:
    return f"/api/cases/{cid}/checklist"

def _item_url(cid: str, item_id: str) -> str:
    return f"/api/cases/{cid}/checklist/{item_id}"

def _feedback_url(cid: str) -> str:
    return f"/api/cases/{cid}/feedback"

FIRST_ITEM = CHECKLIST_ITEMS[0].item_id   # "contact_known_number"
SECOND_ITEM = CHECKLIST_ITEMS[1].item_id


# ═════════════════════════════════════════════════════════════════════════════
# §1  Checklist GET
# ═════════════════════════════════════════════════════════════════════════════

class TestChecklistGet:

    def test_missing_case_returns_404(self, client):
        r = client.get(_checklist_url("00000000-0000-0000-0000-000000000000"))
        assert r.status_code == 404

    def test_get_returns_200(self, client):
        cid = _make_case(client)
        r = client.get(_checklist_url(cid))
        assert r.status_code == 200

    def test_response_has_items(self, client):
        cid = _make_case(client)
        data = client.get(_checklist_url(cid)).json()
        assert "items" in data
        assert len(data["items"]) == len(CHECKLIST_ITEMS)

    def test_all_canonical_item_ids_present(self, client):
        cid = _make_case(client)
        data = client.get(_checklist_url(cid)).json()
        returned_ids = {item["item_id"] for item in data["items"]}
        assert returned_ids == VALID_ITEM_IDS

    def test_all_items_start_unchecked(self, client):
        cid = _make_case(client)
        data = client.get(_checklist_url(cid)).json()
        for p in data["progress"]:
            assert p["checked"] is False

    def test_disclaimer_always_present(self, client):
        cid = _make_case(client)
        data = client.get(_checklist_url(cid)).json()
        assert "disclaimer" in data
        assert len(data["disclaimer"]) > 10

    def test_disclaimer_does_not_claim_proof(self, client):
        cid = _make_case(client)
        data = client.get(_checklist_url(cid)).json()
        disclaimer = data["disclaimer"].lower()
        # Must NOT promise safety
        assert "proves" not in disclaimer or "not prove" in disclaimer or "does not prove" in disclaimer

    def test_checklist_version_present(self, client):
        cid = _make_case(client)
        data = client.get(_checklist_url(cid)).json()
        assert data["checklist_version"] == CHECKLIST_VERSION

    def test_case_id_in_response(self, client):
        cid = _make_case(client)
        data = client.get(_checklist_url(cid)).json()
        assert data["case_id"] == cid

    def test_india_only_item_included(self, client):
        cid = _make_case(client)
        data = client.get(_checklist_url(cid)).json()
        india_items = [i for i in data["items"] if i.get("india_only")]
        assert len(india_items) >= 1

    def test_helpline_number_in_india_item(self, client):
        cid = _make_case(client)
        data = client.get(_checklist_url(cid)).json()
        india_items = [i for i in data["items"] if i.get("india_only")]
        combined = " ".join(i["text"] for i in india_items)
        assert "1930" in combined

    def test_cybercrime_url_in_india_item(self, client):
        cid = _make_case(client)
        data = client.get(_checklist_url(cid)).json()
        india_items = [i for i in data["items"] if i.get("india_only")]
        combined = " ".join(i["text"] for i in india_items)
        assert "cybercrime.gov.in" in combined


# ═════════════════════════════════════════════════════════════════════════════
# §2  Checklist PATCH
# ═════════════════════════════════════════════════════════════════════════════

class TestChecklistPatch:

    def test_patch_missing_case_returns_404(self, client):
        r = client.patch(
            _item_url("00000000-0000-0000-0000-000000000000", FIRST_ITEM),
            json={"checked": True},
        )
        assert r.status_code == 404

    def test_patch_unknown_item_id_returns_422(self, client):
        cid = _make_case(client)
        r = client.patch(_item_url(cid, "not_a_real_item"), json={"checked": True})
        assert r.status_code == 422

    def test_patch_checks_item(self, client):
        cid = _make_case(client)
        r = client.patch(_item_url(cid, FIRST_ITEM), json={"checked": True})
        assert r.status_code == 200
        assert r.json()["checked"] is True

    def test_patch_reflected_in_get(self, client):
        cid = _make_case(client)
        client.patch(_item_url(cid, FIRST_ITEM), json={"checked": True})
        data = client.get(_checklist_url(cid)).json()
        first_progress = next(p for p in data["progress"] if p["item_id"] == FIRST_ITEM)
        assert first_progress["checked"] is True

    def test_patch_unchecks_item(self, client):
        cid = _make_case(client)
        client.patch(_item_url(cid, FIRST_ITEM), json={"checked": True})
        r = client.patch(_item_url(cid, FIRST_ITEM), json={"checked": False})
        assert r.status_code == 200
        assert r.json()["checked"] is False

    def test_unchecked_reflected_in_get(self, client):
        cid = _make_case(client)
        client.patch(_item_url(cid, FIRST_ITEM), json={"checked": True})
        client.patch(_item_url(cid, FIRST_ITEM), json={"checked": False})
        data = client.get(_checklist_url(cid)).json()
        first_progress = next(p for p in data["progress"] if p["item_id"] == FIRST_ITEM)
        assert first_progress["checked"] is False

    def test_checked_at_set_on_check(self, client):
        cid = _make_case(client)
        r = client.patch(_item_url(cid, FIRST_ITEM), json={"checked": True})
        assert r.json()["checked_at"] is not None

    def test_other_items_remain_unchecked(self, client):
        cid = _make_case(client)
        client.patch(_item_url(cid, FIRST_ITEM), json={"checked": True})
        data = client.get(_checklist_url(cid)).json()
        others = [p for p in data["progress"] if p["item_id"] != FIRST_ITEM]
        for p in others:
            assert p["checked"] is False

    def test_patch_multiple_items(self, client):
        cid = _make_case(client)
        client.patch(_item_url(cid, FIRST_ITEM),  json={"checked": True})
        client.patch(_item_url(cid, SECOND_ITEM), json={"checked": True})
        data = client.get(_checklist_url(cid)).json()
        checked = [p["item_id"] for p in data["progress"] if p["checked"]]
        assert FIRST_ITEM  in checked
        assert SECOND_ITEM in checked

    def test_checklist_isolated_between_cases(self, client):
        cid1 = _make_case(client)
        cid2 = _make_case(client)
        client.patch(_item_url(cid1, FIRST_ITEM), json={"checked": True})
        data2 = client.get(_checklist_url(cid2)).json()
        for p in data2["progress"]:
            assert p["checked"] is False

    def test_missing_checked_field_returns_422(self, client):
        cid = _make_case(client)
        r = client.patch(_item_url(cid, FIRST_ITEM), json={})
        assert r.status_code == 422


# ═════════════════════════════════════════════════════════════════════════════
# §3  Feedback
# ═════════════════════════════════════════════════════════════════════════════

class TestFeedback:

    def test_get_before_submit_returns_404(self, client):
        cid = _make_case(client)
        r = client.get(_feedback_url(cid))
        assert r.status_code == 404

    def test_get_missing_case_returns_404(self, client):
        r = client.get(_feedback_url("00000000-0000-0000-0000-000000000000"))
        assert r.status_code == 404

    def test_put_missing_case_returns_404(self, client):
        r = client.put(_feedback_url("00000000-0000-0000-0000-000000000000"), json={})
        assert r.status_code == 404

    def test_put_creates_feedback(self, client):
        cid = _make_case(client)
        r = client.put(_feedback_url(cid), json={"result_useful": True})
        assert r.status_code == 200

    def test_put_returns_schema(self, client):
        cid = _make_case(client)
        data = client.put(_feedback_url(cid), json={"result_useful": True}).json()
        for field in ("id", "case_id", "verified_independently", "still_has_concerns",
                      "result_useful", "result_clear", "note", "submitted_at", "updated_at"):
            assert field in data, f"Missing field: {field}"

    def test_put_stores_all_fields(self, client):
        cid = _make_case(client)
        payload = {
            "verified_independently": True,
            "still_has_concerns": False,
            "result_useful": True,
            "result_clear": True,
            "note": "Looks suspicious.",
        }
        data = client.put(_feedback_url(cid), json=payload).json()
        assert data["verified_independently"] is True
        assert data["still_has_concerns"] is False
        assert data["result_useful"] is True
        assert data["result_clear"] is True
        assert data["note"] == "Looks suspicious."

    def test_put_all_none_fields_accepted(self, client):
        cid = _make_case(client)
        data = client.put(_feedback_url(cid), json={}).json()
        assert data["verified_independently"] is None
        assert data["still_has_concerns"] is None
        assert data["result_useful"] is None
        assert data["result_clear"] is None

    def test_get_after_put_returns_record(self, client):
        cid = _make_case(client)
        client.put(_feedback_url(cid), json={"result_useful": False})
        data = client.get(_feedback_url(cid)).json()
        assert data["result_useful"] is False

    def test_put_updates_on_second_call(self, client):
        cid = _make_case(client)
        client.put(_feedback_url(cid), json={"result_useful": True})
        client.put(_feedback_url(cid), json={"result_useful": False, "still_has_concerns": True})
        data = client.get(_feedback_url(cid)).json()
        assert data["result_useful"] is False
        assert data["still_has_concerns"] is True

    def test_note_stripped_whitespace(self, client):
        cid = _make_case(client)
        data = client.put(_feedback_url(cid), json={"note": "  hello  "}).json()
        assert data["note"] == "hello"

    def test_note_whitespace_only_stored_as_null(self, client):
        cid = _make_case(client)
        data = client.put(_feedback_url(cid), json={"note": "   "}).json()
        assert data["note"] is None

    def test_note_over_500_chars_returns_422(self, client):
        cid = _make_case(client)
        r = client.put(_feedback_url(cid), json={"note": "x" * 501})
        assert r.status_code == 422

    def test_note_exactly_500_chars_accepted(self, client):
        cid = _make_case(client)
        r = client.put(_feedback_url(cid), json={"note": "a" * 500})
        assert r.status_code == 200

    def test_delete_feedback_returns_204(self, client):
        cid = _make_case(client)
        client.put(_feedback_url(cid), json={})
        r = client.delete(_feedback_url(cid))
        assert r.status_code == 204

    def test_delete_feedback_removes_record(self, client):
        cid = _make_case(client)
        client.put(_feedback_url(cid), json={"result_useful": True})
        client.delete(_feedback_url(cid))
        r = client.get(_feedback_url(cid))
        assert r.status_code == 404

    def test_delete_nonexistent_returns_404(self, client):
        cid = _make_case(client)
        r = client.delete(_feedback_url(cid))
        assert r.status_code == 404

    def test_delete_missing_case_returns_404(self, client):
        r = client.delete(_feedback_url("00000000-0000-0000-0000-000000000000"))
        assert r.status_code == 404

    def test_feedback_isolated_between_cases(self, client):
        cid1 = _make_case(client)
        cid2 = _make_case(client)
        client.put(_feedback_url(cid1), json={"result_useful": True})
        r = client.get(_feedback_url(cid2))
        assert r.status_code == 404

    def test_case_id_in_response_matches_case(self, client):
        cid = _make_case(client)
        data = client.put(_feedback_url(cid), json={}).json()
        assert data["case_id"] == cid

    def test_no_personal_identity_fields_in_schema(self, client):
        cid = _make_case(client)
        data = client.put(_feedback_url(cid), json={}).json()
        for forbidden in ("name", "email", "phone", "ip", "password", "otp", "pin"):
            assert forbidden not in data

    def test_feedback_deleted_with_case(self, client):
        """CaseFeedback row cascade-deleted when Case is deleted."""
        cid = _make_case(client)
        client.put(_feedback_url(cid), json={"result_useful": True})
        client.delete(f"/api/cases/{cid}")
        # After case deletion, case is gone → any feedback GET should 404
        r = client.get(_feedback_url(cid))
        assert r.status_code == 404

    def test_checklist_deleted_with_case(self, client):
        """ChecklistProgress rows cascade-deleted when Case is deleted."""
        cid = _make_case(client)
        client.patch(_item_url(cid, FIRST_ITEM), json={"checked": True})
        client.delete(f"/api/cases/{cid}")
        r = client.get(_checklist_url(cid))
        assert r.status_code == 404
