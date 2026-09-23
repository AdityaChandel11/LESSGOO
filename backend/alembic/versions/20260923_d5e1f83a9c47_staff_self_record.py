"""staff self-record

Two changes, both in service of one screen: the person who works at a health
centre being able to open the app and see their own attendance.

`users.staff_ref` is the link that did not exist. Attendance rows have carried
a pseudonymous `staff_ref` since the initial schema, but nothing connected an
account to one, so the only view possible was the facility count. Nullable,
and not a foreign key, for the same reason the other scope columns are not:
facility data is reloaded with TRUNCATE ... CASCADE and that must not delete
accounts. Unique per facility, so two accounts cannot claim one person's
record.

`staff_verifications` is the random re-verification the trust layer always
described (data-trust-layer.md §1 — "random re-verification pings during the
day") and never stored. A check-in says someone started a shift; a ping sent
at an unpredictable moment two hours later, answered from inside the geofence,
says they were still there. Sent and answered are separate columns because an
unanswered ping is a real outcome and must not be indistinguishable from one
that was never sent.

What this table deliberately does not hold: no phone number, no message body,
no recording, no transcript. `staff_ref` is pseudonymous, exactly as it is on
`staff_checkins`, and the API only ever returns these rows to the account
whose own `staff_ref` they carry — never to an officer, and never named.
Rule 8 (v3 §1.8) is unchanged by this migration: nothing here makes a person
visible to anyone but themselves.

Revision ID: d5e1f83a9c47
Revises: c7b2e49a1d83
Create Date: 2026-09-23

"""

from alembic import op
import sqlalchemy as sa

revision = "d5e1f83a9c47"
down_revision = "c7b2e49a1d83"
branch_labels = None
depends_on = None

CHANNELS = ("sms", "ivr")
# `expired` is not `no_reply`: one means the window closed with nothing back,
# the other that a reply arrived carrying no location we could check.
OUTCOMES = ("confirmed", "out_of_range", "unlocatable", "no_reply")


def upgrade() -> None:
    op.add_column("users", sa.Column("staff_ref", sa.Text(), nullable=True))
    op.create_unique_constraint(
        "uq_users_facility_staff_ref", "users", ["facility_id", "staff_ref"]
    )

    op.create_table(
        "staff_verifications",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("facility_id", sa.Text(), nullable=False),
        # Pseudonymous, as on staff_checkins. Never surfaced to anyone but the
        # account it belongs to (rule 8).
        sa.Column("staff_ref", sa.Text(), nullable=False),
        sa.Column("channel", sa.Text(), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False),
        # NULL is the answer when nothing came back. Not zero, not sent_at.
        sa.Column("responded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("loc_method", sa.Text(), nullable=True),
        sa.Column("cell_id", sa.Text(), nullable=True),
        sa.Column("geofence_km", sa.Float(), nullable=True),
        sa.Column("geofence_ok", sa.Boolean(), nullable=True),
        sa.Column("outcome", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["facility_id"], ["facilities.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "channel IN {0}".format(CHANNELS), name="ck_staff_verifications_channel"
        ),
        sa.CheckConstraint(
            "outcome IN {0}".format(OUTCOMES), name="ck_staff_verifications_outcome"
        ),
        # A ping with nothing back cannot carry a response time, and one that
        # was answered must. Enforced here rather than trusted to the writer.
        sa.CheckConstraint(
            "(outcome = 'no_reply') = (responded_at IS NULL)",
            name="ck_staff_verifications_reply",
        ),
    )
    # The one read path: this person's pings, newest first, over a short window.
    op.create_index(
        "ix_staff_verifications_ref_sent",
        "staff_verifications",
        ["staff_ref", "sent_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_staff_verifications_ref_sent", table_name="staff_verifications"
    )
    op.drop_table("staff_verifications")
    op.drop_constraint("uq_users_facility_staff_ref", "users", type_="unique")
    op.drop_column("users", "staff_ref")
