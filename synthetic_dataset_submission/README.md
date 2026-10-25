# SatyaLens — synthetic dataset submission

A reproducible, fully synthetic dataset for SatyaLens, **the code that generated
it**, a validator, and an evaluation of the **real** SatyaLens application on it.
Everything for this submission lives in this folder.

> All data is synthetic: fictional names, banks, merchants, references, links
> (`*.example`) and amounts. **Results here do not measure real-world fraud
> detection.**

## Contents

| Path | What it is |
|---|---|
| `generate_dataset.py` | Generator (seed, size and image options; writes only inside this folder) |
| `validate_dataset.py` | Schema, integrity, duplicate, split-separation and label-leakage checks (exit 1 on a critical failure) |
| `evaluate_dataset.py` | Runs the real SatyaLens app on the dataset in an isolated temporary database |
| `dataset/synthetic_dataset.jsonl` | 518 records (one JSON object per line) |
| `dataset/images/` | 182 rendered PNG payment screenshots (opaque file names) |
| `dataset/dataset_manifest.json` | Seed, config, counts, label distribution, template families, SHA-256s, label rules |
| `schemas/dataset_schema.json` | JSON Schema for one record |
| `docs/` | Dataset card, generation methodology, evaluation protocol, limitations |
| `tests/` | Generation, validation, leakage and metric tests |
| `results/` | Validation report, per-record predictions, evaluation summary and report |

## Dataset at a glance (seed `20261025`)

| Split | Messages (suspicious / legitimate / ambiguous) | Screenshots (discrepancy / consistent / insufficient_evidence) | Total |
|---|---|---|---|
| train | 48 / 48 / 16 | 20 / 16 / 16 | 164 |
| dev | 48 / 48 / 16 | 20 / 16 / 16 | 164 |
| test (held-out) | 48 / 48 / 16 | 30 / 24 / 24 | 190 |

Splits are by **template family**: each message template and each screenshot
(layout × font) family appears in exactly one split. Audio and webpage
detection are not implemented in SatyaLens, so no records exist for them.

## Commands (Windows, from this folder)

The project's backend virtualenv already has every dependency (Pillow, NumPy,
FastAPI, pytest, the `satyalens_analysis` package). Tesseract OCR must be
installed for the screenshot evaluation. Fonts are standard Windows fonts
(`C:/Windows/Fonts`).

```powershell
$py = "..\person 2\person2_app\backend\.venv\Scripts\python.exe"
$env:PYTHONUTF8 = "1"; $env:PYTHONDONTWRITEBYTECODE = "1"

# 1. (fresh machine only) dependencies for generation, validation and tests
& $py -m pip install -r requirements.txt

# 2. Generate (default seed 20261025, ~30 s); --no-images for text only
& $py generate_dataset.py
& $py generate_dataset.py --seed 7 --out dataset_seed7          # another seed, kept inside this folder

# 3. Validate (exit 0 = OK, 1 = critical problem, 2 = files missing)
& $py validate_dataset.py --report results\validation_report.json

# 4. Tests
& $py -m pytest

# 5. Evaluate the real application (dev may be re-run; test is evaluated once)
& $py evaluate_dataset.py --split dev
& $py evaluate_dataset.py --split test
& $py evaluate_dataset.py --report-only                          # rebuild report from saved predictions
```

### Reproducing everything from a clean checkout

```powershell
Remove-Item dataset, results -Recurse -Force      # only inside this folder
& $py generate_dataset.py                          # byte-identical dataset (tested)
& $py validate_dataset.py --report results\validation_report.json
& $py -m pytest
& $py evaluate_dataset.py --split dev
& $py evaluate_dataset.py --split test             # first run on a clean checkout
```

The generator is deterministic for a fixed seed, configuration, Pillow version
and font files (`tests/test_generation.py` checks byte equality, and that the
default command reproduces the shipped `dataset/`). The evaluation is
deterministic for fixed SatyaLens, Tesseract and model versions.

## Results (synthetic-only; full report: `results/evaluation_report.md`)

Predicted positive = SatyaLens decision `verify_first` or `high_risk`.

| Split | Task | n | TP | FP | FN | TN | Precision | Recall | F1 | FPR | FNR |
|---|---|---|---|---|---|---|---|---|---|---|---|
| dev | messages: suspicious vs legitimate | 96 | 40 | 20 | 8 | 28 | 0.667 | 0.833 | 0.741 | 0.417 | 0.167 |
| dev | screenshots: discrepancy vs consistent | 36 | 17 | 0 | 3 | 16 | 1.000 | 0.850 | 0.919 | 0.000 | 0.150 |
| **test** | messages: suspicious vs legitimate | 96 | 40 | 9 | 8 | 39 | 0.816 | 0.833 | 0.825 | 0.188 | 0.167 |
| **test** | screenshots: discrepancy vs consistent | 54 | 29 | 0 | 1 | 24 | 1.000 | 0.967 | 0.983 | 0.000 | 0.033 |

Also on both splits: 0 `low_concern` decisions; every `insufficient_evidence`
screenshot (16 dev, 24 test) unflagged; 0 severe quality warnings on legible
screenshots.

Read these with the caveats in `docs/limitations.md`. In particular, message
results come from ~12 templates per split, so they swing by template (e.g.
`delivery_update` 0/8 false alarms on dev, 8/8 on test). Also, the
screenshot layouts resemble ones used earlier to fix the SatyaLens extractor.
Known failures: `fake_payment_claim` messages are never flagged (0/16), and
`₹` is unreadable by Tesseract's English model.
