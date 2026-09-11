"""contact verification and provider-agnostic email lookup

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-11

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

verification_status = postgresql.ENUM(
    "not_run", "queued", "running", "verified", "mismatch", "unconfirmed", "failed",
    name="verificationstatus", create_type=False,
)


def upgrade() -> None:
    # Email lookup is no longer Apollo-only: rename the column and its enum, add a retryable state.
    op.alter_column("contacts", "apollo_status", new_column_name="email_lookup_status")
    op.execute("ALTER TYPE apollostatus RENAME TO emaillookupstatus")
    op.execute("ALTER TYPE emaillookupstatus ADD VALUE IF NOT EXISTS 'failed'")
    op.add_column("contacts", sa.Column("email_lookup_note", sa.Text(), nullable=True))
    op.add_column("contacts", sa.Column("email_looked_up_at", sa.DateTime(timezone=True), nullable=True))

    # Providers will be added over time; a string avoids an enum migration for each.
    op.execute("ALTER TABLE contacts ALTER COLUMN email_source TYPE varchar(32) USING email_source::text")
    op.execute("DROP TYPE emailsource")

    verification_status.create(op.get_bind(), checkfirst=True)
    op.add_column(
        "contacts",
        sa.Column("verification_status", verification_status, nullable=False, server_default="not_run"),
    )
    op.add_column("contacts", sa.Column("verification_source", sa.String(32), nullable=True))
    op.add_column("contacts", sa.Column("verified_title", sa.String(256), nullable=True))
    op.add_column("contacts", sa.Column("verified_company_url", sa.String(512), nullable=True))
    op.add_column("contacts", sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True))

    op.add_column(
        "candidates",
        sa.Column("contact_id", sa.UUID(), sa.ForeignKey("contacts.id", name="fk_candidates_contact_id"), nullable=True),
    )
    op.add_column("startups", sa.Column("linkedin_url", sa.String(512), nullable=True))
    op.add_column("startups", sa.Column("company_enriched_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("startups", "company_enriched_at")
    op.drop_column("startups", "linkedin_url")
    op.drop_constraint("fk_candidates_contact_id", "candidates", type_="foreignkey")
    op.drop_column("candidates", "contact_id")

    for column in ("verified_at", "verified_company_url", "verified_title", "verification_source", "verification_status"):
        op.drop_column("contacts", column)
    verification_status.drop(op.get_bind(), checkfirst=True)

    op.execute("CREATE TYPE emailsource AS ENUM ('apollo', 'brightdata', 'manual')")
    # Values outside the old enum (e.g. 'hunter') can't be represented; they become NULL.
    op.execute(
        "ALTER TABLE contacts ALTER COLUMN email_source TYPE emailsource USING "
        "(CASE WHEN email_source IN ('apollo', 'brightdata', 'manual') THEN email_source::emailsource END)"
    )

    op.drop_column("contacts", "email_looked_up_at")
    op.drop_column("contacts", "email_lookup_note")
    # Postgres can't drop an enum value; map 'failed' back to 'not_run' and rebuild the old type.
    op.execute("ALTER TABLE contacts ALTER COLUMN email_lookup_status DROP DEFAULT")
    op.execute("ALTER TABLE contacts ALTER COLUMN email_lookup_status TYPE text USING email_lookup_status::text")
    op.execute("UPDATE contacts SET email_lookup_status = 'not_run' WHERE email_lookup_status = 'failed'")
    op.execute("DROP TYPE emaillookupstatus")
    op.execute("CREATE TYPE apollostatus AS ENUM ('not_run', 'running', 'found', 'not_found')")
    op.execute("ALTER TABLE contacts ALTER COLUMN email_lookup_status TYPE apollostatus USING email_lookup_status::apollostatus")
    op.execute("ALTER TABLE contacts ALTER COLUMN email_lookup_status SET DEFAULT 'not_run'")
    op.alter_column("contacts", "email_lookup_status", new_column_name="apollo_status")
