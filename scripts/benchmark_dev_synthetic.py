"""
benchmark_dev_synthetic.py — SYNTHETIC-ONLY development benchmark (dev-synth-v1)
================================================================================

Evaluates a saved text-classifier artifact on the synthetic development dataset
``data/dev/synthetic_dev_messages_v1.csv`` with ``text_classifier.evaluate``
(the model is never trained or modified; evaluate() checks its SHA-256).

The results are SYNTHETIC-ONLY. They do not measure real-world scam-detection
performance, do not represent Indian financial alerts, are not a Protocol v5
result, and must not be used to select a model against the frozen test set.

Before running, the script verifies:
  * the dataset's SHA-256 equals the hash recorded in its provenance file, and the
    provenance marks it synthetic with source marker SYN_DEV;
  * the dataset is not a sealed file (its separation from Protocol v5 sources is
    enforced by tests/test_dev_synthetic_dataset.py; this script never reads the protocol);
  * the model artifact's SHA-256 equals the expected value (by default the
    preserved-artifact record for that path, if one exists).

    python scripts/benchmark_dev_synthetic.py --out reports/synthetic_dev_benchmark_v2
    python scripts/benchmark_dev_synthetic.py --verify      # re-run in a temp dir, compare with
                                                            # reports/synthetic_dev_benchmark_v1
It never overwrites: --out must be a new or empty directory.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from satyalens_analysis import text_classifier as tc  # noqa: E402

DATA = REPO / "data" / "dev" / "synthetic_dev_messages_v1.csv"
PROVENANCE = DATA.with_suffix(".provenance.json")
PRESERVED = REPO / "reports" / "preserved_artifacts_manifest.json"
EXISTING_REPORT = REPO / "reports" / "synthetic_dev_benchmark_v1"
# evaluation.json fields that must be identical between two runs of the same inputs
DETERMINISTIC_KEYS = ("dataset", "data_checks", "coverage", "metrics", "majority_class_baseline",
                      "rule_engine_baseline", "breakdowns", "near_duplicate_sensitivity")


class BenchmarkError(RuntimeError):
    pass


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_dataset(data: Path) -> Dict[str, Any]:
    prov_path = data.with_suffix(".provenance.json")
    if not data.is_file() or not prov_path.is_file():
        raise BenchmarkError(f"Dataset or its provenance file not found: {data.name}")
    prov = json.loads(prov_path.read_text(encoding="utf-8"))
    if prov.get("synthetic") is not True or prov.get("source_marker") != "SYN_DEV":
        raise BenchmarkError("Provenance does not mark the dataset synthetic (source SYN_DEV); refusing.")
    expected = (prov.get("files") or {}).get(data.name, {}).get("sha256")
    actual = _sha(data)
    if actual != expected:
        raise BenchmarkError(f"Dataset SHA-256 {actual} differs from its provenance record {expected}.")
    if tc.is_sealed(data):
        raise BenchmarkError("Dataset is a sealed file; refusing.")
    # Separation from Protocol v5 sources is enforced by tests/test_dev_synthetic_dataset.py; this
    # script deliberately does not read the protocol (only v5 tooling may).
    return {"file": data.name, "sha256": actual, "provenance_version": prov.get("generator_version")}


def expected_model_sha(model_path: Path) -> Optional[str]:
    if not PRESERVED.is_file():
        return None
    files = json.loads(PRESERVED.read_text(encoding="utf-8"))["files"]
    try:
        rel = model_path.resolve().relative_to(REPO).as_posix()
    except ValueError:
        return None
    return files.get(rel)


def verify_model(model_path: Path, expect: Optional[str]) -> str:
    if not model_path.is_file():
        raise BenchmarkError(f"Model artifact not found: {model_path}")
    actual = _sha(model_path)
    if expect is None:
        raise BenchmarkError("No expected model SHA-256 (not a preserved artifact); pass --expect-model-sha256.")
    if actual != expect:
        raise BenchmarkError(f"Model SHA-256 {actual} differs from the expected {expect}.")
    return actual


def _readme(r: Dict[str, Any]) -> str:
    m, rb, b, cov = r["metrics"], r["rule_engine_baseline"], r["majority_class_baseline"], r["coverage"]
    return "\n".join([
        "# SYNTHETIC-ONLY development benchmark (dev-synth-v1)", "",
        "> **Synthetic data only.** Template-generated messages written for this project",
        "> (`data/dev/synthetic_dev_messages_v1.csv`). These numbers do **not** measure real-world",
        "> scam-detection performance, do **not** represent Indian financial alerts, are **not** a",
        "> Protocol v5 result, and must **not** be used to select a model against the frozen test set.", "",
        f"- Produced by `scripts/benchmark_dev_synthetic.py`; model `{r['model']['model_version']}` "
        f"(SHA-256 `{r['model']['artifact_sha256']}`, unchanged after evaluation: "
        f"{r['model']['artifact_unchanged_after_evaluation']})",
        f"- Dataset SHA-256 `{r['dataset']['sha256']}`; rows {r['dataset']['rows']} {r['dataset']['class_counts']}",
        f"- Classified {cov['classified']}, inconclusive {cov['inconclusive']}; evaluation.json metrics use "
        "classified rows only (descriptive)", "",
        "| Synthetic-only metric | Model | Rule engine | Majority class |", "|---|---|---|---|",
        f"| Macro-F1 | {m['macro_avg']['f1']} | {rb['macro_avg']['f1']} | {b['macro_avg']['f1']} |",
        f"| Suspicious recall | {m['suspicious']['recall']} | {rb['suspicious']['recall']} | {b['suspicious']['recall']} |",
        f"| Legitimate false-positive rate | {m['legitimate_false_positive_rate']} | "
        f"{rb['legitimate_false_positive_rate']} | {b['legitimate_false_positive_rate']} |", "",
    ]) + "\n"


def run(out: Path, data: Path = DATA, model_dir: Optional[Path] = None,
        expect_model_sha256: Optional[str] = None) -> Dict[str, Any]:
    out = Path(out)
    if out.exists() and any(out.iterdir()):
        raise BenchmarkError(f"Output directory {out} is not empty; refusing to overwrite.")
    model_path = Path(model_dir or tc.DEFAULT_MODEL_DIR) / tc.ARTIFACT_FILENAME
    verify_dataset(Path(data))
    verify_model(model_path, expect_model_sha256 or expected_model_sha(model_path))
    report = tc.evaluate(Path(data), model_path, out_dir=out)
    (out / "README.md").write_text(_readme(report), encoding="utf-8")
    return report


def compare(a_dir: Path, b_dir: Path) -> List[str]:
    """Differences between two benchmark directories (deterministic fields + predictions.csv)."""
    a = json.loads((a_dir / "evaluation.json").read_text(encoding="utf-8"))
    b = json.loads((b_dir / "evaluation.json").read_text(encoding="utf-8"))
    diffs = [k for k in DETERMINISTIC_KEYS if a.get(k) != b.get(k)]
    for k in ("model_version", "artifact_sha256"):
        if a["model"].get(k) != b["model"].get(k):
            diffs.append(f"model.{k}")
    if (a_dir / "predictions.csv").read_bytes() != (b_dir / "predictions.csv").read_bytes():
        diffs.append("predictions.csv")
    return diffs


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Synthetic-only development benchmark (never real-world evidence).")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--out", help="new or empty directory for the report")
    g.add_argument("--verify", action="store_true", help=f"re-run in a temp dir and compare with {EXISTING_REPORT.name}")
    ap.add_argument("--data", default=str(DATA))
    ap.add_argument("--model-dir", default=None)
    ap.add_argument("--expect-model-sha256", default=None)
    ap.add_argument("--against", default=str(EXISTING_REPORT), help="report directory compared by --verify")
    args = ap.parse_args(argv)
    try:
        if args.verify:
            with tempfile.TemporaryDirectory() as tmp:
                run(Path(tmp) / "bench", Path(args.data), args.model_dir and Path(args.model_dir),
                    args.expect_model_sha256)
                diffs = compare(Path(args.against), Path(tmp) / "bench")
            print("reproducible: YES" if not diffs else f"reproducible: NO, differs in {diffs}")
            return 0 if not diffs else 1
        r = run(Path(args.out), Path(args.data), args.model_dir and Path(args.model_dir), args.expect_model_sha256)
        print(f"SYNTHETIC-ONLY benchmark written to {args.out}: macro-F1 {r['metrics']['macro_avg']['f1']}")
        return 0
    except (BenchmarkError, tc.EvaluationError) as exc:
        print(f"STOP: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
