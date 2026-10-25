# SatyaLens — Segment 6: Transparent Decision Engine

## Overview

The decision engine produces one of four **advisory guidance categories** by
applying deterministic, named rules to the available evidence and analysis
results. It never invents numeric risk scores, fabricates AI findings, or
claims certainty about fraud or authenticity.

---

## Guidance Categories

| Category | When produced | Meaning |
|----------|--------------|---------|
| `insufficient_evidence` | No evidence, all analysis unavailable/failed/zero-findings | Cannot assess — do not assume safe |
| `low_concern` | Completed analysis with findings; none match concern types | No specific concern patterns detected |
| `verify_first` | At least one VERIFY_FIRST-type finding present | Independent verification recommended |
| `high_risk` | At least one HIGH_RISK-type finding present | Strongly recommended: verify before acting |

> **These categories are advisory only.** They do not prove fraud, authenticity,
> or any person's intent.

---

## Decision Rules (v1.0.0)

| Rule | Description |
|------|-------------|
| **R0** | No evidence → `INSUFFICIENT_EVIDENCE` |
| **R1** | All analysis unavailable/failed/not-requested, OR all completed with zero findings → `INSUFFICIENT_EVIDENCE` |
| **R2a** | Any finding with `finding_type` in `HIGH_RISK_FINDING_TYPES` → `HIGH_RISK` |
| **R2b** | Any finding with `finding_type` in `CONCERN_FINDING_TYPES` → `VERIFY_FIRST` |
| **R2c** | Completed analysis, findings present, none match concern types → `LOW_CONCERN` |
| **R3** | Completed analysis with zero findings: inconclusive — does not justify `LOW_CONCERN` alone |
| **R4** | Same `finding_type` appears as both direct measurement and interpretation across items → surfaced as conflict |
| **R5** | Mix of assessed and unassessed items → minimum `VERIFY_FIRST` |

### Concern finding types

```
CONCERN_FINDING_TYPES = {
    "deepfake_score", "audio_manipulation",   → HIGH_RISK
    "scam_pattern",
    "ocr_mismatch", "payment_mismatch",       → VERIFY_FIRST
    "script_match",
}
```

When Person 1's module delivers additional `finding_type` values, extend these
sets and bump `RULE_VERSION`.

---

## Financial Context

Where case description references amounts, payees, or purposes, and the
analysis module extracts corresponding fields from evidence (via OCR / payment
reconciliation), the engine compares them:

| Comparison outcome | Decision effect |
|-------------------|-----------------|
| Exact match | No additional concern (match does not prove legitimacy) |
| Mismatch (`payment_mismatch`, `ocr_mismatch`) | `VERIFY_FIRST` at minimum |
| Not provided | Listed in `factors_unknown`; never assumed to match or mismatch |
| Unable to compare | Listed in `factors_unknown` |

**The engine never adds sensitive financial fields (OTP, PIN, password, account
credentials) to any form or response.**

---

## API Endpoints

### `POST /api/cases/{case_id}/decision`

**Requires:** `consent_status = granted`

Recomputes the decision from current DB state. Updates `Case.decision` column
(the category string, or `null` for `INSUFFICIENT_EVIDENCE` which has no
matching `Decision` enum value). Returns `DecisionSummaryRead`.

**Response codes:** 200, 403 (no consent), 404 (case not found)

### `GET /api/cases/{case_id}/decision`

**Requires:** `consent_status = granted`

Recomputes from live state. Returns 404 if `Case.decision` is null (no decision
yet computed). Returns `DecisionSummaryRead`.

**Response codes:** 200, 403, 404

### `DecisionSummaryRead` schema

| Field | Type | Notes |
|-------|------|-------|
| `category` | string | One of the four guidance categories |
| `short_explanation` | string | Plain-language summary |
| `factors_of_concern` | string[] | Findings that raised the concern level |
| `factors_unknown` | string[] | Items that could not be assessed |
| `conflicts` | string[] | Contradictory findings across evidence |
| `supporting_refs` | EvidenceReferenceRead[] | Evidence items referenced |
| `limitations` | string[] | What the system cannot determine |
| `next_steps` | string[] | Independent-verification suggestions |
| `generated_at` | datetime | UTC generation time |
| `rule_version` | string | Rule set version (bump on rule changes) |

> **No numeric risk score is present in this schema.**

---

## Persistence

The `category` string is persisted in `Case.decision` (existing column, no
new table). The full `DecisionSummaryRead` is recomputed on each request so
it always reflects the current state of analysis results. This means:

- Re-running analysis on evidence and re-requesting a decision produces an
  updated result.
- The decision is deterministic: identical evidence/analysis state → identical
  output.
- `rule_version` in every response allows callers to detect stale decisions.

---

## Handling Missing and Conflicting Evidence

| Situation | Behaviour |
|-----------|-----------|
| Evidence with no analysis requested | `not_requested` → `factors_unknown` |
| Analysis failed | `failed` → `factors_unknown`, never `LOW_CONCERN` |
| Analysis unavailable (module absent) | `unavailable` → `factors_unknown`, never `LOW_CONCERN` |
| Completed, zero findings | `factors_unknown` ("No specific findings returned") |
| Malformed finding (missing required fields) | Discarded with warning log; remaining findings still used |
| Non-dict finding entry | Discarded safely |
| Missing `reliability_note` | Handled gracefully; finding still processed |
| Missing `is_interpretation` | Defaults to `False` |
| Conflicting interpretation flag | Surfaced in `conflicts` list |

---

## Frontend Integration

`DecisionPanel` is mounted in `CaseDetailPage` below the evidence section.

- **Consent gate:** panel shows a lock message if consent not granted.
- **Get Decision / Re-evaluate button:** triggers `POST /api/cases/{id}/decision`.
- **Category banner:** colour-coded (grey/green/yellow/red) with plain-language note.
- **Factors of concern:** red-accented list of concern-level findings.
- **Could Not Assess:** items with unavailable/failed/incomplete analysis.
- **Conflicting Findings:** highlighted in amber.
- **Limitations:** always visible — system cannot guarantee safety.
- **Verification Checklist:** numbered independent-verification steps, always shown.
- **Persistent disclaimer:** "advisory only, not proof of fraud or authenticity."

---

## Limitations

- Analysis accuracy depends entirely on Person 1's module quality.
- Until Person 1's module is integrated, all decisions will be `INSUFFICIENT_EVIDENCE`.
- The engine does not consider timing, caller identity, or external data sources.
- A `LOW_CONCERN` result does not guarantee the interaction is safe.
- A `HIGH_RISK` result does not prove malicious intent.
- This system never contacts banks, payment processors, or third parties.

---

## Test Results

```
tests/test_decision.py  — 42 tests (28 unit + 14 API)
tests/test_analysis.py  — 21 tests
tests/test_evidence.py  — 30 tests
tests/test_cases.py     — 33 tests
tests/test_health.py    —  7 tests
─────────────────────────────────────────────
Total: 133 passed, 0 failed

npm run build: ✅ 0 errors
```

---

## Unresolved Issues

| Issue | Severity | Notes |
|-------|----------|-------|
| Person 1's module absent | Expected | All decisions = INSUFFICIENT_EVIDENCE until integrated |
| No async task queue | Low | Analysis runs synchronously; queue deferred beyond Segment 6 |
| No rate limiting | Low | Prototype only |
| No encryption at rest | Low | Add before production |
| No authentication | Low | All segments — add before production |
