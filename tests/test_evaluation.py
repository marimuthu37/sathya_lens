"""
test_evaluation.py — Tests for the evaluation-only path and dataset preparation

The model under test is a tiny real model trained on toy data in a tmp dir;
evaluation always loads it from disk.
"""
import csv
import importlib.util
import json
from pathlib import Path

import pytest

sklearn = pytest.importorskip("sklearn")

from satyalens_analysis import text_classifier as tc  # noqa: E402
from tests.test_text_classifier import _toy_rows, _write_csv  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
REAL_CSV = REPO / "data" / "real_world_test_v1.csv"

_EVAL_SUSP = ["please share the otp you got so we can cancel", "send gift card codes now",
              "your account is blocked click to verify kyc", "pay fee to claim your prize",
              "install anydesk to get your refund"]
_EVAL_LEGIT = ["salary credited to your a/c", "invoice uploaded to the portal",
               "rent reminder for this month", "emi auto debit scheduled", "otp for login never share"]


def _eval_rows(n_each=5, extra_fields=None):
    rows = []
    for i in range(n_each):
        for label, pool in (("suspicious", _EVAL_SUSP), ("legitimate", _EVAL_LEGIT)):
            row = {"text": f"{pool[i % len(pool)]} ref {i}", "label": label}
            if extra_fields:
                row.update(extra_fields(i, label))
            rows.append(row)
    return rows


def _write_eval(path: Path, rows, fieldnames=None):
    fieldnames = fieldnames or list(rows[0].keys())
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)
    return path


@pytest.fixture(scope="module")
def model_env(tmp_path_factory):
    """Trained toy model + its training CSV."""
    base = tmp_path_factory.mktemp("evalmodel")
    train_csv = _write_csv(base / "train.csv", _toy_rows())
    tc.train(train_csv, model_dir=base / "m", run_cv=False)
    return {"model": base / "m" / tc.ARTIFACT_FILENAME, "train_csv": train_csv}


@pytest.fixture(autouse=True)
def _clear_cache():
    tc.clear_model_cache()
    yield
    tc.clear_model_cache()


def _run(model_env, data, tmp_path, **kw):
    return tc.evaluate(data, model_env["model"], training_data=kw.pop("training_data", model_env["train_csv"]),
                       out_dir=tmp_path / "out", **kw)

# ---------------------------------------------------------------------------
# Dataset validation
# ---------------------------------------------------------------------------

class TestEvaluationDatasetValidation:
    def test_valid_dataset(self, tmp_path):
        ex = tc.load_evaluation_dataset(_write_eval(tmp_path / "e.csv", _eval_rows()))
        assert len(ex) == 10 and {e.label for e in ex} == set(tc.LABELS)

    def test_missing_required_column(self, tmp_path):
        p = _write_eval(tmp_path / "e.csv", [{"text": "x"}])
        with pytest.raises(tc.EvaluationError, match="missing required column"):
            tc.load_evaluation_dataset(p)

    def test_missing_file(self, tmp_path):
        with pytest.raises(tc.EvaluationError, match="not found"):
            tc.load_evaluation_dataset(tmp_path / "nope.csv")

    def test_invalid_label(self, tmp_path):
        rows = _eval_rows() + [{"text": "hello", "label": "smishing"}]
        with pytest.raises(tc.EvaluationError, match="invalid label 'smishing'"):
            tc.load_evaluation_dataset(_write_eval(tmp_path / "e.csv", rows))

    def test_missing_label(self, tmp_path):
        rows = _eval_rows() + [{"text": "hello", "label": ""}]
        with pytest.raises(tc.EvaluationError, match="missing label"):
            tc.load_evaluation_dataset(_write_eval(tmp_path / "e.csv", rows))

    def test_empty_message(self, tmp_path):
        rows = _eval_rows() + [{"text": "   ", "label": "legitimate"}]
        with pytest.raises(tc.EvaluationError, match="line 12: empty text"):
            tc.load_evaluation_dataset(_write_eval(tmp_path / "e.csv", rows))

    def test_empty_dataset(self, tmp_path):
        p = tmp_path / "e.csv"
        p.write_text("text,label\n", encoding="utf-8")
        with pytest.raises(tc.EvaluationError, match="no rows"):
            tc.load_evaluation_dataset(p)

    def test_single_class_dataset(self, tmp_path):
        rows = [r for r in _eval_rows() if r["label"] == "suspicious"]
        with pytest.raises(tc.EvaluationError, match="both labels"):
            tc.load_evaluation_dataset(_write_eval(tmp_path / "e.csv", rows))

# ---------------------------------------------------------------------------
# Duplicates and overlap
# ---------------------------------------------------------------------------

class TestDuplicatesAndOverlap:
    def test_exact_and_near_duplicates_detected(self):
        ex = [tc.EvalExample("Call 0800 now!", "suspicious", 2, {}),
              tc.EvalExample("call 0900   NOW!", "suspicious", 3, {}),      # exact after normalise
              tc.EvalExample("Call now http://a.b/x", "suspicious", 4, {}),  # near (skeleton)
              tc.EvalExample("unrelated text", "legitimate", 5, {})]
        d = tc.find_duplicates(ex)
        assert d["exact_normalised_duplicate_rows"] == 1
        assert d["near_duplicate_clusters"] == 1
        assert d["rows_in_near_duplicate_clusters"] == 3

    def test_training_overlap_kinds(self):
        train = ["Share your OTP now to cancel the payment", "salary credited to your account today"]
        res = tc.find_training_overlap(
            [(2, "share your otp NOW to cancel the payment"),         # exact normalised
             (3, "Share your OTP now!! to cancel the payment 123"),   # skeleton
             (4, "salary credited to your account today please"),     # jaccard 6/7
             (5, "completely different message")], train)
        assert res["exact_normalised"] == [2] and res["skeleton"] == [3]
        assert res["token_jaccard"] == [4] and res["total"] == 3

    def test_evaluate_refuses_training_overlap(self, model_env, tmp_path):
        rows = _eval_rows() + [{"text": _toy_rows()[0][1], "label": "suspicious"}]
        with pytest.raises(tc.EvaluationError, match="overlap the training data"):
            _run(model_env, _write_eval(tmp_path / "e.csv", rows), tmp_path)

    def test_overlap_check_skipped_when_training_file_hash_differs(self, model_env, tmp_path):
        other = _write_csv(tmp_path / "other.csv", _toy_rows()[:-1])
        r = _run(model_env, _write_eval(tmp_path / "e.csv", _eval_rows()), tmp_path, training_data=other)
        ov = r["data_checks"]["training_overlap"]
        assert ov["performed"] is False and "SHA-256 differs" in ov["reason"]

    def test_overlap_check_reported_when_training_file_missing(self, model_env, tmp_path):
        r = _run(model_env, _write_eval(tmp_path / "e.csv", _eval_rows()), tmp_path,
                 training_data=tmp_path / "missing.csv")
        assert r["data_checks"]["training_overlap"]["performed"] is False

# ---------------------------------------------------------------------------
# Model loading failures
# ---------------------------------------------------------------------------

def _copy_model(model_env, dst: Path) -> Path:
    dst.mkdir()
    src = model_env["model"].parent
    for n in (tc.ARTIFACT_FILENAME, tc.META_FILENAME):
        (dst / n).write_bytes((src / n).read_bytes())
    return dst / tc.ARTIFACT_FILENAME


class TestModelFailures:
    def test_missing_model(self, tmp_path):
        data = _write_eval(tmp_path / "e.csv", _eval_rows())
        with pytest.raises(tc.EvaluationError, match="not found"):
            tc.evaluate(data, tmp_path / "none.joblib", out_dir=tmp_path / "o")

    def test_corrupted_model(self, model_env, tmp_path):
        model = _copy_model(model_env, tmp_path / "m")
        model.write_bytes(b"corrupt")
        with pytest.raises(tc.EvaluationError, match="SHA-256"):
            tc.evaluate(_write_eval(tmp_path / "e.csv", _eval_rows()), model, out_dir=tmp_path / "o")

    def test_missing_metadata(self, model_env, tmp_path):
        model = _copy_model(model_env, tmp_path / "m")
        (model.parent / tc.META_FILENAME).unlink()
        with pytest.raises(tc.EvaluationError, match="metadata not found"):
            tc.evaluate(_write_eval(tmp_path / "e.csv", _eval_rows()), model, out_dir=tmp_path / "o")

    def test_model_name_must_match_metadata(self, model_env, tmp_path):
        model = _copy_model(model_env, tmp_path / "m")
        renamed = model.with_name("other.joblib")
        renamed.write_bytes(model.read_bytes())
        with pytest.raises(tc.EvaluationError, match="metadata describes"):
            tc.evaluate(_write_eval(tmp_path / "e.csv", _eval_rows()), renamed, out_dir=tmp_path / "o")

    def test_missing_deps(self, model_env, tmp_path, monkeypatch):
        monkeypatch.setattr(tc, "_ML_DEPS_OK", False)
        with pytest.raises(tc.EvaluationError):
            _run(model_env, _write_eval(tmp_path / "e.csv", _eval_rows()), tmp_path)

# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

class TestMetrics:
    def test_metric_calculation_by_hand(self):
        # legit: 3 right, 1 wrong; susp: 2 right, 2 missed
        y_true = ["legitimate"] * 4 + ["suspicious"] * 4
        y_pred = ["legitimate"] * 3 + ["suspicious"] + ["suspicious"] * 2 + ["legitimate"] * 2
        m = tc.compute_metrics(y_true, y_pred)
        m.update(tc._error_rates(m))
        assert m["confusion_matrix"]["rows_true_cols_pred"] == [[3, 1], [2, 2]]
        assert m["accuracy"] == 0.625
        assert m["suspicious"]["precision"] == pytest.approx(2 / 3, abs=1e-4)
        assert m["suspicious"]["recall"] == 0.5
        assert m["legitimate"]["precision"] == 0.6 and m["legitimate"]["recall"] == 0.75
        assert m["suspicious_false_negative_rate"] == 0.5
        assert m["legitimate_false_positive_rate"] == 0.25
        f1_s, f1_l = 2 * (2 / 3) * 0.5 / (2 / 3 + 0.5), 2 * 0.6 * 0.75 / 1.35
        assert m["macro_avg"]["f1"] == pytest.approx((f1_s + f1_l) / 2, abs=1e-4)

    def test_report_metrics_match_predictions(self, model_env, tmp_path):
        data = _write_eval(tmp_path / "e.csv", _eval_rows())
        r = _run(model_env, data, tmp_path)
        loaded = tc.load_model(model_env["model"].parent)
        ex = tc.load_evaluation_dataset(data)
        preds = [tc.classify_text(e.text, model_dir=model_env["model"].parent).predicted_class for e in ex]
        scored = [(e.label, p) for e, p in zip(ex, preds) if p is not None]
        expected = tc.compute_metrics([a for a, _ in scored], [b for _, b in scored])
        assert r["metrics"]["confusion_matrix"] == expected["confusion_matrix"]
        assert r["coverage"]["classified"] == len(scored)
        assert loaded is not None

    def test_inconclusive_counted_in_coverage_not_metrics(self, model_env, tmp_path):
        rows = _eval_rows() + [{"text": "zzqx 123", "label": "legitimate"}]
        r = _run(model_env, _write_eval(tmp_path / "e.csv", rows), tmp_path)
        assert r["coverage"]["inconclusive"] == 1
        assert r["coverage"]["inconclusive_by_class"]["legitimate"] == 1
        assert sum(map(sum, r["metrics"]["confusion_matrix"]["rows_true_cols_pred"])) == 10

    def test_majority_baseline(self, model_env, tmp_path):
        rows = _eval_rows() + [{"text": "otp for login never share again", "label": "legitimate"}]
        r = _run(model_env, _write_eval(tmp_path / "e.csv", rows), tmp_path)
        assert r["coverage"]["inconclusive"] == 0   # majority is taken over classified rows
        b = r["majority_class_baseline"]
        assert b["predicts"] == "legitimate"
        assert b["suspicious"]["recall"] == 0.0 and b["suspicious_false_negative_rate"] == 1.0

# ---------------------------------------------------------------------------
# Evaluation never fits / never modifies the model
# ---------------------------------------------------------------------------

class TestNeverFits:
    def test_no_fit_called(self, model_env, tmp_path, monkeypatch):
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.linear_model import LogisticRegression
        from sklearn.pipeline import Pipeline

        def _forbidden(*a, **k):
            raise AssertionError("fit must not be called during evaluation")
        for cls in (Pipeline, TfidfVectorizer, LogisticRegression):
            monkeypatch.setattr(cls, "fit", _forbidden)
        monkeypatch.setattr(TfidfVectorizer, "fit_transform", _forbidden)
        monkeypatch.setattr(tc, "train", _forbidden)
        monkeypatch.setattr(tc, "build_pipeline", _forbidden)
        r = _run(model_env, _write_eval(tmp_path / "e.csv", _eval_rows()), tmp_path)
        assert r["evaluation_only"] is True

    def test_artifact_and_metadata_unchanged(self, model_env, tmp_path):
        model, meta = model_env["model"], model_env["model"].parent / tc.META_FILENAME
        before = (tc._sha256(model), meta.read_bytes())
        r = _run(model_env, _write_eval(tmp_path / "e.csv", _eval_rows()), tmp_path)
        assert (tc._sha256(model), meta.read_bytes()) == before
        assert r["model"]["artifact_sha256"] == before[0]
        assert r["model"]["artifact_unchanged_after_evaluation"] is True

# ---------------------------------------------------------------------------
# Breakdowns and report output
# ---------------------------------------------------------------------------

class TestReporting:
    def test_source_and_category_breakdowns(self, model_env, tmp_path):
        rows = _eval_rows(n_each=25, extra_fields=lambda i, lab: {
            "source": "big_source" if i < 22 else "tiny_source", "category": f"cat_{lab}"})
        r = _run(model_env, _write_eval(tmp_path / "e.csv", rows), tmp_path)
        src = r["breakdowns"]["source"]
        assert src["big_source"]["n"] == 44 and "accuracy" in src["big_source"]
        assert src["tiny_source"]["n"] == 6 and "accuracy" not in src["tiny_source"]
        assert "note" in src["tiny_source"]
        cat = r["breakdowns"]["category"]
        assert cat["cat_suspicious"]["class_counts"] == {"legitimate": 0, "suspicious": 25}
        assert "recall_suspicious" in cat["cat_suspicious"]

    def test_near_duplicate_sensitivity(self, model_env, tmp_path):
        rows = _eval_rows(extra_fields=lambda i, lab: {"near_duplicate_cluster": f"{lab}-{i % 2}"})
        r = _run(model_env, _write_eval(tmp_path / "e.csv", rows), tmp_path)
        assert r["near_duplicate_sensitivity"]["rows"] == 4

    def test_report_files_and_required_fields(self, model_env, tmp_path):
        data = _write_eval(tmp_path / "e.csv", _eval_rows())
        r = _run(model_env, data, tmp_path)
        out = tmp_path / "out"
        saved = json.loads((out / "evaluation.json").read_text(encoding="utf-8"))
        assert saved["dataset"]["sha256"] == tc._sha256(data)
        assert saved["model"]["artifact_sha256"] == tc._sha256(model_env["model"])
        assert saved["evaluated_at_utc"] and saved["environment"]["scikit_learn"] == sklearn.__version__
        assert saved["dataset"]["rows"] == 10 and saved["dataset"]["class_counts"] == {
            "legitimate": 5, "suspicious": 5}
        summary = (out / "summary.md").read_text(encoding="utf-8")
        assert "Majority baseline" in summary and "false-negative rate" in summary
        assert "provenance unknown" in summary   # no sidecar -> not claimed real or synthetic
        json.dumps(r)

    def test_cli_evaluate(self, model_env, tmp_path, capsys):
        data = _write_eval(tmp_path / "e.csv", _eval_rows())
        code = tc._main(["evaluate", "--data", str(data), "--model", str(model_env["model"]),
                         "--training-data", str(model_env["train_csv"]), "--out-dir", str(tmp_path / "o")])
        assert code == 0 and "Macro-avg F1" in capsys.readouterr().out

    def test_cli_evaluate_invalid_data_exit_code(self, model_env, tmp_path, capsys):
        bad = _write_eval(tmp_path / "e.csv", [{"text": "x", "label": "spam"}])
        code = tc._main(["evaluate", "--data", str(bad), "--model", str(model_env["model"])])
        assert code == 2 and "ERROR" in capsys.readouterr().err

# ---------------------------------------------------------------------------
# Dataset preparation script
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def prep():
    spec = importlib.util.spec_from_file_location("prep", REPO / "scripts" / "prepare_real_world_test.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestPreparation:
    def test_every_row_accounted_for(self, prep):
        rows = [
            ["ham", "See you at lunch"],
            ["Smishing", "Your bank account is locked, call 07123456789"],
            ["spam", "Ringtone club, text STOP"],
            ["weird", "something"],
            ["ham", "   "],
            ["ham", "see you at LUNCH"],                       # duplicate of row 2
            ["ham", "Same text both labels"],
            ["smishing", "same text both labels"],             # conflicting labels
            ["smishing", "Share your OTP now to cancel the payment"],   # training overlap
        ]
        acc, exc, _ = prep.classify_rows(rows, ["share your otp now to cancel the payment"])
        assert len(acc) + len(exc) == len(rows)
        outcomes = {e["source_row"]: e["outcome"] for e in exc}
        assert outcomes == {4: "ambiguous", 5: "rejected", 6: "rejected", 7: "duplicate",
                            8: "ambiguous", 9: "ambiguous", 10: "training_overlap"}
        labels = {a["source_row"]: a["label"] for a in acc}
        assert labels == {2: "legitimate", 3: "suspicious"}
        assert acc[1]["text"].endswith("###########")   # phone masked
        assert acc[1]["source_label"] == "smishing"     # case-normalised

    def test_anonymisation_does_not_change_model_features(self, prep, model_env):
        loaded = tc.load_model(model_env["model"].parent)
        tfidf = loaded.pipeline.named_steps["tfidf"]
        original = "Call 07123456789 or 0800-123-4567 to claim, ref 12345"
        masked, n_phone, _ = prep.anonymise(original)
        assert n_phone == 2 and "12345" in masked            # short numbers untouched
        assert (tfidf.transform([original]) != tfidf.transform([masked])).nnz == 0

    def test_email_local_part_masked(self, prep):
        masked, _, n_email = prep.anonymise("mail john.doe@example.com today")
        assert n_email == 1 and masked == "mail ***@example.com today"

    def test_heuristic_topic(self, prep):
        assert prep.heuristic_topic("Your bank account is locked") == "financial"
        assert prep.heuristic_topic("see you at lunch") == "other"


@pytest.mark.skipif(not REAL_CSV.is_file(), reason="data/real_world_test_v1.csv not prepared")
class TestPreparedRealWorldDataset:
    def test_valid_independent_and_documented(self):
        ex = tc.load_evaluation_dataset(REAL_CSV)
        prov = json.loads(REAL_CSV.with_suffix(".provenance.json").read_text(encoding="utf-8"))
        val = json.loads(REAL_CSV.with_suffix(".validation.json").read_text(encoding="utf-8"))
        assert prov["synthetic"] is False and prov["evaluation_only"] is True
        assert prov["source"]["licence"].startswith("CC BY 4.0")
        assert len(ex) == prov["rows"] == val["outcomes"]["accepted"]
        assert sum(val["outcomes"].values()) == val["source_rows"]
        assert val["training_overlap"]["total"] == 0
        train = [e.text for e in tc.load_dataset(REPO / "data" / "synthetic_financial_messages_v1.csv")]
        assert tc.find_training_overlap([(e.row, e.text) for e in ex], train)["total"] == 0
