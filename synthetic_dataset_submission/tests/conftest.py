"""Shared fixtures. Every file a test creates lives under tests/_tmp/ (inside this folder)."""

from __future__ import annotations

import shutil
import sys
import uuid
from pathlib import Path

sys.dont_write_bytecode = True     # no .pyc files anywhere, including the application tree
HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

import pytest  # noqa: E402

import generate_dataset as gen  # noqa: E402

TMP_ROOT = HERE / "tests" / "_tmp"


@pytest.fixture()
def work_dir():
    d = TMP_ROOT / uuid.uuid4().hex[:8]
    d.mkdir(parents=True)
    yield d
    shutil.rmtree(d, ignore_errors=True)
    if TMP_ROOT.exists() and not any(TMP_ROOT.iterdir()):
        TMP_ROOT.rmdir()


def small_config(seed: int = 11, images: bool = True) -> "gen.Config":
    return gen.Config(seed=seed, images=images,
                      images_per_category={"train": 1, "dev": 1, "test": 1},
                      texts_per_template={"train": 2, "dev": 2, "test": 2})


@pytest.fixture(scope="session")
def small_dataset():
    """One small generated dataset (with images) shared by read-only tests."""
    d = TMP_ROOT / f"session-{uuid.uuid4().hex[:8]}"
    gen.generate(small_config(), d)
    yield d
    shutil.rmtree(d, ignore_errors=True)
    if TMP_ROOT.exists() and not any(TMP_ROOT.iterdir()):
        TMP_ROOT.rmdir()


def load(dataset_dir: Path) -> list[dict]:
    return [__import__("json").loads(l) for l in
            (dataset_dir / "synthetic_dataset.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
