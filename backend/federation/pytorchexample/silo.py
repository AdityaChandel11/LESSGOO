"""The live silo: one state's rows, read fresh from the platform's database.

There is exactly one medicine-stock database in this system. The dashboard's
movement tab, the trust score beside a facility, and the training windows below
all read these same tables. Nothing here is a copy, an export, or a fixture —
if a delivery is confirmed in the browser while a round is running, the next
round trains on it.

The federated boundary is the query, not the file. Each SuperNode is configured
with one state and every statement it issues is filtered to that state; a node
has no statement that could return another state's rows. In a real deployment
each state runs its own database and its own node, and only the connection
string changes.
"""

from __future__ import annotations

import math
import os
from collections import defaultdict
from datetime import datetime

import numpy as np
import psycopg

SEQ_LEN = 28      # days of history the model sees
HORIZON = 7       # days ahead it must forecast
STRIDE = 3        # windows every third day, so samples are not near-duplicates
FACILITY_FRACTION = 0.25
MIN_FACILITIES = 20
VAL_FRACTION = 0.2
MIN_DAILY_USE = 1.0
TRUST_WINDOW_DAYS = 14

SKUS = ("ORS", "PARA500", "AMOX", "IRONFA", "IVFLUID", "ZINC")

FESTIVAL_DAYS = {
    (10, 20), (10, 21), (10, 22), (3, 8), (3, 9), (8, 15), (1, 26),
}

def dsn() -> str:
    """This node's connection to its own state's data.

    Required, with no default: a connection string carrying a password does not
    belong in source, and a node that silently falls back to some local
    database is a node that might train on the wrong one.
    """
    raw = os.environ.get("SWASTHSETU_DATABASE_URL")
    if not raw:
        raise RuntimeError(
            "SWASTHSETU_DATABASE_URL is not set. Each SuperNode needs the "
            "connection string for the state it serves, for example:\n"
            "    SWASTHSETU_DATABASE_URL=postgresql://USER:PASSWORD@HOST:5432/phc"
        )
    # psycopg speaks the plain URL; the platform's own is SQLAlchemy-flavoured.
    return raw.replace("postgresql+asyncpg://", "postgresql://")


def calendar_features(day: datetime) -> tuple[float, float, float, float]:
    doy = day.timetuple().tm_yday
    angle = 2 * math.pi * doy / 365.25
    return (
        math.sin(angle),
        math.cos(angle),
        1.0 if day.month in (6, 7, 8, 9) else 0.0,
        1.0 if (day.month, day.day) in FESTIVAL_DAYS else 0.0,
    )


def _consumption(series: list[tuple]) -> tuple[list[datetime], np.ndarray]:
    """Daily use from stock levels: a drop is consumption, a rise is a delivery.

    Readings whose source is 'transfer' are stock arriving, never stock used —
    counting those as demand would teach the model that deliveries cause
    illness.
    """
    days: list[datetime] = []
    used: list[float] = []
    for i in range(1, len(series)):
        at, qty, source = series[i]
        if source == "transfer":
            continue
        days.append(at)
        used.append(max(0.0, float(series[i - 1][1]) - float(qty)))
    return days, np.asarray(used, dtype=np.float32)


def _windows(days: list[datetime], used: np.ndarray) -> list[tuple]:
    out = []
    for end in range(SEQ_LEN, len(used) - HORIZON, STRIDE):
        seq = used[end - SEQ_LEN : end]
        scale = float(seq.mean())
        if scale < MIN_DAILY_USE:
            continue
        out.append(
            (
                seq / scale,
                np.asarray(calendar_features(days[end]), dtype=np.float32),
                float(used[end : end + HORIZON].mean()) / scale,
            )
        )
    return out


def load_state(state: str) -> dict:
    """Read this state's rows now, and build training windows from them."""
    with psycopg.connect(dsn(), connect_timeout=10) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM facilities WHERE state_silo = %s", (state,)
        )
        total = cur.fetchone()[0]
        take = max(MIN_FACILITIES, int(total * FACILITY_FRACTION))
        cur.execute(
            "SELECT id FROM facilities WHERE state_silo = %s ORDER BY id LIMIT %s",
            (state, take),
        )
        facility_ids = [r[0] for r in cur.fetchall()]

        cur.execute(
            """
            SELECT facility_id, sku_code, reported_at, qty_on_hand, source
            FROM stock_readings
            WHERE facility_id = ANY(%s) AND sku_code = ANY(%s)
            ORDER BY facility_id, sku_code, reported_at
            """,
            (facility_ids, list(SKUS)),
        )
        series: dict[tuple[str, str], list[tuple]] = defaultdict(list)
        for facility_id, sku, at, qty, source in cur.fetchall():
            series[(facility_id, sku)].append((at, qty, source))

    samples: list[tuple] = []
    for key in sorted(series):
        days, used = _consumption(series[key])
        samples.extend(_windows(days, used))
    if not samples:
        raise RuntimeError(
            f"No training windows for {state}. Is the platform's database seeded?"
        )

    x_seq = np.stack([s[0] for s in samples]).astype(np.float32)
    x_cal = np.stack([s[1] for s in samples]).astype(np.float32)
    y = np.asarray([s[2] for s in samples], dtype=np.float32)

    # Split by position, not at random: a forecaster is judged on weeks it has
    # not seen yet, and a shuffled split would leak next week into this one.
    cut = int(len(y) * (1 - VAL_FRACTION))
    return {
        "state": state,
        "facilities": len(facility_ids),
        "x_seq_train": x_seq[:cut], "x_cal_train": x_cal[:cut], "y_train": y[:cut],
        "x_seq_val": x_seq[cut:], "x_cal_val": x_cal[cut:], "y_val": y[cut:],
    }


def live_trust(state: str) -> tuple[float, dict]:
    """This state's data confidence, computed from the ledger right now.

    The same rows the movement tab shows and the same rule the dashboard's
    trust panel applies: consignments that went unconfirmed past their window,
    and consignments that arrived short. Nothing is read from a stored score,
    so a silo whose paperwork degrades this round contributes less to the
    national model this round.
    """
    with psycopg.connect(dsn(), connect_timeout=10) as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT count(*)                                              AS total,
                   count(*) FILTER (WHERE m.status = 'in_transit'
                                      AND m.expected_by < now())         AS overdue,
                   count(*) FILTER (WHERE m.status = 'short')            AS short
            FROM medicine_movements m
            WHERE m.state_silo = %s
              AND m.dispatched_at >= now() - interval '60 days'
            """,
            (state,),
        )
        total, overdue, short = cur.fetchone()

    if not total:
        return 1.0, {"total": 0, "overdue": 0, "short": 0}
    # Mirrors app/trust.py's receipt_discipline rule: an unconfirmed batch
    # weighs more than a short one, because a short delivery was at least
    # reported.
    penalty = min(1.0, (overdue + 0.5 * short) / total / 0.4)
    return round(max(0.0, 1.0 - penalty), 3), {
        "total": total,
        "overdue": overdue,
        "short": short,
        "flagged_pct": round(100 * (overdue + short) / total, 1),
    }
