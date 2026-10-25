# SatyaLens — Database Migration Fix Report

> **Date**: 2026-10-24
> **All backend tests**: **247 passed, 0 failed**
> **Frontend build**: ✅ 0 errors
> **POST /api/cases**: ✅ HTTP 201
> **GET /api/health**: ✅ `{"status":"ok"}`

---

## Root Cause

Every call to `POST /api/cases` produced HTTP 500 with:

```
{"error":{"code":"INTERNAL_ERROR","message":"An unexpected error occurred. Please try again."}}
```

The actual exception (confirmed by direct ORM reproduction):

```
sqlalchemy.exc.OperationalError: (sqlite3.OperationalError)
table cases has no column named decision_computed

[SQL: INSERT INTO cases (..., decision_computed, ...) VALUES (?, ...)]
```

**Why it happened**: The security-fixes segment (Issue 3) added
`decision_computed: Mapped[bool]` to the `Case` SQLAlchemy model to fix
the `INSUFFICIENT_EVIDENCE` → GET 404 bug. SQLAlchemy's
`Base.metadata.create_all()` was already being called at startup, but
`create_all` only creates tables that are entirely absent — it **never adds
columns to existing tables**. The `cases` table already existed in
`satyalens.db`, so `create_all` was a no-op for it. Every subsequent INSERT
included `decision_computed` in its column list, causing SQLite to raise an
error.

---

## Schema Differences Found

Comparing `PRAGMA table_info(cases)` against the SQLAlchemy `Case` model:

| Column | In DB before fix | In model | Action |
|--------|-----------------|---------|--------|
| `id` | ✅ | ✅ | — |
| `created_at` | ✅ | ✅ | — |
| `updated_at` | ✅ | ✅ | — |
| `status` | ✅ | ✅ | — |
| `consent_status` | ✅ | ✅ | — |
| `consent_at` | ✅ | ✅ | — |
| `analysis_status` | ✅ | ✅ | — |
| `decision` | ✅ | ✅ | — |
| `description` | ✅ | ✅ | — |
| **`decision_computed`** | ❌ **MISSING** | ✅ | **`ALTER TABLE ADD COLUMN`** |

All other tables (`evidence`, `analysis_results`, `checklist_progress`,
`case_feedback`) matched their models exactly — no additional migrations needed.

---

## Migration Implementation

### `app/migrate.py` (new file)

A lightweight, idempotent migration runner designed for SQLite. Key design
decisions:

| Property | Implementation |
|----------|---------------|
| **Idempotent** | Uses `PRAGMA table_info` to check column existence before `ALTER TABLE` |
| **Non-destructive** | Never drops tables, never deletes rows, never alters existing columns |
| **Logged** | Every applied or skipped step logged at INFO |
| **Ordered** | Steps run in a defined list (`MIGRATIONS`); new steps append to the end |
| **Scoped** | Non-SQLite URLs are skipped with a warning (Alembic should handle those) |
| **Extensible** | Adding a future migration is one function + one list entry |

Migration step applied:

```sql
ALTER TABLE cases ADD COLUMN decision_computed BOOLEAN NOT NULL DEFAULT 0
```

`DEFAULT 0` (False) is correct: existing cases have never had a decision
computed, so `False` is the accurate backfill value.

### `app/main.py` (modified)

The lifespan startup sequence was updated to call `run_migrations()` **before**
`create_all()`:

```python
run_migrations(settings.database_url)  # ALTER TABLE for existing columns
Base.metadata.create_all(bind=engine)  # CREATE TABLE for entirely new tables
```

This ensures the migration runs on every startup, is safe to repeat, and
handles both fresh installs (where `create_all` creates the table with all
columns) and existing deployments (where the migration adds the missing column).

---

## Backup Behavior

Before applying the migration to `satyalens.db`, a timestamped backup was created:

```
backend/satyalens.db.backup_20261024_160449  (84 KB)
```

The original database was not deleted or recreated. All existing case records,
evidence metadata, checklist progress, and feedback rows were preserved.

---

## Files Modified

| File | Change |
|------|--------|
| `backend/app/migrate.py` | **Created** — idempotent migration runner |
| `backend/app/main.py` | Added `run_migrations()` call in lifespan before `create_all` |
| `backend/tests/test_migration.py` | **Created** — 17 regression tests |

---

## Tests Added

**`test_migration.py`** — 17 tests across 3 classes:

### `TestMigrationModule` (8 unit tests)
Tests operate on temporary file-based SQLite databases.

| Test | Verifies |
|------|---------|
| `test_adds_decision_computed_to_legacy_db` | Column added when absent |
| `test_migration_is_idempotent_second_run` | No error or duplicate on re-run |
| `test_migration_on_already_modern_db_is_safe` | Already-modern DB untouched |
| `test_existing_rows_preserved_after_migration` | Zero rows deleted |
| `test_existing_rows_get_default_decision_computed_false` | Backfill = 0 |
| `test_migration_skips_nonexistent_table` | No error on empty database |
| `test_migration_column_definition` | NOT NULL + DEFAULT 0 enforced |
| `test_non_sqlite_url_skipped` | Non-SQLite URL skipped without crash |

### `TestMigrationIntegration` (7 integration tests)
Uses the standard TestClient (in-memory fresh schema).

| Test | Verifies |
|------|---------|
| `test_create_case_returns_201` | POST /api/cases succeeds |
| `test_create_case_returns_valid_schema` | All required fields present |
| `test_create_case_decision_not_in_response` | `decision_computed` not exposed in API |
| `test_case_appears_in_list` | Case visible in GET /api/cases |
| `test_case_retrievable_by_id` | GET /api/cases/{id} returns 200 |
| `test_insufficient_evidence_decision_survives_get` | POST + GET for INSUFFICIENT_EVIDENCE |
| `test_multiple_cases_creatable` | 5 concurrent inserts, all distinct IDs |

### `TestLiveDatabaseMigration` (2 tests)
Tests against a copy of the real database.

| Test | Verifies |
|------|---------|
| `test_real_db_has_decision_computed_column` | `satyalens.db` now has the column |
| `test_copy_of_legacy_db_can_be_migrated` | Migration on a DB copy is safe |

---

## Actual Test Results

```
Backend:
  tests/test_migration.py        —  17 passed
  tests/test_security_fixes.py   —  52 passed
  tests/test_checklist.py        —  45 passed
  tests/test_decision.py         —  42 passed
  tests/test_analysis.py         —  21 passed
  tests/test_evidence.py         —  30 passed
  tests/test_cases.py            —  33 passed
  tests/test_health.py           —   7 passed
  ─────────────────────────────────────────
  Total: 247 passed, 0 failed

Frontend:
  npm run build: ✅ 61 modules, 0 errors

Live HTTP:
  GET  /api/health     → {"status":"ok"}
  POST /api/cases      → HTTP 201 ✅
```

---

## Whether the Existing Database Was Migrated

**Yes.** The migration was applied directly to `satyalens.db` during diagnosis:

```python
from app.migrate import run_migrations
from app.config import settings
run_migrations(settings.database_url)
```

The `decision_computed BOOLEAN NOT NULL DEFAULT 0` column was added. All
existing rows received the default value `0` (False). No data was lost.

The database was backed up to `satyalens.db.backup_20261024_160449` before
any changes were applied.

---

## Remaining Issues

| Item | Severity | Notes |
|------|----------|-------|
| No authentication on any route | High | See `SECURITY_NOTICE.md` — local/demo only |
| `satyalens.db.backup_*` files excluded by `.gitignore` | Low | Desired behavior — backups should never be committed |
| Migration uses raw `sqlite3` (not Alembic) | Low | Appropriate for a local SQLite prototype; Alembic recommended for production |
| `create_all` still called for fresh installs | — | Correct — it creates all tables including `cases` with `decision_computed` for brand-new databases |
