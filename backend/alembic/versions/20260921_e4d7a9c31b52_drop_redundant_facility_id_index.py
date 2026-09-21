"""drop the redundant facility_id index on stock_readings

`ix_stock_readings_facility_id` indexes (facility_id). It is a strict prefix of
`ix_readings_facility_sku_time`, which indexes (facility_id, sku_code,
reported_at) — so every lookup the narrow index can serve, the composite serves
too, from its leading column. Postgres agreed in the statistics: on the
deployed database the composite had 293 scans against the narrow index's 6.

It cost 25 MB of a 1 GB volume that was at 90%. That is the whole reason it is
going; on a larger disk it would be tidiness rather than urgency.

Recorded before dropping, so the downgrade restores exactly what was there:

    CREATE INDEX ix_stock_readings_facility_id
        ON public.stock_readings USING btree (facility_id)

Revision ID: e4d7a9c31b52
Revises: b7f3c21d0e58
"""

from typing import Sequence, Union

from alembic import op

revision: str = "e4d7a9c31b52"
down_revision: Union[str, None] = "b7f3c21d0e58"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

INDEX = "ix_stock_readings_facility_id"
TABLE = "stock_readings"


def upgrade() -> None:
    # IF EXISTS because the index may already have been dropped by hand on the
    # deployed database before this migration reached it — that is what the
    # emergency was. A migration that fails on an already-correct database is
    # worse than one that is idempotent.
    op.execute("DROP INDEX IF EXISTS {0}".format(INDEX))


def downgrade() -> None:
    op.create_index(INDEX, TABLE, ["facility_id"], unique=False)
