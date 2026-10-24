"""
text_classifier.py — ML Text Classifier for Suspicious Financial Messages
=========================================================================
A TF-IDF + Logistic Regression pipeline (scikit-learn) trained offline on a
labelled CSV and loaded from a saved artifact for inference.  It is an
evidence source that sits ALONGSIDE the rule-based context analyser; it does
not replace it and does not issue a final risk verdict.

Training (explicit, offline — never triggered by inference)
-----------------------------------------------------------
    python -m satyalens_analysis.text_classifier train \
        --data data/synthetic_financial_messages_v1.csv

  1. Validate CSV (columns text, label, group; labels in LABELS; one label
     per group; both classes present).
  2. De-duplicate on normalised text (lower-case, numbers incl. "1,20,000"
     -> 0, whitespace collapsed); identical normalised text with conflicting
     labels is an error.
  3. Grouped, stratified split (StratifiedGroupKFold, fixed seed): every
     group (campaign / template family) lands entirely in train OR test.
  4. Verify no shared groups or normalised texts between train and test.
  5. Fit the pipeline on train only; evaluate on held-out test against a
     majority-class DummyClassifier baseline.
  6. Save <model_dir>/text_classifier.joblib and text_classifier.meta.json
     (SHA-256, scikit-learn version, dataset provenance, metrics).

Evaluation (evaluation-only — loads the saved artifact, never fits)
-------------------------------------------------------------------
    python -m satyalens_analysis.text_classifier evaluate \
        --data data/real_world_test_v1.csv \
        --model satyalens_analysis/models/text_classifier.joblib

Writes reports/<data stem>_evaluation/{evaluation.json, summary.md}.  Fails
on invalid data, an invalid model, or overlap with the training corpus.

Inference
---------
    from satyalens_analysis.text_classifier import classify_text
    result = classify_text("Share your OTP now")   # -> TextClassifierResult

Model directory resolution: explicit ``model_dir`` argument, else the
``SATYALENS_TEXT_MODEL_DIR`` environment variable, else
``satyalens_analysis/models/``.

Status semantics
----------------
  analysed       — Inference ran; predicted_class and raw_score present.
  inconclusive   — No term of the message is in the model vocabulary, so the
                   output would reflect only the intercept; no class returned.
  not_applicable — Empty or whitespace-only text.
  unavailable    — scikit-learn missing, or artifact / metadata file missing.
  failed         — Artifact present but invalid (SHA-256 mismatch, scikit-learn
                   version mismatch, unreadable, wrong type), invalid input
                   type, or an inference error.
None of unavailable / failed / inconclusive / not_applicable implies the
message is legitimate.

raw_score
---------
``LogisticRegression.predict_proba`` for the 'suspicious' class.  Calibration
has NOT been evaluated; it is not a fraud probability.  predicted_class is
the higher-scoring class (argmax over clf.classes_, which scikit-learn sorts
as ['legitimate', 'suspicious']); no tuned threshold is applied.  Hence, on
the UNROUNDED score: 'suspicious' iff raw_score > 0.5, and an exact tie
(raw_score == 0.5) is 'legitimate'.  TextClassifierResult.to_dict() rounds
raw_score to 6 decimals, so a serialised value of exactly 0.5 (true score in
[0.4999995, 0.5000005)) can accompany either class: always use
predicted_class, never re-derive the class from the rounded raw_score.

SECURITY: joblib artifacts are pickles — loading one can execute arbitrary
code.  Only load artifacts you trained yourself or obtained from a trusted
source.  The SHA-256 check detects accidental corruption or replacement of
the artifact without its metadata; it is not a signature and cannot protect
against an attacker who can rewrite both files.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import logging
import math
import os
import platform
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from satyalens_analysis.schema import (
    ComponentStatus,
    TextClassifierResult,
    text_classifier_failed,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

LABELS: Tuple[str, str] = ("legitimate", "suspicious")   # sorted == classes_ order
POSITIVE_LABEL = "suspicious"
REQUIRED_COLUMNS = ("text", "label", "group")

MODEL_FAMILY = "tfidf-logreg-v1"
ARTIFACT_FILENAME = "text_classifier.joblib"
META_FILENAME = "text_classifier.meta.json"
ENV_MODEL_DIR = "SATYALENS_TEXT_MODEL_DIR"
DEFAULT_MODEL_DIR = Path(__file__).resolve().parent / "models"

DEFAULT_SEED = 42
DEFAULT_TEST_SIZE = 0.25
CV_FOLDS = 5
TOP_TERMS = 5

# Letters-only tokens of 2+ chars: amounts, OTPs and account digits are not features.
TOKEN_PATTERN = r"(?u)\b[^\W\d_][^\W\d_]+\b"

HYPERPARAMETERS: Dict[str, Any] = {
    "tfidf": {"lowercase": True, "strip_accents": "unicode", "token_pattern": TOKEN_PATTERN,
              "ngram_range": [1, 2], "min_df": 1, "sublinear_tf": True},
    "logistic_regression": {"C": 1.0, "class_weight": "balanced", "max_iter": 1000},
}

_STATIC_LIMITATIONS = [
    "raw_score is the Logistic Regression predict_proba output for 'suspicious'. "
    "It is uncalibrated and is NOT a probability that the message is fraudulent.",
    "predicted_class is the higher-scoring class (argmax; no tuned decision threshold is applied). "
    "An exact 0.5 tie resolves to 'legitimate'. The serialised raw_score is rounded to six decimal "
    "places and must not be used to reconstruct predicted_class.",
    "This finding analyses the same message text as the context rules. The two are correlated "
    "(e.g. both react to urgency or OTP wording) and must NOT be counted as independent confirmations.",
    "A 'legitimate' prediction does not confirm the message is safe; a 'suspicious' prediction "
    "does not prove fraud.",
    "Bag-of-words model: ignores sender identity, real link destinations and conversation history, "
    "and can be evaded by paraphrasing.",
    "This component does not issue a final risk verdict.",
]

_NOT_LEGITIMATE_NOTE = "This does NOT indicate the message is legitimate."

# ---------------------------------------------------------------------------
# Dependency check
# ---------------------------------------------------------------------------

def _check_ml_deps() -> Tuple[bool, str]:
    """Return (available, reason). Checks scikit-learn and joblib."""
    missing = []
    try:
        import sklearn  # noqa: F401
    except ImportError:
        missing.append("scikit-learn")
    try:
        import joblib  # noqa: F401
    except ImportError:
        missing.append("joblib")
    if missing:
        deps = ", ".join(missing)
        return False, (
            f"Missing ML text-classifier dependencies: {deps}. "
            'Install with: pip install -e ".[ml]". '
            "Text classification is unavailable without these packages."
        )
    return True, ""


_ML_DEPS_OK, _ML_DEP_REASON = _check_ml_deps()

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class DatasetValidationError(ValueError):
    """Raised when a training dataset is malformed or unsuitable."""


class ModelUnavailableError(RuntimeError):
    """Model artifact, metadata or dependency is missing."""


class ModelInvalidError(RuntimeError):
    """Model artifact or metadata exists but fails validation or loading."""


class EvaluationError(RuntimeError):
    """Evaluation cannot run (invalid dataset, model, or train/eval overlap)."""


class SealedDataError(EvaluationError):
    """A sealed frozen-test file was opened outside the one-shot final evaluation."""

# ---------------------------------------------------------------------------
# Sealed test guard (docs/protocol_v3.json, v4, v5)
# ---------------------------------------------------------------------------

SEALED_MANIFEST_ENV = "SATYALENS_SEALED_MANIFEST"
_REPORTS = Path(__file__).resolve().parent.parent / "reports"
DEFAULT_SEALED_MANIFEST = _REPORTS / "frozen_test_v3_manifest.json"
DEFAULT_SEALED_MANIFESTS = (DEFAULT_SEALED_MANIFEST, _REPORTS / "frozen_test_v4_manifest.json",
                            _REPORTS / "frozen_test_v5_manifest.json")


def _sealed_manifest_paths() -> List[Path]:
    """Default manifests, plus the one named by SATYALENS_SEALED_MANIFEST (protocol v5
    sealing.guard: the variable ADDS a manifest; it never disables the defaults)."""
    paths = list(DEFAULT_SEALED_MANIFESTS)
    env = os.environ.get(SEALED_MANIFEST_ENV)
    if env and Path(env).resolve() not in {p.resolve() for p in paths}:
        paths.append(Path(env))
    return paths


def _sealed_manifests() -> List[Dict[str, Any]]:
    """All existing sealed manifests (defaults + optional env addition)."""
    out = []
    for path in _sealed_manifest_paths():
        if path.is_file():
            manifest = json.loads(path.read_text(encoding="utf-8"))
            manifest["_manifest_path"] = str(path)
            out.append(manifest)
    return out


def _manifest_for(path: Path) -> Optional[Dict[str, Any]]:
    """The manifest that seals *path* (by location or identical content), if any."""
    if not path.is_file():
        return None
    resolved = path.resolve()
    digest = None
    for manifest in _sealed_manifests():
        base = Path(manifest["_manifest_path"]).resolve().parent.parent
        for entry in manifest.get("sealed_files", []):
            if (base / entry["path"]).resolve() == resolved:
                return manifest
            digest = digest or _sha256(path)
            if digest == entry["sha256"]:
                return manifest
    return None


def is_sealed(path) -> bool:
    """True if *path* is a sealed file — by location or by identical content (SHA-256)."""
    return _manifest_for(Path(path)) is not None


def _check_not_sealed(path, allow_sealed: bool) -> None:
    if not allow_sealed and is_sealed(path):
        raise SealedDataError(
            f"{Path(path).name} is a SEALED frozen test file. Only "
            "`evaluate --final-sealed-evaluation` may read it, once (see the manifest's protocol_file)."
        )

# ---------------------------------------------------------------------------
# Dataset handling
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Example:
    text: str
    label: str
    group: str
    row: int   # 1-based line number in the CSV (header is line 1)


def normalise_text(text: str) -> str:
    """Normalisation used for duplicate / leakage detection (not for features)."""
    text = text.lower()
    text = re.sub(r"\d+(?:[.,]\d+)*", "0", text)   # "12,999.50" -> "0"
    return re.sub(r"\s+", " ", text).strip()


def load_dataset(path, allow_sealed: bool = False) -> List[Example]:
    """
    Load and validate a labelled CSV.

    Raises DatasetValidationError listing every problem found (up to 20), or
    SealedDataError if *path* is a sealed frozen test file.
    """
    path = Path(path)
    if not path.is_file():
        raise DatasetValidationError(f"Dataset file not found: {str(path)!r}")
    _check_not_sealed(path, allow_sealed)

    with path.open("r", newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        columns = reader.fieldnames or []
        missing = [c for c in REQUIRED_COLUMNS if c not in columns]
        if missing:
            raise DatasetValidationError(
                f"Dataset is missing required column(s): {missing}. Found: {columns}"
            )
        examples: List[Example] = []
        problems: List[str] = []
        for line_no, raw in enumerate(reader, start=2):
            text = (raw.get("text") or "").strip()
            label = (raw.get("label") or "").strip()
            group = (raw.get("group") or "").strip()
            if not text:
                problems.append(f"line {line_no}: empty text")
            if label not in LABELS:
                problems.append(f"line {line_no}: invalid label {label!r} (allowed: {list(LABELS)})")
            if not group:
                problems.append(f"line {line_no}: empty group")
            if text and label in LABELS and group:
                examples.append(Example(text=text, label=label, group=group, row=line_no))

    if problems:
        shown = problems[:20]
        more = f" (+{len(problems) - 20} more)" if len(problems) > 20 else ""
        raise DatasetValidationError("Invalid dataset rows: " + "; ".join(shown) + more)

    group_labels: Dict[str, set] = {}
    for ex in examples:
        group_labels.setdefault(ex.group, set()).add(ex.label)
    mixed = sorted(g for g, labs in group_labels.items() if len(labs) > 1)
    if mixed:
        raise DatasetValidationError(
            f"Groups must contain a single label; mixed-label groups: {mixed[:10]}"
        )

    present = {ex.label for ex in examples}
    if present != set(LABELS):
        raise DatasetValidationError(
            f"Dataset must contain both labels {list(LABELS)}; found {sorted(present)}"
        )
    return examples


def deduplicate(examples: Sequence[Example]) -> Tuple[List[Example], int]:
    """
    Drop examples whose normalised text was already seen (keeps the first).

    Returns (kept, n_removed). Raises DatasetValidationError if identical
    normalised text carries conflicting labels.
    """
    first_seen: Dict[str, Example] = {}
    kept: List[Example] = []
    conflicts: List[str] = []
    for ex in examples:
        key = normalise_text(ex.text)
        prior = first_seen.get(key)
        if prior is None:
            first_seen[key] = ex
            kept.append(ex)
        elif prior.label != ex.label:
            conflicts.append(f"lines {prior.row} and {ex.row}")
    if conflicts:
        raise DatasetValidationError(
            "Identical (normalised) text with conflicting labels at: " + "; ".join(conflicts[:10])
        )
    return kept, len(examples) - len(kept)


def grouped_split(
    examples: Sequence[Example],
    test_size: float = DEFAULT_TEST_SIZE,
    seed: int = DEFAULT_SEED,
) -> Tuple[List[Example], List[Example]]:
    """
    Split so that each group falls entirely in train or test, stratified by
    label where feasible (StratifiedGroupKFold; the first fold is the test set).
    """
    from sklearn.model_selection import StratifiedGroupKFold

    if not 0.0 < test_size < 0.5:
        raise DatasetValidationError(f"test_size must be in (0, 0.5); got {test_size}")
    n_splits = max(2, round(1.0 / test_size))

    groups_per_label: Dict[str, set] = {}
    for ex in examples:
        groups_per_label.setdefault(ex.label, set()).add(ex.group)
    for label in LABELS:
        n = len(groups_per_label.get(label, ()))
        if n < 2:
            raise DatasetValidationError(
                f"Label {label!r} has {n} group(s); at least 2 are needed for a grouped split."
            )
    n_groups = len({ex.group for ex in examples})
    if n_groups < n_splits:
        raise DatasetValidationError(
            f"Only {n_groups} groups; need at least {n_splits} for test_size={test_size}."
        )

    splitter = StratifiedGroupKFold(n_splits=n_splits, shuffle=True, random_state=seed)
    y = [ex.label for ex in examples]
    groups = [ex.group for ex in examples]
    train_idx, test_idx = next(splitter.split(list(range(len(examples))), y, groups))
    train = [examples[i] for i in train_idx]
    test = [examples[i] for i in test_idx]

    for name, part in (("train", train), ("test", test)):
        if {ex.label for ex in part} != set(LABELS):
            raise DatasetValidationError(
                f"Grouped split produced a {name} set without both labels; add more groups."
            )
    return train, test


def find_leakage(train: Sequence[Example], test: Sequence[Example]) -> Dict[str, List[str]]:
    """Return groups and normalised texts present in both train and test."""
    shared_groups = sorted({e.group for e in train} & {e.group for e in test})
    shared_texts = sorted({normalise_text(e.text) for e in train}
                          & {normalise_text(e.text) for e in test})
    return {"shared_groups": shared_groups, "shared_texts": shared_texts}


def class_distribution(examples: Sequence[Example]) -> Dict[str, int]:
    return {label: sum(1 for e in examples if e.label == label) for label in LABELS}

# ---------------------------------------------------------------------------
# Pipeline, evaluation, training
# ---------------------------------------------------------------------------

def build_pipeline(hyperparameters: Optional[Dict[str, Any]] = None):
    """
    Return an unfitted TF-IDF + Logistic Regression pipeline.

    ``hyperparameters`` has the shape of HYPERPARAMETERS; its "tfidf" and
    "logistic_regression" dicts are passed to the estimators (ngram_range as
    a list is converted to a tuple). Defaults to HYPERPARAMETERS.
    """
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import Pipeline

    hp = hyperparameters or HYPERPARAMETERS
    t = dict(hp["tfidf"])
    if "ngram_range" in t:
        t["ngram_range"] = tuple(t["ngram_range"])
    return Pipeline([
        ("tfidf", TfidfVectorizer(**t)),
        ("clf", LogisticRegression(**hp["logistic_regression"])),
    ])


def compute_metrics(y_true: Sequence[str], y_pred: Sequence[str]) -> Dict[str, Any]:
    """
    Accuracy, per-class precision/recall/F1/support, macro average and the
    confusion matrix (rows = true label, columns = predicted, order LABELS).
    """
    from sklearn.metrics import accuracy_score, confusion_matrix, precision_recall_fscore_support

    labels = list(LABELS)
    p, r, f, s = precision_recall_fscore_support(
        y_true, y_pred, labels=labels, zero_division=0
    )
    per_class = {
        lab: {"precision": round(float(p[i]), 4), "recall": round(float(r[i]), 4),
              "f1": round(float(f[i]), 4), "support": int(s[i])}
        for i, lab in enumerate(labels)
    }
    mp, mr, mf, _ = precision_recall_fscore_support(
        y_true, y_pred, labels=labels, average="macro", zero_division=0
    )
    return {
        "accuracy": round(float(accuracy_score(y_true, y_pred)), 4),
        **per_class,
        "macro_avg": {"precision": round(float(mp), 4), "recall": round(float(mr), 4),
                      "f1": round(float(mf), 4)},
        "confusion_matrix": {
            "labels": labels,
            "rows_true_cols_pred": confusion_matrix(y_true, y_pred, labels=labels).tolist(),
        },
    }


def _grouped_cv(examples: Sequence[Example], seed: int,
                hyperparameters: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Supplementary grouped CV over all de-duplicated data (no tuning is done)."""
    from sklearn.model_selection import StratifiedGroupKFold

    n_groups = len({e.group for e in examples})
    folds = min(CV_FOLDS, n_groups)
    splitter = StratifiedGroupKFold(n_splits=folds, shuffle=True, random_state=seed)
    texts = [e.text for e in examples]
    y = [e.label for e in examples]
    groups = [e.group for e in examples]
    per_fold = []
    for tr, te in splitter.split(texts, y, groups):
        pipe = build_pipeline(hyperparameters).fit([texts[i] for i in tr], [y[i] for i in tr])
        m = compute_metrics([y[i] for i in te], list(pipe.predict([texts[i] for i in te])))
        per_fold.append({"accuracy": m["accuracy"], "macro_f1": m["macro_avg"]["f1"],
                         "suspicious_recall": m[POSITIVE_LABEL]["recall"],
                         "suspicious_precision": m[POSITIVE_LABEL]["precision"]})

    def _mean_std(key: str) -> Dict[str, float]:
        vals = [f[key] for f in per_fold]
        mean = sum(vals) / len(vals)
        std = (sum((v - mean) ** 2 for v in vals) / len(vals)) ** 0.5
        return {"mean": round(mean, 4), "std": round(std, 4)}

    return {
        "method": f"StratifiedGroupKFold(n_splits={folds}, shuffle=True, random_state={seed})",
        "per_fold": per_fold,
        **{k: _mean_std(k) for k in ("accuracy", "macro_f1", "suspicious_recall",
                                     "suspicious_precision")},
    }


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _load_provenance(data_path: Path) -> Dict[str, Any]:
    sidecar = data_path.with_suffix(".provenance.json")
    if sidecar.is_file():
        return json.loads(sidecar.read_text(encoding="utf-8"))
    logger.warning("text_classifier: no provenance sidecar found at %s", sidecar)
    return {"name": data_path.name, "synthetic": None,
            "note": "No provenance sidecar found; provenance unknown."}


def _training_data_label(provenance: Dict[str, Any]) -> str:
    name = provenance.get("name", "unknown dataset")
    version = provenance.get("version")
    kind = {True: "synthetic", False: "non-synthetic", "partial": "partly synthetic"}.get(
        provenance.get("synthetic"), "provenance unknown")
    return f"{name}" + (f" v{version}" if version else "") + f" ({kind})"


PREPROCESSING_ANON_V1 = "anon-v1"
PREPROCESSING_ANON_V2 = "anon-v2"
PREPROCESSING_OPTIONS = (None, PREPROCESSING_ANON_V1, PREPROCESSING_ANON_V2)


def _preprocessor(preprocessing: Optional[str]):
    """Identity, or the shared anonymiser version the model was trained with."""
    import satyalens_analysis.anonymise as anon   # attribute lookup at call time
    if preprocessing == PREPROCESSING_ANON_V1:
        return anon.anonymise
    if preprocessing == PREPROCESSING_ANON_V2:
        return anon.anonymise_v2
    return lambda text: text


def resolve_model_dir(model_dir=None) -> Path:
    if model_dir is not None:
        return Path(model_dir)
    env = os.environ.get(ENV_MODEL_DIR)
    return Path(env) if env else DEFAULT_MODEL_DIR


def train(
    data_path,
    model_dir=None,
    test_size: float = DEFAULT_TEST_SIZE,
    seed: int = DEFAULT_SEED,
    run_cv: bool = True,
    hyperparameters: Optional[Dict[str, Any]] = None,
    preprocessing: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Train, evaluate and persist the classifier. Returns the metadata dict
    that is also written to <model_dir>/text_classifier.meta.json.

    preprocessing="anon-v1" or "anon-v2" anonymises training text (idempotent)
    and makes classify_text()/evaluate() apply the same version to inputs.
    """
    hp = hyperparameters or HYPERPARAMETERS
    if preprocessing not in PREPROCESSING_OPTIONS:
        raise ValueError(f"Unknown preprocessing {preprocessing!r}")
    if not _ML_DEPS_OK:
        raise ModelUnavailableError(_ML_DEP_REASON)
    import joblib
    import sklearn
    from sklearn.dummy import DummyClassifier

    data_path = Path(data_path)
    raw = load_dataset(data_path)
    examples, n_dupes = deduplicate(raw)
    train_set, test_set = grouped_split(examples, test_size=test_size, seed=seed)
    leakage = find_leakage(train_set, test_set)
    if leakage["shared_groups"] or leakage["shared_texts"]:
        raise DatasetValidationError(f"Train/test leakage detected: {leakage}")

    prep = _preprocessor(preprocessing)
    X_tr, y_tr = [prep(e.text) for e in train_set], [e.label for e in train_set]
    X_te, y_te = [prep(e.text) for e in test_set], [e.label for e in test_set]

    pipeline = build_pipeline(hp).fit(X_tr, y_tr)
    model_metrics = compute_metrics(y_te, list(pipeline.predict(X_te)))
    baseline = DummyClassifier(strategy="most_frequent").fit(X_tr, y_tr)
    baseline_metrics = compute_metrics(y_te, list(baseline.predict(X_te)))

    out_dir = resolve_model_dir(model_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    artifact = out_dir / ARTIFACT_FILENAME
    tmp = out_dir / (ARTIFACT_FILENAME + ".tmp")
    joblib.dump(pipeline, tmp)
    os.replace(tmp, artifact)
    artifact_sha = _sha256(artifact)

    provenance = _load_provenance(data_path)
    meta: Dict[str, Any] = {
        "model_version": f"{MODEL_FAMILY}-{artifact_sha[:12]}",
        "model_family": MODEL_FAMILY,
        "created_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "artifact_file": ARTIFACT_FILENAME,
        "artifact_sha256": artifact_sha,
        "sklearn_version": sklearn.__version__,
        "python_version": platform.python_version(),
        "labels": list(LABELS),
        "positive_label": POSITIVE_LABEL,
        "hyperparameters": hp,
        "preprocessing": preprocessing,
        "training_data": _training_data_label(provenance),
        "dataset": {
            "file": data_path.name,
            "sha256": _sha256(data_path),
            "rows_loaded": len(raw),
            "duplicates_removed": n_dupes,
            "rows_used": len(examples),
            "groups": len({e.group for e in examples}),
            "provenance": provenance,
        },
        "split": {
            "method": f"StratifiedGroupKFold first fold (test_size~{test_size}), seed={seed}",
            "seed": seed,
            "train_rows": len(train_set),
            "test_rows": len(test_set),
            "train_groups": len({e.group for e in train_set}),
            "test_groups": len({e.group for e in test_set}),
            "train_class_distribution": class_distribution(train_set),
            "test_class_distribution": class_distribution(test_set),
            "test_group_ids": sorted({e.group for e in test_set}),
            "leakage_check": {"shared_groups": 0, "shared_normalised_texts": 0},
        },
        "evaluation": {
            "held_out_test": model_metrics,
            "majority_class_baseline": baseline_metrics,
            "averaging_note": (
                "Per-class metrics are reported for both labels; 'suspicious' is the positive "
                "class. macro_avg weights both classes equally so the minority class is not "
                "masked by the majority class."
            ),
        },
        "evaluation_caveat": (
            "Development-only evidence. Results reflect held-out template families of the "
            "training dataset and do NOT indicate real-world scam-detection performance."
            if provenance.get("synthetic") is not False else
            "Results reflect a single held-out split; see dataset provenance for limitations."
        ),
    }
    if run_cv:
        meta["evaluation"]["supplementary_grouped_cv"] = _grouped_cv(examples, seed, hp)

    meta_tmp = out_dir / (META_FILENAME + ".tmp")
    meta_tmp.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    os.replace(meta_tmp, out_dir / META_FILENAME)
    clear_model_cache()
    logger.info("text_classifier: trained %s -> %s", meta["model_version"], artifact)
    return meta

# ---------------------------------------------------------------------------
# Loading (validated, cached)
# ---------------------------------------------------------------------------

@dataclass
class LoadedModel:
    pipeline: Any
    meta: Dict[str, Any]
    feature_names: Any


_CACHE: Dict[Tuple[str, str], LoadedModel] = {}
_REQUIRED_META_KEYS = ("model_version", "artifact_file", "artifact_sha256",
                       "sklearn_version", "labels", "positive_label", "training_data")


def clear_model_cache() -> None:
    _CACHE.clear()


def load_model(model_dir=None) -> LoadedModel:
    """
    Load the artifact after verifying metadata, SHA-256 and scikit-learn
    version. Raises ModelUnavailableError or ModelInvalidError.
    """
    if not _ML_DEPS_OK:
        raise ModelUnavailableError(_ML_DEP_REASON)

    directory = resolve_model_dir(model_dir)
    meta_path = directory / META_FILENAME
    if not meta_path.is_file():
        raise ModelUnavailableError(
            f"Model metadata not found at {str(meta_path)!r}. Train the model with: "
            "python -m satyalens_analysis.text_classifier train --data <csv>"
        )
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ModelInvalidError(f"Model metadata is unreadable: {exc}") from exc
    if not isinstance(meta, dict) or any(k not in meta for k in _REQUIRED_META_KEYS):
        raise ModelInvalidError(
            f"Model metadata is missing required keys {list(_REQUIRED_META_KEYS)}."
        )
    if list(meta["labels"]) != list(LABELS) or meta["positive_label"] != POSITIVE_LABEL:
        raise ModelInvalidError(
            f"Model labels {meta['labels']} do not match expected {list(LABELS)}."
        )

    artifact = directory / Path(str(meta["artifact_file"])).name
    if not artifact.is_file():
        raise ModelUnavailableError(f"Model artifact not found at {str(artifact)!r}.")

    key = (str(directory.resolve()), str(meta["artifact_sha256"]))
    cached = _CACHE.get(key)
    if cached is not None:
        return cached

    actual_sha = _sha256(artifact)
    if actual_sha != meta["artifact_sha256"]:
        raise ModelInvalidError(
            "Model artifact SHA-256 does not match metadata; refusing to load "
            f"(expected {meta['artifact_sha256'][:12]}..., got {actual_sha[:12]}...)."
        )

    import sklearn
    if meta["sklearn_version"] != sklearn.__version__:
        raise ModelInvalidError(
            f"Model was trained with scikit-learn {meta['sklearn_version']} but "
            f"{sklearn.__version__} is installed; retrain the model or install the matching version."
        )

    import joblib
    from sklearn.pipeline import Pipeline
    try:
        pipeline = joblib.load(artifact)
    except Exception as exc:  # noqa: BLE001
        raise ModelInvalidError(f"Model artifact could not be loaded: {exc}") from exc

    if not isinstance(pipeline, Pipeline) or set(pipeline.named_steps) != {"tfidf", "clf"}:
        raise ModelInvalidError("Model artifact is not the expected TF-IDF + LogisticRegression pipeline.")
    classes = [str(c) for c in getattr(pipeline.named_steps["clf"], "classes_", [])]
    if classes != list(LABELS):
        raise ModelInvalidError(f"Model classes {classes} do not match expected {list(LABELS)}.")

    loaded = LoadedModel(
        pipeline=pipeline,
        meta=meta,
        feature_names=pipeline.named_steps["tfidf"].get_feature_names_out(),
    )
    _CACHE[key] = loaded
    return loaded

# ---------------------------------------------------------------------------
# Inference
# ---------------------------------------------------------------------------

def _limitations_for(meta: Dict[str, Any]) -> List[str]:
    synthetic = meta.get("dataset", {}).get("provenance", {}).get("synthetic")
    data_note = f"Trained on {meta['training_data']}."
    if synthetic is not False:
        data_note += (" Synthetic / development data only — performance on real "
                      "messages is unknown.")
    return [data_note] + list(_STATIC_LIMITATIONS)


def classify_text(text, model_dir=None) -> TextClassifierResult:
    """
    Classify *text* with the saved pipeline. Never trains; never raises.
    """
    if not isinstance(text, str):
        return text_classifier_failed(
            f"Invalid input: message text must be a string, got {type(text).__name__}."
        )
    if not text.strip():
        return TextClassifierResult(
            status=ComponentStatus.NOT_APPLICABLE,
            reason="No message text was provided.",
        )
    if not _ML_DEPS_OK:
        return TextClassifierResult(
            status=ComponentStatus.UNAVAILABLE,
            reason=f"{_ML_DEP_REASON} {_NOT_LEGITIMATE_NOTE}",
            limitations=[_NOT_LEGITIMATE_NOTE],
        )

    try:
        loaded = load_model(model_dir)
    except ModelUnavailableError as exc:
        return TextClassifierResult(
            status=ComponentStatus.UNAVAILABLE,
            reason=f"{exc} {_NOT_LEGITIMATE_NOTE}",
            limitations=[_NOT_LEGITIMATE_NOTE],
        )
    except ModelInvalidError as exc:
        logger.error("text_classifier: model invalid: %s", exc)
        return text_classifier_failed(f"Model artifact invalid: {exc}")
    except Exception as exc:  # noqa: BLE001
        logger.exception("text_classifier: unexpected load error.")
        return text_classifier_failed(f"Model load error: {exc}")

    meta = loaded.meta
    try:
        tfidf = loaded.pipeline.named_steps["tfidf"]
        clf = loaded.pipeline.named_steps["clf"]
        vec = tfidf.transform([_preprocessor(meta.get("preprocessing"))(text)])
        if vec.nnz == 0:
            return TextClassifierResult(
                status=ComponentStatus.INCONCLUSIVE,
                model_version=meta["model_version"],
                training_data=meta["training_data"],
                reason=("None of the message's terms appear in the model vocabulary; any output "
                        "would reflect only the model's intercept, so no class is returned. "
                        + _NOT_LEGITIMATE_NOTE),
                limitations=_limitations_for(meta),
            )

        proba = clf.predict_proba(vec)[0]
        classes = [str(c) for c in clf.classes_]
        pos_idx = classes.index(POSITIVE_LABEL)
        score = float(proba[pos_idx])
        predicted = classes[int(proba.argmax())]

        # coef_[0] points toward classes_[1]; orient it toward POSITIVE_LABEL.
        sign = 1.0 if pos_idx == 1 else -1.0
        coef = clf.coef_[0]
        contributions = [
            (str(loaded.feature_names[j]), float(v * coef[j] * sign))
            for j, v in zip(vec.indices, vec.data)
        ]
        contributions.sort(key=lambda t: abs(t[1]), reverse=True)
        top_terms = [{"term": t, "weight": round(w, 4)} for t, w in contributions[:TOP_TERMS]]

        return TextClassifierResult(
            status=ComponentStatus.ANALYSED,
            predicted_class=predicted,
            raw_score=score,
            model_version=meta["model_version"],
            training_data=meta["training_data"],
            reason=(f"Classifier output favours '{predicted}' (raw_score={score:.3f}, "
                    "uncalibrated). This is a model signal, not a verdict."),
            top_terms=top_terms,
            limitations=_limitations_for(meta),
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("text_classifier: inference error.")
        return text_classifier_failed(f"Inference error: {exc}")

# ---------------------------------------------------------------------------
# Evaluation (evaluation-only: loads the saved model, never fits)
# ---------------------------------------------------------------------------

EVAL_REQUIRED_COLUMNS = ("text", "label")
# Optional columns reported as breakdowns when present.
BREAKDOWN_COLUMNS = ("source", "source_label", "heuristic_topic", "category")
NEAR_DUP_COLUMN = "near_duplicate_cluster"
MIN_SUBGROUP_N = 20   # smaller subgroups get counts only, no metrics
DEFAULT_REPORTS_DIR = Path(__file__).resolve().parent.parent / "reports"
DEFAULT_DATA_DIR = Path(__file__).resolve().parent.parent / "data"


@dataclass(frozen=True)
class EvalExample:
    text: str
    label: str
    row: int
    fields: Dict[str, str]


def skeleton_text(text: str) -> str:
    """
    Aggressive normalisation for near-duplicate detection: lower-case, drop
    URLs, e-mail addresses, digits and punctuation, collapse whitespace.
    """
    text = text.lower()
    text = re.sub(r"(?:https?://|www\.)\S+", " ", text)
    text = re.sub(r"\S+@\S+", " ", text)
    text = re.sub(r"[^a-z\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def load_evaluation_dataset(path, allow_sealed: bool = False) -> List[EvalExample]:
    """
    Load and validate an evaluation CSV (columns text, label; others kept as
    metadata). Fails on ANY invalid row — evaluation data is expected to be
    curated beforehand, with exclusions recorded by the preparation step.
    Raises SealedDataError for a sealed frozen test file unless allow_sealed.
    """
    path = Path(path)
    if not path.is_file():
        raise EvaluationError(f"Evaluation dataset not found: {str(path)!r}")
    _check_not_sealed(path, allow_sealed)
    with path.open("r", newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        columns = reader.fieldnames or []
        missing = [c for c in EVAL_REQUIRED_COLUMNS if c not in columns]
        if missing:
            raise EvaluationError(
                f"Evaluation dataset is missing required column(s): {missing}. Found: {columns}"
            )
        examples: List[EvalExample] = []
        problems: List[str] = []
        for line_no, raw in enumerate(reader, start=2):
            text = (raw.get("text") or "").strip()
            label = (raw.get("label") or "").strip()
            if not text:
                problems.append(f"line {line_no}: empty text")
            if not label:
                problems.append(f"line {line_no}: missing label")
            elif label not in LABELS:
                problems.append(f"line {line_no}: invalid label {label!r} (allowed: {list(LABELS)})")
            if text and label in LABELS:
                fields = {k: (v or "").strip() for k, v in raw.items()
                          if k not in EVAL_REQUIRED_COLUMNS and k is not None}
                examples.append(EvalExample(text=text, label=label, row=line_no, fields=fields))
    if problems:
        more = f" (+{len(problems) - 20} more)" if len(problems) > 20 else ""
        raise EvaluationError("Invalid evaluation rows: " + "; ".join(problems[:20]) + more)
    if not examples:
        raise EvaluationError("Evaluation dataset contains no rows.")
    present = {e.label for e in examples}
    if present != set(LABELS):
        raise EvaluationError(
            f"Evaluation dataset must contain both labels {list(LABELS)} to compute per-class "
            f"metrics; found only {sorted(present)}."
        )
    return examples


def find_duplicates(examples: Sequence[EvalExample]) -> Dict[str, Any]:
    """Exact (normalised) duplicates and skeleton near-duplicate clusters."""
    by_norm: Dict[str, List[int]] = {}
    by_skel: Dict[str, List[int]] = {}
    for e in examples:
        by_norm.setdefault(normalise_text(e.text), []).append(e.row)
        skel = skeleton_text(e.text)
        if skel:
            by_skel.setdefault(skel, []).append(e.row)
    exact = [rows for rows in by_norm.values() if len(rows) > 1]
    near = [rows for rows in by_skel.values() if len(rows) > 1]
    return {
        "exact_normalised_duplicate_sets": len(exact),
        "exact_normalised_duplicate_rows": sum(len(r) - 1 for r in exact),
        "exact_examples": exact[:20],
        "near_duplicate_clusters": len(near),
        "rows_in_near_duplicate_clusters": sum(len(r) for r in near),
        "near_examples": near[:20],
        "method": "exact: normalise_text(); near: skeleton_text() equality "
                  "(lower-case, URLs/e-mails/digits/punctuation removed)",
    }


def _prefix_len(n: int, threshold: float) -> int:
    return n - math.ceil(threshold * n - 1e-9) + 1


def jaccard_matches(query_sets: Sequence[set], ref_sets: Sequence[set], threshold: float) -> List[bool]:
    """
    For each query set: does any reference set have Jaccard >= threshold?
    Exact result via prefix filtering (if J(x, y) >= t, the prefixes of x and
    y — tokens in a global rarest-first order, of length |s| - ceil(t|s|) + 1 —
    must share a token); candidates are then verified exactly.
    """
    df: Dict[str, int] = {}
    for s in list(query_sets) + list(ref_sets):
        for tok in s:
            df[tok] = df.get(tok, 0) + 1
    order = lambda s: sorted(s, key=lambda t: (df[t], t))  # noqa: E731
    index: Dict[str, List[int]] = {}
    for j, r in enumerate(ref_sets):
        if r:
            for tok in order(r)[:_prefix_len(len(r), threshold)]:
                index.setdefault(tok, []).append(j)
    out = []
    for q in query_sets:
        hit = False
        if q:
            seen = set()
            for tok in order(q)[:_prefix_len(len(q), threshold)]:
                for j in index.get(tok, ()):
                    if j in seen:
                        continue
                    seen.add(j)
                    r = ref_sets[j]
                    inter = len(q & r)
                    if inter and inter / (len(q) + len(r) - inter) >= threshold:
                        hit = True
                        break
                if hit:
                    break
        out.append(hit)
    return out


def jaccard_pairs(sets: Sequence[set], threshold: float) -> List[Tuple[int, int]]:
    """
    All index pairs (i < j) whose sets have Jaccard >= threshold. Exact
    self-join via prefix filtering (same ordering argument as jaccard_matches).
    """
    df: Dict[str, int] = {}
    for s in sets:
        for tok in s:
            df[tok] = df.get(tok, 0) + 1
    index: Dict[str, List[int]] = {}
    pairs: List[Tuple[int, int]] = []
    for i, s in enumerate(sets):
        if not s:
            continue
        prefix = sorted(s, key=lambda t: (df[t], t))[:_prefix_len(len(s), threshold)]
        seen = set()
        for tok in prefix:
            for j in index.get(tok, ()):
                if j in seen:
                    continue
                seen.add(j)
                r = sets[j]
                inter = len(s & r)
                if inter and inter / (len(s) + len(r) - inter) >= threshold:
                    pairs.append((j, i))
            index.setdefault(tok, []).append(i)
    return pairs


def find_training_overlap(eval_texts: Sequence[Tuple[int, str]],
                          train_texts: Sequence[str],
                          jaccard_threshold: float = 0.8) -> Dict[str, Any]:
    """
    Compare evaluation texts (row, text) with training texts: exact normalised
    match, skeleton match, or skeleton-token Jaccard >= threshold.
    """
    train_norm = {normalise_text(t) for t in train_texts}
    train_skel = {skeleton_text(t) for t in train_texts}
    train_tokens = [set(s.split()) for s in train_skel if s]
    exact, skel, near = [], [], []
    pending: List[Tuple[int, set]] = []
    for row, text in eval_texts:
        if normalise_text(text) in train_norm:
            exact.append(row)
            continue
        s = skeleton_text(text)
        if s and s in train_skel:
            skel.append(row)
            continue
        pending.append((row, set(s.split())))
    for (row, _), hit in zip(pending, jaccard_matches([p[1] for p in pending], train_tokens,
                                                      jaccard_threshold)):
        if hit:
            near.append(row)
    return {
        "exact_normalised": exact, "skeleton": skel, "token_jaccard": near,
        "total": len(exact) + len(skel) + len(near),
        "method": f"normalise_text equality, skeleton_text equality, or skeleton token "
                  f"Jaccard >= {jaccard_threshold}",
    }


def _predict_rows(loaded: LoadedModel, texts: Sequence[str]) -> List[Tuple[Optional[str], Optional[float]]]:
    """
    Predict with the loaded pipeline exactly as classify_text() does: rows
    with no in-vocabulary term are 'inconclusive' (None). Uses transform /
    predict_proba only — never fit.
    """
    tfidf = loaded.pipeline.named_steps["tfidf"]
    clf = loaded.pipeline.named_steps["clf"]
    prep = _preprocessor(loaded.meta.get("preprocessing"))
    X = tfidf.transform([prep(t) for t in texts])
    classes = [str(c) for c in clf.classes_]
    pos_idx = classes.index(POSITIVE_LABEL)
    proba = clf.predict_proba(X)
    nnz = X.getnnz(axis=1)
    out: List[Tuple[Optional[str], Optional[float]]] = []
    for i in range(X.shape[0]):
        if nnz[i] == 0:
            out.append((None, None))
        else:
            out.append((classes[int(proba[i].argmax())], float(proba[i][pos_idx])))
    return out


def _error_rates(metrics: Dict[str, Any]) -> Dict[str, Any]:
    (tn, fp), (fn, tp) = metrics["confusion_matrix"]["rows_true_cols_pred"]
    return {
        "suspicious_false_negative_rate": round(fn / (fn + tp), 4) if (fn + tp) else None,
        "suspicious_false_negative_rate_denominator":
            "suspicious messages that received a class (FN + TP)",
        "legitimate_false_positive_rate": round(fp / (fp + tn), 4) if (fp + tn) else None,
        "legitimate_false_positive_rate_denominator":
            "legitimate messages that received a class (FP + TN)",
    }


def _subgroup_report(rows: Sequence[Tuple[EvalExample, Optional[str]]], column: str) -> Dict[str, Any]:
    groups: Dict[str, List[Tuple[EvalExample, Optional[str]]]] = {}
    for ex, pred in rows:
        groups.setdefault(ex.fields.get(column) or "(blank)", []).append((ex, pred))
    out: Dict[str, Any] = {}
    for value, items in sorted(groups.items()):
        scored = [(e, p) for e, p in items if p is not None]
        entry: Dict[str, Any] = {
            "n": len(items),
            "class_counts": {lab: sum(1 for e, _ in items if e.label == lab) for lab in LABELS},
            "inconclusive": len(items) - len(scored),
        }
        if len(scored) >= MIN_SUBGROUP_N:
            entry["accuracy"] = round(sum(1 for e, p in scored if e.label == p) / len(scored), 4)
            for lab in LABELS:
                of_lab = [(e, p) for e, p in scored if e.label == lab]
                entry[f"recall_{lab}"] = (round(sum(1 for e, p in of_lab if p == lab) / len(of_lab), 4)
                                          if len(of_lab) >= MIN_SUBGROUP_N else None)
        else:
            entry["note"] = f"fewer than {MIN_SUBGROUP_N} classified rows; metrics not reported"
        out[value] = entry
    return out


def _resolve_training_texts(meta: Dict[str, Any], training_data) -> Tuple[Optional[List[str]], Dict[str, Any]]:
    """Locate the training CSV and confirm its SHA-256 matches the model metadata."""
    ds = meta.get("dataset", {})
    expected_sha = ds.get("sha256")
    path = Path(training_data) if training_data else (
        DEFAULT_DATA_DIR / ds["file"] if ds.get("file") else None)
    info: Dict[str, Any] = {"training_file": str(path) if path else None,
                            "expected_sha256": expected_sha}
    if path is None or not path.is_file():
        info["performed"] = False
        info["reason"] = "Training dataset file not found; overlap check not performed."
        return None, info
    actual = _sha256(path)
    info["actual_sha256"] = actual
    if expected_sha and actual != expected_sha:
        info["performed"] = False
        info["reason"] = ("Training file found but its SHA-256 differs from the model metadata, "
                          "so it is not the data this model was trained on.")
        return None, info
    info["performed"] = True
    return [e.text for e in load_dataset(path)], info


def _final_sealed_preflight(data_path: Path) -> Dict[str, Any]:
    """Checks for the one-shot final evaluation of a sealed test file."""
    manifest = _manifest_for(data_path)
    if not manifest:
        raise EvaluationError("--final-sealed-evaluation requires a file listed in the sealed manifest.")
    base = Path(manifest["_manifest_path"]).resolve().parent.parent
    protocol = base / manifest["protocol_file"]
    if not protocol.is_file() or _sha256(protocol) != manifest["protocol_sha256"]:
        raise EvaluationError("Protocol file is missing or changed since sealing; final evaluation refused.")
    if _sha256(data_path) != next(e["sha256"] for e in manifest["sealed_files"]
                                  if (base / e["path"]).resolve() == data_path.resolve()
                                  or e["sha256"] == _sha256(data_path)):
        raise EvaluationError("Sealed file content changed since sealing; final evaluation refused.")
    log = base / manifest["evaluation_log"]
    if log.is_file() and log.read_text(encoding="utf-8").strip():
        raise EvaluationError(f"The sealed test has already been evaluated once (see {log.name}); "
                              "a further evaluation requires a new protocol version and test set.")
    return {"manifest": manifest, "log": log}


def evaluate(data_path, model_path, training_data=None, out_dir=None,
             final_sealed: bool = False) -> Dict[str, Any]:
    """
    Evaluate the SAVED model on a labelled CSV. Never fits or modifies the
    model. Raises EvaluationError on invalid inputs or train/eval overlap.
    Writes evaluation.json and summary.md to out_dir.

    A sealed frozen test file can only be evaluated with final_sealed=True,
    once, and only while the protocol file still matches the manifest.
    """
    if not _ML_DEPS_OK:
        raise EvaluationError(_ML_DEP_REASON)
    preflight = _final_sealed_preflight(Path(data_path)) if final_sealed else None
    import joblib
    import numpy
    import sklearn

    model_path = Path(model_path)
    if not model_path.is_file():
        raise EvaluationError(f"Model artifact not found: {str(model_path)!r}")
    try:
        loaded = load_model(model_path.parent)
    except (ModelUnavailableError, ModelInvalidError) as exc:
        raise EvaluationError(f"Model cannot be used for evaluation: {exc}") from exc
    meta = loaded.meta
    if Path(str(meta["artifact_file"])).name != model_path.name:
        raise EvaluationError(
            f"Model metadata describes {meta['artifact_file']!r}, not {model_path.name!r}."
        )
    artifact_sha_before = _sha256(model_path)

    data_path = Path(data_path)
    examples = load_evaluation_dataset(data_path, allow_sealed=final_sealed)
    duplicates = find_duplicates(examples)

    train_texts, overlap_info = _resolve_training_texts(meta, training_data)
    if train_texts is not None:
        overlap = find_training_overlap([(e.row, e.text) for e in examples], train_texts)
        overlap_info.update(overlap)
        if overlap["total"]:
            raise EvaluationError(
                f"{overlap['total']} evaluation row(s) overlap the training data "
                f"(exact {overlap['exact_normalised'][:10]}, skeleton {overlap['skeleton'][:10]}, "
                f"jaccard {overlap['token_jaccard'][:10]}); remove them before evaluating."
            )

    preds = _predict_rows(loaded, [e.text for e in examples])
    paired = [(e, p) for e, (p, _) in zip(examples, preds)]
    scored = [(e, p) for e, p in paired if p is not None]
    if not scored:
        raise EvaluationError("No evaluation row contains any in-vocabulary term; nothing to score.")
    y_true = [e.label for e, _ in scored]
    y_pred = [p for _, p in scored]
    metrics = compute_metrics(y_true, y_pred)
    metrics.update(_error_rates(metrics))

    counts = {lab: y_true.count(lab) for lab in LABELS}
    majority = max(LABELS, key=lambda lab: (counts[lab], lab))
    baseline = compute_metrics(y_true, [majority] * len(y_true))
    baseline.update(_error_rates(baseline))

    # Rule-engine baseline on ALL rows (the rules always return a decision).
    from satyalens_analysis.context_analyser import analyse_context
    rule_preds = ["suspicious" if analyse_context(e.text).flags else "legitimate" for e in examples]
    rule_baseline = compute_metrics([e.label for e in examples], rule_preds)
    rule_baseline.update(_error_rates(rule_baseline))

    report: Dict[str, Any] = {
        "report_type": "text_classifier_evaluation",
        "evaluated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "evaluation_only": True,
        "model": {
            "path": str(model_path),
            "model_version": meta["model_version"],
            "artifact_sha256": artifact_sha_before,
            "trained_with_sklearn": meta["sklearn_version"],
            "training_data": meta["training_data"],
        },
        "environment": {"python": platform.python_version(), "scikit_learn": sklearn.__version__,
                        "joblib": joblib.__version__, "numpy": numpy.__version__},
        "dataset": {
            "file": data_path.name,
            "sha256": _sha256(data_path),
            "provenance": (json.loads(data_path.with_suffix(".provenance.json").read_text(encoding="utf-8"))
                           if data_path.with_suffix(".provenance.json").is_file() else None),
            "rows": len(examples),
            "class_counts": {lab: sum(1 for e in examples if e.label == lab) for lab in LABELS},
        },
        "data_checks": {"duplicates": duplicates, "training_overlap": overlap_info},
        "coverage": {
            "classified": len(scored),
            "inconclusive": len(paired) - len(scored),
            "inconclusive_by_class": {lab: sum(1 for e, p in paired if p is None and e.label == lab)
                                      for lab in LABELS},
        },
        "metrics": metrics,
        "majority_class_baseline": {"predicts": majority, **baseline},
        "rule_engine_baseline": {"description": "context_analyser: any flag -> suspicious; all rows",
                                 "rows": len(examples), **rule_baseline},
        "breakdowns": {col: _subgroup_report(paired, col) for col in BREAKDOWN_COLUMNS
                       if any(col in e.fields for e in examples)},
        "notes": {
            "denominators": (
                "All metrics use the classified rows (coverage.classified). Rows with no "
                "in-vocabulary term are 'inconclusive' (the component returns no class) and are "
                "counted separately in coverage, not as correct or incorrect."
            ),
            "baseline": ("Majority-class baseline always predicts the most frequent label among the "
                         "classified evaluation rows; it is a reference point, not a deployable model."),
            "score": ("raw_score / predict_proba is uncalibrated and was not used for metrics beyond "
                      "the default argmax decision; it is not a probability of fraud."),
            "subgroups": f"Subgroups with fewer than {MIN_SUBGROUP_N} classified rows report counts only.",
        },
    }

    if any(NEAR_DUP_COLUMN in e.fields for e in examples):
        seen, one_per = set(), []
        for e, p in scored:
            key = e.fields.get(NEAR_DUP_COLUMN) or f"row-{e.row}"
            if key not in seen:
                seen.add(key)
                one_per.append((e, p))
        m = compute_metrics([e.label for e, _ in one_per], [p for _, p in one_per])
        m.update(_error_rates(m))
        report["near_duplicate_sensitivity"] = {
            "description": f"Metrics keeping only the first classified row per {NEAR_DUP_COLUMN}.",
            "rows": len(one_per), **m,
        }

    if _sha256(model_path) != artifact_sha_before:
        raise EvaluationError("Model artifact changed during evaluation; results discarded.")
    report["model"]["artifact_unchanged_after_evaluation"] = True

    report["final_sealed_evaluation"] = bool(final_sealed)
    target = Path(out_dir) if out_dir else DEFAULT_REPORTS_DIR / f"{data_path.stem}_evaluation"
    target.mkdir(parents=True, exist_ok=True)
    (target / "evaluation.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    (target / "summary.md").write_text(render_evaluation_summary(report), encoding="utf-8")
    # Per-row results WITHOUT message text, so criteria can be computed later
    # without reopening a sealed test file.
    meta_cols = sorted({k for e in examples for k in e.fields if k not in ("text",)})
    with (target / "predictions.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["row", "label", "prediction", "raw_score", "rule_prediction", *meta_cols])
        for e, (p, s), rp in zip(examples, preds, rule_preds):
            w.writerow([e.row, e.label, p or "", "" if s is None else round(s, 6), rp,
                        *[e.fields.get(c, "") for c in meta_cols]])
    report["output_dir"] = str(target)
    if preflight:
        with preflight["log"].open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"evaluated_at_utc": report["evaluated_at_utc"],
                                 "test_sha256": report["dataset"]["sha256"],
                                 "model_sha256": report["model"]["artifact_sha256"],
                                 "model_version": report["model"]["model_version"],
                                 "report_dir": str(target)}) + "\n")
    return report


def render_evaluation_summary(report: Dict[str, Any]) -> str:
    """Human-readable Markdown summary of an evaluation report."""
    m, b, cov, ds = report["metrics"], report["majority_class_baseline"], report["coverage"], report["dataset"]
    prov = ds.get("provenance") or {}
    synthetic = prov.get("synthetic")
    kind = {True: "SYNTHETIC", False: "real-world (non-synthetic)"}.get(synthetic, "provenance unknown")
    cm = m["confusion_matrix"]["rows_true_cols_pred"]
    lines = [
        f"# Text classifier evaluation — {ds['file']}",
        "",
        f"- Evaluated: {report['evaluated_at_utc']} (evaluation only; model not refit, artifact unchanged)",
        f"- Model: `{report['model']['model_version']}` sha256 `{report['model']['artifact_sha256'][:16]}…` "
        f"trained on {report['model']['training_data']}",
        f"- Dataset: {kind}; sha256 `{ds['sha256'][:16]}…`; {ds['rows']} rows "
        f"(legitimate {ds['class_counts']['legitimate']}, suspicious {ds['class_counts']['suspicious']})",
        f"- Classified: {cov['classified']}; inconclusive (no known vocabulary): {cov['inconclusive']} "
        f"{cov['inconclusive_by_class']}",
        f"- Environment: {report['environment']}",
        "",
        "| Metric (classified rows) | Model | Majority baseline |",
        "|---|---|---|",
        f"| Accuracy | {m['accuracy']} | {b['accuracy']} |",
    ]
    for lab in LABELS:
        for k in ("precision", "recall", "f1"):
            lines.append(f"| {k.capitalize()} — {lab} | {m[lab][k]} | {b[lab][k]} |")
    lines += [
        f"| Macro-avg F1 | {m['macro_avg']['f1']} | {b['macro_avg']['f1']} |",
        f"| Suspicious false-negative rate | {m['suspicious_false_negative_rate']} | {b['suspicious_false_negative_rate']} |",
        f"| Legitimate false-positive rate | {m['legitimate_false_positive_rate']} | {b['legitimate_false_positive_rate']} |",
        "",
        f"Confusion matrix (rows true, cols predicted; legitimate, suspicious): `{cm}`",
        "",
    ]
    if "near_duplicate_sensitivity" in report:
        n = report["near_duplicate_sensitivity"]
        lines += [f"One row per near-duplicate cluster ({n['rows']} rows): accuracy {n['accuracy']}, "
                  f"macro-F1 {n['macro_avg']['f1']}, suspicious FNR {n['suspicious_false_negative_rate']}, "
                  f"legitimate FPR {n['legitimate_false_positive_rate']}", ""]
    for col, groups in report["breakdowns"].items():
        lines += [f"## By `{col}`", "", "| Value | n | legit | susp | inconclusive | accuracy | recall legit | recall susp |",
                  "|---|---|---|---|---|---|---|---|"]
        for val, g in groups.items():
            lines.append(f"| {val} | {g['n']} | {g['class_counts']['legitimate']} | "
                         f"{g['class_counts']['suspicious']} | {g['inconclusive']} | {g.get('accuracy', '—')} | "
                         f"{g.get('recall_legitimate', '—') or '—'} | {g.get('recall_suspicious', '—') or '—'} |")
        lines.append("")
    dup, ov = report["data_checks"]["duplicates"], report["data_checks"]["training_overlap"]
    lines += [
        "## Data checks",
        f"- Exact normalised duplicates remaining: {dup['exact_normalised_duplicate_rows']} rows; "
        f"near-duplicate clusters: {dup['near_duplicate_clusters']} ({dup['rows_in_near_duplicate_clusters']} rows)",
        f"- Training-overlap check performed: {ov.get('performed')}"
        + (f"; overlaps: {ov.get('total')}" if ov.get("performed") else f" ({ov.get('reason')})"),
        "",
        "## Notes",
        *[f"- {v}" for v in report["notes"].values()],
    ]
    if prov.get("limitations"):
        lines += ["", "## Dataset limitations", *[f"- {x}" for x in prov["limitations"]]]
    return "\n".join(lines) + "\n"

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m satyalens_analysis.text_classifier",
        description="Train or run the SatyaLens TF-IDF + Logistic Regression text classifier.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_train = sub.add_parser("train", help="Train, evaluate and save the model.")
    p_train.add_argument("--data", required=True, help="CSV with columns text,label,group.")
    p_train.add_argument("--model-dir", default=None)
    p_train.add_argument("--test-size", type=float, default=DEFAULT_TEST_SIZE)
    p_train.add_argument("--seed", type=int, default=DEFAULT_SEED)
    p_train.add_argument("--no-cv", action="store_true", help="Skip supplementary grouped CV.")

    p_pred = sub.add_parser("predict", help="Classify one message with the saved model.")
    p_pred.add_argument("--text", required=True)
    p_pred.add_argument("--model-dir", default=None)

    p_eval = sub.add_parser("evaluate", help="Evaluate the saved model on a labelled CSV (never fits).")
    p_eval.add_argument("--data", required=True, help="CSV with columns text,label (+ optional metadata).")
    p_eval.add_argument("--model", required=True, help="Path to text_classifier.joblib (metadata alongside).")
    p_eval.add_argument("--training-data", default=None,
                        help="Training CSV for the overlap check (default: data/<file in model metadata>).")
    p_eval.add_argument("--out-dir", default=None, help="Default: reports/<data stem>_evaluation/")
    p_eval.add_argument("--final-sealed-evaluation", action="store_true",
                        help="One-shot evaluation of the sealed frozen test (the sealed manifest's protocol).")

    args = parser.parse_args(argv)
    if args.command == "evaluate":
        try:
            report = evaluate(args.data, args.model, training_data=args.training_data,
                              out_dir=args.out_dir, final_sealed=args.final_sealed_evaluation)
        except (EvaluationError, DatasetValidationError) as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2
        print(render_evaluation_summary(report))
        print(f"Saved: {report['output_dir']}", file=sys.stderr)
        return 0
    if args.command == "train":
        try:
            meta = train(args.data, model_dir=args.model_dir, test_size=args.test_size,
                         seed=args.seed, run_cv=not args.no_cv)
        except (DatasetValidationError, ModelUnavailableError) as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2
        print(json.dumps({k: meta[k] for k in ("model_version", "training_data", "dataset",
                                               "split", "evaluation", "evaluation_caveat")},
                         indent=2))
        print(f"\nSaved: {resolve_model_dir(args.model_dir) / ARTIFACT_FILENAME}", file=sys.stderr)
        return 0

    print(json.dumps(classify_text(args.text, model_dir=args.model_dir).to_dict(), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(_main())
