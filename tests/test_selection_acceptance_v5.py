"""
test_selection_acceptance_v5.py — scripts/select_text_model_v5.py and
scripts/acceptance_v5.py on temporary project layouts. No real pool, test
set or candidate model is created; the real protocol file is only copied.
"""
import csv
import hashlib
import importlib.util
import json
import shutil
import tempfile
from collections import Counter
from pathlib import Path

import pytest

sklearn = pytest.importorskip("sklearn")

from satyalens_analysis import text_classifier as tc  # noqa: E402
from tests.test_text_classifier import _toy_rows, _write_csv  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
PIN = "3889099d1da7fa6cdc987685a2dacda4592508339bd8cedb91a99fa29f25f3ee"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, REPO / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


@pytest.fixture()
def layout(tmp_path):
    """tmp project: docs/protocol_v5.json (+ .sha256), data/, reports/."""
    for d in ("docs", "data", "reports"):
        (tmp_path / d).mkdir()
    shutil.copy(REPO / "docs" / "protocol_v5.json", tmp_path / "docs" / "protocol_v5.json")
    shutil.copy(REPO / "docs" / "protocol_v5.sha256", tmp_path / "docs" / "protocol_v5.sha256")
    return tmp_path

# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------

@pytest.fixture()
def sel(layout, monkeypatch):
    m = _load("select_text_model_v5")
    pool = _write_csv(layout / "data" / "training_pool_v5.csv", _toy_rows())
    sealed = layout / "data" / "sealed_test.csv"
    sealed.write_text("text,label\nquickly tell me the otp code,suspicious\npay day arrived,legitimate\n",
                      encoding="utf-8")
    manifest = layout / "reports" / "frozen_test_v5_manifest.json"
    manifest.write_text(json.dumps({
        "protocol_sha256": PIN, "protocol_file": "docs/protocol_v5.json",
        "sealed_files": [{"path": "data/sealed_test.csv", "sha256": _sha(sealed)}],
        "evaluation_log": "reports/evals.jsonl", "pool_csv_sha256": _sha(pool), "sealed_utc": "t"}), encoding="utf-8")
    monkeypatch.setattr(m, "PROTOCOL", layout / "docs" / "protocol_v5.json")
    monkeypatch.setattr(m, "PROTOCOL_HASH", layout / "docs" / "protocol_v5.sha256")
    monkeypatch.setattr(m, "MANIFEST", manifest)
    monkeypatch.setattr(m, "POOL", pool)
    monkeypatch.setattr(m, "OUT", layout / "reports" / "model_selection_v5")
    monkeypatch.setattr(m, "CANDIDATE_DIR", layout / "models" / "candidates" / "v5")
    monkeypatch.setattr(m, "V2_DIR", layout / "no_v2")
    monkeypatch.setenv(tc.SEALED_MANIFEST_ENV, str(manifest))
    tc.clear_model_cache()
    yield m, layout, pool, sealed, manifest
    tc.clear_model_cache()


class TestSelectionPinAndCandidates:
    def test_pin_matches_repository_protocol(self):
        m = _load("select_text_model_v5")
        assert m.PROTOCOL_V5_SHA256 == PIN == _sha(REPO / "docs" / "protocol_v5.json")
        assert m.verify_protocol()["protocol_version"] == "5.0"

    def test_modified_protocol_refused(self, sel):
        m, layout, *_ = sel
        (layout / "docs" / "protocol_v5.json").write_text("{}", encoding="utf-8")
        with pytest.raises(m.SelectionError, match="pinned"):
            m.verify_protocol()

    def test_candidates_exactly_as_protocol(self):
        m = _load("select_text_model_v5")
        p = json.loads((REPO / "docs" / "protocol_v5.json").read_text(encoding="utf-8"))
        assert set(m.CANDIDATES) == set(p["model_selection"]["candidates"])
        c = m.CANDIDATES
        assert c["A_v1_config"]["tfidf"]["ngram_range"] == [1, 2] and c["A_v1_config"]["logistic_regression"]["C"] == 1.0
        assert c["C_stopwords_unigram"]["tfidf"]["stop_words"] == "english" and c["C_stopwords_unigram"]["tfidf"]["ngram_range"] == [1, 1]
        assert c["E_stopwords_C3"]["logistic_regression"]["C"] == 3.0 and c["E_stopwords_C3"]["tfidf"]["stop_words"] == "english"
        for name, C in (("G_char_wb_3_5", 1.0), ("H_char_wb_3_5_C3", 3.0)):
            assert c[name]["tfidf"]["analyzer"] == "char_wb" and c[name]["tfidf"]["ngram_range"] == [3, 5]
            assert c[name]["tfidf"]["min_df"] == 2 and c[name]["logistic_regression"]["C"] == C
        assert all(v["logistic_regression"]["class_weight"] == "balanced" for v in c.values())

    def _r(self, f1, fpr, don=None, kind="config"):
        return {"kind": kind, "oof": {"macro_avg": {"f1": f1}, "legitimate_false_positive_rate": fpr},
                "donation_oof_fpr": don}

    def test_selection_rule(self):
        m = _load("select_text_model_v5")
        assert m.choose({"a": self._r(0.95, 0.1, 0.2), "b": self._r(0.90, 0.1, 0.05)}) == "b"
        assert m.choose({"a": self._r(0.850, 0.2), "b": self._r(0.847, 0.05)}) == "b"
        assert m.choose({"a": self._r(0.8, 0.1), "base": self._r(0.99, 0.0, kind="baseline")}) == "a"
        with pytest.raises(m.SelectionError):
            m.choose({"a": self._r(0.9, 0.1, 0.5)})


class TestSelectionGuards:
    def test_missing_manifest(self, sel):
        m, _, _, _, manifest = sel
        manifest.unlink()
        with pytest.raises(m.SelectionError, match="manifest not found"):
            m.verify_inputs()

    def test_manifest_with_other_protocol_hash(self, sel):
        m, _, _, _, manifest = sel
        d = json.loads(manifest.read_text(encoding="utf-8"))
        d["protocol_sha256"] = "0" * 64
        manifest.write_text(json.dumps(d), encoding="utf-8")
        with pytest.raises(m.SelectionError, match="protocol hash"):
            m.verify_inputs()

    def test_missing_pool(self, sel):
        m, _, pool, _, _ = sel
        pool.unlink()
        with pytest.raises(m.SelectionError, match="not found"):
            m.verify_inputs()

    def test_pool_changed_after_sealing(self, sel):
        m, _, pool, _, _ = sel
        pool.write_text(pool.read_text(encoding="utf-8") + "x,extra row,suspicious,s0\n", encoding="utf-8")
        with pytest.raises(m.SelectionError, match="recorded at sealing"):
            m.verify_inputs()

    def test_pool_that_is_a_copy_of_the_sealed_test_refused(self, sel, monkeypatch):
        m, layout, _, sealed, manifest = sel
        copy = layout / "data" / "innocent_pool.csv"
        copy.write_bytes(sealed.read_bytes())
        d = json.loads(manifest.read_text(encoding="utf-8"))
        d["pool_csv_sha256"] = _sha(copy)
        manifest.write_text(json.dumps(d), encoding="utf-8")
        monkeypatch.setattr(m, "POOL", copy)
        with pytest.raises(m.SelectionError, match="sealed file"):
            m.verify_inputs()
        with pytest.raises(tc.SealedDataError):
            tc.load_dataset(copy)

    def test_script_never_references_test_labels_or_evaluation_outputs(self):
        src = (REPO / "scripts" / "select_text_model_v5.py").read_text(encoding="utf-8")
        for s in ("frozen_test_v5.csv", "predictions.csv", "evaluation.json", "evaluations.jsonl", "open("):
            assert s not in src, s


class TestSelectionRun:
    def test_end_to_end_on_toy_pool_without_training(self, sel):
        m, layout, *_ = sel
        report = m.run(train_selected=False)
        assert report["selected"] in m.CANDIDATES
        assert report["sealed_test_read"] is False and report["protocol_sha256"] == PIN
        assert {"baseline_majority_class", "baseline_always_legitimate", "baseline_rule_engine"} <= set(report["results"])
        assert report["results"]["baseline_always_legitimate"]["oof"]["suspicious"]["recall"] == 0.0
        assert all(len(report["results"][c]["fold_macro_f1"]) == 5 for c in m.CANDIDATES)
        assert (layout / "reports" / "model_selection_v5" / "selection.json").is_file()
        assert not (layout / "models").exists()                     # nothing trained

    def test_cli_stops_on_guard_failure(self, sel, capsys):
        m, _, _, _, manifest = sel
        manifest.unlink()
        assert m.main([]) == 3 and "STOP" in capsys.readouterr().err

# ---------------------------------------------------------------------------
# Acceptance
# ---------------------------------------------------------------------------

DONATION_CATEGORIES = ("bank_alert", "upi_alert", "otp_notice")


def _pred_rows(n_imc=100, miss=10, n_mwoz=60, fp_mwoz=0, n_don=0, fp_don=0, hey=14, inconclusive=0,
               donors=5, inconclusive_legit=0):
    rows, i = [], 0
    for k in range(n_imc):
        pred = "legitimate" if k < miss else "suspicious"
        if k >= n_imc - inconclusive:
            pred = ""
        rows.append({"row": i, "label": "suspicious", "prediction": pred, "raw_score": "",
                     "rule_prediction": "legitimate", "source": "IMC25", "india_linked": "1" if k % 2 else "0",
                     "category": "hey mum/dad" if k < hey else "banking", "donor": ""})
        i += 1
    for src, n, fp in (("MWOZ_TEST", n_mwoz, fp_mwoz), ("DONATION", n_don, fp_don)):
        for k in range(n):
            pred = "suspicious" if k < fp else "legitimate"
            if src == "MWOZ_TEST" and k >= n - inconclusive_legit:
                pred = ""
            don = src == "DONATION"
            rows.append({"row": i, "label": "legitimate", "prediction": pred,
                         "raw_score": "", "rule_prediction": "legitimate", "source": src, "india_linked": "0",
                         "category": DONATION_CATEGORIES[k % 3] if don else "conversation",
                         "donor": f"D{k % donors:02d}" if don else ""})
            i += 1
    return rows


def _counts(rows):
    out = {}
    for r in rows:
        out.setdefault(r["source"], {}).setdefault(r["label"], 0)
        out[r["source"]][r["label"]] += 1
    return out


def _write_preds(path: Path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    return path


@pytest.fixture()
def acc(layout, monkeypatch):
    a = _load("acceptance_v5")
    sealed = layout / "data" / "frozen_test_v5.csv"
    sealed.write_text("text,label\nsealed content,legitimate\n", encoding="utf-8")
    out = layout / "reports" / "frozen_test_v5_evaluation"
    rows = _pred_rows()
    preds = _write_preds(out / "predictions.csv", rows)
    manifest = layout / "reports" / "frozen_test_v5_manifest.json"

    def write_manifest(coverage_met=False, test_counts=None, **over):
        d = {"protocol_sha256": PIN, "protocol_file": "docs/protocol_v5.json",
             "sealed_files": [{"path": "data/frozen_test_v5.csv", "sha256": _sha(sealed)}],
             "evaluation_log": "reports/frozen_test_v5_evaluations.jsonl",
             "test_counts": test_counts or {"IMC25": {"suspicious": 100}, "MWOZ_TEST": {"legitimate": 60}},
             "coverage": {"met": coverage_met, "L1_status": "evaluable" if coverage_met else "NOT EVALUABLE"},
             "pool_counts": {"IMC25": {"suspicious": 900}, "MWOZ_TRAIN": {"legitimate": 500}}}
        d.update(over)
        manifest.write_text(json.dumps(d), encoding="utf-8")

    def write_log(entries=None):
        entries = entries if entries is not None else [{"test_sha256": _sha(sealed), "report_dir": str(out),
                                                        "model_version": "toy"}]
        (layout / "reports" / "frozen_test_v5_evaluations.jsonl").write_text(
            "".join(json.dumps(e) + "\n" for e in entries), encoding="utf-8")

    write_manifest()
    write_log()
    for name, val in (("PROTOCOL", layout / "docs" / "protocol_v5.json"),
                      ("PROTOCOL_HASH", layout / "docs" / "protocol_v5.sha256"),
                      ("MANIFEST", manifest), ("ROOT", layout)):
        monkeypatch.setattr(a, name, val)
    return {"a": a, "preds": preds, "out": out, "sealed": sealed, "manifest": manifest,
            "write_manifest": write_manifest, "write_log": write_log, "layout": layout}


class TestAcceptanceProvenanceGuards:
    def test_pin(self):
        assert _load("acceptance_v5").PROTOCOL_V5_SHA256 == PIN

    def test_happy_path_partially_evaluated_without_donations(self, acc):
        r = acc["a"].run(acc["preds"], bootstrap=False)
        assert r["L1"]["pass"] is None and "NOT EVALUABLE" in r["L1"]["status"]
        assert r["verdict"].startswith("PARTIALLY EVALUATED") and "VALIDATED (" not in r["verdict"]
        assert (acc["out"] / "acceptance_v5.json").is_file() and (acc["out"] / "acceptance_v5.md").is_file()

    def test_modified_protocol(self, acc):
        (acc["layout"] / "docs" / "protocol_v5.json").write_text("{}", encoding="utf-8")
        with pytest.raises(acc["a"].AcceptanceError, match="pinned"):
            acc["a"].run(acc["preds"], bootstrap=False)

    def test_missing_manifest(self, acc):
        acc["manifest"].unlink()
        with pytest.raises(acc["a"].AcceptanceError, match="manifest not found"):
            acc["a"].run(acc["preds"], bootstrap=False)

    def test_manifest_protocol_hash_changed(self, acc):
        acc["write_manifest"](protocol_sha256="1" * 64)
        with pytest.raises(acc["a"].AcceptanceError, match="protocol hash"):
            acc["a"].run(acc["preds"], bootstrap=False)

    def test_sealed_file_changed(self, acc):
        acc["sealed"].write_text("tampered,suspicious\n", encoding="utf-8")
        with pytest.raises(acc["a"].AcceptanceError, match="changed since sealing"):
            acc["a"].run(acc["preds"], bootstrap=False)

    def test_predictions_unavailable_without_logged_final_evaluation(self, acc):
        (acc["layout"] / "reports" / "frozen_test_v5_evaluations.jsonl").unlink()
        with pytest.raises(acc["a"].AcceptanceError, match="unavailable"):
            acc["a"].run(acc["preds"], bootstrap=False)

    def test_repeated_final_evaluation_refused(self, acc):
        e = {"test_sha256": _sha(acc["sealed"]), "report_dir": str(acc["out"]), "model_version": "toy"}
        acc["write_log"]([e, e])
        with pytest.raises(acc["a"].AcceptanceError, match="one-shot"):
            acc["a"].run(acc["preds"], bootstrap=False)

    def test_log_for_other_test_refused(self, acc):
        acc["write_log"]([{"test_sha256": "2" * 64, "report_dir": str(acc["out"])}])
        with pytest.raises(acc["a"].AcceptanceError, match="not of the sealed test"):
            acc["a"].run(acc["preds"], bootstrap=False)

    def test_predictions_from_other_directory_refused(self, acc):
        other = _write_preds(acc["layout"] / "elsewhere" / "predictions.csv", _pred_rows())
        with pytest.raises(acc["a"].AcceptanceError, match="report directory"):
            acc["a"].run(other, bootstrap=False)

    def test_missing_predictions_file(self, acc):
        acc["preds"].unlink()
        with pytest.raises(acc["a"].AcceptanceError, match="not found"):
            acc["a"].run(acc["preds"], bootstrap=False)

    def test_missing_columns_empty_and_invalid_values(self, acc):
        a = acc["a"]
        acc["preds"].write_text("row,label\n1,suspicious\n", encoding="utf-8")
        with pytest.raises(a.AcceptanceError, match="lacks columns"):
            a.run(acc["preds"], bootstrap=False)
        rows = _pred_rows()
        _write_preds(acc["preds"], rows[:0] or [dict.fromkeys(rows[0], "")])
        acc["preds"].write_text(acc["preds"].read_text(encoding="utf-8").splitlines()[0] + "\n", encoding="utf-8")
        with pytest.raises(a.AcceptanceError, match="empty"):
            a.run(acc["preds"], bootstrap=False)
        rows[0]["prediction"] = "maybe"
        _write_preds(acc["preds"], rows)
        with pytest.raises(a.AcceptanceError, match="Invalid"):
            a.run(acc["preds"], bootstrap=False)

    def test_row_count_must_match_sealed_test(self, acc):
        acc["write_manifest"](test_counts={"IMC25": {"suspicious": 99}, "MWOZ_TEST": {"legitimate": 60}})
        with pytest.raises(acc["a"].AcceptanceError, match="rows"):
            acc["a"].run(acc["preds"], bootstrap=False)


class TestAcceptanceCriteria:
    def test_validated_only_with_coverage_and_all_passes(self):
        a = _load("acceptance_v5")
        rows = _pred_rows(n_don=150)
        assert a.evaluate_criteria(rows, coverage_met=True, bootstrap=False, training_majority="suspicious")["verdict"] == "VALIDATED (advisory-only)"
        assert a.evaluate_criteria(rows, coverage_met=False, bootstrap=False, training_majority="suspicious")["verdict"].startswith("PARTIALLY")
        # A manifest claim of coverage is not enough: 40 donated rows do not meet the requirement.
        r = a.evaluate_criteria(_pred_rows(n_don=40), coverage_met=True, bootstrap=False, training_majority="suspicious")
        assert r["L1"]["pass"] is None and r["verdict"].startswith("PARTIALLY")

    def test_inconclusive_scams_are_misses(self):
        a = _load("acceptance_v5")
        r = a.evaluate_criteria(_pred_rows(miss=0, inconclusive=25), coverage_met=False, bootstrap=False, training_majority="suspicious")
        assert r["S1"]["numerator"] == 75 and r["S1"]["pass"] is False
        assert r["inconclusive"]["suspicious_counted_as_missed"] == 25
        assert r["verdict"] == "NOT VALIDATED"

    def test_failures_reported(self):
        a = _load("acceptance_v5")
        r = a.evaluate_criteria(_pred_rows(fp_mwoz=10), coverage_met=False, bootstrap=False, training_majority="suspicious")
        assert r["L2"]["pass"] is False and r["L2"]["numerator"] == 10 and r["verdict"] == "NOT VALIDATED"

    def test_wilson_intervals(self):
        a = _load("acceptance_v5")
        assert a.wilson(5, 10) == [0.2366, 0.7634]
        assert a.wilson(0, 10)[0] == 0.0 and a.wilson(10, 10)[1] == 1.0
        assert a.wilson(0, 0) is None

    def test_by_source_class_counts_and_cis(self):
        a = _load("acceptance_v5")
        r = a.evaluate_criteria(_pred_rows(), coverage_met=False, bootstrap=False, training_majority="suspicious")
        assert r["by_source"]["IMC25"]["n"] == 100 and r["by_source"]["MWOZ_TEST"]["class_counts"] == {"legitimate": 60}
        assert r["by_source"]["IMC25"]["recall_suspicious"]["ci95"]
        assert r["by_class"]["suspicious"]["n"] == 100 and r["S1"]["ci95"] and r["S2"]["denominator"] == 50

    def test_hey_mum_dad_reported_with_small_sample_warning(self):
        a = _load("acceptance_v5")
        small = a.evaluate_criteria(_pred_rows(hey=14), coverage_met=False, bootstrap=False, training_majority="suspicious")
        h = small["reported_not_gating"]["hey_mum_dad_recall"]
        assert h["denominator"] == 14 and h["ci95"] and "unreliable" in h["warning"] and h["small_sample"] is True
        assert "about 14" in h["warning"] and "does not gate" in h["warning"]
        big = a.evaluate_criteria(_pred_rows(hey=40), coverage_met=False, bootstrap=False, training_majority="suspicious")
        hb = big["reported_not_gating"]["hey_mum_dad_recall"]
        assert hb["small_sample"] is False and "unreliable" not in hb["warning"] and "about 14" in hb["warning"]
        # warning is also in the Markdown report, and the subgroup never changes the verdict
        assert "WARNING" in a.render(small) and small["verdict"] == big["verdict"]

    def test_bootstrap_ci_deterministic(self):
        a = _load("acceptance_v5")
        rows = _pred_rows()
        r1 = a.evaluate_criteria(rows, coverage_met=False, bootstrap=True, training_majority="suspicious")["M1"]["ci95_bootstrap"]
        r2 = a.evaluate_criteria(rows, coverage_met=False, bootstrap=True, training_majority="suspicious")["M1"]["ci95_bootstrap"]
        assert r1 == r2 and r1[0] <= a.compute_metrics([x["label"] for x in rows],
                                                       [x["prediction"] or "legitimate" for x in rows])["macro_avg"]["f1"] <= r1[1]

    def test_b1_uses_majority_class_not_always_legitimate(self):
        a = _load("acceptance_v5")
        r = a.evaluate_criteria(_pred_rows(), coverage_met=False, bootstrap=False, training_majority="suspicious")
        assert r["B1"]["majority_predicts"] == "suspicious"
        assert "always_legitimate_macro_f1_reported_only" in r["B1"]

    def test_acceptance_never_reads_sealed_test_text(self):
        src = (REPO / "scripts" / "acceptance_v5.py").read_text(encoding="utf-8")
        assert "load_evaluation_dataset" not in src and "load_dataset" not in src and "frozen_test_v5.csv" not in src


# ---------------------------------------------------------------------------
# Added: protocol fidelity, leakage safeguards, manifest/verdict hardening
# ---------------------------------------------------------------------------

def _protocol():
    return json.loads((REPO / "docs" / "protocol_v5.json").read_text(encoding="utf-8"))


class TestProtocolFidelity:
    def test_pins_and_v3_hash_preserved(self):
        assert (REPO / "docs" / "protocol_v5.sha256").read_text(encoding="utf-8").split()[0] == PIN
        v3 = "e97171dadedf57083265fc6bb38306942da5df41df32d079439bc11331d79e7e"
        assert _sha(REPO / "docs" / "protocol_v3.json") == v3
        assert (REPO / "docs" / "protocol_v3.sha256").read_text(encoding="utf-8").split()[0] == v3

    def test_selection_constants_match_protocol_text(self):
        m, ms = _load("select_text_model_v5"), _protocol()["model_selection"]
        assert ms["cv"] == f"StratifiedGroupKFold(n_splits={m.FOLDS}, shuffle=True, random_state={m.SEED}) over pool units"
        assert f"<= {m.DONATION_MAX_FPR:.2f}" in ms["rule"] and f">= {m.DONATION_CONSTRAINT_MIN_ROWS} DONATION" in ms["rule"]
        assert f"within {m.TIE}" in ms["rule"] and "alphabetical" in ms["rule"]
        assert m.grouped_oof.__module__ == m.__name__                 # own CV loop, not the v2 helper

    def test_candidate_shared_settings(self):
        c = _load("select_text_model_v5").CANDIDATES
        for name in ("A_v1_config", "C_stopwords_unigram", "E_stopwords_C3"):
            assert c[name]["tfidf"]["token_pattern"] == tc.TOKEN_PATTERN and c[name]["tfidf"]["sublinear_tf"] is True
        assert "stop_words" not in c["A_v1_config"]["tfidf"] and c["E_stopwords_C3"]["tfidf"]["ngram_range"] == [1, 2]
        assert all(c[n]["tfidf"]["sublinear_tf"] is True for n in ("G_char_wb_3_5", "H_char_wb_3_5_C3"))

    def test_acceptance_constants_match_protocol_text(self):
        a, p = _load("acceptance_v5"), _protocol()
        ac = p["acceptance_criteria"]
        for k, sym in (("S1", ">="), ("S2", ">="), ("L1", "<="), ("L2", "<="), ("M1", ">=")):
            assert f"{sym} {a.THRESHOLDS[k]:.2f}" in ac[k], k
        assert "strictly greater" in ac["B1"] and "rule-engine" in ac["B1"] and "majority-class" in ac["B1"]
        req = p["minimum_legitimate_coverage"]["test_requirement"]
        assert f">= {a.COVERAGE_MIN_MESSAGES} donated messages from >= {a.COVERAGE_MIN_DONORS} donors" in req
        assert f">= {a.COVERAGE_MIN_CATEGORIES} categories having >= {a.COVERAGE_CATEGORY_N}" in req
        assert a.sealing_paths(p) == ("data/frozen_test_v5.csv", "reports/frozen_test_v5_evaluations.jsonl")
        assert "PARTIALLY EVALUATED" in ac["verdict"] and "not validated for genuine financial alerts" in ac["verdict"]


class TestSelectionRuleDetails:
    def _r(self, f1, fpr, don=None):
        return {"kind": "config", "oof": {"macro_avg": {"f1": f1}, "legitimate_false_positive_rate": fpr},
                "donation_oof_fpr": don}

    def test_alphabetical_tie_break_and_boundaries(self):
        m = _load("select_text_model_v5")
        assert m.choose({"b": self._r(0.9, 0.1), "a": self._r(0.9, 0.1)}) == "a"
        # exactly 0.10 DONATION FPR is allowed; 0.1001 is not
        assert m.choose({"a": self._r(0.95, 0.2, 0.1001), "b": self._r(0.90, 0.2, 0.10)}) == "b"
        # a 0.006 gap is not a tie
        assert m.choose({"a": self._r(0.856, 0.3), "b": self._r(0.850, 0.0)}) == "a"

    def test_donation_constraint_needs_50_rows(self):
        m = _load("select_text_model_v5")
        ex = lambda n: [tc.Example(text="t", label="legitimate", group=f"DONATION:{i}", row=i) for i in range(n)]  # noqa: E731
        assert m.donation_fpr(ex(49), ["suspicious"] * 49) is None
        assert m.donation_fpr(ex(50), ["suspicious"] * 10 + ["legitimate"] * 40) == 0.2
        assert m.donation_fpr(ex(50), [None] * 50) == 0.0           # inconclusive is not a false positive

    def test_inconclusive_scored_as_miss_in_selection(self):
        m = _load("select_text_model_v5")
        y = ["suspicious"] * 4 + ["legitimate"] * 4
        s = m._scored(y, ["IMC25"] * 4 + ["MWOZ_TRAIN"] * 4, [None, None, "suspicious", "suspicious"] + [None] * 4)
        assert s["oof"]["suspicious"]["recall"] == 0.5 and s["oof"]["legitimate_false_positive_rate"] == 0.0
        assert s["oof"]["inconclusive"] == 6 and s["oof"]["inconclusive_suspicious_counted_as_missed"] == 2
        assert s["by_source"]["IMC25"]["recall_suspicious"] == 0.5
        assert s["by_source"]["IMC25"]["inconclusive_suspicious_counted_as_missed"] == 2


class TestSelectionLeakageSafeguards:
    def test_malformed_manifest(self, sel):
        m, _, _, _, manifest = sel
        manifest.write_text("{not json", encoding="utf-8")
        with pytest.raises(m.SelectionError, match="not valid JSON"):
            m.verify_inputs()

    def test_manifest_without_sealed_files(self, sel):
        m, _, _, _, manifest = sel
        d = json.loads(manifest.read_text(encoding="utf-8"))
        d["sealed_files"] = []
        manifest.write_text(json.dumps(d), encoding="utf-8")
        with pytest.raises(m.SelectionError, match="no sealed file"):
            m.verify_inputs()

    def test_refused_after_final_evaluation_logged(self, sel):
        m, layout, *_ = sel
        (layout / "reports" / "evals.jsonl").write_text('{"test_sha256": "x"}\n', encoding="utf-8")
        with pytest.raises(m.SelectionError, match="already logged"):
            m.verify_inputs()

    def test_empty_log_does_not_block(self, sel):
        m, layout, *_ = sel
        (layout / "reports" / "evals.jsonl").write_text("", encoding="utf-8")
        assert m.verify_inputs()["protocol_sha256"] == PIN

    def test_pool_at_sealed_path_refused(self, sel, monkeypatch):
        m, _, _, sealed, manifest = sel
        d = json.loads(manifest.read_text(encoding="utf-8"))
        d["pool_csv_sha256"] = _sha(sealed)
        manifest.write_text(json.dumps(d), encoding="utf-8")
        monkeypatch.setattr(m, "POOL", sealed)
        with pytest.raises(m.SelectionError, match="sealed file"):
            m.verify_inputs()

    def test_copy_refused_even_if_package_guard_points_elsewhere(self, sel, monkeypatch, tmp_path):
        m, layout, _, sealed, manifest = sel
        # The toy manifest is not a default, so pointing the env addition elsewhere drops it from the
        # package guard (defaults can never be dropped: tests/test_protocol_v5.py).
        monkeypatch.setenv(tc.SEALED_MANIFEST_ENV, str(tmp_path / "absent.json"))
        copy = layout / "data" / "renamed.csv"
        copy.write_bytes(sealed.read_bytes())
        d = json.loads(manifest.read_text(encoding="utf-8"))
        d["pool_csv_sha256"] = _sha(copy)
        manifest.write_text(json.dumps(d), encoding="utf-8")
        monkeypatch.setattr(m, "POOL", copy)
        assert not tc.is_sealed(copy)                         # package guard does not cover the toy manifest
        with pytest.raises(m.SelectionError, match="sealed file"):
            m.verify_inputs()

    def test_run_never_opens_sealed_test_log_or_predictions(self, sel, monkeypatch):
        import builtins
        import io
        m, layout, _, sealed, _ = sel
        forbidden = {sealed.resolve(), (layout / "reports" / "evals.jsonl").resolve()}
        opened = []
        real_open = io.open

        def spy(file, *a, **k):
            if isinstance(file, (str, Path)):
                opened.append(Path(file).resolve())
            return real_open(file, *a, **k)

        pool = m.POOL.resolve()
        with monkeypatch.context() as mp:
            mp.setattr(io, "open", spy)
            mp.setattr(builtins, "open", spy)
            report = m.run(train_selected=False)
        assert report["selected"] in m.CANDIDATES
        assert not forbidden & set(opened)
        assert not any(p.name in ("predictions.csv", "evaluation.json") for p in opened)
        assert pool in opened                                     # the spy did observe file access

    def test_run_works_with_sealed_test_absent(self, sel):
        m, _, _, sealed, _ = sel
        sealed.unlink()                     # selection must not depend on the test file in any way
        assert m.run(train_selected=False)["sealed_test_read"] is False

    def test_report_records_rule_and_convention(self, sel):
        m, *_ = sel
        r = m.run(train_selected=False)
        assert "alphabetical" in r["selection_rule"] and "inconclusive" in r["scoring_convention"]
        assert r["donation_constraint_applied"] is False          # toy pool has no DONATION rows
        for name in m.CANDIDATES:
            assert "inconclusive_suspicious_counted_as_missed" in r["results"][name]["oof"]


class TestMissingProtocolFiles:
    @pytest.mark.parametrize("name", ["protocol_v5.json", "protocol_v5.sha256"])
    def test_selection(self, sel, name):
        m, layout, *_ = sel
        (layout / "docs" / name).unlink()
        with pytest.raises(m.SelectionError, match="not found"):
            m.verify_protocol()

    @pytest.mark.parametrize("name", ["protocol_v5.json", "protocol_v5.sha256"])
    def test_acceptance(self, acc, name):
        (acc["layout"] / "docs" / name).unlink()
        with pytest.raises(acc["a"].AcceptanceError, match="not found"):
            acc["a"].run(acc["preds"], bootstrap=False)

    def test_modified_sha256_record(self, acc):
        (acc["layout"] / "docs" / "protocol_v5.sha256").write_text("0" * 64 + "  docs/protocol_v5.json\n",
                                                                    encoding="utf-8")
        with pytest.raises(acc["a"].AcceptanceError, match="pinned"):
            acc["a"].run(acc["preds"], bootstrap=False)


class TestAcceptanceHardening:
    def test_malformed_manifest(self, acc):
        acc["manifest"].write_text("{oops", encoding="utf-8")
        with pytest.raises(acc["a"].AcceptanceError, match="not valid JSON"):
            acc["a"].run(acc["preds"], bootstrap=False)

    def test_manifest_protocol_file_changed(self, acc):
        acc["write_manifest"](protocol_file="docs/protocol_v3.json")
        with pytest.raises(acc["a"].AcceptanceError, match="protocol_file"):
            acc["a"].run(acc["preds"], bootstrap=False)

    def test_manifest_log_redirected_to_bypass_one_shot(self, acc):
        e = {"test_sha256": _sha(acc["sealed"]), "report_dir": str(acc["out"])}
        acc["write_log"]([e, e])                                       # real log: two evaluations
        (acc["layout"] / "reports" / "fresh.jsonl").write_text(json.dumps(e) + "\n", encoding="utf-8")
        acc["write_manifest"](evaluation_log="reports/fresh.jsonl")
        with pytest.raises(acc["a"].AcceptanceError, match="evaluation-log path"):
            acc["a"].run(acc["preds"], bootstrap=False)

    def test_manifest_sealed_path_changed(self, acc):
        other = acc["layout"] / "data" / "other.csv"
        other.write_bytes(acc["sealed"].read_bytes())
        acc["write_manifest"](sealed_files=[{"path": "data/other.csv", "sha256": _sha(other)}])
        with pytest.raises(acc["a"].AcceptanceError, match="sealed-file"):
            acc["a"].run(acc["preds"], bootstrap=False)

    def test_manifest_with_two_sealed_files(self, acc):
        s = {"path": "data/frozen_test_v5.csv", "sha256": _sha(acc["sealed"])}
        acc["write_manifest"](sealed_files=[s, s])
        with pytest.raises(acc["a"].AcceptanceError, match="exactly one"):
            acc["a"].run(acc["preds"], bootstrap=False)

    def test_sealed_file_deleted(self, acc):
        acc["sealed"].unlink()
        with pytest.raises(acc["a"].AcceptanceError, match="missing or changed"):
            acc["a"].run(acc["preds"], bootstrap=False)

    def test_per_source_counts_must_match(self, acc):
        acc["write_manifest"](test_counts={"IMC25": {"suspicious": 60}, "MWOZ_TEST": {"legitimate": 100}})
        with pytest.raises(acc["a"].AcceptanceError, match="per-source"):
            acc["a"].run(acc["preds"], bootstrap=False)

    def test_duplicate_row_ids(self, acc):
        rows = _pred_rows()
        rows[1]["row"] = rows[0]["row"]
        _write_preds(acc["preds"], rows)
        with pytest.raises(acc["a"].AcceptanceError, match="duplicate"):
            acc["a"].run(acc["preds"], bootstrap=False)

    def test_missing_donor_column(self, acc):
        rows = [{k: v for k, v in r.items() if k != "donor"} for r in _pred_rows()]
        _write_preds(acc["preds"], rows)
        with pytest.raises(acc["a"].AcceptanceError, match="donor"):
            acc["a"].run(acc["preds"], bootstrap=False)

    def test_manifest_claims_coverage_without_donations(self, acc):
        acc["write_manifest"](coverage_met=True)
        with pytest.raises(acc["a"].AcceptanceError, match="coverage"):
            acc["a"].run(acc["preds"], bootstrap=False)

    def test_manifest_denies_coverage_that_predictions_show(self, acc):
        rows = _pred_rows(n_don=150)
        _write_preds(acc["preds"], rows)
        acc["write_manifest"](coverage_met=False, test_counts=_counts(rows))
        with pytest.raises(acc["a"].AcceptanceError, match="coverage"):
            acc["a"].run(acc["preds"], bootstrap=False)

    def test_full_run_with_coverage_validated(self, acc):
        rows = _pred_rows(n_don=150)
        _write_preds(acc["preds"], rows)
        acc["write_manifest"](coverage_met=True, test_counts=_counts(rows))
        r = acc["a"].run(acc["preds"], bootstrap=False)
        assert r["verdict"] == "VALIDATED (advisory-only)" and r["L1"]["denominator"] == 150
        assert r["L1"]["coverage"]["recomputed"]["donors"] == 5


class TestVerdictRules:
    def test_no_donations_never_validated(self):
        a = _load("acceptance_v5")
        for cov in (True, False):
            r = a.evaluate_criteria(_pred_rows(miss=0), coverage_met=cov, bootstrap=False, training_majority="suspicious")   # everything else perfect
            assert r["L1"]["pass"] is None and r["L1"]["status"].startswith("NOT EVALUABLE")
            assert r["verdict"] == "PARTIALLY EVALUATED — not validated for genuine financial alerts"
            assert "VALIDATED (" not in r["verdict"]
            assert r["notes"][0].startswith("Scam recall evaluated; false positives on genuine")
            assert r["L1"]["numerator"] == 0 and r["L1"]["denominator"] == 0 and r["L1"]["value"] is None

    def test_insufficient_donors_or_categories_not_evaluable(self):
        a = _load("acceptance_v5")
        assert a.evaluate_criteria(_pred_rows(n_don=200, donors=4), True, bootstrap=False, training_majority="suspicious")["L1"]["pass"] is None
        rows = _pred_rows(n_don=150)
        for r in rows:
            if r["source"] == "DONATION" and r["category"] == "otp_notice":
                r["category"] = "upi_alert"                         # only 2 categories left
        assert a.evaluate_criteria(rows, True, bootstrap=False, training_majority="suspicious")["L1"]["pass"] is None

    def test_l1_failure_is_not_validated(self):
        a = _load("acceptance_v5")
        r = a.evaluate_criteria(_pred_rows(n_don=150, fp_don=16), coverage_met=True, bootstrap=False, training_majority="suspicious")
        assert r["L1"]["pass"] is False and r["L1"]["numerator"] == 16 and r["verdict"] == "NOT VALIDATED"
        ok = a.evaluate_criteria(_pred_rows(n_don=150, fp_don=15), coverage_met=True, bootstrap=False, training_majority="suspicious")
        assert ok["L1"]["pass"] is True                            # 15/150 = 0.10 is within the threshold

    def test_partial_failure_beats_not_evaluable(self):
        a = _load("acceptance_v5")
        r = a.evaluate_criteria(_pred_rows(miss=30), coverage_met=False, bootstrap=False, training_majority="suspicious")
        assert r["S1"]["pass"] is False and r["verdict"] == "NOT VALIDATED"

    def test_b1_strictly_greater(self):
        a = _load("acceptance_v5")
        rows = _pred_rows()
        for r in rows:
            r["rule_prediction"] = r["prediction"] or "legitimate"     # rules exactly as good as the model
        r = a.evaluate_criteria(rows, coverage_met=False, bootstrap=False, training_majority="suspicious")
        assert r["B1"]["value"] == r["B1"]["rule_engine_baseline_macro_f1"] and r["B1"]["pass"] is False
        assert r["verdict"] == "NOT VALIDATED"

    def test_thresholds_inclusive_at_boundary(self):
        a = _load("acceptance_v5")
        r = a.evaluate_criteria(_pred_rows(miss=20, n_mwoz=100, fp_mwoz=5), coverage_met=False, bootstrap=False, training_majority="suspicious")
        assert r["S1"]["value"] == 0.8 and r["S1"]["pass"] is True
        assert r["L2"]["value"] == 0.05 and r["L2"]["pass"] is True

    def test_inconclusive_legitimate_not_false_positive(self):
        a = _load("acceptance_v5")
        r = a.evaluate_criteria(_pred_rows(inconclusive_legit=10), coverage_met=False, bootstrap=False, training_majority="suspicious")
        assert r["L2"]["numerator"] == 0 and r["inconclusive"]["legitimate_not_counted_as_fp"] == 10
        assert r["by_source"]["MWOZ_TEST"]["inconclusive"] == 10

    def test_by_class_cis_and_markdown_tables(self):
        a = _load("acceptance_v5")
        r = a.evaluate_criteria(_pred_rows(inconclusive=5), coverage_met=False, bootstrap=False, training_majority="suspicious")
        assert r["by_class"]["suspicious"]["recall_numerator"] == 85 and r["by_class"]["suspicious"]["recall_ci95"]
        assert r["by_source"]["IMC25"]["inconclusive_suspicious_counted_as_missed"] == 5
        md = a.render(r)
        assert "| IMC25 | 100 |" in md and "| MWOZ_TEST | 60 |" in md and "| suspicious | 100 |" in md
        assert "NOT EVALUABLE" in md


_V5_TEST_ROWS = [
    ("verify your parcel fee now at the courier portal", "suspicious", "IMC25", "delivery", 0),
    ("your electricity connection will be cut tonight call officer", "suspicious", "IMC25", "government", 1),
    ("hi mum my phone broke please send money to this new account", "suspicious", "IMC25", "hey mum/dad", 0),
    ("i would like a cheap hotel in the centre for two nights", "legitimate", "MWOZ_TEST", "conversation", 0),
    ("the train to cambridge leaves at nine from platform two", "legitimate", "MWOZ_TEST", "conversation", 0),
]


class TestEndToEndOneShotWithAcceptance:
    """Toy sealed test in a temp layout -> tc.evaluate(final_sealed) once -> acceptance_v5."""

    @pytest.fixture()
    def e2e(self, layout, monkeypatch):
        test = layout / "data" / "frozen_test_v5.csv"
        with test.open("w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["id", "text", "label", "source", "group", "category", "india_linked", "donor"])
            for i, (t, lab, s, cat, ind) in enumerate(_V5_TEST_ROWS):
                w.writerow([f"T4-{i:06d}", t, lab, s, f"{s}:{i}", cat, ind, ""])
        manifest = layout / "reports" / "frozen_test_v5_manifest.json"
        manifest.write_text(json.dumps({
            "protocol_file": "docs/protocol_v5.json", "protocol_sha256": PIN,
            "sealed_files": [{"path": "data/frozen_test_v5.csv", "sha256": _sha(test)}],
            "evaluation_log": "reports/frozen_test_v5_evaluations.jsonl", "sealed_utc": "t",
            "test_counts": {"IMC25": {"suspicious": 3}, "MWOZ_TEST": {"legitimate": 2}},
            "coverage": {"met": False, "L1_status": "NOT EVALUABLE"}, "pool_csv_sha256": "n/a",
            "pool_counts": {"IMC25": {"suspicious": 9}, "MWOZ_TRAIN": {"legitimate": 5}}}), encoding="utf-8")
        monkeypatch.setenv(tc.SEALED_MANIFEST_ENV, str(manifest))
        train = _write_csv(layout / "data" / "toy_train.csv", _toy_rows())
        tc.clear_model_cache()
        tc.train(train, model_dir=layout / "m", run_cv=False)
        a = _load("acceptance_v5")
        for name, val in (("PROTOCOL", layout / "docs" / "protocol_v5.json"),
                          ("PROTOCOL_HASH", layout / "docs" / "protocol_v5.sha256"),
                          ("MANIFEST", manifest), ("ROOT", layout)):
            monkeypatch.setattr(a, name, val)
        yield {"a": a, "test": test, "model": layout / "m" / tc.ARTIFACT_FILENAME, "train": train, "layout": layout}
        tc.clear_model_cache()

    def test_predictions_unavailable_before_final_evaluation(self, e2e):
        with pytest.raises(e2e["a"].AcceptanceError, match="unavailable"):
            e2e["a"].run(e2e["layout"] / "reports" / "eval" / "predictions.csv", bootstrap=False)

    def test_one_shot_then_acceptance(self, e2e):
        out = e2e["layout"] / "reports" / "eval"
        with pytest.raises(tc.SealedDataError):
            tc.load_dataset(e2e["test"])                               # selection-side loaders refuse it
        tc.evaluate(e2e["test"], e2e["model"], training_data=e2e["train"], out_dir=out, final_sealed=True)
        cols = set(next(csv.reader((out / "predictions.csv").open(encoding="utf-8"))))
        assert e2e["a"].REQUIRED_COLUMNS <= cols and "text" not in cols
        with pytest.raises(tc.EvaluationError, match="already been evaluated once"):
            tc.evaluate(e2e["test"], e2e["model"], training_data=e2e["train"], out_dir=out / "again",
                        final_sealed=True)
        r = e2e["a"].run(out / "predictions.csv", bootstrap=False)
        assert r["L1"]["status"].startswith("NOT EVALUABLE") and "VALIDATED (" not in r["verdict"]
        assert r["reported_not_gating"]["hey_mum_dad_recall"]["denominator"] == 1
        assert r["S1"]["denominator"] == 3 and r["L2"]["denominator"] == 2
        assert (out / "acceptance_v5.md").is_file()


# ---------------------------------------------------------------------------
# Added (review 2): baselines without test labels, exact thresholds, CV splits
# ---------------------------------------------------------------------------

class TestSelectionSplitsAndBaselines:
    def _pool(self):
        return tc.load_dataset(_write_csv(Path(tempfile.mkdtemp()) / "p.csv", _toy_rows()))

    def test_folds_are_protocol_sgkf_and_never_split_units(self):
        from sklearn.model_selection import StratifiedGroupKFold
        m = _load("select_text_model_v5")
        pool = self._pool()
        splits = m.folds(pool)
        ref = list(StratifiedGroupKFold(n_splits=5, shuffle=True, random_state=42).split(
            [e.text for e in pool], [e.label for e in pool], [e.group for e in pool]))
        assert len(splits) == 5
        assert all((a == c).all() and (b == d).all() for (a, b), (c, d) in zip(splits, ref))
        for tr, te in splits:
            assert not {pool[i].group for i in tr} & {pool[i].group for i in te}
        assert sorted(i for _, te in splits for i in te) == list(range(len(pool)))

    def test_majority_baseline_uses_training_folds_only(self):
        m = _load("select_text_model_v5")
        pool = self._pool()
        splits = m.folds(pool)
        preds = m.training_majority_oof(pool, splits)
        for tr, te in splits:
            c = Counter(pool[i].label for i in tr)
            maj = max(c.items(), key=lambda kv: (kv[1], kv[0]))[0]
            assert {preds[i] for i in te} == {maj}
        # flipping every held-out label of fold 0 cannot change fold 0's baseline prediction
        tr0, te0 = splits[0]
        flipped = [tc.Example(text=e.text, label=("legitimate" if e.label == "suspicious" else "suspicious")
                              if i in set(te0) else e.label, group=e.group, row=e.row) for i, e in enumerate(pool)]
        assert [m.training_majority_oof(flipped, splits)[i] for i in te0] == [preds[i] for i in te0]

    def test_exact_005_gap_is_a_tie(self):
        m = _load("select_text_model_v5")
        r = lambda f1, fpr: {"kind": "config", "donation_oof_fpr": None,  # noqa: E731
                             "oof": {"macro_avg": {"f1": f1}, "legitimate_false_positive_rate": fpr}}
        assert 0.850 - 0.845 > 0.005                                    # the float trap being guarded
        assert m.choose({"a": r(0.850, 0.3), "b": r(0.845, 0.0)}) == "b"
        assert m.choose({"a": r(0.850, 0.3), "b": r(0.8449, 0.0)}) == "a"

    def test_selection_report_baselines_labelled_not_criteria(self, sel):
        m, *_ = sel
        res = m.run(train_selected=False)["results"]
        for b in ("baseline_majority_class", "baseline_always_legitimate"):
            assert res[b]["kind"] == "baseline" and "not a selection criterion" in res[b]["note"]
        assert "training fold" in res["baseline_majority_class"]["note"]

    def test_selection_source_never_reads_test_counts(self):
        src = (REPO / "scripts" / "select_text_model_v5.py").read_text(encoding="utf-8")
        assert "test_counts" not in src and '"coverage"' not in src and "retrospective" not in src


class TestAcceptanceTrainingMajorityAndExactness:
    def test_training_majority_from_manifest(self):
        a = _load("acceptance_v5")
        assert a.training_majority({"pool_counts": {"IMC25": {"suspicious": 9340}, "R115": {"suspicious": 353,
                                                    "legitimate": 343}, "MWOZ_TRAIN": {"legitimate": 548}}}) == "suspicious"
        assert a.training_majority({"pool_counts": {"X": {"legitimate": 5, "suspicious": 2}}}) == "legitimate"
        with pytest.raises(a.AcceptanceError, match="pool_counts"):
            a.training_majority({})

    def test_run_refuses_without_pool_counts(self, acc):
        acc["write_manifest"](pool_counts=None)
        with pytest.raises(acc["a"].AcceptanceError, match="pool_counts"):
            acc["a"].run(acc["preds"], bootstrap=False)

    def test_b1_uses_training_majority_even_when_test_majority_differs(self, acc):
        acc["write_manifest"](pool_counts={"MWOZ_TRAIN": {"legitimate": 900}, "IMC25": {"suspicious": 100}})
        r = acc["a"].run(acc["preds"], bootstrap=False)
        b = r["B1"]
        assert b["majority_predicts"] == "legitimate" and "training pool" in b["majority_class_source"]
        ref = b["retrospective_reference_test_majority"]
        assert ref["predicts"] == "suspicious" and "RETROSPECTIVE REFERENCE ONLY" in ref["note"]
        assert "Retrospective reference only" in (acc["out"] / "acceptance_v5.md").read_text(encoding="utf-8")

    def test_retrospective_reference_never_gates(self):
        a = _load("acceptance_v5")
        rows = _pred_rows()
        for r in rows:
            r["prediction"] = "suspicious"                    # model == test-majority predictor
        res = a.evaluate_criteria(rows, False, bootstrap=False, training_majority="legitimate")
        assert res["B1"]["retrospective_reference_test_majority"]["macro_f1"] == res["B1"]["value"]
        # B1 is decided by the training-majority (legitimate) and rule baselines only
        assert res["B1"]["pass"] == (a.macro_f1_exact([x["label"] for x in rows], ["suspicious"] * len(rows))
                                     > max(res["B1"]["rule_engine_baseline_macro_f1"],
                                           res["B1"]["majority_class_baseline_macro_f1"]))

    def test_invalid_training_majority(self):
        a = _load("acceptance_v5")
        with pytest.raises(a.AcceptanceError):
            a.evaluate_criteria(_pred_rows(), False, bootstrap=False, training_majority="spam")

    def test_threshold_comparisons_are_exact(self):
        a = _load("acceptance_v5")
        assert round(19999 / 25000, 4) == 0.8 and a._meets(19999, 25000, ">=", 0.80) is False
        assert a._meets(20000, 25000, ">=", 0.80) is True
        assert a._meets(5001, 100000, "<=", 0.05) is False and a._meets(5000, 100000, "<=", 0.05) is True
        assert a._meets(0, 0, ">=", 0.80) is False

    def test_rounded_recall_cannot_pass_s1(self):
        a = _load("acceptance_v5")
        r = a.evaluate_criteria(_pred_rows(n_imc=25000, miss=5001, hey=0), False, bootstrap=False,
                                training_majority="suspicious")
        assert r["S1"]["value"] == 0.8 and r["S1"]["pass"] is False


class TestBuilderManifestRecordsPoolCounts:
    def test_manifest_has_pool_counts(self, tmp_path, monkeypatch):
        b = _load("build_pool_and_test_v5")
        (tmp_path / "data").mkdir()
        (tmp_path / "reports").mkdir()
        for name, val in (("REPO", tmp_path), ("POOL_OUT", tmp_path / "data" / "pool.csv"),
                          ("EXCLUDED_OUT", tmp_path / "data" / "pool.excluded.csv"),
                          ("MANIFEST", tmp_path / "reports" / "frozen_test_v5_manifest.json")):
            monkeypatch.setattr(b, name, val)
        row = lambda t, lab, s: {"text": t, "label": lab, "group": f"{s}:1", "source": s, "key": "k",  # noqa: E731
                                 "category": "c", "india_linked": False, "donor": ""}
        p = {"_sha256": PIN, "sources": {}, "sealing": {"test_file": "data/frozen_test_v5.csv (gitignored)"}}
        report = {"test_counts": {"IMC25": {"suspicious": 1}}, "test_units": 1, "coverage": {"met": False},
                  "pool_counts": {"MWOZ_TRAIN": {"legitimate": 1}}}
        paths = {"test_file": "data/frozen_test_v5.csv", "protocol_file": "docs/protocol_v5.json",
                 "evaluation_log": "reports/frozen_test_v5_evaluations.jsonl"}
        b._write_outputs(p, paths, [row("a b c", "legitimate", "MWOZ_TRAIN")], [row("x y z", "suspicious", "IMC25")],
                         [], report)
        m = json.loads((tmp_path / "reports" / "frozen_test_v5_manifest.json").read_text(encoding="utf-8"))
        assert m["pool_counts"] == {"MWOZ_TRAIN": {"legitimate": 1}}
        assert _load("acceptance_v5").training_majority(m) == "legitimate"
        from tests.seal_state_v5 import check_seal_state
        assert check_seal_state(REPO) in ("clean", "sealed")          # real repo: clean or validly sealed


# ---------------------------------------------------------------------------
# Added (v5 verification): inconclusive = miss throughout selection; B1 from
# training-pool counts only. Protocol v5 model_selection.scoring /
# acceptance_criteria.conventions / B1.
# ---------------------------------------------------------------------------

import inspect  # noqa: E402


class TestInconclusiveThroughoutSelectionV5:
    def test_protocol_text_is_what_the_code_implements(self):
        p = json.loads((REPO / "docs" / "protocol_v5.json").read_text(encoding="utf-8"))
        assert "per-fold macro-F1" in p["model_selection"]["scoring"]
        m = _load("select_text_model_v5")
        assert m.effective([None, "suspicious", "legitimate"]) == ["legitimate", "suspicious", "legitimate"]

    def test_fold_macro_f1_counts_inconclusive_as_miss(self, monkeypatch):
        m = _load("select_text_model_v5")
        pool = tc.load_dataset(_write_csv(Path(tempfile.mkdtemp()) / "p.csv", _toy_rows()))
        splits = m.folds(pool)
        real = m._predict

        def abstain_half(pipe, texts):
            p, c = real(pipe, texts)
            return [None if i % 2 == 0 else v for i, v in enumerate(p)], c

        monkeypatch.setattr(m, "_predict", abstain_half)
        preds, cv = m.grouped_oof(pool, m.CANDIDATES["A_v1_config"], splits)
        y = [e.label for e in pool]
        expected = [m._score([y[i] for i in te], m.effective([preds[i] for i in te]))["macro_avg"]["f1"]
                    for _, te in splits]
        assert cv["fold_macro_f1"] == expected
        classified_only = [m._score([y[i] for i in te if preds[i] is not None],
                                    [preds[i] for i in te if preds[i] is not None])["macro_avg"]["f1"] for _, te in splits]
        assert cv["fold_macro_f1"] != classified_only           # the convention changes the numbers

    def test_abstaining_candidate_is_penalised_end_to_end(self, sel, monkeypatch):
        m, *_ = sel
        pool = tc.load_dataset(m.POOL)
        susp = {e.text for e in pool if e.label == "suspicious"}
        real = m._predict

        def char_models_abstain_on_scams(pipe, texts):
            p, c = real(pipe, texts)
            if pipe.named_steps["tfidf"].analyzer == "char_wb":
                p = [None if t in susp else v for t, v in zip(texts, p)]
            return p, c

        r0 = m.run(train_selected=False)                     # same candidates without abstention
        monkeypatch.setattr(m, "_predict", char_models_abstain_on_scams)
        r = m.run(train_selected=False)
        n_susp = len(susp)
        for name in ("G_char_wb_3_5", "H_char_wb_3_5_C3"):
            res = r["results"][name]
            # Every abstention is a missed scam in the pooled score ...
            assert res["oof"]["inconclusive_suspicious_counted_as_missed"] == n_susp
            assert res["oof"]["suspicious"]["recall"] == 0.0 and res["oof"]["classified"] == len(pool)
            # ... in the per-source breakdown ...
            assert sum(s["inconclusive_suspicious_counted_as_missed"] for s in res["by_source"].values()) == n_susp
            # ... and in every fold.
            assert max(res["fold_macro_f1"]) < 0.7
            # Abstaining (only on scams here) leaves the model's legitimate FPR unchanged: no FP is created.
            assert res["oof"]["legitimate_false_positive_rate"] == r0["results"][name]["oof"]["legitimate_false_positive_rate"]
            assert res["oof"]["macro_avg"]["f1"] < r0["results"][name]["oof"]["macro_avg"]["f1"]
        assert r["selected"] not in ("G_char_wb_3_5", "H_char_wb_3_5_C3")
        assert r["scoring_convention"].startswith("inconclusive scored as legitimate")


class TestB1UsesTrainingPoolCountsV5:
    def test_training_majority_has_no_default_or_test_fallback(self):
        a = _load("acceptance_v5")
        param = inspect.signature(a.evaluate_criteria).parameters["training_majority"]
        assert param.default is inspect.Parameter.empty and param.kind is inspect.Parameter.KEYWORD_ONLY
        src = inspect.getsource(a.evaluate_criteria)
        b1 = src[src.index('out["B1"]'):src.index("hey_rate")]
        assert '"pass": f1_model > f1_rules and f1_model > f1_major' in b1
        assert "f1_major = macro_f1_exact(y, [training_majority] * len(y))" in src

    def test_b1_ignores_test_class_balance(self):
        a = _load("acceptance_v5")
        # Test is legitimate-majority (100 scams vs 300 MWOZ); training pool is scam-majority.
        rows = _pred_rows(n_imc=100, n_mwoz=300)
        r = a.evaluate_criteria(rows, False, bootstrap=False, training_majority="suspicious")
        assert r["B1"]["majority_predicts"] == "suspicious"
        assert r["B1"]["retrospective_reference_test_majority"]["predicts"] == "legitimate"
        y = [x["label"] for x in rows]
        assert r["B1"]["majority_class_baseline_macro_f1"] == a.compute_metrics(y, ["suspicious"] * len(y))["macro_avg"]["f1"]

    def test_b1_follows_manifest_pool_counts(self, acc):
        for pool, expected in (({"IMC25": {"suspicious": 9}, "MWOZ_TRAIN": {"legitimate": 1}}, "suspicious"),
                               ({"IMC25": {"suspicious": 1}, "MWOZ_TRAIN": {"legitimate": 9}}, "legitimate")):
            acc["write_manifest"](pool_counts=pool)
            r = acc["a"].run(acc["preds"], bootstrap=False)
            assert r["B1"]["majority_predicts"] == expected
            assert r["B1"]["retrospective_reference_test_majority"]["predicts"] == "suspicious"   # same test both times

    def test_retrospective_reference_cannot_change_pass(self):
        a = _load("acceptance_v5")
        rows = _pred_rows(n_imc=100, n_mwoz=300)
        base = a.evaluate_criteria(rows, False, bootstrap=False, training_majority="suspicious")
        rows_more_legit = rows + [dict(rows[-1], row=10_000 + i) for i in range(200)]   # shift test balance further
        shifted = a.evaluate_criteria(rows_more_legit, False, bootstrap=False, training_majority="suspicious")
        assert base["B1"]["majority_predicts"] == shifted["B1"]["majority_predicts"] == "suspicious"
