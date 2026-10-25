"""
prepare_real_world_test.py — Build data/real_world_test_v1.csv (evaluation only)
================================================================================

Source dataset
--------------
"SMS PHISHING DATASET FOR MACHINE LEARNING AND PATTERN RECOGNITION"
Sandhya Mishra, Devpriya Soni (Jaypee Institute of Information Technology),
Mendeley Data, Version 1, published 2022-06-20, DOI 10.17632/f45bkkt8pr.1
https://data.mendeley.com/datasets/f45bkkt8pr/1 — licence CC BY 4.0.
File: Dataset_5971.zip (Dataset_5971.csv; columns LABEL, TEXT, URL, EMAIL, PHONE).

What this script does
---------------------
1. Downloads Dataset_5971.zip into data/raw/mishra_soni_2022/ ONLY if absent,
   and refuses to continue unless its SHA-256 equals the value published by
   the Mendeley public API.  The raw file is never modified.
2. Assigns every source row exactly one outcome — nothing is silently dropped:
     accepted   -> data/real_world_test_v1.csv
     rejected   (empty text / unknown label)            -> *.excluded.csv
     ambiguous  (source label 'spam'; conflicting
                 labels on identical text)               -> *.excluded.csv
     duplicate  (normalised text seen earlier)           -> *.excluded.csv
     training_overlap (matches the synthetic training
                 corpus: exact / skeleton / Jaccard)     -> *.excluded.csv
3. Label mapping (case-insensitive source labels):
     smishing -> suspicious        ham -> legitimate
     spam     -> EXCLUDED as ambiguous: the source defines it as unsolicited
                 messaging, not fraud, so it maps to neither class without
                 relabelling, which this project does not do.
4. Anonymisation of the accepted text: digits inside runs of >= 7 digits
   (phone/account-number-like) become '#', and e-mail local parts become
   '***'.  The classifier's tokens are letters-only, so digit masking cannot
   change any prediction; e-mail masking removes only the local-part token.
5. Adds derived, clearly named metadata — never invented ground truth:
     near_duplicate_cluster  skeleton-text equality cluster id (derived; the
                             source provides NO campaign/group identifiers)
     heuristic_topic         'financial' / 'other' from a transparent keyword
                             list (a reporting aid, NOT a verified category)
6. Writes provenance and a validation summary next to the CSV.

The evaluation data is NEVER used for training, tuning or rule changes.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
import sys
import urllib.request
import zipfile
from collections import Counter
from pathlib import Path
from typing import Dict, List, Tuple

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from satyalens_analysis.text_classifier import (  # noqa: E402
    find_training_overlap,
    load_dataset,
    normalise_text,
    skeleton_text,
)

SOURCE_NAME = "mishra_soni_2022_sms_phishing"
SOURCE_TITLE = "SMS PHISHING DATASET FOR MACHINE LEARNING AND PATTERN RECOGNITION"
SOURCE_PAGE = "https://data.mendeley.com/datasets/f45bkkt8pr/1"
SOURCE_DOI = "10.17632/f45bkkt8pr.1"
SOURCE_FILE_URL = ("https://data.mendeley.com/public-files/datasets/f45bkkt8pr/files/"
                   "edb361de-918d-469f-9106-e84823830665/file_downloaded")
SOURCE_ZIP_SHA256 = "9bbf3188fdad81495d8e82825648b9b63b53fc86841a3d26c02629990b233cc3"
SOURCE_LICENCE = "CC BY 4.0 (as stated on the Mendeley Data dataset page)"
RETRIEVED = "2026-10-24"

RAW_DIR = REPO / "data" / "raw" / "mishra_soni_2022"
RAW_ZIP = RAW_DIR / "Dataset_5971.zip"
INNER_CSV = "Dataset_5971.csv"
TRAINING_CSV = REPO / "data" / "synthetic_financial_messages_v1.csv"
OUT_STEM = "real_world_test_v1"

LABEL_MAP = {"smishing": "suspicious", "ham": "legitimate"}
AMBIGUOUS_SOURCE_LABELS = {"spam"}

_PHONE_RUN = re.compile(r"(?:\+?\d[\d\s\-]{5,}\d)")
_EMAIL = re.compile(r"\b[\w.+\-]+@([\w\-]+\.[\w.\-]+)\b")
_FINANCIAL = re.compile(
    r"\b(bank|banking|account|a/c|card|credit|debit|payment|pay|paid|transfer|transaction|"
    r"loan|otp|pin|upi|cash|refund|tax|money|salary|bill|invoice|rs|inr|pounds?|dollars?|"
    r"rupees?|paypal|wallet|fee|charge[ds]?)\b",
    re.IGNORECASE,
)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def ensure_raw(download: bool = True) -> bytes:
    if not RAW_ZIP.is_file():
        if not download:
            raise SystemExit(f"Raw file missing: {RAW_ZIP}")
        RAW_DIR.mkdir(parents=True, exist_ok=True)
        print(f"Downloading {SOURCE_FILE_URL}")
        with urllib.request.urlopen(SOURCE_FILE_URL, timeout=120) as resp:  # noqa: S310
            RAW_ZIP.write_bytes(resp.read())
    data = RAW_ZIP.read_bytes()
    actual = sha256_bytes(data)
    if actual != SOURCE_ZIP_SHA256:
        raise SystemExit(f"SHA-256 mismatch for {RAW_ZIP}: expected {SOURCE_ZIP_SHA256}, got {actual}")
    return data


def anonymise(text: str) -> Tuple[str, int, int]:
    """Mask digits in phone-like runs (>=7 digits) and e-mail local parts."""
    n_phone = 0

    def _mask_phone(m: re.Match) -> str:
        nonlocal n_phone
        s = m.group(0)
        if sum(c.isdigit() for c in s) < 7:
            return s
        n_phone += 1
        return re.sub(r"\d", "#", s)

    text = _PHONE_RUN.sub(_mask_phone, text)
    text, n_email = _EMAIL.subn(lambda m: f"***@{m.group(1)}", text)
    return text, n_phone, n_email


def heuristic_topic(text: str) -> str:
    return "financial" if _FINANCIAL.search(text) else "other"


def classify_rows(rows: List[List[str]], training_texts: List[str]) -> Tuple[List[Dict], List[Dict], Dict]:
    """Return (accepted, excluded, stats). Every input row lands in exactly one list."""
    accepted: List[Dict] = []
    excluded: List[Dict] = []
    stats: Counter = Counter()

    candidates: List[Dict] = []
    for idx, row in enumerate(rows, start=2):           # header is line 1
        src_label = (row[0] if len(row) > 0 else "").strip()
        text = (row[1] if len(row) > 1 else "").strip()
        base = {"source_row": idx, "source_label": src_label, "original_text": text}
        norm_label = src_label.lower()
        if not text:
            excluded.append({**base, "outcome": "rejected", "reason": "empty text"})
            continue
        if norm_label in AMBIGUOUS_SOURCE_LABELS:
            excluded.append({**base, "outcome": "ambiguous",
                             "reason": "source label 'spam' is not defined as fraud; maps to neither class"})
            continue
        if norm_label not in LABEL_MAP:
            excluded.append({**base, "outcome": "rejected", "reason": f"unknown source label {src_label!r}"})
            continue
        candidates.append({**base, "label": LABEL_MAP[norm_label], "source_label_norm": norm_label})

    # Identical normalised text with conflicting labels -> ambiguous (all copies).
    labels_by_norm: Dict[str, set] = {}
    for c in candidates:
        labels_by_norm.setdefault(normalise_text(c["original_text"]), set()).add(c["label"])
    conflicting = {k for k, v in labels_by_norm.items() if len(v) > 1}

    first_row: Dict[str, int] = {}
    survivors: List[Dict] = []
    for c in candidates:
        key = normalise_text(c["original_text"])
        base = {k: c[k] for k in ("source_row", "source_label", "original_text")}
        if key in conflicting:
            excluded.append({**base, "outcome": "ambiguous",
                             "reason": "identical normalised text carries both ham and smishing labels"})
            continue
        if key in first_row:
            excluded.append({**base, "outcome": "duplicate",
                             "reason": f"normalised duplicate of source row {first_row[key]}"})
            continue
        first_row[key] = c["source_row"]
        survivors.append(c)

    overlap = find_training_overlap([(c["source_row"], c["original_text"]) for c in survivors], training_texts)
    overlap_rows = {r: kind for kind in ("exact_normalised", "skeleton", "token_jaccard") for r in overlap[kind]}

    skel_cluster: Dict[str, str] = {}
    for c in survivors:
        base = {k: c[k] for k in ("source_row", "source_label", "original_text")}
        if c["source_row"] in overlap_rows:
            excluded.append({**base, "outcome": "training_overlap",
                             "reason": f"matches synthetic training corpus ({overlap_rows[c['source_row']]})"})
            continue
        text, n_phone, n_email = anonymise(c["original_text"])
        stats["rows_with_masked_phone_like_numbers"] += bool(n_phone)
        stats["rows_with_masked_emails"] += bool(n_email)
        skel = skeleton_text(c["original_text"]) or f"empty-{c['source_row']}"
        cluster = skel_cluster.setdefault(skel, f"nd{len(skel_cluster) + 1:05d}")
        accepted.append({
            "id": f"MS22-{c['source_row']:05d}",
            "text": text,
            "label": c["label"],
            "source": SOURCE_NAME,
            "source_label": c["source_label_norm"],
            "source_row": c["source_row"],
            "heuristic_topic": heuristic_topic(c["original_text"]),
            "near_duplicate_cluster": cluster,
        })

    cluster_sizes = Counter(a["near_duplicate_cluster"] for a in accepted)
    stats["near_duplicate_clusters_multi"] = sum(1 for v in cluster_sizes.values() if v > 1)
    stats["rows_in_multi_clusters"] = sum(v for v in cluster_sizes.values() if v > 1)
    return accepted, excluded, {"stats": dict(stats), "training_overlap": overlap}


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare data/real_world_test_v1.csv")
    parser.add_argument("--no-download", action="store_true")
    args = parser.parse_args()

    raw = ensure_raw(download=not args.no_download)
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        csv_bytes = zf.read(INNER_CSV)
    reader = csv.reader(io.StringIO(csv_bytes.decode("utf-8")))
    header = next(reader)
    if [h.strip() for h in header[:2]] != ["LABEL", "TEXT"]:
        raise SystemExit(f"Unexpected source header: {header}")
    rows = list(reader)

    if not TRAINING_CSV.is_file():
        raise SystemExit(f"Training corpus not found for overlap check: {TRAINING_CSV}")
    training_texts = [e.text for e in load_dataset(TRAINING_CSV)]

    accepted, excluded, extra = classify_rows(rows, training_texts)
    assert len(accepted) + len(excluded) == len(rows), "every source row must be accounted for"

    out_dir = REPO / "data"
    out_csv = out_dir / f"{OUT_STEM}.csv"
    with out_csv.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(accepted[0].keys()))
        w.writeheader()
        w.writerows(accepted)
    exc_csv = out_dir / f"{OUT_STEM}.excluded.csv"
    with exc_csv.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["source_row", "source_label", "outcome", "reason", "original_text"])
        w.writeheader()
        w.writerows(sorted(excluded, key=lambda r: r["source_row"]))

    src_labels = Counter(r[0].strip() for r in rows)
    outcome_counts = Counter(e["outcome"] for e in excluded)
    outcome_counts["accepted"] = len(accepted)
    validation = {
        "source_rows": len(rows),
        "source_label_counts_raw": dict(src_labels),
        "outcomes": dict(outcome_counts),
        "accepted_class_counts": dict(Counter(a["label"] for a in accepted)),
        "accepted_by_heuristic_topic": {
            t: dict(Counter(a["label"] for a in accepted if a["heuristic_topic"] == t))
            for t in ("financial", "other")
        },
        "excluded_reasons": dict(Counter(e["reason"].split(" row ")[0] for e in excluded)),
        "training_overlap": {k: extra["training_overlap"][k] for k in
                             ("exact_normalised", "skeleton", "token_jaccard", "total", "method")},
        "anonymisation": extra["stats"],
        "label_case_normalised": "Source labels 'Smishing'/'Spam' were lower-cased before mapping.",
        "excluded_file": exc_csv.name,
    }
    (out_dir / f"{OUT_STEM}.validation.json").write_text(json.dumps(validation, indent=2), encoding="utf-8")

    provenance = {
        "name": "satyalens-real-world-test",
        "version": "1",
        "synthetic": False,
        "evaluation_only": True,
        "source": {
            "title": SOURCE_TITLE,
            "authors": ["Sandhya Mishra", "Devpriya Soni"],
            "publisher": "Mendeley Data",
            "version": "1 (published 2022-06-20)",
            "doi": SOURCE_DOI,
            "page": SOURCE_PAGE,
            "file": "Dataset_5971.zip",
            "file_url": SOURCE_FILE_URL,
            "file_sha256": SOURCE_ZIP_SHA256,
            "retrieved": RETRIEVED,
            "licence": SOURCE_LICENCE,
            "attribution": "Mishra, S., Soni, D. (2022). SMS PHISHING DATASET FOR MACHINE LEARNING AND "
                           "PATTERN RECOGNITION. Mendeley Data, V1. doi:10.17632/f45bkkt8pr.1",
            "collection_method_as_described": "Images obtained from the Internet converted to text with "
                                              "Python code; labelled by source and cleaned.",
        },
        "label_mapping": {"smishing": "suspicious", "ham": "legitimate", "spam": "excluded (ambiguous)"},
        "label_basis": "Labels are the source authors' published labels; they were not re-annotated by "
                       "this project.",
        "derived_columns": {
            "near_duplicate_cluster": "skeleton_text() equality cluster; derived, NOT a source campaign id",
            "heuristic_topic": "keyword heuristic ('financial'/'other'); a reporting aid, NOT ground truth",
        },
        "modifications": ["leading/trailing whitespace stripped",
                          "digits in >=7-digit runs masked with '#'",
                          "e-mail local parts masked with '***'"],
        "rows": len(accepted),
        "limitations": [
            "Domain mismatch: messages are largely UK/Singapore SMS from c. 2000s-2010s (a systematic review "
            "describes the dataset as a partial clone of the UCI SMS Spam Collection), not Indian financial "
            "communications like the SatyaLens use case.",
            "'smishing' in the source includes premium-rate, prize and subscription scams, not only "
            "financial/bank fraud; 'ham' is mostly personal chat with few genuine bank alerts.",
            "No campaign/group identifiers in the source; near-duplicate clusters are derived from text.",
            "Labels were not independently verified; some label noise is likely (OCR-derived text).",
            "Encoding damage in the source (e.g. the pound sign appears as '?').",
            "No message categories in the source; heuristic_topic is keyword-based.",
        ],
        "csv_sha256": hashlib.sha256(out_csv.read_bytes()).hexdigest(),
    }
    (out_dir / f"{OUT_STEM}.provenance.json").write_text(json.dumps(provenance, indent=2), encoding="utf-8")

    print(json.dumps(validation, indent=2))
    print(f"\nWrote {out_csv} ({len(accepted)} rows), {exc_csv} ({len(excluded)} rows)")


if __name__ == "__main__":
    main()
