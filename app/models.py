"""
SQLAlchemy models matching the schema in PLAN.md section 4.

Relationships:
  Resume 1--* Run
  Run    1--* Startup
  Startup 1--* Contact
  Contact 1--* EmailEvent
"""

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# --- enums -------------------------------------------------------------

class RunStatus(str, enum.Enum):
    pending = "pending"
    running = "running"
    completed = "completed"
    failed = "failed"


class EmailLookupStatus(str, enum.Enum):
    not_run = "not_run"
    running = "running"
    found = "found"
    not_found = "not_found"  # provider answered: no email for this person
    failed = "failed"  # couldn't ask (error, plan limit) — retryable


class VerificationStatus(str, enum.Enum):
    not_run = "not_run"
    queued = "queued"
    running = "running"
    verified = "verified"  # profile's current company is the startup
    mismatch = "mismatch"  # profile's current company is a different company
    unconfirmed = "unconfirmed"  # profile shows no current company, or the match is too uncertain
    failed = "failed"  # scrape/lookup error — retryable


class DraftStatus(str, enum.Enum):
    none = "none"
    generated = "generated"
    approved = "approved"


class SendStatus(str, enum.Enum):
    none = "none"
    queued = "queued"
    sent = "sent"
    sent_dev = "sent_dev"  # dev-mode "send" — wrote .eml, never hit Gmail API
    failed = "failed"


class ReplyStatus(str, enum.Enum):
    waiting = "waiting"
    replied = "replied"
    bounced = "bounced"


class EmailDirection(str, enum.Enum):
    out = "out"
    in_ = "in"


class ReplyClassification(str, enum.Enum):
    reply = "reply"
    bounce = "bounce"
    out_of_office = "out_of_office"
    unsubscribe = "unsubscribe"


class UserRole(str, enum.Enum):
    admin = "admin"
    operator = "operator"
    viewer = "viewer"


class CredentialStatus(str, enum.Enum):
    active = "active"
    retired = "retired"


class CandidateStatus(str, enum.Enum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"
    reused = "reused"  # matched an existing contact; kept that contact as-is
    updated = "updated"  # matched an existing contact; re-verify it


# --- tables --------------------------------------------------------------

class Resume(Base):
    __tablename__ = "resumes"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    filename: Mapped[str] = mapped_column(String(512))
    storage_path: Mapped[str] = mapped_column(String(1024))
    parsed_profile: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    extracted_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    extraction_method: Mapped[str | None] = mapped_column(String(32), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="uploaded")
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    runs: Mapped[list["Run"]] = relationship(back_populates="resume")


class Run(Base):
    __tablename__ = "runs"
    __table_args__ = (
        Index(
            "uq_runs_one_active_per_resume",
            "resume_id",
            unique=True,
            postgresql_where=text("status IN ('pending', 'running')"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    resume_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("resumes.id"))
    num_startups: Mapped[int] = mapped_column(default=5)
    num_kdms_per_company: Mapped[int] = mapped_column(default=5)
    status: Mapped[RunStatus] = mapped_column(Enum(RunStatus), default=RunStatus.pending)
    langfuse_trace_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    langfuse_trace_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    started_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    # Active prompt id per node at run start, so later edits don't blur what a run used.
    prompt_versions: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # Counts: tavily_searches, llm_calls, prompt_tokens, completion_tokens, rate_limit_wait_seconds.
    usage: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Touched on every progress event; a stale heartbeat on a "running" run means it died.
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    resume: Mapped["Resume"] = relationship(back_populates="runs")
    startups: Mapped[list["Startup"]] = relationship(back_populates="run")


class Startup(Base):
    __tablename__ = "startups"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("runs.id"))
    name: Mapped[str] = mapped_column(String(256))
    domain: Mapped[str | None] = mapped_column(String(128), nullable=True)
    website: Mapped[str | None] = mapped_column(String(512), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    relevance: Mapped[float | None] = mapped_column(Float, nullable=True)
    # LLM-as-judge score (0-1) from eval_startup_relevance, independent of discovery's own relevance.
    relevance_judge: Mapped[float | None] = mapped_column(Float, nullable=True)
    relevance_judge_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # The company's LinkedIn page, used to verify employment; looked up once (enriched_at set even if none found).
    linkedin_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    company_enriched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    run: Mapped["Run"] = relationship(back_populates="startups")
    contacts: Mapped[list["Contact"]] = relationship(back_populates="startup")


class Contact(Base):
    __tablename__ = "contacts"
    __table_args__ = (UniqueConstraint("linkedin_url", name="uq_contacts_linkedin_url"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    startup_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("startups.id"))
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("runs.id"))
    resume_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("resumes.id"))
    cv_used_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("resumes.id"))

    name: Mapped[str] = mapped_column(String(256))
    title: Mapped[str | None] = mapped_column(String(256), nullable=True)
    linkedin_url: Mapped[str] = mapped_column(String(1024))
    company_at_scrape: Mapped[str | None] = mapped_column(String(256), nullable=True)

    is_duplicate_of_contact_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("contacts.id"), nullable=True
    )

    email_lookup_status: Mapped[EmailLookupStatus] = mapped_column(
        Enum(EmailLookupStatus), default=EmailLookupStatus.not_run, server_default="not_run"
    )
    email_lookup_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    email_looked_up_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    email_source: Mapped[str | None] = mapped_column(String(32), nullable=True)  # apollo | hunter | manual

    verification_status: Mapped[VerificationStatus] = mapped_column(
        Enum(VerificationStatus), default=VerificationStatus.not_run, server_default="not_run"
    )
    employment_verified: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    verification_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    verification_source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    verified_title: Mapped[str | None] = mapped_column(String(256), nullable=True)
    verified_company_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    draft_status: Mapped[DraftStatus] = mapped_column(Enum(DraftStatus), default=DraftStatus.none)
    draft_subject: Mapped[str | None] = mapped_column(String(200), nullable=True)
    draft_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    draft_generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    draft_approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # A person changed the AI draft before approving — a quality signal for evals (PLAN.md §11).
    draft_edited: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    draft_trace_id: Mapped[str | None] = mapped_column(String(64), nullable=True)  # Langfuse trace of the generation
    draft_eval: Mapped[dict | None] = mapped_column(JSONB, nullable=True)  # eval_draft_quality result
    draft_prompt_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("prompts.id"), nullable=True
    )

    approved_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    draft_approved_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    sent_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    # Set on unsubscribe; send_email must refuse, and re-verify must never clear it.
    do_not_contact: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")

    send_status: Mapped[SendStatus] = mapped_column(Enum(SendStatus), default=SendStatus.none)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    gmail_message_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    gmail_thread_id: Mapped[str | None] = mapped_column(String(256), nullable=True)

    reply_status: Mapped[ReplyStatus | None] = mapped_column(Enum(ReplyStatus), nullable=True)
    replied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    mail_service: Mapped[str | None] = mapped_column(String(64), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    startup: Mapped["Startup"] = relationship(back_populates="contacts")
    email_events: Mapped[list["EmailEvent"]] = relationship(back_populates="contact")


class EmailEvent(Base):
    __tablename__ = "email_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    contact_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("contacts.id"))
    direction: Mapped[EmailDirection] = mapped_column(Enum(EmailDirection))
    gmail_message_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    thread_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    snippet: Mapped[str | None] = mapped_column(Text, nullable=True)
    classification: Mapped[ReplyClassification | None] = mapped_column(
        Enum(ReplyClassification), nullable=True
    )
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    contact: Mapped["Contact"] = relationship(back_populates="email_events")


class AppSetting(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[str] = mapped_column(Text)


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    email: Mapped[str] = mapped_column(String(320), unique=True)
    name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    role: Mapped[UserRole] = mapped_column(Enum(UserRole), default=UserRole.viewer)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ApiCredential(Base):
    __tablename__ = "api_credentials"
    __table_args__ = (
        UniqueConstraint("provider", "version", name="uq_api_credentials_provider_version"),
        Index(
            "uq_api_credentials_one_active",
            "provider",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    provider: Mapped[str] = mapped_column(String(64))
    encrypted_value: Mapped[str] = mapped_column(Text)
    last4: Mapped[str] = mapped_column(String(8))
    version: Mapped[int]
    status: Mapped[CredentialStatus] = mapped_column(Enum(CredentialStatus))
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_tested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_test_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)


class Prompt(Base):
    __tablename__ = "prompts"
    __table_args__ = (
        UniqueConstraint("node_name", "version", name="uq_prompts_node_version"),
        Index(
            "uq_prompts_one_active",
            "node_name",
            unique=True,
            postgresql_where=text("is_active"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    node_name: Mapped[str] = mapped_column(String(64))
    version: Mapped[int]
    template: Mapped[str] = mapped_column(Text)
    required_variables: Mapped[list] = mapped_column(JSONB)
    model: Mapped[str] = mapped_column(String(128))
    temperature: Mapped[float] = mapped_column(Float)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class Candidate(Base):
    """A person the discovery graph proposes. Becomes (or links to) a Contact only on approval."""

    __tablename__ = "candidates"
    __table_args__ = (UniqueConstraint("run_id", "linkedin_url", name="uq_candidates_run_linkedin"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("runs.id"), index=True)
    startup_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("startups.id"))
    name: Mapped[str] = mapped_column(String(256))
    title: Mapped[str | None] = mapped_column(String(256), nullable=True)
    linkedin_url: Mapped[str] = mapped_column(String(1024), index=True)  # normalized
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    existing_contact_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("contacts.id"), nullable=True
    )
    # The contact this candidate became (approve) or was linked to (reuse / update).
    contact_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("contacts.id"), nullable=True)
    status: Mapped[CandidateStatus] = mapped_column(
        Enum(CandidateStatus), default=CandidateStatus.pending
    )
    decided_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class RunEvent(Base):
    """Progress feed for the "agent thinking" panel. Integer id gives a stable order to stream by."""

    __tablename__ = "run_events"
    __table_args__ = (Index("ix_run_events_run_id_id", "run_id", "id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("runs.id"))
    node: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(16))  # started | completed | info | warning | failed
    message: Mapped[str] = mapped_column(Text)
    data: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    # Null user = system action (e.g. seeding the vault from .env at startup).
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    action: Mapped[str] = mapped_column(String(64), index=True)
    target_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    target_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    details: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, index=True
    )
