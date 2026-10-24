"""
select_text_model_v4.py — Model selection under docs/protocol_v4.json
=====================================================================
Pinned to protocol v4 SHA-256 f32adc7668ca9f4d44bee2a3a358c4191ba369e32824421de69f77c9c54c0a49.

Preconditions (all enforced, else SelectionError):
  * docs/protocol_v4.json matches the pin and docs/protocol_v4.sha256;
  * the v4 sealed manifest exists (protocol build_order: seal BEFORE selection),
    is valid JSON, carries the pinned protocol hash, lists the sealed file(s),
    and records the SHA-256 of the pool that was built and leakage-checked
    together with the sealed test;
  * data/training_pool_v4.csv matches that recorded pool hash;
  * the pool is not itself a sealed file or a byte-identical copy of one
    (checked against this manifest directly, so it holds even if
    SATYALENS_SEALED_MANIFEST points the package guard elsewhere);
  * no final sealed evaluation has been logged yet (re-selecting after test
    feedback would be tuning on the test). Only the log's size is checked.
The pool is loaded only through text_classifier.load_dataset (sealed-file
guard). The sealed test, its labels and evaluation outputs are never read.

Scoring convention: a row with no in-vocabulary feature is 'inconclusive'
and is scored as 'legitimate' (protocol acceptance_criteria.conventions: a
miss for suspicious rows, not a false positive for legitimate rows), so a
candidate cannot gain macro-F1 by abstaining. Inconclusive counts are reported.

Selection (protocol model_selection, unchanged): StratifiedGroupKFold(5,
shuffle, seed 42) over pool units; candidates A, C, E, G, H; maximise pooled
out-of-fold macro-F1 among candidates with out-of-fold FPR <= 0.10 on DONATION
rows (only if the pool has >= 50 DONATION rows); ties within 0.005 -> lower
OOF legitimate FPR -> name. Baselines (reported only, never selected):
majority class of each TRAINING fold (no held-out label chooses the class),
always-legitimate, rule engine, deployed v1, candidate v2.

    python scripts/select_text_model_v4.py                   # report only
    python scripts/select_text_model_v4.py --train-selected  # + train candidate v4 (needs approval)
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))

from satyalens_analysis import text_classifier as tc  # noqa: E402
from satyalens_analysis.context_analyser import analyse_context  # noqa: E402
from select_text_model_v2 import _by, _predict, _score  # noqa: E402

PROTOCOL_V4_SHA256 = "f32adc7668ca9f4d44bee2a3a358c4191ba369e32824421de69f77c9c54c0a49"
PROTOCOL = REPO / "docs" / "protocol_v4.json"
PROTOCOL_HASH = REPO / "docs" / "protocol_v4.sha256"
MANIFEST = REPO / "reports" / "frozen_test_v4_manifest.json"
POOL = REPO / "data" / "training_pool_v4.csv"
OUT = REPO / "reports" / "model_selection_v4"
CANDIDATE_DIR = REPO / "satyalens_analysis" / "models" / "candidates" / "v4"
V2_DIR = REPO / "satyalens_analysis" / "models" / "candidates" / "v2"
TIE = 0.005
DONATION_CONSTRAINT_MIN_ROWS = 50
DONATION_MAX_FPR = 0.10
FOLDS, SEED = 5, 42


class SelectionError(RuntimeError):
    pass


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _word(**over) -> Dict[str, Any]:
    hp = copy.deepcopy(tc.HYPERPARAMETERS)
    c = over.pop("C", None)
    hp["tfidf"].update(over)
    if c is not None:
        hp["logistic_regression"]["C"] = c
    return hp


def _char(C: float) -> Dict[str, Any]:
    hp = copy.deepcopy(tc.HYPERPARAMETERS)
    hp["tfidf"] = {"lowercase": True, "strip_accents": "unicode", "analyzer": "char_wb",
                   "ngram_range": [3, 5], "min_df": 2, "sublinear_tf": True}
    hp["logistic_regression"]["C"] = C
    return hp


# Exactly protocol v4 model_selection.candidates.
CANDIDATES: Dict[str, Dict[str, Any]] = {
    "A_v1_config": _word(),
    "C_stopwords_unigram": _word(stop_words="english", ngram_range=[1, 1]),
    "E_stopwords_C3": _word(stop_words="english", C=3.0),
    "G_char_wb_3_5": _char(1.0),
    "H_char_wb_3_5_C3": _char(3.0),
}


def verify_protocol() -> Dict:
    for p in (PROTOCOL, PROTOCOL_HASH):
        if not p.is_file():
            raise SelectionError(f"Protocol file not found: {p.name}")
    actual = _sha(PROTOCOL)
    recorded = PROTOCOL_HASH.read_text(encoding="utf-8").split()[0]
    if actual != PROTOCOL_V4_SHA256 or recorded != PROTOCOL_V4_SHA256:
        raise SelectionError("protocol_v4.json does not match the pinned SHA-256 (or its .sha256 record).")
    return json.loads(PROTOCOL.read_text(encoding="utf-8"))


def verify_inputs() -> Dict:
    """Manifest + pool preconditions; never opens the sealed test or the evaluation log."""
    if not MANIFEST.is_file():
        raise SelectionError("Sealed v4 manifest not found: selection runs only after the test is sealed.")
    try:
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SelectionError(f"Sealed v4 manifest is not valid JSON: {exc}") from exc
    if manifest.get("protocol_sha256") != PROTOCOL_V4_SHA256:
        raise SelectionError("Manifest protocol hash differs from the pinned protocol v4 hash.")
    sealed = manifest.get("sealed_files") or []
    if not sealed or not all(isinstance(e, dict) and e.get("path") and e.get("sha256") for e in sealed):
        raise SelectionError("Manifest lists no sealed file (path + sha256); cannot guard against the test.")
    log = MANIFEST.parent.parent / str(manifest.get("evaluation_log") or "")
    if manifest.get("evaluation_log") and log.is_file() and log.stat().st_size > 0:
        raise SelectionError("A final sealed evaluation is already logged; selecting after test feedback is refused.")
    if not POOL.is_file():
        raise SelectionError(f"Training pool not found: {POOL.name}")
    pool_sha = _sha(POOL)
    if pool_sha != manifest.get("pool_csv_sha256"):
        raise SelectionError("Training pool SHA-256 differs from the pool recorded at sealing.")
    base = MANIFEST.resolve().parent.parent
    if (any(pool_sha == e["sha256"] or (base / e["path"]).resolve() == POOL.resolve() for e in sealed)
            or tc.is_sealed(POOL)):
        raise SelectionError("Training pool is a sealed file (or an identical copy of one).")
    return manifest


def effective(preds: List[Optional[str]]) -> List[str]:
    """Protocol convention: inconclusive (None) is scored as 'legitimate'."""
    return [p or "legitimate" for p in preds]


def _scored(y, src, preds) -> Dict[str, Any]:
    eff = effective(preds)
    by = _by(src, y, eff)
    for s, entry in by.items():
        idx = [i for i, x in enumerate(src) if x == s]
        entry["inconclusive"] = sum(1 for i in idx if preds[i] is None)
        entry["inconclusive_suspicious_counted_as_missed"] = sum(
            1 for i in idx if preds[i] is None and y[i] == "suspicious")
    oof = _score(y, eff)
    oof["inconclusive"] = sum(1 for p in preds if p is None)
    oof["inconclusive_suspicious_counted_as_missed"] = sum(
        1 for t, p in zip(y, preds) if p is None and t == "suspicious")
    return {"oof": oof, "by_source": by}


def folds(examples) -> List[Any]:
    """The protocol's CV splits over pool units (identical for every candidate and baseline)."""
    from sklearn.model_selection import StratifiedGroupKFold
    return list(StratifiedGroupKFold(n_splits=FOLDS, shuffle=True, random_state=SEED).split(
        [e.text for e in examples], [e.label for e in examples], [e.group for e in examples]))


def _majority(labels) -> str:
    return max(Counter(labels).items(), key=lambda kv: (kv[1], kv[0]))[0]


def training_majority_oof(examples, splits) -> List[str]:
    """Each held-out fold gets the majority class of its TRAINING folds."""
    y = [e.label for e in examples]
    preds = [""] * len(examples)
    for tr, te in splits:
        maj = _majority(y[i] for i in tr)
        for i in te:
            preds[i] = maj
    return preds


def grouped_oof(examples, hp, splits) -> Tuple[List[Optional[str]], Dict[str, Any]]:
    """Out-of-fold predictions; fold macro-F1 uses the same inconclusive convention as the pooled score."""
    texts = [e.text for e in examples]
    y = [e.label for e in examples]
    preds: List[Optional[str]] = [None] * len(examples)
    fold_f1, cov = [], []
    for tr, te in splits:
        pipe = tc.build_pipeline(hp).fit([texts[i] for i in tr], [y[i] for i in tr])
        p, c = _predict(pipe, [texts[i] for i in te])
        for i, v in zip(te, p):
            preds[i] = v
        fold_f1.append(_score([y[i] for i in te], effective(p))["macro_avg"]["f1"])
        cov.append(c)
    mean = sum(fold_f1) / len(fold_f1)
    std = (sum((f - mean) ** 2 for f in fold_f1) / len(fold_f1)) ** 0.5
    return preds, {"fold_macro_f1": fold_f1, "fold_macro_f1_mean": round(mean, 4), "fold_macro_f1_std": round(std, 4),
                   "vocabulary_coverage_mean": {k: round(sum(c[k] for c in cov) / len(cov), 4) for k in cov[0]}}


def donation_fpr(examples, preds) -> Optional[float]:
    idx = [i for i, e in enumerate(examples) if e.group.startswith("DONATION:")]
    if len(idx) < DONATION_CONSTRAINT_MIN_ROWS:
        return None
    return round(sum(1 for i in idx if preds[i] == "suspicious") / len(idx), 4)


def choose(results: Dict[str, Any]) -> str:
    eligible = [n for n, r in results.items() if r["kind"] == "config"
                and (r["donation_oof_fpr"] is None or r["donation_oof_fpr"] <= DONATION_MAX_FPR)]
    if not eligible:
        raise SelectionError("No candidate meets the DONATION false-positive constraint.")
    best = max(results[n]["oof"]["macro_avg"]["f1"] for n in eligible)
    # F1 values are 4-dp; round the gap so an exact 0.005 gap is a tie (0.850 - 0.845 > 0.005 in floats).
    tied = [n for n in eligible if round(best - results[n]["oof"]["macro_avg"]["f1"], 4) <= TIE]
    return min(tied, key=lambda n: (results[n]["oof"]["legitimate_false_positive_rate"], n))


def run(train_selected: bool = False) -> Dict[str, Any]:
    verify_protocol()
    manifest = verify_inputs()
    pool = tc.load_dataset(POOL)                      # sealed-file guard applies here too
    y = [e.label for e in pool]
    src = [e.group.split(":", 1)[0] for e in pool]
    results: Dict[str, Any] = {}
    splits = folds(pool)

    mp = training_majority_oof(pool, splits)
    results["baseline_majority_class"] = {"kind": "baseline", "predicts_by_fold": sorted(set(mp)),
                                          **_scored(y, src, mp),
                                          "note": "majority class of each training fold; reported only, not a selection criterion"}
    results["baseline_always_legitimate"] = {"kind": "baseline", **_scored(y, src, ["legitimate"] * len(y)),
                                             "note": "fixed predeclared baseline; reported only, not a selection criterion"}
    rp = ["suspicious" if analyse_context(e.text).flags else "legitimate" for e in pool]
    results["baseline_rule_engine"] = {"kind": "baseline", **_scored(y, src, rp)}
    for name, d in (("deployed_v1", tc.DEFAULT_MODEL_DIR), ("candidate_v2", V2_DIR)):
        try:
            m = tc.load_model(d)
            p, cov = _predict(m.pipeline, [e.text for e in pool])
            results[name] = {"kind": "reference", "model_version": m.meta["model_version"],
                             "note": "trained on other data that may overlap this pool; reported only",
                             **_scored(y, src, p), "vocabulary_coverage": cov}
        except (tc.ModelUnavailableError, tc.ModelInvalidError) as exc:
            results[name] = {"kind": "reference", "unavailable": str(exc)}

    for name, hp in CANDIDATES.items():
        preds, cv = grouped_oof(pool, hp, splits)
        results[name] = {"kind": "config", "hyperparameters": hp, **_scored(y, src, preds),
                         "donation_oof_fpr": donation_fpr(pool, preds), **cv}

    selected = choose(results)
    report = {"protocol_sha256": PROTOCOL_V4_SHA256, "manifest_sealed_utc": manifest.get("sealed_utc"),
              "pool": POOL.name, "pool_sha256": _sha(POOL), "rows": len(pool),
              "units": len({e.group for e in pool}), "class_counts": dict(Counter(y)),
              "by_source": {s: dict(Counter(y[i] for i, x in enumerate(src) if x == s)) for s in sorted(set(src))},
              "cv": f"StratifiedGroupKFold(n_splits={FOLDS}, shuffle=True, random_state={SEED})",
              "donation_constraint_applied": any(r.get("donation_oof_fpr") is not None for r in results.values()),
              "selection_rule": (f"max pooled OOF macro-F1 among candidates with DONATION OOF FPR <= {DONATION_MAX_FPR} "
                                 f"(only if >= {DONATION_CONSTRAINT_MIN_ROWS} DONATION rows); ties within {TIE} -> "
                                 "lower OOF legitimate FPR -> alphabetical"),
              "scoring_convention": "inconclusive scored as legitimate (miss for suspicious, not a false positive)",
              "selected": selected, "sealed_test_read": False, "results": results}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "selection.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    if train_selected:
        meta = tc.train(POOL, model_dir=CANDIDATE_DIR, hyperparameters=CANDIDATES[selected],
                        preprocessing=tc.PREPROCESSING_ANON_V2)
        report["candidate_v4"] = {"model_version": meta["model_version"], "dir": str(CANDIDATE_DIR)}
        (OUT / "selection.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--train-selected", action="store_true")
    args = ap.parse_args(argv)
    try:
        report = run(args.train_selected)
    except (SelectionError, tc.SealedDataError) as exc:
        print(f"STOP: {exc}", file=sys.stderr)
        return 3
    print(f"Selected: {report['selected']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
