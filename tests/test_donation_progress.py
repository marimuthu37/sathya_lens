"""
test_donation_progress.py — Validation and privacy-safe progress reporting of
donated genuine messages. Uses the real donate_messages workflow in tmp dirs.
"""
import csv
import importlib.util
import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent


def _load(name):
    spec = importlib.util.spec_from_file_location(name, REPO / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def don():
    return _load("donate_messages")


@pytest.fixture(scope="module")
def prog():
    return _load("donation_progress")


_MSGS = [
    ("482913 is your OTP for login. Do not share it.", "otp_alert", "bank"),
    ("Rs 2,000 debited from A/c XX5521 on 12-Oct-26. Not you? Call 1800 000 0000", "account_debit_credit_alert", "bank"),
    ("Your order has been shipped and arrives Thursday", "delivery_notice", "courier"),
]


def _donate(don, tmp_path: Path, donor: str, donations: Path, msgs=_MSGS):
    raw = tmp_path / f"{donor}_raw.csv"
    with raw.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["text", "category", "sender_type"])
        w.writerows(msgs)
    review = don.mask(raw, donor)
    rows = list(csv.DictReader(review.open(encoding="utf-8")))
    for r in rows:
        r["decision"] = "approve-checked"
    with review.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    don.finalize(review, donor, "CONSENT-v1", True, donations)


def _rewrite(path: Path, mutate):
    rows = list(csv.DictReader(path.open(encoding="utf-8")))
    fields = list(rows[0])
    mutate(rows)
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


class TestValidation:
    def test_empty_when_no_donations(self, prog, tmp_path):
        rep = prog.progress(tmp_path / "none")
        assert rep["verified_messages"] == 0 and rep["donors_with_verified_messages"] == 0
        assert rep["collection_target"]["met"] is False

    def test_workflow_records_verified(self, don, prog, tmp_path):
        d = tmp_path / "don"
        _donate(don, tmp_path, "d0000000a", d)
        _donate(don, tmp_path, "d0000000b", d)
        verified, issues = prog.validate_donations(d)
        assert len(verified) == 6 and issues == []

    @pytest.mark.parametrize("mutate,reason", [
        (lambda rows: rows[0].update(text="your otp is 482913"), "text not in masked form"),
        (lambda rows: rows[0].update(anonymiser_version="anon-v0"), "anonymiser version mismatch"),
        (lambda rows: rows[0].update(category="personal_chat"), "invalid category"),
        (lambda rows: rows[0].update(label="suspicious"), "label/source"),
        (lambda rows: rows[0].update(id="X-1"), "id not in finalize format"),
        (lambda rows: rows[0].update(consent_version="none"), "consent version invalid"),
    ])
    def test_tampered_records_not_counted(self, don, prog, tmp_path, mutate, reason):
        d = tmp_path / "don"
        _donate(don, tmp_path, "d0000000a", d)
        _rewrite(d / "donation_d0000000a.csv", mutate)
        verified, issues = prog.validate_donations(d)
        assert len(verified) == 2
        assert any(reason in i["reason"] for i in issues)

    def test_withdrawn_or_unconsented_donor_not_counted(self, don, prog, tmp_path):
        d = tmp_path / "don"
        _donate(don, tmp_path, "d0000000a", d)
        (d / "donation_d0000000c.csv").write_bytes((d / "donation_d0000000a.csv").read_bytes())  # no ledger entry
        verified, issues = prog.validate_donations(d)
        assert len(verified) == 3 and any("ledger" in i["reason"] for i in issues)
        don.withdraw("d0000000a", d)
        assert prog.validate_donations(d)[0] == []

    def test_manual_file_with_wrong_columns_rejected(self, don, prog, tmp_path):
        d = tmp_path / "don"
        _donate(don, tmp_path, "d0000000a", d)
        p = d / "donation_d0000000a.csv"
        p.write_text("text,label\nsome typed message,legitimate\n", encoding="utf-8")
        verified, issues = prog.validate_donations(d)
        assert verified == [] and "unexpected columns" in issues[0]["reason"]


class TestProgressReport:
    def test_counts_by_donor_and_category_without_text(self, don, prog, tmp_path):
        d = tmp_path / "don"
        _donate(don, tmp_path, "d0000000a", d)
        _donate(don, tmp_path, "d0000000b", d, _MSGS[:1])
        rep = prog.progress(d)
        assert rep["verified_messages"] == 4 and rep["donors_with_verified_messages"] == 2
        assert rep["by_donor"]["d0000000a"] == {"total": 3, "by_category": {
            "otp_alert": 1, "account_debit_credit_alert": 1, "delivery_notice": 1}}
        assert rep["by_category"]["otp_alert"] == 2
        assert rep["duplicate_texts_after_normalisation"] == 1
        blob = json.dumps(rep)
        assert "numtoken is your OTP" not in blob and "shipped" not in blob

    def test_coverage_check_skipped_until_target_met(self, don, prog, tmp_path, monkeypatch):
        monkeypatch.setattr(prog, "REPORT", tmp_path / "progress.json")
        d = tmp_path / "don"
        _donate(don, tmp_path, "d0000000a", d)
        prog.main(["--donations-dir", str(d), "--check-test-coverage"])
        rep = json.loads((tmp_path / "progress.json").read_text(encoding="utf-8"))
        assert rep["test_coverage"].startswith("not checked")
        assert rep["ready_to_request_sealing_confirmation"] is False

    def test_target_logic(self, prog):
        assert prog.TARGET_MESSAGES == 750 and prog.TARGET_DONORS == 10


class TestBuilderUsesValidator:
    def test_builder_loads_only_verified_donations(self, don, tmp_path, monkeypatch):
        b = _load("build_pool_and_test_v3")
        d = tmp_path / "don"
        _donate(don, tmp_path, "d0000000a", d)
        _rewrite(d / "donation_d0000000a.csv", lambda rows: rows[0].update(text="otp 482913"))
        monkeypatch.setattr(b, "DONATIONS", d)
        excluded = []
        rows = b.load_donations(excluded)
        assert len(rows) == 2 and excluded[0]["outcome"] == "unverified_donation"
