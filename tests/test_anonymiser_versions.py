"""
test_anonymiser_versions.py — anon-v1 is frozen (protocol v3 depends on it);
anon-v2 is a proposed, separate version for the protocol v4 draft.
"""
import re

import pytest

from satyalens_analysis.anonymise import (
    ANONYMISERS, ANONYMISER_V2_VERSION, ANONYMISER_VERSION, anonymise, anonymise_v2, pii_warnings,
)

# Golden outputs recorded 2026-10-24. If any of these change, anon-v1 has
# changed, which protocol v3 forbids — create a new version instead.
ANON_V1_GOLDEN = [
    ("Dear SBI user your SBI YONO Account <DATE_TIME> will be closed, update now <URL> call <PHONE_NUMBER>",
     "Dear SBI user your SBI YONO Account datetoken will be closed, update now urltoken call phonetoken"),
    ("<NAMED_ENTITY>-Did You Attempt A Zelle Payment? reply YES or NO <US_BANK_NUMBER>",
     "-Did You Attempt A Zelle Payment? reply YES or NO numtoken"),
    ("482913 is your OTP for txn of Rs 1,499 at FreshMart on card XX1234 on 12-Oct-26 14:05. Do not share.",
     "numtoken is your OTP for txn of Rs 1,499 at FreshMart on card numtoken on datetoken datetoken . Do not share."),
    ("Rs 25000 debited from A/c XXXX5521 to rahul.k@okaxis UPI Ref 629104837251. Call 1800 000 0000 or +91 98765 43210",
     "Rs numtoken debited from A/c numtoken to upitoken UPI Ref numtoken . Call phonetoken or phonetoken"),
    ("Hi Rahul, your parcel from Andheri will arrive tomorrow. Mail me at ravi.s@gmail.com",
     "Hi Rahul, your parcel from Andheri will arrive tomorrow. Mail me at emailtoken"),
    ("Follow us @SBI_Care for updates", "Follow us @SBI_Care for updates"),
    ("Contact rahul@ for help", "Contact rahul@ for help"),
]


class TestAnonV1Frozen:
    @pytest.mark.parametrize("raw,expected", ANON_V1_GOLDEN)
    def test_golden_outputs_unchanged(self, raw, expected):
        assert anonymise(raw) == expected

    def test_version_label_unchanged(self):
        assert ANONYMISER_VERSION == "anon-v1"


class TestAnonV2:
    @pytest.mark.parametrize("raw,expected", [
        ("Follow us @SBI_Care for updates", "Follow us handletoken for updates"),
        ("Your OTP 482913, reply to @HDFC_Bank.", "Your OTP numtoken , reply to handletoken ."),
        ("(@paytm) support", "( handletoken ) support"),
        ("Contact rahul@ for help", "Contact emailtoken for help"),
    ])
    def test_masks_handles_and_email_fragments(self, raw, expected):
        assert anonymise_v2(raw) == expected

    @pytest.mark.parametrize("text", ["loan @ 9.9% only", "only Rs@499 today", "price @ Rs 99", "see you @ 5"])
    def test_rates_prices_and_standalone_at_kept(self, text):
        assert anonymise_v2(text) == anonymise(text)

    @pytest.mark.parametrize("raw", [r for r, _ in ANON_V1_GOLDEN])
    def test_v2_is_v1_plus_new_rules_only(self, raw):
        """Where anon-v1 output contains no '@', anon-v2 must equal anon-v1."""
        v1 = anonymise(raw)
        if "@" not in v1:
            assert anonymise_v2(raw) == v1

    def test_existing_tokens_preserved(self):
        out = anonymise_v2("mail ravi.s@gmail.com, pay rahul.k@okaxis, follow @abc, call 9876543210")
        assert {"emailtoken", "upitoken", "handletoken", "phonetoken"} <= set(re.findall(r"\w+token", out))

    def test_idempotent_and_no_digits(self):
        s = "OTP 482913 from @Bank_Help, mail x@ or y@bank, link www.a.example, call 9876543210"
        assert anonymise_v2(anonymise_v2(s)) == anonymise_v2(s)
        assert not re.search(r"\d{4,}", anonymise_v2(s))

    def test_registry_and_warnings(self):
        assert ANONYMISERS[ANONYMISER_VERSION] is anonymise
        assert ANONYMISERS[ANONYMISER_V2_VERSION] is anonymise_v2
        assert ANONYMISER_V2_VERSION == "anon-v2"
        assert not any("handletoken" in w for w in pii_warnings("contact handletoken now"))

    def test_v2_not_used_by_protocol_v3_pipelines(self):
        """Protocol v3 tooling stays on anon-v1. (text_classifier gained an anon-v2
        hook when protocol v4 was approved; it is used only by models trained with
        preprocessing='anon-v2'.)"""
        from pathlib import Path
        repo = Path(__file__).resolve().parent.parent
        for f in ["scripts/build_pool_and_test_v3.py", "scripts/donate_messages.py",
                  "scripts/select_text_model_v3.py", "scripts/acceptance_v3.py"]:
            assert "anonymise_v2" not in (repo / f).read_text(encoding="utf-8"), f

    def test_preprocessor_versions(self):
        from satyalens_analysis import text_classifier as tc
        s = "follow @SBI_Care, OTP 482913"
        assert tc._preprocessor(None)(s) == s
        assert tc._preprocessor("anon-v1")(s) == anonymise(s)
        assert tc._preprocessor("anon-v2")(s) == anonymise_v2(s)
