"""
test_dev_synthetic_dataset.py — data/dev/synthetic_dev_*_v1 (dev-synth-v1).

Integrity (reproducible, validated, placeholders only), strict separation from
Protocol v5 (never a donated genuine alert, never counted toward donation
coverage, never test-eligible, never a protocol source), and pipeline
integration on every row. Temporary layouts and a toy model only.
"""
import csv
import hashlib
import importlib.util
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from satyalens_analysis import text_classifier as tc
from tests.test_text_classifier import _toy_rows, _write_csv

REPO = Path(__file__).resolve().parent.parent
DEV = REPO / "data" / "dev"
LABELLED = DEV / "synthetic_dev_messages_v1.csv"
EDGE = DEV / "synthetic_dev_edge_cases_v1.csv"
PROV = DEV / "synthetic_dev_messages_v1.provenance.json"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, REPO / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _rows(path):
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


@pytest.fixture(scope="module")
def gen():
    return _load("generate_dev_synthetic_messages")


# ---------------------------------------------------------------------------
# Integrity
# ---------------------------------------------------------------------------

class TestIntegrity:
    def test_files_are_exactly_reproducible(self, gen):
        lab, edg, prov = gen.build_bytes()
        assert LABELLED.read_bytes() == lab and EDGE.read_bytes() == edg and PROV.read_bytes() == prov

    def test_provenance_records_hashes_counts_and_limits(self):
        p = json.loads(PROV.read_text(encoding="utf-8"))
        assert p["files"][LABELLED.name] == {"rows": 168, "sha256": _sha(LABELLED)}
        assert p["files"][EDGE.name] == {"rows": 17, "sha256": _sha(EDGE)}
        assert p["synthetic"] is True and p["provenance"] == "synthetic" and p["source_marker"] == "SYN_DEV"
        assert p["generator_version"] == "dev-synth-v1" and p["generator_seed"] == 20261101
        assert p["label_counts"] == {"legitimate": 78, "suspicious": 90}
        assert any("does not represent the distribution of indian financial alerts" in s.lower()
                   for s in p["limitations"])
        assert any("synthetic-only" in s for s in p["limitations"])

    def test_every_row_carries_markers_and_valid_fields(self, gen):
        rows, edge = _rows(LABELLED), _rows(EDGE)
        assert list(rows[0]) == gen.LABELLED_FIELDS and list(edge[0]) == gen.EDGE_FIELDS
        for r in rows + edge:
            assert (r["source"], r["provenance"], r["generator"], r["generator_version"]) == (
                "SYN_DEV", "synthetic", "scripts/generate_dev_synthetic_messages.py", "dev-synth-v1")
            assert r["id"].startswith("DEV1-")
        assert len({r["id"] for r in rows + edge}) == len(rows) + len(edge)
        assert {r["label"] for r in rows} == set(tc.LABELS)
        assert all(gen.CATEGORY_LABEL[r["category"]] == r["label"] for r in rows)
        assert {r["variation"] for r in rows} == set(gen.VARIATIONS)
        assert {r["language"] for r in rows} == {"en", "hinglish"}

    def test_validator_passes_on_the_files(self, gen):
        report = gen.validate(_rows(LABELLED), _rows(EDGE))
        assert report["duplicates"] == 0 and report["missing_values"] == 0 and report["placeholders_only"]

    @pytest.mark.parametrize("mutate, msg", [
        (lambda r: r.update(text="Pay now at realbank-login.com/verify"), "non-reserved domain"),
        (lambda r: r.update(text="Send money to someone@okaxis today"), "non-placeholder handle"),
        (lambda r: r.update(text="Your code is 482910"), "4\\+ digits"),
        (lambda r: r.update(provenance="real"), "marker wrong"),
        (lambda r: r.update(source="DONATION"), "marker wrong"),
        (lambda r: r.update(label="inconclusive"), "not in"),
        (lambda r: r.update(category="everyday_message", label="suspicious"), "inconsistent"),
        (lambda r: r.update(group=""), "missing"),
    ])
    def test_validator_rejects_bad_rows(self, gen, mutate, msg):
        rows, edge = _rows(LABELLED), _rows(EDGE)
        mutate(rows[0])
        with pytest.raises(ValueError, match=msg):
            gen.validate(rows, edge)

    def test_validator_rejects_duplicates_and_labels_on_edge_rows(self, gen):
        rows, edge = _rows(LABELLED), _rows(EDGE)
        dup = dict(rows[1], id="DEV1-X-99", text=rows[0]["text"].upper())     # same after normalisation
        with pytest.raises(ValueError, match="duplicate messages"):
            gen.validate(rows + [dup], edge)
        with pytest.raises(ValueError, match="must not carry a label"):
            gen.validate(rows, [dict(edge[0], label="suspicious")])

    def test_labelled_file_loads_and_edge_file_is_refused_by_loaders(self):
        assert len(tc.load_dataset(LABELLED)) == 168
        assert len(tc.load_evaluation_dataset(LABELLED)) == 168
        with pytest.raises(tc.DatasetValidationError):
            tc.load_dataset(EDGE)                     # no label/group column: cannot be trained on
        with pytest.raises(tc.EvaluationError):
            tc.load_evaluation_dataset(EDGE)          # no label column: cannot be scored

    def test_no_overlap_with_existing_and_protected_sets(self):
        texts = [(i, r["text"]) for i, r in enumerate(_rows(LABELLED) + _rows(EDGE)) if r["text"].strip()]
        for rel in ("data/synthetic_financial_messages_v1.csv", "data/challenge_suite_v1.csv",
                    "data/real_world_test_v1.csv"):
            if not (REPO / rel).is_file():
                continue                              # retired benchmark is gitignored
            other = [e.text for e in tc.load_evaluation_dataset(REPO / rel)]
            assert tc.find_training_overlap(texts, other)["total"] == 0, rel

    def test_generator_writes_only_its_three_files(self, gen, tmp_path):
        assert gen.main(["--out", str(tmp_path / "out")]) == 0
        assert sorted(p.name for p in (tmp_path / "out").iterdir()) == sorted(
            [LABELLED.name, EDGE.name, PROV.name])
        assert gen.main(["--check"]) == 0             # files on disk equal a fresh in-memory build


# ---------------------------------------------------------------------------
# Separation from Protocol v5 (requirement 8)
# ---------------------------------------------------------------------------

class TestSeparationFromProtocolV5:
    def test_not_a_protocol_v5_source(self):
        p = json.loads((REPO / "docs" / "protocol_v5.json").read_text(encoding="utf-8"))
        pinned = {s.get("file_sha256") for s in p["sources"].values() if isinstance(s, dict)}
        assert not {_sha(LABELLED), _sha(EDGE)} & pinned
        text = (REPO / "docs" / "protocol_v5.json").read_text(encoding="utf-8")
        assert "data/dev" not in text and "synthetic_dev" not in text and "SYN_DEV" not in text
        assert _load("build_pool_and_test_v3").SYN_CSV.name == "synthetic_financial_messages_v1.csv"

    def test_build_selection_and_acceptance_never_reference_dev_data(self):
        for name in ("build_pool_and_test_v3", "build_pool_and_test_v4", "build_pool_and_test_v5",
                     "select_text_model_v5", "acceptance_v5", "donation_progress", "donate_messages"):
            src = (REPO / "scripts" / f"{name}.py").read_text(encoding="utf-8")
            for s in ("data/dev", "synthetic_dev", "SYN_DEV", "generate_dev_synthetic"):
                assert s not in src, (name, s)

    def test_cannot_pass_as_donated_genuine_alerts(self, tmp_path):
        dp = _load("donation_progress")
        don = tmp_path / "donations"
        don.mkdir()
        with (don / "consent_ledger.csv").open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=["donor_id", "consent_version", "recorded_on", "messages",
                                               "status", "withdrawn_on"])
            w.writeheader()
            w.writerow({"donor_id": "dev", "consent_version": "CONSENT-v1", "recorded_on": "2026-10-24",
                        "messages": "1", "status": "active", "withdrawn_on": ""})
        # 1) the dev file as-is, even with an "active" ledger entry: wrong columns -> rejected
        shutil.copy(LABELLED, don / "donation_dev.csv")
        verified, issues = dp.validate_donations(don)
        assert verified == [] and any("unexpected columns" in i["reason"] for i in issues)
        # 2) legitimate dev rows re-shaped into the donation format keep their SYN_DEV source -> rejected
        legit = [r for r in _rows(LABELLED) if r["label"] == "legitimate"][:5]
        with (don / "donation_dev.csv").open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=dp.FIELDS)
            w.writeheader()
            for i, r in enumerate(legit):
                w.writerow({"id": f"DON-dev-{i:04d}", "text": r["text"], "label": "legitimate",
                            "category": "otp_alert", "sender_type": "bank", "donor_id": "dev",
                            "source": r["source"], "anonymiser_version": dp.ANONYMISER_VERSION,
                            "consent_version": "CONSENT-v1"})
        verified, issues = dp.validate_donations(don)
        assert verified == [] and all(i["reason"] == "label/source not legitimate/DONATION" for i in issues)
        # 3) the real donation loader never looks in data/dev
        assert dp.DEFAULT_DONATIONS_DIR.resolve() != DEV.resolve()
        assert DEV.resolve() not in dp.DEFAULT_DONATIONS_DIR.resolve().parents

    def test_cannot_count_toward_donation_coverage(self):
        p = json.loads((REPO / "docs" / "protocol_v5.json").read_text(encoding="utf-8"))
        v3, acc = _load("build_pool_and_test_v3"), _load("acceptance_v5")
        # 504 rows, 9 pseudo-donors, 3 categories: would meet every count threshold if they were DONATION rows
        many = [dict(r, donor=f"D{i % 9}", category=("otp_alert", "delivery_notice", "bank_service_notice")[i % 3])
                for i, r in enumerate(_rows(LABELLED) * 3)]
        assert len(many) >= 150
        cov = v3.coverage(p, [dict(r, india_linked=False) for r in many])
        assert cov["test_donated_messages"] == 0 and cov["met"] is False and cov["L1_status"] == "NOT EVALUABLE"
        assert acc.coverage_from_rows([dict(r, label="legitimate") for r in many])["met"] is False

    def test_cannot_silently_enter_the_frozen_test(self):
        v4 = _load("build_pool_and_test_v4")
        assert "SYN_DEV" not in v4.TEST_ELIGIBLE and "SYN_DEV" not in v4.PRIORITY
        rows = [{"source": "SYN_DEV", "group": f"SYN_DEV:{r['group']}", "label": r["label"]}
                for r in _rows(LABELLED)]
        v4.assign(rows, "satyalens-protocol-v4-2026-10-24", 0.20)
        assert {r["split"] for r in rows} == {"train"}                     # never test, under any salt
        v4.assign(rows, "any-other-salt", 0.99)
        assert {r["split"] for r in rows} == {"train"}

    def test_not_sealed_and_not_a_sealing_path(self):
        p = json.loads((REPO / "docs" / "protocol_v5.json").read_text(encoding="utf-8"))
        for f in (LABELLED, EDGE):
            assert not tc.is_sealed(f)
            assert f.relative_to(REPO).as_posix() != p["sealing"]["test_file"].split(" ")[0]

    @pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
    def test_committed_byte_for_byte_and_not_ignored(self):
        for f in (LABELLED, EDGE, PROV):
            rel = f.relative_to(REPO).as_posix()
            attr = subprocess.run(["git", "check-attr", "text", "--", rel], cwd=REPO, capture_output=True, text=True)
            assert attr.stdout.strip().endswith("text: unset"), rel
            ign = subprocess.run(["git", "check-ignore", "-q", "--no-index", rel], cwd=REPO)
            assert ign.returncode == 1, rel


# ---------------------------------------------------------------------------
# Pipeline integration (toy model trained in a temp dir; deployed artifact untouched)
# ---------------------------------------------------------------------------

@pytest.fixture()
def toy_model_dir(tmp_path, monkeypatch):
    data = _write_csv(tmp_path / "toy.csv", _toy_rows())
    tc.train(data, model_dir=tmp_path / "m", run_cv=False)
    monkeypatch.setenv(tc.ENV_MODEL_DIR, str(tmp_path / "m"))
    tc.clear_model_cache()
    yield tmp_path / "m"
    tc.clear_model_cache()


class TestPipelineIntegration:
    def test_classifier_on_every_labelled_row(self, toy_model_dir):
        for r in _rows(LABELLED):
            res = tc.classify_text(r["text"], model_dir=toy_model_dir)
            assert res.status.value in ("analysed", "inconclusive"), r["id"]
            if res.status.value == "analysed":
                assert res.predicted_class in tc.LABELS and 0.0 <= res.raw_score <= 1.0
            else:
                assert res.predicted_class is None and res.raw_score is None

    def test_edge_case_expected_statuses(self, toy_model_dir):
        for r in _rows(EDGE):
            res = tc.classify_text(r["text"], model_dir=toy_model_dir)
            expected = r["expected_text_classifier_status"]
            if expected in ("inconclusive", "not_applicable"):
                assert res.status.value == expected, (r["id"], res.status.value)
            else:                                     # ambiguous: any model answer, never scored
                assert res.status.value in ("analysed", "inconclusive"), r["id"]

    def test_run_analysis_contract_on_all_rows(self, toy_model_dir):
        from satyalens_analysis.unified import run_analysis
        for r in _rows(LABELLED)[::7] + _rows(EDGE):
            out = run_analysis(message_text=r["text"])
            assert set(out) == {"schema_version", "case_id", "audio", "payment", "context",
                                "text_classifier", "errors"}, r["id"]
            assert out["errors"] == [], r["id"]
            t = out["text_classifier"]
            assert ("predicted_class" in t) == (t["status"] == "analysed"), r["id"]
            if not r["text"].strip():
                assert t["status"] == "not_applicable" and out["context"]["status"] == "not_applicable"

    def test_rule_engine_on_every_row(self):
        from satyalens_analysis.context_analyser import analyse_context
        for r in _rows(LABELLED) + _rows(EDGE):
            res = analyse_context(r["text"]) if r["text"].strip() else None
            if res is not None:
                assert res.status.value in ("analysed", "not_applicable"), r["id"]
