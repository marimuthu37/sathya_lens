"""
inspect_phish360.py — Counts-only inventory of the Phish360 website-screenshot
dataset (dataset/image_detection/phish360.zip). INSPECTION ONLY.
==============================================================================

    python scripts/inspect_phish360.py              # index + content (if readable)
    python scripts/inspect_phish360.py --no-decode  # content without full PNG decode

ACCESS: the supplied phish360.zip is AES-encrypted (zip method 99, all
entries). Without the password AND an AES-capable reader (the optional
`pyzipper` package — not installed, not a project dependency), only the
unencrypted zip INDEX is inspected (names, sizes, CRCs). The password is
read from the SATYALENS_PHISH360_PASSWORD environment variable, never from
the command line, and is never logged. No attempt is made to bypass it.

The zip is read in place (never extracted, modified or moved). Expected layout:
    Phish360/<split>/<P|L><id>_<brand|phish|legitimate>/
        Label/label.txt  RAW-HTML/index.html  SCREEN-SHOT/screen_shoot.png  URL/url.txt

Reports (reports/dataset_inspection/phish360.json + phish360_issues.csv):
counts per split/class/brand, file completeness, label consistency (folder
prefix vs label.txt), PNG validity and dimensions, exact (SHA-256) and
near-identical (64-bit dHash equality) screenshot duplicates within/across
classes and splits, registrable-domain overlap across splits and classes,
and simple HTML privacy indicators. URLs, domains, page text and image
content are NEVER written — only counts and sample ids.

Phish360 supports phishing-WEBPAGE screenshot classification only. It says
nothing about payment receipts, UPI screenshots or settlement.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import re
import struct
import sys
import zipfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlsplit

REPO = Path(__file__).resolve().parent.parent
ZIP_PATH = REPO / "dataset" / "image_detection" / "phish360.zip"
OUT_DIR = REPO / "reports" / "dataset_inspection"
EXPECTED_SHA256 = "64f573e6e52a9deb486f9d07d87c56e245065545fa161e8db6f080732d5ddd5f"  # recorded 2026-10-24
# Matched case-insensitively (120 samples use "URL/URL.txt").
EXPECTED_FILES = {"label/label.txt": "label", "raw-html/index.html": "html",
                  "screen-shot/screen_shoot.png": "png", "url/url.txt": "url"}
SAMPLE_DIR = re.compile(r"^(?P<prefix>[PL])(?P<num>\d+)_(?P<tag>.+)$")
PNG_SIG = b"\x89PNG\r\n\x1a\n"
SECOND_LEVEL = {"co", "com", "net", "org", "gov", "edu", "ac", "or", "ne", "go"}

PROVENANCE = {
    "title": "Phish360",
    "project_page": "https://web.cs.hacettepe.edu.tr/~selman/phish360-dataset/",
    "authors": ["Ahmet Selman Bozkir", "Ahmad H. A. Almakhamreh"],
    "paper": "CrossPhire: Benefiting Multimodality for Robust Phishing Web Page Identification. "
             "Applied Sciences 2026, 16(2), 751. doi:10.3390/app16020751",
    "access": "Google Form on the project page (contact selman@cs.hacettepe.edu.tr)",
    "licence": "NOT A FORMAL LICENCE — download form states 'free for academic purposes' and requires citing "
               "the paper; project page footer 'All rights reserved'; commercial use and redistribution not "
               "addressed. The MIT licence of github.com/almakhamreh/Multimodal-Phishing-Benchmarks covers "
               "that analysis code, not the data. Academic use only; not cleared for commercial use.",
    "version_note": "Project page describes Parquet files; this copy is a folder-per-sample zip. Exact release "
                    "cannot be confirmed; integrity pinned by SHA-256.",
    "real": "Real-world phishing and legitimate pages collected 2020-2023 (per project page)",
}


def registrable_domain(url: str) -> Optional[str]:
    """Approximate eTLD+1 (no public-suffix list): last two labels, or three for co.uk-style."""
    url = url.strip()
    if not url:
        return None
    if "://" not in url:
        url = "http://" + url
    try:
        host = (urlsplit(url).hostname or "").lower().strip(".")
    except ValueError:
        return None
    if not host:
        return None
    if re.fullmatch(r"[\d.]+", host):
        return host
    parts = host.split(".")
    if len(parts) >= 3 and parts[-2] in SECOND_LEVEL and len(parts[-1]) == 2:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def png_header(data: bytes) -> Optional[Tuple[int, int]]:
    if len(data) < 24 or not data.startswith(PNG_SIG) or data[12:16] != b"IHDR":
        return None
    return struct.unpack(">II", data[16:24])


def dhash(img) -> int:
    g = img.convert("L").resize((9, 8))
    flat = getattr(g, "get_flattened_data", None)   # Pillow >= 12; getdata() is deprecated
    px = list(flat() if flat else g.getdata())
    bits = 0
    for row in range(8):
        for col in range(8):
            bits = (bits << 1) | (px[row * 9 + col] > px[row * 9 + col + 1])
    return bits


def decode_png(data: bytes) -> Tuple[bool, Optional[int], str]:
    from PIL import Image
    try:
        with Image.open(io.BytesIO(data)) as im:
            im.verify()
        with Image.open(io.BytesIO(data)) as im:
            im.load()
            return True, dhash(im), im.mode
    except Exception as exc:  # noqa: BLE001
        return False, None, type(exc).__name__


PASSWORD_ENV = "SATYALENS_PHISH360_PASSWORD"


def open_archive(zip_path: Path):
    """
    Return (archive, access_status). Plain zips open with zipfile. AES zips
    need pyzipper AND a password in PASSWORD_ENV; otherwise (None, reason).
    """
    z = zipfile.ZipFile(zip_path)
    files = [i for i in z.infolist() if not i.is_dir()]
    encrypted = sum(1 for i in files if i.flag_bits & 0x1)
    aes = sum(1 for i in files if i.compress_type == 99)
    status = {"files": len(files), "encrypted_entries": encrypted, "aes_entries": aes}
    if not encrypted:
        status["content_readable"] = True
        return z, status
    password = os.environ.get(PASSWORD_ENV)
    if not password:
        status.update(content_readable=False,
                      reason=f"archive is encrypted; password not provided (set {PASSWORD_ENV})")
        return None, status
    try:
        import pyzipper  # optional, not a project dependency
    except ImportError:
        status.update(content_readable=False,
                      reason="archive is AES-encrypted; the optional 'pyzipper' reader is not installed")
        return None, status
    az = pyzipper.AESZipFile(zip_path)
    az.setpassword(password.encode("utf-8"))
    status["content_readable"] = True
    return az, status


def index_inventory(zip_path: Path) -> Dict:
    """Counts from the unencrypted zip index only (names, sizes, CRC32)."""
    z = zipfile.ZipFile(zip_path)
    infos = {i.filename: i for i in z.infolist() if not i.is_dir()}
    samples = group_samples(list(infos))
    split_class, brands, completeness = Counter(), Counter(), Counter()
    unrecognised = 0
    sizes: Dict[str, List[int]] = defaultdict(list)
    crc_index: Dict[str, Dict[Tuple[int, int], List[Tuple]]] = defaultdict(lambda: defaultdict(list))
    for (split, folder), files in samples.items():
        m = SAMPLE_DIR.match(folder)
        if not m:
            unrecognised += 1
            continue
        cls = "phishing" if m.group("prefix") == "P" else "legitimate"
        split_class[(split, cls)] += 1
        if cls == "phishing":
            brands[m.group("tag")] += 1
        for k, kind in EXPECTED_FILES.items():
            if k not in files:
                completeness[f"missing_{kind}"] += 1
            else:
                info = infos[files[k]]
                sizes[kind].append(info.file_size)
                if info.CRC:
                    crc_index[kind][(info.CRC, info.file_size)].append((split, cls, f"{split}/{folder}"))
        extra = [k for k in files if k not in EXPECTED_FILES]
        if extra:
            completeness["samples_with_extra_files"] += 1
            completeness["extra_files"] += len(extra)

    def dup(index):
        groups = [v for v in index.values() if len(v) > 1]
        return {"candidate_groups": len(groups), "rows_in_groups": sum(len(g) for g in groups),
                "groups_spanning_both_classes": sum(1 for g in groups if len({c for _, c, _ in g}) > 1),
                "groups_spanning_both_splits": sum(1 for g in groups if len({s for s, _, _ in g}) > 1)}

    crc_available = any(crc_index[k] for k in crc_index)
    return {
        "samples": sum(split_class.values()), "unrecognised_folders": unrecognised,
        "by_split_and_class": {f"{s}|{c}": n for (s, c), n in sorted(split_class.items())},
        "labels_source": "folder-name prefix (P = phishing, L = legitimate); label.txt not readable",
        "phishing_brand_tags": {"distinct": len(brands), "top_20": brands.most_common(20),
                                "generic_phish_tag": brands.get("phish", 0)},
        "file_completeness": dict(completeness),
        "uncompressed_size_bytes": {k: {"min": min(v), "median": sorted(v)[len(v) // 2], "max": max(v),
                                        "zero_byte": sum(1 for x in v if x == 0)} for k, v in sizes.items()},
        "duplicate_candidates_by_crc32_and_size": ({k: dup(v) for k, v in crc_index.items()} if crc_available
                                                   else "not available: entries store CRC32 = 0 (AES AE-2)"),
    }


def group_samples(names: List[str]) -> Dict[Tuple[str, str], Dict[str, str]]:
    """Map (split, sample folder) -> {lower-cased relative path: zip entry name}."""
    samples: Dict[Tuple[str, str], Dict[str, str]] = defaultdict(dict)
    for n in names:
        parts = n.split("/")
        if len(parts) >= 4 and not n.endswith("/"):
            samples[(parts[1], parts[2])]["/".join(parts[3:]).lower()] = n
    return samples


def inspect(zip_path: Path = ZIP_PATH, out_dir: Path = OUT_DIR, decode: bool = True,
            expected_sha256: Optional[str] = EXPECTED_SHA256) -> Dict:
    file_sha = None
    if expected_sha256:
        h = hashlib.sha256()
        with zip_path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 22), b""):
                h.update(chunk)
        file_sha = h.hexdigest()
        if file_sha != expected_sha256:
            raise RuntimeError("phish360.zip SHA-256 does not match the recorded inventory hash.")

    index = index_inventory(zip_path)
    z, access = open_archive(zip_path)
    if z is None:
        report = {"inspection_only": True, "provenance": PROVENANCE, "file": zip_path.name, "sha256": file_sha,
                  "access": access, "index_inventory": index,
                  "content_inventory": f"NOT PERFORMED — {access['reason']}",
                  "not_assessed_without_content": ["label.txt agreement with folder names", "PNG validity, "
                                                   "dimensions and corruption", "screenshot duplicates/near-duplicates",
                                                   "URL/domain grouping and cross-split domain leakage",
                                                   "HTML privacy indicators", "personal data in screenshots"],
                  "supports": "Binary phishing-vs-legitimate WEBPAGE classification from screenshots (and URL/HTML), "
                              "once readable. Not payment receipts, UPI screenshots, or payment settlement."}
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "phish360.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        return report

    issues: List[Dict[str, str]] = []
    samples = group_samples([i.filename for i in z.infolist()])

    split_class, brands, labels_txt, dims, modes = Counter(), Counter(), Counter(), Counter(), Counter()
    png_sha: Dict[str, List[Tuple]] = defaultdict(list)
    png_dh: Dict[int, List[Tuple]] = defaultdict(list)
    url_seen: Dict[str, List[Tuple]] = defaultdict(list)
    html_sha: Dict[str, List[Tuple]] = defaultdict(list)
    dom_splits: Dict[str, set] = defaultdict(set)
    dom_classes: Dict[str, set] = defaultdict(set)
    dom_samples: Counter = Counter()
    stats = Counter()
    widths, heights = [], []

    for (split, folder), files in sorted(samples.items()):
        sid = f"{split}/{folder}"
        m = SAMPLE_DIR.match(folder)
        if not m:
            issues.append({"sample": sid, "issue": "unrecognised folder name"})
            continue
        cls = "phishing" if m.group("prefix") == "P" else "legitimate"
        tag = m.group("tag")
        split_class[(split, cls)] += 1
        if cls == "phishing":
            brands[tag] += 1
        missing = [k for k in EXPECTED_FILES if k not in files]
        extra = [k for k in files if k not in EXPECTED_FILES]
        for k in missing:
            issues.append({"sample": sid, "issue": f"missing {k}"})
            stats[f"missing_{EXPECTED_FILES[k]}"] += 1
        if extra:
            stats["samples_with_extra_files"] += 1
            stats["extra_files"] += len(extra)

        if "label/label.txt" in files:
            lab = z.read(files["label/label.txt"]).decode("utf-8", "replace").strip().lower()
            labels_txt[lab] += 1
            lab_cls = "phishing" if lab.startswith("phish") or lab == "1" else \
                      "legitimate" if lab.startswith(("legit", "benign")) or lab == "0" else "unknown"
            if lab_cls != cls:
                issues.append({"sample": sid, "issue": f"label.txt ({lab_cls}) disagrees with folder ({cls})"})
                stats["label_mismatch"] += 1

        if "url/url.txt" in files:
            url = z.read(files["url/url.txt"]).decode("utf-8", "replace").strip()
            if url:
                url_seen[url].append((split, cls, sid))
            dom = registrable_domain(url)
            if dom:
                dom_splits[dom].add(split)
                dom_classes[dom].add(cls)
                dom_samples[dom] += 1
            else:
                stats["unparseable_or_empty_url"] += 1
                issues.append({"sample": sid, "issue": "unparseable or empty URL"})

        if "raw-html/index.html" in files:
            html = z.read(files["raw-html/index.html"])
            if not html.strip():
                stats["empty_html"] += 1
                issues.append({"sample": sid, "issue": "empty HTML"})
            html_sha[hashlib.sha256(html).hexdigest()].append((split, cls, sid))
            low = html[:2_000_000].decode("utf-8", "replace").lower()
            if re.search(r"<input[^>]*type=[\"']?password[^>]*value=[\"'][^\"']+[\"']", low):
                stats["html_password_field_with_value"] += 1
            if re.search(r"[\w.+\-]+@[\w\-]+\.[a-z]{2,}", low):
                stats["html_contains_email_like_string"] += 1

        if "screen-shot/screen_shoot.png" in files:
            data = z.read(files["screen-shot/screen_shoot.png"])
            hdr = png_header(data)
            if hdr is None:
                stats["png_bad_header"] += 1
                issues.append({"sample": sid, "issue": "screenshot is not a valid PNG (header)"})
            else:
                w, h = hdr
                dims[f"{w}x{h}"] += 1
                widths.append(w)
                heights.append(h)
                if w < 200 or h < 200:
                    stats["png_smaller_than_200px"] += 1
            png_sha[hashlib.sha256(data).hexdigest()].append((split, cls, sid))
            if decode and hdr is not None:
                ok, dh, mode = decode_png(data)
                if ok:
                    modes[mode] += 1
                    png_dh[dh].append((split, cls, sid))
                else:
                    stats["png_decode_failed"] += 1
                    issues.append({"sample": sid, "issue": f"PNG decode failed ({mode})"})

    def dup_summary(index: Dict) -> Dict[str, int]:
        groups = [v for v in index.values() if len(v) > 1]
        return {"duplicate_groups": len(groups),
                "rows_in_duplicate_groups": sum(len(g) for g in groups),
                "groups_spanning_both_classes": sum(1 for g in groups if len({c for _, c, _ in g}) > 1),
                "groups_spanning_both_splits": sum(1 for g in groups if len({s for s, _, _ in g}) > 1)}

    multi_split = [d for d, s in dom_splits.items() if len(s) > 1]
    multi_class = [d for d, c in dom_classes.items() if len(c) > 1]
    report = {
        "inspection_only": True, "provenance": PROVENANCE, "access": access, "index_inventory": index,
        "file": zip_path.name, "sha256": file_sha, "decoded_all_pngs": decode,
        "samples": sum(split_class.values()),
        "by_split_and_class": {f"{s}|{c}": n for (s, c), n in sorted(split_class.items())},
        "label_txt_values": dict(labels_txt),
        "phishing_brand_tags": {"distinct": len(brands), "top_20": brands.most_common(20),
                                "generic_phish_tag": brands.get("phish", 0)},
        "file_completeness": {k: v for k, v in stats.items() if k.startswith(("missing_", "samples_with", "extra"))},
        "label_mismatches": stats["label_mismatch"],
        "png": {"bad_header": stats["png_bad_header"], "decode_failed": stats["png_decode_failed"],
                "smaller_than_200px": stats["png_smaller_than_200px"],
                "dimension_top_10": dims.most_common(10), "distinct_dimensions": len(dims),
                "width_min_max": [min(widths), max(widths)] if widths else None,
                "height_min_max": [min(heights), max(heights)] if heights else None,
                "colour_modes": dict(modes)},
        "duplicates": {"screenshot_exact_sha256": dup_summary(png_sha),
                       "screenshot_near_identical_dhash": dup_summary(png_dh) if decode else "not computed",
                       "html_exact_sha256": dup_summary(html_sha),
                       "url_exact": dup_summary(url_seen)},
        "domains": {"distinct_registrable_domains": len(dom_splits),
                    "domains_in_both_splits": len(multi_split),
                    "samples_on_domains_in_both_splits": sum(dom_samples[d] for d in multi_split),
                    "domains_in_both_classes": len(multi_class),
                    "samples_on_domains_in_both_classes": sum(dom_samples[d] for d in multi_class),
                    "unparseable_or_empty_url": stats["unparseable_or_empty_url"],
                    "largest_domain_sample_count": max(dom_samples.values()) if dom_samples else 0,
                    "note": "eTLD+1 approximated without a public-suffix list; shared hosting domains "
                            "(e.g. free site builders) group unrelated sites."},
        "privacy_indicators": {"html_password_field_with_value": stats["html_password_field_with_value"],
                               "html_contains_email_like_string": stats["html_contains_email_like_string"],
                               "screenshots_reviewed_for_personal_data": False,
                               "note": "Screenshot content was not inspected; manual spot-check needed before use."},
        "issues_logged": len(issues),
        "supports": "Binary phishing-vs-legitimate WEBPAGE classification from screenshots (and URL/HTML). "
                    "Not payment receipts, UPI screenshots, or payment settlement.",
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "phish360.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    with (out_dir / "phish360_issues.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["sample", "issue"])
        w.writeheader()
        w.writerows(issues)
    return report


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-decode", action="store_true")
    args = ap.parse_args(argv)
    report = inspect(decode=not args.no_decode)
    print(json.dumps({k: v for k, v in report.items() if k != "provenance"}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
