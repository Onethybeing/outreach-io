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

**Phase 3** — discovery graph (`app/discovery/`): resume → profile → search queries → startups →
decision-makers → dedupe against contacts.

| Route | What | Who |
|---|---|---|
| `POST /runs` `{resume_id, num_startups 1-20, num_kdms_per_company 1-10}` | Start a run in the background (202) | admin, operator |
| `GET /runs`, `GET /runs/{id}` | Run status, usage, Langfuse trace link, startups, candidates | all roles |
| `GET /runs/{id}/events?after=` | Progress feed as JSON | all roles |
| `GET /runs/{id}/stream` | Same feed as server-sent events, ends with `event: end` | all roles |
| `GET /candidates?status=&run_id=` | Proposed people across runs, with the CV each was found for | all roles |

**Phase 4** — contacts (`app/contacts/`): human decisions on candidates, employment verification,
email lookup.

| Route | What | Who |
|---|---|---|
| `POST /candidates/{id}/approve` | Create a contact and start verification | admin, operator |
| `POST /candidates/{id}/reject` · `/reuse` · `/update-contact` | Reject; link to the existing contact; re-point it to this run and re-verify | admin, operator |
| `POST /candidates/bulk` `{action: approve\|reject, candidate_ids}` | Decide many, one result per candidate | admin, operator |
| `GET /contacts?view=active\|sent\|no_email\|all&resume_id=&startup_id=` | Contacts table, with the CV each was found for | all roles |
| `POST /contacts/{id}/verify` | Run verification again (background) | admin, operator |
| `POST /contacts/{id}/email/lookup?force=` | Find the email now (skips done/unverified unless forced) | admin, operator |
| `PUT /contacts/{id}/email` `{email}` | Enter an email manually | admin, operator |
| `POST /contacts/email/lookup-bulk` `{dry_run, contact_ids?}` | Count (dry run) or start lookups for all eligible | admin, operator |
| `GET /settings`, `PUT /settings/email-provider` | Mode and email provider (change: admin) | all / admin |

Verification: Apollo company page (free plan) + BrightData LinkedIn profile + LLM tie-break.
Email lookup: Apollo is wired in, but the free plan blocks it — see PLAN.md §3.

**Phase 5** — drafts and dev-mode sending (`app/contacts/drafts.py`, `sending.py`).

| Route | What | Who |
|---|---|---|
| `POST /contacts/{id}/draft/generate?force=` | Write a draft with the active `generate_draft` prompt | admin, operator |
| `PUT /contacts/{id}/draft` `{subject, body}` | Edit (clears approval, marks edited) | admin, operator |
| `POST /contacts/{id}/draft/approve` | Approve for sending | admin, operator |
| `POST /contacts/drafts/generate-bulk` `{dry_run, contact_ids?}` | Drafts for everyone with an email and no draft | admin, operator |
| `POST /contacts/{id}/send?force=` | Dev mode: write `storage/outbox/*.eml` with the CV attached | admin, operator |
| `POST /contacts/send-approved` `{dry_run, contact_ids?}` | Send every approved, unsent draft | admin, operator |
| `GET /contacts/{id}/emails` | Sent/received emails for a contact | all roles |

No real email is sent: prod mode refuses until real sending is explicitly approved.

**Phase 7** — reply tracking (`app/replies/`).

| Route | What | Who |
|---|---|---|
| `POST /replies/poll` | Check Gmail now for replies, bounces, out-of-office, unsubscribes | admin, operator |
| `GET /replies?classification=` | Inbound messages with contact, startup and reply status | all roles |
| `POST /internal/poll-replies` + `X-Internal-Token` | Same check, for Cloud Scheduler; off unless `INTERNAL_TASK_TOKEN` is set | scheduler |

**Phase 8** — stats and evals (`app/stats.py`, `app/evals.py`).

| Route | What | Who |
|---|---|---|
| `GET /stats?days=30&resume_id=&include_dev=&fresh=` | KPIs, funnel, per-CV and per-prompt-version reply rates (with sample sizes), sends/replies per day, provider usage, agent health, quality | all roles |
| `POST /evals/runs/{id}/startups` | Judge a completed run's startups (background) | admin, operator |
| `POST /evals/contacts/{id}/draft` | Judge a draft now | admin, operator |

Resume-parsing accuracy: `python scripts/eval_parse_resume.py [cases_dir] [--langfuse]` with labelled
cases in `evals/parse_resume/cases/*.json` (format in the script's docstring).

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
