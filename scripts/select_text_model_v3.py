"""
select_text_model_v3.py — Model selection under docs/protocol_v3.json
=====================================================================
Uses ONLY data/training_pool_v3.csv (loaded through the package loader, which
refuses sealed files). Grouped 5-fold CV over pool groups; the fixed
candidates and rule from protocol model_selection; baselines reported only.

    python scripts/select_text_model_v3.py                    # report only
    python scripts/select_text_model_v3.py --train-selected   # + train candidate v3

The candidate is written to satyalens_analysis/models/candidates/v3/ with
preprocessing='anon-v1'. The deployed artifact is never touched.
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))

from satyalens_analysis import text_classifier as tc  # noqa: E402
from satyalens_analysis.context_analyser import analyse_context  # noqa: E402
from select_text_model_v2 import _by, _predict, _score, grouped_oof  # noqa: E402

POOL = REPO / "data" / "training_pool_v3.csv"
OUT = REPO / "reports" / "model_selection_v3"
CANDIDATE_DIR = REPO / "satyalens_analysis" / "models" / "candidates" / "v3"
V2_DIR = REPO / "satyalens_analysis" / "models" / "candidates" / "v2"
TIE = 0.005
DONATION_CONSTRAINT_MIN_ROWS = 50
DONATION_MAX_FPR = 0.10


def _word(**over) -> Dict[str, Any]:
    hp = copy.deepcopy(tc.HYPERPARAMETERS)
    c = over.pop("C", None)
    hp["tfidf"].update(over)
    if c is not None:
        hp["logistic_regression"]["C"] = c
    return hp


def _char(C: float = 1.0) -> Dict[str, Any]:
    hp = copy.deepcopy(tc.HYPERPARAMETERS)
    hp["tfidf"] = {"lowercase": True, "strip_accents": "unicode", "analyzer": "char_wb",
                   "ngram_range": [3, 5], "min_df": 2, "sublinear_tf": True}
    hp["logistic_regression"]["C"] = C
    return hp


# Exactly the candidates listed in protocol_v3 model_selection.candidates.
CANDIDATES: Dict[str, Dict[str, Any]] = {
    "A_v1_config": _word(),
    "C_stopwords_unigram": _word(stop_words="english", ngram_range=[1, 1]),
    "E_stopwords_C3": _word(stop_words="english", C=3.0),
    "G_char_wb_3_5": _char(1.0),
    "H_char_wb_3_5_C3": _char(3.0),
}


def donation_fpr(examples, preds) -> Optional[float]:
    idx = [i for i, e in enumerate(examples) if e.group.startswith("DONATION:")]
    if len(idx) < DONATION_CONSTRAINT_MIN_ROWS:
        return None
    return sum(1 for i in idx if preds[i] == "suspicious") / len(idx)


def choose(results: Dict[str, Any]) -> str:
    """Protocol rule: max OOF macro-F1 among candidates meeting the donation-FPR
    constraint (when applicable); ties within 0.005 -> lower legit FPR -> name."""
    eligible = [n for n, r in results.items() if r["kind"] == "config"
                and (r["donation_oof_fpr"] is None or r["donation_oof_fpr"] <= DONATION_MAX_FPR)]
    if not eligible:
        raise RuntimeError("No candidate meets the donation false-positive constraint.")
    best = max(results[n]["oof"]["macro_avg"]["f1"] for n in eligible)
    tied = [n for n in eligible if best - results[n]["oof"]["macro_avg"]["f1"] <= TIE]
    return min(tied, key=lambda n: (results[n]["oof"]["legitimate_false_positive_rate"], n))


def run(train_selected: bool) -> Dict[str, Any]:
    pool = tc.load_dataset(POOL)
    y = [e.label for e in pool]
    src = [e.group.split(":", 1)[0] for e in pool]
    results: Dict[str, Any] = {}

    maj = Counter(y).most_common(1)[0][0]
    results["baseline_majority"] = {"kind": "baseline", "predicts": maj, "oof": _score(y, [maj] * len(y))}
    rp = ["suspicious" if analyse_context(e.text).flags else "legitimate" for e in pool]
    results["baseline_rules"] = {"kind": "baseline", "oof": _score(y, rp), "by_source": _by(src, y, rp),
                                 "note": "rules applied to anonymised pool text"}
    for name, d in (("deployed_v1", tc.DEFAULT_MODEL_DIR), ("candidate_v2", V2_DIR)):
        try:
            m = tc.load_model(d)
            p, cov = _predict(m.pipeline, [e.text for e in pool])
            results[name] = {"kind": "reference", "model_version": m.meta["model_version"], "oof": _score(y, p),
                             "by_source": _by(src, y, p), "vocabulary_coverage": cov,
                             "note": "trained on other data; scored on all pool rows (anonymised text)"}
        except (tc.ModelUnavailableError, tc.ModelInvalidError) as exc:
            results[name] = {"kind": "reference", "unavailable": str(exc)}

    for name, hp in CANDIDATES.items():
        preds, cv = grouped_oof(pool, hp)
        results[name] = {"kind": "config", "hyperparameters": hp, "oof": _score(y, preds),
                         "by_source": _by(src, y, preds), "donation_oof_fpr": donation_fpr(pool, preds), **cv}
        print(f"{name}: OOF macro-F1 {results[name]['oof']['macro_avg']['f1']} "
              f"legit FPR {results[name]['oof']['legitimate_false_positive_rate']}")

    selected = choose(results)
    report = {"protocol_sha256": json.loads((REPO / "reports" / "frozen_test_v3_manifest.json").read_text(
                  encoding="utf-8"))["protocol_sha256"] if (REPO / "reports" / "frozen_test_v3_manifest.json").is_file()
              else None,
              "pool": POOL.name, "pool_sha256": tc._sha256(POOL), "rows": len(pool),
              "groups": len({e.group for e in pool}), "class_counts": dict(Counter(y)),
              "by_source": {s: dict(Counter(y[i] for i, x in enumerate(src) if x == s)) for s in sorted(set(src))},
              "donation_constraint_applied": any(r.get("donation_oof_fpr") is not None for r in results.values()),
              "selected": selected, "frozen_test_used": False, "results": results}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "selection.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    if train_selected:
        meta = tc.train(POOL, model_dir=CANDIDATE_DIR, hyperparameters=CANDIDATES[selected],
                        preprocessing=tc.PREPROCESSING_ANON_V1)
        report["candidate_v3"] = {"model_version": meta["model_version"], "dir": str(CANDIDATE_DIR)}
        (OUT / "selection.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--train-selected", action="store_true")
    report = run(ap.parse_args(argv).train_selected)
    print(f"Selected: {report['selected']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
