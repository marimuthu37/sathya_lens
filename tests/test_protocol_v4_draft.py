"""
test_protocol_v4_draft.py — Protocol v4 approval state (2026-10-24).

The review draft stays unchanged and marked DRAFT; the approved protocol
docs/protocol_v4.json is hashed, records the Q1-Q5 decisions and the G4
grouping policy, and keeps Mendeley out of the test and coverage.
Protocol v3 is unchanged.
"""
import hashlib
import json
from pathlib import Path

from satyalens_analysis.anonymise import ANONYMISER_V2_VERSION

REPO = Path(__file__).resolve().parent.parent
DRAFT = REPO / "docs" / "protocol_v4_DRAFT.json"
V4 = REPO / "docs" / "protocol_v4.json"
V4_HASH = REPO / "docs" / "protocol_v4.sha256"


def _load(p):
    return json.loads(p.read_text(encoding="utf-8"))


def test_draft_still_marked_draft():
    d = _load(DRAFT)
    assert d["protocol_version"].endswith("-DRAFT") and d["status"].startswith("DRAFT")


def test_v4_hash_recorded_and_unchanged():
    recorded = V4_HASH.read_text(encoding="utf-8").split()[0]
    assert hashlib.sha256(V4.read_bytes()).hexdigest() == recorded
    assert _load(V4)["protocol_version"] == "4.0"


def test_v3_unchanged():
    v3 = (REPO / "docs" / "protocol_v3.json").read_bytes()
    assert hashlib.sha256(v3).hexdigest() == (REPO / "docs" / "protocol_v3.sha256").read_text().split()[0]


def test_decisions_recorded():
    p = _load(V4)
    assert set(p["decisions"]) == {"Q1", "Q2", "Q3", "Q4", "Q5"}
    assert p["sources"]["IMC25"]["label_mapping"]["hey mum/dad"] == "suspicious"
    assert any("hey mum/dad" in x for x in p["acceptance_criteria"]["reported_not_gating"])
    assert p["anonymisation"]["version"] == ANONYMISER_V2_VERSION
    assert p["test_assignment"]["fraction"] == 0.20


def test_mendeley_train_only_and_never_genuine_alert_evidence():
    m = _load(V4)["sources"]["MENDELEY"]
    assert m["roles"] == ["train"]
    assert "never counted toward minimum_legitimate_coverage" in m["restrictions"]
    assert "never in the frozen test" in m["restrictions"]
    assert "NEVER treated" in m["label_limitations"]
    assert "Latin-script" in m["script_filter"]


def test_thresholds_identical_to_v3():
    v3, v4 = _load(REPO / "docs" / "protocol_v3.json"), _load(V4)
    for k in ("S1", "S2", "L1", "L2", "M1", "B1"):
        assert v3["acceptance_criteria"][k] == v4["acceptance_criteria"][k]
    assert v3["minimum_legitimate_coverage"]["test_requirement"] == v4["minimum_legitimate_coverage"]["test_requirement"]
    assert set(v3["model_selection"]["candidates"]) == set(v4["model_selection"]["candidates"])


def test_grouping_policy_g4_specified():
    g = _load(V4)["grouping"]
    assert g["policy_id"] == "G4"
    for k in ("step_1_atomic_units", "step_2_giant_units", "step_3_assignment", "step_4_purge",
              "rationale", "trade_offs"):
        assert g[k]


def test_only_v4_tooling_reads_protocol_v4():
    # protocol v4 required_code_changes: builder (before dry run), select/acceptance (before selection)
    allowed = {"build_pool_and_test_v4.py", "select_text_model_v4.py", "acceptance_v4.py"}
    for script in (REPO / "scripts").glob("*.py"):
        if script.name not in allowed:
            assert "protocol_v4.json" not in script.read_text(encoding="utf-8"), script.name
