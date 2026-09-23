"""The one screen that shows a person to themselves — and to nobody else.

Two things are being defended here, and they pull in opposite directions.

The first is rule 8 (v3 §1.8): attendance attaches to a facility and a
pattern, never a named individual. `/me/attendance` is the single endpoint
that returns a per-person history, so the test that matters most is not about
what it returns but about what it *cannot be asked for*: there is no parameter
anywhere on it that names a staff reference, so there is no value an attacker
can vary to reach somebody else's rows. That is checked against the source,
because a permission check can be deleted in a later edit and an absent
parameter cannot be reintroduced by accident.

The second is that the screen has something honest to show. The generator has
to produce a month of daily shifts with real gaps in it, and re-verification
pings whose four outcomes stay consistent with what the database will accept —
a ping recorded as unanswered while carrying a reply time is a row Postgres
refuses, and finding that out during a seed run is finding it out too late.
"""

from __future__ import annotations

import ast
import inspect
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app import attendance
from app.models import VERIFICATION_CHANNELS, VERIFICATION_OUTCOMES
from scripts.seed import ATTENDANCE_DAYS, build_checkins

FACILITIES = [
    {
        "id": "HFR-MH-PHC-00001",
        "name": "Nashik PHC 1",
        "type": "PHC",
        "state_silo": "MH",
        "district": "Nashik",
        "lat": 19.9975,
        "lng": 73.7898,
    },
    {
        "id": "HFR-MH-CHC-00004",
        "name": "Nashik CHC 1",
        "type": "CHC",
        "state_silo": "MH",
        "district": "Nashik",
        "lat": 20.0102,
        "lng": 73.7701,
    },
]

# Column order as `_copy` writes them, so a reordering in the generator fails
# here rather than silently writing cell ids into the geofence column.
CHECKIN_COLS = (
    "facility_id", "staff_ref", "checked_in_at", "checked_out_at", "shift",
    "source", "cell_id", "loc_method", "geofence_km", "geofence_ok",
    "footfall_same_period",
)
PING_COLS = (
    "facility_id", "staff_ref", "channel", "sent_at", "responded_at",
    "loc_method", "cell_id", "geofence_km", "geofence_ok", "outcome",
)


def _generate(seed: int = 7, gaming: set[str] | None = None):
    checkins, pings = build_checkins(FACILITIES, random.Random(seed), gaming or set())
    return (
        [dict(zip(CHECKIN_COLS, row)) for row in checkins],
        [dict(zip(PING_COLS, row)) for row in pings],
    )


# ------------------------------------------------ the boundary, structurally ---


def _endpoint_node() -> ast.FunctionDef:
    tree = ast.parse(Path(inspect.getfile(attendance)).with_name("api.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "my_attendance":
            return node
    raise AssertionError("my_attendance endpoint is gone")


def test_own_record_endpoint_accepts_no_staff_reference():
    """No path, query or body parameter may name whose record to return.

    The reference comes off the signed-in principal or the request fails. If
    somebody later adds `staff_ref: str` to this signature to make an admin
    view convenient, this test is the thing that says no.
    """
    args = _endpoint_node().args
    names = {a.arg for a in args.args + args.posonlyargs + args.kwonlyargs}
    assert "staff_ref" not in names
    assert "facility_id" not in names
    # Only the two injected dependencies.
    assert names == {"session", "user"}


def test_facility_summary_still_returns_no_staff_reference():
    """The officer-facing view stays counts-only (rule 8)."""
    fields = set(attendance.Attendance.__dataclass_fields__)
    assert "staff_ref" not in fields
    assert not any("staff" in f and f != "staff_ref" for f in fields if "ref" in f)


def test_checkin_event_never_carries_the_reference():
    """Published events feed the live map, which anyone signed in can watch."""
    source = inspect.getsource(attendance._publish)
    assert "row.staff_ref" not in source
    assert "never as a named individual" in source  # the comment that says why


# --------------------------------------------------- a month worth showing ---


def test_generates_a_month_of_daily_shifts():
    checkins, _ = _generate()
    days = {r["checked_in_at"].date() for r in checkins}
    # Not "some days": most of the window, or the screen has nothing to show.
    assert len(days) >= ATTENDANCE_DAYS - 2


def test_every_person_has_days_they_missed():
    """A record with no gaps in it teaches the reader nothing."""
    checkins, _ = _generate()
    by_ref: dict[str, set] = {}
    for row in checkins:
        by_ref.setdefault(row["staff_ref"], set()).add(row["checked_in_at"].date())
    assert by_ref, "no staff generated"
    # Across a whole facility roster, somebody must have missed something.
    assert any(len(days) < ATTENDANCE_DAYS for days in by_ref.values())
    # And nobody is absent so often the record looks broken.
    assert all(len(days) >= ATTENDANCE_DAYS // 3 for days in by_ref.values())


def test_references_belong_to_their_own_facility():
    checkins, pings = _generate()
    for row in checkins + pings:
        assert row["staff_ref"].startswith(row["facility_id"])


def test_nothing_is_stamped_in_the_future():
    now = datetime.now(timezone.utc)
    checkins, pings = _generate()
    assert all(r["checked_in_at"] <= now for r in checkins)
    assert all(r["sent_at"] <= now for r in pings)
    assert all(
        r["responded_at"] is None or r["responded_at"] >= r["sent_at"] for r in pings
    )


# -------------------------------------------------------- the random pings ---


def test_pings_satisfy_the_database_check_constraints():
    """Exactly the three CHECKs in migration d5e1f83a9c47, run in Python.

    A seed that violates one of these fails halfway through a COPY, after the
    facilities are already written, which is the worst moment to find out.
    """
    _, pings = _generate()
    assert pings, "no verification pings generated"
    for row in pings:
        assert row["channel"] in VERIFICATION_CHANNELS
        assert row["outcome"] in VERIFICATION_OUTCOMES
        # ck_staff_verifications_reply
        assert (row["outcome"] == "no_reply") == (row["responded_at"] is None)


def test_a_ping_only_follows_a_shift_that_was_started():
    """There is no shift to re-verify if nobody checked in.

    Matched against the shift's start time rather than its calendar date: a
    night shift beginning at 20:00 collects its re-verification after
    midnight, on paper the next day, and that is not a stray ping.
    """
    checkins, pings = _generate()
    starts: dict[str, list[datetime]] = {}
    for r in checkins:
        starts.setdefault(r["staff_ref"], []).append(r["checked_in_at"])
    for row in pings:
        assert any(
            0 <= (row["sent_at"] - t).total_seconds() <= 14 * 3600
            for t in starts.get(row["staff_ref"], [])
        ), f"ping at {row['sent_at']} follows no shift of {row['staff_ref']}"


def test_a_ping_never_claims_a_location_its_channel_cannot_carry():
    """IVR carries the caller's number and nothing more (spec 26.1).

    An answered call is recorded as `unlocatable`, which is a different fact
    from `confirmed` and must never be rounded up into it.
    """
    _, pings = _generate()
    for row in pings:
        if row["outcome"] == "unlocatable":
            assert row["geofence_ok"] is None and row["geofence_km"] is None
        if row["outcome"] == "confirmed":
            assert row["geofence_ok"] is True
        if row["outcome"] == "out_of_range":
            assert row["geofence_ok"] is False
        if row["outcome"] == "no_reply":
            assert row["loc_method"] is None


def test_all_four_outcomes_occur_across_a_national_run():
    """Each branch of the UI needs a row that exercises it."""
    wide = [
        dict(FACILITIES[i % 2], id=f"HFR-MH-PHC-{i:05d}")
        for i in range(60)
    ]
    _, pings = build_checkins(wide, random.Random(11), {wide[0]["id"], wide[1]["id"]})
    seen = {row[PING_COLS.index("outcome")] for row in pings}
    assert seen == set(VERIFICATION_OUTCOMES), f"missing outcomes: {set(VERIFICATION_OUTCOMES) - seen}"


def test_a_centre_marking_everyone_present_fails_its_pings():
    """The gaming signal reaches the worker's own screen too.

    A facility that reports full attendance every day is also one where the
    random re-verification often finds nobody. That contradiction is the whole
    point of 12.6 — this checks it is visible per person, not only in the
    aggregate.
    """
    honest_c, honest_p = _generate(seed=3)
    gaming_c, gaming_p = _generate(seed=3, gaming={f["id"] for f in FACILITIES})

    def unanswered(pings):
        return sum(1 for p in pings if p["outcome"] == "no_reply") / max(len(pings), 1)

    assert unanswered(gaming_p) > unanswered(honest_p)
    # And it marks everyone present every day, which is the thing that looks
    # right until you read the pings next to it.
    assert len(gaming_c) > len(honest_c)


def test_the_window_is_bounded():
    """The size guard forbids unbounded reads; the record is one month."""
    assert attendance.SELF_WINDOW_DAYS == ATTENDANCE_DAYS
    source = inspect.getsource(attendance.own_record)
    assert "StaffCheckin.checked_in_at >= floor" in source
    assert "StaffVerification.sent_at >= floor" in source
    assert "StaffCheckin.staff_ref == staff_ref" in source


def test_the_record_is_newest_first_and_covers_every_day():
    """Including the days with nothing in them — that is the point."""
    source = inspect.getsource(attendance.own_record)
    assert "days.reverse()" in source
    assert "for offset in range(window_days)" in source
