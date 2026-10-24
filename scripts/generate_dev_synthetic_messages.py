"""
generate_dev_synthetic_messages.py — SatyaLens synthetic DEVELOPMENT dataset (dev-synth-v1)
==========================================================================================

Writes, deterministically (seed 20261101):

  data/dev/synthetic_dev_messages_v1.csv         labelled (suspicious / legitimate)
  data/dev/synthetic_dev_edge_cases_v1.csv       UNLABELLED edge cases (no `label` column)
  data/dev/synthetic_dev_messages_v1.provenance.json

WHAT THIS DATA IS
-----------------
Every message is SYNTHETIC: hand-written templates (written for this project)
filled with fictional values. No real messages, people, accounts, UPI IDs,
phone numbers or URLs. Domains use reserved TLDs (.example / .invalid / .test),
UPI handles end in "@examplepay", OTPs are written XXXXXX, and no text contains
a run of four or more digits.

It exists for development, demonstration and functional/integration testing
ONLY. It does NOT represent the distribution of Indian financial alerts, it is
not a Protocol v5 source, it is never a donated genuine alert, and metrics on
it say nothing about real-world performance.

Labels (labelled file) follow the classifier's two labels:
  suspicious  — template written to exhibit a financial social-engineering pattern;
  legitimate  — template written to resemble routine, benign communication.
'inconclusive' is NOT a label in this codebase: it is a classifier status
(no in-vocabulary term). Borderline / ambiguous messages and messages expected
to produce 'inconclusive' or 'not_applicable' therefore live in the separate
edge-case file, which deliberately has no `label` column so the training and
evaluation loaders refuse it.

    python scripts/generate_dev_synthetic_messages.py             # write data/dev/
    python scripts/generate_dev_synthetic_messages.py --check     # regenerate in memory, compare, validate
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import random
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Dict, List, Tuple

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from satyalens_analysis.text_classifier import LABELS, normalise_text  # noqa: E402

GENERATOR = "scripts/generate_dev_synthetic_messages.py"
GENERATOR_VERSION = "dev-synth-v1"
SEED = 20261101
SOURCE = "SYN_DEV"
PROVENANCE = "synthetic"
VARIANTS_PER_TEMPLATE = 6
OUT_DIR = REPO / "data" / "dev"
LABELLED = "synthetic_dev_messages_v1.csv"
EDGE = "synthetic_dev_edge_cases_v1.csv"
PROVENANCE_FILE = "synthetic_dev_messages_v1.provenance.json"

LABELLED_FIELDS = ["id", "text", "label", "group", "category", "language", "variation",
                   "source", "provenance", "generator", "generator_version"]
EDGE_FIELDS = ["id", "text", "case_type", "intended_reading", "expected_text_classifier_status",
               "rationale", "category", "language", "source", "provenance", "generator", "generator_version"]

CATEGORY_LABEL = {
    "fake_payment_confirmation": "suspicious",
    "fee_payment_request": "suspicious",
    "impersonation_urgent_money": "suspicious",
    "delivery_parcel_scam": "suspicious",
    "suspicious_link_credential_request": "suspicious",
    "legit_transaction_notification": "legitimate",
    "legit_delivery_update": "legitimate",
    "legit_otp_delivery": "legitimate",
    "legit_bill_reminder": "legitimate",
    "everyday_message": "legitimate",
}
EDGE_CASE_TYPES = {"ambiguous_no_reference_label", "inconclusive_out_of_vocabulary", "not_applicable_blank"}
RESERVED_TLDS = {"example", "invalid", "test"}

# --- fictional slot values ---------------------------------------------------
SLOTS = {
    "bank": ["Kaveri Trust Bank", "Nilgiri Savings Bank", "Sahyadri Urban Bank", "Brahmaputra Co-op Bank",
             "Vindhya Federal Bank", "Malabar People's Bank"],
    "app": ["PayNova", "QuickRupee", "WalletKart", "UPIBee"],
    "courier": ["SwiftParcel", "Dakshin Express", "PinDrop Logistics", "MetroShip"],
    "store": ["ShopNest", "KiranaKart", "BazaarHub", "TechMandi"],
    "name": ["Riya", "Kabir", "Meera", "Arjun", "Ishaan", "Tara", "Neel", "Anika"],
    "relation": ["Mum", "Dad", "beta", "didi", "bhaiya"],
    "amount": ["Rs 249", "Rs 499", "Rs 1,250", "Rs 2,499", "Rs 3,800", "Rs 7,650", "Rs 12,000", "Rs 18,499"],
    "fee": ["Rs 25", "Rs 49", "Rs 99", "Rs 149", "Rs 299"],
    "ref": ["DEMO-K7QX", "REF-AB2C", "TXN-ZP4Q", "ORD-MN7T", "DEMO-RT9W", "REF-HJ3K"],
    "acct": ["XX12", "XX47", "XX83", "XX05", "XX69"],
    "time": ["10:30 AM", "2:15 PM", "6:45 PM", "9:05 AM"],
    "day": ["today", "tomorrow", "by Friday", "before 6 PM"],
    "badlink": ["secure-kyc-update.example/verify", "refund-claim.invalid/start", "parcel-fee.example/pay",
                "bank-verify-login.test/auth", "upi-reward.example/claim", "redeliver-now.invalid/fee"],
    "upi_bad": ["refund.desk@examplepay", "claim.reward@examplepay", "parcel.fee@examplepay"],
    "upi_ok": ["riya.demo@examplepay", "kirana.store@examplepay", "neel.demo@examplepay"],
    "event": ["dinner", "the team meeting", "cricket practice", "the parent-teacher meeting"],
}

# (group, category, language, template). Each template yields VARIANTS_PER_TEMPLATE rows.
TEMPLATES: List[Tuple[str, str, str, str]] = [
    # ---- suspicious ----
    ("D01_fake_credit_accept", "fake_payment_confirmation", "en",
     "You have received {amount} from {name} on {app}. The payment is on hold. Tap {badlink} to accept the money into your account."),
    ("D02_fake_credit_pin", "fake_payment_confirmation", "en",
     "{app}: {amount} credited request pending. Enter your UPI PIN to receive the amount. Ref {ref}."),
    ("D03_overpaid_return", "fake_payment_confirmation", "en",
     "Hi, I sent {amount} to you by mistake instead of {fee}. Please check and return the extra to {upi_bad} urgently."),
    ("D04_fee_refund_release", "fee_payment_request", "en",
     "Your refund of {amount} from {store} is approved. Pay a processing fee of {fee} at {badlink} to release it {day}."),
    ("D05_fee_loan_approval", "fee_payment_request", "en",
     "Congratulations! Your pre-approved loan of {amount} is ready. Pay {fee} file charge to {upi_bad} to get the money now."),
    ("D06_fee_prize_hinglish", "fee_payment_request", "hinglish",
     "Badhai ho! Aapne {store} lucky draw mein {amount} jeete hain. Prize lene ke liye {fee} fee {upi_bad} par bhejiye."),
    ("D07_family_new_number", "impersonation_urgent_money", "en",
     "Hi {relation}, this is my new number, my phone broke. I need {amount} urgently for a bill, please send it to {upi_bad} and don't call, I'm in a meeting."),
    ("D08_boss_gift_cards", "impersonation_urgent_money", "en",
     "This is {name} from the office. I'm stuck in a meeting and need you to buy gift cards worth {amount} right now. Send me the codes, I will pay you back."),
    ("D09_family_hinglish", "impersonation_urgent_money", "hinglish",
     "{relation} main {name} bol raha hoon, mera phone kho gaya. Abhi {amount} ki zaroorat hai, jaldi {upi_bad} par bhej do please."),
    ("D10_parcel_customs", "delivery_parcel_scam", "en",
     "{courier}: your parcel is held at customs. Pay {fee} duty within 24 hours at {badlink} or it will be returned."),
    ("D11_parcel_address", "delivery_parcel_scam", "en",
     "We could not deliver your package due to an incomplete address. Update details and pay a {fee} redelivery fee here: {badlink}"),
    ("D12_kyc_link", "suspicious_link_credential_request", "en",
     "Dear customer, your {bank} account will be blocked {day} as KYC is pending. Update immediately at {badlink}"),
    ("D13_share_otp", "suspicious_link_credential_request", "en",
     "{bank} security team: to stop an unauthorised debit of {amount}, reply with the OTP you just received. Ref {ref}."),
    ("D14_mixed_warning_link", "suspicious_link_credential_request", "en",
     "Never share your OTP with anyone. To claim your {amount} cashback, log in with your card details at {badlink}"),
    ("D15_kyc_hinglish", "suspicious_link_credential_request", "hinglish",
     "{bank}: aapka account aaj band ho jayega. KYC update karne ke liye turant {badlink} par login karein aur OTP daalein."),
    # ---- legitimate ----
    ("L01_debit_alert", "legit_transaction_notification", "en",
     "{bank}: {amount} debited from A/c {acct} on {time} for a UPI payment to {upi_ok}. Not you? Call the number on your card."),
    ("L02_credit_alert", "legit_transaction_notification", "en",
     "{bank}: A/c {acct} credited with {amount} by NEFT, Ref {ref}. Available balance updated."),
    ("L03_upi_sent", "legit_transaction_notification", "en",
     "You paid {amount} to {name} using {app}. Ref {ref}. If you did not make this payment, report it in the {app} app."),
    ("L04_card_purchase", "legit_transaction_notification", "en",
     "Your {bank} card {acct} was used for {amount} at {store} on {time}. If this was not you, block the card from the official app."),
    ("L05_delivery_out", "legit_delivery_update", "en",
     "{courier}: your {store} order {ref} is out for delivery and will arrive by {time}. No payment is due."),
    ("L06_delivered", "legit_delivery_update", "en",
     "Delivered: your {store} package {ref} was handed to {name} at {time}. Thank you for shopping with us."),
    ("L07_delivery_hinglish", "legit_delivery_update", "hinglish",
     "{courier}: aapka order {ref} aaj {time} tak deliver ho jayega. Koi payment nahi dena hai."),
    ("L08_otp_delivery", "legit_otp_delivery", "en",
     "XXXXXX is your OTP for a payment of {amount} at {store}. Valid for 5 minutes. Do not share it with anyone, including bank staff."),
    ("L09_bill_reminder", "legit_bill_reminder", "en",
     "Reminder: your electricity bill of {amount} is due {day}. Pay from the official app or at the service centre. Ignore if already paid."),
    ("L10_card_due", "legit_bill_reminder", "en",
     "{bank}: your credit card statement is ready. Total due {amount}, minimum due {fee}, due {day}. Pay through net banking or the {bank} app."),
    ("L11_everyday_plans", "everyday_message", "en",
     "Hey {name}, are we still on for {event} {day}? Let me know if the time works for you."),
    ("L12_everyday_split", "everyday_message", "en",
     "Thanks for lunch! I sent you {amount} for my share on {app}, check when you get a chance."),
    ("L13_everyday_hinglish", "everyday_message", "hinglish",
     "{name}, kal {event} ke baad milte hain. Ghar pe sab theek hai, tension mat lo."),
]

# Hand-written short forms (variation "short"): each keeps the cue that defines its label.
SHORT: Dict[str, str] = {
    "D01_fake_credit_accept": "Payment of {amount} on hold. Accept it at {badlink}",
    "D02_fake_credit_pin": "{app}: enter your UPI PIN to receive {amount}.",
    "D03_overpaid_return": "Sent {amount} by mistake, return it to {upi_bad} now",
    "D04_fee_refund_release": "Refund {amount} approved. Pay {fee} fee at {badlink}",
    "D05_fee_loan_approval": "Loan of {amount} ready, pay {fee} charge to {upi_bad}",
    "D06_fee_prize_hinglish": "Prize {amount} jeeta! {fee} fee {upi_bad} par bhejo",
    "D07_family_new_number": "{relation} new number, send {amount} to {upi_bad} asap",
    "D08_boss_gift_cards": "Buy {amount} of gift cards now and send me the codes. {name}",
    "D09_family_hinglish": "{relation} jaldi {amount} {upi_bad} par bhej do",
    "D10_parcel_customs": "Parcel held at customs. Pay {fee} at {badlink}",
    "D11_parcel_address": "Delivery failed. Pay {fee} redelivery fee: {badlink}",
    "D12_kyc_link": "{bank} KYC pending, account blocked {day}. Update: {badlink}",
    "D13_share_otp": "{bank}: reply with your OTP to stop a debit of {amount}",
    "D14_mixed_warning_link": "Claim {amount} cashback with your card details at {badlink}",
    "D15_kyc_hinglish": "{bank} KYC: {badlink} par login karke OTP daalein",
    "L01_debit_alert": "{bank}: {amount} debited from A/c {acct}. Not you? Call the number on your card.",
    "L02_credit_alert": "{bank}: A/c {acct} credited with {amount}, Ref {ref}.",
    "L03_upi_sent": "Paid {amount} to {name} via {app}. Ref {ref}.",
    "L04_card_purchase": "{bank} card {acct} used for {amount} at {store}.",
    "L05_delivery_out": "Order {ref} is out for delivery, arriving by {time}.",
    "L06_delivered": "Delivered: order {ref} at {time}.",
    "L07_delivery_hinglish": "Order {ref} aaj deliver hoga, koi payment nahi.",
    "L08_otp_delivery": "XXXXXX is your OTP for {amount}. Do not share it.",
    "L09_bill_reminder": "Electricity bill of {amount} due {day}. Ignore if paid.",
    "L10_card_due": "{bank} card due {amount}, minimum {fee}, due {day}.",
    "L11_everyday_plans": "{name}, is {event} still on {day}?",
    "L12_everyday_split": "Sent you {amount} for lunch on {app}",
    "L13_everyday_hinglish": "{name}, kal {event} ke baad milte hain",
}

TYPOS = {"account": "acount", "verify": "verfy", "immediately": "immediatly", "delivery": "delivry",
         "payment": "paymnet", "received": "recieved", "urgently": "urgntly", "please": "plz",
         "your": "ur", "receive": "recieve", "available": "availble", "minutes": "mins"}


def _vary(text: str, kind: str, rng: random.Random) -> str:
    if kind == "base":
        return text
    if kind == "typos":
        out = text
        for good, bad in TYPOS.items():
            out = re.sub(rf"\b{good}\b", bad, out, count=1)
        return out if out != text else text.replace(".", "", 1)
    if kind == "no_punctuation":
        # sentence punctuation only: keeps "Rs 1,250", domains and handles intact
        return re.sub(r"[.,!?:;](?=\s|$)", "", text)
    if kind == "shouting":
        return text.upper() + "!!"
    if kind == "short":
        return text                 # the template itself is the hand-written SHORT form
    if kind == "emoji":
        return rng.choice(["⚠️ ", "\U0001F4E6 ", "\U0001F4B0 ", "\U0001F64F "]) + text
    raise ValueError(kind)


VARIATIONS = ["base", "typos", "no_punctuation", "shouting", "short", "emoji"]

EDGE_CASES: List[Tuple[str, str, str, str, str, str, str]] = [
    # (text, case_type, intended_reading, expected_status, rationale, category, language)
    ("Please pay the pending amount today to avoid a late fee.", "ambiguous_no_reference_label", "ambiguous",
     "analysed", "No sender, link or payee: could be a landlord, school or a scam.", "ambiguous_payment_request", "en"),
    ("Call me as soon as you see this, it's about the money.", "ambiguous_no_reference_label", "ambiguous",
     "analysed", "Urgency without a request; depends on who sent it.", "ambiguous_urgent_contact", "en"),
    ("Your account details were updated. If this wasn't you, contact support.", "ambiguous_no_reference_label",
     "ambiguous", "analysed", "Genuine banks send this; so do phishers (no link here).", "ambiguous_account_notice", "en"),
    ("Hi, is this still your number? I owe you Rs 500 from last month.", "ambiguous_no_reference_label", "ambiguous",
     "analysed", "Common opener for scams and for genuine old contacts.", "ambiguous_reconnect", "en"),
    ("You are eligible for a pre-approved personal loan. Visit your nearest branch to know more.",
     "ambiguous_no_reference_label", "ambiguous", "analysed", "Marketing-like; no fee, link or credential asked.",
     "ambiguous_offer", "en"),
    ("We tried to deliver your parcel today. We will try again tomorrow.", "ambiguous_no_reference_label",
     "ambiguous", "analysed", "No fee or link, but unsolicited delivery notices are a scam lure.",
     "ambiguous_delivery_notice", "en"),
    ("Send me the code I just sent to your phone, I typed my number wrong.", "ambiguous_no_reference_label",
     "ambiguous", "analysed", "Classic OTP-takeover script, yet could come from a friend; context needed.",
     "ambiguous_code_request", "en"),
    ("Bhai paise bhej de, baad mein baat karte hain.", "ambiguous_no_reference_label", "ambiguous", "analysed",
     "Hinglish money request between contacts; legitimacy unknowable from text.", "ambiguous_money_request",
     "hinglish"),
    ("Your KYC is due for periodic update. Please visit the branch with your documents.",
     "ambiguous_no_reference_label", "ambiguous", "analysed", "Genuine banks send this; no link or credential.",
     "ambiguous_kyc_notice", "en"),
    ("Thanks, received.", "ambiguous_no_reference_label", "ambiguous", "analysed",
     "Too short to judge.", "ambiguous_short", "en"),
    ("आपका खाता आज बंद हो जाएगा, तुरंत लिंक पर क्लिक करें।", "inconclusive_out_of_vocabulary", "suspicious",
     "inconclusive", "Devanagari script: expected to have no in-vocabulary term for an English/Latin model.",
     "unsupported_script", "hi-Deva"),
    ("आपका ऑर्डर कल पहुँच जाएगा, कोई भुगतान नहीं करना है।", "inconclusive_out_of_vocabulary", "legitimate",
     "inconclusive", "Devanagari script delivery update.", "unsupported_script", "hi-Deva"),
    ("உங்கள் கணக்கு முடக்கப்படும், உடனே இணைப்பைத் திறக்கவும்.", "inconclusive_out_of_vocabulary", "suspicious",
     "inconclusive", "Tamil script.", "unsupported_script", "ta-Taml"),
    ("\U0001F4B0\U0001F4B0\U0001F4B0 \U0001F449\U0001F449 \U0001F381", "inconclusive_out_of_vocabulary", "ambiguous",
     "inconclusive", "Emoji only: no word tokens at all.", "emoji_only", "none"),
    ("??? !!! ... ###", "inconclusive_out_of_vocabulary", "ambiguous", "inconclusive",
     "Punctuation only: no word tokens.", "punctuation_only", "none"),
    ("12 34 56 78", "inconclusive_out_of_vocabulary", "ambiguous", "inconclusive",
     "Digits only (pairs, no 4+ digit run): no word tokens.", "digits_only", "none"),
    ("   ", "not_applicable_blank", "ambiguous", "not_applicable",
     "Whitespace only: run_analysis/classify_text treat it as no message.", "blank", "none"),
]


def generate() -> Tuple[List[Dict[str, str]], List[Dict[str, str]]]:
    rng = random.Random(SEED)
    rows: List[Dict[str, str]] = []
    seen = set()
    if set(SHORT) != {g for g, *_ in TEMPLATES}:
        raise RuntimeError("every template group needs exactly one SHORT form")
    for group, category, lang, template in TEMPLATES:
        for v, kind in enumerate(VARIATIONS[:VARIANTS_PER_TEMPLATE]):
            tmpl = SHORT[group] if kind == "short" else template
            fields = sorted(set(re.findall(r"{(\w+)}", tmpl)))   # sorted: set order varies per process
            for _attempt in range(50):
                text = _vary(tmpl.format(**{f: rng.choice(SLOTS[f]) for f in fields}), kind, rng)
                key = normalise_text(text)
                if key not in seen:
                    seen.add(key)
                    break
            else:
                raise RuntimeError(f"could not make a unique variant for {group}/{kind}")
            rows.append({"id": f"DEV1-{group}-{v:02d}", "text": text, "label": CATEGORY_LABEL[category],
                         "group": group, "category": category, "language": lang, "variation": kind,
                         "source": SOURCE, "provenance": PROVENANCE, "generator": GENERATOR,
                         "generator_version": GENERATOR_VERSION})
    edge = [{"id": f"DEV1-EDGE-{i:02d}", "text": t, "case_type": ct, "intended_reading": ir,
             "expected_text_classifier_status": st, "rationale": why, "category": cat, "language": lang,
             "source": SOURCE, "provenance": PROVENANCE, "generator": GENERATOR, "generator_version": GENERATOR_VERSION}
            for i, (t, ct, ir, st, why, cat, lang) in enumerate(EDGE_CASES)]
    return rows, edge


def to_csv(rows: List[Dict[str, str]], fields: List[str]) -> bytes:
    buf = io.StringIO(newline="")
    w = csv.DictWriter(buf, fieldnames=fields, lineterminator="\n")
    w.writeheader()
    w.writerows(rows)
    return buf.getvalue().encode("utf-8")


_URL = re.compile(r"(?i)\b(?:https?://)?(?:[a-z0-9-]+\.)+([a-z]{2,})(?:/\S*)?")
_HANDLE = re.compile(r"[\w.-]+@([\w-]+(?:\.[\w-]+)*)")    # a trailing sentence full stop is not part of it


def validate(rows: List[Dict[str, str]], edge: List[Dict[str, str]]) -> Dict:
    """Return a validation report; raise ValueError listing every problem found."""
    problems: List[str] = []
    for name, data, fields in (("labelled", rows, LABELLED_FIELDS), ("edge", edge, EDGE_FIELDS)):
        ids = [r["id"] for r in data]
        if len(ids) != len(set(ids)):
            problems.append(f"{name}: duplicate ids")
        for r in data:
            if list(r) != fields:
                problems.append(f"{name} {r.get('id')}: fields {list(r)} != {fields}")
            missing = [k for k in fields if r.get(k) is None or (r.get(k) == "" and k != "text")]
            if missing:
                problems.append(f"{name} {r['id']}: missing {missing}")
            if r["source"] != SOURCE or r["provenance"] != PROVENANCE or r["generator_version"] != GENERATOR_VERSION:
                problems.append(f"{name} {r['id']}: source/provenance/version marker wrong")
            text = r["text"]
            if re.search(r"\d{4,}", text):
                problems.append(f"{name} {r['id']}: run of 4+ digits")
            for m in _URL.finditer(_HANDLE.sub(" ", text)):          # handles are checked separately
                if m.group(1).lower() not in RESERVED_TLDS:
                    problems.append(f"{name} {r['id']}: non-reserved domain {m.group(0)!r}")
            for m in _HANDLE.finditer(text):
                if not m.group(1).lower().endswith("example") and not m.group(1).lower().endswith("examplepay"):
                    problems.append(f"{name} {r['id']}: non-placeholder handle {m.group(0)!r}")
    for r in rows:
        if r["label"] not in LABELS:
            problems.append(f"labelled {r['id']}: label {r['label']!r} not in {LABELS}")
        if CATEGORY_LABEL.get(r["category"]) != r["label"]:
            problems.append(f"labelled {r['id']}: category/label inconsistent")
        if not r["text"].strip():
            problems.append(f"labelled {r['id']}: empty text")
    for r in edge:
        if r["case_type"] not in EDGE_CASE_TYPES:
            problems.append(f"edge {r['id']}: unknown case_type")
        if "label" in r:
            problems.append(f"edge {r['id']}: must not carry a label column")
    norm = Counter(normalise_text(r["text"]) for r in rows + edge if r["text"].strip())
    dups = [k for k, n in norm.items() if n > 1]
    if dups:
        problems.append(f"{len(dups)} duplicate messages after normalisation")
    if problems:
        raise ValueError("Dev dataset validation failed: " + "; ".join(problems[:20]))
    return {"labelled_rows": len(rows), "edge_rows": len(edge), "duplicates": 0, "missing_values": 0,
            "label_category_consistent": True, "placeholders_only": True, "no_4plus_digit_runs": True}


def provenance(rows, edge, labelled_bytes: bytes, edge_bytes: bytes, report: Dict) -> Dict:
    return {
        "name": "satyalens-synthetic-dev-messages", "version": "1", "generator_version": GENERATOR_VERSION,
        "synthetic": True, "provenance": PROVENANCE, "source_marker": SOURCE,
        "generator": GENERATOR, "generator_seed": SEED,
        "files": {LABELLED: {"rows": len(rows), "sha256": hashlib.sha256(labelled_bytes).hexdigest()},
                  EDGE: {"rows": len(edge), "sha256": hashlib.sha256(edge_bytes).hexdigest()}},
        "label_counts": dict(sorted(Counter(r["label"] for r in rows).items())),
        "category_counts": dict(sorted(Counter(r["category"] for r in rows).items())),
        "language_counts": dict(sorted(Counter(r["language"] for r in rows).items())),
        "edge_case_counts": dict(sorted(Counter(r["case_type"] for r in edge).items())),
        "groups": len({r["group"] for r in rows}),
        "label_definitions": {
            "suspicious": "Template written to exhibit a financial social-engineering pattern.",
            "legitimate": "Template written to resemble routine, benign communication."},
        "intended_use": "Development, demonstration and functional/integration testing ONLY.",
        "separation": ("Not a Protocol v5 source; never a donated genuine alert (source SYN_DEV, never DONATION); "
                       "not part of any training pool, frozen test set, exclusion log or sealed manifest."),
        "limitations": [
            "Synthetic, template-generated text written by the project author; narrow, author-biased style.",
            "Does NOT represent the distribution of Indian financial alerts or of real scam traffic.",
            "Labels reflect template intent, not verified real-world outcomes.",
            "Metrics on this data are synthetic-only and must not be used to claim real-world performance "
            "or to select a model against the frozen test set.",
            "Edge-case 'expected_text_classifier_status' values depend on the model vocabulary.",
        ],
        "validation": report,
        "licence": "Created for this project; no third-party data (repository licence, MIT per pyproject.toml).",
    }


def build_bytes() -> Tuple[bytes, bytes, bytes]:
    rows, edge = generate()
    report = validate(rows, edge)
    lab, edg = to_csv(rows, LABELLED_FIELDS), to_csv(edge, EDGE_FIELDS)
    prov = (json.dumps(provenance(rows, edge, lab, edg, report), indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    return lab, edg, prov


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(OUT_DIR))
    ap.add_argument("--check", action="store_true", help="regenerate in memory and compare with the files on disk")
    args = ap.parse_args(argv)
    out = Path(args.out)
    lab, edg, prov = build_bytes()
    targets = {out / LABELLED: lab, out / EDGE: edg, out / PROVENANCE_FILE: prov}
    if args.check:
        bad = [p.name for p, b in targets.items() if not p.is_file() or p.read_bytes() != b]
        print("reproducible: " + ("YES" if not bad else f"NO ({bad})"))
        return 0 if not bad else 1
    out.mkdir(parents=True, exist_ok=True)
    for p, b in targets.items():
        p.write_bytes(b)
    print(json.dumps(json.loads(prov)["files"], indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
