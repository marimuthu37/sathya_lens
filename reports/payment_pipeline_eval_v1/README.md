# payment_pipeline_eval_v1

Synthetic evaluation of the payment-screenshot pipeline through the production
API path. Generator, labels, metrics and run-once guards:
`scripts/evaluate_payment_pipeline.py` (tests: `tests/test_payment_eval_script.py`).

| Directory | Split | Status |
|---|---|---|
| `dev/` | seed 1001 | used to find and fix errors — optimistic |
| `test/` | held-out v1, seed 2002 | registered in `protocol.json`, run once |
| `test2/` | held-out v2, seed 3003 | registered in `protocol_v2.json`, run once after a v1-driven fix |

Each directory has `metrics.json`, `summary.md` and `predictions.csv` (one row per
sample with rendered values, expected values, extracted values, statuses,
quality warnings, findings and decision). Rendered images are not committed;
they are regenerated deterministically from the seed.

`protocol.json` records SHA-256 hashes of the code as it was when v1 ran; the
code has changed since (documented in `protocol_v2.json`), so those file hashes
no longer match the working tree. The config hash still does, and a test checks it.

Reproduce (Windows, from the repo root, backend virtualenv, Tesseract installed):

    "person 2/person2_app/backend/.venv/Scripts/python.exe" scripts/evaluate_payment_pipeline.py --split dev --force

The held-out splits refuse to re-run once a result exists.

All data is synthetic; these numbers are **not** real-world performance.
