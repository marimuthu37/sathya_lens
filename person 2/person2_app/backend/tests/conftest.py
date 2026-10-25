"""
tests/conftest.py
─────────────────
Shared pytest fixtures for the SatyaLens backend test suite.

Design notes:
- Each test gets a fresh in-memory SQLite database (StaticPool so every
  connection checkout reuses the SAME sqlite3 connection object — required
  because SQLite :memory: creates a new DB per connection).
- `get_db` is overridden via FastAPI's dependency_overrides so the injected
  test session is used by all route handlers.
- Segment 1 tests (test_health.py) do not use the `client` fixture and are
  therefore unaffected by this setup.
"""

from __future__ import annotations

import os
import tempfile

# `with TestClient(app)` runs the app lifespan, which applies schema migrations
# and create_all to settings.database_url.  Point it at a throwaway file BEFORE
# app.config is imported so the suite never alters the developer's satyalens.db.
os.environ["DATABASE_URL"] = (
    f"sqlite:///{tempfile.mkdtemp(prefix='satyalens_tests_').replace(os.sep, '/')}/lifespan.db"
)

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event as sa_event, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

# Import models BEFORE Base.metadata.create_all so tables are registered.
import app.models  # noqa: F401

from app.database import Base, get_db
from app.main import app as fastapi_app   # alias avoids shadowing 'app' package


# ── In-memory engine with StaticPool ─────────────────────────────────────────

@pytest.fixture(scope="function")
def db_session():
    """
    Yield a SQLAlchemy Session backed by a fresh in-memory SQLite database.

    StaticPool ensures every connection checkout from the engine (including
    those opened in FastAPI's thread pool) shares the single underlying
    sqlite3 connection — and therefore the same :memory: database with its
    tables intact.
    """
    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    # Enable SQLite FK enforcement via event (SQLAlchemy 2 requires text() for raw SQL)
    @sa_event.listens_for(test_engine, "connect")
    def _fk_pragma(dbapi_conn, _record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(bind=test_engine)

    TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
    session = TestingSession()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=test_engine)
        test_engine.dispose()


@pytest.fixture(scope="function")
def client(db_session):
    """
    Yield a TestClient with `get_db` overridden to inject the isolated session.
    """
    def _override_get_db():
        try:
            yield db_session
        finally:
            pass  # session lifecycle managed by db_session fixture

    fastapi_app.dependency_overrides[get_db] = _override_get_db
    with TestClient(fastapi_app, raise_server_exceptions=False) as c:
        yield c
    fastapi_app.dependency_overrides.pop(get_db, None)
