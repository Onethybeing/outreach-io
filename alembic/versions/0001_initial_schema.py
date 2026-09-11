"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-09-11

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


run_status = postgresql.ENUM("pending", "running", "completed", "failed", name="runstatus", create_type=False)
apollo_status = postgresql.ENUM("not_run", "running", "found", "not_found", name="apollostatus", create_type=False)
email_source = postgresql.ENUM("apollo", "brightdata", "manual", name="emailsource", create_type=False)
draft_status = postgresql.ENUM("none", "generated", "approved", name="draftstatus", create_type=False)
send_status = postgresql.ENUM(
    "none", "queued", "sent", "sent_dev", "failed", name="sendstatus", create_type=False
)
reply_status = postgresql.ENUM("waiting", "replied", "bounced", name="replystatus", create_type=False)
email_direction = postgresql.ENUM("out", "in_", name="emaildirection", create_type=False)


def upgrade() -> None:
    bind = op.get_bind()
    for enum in (
        run_status,
        apollo_status,
        email_source,
        draft_status,
        send_status,
        reply_status,
        email_direction,
    ):
        enum.create(bind, checkfirst=True)

    op.create_table(
        "resumes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("filename", sa.String(512), nullable=False),
        sa.Column("storage_path", sa.String(1024), nullable=False),
        sa.Column("parsed_profile", postgresql.JSONB, nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="uploaded"),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("resume_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("resumes.id"), nullable=False),
        sa.Column("num_startups", sa.Integer, nullable=False, server_default="5"),
        sa.Column("num_kdms_per_company", sa.Integer, nullable=False, server_default="5"),
        sa.Column("status", run_status, nullable=False, server_default="pending"),
        sa.Column("langfuse_trace_id", sa.String(128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "startups",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("runs.id"), nullable=False),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("domain", sa.String(128), nullable=True),
        sa.Column("website", sa.String(512), nullable=True),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("source_url", sa.String(1024), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "contacts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("startup_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("startups.id"), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("runs.id"), nullable=False),
        sa.Column("resume_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("resumes.id"), nullable=False),
        sa.Column("cv_used_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("resumes.id"), nullable=False),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("title", sa.String(256), nullable=True),
        sa.Column("linkedin_url", sa.String(1024), nullable=False),
        sa.Column("company_at_scrape", sa.String(256), nullable=True),
        sa.Column("is_duplicate_of_contact_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("contacts.id"), nullable=True),
        sa.Column("apollo_status", apollo_status, nullable=False, server_default="not_run"),
        sa.Column("email", sa.String(320), nullable=True),
        sa.Column("email_source", email_source, nullable=True),
        sa.Column("employment_verified", sa.Boolean, nullable=True),
        sa.Column("verification_note", sa.Text, nullable=True),
        sa.Column("draft_status", draft_status, nullable=False, server_default="none"),
        sa.Column("draft_text", sa.Text, nullable=True),
        sa.Column("send_status", send_status, nullable=False, server_default="none"),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("gmail_message_id", sa.String(256), nullable=True),
        sa.Column("gmail_thread_id", sa.String(256), nullable=True),
        sa.Column("reply_status", reply_status, nullable=True),
        sa.Column("replied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("mail_service", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("linkedin_url", name="uq_contacts_linkedin_url"),
    )

    op.create_table(
        "email_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("contact_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("contacts.id"), nullable=False),
        sa.Column("direction", email_direction, nullable=False),
        sa.Column("gmail_message_id", sa.String(256), nullable=True),
        sa.Column("thread_id", sa.String(256), nullable=True),
        sa.Column("snippet", sa.Text, nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "app_settings",
        sa.Column("key", sa.String(128), primary_key=True),
        sa.Column("value", sa.Text, nullable=False),
    )


def downgrade() -> None:
    op.drop_table("app_settings")
    op.drop_table("email_events")
    op.drop_table("contacts")
    op.drop_table("startups")
    op.drop_table("runs")
    op.drop_table("resumes")

    bind = op.get_bind()
    for enum in (
        email_direction,
        reply_status,
        send_status,
        draft_status,
        email_source,
        apollo_status,
        run_status,
    ):
        enum.drop(bind, checkfirst=True)
