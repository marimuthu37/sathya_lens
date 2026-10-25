# Payment pipeline evaluation — test split (SYNTHETIC)

- Generated 2026-10-25T04:52:11+00:00; config hash `22ffa36732a49360…`
- Path: real FastAPI routes → Person1Adapter → Tesseract → payment_checks → decision engine
- All screenshots are synthetic renderings; **not** a measure of real-world performance.

## Review-needed detection (legible images)

Positive = screenshot conflicts with the user's expectation or names a placeholder recipient. Predicted positive = decision `verify_first`/`high_risk`.

| n | TP | FP | FN | TN | precision | recall | F1 | FPR | FNR |
|---|---|---|---|---|---|---|---|---|---|
| 120 | 64 | 2 | 11 | 43 | 0.9697 | 0.8533 | 0.9078 | 0.0444 | 0.1467 |

## Per scenario

| scenario | n | expected flag | flagged | decisions |
|---|---|---|---|---|
| consistent | 15 | False | 2 | {'insufficient_evidence': 13, 'verify_first': 2} |
| amount_mismatch | 15 | True | 13 | {'verify_first': 13, 'insufficient_evidence': 2} |
| currency_mismatch | 15 | True | 13 | {'insufficient_evidence': 2, 'verify_first': 13} |
| recipient_mismatch | 15 | True | 10 | {'verify_first': 10, 'insufficient_evidence': 5} |
| reference_mismatch | 15 | True | 15 | {'verify_first': 15} |
| reference_format_only | 15 | False | 0 | {'insufficient_evidence': 15} |
| placeholder_recipient | 15 | True | 13 | {'verify_first': 13, 'insufficient_evidence': 2} |
| no_expected_values | 15 | False | 0 | {'insufficient_evidence': 15} |

## By layout

| layout | n | precision | recall | FPR | FNR |
|---|---|---|---|---|---|
| receipt | 41 | 0.9259 | 1.0 | 0.125 | 0.0 |
| rows | 37 | 1.0 | 0.9 | 0.0 | 0.1 |
| stacked | 42 | 1.0 | 0.7 | 0.0 | 0.3 |

## Extraction exact match (raw OCR string == rendered string)

| field | n | read | missing | exact-match rate | wrong when read | high-conf reads | high-conf but wrong |
|---|---|---|---|---|---|---|---|
| amount | 120 | 107 | 13 | 0.7917 | 12 | 94 | 1 |
| recipient | 120 | 82 | 38 | 0.65 | 4 | 82 | 4 |
| reference | 120 | 120 | 0 | 0.9333 | 8 | 116 | 5 |

## Field comparison status vs construction

- **amount** (n=105): agreement 0.8095; false mismatches 5; withheld 15; {'truth=match -> got=match': 72, 'truth=match -> got=mismatch': 5, 'truth=match -> got=not_compared': 13, 'truth=mismatch -> got=mismatch': 13, 'truth=mismatch -> got=not_compared': 2}
- **currency** (n=105): agreement 0.8; false mismatches 0; withheld 21; {'truth=match -> got=match': 71, 'truth=match -> got=not_compared': 19, 'truth=mismatch -> got=mismatch': 13, 'truth=mismatch -> got=not_compared': 2}
- **recipient** (n=90): agreement 0.6556; false mismatches 0; withheld 31; {'truth=match -> got=match': 50, 'truth=match -> got=not_compared': 25, 'truth=mismatch -> got=mismatch': 9, 'truth=mismatch -> got=not_compared': 6}
- **reference** (n=90): agreement 0.9444; false mismatches 0; withheld 0; {'truth=format_difference -> got=format_difference': 15, 'truth=match -> got=format_difference': 5, 'truth=match -> got=match': 55, 'truth=mismatch -> got=mismatch': 15}

## Image quality

- Degraded images with a severe warning: 30/30 ({'blur_2.5': {'n': 10, 'severe': 10}, 'blur_3.5': {'n': 10, 'severe': 10}, 'downscale_0.22': {'n': 10, 'severe': 10}})
- Expected-value mismatch findings raised on degraded images: 0
- Legible images with a (false) severe warning: 0/120
- Unreadable images: {'n': 6, 'decisions': {'insufficient_evidence': 6}, 'extraction_status': {'no_text': 3, 'no_fields_recognised': 3}, 'any_comparison_ran': 0, 'low_concern': 0}
- `low_concern` decisions anywhere: 0

Undefined ratios (empty denominator) are reported as `null`.
