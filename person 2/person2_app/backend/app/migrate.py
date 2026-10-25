"""
app/migrate.py
──────────────
Lightweight, idempotent SQLite schema migration for SatyaLens.

Why this exists
───────────────
SQLAlchemy's `Base.metadata.create_all()` creates tables that are missing
entirely but does NOT add columns to existing tables. When a new column is
added to a model and the database already contains that table, every INSERT
will fail with:

    sqlite3.OperationalError: table <name> has no column named <col>

This module runs at startup (called from app/main.py lifespan, before any
request is served) and applies any missing ALTER TABLE additions.

Design principles
─────────────────
- Idempotent: safe to run on every startup. Uses PRAGMA table_info to check
  whether a column already exists before issuing ALTER TABLE.
- Non-destructive: never drops tables, never deletes rows, never alters
  existing column types or constraints.
- Explicit: each migration step is a named function with a docstring.
- Inspectable: every applied or skipped step is logged at INFO level.
- Backup-friendly: the caller (main.py) is responsible for backups; this
  module only reads and adds.

Adding future migrations
────────────────────────
1. Write a function `_mXX_your_description(conn)` following the pattern below.
2. Append it to MIGRATIONS at the bottom of this file.
3. Nothing else changes — it will be applied automatically on the next startup.
"""

from __future__ import annotations

import logging
import sqlite3
from typing import Callable

logger = logging.getLogger(__name__)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    """Return the set of existing column names for *table*."""
    cursor = conn.execute(f"PRAGMA table_info({table})")
    return {row[1] for row in cursor.fetchall()}


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    cursor = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    )
    return cursor.fetchone() is not None


def _add_column_if_missing(
    conn: sqlite3.Connection,
    table: str,
    column: str,
    definition: str,
    migration_name: str,
) -> None:
    """
    Add *column* with *definition* to *table* if it does not already exist.
    Logs the outcome.
    """
    if not _table_exists(conn, table):
        logger.warning(
            "[%s] Table %r does not exist yet — skipping (create_all will handle it).",
            migration_name, table,
        )
        return

    existing = _get_columns(conn, table)
    if column in existing:
        logger.info(
            "[%s] Column %r.%r already present — skipping.",
            migration_name, table, column,
        )
        return

    sql = f"ALTER TABLE {table} ADD COLUMN {column} {definition}"
    logger.info("[%s] Applying: %s", migration_name, sql)
    conn.execute(sql)
    conn.commit()
    logger.info("[%s] Column %r.%r added successfully.", migration_name, table, column)


# ── Migration steps ───────────────────────────────────────────────────────────

def _m01_add_cases_decision_computed(conn: sqlite3.Connection) -> None:
    """
    Fix: Add decision_computed BOOLEAN NOT NULL DEFAULT 0 to cases.

    Root cause: The security-fixes segment added this column to the SQLAlchemy
    Case model, but existing databases were not updated. Every INSERT into
    cases then failed with:
        sqlite3.OperationalError: table cases has no column named decision_computed

    DEFAULT 0 is correct: existing cases have never had a decision computed,
    so False is the right backfill value.
    """
    _add_column_if_missing(
        conn=conn,
        table="cases",
        column="decision_computed",
        definition="BOOLEAN NOT NULL DEFAULT 0",
        migration_name="m01_add_cases_decision_computed",
    )


def _m02_add_analysis_results_payment_json(conn: sqlite3.Connection) -> None:
    """
    Add payment_json TEXT NULL to analysis_results.

    Holds the structured payment-screenshot summary (extracted fields, expected
    payment details, comparisons, image quality). NULL is the correct backfill:
    existing rows were analysed without it and keep their limitations text.
    """
    _add_column_if_missing(
        conn=conn,
        table="analysis_results",
        column="payment_json",
        definition="TEXT",
        migration_name="m02_add_analysis_results_payment_json",
    )


# ── Migration registry ────────────────────────────────────────────────────────
# Append new migration functions here. Each is called in order on every startup.
# Each function MUST be idempotent.

MIGRATIONS: list[tuple[str, Callable[[sqlite3.Connection], None]]] = [
    ("m01_add_cases_decision_computed", _m01_add_cases_decision_computed),
    ("m02_add_analysis_results_payment_json", _m02_add_analysis_results_payment_json),
]


# ── Public entry point ────────────────────────────────────────────────────────

def run_migrations(database_url: str) -> None:
    """
    Run all pending migrations against the database at *database_url*.

    Only SQLite databases are handled. For other dialects this function logs
    a warning and returns immediately (Alembic or equivalent should be used).

    Called from app/main.py lifespan before Base.metadata.create_all().
    """
    if not database_url.startswith("sqlite"):
        logger.warning(
            "run_migrations: non-SQLite database detected (%r). "
            "Skipping lightweight migrations — use Alembic for production.",
            database_url.split(":")[0],
        )
        return

    # Extract the file path from the URL (handles sqlite:///./path and sqlite:////abs)
    db_path = database_url.replace("sqlite:///", "", 1)

    logger.info("Running schema migrations on %r ...", db_path)

    try:
        conn = sqlite3.connect(db_path)
        try:
            for name, fn in MIGRATIONS:
                try:
                    fn(conn)
                except Exception:
                    logger.exception("Migration %r failed — stopping migration run.", name)
                    raise
        finally:
            conn.close()
    except Exception:
        logger.exception(
            "Schema migration failed. The application may be in an inconsistent state. "
            "Restore from backup if necessary: <database>.backup_<timestamp>"
        )
        raise

    logger.info("Schema migrations complete.")
