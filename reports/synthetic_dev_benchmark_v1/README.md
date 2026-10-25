# SYNTHETIC-ONLY development benchmark (dev-synth-v1)

> **Synthetic data only.** These numbers come from template-generated messages written for
> this project (`data/dev/synthetic_dev_messages_v1.csv`). They do **not** measure real-world
> scam-detection performance, do **not** represent Indian financial alerts, are **not** a Protocol v5
> result, and must **not** be used to select a model against the frozen test set.

- Model evaluated: `tfidf-logreg-v1-66bab05fd245` (deployed v1; unchanged: `artifact_unchanged_after_evaluation = True`)
- Dataset SHA-256: `f53e57d1b71d2baab7ae3b6a67c63e029846cba63ecb8e9afcabaff747e62190`; rows 168 ({'legitimate': 78, 'suspicious': 90})
- Training-data overlap check: 0 overlapping rows
- Classified 168, inconclusive 0 (by class {'legitimate': 0, 'suspicious': 0}); evaluation.json metrics use classified rows only

| Synthetic-only metric | Model | Rule engine | Majority class |
|---|---|---|---|
| Macro-F1 | 0.9093 | 0.6497 | 0.3488 |
| Suspicious recall | 0.9667 | 0.3889 | 1.0 |
| Legitimate false-positive rate | 0.1538 | 0.0 | 1.0 |

Files: `evaluation.json`, `summary.md`, `predictions.csv` (written by `text_classifier.evaluate`).
