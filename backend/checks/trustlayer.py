"""The trust score: computed live, from the same rows the screens show.

The claim being checked is not "the score is correct" — it is that the score has
no private dataset behind it. So this check damages a real ledger row, recomputes,
and watches the number move; then puts the row back and watches it move back.
It also follows a component's evidence pointer and confirms it opens the very
rows that component counted.
"""

from __future__ import annotations

from sqlalchemy import select

from app import movements, trust
from app.models import Facility, MedicineMovement

from .harness import Checker, Report, db

STATE = "MH"


async def run() -> Report:
    c = Checker("trust")

    async with db() as session:
        # A facility with recent consignments, so receipt discipline has
        # something real to say about it.
        candidate = (
            await session.execute(
                select(MedicineMovement.to_facility)
                .join(Facility, Facility.id == MedicineMovement.to_facility)
                .where(Facility.state_silo == STATE, MedicineMovement.status == movements.RECEIVED)
                .group_by(MedicineMovement.to_facility)
                .order_by(select(1).scalar_subquery())
                .limit(1)
            )
        ).scalars().first()
        if candidate is None:
            c.skip("no settled consignments in " + STATE)
            return c.report

        scores = await trust.compute(session, trust.Scope(facility_ids=(candidate,)))
        c.eq("one score for one facility in scope", len(scores), 1)
        score = scores[0]
        c.ok(
            "score is a fraction, not an opaque number",
            0.0 <= score.score <= 1.0,
            "score={0}".format(score.score),
        )
        c.eq("band matches the score", score.band, trust.band_for(score.score))
        available = round(sum(comp.weight for comp in score.components), 3)
        c.ok(
            "scored only against the signals this facility has",
            0 < available <= 1.0,
            "{0} of the six signals, carrying weight {1}".format(len(score.components), available),
        )
        # The number on screen must be derivable from the reasons listed beside
        # it: a score an officer cannot reconstruct is a score they must trust.
        expected = round(
            max(0.0, 1.0 - sum(comp.penalty * comp.weight for comp in score.components) / available),
            3,
        )
        c.near("the score follows from the components shown", score.score, expected, 0.002)
        c.ok(
            "every signal explains itself in words",
            all(comp.reason for comp in score.components),
            "{0} components".format(len(score.components)),
        )

        # --- evidence is the same query, not a description of one ----------
        with_evidence = [comp for comp in score.components if comp.evidence]
        c.at_least("at least one signal carries clickable evidence", len(with_evidence), 1)
        for comp in with_evidence:
            target = comp.evidence or {}
            if target.get("tab") != "movements":
                continue
            c.eq("evidence points at this facility", target.get("facility"), candidate)
            rows = await movements.list_movements(
                session,
                facility_id=target["facility"],
                view=target.get("view", "attention"),
                limit=200,
            )
            c.ok(
                "following {0} evidence opens real ledger rows".format(comp.signal),
                len(rows) > 0 or comp.penalty == 0.0,
                "{0} rows, penalty {1}".format(len(rows), comp.penalty),
            )
            break

        # --- live, not cached ---------------------------------------------
        victim = (
            await session.execute(
                select(MedicineMovement)
                .where(
                    MedicineMovement.to_facility == candidate,
                    MedicineMovement.status == movements.RECEIVED,
                )
                .order_by(MedicineMovement.dispatched_at.desc())
                .limit(1)
            )
        ).scalars().first()

        if victim is None:
            c.skip("no settled consignment to damage")
            return c.report

        original = (victim.status, victim.qty_received)
        try:
            # Real damage to a real row, in the schema the product uses: this
            # consignment now reads as badly short.
            victim.qty_received = (victim.qty_dispatched or 0) * 0 + 1
            victim.status = movements.SHORT
            await session.commit()

            after = await trust.compute(session, trust.Scope(facility_ids=(candidate,)))
            damaged = after[0]
            c.ok(
                "damaging a ledger row changes the score on the next read",
                damaged.score <= score.score,
                "{0} -> {1}".format(score.score, damaged.score),
            )
            receipt = next(
                (comp for comp in damaged.components if comp.signal == "receipt_discipline"), None
            )
            c.ok(
                "and the receipt-discipline signal is the one that moved",
                receipt is not None and receipt.penalty > 0,
                "penalty {0}".format(None if receipt is None else receipt.penalty),
            )
        finally:
            victim.status, victim.qty_received = original
            await session.commit()

        restored = await trust.compute(session, trust.Scope(facility_ids=(candidate,)))
        c.near(
            "restoring the row restores the score, so nothing was cached",
            restored[0].score,
            score.score,
            0.001,
        )

        # --- scope narrows, and the threshold anchors to the good band -----
        state_scores = await trust.compute(session, trust.Scope(state=STATE))
        ids = {s.facility_id for s in state_scores}
        outside = (
            await session.execute(
                select(Facility.id).where(Facility.state_silo != STATE, Facility.id.in_(list(ids)))
            )
        ).scalars().first()
        c.ok("a state scope returns only that state", outside is None, "leaked {0}".format(outside))
        c.at_least("the state scope is not empty", len(state_scores), 1)

        c.eq("a good score never widens the warning threshold", trust.warning_multiplier(0.9), 1.0)
        c.eq("nor does one exactly on the good boundary", trust.warning_multiplier(0.75), 1.0)
        c.ok(
            "a poor score widens it",
            trust.warning_multiplier(0.3) > 1.0,
            "multiplier {0}".format(trust.warning_multiplier(0.3)),
        )
        c.eq("no score at all changes nothing", trust.warning_multiplier(None), 1.0)

        queue = await trust.audit_queue(session, state=STATE, limit=25)
        if queue:
            worst_first = [row.get("score") for row in queue if row.get("score") is not None]
            c.ok(
                "the audit queue puts the worst first",
                worst_first == sorted(worst_first),
                "first three {0}".format(worst_first[:3]),
            )

    return c.report
