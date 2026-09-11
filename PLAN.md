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
┌─────────────┐      ┌──────────────────┐      ┌───────────────────────────┐
│  Dashboard   │◄────►│   FastAPI backend │◄────►│  Postgres (Neon, primary) │
│ (Next.js UI) │  SSE │  + LangGraph      │      │  + GCP Cloud SQL (backup) │
└─────────────┘      │  agent runtime    │      └───────────────────────────┘
                      └─────────┬─────────┘
                                │ traces/evals
                                ▼
                          Langfuse
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
   → send_email → track_replies
```

### Node-by-node (discovery pipeline)

| Node | Does |
|---|---|
| `ingest_resume` | Extract text via `pdfplumber`/`python-docx` (text-layer PDFs). If extracted text is too short/garbled → fallback to Claude vision OCR on rendered page images (scanned resumes). |
| `parse_resume` | LLM extracts structured profile: role, seniority, skills, domain(s), years of experience, target titles. |
| `build_search_queries` | Turns profile into Tavily query set. |
| `discover_startups` | Tavily web search, top `num_startups`, LLM-filtered for domain relevance, deduped by domain/website. |
| `find_kdms` | Per startup, search for top `num_kdms_per_company` KDMs (founder/CXO/Head of HR/hiring manager for that role) → name, title, LinkedIn URL. |
| `dedupe_against_db` | Look up each LinkedIn URL in Postgres `contacts`. Existing → flag as duplicate with "reuse" or "update" choice instead of creating a new row. |

### Node-by-node (per-contact action graph)

| Node | Trigger | Does |
|---|---|---|
| `verify_employment` | "Run Apollo" (single or bulk) button | Calls Apollo people-enrichment API (primary) to confirm current employer + get email. If Apollo has no data, fallback to BrightData LinkedIn scrape (see §3 for which-to-use-when). |
| `cross_check_company_match` | automatic, right after verify | Fuzzy-matches scraped current employer vs. the startup name. Mismatch → contact flagged, excluded from bulk actions, but visible with a warning badge. |
| `generate_draft` | "Generate draft" (single or bulk) button — only runs for contacts with a confirmed email | LLM drafts a personalized email using resume + startup + contact context. |
| `send_email` | "Send" (single or "send all approved") button | Sends via Gmail (MCP or Gmail API), attaches the resume PDF used for that run. Respects dev/prod toggle (see §6). |
| `track_replies` | Cloud Scheduler cron, e.g. every 10 min | Polls Gmail thread for the sent message, updates `reply_status`. |

---

## 3. APIs / services needed (please provide keys for the ones you want to use)

| Service | Used for | Notes |
|---|---|---|
| **Anthropic API key** | Resume parsing, startup/KDM relevance filtering, draft generation, agent reasoning | Required |
| **Tavily API key** | Web search for startups | Required |
| **Apollo.io API key** | Person enrichment (confirm current employer, find email) | Required — primary enrichment source |
| **BrightData API key** | LinkedIn profile scraping fallback when Apollo has no match | Optional — I'll benchmark both once we have keys and recommend which to lean on by default (Apollo is usually more ToS-safe since it's not scraping LinkedIn directly; BrightData is the fallback for coverage) |
| **Google Cloud project + OAuth credentials (or Gmail MCP)** | Sending email, reading replies | Required — need a Gmail account/alias to send from |
| **Langfuse keys** (public + secret) | Tracing + eval | Langfuse Cloud free tier (hobby, 50k events/mo) to start — no self-host needed initially |
| **Postgres connection string** | Primary datastore | I'll set up Neon (free tier) — see §7 |
| **GCP project + billing** | Deployment (Cloud Run, Secret Manager, Cloud Scheduler), Cloud SQL backup | You said you have free GCP credits — we'll use those |

OCR does **not** need a separate API — text-layer PDFs are parsed for free with `pdfplumber`, and
scanned/image resumes fall back to Claude's vision capability (already covered by the Anthropic
key), so no Google Vision/Tesseract dependency.

---

## 4. Data model (Postgres)

```
resumes(id, filename, storage_path, uploaded_at, parsed_profile jsonb, status)

runs(id, resume_id, num_startups, num_kdms_per_company, status, langfuse_trace_id, created_at)

startups(id, run_id, name, domain, website, description, source_url, created_at)

contacts(
  id, startup_id, run_id, resume_id,
  name, title, linkedin_url UNIQUE,
  company_at_scrape,
  apollo_status enum(not_run, running, found, not_found),
  email, email_source enum(apollo, brightdata, manual),
  employment_verified bool, verification_note,
  is_duplicate_of_contact_id,           -- set by dedupe_against_db
  draft_status enum(none, generated, approved),
  draft_text,
  send_status enum(none, queued, sent, failed),
  sent_at, gmail_message_id, gmail_thread_id,
  reply_status enum(waiting, replied, bounced),
  replied_at,
  cv_used_id -> resumes.id,
  mail_service,
  created_at, updated_at
)

email_events(id, contact_id, direction enum(out,in), gmail_message_id, thread_id, snippet, received_at)

app_settings(key, value)   -- dev/prod toggle, default mail service, per-run cost caps
```

`linkedin_url` unique constraint is what powers the duplicate-detection UI (§5) and prevents
double-emailing the same person across separate resume runs.

---

## 5. Dashboard behavior (spec, refined from your outline)

**Tabs:** Library · Run Agent · Candidates · Contacts (Active / Sent / No-Email) · Replies · Settings

- **Library** — upload/store multiple resumes; each has a status and can be selected from a
  dropdown to start a run.
- **Run Agent** — pick resume, set `num_startups` / `num_kdms_per_company`, click Run. Live
  "agent thinking" panel streamed over SSE from the LangGraph run (node name + short status per
  step), backed by Langfuse trace link.
- **Candidates** — table of proposed KDMs from `dedupe_against_db`: name, title, company,
  LinkedIn URL, CV used (column), and:
  - if new → **Approve** button (kicks off `verify_employment` for that contact)
  - if duplicate found in Postgres → badge "already exists" + **Reuse existing** / **Update
    (re-verify)** buttons instead of Approve
- **Contacts** table (post-approval), with columns incl. CV used, per-row:
  - **Run Apollo** (single) — disabled/greyed if `apollo_status` is `found` or `not_found`
    already (avoids re-spending on a lookup we already did) — button text next to it explains why;
    a manual **Re-run** icon overrides this deliberately.
  - **Run Apollo on all** (bulk) — top-of-table button; skips contacts already `found`/`not_found`
    automatically, shows a cost estimate ("12 contacts × Apollo lookup") before firing.
  - Email column: shows found email, or **"No email found — enter manually"** input if
    `apollo_status = not_found`. Manual entry sets `email_source = manual` and unblocks drafting
    without re-running Apollo.
  - **Generate draft** (single, only enabled if email exists) + **Generate drafts for all (with
    email)** bulk button — explicitly skips no-email contacts.
  - Draft dropdown/expander per row showing full AI-generated email, editable before approval.
  - **Approve draft** (row) + **Send all approved** (bulk, top of table).
  - Sub-tabs split contacts into **Active** (in progress), **Sent** (approved+sent), **No Email**
    (needs manual entry) so the main table doesn't get cluttered.
- **Replies** — thread view per contact, `reply_status`, last message snippet, pulled by the
  `track_replies` cron.
- **Settings** — **Dev/Prod toggle** (see §6), default mail account, Apollo/BrightData spend caps.

---

## 6. Dev vs. Prod mode

- Global toggle in `app_settings`, defaults to **Dev**.
- **Dev mode:** `send_email` node never calls the Gmail send API — it writes an `.eml` file to
  disk/logs and marks `send_status = sent (dev)` so the rest of the pipeline (drafts, tracking UI)
  is fully testable without risk.
- **Prod mode:** requires switching the toggle *and* a second explicit confirmation modal in the
  dashboard before the very first real send in a session. All real sends are logged with
  `mail_service` used.
- I will not wire up or trigger a real send until you explicitly say so, per your standing
  instruction.

---

## 7. Postgres hosting

- **Primary (free): [Neon](https://neon.tech)** — serverless Postgres, generous free tier
  (0.5GB storage, autosuspend when idle so it costs nothing at rest), branching for
  dev/staging, easiest to wire into Cloud Run via connection string + Secret Manager.
- **Backup / production-grade: GCP Cloud SQL for Postgres** — since you already have GCP credits
  and prefer GCP over AWS, this becomes the natural upgrade path once the free Neon tier isn't
  enough (e.g. needing HA, larger storage, or staying inside GCP's network for lower latency to
  Cloud Run). Not needed on day one.
- Alternative free option worth knowing about: **Supabase** (500MB free, pauses after a week of
  inactivity) — mentioning it only as a fallback if Neon ever changes terms; I'd default to Neon.

---

## 8. Tracing & evaluation

- **Langfuse Cloud (free hobby tier)** wraps both graphs — every node call, every LLM call, every
  Apollo/Tavily/BrightData call becomes a traced span, linked back to `runs.langfuse_trace_id`.
- **Evaluation plan** (since this is a pipeline with several LLM judgment calls, not one model
  call):
  1. **Resume parsing accuracy** — small golden set (5–10 resumes you hand-label) scored
     field-by-field (exact/fuzzy match on skills, titles, seniority).
  2. **Startup relevance** — LLM-as-judge score (0–1) of "is this startup actually in the
     candidate's domain," run automatically per discovered startup, logged as a Langfuse score.
  3. **KDM relevance** — rule-based check (title matches target role categories) + your
     approve/reject clicks in the dashboard become free human-labeled ground truth — feed the
     approval rate back into Langfuse as a score to catch drift over time.
  4. **Employment-verification accuracy** — track how often your manual "update" override
     disagrees with what Apollo/BrightData reported; surfaces if one provider is going stale.
  5. **Draft quality** — LLM-as-judge rubric (personalization specificity, tone, length,
     no-hallucinated-facts check against the parsed resume) + real-world outcome metric: reply
     rate per contact, aggregated over time.
- All of this is standard Langfuse "Scores" API usage — no extra service needed.

---

## 9. Deployment (GCP, once built)

- FastAPI + LangGraph runtime → **Cloud Run**
- Dashboard → **Cloud Run** (or Vercel if the chosen frontend template prefers it — TBD, see §10)
- Secrets (Anthropic/Tavily/Apollo/BrightData/Gmail/Langfuse/Postgres) → **Secret Manager**
- Reply-polling cron → **Cloud Scheduler** hitting a `/internal/poll-replies` endpoint
- Images → **Artifact Registry**

---

## 10. Open items to decide together (flagged, not blocking repo creation)

- **Dashboard template** — candidates: Next.js + shadcn/ui + TanStack Table (most flexible, custom
  table/dropdown/tab behavior you described fits well) vs. a full admin framework like Refine.dev
  (faster CRUD scaffolding, less flexible for the bespoke per-row action buttons you want). I lean
  toward Next.js + shadcn/ui given how custom the Contacts table interactions are — happy to
  revisit once we're actually building it.
- **Gmail integration mechanism** — Gmail MCP server vs. direct Gmail API with OAuth in FastAPI.
  MCP is simpler to wire into an agent context; direct API gives more control over reply-polling.
  Recommend direct Gmail API (OAuth) called from FastAPI nodes, since reply-tracking is a
  scheduled job, not something the LLM itself needs to drive interactively.
- **BrightData vs Apollo default** — once you provide both keys I'll do a side-by-side test on a
  few real profiles and report which returns better data (freshness/experience accuracy), then set
  that as primary with the other as fallback.

---

## 11. Edge cases covered by this design

1. Same contact discovered across multiple runs → unique `linkedin_url`, surfaced as
   duplicate with reuse/update choice.
2. Contact changed company since last scrape → "Update" re-runs verification, overwrites company +
   resets `employment_verified`/email state.
3. Apollo/BrightData find no email → contact routed to "No Email" sub-tab, excluded from bulk
   draft/send, manual entry path provided.
4. Manual email entry → sets `email_source = manual`, skips Apollo automatically unless
   explicitly re-run.
5. KDM who has left the company (cross-check fails) → filtered out before becoming an
   actionable contact, but logged for audit, not silently dropped.
6. Cost control — Apollo/BrightData/Tavily are metered; bulk buttons show a cost estimate first
   and skip contacts already processed.
7. Draft generation blocked until employment is verified; send blocked until draft is approved;
   re-send blocked unless explicitly forced.
8. Dev mode never touches the real Gmail send API; prod requires toggle + explicit confirm.
9. Per-run CV attachment tracked via `cv_used_id` so the Contacts table always shows which resume
   a given outreach used.
10. Idempotent node writes (upserts on unique constraints) so retries never duplicate startups,
    contacts, or emails.
11. Reply tracking distinguishes real replies from bounce/NDR messages.
12. Resumes/emails are PII — secrets via Secret Manager (never in repo), Postgres provider's
    at-rest encryption relied on by default.
13. Text-layer PDFs parsed cheaply first; vision-OCR fallback only triggers when extracted text is
    too short/garbled, to save cost.

---

## 12. Build order (phased, no time estimates)

1. Repo scaffold + Postgres schema + FastAPI skeleton + Neon connection.
2. `discovery_graph` (resume ingest → parsed profile → startups → KDMs), no dashboard yet — test
   via API calls / Langfuse traces.
3. Postgres dedupe logic + `contact_graph` nodes (Apollo verify, cross-check) in isolation.
4. Draft generation node + dev-mode send node (writes `.eml`, no real send).
5. Minimal dashboard: Library + Run Agent + Candidates + Contacts tables wired to the above.
6. Reply tracking (Gmail read + Cloud Scheduler poll) + Replies tab.
7. Langfuse eval scoring wired into each judgment node.
8. Deploy to Cloud Run (dev mode only) end-to-end.
9. Only after you explicitly approve: flip prod mode, first real send.
