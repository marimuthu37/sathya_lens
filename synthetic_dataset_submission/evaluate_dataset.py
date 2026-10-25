"""
evaluate_dataset.py — run the REAL SatyaLens application on the synthetic dataset.

    python evaluate_dataset.py --split dev
    python evaluate_dataset.py --split test          # held-out; refuses to overwrite
    python evaluate_dataset.py --report-only         # rebuild the report from results/

Must be run with an interpreter that has the SatyaLens backend dependencies and
the satyalens_analysis package (e.g. "../person 2/person2_app/backend/.venv/
Scripts/python.exe"), with Tesseract installed for screenshots.

How the application is invoked
------------------------------
The FastAPI app in ../person 2/person2_app/backend is imported unchanged and
driven in-process with fastapi.testclient against an ISOLATED temporary SQLite
database and upload directory created inside this folder (.eval_workspace/,
deleted afterwards). ANALYSIS_MODULE_ENABLED=true; if the real Person 1 adapter
does not load, the script stops — it never simulates results.

Per record: create case → grant consent → upload the record's input (PNG, or
the message text as a .txt file) → POST .../analyse (with the record's
`expected_payment` for screenshots) → GET .../analysis → POST .../decision.
Only `input` is sent. `ground_truth` and `meta` are used afterwards to score.

Scoring (predicted positive = decision verify_first or high_risk)
  message_text       positive = suspicious, negative = legitimate; ambiguous
                     reported separately (no right answer is asserted)
  payment_screenshot positive = discrepancy, negative = consistent;
                     insufficient_evidence reported separately (correct
                     behaviour = withheld comparisons, never low_concern)
All results are SYNTHETIC-ONLY and do not measure real-world performance.
"""

from __future__ import annotations

import argparse
import gc
import json
import os
import shutil
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.dont_write_bytecode = True              # never write .pyc files into the application tree
HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent
BACKEND = PROJECT / "person 2" / "person2_app" / "backend"
RESULTS = HERE / "results"
WORKSPACE = HERE / ".eval_workspace"
FLAGGED = {"verify_first", "high_risk"}
RAN = {"match", "mismatch", "format_difference", "partial_match"}


# ── Metrics (pure functions; unit-tested) ────────────────────────────────────

def ratio(num: int, den: int):
    """num/den rounded to 4 places, or None when the denominator is zero (undefined)."""
    return round(num / den, 4) if den else None


def binary_metrics(pairs: list[tuple[bool, bool]]) -> dict:
    """pairs = [(is_positive, predicted_positive)]. Undefined ratios are None."""
    tp = sum(t and p for t, p in pairs)
    fp = sum((not t) and p for t, p in pairs)
    fn = sum(t and (not p) for t, p in pairs)
    tn = sum((not t) and (not p) for t, p in pairs)
    precision, recall = ratio(tp, tp + fp), ratio(tp, tp + fn)
    if precision is None or recall is None:
        f1 = None
    elif precision + recall == 0:
        f1 = 0.0
    else:
        f1 = round(2 * precision * recall / (precision + recall), 4)
    return {"n": len(pairs), "positives": tp + fn, "negatives": tn + fp,
            "tp": tp, "fp": fp, "fn": fn, "tn": tn,
            "precision": precision, "recall": recall, "f1": f1,
            "false_positive_rate": ratio(fp, fp + tn), "false_negative_rate": ratio(fn, fn + tp),
            "denominators": {"precision": tp + fp, "recall": tp + fn, "fpr": fp + tn, "fnr": fn + tp}}


def breakdowns(preds: list[dict], records_by_id: dict) -> dict:
    """Binary metrics per screenshot layout and per message template (joined from the dataset)."""
    by_layout, by_template = defaultdict(list), defaultdict(list)
    for p in preds:
        meta = records_by_id[p["record_id"]]["meta"]
        flagged = p["decision"] in FLAGGED
        if p["modality"] == "payment_screenshot" and p["label"] != "insufficient_evidence":
            by_layout[meta["render"]["layout"]].append((p["label"] == "discrepancy", flagged))
        elif p["modality"] == "message_text" and p["label"] != "ambiguous":
            by_template[meta["template_id"]].append((p["label"] == "suspicious", flagged))
    return {"screenshot_by_layout": {k: binary_metrics(v) for k, v in sorted(by_layout.items())},
            "message_by_template": {k: {"n": len(v), "flagged": sum(f for _, f in v),
                                        "correct": sum(t == f for t, f in v)} for k, v in sorted(by_template.items())}}


def summarise(preds: list[dict]) -> dict:
    """Metrics for one split from prediction rows (ground truth joined after the run)."""
    out: dict = {"low_concern_decisions": sum(p["decision"] == "low_concern" for p in preds),
                 "http_errors": sum(p["http_analyse"] not in (202,) for p in preds)}
    msgs = [p for p in preds if p["modality"] == "message_text"]
    imgs = [p for p in preds if p["modality"] == "payment_screenshot"]

    def flagged(p):
        return p["decision"] in FLAGGED

    def per_category(rows):
        cats = defaultdict(list)
        for p in rows:
            cats[p["category"]].append(p)
        return {c: {"label": rs[0]["label"], "n": len(rs), "flagged": sum(map(flagged, rs)),
                    "decisions": dict(Counter(p["decision"] for p in rs))} for c, rs in sorted(cats.items())}

    if msgs:
        out["message_text"] = {
            "binary_suspicious_vs_legitimate": binary_metrics(
                [(p["label"] == "suspicious", flagged(p)) for p in msgs if p["label"] != "ambiguous"]),
            "ambiguous": {"n": sum(p["label"] == "ambiguous" for p in msgs),
                          "decisions": dict(Counter(p["decision"] for p in msgs if p["label"] == "ambiguous"))},
            "per_category": per_category(msgs),
        }
    if imgs:
        insuff = [p for p in imgs if p["label"] == "insufficient_evidence"]
        status_rows = [(f, p["expected_field_status"][f], p["statuses"].get(f))
                       for p in imgs for f in ("amount", "currency", "recipient", "reference")
                       if p["expected_field_status"][f] != "not_supplied"]
        out["payment_screenshot"] = {
            "binary_discrepancy_vs_consistent": binary_metrics(
                [(p["label"] == "discrepancy", flagged(p)) for p in imgs if p["label"] != "insufficient_evidence"]),
            "insufficient_evidence": {
                "n": len(insuff),
                "flagged_verify_first": sum(map(flagged, insuff)),
                "low_concern": sum(p["decision"] == "low_concern" for p in insuff),
                "with_severe_quality_warning": sum(p["severe_quality"] for p in insuff),
                "with_any_comparison_run": sum(any(s in RAN for s in p["statuses"].values()) for p in insuff),
            },
            "field_status_agreement": {
                "n_supplied_fields": len(status_rows),
                "agree": sum(want == got for _, want, got in status_rows),
                "rate": ratio(sum(want == got for _, want, got in status_rows), len(status_rows)),
                "false_mismatch": sum(want != "mismatch" and got == "mismatch" for _, want, got in status_rows),
                "missed_mismatch": sum(want == "mismatch" and got != "mismatch" for _, want, got in status_rows),
                "confusions": dict(Counter(f"{f}: want={w} got={g}" for f, w, g in status_rows if w != g)),
            },
            "severe_quality_on_legible": sum(p["severe_quality"] for p in imgs if p["label"] != "insufficient_evidence"),
            "per_category": per_category(imgs),
        }
    return out


# ── Running the application ──────────────────────────────────────────────────

def _load_app():
    if WORKSPACE.exists():
        shutil.rmtree(WORKSPACE)
    (WORKSPACE / "uploads").mkdir(parents=True)
    os.environ.update({"DATABASE_URL": f"sqlite:///{(WORKSPACE / 'eval.db').as_posix()}",
                       "UPLOAD_DIR": str(WORKSPACE / "uploads"), "ANALYSIS_MODULE_ENABLED": "true",
                       "DEBUG": "false", "PYTHONUTF8": "1"})
    for p in (str(BACKEND), str(PROJECT)):
        if p not in sys.path:
            sys.path.insert(0, p)
    from fastapi.testclient import TestClient
    from app.main import app
    from app.services import analysis as svc
    if type(svc._adapter).__name__ != "Person1Adapter":
        raise SystemExit(f"The real SatyaLens analysis adapter did not load ({type(svc._adapter).__name__}); "
                         "refusing to evaluate. Check satyalens_analysis and Tesseract are installed.")
    return TestClient(app)


def run_split(records: list[dict], dataset_dir: Path) -> list[dict]:
    client = _load_app()
    preds = []
    try:
        with client as c:
            for n, r in enumerate(records, 1):
                inp = r["input"]          # the ONLY part of the record sent to the application
                cid = c.post("/api/cases", json={"description": "synthetic dataset evaluation"}).json()["id"]
                c.post(f"/api/cases/{cid}/evidence/consent", json={"acknowledged": True})
                if r["modality"] == "payment_screenshot":
                    data, name = (dataset_dir / inp["image_path"]).read_bytes(), Path(inp["image_path"]).name
                else:
                    data, name = inp["message_text"].encode("utf-8"), f"{r['record_id']}.txt"
                up = c.post(f"/api/cases/{cid}/evidence", files={"file": (name, data, inp["media_type"])})
                eid = up.json()["id"]
                body = {"expected_payment": inp["expected_payment"]} if inp.get("expected_payment") else None
                post = c.post(f"/api/cases/{cid}/evidence/{eid}/analyse", json=body)
                res = c.get(f"/api/cases/{cid}/evidence/{eid}/analysis").json()
                dec = c.post(f"/api/cases/{cid}/decision").json()
                pay = res.get("payment") or {}
                q = pay.get("quality") or {}
                # Ground truth is joined only now, after the application has answered.
                preds.append({
                    "record_id": r["record_id"], "split": r["meta"]["split"], "modality": r["modality"],
                    "category": r["meta"]["category"], "label": r["ground_truth"]["label"],
                    "expected_field_status": r["ground_truth"].get("expected_field_status"),
                    "http_upload": up.status_code, "http_analyse": post.status_code,
                    "analysis_status": res.get("status"), "decision": dec.get("category"),
                    "findings": [f["label"] for f in res.get("findings", [])],
                    "statuses": {x["field"]: x["status"] for x in pay.get("comparisons", [])},
                    "extracted": {x["field"]: x["extracted"] for x in pay.get("comparisons", [])},
                    "quality": q.get("assessment"),
                    "severe_quality": any(w.get("severity") == "severe" for w in q.get("warnings", [])),
                })
                if n % 25 == 0:
                    print(f"  {n}/{len(records)}", flush=True)
    finally:
        db = sys.modules.get("app.database")
        if db is not None:
            db.engine.dispose()           # release the SQLite file so the workspace can be deleted
        gc.collect()
        shutil.rmtree(WORKSPACE, ignore_errors=True)
    return preds


# ── Report ────────────────────────────────────────────────────────────────────

def _bin_row(name: str, m: dict) -> str:
    return (f"| {name} | {m['n']} | {m['positives']}/{m['negatives']} | {m['tp']} | {m['fp']} | {m['fn']} | "
            f"{m['tn']} | {m['precision']} | {m['recall']} | {m['f1']} | {m['false_positive_rate']} | "
            f"{m['false_negative_rate']} |")


def write_report(summary: dict) -> None:
    L = ["# SatyaLens on the synthetic submission dataset — evaluation report", "",
         "**Synthetic-only.** Every input was generated by `generate_dataset.py`; labels follow documented "
         "template rules. These numbers do **not** measure real-world fraud detection.", "",
         f"Dataset manifest SHA-256 of records: `{summary.get('dataset_sha256', '?')[:16]}…`, seed "
         f"`{summary.get('seed')}`. Application: real FastAPI routes → Person 1 adapter → Tesseract / text "
         "rules + classifier → payment checks → decision engine, isolated temporary database.", "",
         "Predicted positive = decision `verify_first` or `high_risk`. `null` = undefined (zero denominator).", "",
         "| Task | n | pos/neg | TP | FP | FN | TN | Precision | Recall | F1 | FPR | FNR |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for split in ("dev", "test"):
        s = summary["splits"].get(split)
        if not s:
            continue
        tag = "dev (development)" if split == "dev" else "**test (held-out)**"
        if "message_text" in s:
            L.append(_bin_row(f"{tag} — messages: suspicious vs legitimate", s["message_text"]["binary_suspicious_vs_legitimate"]))
        if "payment_screenshot" in s:
            L.append(_bin_row(f"{tag} — screenshots: discrepancy vs consistent", s["payment_screenshot"]["binary_discrepancy_vs_consistent"]))
    for split in ("dev", "test"):
        s = summary["splits"].get(split)
        if not s:
            continue
        L += ["", f"## {split} split ({'held-out' if split == 'test' else 'development'})", "",
              f"- `low_concern` decisions: {s['low_concern_decisions']}; HTTP errors on analyse: {s['http_errors']}"]
        if "message_text" in s:
            m = s["message_text"]
            L += [f"- Ambiguous messages (n={m['ambiguous']['n']}): decisions {m['ambiguous']['decisions']} "
                  "(no correct answer is asserted; Verify First and Insufficient Evidence are both defensible)", "",
                  "| message category | label | n | flagged | decisions |", "|---|---|---|---|---|"]
            L += [f"| {c} | {v['label']} | {v['n']} | {v['flagged']} | {v['decisions']} |" for c, v in m["per_category"].items()]
        if "payment_screenshot" in s:
            p = s["payment_screenshot"]
            ins, fs = p["insufficient_evidence"], p["field_status_agreement"]
            L += ["", f"- Insufficient-evidence screenshots (n={ins['n']}): flagged {ins['flagged_verify_first']}, "
                  f"low_concern {ins['low_concern']}, severe quality warning {ins['with_severe_quality_warning']}, "
                  f"any comparison run {ins['with_any_comparison_run']}",
                  f"- Severe quality warnings on legible screenshots: {p['severe_quality_on_legible']}",
                  f"- Field comparison status agreement: {fs['agree']}/{fs['n_supplied_fields']} ({fs['rate']}); "
                  f"false mismatches {fs['false_mismatch']}; missed mismatches {fs['missed_mismatch']}",
                  f"- Status confusions: {fs['confusions']}", "",
                  "| screenshot category | label | n | flagged | decisions |", "|---|---|---|---|---|"]
            L += [f"| {c} | {v['label']} | {v['n']} | {v['flagged']} | {v['decisions']} |" for c, v in p["per_category"].items()]
        b = s.get("breakdowns")
        if b:
            L += ["", "| screenshot layout | n | precision | recall | FPR | FNR |", "|---|---|---|---|---|---|"]
            L += [f"| {k} | {m['n']} | {m['precision']} | {m['recall']} | {m['false_positive_rate']} | "
                  f"{m['false_negative_rate']} |" for k, m in b["screenshot_by_layout"].items()]
            L += ["", "| message template (1 per category per split) | n | flagged | correct |", "|---|---|---|---|"]
            L += [f"| `{k}` | {v['n']} | {v['flagged']} | {v['correct']} |" for k, v in b["message_by_template"].items()]
    L += ["", "## How to read these numbers", "",
          "- **Messages:** each category has exactly one template per split, so the 96 scored messages per split "
          "come from 12 templates. Outcomes are strongly correlated within a template (most templates are "
          "8/8 or 0/8). The effective sample size is about 12 templates, not 96 independent messages, and dev "
          "and test differ mainly because they contain different templates.",
          "- **Screenshots:** the `rows`, `receipt` and `stacked` layouts resemble synthetic layouts that were "
          "used earlier, during SatyaLens development, to find and fix extractor defects "
          "(`../reports/payment_pipeline_eval_v1`). Results on them are therefore not independent of the "
          "application's development; `card` is the only layout family new to the application.",
          "- Template families are disjoint across splits, but every split was produced by the same "
          "generator, author and vocabulary (generator bias).",
          "", "## Not evaluable", "",
          "- **Audio deepfake detection** — unavailable in SatyaLens (no model loader); no records generated.",
          "- **Webpage / phishing-page detection** — not implemented; no records generated.",
          "- **Real-world accuracy** — not measurable with synthetic data.",
          "- **Payment settlement** — cannot be determined from a screenshot by any method.", "",
          "See `docs/limitations.md` and `docs/evaluation_protocol.md`."]
    (RESULTS / "evaluation_report.md").write_text("\n".join(L) + "\n", encoding="utf-8")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--split", choices=["dev", "test"])
    ap.add_argument("--dataset-dir", type=Path, default=HERE / "dataset")
    ap.add_argument("--limit", type=int, default=None, help="evaluate only the first N records (smoke test)")
    ap.add_argument("--report-only", action="store_true")
    ap.add_argument("--allow-rerun-test", action="store_true",
                    help="only for reproducing from a clean checkout; never for re-tuning")
    a = ap.parse_args(argv)
    RESULTS.mkdir(exist_ok=True)
    summary_path = RESULTS / "evaluation_summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.exists() else {"splits": {}}
    manifest = json.loads((a.dataset_dir / "dataset_manifest.json").read_text(encoding="utf-8"))
    summary.update({"synthetic_only": True, "seed": manifest["seed"],
                    "dataset_sha256": manifest["files"]["synthetic_dataset.jsonl"]})

    all_records = [json.loads(l) for l in (a.dataset_dir / "synthetic_dataset.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    by_id = {r["record_id"]: r for r in all_records}

    def store(split: str, preds: list[dict], limit) -> None:
        summary["splits"][split] = summarise(preds)
        summary["splits"][split].update(records_evaluated=len(preds), limit=limit,
                                        breakdowns=breakdowns(preds, by_id))

    if a.report_only:
        # Recompute metrics from the saved predictions; the application is not run.
        for split in ("dev", "test"):
            path = RESULTS / f"{split}_predictions.jsonl"
            if path.exists():
                preds = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
                store(split, preds, summary["splits"].get(split, {}).get("limit"))
    else:
        if not a.split:
            ap.error("--split is required unless --report-only")
        pred_path = RESULTS / f"{a.split}_predictions.jsonl"
        if a.split == "test" and pred_path.exists() and not a.allow_rerun_test:
            raise SystemExit("Held-out results already exist; the test split is evaluated once. "
                             "Use --allow-rerun-test only to reproduce from a clean checkout.")
        records = [r for r in all_records if r["meta"]["split"] == a.split][: a.limit]
        print(f"{a.split}: {len(records)} records")
        preds = run_split(records, a.dataset_dir)
        pred_path.write_text("".join(json.dumps(p, ensure_ascii=False) + "\n" for p in preds), encoding="utf-8")
        store(a.split, preds, a.limit)
    summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    write_report(summary)
    print(json.dumps({s: {k: v.get("binary_suspicious_vs_legitimate", v.get("binary_discrepancy_vs_consistent"))
                          for k, v in d.items() if isinstance(v, dict) and k in ("message_text", "payment_screenshot")}
                      for s, d in summary["splits"].items()}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
