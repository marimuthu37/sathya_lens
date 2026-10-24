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

**Test results (final verified run, 2026-10-23):**
```
96 passed  |  4 skipped  |  0 failed  |  0 warnings
Coverage: 85%
Command: python -m pytest tests/ -v --cov=satyalens_analysis --cov-report=term-missing -W error::DeprecationWarning
Exit code: 0
```

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

## Test Results (Final, 2026-10-23)

**Command:**
```
C:\Users\HP\Python311\python.exe -m pytest tests/ -v --tb=short \
    --cov=satyalens_analysis --cov-report=term-missing \
    -W error::DeprecationWarning
```

**Exit code:** 0

| Result | Count |
|--------|-------|
| **Passed** | **96** |
| Skipped | 4 |
| **Failed** | **0** |
| Warnings | 0 |

**Coverage:**

| Module | Stmts | Coverage | Notes |
|--------|-------|----------|-------|
| `__init__.py` | 4 | **100%** | |
| `schema.py` | 94 | **99%** | Line 93: unreachable default branch |
| `context_analyser.py` | 60 | 92% | Lines 266-268, 292-293: error-handling branches |
| `payment_ocr.py` | 122 | 80% | Unavailable-deps and error paths |
| `audio_detector.py` | 53 | 77% | Torch-dep paths (skipped without torch) |
| `unified.py` | 44 | 66% | Component-error paths (require injecting exceptions) |
| **Total** | **377** | **85%** | |

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
