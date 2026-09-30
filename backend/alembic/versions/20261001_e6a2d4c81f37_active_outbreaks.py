"""active outbreaks

Fix list #41 (spec v3 §12.5). `outbreak_events` existed from the initial
schema and was never written. An active outbreak now lives there: the state
(district names repeat across states), where it came from ("officer" or
"idsp", with the IDSP unique id), the officer's stated surge — used only when
the district's readings show no rise, and labelled an assumption — who
declared it, when it lapses (the spec's ttl_days=14) and when an officer ended
it early.

Columns added, all nullable except `source`, which has a server default, so
the table's existing rows (none on Render) need no rewrite. One index, on
state, for "the active outbreaks in this state". Tiny table.

Render applies this at container start (`alembic upgrade head` in the
Dockerfile's CMD), before the new code serves a request.

Revision ID: e6a2d4c81f37
Revises: b41c9e2d7a60
Create Date: 2026-10-01

"""

import sqlalchemy as sa
from alembic import op

revision = "e6a2d4c81f37"
down_revision = "b41c9e2d7a60"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("outbreak_events", sa.Column("state_silo", sa.Text(), nullable=True))
    op.add_column(
        "outbreak_events",
        sa.Column("source", sa.Text(), nullable=False, server_default="officer"),
    )
    op.add_column("outbreak_events", sa.Column("source_ref", sa.Text(), nullable=True))
    op.add_column("outbreak_events", sa.Column("surge_pct", sa.Numeric(), nullable=True))
    op.add_column("outbreak_events", sa.Column("declared_by", sa.Text(), nullable=True))
    op.add_column(
        "outbreak_events", sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "outbreak_events", sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_index(
        op.f("ix_outbreak_events_state_silo"), "outbreak_events", ["state_silo"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_outbreak_events_state_silo"), table_name="outbreak_events")
    for column in (
        "ended_at", "expires_at", "declared_by", "surge_pct", "source_ref", "source", "state_silo"
    ):
        op.drop_column("outbreak_events", column)
