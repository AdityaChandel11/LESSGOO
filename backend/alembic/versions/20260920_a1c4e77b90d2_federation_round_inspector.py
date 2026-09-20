"""federation round inspector

The silo inspector (spec 12.2, 27, 28 B3) needs more than the accuracy history
the base table carries. Each round records which run it belonged to, what
strategy produced it, what the naive baseline scored on the same held-out
windows, and a per-silo table of windows, live trust and the trust-weighted
count each silo actually contributed.

`per_silo_mae` is replaced by `per_silo`, which holds that whole table rather
than one number per state.

Revision ID: a1c4e77b90d2
Revises: 3de61cc073d9
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a1c4e77b90d2"
down_revision: Union[str, None] = "3de61cc073d9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "federation_rounds",
        sa.Column("run_id", sa.Text(), nullable=False, server_default="unknown"),
    )
    op.alter_column("federation_rounds", "run_id", server_default=None)
    op.add_column("federation_rounds", sa.Column("strategy", sa.Text(), nullable=True))
    op.add_column("federation_rounds", sa.Column("baseline_mae", sa.Float(), nullable=True))
    op.add_column(
        "federation_rounds",
        sa.Column("per_silo", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column("federation_rounds", sa.Column("silos_reporting", sa.Integer(), nullable=True))
    op.alter_column(
        "federation_rounds",
        "global_val_mae",
        existing_type=sa.NUMERIC(),
        type_=sa.Float(),
        existing_nullable=True,
    )
    op.create_index(
        op.f("ix_federation_rounds_completed_at"), "federation_rounds", ["completed_at"]
    )
    op.create_index(op.f("ix_federation_rounds_run_id"), "federation_rounds", ["run_id"])
    # One row per round per run: a re-run cannot quietly overwrite the history
    # a claim was made from.
    op.create_index(
        "uq_federation_round", "federation_rounds", ["run_id", "round_no"], unique=True
    )
    op.drop_column("federation_rounds", "per_silo_mae")


def downgrade() -> None:
    op.add_column(
        "federation_rounds",
        sa.Column("per_silo_mae", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.drop_index("uq_federation_round", table_name="federation_rounds")
    op.drop_index(op.f("ix_federation_rounds_run_id"), table_name="federation_rounds")
    op.drop_index(op.f("ix_federation_rounds_completed_at"), table_name="federation_rounds")
    op.alter_column(
        "federation_rounds",
        "global_val_mae",
        existing_type=sa.Float(),
        type_=sa.NUMERIC(),
        existing_nullable=True,
    )
    op.drop_column("federation_rounds", "silos_reporting")
    op.drop_column("federation_rounds", "per_silo")
    op.drop_column("federation_rounds", "baseline_mae")
    op.drop_column("federation_rounds", "strategy")
    op.drop_column("federation_rounds", "run_id")
