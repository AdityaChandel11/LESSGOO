"""Demo-only paths: labelled, deterministic, and absent outside demo mode."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

from app import api, attendance, idsp
from app.auth import Principal

NOW = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)


def test_a_synthetic_record_is_labelled_on_every_day():
    record = attendance.synthetic_record("HFR-MH-PHC-00001", now=NOW)
    assert record.days and all(d.synthetic for d in record.days)
    assert record.days[0].present and record.days[0].source == "biometric"


def test_the_same_day_is_generated_the_same_way():
    a = attendance.synthetic_day("HFR-MH-PHC-00001", NOW.date(), NOW)
    b = attendance.synthetic_day("HFR-MH-PHC-00001", NOW.date(), NOW)
    assert a == b


def test_no_random_check_is_dated_after_now():
    record = attendance.synthetic_record("HFR-MH-PHC-00001", now=NOW)
    assert all(p.sent_at <= NOW for d in record.days for p in d.pings)


def test_the_demo_request_does_not_exist_outside_demo_mode(monkeypatch):
    monkeypatch.setattr(api.settings, "demo_mode", False)
    user = Principal(1, "p@example.test", "P", "facility_user", "MH", "Nashik", "HFR-MH-PHC-00001", None)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(api.demo_neighbour_request("HFR-MH-PHC-00001", session=None, user=user))
    assert exc.value.status_code == 404


def test_stocking_advice_invents_no_demand_trend():
    # Fix #41: the random "demand up N% (simulated)" is gone; the rise comes
    # from declaring the outbreak (observed, or the officer's assumption).
    advice = idsp.stocking_advice("MH", 9)
    assert advice and not any("simulated" in s for a in advice for s in a["signals"])
    assert all(a["medicines"] for a in advice)
