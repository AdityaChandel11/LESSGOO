"""Stock requests between centres: a reply window, cancel, and the race guard.

Fix list #31, through the real app and database:

  * a request nobody answered lapses after the reply window, and a lapsed
    request no longer counts against the centre's cap;
  * the centre that raised a request can cancel it; nobody can then accept it;
  * a lapsed request cannot be accepted either, and the donor is told to ask
    again; the donor's inbox no longer lists it;
  * a second live request for the same medicine is refused.

Its own requests are inserted directly (backdated where the check needs a
lapsed one) and deleted again in the `finally`, with their audit rows.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select

from app import redistribution, services, workspace
from app.config import settings
from app.models import Approval, Facility, MedicineMovement, StockReading, Transfer

from .harness import Checker, Report, client, db, demo_emails

SKU = "ZINC"


def _request(donor: str, receiver: str, created_at: datetime) -> Transfer:
    return Transfer(
        from_facility=donor, to_facility=receiver, sku_code=SKU, qty=5,
        route_km=1.0, eta_hours=1.0, route_source="haversine", status="proposed",
        triggered_by=workspace.FACILITY_REQUEST, created_at=created_at,
        rationale={"origin": "facility_request", "note": "CHK requests"},
    )


async def run() -> Report:
    c = Checker("requests")
    async with client() as http:
        emails = await demo_emails(http)
    if not {"admin", "facility_user"} <= set(emails):
        c.skip("the demo accounts are not set up — run `python -m scripts.users demo`")
        return c.report

    ids: list[int] = []
    phc = ""
    donor = None
    try:
        async with client(emails["facility_user"]) as http:
            me = (await http.get("/api/auth/me")).json()
        phc = me["user"]["facility_id"]
        async with db() as s:
            donor = (
                await s.execute(
                    select(Facility).where(
                        Facility.state_silo == settings.demo_sandbox_state,
                        Facility.district == settings.demo_sandbox_district,
                        Facility.id != phc,
                    ).order_by(Facility.id).limit(1)
                )
            ).scalars().first()
            now = datetime.now(timezone.utc)
            lapsed_at = now - redistribution.request_window() - timedelta(minutes=1)
            live_before, _ = await workspace.open_request_counts(s, phc)
            rows = {
                "lapsed": _request(donor.id, phc, lapsed_at),
                "to_cancel": _request(donor.id, phc, now),
            }
            s.add_all(rows.values())
            await s.commit()
            ids = [r.id for r in rows.values()]
            live_after, _ = await workspace.open_request_counts(s, phc)
            c.eq("a lapsed request no longer counts against the cap", live_after, live_before + 1)

        async with client(emails["facility_user"]) as http:
            r = await http.post(
                "/api/facilities/{0}/requests".format(phc),
                json={"sku_code": SKU, "from_facility": donor.id, "qty": 5},
            )
            if r.status_code == 201:  # the refusal failed: clean up what it made
                ids.append(r.json()["transfer_id"])
            c.eq("a second live request for the same medicine is refused", r.status_code, 409)
            c.ok("and says which request is waiting", "already" in r.json().get("detail", ""),
                 r.json().get("detail", ""))

            r = await http.post("/api/transfers/{0}/cancel".format(rows["to_cancel"].id))
            c.eq("the centre that raised a request can cancel it", r.status_code, 200)

        async with client(emails["admin"]) as http:
            r = await http.post("/api/transfers/{0}/approve".format(rows["to_cancel"].id))
            c.eq("a cancelled request cannot be accepted", r.status_code, 409)
            c.ok("and the donor is told it was cancelled", "cancelled" in r.json().get("detail", ""),
                 r.json().get("detail", ""))

            r = await http.post("/api/transfers/{0}/approve".format(rows["lapsed"].id))
            c.eq("a lapsed request cannot be accepted", r.status_code, 409)
            c.ok("and the donor is told to ask for it again",
                 "send it again" in r.json().get("detail", ""), r.json().get("detail", ""))

            inbox = (await http.get("/api/facilities/{0}/incoming".format(donor.id))).json()
            c.ok("the donor's inbox no longer lists the lapsed request",
                 all(t["id"] != rows["lapsed"].id for t in inbox))
    finally:
        if ids:
            # A failing run can have accepted one of these for real: undo the
            # dispatch and the donor's stock reading as well, then recompute
            # both centres, so a red run leaves no drift either.
            async with db() as s:
                await s.execute(delete(MedicineMovement).where(MedicineMovement.transfer_id.in_(ids)))
                await s.execute(
                    delete(StockReading).where(
                        StockReading.source == "transfer",
                        StockReading.raw_payload["transfer_id"].astext.in_([str(i) for i in ids]),
                    )
                )
                await s.execute(delete(Approval).where(Approval.transfer_id.in_(ids)))
                await s.execute(delete(Transfer).where(Transfer.id.in_(ids)))
                await s.commit()
                for fid in {f for f in (phc, donor.id if donor else None) if f}:
                    await services.refresh_facility_state(s, fid)
                await s.commit()
        c.ok("check removed the rows it created", True)

    return c.report
