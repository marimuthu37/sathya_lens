"""
inspect_message_sources.py — Counts-only inspection of the team-supplied
message datasets in dataset/message_detection/ (INSPECTION ONLY)
=========================================================================

    python scripts/inspect_message_sources.py

Sources are kept SEPARATE and are only read (never modified):
  IMC25     dataset/message_detection/final_dataset_output.csv
            must be byte-identical to the protocol-v3 pinned file.
            Policy applied exactly as docs/protocol_v3.json (label mapping,
            ambiguous categories, English-only) via the v3 builder functions.
  MENDELEY  dataset/message_detection/Financial scams detection dataset.csv
            Mendeley Data znsk27yk3h v1/v2 (identical file), CC BY 4.0.
            NOT part of protocol v3; labels reported as-is (scam / ham).

For each source: column/label validation, anon-v1 anonymisation (shared
module), residual-digit privacy check, exact / normalised / skeleton
duplicates, template clusters (Jaccard >= 0.6, protocol grouping rule), and
overlap with the retired benchmark, the challenge suite, synthetic v1,
training pool v2 and the other source. The retired benchmark is read only to
detect overlap, never for model decisions.

Writes reports/dataset_inspection/message_sources.json and
reports/dataset_inspection/message_exclusions.csv (row references and reasons
only — no message text). Creates no training pool and no test set.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Dict, List, Optional, Tuple

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))

from satyalens_analysis import text_classifier as tc  # noqa: E402
from satyalens_analysis.anonymise import ANONYMISER_VERSION, anonymise  # noqa: E402

DATASET_DIR = REPO / "dataset" / "message_detection"
IMC25_FILE = DATASET_DIR / "final_dataset_output.csv"
MENDELEY_FILE = DATASET_DIR / "Financial scams detection dataset.csv"
OUT_DIR = REPO / "reports" / "dataset_inspection"

MENDELEY = {
    "title": "Financial scams detection dataset",
    "authors": ["Al Rafi Ahmed", "Gazi Faizul Islam"],
    "institution": "International University of Business Agriculture and Technology (Bangladesh)",
    "url": "https://data.mendeley.com/datasets/znsk27yk3h/2",
    "doi": "10.17632/znsk27yk3h.2 (v1 10.17632/znsk27yk3h.1 contains the identical file)",
    "sha256": "58ec802e4de8768643e3be0badbaedb3ae5017a273e8f3898e72f6a29cbc96ad",
    "licence": "CC BY 4.0 (Mendeley public API data_licence, v1 and v2)",
    "attribution": "Ahmed, A. R., Islam, G. F. (2025). Financial scams detection dataset. Mendeley Data, V2. "
                   "doi:10.17632/znsk27yk3h.2. CC BY 4.0.",
    "collection_as_described": "Real messages from university students and general people via Google Forms, "
                               "handwritten submissions and direct observation of SMS/chat/e-mail, with consent; "
                               "personal details removed by the authors; manually labelled scam/ham.",
    "required_columns": ["label", "message"],
    "labels": ["scam", "ham"],
}

# Heuristics (reporting aids only — never ground truth).
_FIN = re.compile(
    r"(?i)\b(debited|credited|balance|bal|tk|taka|bkash|nagad|rocket|otp|account|a/c|payment|paid|transaction|"
    r"txn|trxid|cash\s*(?:in|out)|recharge|bank|card|bill|transfer|received|sent|withdraw|upi|rs|inr)\b|"
    r"টাকা|বিকাশ|নগদ|অ্যাকাউন্ট|একাউন্ট|ব্যালেন্স|লেনদেন|ক্যাশ|রিচার্জ|ওটিপি|ব্যাংক")
_NOTIF = re.compile(
    r"(?i)\b(debited|credited|received|cash\s*(?:in|out)|trxid|txnid|transaction\s*id|balance\s*(?:is|:)|"
    r"your\s+(?:otp|verification\s+code)|is\s+your\s+(?:otp|code)|bill\s+(?:of|is)|recharge\s+of|payment\s+of)\b|"
    r"সফল|গ্রহণ")
_BD = re.compile(r"(?i)\b(bkash|nagad|rocket|taka|tk|dbbl|sonali|bangladesh)\b|টাকা|বিকাশ|নগদ")
_IN = re.compile(r"(?i)\b(upi|paytm|phonepe|sbi|hdfc|icici|aadhaar|rs\.?|inr)\b|₹")
_BENGALI = re.compile(r"[ঀ-৿]")
_DEVANAGARI_ONLY = re.compile(r"[ऀ-ॣ०-ॿ]")   # excludes the shared danda marks


class InspectionError(RuntimeError):
    pass


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def verify(path: Path, expected: str) -> str:
    if not path.is_file():
        raise InspectionError(f"Missing source file: {path.name}")
    actual = sha256(path)
    if actual != expected:
        raise InspectionError(f"{path.name}: SHA-256 {actual[:12]}... does not match verified {expected[:12]}...")
    return actual


def script_of(text: str) -> str:
    if _BENGALI.search(text):
        return "bengali_script"
    if _DEVANAGARI_ONLY.search(text):
        return "devanagari_script"
    return "latin_only"


def ham_kind(text: str) -> str:
    if not _FIN.search(text):
        return "non_financial"
    return "financial_notification_like" if _NOTIF.search(text) else "financial_other"


def region_hint(text: str) -> str:
    bd, ind = bool(_BD.search(text)), bool(_IN.search(text))
    return "both" if bd and ind else "bangladesh_cues" if bd else "india_cues" if ind else "none"


def duplicate_stats(texts: List[str]) -> Dict[str, int]:
    exact = Counter(texts)
    norm = Counter(tc.normalise_text(t) for t in texts)
    skel = Counter(tc.skeleton_text(t) for t in texts if tc.skeleton_text(t))
    return {"exact_duplicate_rows": sum(v - 1 for v in exact.values() if v > 1),
            "normalised_duplicate_rows": sum(v - 1 for v in norm.values() if v > 1),
            "skeleton_duplicate_rows": sum(v - 1 for v in skel.values() if v > 1)}


def privacy_stats(raw: List[str], anon: List[str]) -> Dict[str, int]:
    return {
        "rows_with_10plus_digit_runs_before": sum(1 for t in raw if re.search(r"\d{10,}", t)),
        "rows_with_4plus_digit_runs_before": sum(1 for t in raw if re.search(r"\d{4,}", t)),
        "rows_changed_by_anonymiser": sum(1 for a, r in zip(anon, raw) if a != r),
        "rows_with_4plus_digit_runs_after": sum(1 for t in anon if re.search(r"\d{4,}", t)),
        "rows_with_at_sign_after": sum("@" in t for t in anon),
        "placeholder_tokens_added": dict(Counter(tok for t in anon for tok in re.findall(
            r"\b(urltoken|phonetoken|emailtoken|upitoken|datetoken|numtoken)\b", t))),
    }


def overlaps(anon: List[str], references: Dict[str, List[str]]) -> Dict[str, Dict[str, int]]:
    out = {}
    for name, ref in references.items():
        res = tc.find_training_overlap(list(enumerate(anon)), [anonymise(t) for t in ref])
        out[name] = {k: len(res[k]) for k in ("exact_normalised", "skeleton", "token_jaccard")}
        out[name]["total"] = res["total"]
    return out


def template_clusters(source: str, anon: List[str]) -> Dict[str, int]:
    import build_pool_and_test_v3 as builder
    rows = [{"source": source, "key": f"r{i:06d}", "text": t, "base": None} for i, t in enumerate(anon)]
    stats = builder.assign_groups(rows)[source]
    sizes = Counter(r["group"] for r in rows)
    stats["largest_cluster"] = max(sizes.values()) if sizes else 0
    stats["singleton_clusters"] = sum(1 for v in sizes.values() if v == 1)
    return stats


def read_mendeley(path: Path, exclusions: List[Dict]) -> List[Dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        cols = [c.strip() for c in (reader.fieldnames or [])]
        if cols != MENDELEY["required_columns"]:
            raise InspectionError(f"Mendeley columns {cols} != expected {MENDELEY['required_columns']}")
        rows = []
        for line_no, r in enumerate(reader, start=2):
            label, text = (r.get("label") or "").strip(), (r.get("message") or "").strip()
            if label not in MENDELEY["labels"]:
                exclusions.append({"source": "MENDELEY", "ref": f"line{line_no}", "reason": "invalid label"})
            elif not text:
                exclusions.append({"source": "MENDELEY", "ref": f"line{line_no}", "reason": "empty message"})
            else:
                rows.append({"ref": f"line{line_no}", "label": label, "text": text})
    return rows


def inspect(imc25_path: Path = IMC25_FILE, mendeley_path: Path = MENDELEY_FILE,
            out_dir: Path = OUT_DIR, references: Optional[Dict[str, List[str]]] = None) -> Dict:
    import build_pool_and_test_v3 as builder
    protocol = builder.load_protocol()
    exclusions: List[Dict] = []

    # ---- IMC25 (protocol v3 policy) --------------------------------------
    imc_sha = verify(imc25_path, protocol["sources"]["IMC25"]["file_sha256"])
    builder.IMC25_CSV = imc25_path
    v3_excl: List[Dict] = []
    imc_rows, digits = builder.load_imc25(protocol, v3_excl)
    exclusions += [{"source": "IMC25", "ref": e["source_ref"], "reason": f"{e['outcome']}: {e['reason']}"}
                   for e in v3_excl]
    imc_anon = [anonymise(r["raw"]) for r in imc_rows]

    # ---- Mendeley (separate; not in protocol v3) ------------------------
    men_sha = verify(mendeley_path, MENDELEY["sha256"])
    men_rows = read_mendeley(mendeley_path, exclusions)
    men_anon = [anonymise(r["text"]) for r in men_rows]
    ham = [r for r in men_rows if r["label"] == "ham"]

    if references is None:
        references = {
            "retired_benchmark_v1": [e.text for e in tc.load_evaluation_dataset(REPO / "data" / "real_world_test_v1.csv")]
            if (REPO / "data" / "real_world_test_v1.csv").is_file() else [],
            "challenge_suite_v1": [e.text for e in tc.load_evaluation_dataset(REPO / "data" / "challenge_suite_v1.csv")],
            "synthetic_v1": [e.text for e in tc.load_dataset(REPO / "data" / "synthetic_financial_messages_v1.csv")],
            "training_pool_v2": [e.text for e in tc.load_dataset(REPO / "data" / "training_pool_v2.csv")]
            if (REPO / "data" / "training_pool_v2.csv").is_file() else [],
        }

    report = {
        "inspection_only": True, "anonymiser": ANONYMISER_VERSION,
        "protocol_v3_sha256": protocol["_sha256"],
        "IMC25": {
            "file": str(imc25_path.relative_to(REPO)) if imc25_path.is_relative_to(REPO) else imc25_path.name,
            "sha256": imc_sha, "matches_protocol_v3_pinned_file": True,
            "source": protocol["sources"]["IMC25"]["repository"], "commit": protocol["sources"]["IMC25"]["commit"],
            "licence": protocol["sources"]["IMC25"]["licence"], "attribution": protocol["sources"]["IMC25"]["attribution"],
            "real": True,
            "rows_after_protocol_policy": len(imc_rows),
            "label_counts": dict(Counter(r["label"] for r in imc_rows)),
            "scam_type_counts": dict(Counter(r["category"] for r in imc_rows)),
            "india_linked_rows": sum(1 for r in imc_rows if r["india_linked"]),
            "excluded_by_reason": dict(Counter(e["reason"] for e in exclusions if e["source"] == "IMC25")),
            "long_digit_investigation": digits,
            "duplicates": duplicate_stats(imc_anon),
            "privacy": privacy_stats([r["raw"] for r in imc_rows], imc_anon),
            "template_clusters": template_clusters("IMC25", imc_anon),
            "overlap": overlaps(imc_anon, {k: v for k, v in references.items()}),
        },
        "MENDELEY": {
            "file": mendeley_path.name, "sha256": men_sha, **{k: v for k, v in MENDELEY.items()
                                                              if k not in ("required_columns", "labels", "sha256")},
            "in_protocol_v3": False,
            "real": "claimed real by the authors (forms, handwritten submissions, observation); not independently verifiable",
            "rows_valid": len(men_rows),
            "label_counts": dict(Counter(r["label"] for r in men_rows)),
            "excluded_by_reason": dict(Counter(e["reason"] for e in exclusions if e["source"] == "MENDELEY")),
            "script_by_label": {lab: dict(Counter(script_of(r["text"]) for r in men_rows if r["label"] == lab))
                                for lab in MENDELEY["labels"]},
            "region_cues_by_label": {lab: dict(Counter(region_hint(r["text"]) for r in men_rows if r["label"] == lab))
                                     for lab in MENDELEY["labels"]},
            "ham_kind_heuristic": dict(Counter(ham_kind(r["text"]) for r in ham)),
            "ham_kind_note": "Keyword heuristic, not ground truth. 'financial_notification_like' = transaction/OTP/"
                             "bill notification wording; the dataset does not label message type.",
            "duplicates": duplicate_stats(men_anon),
            "privacy": privacy_stats([r["text"] for r in men_rows], men_anon),
            "template_clusters": template_clusters("MENDELEY", men_anon),
            "overlap": overlaps(men_anon, {**references, "IMC25_after_policy": [r["raw"] for r in imc_rows]}),
        },
        "outputs_created": ["message_sources.json", "message_exclusions.csv"],
        "not_created": ["training pool", "test set", "models"],
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "message_sources.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    with (out_dir / "message_exclusions.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["source", "ref", "reason"])
        w.writeheader()
        w.writerows(exclusions)
    return report


def main(argv: Optional[List[str]] = None) -> int:
    argparse.ArgumentParser(description=__doc__.split("\n")[1]).parse_args(argv)
    try:
        report = inspect()
    except InspectionError as exc:
        print(f"STOP: {exc}", file=sys.stderr)
        return 3
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
