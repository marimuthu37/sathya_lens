# SatyaLens — Segment 5: AI Analysis Integration

## Integration Status

**Person 1's analysis module (audio deepfake detection, OCR, payment reconciliation,
scam-context rules) is not present in this repository.**

The application is fully integrated with a stable, typed contract and an
`UnavailableAdapter` that returns a clearly-labelled placeholder. No analysis
results are fabricated. The UI displays "Analysis Unavailable" honestly.

---

## Analysis Contract

### Request / Response Flow

```
POST /api/cases/{case_id}/evidence/{evidence_id}/analyse
→ 202 AnalysisRequestResponse

GET  /api/cases/{case_id}/evidence/{evidence_id}/analysis
→ 200 AnalysisResultRead
```

### AnalysisResultRead schema

| Field | Type | Notes |
|-------|------|-------|
| `id` | string (UUID) | Result record ID |
| `evidence_id` | string (UUID) | Parent evidence |
| `status` | string | `unavailable` \| `pending` \| `running` \| `completed` \| `failed` |
| `requested_at` | datetime | UTC |
| `completed_at` | datetime \| null | UTC; null until finished |
| `module_name` | string \| null | `"unavailable"` when module absent |
| `module_version` | string \| null | Semver from Person 1's module |
| `findings` | FindingRead[] | Empty unless `status=completed` and module present |
| `limitations` | string \| null | Module-supplied caveats |
| `error_message` | string \| null | Set when `status=failed` |
| `is_stub` | bool | `true` until a real module is installed |
| `module_available` | bool | Reflects `ANALYSIS_MODULE_ENABLED` env var |

### FindingRead schema

| Field | Type | Notes |
|-------|------|-------|
| `finding_type` | string | e.g. `"audio_quality"`, `"ocr_field"` |
| `label` | string | Short human-readable description |
| `detail` | string | Full explanation |
| `evidence_source` | string | Which part of the file, e.g. `"audio_track"` |
| `is_interpretation` | bool | **True** = inferred; must be labelled in UI |
| `reliability_note` | string \| null | Module-supplied caveat |

---

## Enabling Person 1's Module

1. Create `backend/app/services/analysis_person1.py`:
   ```python
   from app.services.analysis import AnalysisAdapter, ModuleResult

   class Person1Adapter(AnalysisAdapter):
       is_available = True

       def analyse(self, file_path, media_type, evidence_id) -> ModuleResult:
           # Implement using Person 1's actual module
           ...

   def get_adapter() -> AnalysisAdapter:
       return Person1Adapter()
   ```
2. Set in `.env`:
   ```
   ANALYSIS_MODULE_ENABLED=true
   ```
3. Restart the backend. No other code changes required.

---

## Behaviour by Status

| Status | When | UI displayed |
|--------|------|--------------|
| `unavailable` | Module absent | "Analysis module not available" banner; no findings |
| `pending` | Queued (async queue — Segment 6) | Spinner + "Queued" badge |
| `running` | In progress | Spinner + "Running" badge |
| `completed` | Module returned result | Finding cards; disclaimer shown |
| `failed` | Module error or missing file | Error message + Retry button |

---

## Endpoints

### `POST /api/cases/{case_id}/evidence/{evidence_id}/analyse`

**Requires:** `consent_status = granted`

**Idempotent:** Returns existing result if status is not `failed`.

**Unavailable module:** Creates result with `status=unavailable` immediately;
no analysis performed; no findings invented.

**Response codes:**
- `202` — accepted (or existing result returned)
- `403` — consent not granted
- `404` — case or evidence not found
- `422` — evidence file missing from disk (when module available)
- `500` — module error

### `GET /api/cases/{case_id}/evidence/{evidence_id}/analysis`

Returns the current `AnalysisResultRead` or `404` if not yet requested.

---

## Honesty and Safety Guarantees

- **No fabricated findings.** `findings` is always `[]` when the module is absent.
- **`is_stub=true`** is set whenever no real module ran. UI enforces this.
- **Interpretations flagged.** `is_interpretation=true` findings are visually
  distinguished and carry reliability notes.
- **No authenticity claims.** Analysis never proves a file is genuine or fraudulent.
- **SHA-256 note maintained.** Hash records bytes received; not a certificate of authenticity.
- **No external API calls** unless Person 1's module explicitly makes them
  with user knowledge.
- **No credential collection.** Upload UI warns against OTPs, PINs, passwords.
- **Cascade delete.** `AnalysisResult` rows are deleted with their `Evidence` row.

---

## Test Results

```
tests/test_analysis.py  — 21 tests
tests/test_evidence.py  — 30 tests
tests/test_cases.py     — 33 tests
tests/test_health.py    —  7 tests
─────────────────────────────────
Total: 91 passed, 0 failed
```

---

## Unresolved Issues / Known Limitations

| Issue | Severity | Notes |
|-------|----------|-------|
| Person 1's module not present | Expected | `UnavailableAdapter` in place |
| Analysis runs synchronously | Low | Async task queue deferred to Segment 6 |
| No rate limiting on `/analyse` | Low | Acceptable for prototype |
| No encryption at rest | Low | Prototype only — add before production |
| No authentication | Low | All segments — add before production |
