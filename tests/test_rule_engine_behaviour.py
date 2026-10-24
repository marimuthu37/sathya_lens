"""
test_rule_engine_behaviour.py — Rule engine (context_analyser) tested on its
own, independently of the ML classifier, plus preserved-artifact checks.

Defects found 2026-10-24 and fixed the same day (the original failing
cases are kept below as regression tests, with suspicious counterparts):
  * a bare mention of "OTP" fired CREDENTIAL_REQUEST on genuine OTP-delivery
    and anti-fraud warnings -> now needs a request verb, not directly negated;
  * "expires in N minutes" (an OTP's validity) fired URGENCY_PRESSURE ->
    routine expiry wording no longer triggers on its own;
  * GIFT_CARD_PAYMENT missed plurals ("gift cards") -> plurals match.
The challenge suite informed these fixes, so it is development data for
the rule engine, not independent evidence of rule accuracy.
"""
import hashlib
import json
from pathlib import Path

import pytest

from satyalens_analysis.context_analyser import analyse_context
from satyalens_analysis.text_classifier import load_evaluation_dataset

REPO = Path(__file__).resolve().parent.parent
CHALLENGE = REPO / "data" / "challenge_suite_v1.csv"
MANIFEST = REPO / "reports" / "preserved_artifacts_manifest.json"

_CHALLENGE = load_evaluation_dataset(CHALLENGE)


def _by_category(cat):
    return [pytest.param(e.text, id=e.fields["id"]) for e in _CHALLENGE if e.fields["category"] == cat]


def _rule_ids(text):
    return {f.rule_id for f in analyse_context(text).flags}


ORDINARY_FINANCIAL = [
    "Rs 25,000 credited to your account XX1234 as salary on 01-Nov.",
    "Your EMI of Rs 8,450 will be auto-debited on 5th. Please maintain sufficient balance.",
    "Electricity bill of Rs 1,230 generated. Due date 15 Nov. Pay via the official app.",
    "Your card statement is ready. Total due Rs 6,780, due date 20 Nov.",
    "Refund of Rs 499 for your cancelled order has been processed to your original payment method.",
    "I transferred my share of the rent to you this morning, let me know when it shows up.",
    "Interest of Rs 312 credited to your savings account for the quarter.",
    "Your fixed deposit has matured and the amount has been credited to your savings account.",
]


class TestNoFalseFlagsOnOrdinaryMessages:
    @pytest.mark.parametrize("text", _by_category("ordinary_conversation"))
    def test_ordinary_conversation_not_flagged(self, text):
        assert _rule_ids(text) == set()

    @pytest.mark.parametrize("text", _by_category("payment_confirmation"))
    def test_payment_confirmation_not_flagged(self, text):
        assert _rule_ids(text) == set()

    @pytest.mark.parametrize("text", _by_category("delivery_notice"))
    def test_delivery_notice_not_flagged(self, text):
        assert _rule_ids(text) == set()

    @pytest.mark.parametrize("text", ORDINARY_FINANCIAL)
    def test_ordinary_financial_messages_not_flagged(self, text):
        assert _rule_ids(text) == set()


BENIGN_CREDENTIAL_WORDING = [
    "Your OTP is 482913. Do not share it with anyone.",
    "Never share your OTP or PIN over phone, SMS or email.",
    "Please do not ever share this code with anyone.",
    "We will never call you to ask for an OTP, CVV or card PIN.",
    "One time password for your login is 120934. Valid for 10 minutes.",
    "Bank staff don't need your PIN. Don't give your card details to callers.",
]
SUSPICIOUS_CREDENTIAL_WORDING = [
    "Don't worry, just share the OTP you got",
    "Please share your OTP with me to complete the process.",
    "Read me the 6 digit number you just received",
    "Approve the request by entering your PIN",
    "Aapka account band ho jayega, abhi OTP bhejo",
    "send me the code you received",
    "Reply with your card number and expiry date",
    "Kindly confirm your netbanking password to avoid suspension",
]


class TestGenuineOtpWarnings:
    def test_verification_code_without_otp_word_not_flagged(self):
        cs06 = next(e.text for e in _CHALLENGE if e.fields["id"] == "CS06")
        assert "CREDENTIAL_REQUEST" not in _rule_ids(cs06)

    # Original failing cases (CS01-CS05) — now regression tests.
    @pytest.mark.parametrize("text", [p for p in _by_category("genuine_otp_warning") if p.id != "CS06"])
    def test_genuine_otp_warning_not_flagged_as_credential_request(self, text):
        assert "CREDENTIAL_REQUEST" not in _rule_ids(text)

    @pytest.mark.parametrize("text", _by_category("genuine_otp_warning"))
    def test_genuine_otp_warning_has_no_flags_at_all(self, text):
        assert _rule_ids(text) == set()

    @pytest.mark.parametrize("text", BENIGN_CREDENTIAL_WORDING)
    def test_benign_or_negated_credential_wording_not_flagged(self, text):
        assert "CREDENTIAL_REQUEST" not in _rule_ids(text)

    @pytest.mark.parametrize("text", SUSPICIOUS_CREDENTIAL_WORDING)
    def test_credential_requests_still_flagged(self, text):
        assert "CREDENTIAL_REQUEST" in _rule_ids(text)


class TestExpiryWording:
    # Original failing case (CS03) — now a regression test.
    def test_otp_expiry_is_not_urgency_pressure(self):
        cs03 = next(e.text for e in _CHALLENGE if e.fields["id"] == "CS03")
        assert "URGENCY_PRESSURE" not in _rule_ids(cs03)

    @pytest.mark.parametrize("text", ["This OTP expires in 5 minutes.", "Your coupon expires today.",
                                      "The link expires tonight for security reasons."])
    def test_routine_expiry_alone_does_not_fire(self, text):
        assert _rule_ids(text) == set()

    @pytest.mark.parametrize("text", ["Pay within 30 minutes or lose access", "Act now, last chance",
                                      "Verify immediately or your account will be blocked"])
    def test_pressure_wording_still_fires(self, text):
        assert "URGENCY_PRESSURE" in _rule_ids(text)


class TestScamDetectionStillWorks:
    @pytest.mark.parametrize("text,rule", [
        ("Share your OTP with me now or your account will be blocked", "CREDENTIAL_REQUEST"),
        ("Your account will be blocked today, act now", "THREAT_OR_COERCION"),
        ("Install AnyDesk so I can process your refund", "REMOTE_ACCESS_REQUEST"),
        ("Buy a Google Play card and send me the code, keep this between us", "GIFT_CARD_PAYMENT"),
    ])
    def test_core_scam_patterns_fire(self, text, rule):
        assert rule in _rule_ids(text)

    # Original failing cases — now regression tests.
    @pytest.mark.parametrize("text", ["buy gift cards for me", "send Google Play cards",
                                      "buy 5 Amazon gift cards and send codes"])
    def test_plural_gift_cards_fire(self, text):
        assert "GIFT_CARD_PAYMENT" in _rule_ids(text)

    @pytest.mark.parametrize("text", ["Thanks for the birthday gift, the cards were lovely",
                                      "I got a new card from the bank today"])
    def test_benign_card_mentions_not_flagged(self, text):
        assert "GIFT_CARD_PAYMENT" not in _rule_ids(text)

    def test_challenge_suite_coverage(self):
        """Documents coverage on the challenge suite (development data): before the
        2026-10-24 fixes 5/26 legitimate were flagged and 4/16 scams; now 0/26 and 9/16."""
        legit = [e for e in _CHALLENGE if e.label == "legitimate"]
        scams = [e for e in _CHALLENGE if e.label == "suspicious"]
        assert sum(bool(_rule_ids(e.text)) for e in legit) == 0
        assert (sum(bool(_rule_ids(e.text)) for e in scams), len(scams)) == (9, 16)


class TestPreservedArtifacts:
    _files = json.loads(MANIFEST.read_text(encoding="utf-8"))["files"]

    @pytest.mark.parametrize("rel,expected", sorted(_files.items()))
    def test_hash_unchanged(self, rel, expected):
        path = REPO / rel
        if not path.is_file():
            pytest.skip(f"{rel} not present on this machine")
        assert hashlib.sha256(path.read_bytes()).hexdigest() == expected
