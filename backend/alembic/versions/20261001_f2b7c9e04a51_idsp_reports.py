"""idsp reports

Fix list #42. One row per IDSP weekly report PDF the model has read: the
rows it found, each with the regex parser's verdict, keyed by the PDF's
SHA-256 so the same report is never sent to the model twice. The table is
pruned to the newest `idsp_reports_kept` (12) in the same write that adds a
report, so it cannot grow: about 10 KB of JSON per report.

Render applies this at container start (`alembic upgrade head` in the
Dockerfile's CMD), before the new code serves a request.

Revision ID: f2b7c9e04a51
Revises: e6a2d4c81f37
Create Date: 2026-10-01

"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "f2b7c9e04a51"
down_revision = "e6a2d4c81f37"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "idsp_reports",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("sha256", sa.Text(), nullable=False),
        sa.Column("year", sa.Integer(), nullable=True),
        sa.Column("week", sa.Integer(), nullable=True),
        sa.Column("source", sa.Text(), nullable=True),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("read_by", sa.Text(), nullable=True),
        sa.Column(
            "read_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("rows", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("dropped", sa.Integer(), server_default="0", nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("sha256"),
    )
    op.create_index(op.f("ix_idsp_reports_read_at"), "idsp_reports", ["read_at"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_idsp_reports_read_at"), table_name="idsp_reports")
    op.drop_table("idsp_reports")
