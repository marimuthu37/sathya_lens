"""
build_pool_and_test_v5.py — Implements docs/protocol_v5.json (build_order, G4)
==============================================================================

    python scripts/build_pool_and_test_v5.py --dry-run   # all checks, counts only
    python scripts/build_pool_and_test_v5.py --seal      # writes pool + test, seals (needs approval)

Pinned to protocol v5 SHA-256 3889099d1da7fa6cdc987685a2dacda4592508339bd8cedb91a99fa29f25f3ee
and refuses to run unless docs/protocol_v5.json matches the pin and its .sha256.

Data construction is identical to protocol v4 (v5 decision Q9): every build
step calls the unchanged v4 functions (verify_sources, load_mendeley, dedupe,
atomic_units, assign, purge) with the same salt and fraction, so the dry-run
counts must equal the reviewed v4 dry run. Only the protocol pin, the v5 file
names, and the manifest fields (protocol_file / evaluation_log taken from the
protocol's sealing section; pool_counts) differ. v3/v4 builders are unchanged.

Stops (exit 3) on: pinned-hash mismatch, any >= 4-digit run after anon-v2,
remaining overlap with the retired benchmark / challenge suite, or any
cross-split pair remaining after the G4 purge. Reports contain counts only.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))

from satyalens_analysis import text_classifier as tc  # noqa: E402
from satyalens_analysis.anonymise import ANONYMISER_V2_VERSION, anonymise_v2  # noqa: E402
import build_pool_and_test_v3 as v3  # noqa: E402
import build_pool_and_test_v4 as v4  # noqa: E402

PROTOCOL_V5_SHA256 = "3889099d1da7fa6cdc987685a2dacda4592508339bd8cedb91a99fa29f25f3ee"
PROTOCOL = REPO / "docs" / "protocol_v5.json"
PROTOCOL_HASH = REPO / "docs" / "protocol_v5.sha256"
POOL_OUT = REPO / "data" / "training_pool_v5.csv"
EXCLUDED_OUT = REPO / "data" / "v5_build.excluded.csv"
REPORT_DIR = REPO / "reports" / "build_v5"
MANIFEST = REPO / "reports" / "frozen_test_v5_manifest.json"
StopBuild = v3.StopBuild


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_protocol() -> Dict:
    for f in (PROTOCOL, PROTOCOL_HASH):
        if not f.is_file():
            raise StopBuild(f"Protocol file not found: {f.name}")
    recorded = PROTOCOL_HASH.read_text(encoding="utf-8").split()[0]
    if _sha(PROTOCOL) != PROTOCOL_V5_SHA256 or recorded != PROTOCOL_V5_SHA256:
        raise StopBuild("Protocol v5 does not match the pinned SHA-256 (or its .sha256 record).")
    p = json.loads(PROTOCOL.read_text(encoding="utf-8"))
    p["_sha256"] = PROTOCOL_V5_SHA256
    return p


def sealing_paths(p: Dict) -> Dict[str, str]:
    """protocol_file, test file and evaluation log exactly as protocol 'sealing' names them."""
    s = p["sealing"]
    found = {k: re.search(rf"{k} '([^']+)'", s["manifest"]) for k in ("protocol_file", "evaluation_log")}
    if not all(found.values()):
        raise StopBuild("Protocol sealing.manifest does not name protocol_file and evaluation_log.")
    return {"test_file": s["test_file"].split(" ")[0], **{k: m.group(1) for k, m in found.items()}}


def _refuse_if_sealed() -> None:
    """A second --seal must change nothing: refuse BEFORE any output (report included) is written."""
    if MANIFEST.exists():
        raise StopBuild("A v5 sealed manifest already exists; refusing to overwrite.")


def build(seal: bool) -> Dict:
    if seal:
        _refuse_if_sealed()                          # fail fast, before reading any source
    p = load_protocol()
    paths = sealing_paths(p)
    hashes = v4.verify_sources(p)
    excluded: List[Dict] = []

    imc25, digits = v3.load_imc25(p, excluded)       # label mapping incl. 'hey mum/dad' (unchanged from v4)
    mendeley = v4.load_mendeley(p, excluded)
    men_digits = sum(1 for r in mendeley if re.search(r"\d{10,}", r["raw"]))
    rows = (v3.load_donations(excluded) + imc25
            + v3.load_mwoz(v3.MWOZ_TEST, "MWOZ_TEST", 150, 20261025)
            + v3.load_mwoz(v3.MWOZ_TRAIN, "MWOZ_TRAIN", 300, 20261024)
            + mendeley + v3.load_r115(p, excluded) + v3.load_syn())
    loaded = v3.counts(rows) | {"MENDELEY": dict(Counter(r["label"] for r in mendeley))}

    for r in rows:
        r["text"] = anonymise_v2(r["raw"])
    for r in [r for r in rows if not r["text"]]:
        excluded.append({"source": r["source"], "source_ref": r["key"], "outcome": "rejected",
                         "reason": "empty after anonymisation"})
    rows = [r for r in rows if r["text"]]
    v3.privacy_check(rows)

    protected = [anonymise_v2(e.text) for e in tc.load_evaluation_dataset(v3.RETIRED)] + \
                [anonymise_v2(e.text) for e in tc.load_evaluation_dataset(v3.CHALLENGE)]
    res = tc.find_training_overlap(list(enumerate(r["text"] for r in rows)), protected)
    hit = {i: k for k in ("exact_normalised", "skeleton", "token_jaccard") for i in res[k]}
    for i, r in enumerate(rows):
        if i in hit:
            excluded.append({"source": r["source"], "source_ref": r["key"], "outcome": "retired_or_challenge_overlap",
                             "reason": f"overlaps retired benchmark or challenge suite ({hit[i]})"})
    rows = [r for i, r in enumerate(rows) if i not in hit]
    if tc.find_training_overlap(list(enumerate(r["text"] for r in rows)), protected)["total"]:
        raise StopBuild("Overlap with retired benchmark / challenge suite remains.")

    rows = v4.dedupe(rows, excluded)
    unit_stats = v4.atomic_units(rows)
    assign_stats = v4.assign(rows, p["test_assignment"]["salt"], p["test_assignment"]["fraction"])
    purge_stats = v4.purge(rows, excluded)

    train = [r for r in rows if r["split"] == "train"]
    test = [r for r in rows if r["split"] == "test"]
    imc_all = [r for r in rows if r["source"] == "IMC25"]
    imc_test = [r for r in test if r["source"] == "IMC25"]
    test_units = Counter(r["group"] for r in test)
    largest = test_units.most_common(1)[0] if test_units else (None, 0)
    report = {
        "protocol": "v5", "protocol_sha256": p["_sha256"], "anonymiser": ANONYMISER_V2_VERSION,
        "built_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"), "dry_run": not seal,
        "source_hashes": hashes, "loaded_counts": loaded,
        "imc25_long_digit_investigation": digits, "mendeley_rows_with_10plus_digit_runs": men_digits,
        "excluded_outcomes": dict(Counter(e["outcome"] for e in excluded)),
        "excluded_by_source_and_outcome": {f"{k[0]}|{k[1]}": v for k, v in
                                           sorted(Counter((e["source"], e["outcome"]) for e in excluded).items())},
        "grouping_G4": {"atomic_units": unit_stats, "assignment": assign_stats, "purge": purge_stats,
                        "largest_test_unit_rows": largest[1],
                        "largest_test_unit_share_of_test": round(largest[1] / len(test), 4) if test else None,
                        "imc25_test_share_of_rows_after_dedup": round(len(imc_test) / len(imc_all), 4) if imc_all else None},
        "pool_counts": v3.counts(train) | ({"MENDELEY": dict(Counter(r["label"] for r in train if r["source"] == "MENDELEY"))}
                                           if any(r["source"] == "MENDELEY" for r in train) else {}),
        "pool_units": len({r["group"] for r in train}),
        "test_counts": v3.counts(test), "test_units": len(test_units),
        "test_india_linked_suspicious": sum(1 for r in imc_test if r["india_linked"]),
        "test_hey_mum_dad_rows": sum(1 for r in imc_test if r["category"] == "hey mum/dad"),
        "coverage": v3.coverage(p, test),
        "placeholder_token_rates_pool": v4._token_rates(train),
        "privacy_check": "no >= 4-digit runs in any anonymised row",
    }
    if seal:
        _refuse_if_sealed()                          # re-check before the first write (manifest may have appeared)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    (REPORT_DIR / ("build_report.json" if seal else "build_report_dry_run.json")).write_text(
        json.dumps(report, indent=2), encoding="utf-8")
    if seal:
        _write_outputs(p, paths, train, test, excluded, report)
    return report


def _write_outputs(p: Dict, paths: Dict[str, str], train: List[Dict], test: List[Dict], excluded: List[Dict],
                   report: Dict) -> None:
    with POOL_OUT.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["id", "text", "label", "group", "source", "source_ref", "category", "india_linked"])
        w.writeheader()
        for i, r in enumerate(train):
            w.writerow({"id": f"P5-{i:06d}", "text": r["text"], "label": r["label"], "group": r["group"],
                        "source": r["source"], "source_ref": r["key"], "category": r["category"],
                        "india_linked": int(r["india_linked"])})
    POOL_OUT.with_suffix(".provenance.json").write_text(json.dumps({
        "name": "satyalens-training-pool", "version": "5", "synthetic": "partial", "protocol_sha256": p["_sha256"],
        "anonymiser": ANONYMISER_V2_VERSION,
        "sources": {k: {kk: v.get(kk) for kk in ("title", "licence", "attribution", "commit", "revision", "real",
                                                 "label_limitations")} if isinstance(v, dict) else v
                    for k, v in p["sources"].items()},
        "rows": len(train), "csv_sha256": _sha(POOL_OUT)}, indent=2), encoding="utf-8")
    test_path = REPO / paths["test_file"]
    with test_path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["id", "text", "label", "source", "group", "category", "india_linked", "donor"])
        w.writeheader()
        for i, r in enumerate(test):
            w.writerow({"id": f"T5-{i:06d}", "text": r["text"], "label": r["label"], "source": r["source"],
                        "group": r["group"], "category": r["category"], "india_linked": int(r["india_linked"]),
                        "donor": r.get("donor", "")})
    with EXCLUDED_OUT.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["source", "source_ref", "outcome", "reason"])
        w.writeheader()
        w.writerows(excluded)
    MANIFEST.write_text(json.dumps({
        "protocol_file": paths["protocol_file"], "protocol_sha256": p["_sha256"],
        "sealed_files": [{"path": paths["test_file"], "sha256": _sha(test_path)}],
        "evaluation_log": paths["evaluation_log"],
        "sealed_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "test_counts": report["test_counts"], "test_units": report["test_units"], "coverage": report["coverage"],
        # training label counts: acceptance B1 takes its majority class from these, never from test labels
        "pool_counts": report["pool_counts"],
        "pool_csv_sha256": _sha(POOL_OUT)}, indent=2), encoding="utf-8")


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--seal", action="store_true")
    args = ap.parse_args(argv)
    try:
        report = build(seal=args.seal)
    except StopBuild as exc:
        print(f"STOP: {exc}", file=sys.stderr)
        return 3
    print(json.dumps({k: report[k] for k in ("dry_run", "protocol_sha256", "loaded_counts",
                                             "imc25_long_digit_investigation", "mendeley_rows_with_10plus_digit_runs",
                                             "excluded_outcomes", "grouping_G4", "pool_counts", "pool_units",
                                             "test_counts", "test_units", "test_india_linked_suspicious",
                                             "test_hey_mum_dad_rows", "coverage")}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
