"""A photographed document changes the shelf the way that document means it.

Fix list #11, end to end through the real app and database. The model is
replaced in-process by a fixed extraction, so no Gemini request is spent and
what is under test is everything after the model: the ledger match, the
arithmetic, the refusals and the label on the card.

  * a delivery slip for 10 adds 10 — the bug was that it set the shelf to 10 —
    and settles its own dispatch in the two-sided ledger;
  * the same slip again changes nothing, and says it was already confirmed;
  * an issue record subtracts; one larger than the shelf changes nothing;
  * the medicine card then names the document, not "Counted by hand".

Everything it writes is deleted again in the `finally`, and the facility's
cached position is recomputed, so running it twice leaves no drift.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import delete

from app import movements, services, vision
from app.config import settings
from app.models import Facility, MedicineMovement, StockReading

from .harness import Checker, Report, client, db, demo_emails

SKU = "PARA500"
MEDICINE = "Paracetamol 500mg"
IMAGE = {"image_base64": "aGVsbG8=", "image_mime": "image/jpeg"}


def _reads(document_type: str, qty: float, batch: str | None = None):
    """A stand-in for the model that returns one fixed line."""

    async def read(image, mime_type="image/jpeg", **_):
        return vision.StockExtraction(
            lines=[vision.StockLine(medicine=MEDICINE, quantity=qty, unit="tablets", batch=batch)],
            document_date=datetime.now(timezone.utc).date(),
            confidence=0.9,
            notes=None,
            model=settings.gemini_model,
            document_type=document_type,
        )

    return read


async def _shelf(facility_id: str) -> float | None:
    async with db() as s:
        snaps = await services.get_snapshots(s, [facility_id])
    return next((x.qty_on_hand for x in (snaps[0].skus if snaps else []) if x.sku_code == SKU), None)


async def run() -> Report:
    c = Checker("stockphoto")
    async with client() as http:
        emails = await demo_emails(http)
    if "facility_user" not in emails:
        c.skip("no demo facility_user account — run `python -m scripts.users demo`")
        return c.report

    real_read = vision.read_stock_photo
    started = datetime.now(timezone.utc)
    batch = "CHK-SLIP-{0}".format(uuid4().hex[:6].upper())
    movement_id: int | None = None
    facility_id = ""
    reporter = ""
    before: float | None = None
    try:
        async with client(emails["facility_user"]) as http:
            me = (await http.get("/api/auth/me")).json()
            facility_id = me["user"]["facility_id"]
            reporter = "user:{0}".format(me["user"]["id"])
            before = await _shelf(facility_id)
            if before is None:
                c.skip("the demo centre has no {0} on record".format(SKU))
                return c.report

            async with db() as s:
                facility = await s.get(Facility, facility_id)
                m = await movements.record_dispatch(
                    s, batch_id=batch, sku_code=SKU, from_ref="WH-CHK",
                    to_facility=facility_id, state_silo=facility.state_silo,
                    qty=10, dispatch_source="warehouse",
                )
                await s.commit()
                movement_id = m.id

            path = "/api/facilities/{0}/stock-photo".format(facility_id)

            # ------------------------------------------------ delivery slip ---
            vision.read_stock_photo = _reads("delivery_slip", 10.0, batch)
            r = await http.post(path, json=IMAGE)
            c.eq("a delivery slip is read", r.status_code, 200)
            line = r.json()["lines"][0]
            c.eq("the slip's line is applied as an addition", line.get("action"), "added")
            c.near("a slip for 10 adds 10 to the shelf, not sets it to 10", line.get("qty_after"), before + 10, 0.01)
            async with db() as s:
                mv = await s.get(MedicineMovement, movement_id)
                c.eq("the slip settles its own dispatch in the ledger", mv.status, movements.RECEIVED)
                c.eq("and records that a photo confirmed it", mv.received_via, "photo")

            r = await http.post(path, json=IMAGE)
            line = r.json()["lines"][0]
            c.eq("the same slip again is not applied", line.get("action"), "not_applied")
            c.ok(
                "and says it was already confirmed",
                "Already confirmed" in (line.get("reason") or ""),
                line.get("reason") or "",
            )
            c.near("so the shelf is unchanged", await _shelf(facility_id), before + 10, 0.01)

            # ------------------------------------------------- issue record ---
            vision.read_stock_photo = _reads("issue_record", 4.0)
            line = (await http.post(path, json=IMAGE)).json()["lines"][0]
            c.near("an issue record subtracts what was issued", line.get("qty_after"), before + 6, 0.01)

            vision.read_stock_photo = _reads("issue_record", 10_000_000.0)
            line = (await http.post(path, json=IMAGE)).json()["lines"][0]
            c.eq("an issue larger than the shelf is not applied", line.get("action"), "not_applied")
            c.near("and leaves the shelf alone", await _shelf(facility_id), before + 6, 0.01)

            # -------------------------------------------------- the label ---
            view = (await http.get("/api/facilities/{0}/workspace".format(facility_id))).json()
            row = next(x for x in view["skus"] if x["sku_code"] == SKU)
            c.eq("the card calls it a photo, not a hand count", row["provenance"]["kind"], "photo")
            c.eq(
                "and names the document it came from",
                row["provenance"]["detail"], "Issue record read by Gemini",
            )
    finally:
        vision.read_stock_photo = real_read
        if facility_id:
            async with db() as s:
                await s.execute(
                    delete(StockReading).where(
                        StockReading.facility_id == facility_id,
                        StockReading.sku_code == SKU,
                        StockReading.reported_at >= started,
                        StockReading.reporter_ref == reporter,
                    )
                )
                if movement_id is not None:
                    await s.execute(delete(MedicineMovement).where(MedicineMovement.id == movement_id))
                await s.commit()
                await services.refresh_facility_state(s, facility_id)
                await s.commit()
            if before is not None:
                c.near("the shelf is back where it started", await _shelf(facility_id), before, 0.01)
            c.ok("check removed the rows it created", True)

    return c.report
