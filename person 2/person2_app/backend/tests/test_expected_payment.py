"""
tests/test_expected_payment.py
──────────────────────────────
User-supplied expected payment details, image-quality gating and the honesty
of the resulting decision (payment_checks.py, analysis route, decision engine).

Scenarios (see the task brief):
  A. legitimate-style payment, all expectations match
  B. expected amount mismatch (€1,000.00 vs 100.00)
  C. expected recipient mismatch / partial / formatting-only difference
  D. generic placeholder recipient
  E. reference discrepancy (TID-241-- vs TID-241)
  F. matching reference (TID-12345)
  G. degraded image → comparisons withheld, limited-analysis state
  H. empty / failed OCR → honest status, no fabricated fields
  I. missing expected values → no comparison claimed
  J. no findings → never "genuine", never LOW_CONCERN
  K. API validation, upload flow, response shape, backwards compatibility

All values are synthetic; no real financial data is used.
"""

from __future__ import annotations

import copy
import io
from decimal import Decimal

import pytest

from app.services.analysis_person1 import Person1Adapter
from app.services.decision import EvidenceInput, compute_decision
from app.services.payment_checks import (
    CORRELATION_GROUP,
    evaluate_payment,
    normalise_amount,
    normalise_currency,
)

from tests.test_analysis_person1 import _BASE_RESULT


# ── Synthetic payment objects ─────────────────────────────────────────────────

def _field(name, value, conf="high"):
    return {"field_name": name, "extracted_value": value, "expected_value": None,
            "match": None, "confidence": conf}


_GOOD_QUALITY = {"assessment": "no_issues_detected", "metrics": {}, "warnings": [], "note": "n"}


def _payment(fields, raw_text="Payment confirmation\nsynthetic receipt text here", quality=_GOOD_QUALITY):
    p = {"status": "analysed", "raw_text": raw_text, "fields": list(fields),
         "mismatches": [], "limitations": ["OCR may produce errors."]}
    if quality is not None:
        p["quality"] = copy.deepcopy(quality)
    return p


def _expected(amount=None, currency=None, recipient=None, reference=None):
    return {"amount": amount, "currency": currency, "recipient": recipient, "reference": reference}


def _cmp(summary, field):
    return next(c for c in summary["comparisons"] if c["field"] == field)


LEGIT = [_field("amount", "€25.00"), _field("payee_name", "Amazon Europe"),
         _field("transaction_id", "TID-7823419"), _field("description", "Order #7823419")]
HIGH_VALUE = [_field("amount", "€1,000.00"), _field("payee_name", "Flipkart India Pvt Ltd"),
              _field("transaction_id", "TID-445677"), _field("description", "Order #445677")]
GENERIC = [_field("amount", "€5.00"), _field("payee_name", "Merchant Name"),
           _field("transaction_id", "TID-12345"), _field("description", "TID-12345")]
INCONSISTENT = [_field("amount", "€1.00"), _field("payee_name", "Merchant Name"),
                _field("transaction_id", "TID-241--"), _field("description", "TID-241")]

_CLAIM_WORDS = ("verified", "settled", "genuine", "safe", "confirmed fraud", "is fraud")
_NEGATIONS = (" not ", "cannot", "n't", " no ", "never")


def _asserts_claim(text: str) -> bool:
    """True if any sentence mentions verification/settlement/genuineness without negating it."""
    import re
    for sentence in re.split(r"(?<=[.;!?])\s+", text):
        s = f" {sentence} "
        if any(w in s for w in _CLAIM_WORDS) and not any(n in s for n in _NEGATIONS):
            return True
    return False


def _decision(payment, expected=None, status="completed"):
    findings, _, summary = evaluate_payment(payment, expected)
    from app.services.analysis import findings_from_json, findings_to_json
    ev = EvidenceInput("e1", "shot.jpg", "image/jpeg", status,
                       findings_from_json(findings_to_json(findings)), payment=summary)
    return compute_decision([ev])


def _text(decision) -> str:
    return " ".join([decision.short_explanation, *decision.factors_of_concern,
                     *decision.factors_unknown, *decision.limitations, *decision.next_steps]).lower()


# ── A. Legitimate-style payment ───────────────────────────────────────────────

class TestA_Legitimate:

    def test_all_expectations_match_without_findings(self):
        exp = _expected("25.00", "EUR", "Amazon Europe", "TID-7823419")
        findings, notes, summary = evaluate_payment(_payment(LEGIT), exp)
        assert findings == []
        assert [c["status"] for c in summary["comparisons"]] == ["match"] * 4
        assert all(c["ran"] for c in summary["comparisons"])
        assert any("does NOT prove payment settlement" in n for n in notes)

    def test_decision_never_claims_verified_or_settled(self):
        d = _decision(_payment(LEGIT), _expected("25", "EUR", "Amazon Europe", "TID-7823419"))
        assert d.category.value == "insufficient_evidence"
        text = _text(d)
        assert not _asserts_claim(text)
        assert "cannot confirm that a payment was made, received or settled" in text

    def test_settlement_status_is_never_determinable(self):
        _, _, summary = evaluate_payment(_payment(LEGIT), _expected("25.00", "EUR"))
        assert summary["settlement_status"] == "not_determinable"


# ── B. Expected amount mismatch ───────────────────────────────────────────────

class TestB_AmountMismatch:

    def test_explicit_amount_mismatch(self):
        findings, _, summary = evaluate_payment(_payment(HIGH_VALUE), _expected("100.00", "EUR"))
        amount = _cmp(summary, "amount")
        assert amount["status"] == "mismatch" and amount["ran"] is True
        assert amount["expected"] == "100.00" and amount["extracted"] == "€1,000.00"
        assert "read as 1000.00" in amount["detail"]
        assert _cmp(summary, "currency")["status"] == "match"
        assert [f.label for f in findings] == ["Amount differs from the amount you expected"]
        f = findings[0]
        assert f.finding_type == "payment_mismatch" and f.is_interpretation is False
        assert "not proof of fraud" in f.detail

    def test_review_recommended_not_high_risk(self):
        d = _decision(_payment(HIGH_VALUE), _expected("100.00", "EUR"))
        assert d.category.value == "verify_first"
        assert any("own bank or payment app" in s for s in d.next_steps)

    def test_high_amount_alone_is_not_a_finding(self):
        fields = [_field("amount", "€1,000,000.00"), _field("payee_name", "Riya Demo Stores"),
                  _field("transaction_id", "TID-9")]
        assert evaluate_payment(_payment(fields))[0] == []
        assert evaluate_payment(_payment(fields), _expected("1000000"))[0] == []

    @pytest.mark.parametrize("expected, extracted", [
        ("100", "€100.00"), ("100.5", "€100.50"), ("1000.00", "€1,000.00"), ("0.30", "€0.30"),
    ])
    def test_decimal_equality(self, expected, extracted):
        _, _, s = evaluate_payment(_payment([_field("amount", extracted)]),
                                   _expected(format(normalise_amount(expected), "f")))
        assert _cmp(s, "amount")["status"] == "match"

    def test_currency_mismatch_is_separate_from_amount(self):
        findings, _, s = evaluate_payment(_payment([_field("amount", "£25.00")]), _expected("25.00", "EUR"))
        assert _cmp(s, "amount")["status"] == "match"
        assert _cmp(s, "currency")["status"] == "mismatch"
        assert [f.label for f in findings] == ["Currency differs from the currency you expected"]


# ── C. Expected recipient mismatch ────────────────────────────────────────────

class TestC_Recipient:

    def test_different_recipient_is_mismatch(self):
        findings, _, s = evaluate_payment(_payment(HIGH_VALUE), _expected(recipient="Amazon Europe"))
        assert _cmp(s, "recipient")["status"] == "mismatch"
        assert [f.label for f in findings] == ["Recipient differs from the recipient you expected"]
        assert d_category(HIGH_VALUE, _expected(recipient="Amazon Europe")) == "verify_first"

    def test_formatting_only_difference_is_not_a_finding(self):
        fields = [_field("payee_name", "Amazon Europe S.à r.l.")]
        findings, notes, s = evaluate_payment(_payment(fields), _expected(recipient="amazon europe s.a r.l"))
        assert _cmp(s, "recipient")["status"] == "format_difference"
        assert findings == []
        assert any("format difference" in n for n in notes)

    def test_partial_match_is_interpretation_for_review(self):
        findings, _, s = evaluate_payment(_payment(LEGIT), _expected(recipient="Amazon"))
        assert _cmp(s, "recipient")["status"] == "partial_match"
        assert len(findings) == 1 and findings[0].is_interpretation is True
        assert "partly matches" in findings[0].label


def d_category(fields, expected):
    return _decision(_payment(fields), expected).category.value


# ── D. Generic recipient ──────────────────────────────────────────────────────

class TestD_GenericRecipient:

    def test_placeholder_observation_not_confirmed_fraud(self):
        findings, _, _ = evaluate_payment(_payment(GENERIC))
        assert [f.label for f in findings] == ["Recipient name looks like a template placeholder"]
        f = findings[0]
        assert f.finding_type == "ocr_mismatch" and f.is_interpretation is True
        d = _decision(_payment(GENERIC))
        assert d.category.value == "verify_first"
        assert "does not confirm fraudulent activity" in d.short_explanation.lower()
        assert not _asserts_claim(_text(d))


# ── E. Reference discrepancy ──────────────────────────────────────────────────

class TestE_ReferenceDiscrepancy:

    def test_explainable_discrepancy(self):
        findings, _, _ = evaluate_payment(_payment(INCONSISTENT))
        ref = [f for f in findings if "reference" in f.label.lower()]
        assert len(ref) == 1
        assert "'TID-241--'" in ref[0].detail and "'TID-241'" in ref[0].detail
        assert "OCR misreading" in ref[0].detail

    def test_expected_reference_differs_only_in_format(self):
        findings, _, s = evaluate_payment(_payment(INCONSISTENT), _expected(reference="TID-241"))
        assert _cmp(s, "reference")["status"] == "format_difference"
        assert "payment_mismatch" not in [f.finding_type for f in findings]

    def test_correlated_observations_do_not_escalate(self):
        findings, _, _ = evaluate_payment(_payment(INCONSISTENT), _expected("2.00", "EUR"))
        assert len(findings) == 3
        assert {f.correlation_group for f in findings} == {CORRELATION_GROUP}
        d = _decision(_payment(INCONSISTENT), _expected("2.00", "EUR"))
        assert d.category.value == "verify_first"
        assert any("3 observations come from the same reading" in s for s in d.limitations)


# ── F. Matching reference ─────────────────────────────────────────────────────

class TestF_MatchingReference:

    def test_no_reference_mismatch(self):
        findings, _, s = evaluate_payment(_payment(GENERIC), _expected(reference="TID-12345"))
        assert _cmp(s, "reference")["status"] == "match"
        assert not any("reference" in f.label.lower() for f in findings)

    def test_prefix_only_difference_is_formatting(self):
        _, _, s = evaluate_payment(_payment(GENERIC), _expected(reference="12345"))
        assert _cmp(s, "reference")["status"] == "format_difference"


# ── G. Degraded image ─────────────────────────────────────────────────────────

_BLURRED = {"assessment": "limited", "metrics": {"edge_sharpness": 0.05}, "note": "n",
            "warnings": [{"code": "blurred", "severity": "severe", "message": "The image appears blurred."}]}
_SOFT = {"assessment": "limited", "metrics": {"edge_sharpness": 0.12}, "note": "n",
         "warnings": [{"code": "possibly_blurred", "severity": "caution", "message": "Maybe blurred."}]}


class TestG_DegradedImage:

    def test_severe_quality_withholds_comparisons(self):
        p = _payment(HIGH_VALUE, quality=_BLURRED)
        findings, _, s = evaluate_payment(p, _expected("100.00", "EUR"))
        amount = _cmp(s, "amount")
        assert amount["status"] == "not_compared" and amount["ran"] is False
        assert "blurred" in amount["detail"]
        assert amount["extracted"] == "€1,000.00"          # raw value still shown for manual check
        assert "payment_mismatch" not in [f.finding_type for f in findings]

    def test_quality_warnings_are_not_findings_and_stay_unknown(self):
        d = _decision(_payment(LEGIT, quality=_BLURRED), _expected("25.00"))
        assert d.category.value == "insufficient_evidence"
        assert any("screenshot quality is limited (blurred)" in u for u in d.factors_unknown)

    def test_caution_warning_does_not_block_comparison(self):
        _, _, s = evaluate_payment(_payment(HIGH_VALUE, quality=_SOFT), _expected("100.00"))
        assert _cmp(s, "amount")["status"] == "mismatch"

    def test_low_confidence_field_is_not_compared(self):
        fields = [_field("amount", "€750", conf="low")]
        findings, _, s = evaluate_payment(_payment(fields), _expected("7.50"))
        assert _cmp(s, "amount")["status"] == "not_compared"
        assert "low OCR confidence" in _cmp(s, "amount")["detail"]
        assert findings == []

    def test_missing_quality_is_reported_not_assumed_good(self):
        d = _decision(_payment(LEGIT, quality=None))
        assert any("quality was not assessed" in u for u in d.factors_unknown)


# ── H. Empty or failed OCR ────────────────────────────────────────────────────

_UNREADABLE = {"assessment": "unreadable", "metrics": {}, "note": "n",
               "warnings": [{"code": "no_text", "severity": "severe", "message": "No text."}]}


class TestH_EmptyOrFailed:

    def test_empty_ocr_has_no_fabricated_fields(self):
        p = _payment([], raw_text="  ", quality=_UNREADABLE)
        findings, notes, s = evaluate_payment(p, _expected("25.00", "EUR", "Amazon", "TID-1"))
        assert findings == []
        assert s["extraction_status"] == "no_text"
        assert s["extracted_fields"] == []
        assert all(c["extracted"] is None and not c["ran"] for c in s["comparisons"])
        assert "OCR produced no text" in notes[0]

    def test_empty_ocr_decision_is_insufficient(self):
        d = _decision(_payment([], raw_text="", quality=_UNREADABLE))
        assert d.category.value == "insufficient_evidence"
        assert any("unreadable" in u for u in d.factors_unknown)

    def test_unread_expected_field_is_not_a_mismatch(self):
        findings, _, s = evaluate_payment(_payment([_field("amount", "€25.00")]),
                                          _expected(recipient="Amazon Europe", reference="TID-1"))
        assert _cmp(s, "recipient")["status"] == "not_compared"
        assert "not treated as a mismatch" in _cmp(s, "recipient")["detail"]
        assert findings == []


# ── I. Missing expected values ────────────────────────────────────────────────

class TestI_MissingExpected:

    def test_no_comparison_is_claimed(self):
        findings, notes, s = evaluate_payment(_payment(HIGH_VALUE))
        assert findings == []
        assert all(c["status"] == "not_supplied" and c["ran"] is False for c in s["comparisons"])
        assert s["expected_supplied"] == _expected()
        assert any("no expected values were supplied" in n for n in notes)
        assert not any("matched" in n for n in notes)

    def test_only_supplied_fields_are_compared(self):
        _, _, s = evaluate_payment(_payment(HIGH_VALUE), _expected(amount="1000.00"))
        ran = {c["field"]: c["ran"] for c in s["comparisons"]}
        assert ran == {"amount": True, "currency": False, "recipient": False, "reference": False}

    def test_decision_says_no_comparison_was_made(self):
        d = _decision(_payment(HIGH_VALUE))
        assert any("no expected payment details were supplied" in u for u in d.factors_unknown)


# ── J. No findings ────────────────────────────────────────────────────────────

class TestJ_NoFindings:

    @pytest.mark.parametrize("fields", [LEGIT, HIGH_VALUE])
    def test_no_findings_is_not_genuine_or_low_concern(self, fields):
        d = _decision(_payment(fields))
        assert d.category.value == "insufficient_evidence"
        assert "not evidence that the communication or payment is genuine" in d.short_explanation
        assert not _asserts_claim(_text(d))

    def test_completed_analysis_is_not_described_as_unavailable(self):
        d = _decision(_payment(LEGIT))
        text = _text(d)
        assert "unavailable, failed, or not yet requested" not in text
        assert "upload evidence and request analysis" not in text
        assert "not evidence that the communication or payment is genuine or safe" in text

    def test_claim_detector_is_not_vacuous(self):
        assert _asserts_claim("the payment is verified.")
        assert not _asserts_claim("this does not mean the payment is verified.")

    @pytest.mark.parametrize("status", ["unavailable", "failed"])
    def test_unavailable_analysis_is_not_low_concern(self, status):
        d = compute_decision([EvidenceInput("e", "shot.jpg", "image/jpeg", status, [])])
        assert d.category.value == "insufficient_evidence"


# ── Import wiring with the module enabled ─────────────────────────────────────

def test_app_import_loads_real_adapter_when_enabled(tmp_path):
    """
    Regression: schemas → payment_checks → analysis → (import-time) adapter →
    payment_checks was a cycle that silently fell back to UnavailableAdapter.
    The in-process suite runs with the module disabled, so check in a fresh process.
    """
    import os
    import subprocess
    import sys
    from pathlib import Path
    pytest.importorskip("satyalens_analysis")
    env = dict(os.environ, ANALYSIS_MODULE_ENABLED="true", PYTHONUTF8="1",
               DATABASE_URL=f"sqlite:///{(tmp_path / 'x.db').as_posix()}")
    code = ("import app.main; from app.services import analysis as a; "
            "print(type(a._adapter).__name__)")
    out = subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).parent.parent,
                         env=env, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip().splitlines()[-1] == "Person1Adapter", out.stderr


# ── Input normalisation ───────────────────────────────────────────────────────

class TestNormalisation:

    @pytest.mark.parametrize("raw, want", [("100", "100"), (" 100.50 ", "100.50"),
                                           ("1,000.00", "1000.00"), ("0.1", "0.1")])
    def test_valid_amounts(self, raw, want):
        assert normalise_amount(raw) == Decimal(want)

    @pytest.mark.parametrize("raw", ["1.000,00", "12,50", "1e3", "-5", "0", "€10", "10.123", "abc", ""])
    def test_invalid_or_ambiguous_amounts(self, raw):
        with pytest.raises(ValueError):
            normalise_amount(raw)

    @pytest.mark.parametrize("raw, want", [("EUR", "EUR"), ("eur", "EUR"), ("€", "EUR"),
                                           ("£", "GBP"), ("₹", "INR"), (" usd ", "USD")])
    def test_valid_currencies(self, raw, want):
        assert normalise_currency(raw) == want

    @pytest.mark.parametrize("raw, msg", [("$", "more than one currency"), ("¥", "more than one"),
                                          ("Rs", "more than one"), ("kr", "more than one"),
                                          ("XYZ", "not a supported"), ("euro", "not a supported")])
    def test_ambiguous_or_invalid_currencies(self, raw, msg):
        with pytest.raises(ValueError, match=msg):
            normalise_currency(raw)

    # Found on the synthetic dev split (reports/payment_pipeline_eval_v1/dev).
    def test_code_prefixed_amount_compares_currency(self):
        _, _, s = evaluate_payment(_payment([_field("amount", "GBP 1,263.83")]), _expected("1263.83", "EUR"))
        assert _cmp(s, "amount")["status"] == "match"
        assert _cmp(s, "currency")["status"] == "mismatch"

    def test_code_with_separator_parses(self):
        _, _, s = evaluate_payment(_payment([_field("amount", "INR: 500")]), _expected("500", "INR"))
        assert _cmp(s, "amount")["status"] == "match" and _cmp(s, "currency")["status"] == "match"

    def test_rupee_read_as_digit_stays_mismatch_but_is_explained(self):
        _, _, s = evaluate_payment(_payment([_field("amount", "21,463.12")]), _expected("1463.12"))
        a = _cmp(s, "amount")
        assert a["status"] == "mismatch"
        assert "may be a currency symbol such as ₹" in a["detail"]

    def test_hint_not_added_for_unrelated_mismatch(self):
        _, _, s = evaluate_payment(_payment([_field("amount", "€1,000.00")]), _expected("100.00"))
        assert "currency symbol such as" not in _cmp(s, "amount")["detail"]

    @pytest.mark.parametrize("extracted, expected", [("UTRO78994550632", "UTR078994550632"),
                                                     ("TID-I2345", "TID-12345")])
    def test_ocr_confusable_reference_is_not_a_mismatch(self, extracted, expected):
        findings, _, s = evaluate_payment(_payment([_field("transaction_id", extracted)]),
                                          _expected(reference=expected))
        assert _cmp(s, "reference")["status"] == "format_difference"
        assert "OCR often confuses" in _cmp(s, "reference")["detail"]
        assert findings == []

    def test_digit_change_in_reference_is_still_a_mismatch(self):
        _, _, s = evaluate_payment(_payment([_field("transaction_id", "TID-12345")]),
                                   _expected(reference="TID-12845"))
        assert _cmp(s, "reference")["status"] == "mismatch"

    @pytest.mark.parametrize("extracted, expected, status", [
        ("$25.00", "USD", "not_compared"),     # $ is shared — cannot confirm USD
        ("$25.00", "EUR", "mismatch"),         # but it is certainly not EUR
        ("25.00", "EUR", "not_compared"),      # no marker
        ("EUR 25.00", "EUR", "match"),
        ("€25 EUR", "EUR", "match"),           # same currency stated twice
        ("€25 USD", "EUR", "not_compared"),    # conflicting markers
    ])
    def test_extracted_currency_comparison(self, extracted, expected, status):
        _, _, s = evaluate_payment(_payment([_field("amount", extracted)]), _expected(currency=expected))
        assert _cmp(s, "currency")["status"] == status


# ── K. API and route integration ──────────────────────────────────────────────

JPEG = b"\xff\xd8\xff" + b"\x00" * 64


class _Recorder:
    def __init__(self, payment):
        self.payment, self.calls = payment, []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        return dict(copy.deepcopy(_BASE_RESULT), payment=copy.deepcopy(self.payment))


def _setup(client, tmp_path, monkeypatch, payment, filename="shot.jpg", data=JPEG, mime="image/jpeg"):
    fake = _Recorder(payment)
    adapter = Person1Adapter(fake, "1.0.0+schema1.0")
    monkeypatch.setattr("app.routes.evidence._storage_path", lambda: tmp_path)
    monkeypatch.setattr("app.routes.analysis._storage_path", lambda: tmp_path)
    monkeypatch.setattr("app.routes.analysis.module_is_available", lambda: True)
    monkeypatch.setattr("app.services.analysis._adapter", adapter)
    cid = client.post("/api/cases", json={}).json()["id"]
    client.post(f"/api/cases/{cid}/evidence/consent", json={"acknowledged": True})
    r = client.post(f"/api/cases/{cid}/evidence", files={"file": (filename, io.BytesIO(data), mime)})
    assert r.status_code == 201
    return cid, r.json()["id"], fake


def _analyse(client, cid, eid, body=None):
    url = f"/api/cases/{cid}/evidence/{eid}/analyse"
    return client.post(url) if body is None else client.post(url, json=body)


class TestK_Api:

    def test_upload_expected_compare_and_decide(self, client, tmp_path, monkeypatch):
        cid, eid, fake = _setup(client, tmp_path, monkeypatch, _payment(HIGH_VALUE))
        body = {"expected_payment": {"amount": "100.00", "currency": "€",
                                     "recipient": "  Flipkart India  Pvt Ltd ", "reference": ""}}
        r = _analyse(client, cid, eid, body)
        assert r.status_code == 202 and r.json()["status"] == "completed"
        # Expectations are compared by the backend, never passed to the package.
        assert fake.calls and "expected_payment_fields" not in fake.calls[0]

        res = client.get(f"/api/cases/{cid}/evidence/{eid}/analysis").json()
        pay = res["payment"]
        assert pay["expected_supplied"] == {"amount": "100.00", "currency": "EUR",
                                            "recipient": "Flipkart India Pvt Ltd", "reference": None}
        status = {c["field"]: c["status"] for c in pay["comparisons"]}
        assert status == {"amount": "mismatch", "currency": "match",
                          "recipient": "match", "reference": "not_supplied"}
        assert {f["field_name"]: f["extracted_value"] for f in pay["extracted_fields"]}["amount"] == "€1,000.00"
        assert pay["settlement_status"] == "not_determinable"
        assert [f["label"] for f in res["findings"]] == ["Amount differs from the amount you expected"]
        assert res["findings"][0]["correlation_group"] == CORRELATION_GROUP

        d = client.post(f"/api/cases/{cid}/decision").json()
        assert d["category"] == "verify_first"
        assert d["rule_version"] == "1.1.0"

    def test_no_body_is_backwards_compatible(self, client, tmp_path, monkeypatch):
        cid, eid, _ = _setup(client, tmp_path, monkeypatch, _payment(GENERIC))
        r = _analyse(client, cid, eid)
        assert r.status_code == 202 and r.json()["status"] == "completed"
        res = client.get(f"/api/cases/{cid}/evidence/{eid}/analysis").json()
        assert all(c["status"] == "not_supplied" for c in res["payment"]["comparisons"])
        assert [f["finding_type"] for f in res["findings"]] == ["ocr_mismatch"]

    def test_empty_json_body_is_accepted(self, client, tmp_path, monkeypatch):
        cid, eid, _ = _setup(client, tmp_path, monkeypatch, _payment(LEGIT))
        assert _analyse(client, cid, eid, {}).status_code == 202

    def test_same_expectations_are_idempotent_new_ones_rerun(self, client, tmp_path, monkeypatch):
        cid, eid, fake = _setup(client, tmp_path, monkeypatch, _payment(HIGH_VALUE))
        body = {"expected_payment": {"amount": "1000"}}
        first = _analyse(client, cid, eid, body).json()["result_id"]
        assert _analyse(client, cid, eid, body).json()["result_id"] == first
        assert _analyse(client, cid, eid).json()["result_id"] == first        # no body → existing
        assert len(fake.calls) == 1
        second = _analyse(client, cid, eid, {"expected_payment": {"amount": "100"}}).json()["result_id"]
        assert second != first and len(fake.calls) == 2
        res = client.get(f"/api/cases/{cid}/evidence/{eid}/analysis").json()
        assert res["payment"]["expected_supplied"]["amount"] == "100"

    @pytest.mark.parametrize("expected, fragment", [
        ({"amount": "1.000,00"}, "not a valid amount"),
        ({"amount": 100}, "string"),
        ({"amount": "-5"}, "not a valid amount"),
        ({"currency": "$"}, "more than one currency"),
        ({"currency": "XYZ"}, "not a supported currency"),
        ({"reference": "<script>"}, "Reference may contain only"),
        ({"recipient": "---"}, "letters or digits"),
        ({"amount": "5", "iban": "X"}, "Extra inputs"),
    ])
    def test_invalid_input_is_rejected(self, client, tmp_path, monkeypatch, expected, fragment):
        cid, eid, fake = _setup(client, tmp_path, monkeypatch, _payment(LEGIT))
        r = _analyse(client, cid, eid, {"expected_payment": expected})
        assert r.status_code == 422
        assert fragment in r.text
        assert fake.calls == []                                   # nothing was analysed
        assert client.get(f"/api/cases/{cid}/evidence/{eid}/analysis").status_code == 404

    def test_expected_details_rejected_for_non_image(self, client, tmp_path, monkeypatch):
        cid, eid, _ = _setup(client, tmp_path, monkeypatch, _payment(LEGIT),
                             filename="msg.txt", data=b"hello", mime="text/plain")
        r = _analyse(client, cid, eid, {"expected_payment": {"amount": "5"}})
        assert r.status_code == 422
        assert r.json()["detail"]["code"] == "EXPECTED_PAYMENT_NOT_APPLICABLE"

    def test_failed_ocr_is_failed_without_summary(self, client, tmp_path, monkeypatch):
        failed = {"status": "failed", "raw_text": "", "fields": [], "mismatches": [],
                  "limitations": ["OCR analysis error: engine crashed"]}
        cid, eid, _ = _setup(client, tmp_path, monkeypatch, failed)
        r = _analyse(client, cid, eid, {"expected_payment": {"amount": "5"}})
        assert r.status_code == 500 and r.json()["detail"]["code"] == "ANALYSIS_FAILED"
        res = client.get(f"/api/cases/{cid}/evidence/{eid}/analysis").json()
        assert res["status"] == "failed" and res["payment"] is None and res["findings"] == []
        assert client.post(f"/api/cases/{cid}/decision").json()["category"] == "insufficient_evidence"

    def test_unreadable_ocr_is_completed_but_honest(self, client, tmp_path, monkeypatch):
        cid, eid, _ = _setup(client, tmp_path, monkeypatch, _payment([], raw_text="", quality=_UNREADABLE))
        _analyse(client, cid, eid, {"expected_payment": {"amount": "5", "currency": "EUR"}})
        res = client.get(f"/api/cases/{cid}/evidence/{eid}/analysis").json()
        assert res["status"] == "completed" and res["findings"] == []
        assert res["payment"]["extraction_status"] == "no_text"
        assert res["payment"]["quality"]["assessment"] == "unreadable"
        assert res["payment"]["extracted_fields"] == []
        assert client.post(f"/api/cases/{cid}/decision").json()["category"] == "insufficient_evidence"

    @pytest.mark.parametrize("blur, expect_status", [(0, "mismatch"), (4, "not_compared")])
    def test_real_ocr_sharp_vs_blurred(self, tmp_path, blur, expect_status):
        """Real Tesseract: a sharp screenshot is compared; a blurred one is withheld."""
        from pathlib import Path
        pytest.importorskip("satyalens_analysis")
        import satyalens_analysis.payment_ocr as po
        font = Path("C:/Windows/Fonts/arial.ttf")
        if not po._DEPENDENCIES_OK or not font.exists():
            pytest.skip("Tesseract or Arial font not available")
        from PIL import Image, ImageDraw, ImageFilter, ImageFont
        from app.services.analysis_person1 import get_adapter

        img = Image.new("RGB", (900, 470), "white")
        draw = ImageDraw.Draw(img)
        f = ImageFont.truetype(str(font), 30)
        for i, (a, b) in enumerate([("Payment Successful", ""), ("Amount", "€7.50"),
                                    ("To", "Riya Demo Stores"), ("Transaction Ref No.", "QX-518234")]):
            draw.text((40, 40 + i * 100), a, fill="black", font=f)
            if b:
                draw.text((480, 40 + i * 100), b, fill="black", font=f)
        if blur:
            img = img.filter(ImageFilter.GaussianBlur(blur))
        shot = tmp_path / "receipt.png"
        img.save(shot)

        res = get_adapter().analyse(shot, "image/png", "e", expected_payment=_expected("70.00", "EUR"))
        assert res.payment["comparisons"][0]["status"] == expect_status
        labels = [x.label for x in res.findings]
        if blur:
            assert res.payment["quality"]["assessment"] != "no_issues_detected"
            assert "Amount differs from the amount you expected" not in labels
        else:
            assert res.payment["quality"]["assessment"] == "no_issues_detected"
            assert res.payment["comparisons"][0]["extracted"] == "€7.50"
            assert "Amount differs from the amount you expected" in labels

    def test_legacy_rows_without_payment_json(self, client, tmp_path, monkeypatch, db_session):
        from app.models import AnalysisResult
        cid, eid, _ = _setup(client, tmp_path, monkeypatch, _payment(GENERIC))
        _analyse(client, cid, eid)
        row = db_session.query(AnalysisResult).filter_by(evidence_id=eid).one()
        row.payment_json = None
        db_session.commit()
        res = client.get(f"/api/cases/{cid}/evidence/{eid}/analysis").json()
        assert res["payment"] is None and len(res["findings"]) == 1
        assert client.post(f"/api/cases/{cid}/decision").json()["category"] == "verify_first"
