"""
app/database.py
───────────────
Centralised SQLAlchemy database module.

Usage
-----
  from app.database import get_db, Base

  # In a route handler:
  def my_route(db: Session = Depends(get_db)):
      ...

  # In models.py:
  from app.database import Base
  class MyModel(Base): ...
"""

from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings


# ── Engine ────────────────────────────────────────────────────────────────────

# connect_args is required for SQLite to enable foreign-key enforcement.
connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}

engine = create_engine(
    settings.database_url,
    connect_args=connect_args,
    echo=settings.sql_echo,      # opt-in (SQL_ECHO=true); see config.py
    hide_parameters=True,        # never log user content bound to SQL statements
)

# Enable SQLite foreign-key support per connection.
if settings.database_url.startswith("sqlite"):
    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, _connection_record) -> None:  # noqa: ANN001
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


# ── Session factory ───────────────────────────────────────────────────────────

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
)


# ── Declarative base (shared by all ORM models) ───────────────────────────────

class Base(DeclarativeBase):
    """Base class for all SQLAlchemy ORM models."""


# ── FastAPI dependency ────────────────────────────────────────────────────────

def get_db() -> Generator[Session, None, None]:
    """
    Yield a database session and guarantee it is closed after the request,
    even if an exception is raised.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
