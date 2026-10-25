# SatyaLens Analysis Package
## Person 1: AI Detection & Evidence Analysis
### RAKSHAM AI Cybersecurity Hackathon 2026

---

## Verified Status (2026-10-24)

| Component | Status | Notes |
|-----------|--------|-------|
| Context analyser | **Operational** | Pure Python, no external deps |
| ML text classifier | **Runs; no model has passed real-world validation** | Deployed v1 flags 67% of legitimate real SMS. Improved candidate v2 (not deployed) fixes false positives (1.3%) but misses 63% of real smishing — see [Training v2](#training-v2-and-model-selection). A `suspicious` prediction must **not** independently trigger a fraud verdict. |
| Payment OCR (Python libs) | **Operational** | pytesseract 0.3.13, Pillow 13, cv2 5.0 |
| Payment OCR (Tesseract binary) | **Operational** | Tesseract 5.5.3 via Scoop, auto-detected |
| Audio detection (ML deps) | Not installed | torch/torchaudio/huggingface_hub absent |
| Audio detection (model loader) | Integration blocker | AASIST requires custom loader; not yet built |

**Test results (verified run, 2026-10-25):**
```
893 collected  |  889 passed  |  4 skipped  |  0 failed  (exit 0)
Coverage: 96%
Command: python -m pytest tests/ -q -rs --cov=satyalens_analysis --cov-report=term-missing -W error::DeprecationWarning -p no:cacheprovider
```

## Package Architecture

```
satyalens_analysis/
  __init__.py          exports run_analysis()
  unified.py           run_analysis(): runs 4 components, each in its own try/except
  schema.py            status enum + result dataclasses (schema_version "1.0")
  context_analyser.py  10 deterministic regex rules           -> result["context"]
  text_classifier.py   TF-IDF + LogisticRegression (train/load/infer, CLI) -> result["text_classifier"]
  payment_ocr.py       Tesseract OCR + field reconciliation   -> result["payment"]
  audio_detector.py    audio interface, always 'unavailable'  -> result["audio"]
  models/              trained artifact + metadata (gitignored; created by training)
data/                  synthetic dataset CSV + provenance JSON
scripts/               generate_synthetic_dataset.py
tests/                 pytest suite
```

The rule-based context analyser and the ML classifier are **separate evidence
sources over the same text** — they are correlated, not independent. This
package never fuses them into a verdict; that is Person 2's evidence-fusion step.

---

## Quick Start

### 1. Core dependencies (always required)

```bash
pip install pytesseract Pillow opencv-python numpy
```

### 2. Tesseract OCR binary — Windows (no admin required)

**Option A — Scoop (recommended, user-level, no UAC):**
```powershell
# Install Scoop (one-time)
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser -Force
Invoke-RestMethod -Uri https://get.scoop.sh | Invoke-Expression

# Install Tesseract + English language data
scoop install tesseract

# Download eng.traineddata (Scoop does not bundle it automatically)
$tessdata = "$env:USERPROFILE\scoop\persist\tesseract\tessdata"
Invoke-WebRequest -Uri https://github.com/tesseract-ocr/tessdata_fast/raw/main/eng.traineddata `
    -OutFile "$tessdata\eng.traineddata" -UseBasicParsing
```

**Option B — UB-Mannheim installer (requires admin):**
```
Download: https://github.com/UB-Mannheim/tesseract/wiki
Default install path: C:\Program Files\Tesseract-OCR\tesseract.exe
```

> **The package auto-detects the Tesseract binary** in the Scoop location, both
> Program Files paths, and system PATH — no manual configuration required.
> Without Tesseract, `analyse_payment()` returns `status='unavailable'`.

### 3. Audio ML dependencies (optional, large — not yet functional)

```bash
# Install these ONLY when the custom AASIST loader is complete (see below)
pip install torch torchaudio --index-url https://download.pytorch.org/whl/cpu
pip install huggingface_hub

# Or all audio extras:
pip install -e ".[audio]"
```

### 4. Install the package (editable)

```bash
pip install -e .
```

### 4b. ML text classifier (optional `[ml]` extra) — install and train

```bash
pip install -e ".[ml]"          # scikit-learn (+ joblib, scipy)

# (optional) regenerate the synthetic dataset — it is already in data/
python scripts/generate_synthetic_dataset.py

# Train + evaluate + save. Never happens automatically at startup or per request.
python -m satyalens_analysis.text_classifier train --data data/synthetic_financial_messages_v1.csv

# Try one message against the saved model
python -m satyalens_analysis.text_classifier predict --text "Share the OTP you received to cancel the payment"
```

Without scikit-learn or without a trained artifact, `result["text_classifier"]["status"]`
is `"unavailable"`; the other components are unaffected.

### 5. Run tests

```bash
# Full suite:
python -m pytest tests/ -v

# With coverage:
python -m pytest tests/ -v --cov=satyalens_analysis --cov-report=term-missing

# With warnings-as-errors (clean check):
python -m pytest tests/ -v --cov=satyalens_analysis -W error::DeprecationWarning
```

### 6. Run OCR verification (after Tesseract install)

```bash
python verify_ocr.py
```

---

## Calling the Package — Person 2 Integration

```python
from satyalens_analysis import run_analysis

result = run_analysis(
    case_id="CASE-001",                            # echoed back, no logic applied
    audio_path="/uploads/voice.wav",               # optional; currently → unavailable
    screenshot_path="/uploads/payment.png",        # optional; auto-detects Tesseract
    message_text="Act now or you'll be blocked!",  # optional; rules + ML classifier
    expected_payment_fields={                      # optional; compared against OCR
        "amount": "5000",
        "payee_name": "John Doe",
        "transaction_id": "UTR123456789",
    },
)
# result is a JSON-serialisable dict
return JSONResponse(content=result)
```

Reading the text findings (actual keys only):

```python
ctx = result["context"]          # rule evidence
ml  = result["text_classifier"]  # ML evidence — same text, correlated with ctx

rule_ids = [f["rule_id"] for f in ctx["flags"]]
if ml["status"] == "analysed":
    ml_class = ml["predicted_class"]     # "suspicious" | "legitimate"
    ml_score = ml["raw_score"]           # uncalibrated; not a fraud probability
    ml_terms = [t["term"] for t in ml["top_terms"]]
else:
    # "unavailable" | "failed" | "inconclusive" | "not_applicable"
    # -> no ML evidence. NEVER treat as legitimate.
    ml_class = None
```

See [`integration_example.py`](integration_example.py) for the complete FastAPI endpoint pattern including file upload and cleanup.

---

## Response Schema (v1.0)

```json
{
  "schema_version": "1.0",
  "case_id": "CASE-001",
  "audio": {
    "status": "analysed | inconclusive | failed | unavailable | not_applicable",
    "finding": "genuine_likely | spoof_likely | inconclusive",
    "reliability": "limited | moderate",
    "reason": "Human-readable explanation",
    "raw_score": 0.82,
    "input_quality": ["List of input quality warnings"],
    "limitations": ["Model/detection limitations"]
  },
  "payment": {
    "status": "analysed | failed | unavailable | not_applicable",
    "raw_text": "Full OCR text (truncated at 4000 chars)",
    "fields": [
      {
        "field_name": "amount",
        "extracted_value": "5000.00",
        "expected_value": "5000",
        "match": true,
        "confidence": "low | medium | high"
      }
    ],
    "mismatches": ["field: expected=X, extracted=Y"],
    "limitations": ["OCR caveats and settlement disclaimer"]
  },
  "context": {
    "status": "analysed | failed | not_applicable",
    "flags": [
      {
        "rule_id": "URGENCY_PRESSURE",
        "label": "Extreme Urgency / Time Pressure",
        "severity": "low | medium | high",
        "explanation": "Why this rule fired"
      }
    ],
    "findings": ["Narrative summary sentences"],
    "limitations": ["Rule-based caveats"]
  },
  "text_classifier": {
    "status": "analysed | inconclusive | failed | unavailable | not_applicable",
    "predicted_class": "suspicious | legitimate",
    "raw_score": 0.599322,
    "model_version": "tfidf-logreg-v1-66bab05fd245",
    "training_data": "satyalens-synthetic-financial-messages v1 (synthetic)",
    "reason": "Human-readable explanation",
    "top_terms": [{"term": "the otp", "weight": 0.21}],
    "limitations": ["Model caveats incl. correlation with context rules"]
  },
  "errors": ["Non-fatal component errors"]
}
```
(`predicted_class` and `raw_score` appear only when `status == "analysed"`.
The values above are illustrative of the shape; see the contract for details.)

### Schema invariants Person 2 can rely on

| Invariant | Guarantee |
|---|---|
| All top-level keys always present | `schema_version`, `case_id`, `audio`, `payment`, `context`, `text_classifier`, `errors` |
| `audio.finding` / `raw_score` | Present **only** when `status == "analysed"` |
| `text_classifier.predicted_class` / `raw_score` | Present **only** when `status == "analysed"` |
| `payment.fields` | Always a list (empty if nothing extracted) |
| `context.flags` | Always a list (empty if no rules fired) |
| `errors` | Always a list (empty if no component errors) |
| Failed/unavailable component | **Never** implies genuine or safe |
| No verdict fields | `verdict`, `risk`, `risk_level`, `decision` are **never** present |

---

## Context Analysis — 10 Rules

| Rule ID | Label | Severity |
|---------|-------|----------|
| `URGENCY_PRESSURE` | Extreme Urgency / Time Pressure | High |
| `SECRECY_DEMAND` | Secrecy / Confidentiality Demand | High |
| `CREDENTIAL_REQUEST` | Credential / OTP / PIN Request | High |
| `GIFT_CARD_PAYMENT` | Gift Card as Payment | High |
| `REFUND_OVERPAYMENT_SCAM` | Refund / Overpayment Setup | High |
| `REMOTE_ACCESS_REQUEST` | Remote Access Software Request | High |
| `THREAT_OR_COERCION` | Threat or Coercion Linked to Payment | High |
| `CRYPTO_PAYMENT` | Cryptocurrency Transfer Request | Medium |
| `NEW_PAYEE_PRESSURE` | Unsolicited New Payee / Account | Medium |
| `IMPERSONATION_CUE` | Authority / Impersonation Cue | Medium |

Rules are deterministic keyword/pattern-based. The component does **not** issue a final verdict.

Known false positive: a bare "OTP" mention fires `CREDENTIAL_REQUEST`, so genuine
bank OTP-delivery SMS ("Your OTP is …, do not share it") are flagged.
**Fixed 2026-10-24:** `CREDENTIAL_REQUEST` now needs a request verb ("share / send /
tell / enter … OTP / PIN / code", plus Hinglish "OTP bhejo") that is not directly
negated ("do not share"); routine expiry wording ("expires in 5 minutes") no longer
triggers `URGENCY_PRESSURE`; `GIFT_CARD_PAYMENT` matches plurals. On the challenge
suite (development data): legitimate falsely flagged 5/26 → 0/26, scams flagged
4/16 → 9/16. The rule-baseline rows in `reports/model_selection_v2/` predate this fix.

---

## ML Text Classifier

### Pipeline
`TfidfVectorizer` (word 1–2-grams, letters-only tokens so amounts/OTPs/account
digits are not features, sublinear TF) → `LogisticRegression` (C=1.0,
`class_weight="balanced"`, max_iter=1000). Hyperparameters are fixed defaults;
**no tuning was done** (there is no validation set), so the test split was
never used for model selection.

### Dataset — `data/synthetic_financial_messages_v1.csv`
| Item | Value |
|---|---|
| Source | Hand-written templates in `scripts/generate_synthetic_dataset.py`, slot-filled with fictional banks / names / `.example` domains (seed 20261024) |
| Real data | **None.** No real messages, customer records or personal information |
| Licence | Created for this project; no third-party data; repository licence (MIT per `pyproject.toml`) |
| Rows / groups | 319 rows, 73 groups (36 suspicious campaigns, 37 legitimate message types) |
| Labels | `suspicious` 142, `legitimate` 177 |
| Label meaning | `suspicious` = template written to exhibit a known financial social-engineering pattern; `legitimate` = template written to resemble routine financial communication. Labels reflect the author's intent, not verified outcomes |
| Hard negatives included | Genuine OTP alerts that say "do not share", bank fraud advisories, branch-KYC reminders, login alerts, internal "urgent" approvals |
| Columns | `id, text, label, group` (+ `.provenance.json` sidecar) |

### Training procedure (`python -m satyalens_analysis.text_classifier train`)
1. Validate CSV: required columns, labels ∈ {`legitimate`,`suspicious`} (case-sensitive), non-empty text/group, single label per group, both classes present.
2. De-duplicate on normalised text (lower-case, numbers → `0`, whitespace collapsed); identical normalised text with conflicting labels aborts training.
3. Grouped + stratified split: `StratifiedGroupKFold(n_splits=4, shuffle=True, random_state=42)`, first fold = test (~25%). Whole groups go to one side.
4. Leakage check: zero shared groups and zero shared normalised texts, or training aborts.
5. Fit on train only; evaluate on test; compare to `DummyClassifier("most_frequent")`.
6. Supplementary 5-fold grouped CV over all data (stability estimate only).
7. Save `satyalens_analysis/models/text_classifier.joblib` + `text_classifier.meta.json`
   (model_version, SHA-256, scikit-learn & Python versions, dataset SHA-256 and provenance, split, metrics).

### Evaluation results — SYNTHETIC DEVELOPMENT EVIDENCE ONLY

> These numbers measure generalisation to **held-out synthetic template families
> written by the same author**. They do **not** indicate real-world
> scam-detection performance and must not be quoted as such.

Run: 2026-10-24, model `tfidf-logreg-v1-66bab05fd245`, scikit-learn 1.9.1, seed 42.

| Split | Rows | Groups | legitimate | suspicious |
|---|---|---|---|---|
| Train | 240 | 55 | 133 | 107 |
| Test (held out) | 79 | 18 | 44 | 35 |

| Metric (held-out test) | Model | Majority baseline |
|---|---|---|
| Accuracy | 0.886 | 0.557 |
| Precision — suspicious | 0.825 | 0.000 |
| Recall — suspicious | 0.943 | 0.000 |
| F1 — suspicious | 0.880 | 0.000 |
| Precision — legitimate | 0.949 | 0.557 |
| Recall — legitimate | 0.841 | 1.000 |
| F1 — legitimate | 0.892 | 0.715 |
| Macro-avg F1 | 0.886 | 0.358 |

Confusion matrix (rows = true, cols = predicted, order legitimate / suspicious):
model `[[37, 7], [2, 33]]`; baseline `[[44, 0], [35, 0]]`.

Averaging: per-class metrics are shown for both labels with `suspicious` as the
positive class; macro-average weights the two classes equally so the minority
class cannot be hidden by the majority class (the baseline's 0.557 accuracy vs
0.358 macro-F1 illustrates why).

Supplementary grouped 5-fold CV (mean ± std over folds): accuracy 0.874 ± 0.074,
macro-F1 0.871 ± 0.076, suspicious recall 0.866 ± 0.131, suspicious precision
0.866 ± 0.101. The fold-to-fold spread shows how small the dataset is.

**Error analysis of the held-out test set (9 errors):**
- 2 genuine OTP-delivery alerts ("Never share this code…") → predicted *suspicious*. The model, like the `CREDENTIAL_REQUEST` rule, over-reacts to OTP wording when it has not seen that exact template family.
- 1 dinner-bill split request, 2 "order shipped… out for delivery today" notices, 1 quarter-close reminder → *suspicious* (words like "send", "today", "delivery").
- 2 electricity-disconnection scams → *legitimate* (they share vocabulary with genuine utility bills).

### Inference
`classify_text(text)` (called by `run_analysis`) loads the artifact once per
process (cached), after checking: metadata present and well-formed, artifact
SHA-256 == metadata, installed scikit-learn version == training version,
object is the expected pipeline with classes `["legitimate","suspicious"]`.
It never trains.

`raw_score` = `predict_proba` for `suspicious`. **Calibration has not been
evaluated; it is not a fraud probability.** `predicted_class` is the argmax
(≡ `raw_score >= 0.5`, scikit-learn's default); no thresholds were invented.
If no word of the message is in the vocabulary, the status is `inconclusive`
(the output would otherwise come only from the intercept).

### Model limitations
- Trained on small, synthetic, single-author data; real-world performance is unknown.
- Bag-of-words: no sender identity, link reputation or conversation history; easily evaded by paraphrase; top terms can include stop-words ("for", "be").
- Correlated with the context rules (same text, overlapping cues) — never an independent confirmation.
- English plus a little romanised Hindi only.
- **Security:** the artifact is a joblib pickle — loading it can execute code. Only load artifacts you trained or received from a trusted source. The SHA-256 check detects corruption or mismatched files; it is not a signature.
- Artifacts are bound to the exact scikit-learn version; after upgrading scikit-learn, retrain.

---

## Real-world evaluation (independent test set)

**Evaluation only.** `data/real_world_test_v1.csv` is never used to train, tune or
change rules, thresholds or parameters.

### Source
| Item | Value |
|---|---|
| Dataset | "SMS PHISHING DATASET FOR MACHINE LEARNING AND PATTERN RECOGNITION" — Sandhya Mishra, Devpriya Soni (Jaypee Institute of Information Technology) |
| Official page | https://data.mendeley.com/datasets/f45bkkt8pr/1 (Mendeley Data, V1, 2022-06-20, DOI 10.17632/f45bkkt8pr.1) |
| Licence | **CC BY 4.0** (stated on the dataset page) — attribution required (see provenance JSON) |
| File / SHA-256 | `Dataset_5971.zip` / `9bbf3188…b233cc3` (matches the Mendeley public API) |
| Retrieved | 2026-10-24 |
| Source labels | ham 4844, smishing 638 (`Smishing` 616 + `smishing` 22), spam 489 (`spam` 466 + `Spam` 23) |
| Collection (as described) | Images from the Internet converted to text with Python; labelled by source |

### Preparation (`python scripts/prepare_real_world_test.py`)
Label mapping: `smishing → suspicious`, `ham → legitimate`; `spam` is **excluded
as ambiguous** (defined as unsolicited messaging, not fraud — mapping it would
require relabelling). Every one of the 5,971 source rows ends up in exactly one
place:

| Outcome | Rows | Where |
|---|---|---|
| Accepted | **5,293** (legitimate 4,833 / suspicious 460) | `data/real_world_test_v1.csv` |
| Ambiguous (`spam`) | 489 | `data/real_world_test_v1.excluded.csv` |
| Duplicate (normalised text) | 189 | `…excluded.csv` |
| Conflicting labels on identical text | 0 | — |
| Overlap with synthetic training corpus (exact / skeleton / Jaccard ≥ 0.8) | 0 | — |
| Rejected (empty text / unknown label) | 0 | — |

Also written: `real_world_test_v1.provenance.json`, `real_world_test_v1.validation.json`.
Raw zip is kept unchanged in `data/raw/` (gitignored; re-downloaded and hash-checked).

Columns: `id, text, label, source, source_label, source_row, heuristic_topic, near_duplicate_cluster`.
These differ from the training CSV (`text, label, group`) on purpose: the source
has **no campaign/group ids or categories**, so none were invented.
`near_duplicate_cluster` is derived from text (23 multi-row clusters, 50 rows)
and `heuristic_topic` is a transparent keyword heuristic (`financial` 318 /
`other` 4,975) — reporting aids, not ground truth. Phone-like digit runs
(353 rows) and e-mail local parts (13 rows) were masked; the classifier's
tokens are letters-only, so digit masking cannot change predictions (tested).

### Run
```bash
python -m satyalens_analysis.text_classifier evaluate \
    --data data/real_world_test_v1.csv \
    --model satyalens_analysis/models/text_classifier.joblib
# -> reports/real_world_test_v1_evaluation/evaluation.json + summary.md
```
The command loads the saved artifact (SHA-256 + scikit-learn version checked),
never fits (tested by making every `fit` raise), confirms the artifact hash is
unchanged afterwards, and fails with exit code 2 on an invalid dataset/model or
any overlap with the training corpus.

### Results — VERIFIED REAL-WORLD EVALUATION (2026-10-24)
Model `tfidf-logreg-v1-66bab05fd245` (sha256 `66bab05f…2fb0288ee`), dataset sha256
`f83b8c30…`, scikit-learn 1.9.1. 5,085 rows classified; 208 legitimate rows were
`inconclusive` (no in-vocabulary word) and are excluded from the metric
denominators.

| Metric (5,085 classified rows) | Model | Majority baseline ("legitimate") |
|---|---|---|
| Accuracy | 0.364 | 0.910 |
| Precision / recall / F1 — suspicious | 0.096 / 0.717 / 0.169 | 0 / 0 / 0 |
| Precision / recall / F1 — legitimate | 0.921 / 0.328 / 0.484 | 0.910 / 1.000 / 0.953 |
| Macro-avg F1 | **0.327** | 0.476 |
| Suspicious false-negative rate (FN / all classified suspicious) | 0.283 | 1.000 |
| Legitimate false-positive rate (FP / all classified legitimate) | **0.672** | 0.000 |

Confusion matrix (rows true, cols predicted; legitimate, suspicious): `[[1519, 3106], [130, 330]]`.
One row per near-duplicate cluster (5,061 rows): macro-F1 0.324 — duplicates do not drive the result.

| Subgroup | n | Accuracy | Recall legit | Recall susp |
|---|---|---|---|---|
| source_label = ham | 4,833 | 0.328 | 0.328 | — |
| source_label = smishing | 460 | 0.717 | — | 0.717 |
| heuristic_topic = financial | 318 | 0.443 | 0.314 | 0.591 |
| heuristic_topic = other | 4,975 | 0.358 | 0.329 | 0.778 |

**Interpretation.** The synthetic-trained model does **not** generalise to this
real data: it is worse than always answering "legitimate" on macro-F1 and
flags about two-thirds of genuine messages. A read-only diagnostic (no changes
made) shows the false positives are driven by conversational function words
("and", "my", "now", "so", "to"): the synthetic legitimate class is almost all
terse transactional alerts, so the model learned "conversational style ⇒
suspicious". The 2,473-term vocabulary is also tiny.

**What this does and does not show.** It is a genuine, independent, real-message
test, but domain-mismatched: mostly UK/Singapore personal SMS (2000s–2010s;
described in a systematic review as a partial clone of the UCI SMS Spam
Collection), `smishing` includes premium-rate/prize scams, and there are few
genuine bank alerts. It shows the current model is unfit for real messages;
it is not a benchmark for Indian financial-scam detection. Labels are the
source authors' and were not re-annotated.

### Other sources examined (not used)
| Source | Why not used |
|---|---|
| UCI SMS Spam Collection (https://archive.ics.uci.edu/dataset/228/sms+spam+collection, CC BY 4.0) | ham/spam only; "spam" is unsolicited marketing, not fraud; the chosen dataset already contains much of it |
| Smishtank "Smishing Dataset I" (arXiv 2402.18430; https://smishtank.com/dataset) | smishing only (no legitimate class); dataset page returned 404 and no dataset licence could be verified (CC BY-NC-SA 4.0 covers the paper) |
| Mendeley "Financial scams detection dataset" (znsk27yk3h) | licence not confirmed in this session |
| Smishing-4C, MIMICS-3500 | licence not stated / available on request only |

---

## Training v2 and model selection

`data/real_world_test_v1.csv` stays **frozen**: never used for fitting, feature
selection, threshold tuning or augmentation. It was read only to remove
overlapping training rows, and evaluated exactly once at the end.

### Why v1 flagged ordinary conversation (from training data and weights only)
- The strongest suspicious feature was **`and`** (+0.82); `now`, `send`, `to`, `my`, `so` were also positive.
  In the synthetic set, `and` occurs in 39/142 suspicious vs 9/177 legitimate messages; `my` in 10 vs 0.
- The synthetic legitimate class is almost entirely terse transactional alerts, so its strongest features were
  template artifacts (`on`, `for`, month abbreviations `sep`/`aug`, `ref`, `credited`). Slot artifacts also leaked
  (`example` from `.example` scam domains, `kavya ggb` from a UPI handle).
- Net effect: "conversational English ⇒ suspicious". On real chat it fired on function words.

### Data plan and sources
| Source | Role | Licence (verified) | Real? | Used |
|---|---|---|---|---|
| SatyaLens synthetic v1 | train | project-created | synthetic | 319 rows |
| [Ridham115/indian-scam-sms-synthetic-audited](https://huggingface.co/datasets/Ridham115/indian-scam-sms-synthetic-audited) rev `45e92c38` | train | **CC BY 4.0** (card metadata) | LLM-generated, rubric-audited | 700 `gpt-oss-120b` rows; 880 Gemini rows excluded pending generator-terms review |
| [MultiWOZ 2.2](https://github.com/budzianowski/multiwoz) commit `fe0c8e65`, `train/dialogues_001.json` | train (legitimate) | **MIT**, © 2019 Paweł Budzianowski (LICENSE file) | human-written | 300 dialogues × 1 user + 1 system turn |
| `data/challenge_suite_v1.csv` | validation (behavioural) | project-created | author-written, intent-labelled | 42 rows, never trained on |
| Rejected: ScamShield (sidzzz07, MIT) | — | — | sources undocumented | no |
| Rejected: Bengali SMS smishing (MIT) | — | — | sources undocumented; promo/smish/normal labels | no |
| Rejected: UCI SMS Spam / NUS / "Super SMS" / SpamHunter | — | — | share sources with the frozen set, or licence unverified | no |

No openly licensed **real** Indian bank/UPI alert or scam SMS corpus was found;
Kaggle UPI datasets found were synthetic transaction records, not message text.
Real conversational English that does not come from NUS/UCI was available only as
MultiWOZ booking dialogue.

### Training pool v2 — `python scripts/prepare_training_pool_v2.py`
1,614 rows (suspicious 499 / legitimate 1,115), 415 groups (SYN template families,
R115 label:topic:language batches, MultiWOZ dialogue ids). Downloads are pinned and
SHA-256-checked into `data/raw/` (gitignored). 5 duplicates removed;
**0 rows overlapped the frozen set or the challenge suite** (exact / skeleton /
Jaccard ≥ 0.8). Every exclusion is in `data/training_pool_v2.excluded.csv`;
provenance and attributions in `data/training_pool_v2.provenance.json`.

### Model selection — `python scripts/select_text_model_v2.py`
Validation = grouped 5-fold CV over the pool (`StratifiedGroupKFold`, seed 42)
+ challenge suite. Rule fixed in advance: max pooled out-of-fold macro-F1, ties
(≤ 0.005) → lower legitimate FPR. Full tables (per-class P/R/F1, confusion
matrices, FPR/FNR, vocabulary coverage, per-source, per-category):
`reports/model_selection_v2/summary.md`.

| Model | OOF macro-F1 | OOF legit FPR | OOF susp recall | Challenge acc | Challenge legit FPR |
|---|---|---|---|---|---|
| Majority baseline | 0.409 | 0.000 | 0.000 | 0.619 | 0.000 |
| Rule baseline (any context flag) | 0.657 | 0.083 | 0.373 | 0.595 | 0.192 |
| Current deployed v1 (on unseen R115+MWOZ rows) | 0.578 | 0.537 | 0.904 | 0.690 | 0.462 |
| A: v1 config on pool | 0.867 | 0.129 | 0.902 | 0.857 | 0.038 |
| E: + English stop-words, C=3 | 0.877 | 0.108 | 0.890 | 0.881 | 0.077 |
| **G: char_wb 3–5-grams (selected)** | **0.890** | **0.094** | 0.898 | **0.905** | 0.115 |

Caveat: MultiWOZ rows are classified 100% correctly by every config — booking
dialogue is trivially separable, which inflates OOF scores. SYN and R115 are
synthetic; the challenge suite is small and written by the same author as SYN.

Candidate `tfidf-logreg-v1-a0753aa43caf` was trained into
`satyalens_analysis/models/candidates/v2/` (gitignored). **The deployed artifact
`satyalens_analysis/models/text_classifier.joblib` was not changed.**
(The model-version prefix is the generic family name; the hash suffix identifies it.)

### Frozen-test result for candidate v2 — evaluated once
`reports/real_world_test_v1_evaluation_v2_candidate/` (the v1 report in
`reports/real_world_test_v1_evaluation/` is preserved unchanged).

Acceptance criteria were fixed **before** this evaluation:

| Criterion | Threshold | Candidate v2 | Deployed v1 |
|---|---|---|---|
| Legitimate FPR | ≤ 0.10 | **0.013** ✅ | 0.672 ❌ |
| Suspicious recall | ≥ 0.50 | **0.367** ❌ | 0.717 ✅ |
| Macro-F1 | ≥ 0.60 | **0.726** ✅ | 0.327 ❌ |
| Majority baseline macro-F1 (reference) | — | 0.477 | 0.476 |

Candidate v2 confusion matrix `[[4766, 64], [291, 169]]` (5,290 classified, 3
inconclusive); suspicious precision 0.725, F1 0.488; accuracy 0.933.
By heuristic topic: financial smishing recall 0.477, other smishing recall 0.315.

**Verdict: candidate v2 does NOT pass** (suspicious recall 0.367 < 0.50). It fixes
the false-positive problem but misses most real smishing, which in this set is
largely UK premium-rate/prize spam unlike the Indian-style training scams. Per
protocol, no further tuning was done against the frozen set; any further change
requires new training/validation data and a new frozen test set (re-using this one
after more iterations would turn it into a tuning set).

### Protocol v3 (written and hashed; no v3 data built yet)
- `docs/protocol_v3.json` — SHA-256 `e97171dadedf57083265fc6bb38306942da5df41df32d079439bc11331d79e7e`
  (recorded in `docs/protocol_v3.sha256`, verified by the test suite).
- Shared anonymiser for every source and class: `satyalens_analysis/anonymise.py` (anon-v1).
- Consented genuine-message donation: `scripts/donate_messages.py`,
  `docs/donation/CONSENT_FORM.md`, `docs/donation/DATA_HANDLING.md`.
- Sealed-test guard: once `reports/frozen_test_v3_manifest.json` exists, the package
  loaders refuse the sealed file (or any identical copy); only
  `evaluate --final-sealed-evaluation` may read it, once.
- Background and source research: `docs/DATA_PLAN_V3.md`.
  `real_world_test_v1.csv` is retired from model selection.
- **Status 2026-10-24:** sources verified; full build run in `--dry-run` mode only
  (`reports/build_v3/build_report_dry_run.json`, counts only). **Not sealed:** 0 donated
  genuine messages exist (target ≥ 750 from ≥ 10 donors), so sealing now would make the
  genuine-alert criterion L1 permanently NOT EVALUABLE under protocol v3.
  Ready-to-run scripts: `scripts/build_pool_and_test_v3.py --seal`,
  `scripts/select_text_model_v3.py --train-selected`, `scripts/acceptance_v3.py`.
- **Protocol v4 approved and hashed (2026-10-24):** `docs/protocol_v4.json`, SHA-256
  `f32adc76…0a49`. Adds IMC25 "hey mum/dad", Mendeley (train-only, Latin script, never
  genuine-alert evidence), anon-v2, and grouping policy G4 (0.8 atomic units, giant units
  to train, 0.6 purge). Counts-only dry run: `scripts/build_pool_and_test_v4.py --dry-run`
  → `reports/build_v4/`. Not sealed; donations still 0. v3 files unchanged.
- **Team-supplied datasets in `dataset/` (inspection only, 2026-10-24):** IMC25 copy
  (identical to the pinned file), Mendeley "Financial scams detection dataset" (CC BY 4.0,
  not a protocol-v3 source), Phish360 (AES-encrypted, licence unverified). See
  `docs/DATASET_INSPECTION.md` and `docs/IMAGE_PIPELINE_DESIGN.md`; counts-only reports in
  `reports/dataset_inspection/`. `dataset/` is gitignored.
- **Collecting donations (decision 2026-10-24: collect before sealing).** Donor guide:
  `docs/donation/DONOR_GUIDE.md`. Progress (counts only, by donor code and category):
  `python scripts/donation_progress.py [--check-test-coverage]`. Only records that pass
  `validate_donations()` count, and the builder uses the same validator. Sealing and the
  one-shot evaluation will be run only after the collection target (≥ 750 messages,
  ≥ 10 donors) and the test-coverage requirement are met **and** confirmed by the team.
Hashes of all models/reports/datasets that must not change:
`reports/preserved_artifacts_manifest.json` (checked by the test suite).

### Operational rule until a model passes
A `text_classifier` result — from v1 or any candidate — must **not**
independently trigger a definitive fraud verdict. It may only be shown as a
supporting, correlated signal alongside the context rules and human review.

### Attribution and what to commit
- CC BY 4.0 attribution is recorded for Mishra & Soni 2022
  (`data/real_world_test_v1.provenance.json`) and Ridham115
  (`data/training_pool_v2.provenance.json`); MultiWOZ's MIT copyright notice is
  recorded in the pool provenance.
- **Recommended not to commit** (regenerate deterministically from the scripts;
  `csv_sha256` in each provenance file verifies the result): `data/raw/`
  (already gitignored), `data/real_world_test_v1.csv`, `data/real_world_test_v1.excluded.csv`
  (contains unmasked source text, including phone numbers),
  `data/training_pool_v2.csv` and `data/training_pool_v2.excluded.csv`
  (third-party text; committing MultiWOZ text would also require shipping its MIT notice).
- Safe to commit: scripts, provenance/validation JSON, `data/challenge_suite_v1.csv`,
  `data/synthetic_financial_messages_v1.*`, and `reports/` (metrics only, no message text).
- Model artifacts are gitignored. No secrets are used anywhere.

---

## Payment OCR — Verified Capabilities

**Tesseract version in use:** 5.5.3.20260724 (via Scoop)

**Auto-detection order (no config required):**
1. `~/scoop/apps/tesseract/current/tesseract.exe`
2. `C:/Program Files/Tesseract-OCR/tesseract.exe`
3. `C:/Program Files (x86)/Tesseract-OCR/tesseract.exe`
4. System PATH

**Extracted fields:**
`amount`, `transaction_id`, `date`, `time`, `payee_name`, `payer_name`, `upi_id`, `bank_name`, `status_text`, `payment_method`

**Verified on synthetic screenshot (OCR verification script output):**
```
Status: analysed
Fields extracted: 5
  amount          → "5000.00"   match=True  (expected "5000.00")
  date            → "23/10/2024" match=True (expected "23/10/2024")
  status_text     → "payment successful"
  time            → "14:30:00"
  transaction_id  → "123456789"  (OCR strips "UTR" prefix — known limitation)
Mismatches correctly reported for wrong expected values: YES
Settlement disclaimer in limitations: YES
JSON-serialisable: YES
```

**Always-present limitations in every response:**
- OCR may produce errors on stylised fonts, low-resolution or compressed screenshots.
- This component cannot verify that a payment transaction actually cleared in real time.
- **A matching screenshot does NOT prove payment settlement; screenshots can be edited.**
- Field extraction uses regex patterns; unusual layouts may produce missed or incorrect fields.
- Confidence ratings reflect OCR quality heuristics, not ground-truth accuracy.

---

## Audio Detection — Verified Status

**Current status: `unavailable` for all audio inputs**

### Verified facts

| Item | Fact |
|------|------|
| Previously stated model ID | `Jenrola/AASIST-Speech-Deepfake-Detector` — **unverifiable** (401 from HF, no public search evidence) |
| Correct verified model ID | `SpeechAntiSpoofingBenchmarks/AASIST` |
| Licence | MIT — Copyright NAVER Corporation |
| Source | https://github.com/clovaai/aasist |
| Training data | ASVspoof 2019 LA track |
| HF API compatibility | **NOT** compatible with `transformers.AutoModelForAudioClassification` |
| Custom loader required | YES — AASIST uses a heterogeneous graph attention network, not a standard transformer |
| ML deps installed | NO (torch, torchaudio, huggingface_hub all absent) |

### Integration blocker

AASIST cannot be loaded with `AutoModelForAudioClassification.from_pretrained()`. A custom loader using `huggingface_hub.snapshot_download()` and a `.pth` checkpoint is required. This is documented in [`audio_detector.py`](satyalens_analysis/audio_detector.py).

### Future integration path

```python
# In audio_detector.py — NOT yet implemented:
from huggingface_hub import snapshot_download
repo_path = snapshot_download("SpeechAntiSpoofingBenchmarks/AASIST")
# Import AASIST class from repo, load .pth checkpoint, run score_batch()
```

### What this means for Person 2

- `result["audio"]["status"]` is always `"unavailable"` now.
- The `limitations` list explicitly states this **never** implies the audio is genuine.
- When the custom loader is built, Person 2's code changes **zero lines** — only the library updates.
- Do not infer safety from an `unavailable` audio status.

---

## Test Results (2026-10-25)

**Environment:** Python 3.11.9, pytest 9.1.1, pytest-cov 7.1.0, scikit-learn 1.9.1, joblib 1.6.0, scipy 1.17.1, numpy 2.4.6

**Command:**
```
python -m pytest tests/ -q -rs --cov=satyalens_analysis --cov-report=term-missing -W error::DeprecationWarning -p no:cacheprovider
```

| Result | Count |
|--------|-------|
| Collected | 893 |
| **Passed** | **889** |
| Skipped | 4 |
| **Failed** | **0** |

The command exited 0 with no warnings (wall time varied between about 90 s and 545 s across runs
on the same machine). An earlier coverage run on 2026-10-25 reported 1 warning that did not
reproduce in any later run; its source was not identified. The slowest test is a bootstrap-CI
determinism test (about 36 s).

**Coverage:**

| Module | Stmts | Coverage | Notes |
|--------|-------|----------|-------|
| `__init__.py` | 4 | **100%** | |
| `schema.py` | 121 | **99%** | |
| `unified.py` | 55 | **100%** | Component-error isolation paths tested by injected failures |
| `text_classifier.py` | 728 | 98% | |
| `anonymise.py` | 55 | 96% | |
| `context_analyser.py` | 70 | 93% | Error-handling branches |
| `payment_ocr.py` | 167 | 87% | Unavailable-deps and error paths |
| `audio_detector.py` | 53 | 79% | Torch-dep paths (skipped without torch) |
| **Total** | **1253** | **96%** | |

`tests/test_evaluation.py` (34 tests) covers the evaluate command, dataset
preparation and the prepared real-world file (skipped if not prepared).
`tests/test_training_v2.py` (17 tests) covers configurable pipelines, training-pool
preparation (generator filtering, grouping, dedup, eval-overlap removal), the
challenge suite, and the selection rule (including that selection never reads the
frozen test set). `tests/test_rule_engine_behaviour.py` tests the rule engine on its
own (including the original failure cases of the 2026-10-24 fixes) and verifies the
preserved-artifact hashes. `tests/test_protocol_v3.py` checks the protocol hash, the
shared anonymiser, the donation tool and the sealed-test guard.

ML tests (`tests/test_text_classifier.py`, 72 tests) train a real tiny model to
a tmp directory and load it from disk — inference is not mocked. They point
`SATYALENS_TEXT_MODEL_DIR` at tmp paths, so they pass whether or not the
default artifact has been trained. They are skipped if scikit-learn is absent.

### Skip breakdown (all legitimate)

| Test group | Count | Reason |
|---|---|---|
| `TestAnalyseAudioWithDeps` | 3 | `torch`, `torchaudio`, `huggingface_hub` not installed |
| `test_fixture_image_analysed` | 1 | `tests/fixtures/sample_payment.png` does not exist (intentional) |
| `TestPaymentWithSyntheticImage` | **0** | Previously skipped; **now pass** after Tesseract install |

---

## Ownership Boundary

| Responsibility | Owner |
|---|---|
| Audio deepfake detection, model integration | **Person 1** |
| Payment OCR and field reconciliation | **Person 1** |
| Financial scam-context rules (10 rules) | **Person 1** |
| ML text classifier (training, artifact, inference) | **Person 1** |
| Common evidence response schema (`schema.py`) | **Person 1** |
| All `tests/` and `verify_ocr.py` | **Person 1** |
| Model/licence disclosures | **Person 1** |
| React + Vite frontend | Person 2 |
| FastAPI application and SQLite case management | Person 2 |
| User consent and file upload workflow | Person 2 |
| **Final** Low/Verify/High-Risk verdict engine, incl. fusing correlated context + ML evidence | Person 2 |
| Evidence ledger, report export | Person 2 |

> **This package returns evidence findings only — never a final risk verdict.**

---

## Reproducing the Verification

```bash
# 1. Install Python deps
python -m pip install -e .
python -m pip install pytest pytest-cov

# 2. Install Tesseract (Windows, no admin)
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser -Force
Invoke-RestMethod -Uri https://get.scoop.sh | Invoke-Expression
scoop install tesseract
$td = "$env:USERPROFILE\scoop\persist\tesseract\tessdata"
Invoke-WebRequest -Uri https://github.com/tesseract-ocr/tessdata_fast/raw/main/eng.traineddata -OutFile "$td\eng.traineddata" -UseBasicParsing

# 3. Install ML extra and train the text classifier
python -m pip install -e ".[ml]"
python -m satyalens_analysis.text_classifier train --data data/synthetic_financial_messages_v1.csv

# 3b. Run full test suite
python -m pytest tests/ -v --cov=satyalens_analysis --cov-report=term-missing

# 4. Run OCR verification
python verify_ocr.py

# 5. Run integration demo
python integration_example.py

# 6. Check audio unavailability is explicit
python -c "
from satyalens_analysis import run_analysis; import json
r = run_analysis(audio_path='any.wav')
print(json.dumps(r['audio'], indent=2))
"
```

---

## Licence

MIT — see individual module headers for third-party component licence notices.

- AASIST model weights: MIT — Copyright NAVER Corporation (https://github.com/clovaai/aasist)
- Tesseract OCR binary: Apache 2.0 (https://github.com/tesseract-ocr/tesseract)
- scikit-learn: BSD-3-Clause (https://github.com/scikit-learn/scikit-learn)
- Synthetic dataset: created for this project, no third-party data
- Scoop package manager: MIT (https://scoop.sh)
