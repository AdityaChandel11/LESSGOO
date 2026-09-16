"""Live updates by polling a durable event log.

Browsers ask "what changed after event N?" every few seconds. This replaced a
server-sent-event stream for two reasons that matter in real hosting:

  * Hosting proxies cap request duration (Firebase Hosting's rewrite to Cloud
    Run is 60 seconds). A short poll never runs into that limit.
  * The log lives in Postgres, so any number of server instances can answer a
    poll. An in-memory stream only reaches browsers connected to the instance
    that produced the event.

The trade is latency: an update appears within one polling interval rather
than instantly. For stock levels that change a few times a day, that is the
right trade.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Event

READING_COMMITTED = "reading.committed"
STATUS_CHANGED = "status.changed"
TRANSFER_PROPOSED = "transfer.proposed"
TRANSFER_DECIDED = "transfer.decided"
FEDERATION_ROUND = "federation.round"
TRUST_FLAGGED = "trust.flagged"
OUTBREAK_SIMULATED = "outbreak.simulated"
MOVEMENT_DISPATCHED = "movement.dispatched"
MOVEMENT_RECEIVED = "movement.received"
BED_REPORTED = "bed.reported"
STAFF_CHECKIN = "staff.checkin"

RETENTION = timedelta(days=2)
# Prune on roughly one insert in this many, so the log never needs a
# separate scheduled job.
PRUNE_EVERY = 500
MAX_BATCH = 200


async def record(
    session: AsyncSession, kind: str, data: dict[str, Any], *, state_silo: str | None = None
) -> int:
    """Append an event and commit. Returns its id."""
    event = Event(kind=kind, payload=data, state_silo=state_silo)
    session.add(event)
    await session.flush()
    if event.id % PRUNE_EVERY == 0:
        await session.execute(
            delete(Event).where(Event.created_at < datetime.now(timezone.utc) - RETENTION)
        )
    await session.commit()
    return event.id


async def since(session: AsyncSession, after: int | None) -> dict[str, Any]:
    """Events newer than `after`, oldest first.

    With no cursor the caller is just starting, so it receives only the current
    cursor rather than a replay of history. If it has fallen so far behind that
    a single batch cannot catch it up, it is told to reload instead.
    """
    server_time = datetime.now(timezone.utc)
    latest = await session.scalar(select(func.max(Event.id))) or 0

    if after is None or after > latest:
        return {"cursor": latest, "events": [], "reset": after is not None, "server_time": server_time}

    rows = (
        await session.execute(
            select(Event).where(Event.id > after).order_by(Event.id).limit(MAX_BATCH + 1)
        )
    ).scalars().all()

    if len(rows) > MAX_BATCH:
        return {"cursor": latest, "events": [], "reset": True, "server_time": server_time}

    return {
        "cursor": rows[-1].id if rows else after,
        "reset": False,
        "server_time": server_time,
        "events": [
            {
                "id": e.id,
                "kind": e.kind,
                "created_at": e.created_at,
                "data": e.payload,
            }
            for e in rows
        ],
    }
