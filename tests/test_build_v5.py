"""
test_build_v5.py — Protocol v5 builder: pin, unchanged v4 build steps, v5 names,
manifest fields from the protocol, and the v5-vs-v4 dry-run equality (counts only).
No pool, test set or manifest is written in the real repository.
"""
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from tests.seal_state_v5 import SealStateError, check_seal_state

REPO = Path(__file__).resolve().parent.parent
PIN = "3889099d1da7fa6cdc987685a2dacda4592508339bd8cedb91a99fa29f25f3ee"
V4_DRY_RUN_SHA = "99b29db0629873f56b3a454afcd5c46f30fbacd1732bc417e488b49763778a15"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, REPO / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


@pytest.fixture(scope="module")
def b5():
    return _load("build_pool_and_test_v5")


class TestPinAndPaths:
    def test_pin_matches_protocol_and_record(self, b5):
        assert b5.PROTOCOL_V5_SHA256 == PIN == _sha(REPO / "docs" / "protocol_v5.json")
        p = b5.load_protocol()
        assert p["protocol_version"] == "5.0" and p["_sha256"] == PIN and p["grouping"]["policy_id"] == "G4"

    def test_tampered_protocol_refused(self, b5, tmp_path, monkeypatch):
        fake = tmp_path / "p.json"
        fake.write_text("{}", encoding="utf-8")
        monkeypatch.setattr(b5, "PROTOCOL", fake)
        with pytest.raises(b5.StopBuild, match="pinned"):
            b5.load_protocol()

    def test_tampered_sha256_record_refused(self, b5, tmp_path, monkeypatch):
        rec = tmp_path / "p.sha256"
        rec.write_text("0" * 64 + "  docs/protocol_v5.json\n", encoding="utf-8")
        monkeypatch.setattr(b5, "PROTOCOL_HASH", rec)
        with pytest.raises(b5.StopBuild, match="pinned"):
            b5.load_protocol()

    def test_missing_protocol_refused(self, b5, tmp_path, monkeypatch):
        monkeypatch.setattr(b5, "PROTOCOL", tmp_path / "absent.json")
        with pytest.raises(b5.StopBuild, match="not found"):
            b5.load_protocol()

    def test_v5_names(self, b5):
        assert b5.POOL_OUT.name == "training_pool_v5.csv" and b5.MANIFEST.name == "frozen_test_v5_manifest.json"
        assert b5.REPORT_DIR.name == "build_v5" and b5.EXCLUDED_OUT.name.endswith(".excluded.csv")
        assert b5.sealing_paths(b5.load_protocol()) == {
            "test_file": "data/frozen_test_v5.csv", "protocol_file": "docs/protocol_v5.json",
            "evaluation_log": "reports/frozen_test_v5_evaluations.jsonl"}

    def test_builder_does_not_name_the_sealed_file_or_v4_protocol(self):
        src = (REPO / "scripts" / "build_pool_and_test_v5.py").read_text(encoding="utf-8")
        assert "frozen_test_v5.csv" not in src and "protocol_v4.json" not in src


class TestIdenticalDataConstruction:
    def test_build_steps_are_the_unchanged_v4_functions(self, b5):
        src = (REPO / "scripts" / "build_pool_and_test_v5.py").read_text(encoding="utf-8")
        for fn in ("verify_sources", "load_mendeley", "dedupe", "atomic_units", "assign", "purge", "_token_rates"):
            assert f"v4.{fn}(" in src, fn
            assert f"def {fn}(" not in src, fn                 # no re-implementation in v5
        assert b5.v4.__file__.endswith("build_pool_and_test_v4.py")

    def test_salt_and_fraction_identical_to_v4(self, b5):
        v4p = json.loads((REPO / "docs" / "protocol_v4.json").read_text(encoding="utf-8"))
        v5p = b5.load_protocol()
        assert v5p["test_assignment"] == v4p["test_assignment"] == {
            "salt": "satyalens-protocol-v4-2026-10-24", "fraction": 0.20}
        for k in ("sources", "anonymisation", "build_order", "grouping", "minimum_legitimate_coverage"):
            assert v5p[k] == v4p[k], k

    @pytest.mark.skipif(not (REPO / "reports" / "build_v5" / "build_report_dry_run.json").is_file(),
                        reason="v5 dry run not run on this machine")
    def test_v5_dry_run_equals_reviewed_v4_dry_run(self):
        v4r = REPO / "reports" / "build_v4" / "build_report_dry_run.json"
        assert _sha(v4r) == V4_DRY_RUN_SHA                     # the reviewed v4 dry run is unchanged
        a = json.loads(v4r.read_text(encoding="utf-8"))
        b = json.loads((REPO / "reports" / "build_v5" / "build_report_dry_run.json").read_text(encoding="utf-8"))
        assert list(a) == list(b)
        assert {k for k in a if a[k] != b[k]} <= {"protocol", "protocol_sha256", "built_utc"}
        assert (a["protocol"], b["protocol"], b["protocol_sha256"], b["dry_run"]) == ("v4", "v5", PIN, True)
        assert b["coverage"]["met"] is False and b["coverage"]["L1_status"] == "NOT EVALUABLE"


class TestManifestFromProtocol:
    def test_write_outputs_in_tmp_only(self, b5, tmp_path, monkeypatch):
        (tmp_path / "data").mkdir()
        (tmp_path / "reports").mkdir()
        for name, val in (("REPO", tmp_path), ("POOL_OUT", tmp_path / "data" / "training_pool_v5.csv"),
                          ("EXCLUDED_OUT", tmp_path / "data" / "v5_build.excluded.csv"),
                          ("MANIFEST", tmp_path / "reports" / "frozen_test_v5_manifest.json")):
            monkeypatch.setattr(b5, name, val)
        p = b5.load_protocol()
        row = lambda t, lab, s, d="": {"text": t, "label": lab, "group": f"{s}:1", "source": s, "key": "k",  # noqa: E731
                                       "category": "c", "india_linked": False, "donor": d}
        report = {"test_counts": {"IMC25": {"suspicious": 1}}, "test_units": 1,
                  "coverage": {"met": False, "L1_status": "NOT EVALUABLE"},
                  "pool_counts": {"MWOZ_TRAIN": {"legitimate": 1}}}
        b5._write_outputs(p, b5.sealing_paths(p), [row("a b c", "legitimate", "MWOZ_TRAIN")],
                          [row("x y z", "suspicious", "IMC25")], [], report)
        m = json.loads((tmp_path / "reports" / "frozen_test_v5_manifest.json").read_text(encoding="utf-8"))
        assert m["protocol_file"] == "docs/protocol_v5.json" and m["protocol_sha256"] == PIN
        assert m["evaluation_log"] == "reports/frozen_test_v5_evaluations.jsonl"
        assert m["sealed_files"][0]["path"] == "data/frozen_test_v5.csv"
        assert m["sealed_files"][0]["sha256"] == _sha(tmp_path / "data" / "frozen_test_v5.csv")
        assert m["pool_counts"] == {"MWOZ_TRAIN": {"legitimate": 1}} and m["coverage"]["met"] is False
        assert m["pool_csv_sha256"] == _sha(tmp_path / "data" / "training_pool_v5.csv")
        # The tmp write never touches the real repository, which must be clean or validly sealed.
        assert check_seal_state(REPO) in ("clean", "sealed")

    def test_seal_refuses_to_overwrite_existing_manifest(self):
        src = (REPO / "scripts" / "build_pool_and_test_v5.py").read_text(encoding="utf-8")
        assert "A v5 sealed manifest already exists; refusing to overwrite." in src


# ---------------------------------------------------------------------------
# Seal-state check (tests/seal_state_v5.py): clean pre-seal OR valid post-seal
# ---------------------------------------------------------------------------

class TestSealStateCheck:
    def _sealed(self, root: Path, **over):
        (root / "data").mkdir(parents=True, exist_ok=True)
        (root / "reports").mkdir(parents=True, exist_ok=True)
        test = root / "data" / "frozen_test_v5.csv"
        pool = root / "data" / "training_pool_v5.csv"
        test.write_text("id,text,label\nT5-000000,toy,suspicious\n", encoding="utf-8")
        pool.write_text("id,text,label,group\nP5-000000,toy pool,legitimate,g\n", encoding="utf-8")
        m = {"protocol_file": "docs/protocol_v5.json", "protocol_sha256": PIN,
             "sealed_files": [{"path": "data/frozen_test_v5.csv", "sha256": _sha(test)}],
             "evaluation_log": "reports/frozen_test_v5_evaluations.jsonl",
             "pool_counts": {"MWOZ_TRAIN": {"legitimate": 1}}, "pool_csv_sha256": _sha(pool)}
        m.update(over)
        (root / "reports" / "frozen_test_v5_manifest.json").write_text(json.dumps(m), encoding="utf-8")
        return test, pool, root / "reports" / "frozen_test_v5_manifest.json"

    def test_clean_and_sealed_states_accepted(self, tmp_path):
        assert check_seal_state(tmp_path) == "clean"
        self._sealed(tmp_path)
        assert check_seal_state(tmp_path) == "sealed"

    @pytest.mark.parametrize("rel", ["data/frozen_test_v5.csv", "data/training_pool_v5.csv",
                                     "reports/frozen_test_v5_evaluations.jsonl", "reports/build_v5/build_report.json"])
    def test_outputs_without_manifest_rejected(self, tmp_path, rel):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text("x", encoding="utf-8")
        with pytest.raises(SealStateError, match="without a sealed manifest"):
            check_seal_state(tmp_path)

    @pytest.mark.parametrize("content", ["{not json", "[]", "\"x\""])
    def test_malformed_manifest_rejected(self, tmp_path, content):
        self._sealed(tmp_path)
        (tmp_path / "reports" / "frozen_test_v5_manifest.json").write_text(content, encoding="utf-8")
        with pytest.raises(SealStateError, match="malformed|not a JSON object"):
            check_seal_state(tmp_path)

    def test_missing_test_or_pool_rejected(self, tmp_path):
        test, pool, _ = self._sealed(tmp_path)
        test.unlink()
        with pytest.raises(SealStateError, match="frozen_test_v5.csv is missing"):
            check_seal_state(tmp_path)
        self._sealed(tmp_path)
        pool.unlink()
        with pytest.raises(SealStateError, match="training_pool_v5.csv is missing"):
            check_seal_state(tmp_path)

    def test_mismatched_hashes_rejected(self, tmp_path):
        test, pool, _ = self._sealed(tmp_path)
        test.write_text(test.read_text(encoding="utf-8") + "T5-000001,extra,legitimate\n", encoding="utf-8")
        with pytest.raises(SealStateError, match="frozen_test_v5.csv SHA-256"):
            check_seal_state(tmp_path)
        self._sealed(tmp_path)
        pool.write_text(pool.read_text(encoding="utf-8") + "P5-000001,more,legitimate,g\n", encoding="utf-8")
        with pytest.raises(SealStateError, match="training_pool_v5.csv SHA-256"):
            check_seal_state(tmp_path)

    @pytest.mark.parametrize("over, msg", [
        ({"protocol_sha256": "0" * 64}, "protocol_sha256"),
        ({"protocol_file": "docs/protocol_v4.json"}, "protocol_file"),
        ({"evaluation_log": "reports/other.jsonl"}, "evaluation_log"),
        ({"sealed_files": []}, "exactly one sealed file"),
        ({"sealed_files": [{"path": "data/other.csv", "sha256": "x"}]}, "exactly one sealed file"),
        ({"pool_csv_sha256": None}, "pool_csv_sha256"),
        ({"pool_counts": None}, "pool_counts"),
    ])
    def test_wrong_manifest_fields_rejected(self, tmp_path, over, msg):
        self._sealed(tmp_path, **over)
        with pytest.raises(SealStateError, match=msg):
            check_seal_state(tmp_path)

    def test_real_repository_is_clean_or_validly_sealed_and_guarded(self):
        state = check_seal_state(REPO)
        assert state in ("clean", "sealed")
        if state == "sealed":
            from satyalens_analysis import text_classifier as tc
            assert tc.is_sealed(REPO / "data" / "frozen_test_v5.csv")
            with pytest.raises(tc.SealedDataError):
                tc.load_dataset(REPO / "data" / "frozen_test_v5.csv")
