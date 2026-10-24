"""
build_pool_and_test_v3.py — Implements docs/protocol_v3.json build_order 1-10
=============================================================================

    python scripts/build_pool_and_test_v3.py --dry-run   # all checks, counts only
    python scripts/build_pool_and_test_v3.py --seal      # writes pool + test, seals

Refuses to run unless docs/protocol_v3.json matches docs/protocol_v3.sha256.
Stops (exit 3) on any integrity, privacy or leakage failure:
  * a pinned source hash mismatch;
  * any run of >= 4 digits left in an output text after anonymisation;
  * any remaining overlap with the retired benchmark / challenge suite;
  * any remaining cross-split leakage after train-group removal.

The build report (reports/build_v3/build_report.json) contains counts only —
never message text. Exclusions are logged by source reference and reason in
data/v3_build.excluded.csv (gitignored).
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from satyalens_analysis.anonymise import ANONYMISER_VERSION, anonymise  # noqa: E402
from satyalens_analysis import text_classifier as tc  # noqa: E402

PROTOCOL = REPO / "docs" / "protocol_v3.json"
PROTOCOL_HASH = REPO / "docs" / "protocol_v3.sha256"
RAW = REPO / "data" / "raw"
IMC25_CSV = RAW / "imc25_smishing" / "final_dataset_output.csv"
R115_CSV = RAW / "ridham115_indian_scam_sms" / "synthetic_audited.csv"
MWOZ_TRAIN = RAW / "multiwoz_2_2" / "dialogues_001.json"
MWOZ_TEST = RAW / "multiwoz_2_2" / "dialogues_017.json"
SYN_CSV = REPO / "data" / "synthetic_financial_messages_v1.csv"
RETIRED = REPO / "data" / "real_world_test_v1.csv"
CHALLENGE = REPO / "data" / "challenge_suite_v1.csv"
DONATIONS = REPO / "data" / "donations"
POOL_OUT = REPO / "data" / "training_pool_v3.csv"
EXCLUDED_OUT = REPO / "data" / "v3_build.excluded.csv"
REPORT_DIR = REPO / "reports" / "build_v3"
MANIFEST = REPO / "reports" / "frozen_test_v3_manifest.json"
PRIORITY = ["DONATION", "IMC25", "MWOZ_TEST", "MWOZ_TRAIN", "R115", "SYN"]
TOKENS = ["urltoken", "phonetoken", "emailtoken", "upitoken", "datetoken", "numtoken"]


class StopBuild(RuntimeError):
    """Integrity, privacy or leakage failure — the build must stop."""


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_protocol() -> Dict:
    recorded = PROTOCOL_HASH.read_text(encoding="utf-8").split()[0]
    if _sha(PROTOCOL) != recorded:
        raise StopBuild("Protocol file does not match its recorded SHA-256.")
    p = json.loads(PROTOCOL.read_text(encoding="utf-8"))
    p["_sha256"] = recorded
    return p


def verify_sources(p: Dict) -> Dict[str, str]:
    expected = {IMC25_CSV: p["sources"]["IMC25"]["file_sha256"],
                R115_CSV: p["sources"]["R115"]["file_sha256"],
                MWOZ_TRAIN: p["sources"]["MWOZ_TRAIN"]["file_sha256"]}
    hashes = {}
    for path, exp in expected.items():
        if not path.is_file() or _sha(path) != exp:
            raise StopBuild(f"Pinned source missing or hash mismatch: {path.relative_to(REPO)}")
        hashes[str(path.relative_to(REPO))] = exp
    if not MWOZ_TEST.is_file():
        raise StopBuild("MultiWOZ test file dialogues_017.json not downloaded.")
    hashes[str(MWOZ_TEST.relative_to(REPO))] = _sha(MWOZ_TEST)   # recorded at download time per protocol
    return hashes


# ---------------------------------------------------------------------------
# Loading (step 2) and digit investigation (step 3)
# ---------------------------------------------------------------------------

def classify_long_digits(text: str) -> List[str]:
    kinds = []
    for m in re.finditer(r"\d{10,}", text):
        run, before = m.group(0), text[max(0, m.start() - 2):m.start()]
        if "+" in before:
            kinds.append("plus_country_code")
        elif len(run) == 10 and run[0] in "6789":
            kinds.append("indian_mobile_10_digit")
        elif run[0] == "0" and 10 <= len(run) <= 12:
            kinds.append("zero_prefixed_landline_or_uk_mobile")
        else:
            kinds.append("other_id_or_number")
    return kinds


def load_imc25(p: Dict, excluded: List[Dict]) -> Tuple[List[Dict], Dict]:
    src = p["sources"]["IMC25"]
    mapping = src["label_mapping"]
    indian = set(src["INDIAN_INSTITUTIONS"])
    csv.field_size_limit(10 ** 8)
    rows, digit_kinds, rows_with_long = [], Counter(), 0
    with IMC25_CSV.open(encoding="utf-8", errors="replace", newline="") as fh:
        for line_no, r in enumerate(csv.DictReader(fh), start=2):
            ref = f"row{line_no}"
            text = (r.get("text") or "").strip()
            stype = (r.get("scam_type") or "").strip()
            if not text:
                excluded.append({"source": "IMC25", "source_ref": ref, "outcome": "rejected", "reason": "empty text"})
                continue
            if stype not in mapping:
                excluded.append({"source": "IMC25", "source_ref": ref, "outcome": "ambiguous",
                                 "reason": f"scam_type {stype or '<empty>'!r} excluded as ambiguous"})
                continue
            if (r.get("language") or "").strip() != "English":
                excluded.append({"source": "IMC25", "source_ref": ref, "outcome": "excluded_language",
                                 "reason": "non-English (protocol language filter)"})
                continue
            kinds = classify_long_digits(text)
            if kinds:
                rows_with_long += 1
                digit_kinds.update(kinds)
            rows.append({"source": "IMC25", "key": ref, "raw": text, "label": mapping[stype],
                         "base": None, "category": stype,
                         "india_linked": (r.get("original_network_country") == "IND"
                                          or (r.get("named_entity") or "").strip() in indian)})
    return rows, {"rows_with_10plus_digit_runs": rows_with_long, "runs_by_kind": dict(digit_kinds)}


def load_donations(excluded: List[Dict]) -> List[Dict]:
    """Only records passing donation_progress.validate_donations are used."""
    sys.path.insert(0, str(REPO / "scripts"))
    from donation_progress import validate_donations
    verified, issues = validate_donations(DONATIONS)
    for i in issues:
        excluded.append({"source": "DONATION", "source_ref": i["record"], "outcome": "unverified_donation",
                         "reason": i["reason"]})
    return [{"source": "DONATION", "key": r["id"], "raw": r["text"], "label": "legitimate",
             "base": f"{r['donor_id']}:{r['sender_type']}", "category": r["category"],
             "donor": r["donor_id"], "india_linked": False} for r in verified]


def load_mwoz(path: Path, source: str, n: int, seed: int) -> List[Dict]:
    dialogues = sorted(json.loads(path.read_text(encoding="utf-8")), key=lambda d: d["dialogue_id"])
    rng = random.Random(seed)
    rng.shuffle(dialogues)
    rows = []
    for d in dialogues[:n]:
        for speaker in ("USER", "SYSTEM"):
            turns = [t["utterance"].strip() for t in d["turns"]
                     if t["speaker"] == speaker and len(t["utterance"].split()) >= 5]
            if turns:
                rows.append({"source": source, "key": f"{d['dialogue_id']}:{speaker}", "raw": rng.choice(turns),
                             "label": "legitimate", "base": d["dialogue_id"], "category": "conversation",
                             "india_linked": False})
    return rows


def load_r115(p: Dict, excluded: List[Dict]) -> List[Dict]:
    gens = set(p["sources"]["R115"]["generators_used"])
    mapping = p["sources"]["R115"]["label_mapping"]
    rows = []
    with R115_CSV.open(newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            if r["generator"] not in gens:
                excluded.append({"source": "R115", "source_ref": r["row_id"], "outcome": "excluded_generator",
                                 "reason": "generator not in protocol list"})
                continue
            rows.append({"source": "R115", "key": r["row_id"], "raw": r["text"].strip(), "label": mapping[r["label"]],
                         "base": f"{r['label']}:{r['topic']}:{r['language']}", "category": r["topic"],
                         "india_linked": False})
    return rows


def load_syn() -> List[Dict]:
    return [{"source": "SYN", "key": f"line{e.row}", "raw": e.text, "label": e.label, "base": e.group,
             "category": "synthetic", "india_linked": False} for e in tc.load_dataset(SYN_CSV)]


# ---------------------------------------------------------------------------
# Steps 4-8
# ---------------------------------------------------------------------------

def _tokens(text: str) -> set:
    return set(tc.skeleton_text(text).split())


def remove_protected_overlap(rows: List[Dict], protected: List[str], excluded: List[Dict]) -> List[Dict]:
    prot = [anonymise(t) for t in protected]
    res = tc.find_training_overlap(list(enumerate(r["text"] for r in rows)), prot)
    hit = {i: k for k in ("exact_normalised", "skeleton", "token_jaccard") for i in res[k]}
    kept = []
    for i, r in enumerate(rows):
        if i in hit:
            excluded.append({"source": r["source"], "source_ref": r["key"], "outcome": "retired_or_challenge_overlap",
                             "reason": f"overlaps retired benchmark or challenge suite ({hit[i]})"})
        else:
            kept.append(r)
    return kept


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


class _UF:
    def __init__(self, n: int):
        self.p = list(range(n))

    def find(self, x: int) -> int:
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[max(ra, rb)] = min(ra, rb)


def assign_groups(rows: List[Dict], threshold: float = 0.6) -> Dict[str, int]:
    """Union-find of base-key links and template links (Jaccard >= threshold,
    candidates share one of their 3 rarest within-source tokens). Sets r['group']."""
    stats = {}
    by_source: Dict[str, List[int]] = defaultdict(list)
    for i, r in enumerate(rows):
        by_source[r["source"]].append(i)
    for source, idxs in by_source.items():
        uf = _UF(len(idxs))
        toks = [_tokens(rows[i]["text"]) for i in idxs]
        base_first: Dict[str, int] = {}
        for local, i in enumerate(idxs):
            b = rows[i]["base"]
            if b is not None:
                if b in base_first:
                    uf.union(local, base_first[b])
                else:
                    base_first[b] = local
        df = Counter(t for s in toks for t in s)
        buckets: Dict[str, List[int]] = defaultdict(list)
        for local, s in enumerate(toks):
            for t in sorted(s, key=lambda t: (df[t], t))[:3]:
                buckets[t].append(local)
        links = 0
        for members in buckets.values():
            for a_pos in range(len(members)):
                a = members[a_pos]
                for b in members[a_pos + 1:]:
                    if uf.find(a) == uf.find(b):
                        continue
                    sa, sb = toks[a], toks[b]
                    inter = len(sa & sb)
                    if inter and inter / (len(sa) + len(sb) - inter) >= threshold:
                        uf.union(a, b)
                        links += 1
        comp_key: Dict[int, str] = {}
        for local, i in enumerate(idxs):
            root = uf.find(local)
            comp_key[root] = min(comp_key.get(root, rows[i]["key"]), rows[i]["key"])
        for local, i in enumerate(idxs):
            rows[i]["group"] = f"{source}:{comp_key[uf.find(local)]}"
        stats[source] = {"rows": len(idxs), "groups": len({rows[i]['group'] for i in idxs}), "template_links": links}
    return stats


def assignment_u(salt: str, group_id: str) -> float:
    return int(hashlib.sha256(f"{salt}|{group_id}".encode()).hexdigest()[:8], 16) / 2 ** 32


def assign_split(rows: List[Dict], salt: str, fraction: float = 0.20) -> None:
    for r in rows:
        if r["source"] in ("IMC25", "DONATION"):
            r["split"] = "test" if assignment_u(salt, r["group"]) < fraction else "train"
        elif r["source"] == "MWOZ_TEST":
            r["split"] = "test"
        else:
            r["split"] = "train"


def remove_cross_split_leakage(rows: List[Dict], excluded: List[Dict]) -> Dict[str, int]:
    removed_groups, rounds = set(), 0
    while True:
        rounds += 1
        train = [r for r in rows if r["split"] == "train" and r["group"] not in removed_groups]
        test = [r for r in rows if r["split"] == "test"]
        res = tc.find_training_overlap(list(enumerate(r["text"] for r in train)), [r["text"] for r in test])
        hits = {i for k in ("exact_normalised", "skeleton", "token_jaccard") for i in res[k]}
        new = {train[i]["group"] for i in hits} - removed_groups
        if not new:
            break
        removed_groups |= new
    removed_rows = 0
    for r in rows:
        if r["split"] == "train" and r["group"] in removed_groups:
            r["split"] = "removed"
            removed_rows += 1
            excluded.append({"source": r["source"], "source_ref": r["key"], "outcome": "train_group_removed_for_leakage",
                             "reason": "train group near-duplicates a test row"})
    # Final verification (must be zero).
    train = [r for r in rows if r["split"] == "train"]
    test = [r for r in rows if r["split"] == "test"]
    final = tc.find_training_overlap(list(enumerate(r["text"] for r in test)), [r["text"] for r in train])
    if final["total"]:
        raise StopBuild(f"Cross-split leakage remains after removal: {final['total']} test rows.")
    return {"train_groups_removed": len(removed_groups), "train_rows_removed": removed_rows, "rounds": rounds,
            "final_test_train_overlap": 0}


def coverage(p: Dict, test: List[Dict]) -> Dict:
    don = [r for r in test if r["source"] == "DONATION"]
    cats = Counter(r["category"] for r in don)
    donors = {r["donor"] for r in don}
    met = len(don) >= 150 and len(donors) >= 5 and sum(1 for c in cats.values() if c >= 25) >= 3
    return {"test_donated_messages": len(don), "test_donors": len(donors), "test_categories": dict(cats),
            "requirement": p["minimum_legitimate_coverage"]["test_requirement"], "met": met,
            "L1_status": "evaluable" if met else "NOT EVALUABLE"}


def token_rates(rows: Iterable[Dict]) -> Dict:
    agg: Dict[str, Counter] = defaultdict(Counter)
    n: Counter = Counter()
    for r in rows:
        k = f"{r['source']}|{r['label']}"
        n[k] += 1
        words = set(r["text"].split())
        for t in TOKENS:
            agg[k][t] += t in words
    return {k: {t: round(agg[k][t] / n[k], 4) for t in TOKENS} for k in sorted(n)}


def privacy_check(rows: Iterable[Dict]) -> None:
    bad = [f"{r['source']}:{r['key']}" for r in rows if re.search(r"\d{4,}", r["text"])]
    if bad:
        raise StopBuild(f"{len(bad)} anonymised rows still contain >= 4-digit runs, e.g. {bad[:5]}")


def counts(rows: List[Dict]) -> Dict:
    return {s: dict(Counter(r["label"] for r in rows if r["source"] == s)) for s in PRIORITY
            if any(r["source"] == s for r in rows)}


# ---------------------------------------------------------------------------

def build(seal: bool) -> Dict:
    p = load_protocol()
    hashes = verify_sources(p)
    excluded: List[Dict] = []

    imc25, digits = load_imc25(p, excluded)
    rows = (load_donations(excluded) + imc25
            + load_mwoz(MWOZ_TEST, "MWOZ_TEST", 150, 20261025)
            + load_mwoz(MWOZ_TRAIN, "MWOZ_TRAIN", 300, 20261024)
            + load_r115(p, excluded) + load_syn())
    loaded = counts(rows)

    for r in rows:
        r["text"] = anonymise(r["raw"])
    empty = [r for r in rows if not r["text"]]
    for r in empty:
        excluded.append({"source": r["source"], "source_ref": r["key"], "outcome": "rejected",
                         "reason": "empty after anonymisation"})
    rows = [r for r in rows if r["text"]]
    privacy_check(rows)

    protected = [e.text for e in tc.load_evaluation_dataset(RETIRED)] + \
                [e.text for e in tc.load_evaluation_dataset(CHALLENGE)]
    rows = remove_protected_overlap(rows, protected, excluded)
    recheck = tc.find_training_overlap(list(enumerate(r["text"] for r in rows)), [anonymise(t) for t in protected])
    if recheck["total"]:
        raise StopBuild("Overlap with retired benchmark / challenge suite remains.")

    rows = dedupe(rows, excluded)
    group_stats = assign_groups(rows)
    assign_split(rows, p["test_assignment"]["salt"])
    leakage = remove_cross_split_leakage(rows, excluded)

    train = [r for r in rows if r["split"] == "train"]
    test = [r for r in rows if r["split"] == "test"]
    cov = coverage(p, test)

    report = {
        "protocol_sha256": p["_sha256"], "anonymiser": ANONYMISER_VERSION,
        "built_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "dry_run": not seal, "source_hashes": hashes, "loaded_counts": loaded,
        "imc25_long_digit_investigation": digits,
        "excluded_outcomes": dict(Counter(e["outcome"] for e in excluded)),
        "excluded_by_source_and_outcome": {f"{k[0]}|{k[1]}": v for k, v in
                                           sorted(Counter((e["source"], e["outcome"]) for e in excluded).items())},
        "grouping": group_stats, "leakage": leakage,
        "pool_counts": counts(train), "pool_groups": len({r["group"] for r in train}),
        "test_counts": counts(test), "test_groups": len({r["group"] for r in test}),
        "test_india_linked_suspicious": sum(1 for r in test if r["source"] == "IMC25" and r["india_linked"]),
        "coverage": cov,
        "placeholder_token_rates_pool": token_rates(train),
        "privacy_check": "no >= 4-digit runs in any anonymised row",
    }
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    name = "build_report.json" if seal else "build_report_dry_run.json"
    (REPORT_DIR / name).write_text(json.dumps(report, indent=2), encoding="utf-8")

    if seal:
        if MANIFEST.exists():
            raise StopBuild("A sealed manifest already exists; refusing to overwrite (protocol: new version needed).")
        _write_outputs(p, train, test, excluded, report)
    return report


def _write_outputs(p: Dict, train: List[Dict], test: List[Dict], excluded: List[Dict], report: Dict) -> None:
    with POOL_OUT.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["id", "text", "label", "group", "source", "source_ref",
                                           "category", "india_linked"])
        w.writeheader()
        for i, r in enumerate(train):
            w.writerow({"id": f"P3-{i:06d}", "text": r["text"], "label": r["label"], "group": r["group"],
                        "source": r["source"], "source_ref": r["key"], "category": r["category"],
                        "india_linked": int(r["india_linked"])})
    POOL_OUT.with_suffix(".provenance.json").write_text(json.dumps({
        "name": "satyalens-training-pool", "version": "3", "synthetic": "partial",
        "protocol_sha256": p["_sha256"], "anonymiser": ANONYMISER_VERSION,
        "sources": {k: {kk: v.get(kk) for kk in ("title", "licence", "attribution", "commit", "revision", "real")}
                    for k, v in p["sources"].items()},
        "rows": len(train), "csv_sha256": _sha(POOL_OUT)}, indent=2), encoding="utf-8")
    test_path = REPO / p["sealing"]["test_file"].split(" ")[0]
    with test_path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["id", "text", "label", "source", "group", "category", "india_linked", "donor"])
        w.writeheader()
        for i, r in enumerate(test):
            w.writerow({"id": f"T3-{i:06d}", "text": r["text"], "label": r["label"], "source": r["source"],
                        "group": r["group"], "category": r["category"], "india_linked": int(r["india_linked"]),
                        "donor": r.get("donor", "")})
    with EXCLUDED_OUT.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["source", "source_ref", "outcome", "reason"])
        w.writeheader()
        w.writerows(excluded)
    MANIFEST.write_text(json.dumps({
        "protocol_file": "docs/protocol_v3.json", "protocol_sha256": p["_sha256"],
        "sealed_files": [{"path": str(test_path.relative_to(REPO)).replace("\\", "/"), "sha256": _sha(test_path)}],
        "evaluation_log": "reports/frozen_test_v3_evaluations.jsonl",
        "sealed_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "test_counts": report["test_counts"], "test_groups": report["test_groups"],
        "coverage": report["coverage"], "pool_csv_sha256": _sha(POOL_OUT)}, indent=2), encoding="utf-8")


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
    print(json.dumps({k: report[k] for k in ("dry_run", "loaded_counts", "imc25_long_digit_investigation",
                                             "excluded_outcomes", "grouping", "leakage", "pool_counts",
                                             "pool_groups", "test_counts", "test_groups",
                                             "test_india_linked_suspicious", "coverage")}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
