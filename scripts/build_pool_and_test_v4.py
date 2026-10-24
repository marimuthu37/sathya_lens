"""
build_pool_and_test_v4.py — Implements docs/protocol_v4.json (build_order, G4)
==============================================================================

    python scripts/build_pool_and_test_v4.py --dry-run   # all checks, counts only
    python scripts/build_pool_and_test_v4.py --seal      # writes pool + test, seals (needs approval)

Refuses to run unless docs/protocol_v4.json matches docs/protocol_v4.sha256.
Stops (exit 3) on: pinned-hash mismatch, any >= 4-digit run after anon-v2,
remaining overlap with the retired benchmark / challenge suite, or any
cross-split pair remaining after the G4 purge.

Reuses the verified protocol-v3 loaders where v4 rules are identical
(IMC25 parsing with the v4 label mapping, MultiWOZ, R115, synthetic, validated
donations). Reports contain counts only, never message text.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))

from satyalens_analysis import text_classifier as tc  # noqa: E402
from satyalens_analysis.anonymise import ANONYMISER_V2_VERSION, anonymise_v2  # noqa: E402
import build_pool_and_test_v3 as v3  # noqa: E402

PROTOCOL = REPO / "docs" / "protocol_v4.json"
PROTOCOL_HASH = REPO / "docs" / "protocol_v4.sha256"
MENDELEY_CSV = REPO / "dataset" / "message_detection" / "Financial scams detection dataset.csv"
POOL_OUT = REPO / "data" / "training_pool_v4.csv"
EXCLUDED_OUT = REPO / "data" / "v4_build.excluded.csv"
REPORT_DIR = REPO / "reports" / "build_v4"
MANIFEST = REPO / "reports" / "frozen_test_v4_manifest.json"
PRIORITY = ["DONATION", "IMC25", "MWOZ_TEST", "MWOZ_TRAIN", "MENDELEY", "R115", "SYN"]
TEST_ELIGIBLE = ("IMC25", "DONATION")
TOKENS = v3.TOKENS + ["handletoken"]
_NON_LATIN = re.compile(r"[ঀ-৿ऀ-ॣ०-ॿ]")
StopBuild = v3.StopBuild


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_protocol() -> Dict:
    recorded = PROTOCOL_HASH.read_text(encoding="utf-8").split()[0]
    if _sha(PROTOCOL) != recorded:
        raise StopBuild("Protocol v4 does not match its recorded SHA-256.")
    p = json.loads(PROTOCOL.read_text(encoding="utf-8"))
    p["_sha256"] = recorded
    return p


def verify_sources(p: Dict) -> Dict[str, str]:
    s = p["sources"]
    expected = {v3.IMC25_CSV: s["IMC25"]["file_sha256"], MENDELEY_CSV: s["MENDELEY"]["file_sha256"],
                v3.R115_CSV: s["R115"]["file_sha256"], v3.MWOZ_TRAIN: s["MWOZ_TRAIN"]["file_sha256"],
                v3.MWOZ_TEST: s["MWOZ_TEST"]["file_sha256"]}
    out = {}
    for path, exp in expected.items():
        if not path.is_file() or _sha(path) != exp:
            raise StopBuild(f"Pinned source missing or hash mismatch: {path.name}")
        out[str(path.relative_to(REPO)).replace("\\", "/")] = exp
    return out


def load_mendeley(p: Dict, excluded: List[Dict]) -> List[Dict]:
    mapping = p["sources"]["MENDELEY"]["label_mapping"]
    rows = []
    with MENDELEY_CSV.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        if [c.strip() for c in (reader.fieldnames or [])] != ["label", "message"]:
            raise StopBuild("Mendeley columns differ from the verified file.")
        for line_no, r in enumerate(reader, start=2):
            ref, label, text = f"line{line_no}", (r.get("label") or "").strip(), (r.get("message") or "").strip()
            if label not in mapping or not text:
                excluded.append({"source": "MENDELEY", "source_ref": ref, "outcome": "rejected",
                                 "reason": "invalid label or empty message"})
            elif _NON_LATIN.search(text):
                excluded.append({"source": "MENDELEY", "source_ref": ref, "outcome": "excluded_script",
                                 "reason": "non-Latin script (protocol v4 Q2)"})
            else:
                rows.append({"source": "MENDELEY", "key": ref, "raw": text, "label": mapping[label], "base": None,
                             "category": f"mendeley_{label}", "india_linked": False})
    return rows


def dedupe(rows: List[Dict], excluded: List[Dict]) -> List[Dict]:
    rows = sorted(rows, key=lambda r: PRIORITY.index(r["source"]))
    labels: Dict[str, set] = defaultdict(set)
    for r in rows:
        labels[tc.normalise_text(r["text"])].add(r["label"])
    seen: Dict[str, str] = {}
    kept = []
    for r in rows:
        k = tc.normalise_text(r["text"])
        if len(labels[k]) > 1:
            excluded.append({"source": r["source"], "source_ref": r["key"], "outcome": "ambiguous",
                             "reason": "identical normalised text with conflicting labels"})
        elif k in seen:
            excluded.append({"source": r["source"], "source_ref": r["key"], "outcome": "duplicate",
                             "reason": f"normalised duplicate of {seen[k]}"})
        else:
            seen[k] = f"{r['source']}:{r['key']}"
            kept.append(r)
    return kept


def _tokens(text: str) -> set:
    return set(tc.skeleton_text(text).split())


def atomic_units(rows: List[Dict], threshold: float = 0.8) -> Dict[str, Dict]:
    """G4 step 1: per source, single-link at Jaccard >= threshold + base keys."""
    stats = {}
    by_source: Dict[str, List[int]] = defaultdict(list)
    for i, r in enumerate(rows):
        by_source[r["source"]].append(i)
    for source, idxs in by_source.items():
        uf = v3._UF(len(idxs))
        sets = [_tokens(rows[i]["text"]) for i in idxs]
        for a, b in tc.jaccard_pairs(sets, threshold):
            uf.union(a, b)
        first: Dict[str, int] = {}
        for local, i in enumerate(idxs):
            base = rows[i]["base"]
            if base is not None:
                if base in first:
                    uf.union(local, first[base])
                else:
                    first[base] = local
        root_key: Dict[int, str] = {}
        for local, i in enumerate(idxs):
            root = uf.find(local)
            root_key[root] = min(root_key.get(root, rows[i]["key"]), rows[i]["key"])
        for local, i in enumerate(idxs):
            rows[i]["group"] = f"{source}:{root_key[uf.find(local)]}"
        sizes = Counter(rows[i]["group"] for i in idxs)
        stats[source] = {"rows": len(idxs), "units": len(sizes), "singletons": sum(1 for v in sizes.values() if v == 1),
                         "largest_10_units": [v for _, v in sizes.most_common(10)]}
    return stats


def assign(rows: List[Dict], salt: str, fraction: float = 0.20) -> Dict[str, Dict]:
    """G4 steps 2-3: giant units -> TRAIN; others hash-assigned; role-based sources."""
    stats = {}
    for source in TEST_ELIGIBLE:
        src_rows = [r for r in rows if r["source"] == source]
        if not src_rows:
            continue
        cap = max(50, int(0.01 * len(src_rows)))
        sizes = Counter(r["group"] for r in src_rows)
        giant = {g for g, n in sizes.items() if n > cap}
        for r in src_rows:
            if r["group"] in giant:
                r["split"] = "train"
                r["forced_train"] = True
            else:
                r["split"] = "test" if v3.assignment_u(salt, r["group"]) < fraction else "train"
        stats[source] = {"giant_cap_rows": cap, "forced_train_units": len(giant),
                         "forced_train_rows": sum(sizes[g] for g in giant)}
    for r in rows:
        if r["source"] == "MWOZ_TEST":
            r["split"] = "test"
        elif r["source"] not in TEST_ELIGIBLE:
            r["split"] = "train"
    return stats


def purge(rows: List[Dict], excluded: List[Dict], threshold: float = 0.6) -> Dict:
    """G4 step 4: remove TRAIN rows matching any TEST row (exact, skeleton, Jaccard >= threshold)."""
    test = [r for r in rows if r["split"] == "test"]
    train = [r for r in rows if r["split"] == "train"]
    test_norm = {tc.normalise_text(r["text"]) for r in test}
    test_skel = {tc.skeleton_text(r["text"]) for r in test} - {""}
    hits = tc.jaccard_matches([_tokens(r["text"]) for r in train], [_tokens(r["text"]) for r in test], threshold)
    purged = Counter()
    for r, hit in zip(train, hits):
        if hit or tc.normalise_text(r["text"]) in test_norm or tc.skeleton_text(r["text"]) in test_skel:
            r["split"] = "purged"
            purged[r["source"]] += 1
            excluded.append({"source": r["source"], "source_ref": r["key"], "outcome": "purged_near_test",
                             "reason": f"train row within Jaccard >= {threshold} (or exact/skeleton) of a test row"})
    remaining = [r for r in rows if r["split"] == "train"]
    final = tc.jaccard_matches([_tokens(r["text"]) for r in remaining], [_tokens(r["text"]) for r in test], threshold)
    exact_left = sum(1 for r in remaining if tc.normalise_text(r["text"]) in test_norm
                     or tc.skeleton_text(r["text"]) in test_skel)
    if any(final) or exact_left:
        raise StopBuild("Cross-split near-duplicates remain after the G4 purge.")
    return {"purged_rows_by_source": dict(purged), "purged_rows_total": sum(purged.values()),
            "final_cross_split_pairs_jaccard_ge_0_6": 0, "final_exact_or_skeleton_matches": 0}


def build(seal: bool) -> Dict:
    p = load_protocol()
    hashes = verify_sources(p)
    excluded: List[Dict] = []

    imc25, digits = v3.load_imc25(p, excluded)       # v4 label mapping incl. 'hey mum/dad'
    mendeley = load_mendeley(p, excluded)
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

    rows = dedupe(rows, excluded)
    unit_stats = atomic_units(rows)
    assign_stats = assign(rows, p["test_assignment"]["salt"], p["test_assignment"]["fraction"])
    purge_stats = purge(rows, excluded)

    train = [r for r in rows if r["split"] == "train"]
    test = [r for r in rows if r["split"] == "test"]
    imc_all = [r for r in rows if r["source"] == "IMC25"]
    imc_test = [r for r in test if r["source"] == "IMC25"]
    test_units = Counter(r["group"] for r in test)
    largest = test_units.most_common(1)[0] if test_units else (None, 0)
    report = {
        "protocol": "v4", "protocol_sha256": p["_sha256"], "anonymiser": ANONYMISER_V2_VERSION,
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
        "placeholder_token_rates_pool": _token_rates(train),
        "privacy_check": "no >= 4-digit runs in any anonymised row",
    }
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    (REPORT_DIR / ("build_report.json" if seal else "build_report_dry_run.json")).write_text(
        json.dumps(report, indent=2), encoding="utf-8")
    if seal:
        if MANIFEST.exists():
            raise StopBuild("A v4 sealed manifest already exists; refusing to overwrite.")
        _write_outputs(p, train, test, excluded, report)
    return report


def _token_rates(rows) -> Dict:
    agg: Dict[str, Counter] = defaultdict(Counter)
    n: Counter = Counter()
    for r in rows:
        k = f"{r['source']}|{r['label']}"
        n[k] += 1
        words = set(r["text"].split())
        for t in TOKENS:
            agg[k][t] += t in words
    return {k: {t: round(agg[k][t] / n[k], 4) for t in TOKENS} for k in sorted(n)}


def _write_outputs(p: Dict, train: List[Dict], test: List[Dict], excluded: List[Dict], report: Dict) -> None:
    with POOL_OUT.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["id", "text", "label", "group", "source", "source_ref", "category", "india_linked"])
        w.writeheader()
        for i, r in enumerate(train):
            w.writerow({"id": f"P4-{i:06d}", "text": r["text"], "label": r["label"], "group": r["group"],
                        "source": r["source"], "source_ref": r["key"], "category": r["category"],
                        "india_linked": int(r["india_linked"])})
    POOL_OUT.with_suffix(".provenance.json").write_text(json.dumps({
        "name": "satyalens-training-pool", "version": "4", "synthetic": "partial", "protocol_sha256": p["_sha256"],
        "anonymiser": ANONYMISER_V2_VERSION,
        "sources": {k: {kk: v.get(kk) for kk in ("title", "licence", "attribution", "commit", "revision", "real",
                                                 "label_limitations")} if isinstance(v, dict) else v
                    for k, v in p["sources"].items()},
        "rows": len(train), "csv_sha256": _sha(POOL_OUT)}, indent=2), encoding="utf-8")
    test_path = REPO / p["sealing"]["test_file"].split(" ")[0]
    with test_path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["id", "text", "label", "source", "group", "category", "india_linked", "donor"])
        w.writeheader()
        for i, r in enumerate(test):
            w.writerow({"id": f"T4-{i:06d}", "text": r["text"], "label": r["label"], "source": r["source"],
                        "group": r["group"], "category": r["category"], "india_linked": int(r["india_linked"]),
                        "donor": r.get("donor", "")})
    with EXCLUDED_OUT.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["source", "source_ref", "outcome", "reason"])
        w.writeheader()
        w.writerows(excluded)
    MANIFEST.write_text(json.dumps({
        "protocol_file": "docs/protocol_v4.json", "protocol_sha256": p["_sha256"],
        "sealed_files": [{"path": str(test_path.relative_to(REPO)).replace("\\", "/"), "sha256": _sha(test_path)}],
        "evaluation_log": "reports/frozen_test_v4_evaluations.jsonl",
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
