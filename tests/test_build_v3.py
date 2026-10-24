"""
test_build_v3.py — Protocol v3 builder functions, exact overlap join,
anon-v1 inference hook, evaluation outputs, v3 selection rule and acceptance
criteria. All on synthetic toy inputs; no v3 pool or sealed test is needed.
"""
import csv
import importlib.util
import json
import random
from pathlib import Path

import pytest

sklearn = pytest.importorskip("sklearn")

from satyalens_analysis import text_classifier as tc  # noqa: E402
from tests.test_text_classifier import _toy_rows, _write_csv  # noqa: E402

REPO = Path(__file__).resolve().parent.parent


def _load(name):
    spec = importlib.util.spec_from_file_location(name, REPO / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def b():
    return _load("build_pool_and_test_v3")


@pytest.fixture(scope="module")
def acc():
    return _load("acceptance_v3")


@pytest.fixture(scope="module")
def sel3():
    return _load("select_text_model_v3")


def _row(source, key, text, label, base=None, **kw):
    return {"source": source, "key": key, "raw": text, "text": text, "label": label, "base": base,
            "category": kw.get("category", "c"), "india_linked": kw.get("india", False), **kw}

# ---------------------------------------------------------------------------
# Exact Jaccard join
# ---------------------------------------------------------------------------

class TestJaccardJoin:
    def test_matches_brute_force(self):
        rng = random.Random(7)
        vocab = [f"w{i}" for i in range(40)]
        q = [set(rng.sample(vocab, rng.randint(1, 8))) for _ in range(300)]
        r = [set(rng.sample(vocab, rng.randint(1, 8))) for _ in range(300)]
        for t in (0.5, 0.6, 0.8):
            brute = [any(len(a & c) / len(a | c) >= t for c in r) for a in q]
            assert tc.jaccard_matches(q, r, t) == brute

    def test_empty_sets(self):
        assert tc.jaccard_matches([set(), {"a"}], [set(), {"a"}], 0.8) == [False, True]

# ---------------------------------------------------------------------------
# Builder functions
# ---------------------------------------------------------------------------

class TestBuilder:
    def test_protocol_tamper_detected(self, b, tmp_path, monkeypatch):
        fake = tmp_path / "p.json"
        fake.write_text("{}", encoding="utf-8")
        monkeypatch.setattr(b, "PROTOCOL", fake)
        with pytest.raises(b.StopBuild, match="does not match"):
            b.load_protocol()

    def test_protocol_loads_when_untouched(self, b):
        assert b.load_protocol()["_sha256"] == (REPO / "docs" / "protocol_v3.sha256").read_text().split()[0]

    def test_long_digit_classification(self, b):
        kinds = b.classify_long_digits("call 9876543210 or +919876543210 or 07821230901, ref 123456789012")
        assert kinds == ["indian_mobile_10_digit", "plus_country_code",
                         "zero_prefixed_landline_or_uk_mobile", "other_id_or_number"]

    def test_privacy_check(self, b):
        b.privacy_check([_row("X", "1", "Rs 1,499 paid numtoken", "legitimate")])
        with pytest.raises(b.StopBuild, match="4-digit"):
            b.privacy_check([_row("X", "1", "otp 4821", "legitimate")])

    def test_dedupe_priority_and_conflicts(self, b):
        ex = []
        rows = [_row("SYN", "s1", "your parcel is out for delivery", "legitimate"),
                _row("IMC25", "i1", "Your parcel is OUT for delivery", "legitimate"),
                _row("R115", "r1", "same text here", "suspicious"),
                _row("MWOZ_TRAIN", "m1", "same text here", "legitimate")]
        kept = b.dedupe(rows, ex)
        assert [r["key"] for r in kept] == ["i1"]          # IMC25 outranks SYN
        assert sorted(e["outcome"] for e in ex) == ["ambiguous", "ambiguous", "duplicate"]

    def test_grouping_links_templates_and_base_keys(self, b):
        rows = [_row("IMC25", "row2", "your sbi account will be blocked update kyc now urltoken", "suspicious"),
                _row("IMC25", "row3", "your sbi account will be blocked update kyc today urltoken", "suspicious"),
                _row("IMC25", "row4", "congratulations you won a lottery prize claim", "suspicious"),
                _row("R115", "a", "first message", "legitimate", base="safe:x:en"),
                _row("R115", "b", "totally different words", "legitimate", base="safe:x:en")]
        b.assign_groups(rows)
        assert rows[0]["group"] == rows[1]["group"] == "IMC25:row2"
        assert rows[2]["group"] == "IMC25:row4"
        assert rows[3]["group"] == rows[4]["group"] == "R115:a"

    def test_assignment_is_deterministic_and_role_based(self, b):
        rows = [_row("IMC25", f"row{i}", "t", "suspicious") for i in range(500)] + \
               [_row("MWOZ_TEST", "m", "t", "legitimate"), _row("R115", "r", "t", "legitimate"),
                _row("SYN", "s", "t", "legitimate")]
        for r in rows:
            r["group"] = f"{r['source']}:{r['key']}"
        b.assign_split(rows, "salt-x")
        first = [r["split"] for r in rows]
        b.assign_split(rows, "salt-x")
        assert first == [r["split"] for r in rows]
        assert rows[-3]["split"] == "test" and rows[-2]["split"] == rows[-1]["split"] == "train"
        share = sum(r["split"] == "test" for r in rows[:500]) / 500
        assert 0.15 < share < 0.25

    def test_leakage_removes_train_groups_not_test(self, b):
        rows = [_row("IMC25", "t1", "verify your account now at urltoken please", "suspicious"),
                _row("IMC25", "tr1", "verify your account now at urltoken please", "suspicious"),
                _row("IMC25", "tr2", "another member of the same train group", "suspicious"),
                _row("IMC25", "tr3", "an unrelated clean training message", "suspicious")]
        for r, g, s in zip(rows, ["G1", "G2", "G2", "G3"], ["test", "train", "train", "train"]):
            r["group"], r["split"] = g, s
        ex = []
        stats = b.remove_cross_split_leakage(rows, ex)
        assert [r["split"] for r in rows] == ["test", "removed", "removed", "train"]
        assert stats["train_groups_removed"] == 1 and len(ex) == 2

    def test_coverage_rule(self, b):
        p = b.load_protocol()
        assert b.coverage(p, [])["L1_status"] == "NOT EVALUABLE"
        test = [{"source": "DONATION", "donor": f"d{i % 5}", "category": ["otp_alert", "delivery_notice",
                 "account_debit_credit_alert"][i % 3]} for i in range(150)]
        assert b.coverage(p, test)["met"] is True
        assert b.coverage(p, test[:149])["met"] is False

    def test_token_rates(self, b):
        rates = b.token_rates([_row("IMC25", "1", "click urltoken now", "suspicious"),
                               _row("IMC25", "2", "call phonetoken", "suspicious")])
        assert rates["IMC25|suspicious"]["urltoken"] == 0.5

    def test_builder_never_names_the_sealed_file(self):
        src = (REPO / "scripts" / "build_pool_and_test_v3.py").read_text(encoding="utf-8")
        assert "frozen_test_v3.csv" not in src

# ---------------------------------------------------------------------------
# anon-v1 inference hook and evaluation outputs
# ---------------------------------------------------------------------------

class TestAnonPreprocessing:
    def test_meta_records_and_inference_applies_anonymiser(self, tmp_path, monkeypatch):
        meta = tc.train(_write_csv(tmp_path / "t.csv", _toy_rows()), model_dir=tmp_path / "m",
                        run_cv=False, preprocessing="anon-v1")
        assert meta["preprocessing"] == "anon-v1"
        seen = []
        import satyalens_analysis.anonymise as an
        real = an.anonymise
        monkeypatch.setattr(an, "anonymise", lambda t: seen.append(t) or real(t))
        tc.clear_model_cache()
        tc.classify_text("share otp 482913 now", model_dir=tmp_path / "m")
        assert "share otp 482913 now" in seen

    def test_unknown_preprocessing_rejected(self, tmp_path):
        with pytest.raises(ValueError):
            tc.train(_write_csv(tmp_path / "t.csv", _toy_rows()), model_dir=tmp_path / "m", preprocessing="x")

    def test_legacy_models_unchanged(self, tmp_path):
        meta = tc.train(_write_csv(tmp_path / "t.csv", _toy_rows()), model_dir=tmp_path / "m", run_cv=False)
        assert meta["preprocessing"] is None


class TestEvaluationOutputs:
    def test_predictions_csv_has_no_text_and_rule_baseline_reported(self, tmp_path):
        model_dir = tmp_path / "m"
        train = _write_csv(tmp_path / "t.csv", _toy_rows())
        tc.train(train, model_dir=model_dir, run_cv=False)
        data = tmp_path / "e.csv"
        with data.open("w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["text", "label", "source"])
            w.writerow(["quickly share the otp code you received", "suspicious", "IMC25"])
            w.writerow(["your monthly pay has arrived in savings", "legitimate", "MWOZ_TEST"])
        r = tc.evaluate(data, model_dir / tc.ARTIFACT_FILENAME, training_data=train, out_dir=tmp_path / "o")
        assert r["rule_engine_baseline"]["rows"] == 2
        content = (tmp_path / "o" / "predictions.csv").read_text(encoding="utf-8")
        assert "quickly share" not in content and "monthly pay" not in content
        rows = list(csv.DictReader((tmp_path / "o" / "predictions.csv").open(encoding="utf-8")))
        assert rows[0]["rule_prediction"] == "suspicious" and rows[0]["source"] == "IMC25"

# ---------------------------------------------------------------------------
# v3 selection rule and acceptance criteria
# ---------------------------------------------------------------------------

class TestSelectionV3:
    def _r(self, f1, fpr, don=None):
        return {"kind": "config", "oof": {"macro_avg": {"f1": f1}, "legitimate_false_positive_rate": fpr},
                "donation_oof_fpr": don}

    def test_candidates_match_protocol(self, sel3):
        p = json.loads((REPO / "docs" / "protocol_v3.json").read_text(encoding="utf-8"))
        assert set(sel3.CANDIDATES) == set(p["model_selection"]["candidates"])

    def test_donation_constraint(self, sel3):
        r = {"a": self._r(0.95, 0.1, don=0.2), "b": self._r(0.90, 0.1, don=0.05)}
        assert sel3.choose(r) == "b"

    def test_constraint_not_applied_without_donations(self, sel3):
        assert sel3.choose({"a": self._r(0.95, 0.1), "b": self._r(0.90, 0.0)}) == "a"

    def test_no_eligible_candidate(self, sel3):
        with pytest.raises(RuntimeError):
            sel3.choose({"a": self._r(0.9, 0.1, don=0.5)})

    def test_selection_uses_guarded_loader_only(self):
        src = (REPO / "scripts" / "select_text_model_v3.py").read_text(encoding="utf-8")
        assert "tc.load_dataset(POOL)" in src and "frozen_test_v3.csv" not in src
        assert "open(" not in src.replace("read_text", "")


def _pred(source, label, pred, rule="legitimate", india="0"):
    return {"source": source, "label": label, "prediction": pred, "rule_prediction": rule, "india_linked": india}


class TestAcceptance:
    def _good(self):
        rows = [_pred("IMC25", "suspicious", "suspicious", india="1") for _ in range(90)]
        rows += [_pred("IMC25", "suspicious", "legitimate") for _ in range(10)]
        rows += [_pred("MWOZ_TEST", "legitimate", "legitimate") for _ in range(60)]
        rows += [_pred("DONATION", "legitimate", "legitimate") for _ in range(50)]
        return rows

    def test_all_pass_validated(self, acc):
        out = acc.evaluate_criteria(self._good(), coverage_met=True)
        assert out["verdict"] == "VALIDATED (advisory-only)"
        assert out["S1"]["value"] == 0.9 and out["L1"]["pass"] is True

    def test_coverage_not_met_is_not_evaluable_not_pass(self, acc):
        out = acc.evaluate_criteria(self._good(), coverage_met=False)
        assert out["L1"]["pass"] is None and "NOT EVALUABLE" in out["L1"]["status"]
        assert out["verdict"].startswith("PARTIALLY EVALUATED")

    def test_inconclusive_suspicious_counts_as_miss(self, acc):
        rows = self._good()
        for r in rows[:30]:
            r["prediction"] = ""
        out = acc.evaluate_criteria(rows, coverage_met=True)
        assert out["S1"]["numerator"] == 60 and out["S1"]["pass"] is False
        assert out["inconclusive"]["suspicious_counted_as_missed"] == 30
        assert out["verdict"] == "NOT VALIDATED"

    def test_inconclusive_legitimate_not_false_positive(self, acc):
        rows = self._good()
        for r in rows:
            if r["source"] == "MWOZ_TEST":
                r["prediction"] = ""
        assert acc.evaluate_criteria(rows, coverage_met=True)["L2"]["numerator"] == 0

    def test_failures_reported(self, acc):
        rows = self._good()
        for r in rows:
            if r["source"] == "MWOZ_TEST":
                r["prediction"] = "suspicious"
        out = acc.evaluate_criteria(rows, coverage_met=True)
        assert out["L2"]["pass"] is False and out["verdict"] == "NOT VALIDATED"

    def test_b1_requires_beating_rule_baseline(self, acc):
        rows = self._good()
        for r in rows:
            r["rule_prediction"] = r["label"]          # perfect rules
        assert acc.evaluate_criteria(rows, coverage_met=True)["B1"]["pass"] is False
