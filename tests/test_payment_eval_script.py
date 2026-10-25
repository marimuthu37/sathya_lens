"""Tests for scripts/evaluate_payment_pipeline.py (metrics, labels, run-once guards)."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parent.parent / "scripts" / "evaluate_payment_pipeline.py"
_spec = importlib.util.spec_from_file_location("evaluate_payment_pipeline", _PATH)
ev = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ev)


def _rows(tp=0, fp=0, fn=0, tn=0):
    return ([{"positive": True, "pred": True}] * tp + [{"positive": False, "pred": True}] * fp
            + [{"positive": True, "pred": False}] * fn + [{"positive": False, "pred": False}] * tn)


def test_binary_metrics_denominators():
    m = ev.binary_metrics(_rows(tp=8, fp=2, fn=2, tn=8))
    assert m["confusion_matrix"] == {"tp": 8, "fp": 2, "fn": 2, "tn": 8}
    assert m["precision"] == 0.8 and m["recall"] == 0.8 and m["f1"] == 0.8
    assert m["false_positive_rate"] == 0.2 and m["false_negative_rate"] == 0.2


def test_undefined_ratios_are_none_not_zero():
    m = ev.binary_metrics(_rows(tn=5))              # no positives, no predicted positives
    assert m["precision"] is None and m["recall"] is None
    assert m["false_negative_rate"] is None and m["false_positive_rate"] == 0.0


def test_labels_by_construction():
    samples = ev.build_samples("dev")
    legible = [s for s in samples if not s["degradation"]]
    assert {s["scenario"] for s in legible} == set(ev.CONFIG["scenarios"])
    for s in legible:
        assert s["positive"] == (s["scenario"] in ev.POSITIVE_SCENARIOS)
        if s["scenario"] == "amount_mismatch":
            assert s["expected"]["amount"] != ev._fmt_amount(ev.Decimal(s["shown"]["amount"]))
        if s["scenario"] == "no_expected_values":
            assert s["expected"] is None


def test_splits_are_disjoint_in_fonts_and_names():
    dev, t1, t2 = (ev.split_config(s) for s in ("dev", "test", "test2"))
    assert not set(dev["fonts"]) & set(t1["fonts"])
    assert not (set(dev["fonts"]) | set(t1["fonts"])) & set(t2["fonts"])
    assert not set(ev.NAMES_A) & set(ev.NAMES_B) and not (set(ev.NAMES_A) | set(ev.NAMES_B)) & set(ev.NAMES_C)
    assert "stacked" not in dev["layouts"]


def test_generation_is_deterministic():
    a, b = ev.build_samples("test"), ev.build_samples("test")
    assert json.dumps(a, sort_keys=True, default=str) == json.dumps(b, sort_keys=True, default=str)


def test_registered_hashes_still_match_the_code():
    for split, name in (("test", "protocol.json"), ("test2", "protocol_v2.json")):
        p = ev.REPORT_DIR / name
        if not p.exists():
            pytest.skip("protocol not registered in this checkout")
        assert json.loads(p.read_text(encoding="utf-8"))["config_hash"] == ev.config_hash(split)


def test_held_out_split_is_not_rerun(monkeypatch):
    if not (ev.REPORT_DIR / "test" / "metrics.json").exists():
        pytest.skip("held-out result not present")
    monkeypatch.setattr(sys, "argv", ["x", "--split", "test"])
    with pytest.raises(SystemExit, match="run once"):
        ev.main()
