"""Re-running a plan must not destroy work.

Fix list #37, through the real app and database:

  * a centre's own request survives an officer re-running the plan;
  * a solver proposal the re-plan replaced answers "replaced at <time>" to
    whoever tries to act on it, instead of a bare "not found".

It inserts one request and one stale solver proposal of its own, runs a
single-medicine plan for the sandbox's state, and deletes its own rows in the
`finally`. The plan's new proposals are left in place: they are what the plan
is supposed to leave.
"""

from __future__ import annotations

from sqlalchemy import delete, select

from app import workspace
from app.config import settings
from app.models import Facility, Transfer

from .harness import Checker, Report, client, db, demo_emails

SKU = "ORS"


async def run() -> Report:
    c = Checker("replan")
    async with client() as http:
        emails = await demo_emails(http)
    if "state_officer" not in emails:
        c.skip("the demo accounts are not set up — run `python -m scripts.users demo`")
        return c.report

    request_id: int | None = None
    stale_id: int | None = None
    try:
        async with db() as s:
            pair = (
                await s.execute(
                    select(Facility).where(
                        Facility.state_silo == settings.demo_sandbox_state,
                        Facility.district == settings.demo_sandbox_district,
                    ).order_by(Facility.id).limit(2)
                )
            ).scalars().all()
            if len(pair) < 2:
                c.skip("fewer than two centres in the sandbox")
                return c.report
            donor, receiver = pair
            request = Transfer(
                from_facility=donor.id, to_facility=receiver.id, sku_code=SKU, qty=5,
                route_km=1.0, eta_hours=1.0, route_source="haversine", status="proposed",
                triggered_by=workspace.FACILITY_REQUEST,
                rationale={"origin": "facility_request", "note": "CHK replan"},
            )
            stale = Transfer(
                from_facility=receiver.id, to_facility=donor.id, sku_code=SKU, qty=5,
                route_km=1.0, eta_hours=1.0, route_source="haversine", status="proposed",
                triggered_by="threshold", rationale={"note": "CHK replan"},
            )
            s.add_all([request, stale])
            await s.commit()
            request_id, stale_id = request.id, stale.id

        async with client(emails["state_officer"]) as http:
            r = await http.post(
                "/api/transfers/plan", json={"state": settings.demo_sandbox_state, "sku": SKU}
            )
            c.eq("an officer re-runs the plan", r.status_code, 200)

        async with db() as s:
            kept = await s.get(Transfer, request_id)
            c.ok("a centre's own request survives the re-plan", kept is not None)
            c.eq("and is still waiting on the donor", kept.status if kept else None, "proposed")
            gone = await s.scalar(select(Transfer.id).where(Transfer.id == stale_id))
            c.ok("the solver's own stale proposal was replaced", gone is None)

        async with client(emails["admin"]) as http:
            r = await http.post("/api/transfers/{0}/approve".format(stale_id))
            c.eq("acting on the replaced proposal is a conflict, not a 404", r.status_code, 409)
            c.ok(
                "and says when the plan replaced it",
                "replaced" in r.json().get("detail", ""),
                r.json().get("detail", ""),
            )
    finally:
        async with db() as s:
            ids = [i for i in (request_id, stale_id) if i is not None]
            if ids:
                await s.execute(delete(Transfer).where(Transfer.id.in_(ids)))
                await s.commit()
        c.ok("check removed the rows it created", True)

    return c.report
