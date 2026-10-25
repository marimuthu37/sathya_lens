"""Split separation and label leakage — in the data and in what the evaluator sends."""

from __future__ import annotations

import json
import re
import shutil
from collections import defaultdict

import pytest

import evaluate_dataset as ev
import generate_dataset as gen
import validate_dataset as v
from conftest import HERE, load

SCHEMA = HERE / "schemas" / "dataset_schema.json"


def _datasets(small_dataset):
    out = [small_dataset]
    if (HERE / "dataset" / "synthetic_dataset.jsonl").exists():
        out.append(HERE / "dataset")
    return out


def test_splits_share_no_template_family(small_dataset):
    for d in _datasets(small_dataset):
        fam = defaultdict(set)
        for r in load(d):
            fam[r["meta"]["split"]].add(r["meta"]["template_family"])
        assert not fam["train"] & fam["dev"] and not fam["train"] & fam["test"] and not fam["dev"] & fam["test"]


def test_no_message_skeleton_crosses_splits(small_dataset):
    for d in _datasets(small_dataset):
        sk = defaultdict(set)
        for r in load(d):
            if r["modality"] == "message_text":
                sk[r["meta"]["split"]].add(gen.skeleton(r["input"]["message_text"]))
        assert not sk["train"] & sk["test"] and not sk["dev"] & sk["test"] and not sk["train"] & sk["dev"]


def test_inputs_contain_no_ground_truth(small_dataset):
    for d in _datasets(small_dataset):
        for r in load(d):
            assert set(r["input"]) == v.INPUT_KEYS[r["modality"]]
            blob = json.dumps(r["input"]).lower()
            for key in ("label", "category", "rationale", "rendered", "expected_field_status", "split", "template"):
                assert f'"{key}' not in blob
            assert v.input_leaks(r) == []


def test_image_names_are_opaque(small_dataset):
    for r in load(small_dataset):
        if r["modality"] == "payment_screenshot":
            assert re.fullmatch(r"images/[0-9a-f]{20}\.png", r["input"]["image_path"])


def test_validator_flags_label_word_in_input(small_dataset, work_dir):
    d = shutil.copytree(small_dataset, work_dir / "d")
    records = load(d)
    m = next(r for r in records if r["modality"] == "message_text")
    m["input"]["message_text"] += " (this one is suspicious)"
    (d / "synthetic_dataset.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in records), encoding="utf-8")
    assert "label/category words in model input" in "\n".join(v.validate(d, SCHEMA)["critical"])


def test_validator_flags_shared_template_family(small_dataset, work_dir):
    d = shutil.copytree(small_dataset, work_dir / "d")
    records = load(d)
    test_rec = next(r for r in records if r["meta"]["split"] == "test")
    train_fam = next(r["meta"]["template_family"] for r in records if r["meta"]["split"] == "train")
    test_rec["meta"]["template_family"] = train_fam
    (d / "synthetic_dataset.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in records), encoding="utf-8")
    assert "template families shared by" in "\n".join(v.validate(d, SCHEMA)["critical"])


# ── The evaluator sends only `input` to the application ─────────────────────

class _Resp:
    def __init__(self, payload, status=200):
        self._p, self.status_code = payload, status

    def json(self):
        return self._p


class _RecordingClient:
    """Stands in for the FastAPI TestClient; records every request body and upload."""

    def __init__(self):
        self.sent: list[str] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def post(self, url, json=None, files=None):
        self.sent.append(str(json))
        if files:
            name, data, mime = files["file"]
            self.sent.append(name)
            self.sent.append(data.decode("utf-8", "ignore") if mime == "text/plain" else "<png>")
        if url.endswith("/api/cases"):
            return _Resp({"id": "c1"}, 201)
        if url.endswith("/evidence"):
            return _Resp({"id": "e1"}, 201)
        if url.endswith("/analyse"):
            return _Resp({}, 202)
        return _Resp({"category": "insufficient_evidence"})

    def get(self, url):
        return _Resp({"status": "completed", "findings": [], "payment": None})


def test_evaluator_never_sends_ground_truth(small_dataset, monkeypatch):
    client = _RecordingClient()
    monkeypatch.setattr(ev, "_load_app", lambda: client)
    records = load(small_dataset)
    ev.run_split(records, small_dataset)
    sent = "\n".join(client.sent).lower()
    forbidden = {r["meta"]["category"] for r in records} | {"suspicious", "legitimate", "ambiguous",
                                                              "discrepancy", "consistent", "insufficient_evidence"}
    for word in forbidden:
        assert re.search(r"(?<![a-z_])" + re.escape(word) + r"(?![a-z_])", sent) is None, word
    # expected payment details ARE sent (they are user input), rendered values are not
    for r in records:
        if r["modality"] == "payment_screenshot" and r["input"]["expected_payment"]:
            assert str({"expected_payment": r["input"]["expected_payment"]}).lower() in sent
    assert not (HERE / ".eval_workspace").exists()
