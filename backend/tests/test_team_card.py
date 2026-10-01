"""Today's team card carries the last check-in at the centre (fix list #27)."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone

from app.api import AttendanceOut
from app.attendance import ROSTER_WINDOW_DAYS, Attendance


def _summary(**extra) -> Attendance:
    return Attendance(
        facility_id="HFR-MH-PHC-00001", roster=5, present=0, rate=0.0, by_method={},
        geofence_pass=0, geofence_checked=0, footfall_today=None, contradiction=None,
        **extra,
    )


def test_last_checkin_reaches_the_response():
    at = datetime(2026, 9, 28, 3, 15, tzinfo=timezone.utc)
    out = AttendanceOut(**asdict(_summary(last_checkin_at=at)))
    assert out.last_checkin_at == at


def test_no_checkin_in_the_window_is_none_not_a_date():
    assert AttendanceOut(**asdict(_summary())).last_checkin_at is None


def test_the_card_says_thirty_days_because_the_roster_window_is_thirty():
    # Team.tsx words the roster as "checked in here in the last 30 days".
    assert ROSTER_WINDOW_DAYS == 30
