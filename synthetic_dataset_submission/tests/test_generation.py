"""Determinism, configurability, label rules and output-location guarantees of the generator."""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from pathlib import Path

import pytest

import generate_dataset as gen
from conftest import HERE, load, small_config


def _digest(d: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(d.rglob("*")):
        if p.is_file():
            h.update(p.relative_to(d).as_posix().encode())
            h.update(p.read_bytes())
    return h.hexdigest()


def test_same_seed_and_config_is_byte_identical(work_dir):
    a, b = work_dir / "a", work_dir / "b"
    gen.generate(small_config(seed=5), a)
    gen.generate(small_config(seed=5), b)
    assert (a / "synthetic_dataset.jsonl").read_bytes() == (b / "synthetic_dataset.jsonl").read_bytes()
    assert _digest(a) == _digest(b)                       # records, labels, manifest and PNGs


def test_different_seeds_differ(work_dir):
    gen.generate(small_config(seed=1, images=False), work_dir / "s1")
    gen.generate(small_config(seed=2, images=False), work_dir / "s2")
    t1 = [r["input"]["message_text"] for r in load(work_dir / "s1")]
    t2 = [r["input"]["message_text"] for r in load(work_dir / "s2")]
    assert t1 != t2


def test_size_is_configurable(work_dir):
    m = gen.generate(gen.Config(seed=3, images=False, texts_per_template={"train": 1, "dev": 2, "test": 3}),
                     work_dir / "d")
    n_categories = len(gen.MESSAGE_CATEGORIES)
    assert m["split_counts"] == {"train": n_categories, "dev": 2 * n_categories, "test": 3 * n_categories}
    assert m["record_count"] == 6 * n_categories and m["image_count"] == 0


def test_labels_follow_documented_rules(small_dataset):
    for r in load(small_dataset):
        table = gen.MESSAGE_CATEGORIES if r["modality"] == "message_text" else gen.IMAGE_CATEGORIES
        assert r["ground_truth"]["label"] == table[r["meta"]["category"]][0]


def test_screenshot_ground_truth_matches_category(small_dataset):
    for r in (x for x in load(small_dataset) if x["modality"] == "payment_screenshot"):
        cat, st, exp = r["meta"]["category"], r["ground_truth"]["expected_field_status"], r["input"]["expected_payment"]
        rendered = r["ground_truth"]["rendered"]
        if cat.endswith("_mismatch"):
            field = cat.removesuffix("_mismatch")
            assert st[field] == "mismatch"
            if field == "amount":
                assert Decimal(exp["amount"].replace(",", "")) != Decimal(rendered["amount"])
            else:
                assert exp[field] != rendered[field]
        if cat == "amount_format_only":
            # Regression: small amounts such as 12.34 used to be written identically.
            assert exp["amount"] != gen._fmt(Decimal(rendered["amount"]))          # written differently
            assert Decimal(exp["amount"]) == Decimal(rendered["amount"])           # same value
        if cat == "no_expected_values":
            assert exp is None and set(st.values()) == {"not_supplied"}
        if cat == "placeholder_recipient":
            assert rendered["recipient"] in gen.PLACEHOLDER_RECIPIENTS and "recipient" not in exp
        if cat == "missing_recipient":
            assert rendered["recipient"] is None and st["recipient"] == "not_compared"


@pytest.mark.parametrize("seed", range(40))
def test_amount_format_only_always_differs_in_writing(seed):
    for r in gen.build_image_records(gen.Config(seed=seed, images_per_category={"train": 2, "dev": 2, "test": 2})):
        if r["meta"]["category"] == "amount_format_only":
            exp, shown = r["input"]["expected_payment"]["amount"], Decimal(r["ground_truth"]["rendered"]["amount"])
            assert exp != gen._fmt(shown) and Decimal(exp) == shown


def test_manifest_contents(small_dataset):
    m = json.loads((small_dataset / "dataset_manifest.json").read_text(encoding="utf-8"))
    for key in ("seed", "config", "record_count", "label_distribution", "schema_version", "methodology",
                "template_families", "files", "label_rules", "generator_sha256"):
        assert key in m
    assert m["seed"] == 11 and m["schema_version"] == gen.SCHEMA_VERSION
    assert m["synthetic"] is True and "not_generated" in m


def test_default_seed_is_documented():
    assert gen.DEFAULT_SEED == 20261025
    readme = (HERE / "README.md").read_text(encoding="utf-8")
    assert str(gen.DEFAULT_SEED) in readme


# ── Output location ──────────────────────────────────────────────────────────

def test_refuses_to_write_outside_the_folder():
    with pytest.raises(SystemExit, match="Refusing to write outside"):
        gen.generate(small_config(images=False), HERE.parent / "should_not_exist")
    assert not (HERE.parent / "should_not_exist").exists()


def test_cli_rejects_outside_path():
    with pytest.raises(SystemExit):
        gen.main(["--no-images", "--out", str(HERE.parent / "elsewhere")])
    assert not (HERE.parent / "elsewhere").exists()


def test_all_outputs_stay_inside_output_dir(work_dir):
    out = work_dir / "inside"
    gen.generate(small_config(), out)
    for p in out.rglob("*"):
        assert out in p.resolve().parents
    for r in load(out):
        if r["input"].get("image_path"):
            assert (out / r["input"]["image_path"]).resolve().parent == (out / "images").resolve()


def test_shipped_dataset_was_produced_by_the_default_command(work_dir):
    """Regenerating with the documented default command reproduces dataset/ exactly."""
    shipped = HERE / "dataset" / "dataset_manifest.json"
    if not shipped.exists():
        pytest.skip("dataset/ not generated in this checkout")
    m = gen.generate(gen.Config(), work_dir / "regen")
    assert m["files"] == json.loads(shipped.read_text(encoding="utf-8"))["files"]
