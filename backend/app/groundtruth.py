"""What the synthetic generator knows and the product must never read.

The trust layer's recall can only be measured against the truth of which
facilities were *deliberately* made to misreport. Only `scripts/seed.py` knows
that, so it writes the answer here, once, at the end of a seed run.

This file is read by exactly two things: `app/evaluation.py` and the checks
under `backend/checks/`. Nothing that serves a request may import it. The score
on screen has to stand on the live tables alone, or the measurement below is
meaningless.

It is a file rather than a table on purpose. It is not health data, no endpoint
should be able to reach it, and it has to survive being compared against a
database that may have been reseeded since — which is what `mismatches()` is
for: a stale ground truth is worse than none, because it silently reports a
worse number than the layer deserves.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Facility

BACKEND_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PATH = BACKEND_ROOT / "eval_reports" / "ground-truth.json"

GAMING = "gaming"
SUPPLY_FAILURE = "supply_failure"


@dataclass(frozen=True)
class GroundTruth:
    """The generator's own record of what it made dishonest, and when."""

    seed: int
    written_at: datetime
    facility_count: int
    gaming: frozenset[str]
    supply_failure: frozenset[str]
    path: Path

    @property
    def dishonest(self) -> frozenset[str]:
        """Facilities the trust layer is supposed to catch.

        Supply failures are deliberately *not* included: a facility that runs
        out because its consignment never arrived is reporting honestly, and
        flagging it would be the layer's mistake, not its success.
        """
        return self.gaming


def write(
    path: Path | None,
    *,
    seed: int,
    facility_ids: list[str],
    gaming: set[str],
    supply_failure: set[str],
) -> Path:
    target = Path(path or DEFAULT_PATH)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(
            {
                "generator": "scripts/seed.py",
                "seed": seed,
                "written_at": datetime.now(timezone.utc).isoformat(),
                "facility_count": len(facility_ids),
                "gaming": sorted(gaming),
                "supply_failure": sorted(supply_failure),
                "note": (
                    "Ground truth for the evaluation harness only. Nothing that "
                    "serves a request may read this file."
                ),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return target


def read(path: Path | None = None) -> GroundTruth | None:
    target = Path(path or DEFAULT_PATH)
    if not target.is_file():
        return None
    body = json.loads(target.read_text(encoding="utf-8"))
    return GroundTruth(
        seed=int(body["seed"]),
        written_at=datetime.fromisoformat(body["written_at"]),
        facility_count=int(body["facility_count"]),
        gaming=frozenset(body.get("gaming", ())),
        supply_failure=frozenset(body.get("supply_failure", ())),
        path=target,
    )


async def mismatches(session: AsyncSession, truth: GroundTruth) -> list[str]:
    """Reasons this ground truth does not describe the database in front of us."""
    problems: list[str] = []
    count = (await session.execute(select(func.count()).select_from(Facility))).scalar_one()
    if count != truth.facility_count:
        problems.append(
            f"ground truth counted {truth.facility_count:,} facilities, the database has {count:,}"
        )
    named = set(truth.gaming | truth.supply_failure)
    if named:
        present = set(
            (
                await session.execute(select(Facility.id).where(Facility.id.in_(list(named))))
            )
            .scalars()
            .all()
        )
        missing = named - present
        if missing:
            problems.append(
                f"{len(missing)} facilities named in the ground truth are not in the database "
                f"(e.g. {sorted(missing)[0]})"
            )
    return problems
