"""The pharmacist's loop, end to end, through the real app.

What this proves, in the order a judge walks it:

  * a facility_user opening their own centre sees its medicines, each with
    how the figure was checked and when it runs out;
  * the same account opening someone else's centre is refused, and the
    refusal does not name what it refused;
  * "find supply" offers only centres that can give without breaching their
    own floor, and offers none at all for a controlled substance;
  * raising a request moves *nothing* — that is the invariant the whole
    approval design exists to protect (spec 12.3);
  * both caps hold, and the message names the limit;
  * an officer approving it debits the donor and opens a batch;
  * confirming the delivery credits the recipient.

Everything it writes is deleted again in the `finally`, including the stock
readings the approval and the receipt wrote, and both facilities' cached
positions are recomputed — so running the checks twice leaves no drift.
"""

from __future__ import annotations

from sqlalchemy import delete, select

from app import movements, redistribution, services, workspace
from app.config import settings
from app.models import Approval, Facility, MedicineMovement, StockReading, Transfer

from .harness import PREFIX, Checker, Report, client, db, demo_emails

# Amoxicillin: plain, not cold-chain, stocked everywhere. MORPH is the
# controlled one, and is checked precisely because it is stocked everywhere too.
SKU = "AMOX"
CONTROLLED_SKU = "MORPH"
BY_REF = "chk:workspace"


async def _briefing_assertions(c, http, facility_id: str, other_id: str) -> None:
    """The briefing, on the path that has to work when there is no model.

    Forced onto the deterministic branch by the caller, whatever LLM_MODE says.
    These checks are a release gate and run constantly; the free tier allows
    twenty generate requests a day per model, so a check that spent one on
    every run would exhaust the quota by lunchtime and then start failing for a
    reason that has nothing to do with the code.

    The live path is proved in the browser and by scripts/check_gemini, where a
    person is choosing to spend the call. What matters here is the branch that
    must hold when the model cannot answer: a line that is still useful,
    carrying no AI label and naming no model.
    """
    view = (
        await http.get("/api/facilities/{0}/workspace".format(facility_id))
    ).json()
    c.ok(
        "the workspace carries a computed line in both languages",
        set(view.get("briefing", {})) == {"en", "hi"}
        and all(view["briefing"].values()),
        "keys: {0}".format(sorted(view.get("briefing", {}))),
    )
    c.ok(
        "and the two languages are not the same string",
        view["briefing"]["en"] != view["briefing"]["hi"],
    )

    for lang in ("en", "hi"):
        r = await http.post(
            "/api/facilities/{0}/briefing?lang={1}".format(facility_id, lang)
        )
        c.eq("a briefing answers for {0}".format(lang), r.status_code, 200)
        got = r.json()
        c.eq("and in the language asked for", got["lang"], lang)
        c.ok("it is never empty", bool(got["body"]))
        c.eq("computed text claims no model", got["model"], None)
        c.eq("and reports its source honestly", got["source"], "rules")
        c.ok("and carries no AI label", got["ai"] is False)

    r = await http.post("/api/facilities/{0}/briefing?lang=fr".format(facility_id))
    c.eq("an unsupported language is refused", r.status_code, 422)

    r = await http.post("/api/facilities/{0}/briefing?lang=en".format(other_id))
    c.eq("another centre's briefing is refused", r.status_code, 403)


async def run() -> Report:
    c = Checker("workspace")
    transfer_ids: list[int] = []
    batches: list[str] = []
    facility_id = ""
    donor_id = ""
    # Where both shelves started. Asserted again after the cleanup: a check
    # that moves stock and puts back only half of it leaves the next run
    # looking at a facility that is no longer short, which is how this one
    # first went wrong.
    shelves_before: dict[str, float] = {}

    async with client() as http:
        emails = await demo_emails(http)
        if "facility_user" not in emails:
            c.skip("no demo facility_user account — run `python -m scripts.users demo`")
            return c.report

    try:
        # ---------------------------------------------------- the pharmacist ---
        async with client(emails["facility_user"]) as http:
            me = (await http.get("/api/auth/me")).json()
            facility_id = me["user"]["facility_id"]
            if not facility_id:
                c.skip("the demo facility_user is not attached to a facility")
                return c.report

            r = await http.get("/api/facilities/{0}/workspace".format(facility_id))
            c.eq("a pharmacist can open their own centre", r.status_code, 200)
            view = r.json()
            c.eq("the workspace names that centre", view["facility"]["id"], facility_id)
            c.at_least("it lists the medicines it stocks", len(view["skus"]), 1)

            c.ok(
                "every medicine says how its figure was checked",
                all(s["provenance"]["kind"] for s in view["skus"]),
                "kinds: {0}".format(sorted({s["provenance"]["kind"] for s in view["skus"]})),
            )
            c.ok(
                "a medicine with no burn rate carries no stock-out date",
                all(
                    s["stockout_on"] is None
                    for s in view["skus"]
                    if not s["daily_burn_rate"]
                ),
                "review focus 1",
            )
            c.ok(
                "no medicine is described as counted when nothing was reported",
                all(
                    s["provenance"]["kind"] != "counted"
                    for s in view["skus"]
                    if s["last_source"] in ("seed", "transfer")
                    and s["last_receipt"] is None
                ),
            )

            # --- someone else's centre is not theirs to open ----------------
            async with db() as session:
                other = (
                    await session.execute(
                        select(Facility).where(Facility.id != facility_id).limit(1)
                    )
                ).scalars().first()
                other_id, other_name = other.id, other.name

            r = await http.get("/api/facilities/{0}/workspace".format(other_id))
            c.eq("another centre's workspace is refused", r.status_code, 403)
            c.ok(
                "and the refusal does not name what it refused",
                other_name not in r.text,
                "review focus 2",
            )

            # --- the briefing, on the path that works without a model -------
            was_live = settings.llm_mode
            settings.llm_mode = "mock"
            try:
                await _briefing_assertions(c, http, facility_id, other_id)
            finally:
                settings.llm_mode = was_live

            # --- find supply -------------------------------------------------
            r = await http.get(
                "/api/facilities/{0}/supply".format(facility_id), params={"sku": SKU}
            )
            c.eq("find supply answers for a plain medicine", r.status_code, 200)
            supply = r.json()
            c.ok(
                "donors are listed nearest first",
                [d["km"] for d in supply["donors"]]
                == sorted(d["km"] for d in supply["donors"]),
            )
            c.ok(
                "every donor keeps at least its own floor",
                all(
                    d["days_kept"] >= settings.donor_floor_days - 1e-6
                    for d in supply["donors"]
                ),
                "floor is {0} days".format(settings.donor_floor_days),
            )
            c.ok(
                "no donor is the centre itself",
                all(d["facility_id"] != facility_id for d in supply["donors"]),
            )
            c.ok(
                "every distance says it is a straight line",
                all(d["distance_basis"] == "straight_line_x1.3" for d in supply["donors"]),
            )

            r = await http.get(
                "/api/facilities/{0}/supply".format(facility_id),
                params={"sku": CONTROLLED_SKU},
            )
            controlled = r.json()
            c.ok(
                "a controlled substance is never offered a donor",
                controlled["donors"] == [] and controlled["manual_only"] is True,
                "review focus 3 — spec 12.3",
            )

            if not supply["donors"]:
                c.skip("no donor has spare {0} today; the rest needs one".format(SKU))
                return c.report
            donor = supply["donors"][0]
            donor_id = donor["facility_id"]

            # --- raising a request must move nothing -------------------------
            async with db() as session:
                for fid in (facility_id, donor_id):
                    snap = await services.get_snapshots(session, [fid])
                    shelves_before[fid] = next(
                        s.qty_on_hand for s in snap[0].skus if s.sku_code == SKU
                    )
                donor_before = shelves_before[donor_id]

            qty = min(supply["units_needed"], donor["spare_units"])
            r = await http.post(
                "/api/facilities/{0}/requests".format(facility_id),
                json={"sku_code": SKU, "from_facility": donor_id, "qty": qty},
            )
            c.eq("a request is accepted", r.status_code, 201)
            made = r.json()
            transfer_ids.append(made["transfer_id"])
            c.eq("it is only a proposal", made["status"], "proposed")
            c.eq(
                "its reference is derived from the row",
                made["reference"],
                workspace.reference(made["transfer_id"]),
            )
            c.ok(
                "the delivery date is labelled an estimate",
                made["estimate_label"] == "estimate"
                and made["distance_basis"] == "straight_line_x1.3",
            )
            c.eq(
                "and it shows every constant behind it",
                sorted(made["assumptions"]),
                ["avg_speed_kmh", "dispatch_cutoff_hour", "handling_hours",
                 "road_factor", "working_hours_per_day"],
            )

            async with db() as session:
                after = await services.get_snapshots(session, [donor_id])
                donor_after = next(
                    s.qty_on_hand for s in after[0].skus if s.sku_code == SKU
                )
            c.near(
                "raising a request moves no stock at all",
                donor_after - donor_before,
                0.0,
                0.001,
            )

            # --- a controlled medicine cannot be requested -------------------
            r = await http.post(
                "/api/facilities/{0}/requests".format(facility_id),
                json={"sku_code": CONTROLLED_SKU, "from_facility": donor_id, "qty": 10},
            )
            c.eq("a controlled medicine cannot be requested", r.status_code, 422)

            # --- a request that would breach the donor's floor ---------------
            r = await http.post(
                "/api/facilities/{0}/requests".format(facility_id),
                json={"sku_code": SKU, "from_facility": donor_id, "qty": 10_000_000},
            )
            c.eq("an impossible quantity is refused at once", r.status_code, 422)
            c.ok(
                "and the refusal names the floor",
                "floor" in r.json()["detail"].lower(),
                "review focus 5 — got: {0}".format(r.json()["detail"][:90]),
            )

            # --- the per-facility cap ----------------------------------------
            limit = settings.max_open_requests_per_facility
            while True:
                async with db() as session:
                    open_here, _ = await workspace.open_request_counts(session, facility_id)
                if open_here >= limit:
                    break
                r = await http.post(
                    "/api/facilities/{0}/requests".format(facility_id),
                    json={"sku_code": SKU, "from_facility": donor_id, "qty": qty},
                )
                if r.status_code != 201:
                    break
                transfer_ids.append(r.json()["transfer_id"])

            r = await http.post(
                "/api/facilities/{0}/requests".format(facility_id),
                json={"sku_code": SKU, "from_facility": donor_id, "qty": qty},
            )
            c.eq("the cap refuses the next request", r.status_code, 409)
            detail = r.json()["detail"]
            c.ok(
                "and the message names the limit",
                str(limit) in detail and "this centre" in detail.lower(),
                "got: {0}".format(detail[:90]),
            )

        # ------------------------------------------------------- the officer ---
        async with db() as session:
            donor_facility = await session.get(Facility, donor_id)
            me_facility = await session.get(Facility, facility_id)
            same_district = (
                donor_facility.district == me_facility.district
                and donor_facility.state_silo == me_facility.state_silo
            )
        officer = emails["block_mo"] if same_district else emails["state_officer"]

        async with client(officer) as http:
            async with db() as session:
                before = await services.get_snapshots(session, [donor_id])
                donor_before = next(
                    s.qty_on_hand for s in before[0].skus if s.sku_code == SKU
                )

            first = transfer_ids[0]
            r = await http.post("/api/transfers/{0}/approve".format(first))
            c.ok(
                "the officer the request named can approve it",
                r.status_code == 200,
                "{0} on transfer {1}: {2}".format(r.status_code, first, r.text[:120]),
            )

            async with db() as session:
                after = await services.get_snapshots(session, [donor_id])
                donor_after = next(
                    s.qty_on_hand for s in after[0].skus if s.sku_code == SKU
                )
                movement = (
                    await session.execute(
                        select(MedicineMovement).where(
                            MedicineMovement.transfer_id == first
                        )
                    )
                ).scalars().first()

            c.near(
                "approving debits the donor by exactly what was asked",
                donor_before - donor_after,
                float(qty),
                0.5,
            )
            c.ok("approving opens a batch on the ledger", movement is not None)
            if movement is not None:
                batches.append(movement.batch_id)
                movement_id = movement.id

        # --------------------------------------------- back to the pharmacist ---
        if batches:
            async with client(emails["facility_user"]) as http:
                async with db() as session:
                    before = await services.get_snapshots(session, [facility_id])
                    mine_before = next(
                        s.qty_on_hand for s in before[0].skus if s.sku_code == SKU
                    )

                r = await http.post(
                    "/api/movements/{0}/receipt".format(movement_id),
                    json={"qty_received": qty, "via": "form"},
                )
                c.eq("the pharmacist can confirm their own delivery", r.status_code, 200)

                async with db() as session:
                    after = await services.get_snapshots(session, [facility_id])
                    mine_after = next(
                        s.qty_on_hand for s in after[0].skus if s.sku_code == SKU
                    )
                c.near(
                    "confirming credits the centre by what arrived",
                    mine_after - mine_before,
                    float(qty),
                    0.5,
                )

                r = await http.get("/api/facilities/{0}/workspace".format(facility_id))
                row = next(s for s in r.json()["skus"] if s["sku_code"] == SKU)
                c.eq(
                    "and the medicine now reads as verified by that delivery",
                    row["provenance"]["kind"],
                    "delivery",
                )

    finally:
        async with db() as cleanup:
            if transfer_ids:
                await cleanup.execute(
                    delete(Approval).where(Approval.transfer_id.in_(transfer_ids))
                )
                await cleanup.execute(
                    delete(MedicineMovement).where(
                        MedicineMovement.transfer_id.in_(transfer_ids)
                    )
                )
                await cleanup.execute(
                    delete(Transfer).where(Transfer.id.in_(transfer_ids))
                )
            # The approval and the receipt each wrote a reading; without these
            # the shelf keeps the units this check invented.
            if transfer_ids:
                await cleanup.execute(
                    delete(StockReading).where(
                        StockReading.source == "transfer",
                        StockReading.raw_payload["transfer_id"].astext.in_(
                            [str(i) for i in transfer_ids]
                        ),
                    )
                )
            if batches:
                await cleanup.execute(
                    delete(StockReading).where(
                        StockReading.source == "transfer",
                        StockReading.raw_payload["batch_id"].astext.in_(batches),
                    )
                )
            await cleanup.commit()

            left = (
                await cleanup.execute(
                    select(Transfer).where(Transfer.id.in_(transfer_ids or [-1]))
                )
            ).first()
            c.ok("check removed the rows it created", left is None)

            # Both shelves go back to where they started — and that is
            # asserted, not assumed. The approval writes the donor's debit
            # keyed by transfer_id and the receipt writes the recipient's
            # credit keyed by batch_id; deleting on one key alone removes half
            # the movement and silently inflates the other facility.
            for fid in filter(None, {facility_id, donor_id}):
                await services.refresh_facility_state(cleanup, fid)
            await cleanup.commit()

            for fid, started_at in shelves_before.items():
                snap = await services.get_snapshots(cleanup, [fid])
                now_at = next(
                    (s.qty_on_hand for s in snap[0].skus if s.sku_code == SKU), None
                )
                c.near(
                    "{0} is back to the stock it started with".format(fid),
                    now_at,
                    started_at,
                    0.5,
                )

    return c.report
