# SatyaLens Analysis — Integration Contract
## Schema version 1.0 | Person 1 → Person 2 interface

This document is the authoritative reference for the JSON contract between
Person 1's `satyalens_analysis` package and Person 2's FastAPI application.
Any breaking change to the schema MUST be agreed and reflected here first.

---

## Entry point

```python
from satyalens_analysis import run_analysis

result: dict = run_analysis(
    case_id="CASE-001",              # str  — echoed back, no analysis applied
    audio_path=None,                 # str | None — path to audio file
    screenshot_path=None,            # str | None — path to screenshot image
    message_text=None,               # str | None — suspicious message text
    expected_payment_fields=None,    # dict[str, str] | None — values to compare
)
# result is always JSON-serialisable. Pass directly to JSONResponse(content=result).
```

`run_analysis()` never raises. All errors are contained within the component
result objects and the top-level `errors` list.

---

## Top-level response structure

```
{
  "schema_version":  "1.0",     // always "1.0" in this package version
  "case_id":         "<str>",   // echoed from caller; may be empty string
  "audio":           <AudioResult>,
  "payment":         <PaymentResult>,
  "context":         <ContextResult>,
  "text_classifier": <TextClassifierResult>,   // ADDED 2026-10-24 (additive)
  "errors":          [<str>, ...]  // non-empty only on unexpected component errors
}
```

All seven top-level keys are **always present**.

> ### ⚠ Change notice for Person 2 (2026-10-24)
> A new top-level key **`text_classifier`** was added. Nothing was renamed or
> removed; the six original keys and their semantics are unchanged.
> Under the "Breaking vs. non-breaking" table below this is an additive,
> non-breaking change, so `schema_version` stays `"1.0"`.
> **However**, if your code validates the response with a strict key set
> (e.g. a Pydantic model with `extra="forbid"`, or `set(result) == {...}`),
> it will reject the new key — add `text_classifier` (or allow extra keys)
> before upgrading this package.

---

## `audio` object

| Key | Type | When present | Notes |
|-----|------|-------------|-------|
| `status` | string | always | See status values below |
| `finding` | string | only when `status == "analysed"` | `genuine_likely`, `spoof_likely`, `inconclusive` |
| `reliability` | string | only when `status == "analysed"` | `limited`, `moderate` |
| `reason` | string | always | Human-readable explanation |
| `raw_score` | float | only when `status == "analysed"` | Sigmoid model output, NOT a probability |
| `input_quality` | list[str] | only when model ran | Duration/SR/clipping warnings |
| `limitations` | list[str] | always when not `not_applicable` | Model caveats |

### Audio status — current state (2026-10-23)

**Audio detection is currently `unavailable` for all inputs.**

The verified model (`SpeechAntiSpoofingBenchmarks/AASIST`, MIT licence, NAVER Corp.)
uses a custom graph-attention architecture that is **not compatible** with
`transformers.AutoModelForAudioClassification`. A custom loader is required
but not yet implemented. See `audio_detector.py` module docstring.

**`status: "unavailable"` NEVER implies the audio is genuine or the request is safe.**
Person 2 must not infer safety from this status.

When integration is complete, the status will move to `"analysed"` or
`"inconclusive"` — Person 2's code requires **no changes** to the call site.

### Critical audio invariants

```
audio.status == "unavailable"  =>  audio.finding is ABSENT
audio.status == "failed"       =>  audio.finding is ABSENT
audio.status == "not_applicable" =>  audio.finding is ABSENT
audio.status == "analysed"     =>  audio.finding IS present
                                   audio.raw_score IS present (float in [0,1])
```

---

## `payment` object

| Key | Type | Always present | Notes |
|-----|------|--------------|-------|
| `status` | string | yes | See status values |
| `raw_text` | string | yes | OCR output, truncated at 4000 chars |
| `fields` | list[PaymentField] | yes | Empty list if nothing extracted |
| `mismatches` | list[str] | yes | Empty list if all expected fields match |
| `limitations` | list[str] | yes | Always non-empty |

### PaymentField object

```json
{
  "field_name":      "amount",
  "extracted_value": "5000.00",      // original OCR output — never normalised
  "expected_value":  "5000",         // caller-supplied value — never normalised
  "match":           true,           // null if expected_value was not supplied
  "confidence":      "medium"        // "low" | "medium" | "high" (OCR heuristic)
}
```

`extracted_value` is **always the raw OCR string**. Normalisation is applied to
copies for comparison only. Person 2 should display `extracted_value` to the user.

### Comparison policy (Person 2 must understand this)

| Field | Policy | Example of equal pairs |
|-------|--------|----------------------|
| `amount` | Python `Decimal` after stripping currency symbols and commas | `"5000"` == `"5,000.00"` == `"Rs. 5000"` |
| `transaction_id` | Strip keyword prefix (UTR, TXN, REF, NEFT, IMPS, RTGS) and non-alphanumeric chars, then case-insensitive | `"UTR123456"` == `"123456"` == `"utr-123456"` |
| all others | Case-insensitive strip | `"John Doe"` == `"john doe"` |

When a mismatch occurs, the mismatch string includes a `[...]` note explaining
which normalised values were compared, e.g.:
```
amount: expected='5000', extracted='5,000.00'
    [Decimal comparison: normalised extracted='5000.00', normalised expected='5000']
```

### Payment limitations (always in response)

1. OCR may produce errors on stylised fonts, low-resolution or compressed screenshots.
2. **This component cannot verify that a payment actually cleared in real time.**
3. **A matching screenshot does NOT prove payment settlement; screenshots can be edited.**
4. Field extraction uses regex patterns; unusual layouts may miss fields.
5. Amount comparison uses Decimal; transaction IDs strip keyword prefixes.
6. Confidence ratings are OCR heuristics, not ground-truth accuracy.

---

## `context` object

| Key | Type | Always present | Notes |
|-----|------|--------------|-------|
| `status` | string | yes | |
| `flags` | list[ContextFlag] | yes | Empty list if no rules fired |
| `findings` | list[str] | yes | Narrative summaries |
| `limitations` | list[str] | yes | Always non-empty on `analysed` |

### ContextFlag object

```json
{
  "rule_id":     "URGENCY_PRESSURE",
  "label":       "Extreme Urgency / Time Pressure",
  "severity":    "high",
  "explanation": "Phrase \"urgent\" creates artificial urgency..."
}
```

### The 10 rules (deterministic, keyword/pattern-based)

| Rule ID | Severity |
|---------|----------|
| `URGENCY_PRESSURE` | high |
| `SECRECY_DEMAND` | high |
| `CREDENTIAL_REQUEST` | high |
| `GIFT_CARD_PAYMENT` | high |
| `REFUND_OVERPAYMENT_SCAM` | high |
| `REMOTE_ACCESS_REQUEST` | high |
| `THREAT_OR_COERCION` | high |
| `CRYPTO_PAYMENT` | medium |
| `NEW_PAYEE_PRESSURE` | medium |
| `IMPERSONATION_CUE` | medium |

**Context analysis does not issue a final verdict.** A high flag count raises
concern but is not proof of fraud. Zero flags does not confirm legitimacy.

---

## `text_classifier` object

TF-IDF + Logistic Regression model (scikit-learn), trained offline and loaded
from a saved artifact. Runs on the same `message_text` as `context`, in its
own error-isolation boundary (a failure in one never disables the other).

| Key | Type | When present | Notes |
|-----|------|-------------|-------|
| `status` | string | always | See status table below |
| `predicted_class` | string | only when `status == "analysed"` | `"suspicious"` or `"legitimate"` |
| `raw_score` | float in [0,1] | only when `status == "analysed"` | `predict_proba` for `"suspicious"` — **uncalibrated, NOT a fraud probability** |
| `model_version` | string | when a model was loaded (`analysed`, `inconclusive`) | e.g. `"tfidf-logreg-v1-66bab05fd245"` |
| `training_data` | string | when a model was loaded | e.g. `"satyalens-synthetic-financial-messages v1 (synthetic)"` |
| `reason` | string | always | Human-readable explanation |
| `top_terms` | list[{`term`: str, `weight`: float}] | always (empty unless `analysed`) | Up to 5 terms that most influenced the score; positive weight → toward `suspicious` |
| `limitations` | list[str] | always (may be empty for `not_applicable`) | Model caveats |

### Status semantics

| Status | Meaning |
|--------|---------|
| `analysed` | Inference ran on the saved model |
| `inconclusive` | No word of the message is in the model vocabulary; no class is returned |
| `not_applicable` | `message_text` absent, empty or whitespace-only |
| `unavailable` | scikit-learn not installed, or model artifact/metadata not found (model not trained on this machine) |
| `failed` | Artifact present but invalid (SHA-256 mismatch, scikit-learn version mismatch, unreadable, wrong type), non-string input, or inference error |

### Critical text_classifier invariants

```
text_classifier.status != "analysed"  =>  predicted_class ABSENT, raw_score ABSENT
text_classifier.status == "analysed"  =>  predicted_class IS present ("suspicious"|"legitimate")
                                          raw_score IS present (float in [0,1])
predicted_class                       =   higher-scoring class (argmax; no tuned threshold)
```

The class is decided on the **unrounded** score: `"suspicious"` only if it is
strictly above 0.5; an exact tie (0.5) is `"legitimate"`. The serialised
`raw_score` is rounded to 6 decimals, so a reported value of exactly `0.5` can
accompany either class. Always read `predicted_class`; never re-derive the
class by thresholding the reported `raw_score`.

### Rules for Person 2's evidence-fusion policy

1. **Correlated evidence.** `text_classifier` and `context` analyse the *same
   text* and react to overlapping wording (OTP, urgency, "blocked", etc.).
   Do **not** count a `suspicious` prediction plus context flags as two
   independent confirmations. Compare `top_terms` with the context flags'
   `explanation` to see the overlap.
2. `unavailable`, `failed`, `inconclusive` and `not_applicable` **never** mean
   the message is legitimate.
3. `predicted_class == "legitimate"` does **not** mean the message is safe.
4. `raw_score` is not calibrated; do not display it as a percentage
   likelihood of fraud or threshold it as if it were.
5. The current model is trained on a **synthetic development dataset**
   (`training_data` says so). On an independent real SMS evaluation set
   (offline evaluation 2026-10-24 of the deployed model
   `tfidf-logreg-v1-66bab05fd245`) it flagged 67% of
   legitimate messages as `suspicious` and scored below a majority-class
   baseline (macro-F1 0.33 vs 0.48). An improved candidate (v2,
   `tfidf-logreg-v1-a0753aa43caf`, not deployed) cut that false-positive
   rate to 1.3% but missed 63% of real
   smishing, so it also failed the pre-registered acceptance criteria.
   **Until a model passes real-world evaluation, a `suspicious` prediction
   must NOT independently trigger a definitive fraud verdict**; treat it
   only as a supporting, correlated signal requiring other evidence or
   human review.

The response schema is unchanged by the evaluation work; evaluation is an
offline CLI (`python -m satyalens_analysis.text_classifier evaluate`) and is
not called by `run_analysis()`.

### Deployment note

The trained artifact is **not committed** (gitignored). On a machine where it
has not been trained, `text_classifier.status` is `"unavailable"`. To enable:

```bash
pip install -e ".[ml]"
python -m satyalens_analysis.text_classifier train --data data/synthetic_financial_messages_v1.csv
```

The artifact location can be overridden with the `SATYALENS_TEXT_MODEL_DIR`
environment variable. joblib artifacts are pickles: only load artifacts you
trained or received from a trusted source.

---

## Status values (all components)

| Value | Meaning |
|-------|---------|
| `"analysed"` | Component ran and produced a result |
| `"inconclusive"` | Component ran but could not make a determination |
| `"failed"` | Runtime error during analysis |
| `"not_applicable"` | No input was provided for this component |
| `"unavailable"` | Required dependency or model is not installed/integrated |

> **Critical invariant**: `"failed"` or `"unavailable"` for any component
> NEVER implies the media is genuine or the case is safe. Person 2's risk
> engine must handle all status values explicitly.

---

## `errors` list

Non-empty only when a component raised an unexpected exception that was caught
by the unified runner. The affected component will also have `status: "failed"`.
Errors do not abort other components.

---

## `expected_payment_fields` keys

Supported keys for the `expected_payment_fields` parameter:

| Key | Comparison policy |
|-----|-----------------|
| `amount` | Decimal |
| `transaction_id` | Prefix-stripped, case-insensitive |
| `date` | Case-insensitive string |
| `time` | Case-insensitive string |
| `payee_name` | Case-insensitive string |
| `payer_name` | Case-insensitive string |
| `upi_id` | Case-insensitive string |
| `status_text` | Case-insensitive string |
| `payment_method` | Case-insensitive string |
| `bank_name` | Case-insensitive string |

Unrecognised keys are silently ignored.

---

## What Person 2 must NOT do

- Use `audio.status == "unavailable"` to infer the audio is genuine.
- Use `payment.match == true` on any field to assert payment settled.
- Treat `context.flags == []` as a guarantee the message is legitimate.
- Read `errors == []` as a signal the case is safe.
- Use `extracted_value` directly as a parsed number or date — it is raw OCR text.
- Expect `audio.finding` or `audio.raw_score` to be present when status is not `"analysed"`.
- Treat `text_classifier.predicted_class == "legitimate"` as proof the message is safe.
- Treat `text_classifier` and `context` as independent confirmations of each other.
- Present `text_classifier.raw_score` as a calibrated fraud probability.
- Expect `text_classifier.predicted_class` / `raw_score` when status is not `"analysed"`.

---

## File upload security (Person 2's responsibility)

The analysis package accepts filesystem paths. Person 2's FastAPI layer is
responsible for safe file handling. See `integration_example.py` for the
reference pattern which demonstrates:

1. Server-side random filenames via `uuid.uuid4().hex + validated_ext` — client
   filename is **never** used as a path component.
2. Extension allowlist enforced before writing to disk.
3. Per-type upload size limits (10 MB audio, 5 MB screenshot).
4. Temporary files deleted in a `finally` block covering both success and failure.

---

## Breaking vs. non-breaking changes

| Change | Breaking? |
|--------|----------|
| Adding a new optional key to any object | No |
| Removing or renaming an existing key | YES |
| Changing `schema_version` | YES |
| Adding a new `status` enum value | Potentially — Person 2 should handle unknown values gracefully |
| Changing comparison policy for a field type | YES — document here first |
| Audio becoming `analysed` when model is integrated | No — schema is already defined |
| Adding top-level `text_classifier` (2026-10-24) | No per this table — but breaks strict key-set validators; see change notice above |
