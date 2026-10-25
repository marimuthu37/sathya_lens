# Evaluation protocol

Written **before** the held-out (`test`) split was evaluated. The dev split had
been evaluated once (results/dev_predictions.jsonl); no change to the dataset,
generator or SatyaLens application was made after seeing dev results.

## Frozen inputs

| Item | Value |
|---|---|
| Generator seed | `20261025` (default) |
| Records SHA-256 (`dataset/synthetic_dataset.jsonl`) | `b73ece3c97ee8b91fbaa91c7cdd567a2c1105aa80f445ef25fdef97226adc7df` |
| Splits | train 164 · dev 164 · test 190 records |
| Application | SatyaLens working tree as found (not modified for this task) |

## What is sent to the application

Only each record's `input`: the PNG (or the message text as a `.txt` upload)
and, for screenshots, `input.expected_payment` — the same optional "expected
payment details" a user can type into the SatyaLens UI. `ground_truth` and
`meta` (labels, categories, rendered values, template ids) are never sent; they
are joined to the application's answers afterwards. Image file names are opaque
hashes, and `validate_dataset.py` fails if any label or category word appears in
an input.

## Decision rule and metrics

Predicted positive = the SatyaLens decision category is `verify_first` or
`high_risk`. No threshold is set or tuned by this evaluation.

| Modality | Positive | Negative | Reported separately |
|---|---|---|---|
| message_text | `suspicious` | `legitimate` | `ambiguous` (decision distribution only) |
| payment_screenshot | `discrepancy` | `consistent` | `insufficient_evidence` (flagged count, `low_concern` count, severe-quality count, comparisons run) |

Per task: confusion matrix (TP, FP, FN, TN), precision = TP/(TP+FP),
recall = TP/(TP+FN), F1, FPR = FP/(FP+TN), FNR = FN/(FN+TP). Every
denominator is written to `results/evaluation_summary.json`; a zero denominator
gives `null`, never 0 or 1. Also reported: field-status agreement for every
supplied expected field, false/missed mismatches, per-category decisions, and
the number of `low_concern` decisions (expected 0).

## Run policy

- `dev` may be re-run. `test` is evaluated **once**; `evaluate_dataset.py`
  refuses to overwrite `results/test_predictions.jsonl` unless
  `--allow-rerun-test` is given, which exists only to reproduce the result from
  a clean checkout (the pipeline is deterministic for fixed software versions).
- No rule, threshold, template or label is changed in response to test results.
  Any future change must be evaluated on newly generated data with a different
  seed **and** new template families, registered here first.
- No new held-out set is generated to search for better numbers.

## Not evaluable

Audio deepfake detection (unavailable), webpage/phishing-page detection (not
implemented), real-world accuracy (synthetic data cannot measure it), payment
settlement (not determinable from a screenshot).
