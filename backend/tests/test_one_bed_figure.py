"""One bed figure, with its source and age (fix list #68).

The live audit found four bed figures for one centre: a header "3 of 7" that
matched no report (it was the seeded `bed_status` series), a report of 13 of
20 beds at a centre registered for 7, a verified 2 of 7 from days earlier, and
seeded rows labelled "Read by mock" on a deployment running Gemini.

The rule now: a centre's bed figure is its latest *verified* ward report, with
the time it was verified; older than `bed_stale_hours` it is stale and not
counted as available; a report that exceeds the registered capacity is
rejected with the reason; seeded rows say they are seeded.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy.dialects import postgresql

from app import beds
from app.beds import REJECTED, VERIFIED
from app.config import settings
from app.vision import BedExtraction

NOW = datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc)


def extraction(total=7, occupied=3, code="AB12", model="gemini-test") -> BedExtraction:
    return BedExtraction(
        beds_total=total, beds_occupied=occupied, code_read=code,
        confidence=0.9, notes=None, model=model,
    )


def verify(ex: BedExtraction, registered_total=7):
    return beds.verify(
        ex, expected_code="AB12", loc_method="gps", distance=0.02,
        register_admissions=ex.beds_occupied, registered_total=registered_total,
    )


# ------------------------------------------------------- capacity check ---


def test_a_report_within_the_registered_capacity_still_verifies():
    assert verify(extraction()).verification == VERIFIED


def test_a_photo_showing_more_beds_than_registered_is_rejected_with_the_reason():
    checks = verify(extraction(total=20, occupied=13))
    assert checks.verification == REJECTED
    assert any("20 beds" in r and "registered for 7" in r for r in checks.reasons)


def test_a_photo_showing_fewer_beds_than_registered_is_rejected_too():
    checks = verify(extraction(total=5, occupied=2))
    assert checks.verification == REJECTED


def test_more_occupied_than_the_registered_capacity_is_rejected():
    # The typed path (BEDS 12 over SMS) carries the registered total itself,
    # so only the occupied count can exceed it.
    checks = verify(extraction(total=7, occupied=12, code=None))
    assert checks.verification == REJECTED
    assert any("12 occupied" in r and "7 registered" in r for r in checks.reasons)


def test_without_a_registered_capacity_the_check_does_not_run():
    assert verify(extraction(total=20, occupied=13), registered_total=None).verification == VERIFIED


# ------------------------------------------------------------ staleness ---


def test_a_verified_count_older_than_the_window_is_stale():
    hours = settings.bed_stale_hours
    assert not beds.is_stale(NOW - timedelta(hours=hours - 1), NOW)
    assert beds.is_stale(NOW - timedelta(hours=hours + 1), NOW)


def test_the_stale_window_is_a_day_because_the_code_changes_daily():
    assert settings.bed_stale_hours == 24


# -------------------------------------------------------- one source ---


class CapturingSession:
    def __init__(self, rows):
        self.rows = rows
        self.sql = ""

    async def execute(self, stmt):
        self.sql = str(stmt.compile(dialect=postgresql.dialect()))
        rows = self.rows

        class Result:
            def all(self_inner):
                return rows

        return Result()


def test_the_figure_is_the_latest_verified_report_per_centre():
    old = NOW - timedelta(days=3)
    session = CapturingSession(
        [("A", 2, NOW - timedelta(hours=2)), ("A", 5, old), ("B", 4, old)]
    )
    figures = asyncio.run(beds.latest_verified(session, ["A", "B"], NOW))
    assert figures == {
        "A": beds.BedFigure(occupied=2, as_of=NOW - timedelta(hours=2), stale=False),
        "B": beds.BedFigure(occupied=4, as_of=old, stale=True),
    }
    assert "bed_reports" in session.sql
    assert "bed_reports.verification" in session.sql
    assert "bed_status" not in session.sql
    # Bounded by date as well as by facility: never a read of all history.
    assert "bed_reports.reported_at >=" in session.sql


def test_the_snapshot_no_longer_reads_the_seeded_bed_status_series():
    source = Path(__file__).resolve().parents[1] / "app" / "services.py"
    assert "BedStatus" not in source.read_text(encoding="utf-8")


# ------------------------------------------------------ honest labels ---


def test_seeded_rows_say_they_are_seeded_never_read_by_mock():
    seed = Path(__file__).resolve().parents[1] / "scripts" / "seed.py"
    assert beds.SEED_NOTE in seed.read_text(encoding="utf-8")
    assert (
        beds.read_by(model="mock", notes=beds.SEED_NOTE, source="photo")
        == "Seeded demonstration report (no photo)"
    )


def test_a_typed_count_says_it_was_typed_and_had_no_photo():
    assert beds.read_by(model="typed", notes="typed over sms", source="sms") == (
        "Typed over SMS (no photo)"
    )


def test_the_test_extractor_says_no_model_was_called():
    assert beds.read_by(model="mock", notes=None, source="photo") == (
        "Test extractor — no model was called"
    )


def test_a_model_read_names_the_model():
    assert beds.read_by(model="gemini-3.5-flash-lite", notes=None, source="photo") == (
        "Read by gemini-3.5-flash-lite"
    )


# ------------------------------------------------- the typed SMS reply ---


def test_a_typed_count_over_capacity_is_refused_in_the_reply(monkeypatch):
    from app import ingest
    from app.models import BedReport, Facility

    facility = Facility(id="F", name="Nashik PHC 1", type="PHC", state_silo="MH",
                        district="Nashik", lat=0.0, lng=0.0, beds_total=7)

    class Session:
        async def get(self, model, key):
            return facility

        async def commit(self):
            pass

    async def not_seen(session, external_id):
        return False

    async def identify(session, sender_ref):
        return type("C", (), {"facility_id": "F", "phone_hash": "h", "last_seen_at": None})()

    async def record(session, fac, ex, **kw):
        checks = beds.verify(ex, expected_code="AB12", loc_method=None, distance=None,
                             register_admissions=None, registered_total=fac.beds_total)
        return BedReport(id=9, verification=checks.verification), checks

    monkeypatch.setattr(ingest, "_already_seen", not_seen)
    monkeypatch.setattr(ingest, "identify", identify)
    monkeypatch.setattr(ingest.beds, "record_report", record)
    out = asyncio.run(ingest.process(Session(), ingest.RawSubmission(
        channel="sms", sender_ref="+919800000001", external_id="x1", text="BEDS 12")))
    assert out.accepted is False
    assert "12 occupied is more than the 7 registered beds" in out.reply
    assert "recorded, unverified" not in out.reply
