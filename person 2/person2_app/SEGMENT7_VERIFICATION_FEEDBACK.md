# SatyaLens — Segment 7: Verification Checklist and User Feedback

## Overview

Segment 7 adds a practical independent-verification checklist and an optional
user-feedback form to each case. These are decision-support tools — completing
the checklist does **not** prove a payment or requester is legitimate or safe.

---

## Checklist Design

### Checklist items

9 canonical items, defined server-side in
`backend/app/services/checklist.py`, across 5 categories:

| Category | Items |
|----------|-------|
| `contact` | Contact via known/independent number; verify through official channel; discuss with a trusted person |
| `payment` | Confirm payee and amount independently; verify account/IFSC/UPI handle against trusted source |
| `warning_signs` | Urgency/pressure as red flag; unexpected requests regardless of caller familiarity |
| `credentials` | Never share OTP, UPI PIN, net-banking password, card number, CVV |
| `reporting` | 🇮🇳 India-specific: call 1930; report at https://cybercrime.gov.in |

> **Verify cybercrime.gov.in and 1930 against official government sources before use in production or demo.**

### Key design decisions

- **Server-side item IDs**: `item_id` values are validated against `VALID_ITEM_IDS`; unknown IDs → 422.
- **No proof claim**: the disclaimer always reads "completion does not prove safety."
- **Per-case isolation**: progress is scoped to `case_id`; cross-case bleed is impossible.
- **Cascade deletion**: `ChecklistProgress` rows are deleted when their parent Case is deleted.
- **No personal data**: only `case_id` is stored; no identity, location, or credentials.

---

## Feedback Design

### Fields collected

| Field | Type | Purpose |
|-------|------|---------|
| `verified_independently` | bool? | Did user complete independent verification |
| `still_has_concerns` | bool? | Does user still have concerns |
| `result_useful` | bool? | Was the guidance useful |
| `result_clear` | bool? | Was the guidance clearly explained |
| `note` | string ≤500 chars | Optional short comment |

### Fields NOT collected

- Name, email, phone, IP address, location
- OTPs, PINs, passwords, card numbers
- Any payment credentials or account data

---

## API Endpoints

### `GET /api/cases/{case_id}/checklist`

Returns canonical items, per-case progress, `checklist_version`, and `disclaimer`.
No consent required (checklist is informational).

**Response:** `ChecklistStateRead` — 200, or 404 (case not found)

### `PATCH /api/cases/{case_id}/checklist/{item_id}`

Marks an item checked or unchecked. Upserts (creates or updates) the progress row.
`item_id` must be a valid server-side ID.

**Body:** `{"checked": bool}`
**Response:** `ChecklistProgressRead` — 200, 404 (case), 422 (invalid item_id)

### `GET /api/cases/{case_id}/feedback`

Returns existing feedback. 404 if none submitted.

### `PUT /api/cases/{case_id}/feedback`

Creates or replaces feedback (upsert — one record per case). All fields optional.
Note trimmed; whitespace-only → null; >500 chars → 422.

**Response:** `FeedbackRead` — 200, 404 (case), 422 (validation)

### `DELETE /api/cases/{case_id}/feedback`

Deletes the feedback record. 204 on success, 404 if none exists.

---

## Data Model

### `checklist_progress` table

| Column | Type | Notes |
|--------|------|-------|
| `id` | UUID string | PK |
| `case_id` | UUID string | FK → `cases.id` CASCADE DELETE |
| `item_id` | string(64) | Must be in VALID_ITEM_IDS |
| `checked` | bool | User-set; does not prove independent action |
| `checked_at` | datetime (UTC) | Server-set |

### `case_feedback` table

| Column | Type | Notes |
|--------|------|-------|
| `id` | UUID string | PK |
| `case_id` | UUID string | FK → `cases.id` CASCADE DELETE; UNIQUE |
| `verified_independently` | bool? | Nullable |
| `still_has_concerns` | bool? | Nullable |
| `result_useful` | bool? | Nullable |
| `result_clear` | bool? | Nullable |
| `note` | string(500)? | Nullable; trimmed server-side |
| `submitted_at` | datetime (UTC) | Server-set |
| `updated_at` | datetime (UTC) | Server-set on update |

---

## Retention and Deletion

| Event | Effect |
|-------|--------|
| Case deleted | All `checklist_progress` and `case_feedback` rows for that case are cascade-deleted |
| User unchecks item | `checked=False` stored; row is retained |
| User deletes feedback | `DELETE /feedback` → row removed; GET returns 404 |
| Individual item delete | Not supported; use PATCH with `checked: false` |

---

## Privacy Considerations

> **⚠️ This is a LOCAL/DEMO PROTOTYPE.**
> It must NOT be exposed publicly with real, sensitive case data unless
> authentication (e.g. JWT/OAuth), HTTPS, and encryption at rest are added.
> No user identity is collected, but case descriptions and evidence metadata
> may contain sensitive information.

- No user identity fields are accepted by any endpoint.
- No credentials or payment data are accepted.
- All timestamps are server-side UTC.
- No IP addresses or session tokens are stored.

---

## Frontend

`ChecklistPanel` is mounted in `CaseDetailPage` below `DecisionPanel`.

| Feature | Implementation |
|---------|---------------|
| Grouped display | Items organised by category with section headers |
| Custom checkboxes | Accessible label+checkbox, keyboard-navigable, focus ring |
| Live toggle | PATCH on change; spinner during save; error surfaced inline |
| Progress bar | Fills as items are checked; labelled for screen readers |
| Persistent state | Re-loaded from server on mount; survives page refresh |
| India-specific badge | 🇮🇳 flag on `india_only` items |
| Disclaimer | Always visible above checklist: completion ≠ proof of safety |
| Feedback form | Tri-state buttons (Yes/No/Not answered) + textarea with char counter |
| Save/loading/error/success | All states handled with accessible ARIA attributes |

---

## Limitations

- Checklist completion records user intent only — the system cannot independently verify actions.
- The cybercrime helpline number (1930) and URL (cybercrime.gov.in) should be verified against official government sources before each demo.
- No authentication — all cases are accessible to any local user.
- Evidence and case descriptions may be sensitive; do not expose publicly.

---

## Test Results

```
tests/test_checklist.py  — 45 tests (12 GET + 11 PATCH + 22 Feedback)
tests/test_decision.py   — 42 tests
tests/test_analysis.py   — 21 tests
tests/test_evidence.py   — 30 tests
tests/test_cases.py      — 33 tests
tests/test_health.py     —  7 tests
──────────────────────────────────────────────
Total: 178 passed, 0 failed

npm run build: ✅ 0 errors — ~65 modules
```

---

## Unresolved Issues

| Issue | Severity | Notes |
|-------|----------|-------|
| No authentication | High | Local prototype only — add before any public deployment |
| cybercrime.gov.in URL / 1930 helpline | Medium | Verify before each demo |
| No async analysis queue | Low | Synchronous — deferred |
| No encryption at rest | Low | Add before production |
