"""
donation_progress.py — Privacy-safe validation and progress report for
donated genuine messages (protocol v3 minimum_legitimate_coverage)
=========================================================================

    python scripts/donation_progress.py                        # validate + counts
    python scripts/donation_progress.py --check-test-coverage  # + build dry run (counts only)

Prints and writes reports/donations/progress.json with COUNTS ONLY: by
pseudonymous donor code, category and sender type. Never prints message
text, and never writes the frozen test set (the coverage check uses the
builder's --dry-run path).

A record counts as a verified genuine message only if it passes every
check in validate_donations(); everything else is listed by record id and
reason and is NOT counted.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))

from satyalens_analysis.anonymise import ANONYMISER_VERSION, anonymise  # noqa: E402
from satyalens_analysis.text_classifier import normalise_text  # noqa: E402
from donate_messages import CATEGORIES, CONSENT_VERSIONS, DEFAULT_DONATIONS_DIR, SENDER_TYPES  # noqa: E402

FIELDS = ["id", "text", "label", "category", "sender_type", "donor_id", "source",
          "anonymiser_version", "consent_version"]
TARGET_MESSAGES = 750
TARGET_DONORS = 10
REPORT = REPO / "reports" / "donations" / "progress.json"


def validate_donations(donations_dir: Path = DEFAULT_DONATIONS_DIR) -> Tuple[List[Dict[str, str]], List[Dict[str, str]]]:
    """Return (verified_records, issues). Issues carry record id and reason only."""
    verified: List[Dict[str, str]] = []
    issues: List[Dict[str, str]] = []
    if not donations_dir.is_dir():
        return verified, issues
    ledger_path = donations_dir / "consent_ledger.csv"
    ledger: Dict[str, Dict[str, str]] = {}
    if ledger_path.is_file():
        with ledger_path.open(newline="", encoding="utf-8") as fh:
            ledger = {e["donor_id"]: e for e in csv.DictReader(fh)}
    for f in sorted(donations_dir.glob("donation_*.csv")):
        file_donor = f.stem[len("donation_"):]
        entry = ledger.get(file_donor)
        if not entry or entry.get("status") != "active" or entry.get("consent_version") not in CONSENT_VERSIONS:
            issues.append({"record": f.name, "reason": "donor not active with valid consent in ledger"})
            continue
        with f.open(newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            if reader.fieldnames != FIELDS:
                issues.append({"record": f.name, "reason": "unexpected columns (not produced by finalize)"})
                continue
            for r in reader:
                problem = _record_problem(r, file_donor)
                if problem:
                    issues.append({"record": r.get("id", "?"), "reason": problem})
                else:
                    verified.append(r)
    return verified, issues


def _record_problem(r: Dict[str, str], file_donor: str) -> Optional[str]:
    text = r.get("text") or ""
    if r.get("donor_id") != file_donor:
        return "donor_id does not match file"
    if not re.fullmatch(rf"DON-{re.escape(file_donor)}-\d{{4}}", r.get("id") or ""):
        return "id not in finalize format"
    if r.get("label") != "legitimate" or r.get("source") != "DONATION":
        return "label/source not legitimate/DONATION"
    if r.get("anonymiser_version") != ANONYMISER_VERSION:
        return "anonymiser version mismatch"
    if r.get("consent_version") not in CONSENT_VERSIONS:
        return "consent version invalid"
    if r.get("category") not in CATEGORIES or r.get("sender_type") not in SENDER_TYPES:
        return "invalid category or sender_type"
    if not text.strip():
        return "empty text"
    if anonymise(text) != text:
        return "text not in masked form (re-masking changes it)"
    if re.search(r"\d{4,}", text):
        return "contains a run of 4+ digits"
    return None


def progress(donations_dir: Path = DEFAULT_DONATIONS_DIR) -> Dict:
    verified, issues = validate_donations(donations_dir)
    by_donor: Dict[str, Counter] = defaultdict(Counter)
    for r in verified:
        by_donor[r["donor_id"]][r["category"]] += 1
    norm = Counter(normalise_text(r["text"]) for r in verified)
    donors = len(by_donor)
    cats = Counter(r["category"] for r in verified)
    return {
        "verified_messages": len(verified),
        "donors_with_verified_messages": donors,
        "by_donor": {d: {"total": sum(c.values()), "by_category": dict(c)} for d, c in sorted(by_donor.items())},
        "by_category": dict(cats),
        "by_sender_type": dict(Counter(r["sender_type"] for r in verified)),
        "duplicate_texts_after_normalisation": sum(v - 1 for v in norm.values() if v > 1),
        "rejected_records": len(issues),
        "rejection_reasons": dict(Counter(i["reason"] for i in issues)),
        "rejected_record_ids": [i["record"] for i in issues][:200],
        "collection_target": {"messages": TARGET_MESSAGES, "donors": TARGET_DONORS,
                              "met": len(verified) >= TARGET_MESSAGES and donors >= TARGET_DONORS},
        "note": "Counts only. Test coverage (>= 150 donated test messages from >= 5 donors, >= 3 categories "
                "with >= 25) is checked with --check-test-coverage via the builder dry run.",
    }


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--donations-dir", default=str(DEFAULT_DONATIONS_DIR))
    ap.add_argument("--check-test-coverage", action="store_true")
    args = ap.parse_args(argv)
    rep = progress(Path(args.donations_dir))
    if args.check_test_coverage:
        if not rep["collection_target"]["met"]:
            rep["test_coverage"] = "not checked: collection target not yet met"
        else:
            import build_pool_and_test_v3 as builder
            dry = builder.build(seal=False)
            rep["test_coverage"] = dry["coverage"]
            rep["dry_run_test_counts"] = dry["test_counts"]
    rep["ready_to_request_sealing_confirmation"] = bool(
        rep["collection_target"]["met"] and isinstance(rep.get("test_coverage"), dict)
        and rep["test_coverage"].get("met"))
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(rep, indent=2), encoding="utf-8")
    print(json.dumps(rep, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
