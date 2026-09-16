"""Run the eval harness and record the result — spec 19.2.

    python -m scripts.run_eval
    python -m scripts.run_eval --state MH --impact-days 120

Run this before every rehearsal. These are the numbers quoted on stage, and a
number nobody has recomputed since the data changed is not a measurement any
more — it is a memory.

Everything here is a simulation over synthetic data with a recorded seed. Say
so out loud: a computed, honestly-labelled figure survives a hostile question
in a way a confident claim does not.
"""

from __future__ import annotations

import argparse
import asyncio
import json

from app import evaluation
from app.db import SessionLocal
from app.models import EvalReport

DEFAULT_SEED = 20260915


def show(title: str, body: dict) -> None:
    print(f"\n{title}")
    print("-" * len(title))
    if not body.get("available", True):
        print(f"  unavailable: {body.get('reason')}")
        return
    for key, value in body.items():
        if key in ("available", "note", "examples", "per_silo", "rules_checked", "baselines"):
            continue
        print(f"  {key.replace('_', ' '):<34} {value}")
    if body.get("baselines"):
        print("  baselines it must beat:")
        for name, value in body["baselines"].items():
            print(f"    {name.replace('_', ' '):<32} {value}")
    for line in body.get("rules_checked", []):
        print(f"    rule: {line}")
    for line in body.get("examples", []):
        print(f"    violation: {line}")
    if body.get("note"):
        print(f"  note: {body['note']}")


async def main() -> None:
    p = argparse.ArgumentParser(description="Run the SwasthSetu eval harness")
    p.add_argument("--state", default="MH", help="state for solver and impact metrics")
    p.add_argument("--impact-days", type=int, default=120)
    p.add_argument("--seed", type=int, default=DEFAULT_SEED)
    p.add_argument("--json", action="store_true", help="print the raw report")
    args = p.parse_args()

    async with SessionLocal() as session:
        sections = {
            "trust": await evaluation.trust_metrics(session),
            "redistribution": await evaluation.redistribution_metrics(session, args.state),
            "federation": await evaluation.federation_metrics(session),
            "impact": await evaluation.impact_replay(session, args.state, args.impact_days),
        }
        session.add(
            EvalReport(
                dataset_seed=args.seed,
                sections=sections,
                notes="Simulation over synthetic data; seed recorded for replay.",
            )
        )
        await session.commit()

    if args.json:
        print(json.dumps(sections, indent=2, default=str))
        return

    print("SwasthSetu eval harness — simulation over synthetic data")
    show("Trust layer, against the generator's own ground truth", sections["trust"])
    show("Redistribution, against its own safety rules", sections["redistribution"])
    show("Federated forecasting, as measured during the run", sections["federation"])
    show("Impact replay, counted from the seeded history", sections["impact"])
    print("\nStored. /api/eval/report serves the same figures to the dashboard.")


if __name__ == "__main__":
    asyncio.run(main())
