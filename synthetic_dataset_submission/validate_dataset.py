"""
validate_dataset.py — integrity, schema and leakage checks for the synthetic dataset.

    python validate_dataset.py                        # validates dataset/
    python validate_dataset.py --dataset-dir dataset --report results/validation_report.json

Exit status: 0 = no critical problem, 1 = at least one critical problem,
2 = dataset files missing or unreadable.

Critical checks
  - every line is valid JSON and conforms to schemas/dataset_schema.json
  - modality rules: allowed input keys, valid label for the modality, category→label
    matches the generator's documented rule (manifest.label_rules)
  - unique record_id and scenario_id; no duplicate message text; no duplicate image
  - each image exists, lies inside dataset/images, and matches its SHA-256
  - train/dev/test share no template family and no near-duplicate message skeleton
  - no label or category word appears in any model input; image names are opaque
  - manifest matches the data (counts, label distribution, families, file hashes)
Warnings
  - a split lacks some label for a modality
  - rendered payment values repeated across splits
  - overlap with the text classifier's existing training data (../data/…csv),
    which would inflate text metrics (reported, read-only)
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.dont_write_bytecode = True
import generate_dataset as gen  # noqa: E402  (shares label rules, normalisation, skeleton)

MESSAGE_LABELS = {"suspicious", "legitimate", "ambiguous"}
IMAGE_LABELS = {"discrepancy", "consistent", "insufficient_evidence"}
INPUT_KEYS = {"message_text": {"message_text", "media_type"},
              "payment_screenshot": {"image_path", "media_type", "expected_payment"}}
CLASSIFIER_TRAINING_CSV = HERE.parent / "data" / "synthetic_financial_messages_v1.csv"


# ── Minimal JSON-Schema subset (type, enum, pattern, required, properties,
#    additionalProperties, minLength, maxLength) — avoids a new dependency ─────

_TYPES = {"object": dict, "string": str, "array": list, "boolean": bool, "null": type(None)}


def schema_errors(value, schema: dict, path: str = "$") -> list[str]:
    errs: list[str] = []
    types = schema.get("type")
    if types:
        allowed = types if isinstance(types, list) else [types]
        if not any(isinstance(value, _TYPES[t]) for t in allowed):
            return [f"{path}: expected {allowed}, got {type(value).__name__}"]
    if "enum" in schema and value not in schema["enum"]:
        errs.append(f"{path}: {value!r} not in {schema['enum']}")
    if isinstance(value, str):
        if "pattern" in schema and not re.search(schema["pattern"], value):
            errs.append(f"{path}: {value!r} does not match {schema['pattern']}")
        if len(value) < schema.get("minLength", 0):
            errs.append(f"{path}: shorter than {schema['minLength']}")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            errs.append(f"{path}: longer than {schema['maxLength']}")
    if isinstance(value, dict):
        props = schema.get("properties", {})
        for key in schema.get("required", []):
            if key not in value:
                errs.append(f"{path}: missing required '{key}'")
        if schema.get("additionalProperties") is False:
            for key in value:
                if key not in props:
                    errs.append(f"{path}: unexpected key '{key}'")
        for key, sub in props.items():
            if key in value:
                errs.extend(schema_errors(value[key], sub, f"{path}.{key}"))
    return errs


# ── Checks ────────────────────────────────────────────────────────────────────

def _forbidden_words() -> list[str]:
    words = set(MESSAGE_LABELS | IMAGE_LABELS)
    for c in list(gen.MESSAGE_CATEGORIES) + list(gen.IMAGE_CATEGORIES):
        words.add(c)
        words.add(c.replace("_", " "))
    words.discard("unreadable")   # also a plain English word; checked via image names instead
    return sorted(words, key=len, reverse=True)


def input_leaks(record: dict) -> list[str]:
    """Label/category words inside model input strings (case-insensitive, whole words)."""
    texts = []
    inp = record["input"]
    if isinstance(inp.get("message_text"), str):
        texts.append(inp["message_text"])
    for v in (inp.get("expected_payment") or {}).values():
        texts.append(str(v))
    if inp.get("image_path"):
        texts.append(inp["image_path"])
    hits = []
    for t in texts:
        low = t.lower()
        for w in _forbidden_words():
            if re.search(r"(?<![a-z])" + re.escape(w) + r"(?![a-z])", low):
                hits.append(w)
    return sorted(set(hits))


def validate(dataset_dir: Path, schema_path: Path) -> dict:
    critical: list[str] = []
    warnings: list[str] = []

    jsonl, manifest_path = dataset_dir / "synthetic_dataset.jsonl", dataset_dir / "dataset_manifest.json"
    if not jsonl.exists() or not manifest_path.exists():
        return {"fatal": f"missing {jsonl.name} or {manifest_path.name} in {dataset_dir}"}
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    raw = jsonl.read_bytes()

    records = []
    for n, line in enumerate(raw.decode("utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError as exc:
            critical.append(f"line {n}: malformed JSON ({exc.msg})")

    rules = manifest.get("label_rules", {})
    images_dir = (dataset_dir / "images").resolve()
    ids, scenarios, texts, image_hashes = Counter(), Counter(), defaultdict(set), Counter()
    for r in records:
        rid = r.get("record_id", "?")
        errs = schema_errors(r, schema)
        if errs:
            critical.extend(f"{rid}: {e}" for e in errs[:5])
            continue
        ids[rid] += 1
        scenarios[r["meta"]["scenario_id"]] += 1
        mod, label, cat = r["modality"], r["ground_truth"]["label"], r["meta"]["category"]
        if set(r["input"]) != INPUT_KEYS[mod]:
            critical.append(f"{rid}: input keys {sorted(r['input'])} != {sorted(INPUT_KEYS[mod])}")
        if label not in (MESSAGE_LABELS if mod == "message_text" else IMAGE_LABELS):
            critical.append(f"{rid}: label {label!r} invalid for {mod}")
        expected_rule = rules.get(mod, {}).get(cat, {}).get("label")
        if expected_rule != label:
            critical.append(f"{rid}: category {cat!r} has label {label!r}, rule says {expected_rule!r}")
        leaks = input_leaks(r)
        if leaks:
            critical.append(f"{rid}: label/category words in model input: {leaks}")
        if mod == "message_text":
            if r["input"]["media_type"] != "text/plain" or "expected_field_status" in r["ground_truth"]:
                critical.append(f"{rid}: malformed message record")
            texts[gen.normalise_text(r["input"]["message_text"])].add(rid)
        else:
            if r["input"]["media_type"] != "image/png" or "expected_field_status" not in r["ground_truth"]:
                critical.append(f"{rid}: malformed screenshot record")
            img = (dataset_dir / r["input"]["image_path"]).resolve()
            if images_dir not in img.parents:
                critical.append(f"{rid}: image path escapes dataset/images")
            elif not img.exists():
                critical.append(f"{rid}: image file missing")
            else:
                digest = hashlib.sha256(img.read_bytes()).hexdigest()
                image_hashes[digest] += 1
                if digest != r["meta"]["image_sha256"]:
                    critical.append(f"{rid}: image SHA-256 does not match meta")

    critical += [f"duplicate record_id {k}" for k, n in ids.items() if n > 1]
    critical += [f"duplicate scenario_id {k}" for k, n in scenarios.items() if n > 1]
    critical += [f"duplicate message text in {sorted(v)}" for v in texts.values() if len(v) > 1]
    critical += [f"duplicate image content {k[:12]}… ({n}×)" for k, n in image_hashes.items() if n > 1]

    # ── Split separation ─────────────────────────────────────────────────────
    fam_by_split = defaultdict(set)
    skel_by_split = defaultdict(set)
    rendered_by_split = defaultdict(set)
    for r in records:
        s = r["meta"]["split"]
        fam_by_split[s].add(r["meta"]["template_family"])
        if r["modality"] == "message_text":
            skel_by_split[s].add(gen.skeleton(r["input"]["message_text"]))
        elif r["ground_truth"].get("rendered", {}).get("reference"):
            g = r["ground_truth"]["rendered"]
            rendered_by_split[s].add((g["amount"], g["recipient"], g["reference"]))
    splits = sorted(fam_by_split)
    for i, a in enumerate(splits):
        for b in splits[i + 1:]:
            if fam_by_split[a] & fam_by_split[b]:
                critical.append(f"template families shared by {a} and {b}: {sorted(fam_by_split[a] & fam_by_split[b])[:5]}")
            if skel_by_split[a] & skel_by_split[b]:
                critical.append(f"{len(skel_by_split[a] & skel_by_split[b])} message skeleton(s) shared by {a} and {b}")
            if rendered_by_split[a] & rendered_by_split[b]:
                warnings.append(f"rendered payment values repeated across {a}/{b}")

    # ── Distribution / coverage ──────────────────────────────────────────────
    dist = defaultdict(lambda: defaultdict(Counter))
    for r in records:
        dist[r["meta"]["split"]][r["modality"]][r["ground_truth"]["label"]] += 1
    for s in splits:
        for mod, labels in (("message_text", MESSAGE_LABELS), ("payment_screenshot", IMAGE_LABELS)):
            if dist[s][mod] and set(dist[s][mod]) != labels:
                warnings.append(f"split {s} lacks {sorted(labels - set(dist[s][mod]))} for {mod}")
    if not records:
        critical.append("dataset is empty")

    # ── Manifest consistency ─────────────────────────────────────────────────
    plain = {s: {m: dict(c) for m, c in v.items()} for s, v in dist.items()}
    checks = {
        "schema_version": (manifest.get("schema_version"), gen.SCHEMA_VERSION),
        "record_count": (manifest.get("record_count"), len(records)),
        "image_count": (manifest.get("image_count"), sum(image_hashes.values())),
        "split_counts": (manifest.get("split_counts"), {s: sum(r["meta"]["split"] == s for r in records) for s in gen.SPLITS}),
        "label_distribution": (manifest.get("label_distribution"), plain),
        "template_families": (manifest.get("template_families"), {s: sorted(fam_by_split.get(s, set())) for s in gen.SPLITS}),
        "jsonl_sha256": (manifest.get("files", {}).get("synthetic_dataset.jsonl"), hashlib.sha256(raw).hexdigest()),
    }
    image_digest = hashlib.sha256("".join(
        f"{Path(r['input']['image_path']).name}:{r['meta']['image_sha256']}\n"
        for r in sorted((r for r in records if r.get("modality") == "payment_screenshot"),
                        key=lambda r: Path(r["input"]["image_path"]).name)).encode()).hexdigest()
    checks["images_digest"] = (manifest.get("files", {}).get("images_digest"), image_digest)
    for name, (got, want) in checks.items():
        if got != want:
            critical.append(f"manifest {name} does not match the data")

    # ── Overlap with the deployed classifier's training data (informational) ─
    overlap = {"checked": False}
    if CLASSIFIER_TRAINING_CSV.exists():
        with CLASSIFIER_TRAINING_CSV.open(encoding="utf-8") as fh:
            train_rows = [row["text"] for row in csv.DictReader(fh)]
        exact = {gen.normalise_text(t) for t in train_rows}
        skel = {gen.skeleton(t) for t in train_rows}
        msgs = [r["input"]["message_text"] for r in records if r.get("modality") == "message_text"]
        overlap = {"checked": True, "source": "data/synthetic_financial_messages_v1.csv",
                   "exact": sum(gen.normalise_text(m) in exact for m in msgs),
                   "skeleton": sum(gen.skeleton(m) in skel for m in msgs), "messages": len(msgs)}
        if overlap["exact"] or overlap["skeleton"]:
            warnings.append(f"{overlap['exact']} exact / {overlap['skeleton']} skeleton overlaps with the "
                            "text classifier's training data — text metrics would be inflated")

    return {"ok": not critical, "critical": critical, "warnings": warnings,
            "stats": {"records": len(records), "images": sum(image_hashes.values()),
                      "label_distribution": plain,
                      "template_families": {s: len(v) for s, v in fam_by_split.items()}},
            "classifier_training_overlap": overlap}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dataset-dir", type=Path, default=HERE / "dataset")
    ap.add_argument("--schema", type=Path, default=HERE / "schemas" / "dataset_schema.json")
    ap.add_argument("--report", type=Path, default=None, help="write the JSON report here (inside this folder)")
    a = ap.parse_args(argv)
    result = validate(a.dataset_dir.resolve(), a.schema)
    if "fatal" in result:
        print("FATAL:", result["fatal"])
        return 2
    if a.report:
        gen._inside(a.report).parent.mkdir(parents=True, exist_ok=True)
        a.report.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: result[k] for k in ("ok", "stats", "classifier_training_overlap")}, indent=1))
    for w in result["warnings"]:
        print("WARNING:", w)
    for c in result["critical"][:50]:
        print("CRITICAL:", c)
    if len(result["critical"]) > 50:
        print(f"... {len(result['critical']) - 50} more critical problems")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
