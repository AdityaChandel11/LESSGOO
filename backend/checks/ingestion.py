"""The ingestion spine: one pipeline, every channel, always an answer.

Spec 13. The assertions that matter are the ones about failure: an
unregistered number is refused rather than guessed at, a provider retry cannot
double-count, an unrecognised medicine is named back rather than silently
dropped, and every path — accepted or refused — produces a reply. A worker who
reports into silence stops reporting.

Runs entirely on `POST /ingest/simulate`, with no Twilio account in existence.
"""

from __future__ import annotations

from sqlalchemy import delete, func, select

from app import ingest, movements
from app.models import BedReport, Facility, MedicineMovement, StaffCheckin, StockReading

from .harness import PREFIX, Checker, Report, client, db

STATE = "MH"
BATCH = "{0}-SPINE".format(PREFIX)


async def run() -> Report:
    c = Checker("ingestion")

    async with db() as session:
        facility = (
            await session.execute(
                select(Facility)
                .where(Facility.state_silo == STATE, Facility.beds_total > 0)
                .order_by(Facility.id)
                .limit(1)
            )
        ).scalars().first()
        if facility is None:
            c.skip("no facility in " + STATE)
            return c.report
        facility_id, facility_name = facility.id, facility.name

    reporter = ingest.demo_number(facility_id, "reporter")
    supervisor = ingest.demo_number(facility_id, "supervisor")

    # The registry stores a hash and a mask, never the number itself.
    c.ok(
        "the number itself is never stored",
        ingest.hash_phone(reporter) != reporter and reporter[-4:] in ingest.mask_phone(reporter),
        "masked as {0}".format(ingest.mask_phone(reporter)),
    )
    c.eq(
        "the same handset hashes the same however it is written",
        ingest.hash_phone(reporter),
        ingest.hash_phone("+91 " + reporter),
    )

    try:
        async with db() as session:
            movement = await movements.record_dispatch(
                session,
                batch_id=BATCH,
                sku_code="ORS",
                from_ref="WH-{0}".format(PREFIX),
                to_facility=facility_id,
                state_silo=STATE,
                qty=100,
                dispatch_source="warehouse",
                transit_hours=48,
            )
            await session.commit()
            movement_id = movement.id
            before_checkins = await session.scalar(
                select(func.count()).select_from(StaffCheckin).where(
                    StaffCheckin.facility_id == facility_id
                )
            )
            before_beds = await session.scalar(
                select(func.count()).select_from(BedReport).where(
                    BedReport.facility_id == facility_id
                )
            )

        async with client("admin@demo.swasthsetu.in") as http:

            async def send(text: str, sender: str = reporter, **extra) -> dict:
                body = {"channel": "sms", "sender": sender, "text": text, **extra}
                return (await http.post("/api/ingest/simulate", json=body)).json()

            # --- the forgiving grammar ---------------------------------
            clean = await send("ORS 60 ZINC 20")
            c.eq("a two-medicine message commits both", len(clean["readings"]), 2)
            c.ok(
                "and the reply states days of cover, not just 'ok'",
                "days" in clean["reply"],
                clean["reply"][:70],
            )
            c.eq("no space still parses", (await send("ORS60"))["stage"], "committed")
            c.eq("a hyphen still parses", (await send("ORS-45"))["stage"], "committed")
            c.eq("a misspelling still resolves", (await send("zink 30"))["stage"], "committed")

            # --- and what it refuses -----------------------------------
            nonsense = await send("tractor 40")
            c.eq("an unrelated word is not matched to a medicine", nonsense["stage"], "resolve")
            c.ok(
                "and the reply names what it could not read",
                "tractor" in nonsense["reply"],
                nonsense["reply"][:70],
            )
            unreadable = await send("hello?")
            c.eq("an unparseable message is refused", unreadable["stage"], "extract")
            c.ok(
                "with an example, never a silent drop",
                "ORS" in unreadable["reply"],
                unreadable["reply"][:70],
            )
            stranger = await send("ORS 10", sender="9000000000")
            c.eq("an unregistered number is refused", stranger["stage"], "identify")
            c.ok("every reply says something", all(
                r["reply"] for r in (clean, nonsense, unreadable, stranger)
            ))

            # --- a provider retry ---------------------------------------
            once = await send("ORS 99", external_id="{0}-RETRY".format(PREFIX))
            twice = await send("ORS 99", external_id="{0}-RETRY".format(PREFIX))
            c.eq("the first send commits", once["stage"], "committed")
            c.ok(
                "the retry is recognised, not recorded again",
                twice["duplicate"] and twice["stage"] == "dedupe",
                "stage={0} duplicate={1}".format(twice["stage"], twice["duplicate"]),
            )

            # --- the commands that are not readings ---------------------
            checkin = await send("IN")
            c.eq("IN checks the worker in", checkin["stage"], "attendance")
            c.ok(
                "and admits the channel cannot prove where they are",
                "not verified" in checkin["reply"].lower(),
                checkin["reply"][:70],
            )
            bed = await send("BEDS 9")
            c.eq("BEDS records occupancy", bed["stage"], "beds")
            c.ok(
                "as unverified, because nothing was photographed",
                "unverified" in bed["reply"],
                bed["reply"][:80],
            )
            receipt = await send("GOT {0} 92".format(BATCH))
            c.eq("GOT settles the real consignment", receipt["stage"], "receipt")
            c.ok(
                "and says honestly that it was short",
                "short" in receipt["reply"],
                receipt["reply"][:80],
            )
            missing = await send("GOT {0}-NOPE 10".format(PREFIX))
            c.eq("a consignment nobody sent is refused", missing["stage"], "receipt")

            wrong_role = await send("APPROVE 1")
            c.eq("a reporter cannot approve a transfer", wrong_role["stage"], "authorise")
            c.ok("and is told why", "supervisor" in wrong_role["reply"], wrong_role["reply"][:60])
            helped = await send("HELP")
            c.ok("HELP returns the format", "ORS" in helped["reply"])

        async with db() as session:
            after_checkins = await session.scalar(
                select(func.count()).select_from(StaffCheckin).where(
                    StaffCheckin.facility_id == facility_id
                )
            )
            after_beds = await session.scalar(
                select(func.count()).select_from(BedReport).where(
                    BedReport.facility_id == facility_id
                )
            )
            settled = await session.get(MedicineMovement, movement_id)
            c.eq("the check-in reached the table", after_checkins, before_checkins + 1)
            c.eq("the bed report reached the table", after_beds, before_beds + 1)
            c.eq("the consignment settled as short", settled.status, movements.SHORT)
            c.ok(
                "a reading carries the channel and a hashed sender",
                (
                    await session.scalar(
                        select(StockReading.reporter_ref)
                        .where(
                            StockReading.facility_id == facility_id,
                            StockReading.source == "sms",
                        )
                        .order_by(StockReading.reported_at.desc())
                        .limit(1)
                    )
                )
                == ingest.hash_phone(reporter),
                "provenance is the salted hash, not the number",
            )

    finally:
        async with db() as cleanup:
            await cleanup.execute(
                delete(StockReading).where(
                    StockReading.facility_id == facility_id,
                    StockReading.reporter_ref.in_(
                        [ingest.hash_phone(reporter), ingest.hash_phone(supervisor)]
                    ),
                )
            )
            await cleanup.execute(
                delete(StaffCheckin).where(
                    StaffCheckin.facility_id == facility_id,
                    StaffCheckin.staff_ref == ingest.hash_phone(reporter),
                )
            )
            await cleanup.execute(
                delete(BedReport).where(
                    BedReport.facility_id == facility_id, BedReport.source == "sms"
                )
            )
            await cleanup.execute(
                delete(MedicineMovement).where(MedicineMovement.batch_id == BATCH)
            )
            await cleanup.commit()
            left = (
                await cleanup.execute(
                    select(MedicineMovement).where(MedicineMovement.batch_id == BATCH)
                )
            ).first()
            c.ok("check removed everything it wrote", left is None)

    return c.report
