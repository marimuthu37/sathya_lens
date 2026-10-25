"""
tests/test_case_status.py
─────────────────────────
Regression: Case.analysis_status was never updated (always "not_started"), and
an INSUFFICIENT_EVIDENCE decision was indistinguishable from "no decision" in
CaseRead. Found in the live browser run (status overview contradicted the
completed analysis shown below it).
"""

from __future__ import annotations

import io

from tests.test_expected_payment import GENERIC, JPEG, LEGIT, _analyse, _payment, _setup


def _case(client, cid):
    return client.get(f"/api/cases/{cid}").json()


def test_analysis_status_follows_evidence(client, tmp_path, monkeypatch):
    cid, eid, _ = _setup(client, tmp_path, monkeypatch, _payment(LEGIT))
    assert _case(client, cid)["analysis_status"] == "not_started"
    assert _analyse(client, cid, eid).status_code == 202
    assert _case(client, cid)["analysis_status"] == "done"

    # A second, unanalysed item → only partly analysed
    r = client.post(f"/api/cases/{cid}/evidence", files={"file": ("b.jpg", io.BytesIO(JPEG), "image/jpeg")})
    eid2 = r.json()["id"]
    assert _case(client, cid)["analysis_status"] == "queued"

    # Deleting it → fully analysed again
    assert client.delete(f"/api/cases/{cid}/evidence/{eid2}").status_code == 204
    assert _case(client, cid)["analysis_status"] == "done"


def test_failed_analysis_marks_case_failed(client, tmp_path, monkeypatch):
    failed = {"status": "failed", "raw_text": "", "fields": [], "mismatches": [],
              "limitations": ["OCR analysis error"]}
    cid, eid, _ = _setup(client, tmp_path, monkeypatch, failed)
    assert _analyse(client, cid, eid).status_code == 500
    assert _case(client, cid)["analysis_status"] == "failed"


def test_insufficient_evidence_is_read_from_the_decision_endpoint(client, tmp_path, monkeypatch):
    # CaseRead keeps decision=None for INSUFFICIENT_EVIDENCE (decision_computed stays
    # internal by design, see test_migration); the UI reads GET .../decision instead.
    cid, eid, _ = _setup(client, tmp_path, monkeypatch, _payment(LEGIT))
    assert client.get(f"/api/cases/{cid}/decision").status_code == 404
    _analyse(client, cid, eid)
    assert client.post(f"/api/cases/{cid}/decision").json()["category"] == "insufficient_evidence"
    assert _case(client, cid)["decision"] is None
    assert client.get(f"/api/cases/{cid}/decision").json()["category"] == "insufficient_evidence"


def test_verify_first_decision_persisted(client, tmp_path, monkeypatch):
    cid, eid, _ = _setup(client, tmp_path, monkeypatch, _payment(GENERIC))
    _analyse(client, cid, eid)
    client.post(f"/api/cases/{cid}/decision")
    c = _case(client, cid)
    assert c["decision"] == "verify_first"
    assert c["evidence_items"][0]["processing_status"] == "processed"
