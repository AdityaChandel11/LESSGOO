"""Only the centre itself states facts about the centre; officers chase.

Fix list #74, through the real app and database:

  * an officer cannot check in a centre's staff or send its bed report
    outside the demo sandbox — the drill's labelled exception is inside it;
  * chasing a centre logs one reminder on the channel simulator, to the
    centre's masked handset, never a real number;
  * a pharmacist cannot chase anyone.

The reminder row it writes is deleted again in the `finally`.
"""

from __future__ import annotations

from sqlalchemy import delete, select

from app.config import settings
from app.models import Facility, OutboundMessage

from .harness import Checker, Report, client, db, demo_emails


async def run() -> Report:
    c = Checker("facts")
    async with client() as http:
        emails = await demo_emails(http)
    if not {"admin", "block_mo", "facility_user"} <= set(emails):
        c.skip("the demo accounts are not set up — run `python -m scripts.users demo`")
        return c.report

    async with db() as s:
        inside = (
            await s.execute(
                select(Facility).where(
                    Facility.state_silo == settings.demo_sandbox_state,
                    Facility.district == settings.demo_sandbox_district,
                ).order_by(Facility.id).limit(1)
            )
        ).scalars().first()
        outside = (
            await s.execute(
                select(Facility).where(
                    Facility.state_silo == settings.demo_sandbox_state,
                    Facility.district != settings.demo_sandbox_district,
                ).order_by(Facility.id).limit(1)
            )
        ).scalars().first()
    if inside is None or outside is None:
        c.skip("no facility on one side of the sandbox line")
        return c.report

    reminder_id: int | None = None
    try:
        async with client(emails["admin"]) as http:
            r = await http.post(
                "/api/facilities/{0}/checkins".format(outside.id),
                json={"staff_ref": "CHK-S1", "action": "in"},
            )
            c.eq("an administrator cannot check a centre's staff in", r.status_code, 403)
            r = await http.post("/api/facilities/{0}/bed-reports".format(outside.id), json={})
            c.eq("nor send its bed report", r.status_code, 403)

        async with client(emails["block_mo"]) as http:
            r = await http.post(
                "/api/facilities/{0}/chase".format(inside.id), json={"topic": "checkin"}
            )
            c.eq("a district officer can chase a centre in the district", r.status_code, 200)
            body = r.json()
            c.eq("the reminder is logged as simulated", body.get("status"), "simulated")
            c.ok(
                "to the centre's masked handset, never a number",
                "•" in (body.get("sent_to") or ""),
                body.get("sent_to") or "",
            )
            async with db() as s:
                row = await s.scalar(
                    select(OutboundMessage).where(OutboundMessage.body == body.get("body"))
                    .order_by(OutboundMessage.id.desc()).limit(1)
                )
                reminder_id = row.id if row else None
                c.ok("and one row lands in the outbound log", row is not None)

        async with client(emails["facility_user"]) as http:
            r = await http.post(
                "/api/facilities/{0}/chase".format(inside.id), json={"topic": "beds"}
            )
            c.eq("a pharmacist cannot chase anyone", r.status_code, 403)
    finally:
        if reminder_id is not None:
            async with db() as s:
                await s.execute(delete(OutboundMessage).where(OutboundMessage.id == reminder_id))
                await s.commit()
        c.ok("check removed the rows it created", True)

    return c.report
