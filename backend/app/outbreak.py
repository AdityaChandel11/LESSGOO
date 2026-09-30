"""Outbreak pre-positioning — spec v3 §12.5, fix list #41.

    "Outbreak pre-positioning — a multiplier into 12.3, not a new subsystem …
     same solver, no new code … Its output still funnels through the same
     human-approval gate."

An active outbreak — declared by an officer, or a recent IDSP report row once
#42/#57 write them — raises the expected daily use of the medicines its
disease drives, in its district, for `outbreak_ttl_days` (the spec's
ttl_days=14). The redistribution solver then sees shorter cover there and
proposes pre-positioning trips, which a person approves like any other.

The size of the rise has to be honest, so it comes from one of two places:

  observed    the district's own use of that medicine over the last 14 days
              against the 14 before, when its readings show a rise of at least
              `outbreak_min_rise`;
  assumption  otherwise, the expected surge the declaring officer typed,
              labelled as that officer's assumption.

With neither, nothing is multiplied and the screen says so. Which medicines a
disease drives comes from `idsp.DISEASE_MEDICINES`; how much comes only from
the two sources above — never from a random generator or a table of guesses.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from . import idsp, services
from .auth import Principal, can_plan_state, demo_may_write, demo_sandbox_refusal
from .config import settings
from .models import Facility, FacilitySkuState, OutbreakEvent, Sku, StockReading

if TYPE_CHECKING:  # redistribution imports this module to apply surges
    from .redistribution import StockNode

OBSERVED = "observed"
ASSUMPTION = "assumption"
# At the surge rate the shelf would already be empty: a count, not a date.
OVERDUE = "overdue"


def medicines_for(disease: str) -> list[str]:
    return list(idsp.DISEASE_MEDICINES.get(disease, []))


def surge_from(*, observed: float | None, surge_pct: float | None) -> tuple[float, str] | None:
    """(multiplier, basis): data first, then the officer's stated expectation."""
    if observed is not None and observed >= 1 + settings.outbreak_min_rise:
        return round(observed, 3), OBSERVED
    if surge_pct is not None and surge_pct > 0:
        return round(1 + surge_pct / 100.0, 3), ASSUMPTION
    return None


def consumption_ratio(
    series_by_facility: dict[str, list[tuple[datetime, float, str | None]]], now: datetime
) -> float | None:
    """District use over the last window against the window before it.

    Each facility's use in each window is the same burn rule the cards use
    (services._burn_rate: declines only, transfers excluded), summed across
    the district's facilities.
    """
    window = timedelta(days=settings.outbreak_window_days)
    split = now - window
    start = split - window
    recent_total = prior_total = 0.0
    for series in series_by_facility.values():
        prior = [p for p in series if start <= p[0] <= split]
        recent = [p for p in series if split <= p[0] <= now]
        prior_rate = services._burn_rate(prior)
        recent_rate = services._burn_rate(recent)
        if prior_rate is None or recent_rate is None:
            continue
        prior_total += prior_rate
        recent_total += recent_rate
    if prior_total <= 0:
        return None
    return recent_total / prior_total


def apply_surges(
    nodes_by_sku: dict[str, list[StockNode]], surges: dict[tuple[str, str], float]
) -> dict[str, list[StockNode]]:
    """The §12.5 temporary forecast: burn × multiplier where an outbreak is."""
    out: dict[str, list[StockNode]] = {}
    for sku, nodes in nodes_by_sku.items():
        out[sku] = []
        for n in nodes:
            m = surges.get((n.facility_id, sku))
            if m and n.burn > 0:
                burn = n.burn * m
                n = replace(n, burn=burn, days=n.qty / burn)
            out[sku].append(n)
    return out


def warning_for(
    *, qty: float, burn: float, last_reported_at: datetime, multiplier: float, now: datetime
) -> tuple[date, date] | str | None:
    """(run-out at the surge rate, run-out without the outbreak), both counted
    from the last count, when the surge run-out falls inside the outbreak's
    horizon. OVERDUE when it has already passed — the shelf may be empty and
    the instruction is to count it (fix #26's rule). None otherwise: a centre
    with months of cover is not a warning."""
    if burn <= 0:
        return None
    normal = last_reported_at + timedelta(days=qty / burn)
    surged = last_reported_at + timedelta(days=qty / (burn * multiplier))
    if surged <= now:
        return OVERDUE
    if surged > now + timedelta(days=settings.outbreak_ttl_days):
        return None
    return surged.date(), normal.date()


def _day(d: date) -> str:
    return "{0} {1}".format(d.day, d.strftime("%b"))


def warning_line(district: str, sku_name: str, surged: date, normal: date) -> str:
    return "{0} · {1} · runs out {2} ({3} without the outbreak)".format(
        district, sku_name, _day(surged), _day(normal)
    )


def may_declare(p: Principal, state: str, district: str) -> str | None:
    """None when allowed, else the refusal. Declaring re-plans the state's
    medicines, so it takes the same right as a plan, and a public demo account
    stays inside the sandbox."""
    if not can_plan_state(p, state):
        return "Only this state's officers can declare an outbreak in it"
    if not demo_may_write(p, state=state, district=district):
        return demo_sandbox_refusal()
    return None


# ================================================================ database ===


def is_active(row: OutbreakEvent, now: datetime) -> bool:
    return row.ended_at is None and row.expires_at is not None and row.expires_at > now


async def active(
    session: AsyncSession, now: datetime, state: str | None = None
) -> list[OutbreakEvent]:
    stmt = select(OutbreakEvent).where(
        OutbreakEvent.ended_at.is_(None), OutbreakEvent.expires_at > now
    )
    if state:
        stmt = stmt.where(OutbreakEvent.state_silo == state)
    return list((await session.execute(stmt.order_by(OutbreakEvent.triggered_at.desc()))).scalars())


async def district_ids(session: AsyncSession, state: str, district: str) -> list[str]:
    return list(
        (
            await session.execute(
                select(Facility.id).where(
                    Facility.state_silo == state, Facility.district == district
                )
            )
        ).scalars()
    )


async def observed_ratio(
    session: AsyncSession, ids: list[str], sku: str, now: datetime
) -> float | None:
    """Bounded by the district's facility ids, one medicine and 28 days."""
    if not ids:
        return None
    since = now - timedelta(days=2 * settings.outbreak_window_days)
    rows = await session.execute(
        select(StockReading.facility_id, StockReading.reported_at, StockReading.qty_on_hand,
               StockReading.source)
        .where(
            StockReading.facility_id.in_(ids),
            StockReading.sku_code == sku,
            StockReading.reported_at >= since,
            StockReading.superseded_by.is_(None),
        )
        .order_by(StockReading.facility_id, StockReading.reported_at)
    )
    series: dict[str, list[tuple]] = defaultdict(list)
    for fid, at, qty, source in rows.all():
        series[fid].append((at, float(qty), source))
    return consumption_ratio(series, now)


@dataclass(frozen=True)
class Surge:
    outbreak_id: int
    disease: str
    district: str
    sku_code: str
    sku_name: str
    multiplier: float
    basis: str
    detail: str

    def rationale(self, days_without: float | None) -> dict:
        return {
            "outbreak_id": self.outbreak_id,
            "disease": self.disease,
            "district": self.district,
            "multiplier": self.multiplier,
            "basis": self.basis,
            "detail": self.detail,
            "recipient_days_without_outbreak": days_without,
        }


def _detail(
    basis: str, multiplier: float, sku_name: str, district: str, declared_by: str | None
) -> str:
    pct = round((multiplier - 1) * 100)
    if basis == OBSERVED:
        return (
            f"{sku_name} use in {district} up {pct}% over the last "
            f"{settings.outbreak_window_days} days against the {settings.outbreak_window_days} "
            "before (observed in this district's readings)"
        )
    return (
        f"expected {sku_name} surge of {pct}% set by {declared_by or 'the declaring officer'} "
        "— an assumption, not a measurement"
    )


@dataclass
class OutbreakView:
    row: OutbreakEvent
    ids: list[str]
    medicines: list[dict]
    surges: list[Surge]
    # Centres whose shelf would already be empty at the surge rate.
    overdue: int = 0


async def evaluate(session: AsyncSession, row: OutbreakEvent, now: datetime) -> OutbreakView:
    """One outbreak's surge per medicine, computed now from the readings."""
    ids = await district_ids(session, row.state_silo, row.district)
    names = {s.code: s.name for s in (await session.execute(select(Sku))).scalars()}
    medicines: list[dict] = []
    surges: list[Surge] = []
    for sku in medicines_for(row.disease_category or ""):
        if sku not in names:
            continue
        ratio = await observed_ratio(session, ids, sku, now)
        got = surge_from(
            observed=ratio, surge_pct=float(row.surge_pct) if row.surge_pct is not None else None
        )
        entry = {
            "sku_code": sku,
            "sku_name": names[sku],
            "observed_ratio": round(ratio, 3) if ratio is not None else None,
            "multiplier": got[0] if got else None,
            "basis": got[1] if got else None,
            "detail": (
                _detail(got[1], got[0], names[sku], row.district, row.declared_by)
                if got
                else f"No rise in {names[sku]} use observed in {row.district}, and no expected "
                "surge set — nothing is pre-positioned for it."
            ),
        }
        medicines.append(entry)
        if got:
            surges.append(
                Surge(
                    outbreak_id=row.id, disease=row.disease_category or "", district=row.district,
                    sku_code=sku, sku_name=names[sku], multiplier=got[0], basis=got[1],
                    detail=entry["detail"],
                )
            )
    return OutbreakView(row=row, ids=ids, medicines=medicines, surges=surges)


async def surges_for_state(
    session: AsyncSession, state: str, now: datetime
) -> dict[tuple[str, str], Surge]:
    """(facility, medicine) → the surge the solver applies. Where two active
    outbreaks drive the same medicine in one district, the larger one."""
    out: dict[tuple[str, str], Surge] = {}
    for row in await active(session, now, state):
        view = await evaluate(session, row, now)
        for s in view.surges:
            for fid in view.ids:
                key = (fid, s.sku_code)
                if key not in out or out[key].multiplier < s.multiplier:
                    out[key] = s
    return out


async def warnings(session: AsyncSession, view: OutbreakView, now: datetime) -> list[dict]:
    """The district's centres that run out inside the horizon at the surge
    rate. Centres already past their run-out at that rate are counted on the
    view (`view.overdue`), not listed with a date in the past."""
    view.overdue = 0
    if not view.surges or not view.ids:
        return []
    by_sku = {s.sku_code: s for s in view.surges}
    rows = await session.execute(
        select(
            FacilitySkuState.facility_id, Facility.name, FacilitySkuState.sku_code,
            FacilitySkuState.qty_on_hand, FacilitySkuState.daily_burn_rate,
            FacilitySkuState.last_reported_at,
        )
        .join(Facility, Facility.id == FacilitySkuState.facility_id)
        .where(
            FacilitySkuState.facility_id.in_(view.ids),
            FacilitySkuState.sku_code.in_(list(by_sku)),
        )
    )
    out: list[dict] = []
    for fid, name, sku, qty, burn, at in rows.all():
        s = by_sku[sku]
        if burn is None or at is None:
            continue
        got = warning_for(
            qty=float(qty), burn=float(burn), last_reported_at=at, multiplier=s.multiplier, now=now
        )
        if got is None:
            continue
        if got == OVERDUE:
            view.overdue += 1
            continue
        surged, normal = got
        out.append({
            "outbreak_id": view.row.id,
            "facility_id": fid,
            "facility_name": name,
            "district": view.row.district,
            "sku_code": sku,
            "sku_name": s.sku_name,
            "runs_out_on": surged,
            "runs_out_without": normal,
            "basis": s.basis,
            "line": warning_line(name, s.sku_name, surged, normal),
        })
    out.sort(key=lambda w: (w["runs_out_on"], w["facility_name"]))
    return out
