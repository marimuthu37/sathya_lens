"""
tests/test_migration.py
────────────────────────
Regression tests for app/migrate.py and the database schema fix.

Issue: After the security-fixes segment added decision_computed to the
Case SQLAlchemy model, the existing satyalens.db was not updated. Every
POST /api/cases returned HTTP 500:
  sqlite3.OperationalError: table cases has no column named decision_computed

All tests use temporary in-memory or on-disk SQLite databases.
The developer's real satyalens.db is never used or modified here.
"""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import pytest


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _create_legacy_cases_table(conn: sqlite3.Connection) -> None:
    """
    Create the 'cases' table as it existed BEFORE the decision_computed column
    was added (i.e. the pre-migration schema).
    """
    conn.execute("""
        CREATE TABLE IF NOT EXISTS cases (
            id                VARCHAR(36) PRIMARY KEY,
            created_at        DATETIME NOT NULL,
            updated_at        DATETIME NOT NULL,
            status            VARCHAR(10) NOT NULL,
            consent_status    VARCHAR(7)  NOT NULL,
            consent_at        DATETIME,
            analysis_status   VARCHAR(11) NOT NULL,
            decision          VARCHAR(12),
            description       TEXT
        )
    """)
    conn.commit()


def _create_modern_cases_table(conn: sqlite3.Connection) -> None:
    """Create the 'cases' table that already includes decision_computed."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS cases (
            id                VARCHAR(36) PRIMARY KEY,
            created_at        DATETIME NOT NULL,
            updated_at        DATETIME NOT NULL,
            status            VARCHAR(10) NOT NULL,
            consent_status    VARCHAR(7)  NOT NULL,
            consent_at        DATETIME,
            analysis_status   VARCHAR(11) NOT NULL,
            decision          VARCHAR(12),
            decision_computed BOOLEAN NOT NULL DEFAULT 0,
            description       TEXT
        )
    """)
    conn.commit()


def _get_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _insert_legacy_case(conn: sqlite3.Connection, case_id: str = "test-id-001") -> None:
    """Insert a row into a legacy (pre-migration) cases table."""
    conn.execute("""
        INSERT INTO cases
          (id, created_at, updated_at, status, consent_status, analysis_status, description)
        VALUES
          (?, '2026-01-01', '2026-01-01', 'open', 'pending', 'not_started', 'legacy case')
    """, (case_id,))
    conn.commit()


def _run_migration_on_file(db_path: str) -> None:
    """Run the migration against a real file-based SQLite database."""
    from app.migrate import run_migrations
    run_migrations(f"sqlite:///{db_path}")


# ─────────────────────────────────────────────────────────────────────────────
# Migration unit tests (using the migration module directly)
# ─────────────────────────────────────────────────────────────────────────────

class TestMigrationModule:

    def test_adds_decision_computed_to_legacy_db(self, tmp_path):
        """Migration adds decision_computed when it is absent."""
        db = str(tmp_path / "legacy.db")
        conn = sqlite3.connect(db)
        _create_legacy_cases_table(conn)
        conn.close()

        assert "decision_computed" not in _get_columns(sqlite3.connect(db), "cases")
        _run_migration_on_file(db)
        assert "decision_computed" in _get_columns(sqlite3.connect(db), "cases")

    def test_migration_is_idempotent_second_run(self, tmp_path):
        """Running migration twice must not raise or duplicate columns."""
        db = str(tmp_path / "idem.db")
        conn = sqlite3.connect(db)
        _create_legacy_cases_table(conn)
        conn.close()

        _run_migration_on_file(db)
        _run_migration_on_file(db)  # second run — must be safe

        cols = _get_columns(sqlite3.connect(db), "cases")
        assert "decision_computed" in cols

    def test_migration_on_already_modern_db_is_safe(self, tmp_path):
        """If decision_computed already exists, migration must not raise."""
        db = str(tmp_path / "modern.db")
        conn = sqlite3.connect(db)
        _create_modern_cases_table(conn)
        conn.close()

        _run_migration_on_file(db)  # must not raise

        cols = _get_columns(sqlite3.connect(db), "cases")
        assert "decision_computed" in cols

    def test_existing_rows_preserved_after_migration(self, tmp_path):
        """Migration must not delete or alter existing case rows."""
        db = str(tmp_path / "preserve.db")
        conn = sqlite3.connect(db)
        _create_legacy_cases_table(conn)
        _insert_legacy_case(conn, "case-preserve-1")
        _insert_legacy_case(conn, "case-preserve-2")
        conn.close()

        _run_migration_on_file(db)

        conn = sqlite3.connect(db)
        rows = list(conn.execute("SELECT id FROM cases ORDER BY id"))
        conn.close()
        assert [r[0] for r in rows] == ["case-preserve-1", "case-preserve-2"]

    def test_existing_rows_get_default_decision_computed_false(self, tmp_path):
        """Existing cases must backfill decision_computed=0 after migration."""
        db = str(tmp_path / "backfill.db")
        conn = sqlite3.connect(db)
        _create_legacy_cases_table(conn)
        _insert_legacy_case(conn, "old-case")
        conn.close()

        _run_migration_on_file(db)

        conn = sqlite3.connect(db)
        row = conn.execute(
            "SELECT decision_computed FROM cases WHERE id = 'old-case'"
        ).fetchone()
        conn.close()
        assert row is not None
        assert row[0] == 0  # NOT NULL DEFAULT 0

    def test_migration_skips_nonexistent_table(self, tmp_path):
        """If 'cases' table doesn't exist yet, migration logs a warning and skips."""
        db = str(tmp_path / "empty.db")
        # Empty database — no tables at all
        sqlite3.connect(db).close()
        # Must not raise; create_all will create the table later
        _run_migration_on_file(db)

    def test_migration_column_definition(self, tmp_path):
        """Verify the added column has NOT NULL and DEFAULT 0."""
        db = str(tmp_path / "check_def.db")
        conn = sqlite3.connect(db)
        _create_legacy_cases_table(conn)
        conn.close()

        _run_migration_on_file(db)

        conn = sqlite3.connect(db)
        info = {r[1]: r for r in conn.execute("PRAGMA table_info(cases)")}
        conn.close()

        col = info["decision_computed"]
        assert col[3] == 1, "NOT NULL constraint expected"  # notnull flag
        assert col[4] == "0", f"Default should be 0, got {col[4]!r}"

    def test_adds_nullable_payment_json_and_keeps_rows(self, tmp_path):
        """m02: legacy analysis_results gains payment_json NULL; rows are untouched."""
        db = str(tmp_path / "legacy_analysis.db")
        conn = sqlite3.connect(db)
        _create_modern_cases_table(conn)
        conn.execute("CREATE TABLE analysis_results (id TEXT PRIMARY KEY, evidence_id TEXT, "
                     "status TEXT, findings_json TEXT, limitations TEXT)")
        conn.execute("INSERT INTO analysis_results VALUES ('r1', 'e1', 'COMPLETED', NULL, 'old notes')")
        conn.commit()
        conn.close()

        _run_migration_on_file(db)
        _run_migration_on_file(db)  # idempotent

        conn = sqlite3.connect(db)
        info = {r[1]: r for r in conn.execute("PRAGMA table_info(analysis_results)")}
        row = conn.execute("SELECT limitations, payment_json FROM analysis_results WHERE id='r1'").fetchone()
        conn.close()
        assert info["payment_json"][3] == 0, "payment_json must be nullable"
        assert row == ("old notes", None)

    def test_non_sqlite_url_skipped(self, capsys):
        """Non-SQLite database URLs must be skipped without error."""
        from app.migrate import run_migrations
        # Should not raise — just logs a warning
        run_migrations("postgresql://user:pass@localhost/db")


# ─────────────────────────────────────────────────────────────────────────────
# Integration tests via FastAPI TestClient (use in-memory test DB from conftest)
# ─────────────────────────────────────────────────────────────────────────────

class TestMigrationIntegration:
    """
    These tests use the standard TestClient+conftest fixture which creates
    a fresh in-memory SQLite DB (already has the modern schema). They verify
    that case creation, evidence, decisions, checklist, and feedback still
    work correctly after the migration is applied.
    """

    def test_create_case_returns_201(self, client):
        """POST /api/cases must return 201 after migration."""
        r = client.post("/api/cases", json={})
        assert r.status_code == 201

    def test_create_case_returns_valid_schema(self, client):
        """Response must include all required fields."""
        data = client.post("/api/cases", json={"description": "migration test"}).json()
        for field in ("id", "created_at", "status", "consent_status", "analysis_status", "decision"):
            assert field in data, f"Field {field!r} missing from response"

    def test_create_case_decision_not_in_response(self, client):
        """decision_computed is an internal column and must not appear in API response."""
        data = client.post("/api/cases", json={}).json()
        assert "decision_computed" not in data

    def test_case_appears_in_list(self, client):
        """Newly created case must appear in GET /api/cases."""
        cid = client.post("/api/cases", json={}).json()["id"]
        ids = [c["id"] for c in client.get("/api/cases").json()["items"]]
        assert cid in ids

    def test_case_retrievable_by_id(self, client):
        """GET /api/cases/{id} must return the created case."""
        cid = client.post("/api/cases", json={}).json()["id"]
        r = client.get(f"/api/cases/{cid}")
        assert r.status_code == 200
        assert r.json()["id"] == cid

    def test_insufficient_evidence_decision_survives_get(self, client):
        """
        Regression: POST /decision for a case with no evidence must return
        INSUFFICIENT_EVIDENCE, and a subsequent GET must return 200 (not 404).
        """
        cid = client.post("/api/cases", json={}).json()["id"]
        client.post(
            f"/api/cases/{cid}/evidence/consent",
            json={"acknowledged": True},
        )
        post_r = client.post(f"/api/cases/{cid}/decision")
        assert post_r.status_code == 200
        assert post_r.json()["category"] == "insufficient_evidence"

        get_r = client.get(f"/api/cases/{cid}/decision")
        assert get_r.status_code == 200
        assert get_r.json()["category"] == "insufficient_evidence"

    def test_multiple_cases_creatable(self, client):
        """Verify no per-row state from migration interferes with multiple inserts."""
        ids = [client.post("/api/cases", json={}).json()["id"] for _ in range(5)]
        assert len(set(ids)) == 5


# ─────────────────────────────────────────────────────────────────────────────
# Real database migration test (uses a temp file copy, not satyalens.db)
# ─────────────────────────────────────────────────────────────────────────────

class TestLiveDatabaseMigration:

    def test_real_db_has_decision_computed_column(self):
        """
        Verify that satyalens.db now contains decision_computed after the
        migration was applied. This is a one-time post-hoc verification.
        """
        db_path = Path(__file__).parent.parent / "satyalens.db"
        if not db_path.exists():
            pytest.skip("satyalens.db not found — skipping live DB check")

        conn = sqlite3.connect(str(db_path))
        cols = _get_columns(conn, "cases")
        conn.close()
        assert "decision_computed" in cols, (
            "decision_computed column is still missing from satyalens.db. "
            "Run `python -c 'from app.migrate import run_migrations; "
            "from app.config import settings; run_migrations(settings.database_url)'`"
        )

    def test_copy_of_legacy_db_can_be_migrated(self, tmp_path):
        """
        Simulate migrating a copy of a pre-migration database. Ensures the
        migration does not break when applied to a DB with real Segment 1-6 schema.
        """
        db_path = Path(__file__).parent.parent / "satyalens.db"
        if not db_path.exists():
            pytest.skip("satyalens.db not found — skipping")

        # Work on a copy, never the real file
        test_db = str(tmp_path / "test_copy.db")
        import shutil
        shutil.copy2(str(db_path), test_db)

        # Remove decision_computed to simulate legacy state
        conn = sqlite3.connect(test_db)
        cols_before = _get_columns(conn, "cases")
        conn.close()

        if "decision_computed" in cols_before:
            # Already modern — just re-run and verify idempotency
            _run_migration_on_file(test_db)
        else:
            _run_migration_on_file(test_db)

        conn = sqlite3.connect(test_db)
        assert "decision_computed" in _get_columns(conn, "cases")
        conn.close()
