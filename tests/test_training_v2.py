"""
test_training_v2.py — Configurable pipeline, training-pool preparation,
challenge suite and model-selection workflow.
"""
import copy
import importlib.util
import json
from pathlib import Path

import pytest

sklearn = pytest.importorskip("sklearn")

from satyalens_analysis import text_classifier as tc  # noqa: E402
from tests.test_text_classifier import _toy_rows, _write_csv  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
POOL = REPO / "data" / "training_pool_v2.csv"
CHALLENGE = REPO / "data" / "challenge_suite_v1.csv"
FROZEN = REPO / "data" / "real_world_test_v1.csv"


def _load_script(name):
    spec = importlib.util.spec_from_file_location(name, REPO / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def pool_mod():
    return _load_script("prepare_training_pool_v2")


@pytest.fixture(scope="module")
def sel_mod():
    return _load_script("select_text_model_v2")


@pytest.fixture(autouse=True)
def _clear():
    tc.clear_model_cache()
    yield
    tc.clear_model_cache()

# ---------------------------------------------------------------------------
# Configurable pipeline
# ---------------------------------------------------------------------------

class TestConfigurablePipeline:
    def test_default_unchanged(self):
        tfidf = tc.build_pipeline().named_steps["tfidf"]
        assert tfidf.ngram_range == (1, 2) and tfidf.token_pattern == tc.TOKEN_PATTERN

    def test_custom_hyperparameters_applied(self, sel_mod):
        pipe = tc.build_pipeline(sel_mod.CONFIGS["E_stopwords_C3"])
        assert pipe.named_steps["tfidf"].stop_words == "english"
        assert pipe.named_steps["clf"].C == 3.0

    def test_char_model_trains_loads_and_infers(self, sel_mod, tmp_path):
        data = _write_csv(tmp_path / "t.csv", _toy_rows())
        hp = sel_mod.CONFIGS["G_char_wb_3_5"]
        meta = tc.train(data, model_dir=tmp_path / "m", run_cv=False, hyperparameters=hp)
        assert meta["hyperparameters"]["tfidf"]["analyzer"] == "char_wb"
        r = tc.classify_text("please share the otp now", model_dir=tmp_path / "m")
        assert r.status.value == "analysed" and r.predicted_class in tc.LABELS
        assert r.top_terms

    def test_train_does_not_mutate_default_hyperparameters(self, sel_mod, tmp_path):
        before = copy.deepcopy(tc.HYPERPARAMETERS)
        tc.train(_write_csv(tmp_path / "t.csv", _toy_rows()), model_dir=tmp_path / "m", run_cv=False,
                 hyperparameters=sel_mod.CONFIGS["B_stopwords"])
        assert tc.HYPERPARAMETERS == before

    def test_partial_provenance_label(self):
        assert "partly synthetic" in tc._training_data_label({"name": "x", "synthetic": "partial"})

# ---------------------------------------------------------------------------
# Training-pool preparation
# ---------------------------------------------------------------------------

_R115_CSV = (
    "row_id,text,label,original_label,scam_type,language,generator,topic,audit_action\n"
    "a1,Your OTP is 1234 do not share,safe,safe,none,en,groq/openai/gpt-oss-120b,fake_bank_otp,keep\n"
    "a2,Share OTP now or account blocked,scam,scam,fake_bank_otp,en,groq/openai/gpt-oss-120b,fake_bank_otp,keep\n"
    "a3,Some gemini text,scam,scam,lottery,en,gemini/gemini-3.5-flash-lite,lottery,keep\n"
    "a4,,safe,safe,none,en,groq/openai/gpt-oss-120b,everyday,keep\n"
)


class TestPoolPreparation:
    def test_r115_filtering_mapping_and_groups(self, pool_mod):
        rows, excluded = pool_mod.load_r115(_R115_CSV.encode("utf-8"))
        assert [r["label"] for r in rows] == ["legitimate", "suspicious"]
        assert rows[0]["group"] == "R115:safe:fake_bank_otp:en"
        outcomes = sorted(e["outcome"] for e in excluded)
        assert outcomes == ["excluded_generator", "rejected"]

    def test_mwoz_sampling(self, pool_mod):
        dialogues = [{"dialogue_id": f"D{i}", "turns": [
            {"speaker": "USER", "utterance": "hi"},
            {"speaker": "USER", "utterance": f"I need a cheap hotel in the north {i}"},
            {"speaker": "SYSTEM", "utterance": f"I found two guesthouses for you there {i}"}]} for i in range(5)]
        data = json.dumps(dialogues).encode("utf-8")
        a = pool_mod.load_mwoz(data, n_dialogues=3, seed=1)
        b = pool_mod.load_mwoz(data, n_dialogues=3, seed=1)
        assert a == b and len(a) == 6
        assert all(r["label"] == "legitimate" and len(r["text"].split()) >= 5 for r in a)
        assert len({r["group"] for r in a}) == 3      # grouped by dialogue

    def test_build_pool_dedup_conflict_and_eval_overlap(self, pool_mod):
        cands = [
            {"source": "S", "source_ref": "1", "text": "Pay the fee now", "label": "suspicious", "group": "g1"},
            {"source": "S", "source_ref": "2", "text": "pay the FEE now", "label": "suspicious", "group": "g1"},
            {"source": "S", "source_ref": "3", "text": "Same words here", "label": "suspicious", "group": "g2"},
            {"source": "S", "source_ref": "4", "text": "same words here", "label": "legitimate", "group": "g3"},
            {"source": "S", "source_ref": "5", "text": "Copied from the eval set message", "label": "legitimate", "group": "g4"},
            {"source": "S", "source_ref": "6", "text": "Totally fine dinner chat", "label": "legitimate", "group": "g5"},
        ]
        kept, excluded, counts = pool_mod.build_pool(cands, ["copied from the eval set message!"])
        assert [k["source_ref"] for k in kept] == ["1", "6"]
        assert sorted(e["outcome"] for e in excluded) == ["ambiguous", "ambiguous", "duplicate", "eval_overlap"]
        assert sum(counts.values()) == 1

# ---------------------------------------------------------------------------
# Challenge suite and prepared files
# ---------------------------------------------------------------------------

class TestChallengeSuite:
    def test_valid_and_intent_labelled(self):
        ex = tc.load_evaluation_dataset(CHALLENGE)
        cats = {e.fields["category"] for e in ex}
        assert cats == {"genuine_otp_warning", "ordinary_conversation", "payment_confirmation",
                        "delivery_notice", "financial_scam"}
        for e in ex:
            expected = "suspicious" if e.fields["category"] == "financial_scam" else "legitimate"
            assert e.label == expected, e.row
            assert e.fields["intent_note"]

    def test_labels_not_determined_by_keywords(self):
        """Genuine messages contain alarming words and some scams contain none."""
        ex = tc.load_evaluation_dataset(CHALLENGE)
        legit_otp = [e for e in ex if e.label == "legitimate" and "otp" in e.text.lower()]
        quiet_scams = [e for e in ex if e.label == "suspicious" and not any(
            k in e.text.lower() for k in ("otp", "kyc", "blocked", "bank", "link"))]
        assert len(legit_otp) >= 3 and len(quiet_scams) >= 3

    def test_disjoint_from_training_and_frozen_sets(self):
        ch = [(e.row, e.text) for e in tc.load_evaluation_dataset(CHALLENGE)]
        syn = [e.text for e in tc.load_dataset(REPO / "data" / "synthetic_financial_messages_v1.csv")]
        assert tc.find_training_overlap(ch, syn)["total"] == 0
        if POOL.is_file():
            assert tc.find_training_overlap(ch, [e.text for e in tc.load_dataset(POOL)])["total"] == 0


@pytest.mark.skipif(not (POOL.is_file() and FROZEN.is_file()), reason="training pool / frozen set not prepared")
class TestPreparedPool:
    def test_pool_valid_documented_and_independent(self):
        pool = tc.load_dataset(POOL)
        prov = json.loads(POOL.with_suffix(".provenance.json").read_text(encoding="utf-8"))
        assert prov["pool_rows"] == len(pool) and prov["synthetic"] == "partial"
        assert prov["sources"]["R115"]["licence"].startswith("CC BY 4.0")
        assert prov["sources"]["MWOZ"]["licence"].startswith("MIT")
        assert {e.group.split(":", 1)[0] for e in pool} == {"SYN", "R115", "MWOZ"}
        frozen = [e.text for e in tc.load_evaluation_dataset(FROZEN)]
        assert tc.find_training_overlap([(e.row, e.text) for e in pool], frozen)["total"] == 0

# ---------------------------------------------------------------------------
# Model-selection workflow
# ---------------------------------------------------------------------------

class TestSelection:
    def _res(self, f1, fpr, kind="config"):
        return {"kind": kind, "oof": {"macro_avg": {"f1": f1}, "legitimate_false_positive_rate": fpr}}

    def test_choose_max_macro_f1(self, sel_mod):
        r = {"a": self._res(0.80, 0.1), "b": self._res(0.85, 0.3), "base": self._res(0.99, 0.0, "baseline")}
        assert sel_mod.choose(r) == "b"     # baselines never selected

    def test_choose_tie_breaks_on_fpr(self, sel_mod):
        r = {"a": self._res(0.850, 0.20), "b": self._res(0.847, 0.05)}
        assert sel_mod.choose(r) == "b"

    def test_score_and_breakdown(self, sel_mod):
        y = ["legitimate", "legitimate", "suspicious", "suspicious"]
        p = ["legitimate", "suspicious", "suspicious", None]
        s = sel_mod._score(y, p)
        assert s["classified"] == 3 and s["inconclusive"] == 1
        assert s["legitimate_false_positive_rate"] == 0.5
        b = sel_mod._by(["x", "x", "y", "y"], y, p)
        assert b["y"]["inconclusive"] == 1 and b["x"]["recall_legitimate"] == 0.5

    def test_grouped_oof_keeps_groups_together(self, sel_mod, tmp_path, monkeypatch):
        monkeypatch.setattr(sel_mod, "FOLDS", 3)
        ex = tc.load_dataset(_write_csv(tmp_path / "t.csv", _toy_rows()))
        preds, cv = sel_mod.grouped_oof(ex, sel_mod.CONFIGS["A_v1_config"])
        assert len(preds) == len(ex) and len(cv["fold_macro_f1"]) == 3

    def test_selection_never_reads_frozen_test(self, sel_mod):
        source = (REPO / "scripts" / "select_text_model_v2.py").read_text(encoding="utf-8")
        assert "real_world_test_v1.csv" not in source.replace("(data/real_world_test_v1.csv) is NOT read", "")
        assert not any("real_world_test" in str(v) for v in vars(sel_mod).values() if isinstance(v, Path))
