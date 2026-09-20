"""Staff attendance: honest about what each channel can prove.

A smartphone can offer a GPS fix. An IVR call cannot offer anything at all. The
rule being checked is that the second case is recorded as "no check ran" rather
than as a pass, and that everything the product shows is a facility-level
aggregate — never a named person.
"""

from __future__ import annotations

from sqlalchemy import delete, select

from app import attendance
from app.models import Facility, StaffCheckin

from .harness import PREFIX, Checker, Report, db

STAFF_REF = "{0}:staff-1".format(PREFIX)


async def run() -> Report:
    c = Checker("attendance")
    facility_id: str | None = None

    async with db() as session:
        facility = (
            await session.execute(select(Facility).order_by(Facility.id).limit(1))
        ).scalars().first()
        if facility is None:
            c.skip("no facilities seeded")
            return c.report
        facility_id = facility.id

        try:
            # --- what each channel can supply ------------------------------
            near_km, near_ok = attendance.resolve_location(
                facility, lat=facility.lat, lng=facility.lng, loc_method="gps"
            )
            c.ok("a gps fix at the facility passes", near_ok is True, "distance {0} km".format(near_km))

            far_km, far_ok = attendance.resolve_location(
                facility, lat=facility.lat + 1.5, lng=facility.lng + 1.5, loc_method="gps"
            )
            c.ok("a gps fix far away fails", far_ok is False, "distance {0} km".format(far_km))

            ivr_km, ivr_ok = attendance.resolve_location(facility, lat=None, lng=None, loc_method="ivr")
            c.eq("an IVR call carries no location at all", (ivr_km, ivr_ok), (None, None))
            sms_km, sms_ok = attendance.resolve_location(facility, lat=None, lng=None, loc_method="sms")
            c.eq("nor does an SMS", (sms_km, sms_ok), (None, None))

            # --- a check-in, through the real path -------------------------
            checkin = await attendance.record_checkin(
                session,
                facility,
                staff_ref=STAFF_REF,
                action="in",
                shift="morning",
                source="check",
                lat=facility.lat,
                lng=facility.lng,
                loc_method="gps",
                cell_id=None,
            )
            await session.commit()
            c.eq("the check-in stores which method ran", checkin.loc_method, "gps")
            c.eq("and whether the geofence passed", checkin.geofence_ok, True)

            blind = await attendance.record_checkin(
                session,
                facility,
                staff_ref="{0}:staff-2".format(PREFIX),
                action="in",
                shift="morning",
                source="ivr",
                lat=None,
                lng=None,
                loc_method="ivr",
                cell_id=None,
            )
            await session.commit()
            c.eq(
                "an IVR check-in is stored as unverified, not as verified",
                (blind.loc_method, blind.geofence_ok),
                ("ivr", None),
            )

            summary = await attendance.summarise(session, facility.id)
            c.ok(
                "the summary is a facility-level aggregate",
                hasattr(summary, "present") or hasattr(summary, "present_pct"),
                "fields: {0}".format(sorted(vars(summary))[:8]),
            )
            values = " ".join(str(v) for v in vars(summary).values())
            c.ok(
                "and names no individual",
                STAFF_REF not in values,
                "staff ref must not appear in what the screen shows",
            )

            footfall = await attendance.footfall_for(session, facility.id, checkin.checked_in_at)
            c.ok(
                "footfall cross-check answers with a number or an honest nothing",
                footfall is None or isinstance(footfall, int),
                "footfall={0!r}".format(footfall),
            )

            recent = await attendance.recent(session, facility.id, limit=10)
            c.ok(
                "the check-in is readable back through the product's own view",
                any(getattr(r, "id", None) == checkin.id for r in recent)
                or any(isinstance(r, dict) and r.get("id") == checkin.id for r in recent),
                "{0} recent rows".format(len(recent)),
            )

        finally:
            async with db() as cleanup:
                await cleanup.execute(
                    delete(StaffCheckin).where(
                        StaffCheckin.facility_id == facility_id,
                        StaffCheckin.staff_ref.like("{0}:%".format(PREFIX)),
                    )
                )
                await cleanup.commit()
                left = (
                    await cleanup.execute(
                        select(StaffCheckin).where(
                            StaffCheckin.staff_ref.like("{0}:%".format(PREFIX))
                        )
                    )
                ).first()
                c.ok("check removed the check-ins it wrote", left is None)

    return c.report
