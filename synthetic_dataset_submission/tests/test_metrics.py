"""Metric arithmetic, zero-denominator cases, and an optional real-application smoke test."""

from __future__ import annotations

import pytest

import evaluate_dataset as ev
from conftest import HERE, load


def _pairs(tp=0, fp=0, fn=0, tn=0):
    return [(True, True)] * tp + [(False, True)] * fp + [(True, False)] * fn + [(False, False)] * tn


def test_known_values():
    m = ev.binary_metrics(_pairs(tp=6, fp=2, fn=4, tn=8))
    assert (m["tp"], m["fp"], m["fn"], m["tn"]) == (6, 2, 4, 8)
    assert m["precision"] == 0.75 and m["recall"] == 0.6
    assert m["f1"] == round(2 * 0.75 * 0.6 / 1.35, 4)
    assert m["false_positive_rate"] == 0.2 and m["false_negative_rate"] == 0.4
    assert m["denominators"] == {"precision": 8, "recall": 10, "fpr": 10, "fnr": 10}


def test_no_predicted_positives_gives_undefined_precision():
    m = ev.binary_metrics(_pairs(fn=3, tn=2))
    assert m["precision"] is None and m["recall"] == 0.0 and m["f1"] is None


def test_no_positives_gives_undefined_recall_and_fnr():
    m = ev.binary_metrics(_pairs(fp=1, tn=4))
    assert m["recall"] is None and m["false_negative_rate"] is None and m["false_positive_rate"] == 0.2


def test_zero_precision_and_recall_gives_f1_zero():
    m = ev.binary_metrics(_pairs(fp=2, fn=2))
    assert m["precision"] == 0.0 and m["recall"] == 0.0 and m["f1"] == 0.0


def test_empty_input():
    m = ev.binary_metrics([])
    assert m["n"] == 0 and all(m[k] is None for k in ("precision", "recall", "f1", "false_positive_rate"))


def test_summarise_separates_ambiguous_and_insufficient():
    preds = [
        {"modality": "message_text", "category": "a", "label": "suspicious", "decision": "verify_first", "http_analyse": 202},
        {"modality": "message_text", "category": "b", "label": "ambiguous", "decision": "verify_first", "http_analyse": 202},
        {"modality": "payment_screenshot", "category": "c", "label": "insufficient_evidence",
         "decision": "low_concern", "http_analyse": 202, "severe_quality": True, "statuses": {},
         "expected_field_status": {"amount": "not_compared", "currency": "not_supplied",
                                   "recipient": "not_supplied", "reference": "not_supplied"}},
    ]
    s = ev.summarise(preds)
    assert s["message_text"]["binary_suspicious_vs_legitimate"]["n"] == 1      # ambiguous excluded
    assert s["message_text"]["ambiguous"]["n"] == 1
    assert s["payment_screenshot"]["binary_discrepancy_vs_consistent"]["n"] == 0
    assert s["payment_screenshot"]["insufficient_evidence"]["low_concern"] == 1
    assert s["low_concern_decisions"] == 1


def test_held_out_results_are_not_overwritten(monkeypatch):
    if not (HERE / "results" / "test_predictions.jsonl").exists():
        pytest.skip("held-out results not present")
    with pytest.raises(SystemExit, match="evaluated once"):
        ev.main(["--split", "test"])


def test_real_application_smoke(small_dataset):
    """Two text records through the real SatyaLens app (skipped if it cannot be imported)."""
    if not (ev.BACKEND / "app" / "main.py").exists():
        pytest.skip("SatyaLens backend not present")
    pytest.importorskip("fastapi")
    pytest.importorskip("satyalens_analysis")
    records = [r for r in load(small_dataset) if r["modality"] == "message_text"][:2]
    preds = ev.run_split(records, small_dataset)
    assert len(preds) == 2
    assert all(p["http_analyse"] == 202 and p["analysis_status"] == "completed" for p in preds)
    assert all(p["decision"] in ("verify_first", "insufficient_evidence", "high_risk") for p in preds)
    assert not (HERE / ".eval_workspace").exists()
