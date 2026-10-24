"""
test_protocol_v3.py — Protocol integrity, shared anonymiser, donation tool and
the sealed-test guard. No v3 data exists yet; sealing is tested with
temporary manifests.
"""
import csv
import hashlib
import importlib.util
import json
import re
from pathlib import Path

import pytest

from satyalens_analysis import text_classifier as tc
from satyalens_analysis.anonymise import ANONYMISER_VERSION, anonymise, pii_warnings

REPO = Path(__file__).resolve().parent.parent
PROTOCOL = REPO / "docs" / "protocol_v3.json"
PROTOCOL_HASH = REPO / "docs" / "protocol_v3.sha256"


def _load_script(name):
    spec = importlib.util.spec_from_file_location(name, REPO / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

# ---------------------------------------------------------------------------
# Protocol integrity
# ---------------------------------------------------------------------------

class TestProtocol:
    def test_protocol_hash_recorded_and_unchanged(self):
        recorded = PROTOCOL_HASH.read_text(encoding="utf-8").split()[0]
        assert hashlib.sha256(PROTOCOL.read_bytes()).hexdigest() == recorded

    def test_protocol_specifies_every_required_part(self):
        p = json.loads(PROTOCOL.read_text(encoding="utf-8"))
        for key in ("sources", "anonymisation", "build_order", "grouping", "test_assignment",
                    "minimum_legitimate_coverage", "model_selection", "acceptance_criteria",
                    "sealing", "withdrawal", "deviations", "commit_policy"):
            assert key in p, key
        assert p["anonymisation"]["version"] == ANONYMISER_VERSION
        assert p["test_assignment"]["salt"]
        crit = p["acceptance_criteria"]
        assert all(k in crit for k in ("S1", "S2", "L1", "L2", "M1", "B1", "verdict"))
        assert p["sources"]["IMC25"]["licence"].startswith("CC BY 4.0")
        assert "spam" in p["sources"]["IMC25"]["excluded_as_ambiguous"]
        assert set(p["sources"]["R115"]["roles"]) == {"train"} and set(p["sources"]["SYN"]["roles"]) == {"train"}

    def test_assignment_rule_is_deterministic(self):
        salt = json.loads(PROTOCOL.read_text(encoding="utf-8"))["test_assignment"]["salt"]

        def u(group_id):
            return int(hashlib.sha256(f"{salt}|{group_id}".encode()).hexdigest()[:8], 16) / 2 ** 32
        ids = [f"IMC25:{i}" for i in range(2000)]
        share = sum(u(g) < 0.20 for g in ids) / len(ids)
        assert [u(g) for g in ids[:5]] == [u(g) for g in ids[:5]]
        assert 0.17 < share < 0.23

    def test_no_script_references_the_sealed_test_file(self):
        for script in (REPO / "scripts").glob("*.py"):
            assert "frozen_test_v3.csv" not in script.read_text(encoding="utf-8"), script.name

# ---------------------------------------------------------------------------
# Shared anonymiser
# ---------------------------------------------------------------------------

class TestAnonymiser:
    def test_imc25_placeholders_and_raw_values_map_to_the_same_tokens(self):
        scam = anonymise("Update now <URL> or call <PHONE_NUMBER> before <DATE_TIME>")
        legit = anonymise("Track at https://courier.example/t/1 or call 9876543210 before 12/10/2026")
        for tok in ("urltoken", "phonetoken", "datetoken"):
            assert tok in scam and tok in legit

    def test_unreproducible_entity_placeholders_removed(self):
        out = anonymise("<NAMED_ENTITY> alert for <LOCATION> resident <NRP>")
        assert "<" not in out and "entity" not in out.lower() and out == "alert for resident"

    def test_email_upi_phone_and_ids(self):
        out = anonymise("Mail ravi.s@gmail.com, pay rahul.k@okaxis, call +91 98765 43210, ref 629104837251")
        assert "emailtoken" in out and "upitoken" in out and "phonetoken" in out
        assert out.endswith("ref numtoken")

    def test_no_long_digit_runs_remain_and_amounts_kept(self):
        out = anonymise("OTP 482913 for Rs 1,499 on card XX1234, A/c 0012345678901")
        assert not re.search(r"\d{4,}", out) and "Rs 1,499" in out

    def test_idempotent(self):
        s = "Dear user, OTP 482913 valid till 14:05 on 12-Oct-26. Visit www.bank.example. Call 1800 000 0000"
        assert anonymise(anonymise(s)) == anonymise(s)

    def test_pii_warnings(self):
        assert pii_warnings(anonymise("Rs 1,499 debited from A/c XX1234")) == []
        warns = pii_warnings("Hi Rahul, your parcel from Andheri is here")
        assert any("Rahul" in w for w in warns) and any("Andheri" in w for w in warns)
        assert any("digits" in w for w in pii_warnings("code 123 here"))

# ---------------------------------------------------------------------------
# Donation tool
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def don():
    return _load_script("donate_messages")


def _raw(path: Path, rows, header=("text", "category", "sender_type")):
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        w.writerows(rows)
    return path


_GENUINE = [
    ("482913 is your OTP for login. Do not share it. -BNB", "otp_alert", "bank"),
    ("Rs 2,000 debited from A/c XX5521 to rahul.k@okaxis on 12-Oct-26", "account_debit_credit_alert", "bank"),
    ("Hi Rahul, your parcel will arrive today", "delivery_notice", "courier"),
]


class TestDonationTool:
    def test_new_donor_id_format(self, don):
        assert re.match(r"^d[0-9a-f]{8}$", don.new_donor_id())

    def test_refuses_raw_input_inside_repo(self, don):
        with pytest.raises(don.DonationError, match="OUTSIDE the repository"):
            don.mask(REPO / "docs" / "donation" / "donation_input_template.csv", "d0123abcd")

    def test_minimisation_rejects_extra_columns(self, don, tmp_path):
        p = _raw(tmp_path / "raw.csv", [("x", "otp_alert", "bank", "9876543210")],
                 header=("text", "category", "sender_type", "sender_number"))
        with pytest.raises(don.DonationError, match="exactly the columns"):
            don.mask(p, "d0123abcd")

    def test_invalid_category_and_limit(self, don, tmp_path):
        with pytest.raises(don.DonationError, match="category"):
            don.mask(_raw(tmp_path / "a.csv", [("hello", "personal_chat", "bank")]), "d0123abcd")
        with pytest.raises(don.DonationError, match="At most"):
            don.mask(_raw(tmp_path / "b.csv", [("m", "otp_alert", "bank")] * 151), "d0123abcd")

    def test_invalid_donor_id(self, don, tmp_path):
        with pytest.raises(don.DonationError, match="donor id"):
            don.mask(_raw(tmp_path / "a.csv", _GENUINE), "rahul")

    def test_review_file_has_masked_text_only(self, don, tmp_path):
        review = don.mask(_raw(tmp_path / "raw.csv", _GENUINE), "d0123abcd")
        content = review.read_text(encoding="utf-8")
        assert "482913" not in content and "rahul.k@okaxis" not in content and "XX5521" not in content
        rows = list(csv.DictReader(review.open(encoding="utf-8")))
        assert set(rows[0]) == {"review_id", "category", "sender_type", "masked_text", "warnings", "decision", "redact"}
        assert "Rahul" in rows[2]["warnings"]

    def _review(self, don, tmp_path, decisions, redacts=("", "", "")):
        review = don.mask(_raw(tmp_path / "raw.csv", _GENUINE), "d0123abcd")
        rows = list(csv.DictReader(review.open(encoding="utf-8")))
        for r, d, x in zip(rows, decisions, redacts):
            r["decision"], r["redact"] = d, x
        with review.open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
        return review

    def test_finalize_requires_every_decision_and_consent(self, don, tmp_path):
        review = self._review(don, tmp_path, ["approve", "", "reject"])
        with pytest.raises(don.DonationError, match="Every row needs"):
            don.finalize(review, "d0123abcd", "CONSENT-v1", True, tmp_path / "don")
        review = self._review(don, tmp_path, ["approve", "approve", "reject"])
        with pytest.raises(don.DonationError, match="consent"):
            don.finalize(review, "d0123abcd", "CONSENT-v1", False, tmp_path / "don")

    def test_finalize_redaction_warnings_and_ledger(self, don, tmp_path):
        review = self._review(don, tmp_path, ["approve", "approve", "approve"], ("", "", "Rahul"))
        counts = don.finalize(review, "d0123abcd", "CONSENT-v1", True, tmp_path / "don")
        assert counts == {"approved": 3, "rejected_by_donor": 0, "rejected_unresolved_warnings": 0}
        out = list(csv.DictReader((tmp_path / "don" / "donation_d0123abcd.csv").open(encoding="utf-8")))
        assert all(r["label"] == "legitimate" and r["anonymiser_version"] == ANONYMISER_VERSION for r in out)
        assert "Rahul" not in out[2]["text"]
        ledger = list(csv.DictReader((tmp_path / "don" / "consent_ledger.csv").open(encoding="utf-8")))
        assert ledger[0]["status"] == "active" and ledger[0]["messages"] == "3"

    def test_unresolved_warnings_dropped_unless_checked(self, don, tmp_path):
        review = self._review(don, tmp_path, ["approve", "approve", "approve"])
        counts = don.finalize(review, "d0123abcd", "CONSENT-v1", True, tmp_path / "don")
        assert counts["rejected_unresolved_warnings"] == 1
        review = self._review(don, tmp_path, ["approve", "approve", "approve-checked"])
        counts = don.finalize(review, "d0123abcd", "CONSENT-v1", True, tmp_path / "don", replace=True)
        assert counts["approved"] == 3

    def test_withdraw_deletes_and_marks_ledger(self, don, tmp_path):
        review = self._review(don, tmp_path, ["approve", "approve", "reject"])
        don.finalize(review, "d0123abcd", "CONSENT-v1", True, tmp_path / "don")
        actions = don.withdraw("d0123abcd", tmp_path / "don")
        assert not (tmp_path / "don" / "donation_d0123abcd.csv").exists()
        ledger = list(csv.DictReader((tmp_path / "don" / "consent_ledger.csv").open(encoding="utf-8")))
        assert ledger[0]["status"] == "withdrawn" and ledger[0]["messages"] == "0"
        assert any("rebuild" in a for a in actions)

# ---------------------------------------------------------------------------
# Sealed-test guard
# ---------------------------------------------------------------------------

sklearn = pytest.importorskip("sklearn")
from tests.test_text_classifier import _toy_rows, _write_csv  # noqa: E402


_SEALED_ROWS = [("quickly tell me the otp code you received", "suspicious"),
                ("your monthly pay has arrived in savings", "legitimate"),
                ("purchase gift cards urgently and share codes", "suspicious"),
                ("thank you for paying the electricity invoice", "legitimate")]
_OTHER_ROWS = [("forward the otp to me right away", "suspicious"),
               ("dinner is at eight, see you there", "legitimate")]


def _eval_csv(path: Path, rows=_SEALED_ROWS) -> Path:
    """Rows are disjoint from the toy training data, so the overlap check passes."""
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["text", "label"])
        w.writerows(rows)
    return path


@pytest.fixture()
def sealed(tmp_path, monkeypatch):
    """A temporary project layout with a sealed test file and manifest."""
    (tmp_path / "data").mkdir()
    (tmp_path / "reports").mkdir()
    (tmp_path / "docs").mkdir()
    test_file = _eval_csv(tmp_path / "data" / "sealed_test.csv")
    protocol = tmp_path / "docs" / "protocol.json"
    protocol.write_text('{"p": 1}', encoding="utf-8")
    manifest = tmp_path / "reports" / "manifest.json"
    manifest.write_text(json.dumps({
        "sealed_files": [{"path": "data/sealed_test.csv", "sha256": tc._sha256(test_file)}],
        "protocol_file": "docs/protocol.json", "protocol_sha256": tc._sha256(protocol),
        "evaluation_log": "reports/evaluations.jsonl"}), encoding="utf-8")
    monkeypatch.setenv(tc.SEALED_MANIFEST_ENV, str(manifest))
    tc.clear_model_cache()
    yield {"test": test_file, "protocol": protocol, "log": tmp_path / "reports" / "evaluations.jsonl",
           "root": tmp_path}
    tc.clear_model_cache()


@pytest.fixture()
def toy_model(tmp_path):
    data = _write_csv(tmp_path / "train.csv", _toy_rows())
    tc.train(data, model_dir=tmp_path / "m", run_cv=False)
    return {"model": tmp_path / "m" / tc.ARTIFACT_FILENAME, "train": data}


class TestSealedGuard:
    def test_no_manifest_means_no_restriction(self, tmp_path, monkeypatch):
        monkeypatch.setenv(tc.SEALED_MANIFEST_ENV, str(tmp_path / "absent.json"))
        assert len(tc.load_evaluation_dataset(_eval_csv(tmp_path / "e.csv"))) == len(_SEALED_ROWS)

    def test_loaders_refuse_sealed_file(self, sealed):
        with pytest.raises(tc.SealedDataError):
            tc.load_evaluation_dataset(sealed["test"])
        with pytest.raises(tc.SealedDataError):
            tc.load_dataset(sealed["test"])

    def test_loaders_refuse_byte_identical_copy(self, sealed, tmp_path):
        copy = tmp_path / "innocent_name.csv"
        copy.write_bytes(sealed["test"].read_bytes())
        with pytest.raises(tc.SealedDataError):
            tc.load_evaluation_dataset(copy)

    def test_selection_script_cannot_read_sealed_file(self, sealed, monkeypatch):
        sel = _load_script("select_text_model_v2")
        monkeypatch.setattr(sel, "POOL", sealed["test"])
        with pytest.raises(tc.SealedDataError):
            tc.load_dataset(sel.POOL)
        with pytest.raises(tc.SealedDataError):
            sel.main.__globals__["tc"].load_evaluation_dataset(sealed["test"])

    def test_plain_evaluate_refused(self, sealed, toy_model):
        with pytest.raises(tc.SealedDataError):
            tc.evaluate(sealed["test"], toy_model["model"], training_data=toy_model["train"],
                        out_dir=sealed["root"] / "out")

    def test_final_sealed_evaluation_runs_exactly_once(self, sealed, toy_model):
        r = tc.evaluate(sealed["test"], toy_model["model"], training_data=toy_model["train"],
                        out_dir=sealed["root"] / "out", final_sealed=True)
        assert r["final_sealed_evaluation"] is True
        entry = json.loads(sealed["log"].read_text(encoding="utf-8").strip())
        assert entry["test_sha256"] == tc._sha256(sealed["test"])
        with pytest.raises(tc.EvaluationError, match="already been evaluated once"):
            tc.evaluate(sealed["test"], toy_model["model"], training_data=toy_model["train"],
                        out_dir=sealed["root"] / "out2", final_sealed=True)

    def test_final_refused_if_protocol_changed(self, sealed, toy_model):
        sealed["protocol"].write_text('{"p": 2}', encoding="utf-8")
        with pytest.raises(tc.EvaluationError, match="Protocol file"):
            tc.evaluate(sealed["test"], toy_model["model"], out_dir=sealed["root"] / "o", final_sealed=True)

    def test_final_refused_if_test_changed(self, sealed, toy_model):
        sealed["test"].write_text(sealed["test"].read_text(encoding="utf-8") + "extra row,legitimate\n",
                                  encoding="utf-8")
        with pytest.raises(tc.EvaluationError, match="changed since sealing"):
            tc.evaluate(sealed["test"], toy_model["model"], out_dir=sealed["root"] / "o", final_sealed=True)

    def test_final_flag_requires_a_sealed_file(self, sealed, toy_model, tmp_path):
        with pytest.raises(tc.EvaluationError, match="requires a file listed"):
            tc.evaluate(_eval_csv(tmp_path / "other.csv", _OTHER_ROWS), toy_model["model"],
                        out_dir=tmp_path / "o", final_sealed=True)
