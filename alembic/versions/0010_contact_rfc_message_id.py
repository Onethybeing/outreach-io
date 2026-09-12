"""contacts.rfc_message_id: recognise a send whose response was lost

Revision ID: 0010
Revises: 0009
Create Date: 2026-09-12

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: Union[str, None] = "0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # The Message-ID written into the MIME headers, saved before the send. If the Gmail response is
    # lost, this is what proves the message did go out — so a retry can't email someone twice.
    op.add_column("contacts", sa.Column("rfc_message_id", sa.String(256), nullable=True))


def downgrade() -> None:
    op.drop_column("contacts", "rfc_message_id")
