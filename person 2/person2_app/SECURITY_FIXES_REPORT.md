# SatyaLens — Security Fixes Report

> **Run date**: 2026-10-24
> **Backend tests**: **230 passed, 0 failed** (178 pre-existing + 52 new regression)
> **Frontend build**: ✅ 0 errors — 61 modules, 390 ms

---

## Issue 1 — Case deletion and evidence file cleanup

**Root cause**
`DELETE /api/cases/{case_id}` delegated deletion entirely to SQLAlchemy's
`cascade="all, delete-orphan"`, which removes the database rows for Evidence
records but never touches the on-disk files. The original code even contained
the comment: _"Raw evidence files are NOT deleted by this endpoint."_

**Fix**
In [`app/routes/cases.py`](satyalens/backend/app/routes/cases.py):

1. Before the DB delete, `_collect_evidence_paths()` derives the server-side
   filename for each evidence item using the same logic as the upload route
   and returns a list of `Path` objects. Each path is resolved with
   `.relative_to(upload_dir)` — anything that escapes the upload directory is
   skipped and logged (path-traversal guard).
2. The DB delete is committed first (so metadata is always gone even if a file
   cannot be removed).
3. `_delete_evidence_files()` then removes each file, catching `OSError` per
   file. Failures are logged with full paths for operator reconciliation; they
   do NOT cause a 500 (the DB is already committed — a false failure response
   would be inaccurate and misleading).

**Orphaned file strategy**
If a file cannot be removed, the path is logged at ERROR level. An operator
can search logs for `"Could not remove evidence file"` and clean up manually.
No silent failure occurs.

**Tests added** — `TestIssue1CaseDeletion` (4 tests)
- `test_case_deletion_removes_evidence_file` — file gone after case delete
- `test_case_deletion_with_no_evidence_succeeds` — empty case still deleted
- `test_case_deletion_removes_db_record` — 404 after delete
- `test_case_deletion_path_traversal_guard` — out-of-bounds path is skipped

---

## Issue 2 — Enforce explicit consent on the backend

**Root cause**
`POST /api/cases/{case_id}/evidence/consent` accepted no body. Any caller
(including a script that skipped the frontend entirely) could grant consent
without ever seeing or acknowledging the consent notice.

**Fix**

| File | Change |
|------|--------|
| `app/schemas.py` | Added `ConsentRequest` with required `acknowledged: bool` field validated by a Pydantic validator that rejects `False` |
| `app/schemas.py` | Added `CONSENT_POLICY_VERSION = "1.0"` constant stored server-side |
| `app/schemas.py` | `ConsentResponse` now includes `consent_policy_version` |
| `app/routes/evidence.py` | `record_consent` now requires `body: ConsentRequest`; records server-generated UTC timestamp and policy version |
| `frontend/src/api/api.js` | `recordConsent()` now sends `{ acknowledged: true }` JSON body |
| `frontend/src/components/ConsentPanel/ConsentPanel.jsx` | Removed unsupported promise "No backups are kept"; replaced with honest text about host-filesystem copies |

**What is NOT changed**
- User identity is still not verified (no auth). The consent record only
  confirms the API received `acknowledged: true` in the request body.
  The limitation is documented in `SECURITY_NOTICE.md`.

**Tests added** — `TestIssue2ConsentAcknowledgement` (7 tests)
- Missing body → 422
- `acknowledged: false` → 422
- `acknowledged: true` → 200 with `consent_policy_version`
- Idempotent with `acknowledged: true`
- Missing field in otherwise-present body → 422
- Upload gate still enforced

---

## Issue 3 — Decision persistence and retrieval

**Root cause**
`INSUFFICIENT_EVIDENCE` has no matching `Decision` enum value, so
`Case.decision = None` was stored. The GET endpoint checked
`if case.decision is None → 404`, which meant any POST that produced
`INSUFFICIENT_EVIDENCE` would be unretrievable via GET.

**Fix**

| File | Change |
|------|--------|
| `app/models.py` | Added `decision_computed: Mapped[bool] = mapped_column(default=False)` to `Case` |
| `app/routes/decision.py` | POST sets `case.decision_computed = True` after every successful computation |
| `app/routes/decision.py` | GET gates on `not case.decision_computed` instead of `case.decision is None` |

This cleanly separates "has a decision ever been computed?" from "what is the
decision category?", preserving the existing `Case.decision` column for the
case-list view.

**Tests added** — `TestIssue3DecisionPersistence` (6 tests)
- POST + GET for `INSUFFICIENT_EVIDENCE` returns 200, not 404
- GET before POST returns 404
- POST and GET categories are consistent
- `decision_computed` flag is True after POST, False before
- All categories retrievable (structural coverage)

---

## Issue 4 — Evidence deletion failures

**Root cause**
The deletion endpoint deleted the DB record first, then tried to unlink the
file. If the file unlink threw `OSError`, it was silently swallowed (`logger.warning`
only) and 204 was returned — the caller believed deletion succeeded while the
file remained on disk.

**Fix**
In `app/routes/evidence.py`, `delete_evidence` now uses a **rename-then-commit** strategy:

1. If the file exists, rename it to `<name>.deleting` (atomic on same filesystem).
2. If the rename fails → 500, no DB record touched.
3. Commit the DB delete.
4. If the DB commit fails → rollback, rename `.deleting` back to original → 500.
5. If the DB commit succeeds → `unlink(.deleting)`. If unlink fails, log ERROR for manual cleanup.

**What is NOT falsely reported**
- If rename fails: 500 returned, DB untouched.
- If DB fails after rename: 500 returned, file restored.
- If unlink of `.deleting` fails after DB commit: 204 returned with ERROR log.
  (The DB is already committed — returning 500 would be inaccurate. The orphaned
  `.deleting` file is logged for manual cleanup.)

**Tests added** — `TestIssue4EvidenceDeletion` (4 tests)
- Full success: file and record both removed
- Staging failure (rename mock) → 500, record unchanged
- Missing file → 204, metadata still removed (already-absent file is safe)
- Nonexistent evidence → 404

---

## Issue 5 — Strict analysis findings validation

**Root cause**
`findings_from_json()` in `app/services/analysis.py` returned raw deserialized
dicts without field-level type validation. The decision engine used
`bool(f.get("is_interpretation", False))`, and because `bool("false") == True`
in Python, a finding serialized with `"is_interpretation": "false"` (string)
would be silently treated as an interpretation finding — changing the
category outcome.

**Fix**
Added `_validate_finding_dict()` that enforces:
- Entry must be a `dict`.
- Required string fields (`finding_type`, `label`, `detail`, `evidence_source`)
  must be present and non-empty `str`.
- `is_interpretation` must be an actual Python `bool` — not `str`, not `int`.
  Anything else is discarded with a WARNING log.
- `reliability_note` must be `str` or `None` if present.
- Output is a **clean copy** containing only the 6 expected fields; no extra
  keys from the raw JSON (e.g. embedded secrets) leak through.
- Discarded entries are logged at WARNING level — never silently ignored.

**Tests added** — `TestIssue5FindingsValidation` (14 tests)
- `"false"`, `"true"`, `1` for `is_interpretation` all discarded
- `False`, `True` (real bools) accepted
- Missing required field discarded
- Non-dict entry discarded
- Non-list top-level JSON → empty
- Malformed JSON → empty
- Empty/null → empty
- Valid finding with all fields accepted
- Mixed valid/invalid → only valid returned
- Output contains only expected fields (no leakage)

---

## Issue 6 — Upload handling: streaming temp file

**Root cause**
The upload route collected all chunks into a list (`chunks: list[bytes]`) and
called `b"".join(chunks)`. For large files this held the entire file content
in memory simultaneously. It also used `Path.write_bytes(file_bytes)` — a
single atomic call — so there was no partial-write cleanup path.

**Fix**
The route now:
1. Opens a `NamedTemporaryFile(delete=False, suffix=".tmp")` in the upload dir.
2. Streams 64 KiB chunks into it while simultaneously updating SHA-256 and
   enforcing the size limit. Only one chunk lives in memory at a time.
3. Reads just the first 16 bytes from the temp file for magic-byte validation.
4. After all validation passes, calls `tmp_path.rename(dest_path)` — an atomic
   rename on the same filesystem.
5. On any failure (413, 415, OSError, 400), the temp file is unlinked.

The `test_upload_storage_error_returns_500` test was updated to mock
`Path.rename` (the new failure point) rather than `Path.write_bytes`.

**Tests added** — `TestIssue6UploadStreaming` (4 tests)
- Rename failure → 500, no DB record
- Rename failure → temp file cleaned up
- Successful upload leaves only final file (no `.tmp` residue)
- Oversized upload cleans up temp file after 413

---

## Issue 7 — Authentication limitation documented

**Status**: No authentication exists. A rushed incomplete implementation
would create a false sense of security.

**What was done**:
- Created [`SECURITY_NOTICE.md`](satyalens/SECURITY_NOTICE.md) documenting:
  - Every route's authentication status (all: unauthenticated)
  - Minimum requirements for public deployment
  - Why incomplete auth was not added
  - Safe use instructions (localhost only)
- Router docstrings updated with `NOTE: This application has no authentication.`

**Tests added** — `TestIssue7AuthLimitation` (6 tests)
These tests **confirm** that routes accept unauthenticated requests. They are
documentation tests — if authentication is added later, these tests serve as
the baseline to update.

**Routes accessible without credentials (all routes)**:
- Case management: CREATE, LIST, GET, DELETE
- Consent, evidence upload, evidence deletion
- Analysis, decisions
- Checklist, feedback

---

## Issue 8 — Source distribution / packaging

**Fix**

| File | Change |
|------|--------|
| `.gitignore` | Added `.env.*`, `*.env`, `*.p12`, `*.pfx`, `secrets/`, `backend/tmp/`, `.ruff_cache/`, `*.pytest_cache`, source ZIP pattern `satyalens_release_*.zip` |
| `Makefile` | `make package` command creates a dated ZIP excluding `.venv`, `node_modules`, `uploads`, `*.db`, `.env*`, `*.pem`, `*.key`, `dist/`, `.git/`, caches, and any prior ZIPs |

**Tests added** — `TestIssue8SourceDistribution` (7 tests)
- `.gitignore` excludes: `.venv/`, `node_modules/`, `backend/uploads/`, `*.db`, `.env`, `*.pem`
- `SECURITY_NOTICE.md` exists and contains auth/demo language

---

## Files modified

| File | Type | Reason |
|------|------|--------|
| `backend/app/routes/cases.py` | Modified | Issue 1: file cleanup on case delete |
| `backend/app/models.py` | Modified | Issue 3: `decision_computed` column |
| `backend/app/routes/decision.py` | Modified | Issue 3: set/check `decision_computed` |
| `backend/app/schemas.py` | Modified | Issue 2: `ConsentRequest`, `ConsentResponse`, `CONSENT_POLICY_VERSION` |
| `backend/app/routes/evidence.py` | Modified | Issues 2, 4, 6: consent body, atomic delete, streaming upload |
| `backend/app/services/analysis.py` | Modified | Issue 5: `_validate_finding_dict`, strict `findings_from_json` |
| `backend/tests/test_evidence.py` | Modified | Update consent helpers + storage error mock |
| `backend/tests/test_analysis.py` | Modified | Update consent helpers |
| `backend/tests/test_decision.py` | Modified | Update consent helpers |
| `frontend/src/api/api.js` | Modified | Issue 2: send `acknowledged: true` body |
| `frontend/src/components/ConsentPanel/ConsentPanel.jsx` | Modified | Issue 2: remove unsupported promises |
| `.gitignore` | Modified | Issue 8: broader exclusion coverage |

## Files created

| File | Reason |
|------|--------|
| `backend/tests/test_security_fixes.py` | 52 regression tests (Issues 1–8) |
| `SECURITY_NOTICE.md` | Issue 7: authentication limitation documentation |
| `Makefile` | Issue 8: `make package` clean distribution command |

---

## Remaining limitations

| Limitation | Severity | Notes |
|-----------|----------|-------|
| No authentication | **High** | All routes unauthenticated. See SECURITY_NOTICE.md. Local/demo only. |
| No HTTPS | **High** | Must be added before any network deployment |
| No per-case ownership | **High** | Any caller with a case ID can modify it |
| Orphaned `.deleting` files | **Low** | If unlink fails after DB commit, logged at ERROR. Manual cleanup required. |
| Host-filesystem copies | **Low** | OS temp files, filesystem snapshots outside app's control |
| No rate limiting | **Medium** | Upload endpoint has app-layer size limit only |
| Encryption at rest | **Low** | Evidence files stored as plaintext on disk |

---

## Test results

```
Platform: darwin, Python 3.9.6, pytest-8.3.4

test_cases.py       — 33 passed
test_analysis.py    — 21 passed
test_decision.py    — 42 passed
test_evidence.py    — 30 passed
test_checklist.py   — 45 passed
test_health.py      —  7 passed
test_security_fixes — 52 passed  ← NEW
────────────────────────────────
Total: 230 passed, 0 failed

npm run build: ✅ 0 errors, 61 modules
```

> No tests were weakened, removed, or assertion-softened to obtain a passing suite.
