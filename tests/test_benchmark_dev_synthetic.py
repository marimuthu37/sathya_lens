"""
test_benchmark_dev_synthetic.py — scripts/benchmark_dev_synthetic.py: input verification,
no-overwrite rule, synthetic-only labelling, determinism, and reproduction of the existing
report. Toy model in a temp dir; the deployed artifact is only read.
"""
import hashlib
import importlib.util
import json
import shutil
from pathlib import Path

import pytest

from satyalens_analysis import text_classifier as tc
from tests.test_text_classifier import _toy_rows, _write_csv

REPO = Path(__file__).resolve().parent.parent


def _load():
    spec = importlib.util.spec_from_file_location("bench", REPO / "scripts" / "benchmark_dev_synthetic.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


@pytest.fixture(scope="module")
def bench():
    return _load()


@pytest.fixture()
def toy(tmp_path):
    tc.train(_write_csv(tmp_path / "toy.csv", _toy_rows()), model_dir=tmp_path / "m", run_cv=False)
    tc.clear_model_cache()
    yield {"dir": tmp_path / "m", "sha": _sha(tmp_path / "m" / tc.ARTIFACT_FILENAME), "root": tmp_path}
    tc.clear_model_cache()


class TestInputVerification:
    def test_dataset_verified_against_provenance(self, bench):
        info = bench.verify_dataset(bench.DATA)
        assert info["sha256"] == _sha(bench.DATA) and info["provenance_version"] == "dev-synth-v1"

    def test_tampered_dataset_refused(self, bench, tmp_path):
        data = tmp_path / bench.DATA.name
        shutil.copy(bench.DATA, data)
        shutil.copy(bench.PROVENANCE, data.with_suffix(".provenance.json"))
        data.write_bytes(data.read_bytes().replace(b"Riya", b"Rina", 1))
        with pytest.raises(bench.BenchmarkError, match="differs from its provenance record"):
            bench.verify_dataset(data)

    def test_non_synthetic_dataset_refused(self, bench, tmp_path):
        data = tmp_path / bench.DATA.name
        shutil.copy(bench.DATA, data)
        prov = json.loads(bench.PROVENANCE.read_text(encoding="utf-8"))
        prov["synthetic"] = False
        data.with_suffix(".provenance.json").write_text(json.dumps(prov), encoding="utf-8")
        with pytest.raises(bench.BenchmarkError, match="synthetic"):
            bench.verify_dataset(data)

    def test_missing_provenance_refused(self, bench, tmp_path):
        data = tmp_path / "x.csv"
        shutil.copy(bench.DATA, data)
        with pytest.raises(bench.BenchmarkError, match="not found"):
            bench.verify_dataset(data)

    def test_model_hash_required_and_checked(self, bench, toy):
        path = toy["dir"] / tc.ARTIFACT_FILENAME
        with pytest.raises(bench.BenchmarkError, match="No expected model"):
            bench.verify_model(path, None)
        with pytest.raises(bench.BenchmarkError, match="differs from the expected"):
            bench.verify_model(path, "0" * 64)
        assert bench.verify_model(path, toy["sha"]) == toy["sha"]

    def test_deployed_model_expected_hash_comes_from_preserved_manifest(self, bench):
        manifest = json.loads(bench.PRESERVED.read_text(encoding="utf-8"))["files"]
        path = tc.DEFAULT_MODEL_DIR / tc.ARTIFACT_FILENAME
        assert bench.expected_model_sha(path) == manifest["satyalens_analysis/models/text_classifier.joblib"]


class TestRunAndDeterminism:
    def test_run_writes_synthetic_only_report(self, bench, toy):
        out = toy["root"] / "bench"
        r = bench.run(out, model_dir=toy["dir"], expect_model_sha256=toy["sha"])
        assert sorted(p.name for p in out.iterdir()) == ["README.md", "evaluation.json", "predictions.csv", "summary.md"]
        readme = (out / "README.md").read_text(encoding="utf-8")
        assert "SYNTHETIC-ONLY" in readme and "must **not** be used to select a model" in readme
        assert r["dataset"]["provenance"]["synthetic"] is True
        assert r["model"]["artifact_unchanged_after_evaluation"] is True

    def test_refuses_non_empty_output(self, bench, toy):
        out = toy["root"] / "bench"
        out.mkdir()
        (out / "keep.txt").write_text("existing", encoding="utf-8")
        with pytest.raises(bench.BenchmarkError, match="not empty; refusing to overwrite"):
            bench.run(out, model_dir=toy["dir"], expect_model_sha256=toy["sha"])
        assert (out / "keep.txt").read_text(encoding="utf-8") == "existing"

    def test_two_runs_identical_and_compare_detects_changes(self, bench, toy):
        a, b = toy["root"] / "a", toy["root"] / "b"
        for d in (a, b):
            bench.run(d, model_dir=toy["dir"], expect_model_sha256=toy["sha"])
        assert bench.compare(a, b) == []
        ev = json.loads((b / "evaluation.json").read_text(encoding="utf-8"))
        ev["metrics"]["macro_avg"]["f1"] = 0.0
        (b / "evaluation.json").write_text(json.dumps(ev), encoding="utf-8")
        assert "metrics" in bench.compare(a, b)

    def test_cli_stop_exit_code(self, bench, toy, capsys):
        out = toy["root"] / "bench"
        out.mkdir()
        (out / "x").write_text("x", encoding="utf-8")
        assert bench.main(["--out", str(out), "--model-dir", str(toy["dir"]),
                           "--expect-model-sha256", toy["sha"]]) == 3
        assert "STOP" in capsys.readouterr().err


@pytest.mark.skipif(not (tc.DEFAULT_MODEL_DIR / tc.ARTIFACT_FILENAME).is_file()
                    or not (REPO / "reports" / "synthetic_dev_benchmark_v1" / "evaluation.json").is_file(),
                    reason="deployed artifact or existing benchmark report not present")
def test_existing_report_reproduces_from_repository(bench):
    before = {p.name: _sha(p) for p in bench.EXISTING_REPORT.iterdir()}
    assert bench.main(["--verify"]) == 0
    assert {p.name: _sha(p) for p in bench.EXISTING_REPORT.iterdir()} == before      # untouched
