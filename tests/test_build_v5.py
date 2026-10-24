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


# ---------------------------------------------------------------------------
# Repeated --seal: refuse before ANY write (build report included)
# ---------------------------------------------------------------------------

class TestRepeatedSealRefused:
    OUTPUTS = ("reports/build_v5/build_report.json", "data/training_pool_v5.csv",
               "data/training_pool_v5.provenance.json", "data/frozen_test_v5.csv",
               "data/v5_build.excluded.csv", "reports/frozen_test_v5_manifest.json")

    @pytest.fixture()
    def tmp_layout(self, b5, tmp_path, monkeypatch):
        for name, val in (("REPO", tmp_path), ("POOL_OUT", tmp_path / "data" / "training_pool_v5.csv"),
                          ("EXCLUDED_OUT", tmp_path / "data" / "v5_build.excluded.csv"),
                          ("REPORT_DIR", tmp_path / "reports" / "build_v5"),
                          ("MANIFEST", tmp_path / "reports" / "frozen_test_v5_manifest.json")):
            monkeypatch.setattr(b5, name, val)
        return tmp_path

    def _sealed(self, root):
        for rel in self.OUTPUTS:
            f = root / rel
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_bytes(f"sealed content of {rel}\n".encode())
        return {rel: ((root / rel).read_bytes(), (root / rel).stat().st_mtime_ns) for rel in self.OUTPUTS}

    def _unchanged(self, root, before):
        for rel, (data, mtime) in before.items():
            assert (root / rel).read_bytes() == data, rel
            assert (root / rel).stat().st_mtime_ns == mtime, rel

    def test_second_seal_refuses_before_reading_sources_and_changes_nothing(self, b5, tmp_layout, monkeypatch):
        before = self._sealed(tmp_layout)

        def must_not_run(*a, **k):
            raise AssertionError("a repeated seal must refuse before loading the protocol or sources")

        monkeypatch.setattr(b5, "load_protocol", must_not_run)
        monkeypatch.setattr(b5.v4, "verify_sources", must_not_run)
        with pytest.raises(b5.StopBuild, match="already exists; refusing to overwrite"):
            b5.build(seal=True)
        self._unchanged(tmp_layout, before)

    def test_cli_second_seal_exits_3_and_changes_nothing(self, b5, tmp_layout, capsys):
        before = self._sealed(tmp_layout)
        assert b5.main(["--seal"]) == 3
        assert "already exists" in capsys.readouterr().err
        self._unchanged(tmp_layout, before)

    def test_manifest_appearing_mid_run_is_caught_before_the_first_write(self, b5, tmp_layout, monkeypatch):
        """Fast stubs for the build steps; the manifest is created during assignment."""
        manifest = tmp_layout / "reports" / "frozen_test_v5_manifest.json"
        empty_overlap = {"exact_normalised": [], "skeleton": [], "token_jaccard": [], "total": 0}

        def assign_and_create_manifest(rows, salt, fraction):
            manifest.parent.mkdir(parents=True, exist_ok=True)
            manifest.write_text("{}", encoding="utf-8")
            return {}

        for mod, name, val in (
                (b5.v4, "verify_sources", lambda p: {}), (b5.v3, "load_imc25", lambda p, ex: ([], {})),
                (b5.v4, "load_mendeley", lambda p, ex: []), (b5.v3, "load_donations", lambda ex: []),
                (b5.v3, "load_mwoz", lambda *a: []), (b5.v3, "load_r115", lambda p, ex: []),
                (b5.v3, "load_syn", lambda: []), (b5.v3, "privacy_check", lambda rows: None),
                (b5.tc, "load_evaluation_dataset", lambda path, **k: []),
                (b5.tc, "find_training_overlap", lambda rows, prot: dict(empty_overlap)),
                (b5.v4, "dedupe", lambda rows, ex: rows), (b5.v4, "atomic_units", lambda rows: {}),
                (b5.v4, "assign", assign_and_create_manifest), (b5.v4, "purge", lambda rows, ex: {}),
                (b5.v3, "coverage", lambda p, test: {"met": False}), (b5.v4, "_token_rates", lambda rows: {})):
            monkeypatch.setattr(mod, name, val)
        with pytest.raises(b5.StopBuild, match="already exists; refusing to overwrite"):
            b5.build(seal=True)
        for rel in self.OUTPUTS[:-1]:
            assert not (tmp_layout / rel).exists(), rel           # nothing written, build report included
        assert manifest.read_text(encoding="utf-8") == "{}"         # the existing manifest is untouched

    def test_check_precedes_every_write_in_source(self):
        src = (REPO / "scripts" / "build_pool_and_test_v5.py").read_text(encoding="utf-8")
        body = src[src.index("def build(seal: bool)"):src.index("def _write_outputs(")]
        assert body.index("_refuse_if_sealed()") < body.index("load_protocol()")
        assert body.rindex("_refuse_if_sealed()") < body.index("REPORT_DIR.mkdir")


# ---------------------------------------------------------------------------
# Pool provenance: licence/attribution supplement (R115) without changing selection
# ---------------------------------------------------------------------------

class TestProvenanceAttribution:
    def test_r115_gets_declared_licence_attribution_and_unverified_note(self, b5):
        s = b5.provenance_sources(b5.load_protocol())["R115"]
        assert s["licence"].startswith("CC BY 4.0") and "creativecommons.org/licenses/by/4.0" in s["licence"]
        assert s["attribution"].startswith('Ridham115, "Indian Scam SMS (synthetic, audited)"')
        assert "45e92c38ba20e973ef3ff5d1893a0a710f622018" in s["attribution"]
        assert "Modified by SatyaLens" in s["attribution"] and "groq/openai/gpt-oss-120b" in s["attribution"]
        assert s["upstream_generator_terms"].startswith("NOT VERIFIED")
        assert "not legal clearance" in s["upstream_generator_terms"]
        assert "PROVENANCE_SUPPLEMENT" in s["metadata_origin"]
        assert s["title"] == b5.load_protocol()["sources"]["R115"]["title"] and s["real"] is False

    def test_protocol_sourced_entries_unchanged(self, b5):
        p = b5.load_protocol()
        out = b5.provenance_sources(p)
        for name, src in p["sources"].items():
            if name in b5.PROVENANCE_SUPPLEMENT:
                continue
            assert out[name] == {k: src.get(k) for k in b5.PROVENANCE_FIELDS}, name
        assert out["IMC25"]["attribution"] == p["sources"]["IMC25"]["attribution"]
        assert out["MENDELEY"]["licence"] == p["sources"]["MENDELEY"]["licence"]

    @pytest.mark.parametrize("change", [{"revision": "f" * 40}, {"file_sha256": "0" * 64},
                                        {"revision": "f" * 40, "file_sha256": "0" * 64}])
    def test_supplement_requires_both_pinned_revision_and_hash(self, b5, change):
        import copy
        p = copy.deepcopy(b5.load_protocol())
        p["sources"]["R115"].update(change)
        s = b5.provenance_sources(p)["R115"]
        assert s["licence"] is None and s["attribution"] is None
        assert "upstream_generator_terms" not in s and "metadata_origin" not in s

    def test_protocol_values_win_and_origin_is_accurate(self, b5):
        import copy
        p = copy.deepcopy(b5.load_protocol())
        p["sources"]["R115"]["licence"] = "licence from protocol"
        s = b5.provenance_sources(p)["R115"]
        assert s["licence"] == "licence from protocol"
        assert s["attribution"] == b5.PROVENANCE_SUPPLEMENT["R115"]["attribution"]
        assert s["metadata_origin"].startswith("attribution added by")
        p["sources"]["R115"]["attribution"] = "attribution from protocol"
        s = b5.provenance_sources(p)["R115"]
        assert (s["licence"], s["attribution"]) == ("licence from protocol", "attribution from protocol")
        assert s["metadata_origin"].startswith("licence/attribution taken from the protocol")
        assert s["upstream_generator_terms"].startswith("NOT VERIFIED")
        assert b5.provenance_sources(b5.load_protocol())["R115"]["metadata_origin"].startswith(
            "licence/attribution added by")

    def test_written_pool_provenance_carries_attribution(self, b5, tmp_path, monkeypatch):
        (tmp_path / "data").mkdir()
        (tmp_path / "reports").mkdir()
        for name, val in (("REPO", tmp_path), ("POOL_OUT", tmp_path / "data" / "training_pool_v5.csv"),
                          ("EXCLUDED_OUT", tmp_path / "data" / "v5_build.excluded.csv"),
                          ("MANIFEST", tmp_path / "reports" / "frozen_test_v5_manifest.json")):
            monkeypatch.setattr(b5, name, val)
        p = b5.load_protocol()
        row = lambda t, lab, s: {"text": t, "label": lab, "group": f"{s}:1", "source": s, "key": "k",  # noqa: E731
                                 "category": "c", "india_linked": False, "donor": ""}
        b5._write_outputs(p, b5.sealing_paths(p), [row("a b c", "legitimate", "R115")],
                          [row("x y z", "suspicious", "IMC25")], [],
                          {"test_counts": {}, "test_units": 1, "coverage": {"met": False}, "pool_counts": {}})
        prov = json.loads((tmp_path / "data" / "training_pool_v5.provenance.json").read_text(encoding="utf-8"))
        r115 = prov["sources"]["R115"]
        # the serializer writes every field built in memory, unchanged
        assert r115 == b5.provenance_sources(p)["R115"]
        assert {"licence", "attribution", "upstream_generator_terms", "metadata_origin"} <= set(r115)
        assert r115["licence"] == b5.PROVENANCE_SUPPLEMENT["R115"]["licence"]
        assert r115["attribution"] == b5.PROVENANCE_SUPPLEMENT["R115"]["attribution"]
        assert r115["upstream_generator_terms"].startswith("NOT VERIFIED")
        assert prov["sources"]["IMC25"] == {k: p["sources"]["IMC25"].get(k) for k in b5.PROVENANCE_FIELDS}
        assert prov["protocol_sha256"] == PIN
        assert check_seal_state(REPO) in ("clean", "sealed")        # the tmp write never touched the real repo

    def test_attribution_text_matches_the_review_document(self, b5):
        import re
        doc = (REPO / "docs" / "R115_GENERATOR_TERMS_REVIEW.md").read_text(encoding="utf-8")
        flat = re.sub(r"\s+", " ", re.sub(r"(?m)^\s*>\s?", "", doc))
        assert b5.PROVENANCE_SUPPLEMENT["R115"]["attribution"] in flat

    def test_r115_selection_rules_unchanged(self, b5):
        p = b5.load_protocol()
        assert p["sources"]["R115"]["generators_used"] == ["groq/openai/gpt-oss-120b"]
        assert p["sources"]["R115"]["roles"] == ["train"] and "R115" not in b5.v4.TEST_ELIGIBLE
        if not b5.v3.R115_CSV.is_file():
            pytest.skip("R115 raw file not present")
        ex = []
        rows = b5.v3.load_r115(p, ex)
        assert len(rows) == 700 and sum(1 for e in ex if e["outcome"] == "excluded_generator") == 880
