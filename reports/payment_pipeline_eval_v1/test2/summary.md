# Payment pipeline evaluation — test2 split (SYNTHETIC)

- Generated 2026-10-25T04:56:03+00:00; config hash `6f794eb14a343ff3…`
- Path: real FastAPI routes → Person1Adapter → Tesseract → payment_checks → decision engine
- All screenshots are synthetic renderings; **not** a measure of real-world performance.

## Review-needed detection (legible images)

Positive = screenshot conflicts with the user's expectation or names a placeholder recipient. Predicted positive = decision `verify_first`/`high_risk`.

| n | TP | FP | FN | TN | precision | recall | F1 | FPR | FNR |
|---|---|---|---|---|---|---|---|---|---|
| 120 | 58 | 2 | 17 | 43 | 0.9667 | 0.7733 | 0.8593 | 0.0444 | 0.2267 |

## Per scenario

| scenario | n | expected flag | flagged | decisions |
|---|---|---|---|---|
| consistent | 15 | False | 1 | {'insufficient_evidence': 14, 'verify_first': 1} |
| amount_mismatch | 15 | True | 11 | {'verify_first': 11, 'insufficient_evidence': 4} |
| currency_mismatch | 15 | True | 11 | {'verify_first': 11, 'insufficient_evidence': 4} |
| recipient_mismatch | 15 | True | 9 | {'insufficient_evidence': 6, 'verify_first': 9} |
| reference_mismatch | 15 | True | 14 | {'verify_first': 14, 'insufficient_evidence': 1} |
| reference_format_only | 15 | False | 1 | {'insufficient_evidence': 14, 'verify_first': 1} |
| placeholder_recipient | 15 | True | 13 | {'verify_first': 13, 'insufficient_evidence': 2} |
| no_expected_values | 15 | False | 0 | {'insufficient_evidence': 15} |

## By layout

| layout | n | precision | recall | FPR | FNR |
|---|---|---|---|---|---|
| receipt | 41 | 1.0 | 0.875 | 0.0 | 0.125 |
| rows | 43 | 0.9583 | 0.8846 | 0.0588 | 0.1154 |
| stacked | 36 | 0.9333 | 0.56 | 0.0909 | 0.44 |

## Extraction exact match (raw OCR string == rendered string)

| field | n | read | missing | exact-match rate | wrong when read | high-conf reads | high-conf but wrong |
|---|---|---|---|---|---|---|---|
| amount | 120 | 102 | 18 | 0.6917 | 19 | 83 | 2 |
| recipient | 120 | 86 | 34 | 0.7 | 2 | 86 | 2 |
| reference | 120 | 118 | 2 | 0.9333 | 6 | 105 | 3 |

## Field comparison status vs construction

- **amount** (n=105): agreement 0.7048; false mismatches 1; withheld 30; {'truth=match -> got=match': 63, 'truth=match -> got=mismatch': 1, 'truth=match -> got=not_compared': 26, 'truth=mismatch -> got=mismatch': 11, 'truth=mismatch -> got=not_compared': 4}
- **currency** (n=105): agreement 0.7048; false mismatches 0; withheld 31; {'truth=match -> got=match': 63, 'truth=match -> got=not_compared': 27, 'truth=mismatch -> got=mismatch': 11, 'truth=mismatch -> got=not_compared': 4}
- **recipient** (n=90): agreement 0.6778; false mismatches 0; withheld 29; {'truth=match -> got=match': 53, 'truth=match -> got=not_compared': 22, 'truth=mismatch -> got=mismatch': 8, 'truth=mismatch -> got=not_compared': 7}
- **reference** (n=90): agreement 0.9444; false mismatches 3; withheld 2; {'truth=format_difference -> got=format_difference': 14, 'truth=format_difference -> got=mismatch': 1, 'truth=match -> got=match': 57, 'truth=match -> got=mismatch': 2, 'truth=match -> got=not_compared': 1, 'truth=mismatch -> got=mismatch': 14, 'truth=mismatch -> got=not_compared': 1}

## Image quality

- Degraded images with a severe warning: 30/30 ({'blur_2.5': {'n': 10, 'severe': 10}, 'blur_3.5': {'n': 10, 'severe': 10}, 'downscale_0.22': {'n': 10, 'severe': 10}})
- Expected-value mismatch findings raised on degraded images: 0
- Legible images with a (false) severe warning: 0/120
- Unreadable images: {'n': 6, 'decisions': {'insufficient_evidence': 6}, 'extraction_status': {'no_text': 3, 'no_fields_recognised': 3}, 'any_comparison_ran': 0, 'low_concern': 0}
- `low_concern` decisions anywhere: 0

Undefined ratios (empty denominator) are reported as `null`.
