"""
acceptance_v3.py — Apply docs/protocol_v3.json acceptance_criteria
==================================================================
Reads ONLY the per-row predictions.csv written by the one-shot
`evaluate --final-sealed-evaluation` (no message text) and the sealed
manifest's coverage block. Never opens the sealed test file.

Conventions (protocol): an inconclusive prediction counts as 'legitimate'
— a MISS for suspicious rows, NOT a false positive for legitimate rows.

    python scripts/acceptance_v3.py --predictions reports/frozen_test_v3_evaluation/predictions.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from satyalens_analysis.text_classifier import compute_metrics  # noqa: E402

MANIFEST = REPO / "reports" / "frozen_test_v3_manifest.json"
THRESHOLDS = {"S1": 0.80, "S2": 0.80, "L1": 0.10, "L2": 0.05, "M1": 0.80}


def _rate(num: int, den: int) -> Optional[float]:
    return round(num / den, 4) if den else None


def evaluate_criteria(rows: List[Dict[str, str]], coverage_met: bool) -> Dict[str, Any]:
    eff = [r["prediction"] or "legitimate" for r in rows]
    y = [r["label"] for r in rows]

    def recall(sel):
        idx = [i for i, r in enumerate(rows) if sel(r)]
        hit = sum(1 for i in idx if eff[i] == "suspicious")
        return hit, len(idx)

    def fpr(sel):
        idx = [i for i, r in enumerate(rows) if sel(r)]
        fp = sum(1 for i in idx if eff[i] == "suspicious")
        return fp, len(idx)

    out: Dict[str, Any] = {}
    n, d = recall(lambda r: r.get("source") == "IMC25")
    out["S1"] = {"name": "Suspicious recall, IMC25", "value": _rate(n, d), "numerator": n, "denominator": d,
                 "threshold": ">= 0.80", "pass": d > 0 and n / d >= THRESHOLDS["S1"]}
    n, d = recall(lambda r: r.get("source") == "IMC25" and str(r.get("india_linked")) == "1")
    out["S2"] = {"name": "Suspicious recall, India-linked IMC25", "value": _rate(n, d), "numerator": n,
                 "denominator": d, "threshold": ">= 0.80", "pass": d > 0 and n / d >= THRESHOLDS["S2"]}
    n, d = fpr(lambda r: r.get("source") == "DONATION")
    if coverage_met:
        out["L1"] = {"name": "FPR, donated genuine messages", "value": _rate(n, d), "numerator": n,
                     "denominator": d, "threshold": "<= 0.10", "pass": d > 0 and n / d <= THRESHOLDS["L1"]}
    else:
        out["L1"] = {"name": "FPR, donated genuine messages", "value": _rate(n, d), "numerator": n,
                     "denominator": d, "threshold": "<= 0.10", "pass": None,
                     "status": "NOT EVALUABLE (minimum donated-message coverage not met)"}
    n, d = fpr(lambda r: r.get("source") == "MWOZ_TEST")
    out["L2"] = {"name": "FPR, MultiWOZ test turns", "value": _rate(n, d), "numerator": n, "denominator": d,
                 "threshold": "<= 0.05", "pass": d > 0 and n / d <= THRESHOLDS["L2"]}

    model = compute_metrics(y, eff)
    rules = compute_metrics(y, [r["rule_prediction"] for r in rows])
    counts = Counter(y)
    majority_label = max(counts, key=lambda k: (counts[k], k))
    majority = compute_metrics(y, [majority_label] * len(y))
    out["M1"] = {"name": "Macro-F1, full test", "value": model["macro_avg"]["f1"], "threshold": ">= 0.80",
                 "pass": model["macro_avg"]["f1"] >= THRESHOLDS["M1"],
                 "confusion_matrix": model["confusion_matrix"]}
    out["B1"] = {"name": "Macro-F1 above rule and majority baselines", "value": model["macro_avg"]["f1"],
                 "rule_baseline_macro_f1": rules["macro_avg"]["f1"],
                 "majority_baseline_macro_f1": majority["macro_avg"]["f1"], "majority_predicts": majority_label,
                 "pass": model["macro_avg"]["f1"] > rules["macro_avg"]["f1"]
                 and model["macro_avg"]["f1"] > majority["macro_avg"]["f1"]}

    others = [out[k]["pass"] for k in ("S1", "S2", "L2", "M1", "B1")]
    if not all(others) or out["L1"]["pass"] is False:
        verdict = "NOT VALIDATED"
    elif out["L1"]["pass"] is None:
        verdict = "PARTIALLY EVALUATED — not validated for genuine financial alerts"
    else:
        verdict = "VALIDATED (advisory-only)"
    out["inconclusive"] = {"total": sum(1 for r in rows if not r["prediction"]),
                           "suspicious_counted_as_missed": sum(1 for r in rows if not r["prediction"]
                                                               and r["label"] == "suspicious"),
                           "legitimate": sum(1 for r in rows if not r["prediction"] and r["label"] == "legitimate")}
    out["verdict"] = verdict
    return out


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--predictions", required=True)
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    with Path(args.predictions).open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    result = evaluate_criteria(rows, bool(manifest["coverage"]["met"]))
    result["protocol_sha256"] = manifest["protocol_sha256"]
    out = Path(args.out) if args.out else Path(args.predictions).with_name("acceptance.json")
    out.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
