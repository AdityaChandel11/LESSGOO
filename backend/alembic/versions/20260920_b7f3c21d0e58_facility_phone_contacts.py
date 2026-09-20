"""facility phone contacts

The phone registry the ingestion spine identifies senders with (spec 13, step
2). A raw number is never stored: only a salted hash to match on, and a masked
form to show. Dropping the salt therefore orphans every handset, which is why
PHONE_HASH_SALT is its own required secret rather than derived from another.

Revision ID: b7f3c21d0e58
Revises: a1c4e77b90d2
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b7f3c21d0e58"
down_revision: Union[str, None] = "a1c4e77b90d2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "facility_contacts",
        sa.Column("phone_hash", sa.Text(), nullable=False),
        sa.Column("facility_id", sa.Text(), nullable=False),
        sa.Column("masked", sa.Text(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("language", sa.Text(), nullable=False, server_default="en"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "registered_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["facility_id"], ["facilities.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("phone_hash"),
        sa.CheckConstraint("role IN ('reporter', 'supervisor')", name="ck_contacts_role"),
    )
    op.create_index(
        op.f("ix_facility_contacts_facility_id"), "facility_contacts", ["facility_id"]
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_facility_contacts_facility_id"), table_name="facility_contacts")
    op.drop_table("facility_contacts")
