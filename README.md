<img src="docs/logo.png" alt="" width="72" align="left" hspace="12" />

# outreach-io

Reads a CV, finds startups that fit it, works out who to contact at each, writes the emails, and
sends them once a person approves. Built on LangGraph, FastAPI and Next.js.

**Live:** [outreach-dashboard-wqb3gj6hna-ul.a.run.app](https://outreach-dashboard-wqb3gj6hna-ul.a.run.app)
· [About](https://outreach-dashboard-wqb3gj6hna-ul.a.run.app/about)
· [Privacy](https://outreach-dashboard-wqb3gj6hna-ul.a.run.app/privacy)

Sign-in is limited to accounts the administrator has added, so the dashboard links above will ask you
to sign in. The About and Privacy pages are public.

## Demo

_Recording to be added._ It will cover: upload a CV, run the agent and watch it work, approve
candidates, find emails, review a draft, and send. In dev mode every email is redirected to the
operator's own inbox, so nothing reaches a real person while testing.

<!-- To add it: drop the file at docs/demo.gif and replace the line above with
     ![Dashboard walkthrough](docs/demo.gif)
     For an mp4, upload it by dragging the file into a GitHub issue or the README web editor, then
     paste the github.com/user-attachments link GitHub gives back. -->

## What it does

1. **Library** stores CVs. Text is extracted, with OCR as a fallback for image-only PDFs.
2. **Run agent** searches for startups matching the CV (N per run), reads up on each one, then finds
   decision-makers at each (M per company) and flags anyone already in your contacts.
3. **Candidates** is where you approve people. Approving checks they still work there.
4. **Contacts** finds an email, writes a draft you edit and approve, then sends with the CV attached.
5. **Replies** matches answers back to the contact and labels them (reply, bounce, out of office,
   unsubscribe). Unsubscribes stop all future contact permanently.
6. **Stats** tracks the funnel and reply rates per CV and per prompt version, each with its sample size.
7. **Settings** holds the encrypted API key vault, editable node prompts, users and roles, the audit
   log, and the dev/production toggle.

See [PLAN.md](./PLAN.md) for the full design.

## Status

**Phase 1**: FastAPI app, Postgres schema (Alembic), resume upload/list.

**Phase 2**: backend for login, roles, API key vault and editable prompts:

| Area | Routes | Who |
|---|---|---|
| Google sign-in | `GET /auth/login`, `GET /auth/callback`, `GET /auth/me`, `POST /auth/logout` | listed users only |
| Users & roles | `GET/POST /users`, `PATCH /users/{id}` | admin |
| Audit log | `GET /audit-log?action=&user_id=&before=&limit=` | admin |
| API vault | `GET /vault`, `PUT /vault/{provider}`, `POST /vault/{provider}/test`, `POST /vault/{provider}/rollback/{version}`, `GET /vault/{provider}/versions` | admin |
| Node prompts | `GET /prompts`, `GET /prompts/{node}/versions`, `GET /prompts/{node}/diff?a=&b=` | admin, operator |
| | `POST /prompts/{node}/versions`, `/activate/{version}`, `/reset`, `/test` | admin |
| Resumes | `GET /resumes` (all roles), `POST /resumes` (admin, operator) | |

**Phase 3**: discovery graph (`app/discovery/`): resume → profile → search queries → startups →
decision-makers → dedupe against contacts.

| Route | What | Who |
|---|---|---|
| `POST /runs` `{resume_id, num_startups 1-20, num_kdms_per_company 1-10}` | Start a run in the background (202) | admin, operator |
| `GET /runs`, `GET /runs/{id}` | Run status, usage, Langfuse trace link, startups, candidates | all roles |
| `GET /runs/{id}/events?after=` | Progress feed as JSON | all roles |
| `GET /runs/{id}/stream` | Same feed as server-sent events, ends with `event: end` | all roles |
| `GET /candidates?status=&run_id=` | Proposed people across runs, with the CV each was found for | all roles |

**Phase 4**: contacts (`app/contacts/`): human decisions on candidates, employment verification,
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
| `PUT /contacts/{id}/do-not-contact` `{do_not_contact}` | Honour (or undo) a request not to be emailed | admin, operator |
| `DELETE /contacts/{id}` | Erase a person's record and email history (deletion request) | admin |
| `GET /settings`, `PUT /settings/email-provider` | Mode and email provider (change: admin) | all / admin |
| `PUT /settings/mode` `{mode: dev\|prod}` | Switch who receives sent email | admin |

Verification: Apollo company page (free plan) + BrightData LinkedIn profile + LLM tie-break.
Email lookup: Apollo is wired in, but the free plan blocks it, see PLAN.md §3.

Marking do-not-contact, an unsubscribe reply, and erasing all record a hash of the LinkedIn URL in
`contact_suppressions`. Later runs drop those people, and approving one is refused, so an erasure
can't quietly undo an opt-out. Erasing also scrubs the candidate rows that named the person and
deletes their `.eml` copies from storage. Undoing do-not-contact lifts only an operator's own block;
an unsubscribe the person sent themselves stands.

**Phase 5**: drafts and dev-mode sending (`app/contacts/drafts.py`, `sending.py`).

| Route | What | Who |
|---|---|---|
| `POST /contacts/{id}/draft/generate?force=` | Write a draft with the active `generate_draft` prompt | admin, operator |
| `PUT /contacts/{id}/draft` `{subject, body}` | Edit (clears approval, marks edited) | admin, operator |
| `POST /contacts/{id}/draft/approve` | Approve for sending | admin, operator |
| `POST /contacts/drafts/generate-bulk` `{dry_run, contact_ids?}` | Drafts for everyone with an email and no draft | admin, operator |
| `POST /contacts/{id}/send?force=&expected_mode=` | Send through Gmail with the CV attached | admin, operator |
| `POST /contacts/send-approved` `{dry_run, contact_ids?, expected_mode?}` | Send every approved, unsent draft | admin, operator |
| `GET /contacts/{id}/emails` | Sent/received emails for a contact | all roles |

Both modes send real email through Gmail; the mode decides only who receives it. **dev** redirects
every message to `DEV_REDIRECT_EMAIL` (or the sending account) with `[DEV → real@address]` in the
subject and a banner in the body, so contacts receive nothing. **prod** sends to the contact. A copy
of what was sent is archived under `outbox/`, and `expected_mode` makes a send fail rather than go
out if an admin changed the mode in the meantime.

**Phase 7**: reply tracking (`app/replies/`).

| Route | What | Who |
|---|---|---|
| `POST /replies/poll` | Check Gmail now for replies, bounces, out-of-office, unsubscribes | admin, operator |
| `GET /replies?classification=` | Inbound messages with contact, startup and reply status | all roles |
| `POST /internal/poll-replies` + `X-Internal-Token` | Same check, for Cloud Scheduler; off unless `INTERNAL_TASK_TOKEN` is set | scheduler |

**Phase 8**: stats and evals (`app/stats.py`, `app/evals.py`).

| Route | What | Who |
|---|---|---|
| `GET /stats?days=30&resume_id=&include_dev=&fresh=` | KPIs, funnel, per-CV and per-prompt-version reply rates (with sample sizes), sends/replies per day, provider usage, agent health, quality | all roles |
| `POST /evals/runs/{id}/startups` | Judge a completed run's startups (background) | admin, operator |
| `POST /evals/contacts/{id}/draft` | Judge a draft now | admin, operator |

Resume-parsing accuracy: `python scripts/eval_parse_resume.py [cases_dir] [--langfuse]` with labelled
cases in `evals/parse_resume/cases/*.json` (format in the script's docstring).

**Phase 9**: deployment (GCP project `outreach-io-sj26`, dev mode).

- API: Cloud Run service `outreach-api` in `us-east5` (closest region to the Neon database).
- Files: CVs and dev `.eml` files in the private bucket `gs://outreach-io-sj26-files` (`STORAGE_BACKEND=gcs`).
- Secrets: `DATABASE_URL`, `VAULT_MASTER_KEY`, `SESSION_SECRET`, `GOOGLE_LOGIN_CLIENT_ID/SECRET`,
  `INITIAL_ADMIN_EMAIL`, `INTERNAL_TASK_TOKEN` in Secret Manager, readable only by the
  `outreach-api` service account. Provider API keys stay in the app's encrypted vault.
- Reply checks: Cloud Scheduler job `poll-replies` (`us-east1`, every 10 minutes) → `POST /internal/poll-replies`.
- Deploy: `alembic upgrade head` then `bash scripts/deploy.sh` (Cloud Build, no local Docker).
  One instance, CPU always on, so in-process background jobs keep running.

**Phase 6**: dashboard (`dashboard/`, Next.js 16 + shadcn/ui).

Tabs: Stats, Library (upload CVs), Run agent (live progress over SSE), Candidates (approve, reject,
reuse, update, in bulk), Contacts (Active / Sent / No email; verify, find or enter email, draft,
approve, send, in bulk), Replies, Settings (mode and email provider, API vault, prompts, users, audit log).
Buttons follow the signed-in role; the API still checks every action.

The browser only talks to the dashboard. Next.js forwards `/api/*` and `/auth/*` to the API, so the
session cookie is same-origin and sign-in starts and ends on the dashboard.

- Deploy: `bash scripts/deploy_dashboard.sh` after the API. It builds with Cloud Build, deploys Cloud Run
  service `outreach-dashboard`, and sets the API's `PUBLIC_BASE_URL` to the dashboard URL.
- The Google sign-in client needs `<dashboard URL>/auth/callback` as an authorized redirect URI.
- `/about` and `/privacy` are public (no sign-in); they are the homepage and privacy-policy URLs for
  the Google consent screen. Everything else lives behind the sign-in gate in `src/app/(app)/`.

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

Dashboard (in another terminal):

```bash
cd dashboard
npm install
npm run dev                 # http://localhost:3000, proxies to the API at API_ORIGIN (default http://localhost:8000)
```

For sign-in through the dashboard, set `PUBLIC_BASE_URL=http://localhost:3000` and `POST_LOGIN_REDIRECT=/`
in `.env`. The Google OAuth client needs the matching redirect URI (`http://localhost:3000/auth/callback`),
and while the consent screen is in Testing, every login email must also be a test user.

## Tests

```bash
pytest -q
```

Tests use the Postgres in `DATABASE_URL`, but each runs inside a transaction that is rolled back,
so nothing persists. Google, Groq and provider key checks are faked, no real API calls or credits.
