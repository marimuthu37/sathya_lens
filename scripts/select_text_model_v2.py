"""
select_text_model_v2.py — Compare TF-IDF + LR configurations on VALIDATION data
===============================================================================

Model selection uses ONLY:
  * grouped, stratified 5-fold cross-validation over data/training_pool_v2.csv
    (StratifiedGroupKFold, seed 42; whole groups — template families, R115
    generation batches, MultiWOZ dialogues — stay in one fold), and
  * data/challenge_suite_v1.csv (author-written behavioural checks, reported
    per category; never trained on).
The frozen evaluation set (data/real_world_test_v1.csv) is NOT read here.

Selection rule (fixed before running): highest pooled out-of-fold macro-F1;
ties within 0.005 are broken by the lower legitimate false-positive rate.

Compared:
  baselines  majority class; rule baseline (context_analyser: any flag ->
             suspicious); current deployed artifact (on rows it never saw)
  configs    TF-IDF + LogisticRegression variants (CONFIGS below)

Outputs reports/model_selection_v2/{selection.json, summary.md}; with
--train-selected also trains the winner into
satyalens_analysis/models/candidates/v2/ (the deployed artifact is untouched).
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from satyalens_analysis import text_classifier as tc  # noqa: E402
from satyalens_analysis.context_analyser import analyse_context  # noqa: E402

POOL = REPO / "data" / "training_pool_v2.csv"
CHALLENGE = REPO / "data" / "challenge_suite_v1.csv"
OUT = REPO / "reports" / "model_selection_v2"
CANDIDATE_DIR = REPO / "satyalens_analysis" / "models" / "candidates" / "v2"
SEED = 42
FOLDS = 5
TIE = 0.005


def _cfg(**tfidf_overrides) -> Dict[str, Any]:
    hp = copy.deepcopy(tc.HYPERPARAMETERS)
    c = tfidf_overrides.pop("C", None)
    hp["tfidf"].update(tfidf_overrides)
    if c is not None:
        hp["logistic_regression"]["C"] = c
    return hp


def _char_cfg() -> Dict[str, Any]:
    hp = copy.deepcopy(tc.HYPERPARAMETERS)
    hp["tfidf"] = {"lowercase": True, "strip_accents": "unicode", "analyzer": "char_wb",
                   "ngram_range": [3, 5], "min_df": 2, "sublinear_tf": True}
    return hp


CONFIGS: Dict[str, Dict[str, Any]] = {
    "A_v1_config": _cfg(),
    "B_stopwords": _cfg(stop_words="english"),
    "C_stopwords_unigram": _cfg(stop_words="english", ngram_range=[1, 1]),
    "D_stopwords_C0.3": _cfg(stop_words="english", C=0.3),
    "E_stopwords_C3": _cfg(stop_words="english", C=3.0),
    "F_maxdf0.3": _cfg(max_df=0.3),
    "G_char_wb_3_5": _char_cfg(),
}


def _predict(pipeline, texts: Sequence[str]) -> Tuple[List[Optional[str]], Dict[str, float]]:
    """Predictions with the component's 'inconclusive' rule + vocabulary coverage."""
    tfidf = pipeline.named_steps["tfidf"]
    clf = pipeline.named_steps["clf"]
    X = tfidf.transform(list(texts))
    nnz = X.getnnz(axis=1)
    raw = clf.predict(X)
    preds = [None if nnz[i] == 0 else str(raw[i]) for i in range(len(texts))]
    analyzer, vocab = tfidf.build_analyzer(), tfidf.vocabulary_
    tok_total = tok_hit = 0
    for t in texts:
        toks = analyzer(t)
        tok_total += len(toks)
        tok_hit += sum(1 for k in toks if k in vocab)
    return preds, {"rows_with_any_known_feature": round(float((nnz > 0).mean()), 4),
                   "feature_token_coverage": round(tok_hit / tok_total, 4) if tok_total else 0.0}


def _score(y_true: Sequence[str], preds: Sequence[Optional[str]]) -> Dict[str, Any]:
    pairs = [(t, p) for t, p in zip(y_true, preds) if p is not None]
    m = tc.compute_metrics([t for t, _ in pairs], [p for _, p in pairs])
    m.update(tc._error_rates(m))
    m["classified"] = len(pairs)
    m["inconclusive"] = len(preds) - len(pairs)
    return m


def _by(values: Sequence[str], y: Sequence[str], preds: Sequence[Optional[str]]) -> Dict[str, Any]:
    out = {}
    for v in sorted(set(values)):
        idx = [i for i, x in enumerate(values) if x == v]
        yt = [y[i] for i in idx]
        pp = [preds[i] for i in idx]
        scored = [(a, b) for a, b in zip(yt, pp) if b is not None]
        entry = {"n": len(idx), "class_counts": dict(Counter(yt)), "inconclusive": len(idx) - len(scored),
                 "accuracy": round(sum(a == b for a, b in scored) / len(scored), 4) if scored else None}
        for lab in tc.LABELS:
            of = [(a, b) for a, b in scored if a == lab]
            if of:
                entry[f"recall_{lab}"] = round(sum(a == b for a, b in of) / len(of), 4)
        out[v] = entry
    return out


def grouped_oof(examples, hp) -> Tuple[List[Optional[str]], Dict[str, Any]]:
    from sklearn.model_selection import StratifiedGroupKFold
    texts = [e.text for e in examples]
    y = [e.label for e in examples]
    groups = [e.group for e in examples]
    preds: List[Optional[str]] = [None] * len(examples)
    fold_f1, cov = [], []
    for tr, te in StratifiedGroupKFold(n_splits=FOLDS, shuffle=True, random_state=SEED).split(texts, y, groups):
        pipe = tc.build_pipeline(hp).fit([texts[i] for i in tr], [y[i] for i in tr])
        p, c = _predict(pipe, [texts[i] for i in te])
        for i, v in zip(te, p):
            preds[i] = v
        fold_f1.append(_score([y[i] for i in te], p)["macro_avg"]["f1"])
        cov.append(c)
    mean = sum(fold_f1) / len(fold_f1)
    std = (sum((f - mean) ** 2 for f in fold_f1) / len(fold_f1)) ** 0.5
    return preds, {"fold_macro_f1": fold_f1, "fold_macro_f1_mean": round(mean, 4), "fold_macro_f1_std": round(std, 4),
                   "vocabulary_coverage_mean": {k: round(sum(c[k] for c in cov) / len(cov), 4) for k in cov[0]}}


def choose(results: Dict[str, Any]) -> str:
    """Apply the fixed selection rule to config results (baselines are never selected)."""
    configs = [n for n, r in results.items() if r["kind"] == "config"]
    best_f1 = max(results[n]["oof"]["macro_avg"]["f1"] for n in configs)
    tied = [n for n in configs if best_f1 - results[n]["oof"]["macro_avg"]["f1"] <= TIE]
    return min(tied, key=lambda n: (results[n]["oof"]["legitimate_false_positive_rate"], n))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--train-selected", action="store_true")
    args = ap.parse_args()

    pool = tc.load_dataset(POOL)
    pool_src = [e.group.split(":", 1)[0] for e in pool]
    y = [e.label for e in pool]
    challenge = tc.load_evaluation_dataset(CHALLENGE)
    ch_y = [e.label for e in challenge]
    ch_cat = [e.fields["category"] for e in challenge]
    ch_txt = [e.text for e in challenge]

    results: Dict[str, Any] = {}

    maj = Counter(y).most_common(1)[0][0]
    results["baseline_majority"] = {"kind": "baseline", "predicts": maj,
                                    "oof": _score(y, [maj] * len(y)),
                                    "challenge": _score(ch_y, [maj] * len(ch_y)),
                                    "challenge_by_category": _by(ch_cat, ch_y, [maj] * len(ch_y))}

    def rules(texts):
        return ["suspicious" if analyse_context(t).flags else "legitimate" for t in texts]
    rp, rc = rules([e.text for e in pool]), rules(ch_txt)
    results["baseline_rules"] = {"kind": "baseline", "description": "context_analyser: any flag -> suspicious",
                                 "oof": _score(y, rp), "oof_by_source": _by(pool_src, y, rp),
                                 "challenge": _score(ch_y, rc), "challenge_by_category": _by(ch_cat, ch_y, rc)}

    try:
        current = tc.load_model(tc.DEFAULT_MODEL_DIR)
        unseen = [i for i, s in enumerate(pool_src) if s != "SYN"]
        cp, ccov = _predict(current.pipeline, [pool[i].text for i in unseen])
        chp, chcov = _predict(current.pipeline, ch_txt)
        results["current_artifact"] = {
            "kind": "current", "model_version": current.meta["model_version"],
            "note": "Trained on SYN only; scored on pool rows from R115 and MWOZ (never seen) and the challenge suite.",
            "unseen_pool": _score([y[i] for i in unseen], cp),
            "unseen_pool_by_source": _by([pool_src[i] for i in unseen], [y[i] for i in unseen], cp),
            "vocabulary_coverage_unseen_pool": ccov,
            "challenge": _score(ch_y, chp), "challenge_by_category": _by(ch_cat, ch_y, chp),
            "vocabulary_coverage_challenge": chcov}
    except (tc.ModelUnavailableError, tc.ModelInvalidError) as exc:
        results["current_artifact"] = {"kind": "current", "unavailable": str(exc)}

    for name, hp in CONFIGS.items():
        oof_p, cv = grouped_oof(pool, hp)
        full = tc.build_pipeline(hp).fit([e.text for e in pool], y)
        chp, chcov = _predict(full, ch_txt)
        results[name] = {"kind": "config", "hyperparameters": hp, "oof": _score(y, oof_p),
                         "oof_by_source": _by(pool_src, y, oof_p), **cv,
                         "challenge": _score(ch_y, chp), "challenge_by_category": _by(ch_cat, ch_y, chp),
                         "vocabulary_coverage_challenge": chcov}
        print(f"{name}: OOF macro-F1 {results[name]['oof']['macro_avg']['f1']}  "
              f"legit FPR {results[name]['oof']['legitimate_false_positive_rate']}  "
              f"challenge acc {results[name]['challenge']['accuracy']}")

    selected = choose(results)

    report = {
        "selection_rule": f"max pooled OOF macro-F1; ties within {TIE} -> lower legitimate FPR",
        "validation": {"pool": POOL.name, "pool_sha256": tc._sha256(POOL), "rows": len(pool),
                       "groups": len({e.group for e in pool}), "class_counts": dict(Counter(y)),
                       "by_source": {s: dict(Counter(y[i] for i, x in enumerate(pool_src) if x == s))
                                     for s in sorted(set(pool_src))},
                       "cv": f"StratifiedGroupKFold(n_splits={FOLDS}, shuffle=True, random_state={SEED})",
                       "challenge_suite": CHALLENGE.name, "challenge_sha256": tc._sha256(CHALLENGE),
                       "challenge_rows": len(challenge)},
        "frozen_test_used": False,
        "selected": selected,
        "results": results,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "selection.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    (OUT / "summary.md").write_text(render(report), encoding="utf-8")
    print(f"\nSelected: {selected}\nReport: {OUT}")

    if args.train_selected:
        meta = tc.train(POOL, model_dir=CANDIDATE_DIR, hyperparameters=CONFIGS[selected])
        print(f"Trained candidate {meta['model_version']} -> {CANDIDATE_DIR}")


def render(rep: Dict[str, Any]) -> str:
    v = rep["validation"]
    L = ["# Text classifier model selection v2 (validation only — frozen test NOT used)", "",
         f"- Pool `{v['pool']}`: {v['rows']} rows, {v['groups']} groups, {v['class_counts']}, by source {v['by_source']}",
         f"- CV: {v['cv']}; challenge suite `{v['challenge_suite']}` ({v['challenge_rows']} rows, author-written)",
         f"- Rule: {rep['selection_rule']}", f"- **Selected: `{rep['selected']}`**", "",
         "## Pooled out-of-fold (grouped CV) — baselines and configs", "",
         "| Model | macro-F1 | fold mean±std | P/R/F1 suspicious | P/R/F1 legitimate | legit FPR | susp FNR | CM [[TN,FP],[FN,TP]] | rows w/ known feature |",
         "|---|---|---|---|---|---|---|---|---|"]
    for n, r in rep["results"].items():
        if "oof" not in r:
            continue
        m = r["oof"]
        s, g = m["suspicious"], m["legitimate"]
        fold = f"{r['fold_macro_f1_mean']}±{r['fold_macro_f1_std']}" if "fold_macro_f1_mean" in r else "—"
        cov = r.get("vocabulary_coverage_mean", {}).get("rows_with_any_known_feature", "—")
        L.append(f"| {n} | {m['macro_avg']['f1']} | {fold} | {s['precision']}/{s['recall']}/{s['f1']} | "
                 f"{g['precision']}/{g['recall']}/{g['f1']} | {m['legitimate_false_positive_rate']} | "
                 f"{m['suspicious_false_negative_rate']} | {m['confusion_matrix']['rows_true_cols_pred']} | {cov} |")
    cur = rep["results"].get("current_artifact", {})
    if "unseen_pool" in cur:
        m = cur["unseen_pool"]
        L += ["", f"Current deployed artifact on unseen pool rows (R115 + MWOZ): macro-F1 {m['macro_avg']['f1']}, "
                  f"legit FPR {m['legitimate_false_positive_rate']}, susp recall {m['suspicious']['recall']}, "
                  f"CM {m['confusion_matrix']['rows_true_cols_pred']}; by source "
                  + json.dumps({k: {kk: vv for kk, vv in x.items() if kk in ('n', 'accuracy')} for k, x in cur['unseen_pool_by_source'].items()})]
    cats = sorted({c for r in rep["results"].values() for c in r.get("challenge_by_category", {})})
    L += ["", "## Challenge suite — accuracy by category (author-written, intent-labelled)", "",
          "| Model | overall acc | legit FPR | susp recall | " + " | ".join(cats) + " |",
          "|---|---|---|---|" + "---|" * len(cats)]
    for n, r in rep["results"].items():
        if "challenge" not in r:
            continue
        m = r["challenge"]
        L.append(f"| {n} | {m['accuracy']} | {m['legitimate_false_positive_rate']} | {m['suspicious']['recall']} | "
                 + " | ".join(str(r["challenge_by_category"].get(c, {}).get("accuracy")) for c in cats) + " |")
    L += ["", "## OOF accuracy by source (configs)", "", "| Model | SYN | R115 | MWOZ |", "|---|---|---|---|"]
    for n, r in rep["results"].items():
        if "oof_by_source" in r:
            b = r["oof_by_source"]
            L.append(f"| {n} | " + " | ".join(str(b.get(s, {}).get("accuracy")) for s in ("SYN", "R115", "MWOZ")) + " |")
    L += ["", "Caveats: validation data is partly synthetic (SYN, R115) and MWOZ is booking dialogue, so these "
              "numbers are development evidence only. The challenge suite is small and written by the same author "
              "as SYN."]
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    main()
