"""transfer cancelled status

Fix list #31. A centre could raise a request for stock but never withdraw it:
the per-centre cap told the pharmacist to "confirm or cancel one" and there was
no cancel. A cancelled request is a real outcome — the centre changed its
mind, or found the stock elsewhere — and it must stay on record rather than be
deleted, so it gets its own status instead of being folded into 'rejected',
which is the donor's word, not the requester's.

One constraint widened, nothing else. The new set is a superset of the old
one, so every existing row already satisfies it; `transfers` is small (plans
replace their own proposals), so the validation scan is brief.

Render applies this at container start (`alembic upgrade head` in the
Dockerfile's CMD), before the new code serves a request.

Downgrade restores the old set as NOT VALID, so it succeeds even after a
request has been cancelled: existing 'cancelled' rows are left as they are
rather than rewritten into a status that would misstate who closed them.
New rows are checked against the old set again from that point on.

Revision ID: b41c9e2d7a60
Revises: d5e1f83a9c47
Create Date: 2026-09-30

"""

from alembic import op

revision = "b41c9e2d7a60"
down_revision = "d5e1f83a9c47"
branch_labels = None
depends_on = None

WITH_CANCELLED = "status IN ('proposed', 'approved', 'rejected', 'completed', 'cancelled')"
WITHOUT_CANCELLED = "status IN ('proposed', 'approved', 'rejected', 'completed')"


def upgrade() -> None:
    op.drop_constraint("ck_transfers_status", "transfers", type_="check")
    op.create_check_constraint("ck_transfers_status", "transfers", WITH_CANCELLED)


def downgrade() -> None:
    op.drop_constraint("ck_transfers_status", "transfers", type_="check")
    op.execute(
        "ALTER TABLE transfers ADD CONSTRAINT ck_transfers_status CHECK ({0}) NOT VALID".format(
            WITHOUT_CANCELLED
        )
    )
