# Payment pipeline evaluation — dev split (SYNTHETIC)

- Generated 2026-10-25T04:49:36+00:00; config hash `22ffa36732a49360…`
- Path: real FastAPI routes → Person1Adapter → Tesseract → payment_checks → decision engine
- All screenshots are synthetic renderings; **not** a measure of real-world performance.

## Review-needed detection (legible images)

Positive = screenshot conflicts with the user's expectation or names a placeholder recipient. Predicted positive = decision `verify_first`/`high_risk`.

| n | TP | FP | FN | TN | precision | recall | F1 | FPR | FNR |
|---|---|---|---|---|---|---|---|---|---|
| 64 | 39 | 1 | 1 | 23 | 0.975 | 0.975 | 0.975 | 0.0417 | 0.025 |

## Per scenario

| scenario | n | expected flag | flagged | decisions |
|---|---|---|---|---|
| consistent | 8 | False | 0 | {'insufficient_evidence': 8} |
| amount_mismatch | 8 | True | 7 | {'verify_first': 7, 'insufficient_evidence': 1} |
| currency_mismatch | 8 | True | 8 | {'verify_first': 8} |
| recipient_mismatch | 8 | True | 8 | {'verify_first': 8} |
| reference_mismatch | 8 | True | 8 | {'verify_first': 8} |
| reference_format_only | 8 | False | 1 | {'insufficient_evidence': 7, 'verify_first': 1} |
| placeholder_recipient | 8 | True | 8 | {'verify_first': 8} |
| no_expected_values | 8 | False | 0 | {'insufficient_evidence': 8} |

## By layout

| layout | n | precision | recall | FPR | FNR |
|---|---|---|---|---|---|
| receipt | 33 | 0.9524 | 1.0 | 0.0769 | 0.0 |
| rows | 31 | 1.0 | 0.95 | 0.0 | 0.05 |

## Extraction exact match (raw OCR string == rendered string)

| field | n | read | missing | exact-match rate | wrong when read | high-conf reads | high-conf but wrong |
|---|---|---|---|---|---|---|---|
| amount | 64 | 59 | 5 | 0.8281 | 6 | 54 | 1 |
| recipient | 64 | 64 | 0 | 1.0 | 0 | 64 | 0 |
| reference | 64 | 64 | 0 | 0.9688 | 2 | 59 | 1 |

## Field comparison status vs construction

- **amount** (n=56): agreement 0.8571; false mismatches 3; withheld 5; {'truth=match -> got=match': 41, 'truth=match -> got=mismatch': 3, 'truth=match -> got=not_compared': 4, 'truth=mismatch -> got=mismatch': 7, 'truth=mismatch -> got=not_compared': 1}
- **currency** (n=56): agreement 0.8571; false mismatches 0; withheld 8; {'truth=match -> got=match': 40, 'truth=match -> got=not_compared': 8, 'truth=mismatch -> got=mismatch': 8}
- **recipient** (n=48): agreement 1.0; false mismatches 0; withheld 0; {'truth=match -> got=match': 40, 'truth=mismatch -> got=mismatch': 8}
- **reference** (n=48): agreement 1.0; false mismatches 0; withheld 0; {'truth=format_difference -> got=format_difference': 8, 'truth=match -> got=match': 32, 'truth=mismatch -> got=mismatch': 8}

## Image quality

- Degraded images with a severe warning: 16/16 ({'blur_2.5': {'n': 6, 'severe': 6}, 'blur_3.5': {'n': 5, 'severe': 5}, 'downscale_0.22': {'n': 5, 'severe': 5}})
- Expected-value mismatch findings raised on degraded images: 0
- Legible images with a (false) severe warning: 0/64
- Unreadable images: {'n': 4, 'decisions': {'insufficient_evidence': 4}, 'extraction_status': {'no_text': 2, 'no_fields_recognised': 2}, 'any_comparison_ran': 0, 'low_concern': 0}
- `low_concern` decisions anywhere: 0

Undefined ratios (empty denominator) are reported as `null`.
