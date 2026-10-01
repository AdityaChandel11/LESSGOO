"""briefing state language

Fix list #85. The Today list is written in English, Hindi and the state's own
language (Marathi in Maharashtra), and the cache could store only the first
two: `lang IN ('en', 'hi')`. The constraint becomes "a two-letter language
code". Which codes are actually written is decided in code
(workspace.STATE_LANGUAGES), not in the database.

One constraint widened, nothing else. Every existing row ('en' or 'hi')
already satisfies the new rule, and `facility_briefings` is a cache capped at
`max_briefing_rows` (500), so the validation scan is a few hundred rows.

Render applies this at container start (`alembic upgrade head` in the
Dockerfile's CMD), before the new code serves a request.

Downgrade restores the old set as NOT VALID, so it succeeds even after a
state-language line has been cached; the cache evicts those rows on its own.

Revision ID: a9d3f5b27c14
Revises: f2b7c9e04a51
Create Date: 2026-10-01

"""

from alembic import op

revision = "a9d3f5b27c14"
down_revision = "f2b7c9e04a51"
branch_labels = None
depends_on = None

ANY_LANGUAGE = "lang ~ '^[a-z]{2}$'"
EN_HI = "lang IN ('en', 'hi')"


def upgrade() -> None:
    op.drop_constraint("ck_briefings_lang", "facility_briefings", type_="check")
    op.create_check_constraint("ck_briefings_lang", "facility_briefings", ANY_LANGUAGE)


def downgrade() -> None:
    op.drop_constraint("ck_briefings_lang", "facility_briefings", type_="check")
    op.execute(
        "ALTER TABLE facility_briefings ADD CONSTRAINT ck_briefings_lang "
        "CHECK ({0}) NOT VALID".format(EN_HI)
    )
