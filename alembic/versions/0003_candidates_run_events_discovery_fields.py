"""candidates run events discovery fields

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-11

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

candidate_status = postgresql.ENUM(
    "pending", "approved", "rejected", "reused", "updated", name="candidatestatus", create_type=False
)


def upgrade() -> None:
    candidate_status.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "run_events",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("run_id", sa.UUID(), sa.ForeignKey("runs.id", name="fk_run_events_run_id"), nullable=False),
        sa.Column("node", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("data", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_run_events_run_id_id", "run_events", ["run_id", "id"])

    op.create_table(
        "candidates",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("run_id", sa.UUID(), sa.ForeignKey("runs.id", name="fk_candidates_run_id"), nullable=False),
        sa.Column("startup_id", sa.UUID(), sa.ForeignKey("startups.id", name="fk_candidates_startup_id"), nullable=False),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("title", sa.String(256), nullable=True),
        sa.Column("linkedin_url", sa.String(1024), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column(
            "existing_contact_id", sa.UUID(),
            sa.ForeignKey("contacts.id", name="fk_candidates_existing_contact_id"), nullable=True,
        ),
        sa.Column("status", candidate_status, nullable=False),
        sa.Column("decided_by", sa.UUID(), sa.ForeignKey("users.id", name="fk_candidates_decided_by"), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("run_id", "linkedin_url", name="uq_candidates_run_linkedin"),
    )
    op.create_index("ix_candidates_run_id", "candidates", ["run_id"])
    op.create_index("ix_candidates_linkedin_url", "candidates", ["linkedin_url"])

    op.add_column("resumes", sa.Column("extracted_text", sa.Text(), nullable=True))
    op.add_column("resumes", sa.Column("extraction_method", sa.String(32), nullable=True))
    op.add_column("runs", sa.Column("langfuse_trace_url", sa.String(512), nullable=True))
    op.add_column("runs", sa.Column("usage", postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column("runs", sa.Column("error", sa.Text(), nullable=True))
    op.add_column("runs", sa.Column("started_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("runs", sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("runs", sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("startups", sa.Column("relevance", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("startups", "relevance")
    for column in ("heartbeat_at", "finished_at", "started_at", "error", "usage", "langfuse_trace_url"):
        op.drop_column("runs", column)
    op.drop_column("resumes", "extraction_method")
    op.drop_column("resumes", "extracted_text")

    op.drop_index("ix_candidates_linkedin_url", table_name="candidates")
    op.drop_index("ix_candidates_run_id", table_name="candidates")
    op.drop_table("candidates")
    op.drop_index("ix_run_events_run_id_id", table_name="run_events")
    op.drop_table("run_events")

    candidate_status.drop(op.get_bind(), checkfirst=True)
