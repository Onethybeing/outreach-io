# outreach-io

Resume-to-outreach LangGraph agent. See [PLAN.md](./PLAN.md) for the full design.

## Phase 1 status

- FastAPI skeleton (`app/main.py`) with `/health` and a resume-library router (`/resumes`
  upload + list).
- SQLAlchemy models (`app/models.py`) for the full schema in `PLAN.md` §4: resumes, runs,
  startups, contacts, email_events, app_settings.
- Alembic configured (`alembic/`), with hand-written initial migration
  `alembic/versions/0001_initial_schema.py` (no live DB was available yet to autogenerate
  against — verify it against Neon before trusting it blindly, see below).

## Setup

```bash
python -m venv .venv
.venv/Scripts/activate      # or source .venv/bin/activate on macOS/Linux
pip install -r requirements.txt
cp .env.example .env        # fill in DATABASE_URL at minimum to run migrations
alembic upgrade head
uvicorn app.main:app --reload
```

`GET /health` reports `{"status": "ok", "db": "ok"}` once `DATABASE_URL` points at a reachable
Postgres instance (e.g. a Neon connection string).

## Next (Phase 2)

`discovery_graph` — resume ingest/OCR, LLM parsing, Tavily startup search, KDM discovery,
Postgres dedupe — per `PLAN.md` §2.
