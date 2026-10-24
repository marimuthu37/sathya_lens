"""
test_protocol_v5.py — docs/protocol_v5.json: hash pin, exact diff against v4,
decision text, sealed-manifest defaults and SATYALENS_SEALED_MANIFEST addition
semantics, the one-shot guard for a v5 manifest, .gitignore safeguards, and
v3/v4 artifacts unchanged. Temporary layouts only; no real pool/test/manifest.
"""
import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from satyalens_analysis import text_classifier as tc
from tests.test_protocol_v3 import _OTHER_ROWS, _eval_csv
from tests.test_text_classifier import _toy_rows, _write_csv

REPO = Path(__file__).resolve().parent.parent
PIN_V5 = "3889099d1da7fa6cdc987685a2dacda4592508339bd8cedb91a99fa29f25f3ee"
PIN_V4 = "f32adc7668ca9f4d44bee2a3a358c4191ba369e32824421de69f77c9c54c0a49"
PIN_V3 = "e97171dadedf57083265fc6bb38306942da5df41df32d079439bc11331d79e7e"

# SHA-256 of v3/v4 records, recorded 2026-10-24 before any v5 file was written.
V3_V4_BASELINE = {
    "docs/protocol_v3.json": PIN_V3,
    "docs/protocol_v3.sha256": "508d5d986556b9e94b6b7be596bf036cc474d3145a1d78dbeb8d1149e8b04ad6",
    "docs/protocol_v4.json": PIN_V4,
    "docs/protocol_v4.sha256": "c34c715e9d80b0bdc6ee0fa8451005fd145d2b4f8c54d061eb6522c26b364e17",
    "docs/protocol_v4_DRAFT.json": "fb45f4144830116f4235d9adc854b98240b5a89e47c877599dedd6c63b8af76a",
    "docs/PROTOCOL_V4_PROPOSAL.md": "7943686c8c9987f7603178a4d55fb9461a98053d3cf09e677ab8c618efc712cf",
    "scripts/build_pool_and_test_v3.py": "b8b91dfe39baf5533b7a87c5a2b09f701cde3851cac7af615fb2bc08e370e876",
    "scripts/acceptance_v3.py": "84045c2a31597a9a70175b1b8e20d223691b3e0402ee0e70fe905ff7d56d0d8b",
    "scripts/select_text_model_v3.py": "17942795067cf4ea790b9d4d7d09173cdf95af72a432081cd0aa8f60d8c34897",
    "scripts/build_pool_and_test_v4.py": "484e2ed3e9f802d893b32ee3fc026df60812bfefea8870830197be52520cee16",
    "scripts/select_text_model_v4.py": "f5932cdabeb70c5c4fd40b63cf9096b27b20bc1f5839897952272987df2de70a",
    "scripts/acceptance_v4.py": "785fb85be98ff70e19247b20480609e3aa66188485d0b2fec65e183f70f5fea0",
    "scripts/grouping_diagnostics_v4.py": "668b4a733c6b2e0abb0b64de8250d9caaf4485c95325b8affc23376b027845aa",
    "reports/build_v3/build_report_dry_run.json": "8273658fd84360bb4aabc26fbd90f877729b740c20be50f2edc11b8751366b0e",
    "reports/build_v4/build_report_dry_run.json": "99b29db0629873f56b3a454afcd5c46f30fbacd1732bc417e488b49763778a15",
    "reports/grouping_v4/diagnostics.json": "a1e71e574548786fd04f01026035b3c7a0e01181bebb3ab2ce70fae2a544ab0a",
}


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _p(v):
    return json.loads((REPO / "docs" / f"protocol_v{v}.json").read_text(encoding="utf-8"))


def _diff_paths(a, b, path=""):
    if isinstance(a, dict) and isinstance(b, dict):
        out = set()
        for k in set(a) | set(b):
            out |= _diff_paths(a.get(k, "<absent>"), b.get(k, "<absent>"), f"{path}.{k}" if path else k)
        return out
    return set() if a == b else {path}


# ---------------------------------------------------------------------------
# Hash pin and the exact v4 -> v5 diff
# ---------------------------------------------------------------------------

class TestHashAndDiff:
    def test_v5_hash_pinned(self):
        assert _sha(REPO / "docs" / "protocol_v5.json") == PIN_V5
        assert (REPO / "docs" / "protocol_v5.sha256").read_text(encoding="utf-8").split() == [
            PIN_V5, "docs/protocol_v5.json"]
        assert _p(5)["protocol_version"] == "5.0" and _p(5)["protocol_id"] == "satyalens-text-classifier-v5"

    def test_v3_v4_artifacts_unchanged(self):
        for rel, expected in V3_V4_BASELINE.items():
            path = REPO / rel
            assert path.is_file(), rel
            assert _sha(path) == expected, rel

    def test_only_the_reviewed_paths_changed(self):
        assert _diff_paths(_p(4), _p(5)) == {
            "protocol_id", "protocol_version", "status", "supersedes", "draft_record",
            "decisions.Q6", "decisions.Q7", "decisions.Q8", "decisions.Q9", "decisions.Q10", "decisions.Q11",
            "model_selection.data", "model_selection.scoring", "model_selection.reported_baselines",
            "model_selection.baselines_role", "model_selection.final_training",
            "acceptance_criteria.evaluated_on", "acceptance_criteria.conventions", "acceptance_criteria.B1",
            "acceptance_criteria.reported_not_gating",
            "sealing.test_file", "sealing.manifest", "sealing.guard", "sealing.script_rule",
            "sealing.commit_record", "sealing.residual_risk",
            "required_code_changes.before_dry_run", "required_code_changes.before_selection_and_evaluation",
            "commit_policy.never_commit", "commit_policy.commit"}

    def test_preserved_sections_identical(self):
        v4, v5 = _p(4), _p(5)
        for k in ("approved_utc", "scope", "sources", "anonymisation", "build_order", "grouping", "test_assignment",
                  "minimum_legitimate_coverage", "withdrawal", "deviations"):
            assert v5[k] == v4[k], k
        for k in ("cv", "input_text", "candidates", "rule", "reporting"):
            assert v5["model_selection"][k] == v4["model_selection"][k], k
        for k in ("S1", "S2", "L1", "L2", "M1", "verdict"):
            assert v5["acceptance_criteria"][k] == v4["acceptance_criteria"][k], k
        assert v5["sealing"]["one_shot"] == v4["sealing"]["one_shot"]
        assert all(v5["decisions"][q] == v4["decisions"][q] for q in ("Q1", "Q2", "Q3", "Q4", "Q5"))
        assert v5["test_assignment"]["salt"] == "satyalens-protocol-v4-2026-10-24"


class TestDecisionText:
    def test_inconclusive_everywhere(self):
        p = _p(5)
        s = p["model_selection"]["scoring"]
        assert "'legitimate' in every selection quantity" in s and "per-fold macro-F1" in s and "MISS" in s
        c = p["acceptance_criteria"]["conventions"]
        assert "every acceptance quantity" in c and "Inconclusive suspicious rows count as MISSES" in c
        assert "acceptance_v5.json" in c and "authoritative" in c and "descriptive only" in c

    def test_majority_baselines(self):
        p = _p(5)
        assert p["model_selection"]["reported_baselines"][0].startswith("majority class of each training fold")
        assert "always-legitimate (fixed)" in p["model_selection"]["reported_baselines"]
        assert "never selection criteria" in p["model_selection"]["baselines_role"]
        b1 = p["acceptance_criteria"]["B1"]
        assert "pool_counts" in b1 and "never from test labels" in b1 and "strictly greater (unrounded)" in b1
        retro = [x for x in p["acceptance_criteria"]["reported_not_gating"] if "RETROSPECTIVE" in x]
        assert len(retro) == 1 and "never gating" in retro[0] and "never used for selection" in retro[0]

    def test_coverage_and_verdict_not_relaxed(self):
        p = _p(5)
        assert "NOT EVALUABLE" in p["minimum_legitimate_coverage"]["if_not_met"]
        assert "PARTIALLY EVALUATED — not validated for genuine financial alerts" in p["acceptance_criteria"]["verdict"]
        assert "VALIDATED only if S1, S2, L1, L2, M1 and B1 all pass" in p["acceptance_criteria"]["verdict"]

    def test_sealing_r1_r2(self):
        s = _p(5)["sealing"]
        assert "adds one further manifest" in s["guard"] and "never disables them" in s["guard"]
        assert "committed to git at sealing" in s["commit_record"] and "evaluation log" in s["commit_record"]
        assert "predictions.csv (per-row test labels) is never committed" in s["commit_record"]
        assert "delete the evaluation log" in s["residual_risk"]
        assert "evaluation_log 'reports/frozen_test_v5_evaluations.jsonl'" in s["manifest"]
        assert "pool_counts" in s["manifest"] and s["test_file"].startswith("data/frozen_test_v5.csv")

    def test_proposal_records_finalized_decisions(self):
        doc = (REPO / "docs" / "PROTOCOL_V5_PROPOSAL.md").read_text(encoding="utf-8")
        assert "FINALIZED" in doc and PIN_V5 in doc and PIN_V4 in doc
        for d in ("D1", "D2", "P1", "P2", "P3", "P4", "P5", "P6", "R1", "R2", "R3"):
            assert d in doc, d

    def test_only_v5_tooling_reads_protocol_v5(self):
        allowed = {"build_pool_and_test_v5.py", "select_text_model_v5.py", "acceptance_v5.py"}
        for script in (REPO / "scripts").glob("*.py"):
            if script.name not in allowed:
                assert "protocol_v5.json" not in script.read_text(encoding="utf-8"), script.name


# ---------------------------------------------------------------------------
# Sealed-manifest defaults and SATYALENS_SEALED_MANIFEST (R1)
# ---------------------------------------------------------------------------

class TestManifestDefaultsAndOverride:
    def test_defaults_include_v3_v4_v5(self):
        names = [p.name for p in tc.DEFAULT_SEALED_MANIFESTS]
        assert names == ["frozen_test_v3_manifest.json", "frozen_test_v4_manifest.json",
                         "frozen_test_v5_manifest.json"]
        assert all(p.parent.name == "reports" for p in tc.DEFAULT_SEALED_MANIFESTS)

    @pytest.fixture()
    def four(self, tmp_path, monkeypatch):
        """Three tmp 'default' manifests (v3/v4/v5) + one extra manifest for the env variable."""
        files, defaults = {}, []
        for name in ("v3", "v4", "v5", "extra"):
            root = tmp_path / name
            (root / "data").mkdir(parents=True)
            (root / "reports").mkdir()
            f = root / "data" / f"sealed_{name}.csv"
            f.write_text(f"text,label\nsealed row for {name},legitimate\n", encoding="utf-8")
            m = root / "reports" / f"manifest_{name}.json"
            m.write_text(json.dumps({"sealed_files": [{"path": f"data/sealed_{name}.csv", "sha256": _sha(f)}],
                                     "protocol_file": "x", "protocol_sha256": "x",
                                     "evaluation_log": "reports/log.jsonl"}), encoding="utf-8")
            files[name] = (f, m)
            if name != "extra":
                defaults.append(m)
        monkeypatch.setattr(tc, "DEFAULT_SEALED_MANIFESTS", tuple(defaults))
        monkeypatch.delenv(tc.SEALED_MANIFEST_ENV, raising=False)
        return files

    def test_defaults_enforced_without_env(self, four):
        for name in ("v3", "v4", "v5"):
            assert tc.is_sealed(four[name][0]), name
            with pytest.raises(tc.SealedDataError):
                tc.load_dataset(four[name][0])
        assert not tc.is_sealed(four["extra"][0])

    def test_env_adds_and_never_disables_defaults(self, four, monkeypatch):
        monkeypatch.setenv(tc.SEALED_MANIFEST_ENV, str(four["extra"][1]))
        for name in ("v3", "v4", "v5", "extra"):
            assert tc.is_sealed(four[name][0]), name
            with pytest.raises(tc.SealedDataError):
                tc.load_evaluation_dataset(four[name][0])

    def test_env_pointing_at_missing_file_keeps_defaults(self, four, monkeypatch, tmp_path):
        monkeypatch.setenv(tc.SEALED_MANIFEST_ENV, str(tmp_path / "absent.json"))
        assert all(tc.is_sealed(four[n][0]) for n in ("v3", "v4", "v5"))

    def test_byte_identical_copy_refused_despite_env(self, four, monkeypatch, tmp_path):
        monkeypatch.setenv(tc.SEALED_MANIFEST_ENV, str(four["extra"][1]))
        copy = tmp_path / "renamed.csv"
        copy.write_bytes(four["v5"][0].read_bytes())
        with pytest.raises(tc.SealedDataError):
            tc.load_dataset(copy)

    def test_env_equal_to_a_default_is_not_duplicated(self, four, monkeypatch):
        monkeypatch.setenv(tc.SEALED_MANIFEST_ENV, str(four["v4"][1]))
        assert len(tc._sealed_manifest_paths()) == 3

    def test_unrelated_file_still_loads(self, four, tmp_path, monkeypatch):
        monkeypatch.setenv(tc.SEALED_MANIFEST_ENV, str(four["extra"][1]))
        assert len(tc.load_evaluation_dataset(_eval_csv(tmp_path / "free.csv", _OTHER_ROWS))) == len(_OTHER_ROWS)


# ---------------------------------------------------------------------------
# One-shot guard with a v5 manifest
# ---------------------------------------------------------------------------

class TestOneShotV5:
    @pytest.fixture()
    def sealed_v5(self, tmp_path, monkeypatch):
        for d in ("data", "reports", "docs"):
            (tmp_path / d).mkdir()
        shutil.copy(REPO / "docs" / "protocol_v5.json", tmp_path / "docs" / "protocol_v5.json")
        test = _eval_csv(tmp_path / "data" / "frozen_test_v5.csv")
        manifest = tmp_path / "reports" / "frozen_test_v5_manifest.json"
        manifest.write_text(json.dumps({
            "protocol_file": "docs/protocol_v5.json", "protocol_sha256": PIN_V5,
            "sealed_files": [{"path": "data/frozen_test_v5.csv", "sha256": _sha(test)}],
            "evaluation_log": "reports/frozen_test_v5_evaluations.jsonl"}), encoding="utf-8")
        monkeypatch.setattr(tc, "DEFAULT_SEALED_MANIFESTS",
                            (tmp_path / "reports" / "frozen_test_v3_manifest.json",
                             tmp_path / "reports" / "frozen_test_v4_manifest.json", manifest))
        monkeypatch.delenv(tc.SEALED_MANIFEST_ENV, raising=False)
        train = _write_csv(tmp_path / "train.csv", _toy_rows())
        tc.clear_model_cache()
        tc.train(train, model_dir=tmp_path / "m", run_cv=False)
        yield {"test": test, "model": tmp_path / "m" / tc.ARTIFACT_FILENAME, "train": train, "root": tmp_path,
               "log": tmp_path / "reports" / "frozen_test_v5_evaluations.jsonl"}
        tc.clear_model_cache()

    def test_plain_evaluate_refused_even_with_env(self, sealed_v5, monkeypatch):
        monkeypatch.setenv(tc.SEALED_MANIFEST_ENV, str(sealed_v5["root"] / "other.json"))
        with pytest.raises(tc.SealedDataError):
            tc.evaluate(sealed_v5["test"], sealed_v5["model"], training_data=sealed_v5["train"],
                        out_dir=sealed_v5["root"] / "o")

    def test_exactly_once_and_env_cannot_reset(self, sealed_v5, monkeypatch):
        r = tc.evaluate(sealed_v5["test"], sealed_v5["model"], training_data=sealed_v5["train"],
                        out_dir=sealed_v5["root"] / "out", final_sealed=True)
        assert r["final_sealed_evaluation"] is True
        entry = json.loads(sealed_v5["log"].read_text(encoding="utf-8").strip())
        assert entry["test_sha256"] == _sha(sealed_v5["test"])
        for env in (None, str(sealed_v5["root"] / "other.json")):
            if env:
                monkeypatch.setenv(tc.SEALED_MANIFEST_ENV, env)
            with pytest.raises(tc.EvaluationError, match="already been evaluated once"):
                tc.evaluate(sealed_v5["test"], sealed_v5["model"], training_data=sealed_v5["train"],
                            out_dir=sealed_v5["root"] / "again", final_sealed=True)

    def test_copy_cannot_be_evaluated_twice(self, sealed_v5):
        tc.evaluate(sealed_v5["test"], sealed_v5["model"], training_data=sealed_v5["train"],
                    out_dir=sealed_v5["root"] / "out", final_sealed=True)
        copy = sealed_v5["root"] / "copy.csv"
        copy.write_bytes(sealed_v5["test"].read_bytes())
        with pytest.raises(tc.EvaluationError, match="already been evaluated once"):
            tc.evaluate(copy, sealed_v5["model"], training_data=sealed_v5["train"],
                        out_dir=sealed_v5["root"] / "again", final_sealed=True)

    def test_modified_protocol_refused(self, sealed_v5):
        (sealed_v5["root"] / "docs" / "protocol_v5.json").write_text("{}", encoding="utf-8")
        with pytest.raises(tc.EvaluationError, match="Protocol file"):
            tc.evaluate(sealed_v5["test"], sealed_v5["model"], out_dir=sealed_v5["root"] / "o", final_sealed=True)

    def test_modified_test_refused(self, sealed_v5):
        sealed_v5["test"].write_text(sealed_v5["test"].read_text(encoding="utf-8") + "extra row,legitimate\n",
                                     encoding="utf-8")
        with pytest.raises(tc.EvaluationError, match="changed since sealing"):
            tc.evaluate(sealed_v5["test"], sealed_v5["model"], out_dir=sealed_v5["root"] / "o", final_sealed=True)


# ---------------------------------------------------------------------------
# .gitignore (R3)
# ---------------------------------------------------------------------------

def _ignored(rel: str) -> bool:
    r = subprocess.run(["git", "check-ignore", "-q", "--no-index", rel], cwd=REPO, capture_output=True)
    if r.returncode not in (0, 1):
        pytest.skip(f"git check-ignore unavailable: {r.stderr!r}")
    return r.returncode == 0


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
class TestGitignore:
    @pytest.mark.parametrize("rel", [
        "data/training_pool_v3.csv", "data/frozen_test_v3.csv",
        "data/training_pool_v4.csv", "data/frozen_test_v4.csv",
        "data/training_pool_v5.csv", "data/frozen_test_v5.csv", "data/v5_build.excluded.csv",
        "reports/frozen_test_v4_evaluation/predictions.csv", "reports/frozen_test_v5_evaluation/predictions.csv",
        "data/donations/x.csv", "reports/donations/progress.json", "dataset/x.csv", "data/raw/x.csv",
        "satyalens_analysis/models/candidates/v5/text_classifier.joblib",
    ])
    def test_protected_artifacts_ignored(self, rel):
        assert _ignored(rel), rel

    @pytest.mark.parametrize("rel", [
        "docs/protocol_v5.json", "docs/protocol_v5.sha256", "reports/frozen_test_v5_manifest.json",
        "reports/frozen_test_v5_evaluations.jsonl", "reports/frozen_test_v5_evaluation/evaluation.json",
        "reports/frozen_test_v5_evaluation/summary.md", "reports/frozen_test_v5_evaluation/acceptance_v5.json",
        "reports/frozen_test_v5_evaluation/acceptance_v5.md", "reports/build_v5/build_report_dry_run.json",
        "scripts/build_pool_and_test_v5.py",
    ])
    def test_committed_records_not_ignored(self, rel):
        assert not _ignored(rel), rel


# ---------------------------------------------------------------------------
# .gitattributes: hash-pinned files are committed byte-for-byte (no EOL conversion)
# ---------------------------------------------------------------------------

def _git_out(*args):
    r = subprocess.run(["git", *args], cwd=REPO, capture_output=True)
    if r.returncode != 0:
        pytest.skip(f"git unavailable here: {r.stderr!r}")
    return r.stdout.decode().strip()


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
class TestGitattributes:
    PINNED = sorted(set(V3_V4_BASELINE) | {"docs/protocol_v5.json", "docs/protocol_v5.sha256",
                                           "docs/PROTOCOL_V5_PROPOSAL.md", "reports/build_v5/build_report_dry_run.json",
                                           "reports/frozen_test_v5_manifest.json", "reports/build_v5/build_report.json",
                                           "data/synthetic_financial_messages_v1.csv", "data/challenge_suite_v1.csv",
                                           "reports/preserved_artifacts_manifest.json"})

    @pytest.mark.parametrize("rel", PINNED)
    def test_text_conversion_disabled(self, rel):
        assert _git_out("check-attr", "text", "--", rel).endswith(": text: unset"), rel

    @pytest.mark.parametrize("rel", [r for r in PINNED if (REPO / r).is_file()])
    def test_blob_equals_bytes_on_disk(self, rel):
        # What `git add` would store (filters applied) must equal the raw bytes that are hash-pinned.
        assert _git_out("hash-object", f"--path={rel}", "--", rel) == _git_out("hash-object", "--no-filters", "--", rel), rel
