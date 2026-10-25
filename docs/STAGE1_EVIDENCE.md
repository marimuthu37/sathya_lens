# SatyaLens — Stage 1 technical evidence (2026-10-25)

Everything here is reproducible from this repository. Numbers come from runs
recorded in `reports/`; nothing is estimated. **The official Stage 1 rubric was
not available in the repository**, so this document does not self-score.

## 1. What the system does (data flow)

```
React (Vite) ──HTTP──► FastAPI backend ───────────────────────────────────────► SQLite
  case → consent → upload   │ consent enforced server-side (403 otherwise)
  optional expected payment │ upload: MIME allow-list + magic bytes + size limit,
  details (image only)      │         UUID filenames, SHA-256 recorded
                            ▼
                 analysis_person1.Person1Adapter  (modular adapter)
                    │ image → satyalens_analysis.payment_ocr (Tesseract, 2 passes,
                    │         per-field confidence, image-quality assessment)
                    │ text  → context rules + TF-IDF/LogReg classifier
                    ▼
                 payment_checks: placeholder recipient, reference/description
                 consistency, expected-value comparison (Decimal; withheld when
                 OCR is low-confidence or the image is severely degraded)
                    ▼
                 decision engine (rules v1.1.0): Verify First / High Risk /
                 Low Concern / Insufficient Evidence + reasons + next steps
```

Honesty rules enforced in code and tests: no findings ≠ genuine; unavailable or
failed analysis never yields Low Concern; a screenshot never proves settlement;
observations from one OCR reading are marked correlated; image-quality warnings
are not fraud findings; ML text output alone never yields High Risk.

## 2. Measured performance — payment-screenshot pipeline (synthetic)

Script: `scripts/evaluate_payment_pipeline.py` (production path: real FastAPI
routes in-process → adapter → Tesseract → checks → decision). Protocols:
`reports/payment_pipeline_eval_v1/protocol.json`, `protocol_v2.json`, registered
with config hashes **before** each held-out split was generated. Each held-out
split runs once; the script refuses to re-run it.

Task: *does this screenshot need human review?* Positive = the screenshot
conflicts with the user's stated expectation (amount, currency, recipient,
reference) or names a placeholder recipient. Predicted positive = decision
`verify_first`/`high_risk`. Legible images only; degraded images reported apart.

| Split | Role | n (pos/neg) | TP | FP | FN | TN | Precision | Recall | F1 | FPR | FNR |
|---|---|---|---|---|---|---|---|---|---|---|---|
| dev (seed 1001) | errors found & fixed here | 64 (40/24) | 39 | 1 | 1 | 23 | 0.975 | 0.975 | 0.975 | 0.042 | 0.025 |
| **held-out v1** (seed 2002, unseen fonts, +unseen layout) | run once | 120 (75/45) | 64 | 2 | 11 | 43 | **0.970** | **0.853** | **0.908** | 0.044 | 0.147 |
| **held-out v2** (seed 3003, fonts used nowhere else) | run once, after a v1-driven fix | 120 (75/45) | 58 | 2 | 17 | 43 | **0.967** | **0.773** | **0.859** | 0.044 | 0.227 |

Dev numbers are optimistic by construction (fixes were made on dev). Held-out v2
is lower than v1: the stacked-layout fix made after v1 did **not** work on v2
(Tesseract inserts a blank line the fix did not allow for). That bug has since
been fixed and unit-tested on the exact OCR text, but **that final fix is not
evaluated on any held-out split**.

Secondary results (held-out v1 / v2):
- Degraded images (blur σ 2.5/3.5, ×0.22 downscale): severe quality warning on **30/30 and 30/30**; expected-value mismatch findings raised on degraded images: **0 and 0**.
- Legible images with a false severe warning: **0/120 and 0/120**.
- Unreadable (blank/noise) images: all Insufficient Evidence, no comparison ran.
- `low_concern` decisions anywhere: **0**.
- Recipient extraction missing on stacked layout: dominant false-negative cause (8/11 FN in v1).
- `₹` is not in Tesseract's English model: it is dropped or read as a leading digit (e.g. `₹20,674.73` → `220,674.73`) — causes of all FPs and several FNs. The mismatch detail now says so.
- Tesseract confidence ≠ correctness: e.g. held-out v1 had 5 wrong references read at *high* confidence.

**This is synthetic data.** It measures the pipeline's logic and OCR robustness
on rendered screenshots; it does **not** measure real-world fraud detection.

## 3. Text modality

Existing reports, unchanged here: `reports/real_world_test_v1_evaluation/`
(deployed model on a real SMS set: macro-F1 0.33 vs 0.48 majority baseline;
67% legitimate FPR — the model is advisory only) and
`reports/synthetic_dev_benchmark_v1/` (synthetic, dev-only). The real-world set
is retired from model selection (`docs/DATA_PLAN_V3.md`). Real-world text
accuracy remains **NOT EVALUABLE** for a deployable claim.

## 4. Demonstration sequence (verified live 2026-10-25 in Edge, scratch DB)

Start backend with `ANALYSIS_MODULE_ENABLED=true` and a scratch `DATABASE_URL`,
then the frontend. For each row: New Case → consent → upload → (expected details)
→ Analyse → Get Decision.

| # | Input | Expected details | Observed result |
|---|---|---|---|
| 1 | consistent mock-up (€25.00) | 25.00 EUR, TID-7823419 | 3/3 match; low-confidence payee caution; **Insufficient Evidence** (no claim of genuineness) |
| 2 | €1,000.00 mock-up | 100.00 EUR | Amount differs → **Verify First** |
| 3 | Spotify mock-up | recipient "Netflix" | Recipient differs → **Verify First** |
| 4 | TID-241-- mock-up | reference TID-241 | "Formatting differs only" (no mismatch); placeholder + reference/description observations, marked correlated → **Verify First** |
| 5 | same mock-up blurred σ1.5 | 250.00 EUR | Severe blur + low OCR confidence; 0 of 2 comparisons run → **Insufficient Evidence** |
| 6 | legitimate bill SMS (.txt) | — | No rule fired → **Insufficient Evidence** |
| 7 | "account blocked… share the OTP" SMS | — | Urgency + OTP-request rules + ML (correlated) → **Verify First** (never High Risk from ML/rules alone) |
| 8 | noisy mock-up, no expectations | none | "No comparison was made" → **Insufficient Evidence** |

Every payment view shows *Payment verification: Not verified — cannot be
determined from a screenshot* and the next step: confirm in your own bank or
payment app.

## 5. Feature status

| Feature | Status | Evidence |
|---|---|---|
| Case, consent, upload, SHA-256 | Delivered | backend tests; live run |
| Payment OCR + per-field confidence | Delivered | package tests; eval above |
| Expected amount/currency/recipient/reference | Delivered (UI + API + DB) | `test_expected_payment.py`; live run |
| Image-quality assessment | Delivered, thresholds synthetic-calibrated | eval §2 |
| Correlated-observation handling | Delivered | decision tests; live #4 |
| Text rules + ML classifier | Delivered, advisory only | text reports §3 |
| Audio deepfake detection | **Not available** (model loader not implemented) | contract |
| Webpage/phishing-page detection | **Not implemented** | README |
| Real-time payment/settlement verification | **Not implemented, by design impossible from a screenshot** | — |
| Real-world accuracy (any modality) | **NOT EVALUABLE** | — |

## 6. Known failure cases and mitigations

| Failure | Mitigation in place | Remaining risk |
|---|---|---|
| `₹` unreadable by Tesseract `eng` | INR shown as code is compared; mismatch detail explains the symbol-as-digit effect | INR-symbol screenshots get false or withheld amount checks |
| Label above value (stacked layouts) | extractor extended; unit-tested on real OCR output | not held-out-evaluated |
| High-confidence misreads | low-confidence values withheld; raw values always shown | high-confidence misreads still compared |
| Blur / tiny text | severe warning withholds comparisons | thresholds from synthetic fixtures only |
| Mixed / shared currency symbols ($, ¥, Rs) | rejected as user input; extracted ones not confirmed | — |

## 7. Engineering contribution (this iteration)

Fixed from measured evidence: currency codes dropped by the amount extractor
(dev currency-mismatch recall 1/8 → 8/8); OCR O/0-style confusions treated as
formatting differences; case analysis status never updated; overview showing
"Pending analysis" after a decision; SQL echo logging user content by default.
Added the reproducible, pre-registered evaluation harness above.
