# SatyaLens Analysis Package
## Person 1: AI Detection & Evidence Analysis
### RAKSHAM AI Cybersecurity Hackathon 2026

---

## Verified Status (2026-10-23)

| Component | Status | Notes |
|-----------|--------|-------|
| Context analyser | **Operational** | Pure Python, no external deps |
| Payment OCR (Python libs) | **Operational** | pytesseract 0.3.13, Pillow 13, cv2 5.0 |
| Payment OCR (Tesseract binary) | **Operational** | Tesseract 5.5.3 via Scoop, auto-detected |
| Audio detection (ML deps) | Not installed | torch/torchaudio/huggingface_hub absent |
| Audio detection (model loader) | Integration blocker | AASIST requires custom loader; not yet built |
| ML text classifier | **Advisory only; not validated** | Not reliable on real SMS (see below); never a sole basis for a fraud verdict |
| Genuine financial-alert coverage | **NOT EVALUABLE** | 0 donated genuine messages, 0 donors |

**Test results (verified runs, 2026-10-25):**
```
Working tree (local data/models present): 893 collected | 889 passed | 4 skipped  | 0 failed | 0 warnings | coverage 96%
Clean checkout of the committed tree:     811 collected | 792 passed | 19 skipped | 0 failed | 0 warnings | coverage 92%
Command: python -m pytest tests/ -v --cov=satyalens_analysis --cov-report=term-missing -W error::DeprecationWarning
Exit code: 0 (both)
```

**Validation status (read before use):**
- **Genuine Indian financial-alert coverage is NOT EVALUABLE: there are zero donated genuine messages
  (0 donors).** Protocol v5 needs at least 150 donated test messages from at least 5 donors, with at least
  3 categories of 25 or more. False positives on genuine bank/UPI alerts are therefore not validated.
- Protocol v5 (`docs/protocol_v5.json`) is **unsealed**: no frozen test exists and no model has been
  evaluated under it. Without donations its best possible verdict is "PARTIALLY EVALUATED - not
  validated for genuine financial alerts".
- The ML text classifier is **advisory only**. On an independent real SMS set the deployed v1 model
  flagged 67% of legitimate messages (macro-F1 0.33 vs 0.48 for a majority-class baseline); candidate v2
  (not deployed) had 1.3% false positives but missed 63% of smishing (`INTEGRATION_CONTRACT.md`).
- Results on synthetic data (`data/dev/`, the dev-synth-v1 benchmark) are development checks only and
  do **not** demonstrate real-world fraud-detection performance.
- Payment OCR extracts text; a matching screenshot is **not** proof of payment settlement. Audio
  detection is unavailable (no model loader). Webpage-screenshot (Phish360) detection is not built.

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
    message_text="Act now or you'll be blocked!",  # optional; pure-Python rules
    expected_payment_fields={                      # optional; compared against OCR
        "amount": "5000",
        "payee_name": "John Doe",
        "transaction_id": "UTR123456789",
    },
)
# result is a JSON-serialisable dict
return JSONResponse(content=result)
```

See [`integration_example.py`](integration_example.py) for the complete FastAPI endpoint pattern including file upload and cleanup.

---

## Response Schema (v1.0)

> `run_analysis()` returns seven top-level keys: the six below plus the additive `text_classifier`
> object. `INTEGRATION_CONTRACT.md` is the authoritative field reference.

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
  "errors": ["Non-fatal component errors"]
}
```

### Schema invariants Person 2 can rely on

| Invariant | Guarantee |
|---|---|
| All top-level keys always present | `schema_version`, `case_id`, `audio`, `payment`, `context`, `errors` |
| `audio.finding` / `raw_score` | Present **only** when `status == "analysed"` |
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

**Command:**
```
python -m pytest tests/ -v --cov=satyalens_analysis --cov-report=term-missing -W error::DeprecationWarning
```

**Exit code:** 0

| Result | Working tree | Clean checkout |
|--------|-------|-------|
| Collected | 893 | 811 |
| **Passed** | **889** | **792** |
| Skipped | 4 | 19 |
| **Failed** | **0** | **0** |
| Warnings | 0 | 0 |

The clean checkout is a `git archive` export of the committed tree. It has fewer tests because
some test files are not yet committed, and more skips because gitignored local data and model
artifacts are absent (see the skip breakdown).

**Coverage:**

| Module | Stmts | Working tree | Clean checkout | Notes |
|--------|-------|-------|-------|-------|
| `__init__.py` | 4 | **100%** | **100%** | |
| `schema.py` | 121 | **99%** | **99%** | |
| `unified.py` | 55 | **100%** | **100%** | |
| `text_classifier.py` | 728 | 98% | 93% | Clean checkout lacks local-only evaluation tests and data |
| `anonymise.py` | 55 | 96% | 96% | |
| `context_analyser.py` | 70 | 93% | 93% | Error-handling branches |
| `payment_ocr.py` | 167 | 87% | 87% | Unavailable-deps and error paths |
| `audio_detector.py` | 53 | 79% | 79% | Torch-dep paths (skipped without torch) |
| **Total** | **1253** | **96%** | **92%** | |

### Skip breakdown (all legitimate)

| Test group | Count | Reason |
|---|---|---|
| `TestAnalyseAudioWithDeps` | 3 | `torch`, `torchaudio`, `huggingface_hub` not installed |
| `test_fixture_image_analysed` | 1 | `tests/fixtures/sample_payment.png` does not exist (intentional) |
| `TestPaymentWithSyntheticImage` | **0** | Previously skipped; **now pass** after Tesseract install |

Clean checkout only (15 further skips): 12 preserved-artifact hash checks, 1 Mendeley loader test,
1 R115 selection test and 1 benchmark-reproduction test skip because the gitignored local datasets,
models and benchmark report are not part of the committed tree.

---

## Ownership Boundary

| Responsibility | Owner |
|---|---|
| Audio deepfake detection, model integration | **Person 1** |
| Payment OCR and field reconciliation | **Person 1** |
| Financial scam-context rules (10 rules) | **Person 1** |
| Common evidence response schema (`schema.py`) | **Person 1** |
| All `tests/` and `verify_ocr.py` | **Person 1** |
| Model/licence disclosures | **Person 1** |
| React + Vite frontend | Person 2 |
| FastAPI application and SQLite case management | Person 2 |
| User consent and file upload workflow | Person 2 |
| **Final** Low/Verify/High-Risk verdict engine | Person 2 |
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

# 3. Run full test suite
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
- Scoop package manager: MIT (https://scoop.sh)
