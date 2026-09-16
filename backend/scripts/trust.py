"""Refresh the materialised copy of every facility's confidence score.

    python -m scripts.trust

The score itself is never read from here. Every screen that shows a score —
the facility drawer, the audit queue — and the federated trainer compute it
live from the ledger, the ward photos and the check-ins at the moment they
ask, using `trust.compute()`.

What this job maintains is the bulk copy the national map needs: painting
thousands of facilities on every pan cannot re-derive six signals per
facility, so `facility_sku_state` uses the stored score to widen warning
thresholds. It is the same function and the same query as the live path —
a materialisation, never a second calculation that could disagree.

Runs nightly as a Cloud Run job on the same image. A missed run costs the map
some freshness; it can never make the map and the drawer tell different
stories, because both come from this one query.
"""

from __future__ import annotations

import asyncio
from collections import Counter

from app import trust
from app.db import SessionLocal


async def main() -> None:
    async with SessionLocal() as session:
        scores = await trust.compute(session)
        await trust.store(session, scores)

    bands = Counter(s.band for s in scores)
    print(f"scored {len(scores):,} facilities")
    for band in (trust.GOOD, trust.WATCH, trust.AUDIT):
        print(f"  {band:<6} {bands[band]:>6,}")
    worst = sorted(scores, key=lambda s: s.score)[:5]
    if worst:
        print("\nlowest confidence:")
        for s in worst:
            top = s.components[0] if s.components else None
            print(f"  {s.facility_id}  {s.score:.2f}  {top.reason if top else ''}")


if __name__ == "__main__":
    asyncio.run(main())
