"""The validator accepts the real dataset and detects each class of corruption."""

from __future__ import annotations

import json
import shutil

import pytest

import validate_dataset as v
from conftest import HERE, load

SCHEMA = HERE / "schemas" / "dataset_schema.json"


def _copy(src, dst):
    shutil.copytree(src, dst)
    return dst


def _rewrite(d, mutate):
    """Apply mutate(records) and rewrite the JSONL (the manifest is left as it was)."""
    records = load(d)
    records = mutate(records) or records
    (d / "synthetic_dataset.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in records), encoding="utf-8")


def _critical(d) -> str:
    return "\n".join(v.validate(d, SCHEMA)["critical"])


# ── Accepts valid data ───────────────────────────────────────────────────────

def test_shipped_dataset_is_valid():
    if not (HERE / "dataset" / "synthetic_dataset.jsonl").exists():
        pytest.skip("dataset/ not generated")
    r = v.validate(HERE / "dataset", SCHEMA)
    assert r["ok"], r["critical"][:5]
    assert r["classifier_training_overlap"].get("exact", 0) == 0


def test_fresh_small_dataset_is_valid(small_dataset):
    r = v.validate(small_dataset, SCHEMA)
    assert r["ok"], r["critical"][:5]


def test_every_record_conforms_to_schema(small_dataset):
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    for rec in load(small_dataset):
        assert v.schema_errors(rec, schema) == []


# ── Schema subset implementation ─────────────────────────────────────────────

@pytest.mark.parametrize("value, schema, fragment", [
    (5, {"type": "string"}, "expected"),
    ({}, {"type": "object", "required": ["a"]}, "missing required 'a'"),
    ({"a": 1, "b": 2}, {"type": "object", "properties": {"a": {}}, "additionalProperties": False}, "unexpected key 'b'"),
    ("x", {"type": "string", "enum": ["y"]}, "not in"),
    ("abc", {"type": "string", "pattern": "^[0-9]+$"}, "does not match"),
    ("ab", {"type": "string", "minLength": 3}, "shorter"),
    (None, {"type": ["string", "null"]}, None),
])
def test_schema_subset(value, schema, fragment):
    errs = v.schema_errors(value, schema)
    assert (not errs) if fragment is None else any(fragment in e for e in errs)


# ── Detects corruption ───────────────────────────────────────────────────────

def test_duplicate_record_detected(small_dataset, work_dir):
    d = _copy(small_dataset, work_dir / "d")
    _rewrite(d, lambda rs: rs + [rs[0]])
    c = _critical(d)
    assert "duplicate record_id" in c and "duplicate message text" in c


def test_wrong_label_for_category_detected(small_dataset, work_dir):
    d = _copy(small_dataset, work_dir / "d")

    def flip(rs):
        m = next(r for r in rs if r["ground_truth"]["label"] == "suspicious")
        m["ground_truth"]["label"] = "legitimate"
    _rewrite(d, flip)
    assert "rule says 'suspicious'" in _critical(d)


def test_invalid_label_value_detected(small_dataset, work_dir):
    d = _copy(small_dataset, work_dir / "d")

    def bad(rs):
        rs[0]["ground_truth"]["label"] = "fraud"
    _rewrite(d, bad)
    assert "not in" in _critical(d)


def test_missing_and_malformed_values_detected(small_dataset, work_dir):
    d = _copy(small_dataset, work_dir / "d")

    def bad(rs):
        del rs[0]["meta"]["split"]
        img = next(r for r in rs if r["modality"] == "payment_screenshot" and r["input"]["expected_payment"])
        img["input"]["expected_payment"]["amount"] = "-12"
    _rewrite(d, bad)
    c = _critical(d)
    assert "missing required 'split'" in c and "does not match" in c


def test_malformed_json_line_detected(small_dataset, work_dir):
    d = _copy(small_dataset, work_dir / "d")
    with (d / "synthetic_dataset.jsonl").open("a", encoding="utf-8") as fh:
        fh.write("{not json\n")
    assert "malformed JSON" in _critical(d)


def test_tampered_image_detected(small_dataset, work_dir):
    d = _copy(small_dataset, work_dir / "d")
    img = next((d / "images").iterdir())
    img.write_bytes(img.read_bytes() + b"x")
    assert "image SHA-256 does not match" in _critical(d)


def test_manifest_inconsistency_detected(small_dataset, work_dir):
    d = _copy(small_dataset, work_dir / "d")
    m = json.loads((d / "dataset_manifest.json").read_text(encoding="utf-8"))
    m["record_count"] += 1
    (d / "dataset_manifest.json").write_text(json.dumps(m), encoding="utf-8")
    assert "manifest record_count does not match" in _critical(d)


def test_missing_files_give_exit_status_2(work_dir):
    assert v.main(["--dataset-dir", str(work_dir)]) == 2


def test_critical_failure_gives_exit_status_1(small_dataset, work_dir):
    d = _copy(small_dataset, work_dir / "d")
    _rewrite(d, lambda rs: rs + [rs[0]])
    assert v.main(["--dataset-dir", str(d)]) == 1


def test_valid_dataset_gives_exit_status_0(small_dataset):
    assert v.main(["--dataset-dir", str(small_dataset)]) == 0
