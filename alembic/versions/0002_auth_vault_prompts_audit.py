"""auth vault prompts audit

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-11

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# create_type=False everywhere: types are created once, explicitly, below.
user_role = postgresql.ENUM("admin", "operator", "viewer", name="userrole", create_type=False)
credential_status = postgresql.ENUM("active", "retired", name="credentialstatus", create_type=False)
reply_classification = postgresql.ENUM(
    "reply", "bounce", "out_of_office", "unsubscribe", name="replyclassification", create_type=False
)
ENUMS = (user_role, credential_status, reply_classification)

FOREIGN_KEYS = (
    # (name, source table, referent table, local column)
    ("fk_contacts_draft_prompt_version_id", "contacts", "prompts", "draft_prompt_version_id"),
    ("fk_contacts_approved_by", "contacts", "users", "approved_by"),
    ("fk_contacts_draft_approved_by", "contacts", "users", "draft_approved_by"),
    ("fk_contacts_sent_by", "contacts", "users", "sent_by"),
    ("fk_runs_started_by", "runs", "users", "started_by"),
)


def upgrade() -> None:
    bind = op.get_bind()
    for enum in ENUMS:
        enum.create(bind, checkfirst=True)

    op.create_table(
        "users",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("email", sa.String(320), nullable=False, unique=True),
        sa.Column("name", sa.String(256), nullable=True),
        sa.Column("role", user_role, nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "api_credentials",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("encrypted_value", sa.Text(), nullable=False),
        sa.Column("last4", sa.String(8), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", credential_status, nullable=False),
        sa.Column("created_by", sa.UUID(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_tested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_test_ok", sa.Boolean(), nullable=True),
        sa.UniqueConstraint("provider", "version", name="uq_api_credentials_provider_version"),
    )
    op.create_index(
        "uq_api_credentials_one_active",
        "api_credentials",
        ["provider"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )

    op.create_table(
        "audit_log",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("user_id", sa.UUID(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("target_type", sa.String(64), nullable=True),
        sa.Column("target_id", sa.String(128), nullable=True),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_audit_log_action", "audit_log", ["action"])
    op.create_index("ix_audit_log_created_at", "audit_log", ["created_at"])

    op.create_table(
        "prompts",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("node_name", sa.String(64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("template", sa.Text(), nullable=False),
        sa.Column("required_variables", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("model", sa.String(128), nullable=False),
        sa.Column("temperature", sa.Float(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_by", sa.UUID(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("node_name", "version", name="uq_prompts_node_version"),
    )
    op.create_index(
        "uq_prompts_one_active",
        "prompts",
        ["node_name"],
        unique=True,
        postgresql_where=sa.text("is_active"),
    )

    op.add_column("contacts", sa.Column("draft_prompt_version_id", sa.UUID(), nullable=True))
    op.add_column("contacts", sa.Column("approved_by", sa.UUID(), nullable=True))
    op.add_column("contacts", sa.Column("draft_approved_by", sa.UUID(), nullable=True))
    op.add_column("contacts", sa.Column("sent_by", sa.UUID(), nullable=True))
    op.add_column(
        "contacts",
        sa.Column("do_not_contact", sa.Boolean(), server_default="false", nullable=False),
    )
    op.add_column("email_events", sa.Column("classification", reply_classification, nullable=True))
    op.add_column("runs", sa.Column("started_by", sa.UUID(), nullable=True))
    op.add_column(
        "runs",
        sa.Column("prompt_versions", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )

    for name, source, referent, column in FOREIGN_KEYS:
        op.create_foreign_key(name, source, referent, [column], ["id"])


def downgrade() -> None:
    for name, source, _, _ in FOREIGN_KEYS:
        op.drop_constraint(name, source, type_="foreignkey")

    op.drop_column("runs", "prompt_versions")
    op.drop_column("runs", "started_by")
    op.drop_column("email_events", "classification")
    op.drop_column("contacts", "do_not_contact")
    op.drop_column("contacts", "sent_by")
    op.drop_column("contacts", "draft_approved_by")
    op.drop_column("contacts", "approved_by")
    op.drop_column("contacts", "draft_prompt_version_id")

    op.drop_index("uq_prompts_one_active", table_name="prompts")
    op.drop_table("prompts")
    op.drop_index("ix_audit_log_created_at", table_name="audit_log")
    op.drop_index("ix_audit_log_action", table_name="audit_log")
    op.drop_table("audit_log")
    op.drop_index("uq_api_credentials_one_active", table_name="api_credentials")
    op.drop_table("api_credentials")
    op.drop_table("users")

    bind = op.get_bind()
    for enum in reversed(ENUMS):
        enum.drop(bind, checkfirst=True)
