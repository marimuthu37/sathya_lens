"""
donate_messages.py — Local masking, donor review and withdrawal for
voluntarily donated GENUINE messages (consent form: docs/donation/CONSENT_FORM.md)

Runs entirely on the donor's machine. Nothing is uploaded.

Workflow
--------
1. python scripts/donate_messages.py new-donor-id
       -> prints a random pseudonymous id (e.g. d3f9a1c07). No name is stored.
2. Donor copies GENUINE messages they personally received from institutions
   (banks, payment apps, merchants, couriers, utilities) into a CSV OUTSIDE
   this repository with exactly the columns: text,category,sender_type
   (template: docs/donation/donation_input_template.csv). No sender numbers,
   timestamps, contact names or personal chats.
3. python scripts/donate_messages.py mask --input C:/Users/me/raw.csv --donor-id d3f9a1c07
       -> writes <input>.review.csv next to the raw file: masked text + PII
          warnings + empty 'decision' and 'redact' columns. Raw text is NOT
          copied into the review file.
4. Donor reviews EVERY row against their phone and fills:
       decision = approve | approve-checked | reject
                  (approve-checked = warnings were checked and are false alarms)
       redact   = words to delete, separated by ';' (e.g. names, places)
       The donor may also edit masked_text directly to delete content.
5. python scripts/donate_messages.py finalize --review C:/Users/me/raw.review.csv \
           --donor-id d3f9a1c07 --consent-version CONSENT-v1 --consent-confirmed
       -> re-masks, re-checks, writes data/donations/donation_<id>.csv
          (gitignored) and updates data/donations/consent_ledger.csv.
6. Donor deletes the raw CSV (the tool prints a reminder).

Withdrawal
----------
    python scripts/donate_messages.py withdraw --donor-id d3f9a1c07
deletes that donor's donation file, marks the ledger entry withdrawn, and
lists datasets that must be rebuilt (see docs/donation/DATA_HANDLING.md).
"""

from __future__ import annotations

import argparse
import csv
import re
import secrets
import sys
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from satyalens_analysis.anonymise import ANONYMISER_VERSION, anonymise, pii_warnings  # noqa: E402

INPUT_COLUMNS = ["text", "category", "sender_type"]
CATEGORIES = {
    "otp_alert", "account_debit_credit_alert", "upi_payment_confirmation", "card_transaction_alert",
    "delivery_notice", "bill_or_due_reminder", "bank_service_notice", "other_institutional_financial",
}
SENDER_TYPES = {
    "bank", "payment_app", "merchant_or_ecommerce", "utility_or_telecom", "courier", "government",
    "insurer_or_investment", "other_institution",
}
DECISIONS = {"approve", "approve-checked", "reject"}
CONSENT_VERSIONS = {"CONSENT-v1"}
MAX_MESSAGES_PER_DONOR = 150
MAX_CHARS = 640
DONOR_ID = re.compile(r"^d[0-9a-f]{8}$")
DEFAULT_DONATIONS_DIR = REPO / "data" / "donations"
LEDGER_COLUMNS = ["donor_id", "consent_version", "recorded_on", "messages", "status", "withdrawn_on"]


class DonationError(ValueError):
    pass


def new_donor_id() -> str:
    return "d" + secrets.token_hex(4)


def _check_donor_id(donor_id: str) -> None:
    if not DONOR_ID.match(donor_id or ""):
        raise DonationError("donor id must look like 'd' + 8 hex characters; use `new-donor-id`")


def _inside_repo(path: Path) -> bool:
    try:
        path.resolve().relative_to(REPO.resolve())
        return True
    except ValueError:
        return False


def mask(input_path: Path, donor_id: str) -> Path:
    """Read raw donor CSV (outside the repo) and write a review CSV beside it."""
    _check_donor_id(donor_id)
    if _inside_repo(input_path):
        raise DonationError("Raw messages must stay OUTSIDE the repository; move the input file.")
    if not input_path.is_file():
        raise DonationError(f"Input not found: {input_path}")
    with input_path.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        if [c.strip() for c in (reader.fieldnames or [])] != INPUT_COLUMNS:
            raise DonationError(f"Input must have exactly the columns {INPUT_COLUMNS} "
                                f"(found {reader.fieldnames}); do not include sender numbers or dates.")
        rows = list(reader)
    if not rows:
        raise DonationError("Input contains no messages.")
    if len(rows) > MAX_MESSAGES_PER_DONOR:
        raise DonationError(f"At most {MAX_MESSAGES_PER_DONOR} messages per donor (data minimisation).")
    problems = []
    out_rows = []
    for i, r in enumerate(rows, start=2):
        text, cat, snd = (r.get("text") or "").strip(), (r.get("category") or "").strip(), (r.get("sender_type") or "").strip()
        if not text:
            problems.append(f"line {i}: empty text")
        if len(text) > MAX_CHARS:
            problems.append(f"line {i}: longer than {MAX_CHARS} characters")
        if cat not in CATEGORIES:
            problems.append(f"line {i}: category {cat!r} not in {sorted(CATEGORIES)}")
        if snd not in SENDER_TYPES:
            problems.append(f"line {i}: sender_type {snd!r} not in {sorted(SENDER_TYPES)}")
        masked = anonymise(text)
        out_rows.append({"review_id": f"R{i - 1:04d}", "category": cat, "sender_type": snd,
                         "masked_text": masked, "warnings": " | ".join(pii_warnings(masked)),
                         "decision": "", "redact": ""})
    if problems:
        raise DonationError("Fix the input first: " + "; ".join(problems[:20]))
    review = input_path.with_suffix(".review.csv")
    with review.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(out_rows[0]))
        w.writeheader()
        w.writerows(out_rows)
    return review


def _apply_redactions(text: str, redact: str) -> str:
    for word in [w.strip() for w in redact.split(";") if w.strip()]:
        text = re.sub(rf"\b{re.escape(word)}\b", " ", text, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", text).strip()


def _read_ledger(ledger: Path) -> List[Dict[str, str]]:
    if not ledger.is_file():
        return []
    with ledger.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _write_ledger(ledger: Path, rows: List[Dict[str, str]]) -> None:
    with ledger.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=LEDGER_COLUMNS)
        w.writeheader()
        w.writerows(rows)


def finalize(review_path: Path, donor_id: str, consent_version: str, consent_confirmed: bool,
             donations_dir: Path = DEFAULT_DONATIONS_DIR, replace: bool = False) -> Dict[str, int]:
    """Validate the donor's review and write the masked donation file."""
    _check_donor_id(donor_id)
    if not consent_confirmed or consent_version not in CONSENT_VERSIONS:
        raise DonationError(f"A signed consent form ({sorted(CONSENT_VERSIONS)}) must be confirmed "
                            "with --consent-confirmed before finalising.")
    with review_path.open(newline="", encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))
    undecided = [r["review_id"] for r in rows if (r.get("decision") or "").strip() not in DECISIONS]
    if undecided:
        raise DonationError(f"Every row needs decision approve | approve-checked | reject; "
                            f"missing/invalid for {undecided[:20]}")
    accepted, counts = [], {"approved": 0, "rejected_by_donor": 0, "rejected_unresolved_warnings": 0}
    for r in rows:
        decision = r["decision"].strip()
        if decision == "reject":
            counts["rejected_by_donor"] += 1
            continue
        text = anonymise(_apply_redactions(r["masked_text"], r.get("redact") or ""))
        if not text or r["category"] not in CATEGORIES or r["sender_type"] not in SENDER_TYPES:
            counts["rejected_by_donor"] += 1
            continue
        if pii_warnings(text) and decision != "approve-checked":
            counts["rejected_unresolved_warnings"] += 1
            continue
        counts["approved"] += 1
        accepted.append({"id": f"DON-{donor_id}-{len(accepted) + 1:04d}", "text": text, "label": "legitimate",
                         "category": r["category"], "sender_type": r["sender_type"], "donor_id": donor_id,
                         "source": "DONATION", "anonymiser_version": ANONYMISER_VERSION,
                         "consent_version": consent_version})
    donations_dir.mkdir(parents=True, exist_ok=True)
    out = donations_dir / f"donation_{donor_id}.csv"
    if out.exists() and not replace:
        raise DonationError(f"{out.name} already exists; pass --replace to overwrite.")
    fields = ["id", "text", "label", "category", "sender_type", "donor_id", "source",
              "anonymiser_version", "consent_version"]
    with out.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        w.writerows(accepted)
    ledger = donations_dir / "consent_ledger.csv"
    entries = [e for e in _read_ledger(ledger) if e["donor_id"] != donor_id]
    entries.append({"donor_id": donor_id, "consent_version": consent_version,
                    "recorded_on": date.today().isoformat(), "messages": str(len(accepted)),
                    "status": "active", "withdrawn_on": ""})
    _write_ledger(ledger, entries)
    return counts


def withdraw(donor_id: str, donations_dir: Path = DEFAULT_DONATIONS_DIR) -> List[str]:
    """Delete a donor's data and mark the ledger. Returns follow-up actions."""
    _check_donor_id(donor_id)
    out = donations_dir / f"donation_{donor_id}.csv"
    deleted = out.exists()
    if deleted:
        out.unlink()
    ledger = donations_dir / "consent_ledger.csv"
    entries = _read_ledger(ledger)
    found = False
    for e in entries:
        if e["donor_id"] == donor_id:
            e.update(status="withdrawn", withdrawn_on=date.today().isoformat(), messages="0")
            found = True
    if not found:
        entries.append({"donor_id": donor_id, "consent_version": "", "recorded_on": "", "messages": "0",
                        "status": "withdrawn", "withdrawn_on": date.today().isoformat()})
    _write_ledger(ledger, entries)
    return [
        f"donation file {'deleted' if deleted else 'not present'}: {out.name}",
        "rebuild data/training_pool_v3.csv without this donor (scripts/prepare_training_pool_v3.py)",
        "if the frozen test v3 is already sealed and contains this donor: follow the withdrawal rule in "
        "docs/protocol_v3.json (rebuild and reseal before final evaluation; after final evaluation, "
        "delete the data and annotate the report)",
        "delete any local copies, review files and the raw input file",
    ]


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Mask, review and withdraw donated genuine messages (local only).")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("new-donor-id")
    m = sub.add_parser("mask")
    m.add_argument("--input", required=True)
    m.add_argument("--donor-id", required=True)
    f = sub.add_parser("finalize")
    f.add_argument("--review", required=True)
    f.add_argument("--donor-id", required=True)
    f.add_argument("--consent-version", required=True)
    f.add_argument("--consent-confirmed", action="store_true")
    f.add_argument("--donations-dir", default=str(DEFAULT_DONATIONS_DIR))
    f.add_argument("--replace", action="store_true")
    w = sub.add_parser("withdraw")
    w.add_argument("--donor-id", required=True)
    w.add_argument("--donations-dir", default=str(DEFAULT_DONATIONS_DIR))
    args = ap.parse_args(argv)
    try:
        if args.cmd == "new-donor-id":
            print(new_donor_id())
        elif args.cmd == "mask":
            review = mask(Path(args.input), args.donor_id)
            print(f"Review file: {review}\nReview EVERY row (decision + redact), then run `finalize`.")
        elif args.cmd == "finalize":
            counts = finalize(Path(args.review), args.donor_id, args.consent_version, args.consent_confirmed,
                              Path(args.donations_dir), args.replace)
            print(counts)
            print("Now delete your raw input CSV and the review file.")
        else:
            for line in withdraw(args.donor_id, Path(args.donations_dir)):
                print("-", line)
    except DonationError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
