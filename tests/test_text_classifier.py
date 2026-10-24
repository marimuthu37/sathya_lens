"""
test_text_classifier.py — Tests for the ML text classifier and its integration

All inference tests train a real (tiny) model and load it from disk — no
mocked predictions.  Model directories are always pointed at tmp paths so
results do not depend on whether a locally trained default artifact exists.
"""
import csv
import json
import sys
from pathlib import Path

import pytest

from satyalens_analysis import text_classifier as tc
from satyalens_analysis.schema import ComponentStatus, TextClassifierResult
from satyalens_analysis.unified import run_analysis

sklearn = pytest.importorskip("sklearn")

REPO_ROOT = Path(__file__).resolve().parent.parent
SYNTHETIC_CSV = REPO_ROOT / "data" / "synthetic_financial_messages_v1.csv"

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

_SUSPICIOUS = [
    ["share the otp you received to cancel the payment", "tell me your otp now sir",
     "forward the otp code immediately"],
    ["your account will be blocked today click link to update kyc",
     "kyc pending account suspended verify at link", "complete kyc now or account closed"],
    ["pay processing fee to claim lottery prize", "you won lucky draw pay registration charge",
     "prize money released after fee payment"],
    ["install anydesk to receive refund", "download screen sharing app for cashback",
     "share remote access code with support"],
    ["buy gift cards and send me the codes keep it secret",
     "purchase google play cards urgently do not tell anyone", "need gift card codes for client"],
    ["police will arrest you pay security amount", "fir registered pay now or arrest",
     "cyber cell case transfer money for verification"],
]
_LEGITIMATE = [
    ["otp for your login do not share it with anyone", "use this otp to register never share it",
     "one time password for transaction valid ten minutes do not share"],
    ["amount debited from your account at merchant", "your account is debited by upi",
     "debit alert available balance updated"],
    ["salary credited to your account", "neft credit received from employer",
     "your account has been credited"],
    ["invoice attached payable to usual account within thirty days",
     "please find the invoice for october consignment", "invoice uploaded to vendor portal"],
    ["rent due on the fifth same account as usual", "gentle reminder rent for this month",
     "please transfer rent by the due date"],
    ["emi will be auto debited keep sufficient balance", "loan emi due next week",
     "emi received thank you"],
]


def _write_csv(path: Path, rows, header=("id", "text", "label", "group")):
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        for r in rows:
            w.writerow(r)
    return path


def _toy_rows():
    rows = []
    for label, groups in (("suspicious", _SUSPICIOUS), ("legitimate", _LEGITIMATE)):
        for gi, texts in enumerate(groups):
            for ti, text in enumerate(texts):
                rows.append((f"{label}-{gi}-{ti}", text, label, f"{label[0]}{gi}"))
    return rows


@pytest.fixture()
def toy_csv(tmp_path):
    return _write_csv(tmp_path / "toy.csv", _toy_rows())


@pytest.fixture(scope="module")
def trained_dir(tmp_path_factory):
    """A real model trained on the toy dataset and saved to disk."""
    base = tmp_path_factory.mktemp("model")
    data = _write_csv(base / "toy.csv", _toy_rows())
    tc.train(data, model_dir=base / "m", run_cv=False)
    return base / "m"


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    """Clear the model cache and point the default model dir at an empty tmp dir."""
    tc.clear_model_cache()
    monkeypatch.setenv(tc.ENV_MODEL_DIR, str(tmp_path / "no_model_here"))
    yield
    tc.clear_model_cache()

# ---------------------------------------------------------------------------
# 1. Dataset validation
# ---------------------------------------------------------------------------

class TestDatasetValidation:
    def test_valid_dataset_loads(self, toy_csv):
        examples = tc.load_dataset(toy_csv)
        assert len(examples) == len(_toy_rows())
        assert {e.label for e in examples} == {"suspicious", "legitimate"}

    def test_invalid_label_rejected(self, tmp_path):
        rows = _toy_rows() + [("x", "some text", "fraud", "g-x")]
        with pytest.raises(tc.DatasetValidationError, match="invalid label 'fraud'"):
            tc.load_dataset(_write_csv(tmp_path / "d.csv", rows))

    def test_label_case_is_strict(self, tmp_path):
        rows = _toy_rows() + [("x", "some text", "Suspicious", "g-x")]
        with pytest.raises(tc.DatasetValidationError, match="invalid label"):
            tc.load_dataset(_write_csv(tmp_path / "d.csv", rows))

    def test_empty_text_rejected(self, tmp_path):
        rows = _toy_rows() + [("x", "   ", "suspicious", "g-x")]
        with pytest.raises(tc.DatasetValidationError, match="empty text"):
            tc.load_dataset(_write_csv(tmp_path / "d.csv", rows))

    def test_empty_group_rejected(self, tmp_path):
        rows = _toy_rows() + [("x", "text here", "suspicious", "")]
        with pytest.raises(tc.DatasetValidationError, match="empty group"):
            tc.load_dataset(_write_csv(tmp_path / "d.csv", rows))

    def test_missing_column_rejected(self, tmp_path):
        path = _write_csv(tmp_path / "d.csv", [("1", "t", "suspicious")], header=("id", "text", "label"))
        with pytest.raises(tc.DatasetValidationError, match="missing required column"):
            tc.load_dataset(path)

    def test_missing_file_rejected(self, tmp_path):
        with pytest.raises(tc.DatasetValidationError, match="not found"):
            tc.load_dataset(tmp_path / "nope.csv")

    def test_single_class_rejected(self, tmp_path):
        rows = [r for r in _toy_rows() if r[2] == "suspicious"]
        with pytest.raises(tc.DatasetValidationError, match="both labels"):
            tc.load_dataset(_write_csv(tmp_path / "d.csv", rows))

    def test_mixed_label_group_rejected(self, tmp_path):
        rows = _toy_rows() + [("x", "totally new text", "legitimate", "s0")]
        with pytest.raises(tc.DatasetValidationError, match="mixed-label groups"):
            tc.load_dataset(_write_csv(tmp_path / "d.csv", rows))

    def test_shipped_synthetic_dataset_is_valid(self):
        examples = tc.load_dataset(SYNTHETIC_CSV)
        assert len(examples) > 100
        prov = json.loads(SYNTHETIC_CSV.with_suffix(".provenance.json").read_text(encoding="utf-8"))
        assert prov["synthetic"] is True

# ---------------------------------------------------------------------------
# 2. Duplicates, grouped split, leakage, reproducibility
# ---------------------------------------------------------------------------

class TestSplitAndLeakage:
    def test_normalised_duplicates_removed(self, tmp_path):
        rows = _toy_rows() + [("d", "SHARE the OTP you received   to cancel the payment", "suspicious", "s5")]
        kept, removed = tc.deduplicate(tc.load_dataset(_write_csv(tmp_path / "d.csv", rows)))
        assert removed == 1
        assert len(kept) == len(_toy_rows())

    def test_digit_only_variants_are_duplicates(self):
        assert tc.normalise_text("Pay Rs 500 now") == tc.normalise_text("pay rs 12,999 NOW")

    def test_conflicting_duplicate_labels_rejected(self, tmp_path):
        rows = _toy_rows() + [("c", "salary credited to your account", "suspicious", "s5")]
        with pytest.raises(tc.DatasetValidationError, match="conflicting labels"):
            tc.deduplicate(tc.load_dataset(_write_csv(tmp_path / "d.csv", rows)))

    def test_groups_never_span_train_and_test(self, toy_csv):
        train, test = tc.grouped_split(tc.load_dataset(toy_csv), seed=1)
        leak = tc.find_leakage(train, test)
        assert leak == {"shared_groups": [], "shared_texts": []}

    def test_both_classes_in_each_split(self, toy_csv):
        train, test = tc.grouped_split(tc.load_dataset(toy_csv))
        assert {e.label for e in train} == {e.label for e in test} == {"suspicious", "legitimate"}

    def test_split_is_reproducible_with_same_seed(self, toy_csv):
        ex = tc.load_dataset(toy_csv)
        a = tc.grouped_split(ex, seed=7)
        b = tc.grouped_split(ex, seed=7)
        assert [e.row for e in a[1]] == [e.row for e in b[1]]

    def test_split_changes_with_seed(self, toy_csv):
        ex = tc.load_dataset(toy_csv)
        tests = {tuple(sorted({e.group for e in tc.grouped_split(ex, seed=s)[1]})) for s in range(6)}
        assert len(tests) > 1

    def test_too_few_groups_rejected(self, tmp_path):
        rows = [("1", "a b", "suspicious", "g1"), ("2", "c d", "legitimate", "g2"),
                ("3", "e f", "legitimate", "g3")]
        with pytest.raises(tc.DatasetValidationError, match="at least 2"):
            tc.grouped_split(tc.load_dataset(_write_csv(tmp_path / "d.csv", rows)))

    def test_find_leakage_detects_overlap(self):
        a = [tc.Example("Pay 5 now", "suspicious", "g1", 2)]
        b = [tc.Example("pay 9 NOW", "suspicious", "g1", 3)]
        leak = tc.find_leakage(a, b)
        assert leak["shared_groups"] == ["g1"] and leak["shared_texts"] == ["pay 0 now"]

    def test_invalid_test_size_rejected(self, toy_csv):
        with pytest.raises(tc.DatasetValidationError, match="test_size"):
            tc.grouped_split(tc.load_dataset(toy_csv), test_size=0.9)

# ---------------------------------------------------------------------------
# 3. Training and persistence
# ---------------------------------------------------------------------------

class TestTraining:
    def test_training_writes_artifact_and_metadata(self, toy_csv, tmp_path):
        meta = tc.train(toy_csv, model_dir=tmp_path / "m", run_cv=False)
        artifact = tmp_path / "m" / tc.ARTIFACT_FILENAME
        meta_file = tmp_path / "m" / tc.META_FILENAME
        assert artifact.is_file() and meta_file.is_file()
        on_disk = json.loads(meta_file.read_text(encoding="utf-8"))
        assert on_disk["artifact_sha256"] == tc._sha256(artifact)
        assert on_disk["sklearn_version"] == sklearn.__version__
        assert on_disk["model_version"] == meta["model_version"]
        assert on_disk["model_version"].startswith(tc.MODEL_FAMILY)

    def test_metadata_records_split_metrics_and_baseline(self, toy_csv, tmp_path):
        meta = tc.train(toy_csv, model_dir=tmp_path / "m", run_cv=False)
        split = meta["split"]
        assert split["train_rows"] + split["test_rows"] == meta["dataset"]["rows_used"]
        assert split["leakage_check"] == {"shared_groups": 0, "shared_normalised_texts": 0}
        ev = meta["evaluation"]
        for block in (ev["held_out_test"], ev["majority_class_baseline"]):
            assert set(block) >= {"accuracy", "suspicious", "legitimate", "macro_avg", "confusion_matrix"}
            cm = block["confusion_matrix"]["rows_true_cols_pred"]
            assert sum(map(sum, cm)) == split["test_rows"]
        # Majority baseline predicts one class only.
        base_cm = ev["majority_class_baseline"]["confusion_matrix"]["rows_true_cols_pred"]
        assert 0 in (base_cm[0][0] + base_cm[1][0], base_cm[0][1] + base_cm[1][1])

    def test_training_is_reproducible(self, toy_csv, tmp_path):
        m1 = tc.train(toy_csv, model_dir=tmp_path / "a", run_cv=False)
        m2 = tc.train(toy_csv, model_dir=tmp_path / "b", run_cv=False)
        assert m1["split"] == m2["split"]
        assert m1["evaluation"] == m2["evaluation"]

    def test_unknown_provenance_recorded_not_invented(self, toy_csv, tmp_path):
        meta = tc.train(toy_csv, model_dir=tmp_path / "m", run_cv=False)
        assert meta["dataset"]["provenance"]["synthetic"] is None
        assert "provenance unknown" in meta["training_data"]

    def test_grouped_cv_reported(self, toy_csv, tmp_path):
        meta = tc.train(toy_csv, model_dir=tmp_path / "m", run_cv=True)
        cv = meta["evaluation"]["supplementary_grouped_cv"]
        assert len(cv["per_fold"]) >= 2 and "mean" in cv["macro_f1"]

    def test_train_raises_without_deps(self, toy_csv, tmp_path, monkeypatch):
        monkeypatch.setattr(tc, "_ML_DEPS_OK", False)
        with pytest.raises(tc.ModelUnavailableError):
            tc.train(toy_csv, model_dir=tmp_path / "m")

    def test_cli_train_and_predict(self, toy_csv, tmp_path, capsys):
        assert tc._main(["train", "--data", str(toy_csv), "--model-dir", str(tmp_path / "m"),
                         "--no-cv"]) == 0
        assert "model_version" in json.loads(capsys.readouterr().out)
        assert tc._main(["predict", "--text", "share otp now", "--model-dir", str(tmp_path / "m")]) == 0
        assert json.loads(capsys.readouterr().out)["status"] == "analysed"

    def test_cli_train_bad_data_exit_code(self, tmp_path, capsys):
        assert tc._main(["train", "--data", str(tmp_path / "missing.csv")]) == 2
        assert "ERROR" in capsys.readouterr().err

# ---------------------------------------------------------------------------
# 4. Loading and inference (real model from disk)
# ---------------------------------------------------------------------------

class TestInference:
    def test_analysed_result_from_saved_model(self, trained_dir):
        r = tc.classify_text("please share the otp you received now", model_dir=trained_dir)
        assert r.status == ComponentStatus.ANALYSED
        assert r.predicted_class in tc.LABELS
        assert 0.0 <= r.raw_score <= 1.0
        assert r.model_version.startswith(tc.MODEL_FAMILY)
        assert r.top_terms and all(set(t) == {"term", "weight"} for t in r.top_terms)

    def test_predicted_class_consistent_with_raw_score(self, trained_dir):
        for text in ("salary credited to your account", "buy gift cards and send codes secretly"):
            r = tc.classify_text(text, model_dir=trained_dir)
            # r.raw_score is the UNROUNDED in-memory score (only to_dict() rounds it); never use the
            # serialised value here. 'suspicious' iff strictly > 0.5; an exact tie is 'legitimate'.
            assert (r.predicted_class == "suspicious") == (r.raw_score > 0.5)

    def test_predictions_match_pipeline_predict(self, trained_dir):
        loaded = tc.load_model(trained_dir)
        for text in ("invoice for october payable to usual account", "police will arrest you pay now"):
            r = tc.classify_text(text, model_dir=trained_dir)
            assert r.predicted_class == loaded.pipeline.predict([text])[0]

    def test_inference_does_not_train(self, monkeypatch):
        def _boom(*a, **k):
            raise AssertionError("train() must never be called during inference")
        monkeypatch.setattr(tc, "train", _boom)
        r = tc.classify_text("share otp")   # default dir points at an empty tmp dir
        assert r.status == ComponentStatus.UNAVAILABLE

    def test_model_is_cached(self, trained_dir):
        assert tc.load_model(trained_dir) is tc.load_model(trained_dir)

    def test_limitations_state_correlation_and_uncalibrated(self, trained_dir):
        r = tc.classify_text("tell me your otp", model_dir=trained_dir)
        joined = " ".join(r.limitations).lower()
        assert "uncalibrated" in joined
        assert "independent confirmations" in joined
        assert "verdict" in joined

    def test_json_serialisable(self, trained_dir):
        d = tc.classify_text("rent due on the fifth", model_dir=trained_dir).to_dict()
        loaded = json.loads(json.dumps(d))
        assert loaded["status"] == "analysed"
        assert set(loaded) == {"status", "predicted_class", "raw_score", "model_version",
                               "training_data", "reason", "top_terms", "limitations"}

# ---------------------------------------------------------------------------
# 5. Malformed input
# ---------------------------------------------------------------------------

class TestMalformedInput:
    @pytest.mark.parametrize("text", ["", "   ", "\n\t "])
    def test_empty_or_whitespace_not_applicable(self, text, trained_dir):
        r = tc.classify_text(text, model_dir=trained_dir)
        assert r.status == ComponentStatus.NOT_APPLICABLE
        assert r.predicted_class is None

    @pytest.mark.parametrize("bad", [None, 123, b"bytes", ["list"]])
    def test_non_string_fails_not_legitimate(self, bad, trained_dir):
        r = tc.classify_text(bad, model_dir=trained_dir)
        assert r.status == ComponentStatus.FAILED
        assert r.predicted_class is None

    def test_out_of_vocabulary_is_inconclusive(self, trained_dir):
        r = tc.classify_text("zzqx 12345 !!!", model_dir=trained_dir)
        assert r.status == ComponentStatus.INCONCLUSIVE
        assert r.predicted_class is None and r.raw_score is None
        assert "not indicate the message is legitimate" in r.reason.lower()

    def test_very_long_and_unicode_text(self, trained_dir):
        r = tc.classify_text("share otp now ₹ 😀 " * 2000, model_dir=trained_dir)
        assert r.status == ComponentStatus.ANALYSED

# ---------------------------------------------------------------------------
# 6. Missing / corrupted artifacts and dependencies
# ---------------------------------------------------------------------------

def _copy_model(src: Path, dst: Path) -> Path:
    dst.mkdir(parents=True)
    for name in (tc.ARTIFACT_FILENAME, tc.META_FILENAME):
        (dst / name).write_bytes((src / name).read_bytes())
    return dst


class TestArtifactFailures:
    def test_missing_model_dir_unavailable(self, tmp_path):
        r = tc.classify_text("share otp", model_dir=tmp_path / "empty")
        assert r.status == ComponentStatus.UNAVAILABLE
        assert r.predicted_class is None
        assert "not indicate the message is legitimate" in r.reason.lower()

    def test_missing_artifact_unavailable(self, trained_dir, tmp_path):
        d = _copy_model(trained_dir, tmp_path / "m")
        (d / tc.ARTIFACT_FILENAME).unlink()
        assert tc.classify_text("share otp", model_dir=d).status == ComponentStatus.UNAVAILABLE

    def test_corrupted_artifact_fails_on_hash(self, trained_dir, tmp_path):
        d = _copy_model(trained_dir, tmp_path / "m")
        (d / tc.ARTIFACT_FILENAME).write_bytes(b"not a pickle")
        r = tc.classify_text("share otp", model_dir=d)
        assert r.status == ComponentStatus.FAILED
        assert "SHA-256" in r.reason and r.predicted_class is None

    def test_corrupted_artifact_with_matching_hash_fails_on_load(self, trained_dir, tmp_path):
        d = _copy_model(trained_dir, tmp_path / "m")
        (d / tc.ARTIFACT_FILENAME).write_bytes(b"garbage bytes")
        meta = json.loads((d / tc.META_FILENAME).read_text(encoding="utf-8"))
        meta["artifact_sha256"] = tc._sha256(d / tc.ARTIFACT_FILENAME)
        (d / tc.META_FILENAME).write_text(json.dumps(meta), encoding="utf-8")
        r = tc.classify_text("share otp", model_dir=d)
        assert r.status == ComponentStatus.FAILED
        assert "could not be loaded" in r.reason

    def test_wrong_object_type_fails(self, trained_dir, tmp_path):
        import joblib
        d = _copy_model(trained_dir, tmp_path / "m")
        joblib.dump({"not": "a pipeline"}, d / tc.ARTIFACT_FILENAME)
        meta = json.loads((d / tc.META_FILENAME).read_text(encoding="utf-8"))
        meta["artifact_sha256"] = tc._sha256(d / tc.ARTIFACT_FILENAME)
        (d / tc.META_FILENAME).write_text(json.dumps(meta), encoding="utf-8")
        r = tc.classify_text("share otp", model_dir=d)
        assert r.status == ComponentStatus.FAILED
        assert "expected TF-IDF" in r.reason

    def test_sklearn_version_mismatch_fails(self, trained_dir, tmp_path):
        d = _copy_model(trained_dir, tmp_path / "m")
        meta = json.loads((d / tc.META_FILENAME).read_text(encoding="utf-8"))
        meta["sklearn_version"] = "0.0.1"
        (d / tc.META_FILENAME).write_text(json.dumps(meta), encoding="utf-8")
        r = tc.classify_text("share otp", model_dir=d)
        assert r.status == ComponentStatus.FAILED
        assert "scikit-learn 0.0.1" in r.reason

    def test_invalid_metadata_json_fails(self, trained_dir, tmp_path):
        d = _copy_model(trained_dir, tmp_path / "m")
        (d / tc.META_FILENAME).write_text("{not json", encoding="utf-8")
        assert tc.classify_text("share otp", model_dir=d).status == ComponentStatus.FAILED

    def test_metadata_missing_keys_fails(self, trained_dir, tmp_path):
        d = _copy_model(trained_dir, tmp_path / "m")
        (d / tc.META_FILENAME).write_text(json.dumps({"model_version": "x"}), encoding="utf-8")
        assert tc.classify_text("share otp", model_dir=d).status == ComponentStatus.FAILED

    def test_metadata_wrong_labels_fails(self, trained_dir, tmp_path):
        d = _copy_model(trained_dir, tmp_path / "m")
        meta = json.loads((d / tc.META_FILENAME).read_text(encoding="utf-8"))
        meta["labels"] = ["ham", "spam"]
        (d / tc.META_FILENAME).write_text(json.dumps(meta), encoding="utf-8")
        assert tc.classify_text("share otp", model_dir=d).status == ComponentStatus.FAILED

    def test_env_var_selects_model_dir(self, trained_dir, monkeypatch):
        monkeypatch.setenv(tc.ENV_MODEL_DIR, str(trained_dir))
        assert tc.classify_text("share otp now").status == ComponentStatus.ANALYSED

    def test_missing_deps_unavailable(self, trained_dir, monkeypatch):
        monkeypatch.setattr(tc, "_ML_DEPS_OK", False)
        monkeypatch.setattr(tc, "_ML_DEP_REASON", "Missing ML text-classifier dependencies: scikit-learn.")
        r = tc.classify_text("share otp", model_dir=trained_dir)
        assert r.status == ComponentStatus.UNAVAILABLE
        assert r.predicted_class is None
        with pytest.raises(tc.ModelUnavailableError):
            tc.load_model(trained_dir)

    def test_dependency_check_detects_missing_sklearn(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "sklearn", None)   # makes `import sklearn` raise
        ok, reason = tc._check_ml_deps()
        assert ok is False and "scikit-learn" in reason

    def test_inference_exception_fails(self, trained_dir, monkeypatch):
        loaded = tc.load_model(trained_dir)

        def _explode(*a, **k):
            raise RuntimeError("boom")
        monkeypatch.setattr(loaded.pipeline.named_steps["clf"], "predict_proba", _explode)
        r = tc.classify_text("share otp now", model_dir=trained_dir)
        assert r.status == ComponentStatus.FAILED
        assert "Inference error: boom" in r.reason

# ---------------------------------------------------------------------------
# 7. Schema
# ---------------------------------------------------------------------------

class TestSchema:
    def test_optional_fields_absent_when_not_analysed(self):
        d = TextClassifierResult(status=ComponentStatus.UNAVAILABLE, reason="x").to_dict()
        assert "predicted_class" not in d and "raw_score" not in d
        assert d["top_terms"] == [] and isinstance(d["limitations"], list)

    def test_raw_score_rounded(self):
        d = TextClassifierResult(status=ComponentStatus.ANALYSED, predicted_class="suspicious",
                                 raw_score=0.123456789).to_dict()
        assert d["raw_score"] == 0.123457

# ---------------------------------------------------------------------------
# 8. Integration with run_analysis()
# ---------------------------------------------------------------------------

class TestUnifiedIntegration:
    def test_not_applicable_without_text(self):
        r = run_analysis(case_id="C1")
        assert r["text_classifier"]["status"] == "not_applicable"

    def test_whitespace_text_not_applicable(self):
        r = run_analysis(message_text="   ")
        assert r["text_classifier"]["status"] == "not_applicable"
        assert r["context"]["status"] == "not_applicable"

    def test_analysed_with_trained_model(self, trained_dir, monkeypatch):
        monkeypatch.setenv(tc.ENV_MODEL_DIR, str(trained_dir))
        r = run_analysis(case_id="C2", message_text="Share your OTP immediately, don't tell anyone.")
        assert r["text_classifier"]["status"] == "analysed"
        assert r["text_classifier"]["predicted_class"] in tc.LABELS
        assert r["context"]["status"] == "analysed"
        assert {f["rule_id"] for f in r["context"]["flags"]} >= {"CREDENTIAL_REQUEST", "SECRECY_DEMAND"}
        json.dumps(r)

    def test_missing_model_does_not_affect_context(self):
        r = run_analysis(message_text="Install AnyDesk right now.")
        assert r["text_classifier"]["status"] == "unavailable"
        assert "predicted_class" not in r["text_classifier"]
        assert r["context"]["status"] == "analysed"
        assert r["errors"] == []

    def test_classifier_crash_isolated(self, monkeypatch):
        def _crash(text, model_dir=None):
            raise RuntimeError("classifier exploded")
        monkeypatch.setattr(tc, "classify_text", _crash)
        r = run_analysis(message_text="Pay using a gift card immediately.",
                         screenshot_path="/nonexistent/x.png")
        assert r["text_classifier"]["status"] == "failed"
        assert "predicted_class" not in r["text_classifier"]
        assert any("Text classifier" in e for e in r["errors"])
        assert r["context"]["status"] == "analysed"
        assert r["payment"]["status"] in ("failed", "unavailable")

    def test_context_crash_does_not_disable_classifier(self, trained_dir, monkeypatch):
        import satyalens_analysis.context_analyser as ca
        monkeypatch.setenv(tc.ENV_MODEL_DIR, str(trained_dir))

        def _crash(text):
            raise RuntimeError("rules exploded")
        monkeypatch.setattr(ca, "analyse_context", _crash)
        r = run_analysis(message_text="share the otp now")
        assert r["context"]["status"] == "failed"
        assert r["text_classifier"]["status"] == "analysed"
        assert any("Context component" in e for e in r["errors"])

    def test_audio_and_payment_crash_isolated(self, trained_dir, monkeypatch):
        import satyalens_analysis.audio_detector as ad
        import satyalens_analysis.payment_ocr as po
        monkeypatch.setenv(tc.ENV_MODEL_DIR, str(trained_dir))
        monkeypatch.setattr(ad, "analyse_audio", lambda p: (_ for _ in ()).throw(RuntimeError("a")))
        monkeypatch.setattr(po, "analyse_payment", lambda **k: (_ for _ in ()).throw(RuntimeError("p")))
        r = run_analysis(audio_path="x.wav", screenshot_path="y.png", message_text="share otp")
        assert r["audio"]["status"] == "failed" and r["payment"]["status"] == "failed"
        assert "finding" not in r["audio"]
        assert r["context"]["status"] == "analysed"
        assert r["text_classifier"]["status"] == "analysed"
        assert len(r["errors"]) == 2

    def test_audio_remains_unavailable(self, tmp_path, trained_dir, monkeypatch):
        monkeypatch.setenv(tc.ENV_MODEL_DIR, str(trained_dir))
        wav = tmp_path / "clip.wav"
        wav.write_bytes(b"RIFF0000WAVE")
        r = run_analysis(audio_path=str(wav), message_text="share otp")
        assert r["audio"]["status"] == "unavailable"
        assert "finding" not in r["audio"] and "raw_score" not in r["audio"]
        assert r["text_classifier"]["status"] == "analysed"

    def test_no_verdict_fields_anywhere(self, trained_dir, monkeypatch):
        monkeypatch.setenv(tc.ENV_MODEL_DIR, str(trained_dir))
        r = run_analysis(message_text="share otp now")
        blob = json.dumps(r)
        for k in ('"verdict"', '"risk"', '"risk_level"', '"decision"'):
            assert k not in blob


class TestDecisionBoundaryAsDocumented:
    """INTEGRATION_CONTRACT.md / module docstring: class = argmax on the UNROUNDED score;
    an exact 0.5 tie is 'legitimate'; to_dict() rounds raw_score to 6 decimals."""

    def test_exact_tie_is_legitimate(self, trained_dir):
        loaded = tc.load_model(trained_dir)
        clf = loaded.pipeline.named_steps["clf"]
        assert [str(c) for c in clf.classes_] == ["legitimate", "suspicious"]
        clf.coef_[:] = 0.0                      # cached in-memory copy only; the artifact is untouched
        clf.intercept_[:] = 0.0
        r = tc.classify_text("urgent verify your account otp now", model_dir=trained_dir)
        assert r.status.value == "analysed"
        assert r.raw_score == 0.5 and r.predicted_class == "legitimate"

    def test_reported_score_is_rounded_and_can_read_0_5_for_either_class(self):
        from satyalens_analysis.schema import ComponentStatus, TextClassifierResult
        above = TextClassifierResult(status=ComponentStatus.ANALYSED, predicted_class="suspicious", raw_score=0.5000004)
        below = TextClassifierResult(status=ComponentStatus.ANALYSED, predicted_class="legitimate", raw_score=0.4999996)
        assert above.to_dict()["raw_score"] == below.to_dict()["raw_score"] == 0.5

    def test_docs_state_the_tie_rule(self):
        doc = (Path(__file__).resolve().parent.parent / "INTEGRATION_CONTRACT.md").read_text(encoding="utf-8")
        assert "raw_score >= 0.5" not in doc and "an exact tie (0.5) is `\"legitimate\"`" in doc
        assert "never re-derive the" in doc and 'see README "Real-world evaluation"' not in doc
        assert "raw_score > 0.5, and an exact tie" in tc.__doc__ and "rounds\nraw_score to 6 decimals" in tc.__doc__

    def test_returned_limitations_state_the_tie_and_rounding_rule(self, trained_dir):
        r = tc.classify_text("urgent verify your account otp now", model_dir=trained_dir)
        assert r.status.value == "analysed"
        text = " ".join(r.limitations)
        assert "raw_score >= 0.5" not in text
        assert "higher-scoring class (argmax" in text
        assert "An exact 0.5 tie resolves to 'legitimate'." in text
        assert "rounded to six decimal places and must not be used to reconstruct predicted_class" in text
        # Same text on every path that returns model limitations (inconclusive included).
        inc = tc.classify_text("zzqx", model_dir=trained_dir)
        assert inc.status.value == "inconclusive" and "raw_score >= 0.5" not in " ".join(inc.limitations)
        assert "An exact 0.5 tie resolves to 'legitimate'." in " ".join(inc.limitations)
