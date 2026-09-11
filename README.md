# outreach-io

Resume-to-outreach LangGraph agent. See [PLAN.md](./PLAN.md) for the full design.

## Status

**Phase 1** — FastAPI app, Postgres schema (Alembic), resume upload/list.

**Phase 2** — backend for login, roles, API key vault and editable prompts:

| Area | Routes | Who |
|---|---|---|
| Google sign-in | `GET /auth/login`, `GET /auth/callback`, `GET /auth/me`, `POST /auth/logout` | listed users only |
| Users & roles | `GET/POST /users`, `PATCH /users/{id}` | admin |
| Audit log | `GET /audit-log?action=&user_id=&before=&limit=` | admin |
| API vault | `GET /vault`, `PUT /vault/{provider}`, `POST /vault/{provider}/test`, `POST /vault/{provider}/rollback/{version}`, `GET /vault/{provider}/versions` | admin |
| Node prompts | `GET /prompts`, `GET /prompts/{node}/versions`, `GET /prompts/{node}/diff?a=&b=` | admin, operator |
| | `POST /prompts/{node}/versions`, `/activate/{version}`, `/reset`, `/test` | admin |
| Resumes | `GET /resumes` (all roles), `POST /resumes` (admin, operator) | |

On startup the app checks `VAULT_MASTER_KEY` / `SESSION_SECRET`, makes `INITIAL_ADMIN_EMAIL` an
admin if there is no active admin, seeds version 1 of every node prompt, and imports provider keys
from `.env` into the vault (once per provider).

## Setup

```bash
python -m venv .venv
.venv/Scripts/activate      # or source .venv/bin/activate on macOS/Linux
pip install -r requirements.txt
cp .env.example .env        # fill in; generation commands are in the file
alembic upgrade head
uvicorn app.main:app --port 8000
```

Sign in at http://localhost:8000/auth/login. The Google OAuth client needs the authorized redirect
URI `http://localhost:8000/auth/callback`, and while the consent screen is in Testing, every login
email must also be a test user.

## Tests

```bash
pytest -q
```

Tests use the Postgres in `DATABASE_URL`, but each runs inside a transaction that is rolled back,
so nothing persists. Google, Groq and provider key checks are faked — no real API calls or credits.
