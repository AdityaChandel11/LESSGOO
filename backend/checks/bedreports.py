"""Ward bed reports: a rotating code, a geofence, and the register as a third voice.

The rule this check defends is the one that is easy to get wrong and impossible
to notice: a check that could not run must never look like one that passed. A
photograph with the right code but no location is *not* verified, and an
implausible count is dropped rather than believed.
"""

from __future__ import annotations

from sqlalchemy import delete, select

from app import beds, vision
from app.config import settings
from app.models import BedReport, Facility

from .harness import Checker, Report, db


async def run() -> Report:
    c = Checker("beds")
    facility_id: str | None = None

    async with db() as session:
        facility = (
            await session.execute(
                select(Facility).where(Facility.beds_total > 0).order_by(Facility.id).limit(1)
            )
        ).scalars().first()
        if facility is None:
            c.skip("no facility with registered beds")
            return c.report
        facility_id = facility.id

        try:
            # --- the code itself ------------------------------------------
            code = await beds.code_for(session, facility.id)
            await session.commit()
            c.eq("the daily code is four characters", len(code.code), beds.CODE_LENGTH)
            c.ok(
                "no character that can be misread down a phone line",
                set(code.code) <= set(beds.CODE_ALPHABET),
                "code alphabet excludes O/0, I/1, S/5",
            )
            again = await beds.code_for(session, facility.id)
            c.eq("the same day returns the same code", again.code, code.code)

            # --- what each channel can actually prove ----------------------
            c.eq("gps is held to a tight radius", beds.geofence_radius_km("gps"), settings.geofence_gps_km)
            c.eq("a cell tower to a loose one", beds.geofence_radius_km("cell_id"), settings.geofence_cell_km)
            c.eq("a channel with no location gets no radius", beds.geofence_radius_km(None), None)

            good = vision.parse_extraction(
                {"beds_total": facility.beds_total, "beds_occupied": 3, "code_read": code.code, "confidence": 0.9}, model="mock"
            )
            inside = beds.verify(
                good,
                expected_code=code.code,
                loc_method="gps",
                distance=0.05,
                register_admissions=3,
            )
            c.eq("right code, on site, matching register: verified", inside.verification, beds.VERIFIED)
            c.ok("and it is marked complete", inside.code_ok and inside.geofence_ok, "")

            no_location = beds.verify(
                good, expected_code=code.code, loc_method=None, distance=None, register_admissions=3
            )
            c.ok(
                "right code but no location is NOT verified",
                no_location.verification != beds.VERIFIED,
                "verification={0}".format(no_location.verification),
            )
            c.eq("and the geofence records that it could not run", no_location.geofence_ok, None)

            wrong = beds.verify(
                vision.parse_extraction(
                    {"beds_total": facility.beds_total, "beds_occupied": 3, "code_read": "ZZZZ", "confidence": 0.9},
                    model="mock",
                ),
                expected_code=code.code,
                loc_method="gps",
                distance=0.05,
                register_admissions=3,
            )
            c.eq("the wrong code is rejected outright", wrong.verification, beds.REJECTED)

            far = beds.verify(
                good, expected_code=code.code, loc_method="gps", distance=25.0, register_admissions=3
            )
            c.ok(
                "a gps fix 25 km away contradicts the report",
                far.verification != beds.VERIFIED and far.geofence_ok is False,
                "verification={0}".format(far.verification),
            )

            # --- an implausible count is not believed ----------------------
            silly = vision.parse_extraction(
                {"beds_total": 6, "beds_occupied": 40, "code_read": code.code, "confidence": 0.9}, model="mock"
            )
            c.ok(
                "more occupied than the ward has is dropped, not stored",
                silly.beds_occupied is None,
                "beds_occupied={0!r}".format(silly.beds_occupied),
            )

            # --- and the whole path, written to the real table --------------
            # Whichever mode is configured is the one that gets exercised. With
            # LLM_MODE=live the simulate shortcut is refused by design, so the
            # check draws a real whiteboard and sends it to the model.
            if settings.llm_mode == "live" and settings.gemini_api_key:
                try:
                    from scripts.ward_photo import draw
                except ImportError:  # Pillow is a dev dependency
                    c.skip("LLM_MODE=live but Pillow is not installed to draw a test board")
                    return c.report
                png = draw(facility.beds_total, 4, code.code)
                extraction = await vision.read_ward_photo(png, "image/png")
                c.ok(
                    "the live model read the board",
                    extraction.model != "mock" and extraction.code_read == code.code,
                    "model={0} code={1!r} total={2} occupied={3}".format(
                        extraction.model, extraction.code_read,
                        extraction.beds_total, extraction.beds_occupied,
                    ),
                )
            else:
                extraction = await vision.read_ward_photo(
                    None,
                    simulate={
                        "beds_total": facility.beds_total,
                        "beds_occupied": 4,
                        "code_read": code.code,
                        "confidence": 0.88,
                    },
                )
                c.eq("the mock vision path needs no key", extraction.model, "mock")
            report, checks = await beds.record_report(
                session,
                facility,
                extraction,
                ward="general",
                source="check",
                loc_method="gps",
                loc_lat=facility.lat,
                loc_lng=facility.lng,
                loc_accuracy_m=8.0,
                register_admissions=4,
                media_ref=None,
            )
            await session.commit()
            c.eq("the stored report records the verdict", report.verification, beds.VERIFIED)
            c.eq("and which checks ran", (report.code_ok, report.geofence_ok), (True, True))
            c.ok(
                "the report is readable back through the product's own view",
                any(r.id == report.id for r in await beds.recent_reports(session, facility.id, limit=5)),
            )

        finally:
            async with db() as cleanup:
                await cleanup.execute(
                    delete(BedReport).where(BedReport.facility_id == facility_id, BedReport.source == "check")
                )
                await cleanup.commit()
                left = (
                    await cleanup.execute(
                        select(BedReport).where(
                            BedReport.facility_id == facility_id, BedReport.source == "check"
                        )
                    )
                ).first()
                c.ok("check removed the reports it wrote", left is None)

    return c.report
