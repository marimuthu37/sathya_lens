# SatyaLens — Architecture Notes (Segment 1)

## Decisions made in this segment

### Backend module layout

```
backend/
  app/
    __init__.py
    main.py          # FastAPI app factory + startup
    config.py        # Pydantic Settings — reads from .env
    database.py      # SQLAlchemy engine + session factory
    models.py        # ORM models (placeholder for Segment 2+)
    schemas.py       # Pydantic request/response schemas
    routes/
      __init__.py
      health.py      # GET /api/health
  tests/
    __init__.py
    test_health.py
  requirements.txt
```

### Design rationale

- **`config.py` via Pydantic Settings** — Single source of truth for all env vars. Avoids
  scattered `os.getenv()` calls throughout the codebase.

- **`database.py` centralised** — `get_db()` dependency injected into routes; no raw
  connections elsewhere.

- **CORS from env** — `CORS_ORIGINS` is a comma-separated list, parsed at startup, so
  the same codebase works in dev and CI without code changes.

- **Consistent error JSON** — FastAPI exception handlers return `{"error": {"code": ...,
  "message": ...}}` for all 4xx / 5xx responses.

- **`uploads/` and `reports/` excluded from git** — user evidence must never be committed.

### Deferred to later segments

- Authentication / JWT (not a confirmed requirement yet)
- Case ORM models and CRUD (Segment 2)
- Evidence hashing and ledger (Segment 3)
- Analysis module integration (Person 1 boundary)
