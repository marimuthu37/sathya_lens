"""
acceptance_v4.py — Apply docs/protocol_v4.json acceptance_criteria
==================================================================
Pinned to protocol v4 SHA-256 f32adc7668ca9f4d44bee2a3a358c4191ba369e32824421de69f77c9c54c0a49.

Reads ONLY the per-row predictions.csv (no message text) written by the
one-shot `evaluate --final-sealed-evaluation`, plus the v4 manifest and its
evaluation log. Never opens the sealed test file's content (only its SHA-256
is checked). Refuses (AcceptanceError) unless:
  * the protocol file matches the pin and its .sha256 record;
  * the manifest exists, is valid JSON, carries the pinned protocol hash and
    the protocol's sealed-test and evaluation-log paths (protocol 'sealing');
  * the sealed file listed in the manifest still has its sealed SHA-256;
  * the evaluation log has EXACTLY ONE entry, for that sealed file, whose
    report directory is the directory of the given predictions.csv;
  * predictions.csv exists, has the required columns, unique row ids, and
    exactly the manifest's per-source / per-class test counts;
  * the donated-message coverage recomputed from the predictions agrees with
    the manifest's coverage flag.

Protocol conventions: an inconclusive prediction counts as 'legitimate' — a
MISS for suspicious rows, NOT a false positive for legitimate rows. L1 is
evaluable only if minimum_legitimate_coverage is met (manifest AND recomputed);
otherwise it is NOT EVALUABLE and the verdict cannot be VALIDATED. Pass/fail
uses the protocol thresholds on exact point estimates (fractions k/n and
unrounded macro-F1; reported values are rounded to 4 dp); 95 % confidence
intervals (Wilson for proportions, seeded bootstrap for macro-F1) are reported,
not gating.

B1 majority-class baseline: the class is fixed by the TRAINING pool label
counts recorded in the manifest (pool_counts) and then scored on the test. A
test-majority predictor is reported only as a labelled retrospective reference.

    python scripts/acceptance_v4.py --predictions reports/frozen_test_v4_evaluation/predictions.csv
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import random
import re
import sys
from collections import Counter
from fractions import Fraction
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from satyalens_analysis.text_classifier import LABELS, compute_metrics  # noqa: E402

PROTOCOL_V4_SHA256 = "f32adc7668ca9f4d44bee2a3a358c4191ba369e32824421de69f77c9c54c0a49"
PROTOCOL = REPO / "docs" / "protocol_v4.json"
PROTOCOL_HASH = REPO / "docs" / "protocol_v4.sha256"
MANIFEST = REPO / "reports" / "frozen_test_v4_manifest.json"
ROOT = REPO                                   # base for manifest-relative paths
REQUIRED_COLUMNS = {"row", "label", "prediction", "rule_prediction", "source", "india_linked", "category", "donor"}
THRESHOLDS = {"S1": 0.80, "S2": 0.80, "L1": 0.10, "L2": 0.05, "M1": 0.80}
# protocol minimum_legitimate_coverage.test_requirement
COVERAGE_MIN_MESSAGES, COVERAGE_MIN_DONORS, COVERAGE_MIN_CATEGORIES, COVERAGE_CATEGORY_N = 150, 5, 3, 25
SMALL_SUBGROUP_N = 30
EXPECTED_HEY_MUM_DAD_ROWS = 14                               # reports/build_v4/build_report_dry_run.json
BOOTSTRAP_REPS, BOOTSTRAP_SEED = 1000, 20261024


class AcceptanceError(RuntimeError):
    pass


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def wilson(k: int, n: int, z: float = 1.959963984540054) -> Optional[List[float]]:
    if n == 0:
        return None
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return [round(max(0.0, centre - half), 4), round(min(1.0, centre + half), 4)]


def sealing_paths(protocol: Dict) -> Tuple[str, str]:
    """(sealed test path, evaluation log path) as fixed by protocol 'sealing'."""
    s = protocol["sealing"]
    log = re.search(r"evaluation_log '([^']+)'", s["manifest"])
    if not log:
        raise AcceptanceError("Protocol sealing.manifest names no evaluation_log.")
    return s["test_file"].split(" ")[0], log.group(1)


def verify_provenance(predictions: Path) -> Dict:
    for p in (PROTOCOL, PROTOCOL_HASH):
        if not p.is_file():
            raise AcceptanceError(f"Protocol file not found: {p.name}")
    if _sha(PROTOCOL) != PROTOCOL_V4_SHA256 or PROTOCOL_HASH.read_text(encoding="utf-8").split()[0] != PROTOCOL_V4_SHA256:
        raise AcceptanceError("protocol_v4.json does not match the pinned SHA-256 (or its .sha256 record).")
    test_rel, log_rel = sealing_paths(json.loads(PROTOCOL.read_text(encoding="utf-8")))
    if not MANIFEST.is_file():
        raise AcceptanceError("Sealed v4 manifest not found.")
    try:
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise AcceptanceError(f"Sealed v4 manifest is not valid JSON: {exc}") from exc
    if manifest.get("protocol_sha256") != PROTOCOL_V4_SHA256:
        raise AcceptanceError("Manifest protocol hash differs from the pinned protocol v4 hash.")
    if manifest.get("protocol_file") != "docs/protocol_v4.json":
        raise AcceptanceError("Manifest protocol_file is not docs/protocol_v4.json.")
    sealed = manifest.get("sealed_files") or []
    if len(sealed) != 1:
        raise AcceptanceError("Manifest must list exactly one sealed test file.")
    if sealed[0].get("path") != test_rel or manifest.get("evaluation_log") != log_rel:
        raise AcceptanceError("Manifest sealed-file or evaluation-log path differs from protocol v4 sealing.")
    test_path = ROOT / sealed[0]["path"]
    if not test_path.is_file() or _sha(test_path) != sealed[0].get("sha256"):
        raise AcceptanceError("Sealed test file missing or changed since sealing.")
    log = ROOT / manifest["evaluation_log"]
    entries = [json.loads(l) for l in log.read_text(encoding="utf-8").splitlines() if l.strip()] if log.is_file() else []
    if not entries:
        raise AcceptanceError("No final sealed evaluation is logged; predictions are unavailable.")
    if len(entries) > 1:
        raise AcceptanceError("Evaluation log has more than one entry: the one-shot rule was violated.")
    entry = entries[0]
    if entry.get("test_sha256") != sealed[0]["sha256"]:
        raise AcceptanceError("Logged evaluation is not of the sealed test file.")
    if Path(entry.get("report_dir", "")).resolve() != predictions.parent.resolve():
        raise AcceptanceError("predictions.csv is not from the logged final evaluation's report directory.")
    return {"manifest": manifest, "log_entry": entry}


def load_predictions(path: Path, expected_rows: Optional[int],
                     expected_counts: Optional[Dict[str, Dict[str, int]]] = None) -> List[Dict[str, str]]:
    if not path.is_file():
        raise AcceptanceError(f"Predictions file not found: {path}")
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        missing = REQUIRED_COLUMNS - set(reader.fieldnames or [])
        if missing:
            raise AcceptanceError(f"Predictions file lacks columns {sorted(missing)}.")
        rows = list(reader)
    if not rows:
        raise AcceptanceError("Predictions file is empty.")
    bad = [r["row"] for r in rows if r["label"] not in LABELS or r["prediction"] not in ("", *LABELS)
           or r["rule_prediction"] not in LABELS]
    if bad:
        raise AcceptanceError(f"Invalid label/prediction values in rows {bad[:10]}.")
    if expected_rows is not None and len(rows) != expected_rows:
        raise AcceptanceError(f"Predictions have {len(rows)} rows; the sealed test has {expected_rows}.")
    if len({r["row"] for r in rows}) != len(rows):
        raise AcceptanceError("Predictions contain duplicate row ids.")
    if expected_counts:
        got: Dict[str, Dict[str, int]] = {}
        for r in rows:
            got.setdefault(r["source"], Counter())[r["label"]] += 1
        if {s: dict(c) for s, c in got.items()} != {s: dict(c) for s, c in expected_counts.items()}:
            raise AcceptanceError("Predictions' per-source / per-class counts differ from the manifest test_counts.")
    return rows


def coverage_from_rows(rows: List[Dict[str, str]]) -> Dict[str, Any]:
    """protocol minimum_legitimate_coverage.test_requirement, recomputed from the predictions."""
    don = [r for r in rows if r["source"] == "DONATION" and r["label"] == "legitimate"]
    cats = Counter(r["category"] for r in don)
    donors = {r["donor"] for r in don if r["donor"]}
    met = (len(don) >= COVERAGE_MIN_MESSAGES and len(donors) >= COVERAGE_MIN_DONORS
           and sum(1 for c in cats.values() if c >= COVERAGE_CATEGORY_N) >= COVERAGE_MIN_CATEGORIES)
    return {"donated_messages": len(don), "donors": len(donors), "categories": dict(cats), "met": met}


def _eff(r: Dict[str, str]) -> str:
    return r["prediction"] or "legitimate"


def _macro_f1(y: Sequence[str], p: Sequence[str]) -> float:
    return compute_metrics(list(y), list(p))["macro_avg"]["f1"]


def macro_f1_exact(y: Sequence[str], p: Sequence[str]) -> float:
    """Unrounded macro-F1 (compute_metrics rounds to 4 dp); used for M1/B1 pass/fail."""
    from sklearn.metrics import f1_score
    return float(f1_score(list(y), list(p), labels=list(LABELS), average="macro", zero_division=0))


def _meets(k: int, n: int, op: str, threshold: float) -> bool:
    """Exact k/n comparison with the protocol threshold (no rounding)."""
    if n == 0:
        return False
    q, t = Fraction(k, n), Fraction(str(threshold))
    return q >= t if op == ">=" else q <= t


def training_majority(manifest: Dict) -> str:
    """Majority class of the training pool, from the manifest's pool_counts {source: {label: n}}."""
    totals: Counter = Counter()
    for by_label in (manifest.get("pool_counts") or {}).values():
        totals.update(by_label)
    totals = Counter({k: v for k, v in totals.items() if k in LABELS})
    if not totals:
        raise AcceptanceError("Manifest has no pool_counts; the training-derived majority class is unavailable.")
    return max(totals.items(), key=lambda kv: (kv[1], kv[0]))[0]


def bootstrap_macro_f1(y: List[str], p: List[str]) -> List[float]:
    rng = random.Random(BOOTSTRAP_SEED)
    n = len(y)
    vals = []
    for _ in range(BOOTSTRAP_REPS):
        idx = [rng.randrange(n) for _ in range(n)]
        vals.append(_macro_f1([y[i] for i in idx], [p[i] for i in idx]))
    vals.sort()
    return [round(vals[int(0.025 * BOOTSTRAP_REPS)], 4), round(vals[int(0.975 * BOOTSTRAP_REPS) - 1], 4)]


def _rate(rows, positive: str) -> Dict[str, Any]:
    k = sum(1 for r in rows if _eff(r) == positive)
    n = len(rows)
    return {"numerator": k, "denominator": n, "value": round(k / n, 4) if n else None, "ci95": wilson(k, n)}


def evaluate_criteria(rows: List[Dict[str, str]], coverage_met: bool, bootstrap: bool = True, *,
                      training_majority: str) -> Dict[str, Any]:
    """*coverage_met* is the manifest's flag; L1 is evaluable only if the rows also meet it.
    *training_majority* is the majority class of the training pool (never chosen from test labels)."""
    if training_majority not in LABELS:
        raise AcceptanceError(f"Invalid training majority class {training_majority!r}.")
    imc = [r for r in rows if r["source"] == "IMC25" and r["label"] == "suspicious"]
    india = [r for r in imc if str(r["india_linked"]) == "1"]
    don = [r for r in rows if r["source"] == "DONATION" and r["label"] == "legitimate"]
    mwoz = [r for r in rows if r["source"] == "MWOZ_TEST" and r["label"] == "legitimate"]
    hey = [r for r in imc if r["category"] == "hey mum/dad"]
    recomputed = coverage_from_rows(rows)
    evaluable = bool(coverage_met) and recomputed["met"]
    y, p = [r["label"] for r in rows], [_eff(r) for r in rows]

    out: Dict[str, Any] = {}
    out["S1"] = {"name": "Suspicious recall, IMC25", "threshold": ">= 0.80", **_rate(imc, "suspicious")}
    out["S1"]["pass"] = _meets(out["S1"]["numerator"], len(imc), ">=", THRESHOLDS["S1"])
    out["S2"] = {"name": "Suspicious recall, India-linked IMC25", "threshold": ">= 0.80", **_rate(india, "suspicious")}
    out["S2"]["pass"] = _meets(out["S2"]["numerator"], len(india), ">=", THRESHOLDS["S2"])
    out["L1"] = {"name": "False-positive rate, donated genuine messages", "threshold": "<= 0.10", **_rate(don, "suspicious")}
    out["L1"]["coverage"] = {"manifest_met": bool(coverage_met), "recomputed": recomputed,
                             "requirement": f">= {COVERAGE_MIN_MESSAGES} donated messages from >= {COVERAGE_MIN_DONORS} "
                                            f"donors, >= {COVERAGE_MIN_CATEGORIES} categories with >= {COVERAGE_CATEGORY_N}"}
    if evaluable:
        out["L1"]["pass"] = _meets(out["L1"]["numerator"], len(don), "<=", THRESHOLDS["L1"])
    else:
        out["L1"]["pass"] = None
        out["L1"]["status"] = "NOT EVALUABLE (minimum donated-message coverage not met)"
    out["L2"] = {"name": "False-positive rate, MultiWOZ test turns", "threshold": "<= 0.05", **_rate(mwoz, "suspicious")}
    out["L2"]["pass"] = _meets(out["L2"]["numerator"], len(mwoz), "<=", THRESHOLDS["L2"])

    model = compute_metrics(y, p)
    rule_p = [r["rule_prediction"] for r in rows]
    rules = compute_metrics(y, rule_p)
    majority = compute_metrics(y, [training_majority] * len(y))
    f1_model = macro_f1_exact(y, p)
    f1_rules = macro_f1_exact(y, rule_p)
    f1_major = macro_f1_exact(y, [training_majority] * len(y))
    counts = Counter(y)
    test_major = max(counts, key=lambda k: (counts[k], k))
    out["M1"] = {"name": "Macro-F1, full test", "threshold": ">= 0.80", "value": model["macro_avg"]["f1"],
                 "denominator": len(rows), "note": "macro-F1 is not a single ratio; n rows and confusion matrix given",
                 "ci95_bootstrap": bootstrap_macro_f1(y, p) if bootstrap else None,
                 "pass": f1_model >= THRESHOLDS["M1"], "confusion_matrix": model["confusion_matrix"]}
    out["B1"] = {"name": "Macro-F1 above rule-engine and majority-class baselines", "value": model["macro_avg"]["f1"],
                 "threshold": "strictly greater than both (unrounded)", "denominator": len(rows),
                 "rule_engine_baseline_macro_f1": rules["macro_avg"]["f1"],
                 "majority_class_baseline_macro_f1": majority["macro_avg"]["f1"], "majority_predicts": training_majority,
                 "majority_class_source": "training pool label counts (manifest pool_counts); not test labels",
                 "always_legitimate_macro_f1_reported_only": compute_metrics(y, ["legitimate"] * len(y))["macro_avg"]["f1"],
                 "retrospective_reference_test_majority": {
                     "predicts": test_major,
                     "macro_f1": compute_metrics(y, [test_major] * len(y))["macro_avg"]["f1"],
                     "note": "RETROSPECTIVE REFERENCE ONLY: class chosen from test labels after the fact; "
                             "not a gating criterion and never used for model selection"},
                 "pass": f1_model > f1_rules and f1_model > f1_major}

    hey_rate = _rate(hey, "suspicious")
    small = len(hey) < SMALL_SUBGROUP_N
    out["reported_not_gating"] = {"hey_mum_dad_recall": {
        "name": "S1 restricted to IMC25 'hey mum/dad' test rows (reported, not gating)", **hey_rate,
        "small_sample": small,
        "warning": (f"Small subgroup: {len(hey)} 'hey mum/dad' test rows (the v4 dry run expected only about "
                    f"{EXPECTED_HEY_MUM_DAD_ROWS}). "
                    + (f"With fewer than {SMALL_SUBGROUP_N} rows this estimate is unreliable; see the wide CI. "
                       if small else "Interpret with the CI. ")
                    + "It does not gate the verdict.")}}

    by_source = {}
    for s in sorted({r["source"] for r in rows}):
        sr = [r for r in rows if r["source"] == s]
        entry = {"n": len(sr), "class_counts": dict(Counter(r["label"] for r in sr)),
                 "inconclusive": sum(1 for r in sr if not r["prediction"]),
                 "inconclusive_suspicious_counted_as_missed":
                     sum(1 for r in sr if not r["prediction"] and r["label"] == "suspicious")}
        for lab in LABELS:
            of = [r for r in sr if r["label"] == lab]
            if of:
                key = "recall_suspicious" if lab == "suspicious" else "false_positive_rate"
                entry[key] = _rate(of, "suspicious")
        by_source[s] = entry
    out["by_source"] = by_source
    out["by_class"] = {}
    for lab in LABELS:
        of = [r for r in rows if r["label"] == lab]
        hit = sum(1 for r in of if _eff(r) == lab)
        out["by_class"][lab] = {"n": counts.get(lab, 0), "precision": model[lab]["precision"],
                                "recall": model[lab]["recall"], "recall_numerator": hit,
                                "recall_ci95": wilson(hit, len(of)), "f1": model[lab]["f1"]}
    out["inconclusive"] = {"total": sum(1 for r in rows if not r["prediction"]),
                           "suspicious_counted_as_missed": sum(1 for r in rows if not r["prediction"] and r["label"] == "suspicious"),
                           "legitimate_not_counted_as_fp": sum(1 for r in rows if not r["prediction"] and r["label"] == "legitimate")}

    gating = [out[k]["pass"] for k in ("S1", "S2", "L2", "M1", "B1")]
    if not all(gating) or out["L1"]["pass"] is False:
        verdict = "NOT VALIDATED"
    elif out["L1"]["pass"] is None:
        verdict = "PARTIALLY EVALUATED — not validated for genuine financial alerts"
    else:
        verdict = "VALIDATED (advisory-only)"
    out["verdict"] = verdict
    out["notes"] = ["Pass/fail uses protocol thresholds on exact (unrounded) point estimates; confidence intervals "
                    "are reported only.",
                    "B1 majority class comes from training-pool label counts; the test-majority figure is a "
                    "retrospective reference only.",
                    "This file, not evaluation.json, is authoritative for criteria: evaluation.json excludes "
                    "inconclusive rows from its metrics.",
                    "Inconclusive suspicious predictions count as misses; inconclusive legitimate are not false positives.",
                    "MENDELEY is training-only and never appears in this test.",
                    "Even if VALIDATED, the classifier remains advisory-only."]
    if out["L1"]["pass"] is None:
        out["notes"].insert(0, "Scam recall evaluated; false positives on genuine Indian financial alerts NOT validated.")
    return out


def render(result: Dict[str, Any]) -> str:
    lines = [f"# Protocol v4 acceptance — {result['verdict']}", "", "| Criterion | Value | 95% CI | n | Threshold | Result |",
             "|---|---|---|---|---|---|"]
    for k in ("S1", "S2", "L1", "L2", "M1"):
        c = result[k]
        res = c.get("status") or ("PASS" if c["pass"] else "FAIL")
        lines.append(f"| {k} {c['name']} | {c['value']} | {c.get('ci95') or c.get('ci95_bootstrap')} | "
                     f"{c.get('denominator', '—')} | {c['threshold']} | {res} |")
    b = result["B1"]
    lines.append(f"| B1 {b['name']} | {b['value']} vs rules {b['rule_engine_baseline_macro_f1']} / training-majority "
                 f"'{b['majority_predicts']}' {b['majority_class_baseline_macro_f1']} | — | — | strictly greater | "
                 f"{'PASS' if b['pass'] else 'FAIL'} |")
    t = b["retrospective_reference_test_majority"]
    lines.append(f"\nRetrospective reference only (not gating): test-majority '{t['predicts']}' macro-F1 {t['macro_f1']}; "
                 f"always-legitimate macro-F1 {b['always_legitimate_macro_f1_reported_only']}.")
    h = result["reported_not_gating"]["hey_mum_dad_recall"]
    lines += ["", f"'hey mum/dad' recall (reported, not gating): {h['value']} ({h['numerator']}/{h['denominator']}), "
                  f"CI {h['ci95']} — WARNING: {h['warning']}", "",
              "| Source | n | Classes | Inconclusive | Scam recall (k/n, 95% CI) | FPR (k/n, 95% CI) |",
              "|---|---|---|---|---|---|"]
    fmt = lambda x: f"{x['value']} ({x['numerator']}/{x['denominator']}, {x['ci95']})" if x else "—"  # noqa: E731
    for s, e in result["by_source"].items():
        lines.append(f"| {s} | {e['n']} | {e['class_counts']} | {e['inconclusive']} | "
                     f"{fmt(e.get('recall_suspicious'))} | {fmt(e.get('false_positive_rate'))} |")
    lines += ["", "| Class | n | Precision | Recall (95% CI) | F1 |", "|---|---|---|---|---|"]
    for lab, c in result["by_class"].items():
        lines.append(f"| {lab} | {c['n']} | {c['precision']} | {c['recall']} ({c['recall_ci95']}) | {c['f1']} |")
    lines += ["", *[f"- {n}" for n in result["notes"]]]
    return "\n".join(lines) + "\n"


def run(predictions: Path, bootstrap: bool = True) -> Dict[str, Any]:
    prov = verify_provenance(predictions)
    manifest = prov["manifest"]
    counts = manifest.get("test_counts") or {}
    expected = sum(sum(v.values()) for v in counts.values()) or None
    rows = load_predictions(predictions, expected, counts)
    manifest_met = bool((manifest.get("coverage") or {}).get("met"))
    if manifest_met != coverage_from_rows(rows)["met"]:
        raise AcceptanceError("Manifest coverage flag disagrees with the donated-message coverage in the predictions.")
    result = evaluate_criteria(rows, manifest_met, bootstrap=bootstrap, training_majority=training_majority(manifest))
    result.update(protocol_sha256=PROTOCOL_V4_SHA256, evaluated_model=prov["log_entry"].get("model_version"),
                  test_sha256=prov["log_entry"].get("test_sha256"), coverage=manifest.get("coverage"))
    (predictions.parent / "acceptance_v4.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    (predictions.parent / "acceptance_v4.md").write_text(render(result), encoding="utf-8")
    return result


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--predictions", required=True)
    args = ap.parse_args(argv)
    try:
        result = run(Path(args.predictions))
    except AcceptanceError as exc:
        print(f"STOP: {exc}", file=sys.stderr)
        return 3
    print(render(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
