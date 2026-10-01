"""Fix #59: the outbreak panel warns instead of archiving.

A report row says how old it is, whether this network has centres in its
district and how many, which medicines its disease drives, and whether an
outbreak is active there now. Rows that began inside the outbreak window come
first; older ones are grouped as historical. The map rings a district with an
active outbreak.
"""

from __future__ import annotations

import asyncio
from datetime import date

from app import api, geo, idsp


def row(uid, district, disease, start, *, week=32, state="MH", cases=10, **kw):
    base = {
        "unique_id": uid, "year": 2026, "week": week, "state": "Maharashtra", "state_code": state,
        "district": district, "disease": disease, "cases": cases, "deaths": 0,
        "start_date": start, "reported_date": start, "status": "Under Surveillance",
    }
    base.update(kw)
    return base


TODAY = date(2026, 10, 1)
OURS = {("MH", "nashik"), ("MH", "pune")}
COUNTS = {("MH", "Nashik"): 31, ("MH", "Pune"): 28}


def panel(rows, active=()):
    return idsp.panel_rows(
        rows, ours=OURS, facility_counts=COUNTS, today=TODAY, ttl_days=14, active=set(active)
    )


def test_a_row_carries_its_age_its_centres_and_its_medicines():
    (r,) = panel([row("a", "Nashik", "Cholera", "2026-09-24")])
    assert r["age_days"] == 7 and r["historical"] is False
    assert r["in_network"] is True and r["facilities"] == 31
    assert r["medicines"] == [
        "Oral Rehydration Salts", "IV Fluid Ringer Lactate", "Zinc Sulphate 20mg",
    ]
    assert r["active"] is False


def test_a_district_outside_the_network_has_no_centres_and_says_so():
    (r,) = panel([row("a", "Latur", "Cholera", "2026-09-24")])
    assert r["in_network"] is False and r["facilities"] == 0


def test_a_disease_the_map_does_not_cover_drives_no_medicine():
    (r,) = panel([row("a", "Nashik", "Scrub Typhus", "2026-09-24")])
    assert r["medicines"] == []


def test_rows_past_the_window_are_historical_and_an_undated_row_is_too():
    old, undated = panel([
        row("a", "Nashik", "Cholera", "2026-08-09"),
        row("b", "Nashik", "Cholera", None),
    ])
    assert old["historical"] is True and old["age_days"] == 53
    assert undated["historical"] is True and undated["age_days"] is None


def test_recent_rows_come_first_then_the_newest_report_then_the_network():
    out = panel([
        row("old-ours", "Nashik", "Cholera", "2026-08-09", week=32),
        row("old-other", "Latur", "Cholera", "2026-08-10", week=32),
        row("older-report", "Pune", "Cholera", "2026-08-03", week=31),
        row("recent-other", "Latur", "Dengue", "2026-09-25", week=32),
        row("recent-ours", "Pune", "Dengue", "2026-09-22", week=32),
    ])
    assert [r["unique_id"] for r in out] == [
        "recent-ours", "recent-other", "old-ours", "old-other", "older-report",
    ]


def test_a_row_knows_when_an_outbreak_is_active_in_its_district():
    quiet, live = panel(
        [row("a", "Pune", "Dengue", "2026-09-22"), row("b", "Nashik", "Cholera", "2026-09-24")],
        active={("MH", "nashik")},
    )
    assert {quiet["unique_id"]: quiet["active"], live["unique_id"]: live["active"]} == {
        "a": False, "b": True,
    }


def test_a_district_has_a_place_on_the_map():
    lat, lng = geo.district_anchor("MH", "Nashik")
    assert (round(lat, 2), round(lng, 2)) == (20.0, 73.79)
    assert geo.district_anchor("MH", "nashik") == (lat, lng)
    assert geo.district_anchor("MH", "Atlantis") is None


def test_the_endpoint_returns_the_panel_rows(monkeypatch):
    async def counts(session):
        return {("KL", "Kozhikode"): 12}

    async def none_active(session, now, state=None):
        return []

    monkeypatch.setattr(api, "_district_facility_counts", counts)
    monkeypatch.setattr(api.outbreak, "active", none_active)
    out = asyncio.run(api.outbreaks("KL", session=object()))
    assert out.ttl_days == 14
    assert out.rows and all(r.state_code == "KL" for r in out.rows)
    assert all(r.historical for r in out.rows)  # NCDC's latest reports are weeks old
    assert all(r.age_days is None or r.age_days > 14 for r in out.rows)
