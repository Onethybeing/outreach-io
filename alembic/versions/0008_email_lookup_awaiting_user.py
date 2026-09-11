"""email lookup status: awaiting_user

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-12

"""
from typing import Sequence, Union

from alembic import op

revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TYPE emaillookupstatus ADD VALUE IF NOT EXISTS 'awaiting_user'")


def downgrade() -> None:
    # Postgres can't drop an enum value: map it to 'failed' (also retryable) and rebuild the type.
    op.execute("ALTER TABLE contacts ALTER COLUMN email_lookup_status DROP DEFAULT")
    op.execute("ALTER TABLE contacts ALTER COLUMN email_lookup_status TYPE text USING email_lookup_status::text")
    op.execute("UPDATE contacts SET email_lookup_status = 'failed' WHERE email_lookup_status = 'awaiting_user'")
    op.execute("DROP TYPE emaillookupstatus")
    op.execute("CREATE TYPE emaillookupstatus AS ENUM ('not_run', 'running', 'found', 'not_found', 'failed')")
    op.execute("ALTER TABLE contacts ALTER COLUMN email_lookup_status TYPE emaillookupstatus USING email_lookup_status::emaillookupstatus")
    op.execute("ALTER TABLE contacts ALTER COLUMN email_lookup_status SET DEFAULT 'not_run'")
