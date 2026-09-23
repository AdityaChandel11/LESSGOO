"""National synthetic data generator — spec Section 10, scaled to all states.

Run (after `alembic upgrade head`):
      python -m scripts.seed
      python -m scripts.seed --days 35 --focus-days 400

HONESTY NOTE for the pitch: district/city coordinates in app/geo.py are real.
Facility positions are synthetic scatter around them and every stock number is
simulated. Facility counts are a ~12% proportional sample of each state's
published PHC count, not the real network size. Say that ratio out loud.

History depth is deliberately uneven, because the two consumers need different
things. Every facility gets enough history for a burn rate and the live map;
the focus state gets deep history because that is what the federated
forecasting layer in 12.2 will actually train on.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import random
from datetime import date as date_cls
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import insert, text

from app import groundtruth, ingest
from app.config import settings
from app.db import SessionLocal, engine
from app.geo import INDIA_STATES, TOTAL_SEEDED_FACILITIES, StateGeo
from app.models import Facility, Sku
from app.services import classify

DEFAULT_SEED = 20260915

SKUS: list[dict] = [
    {"code": "ORS", "name": "Oral Rehydration Salts", "unit": "sachet", "base": 45,
     "monsoon_sensitive": True,
     "aliases": ["ors", "o r s", "oral rehydration salts", "ओआरएस", "orz"]},
    {"code": "ZINC", "name": "Zinc Sulphate 20mg", "unit": "tablet", "base": 38,
     "monsoon_sensitive": True, "aliases": ["zinc", "zinc sulphate", "जिंक"]},
    {"code": "PARA500", "name": "Paracetamol 500mg", "unit": "tablet", "base": 90,
     "monsoon_sensitive": False, "aliases": ["paracetamol", "para", "pcm", "dolo"]},
    {"code": "AMOX", "name": "Amoxicillin 250mg", "unit": "capsule", "base": 40,
     "monsoon_sensitive": True, "aliases": ["amoxicillin", "amox", "amoxy"]},
    {"code": "IVFLUID", "name": "IV Fluid Ringer Lactate 500ml", "unit": "bottle",
     "base": 22, "monsoon_sensitive": True,
     "aliases": ["iv fluid", "ringer lactate", "rl", "drip"]},
    {"code": "IRONFA", "name": "Iron Folic Acid", "unit": "tablet", "base": 70,
     "monsoon_sensitive": False, "aliases": ["iron folic acid", "ifa", "iron"]},
    {"code": "TBDOTS", "name": "Anti-TB FDC (DOTS)", "unit": "blister", "base": 12,
     "monsoon_sensitive": False, "aliases": ["tb drug", "dots", "anti tb"]},
    {"code": "ACT", "name": "Artemisinin Combination Therapy", "unit": "blister",
     "base": 9, "monsoon_sensitive": True, "aliases": ["act", "antimalarial"]},
    {"code": "AMLO", "name": "Amlodipine 5mg", "unit": "tablet", "base": 55,
     "monsoon_sensitive": False, "aliases": ["amlodipine", "amlo", "bp tablet"]},
    {"code": "METF", "name": "Metformin 500mg", "unit": "tablet", "base": 60,
     "monsoon_sensitive": False, "aliases": ["metformin", "sugar tablet"]},
    {"code": "OXY", "name": "Oxytocin Injection 5IU", "unit": "ampoule", "base": 8,
     "monsoon_sensitive": False, "cold_chain": True, "aliases": ["oxytocin", "oxy"]},
    {"code": "MORPH", "name": "Morphine Sulphate 10mg", "unit": "ampoule", "base": 3,
     "monsoon_sensitive": False, "is_controlled": True, "aliases": ["morphine"]},
]

OUTBREAK_DISTRICT = "Nashik"
OUTBREAK_SKUS = ("ORS", "ZINC", "IVFLUID")
OUTBREAK_DAYS_AGO = 140
OUTBREAK_LENGTH_DAYS = 18
OUTBREAK_MULTIPLIER = 2.8

FESTIVAL_ANCHORS = ((10, 20), (11, 5))
REORDER_POINT_DAYS = 7.0
RESUPPLY_COVER_DAYS = 30.0
LEAD_TIME_RANGE = (2, 6)
BURN_WINDOW = 29


def _is_festival(day: datetime) -> bool:
    return any(day.month == m and abs(day.day - d) <= 3 for m, d in FESTIVAL_ANCHORS)


def build_facilities(rng: random.Random) -> list[dict]:
    rows: list[dict] = []
    for state in INDIA_STATES:
        for i in range(state.facilities):
            district, lat, lng = state.anchors[i % len(state.anchors)]
            is_chc = i % 5 == 4
            kind = "CHC" if is_chc else "PHC"
            rows.append(
                {
                    "id": f"HFR-{state.code}-{kind}-{i:05d}",
                    "name": f"{district} {kind} {i // len(state.anchors) + 1}",
                    "type": kind,
                    "state_silo": state.code,
                    "district": district,
                    "lat": round(lat + rng.gauss(0, state.spread / 2), 6),
                    "lng": round(lng + rng.gauss(0, state.spread / 2), 6),
                    "beds_total": (30 if is_chc else 6) + rng.randint(0, 6),
                }
            )
    return rows


def _burn_from_window(window: list[tuple[datetime, float]]) -> float:
    """Mirror of services._burn_rate, so seeded status matches recomputed."""
    if len(window) < 2:
        return 0.0
    drops = sum(
        max(0.0, prev - curr)
        for (_, prev), (_, curr) in zip(window, window[1:])
    )
    span = max((window[-1][0] - window[0][0]).total_seconds() / 86400.0, 1.0)
    return drops / span


def simulate_facility(
    facility: dict,
    state: StateGeo,
    days: int,
    rng: random.Random,
    *,
    is_gaming: bool,
    supply_failure: bool,
) -> tuple[list[tuple], list[dict], list[tuple]]:
    """Return (reading_records, snapshot_rows, bed_records)."""
    # Never stamp a reading in the future. A fixed hour-of-day looks tidier in
    # the history, but any facility whose latest seeded reading is later than
    # the wall clock would silently outrank a live report made this morning —
    # the demo would take the report and the map would not move.
    now = datetime.now(timezone.utc)
    today = min(now.replace(hour=8, minute=0, second=0, microsecond=0),
                now - timedelta(minutes=5))
    start = today - timedelta(days=days - 1)

    readings: list[tuple] = []
    snapshots: list[dict] = []
    beds: list[tuple] = []

    gaming_window = None
    if is_gaming and days > 70:
        g_len = rng.randint(60, min(200, days - 10))
        g_start = rng.randint(0, max(0, days - g_len - 1))
        gaming_window = (g_start, g_start + g_len)

    scale = 2.2 if facility["type"] == "CHC" else 1.0

    for sku in SKUS:
        base_rate = sku["base"] * scale * rng.uniform(0.6, 1.5)
        phase = state.seasonal_phase + rng.uniform(-0.4, 0.4)

        qty = base_rate * rng.uniform(12, 34)
        pending: list[tuple[int, float]] = []
        frozen_qty: float | None = None
        window: list[tuple[datetime, float]] = []
        last_reported = today

        for d in range(days):
            day = start + timedelta(days=d)
            doy = day.timetuple().tm_yday
            days_ago = days - 1 - d

            seasonal = 1 + 0.4 * math.sin(2 * math.pi * doy / 365 - phase)
            monsoon = (
                1 + state.monsoon_boost
                if (sku["monsoon_sensitive"] and day.month in state.monsoon_months)
                else 1.0
            )
            festival = 0.75 if _is_festival(day) else 1.0
            outbreak = (
                OUTBREAK_MULTIPLIER
                if (
                    facility["district"] == OUTBREAK_DISTRICT
                    and sku["code"] in OUTBREAK_SKUS
                    and OUTBREAK_DAYS_AGO - OUTBREAK_LENGTH_DAYS
                    <= days_ago
                    <= OUTBREAK_DAYS_AGO
                )
                else 1.0
            )

            consumption = max(
                0.0,
                base_rate * seasonal * monsoon * festival * outbreak
                + rng.gauss(0, base_rate * 0.12),
            )

            for arrival, amount in list(pending):
                if arrival == d:
                    qty += amount
                    pending.remove((arrival, amount))

            qty = max(0.0, qty - consumption)

            if qty / max(base_rate, 1e-6) < REORDER_POINT_DAYS and not pending:
                if not (supply_failure and days_ago < 12):
                    pending.append(
                        (
                            min(days - 1, d + rng.randint(*LEAD_TIME_RANGE)),
                            base_rate * RESUPPLY_COVER_DAYS,
                        )
                    )

            footfall = max(
                0,
                int(
                    (18 if facility["type"] == "CHC" else 9)
                    * seasonal * monsoon * outbreak * rng.uniform(0.7, 1.3)
                ),
            )

            reported = qty
            if gaming_window and gaming_window[0] <= d <= gaming_window[1]:
                if frozen_qty is None:
                    frozen_qty = max(qty, base_rate * 15)
                reported = frozen_qty
            else:
                frozen_qty = None

            reported = round(reported, 2)
            readings.append(
                (
                    facility["id"],
                    sku["code"],
                    Decimal(str(reported)),
                    day,
                    "seed",
                    footfall,
                    Decimal("1.0"),
                )
            )
            window.append((day, reported))
            if len(window) > BURN_WINDOW:
                window.pop(0)
            last_reported = day

        burn = _burn_from_window(window)
        final_qty = window[-1][1]
        dos = final_qty / max(burn, 1e-6) if burn > 0 else None
        snapshots.append(
            {
                "facility_id": facility["id"],
                "sku_code": sku["code"],
                "qty_on_hand": final_qty,
                "daily_burn_rate": burn,
                "days_of_stock": dos,
                "status": classify(dos),
                "last_reported_at": last_reported,
                "last_source": "seed",
                "last_confidence": 1.0,
            }
        )

    for d in range(max(0, days - 30), days):
        day = start + timedelta(days=d)
        ratio = rng.uniform(0.35, 0.8)
        if supply_failure and (days - 1 - d) < 10:
            ratio = rng.uniform(0.88, 1.0)
        beds.append((facility["id"], int(facility["beds_total"] * ratio), day))

    return readings, snapshots, beds


ATTENDANCE_DAYS = 30
IST_OFFSET = timedelta(hours=5, minutes=30)
# Sent at an unpredictable moment inside a shift, roughly this often. Not every
# day: a ping a worker can set their watch by is a ping they can arrange to be
# somewhere for, which defeats the whole point of it being random.
PING_CHANCE = 0.45


def build_checkins(
    facilities: list[dict], rng: random.Random, gaming: set[str]
) -> tuple[list[tuple], list[tuple]]:
    """Daily shift check-ins and their random re-verification pings.

    Two row-sets, built together because the second depends on the first: a
    ping is only ever sent to somebody who checked in, since there is no shift
    to re-verify otherwise.

    Channels are mixed deliberately. Most facilities check in from a phone with
    GPS, some over a USSD gateway that carries only a cell tower, and some by
    IVR, which carries no location at all. A facility reporting entirely
    through a channel that cannot be geofenced is not doing anything wrong --
    but it is a facility whose attendance nobody can verify, and the trust
    score has to be able to see that.

    Absences are generated per person per day, not as one ratio applied to a
    facility, because that is what makes a pattern legible on the worker's own
    screen: a scatter of missed days reads as leave, a run of five reads as a
    posting elsewhere, and the app says it cannot tell which.
    """
    checkins: list[tuple] = []
    pings: list[tuple] = []
    now = datetime.now(timezone.utc)

    for fac in facilities:
        roster = 8 if fac["type"] == "CHC" else 4
        # How this facility reports: most have a smartphone somewhere on site.
        source, method = rng.choices(
            [("form", "gps"), ("ussd", "simulated"), ("ivr", "none")],
            weights=[0.7, 0.18, 0.12],
        )[0]
        # A gaming facility marks everyone present every single day. Its
        # footfall is the signal that disagrees, which is the whole point of
        # 12.6 -- and, on the worker's own screen, its pings go unanswered.
        is_gaming = fac["id"] in gaming

        for staff in range(roster):
            ref = f"{fac['id']}-S{staff:02d}"
            # Each person keeps their own attendance rate across the month, so
            # one centre can hold a reliable worker and a patchy one at once.
            attends = 1.0 if is_gaming else rng.choice([0.93, 0.9, 0.87, 0.82, 0.7])
            # One stretch of consecutive absence somewhere in the window for
            # about a third of people -- leave, training, or a posting.
            block_start = (
                rng.randint(2, ATTENDANCE_DAYS - 6)
                if not is_gaming and rng.random() < 0.45
                else None
            )
            block_len = rng.randint(2, 5) if block_start is not None else 0

            for days_ago in range(ATTENDANCE_DAYS):
                if (
                    block_start is not None
                    and block_start <= days_ago < block_start + block_len
                ):
                    continue
                if rng.random() >= attends:
                    continue

                shift = rng.choice(["morning", "morning", "evening", "night"])
                # Rostered in Indian Standard Time, stored in UTC. Writing 08:00
                # straight into a UTC column would put a shift labelled "morning"
                # on screen at half past one in the afternoon, which is how a
                # timestamp bug gets read as a data problem by the one person
                # who knows what time they actually started.
                start_hour = {"morning": 8, "evening": 14, "night": 20}[shift]
                day = now - timedelta(days=days_ago)
                checked_in = day.replace(
                    hour=0, minute=0, second=0, microsecond=0
                ) + timedelta(hours=start_hour, minutes=rng.randint(0, 40)) - IST_OFFSET
                # A night shift nominally starting at 20:00 has not started
                # yet if it is two in the afternoon. Seeding it anyway would
                # put a check-in in the future, where it outranks every real
                # report made this morning.
                if checked_in > now:
                    continue
                # Today's shift may still be running; every earlier one closed.
                checked_out = (
                    None
                    if days_ago == 0 and rng.random() < 0.5
                    else checked_in + timedelta(hours=8, minutes=rng.randint(-25, 40))
                )
                _, _, km, ok, cell = _fix(fac, method, rng)
                footfall = 0 if is_gaming and rng.random() < 0.6 else rng.randint(8, 70)
                checkins.append((
                    fac["id"], ref, checked_in, checked_out, shift,
                    source, cell, method, km, ok, footfall,
                ))

                if rng.random() >= PING_CHANCE:
                    continue
                # Somewhere inside the shift, never at its edges.
                sent = checked_in + timedelta(
                    minutes=rng.randint(70, 380), seconds=rng.randint(0, 59)
                )
                if sent > now:
                    continue
                channel = "sms" if method != "none" else "ivr"
                pings.append(_ping(fac, ref, sent, channel, method, is_gaming, rng))

    return checkins, pings


def _fix(
    fac: dict, method: str, rng: random.Random
) -> tuple[float | None, float | None, float | None, bool | None, str | None]:
    """The location a channel actually supplies, and whether it clears."""
    if method == "gps":
        # Within the compound, give or take a GPS fix.
        lat = fac["lat"] + rng.gauss(0, 0.0006)
        lng = fac["lng"] + rng.gauss(0, 0.0006)
        km = round(math.dist((fac["lat"], fac["lng"]), (lat, lng)) * 111.0, 3)
        return lat, lng, km, km <= 0.25, None
    if method == "simulated":
        cell = f"404-{rng.randint(10, 99)}-{rng.randint(1000, 9999)}"
        km = round(rng.uniform(0.2, 2.6), 3)
        return None, None, km, km <= 2.0, cell
    # IVR carries the caller's number and nothing more. A check that could not
    # run is never written as one that passed.
    return None, None, None, None, None


def _ping(
    fac: dict,
    ref: str,
    sent: datetime,
    channel: str,
    method: str,
    is_gaming: bool,
    rng: random.Random,
) -> tuple:
    """One random re-verification, and the four things that can come of it."""
    # A centre marking everyone present regardless is also a centre where the
    # ping often finds nobody to answer it.
    if rng.random() < (0.45 if is_gaming else 0.08):
        return (fac["id"], ref, channel, sent, None, None, None, None, None, "no_reply")

    replied = sent + timedelta(minutes=rng.randint(1, 22))
    if method == "none":
        # Answered, but over a channel that places nobody anywhere.
        return (
            fac["id"], ref, channel, sent, replied, "none", None, None, None,
            "unlocatable",
        )
    _, _, km, ok, cell = _fix(fac, method, rng)
    return (
        fac["id"], ref, channel, sent, replied, method, cell, km, ok,
        "confirmed" if ok else "out_of_range",
    )


def build_movements(
    facilities: list[dict],
    rng: random.Random,
    quality: dict[str, float] | None = None,
    days: int = 45,
) -> list[tuple]:
    """Warehouse dispatches and their receipts — spec 26.3.

    Most consignments arrive on time and in full, because most do. The
    interesting rows are the minority: short deliveries, the occasional
    over-count, and batches nobody has confirmed. Those are what the ledger
    exists to surface, so the seed has to contain them or the movement tab
    looks like a table of nothing happening.

    `quality` scales how often a state's consignments go wrong. A weak silo
    does not get a low trust score handed to it — it gets consignments that
    genuinely go unconfirmed and arrive short, in these very rows. Every
    number the trust layer later shows is then derived from evidence an
    officer can open and read, which is the only kind of score worth putting
    in front of one.
    """
    quality = quality or {}
    rows: list[tuple] = []
    now = datetime.now(timezone.utc)
    stocked = [s for s in SKUS if not s.get("is_controlled")]
    counter = 0

    for fac in facilities:
        for _ in range(rng.randint(2, 6)):
            counter += 1
            sku = rng.choice(stocked)
            sent = now - timedelta(
                days=rng.uniform(1, days), hours=rng.uniform(0, 23)
            )
            # District warehouse, the way state supply chains are actually run.
            district_code = fac["district"].upper().replace(" ", "-")[:14].strip("-")
            source = f"WH-{fac['state_silo']}-{district_code}"
            qty = Decimal(str(round(sku["base"] * rng.uniform(8, 30))))
            window = timedelta(hours=rng.choice([48, 72, 72, 96]))
            expected = sent + window
            batch = f"B{sent:%y%m}-{counter:06d}"

            # How badly this state's supply paperwork is kept, this run.
            slip = quality.get(fac["state_silo"], 1.0)
            draw = rng.random()
            settled_late = expected < now
            age_days = (now - sent).days
            # Unconfirmed batches are recent ones. A consignment nobody has
            # acknowledged for six weeks would have been chased by phone long
            # before a dashboard noticed, so leaving those open would fill the
            # queue with clutter no officer would believe.
            if draw < min(0.5, 0.18 * slip) and settled_late and age_days <= 12:
                # Nobody has confirmed it and the window has passed.
                rows.append((batch, sku["code"], source, fac["id"], fac["state_silo"],
                             qty, sent, "warehouse", expected,
                             None, None, None, None, None, "in_transit"))
                continue
            if not settled_late:
                rows.append((batch, sku["code"], source, fac["id"], fac["state_silo"],
                             qty, sent, "warehouse", expected,
                             None, None, None, None, None, "in_transit"))
                continue

            got = qty
            status = "received"
            note = None
            if draw < min(0.35, 0.11 * slip):
                got = (qty * Decimal(str(round(rng.uniform(0.55, 0.92), 2)))).quantize(Decimal("1"))
                status = "short"
                note = rng.choice([
                    "Two cartons missing on arrival",
                    "Short supply noted on challan",
                    "Damaged in transit, quantity reduced",
                ])
            elif draw < min(0.4, 0.13 * slip):
                got = (qty * Decimal(str(round(rng.uniform(1.04, 1.15), 2)))).quantize(Decimal("1"))
                status = "over"
                note = "Received more than challan quantity"

            late_factor = 0.95 + 0.35 * (slip - 1.0) if slip > 1 else 0.95
            got_at = sent + window * rng.uniform(0.3, max(0.4, late_factor))
            rows.append((batch, sku["code"], source, fac["id"], fac["state_silo"],
                         qty, sent, "warehouse", expected,
                         got, got_at, rng.choice(["form", "sms", "ivr", "whatsapp"]),
                         f"seed:{fac['id']}", note, status))
    return rows


def build_bed_reports(
    facilities: list[dict], rng: random.Random, days: int = 21
) -> tuple[list[tuple], list[tuple]]:
    """Ward photos and the day's codes — spec 26.2.

    Returns (verification_code_rows, bed_report_rows). Most days a facility
    photographs the ward with the right code from the right place and the
    report verifies. The rest are the point of the exercise: a code from an
    earlier day, a photo taken kilometres away, a count the register disputes,
    and reports from channels that carry no location at all.
    """
    codes: list[tuple] = []
    reports: list[tuple] = []
    now = datetime.now(timezone.utc)
    alphabet = "ABCDEFGHJKLMNPQRTUVWXYZ2346789"

    def code_for(fac_id: str, day: date_cls) -> str:
        # Deterministic per facility-day so a reseed reproduces the same
        # history, but unpredictable from the outside.
        r = random.Random(f"{fac_id}:{day.isoformat()}:{DEFAULT_SEED}")
        return "".join(r.choice(alphabet) for _ in range(4))

    for fac in facilities:
        # Every PHC has a handful of inpatient beds and a CHC has a ward; only
        # a facility with no beds at all has nothing to photograph.
        if fac["beds_total"] < 4:
            continue
        today = now.date()
        codes.append((fac["id"], today, code_for(fac["id"], today), now, now))

        reporting_rate = rng.choice([0.9, 0.85, 0.95, 0.6])
        for d in range(days, 0, -1):
            day = (now - timedelta(days=d)).date()
            if rng.random() > reporting_rate:
                continue
            at = now - timedelta(days=d, hours=rng.uniform(-3, 3))
            codes.append((fac["id"], day, code_for(fac["id"], day), at, at))

            total = fac["beds_total"]
            occupied = max(0, min(total, int(total * rng.uniform(0.3, 0.85))))
            register = max(0, occupied + rng.randint(-2, 2))
            expected = code_for(fac["id"], day)
            draw = rng.random()

            code_read, code_ok = expected, True
            method = rng.choice(["gps", "gps", "gps", "cell_id", "none"])
            lat, lng = fac["lat"], fac["lng"]
            accuracy = {"gps": 12.0, "cell_id": 1400.0}.get(method)
            verification = "verified"
            register_ok_gap = 0

            if draw < 0.04:
                # Yesterday's photo, resubmitted.
                code_read, code_ok = code_for(fac["id"], day - timedelta(days=1)), False
                verification = "rejected"
            elif draw < 0.07:
                # Illegible code — a request for a better photo, not a finding.
                code_read, code_ok = None, None
                verification = "unverified"
            elif draw < 0.10:
                # Taken somewhere else entirely.
                method = "gps"
                lat, lng = fac["lat"] + rng.uniform(0.05, 0.2), fac["lng"] + rng.uniform(0.05, 0.2)
                accuracy = 15.0
                verification = "rejected"
            elif draw < 0.14:
                # The count and the register disagree.
                register_ok_gap = max(3, int(occupied * rng.uniform(0.4, 0.8)))
                register = max(0, occupied - register_ok_gap)

            if method == "none":
                lat = lng = accuracy = None
                # No location means one check could not run at all.
                if verification == "verified":
                    verification = "unverified"

            geo_km = None
            geo_ok = None
            if lat is not None:
                geo_km = round(
                    math.dist((fac["lat"], fac["lng"]), (lat, lng)) * 111.0, 3
                )
                geo_ok = geo_km <= (0.25 if method == "gps" else 2.0)

            confidence = round(rng.uniform(0.62, 0.95), 2)
            reports.append((
                fac["id"], "general", total, occupied, at, "photo",
                expected, code_read, code_ok,
                method, lat, lng, accuracy, geo_km, geo_ok,
                register, confidence, None,
                json.dumps({"model": "mock", "notes": "Seeded ward photo extraction."}),
                verification,
            ))
    return codes, reports


async def _copy(conn, table: str, columns: list[str], records: list[tuple]) -> None:
    """Bulk load via asyncpg COPY — orders of magnitude faster than INSERT."""
    if not records:
        return
    raw = await conn.get_raw_connection()
    await raw.driver_connection.copy_records_to_table(
        table, records=records, columns=columns
    )


COLLATERAL = (
    ("forecasts", "published forecasts — the map falls back to the burn rate until a "
                  "training run and federation/publish_forecast.py replace them"),
    ("federation_rounds", "recorded federated rounds"),
)


async def _warn_about_collateral(approved: bool) -> None:
    """Refuse to silently destroy work that is expensive to recreate."""
    losses: list[str] = []
    async with engine.begin() as conn:
        for table, description in COLLATERAL:
            exists = await conn.scalar(text("SELECT to_regclass(:t)"), {"t": table})
            if not exists:
                continue
            count = await conn.scalar(text(f"SELECT count(*) FROM {table}"))  # noqa: S608 - fixed names
            if count:
                losses.append(f"  {count:,} rows in {table}: {description}")
    if not losses:
        return
    print("reseeding will also destroy:")
    for line in losses:
        print(line)
    if approved:
        print("  proceeding: --yes was given")
        return
    raise SystemExit(
        "Refusing to reseed. Re-run with --yes if that is intended, or publish "
        "forecasts again afterwards with federation/publish_forecast.py."
    )


def build_contacts(facilities: list[dict]) -> list[tuple]:
    """One reporting handset and one supervisor per facility — spec 13.

    The numbers are never stored. Each is derived from the facility id, hashed
    exactly as an inbound message is hashed, and only the hash and a masked
    form are written. The demo can therefore offer a number to type without
    the platform ever having held one.
    """
    rows: list[tuple] = []
    now = datetime.now(timezone.utc)
    for fac in facilities:
        for role in ("reporter", "supervisor"):
            number = ingest.demo_number(fac["id"], role)
            rows.append(
                (
                    ingest.hash_phone(number),
                    fac["id"],
                    ingest.mask_phone(number),
                    role,
                    "en",
                    True,
                    now,
                    None,
                )
            )
    return rows


async def main() -> None:
    p = argparse.ArgumentParser(description="Seed SwasthSetu at national scale")
    p.add_argument("--days", type=int, default=35, help="history for every facility")
    p.add_argument("--focus-days", type=int, default=400, help="history for focus states")
    p.add_argument("--seed", type=int, default=DEFAULT_SEED)
    p.add_argument("--gaming-pct", type=float, default=0.08)
    p.add_argument("--supply-failure-pct", type=float, default=0.18)
    p.add_argument(
        "--yes",
        action="store_true",
        help="proceed even though reseeding destroys published forecasts and federation rounds",
    )
    p.add_argument(
        "--allow-production",
        action="store_true",
        help="required to run against a production database; it replaces all facility data",
    )
    args = p.parse_args()

    if settings.is_production and not args.allow_production:
        raise SystemExit(
            "Refusing to seed: ENVIRONMENT=production and this replaces every facility, "
            "stock reading and transfer. Pass --allow-production if that is intended."
        )

    rng = random.Random(args.seed)

    # Schema is owned by migrations, never by this script.
    async with engine.connect() as conn:
        migrated = await conn.scalar(text("SELECT to_regclass('public.alembic_version') IS NOT NULL"))
    if not migrated:
        raise SystemExit("Database has no schema yet. Run `alembic upgrade head` first.")

    # Reseeding takes more than facility data with it. forecasts has foreign
    # keys into facilities and skus, so CASCADE empties it silently, and the
    # dashboard then falls back to the burn rate with nothing on screen to say
    # why. Say what is about to be lost, and make the caller agree to it.
    await _warn_about_collateral(args.yes)

    # TRUNCATE, never DELETE: no per-row FK checks, and it resets sequences.
    # The users table is deliberately not listed and has no foreign keys into
    # these tables, so reseeding facility data never deletes an account.
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "TRUNCATE facility_sku_state, stock_readings, bed_status, "
                "medicine_movements, verification_codes, bed_reports, "
                "facility_trust, staff_checkins, staff_verifications, approvals, "
                "transfers, trust_flags, "
                "outbreak_events, route_matrix_cache, outbound_messages, "
                "impact_ledger, federation_rounds, events, facility_contacts, facilities, skus "
                "RESTART IDENTITY CASCADE"
            )
        )
    print("existing facility data cleared")

    facilities = build_facilities(rng)
    by_code = {s.code: s for s in INDIA_STATES}
    ids = [f["id"] for f in facilities]
    # Reporting discipline is not uniform across a country, and a federated
    # model that weights silos by data confidence is only interesting if the
    # silos genuinely differ in how reliable their numbers are. So one
    # deep-history state is drawn to report noticeably worse than the others,
    # and one noticeably better.
    #
    # Which state gets which is drawn afresh every run and printed below. It is
    # a property of this synthetic generator and says nothing whatever about
    # any real state's health administration.
    deep = [s.code for s in INDIA_STATES if "focus" in s.tags]
    rng.shuffle(deep)
    quality = {deep[0]: 3.0, deep[-1]: 0.4} if len(deep) >= 2 else {}
    gaming = set(
        rng.choices(
            ids,
            weights=[quality.get(f["state_silo"], 1.0) for f in facilities],
            k=max(1, int(len(ids) * args.gaming_pct)),
        )
    )
    failing = set(rng.sample(ids, k=max(1, int(len(ids) * args.supply_failure_pct))))

    # The only place that knows which facilities were deliberately made
    # dishonest records it for the evaluation harness. Written here rather than
    # at the end so a half-finished run cannot leave a ground truth describing
    # data that was never committed; every run rewrites it from scratch.
    truth_path = groundtruth.write(
        None, seed=args.seed, facility_ids=ids, gaming=gaming, supply_failure=failing
    )

    async with SessionLocal() as session:
        await session.execute(
            insert(Sku),
            [
                {
                    "code": s["code"], "name": s["name"], "unit": s["unit"],
                    "aliases": s["aliases"],
                    "is_controlled": s.get("is_controlled", False),
                    "cold_chain": s.get("cold_chain", False),
                }
                for s in SKUS
            ],
        )
        await session.execute(insert(Facility), facilities)
        await session.commit()
        print(f"seeded {len(facilities)} facilities across {len(INDIA_STATES)} states/UTs")

    total_readings = 0
    async with engine.begin() as conn:
        batch_r: list[tuple] = []
        batch_b: list[tuple] = []
        snap_rows: list[dict] = []

        for idx, fac in enumerate(facilities, start=1):
            state = by_code[fac["state_silo"]]
            days = args.focus_days if "focus" in state.tags else args.days
            r, s, b = simulate_facility(
                fac, state, days, rng,
                is_gaming=fac["id"] in gaming,
                supply_failure=fac["id"] in failing,
            )
            batch_r.extend(r)
            batch_b.extend(b)
            snap_rows.extend(s)
            total_readings += len(r)

            if len(batch_r) >= 200_000:
                await _copy(conn, "stock_readings",
                            ["facility_id", "sku_code", "qty_on_hand", "reported_at",
                             "source", "footfall_same_day", "confidence"], batch_r)
                batch_r = []
            if idx % 500 == 0:
                print(f"  {idx}/{len(facilities)} simulated ({total_readings:,} readings)")

        await _copy(conn, "stock_readings",
                    ["facility_id", "sku_code", "qty_on_hand", "reported_at",
                     "source", "footfall_same_day", "confidence"], batch_r)
        await _copy(conn, "bed_status",
                    ["facility_id", "beds_occupied", "recorded_at"], batch_b)
        checkin_rows, ping_rows = build_checkins(facilities, rng, gaming)
        await _copy(
            conn, "staff_checkins",
            ["facility_id", "staff_ref", "checked_in_at", "checked_out_at", "shift",
             "source", "cell_id", "loc_method", "geofence_km", "geofence_ok",
             "footfall_same_period"],
            checkin_rows,
        )
        await _copy(
            conn, "staff_verifications",
            ["facility_id", "staff_ref", "channel", "sent_at", "responded_at",
             "loc_method", "cell_id", "geofence_km", "geofence_ok", "outcome"],
            ping_rows,
        )
        code_rows, bed_report_rows = build_bed_reports(facilities, rng)
        await _copy(
            conn, "verification_codes",
            ["facility_id", "for_date", "code", "issued_at", "delivered_at"],
            code_rows,
        )
        await _copy(
            conn, "bed_reports",
            ["facility_id", "ward", "beds_total", "beds_occupied", "reported_at",
             "source", "code_expected", "code_read", "code_ok", "loc_method",
             "loc_lat", "loc_lng", "loc_accuracy_m", "geofence_km", "geofence_ok",
             "register_admissions", "model_confidence", "media_ref", "raw_payload",
             "verification"],
            bed_report_rows,
        )
        movement_rows = build_movements(facilities, rng, quality)
        await _copy(
            conn, "medicine_movements",
            ["batch_id", "sku_code", "from_ref", "to_facility", "state_silo",
             "qty_dispatched", "dispatched_at", "dispatch_source", "expected_by",
             "qty_received", "received_at", "received_via", "received_by_ref",
             "note", "status"],
            movement_rows,
        )
        await _copy(
            conn, "facility_contacts",
            ["phone_hash", "facility_id", "masked", "role", "language",
             "is_active", "registered_at", "last_seen_at"],
            build_contacts(facilities),
        )
        await _copy(
            conn, "facility_sku_state",
            ["facility_id", "sku_code", "qty_on_hand", "daily_burn_rate",
             "days_of_stock", "status", "last_reported_at", "last_source",
             "last_confidence"],
            [
                (r["facility_id"], r["sku_code"], r["qty_on_hand"],
                 r["daily_burn_rate"], r["days_of_stock"], r["status"],
                 r["last_reported_at"], r["last_source"], r["last_confidence"])
                for r in snap_rows
            ],
        )

    focus = [s.name for s in INDIA_STATES if "focus" in s.tags]
    print(
        f"done: {len(facilities):,} facilities "
        f"({TOTAL_SEEDED_FACILITIES:,} allocated), {total_readings:,} readings, "
        f"{len(snap_rows):,} snapshot rows\n"
        f"  rng seed          : {args.seed}  (recorded for reproducible replay)\n"
        f"  history           : {args.days}d everywhere, {args.focus_days}d in {focus}\n"
        f"  gaming facilities : {len(gaming)} (trust-layer ground truth)\n"
        f"  ground truth      : {truth_path.name} (read only by the eval harness)\n"
        f"  reporting quality : weakest {deep[0]}, strongest {deep[-1]} — drawn at\n"
        f"                      random this run; synthetic, and not a claim about\n"
        f"                      either state\n"
        f"  supply failures   : {len(failing)} (these produce the map's reds)\n"
        f"  outbreak cluster  : {OUTBREAK_DISTRICT}, ~{OUTBREAK_DAYS_AGO} days ago"
    )


if __name__ == "__main__":
    asyncio.run(main())
