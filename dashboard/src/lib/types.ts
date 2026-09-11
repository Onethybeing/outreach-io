// Mirrors app/schemas.py and the dict-shaped responses (stats, replies). Dates are ISO strings.

export type Rate = { value: number | null; n: number }

export type Role = "admin" | "operator" | "viewer"

export type User = {
  id: string
  email: string
  name: string | null
  role: Role
  is_active: boolean
  created_at: string
  last_login_at: string | null
}

export type Me = User & { permissions: string[] }

export type AppSettings = { app_mode: string; email_provider: string; email_providers: string[] }

export type Resume = { id: string; filename: string; status: string; uploaded_at: string }

export type Run = {
  id: string
  resume_id: string
  num_startups: number
  num_kdms_per_company: number
  status: "pending" | "running" | "completed" | "failed"
  error: string | null
  usage: Record<string, number> | null
  langfuse_trace_url: string | null
  created_at: string
  started_at: string | null
  finished_at: string | null
}

export type RunEvent = {
  id: number
  node: string
  kind: string // started | completed | info | warning | failed
  message: string
  data: Record<string, unknown> | null
  created_at: string
}

export type Startup = {
  id: string
  name: string
  domain: string | null
  website: string | null
  description: string | null
  source_url: string | null
  relevance: number | null
}

export type Candidate = {
  id: string
  run_id: string
  startup_id: string
  startup_name: string
  name: string
  title: string | null
  linkedin_url: string
  reason: string | null
  existing_contact_id: string | null
  contact_id: string | null
  status: "pending" | "approved" | "rejected" | "reused" | "updated"
  cv_filename: string
}

export type RunDetail = Run & { resume_filename: string; startups: Startup[]; candidates: Candidate[] }

export type Contact = {
  id: string
  name: string
  title: string | null
  linkedin_url: string
  startup_id: string
  startup_name: string
  startup_website: string | null
  run_id: string
  cv_used_id: string
  cv_filename: string
  verification_status: string
  verification_note: string | null
  employment_verified: boolean | null
  company_at_scrape: string | null
  verified_title: string | null
  verified_company_url: string | null
  verified_at: string | null
  email: string | null
  email_source: string | null
  email_lookup_status: string
  email_lookup_note: string | null
  draft_status: "none" | "generated" | "approved"
  draft_subject: string | null
  draft_text: string | null
  draft_generated_at: string | null
  draft_approved_at: string | null
  draft_edited: boolean
  send_status: "none" | "queued" | "sent" | "sent_dev" | "failed"
  sent_at: string | null
  mail_service: string | null
  reply_status: string | null
  do_not_contact: boolean
  created_at: string
  updated_at: string
}

export type ContactView = "active" | "sent" | "no_email" | "all"

export type EmailEvent = {
  id: string
  direction: "out" | "in"
  gmail_message_id: string | null
  snippet: string | null
  classification: string | null
  received_at: string
}

export type CandidateDecision = { candidate_id: string; status: string; contact: Contact | null }
export type BulkDecisionResult = { candidate_id: string; ok: boolean; status: string | null; error: string | null }
export type BulkActionOut = { eligible: number; queued: boolean }
export type BulkLookupOut = BulkActionOut & { provider: string }

export type Reply = {
  event_id: string
  contact_id: string
  contact_name: string
  contact_email: string | null
  startup_name: string
  classification: string | null
  snippet: string | null
  received_at: string
  reply_status: string | null
  do_not_contact: boolean
}

export type PollOut = { contacts: number; new_messages: number; by_label: Record<string, number>; errors: string[] }

export type ProviderStatus = {
  provider: string
  configured: boolean
  fields: string[]
  version: number | null
  last4: string | null
  created_at: string | null
  created_by: string | null
  last_tested_at: string | null
  last_test_ok: boolean | null
}

export type CredentialVersion = {
  version: number
  status: string
  last4: string
  created_at: string
  retired_at: string | null
  last_tested_at: string | null
  last_test_ok: boolean | null
}

export type TestResult = { ok: boolean; message: string }

export type PromptVersion = {
  id: string
  node_name: string
  version: number
  template: string
  required_variables: string[]
  model: string
  temperature: number
  is_active: boolean
  note: string | null
  created_by: string | null
  created_at: string
}

export type NodePrompt = {
  node: string
  description: string
  required_variables: string[]
  optional_variables: string[]
  active: PromptVersion | null
}

export type PromptTestOut = { rendered_prompt: string; output: string }

export type AuditEntry = {
  id: string
  user_email: string | null
  action: string
  target_type: string | null
  target_id: string | null
  details: Record<string, unknown> | null
  created_at: string
}

export type Stats = {
  window_days: number
  resume_id: string | null
  include_dev_sends: boolean
  kpis: {
    resumes: number
    runs: number
    candidates_found: number
    contacts: number
    emails_sent: number
    reply_rate: Rate
    bounce_rate: Rate
  }
  funnel: { step: string; count: number }[]
  per_resume: { resume_id: string; filename: string; contacts: number; sent: number; reply_rate: Rate }[]
  per_prompt_version: {
    prompt_id: string
    version: number
    drafts: number
    sent: number
    reply_rate: Rate
    edited_before_approval: Rate
    avg_judge_score: Rate
  }[]
  over_time: { date: string; sent: number; replied: number }[]
  providers: Record<string, number | Rate>
  agent_health: {
    runs: number
    success_rate: Rate
    avg_run_seconds: number | null
    failures_by_node: Record<string, number>
  }
  quality: Record<string, Rate>
}
