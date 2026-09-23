"""call log

One table, and the smallest one in the schema on purpose.

Twilio already keeps the call record and any recording on its own side, under
its own retention. Duplicating that here would mean this database held a log
of who rang whom — exactly what the salted-hash design across the rest of the
schema exists to prevent. So this stores no audio, no transcript, no phone
number and not even a hash of one: a reference that means something to Twilio,
what happened, when, which facility it concerned, and which way it went.

`call_ref` is Twilio's CallSid and is the primary key, so a status callback
delivered twice — which Twilio does deliberately on retry — updates one row
rather than appending a second. The index on `created_at` is read by the
eviction sweep that holds the table to `max_call_log_rows` (200) and by the
demo panel; nothing else reads it.

Revision ID: c7b2e49a1d83
Revises: f3a81c6d5492
Create Date: 2026-09-23

"""

from alembic import op
import sqlalchemy as sa

revision = "c7b2e49a1d83"
down_revision = "f3a81c6d5492"
branch_labels = None
depends_on = None

OUTCOMES = (
    "queued", "ringing", "in-progress", "completed",
    "busy", "no-answer", "failed", "canceled",
)


def upgrade() -> None:
    op.create_table(
        "call_logs",
        sa.Column("call_ref", sa.Text(), nullable=False),
        # SET NULL rather than CASCADE: facility data is reloaded with
        # TRUNCATE ... CASCADE, and a reseed should not silently delete the
        # record that a call happened.
        sa.Column("facility_id", sa.Text(), nullable=True),
        sa.Column("direction", sa.Text(), nullable=False),
        sa.Column("outcome", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["facility_id"], ["facilities.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("call_ref"),
        sa.CheckConstraint(
            "direction IN ('inbound', 'outbound')", name="ck_call_logs_direction"
        ),
        sa.CheckConstraint(
            "outcome IN {0}".format(OUTCOMES), name="ck_call_logs_outcome"
        ),
    )
    op.create_index("ix_call_logs_created_at", "call_logs", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_call_logs_created_at", table_name="call_logs")
    op.drop_table("call_logs")
