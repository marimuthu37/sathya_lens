# SatyaLens

> Cybersecurity prototype for detecting and verifying suspicious financial communications.

---

## Project structure

```
satyalens/
├── frontend/          # React + Vite application (Person 2)
├── backend/           # Python FastAPI application (Person 2)
├── docs/              # Architecture and design notes
├── .env.example       # Environment variable template
├── .gitignore
└── README.md
```

---

## Quick start

### Prerequisites

| Tool | Version |
|------|---------|
| Node.js | ≥ 20 LTS |
| Python | ≥ 3.11 |
| npm | ≥ 10 |

### 1 — Environment variables

```bash
cp .env.example .env
# Edit .env with your local values
```

### 2 — Backend

```bash
cd backend
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Health check:

```bash
curl http://localhost:8000/api/health
```

Run tests:

```bash
pytest -v
```

### 3 — Frontend

```bash
cd frontend
npm install
npm run dev
```

Open `http://localhost:5173` in your browser.

---

## Architecture

| Layer | Technology |
|-------|-----------|
| Frontend | React 18, Vite 5, plain CSS |
| Backend API | Python 3.11, FastAPI, Uvicorn |
| Database | SQLite via SQLAlchemy 2 |
| Validation | Pydantic v2 |
| File uploads | python-multipart |

---

## Ownership

| Area | Owner |
|------|-------|
| Analysis module (audio deepfake, OCR, reconciliation, rules, metrics) | Person 1 |
| Frontend, backend API, DB, decision engine, evidence ledger, exports | Person 2 |

---

## Segment status

- [x] Segment 1 — Project foundation
- [ ] Segment 2 — Consent & file-upload workflow
- [ ] Segment 3 — Decision engine & evidence ledger
- [ ] Segment 4 — Reporting & export
