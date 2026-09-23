"""Stage C on the simulator: the channels, the door, and what is not stored.

What this proves:

  * **the door is shut.** Every inbound webhook refuses a request it cannot
    prove came from Twilio — and in simulator mode, with no auth token, that
    means it refuses everything. These are the only unauthenticated *write*
    routes in the system, so this is the assertion that matters most;
  * a signature that *is* valid validates, so the refusals above are a real
    check rather than a route that is simply broken;
  * the simulator drives the same spine a real SMS would;
  * a ward photo arriving on a channel is read and recorded, and leaves **no
    image bytes anywhere in the database** — only the provider's reference;
  * a voice call stores a reference, an outcome and a time, and nothing that
    could say who rang;
  * both logs hold their caps, so a judge pressing a button repeatedly cannot
    grow a 1 GB volume.

Everything written here carries a CHK prefix and is deleted again.
"""

from __future__ import annotations

from sqlalchemy import delete, func, select

from app import comms, ingest
from app.config import settings
from app.models import BedReport, CallLog, Facility, OutboundMessage, StockReading

from .harness import PREFIX, Checker, Report, client, db, demo_emails

STATE = "MH"
# A 1x1 PNG. Enough to be "media" as far as the spine is concerned; the mock
# extractor is what actually answers, because checks never reach a live model.
TINY_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc`\x00\x00"
    b"\x00\x02\x00\x01\xe2!\xbc\x33\x00\x00\x00\x00IEND\xaeB`\x82"
)


async def run() -> Report:
    c = Checker("comms")
    facility_id = ""
    call_refs: list[str] = []
    msg_ids: list[str] = []

    async with db() as session:
        facility = (
            await session.execute(
                select(Facility).where(Facility.state_silo == STATE)
                .order_by(Facility.id).limit(1)
            )
        ).scalars().first()
        if facility is None:
            c.skip("no facility in " + STATE)
            return c.report
        facility_id = facility.id

    reporter = ingest.demo_number(facility_id, "reporter")

    try:
        # ---- the door -------------------------------------------------------
        # Driven with no session at all, because that is how a stranger would
        # drive it.
        async with client() as http:
            for path in (
                "/api/webhooks/sms",
                "/api/webhooks/whatsapp",
                "/api/webhooks/voice/status",
            ):
                r = await http.post(path, data={"From": reporter, "Body": "ORS 40"})
                c.eq("{0} refuses an unsigned request".format(path), r.status_code, 403)

            r = await http.post(
                "/api/webhooks/sms",
                data={"From": reporter, "Body": "ORS 40"},
                headers={"X-Twilio-Signature": "obviously-not-a-real-signature"},
            )
            c.eq("and refuses a forged signature", r.status_code, 403)
            c.ok(
                "the refusal explains nothing to whoever sent it",
                "hmac" not in r.text.lower() and "expected" not in r.text.lower(),
                "body: {0}".format(r.text[:70]),
            )

        # The refusals above would also pass if the route were simply broken,
        # so prove the validator accepts a genuine signature.
        token = "chk-token-{0}".format("x" * 20)
        url = "http://checks.local/api/webhooks/sms"
        params = {"From": reporter, "Body": "ORS 40", "MessageSid": PREFIX + "-SIG"}
        good = comms.expected_signature(url, params, token)
        c.ok(
            "a correctly signed request validates",
            comms.validate_signature(url, params, good, auth_token=token),
        )
        c.ok(
            "and the same signature fails against a changed body",
            not comms.validate_signature(
                url, dict(params, Body="ORS 9999"), good, auth_token=token
            ),
        )
        c.ok(
            "with no token configured nothing validates at all",
            not comms.validate_signature(url, params, good, auth_token=""),
            "simulator mode: the door stays shut",
        )

        # ---- C1/C2: the simulator drives the real spine ---------------------
        async with client() as anon:
            emails = await demo_emails(anon)
        async with client(emails["admin"]) as http:
            msg_id = PREFIX + "-SMS-1"
            msg_ids.append(msg_id)
            r = await http.post(
                "/api/ingest/simulate",
                json={
                    "channel": "sms",
                    "sender": reporter,
                    "text": "ORS 40",
                    "external_id": msg_id,
                },
            )
            c.eq("the simulator accepts an SMS reading", r.status_code, 200)
            out = r.json()
            c.ok("it commits the reading", out["accepted"], "stage: {0}".format(out["stage"]))
            c.eq("and attributes it to the right centre", out["facility_id"], facility_id)
            c.ok(
                "the sender is masked, never stored raw",
                out["masked_sender"] and reporter[-4:] not in str(out["masked_sender"])[:-4],
                "masked: {0}".format(out["masked_sender"]),
            )

            again = await http.post(
                "/api/ingest/simulate",
                json={
                    "channel": "sms", "sender": reporter,
                    "text": "ORS 40", "external_id": msg_id,
                },
            )
            c.ok("a provider retry is not double-counted", again.json()["duplicate"])

        # ---- C3: a photo, and what it must not leave behind ------------------
        async with db() as session:
            submission = ingest.RawSubmission(
                channel="whatsapp",
                sender_ref=reporter,
                external_id=PREFIX + "-PHOTO-1",
                media=TINY_PNG,
                media_mime="image/png",
                media_ref="https://api.twilio.com/{0}/Media/ME-chk".format(PREFIX),
            )
            outcome = await ingest.process(session, submission)
        c.eq("a ward photo is handled as a photo", outcome.stage, "photo")

        async with db() as session:
            report = (
                await session.execute(
                    select(BedReport).where(BedReport.facility_id == facility_id)
                    .order_by(BedReport.id.desc()).limit(1)
                )
            ).scalars().first()
            c.ok("it produced a bed report", report is not None)
            if report is not None:
                c.ok(
                    "the stored reference is the provider's URL",
                    (report.media_ref or "").startswith("https://api.twilio.com/"),
                    "media_ref: {0}".format(report.media_ref),
                )
                payload = report.raw_payload or {}
                blob = repr(payload) + repr(report.media_ref)
                c.ok(
                    "and no image bytes reached the database",
                    len(blob) < 2000 and "iVBOR" not in blob and "\\x89PNG" not in blob,
                    "stored {0} characters".format(len(blob)),
                )

        # ---- C4: a call, reduced to almost nothing --------------------------
        async with db() as session:
            ref = CALL_REF = PREFIX + "-CALL-1"
            call_refs.append(ref)
            await comms.record_call(
                session, call_ref=ref, outcome="ringing",
                facility_id=facility_id, direction="inbound",
            )
            await session.commit()
            await comms.record_call(session, call_ref=ref, outcome="completed")
            await session.commit()

            rows = (
                await session.execute(select(CallLog).where(CallLog.call_ref == ref))
            ).scalars().all()
            c.eq("a retried status callback updates one row", len(rows), 1)
            c.eq("and the last status wins", rows[0].outcome, "completed")
            c.eq("the facility survives the update", rows[0].facility_id, facility_id)

            stored = {k: v for k, v in vars(rows[0]).items() if not k.startswith("_")}
            c.ok(
                "the call record holds nothing that says who rang",
                set(stored) <= {"call_ref", "facility_id", "direction", "outcome", "created_at"},
                "columns: {0}".format(sorted(stored)),
            )

            total = await session.scalar(select(func.count()).select_from(CallLog))
            c.ok(
                "the call log stays within its cap",
                int(total or 0) <= settings.max_call_log_rows,
                "{0} of {1}".format(total, settings.max_call_log_rows),
            )

        # ---- the outbound log ------------------------------------------------
        async with db() as session:
            sent = await comms.send(
                session, channel="sms", to_ref="masked",
                body="{0} acknowledgement".format(PREFIX),
            )
            await session.commit()
            c.ok("a simulated send is labelled simulated", sent.simulated)
            c.eq("and is recorded as such", sent.status, "simulated")

            total = await session.scalar(select(func.count()).select_from(OutboundMessage))
            c.ok(
                "the outbound log stays within its cap",
                int(total or 0) <= comms.MAX_OUTBOUND_ROWS,
                "{0} of {1}".format(total, comms.MAX_OUTBOUND_ROWS),
            )

    finally:
        async with db() as cleanup:
            if call_refs:
                await cleanup.execute(
                    delete(CallLog).where(CallLog.call_ref.in_(call_refs))
                )
            await cleanup.execute(
                delete(OutboundMessage).where(
                    OutboundMessage.body.like("%{0}%".format(PREFIX))
                )
            )
            if msg_ids:
                await cleanup.execute(
                    delete(StockReading).where(
                        StockReading.channel_msg_id.like("%{0}%".format(PREFIX))
                    )
                )
            await cleanup.execute(
                delete(BedReport).where(
                    BedReport.media_ref.like("%{0}%".format(PREFIX))
                )
            )
            await cleanup.commit()

            left = (
                await cleanup.execute(
                    select(CallLog).where(CallLog.call_ref.in_(call_refs or [""]))
                )
            ).first()
            c.ok("check removed the rows it created", left is None)

    return c.report
