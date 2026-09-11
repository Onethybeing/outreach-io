# Outreach.io — Resume-to-Outreach Agent

## 1. What this system does

Input: a resume (PDF/DOCX/image scan).
Output: a human-reviewed, human-approved set of personalized cold emails sent to relevant
decision-makers (founders / HR / hiring managers) at startups that match the candidate's domain —
with full traceability, deduping against a Postgres contact DB, and a dashboard to drive every
step.

Two tunable parameters per run:
- `num_startups` (e.g. 5) — startups discovered via Tavily
- `num_kdms_per_company` (e.g. 5) — key decision-makers found per startup

Everything downstream of "candidate KDMs found" is **gated by human approval in the dashboard**,
per contact, and can happen at different times (you approve contact A today, contact B next week
— each approval fires only that contact's next node).

---

## 2. High-level architecture

```
┌──────────────┐      ┌───────────────────┐      ┌───────────────────────────┐
│  Dashboard    │◄────►│  FastAPI backend   │◄────►│  Postgres (Neon, primary) │
│ (Next.js UI)  │  SSE │  + LangGraph agent │      │  + GCP Cloud SQL (backup) │
│ login + RBAC  │      │  + vault + prompts │      └───────────────────────────┘
└──────────────┘      └─────────┬──────────┘
                                 │ traces / evals        LLM: Groq
                                 ▼                       Search: Tavily
                             Langfuse                    Enrich: Apollo / BrightData
                                                         Mail: Gmail API
```

Two graphs, both traced in Langfuse:

**A. Discovery pipeline (`discovery_graph`)** — runs automatically end-to-end once you click
"Run agent" on a resume. Fully autonomous, no human gate, because it only *proposes* candidates —
nothing external happens yet.

```
ingest_resume → parse_resume → build_search_queries → discover_startups
   → find_kdms → dedupe_against_db → present_candidates (dashboard)
```

**B. Per-contact action graph (`contact_graph`)** — NOT run automatically. Each node is invoked
independently by a dashboard button click on a specific contact (or a "run on all" bulk button).
LangGraph's checkpointer persists state keyed by `contact_id`, so a node fired today and the next
node fired next week resume from the correct state — this is what makes "approve contact now, run
Apollo later, draft next week" work.

```
verify_employment (Apollo/BrightData) → cross_check_company_match
   → [human: approve/reject] → generate_draft → [human: approve draft]
   → send_email → track_replies → classify_reply
```

Every node reads its API keys from the **vault** (§7) and its system prompt from the **prompt
store** (§8) at call time — never from hard-coded values — so rotating a key or editing a prompt
takes effect on the next node call without a redeploy.

### Node-by-node (discovery pipeline)

| Node | Does |
|---|---|
| `ingest_resume` | Extract text via `pdfplumber`/`python-docx` (text-layer PDFs). If a PDF yields under 300 characters → OCR on rendered page images with `qwen/qwen3.8-27b` on Groq (checked: transcribes cleanly). Text is cached on the resume. |
| `parse_resume` | LLM extracts structured profile: role, seniority, skills, domain(s), years of experience, target titles. |
| `build_search_queries` | Turns profile into Tavily query set. |
| `discover_startups` | Tavily web search, top `num_startups`, LLM-filtered for domain relevance, deduped by domain/website. |
| `find_kdms` | Per startup, search for top `num_kdms_per_company` KDMs (founder/CXO/Head of HR/hiring manager for that role) → name, title, LinkedIn URL. |
| `dedupe_against_db` | Look up each LinkedIn URL in Postgres `contacts`. Existing → flag as duplicate with "reuse" or "update" choice instead of creating a new row. |

### Node-by-node (per-contact action graph)

| Node | Trigger | Does |
|---|---|---|
| `enrich_company` | automatic, first step of verification | Apollo organization enrich (free plan) by website domain, or by company name when there's no website (trusted only if the returned name matches). Caches the startup's LinkedIn company page and fills a missing website. |
| `scrape_profile` | Approve / Update / "Verify again" | BrightData LinkedIn profile scrape (async, ~50–60s): the person's current company name + LinkedIn company page. |
| `cross_check_company_match` | automatic, after the scrape | verified if the company page matches, else if the names match; mismatch if both pages are known and differ; otherwise the LLM tie-break prompt (≥0.7 confidence) decides. Mismatch/unconfirmed contacts are excluded from bulk email lookup. |
| `find_email` | "Find email" (single, synchronous) or "Find emails for all" (background, with a free dry-run count first) | Swappable provider (`settings.email_provider`). Apollo is wired in, but its free plan blocks `people/match` — the app says so clearly, marks the lookup failed (retryable) and a bulk job stops instead of trying everyone. Manual email entry always works. |
| `generate_draft` | "Generate draft" (single or bulk) — only for contacts with an email | LLM drafts a personalized email using resume + startup + contact context. |
| `send_email` | "Send" (single or "send all approved") | Sends via Gmail API, attaches the resume used for that run. Respects dev/prod toggle (§6). |
| `track_replies` | Cloud Scheduler cron, e.g. every 10 min | Polls the Gmail thread for each sent message. |
| `classify_reply` | automatic, on each new inbound message | Labels it: real reply / bounce / out-of-office / unsubscribe request. Only real replies count toward reply rate. |

---

## 3. APIs / services

| Service | Used for | Status |
|---|---|---|
| **Groq API** | All LLM calls: parsing, relevance filters, drafts, reply classification, eval judges | ✅ received |
| **Tavily** | Web search for startups + KDMs | ✅ received |
| **BrightData** | LinkedIn profile scrape = employment verification | ✅ works — live-checked: "LinkedIn people profiles" scraper returns the current company and its LinkedIn page in ~50–60s per profile (billed per profile) |
| **Neon Postgres** | Primary database | ✅ received — migration applied and verified |
| **Apollo.io** | Company enrichment; email lookup | ⚠️ free plan — live-checked: `organizations/enrich` and `contacts/search` work; `people/match`, `people/bulk_match` and `mixed_people/api_search` return 403 "not included in your Free plan". **Default email provider is "Apollo saved contacts"**: reveal an email on apollo.io (free credits), save the person, and the app's lookup pulls it in. The automatic `apollo` provider works on a paid plan. |
| **Gmail OAuth** (sender `sourav.jhinjha@gmail.com`) | Send mail, read replies | ✅ signed in, refresh token verified — consent screen is in Testing, so the token expires every 7 days until the app is published (Phase 9) |
| **Langfuse** (US cloud, project `outreach.io`) | Tracing + evals | ✅ received — key valid |
| **GCP** — account `sourav.jhinjha@gmail.com` (free credits, billing set up) | Cloud Run, Secret Manager, Cloud Scheduler, Artifact Registry, Cloud SQL backup | ❌ needs a one-time `gcloud auth login` by you at deploy time |

Anthropic is no longer required — Groq covers LLM calls. The code keeps the LLM behind one small
client wrapper so switching provider per node later is a config change, not a rewrite.

---

## 4. Data model (Postgres)

### Built in Phase 1 (live on Neon)

```
resumes(id, filename, storage_path, uploaded_at, parsed_profile jsonb, status)

runs(id, resume_id, num_startups, num_kdms_per_company, status, langfuse_trace_id, created_at)

startups(id, run_id, name, domain, website, description, source_url, created_at)

contacts(
  id, startup_id, run_id, resume_id, cv_used_id,
  name, title, linkedin_url UNIQUE, company_at_scrape,
  is_duplicate_of_contact_id,
  apollo_status enum(not_run, running, found, not_found),
  email, email_source enum(apollo, brightdata, manual),
  employment_verified, verification_note,
  draft_status enum(none, generated, approved), draft_text,
  send_status enum(none, queued, sent, sent_dev, failed),
  sent_at, gmail_message_id, gmail_thread_id,
  reply_status enum(waiting, replied, bounced), replied_at,
  mail_service, created_at, updated_at
)

email_events(id, contact_id, direction enum(out,in), gmail_message_id, thread_id, snippet, received_at)

app_settings(key, value)   -- dev/prod toggle, default mail service, per-run cost caps
```

### Added in Phase 2 (vault, prompts, RBAC, audit)

```
users(id, email UNIQUE, name, role enum(admin, operator, viewer), is_active,
      created_at, last_login_at)

api_credentials(id, provider, encrypted_value, last4, version,
                status enum(active, retired), created_by -> users.id,
                created_at, retired_at, last_tested_at, last_test_ok)
  -- partial unique index: at most one active row per provider

prompts(id, node_name, version, template, required_variables jsonb, model, temperature,
        is_active, note, created_by -> users.id, created_at)
  -- UNIQUE(node_name, version); partial unique index: one active version per node

audit_log(id, user_id -> users.id, action, target_type, target_id, details jsonb, created_at)

contacts  + draft_prompt_version_id -> prompts.id, approved_by, draft_approved_by, sent_by,
            do_not_contact bool default false   -- set on unsubscribe, blocks all sends
runs      + started_by -> users.id, prompt_versions jsonb   -- snapshot of active prompt ids at run start
email_events + classification enum(reply, bounce, out_of_office, unsubscribe)
```

### Added in Phase 4 (contacts, verification, email lookup)

```
contacts  apollo_status → email_lookup_status enum(not_run, running, found, not_found, failed)
          + email_lookup_note, email_looked_up_at; email_source is now a string (apollo | hunter | manual)
          + verification_status enum(not_run, queued, running, verified, mismatch, unconfirmed, failed)
          + verification_source, verified_title, verified_company_url, verified_at
candidates + contact_id   -- the contact it became (approve) or was linked to (reuse / update)
startups   + linkedin_url, company_enriched_at   -- company page cache for verification
```

Contact state lives in these columns rather than a LangGraph checkpointer: every human gate is an
API call that reads and advances the row, so a checkpointer would duplicate the same state.

### Added in Phase 3 (discovery)

```
candidates(id, run_id, startup_id, name, title, linkedin_url (normalized), reason,
           existing_contact_id -> contacts.id, status enum(pending, approved, rejected, reused, updated),
           decided_by, decided_at, created_at)
  -- UNIQUE(run_id, linkedin_url). Proposed people live here; a Contact is created (or reused)
  -- only on approval, so contacts.linkedin_url can stay unique.

run_events(id bigint, run_id, node, kind (started|completed|info|warning|failed), message, data jsonb, created_at)
  -- the "agent thinking" feed; streamed over SSE at GET /runs/{id}/stream

resumes   + extracted_text, extraction_method (pdf_text | docx | vision_ocr)
runs      + usage jsonb, error, started_at, finished_at, heartbeat_at, langfuse_trace_url
startups  + relevance
```

`linkedin_url` unique constraint powers duplicate detection (§5) and prevents double-emailing the
same person across separate resume runs.

---

## 5. Dashboard

**Built (Phase 6)** in `dashboard/` — Next.js 16 (App Router) + shadcn/ui + TanStack Table, deployed
to Cloud Run. The browser only talks to the dashboard: Next.js forwards `/api/*` and `/auth/*` to the
API, so the session cookie stays same-origin and sign-in starts and ends on the dashboard's URL.
Buttons follow the signed-in role, and the API still checks every action. The live run feed needs
`Cache-Control: no-transform` on the SSE response — without it the proxy gzips the stream and holds
every event until the run ends.

**Top-level tabs:** Stats · Library · Run Agent · Candidates · Contacts (Active / Sent / No-Email) ·
Replies · Settings (Vault / Prompts / Users & Roles / Mode / Audit Log)

What each role can see and click is in §9.

- **Stats** — see §10.
- **Library** — upload/store multiple resumes; each has a status and can be selected from a
  dropdown to start a run.
- **Run Agent** — pick resume, set `num_startups` / `num_kdms_per_company`, click Run. Live
  "agent thinking" panel streamed over SSE from the LangGraph run (node name + short status per
  step), with a Langfuse trace link.
- **Candidates** — table of proposed KDMs from `dedupe_against_db`: name, title, company,
  LinkedIn URL, CV used, and:
  - if new → **Approve** button (kicks off `verify_employment` for that contact)
  - if already in Postgres → badge "already exists" + **Reuse existing** / **Update
    (re-verify)** buttons instead of Approve
- **Contacts** table (post-approval), with CV-used column, per-row:
  - **Run Apollo** (single) — disabled if `apollo_status` is already `found` or `not_found`
    (no re-spending on a lookup already done), with a tooltip saying why; a deliberate
    **Re-run** icon overrides this.
  - **Run Apollo on all** (bulk) — skips contacts already `found`/`not_found`, shows a cost
    estimate ("12 contacts × Apollo lookup") before firing.
  - Email column: found email, or **"No email found — enter manually"** input when
    `apollo_status = not_found`. Manual entry sets `email_source = manual` and unblocks drafting.
  - **Generate draft** (single, needs an email) + **Generate drafts for all (with email)** bulk
    button — skips no-email contacts.
  - Draft dropdown/expander per row with the full AI email, editable before approval.
  - **Approve draft** (row) + **Send all approved** (bulk).
  - Sub-tabs: **Active** (in progress), **Sent**, **No Email** (needs manual entry).
- **Replies** — thread view per contact, reply classification, last message snippet.
- **Settings** — Vault (§7), Prompts (§8), Users & Roles (§9), Mode (§6), Audit Log (§9).

---

## 6. Dev vs. Prod mode

- Global toggle in `app_settings`, defaults to **Dev**. Only admins can change it.
- **Dev mode:** `send_email` never calls the Gmail send API — it writes a complete `.eml` (From,
  To, Subject, body, the CV used for that contact attached) to `storage/outbox/`, records an
  outbound email event, and marks `send_status = sent_dev`. Built in Phase 5; real Gmail sending is
  not wired in yet, and prod mode currently refuses to send.
- **Prod mode:** needs the toggle *and* a second confirmation modal before the first real send in
  a session. Every real send is audit-logged.
- No real email is sent until you explicitly say so.

---

## 7. Settings → API Vault (key storage + rotation)

**Goal:** every provider key lives in one place, can be rotated from the UI, and is never
exposed again after it's saved.

- **Only bootstrap secrets stay in env / GCP Secret Manager:** `DATABASE_URL`,
  `VAULT_MASTER_KEY`, auth/session secret, the Google sign-in OAuth client ID/secret used for
  dashboard login (§9), `INITIAL_ADMIN_EMAIL`. Login must not depend on the vault, or a bad vault
  entry would lock the admin out of the page needed to fix it. Everything else (Groq, Tavily,
  BrightData, Apollo, Gmail sending OAuth, Langfuse) lives in the `api_credentials` table.
- **Encryption:** each key is encrypted with the master key (Fernet / AES from the `cryptography`
  package) before it's written. The database only ever holds ciphertext + the last 4 characters.
- **Write-only:** after saving, the API never returns the full key — the UI shows
  `provider · ••••last4 · active · last tested ✓ · rotated 3d ago by admin@…`.
- **Rotate flow:**
  1. Admin pastes the new key.
  2. Backend makes one cheap test call to that provider with it.
  3. Test passes → new version becomes `active`, old version becomes `retired`, in-memory key
     cache is cleared. Test fails → nothing changes, error shown.
  4. Retired versions are kept (encrypted) for 7 days for rollback, then purged.
- **Test button** per provider to re-check a key anytime (shows last result + time).
- **Missing key** → nodes needing that provider are disabled in the UI with "Apollo not
  configured" instead of failing mid-run.
- **Seeding:** on first start, keys present in `.env` are imported into the vault once; after
  that the provider keys can be deleted from `.env`.
- **Audit:** every create / rotate / test / rollback is written to `audit_log`.
- **Admin only.**

---

## 8. Settings → Node Prompts (editable system prompts)

**Goal:** tune prompts like draft generation from the dashboard without a code change, and never
lose a working version.

- **Nodes with editable prompts:** `parse_resume`, `build_search_queries`, `discover_startups`
  (relevance filter), `find_kdms` (title relevance), `cross_check_company_match` (tie-break),
  `generate_draft`, `classify_reply`, plus the eval judge prompts (§11).
- **One editor per node:** template text, model, temperature, and a list of the variables the
  node supplies (e.g. `{candidate_summary}`, `{contact_name}`, `{startup_description}`) shown as
  insertable chips.
- **Versioned, never overwritten:** saving creates a new version. You can view any old version,
  diff two versions, and **activate** any version (that's rollback).
- **Validation before save:** all required variables must be present and the template must
  render against sample data — a broken edit can't reach a live node.
- **Test on sample:** runs that node with the draft prompt on a sample contact/resume and shows
  the output, without writing anything to contacts or sending anything.
- **Traceability:** each node call records the prompt version id in Langfuse metadata, and
  `contacts.draft_prompt_version_id` stores which prompt wrote each draft — so Stats can compare
  reply rate across prompt versions.
- **Defaults** ship in code as version 1 of each prompt; "Reset to default" re-activates it.
- **Admin edits and activates; operators can view.**

---

## 9. Auth + RBAC

- **Login:** Google sign-in (OAuth/OIDC). Only emails present in `users` and marked active can
  log in — no open sign-up. Session in an httpOnly secure cookie.
- **First admin:** created from `INITIAL_ADMIN_EMAIL` on startup, only when there is no active
  admin (so it recovers a lockout but never overrides a deliberate demotion).
- **While the Google consent screen is in Testing,** every login email must also be added as a
  test user there, in addition to being in `users`.
- **Enforcement is server-side:** every FastAPI route declares a permission
  (e.g. `Depends(require("contacts.send"))`). The UI hides buttons the user can't use, but the
  backend check is the real gate.

| Action | Admin | Operator | Viewer |
|---|:-:|:-:|:-:|
| View Stats, Library, Candidates, Contacts, Replies | ✓ | ✓ | ✓ |
| Upload resume | ✓ | ✓ | |
| Delete resume | ✓ | | |
| Run agent | ✓ | ✓ | |
| Approve / reuse / update candidates, run Apollo, enter manual email | ✓ | ✓ | |
| Generate, edit, approve drafts | ✓ | ✓ | |
| Send email (dev mode) | ✓ | ✓ | |
| Send email (prod mode) | ✓ | ✓ | |
| Toggle dev / prod | ✓ | | |
| Vault: view, test, rotate keys | ✓ | | |
| Prompts: view | ✓ | ✓ | |
| Prompts: edit, activate, rollback | ✓ | | |
| Manage users & roles | ✓ | | |
| View audit log | ✓ | | |

- **Audit log** records: login, run started, candidate approved, Apollo run, manual email set,
  draft approved, email sent (dev/prod), mode toggled, key created/rotated/tested, prompt
  saved/activated, user role changed. Filterable in Settings → Audit Log.
- **Guardrails:** the last active admin can't be demoted or deactivated; role changes take
  effect on the user's next request (no stale session permissions).

---

## 10. Stats tab

Numbers computed from Postgres (SQL queries, cached ~1 min), filterable by **date range, resume,
run, and prompt version**. Dev-mode sends are excluded by default (toggle to include).

- **KPI tiles:** resumes, runs, contacts found, emails sent, reply rate, bounce rate.
- **Outreach funnel:** candidates found → approved → employment verified → email found → draft
  generated → draft approved → sent → replied. Shows where contacts drop off.
- **Sends & replies over time:** per day.
- **Per-resume table:** contacts, sent, replies, reply rate — which CV performs better.
- **Per prompt version (draft node):** drafts, sent, replies, reply rate, with sample size shown
  next to every rate — no "winner" label on small samples.
- **Provider health:** Apollo email hit rate, BrightData verification success rate, Tavily/Groq
  call counts, usage vs. free-tier limits per month.
- **Agent health:** run success/failure rate, average run duration, errors per node (links to the
  Langfuse traces).

---

## 11. Tracing & evaluation

- **Langfuse Cloud (free hobby tier)** wraps both graphs — every node, LLM call and
  Apollo/Tavily/BrightData call is a traced span linked to `runs.langfuse_trace_id`, with the
  prompt version id in metadata.
- **Evaluation plan:**
  1. **Resume parsing accuracy** — small golden set (5–10 resumes you hand-label), scored
     field-by-field.
  2. **Startup relevance** — LLM-as-judge score (0–1) per discovered startup, logged as a
     Langfuse score.
  3. **KDM relevance** — rule-based title check + your approve/reject clicks as free human
     labels; approval rate tracked over time.
  4. **Employment-verification accuracy** — how often your manual "update" disagrees with what
     Apollo/BrightData reported.
  5. **Draft quality** — LLM-as-judge rubric (personalization, tone, length, no facts not in the
     resume) + the real outcome: reply rate per prompt version (§10).
- All via the Langfuse Scores API — no extra service.
- **Built (Phase 8):**
  - Judges are editable prompts `eval_startup_relevance` (light model) and `eval_draft_quality`
    (heavy model). They run automatically in a 1-worker background pool after each completed run
    and after each generated or edited draft (`AUTO_EVALS=false` turns that off); manual triggers:
    `POST /evals/runs/{id}/startups`, `POST /evals/contacts/{id}/draft`.
  - Results live in `startups.relevance_judge(_reason)` and `contacts.draft_eval`, and are sent as
    Langfuse scores on the run trace / the draft's generation trace (`contacts.draft_trace_id`).
    A draft eval is discarded if the draft changed while it was being judged.
  - Human signals as Langfuse scores: `kdm_approved` (approve/reuse/update = 1, reject = 0),
    `draft_edited_before_approval`, `reply_outcome`. A scoring failure never affects the action.
  - Resume-parsing golden set: put labelled cases in `evals/parse_resume/cases/*.json` and run
    `scripts/eval_parse_resume.py [--langfuse]` (field-level scores: word overlap for text, F1 for
    lists, ±1 year for experience).
  - Live check: the startup judge scored a real run's 2 startups at 1.0 each — likely lenient;
    calibrate the prompt once there are human approve/reject labels to compare against.

---

## 12. Postgres hosting

- **Primary (free): Neon** — project `super-mud-19166881`, branch `production`. Schema from
  Phase 1 is applied and verified (`alembic check` clean).
- **Backup / production-grade: GCP Cloud SQL for Postgres** on the `sourav.jhinjha@gmail.com` GCP
  account's credits, once the Neon free tier isn't enough (HA, storage, same network as Cloud
  Run). Plus a scheduled `pg_dump` to a GCS bucket as a cheap independent backup.

---

## 13. Deployment (GCP account `sourav.jhinjha@gmail.com`)

**Built (Phase 9):** project `outreach-io-sj26` (billing linked), Cloud Run service `outreach-api`
in `us-east5` (closest to the Neon database in AWS us-east-2), image built by Cloud Build into
Artifact Registry `us-east5-docker.pkg.dev/outreach-io-sj26/outreach`, private bucket
`gs://outreach-io-sj26-files` for CVs and dev `.eml` files, 7 Secret Manager secrets readable only
by the `outreach-api` service account, Cloud Scheduler job `poll-replies` in `us-east1` (Cloud
Scheduler doesn't offer us-east5). One instance with CPU always on and min-instances=1, because
background jobs run in-process. Deploy with `bash scripts/deploy.sh`; migrations are run separately.

- FastAPI + LangGraph runtime → **Cloud Run**. Discovery runs execute in the background after the
  request returns, so the service needs **CPU always allocated** (otherwise Cloud Run throttles it
  and runs stall), or runs move to Cloud Tasks / Cloud Run Jobs.
- Dashboard → **Cloud Run** (service `outreach-dashboard`, same region; `bash scripts/deploy_dashboard.sh`,
  which also points the API's `PUBLIC_BASE_URL` at the dashboard so Google sign-in lands there)
- Bootstrap secrets (`DATABASE_URL`, `VAULT_MASTER_KEY`, session secret, login OAuth client) →
  **Secret Manager**
  (provider API keys live in the vault, §7)
- Reply polling → **Cloud Scheduler** hitting an internal endpoint
- Nightly `pg_dump` → **GCS** bucket
- Images → **Artifact Registry**

---

## 14. Open items to decide together

- **Dashboard template** — leaning Next.js + shadcn/ui + TanStack Table (fits the custom per-row
  actions) over a full admin framework like Refine.dev.
- **Gmail integration** — recommend direct Gmail API (OAuth) from FastAPI over a Gmail MCP
  server, since reply tracking is a scheduled job, not an LLM-driven action.
- **Email provider** — Apollo's free plan can't return emails. Options: upgrade Apollo, add a
  Hunter.io key (free tier ~25 lookups/month), or manual entry only for now.
- **Neon CLI** — not needed by the app (it connects with the connection string). The global
  install was blocked by a local safety check; run it yourself if you want the Neon MCP/skills.

---

## 15. Edge cases covered

1. Same contact found across runs → unique `linkedin_url`, shown as duplicate with reuse/update.
2. Contact changed company → "Update" re-verifies and resets email/verification state.
3. No email found → "No Email" sub-tab, excluded from bulk draft/send, manual entry available.
4. Manual email entered → `email_source = manual`, Apollo skipped unless explicitly re-run.
5. KDM left the company → filtered out before becoming actionable, logged for audit.
6. Metered APIs → bulk buttons show a cost estimate and skip already-processed contacts.
7. Draft blocked until employment is verified or the email was entered manually (manual entry is
   the user vouching for the contact); send blocked until draft approved; re-send blocked unless
   forced.
8. Dev mode never touches the Gmail send API; prod needs admin toggle + explicit confirm.
9. Which CV was used is tracked per contact (`cv_used_id`) and shown in the table.
10. Idempotent writes (upserts on unique constraints) so retries never duplicate rows or emails.
11. Replies classified — bounces, out-of-office and unsubscribe requests don't count as replies;
    an unsubscribe request sets `contacts.do_not_contact = true`, which `send_email` checks and
    which "Update (re-verify)" never resets.
12. Resumes and emails are PII — bootstrap secrets in Secret Manager, provider keys encrypted in
    the vault, nothing secret in the repo.
13. Text-layer PDFs parsed cheaply first; vision OCR only when text extraction fails.
14. Key rotated mid-run → in-flight calls finish, next call picks up the new key; if the provider
    already revoked the old key, the call is retried once after a cache refresh.
15. Bad prompt edit → validation + test-on-sample before save, one-click rollback.
16. Last admin can't be removed; permission changes apply on the next request.
17. Keys that were shared in plain text (chat, `.env`) → import into vault, then rotate at the
    provider using the vault's rotate flow.
18. Missing provider key → dependent buttons disabled with a clear reason, not a mid-run crash.
19. Rates on small samples always shown with their sample size.
20. Gmail refresh token expired or revoked (7-day limit while the Google app is in Testing) →
    `send_email` and `track_replies` stop with a "Gmail sign-in expired" banner instead of
    failing silently; sends stay queued until it's renewed.
21. Model returns a LinkedIn link that search never returned → dropped (can't be trusted).
22. Search matches a lookalike company ("Founder at Sapling Says" for Sapling.ai) → dropped by a
    title check in code, since the prompt rule alone proved unreliable in a live test. Subtler cases
    are left to Apollo verification in Phase 4.
23. Same resume run again → startups found in earlier runs are excluded, so a re-run finds new ones.
24. Server restarts mid-run → runs with no progress heartbeat for 10 minutes are marked failed
    (at startup and before starting a new run), instead of showing "running" forever.
25. Groq's 8k tokens/minute limit → calls wait for the reset and show "waiting Ns for the Groq rate
    limit" in the progress feed; a run on this tier takes a minute or two.
26. Two runs on the same resume at once → the second is refused (409) to avoid double spend.
27. Same person approved from two runs at once → unique LinkedIn URL + row lock; the second gets
    409 "choose Reuse or Update".
28. Person became a contact after discovery ran → re-checked at decision time, not just at discovery.
29. Startup without a website → company page looked up by name, trusted only if the name matches.
30. Email provider can't answer (plan limit, bad key) → clear message, lookup marked failed
    (retryable), bulk job stops instead of hitting every contact.
31. Server restarts during verification or lookup → marked failed with "run it again".
32. "Update" on a contact that was already emailed → email/draft reset for the new company, but
    send history and do-not-contact are kept, so a re-send still needs an explicit force.
33. Draft regenerated or edited after approval → approval is cleared; it must be approved again.
    Human edits set `draft_edited` (tracked for evals).
34. The model merges unrelated resume facts into one invented claim → prompt rule against it (seen
    in a live draft); remaining risk is measured by the Phase 8 no-hallucination eval, and every
    draft still needs human approval.
35. CV file missing at send time → refused before anything is marked queued; a crash while writing
    marks the send failed, never stuck in queued; interrupted sends are failed at startup.
36. Reply tracking only watches real sends (dev sends never reach a mailbox), for 60 days after
    sending. It looks at messages from the contact's address, messages in the sent thread, and
    bounce notices naming the address; each Gmail message is stored once (idempotent polls).
37. Bounces and out-of-office replies are labeled by header/subject rules without an LLM call; an
    unrecognised LLM label counts as a human reply (surfaced rather than dropped).
38. Gmail sign-in expired during a poll → the poll stops and reports it; overlapping polls → 409.
39. Known limit: classify_reply has no "other" label, so an automated non-bounce message from the
    contact's own address (e.g. a newsletter) is counted as a reply.

---

## 16. Build order (phased, no time estimates)

1. ✅ Repo scaffold + Postgres schema + FastAPI skeleton + Neon connection.
2. ✅ Auth + RBAC + audit log + API vault + prompt store (backend). Done before the agent so
   every node reads keys and prompts from the vault/store from day one.
3. ✅ `discovery_graph` (resume ingest → profile → startups → KDMs → dedupe), tested via API + Langfuse.
4. ✅ Candidate approve / reject / reuse / update → contacts; verification (Apollo company page +
   BrightData profile + LLM tie-break); provider-agnostic email lookup; manual email.
5. ✅ Draft generation (single + bulk), edit, approve; dev-mode send (`.eml` with CV attached, single + bulk).
6. ✅ Dashboard (`dashboard/`, Next.js 16 + shadcn/ui): Stats, Library, Run Agent (live SSE),
   Candidates, Contacts, Replies, Settings (Vault / Prompts / Users / Mode / Audit Log), deployed to
   Cloud Run as `outreach-dashboard`.
7. ✅ Reply tracking + classification (backend: `POST /replies/poll`, `GET /replies`, scheduler endpoint
   `POST /internal/poll-replies` with `INTERNAL_TASK_TOKEN`). Replies tab comes with the dashboard.
8. ✅ Stats (`GET /stats`, cached 60s) + Langfuse eval scoring (backend). Stats tab comes with the dashboard.
9. ✅ API deployed to Cloud Run (dev mode, Python 3.12) with GCS file storage, Secret Manager and
   the reply-check schedule. Still to do once the dashboard exists: a public homepage + privacy
   policy page, filled into the Google consent screen's Branding page, then publish the Google app
   so the Gmail refresh token stops expiring.
10. Only after your explicit approval: switch to prod mode, first real send.
