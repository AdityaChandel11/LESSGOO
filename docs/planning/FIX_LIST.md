# SwasthSetu — Fix List

Working list from the 2026-09-29 brainstorm. Each fix is worked one at a time in a
fresh session: "begin fixing #N" means read this file, read the fix's section,
then do it. Nothing here is pushed without Aditya's explicit OK.

## START HERE (for a fresh session)

1. Read, in order: this section → **Standing rules** → **TIERS** → **BUILD ORDER** (near the end)
   → the section of the fix being worked. Don't read the whole file up front; jump by `Fix #N`.
2. Work strictly in BUILD ORDER. Tier 0 first. **No Tier 2 item** until every Tier 0 and Tier 1 item
   is done and deployed.
3. Per fix: name the governing spec section and quote the rule (CLAUDE.md) → classify it
   (bounded / architectural, superpowers:brainstorming) → present the short design → **wait for
   Aditya's yes** → build (TDD for backend logic) → verify (tests, `npm run build` + type-check,
   browser check on a **local** server) → commit one fix per commit on the working branch.
4. When a fix is done, change its heading's `status: TODO` to `status: DONE <commit sha>` and note
   anything that turned out different from this file.
5. **Stop and ask Aditya** before: any write to Render (#11r, #17b, #30, #36 seeds, #79 repair, #81),
   any download (#43 IDSP PDFs, #73 Google logo), any new dependency, any key (#73
   `GOOGLE_MAPS_BROWSER_KEY`, #9 `CHALLAN_SIGNING_KEY`), any spec conflict, any push.
6. "Verified facts" sections were true on 2026-09-29 against commit `e09fb0c` and the live site.
   Re-check a fact if the code has moved; never trust a status line without git.

## Standing rules for every fix

- No fabricated predictions, districts, dates, percentages or "signal sources".
  Every number on screen must trace to a real DB row or recorded run.
- No fake-live animation. Anything scripted is labelled "scripted scenario".
- No "beats any single state" claim (no single-state model exists yet).
- No trust override button. No live FL on Render.
- The system holds **no patient data at all** — never say "patient records stayed".
- No differential privacy, no secure aggregation — say "future work" if raised.
- Look: flat gov palette (navy/gray), no glow, no emoji, no animation, English + Hindi.
- Aggregator is "Aggregator", never "NHM" (we are not NHM).
- Verify: frontend build + type-check, backend `pytest` if the API is touched,
  browser check on a **local** server. Commit per fix. **Do not push without asking.**
- Before building: read v3 §12.2 and §27 (FL fixes) and quote the rule applied (CLAUDE.md).

## Verified facts (from code, 2026-09-29) — use these, don't re-derive

**Model** ([task.py](../../backend/federation/pytorchexample/task.py), [silo.py](../../backend/federation/pytorchexample/silo.py))
- Target: next-7-day mean daily use ÷ trailing-28-day mean, per facility × medicine.
  1.0 = "next week like last four" = the burn-rate rule.
- Input: 28 days of consumption + 4 calendar features (sin/cos day-of-year, monsoon, festival).
- 6 SKUs: ORS, PARA500, AMOX, IRONFA, IVFLUID, ZINC. 4 silos hard-coded: MH, KL, BR, UP.
- Trains on the first 25% of each state's facilities (`FACILITY_FRACTION = 0.25`).
- Small LSTM (hidden 32) + 2-layer head = 8 tensors, 5,697 params, 22,788 B.
  The 8 tensors are layers, NOT "indicators".
- MAE is on the ratio: 0.1106 ≈ "off by ~11% of a facility's usual daily use".
- `num-server-rounds = 8`; row 0 = untrained model evaluated. Fixed count, no stopping rule.
- Recorded run: MAE 1.0674 (round 0) → 0.1623 (r1) → 0.1154 (r2) … 0.1106 final,
  burn-rate baseline 0.1494. Model beats burn rate **from round 2**. 26% lower error vs burn rate.
- Training windows (train split): BR 33,918 · KL 15,624 · MH 35,547 · UP 54,338 = **139,427**.

**Trust / Bihar**
- FL trust = ledger receipt discipline only: `(overdue + 0.5·short) / total / 0.4` over
  the last 60 days of `medicine_movements` ([silo.py:156](../../backend/federation/pytorchexample/silo.py)).
- `flagged_pct` = % of **consignments** unconfirmed-past-window or short. NOT facilities,
  NOT "numbers disagreeing", NOT "reporting windows".
- Bihar's low trust is injected: seed picks one focus state at random for 3× worse
  paperwork, one for 0.4× ([seed.py:751](../../backend/scripts/seed.py)). Synthetic, not a claim about Bihar.
- Weighting: `num-examples = round(windows × trust)`, fed to Flower's FedProx (mu 0.01).

**Known bugs on the live panel** ([federation.tsx](../../frontend/src/federation.tsx))
- L501–508: "37.1% of its facilities flagged" and "facilities whose numbers disagree" — false.
- L468: "Across the whole run" = 9 × one model size; ignores 4 silos uploading per round.
  Recorder stores one model's bytes per round ([inspector.py](../../backend/federation/pytorchexample/inspector.py)).
- L421: "Error at round one" shows round 0's value.

**Not available yet**
- Per-state federated MAE: [evaluate_model.py](../../backend/federation/evaluate_model.py) can
  compute it, but `eval_reports/federation.json` is not in the repo.
- Local-only (single-state) MAE: never computed; needs 4 new trainings.
- Local DB is stale (34 regions vs Render's 36). Render has size limits — see CLAUDE.md.

**Available**
- `forecasts` table, written by [publish_forecast.py](../../backend/federation/publish_forecast.py):
  one row per facility × SKU for the trained SKUs.
- `app/idsp.py` + `outbreaks.tsx`: IDSP outbreak warnings exist.
- `app/ingest.py`, `field.tsx` exist (SPEC_DIGEST §5 is stale on this).

---

## Fix #1 — Federation tab tells the truth, in plain language  · APPROVED · status: TODO

Scope: [frontend/src/federation.tsx](../../frontend/src/federation.tsx); maybe one field on
`/api/federation/inspector` if the correct byte total needs silo count.
Only numbers already recorded. Out of scope: training, forecasts, emergency, map.

1. **Fix false claims.** "37.1% of its warehouse consignments in the last 60 days went
   unconfirmed or arrived short." Replace "numbers disagree" with the real rule.
   Beside Bihar: "Synthetic data: the weak state was chosen at random by the seed."
2. **Header.** "139,427 training examples stayed in their states. Each state sent
   22.3 KB of model weights per round." Replay button labelled "Replay a recorded run
   (Sept 20, 4 states on Flower)". "Run next round is off here" → footnote.
3. **Chart.** Burn-rate baseline drawn as a labelled line at 0.1494. Scale focused on
   rounds 1–8; round 0 noted "untrained start: 1.07". Mark "beats burn rate from round 2".
   Headline: "0.1106 vs 0.1494 — 26% lower error than the burn-rate rule, on held-out weeks."
4. **Arithmetic/labels.** Correct upload total for the whole run. "Error at round one" →
   "Before training (round 0)". "Rows" → "Facility records sent: 0".
5. **Audit details.** Hash, tensor shapes, mu into a collapsed section, plus:
   "FedProx: keeps each state's training close to the shared model, because states' seasons differ."
6. **Official / Technical toggle.** Label swap only; every official label maps to a real
   field. No invented indicators or accuracy claims.
7. **Flat topology diagram.** Four state boxes + one "Aggregator" box, arrows labelled
   "22.3 KB weights per round". No glow, emoji or animation.
8. **Bihar link.** Only if an existing view already opens filtered to one state (audit
   queue or movement ledger). If none exists: say so and skip.
9. **Trust wording:** the federation weight uses receipt discipline ONLY (not trust.py's
   6 signals) — describe exactly that unless #15b has landed.
10. **Honesty note** (UI or notes): no differential privacy or secure aggregation (future
   work); the prototype uses one synthetic DB standing in for four state stores
   (the federated boundary is the per-state query).

Done when: build + type-check pass, browser check on local server, committed, not pushed.

## Fix #2 — Payoff panel from real `forecasts` rows  · MERGED into #45 (build once, show on Federation tab too)

"What the federated model is predicting now": named districts / facilities at risk,
medicine, days of stock, linked to the map. Real rows only — if a forecast doesn't
exist for something, don't show it. No "signal source" attribution (FedAvg can't
attribute). Line under it may say forecasts come from a model trained on shared
weights, not shared records. Must not slip.

## Fix #3 — Local-only vs federated MAE per state  · Tier 2 · status: TODO
Offline: 4 single-state trainings + evaluate_model per state. **Needs Aditya's
decision on which DB** (local is stale; Render has size limits). Only then may the
UI say anything about "better than a state alone".

## Fix #4 — Role-based data separation + architecture note  · UPGRADED → see #77 (Tier 1)
National role sees district aggregates only; facility detail only in state role.
Answers "isn't this just a central server with access controls?"

## Fix #5 — Simulate Emergency  · SUPERSEDED by #44 (outbreak-driven drill)
Must trigger real computation on real outputs (build on `idsp.py` outbreak code),
or be clearly labelled "scripted scenario". No setTimeout theatre presented as live.

## Fix #6 — Add a state by config  · Tier 2 · status: TODO
`SILOS` is hard-coded to 4 and `load_data` raises otherwise. Make joining = config +
one SuperNode. Answers "what happens when a 5th state joins?"

---

## Other glitches (brainstorm in progress — add below as #7, #8, …)

---

## ORDERS TAB (PHC staff workspace) — #7, #8, #9, #9b

### Pitch line (use this, never "nothing can be faked")
"Faking one record is easy. Faking four independent records that agree with each
other is hard — and every disagreement surfaces by itself, in real time."
A visible "What this can't catch" note is part of the design (see #9).

### Verified facts for the Orders tab (2026-09-29)
- Screen: [Orders.tsx](../../frontend/src/workspace/Orders.tsx) + [Incoming.tsx](../../frontend/src/workspace/Incoming.tsx).
  Backend: [movements.py](../../backend/app/movements.py), [workspace.py](../../backend/app/workspace.py).
- `medicine_movements` already holds: batch_id, sku, from_ref (e.g. `WH-MH-NASHIK` or a
  facility id for transfers), to_facility, qty_dispatched, dispatched_at, dispatch_source
  (warehouse/transfer/seed), transfer_id, expected_by, qty_received, received_at,
  received_via (form/sms/ivr/whatsapp/photo), received_by_ref (masked), note, status
  (in_transit/received/short/over/cancelled; "overdue" derived at read time).
- `confirm_receipt` writes a `stock_readings` row with source `transfer`
  ([movements.py:223](../../backend/app/movements.py)) — so stock before/after is real and derivable.
- `stock_readings` has provenance: source, confidence, raw_payload (JSONB), reporter_ref,
  channel_msg_id (unique), superseded_by.
- Gemini bill reading exists ([StockPhoto.tsx](../../frontend/src/workspace/StockPhoto.tsx),
  `read_stock_photo` in [vision.py](../../backend/app/vision.py)) — consumption side only, NOT wired
  to delivery receipts. Photo never stored, only the extraction.
- `delivery_estimate()`, `stockout_date()`, `provenance()`, `rank_donors()` exist in workspace.py.
  `FindSupply`, `RequestStock`, `Incoming` screens exist.
- Render: `LLM_MODE=live`, `FORECAST_MODE=federated`, `COMMS_MODE=simulator`, `MAPS_MODE=osm`.
- "Simulate a request from a neighbour" → `POST /facilities/{id}/demo-request`
  ([api.py:1277](../../backend/app/api.py)): runs the real solver, creates a real proposed transfer.
  Real row, manually triggered. Only in `demo_mode`.
- NOT in schema: vehicle no., challan no., expiry, receiver name, bill image, claims table,
  itemised dispensing. Do not invent them.
- Model has no weather input (only monsoon flag + day-of-year). No "rain forecast" claims.
- Unverified, check at start of #8/#9: can a donor centre mark a transfer "sent" in the UI
  (`record_dispatch()` exists)? Does an officer view already refresh from the `events` feed?

### Fix #7 — Orders tab tells the truth  · APPROVED · status: TODO · size: small
1. Bug: "On its way to you" lists deliveries marked Received. Split into
   **Arriving** (in_transit/overdue) and **Received** (received/short/over).
2. Rename "Simulate a request from a neighbour" → "Demo: have a nearby centre ask you
   for stock" (the request it creates is real; the trigger is manual).

### Fix #8 — Delivery trail on tap (read-only)  · APPROVED · status: TODO · size: medium, no schema change
Each delivery card expands. Only existing rows; anything not available is shown as absent.
1. **Chain:** from_ref → this centre · dispatched · expected-by · received · transit days
   vs this sender's usual (median over its movements).
2. **How it was confirmed:** received_via channel, masked reporter, and a verification
   chip computed per delivery from what exists: dispatch record ✓ · physical count ✓ ·
   scan ✓/— . Never an unconditional "Verified ✓". Seeded rows say
   "Seeded history: dispatch and count only".
3. **Checks derivable today** (Pass / Fail / Couldn't check + reason):
   - counted qty vs dispatched qty
   - transit physically possible vs `delivery_estimate()` for the road distance
   - following stock readings consistent with before + received
4. **What changed in your stock:** reading before → + received → reading after, with
   stock_readings row ids.
5. **What it means:** current days of stock (forecast if a `forecasts` row exists);
   for a short delivery, "without the shortfall you'd have had N more days".
6. **Sender's record:** "This sender delivered short on X of your last Y deliveries"
   (label synthetic — seed shortfalls are random per state).
7. **Short delivery →** button "Find N from a nearby centre", prefilled with the
   shortfall, into existing `FindSupply` / `rank_donors`. Also state truthfully what
   already happens: shows on the officer's movement ledger, lowers facility/state trust.
   No "claim raised" (no claims table).

### Fix #9 — Signed challan + scan-to-confirm + live trace  · APPROVED · status: TODO · size: large
**Keys (ask before starting):** new server secret `CHALLAN_SIGNING_KEY` → `.env` and
Render secret settings. `GEMINI_API_KEY` already live on Render.
**Migration:** one new column for stored check results (e.g. `verification JSONB` on
`medicine_movements`) + image SHA. Not a bulk write — CLAUDE.md size guard still applies.

1. **Signed challan.** On dispatch, the server computes a short code
   (e.g. `K7M2-QX9P`) = HMAC(`CHALLAN_SIGNING_KEY`, batch, qty, destination). Printable
   challan reuses [Receipt.tsx](../../frontend/src/workspace/Receipt.tsx)'s print stylesheet. Same idea as
   the bed-photo rotating code. No new JS dependency (Gemini reads the code; no QR lib).
2. **Scan-to-confirm.** The confirm form gets "Photograph the challan". Existing Gemini
   extraction pipeline reads batch id, quantity, code → prefilled. Pharmacist then
   types the physical count. Photo never stored; only extraction + SHA-256 of the image.
3. **Server checks, stored as a row** (Pass / Fail / Couldn't check + reason):
   - challan code valid for the qty printed (catches a pen-edited challan)
   - batch on paper = batch dispatched
   - paper qty = dispatched qty (short-loaded at source)
   - counted qty = paper qty (loss after arrival)
   - transit physically possible (from #8)
   - same image SHA scanned before (reused photo)
   - later stock readings consistent (from #8)
   - sender's record (from #8)
   Chip: "N of M checks passed · K couldn't run (reason)".
4. **Live trace** on the delivery: timestamped steps with real row ids, each clickable —
   photo received (SHA) → Gemini read (model, latency, fields) → server check results →
   pharmacist count → ledger row written → stock reading written → days of stock
   before/after → trust recomputed before/after → event emitted to officer ledger.
5. **Principle shown on screen:** Gemini never writes the DB. Gemini reads → server
   checks → human confirms → server writes. (Spec: nothing auto-executes.)
6. **"What this can't catch"** note: sender and receiver colluding on the same false
   number; a count typed without counting. Mitigation shown: sender record + trust
   score surface the pattern → audit queue.
7. **Demo (90 s, split screen PHC phone | district officer ledger):** officer approves
   transfer → donor marks sent → signed challan prints → quantity pen-edited on paper →
   receiver photographs → Gemini reads → code check FAILS live with reason → pharmacist
   counts → right screen shows SHORT + trust change within seconds → "Find 250 nearby"
   → new request. Every step a real row. Nothing scripted.

### Fix #9b — Tamper-evident receipt log  · OPTIONAL · status: IF TIME · size: small–medium
Each receipt stores SHA-256 of (its payload + previous receipt's hash) per facility;
"Verify chain" button recomputes. Required on-screen caveat: "Detects edits made outside
the app. Not proof against an administrator who rewrites the whole chain." Call it
"tamper-evident log", never "blockchain".

### Parked (Orders-related)
- Location check at receipt: on a laptop demo the browser location is the laptop's, so
  it would fail or need faking. Revisit only for a phone demo.
- #10 Real climate input (IMD rainfall → model → retrain): needs a feasibility check on
  a machine-readable IMD source first.
- Spot-check requests, IVR keypad simulator, offline sync queue, expiry/vehicle fields.

### Build order overall
Superseded — see "BUILD ORDER (current)" at the end of this file.

---

## MEDICINES TAB (PHC staff workspace) — #11 … #16

### Verified facts (2026-09-29)
- **P0 bug:** `POST /facilities/{id}/stock-photo` ([api.py:1107](../../backend/app/api.py)) writes each
  line's quantity as `qty_on_hand` (absolute shelf level). A delivery slip for 10 wipes a
  shelf of 500 down to 10. Prompt ([vision.py:713](../../backend/app/vision.py)) reads "a bill,
  delivery slip or stock register page" but never asks which.
- Knock-on: FL training treats any drop as consumption ([silo.py:70](../../backend/federation/pytorchexample/silo.py)),
  so a wrong write teaches fake demand. trust.py's consumption/smoothness checks exclude
  `transfer`/`seed` but NOT `photo`, so it also skews trust.
- "Order Paracetamol today — 0.1 days of cover" on Nashik PHC 1 is almost certainly caused
  by the user's test photo setting the shelf to 10 (on Render if done on the live site).
- "verified" label = Gemini live AND ≥1 line committed ([api.py:1162](../../backend/app/api.py)).
  Nothing is checked. "confidence 100%" is the model's self-estimate.
- No document-date check (a 2024-12-13 bill was applied in Sept 2026).
- `skus.unit` exists (tablet/capsule/blister…). Prompt says "Do not convert units" — keep.
- The ledger exists as data, not as a screen: `stock_readings` has source, confidence,
  raw_payload (as_printed, match_score, read_by, document_date), reporter_ref,
  `superseded_by`. Photo is never stored.
- **Two trust systems:** [trust.py](../../backend/app/trust.py) = facility-level, 6 signals
  (attendance vs footfall, consumption vs footfall, beds vs register, receipt discipline,
  implausible smoothness, verification quality). Federation weight
  ([silo.py:156](../../backend/federation/pytorchexample/silo.py)) = state-level, receipt discipline ONLY —
  contradicting [client_app.py:28-33](../../backend/federation/pytorchexample/client_app.py)'s docstring.
- Simulator pieces exist (`/ingest/simulate`, [field.tsx](../../frontend/src/field.tsx)); end-to-end
  behaviour and whether a PHC user can reach it are UNVERIFIED.

### Rejected ideas (don't reintroduce)
Named people ("Pharmacist Rao", "ANM Sunita"), "Play recording" (none exist), emoji
badges, "Add as new medicine" from a photo (catalogue pollution, controlled SKUs),
bounding boxes (#12b cut), refusing negative stock silently.

### Fix #11 — Document-type bug  · APPROVED · P0 · do FIRST · status: DONE f26b8ac
1. Gemini returns `document_type` (delivery slip / issue-dispensing record / stock count)
   and `unit` per line. Server: slip **adds**, issue **subtracts**, count **sets**.
2. Slip rule (no double-count):
   - matches an arriving movement (batch, or sku+qty+sender) → goes through existing
     `confirm_receipt`; adds once.
   - movement already confirmed → "Already confirmed on <date>. Not added again."
   - no dispatch record → held for review, flagged "Delivery with no dispatch record".
3. Held (not refused, not applied): result would be negative; unit ≠ SKU unit; document
   date older than threshold or before the last count for that SKU (would double-count).
4. Replace "verified" with honest chips (see #12). Label confidence "model's own estimate".
   **Provenance label must match the document:** live audit shows the test *bill* labelled
   "Counted by hand, today · Read from a photographed register". Slip → "Delivery slip read by
   Gemini", issue record → "Issue record read by Gemini", count page → "Stock count read by Gemini".
5. Regression tests: slip adds (not overwrites); matched slip doesn't double-count;
   unit mismatch held; negative result held; stale date held.
6. Exclude photo-count semantics issue from trust/FL once fixed (readings carry the right delta).

### Fix #11r — Repair Nashik PHC 1's bad reading on Render  · NEEDS ADITYA'S OK · status: DONE b0e2eb6 (script `scripts/repair_readings.py bill`; Aditya runs it — docs/RENDER_OPS.md step 3)
Supersede the one bad reading (`superseded_by`). Single-row remote write via
`python -m scripts.remote --confirm`; ask for the dashboard % before and after (CLAUDE.md).

### Fix #12 — Review before save  · APPROVED · P1 · status: TODO
- Side-by-side: photo (browser memory only, never stored) | read lines.
- **Centrepiece: before → after preview per line** ("Paracetamol 10 → 510 · Confirm?").
- Editable document-type dropdown (Gemini's guess is a suggestion). Unit shown and
  confirmed; mismatch asks for pack size, never auto-converts.
- Unmatched lines: "Pick from this centre's list" or "Skip".
- Check chips: "Read by Gemini (live)", "Matched N of M", "⚠ Dated X ago",
  "⚠ Dated before your last count", "Same photo scanned on <date>", "Confirmed by you".
- Duplicate detection: SHA-256 of the image stored, not the image.
- "Needs review" queue backed by a **small separate pending table** (not a flag on
  `stock_readings` — every reader would have to exclude it). Pending never reaches
  the map, trust, FL, forecast or solver until resolved.

### Fix #13a — Stock Register tab, minimal  · APPROVED · P1 · status: TODO
Tabs: Medicines · **Stock Register · स्टॉक रजिस्टर** · Orders · Beds · Attendance.
Passbook rows from `stock_readings` (bounded: this facility, recent window, paginated):
time · medicine · change (+10 / −14 / set to 120) · before → after · source chip in text
(Photo · read by Gemini / Delivery confirmed (batch) / SMS·IVR·WhatsApp via channel
simulator / Counted by hand / Seeded opening balance / Used (inferred from counts)).
Tap → evidence (as printed, matched to, match score, model, document date; delivery →
Orders trail #8). Corrections struck through with "corrected by #id". Filters: medicine,
source, needs review. Footer: "In a state deployment this register stays in the state's
own database. This prototype uses one synthetic database for all states."

### Fix #13b — Register: effect on tap + live  · P2 · status: TODO
Per row: days of cover before → after, status change, "sent to district map" (event id).
Live refresh via existing `events` feed (split-screen demo: PHC photo → row → map).

### Fix #14 — "Today" card  · small · status: TODO
Grammar ("needs" → "need"). "0.1 days of cover" → "under 1 day of stock: 10 tablets
left, you use about X a day". Tap → the register row that caused it.

### Fix #15 — Register → trust → federation  · status: TODO
(a) **Live:** 7th trust signal "stock register hygiene" in trust.py — stale documents
applied, unresolved review items, deliveries with no dispatch record, single-day drops
too large to be consumption. Evidence shown in the trust panel / audit queue.
(b) **FL:** state weight = mean of the state's facility trust (matches client_app
docstring). Only visible after a **new recorded run on a laptop** — never on Render.
Until (b) lands, Fix #1 must describe the federation weight as receipt discipline only.

### Fix #16 — SMS/IVR simulator end to end  · CHECK FIRST · status: TODO
Verify: typed SMS/IVR in the simulator → reading row → register (#13a) → map change,
and that a PHC user can reach it. Label "via channel simulator" while
`COMMS_MODE=simulator`. Scope any fix only after the check.

### MEDICINES (cont.) — Away from a screen, Today's team, Data confidence, stale data — #24 … #30

#### Verified facts (2026-09-29)
- **Simulator link broken twice.** (1) `href="/?view=field"` ([Medicines.tsx:228](../../frontend/src/workspace/Medicines.tsx))
  does a full reload → AuthGate's `entered` flag resets → landing page ("front door",
  [AuthGate.tsx:153](../../frontend/src/AuthGate.tsx)); session is intact. (2) Even after resume, a
  `facility_user` always gets `Workspace` ([AuthGate.tsx:168](../../frontend/src/AuthGate.tsx)); the simulator
  lives only in `App.tsx`, and Workspace ignores `?view=field`. The link never had a
  destination for PHC users.
- Simulator ([field.tsx](../../frontend/src/field.tsx)) runs the real ingest spine via authenticated
  `/api/ingest/simulate`; facility identified by sender number (`sender_ref`). Deliberately
  not public. UNVERIFIED: whether a PHC user can send as another centre's number.
- "Today's team 0 of 5": real live count; 0 because nobody checked in today. "Roster" =
  distinct staff seen checking in in the last 30 days ([attendance.py:36-39](../../backend/app/attendance.py)),
  not an establishment list (seed makes 4; a 5th ref from a test/live check-in). Shrinks
  toward 0 as the seed ages.
- "Data confidence 0.58": real, live from trust.py's 6 signals; `why_rows()` exists but the
  staff card shows no reasons. "3 of 3 stock requests open" sits under it, unrelated
  (probably the open-request cap — verify).
- **Stale projection:** Amoxicillin 300 capsules, 3.1 days cover, "runs out 27 Sept" shown on
  29 Sept; last count 5 days ago. Projection runs from last reading date.
- **Map/national totals stale too:** `facility_sku_state` stores days_of_stock and status at
  last-report time; map and national counts read it; the clock never moves them.
- Render free DB is deleted 30 days after creation; CLAUDE.md forbids schedulers/cron/auto-seed.

#### Rejected (don't reintroduce)
Public simulator route; scheduled/cron top-up; query-time timestamp shifting;
rule-generated fake attendance; national-map link for PHC staff.

#### Fix #24 — Field simulator inside the workspace  · APPROVED · P0 · status: DONE 24d5358
Render as a phone screen (reuse field.tsx handset) inside the PHC workspace, opened by
in-app navigation (no reload), authenticated, pre-bound to the user's centre.
**Restrict to the user's own centre's registered numbers** — verify, enforce, test.
Close the loop inside the simulator: reply SMS · row id written · days of cover
before → after · status change · "sent to district map" (event id). Map change shown via
split-screen with officer login. Test: clicking the entry does not reload the page.

#### Fix #26 — As-of-now cover on facility + Today cards  · APPROVED · P0 · status: DONE e3742bc
(Scoped to the PHC workspace: medicine cards, Today card, Gemini briefing input. The officer drawer and map
stay on stored cover until #29. Deploy only after #30 has run.)
Days of cover = stored cover − days since last report (floor 0). Past run-out date →
"Last counted 300 on 24 Sept. At your usual use it would have run out around 27 Sept.
Count the shelf." Status "Count overdue". Today card uses the same logic.

#### Fix #29 — As-of-now for map + national totals  · APPROVED · ships ONLY with #30
Read-time recompute (stored cover − days since last report, reclassify); never overwrite
stored rows; bounded SQL. New 4th legend state: "Not reporting / count overdue"
("silence is an alert"). Without #30 the whole map goes grey — never ship alone.

#### Fix #30 — Manual roll-forward for the demo district  · NEEDS ADITYA'S OK + dashboard % · status: DONE b0e2eb6 (script `scripts/roll_forward.py`; Aditya runs it — docs/RENDER_OPS.md step 1)
Script run by hand before judging: moves the last N days forward for the demo district
(Nashik) and deletes the same amount of old rows **in the same commit** (flat size).
Via `python -m scripts.remote --confirm`. Not scheduled. Fallback: smaller reseed
(`--days 28 --focus-days 60`) only if Aditya asks. #26 stays regardless.

#### Fix #25 — Confidence card explains itself  · P1 · with #15a
Lead with plain words ("Needs attention: 2 of 9 deliveries unconfirmed past their
window"), raw 0.58 second. Each signal that pulled it down, with evidence count;
"what would raise it"; tap → rows. Move the requests line to Orders with truthful
wording once verified.

#### Fix #27 — Today's team card  · small · with #17/#18
"5 people checked in here in the last 30 days" · "Last check-in at this centre: <date>".
Goes 0 → 1 live when #18 lands.

#### Fix #28 — "How this demo data is generated" page  · small · near end
Plain language from seed.py's real rules (random weak state, gaming facilities %,
shortfall rates, history window). Honest rigour.

#### Blocked on Aditya
Render DB creation date · when judging opens · dashboard % for #11r, #17(b), #30.

### MEDICINES (cont.) — Stock requests between centres — #31 … #35 + #36

#### Verified facts (2026-09-29)
- Flow: medicine card "Find supply" → [FindSupply.tsx](../../frontend/src/workspace/FindSupply.tsx) (`rank_donors`,
  nearest 3 that can spare without dropping below their floor) → [RequestStock.tsx](../../frontend/src/workspace/RequestStock.tsx)
  → "Request sent" receipt ([Receipt.tsx](../../frontend/src/workspace/Receipt.tsx)).
- `transfers` statuses: proposed / approved / rejected / completed. No `cancelled`, no reason
  column, no partial quantity. `approvals` stores actor, role, decision, channel, decided_at.
- **Donor inbox exists**: donor's Orders tab "Requests for your stock"
  ([Incoming.tsx](../../frontend/src/workspace/Incoming.tsx)) — "Accept and send" dispatches at once (real
  movement via `record_dispatch`), "Decline" rejects. Endpoints `/transfers/{id}/approve|reject`.
  `decide_transfer` locks the row (`SELECT … FOR UPDATE`, [redistribution.py:688](../../backend/app/redistribution.py)).
- **Missing:** requester-side status view, card chip, timeout, cancel, decline reason, partial accept.
- **Live bug:** cap counts only `status='proposed'` facility requests ([workspace.py:617](../../backend/app/workspace.py));
  nothing expires them → Nashik PHC 1 showed "3 of 3 stock requests open" → likely locked out.
  Cap message says "Confirm or cancel one" but cancel does not exist.
- Receipt leaks raw values ("proposed", "donor_facility") and shows "Estimated delivery"
  for an unapproved request.
- Donor figures ("can spare 519", "keep 14.0 days") come from stale stored state (same as #26).
- Donor search is statewide, cross-district possible ([redistribution.py:363](../../backend/app/redistribution.py));
  nearest 3 shown, so usually all Nashik. Never cross-state (by design). Warehouse stock is
  not modelled anywhere.
- CORRECTED (checked live 2026-09-29): landing page HAS one-click demo roles, no password —
  Administrator · Maharashtra NHM state officer · Nashik district logistics officer ·
  Pharmacist, Nashik PHC 1. There is NO donor-centre role (e.g. PHC 13) and NO demo script.
- Receipt screenshot (Paracetamol / PHC 8, SS-002231) vs the Amoxicillin / PHC 13 flow: probably
  an earlier request (file dated 28 Sept). VERIFY by creating one and matching the receipt.

#### Rejected (don't reintroduce)
In-app "Act as donor" switch (impersonation in auth); auto-responding simulated donors;
background timeout job (CLAUDE.md forbids background writers); district store as a donor
(warehouse stock not modelled — would be invented).

#### Fix #31 — Request basics  · APPROVED · P0 (Tier 0) · status: DONE 895b2a7
1. **Derived timeout:** a `proposed` request older than a window (configurable; minutes in
   demo mode) reads as **"No response"** — computed at read time like movement "overdue";
   nothing written. The cap counts only live (unexpired) requests → unlocks PHC 1.
2. **Race guard:** inside `decide_transfer`'s existing row lock, re-check expiry/cancel;
   expired or cancelled cannot be approved → donor sees "This request expired at <time>.
   Ask <centre> to send it again." Tests: approve-after-expiry fails; approve-after-cancel fails.
3. **Requester Cancel:** new `cancelled` transfer status (small migration on the check
   constraint) + endpoint + button. Cap message then tells the truth.
4. **Plain words:** "Awaiting reply from Nashik PHC 13 (pharmacist)", never `proposed` /
   `donor_facility`.
5. **Estimate:** "If approved by 14:00 today, about 29 Sept" — never a flat date.
6. **Medicine-card chip** replaces "Find supply" while a request is live: "Requested 519 from
   Nashik PHC 13 · awaiting reply · 3h"; tap → tracker (#32). Blocks duplicates.
7. Verify the receipt mismatch.

#### Fix #32 — Requester tracker  · Tier 2 (Tier 1 if the demo uses requests) · status: TODO
"My requests" on Orders + per-request timeline from real rows only: created → decision
(who, role, when, reason) → dispatched (movement) → received (quantity confirmed).
Replaces the static receipt; Print kept.

#### Fix #33 — Donor answers properly  · Tier 2 · status: TODO
- Decline reason picklist (column on `approvals`): our stock is lower than shown / expecting
  local surge / no transport / pending own order / other (text).
- **"Our stock is lower than shown" requires the recounted number.** It becomes a real
  stock reading ("counted by hand"). Only a **material gap** vs the register feeds the
  #15a "register hygiene" trust signal (threshold set at build time). No unverifiable claims
  into trust.
- Partial accept: donor chooses qty ≤ requested; requester sees the remainder.

#### Fix #34 — Shortfall honesty  · Tier 2 · needs #26 logic + #30 run first · status: TODO
"You need 1,077; Nashik PHC 13 can spare 519 — 558 still uncovered" + next donor.
Donor figures as-of-now with count age; donor's district on each card. Honest note:
"District warehouse stock is not in this system."

#### Fix #35 — Escalation + officer metrics  · Tier 2 · status: TODO
Derived "Unanswered requests in your district" in the officer queue (query, no writes).
Metrics: response rate, median response time, decline reasons, non-responding centres
(facility-level only).

#### Fix #36 — Judge alone on the site  · Tier 1 · status: PARTIAL b23fa19 (PHC 13 donor role + demo script in code; Render account and seeded requests pending)
- **Seeded requests in every state** (accepted, declined with reason, partly accepted,
  expired, received), ~10 rows for Nashik, created **through the real code path**. Remote
  write → Aditya's OK + dashboard % before/after.
- **Demo roles exist already** (one-click, no password). Add: a **donor-centre role**
  (e.g. "Pharmacist, Nashik PHC 13") so the two-login demo works, and a **5-step demo script**
  on the landing page. Demo writes stay scoped to the Nashik sandbox (verify
  `reset_nashik.py`); global request cap stays on as the size guard.
- Demo (video): two real logins side by side — PHC 1 requests → PHC 13 declines "stock lower
  than shown" with a recount → PHC 1's chip turns Declined + reason + next donor → trust
  panel shows the new evidence.

#### Deploy-order rule
#30 (roll-forward) runs on Render **before** #26, #29 or #34 is deployed; otherwise Nashik's
cards and donors flip to "count overdue".

---

## ATTENDANCE TAB (PHC staff workspace) — #17 … #23

### Verified facts (2026-09-29)
- For the demo account (no `staff_ref`), `GET /me/attendance` ([api.py:2083](../../backend/app/api.py))
  returns `attendance.synthetic_record()` — all 30 days incl. today generated per request
  by a seeded RNG ([attendance.py:444](../../backend/app/attendance.py)), never stored. Labelled
  "synthetic demo", but nobody actually checked in. In demo mode, real accounts also get
  `with_synthetic_today()` filling an empty today.
- "biometric scanner" exists ONLY in that generator; attendance.py's own header says the
  platform has no biometric capture.
- Ping tally: answered − confirmed = IVR answers (IVR carries no location → "unlocatable").
- Real machinery exists but isn't wired to this screen: `POST /facilities/{id}/checkins`
  → `record_checkin` → geofence (GPS 250 m, cell tower 2 km) → row + event;
  `facility_attendance` (count-only officer view); SMS check-in via ingest.
- Date is NOT a bug: screenshot's top row is "Tue today 29 Sept".
- Seeded history likely stops at the Render reseed (21 Sept) → 22–28 Sept may be blank
  once the demo account reads stored rows. VERIFY first.
- Seed roster: 4 staff per PHC, 8 per CHC.

### Rejected (don't reintroduce)
Location override / "demo location" toggle; any "biometric" wording; claiming WebAuthn
identifies a person; suppressing per-centre counts (breaks the personnel pillar).

### Fix #17 — Stop printing  · APPROVED · P1 · status: TODO
1. Link the demo account to one seeded staff member; record comes from stored rows.
   Remove `synthetic_record` / `with_synthetic_today` from the demo path.
2. Remove "biometric scanner" everywhere.
3. Ping tally, four ways: confirmed at the centre / answered by call, location unknown /
   answered outside the boundary / unanswered.
4. Privacy line, truthful: "Your district officer sees how many staff were present at this
   centre and how it was verified — never your personal record. At a small centre, a count
   can point to a person."
5. History gap: verify. Then either (a) show honestly "No records since the demonstration
   data was loaded on 21 Sept", or (b) backfill the last few days for ONE demo staff member
   on Render — remote write, needs Aditya's OK + dashboard % before/after. Preferred: (b).
6. Today is never blank-and-dead: empty today shows a large "Check in now" (#18).

### Fix #18 — Check in now, live  · APPROVED · P1 · status: TODO
Button → browser geolocation with accuracy radius → existing `POST /checkins` → server
verdict → row appears in Day by Day, event emitted. Check-out too.
**Verdict rule (server-side, distance vs accuracy):**
| Condition | Result |
|---|---|
| distance − accuracy > boundary | Outside the centre (certain) |
| distance + accuracy ≤ boundary | Inside the centre |
| otherwise (overlap) | Couldn't verify — accuracy shown |
| permission denied / no fix | No location — unverified (check-in still saved) |
Shown: distance, method, accuracy. **Tests:** all four verdicts, incl. laptop case
1,140 km ±1.2 km → Outside.

### Fix #20 — Officer side live  · APPROVED · P1 · status: TODO
(Live audit: the officer facility panel already shows "Staff on duty 0 of 5 on the roster checked in
today" per centre. Missing: district/state roll-up → #83, and live refresh.)
District officer's per-centre count ("Nashik PHC 1: 3 of 4 present, 2 verified") updates
within seconds via events. National/state roles see district roll-ups only (aligns with
Fix #4). CHECK FIRST what `Team.tsx` and `facility_attendance` already show.

### Fix #19 — Random check on demand  · status: TODO
Labelled demo trigger ("in real use the server picks the moment") sends a check through
the channel simulator; reply recorded as confirmed / outside boundary / location unknown /
unanswered; tally updates. CHECK FIRST whether a ping-reply endpoint exists.

### Optional
- **#21 Device passkey (WebAuthn):** "checked in from this staff member's registered
  phone, unlocked by its owner." New server dependency → ask first. Never "biometric".
- **#22 Double-critical flag:** stock critical + no verified staff today — only where
  today's attendance data exists, else "unknown".
- **#23 Demo venue facility:** clearly labelled "Demo venue (not a real PHC)" at the demo
  location, for a live geofence pass.

### Demo loop (60 s)
Judge's laptop checks in → "Outside the centre: 1,140 km away (±1.2 km)" → phone at demo
venue passes → officer count 2 → 3 → random check goes unanswered, tally shows it.

---

## OFFICER — REDISTRIBUTION TAB — #37 … #40, #46 … #52

### Verified facts (code + live site as Maharashtra state officer, 2026-09-29)
- Solver ([redistribution.py](../../backend/app/redistribution.py)): OR-Tools min-cost flow + greedy fallback;
  donor floor; controlled SKUs → manual queue; cold-chain distance cap; max km; same state,
  cross-district allowed; unmet needs reported as "escalate to state warehouse".
- **Donors already decide.** Live MH list: every trip reads "Awaiting the donor centre's
  decision" (commit 02a3a50). But the **Administrator** view (Rajasthan screenshot) shows
  "Approve trip (4)", and another view showed "View only" — role behaviour is inconsistent.
- **`generate_plan` deletes every `proposed` transfer in the state for the SKU**
  ([redistribution.py:434-440](../../backend/app/redistribution.py)) — no `triggered_by` filter → wipes pharmacists'
  peer requests (tagged `facility_request`) AND solver proposals donors are mid-deciding.
  "Simulate emergency" calls exactly this ([liveloop.tsx:307](../../frontend/src/liveloop.tsx)). Solver also
  ignores stock already promised to open requests (can double-promise a donor).
- **Cross-district not visible:** every MH trip seen live is within one district
  (Nanded→Nanded, Nagpur→Nagpur…). The drill is Nashik-only at both ends by construction.
- **Back button:** live test — "no back history". The app rewrites the URL without adding
  history entries, so Back leaves the site. No breadcrumb. Switching state = reload/map.
- **"Re-run plan"**: unexplained to a judge/official; takes ~14.6 s; deletes and rebuilds rows.
- **"Why?" (Gemini)** restates numbers already on the card ([api.py:540](../../backend/app/api.py): from the
  solver's own figures, rules fallback, ungrounded-figure check). No new information →
  decorative. Not to be counted as the Gemini pillar.
- Trip numbers cryptic: "Receiver <1 day → 2.0 days (14 days with 1 more)".
- Header claims "182 facilities helped" before anything has moved.
- State list shows dozens of "<1 day" centres — stale stored status (#29/#30).
- Panels have nested scrollbars (visible in screenshots).

### Decision recorded — approval model (spec v3 §1.6: "always ends in human approval, nothing auto-executes")
Aditya's direction: higher authorities should not be a gate on routine trips.
**Resolution that satisfies the spec:** the human approval is the **donor centre's**
(already built). Officers get **oversight + exceptions only**. **Rejected:** Sonnet's
"auto-execute unless an officer holds it" — violates the spec invariant.
Officer/admin "Approve trip" on routine trips is removed (consistency). Kept: controlled-
substance manual queue; the labelled sandbox drill where an officer acts for the donor.

### Fix #37 — Re-plan must not destroy work  · Tier 0 · status: DONE ff742b0
`generate_plan` never deletes `facility_request` transfers, nor solver proposals a donor has
already opened/decided; solver subtracts stock already promised to open requests. Donor sees
"This recommendation was updated at <time>" if one is replaced. Regression tests.

### Fix #38 — Cross-district, truthfully  · Tier 1 · status: TODO
Verify locally whether real plans produce cross-district trips. Badge "Nashik → Ahmednagar ·
cross-district". Summary: "N trips · M cross-district · K needs unmet → escalate to state
warehouse". Widen drill sandbox to Nashik + one neighbouring district (reset script covers
both). If cross-district trips don't occur naturally, don't fake them — say why; the unmet
list carries the escalation story.

### Fix #39 — Redistribution tab = oversight, not an approval queue  · Tier 1 · status: DONE 31c60ee
1. Plain header: "The system recommends 108 transfers in Maharashtra, computed 14:02 from
   stock as of today. Each goes to the donor centre to accept — nothing moves without them."
2. "Re-run plan" → **"Update recommendations"** + "Last computed 14:02 · 37 stock reports
   since" + progress while solving. Only state officers for their own state.
3. **Pipeline with counts:** Recommended → Accepted by donor → Dispatched → Received →
   Verified. Stuck lists: no donor reply past window; dispatched but not received past
   expected-by.
4. **Exceptions queue = the officer's only actions:** controlled-substance manual queue;
   no donor response (#35); declined "stock lower than shown" (#33); low-trust end (#51);
   unmet needs → state warehouse.
5. Remove routine "Approve trip" for officer/admin. "182 facilities helped" → "182 would be
   lifted above 3 days if accepted".

### Fix #40 — Recommendations reach the donor PHC  · Tier 1 · status: TODO
Verify the donor's Orders → "Requests for your stock" shows solver recommendations (not only
peer requests), labelled "Recommended by the state plan", with Accept / Decline (reason #33).
Recipient sees it arriving (exists).

### Fix #46 — "Why?" that tells a first-time reader something new  · Tier 1 · status: TODO
From solver data only: why this donor (next-best alternative and why not — e.g. "Nanded PHC 3
is closer but would drop to 9 days, below the 14-day floor"); what happens if nobody sends
("Receiver runs out 30 Sept"); how fresh the numbers are (donor's last count date + method,
receiver trust band); route is a straight-line estimate. Gemini writes it in plain English +
Hindi, grounded (existing ungrounded-figure check). Label: "Explanation written by Gemini from
the solver's figures. The plan is computed by OR-Tools." Needs a post-hoc next-best-donor calc.

### Fix #47 — Navigation  · Tier 1 · status: TODO
Corrected by the live audit: a header breadcrumb exists but truncates ("India › An…"); the facility
panel has "← Maharashtra"; the map has "Back to all of India". Missing: browser Back (no history
entries). Fix: pushState on view/state/district/facility changes; breadcrumb never truncates the
current level (wrap or abbreviate the parent); roles pinned to one state see it within that state.

### Fix #48 — Plain words for trip figures  · Tier 1 · small · status: TODO
"Receiver <1 day → 2.0 days (14 days with 1 more)" → "Nanded PHC 9 has under 1 day left.
This delivery gives it 2 days; one more delivery would reach 14." Donor line likewise.

### Fix #49 — Receipt verification + outcomes  · Tier 1 (small) · status: TODO
Sent vs received per completed transfer (ledger) — mismatches flagged as leakage signals.
Outcomes from real rows: median hours recommendation → receipt; centres lifted out of
critical after receipt. "Stock-outs averted" only if labelled an estimate.

### Fix #50 — National tier: state surplus/deficit + chronic receivers  · Tier 2 · status: TODO
Admin/national: surplus/deficit per state per medicine (aggregate, for central procurement —
no cross-state transfers). Centres that receive help repeatedly → "raise its indent".

### Fix #51 — Trust on trip ends  · Tier 2 · status: TODO
Trust band on donor and receiver. Low trust → flagged to the officer's exceptions queue.
**Never withhold medicine from a low-trust centre** — patients shouldn't pay for paperwork.

### Fix #52 — Panel layout  · Tier 1 · status: TODO
One scroll container per panel (parent flex column, list `flex:1; min-height:0;
overflow-y:auto`); remove nested scrollbars in Redistribution and Federation; collapse the
header block on scroll; Field reports → top-bar badge opening a drawer; list keeps ≥ ~60% of
panel height; test at 1366×650 and phone width. Tier 2: list virtualization (new dependency →
ask first), mobile bottom sheet.

### Rejected (officer)
Auto-execute trips (spec §1.6); near-expiry moves (no expiry data in schema); staff
reassignment suggestions (new domain, individual-level); excluding low-trust centres from
receiving stock.

---

## EMERGENCY EARLY WARNING — #41 … #44

### Verified facts (2026-09-29)
- [idsp.py](../../backend/app/idsp.py): 40 real rows parsed from NCDC IDSP weekly reports (weeks 53/2020 and
  24/2023) into `data/idsp_outbreaks.json`; source cited, report weeks printed. Parser is regex.
- **"Stocking advice" demand rise % is from an RNG** seeded per outbreak
  ([idsp.py:183](../../backend/app/idsp.py)), labelled "(simulated)" — painted.
- **Outbreaks feed nothing**: advice is a sentence ("raise the reorder target by X%… let the
  redistribution plan fill it"); no use in forecast, days of cover or solver. Spec v3 §12.5
  ("temporary multiplier into the existing redistribution solver") is NOT built.
- "Simulate emergency" ([liveloop.tsx](../../frontend/src/liveloop.tsx)) = real stock-out drill: drops one Nashik
  facility to 1.5 days via its own burn rate → spine recomputes → real solver → human approval
  → stock moves; prints each API call. Honest and strong — but no outbreak in it, and it
  triggers #37's deletion bug.

### Fix #41 — Outbreak → surge → warning → plan, computed  · Tier 1 (core of the challenge) · status: DONE 0139405
(Which medicines: idsp.DISEASE_MEDICINES; how much: observed 14-day rise ≥ 10%, else the officer's assumption — the spec's commodity multipliers are not used. Declaring takes can_plan_state.)
Read v3 §12.5 first. An active outbreak (IDSP row or officer-declared) applies a temporary
demand multiplier to mapped medicines in that district; days of cover recomputes; warnings
read "Nashik · ORS · runs out 4 Oct (19 Oct without the outbreak)"; solver raises target
cover there → pre-positioning trips. Multiplier source must be honest: (a) district's own
observed 14-day consumption rise where data shows one, else (b) officer-set expected surge,
labelled an assumption. **Remove the RNG percentage.**

### Fix #42 — Gemini reads the IDSP report  · Tier 1 · spike first · status: BLOCKED (code 9fcdcc3; live spike needs GEMINI_API_KEY — BLOCKERS.md)
Officer uploads this week's IDSP PDF → Gemini extracts rows (state, district, disease, cases,
deaths, dates, status) → regex parser cross-checks; disagreements flagged → rows feed #41.
Load-bearing Gemini: remove it and "new report in → warnings out" disappears.

### Fix #43 — Fresh outbreak data  · Tier 1 · NEEDS ADITYA'S OK to download · status: DONE 18a9393
(Weeks 31–32/2026 parsed; the 2026 layout needed a broken-date fix. All 95 rows are past the 14-day window, and the panel says so.)
Fetch the latest available IDSP reports (ask before downloading). Until then label old rows
"historical report used to demonstrate the pipeline".

### Live-site findings for emergency (tested 2026-09-29, admin + MH officer, read-only)
- National outbreak panel: "40" outbreaks, weeks 53/2020 + 24/2023, first rows e.g. Longleng NL
  2020, Ballari KA "in network". Plain table + "Stocking advice (demo)" cards whose "demand up
  44% (simulated)" is the RNG. Reads as a printed archive, not a warning.
- **PHC staff never see outbreaks** — only `App.tsx` imports `outbreaks.tsx`; workspace has none.
- **`outbreak_events` table exists and is unused** (district, disease_category, radius_km,
  severity, triggered_at) — the ready slot for live outbreak data.
- **NCDC source checked:** https://ncdc.mohfw.gov.in/includes/WeeklyOutbreaks.php lists weekly
  reports per year as direct PDFs at predictable paths
  (`/uploads/weekly_outbreaks/2026/week8_<timestamp>.pdf`). 2026 weeks 1–8 visible (listing may
  show 8 per year behind a "+" — latest week UNVERIFIED). Upload timestamps ≈ 8 Sept 2026 →
  publication lag is likely large. Site "last updated 28-09-2026".

### Fix #57 — Live IDSP intake ("auto-refresh when a new weekly report is released")  · Tier 1 · DECISION NEEDED · status: TODO
- Fetch the NCDC listing → detect a new week → fetch that PDF → **Gemini extracts rows**
  (#42) → regex parser cross-checks, disagreements flagged → rows into `outbreak_events`
  (extend columns as needed: state, cases, deaths, dates, status, source week, source URL) →
  #41 surge recompute → event emitted → warnings update on every screen.
- **Trigger — CLAUDE.md forbids schedulers/cron/background writers on Render.** Options for
  Aditya: (a) **check-on-use**: when the outbreak endpoint is read and the last check is > 24 h
  old, check NCDC (one small bounded write per new week) — automatic, no scheduler;
  (b) manual "Check NCDC now" button for officers; (c) external weekly cron (e.g. GitHub
  Actions) — a scheduled writer, needs Aditya's OK + retention policy in the same commit.
  Recommended: (a) + (b). Retention: keep the last 12 weeks in the DB.
- **Freshness shown honestly:** "Latest IDSP report: week N 2026, uploaded <date>, checked
  <time>". If NCDC's lag is weeks, say so — the warning is as fresh as the government's report.
  Between reports, #41's observed consumption trend is the nowcast.
- Downloading NCDC PDFs during development needs Aditya's OK (#43).

### Fix #58 — Outbreak alert on the PHC staff page  · Tier 1 · after #41 · status: TODO
When an active outbreak is in the centre's district: "Cholera reported in Nashik district
(IDSP week 30, 2026)" → medicines it drives (from the disease map) → this centre's days of
cover at the surge rate → one-tap "Find supply" / request (#31). Nothing shown when there is no
outbreak nearby. Same data as the officer view, never a separate copy.

### Fix #59 — Outbreak panel that warns instead of archives  · Tier 1 · small · status: TODO
Sort by recency, then "in network" first; each row → number of network facilities in that
district + medicines at risk + link to the surge warnings (#41/#45); outbreak districts as a
map layer; report week + age on every row ("reported 3 weeks ago"). Old rows grouped under
"Historical" (#43). Remove the RNG percentage (#41).

### Fix #44 — Drill becomes outbreak-driven  · Tier 1 · after #37, #38, #41 · status: DONE 8f339bb
(Checked locally end to end: declare → 50 warnings → 52 pre-positioning trips → accept → dispatch → receipt. Follows a trip inside Nashik until #38 widens the sandbox.)
"Simulate emergency": pick/declare an outbreak → surge (#41) → warnings → plan (cross-district
via #38's wider sandbox) → donor accepts → dispatch → receipt. If not outbreak-driven, rename
"Stock-out drill".

---

## NATIONAL — #45

### Fix #45 — National "Next 14 days" warning strip  · Tier 1 · **absorbs Fix #2** · status: TODO
Top district × medicine pairs with projected run-out date, from `forecasts` + as-of-now cover
(#26/#29) + outbreak multiplier (#41). Each links to its recommendations. Fix #2's payoff panel
is the same data seen from the Federation tab — build once, show in both places.
### Live-site findings, national (tested 2026-09-29, admin view, read-only)
- Landing: clear headline ("Medicine on the shelf, before the patient arrives"), 4 one-click
  demo roles, "How it works" section. Works. No demo script (#36).
- National overview: map of India with a ring per state (critical count in centre),
  660 critical / 1,468 at risk / 1,382 adequate, states ranked by critical count, 3,510
  facilities · 36 states & UTs · 157 districts. Counts are stale stored status (#29).
- "Live" badge: `/api/events?after=87` — only 87 events have ever happened on the deployed
  site; the badge polls, nothing moves unless someone acts.
- Movements tab: strong ("2,64,247 units unaccounted for across 1353 short deliveries"), but a
  flat list of 2,754 cards, no roll-up by warehouse; newest rows are from August (ageing seed).
- Data trust tab: national level is only an alphabetical state list with no scores; the map
  still shows stock, not trust. State level is strong and specific ("Staff recorded present on
  3 of 3 shifts where no patients were logged") but takes ~15 s to score and rests on tiny
  samples ("3 of 3") because ~6 seeded days remain in the 14-day window — shrinking daily.
- There IS a map-level "Back to all of India" control; the browser Back button and a panel
  breadcrumb are still missing (#47).
- One 401 in console on load (auth check before sign-in) — harmless.

### Fix #60 — National Data-trust overview + evidence threshold  · Tier 2 (threshold Tier 1) · status: TODO
National: states ranked by share of low-confidence facilities, from the one materialized
trust copy spec §12.6 allows (refreshed from `trust.compute()`); map switches to trust colours on
this tab. **Tier 1 part:** show the sample size on every signal ("based on 3 shifts") and don't
score a signal below a minimum number of observations. Ages with the seed → pair with #30.

### Fix #61 — Movements roll-up by warehouse  · SUPERSEDED by #76
Leakage leaderboard by source warehouse (% short, units lost, trend), state filter, newest
first. Complements the per-sender record in #8.

### Fix #62 — Honest "Live" badge + scale statement  · Tier 1 · small · status: TODO
"Live · last change <time ago>" instead of an unconditional "Live". Scale line where the
facility count appears: "3,510 synthetic facilities in 157 districts — a sample; India has far more
PHCs/CHCs and districts" (e.g. Maharashtra shows 8 districts; cite real numbers only from a public
source such as Rural Health Statistics, or omit).

---

## GOVERNANCE, MOVEMENTS, DATA TRUST, FIELD REPORTS — #74 … #80

### Verified facts (screenshots of Andaman & Nicobar views + code, 2026-09-29)
- **Officers can write facts about a centre.** `can_submit_reading` ([auth.py:112](../../backend/app/auth.py)):
  admin → any facility in India; state officer → any in their state; block_mo → any in their
  district; facility_user → own. Delivery confirmation uses it ([api.py:1742](../../backend/app/api.py)), so the
  Movements tab shows an admin a "Quantity actually received … Confirm receipt" form for Port Blair
  PHC 1. **The two-sided ledger collapses: the same side can settle both halves.**
- **Receipt quantity is pre-filled with the dispatched quantity** (797 shown) — nudges "confirm in
  full". Same in the PHC Orders form ([Orders.tsx](../../frontend/src/workspace/Orders.tsx) `useState(String(m.qty_dispatched))`).
- **Everyone signed in sees everything**: [auth.py:131](../../backend/app/auth.py) — "The national picture stays
  visible to every signed-in user … scope narrows what you may change, never what you may see."
  So the national/admin role browses any state's facility-level rows; the "states don't see each
  other's live data" premise of federation is not enforced anywhere except training queries.
- **Warehouse side is seeded**: dispatch rows like `WH-AN-PORT-BLAIR` have no warehouse user or
  integration behind them.
- **Data trust Gemini text speculates a cause**: "…most likely stem from routine administrative
  oversights such as registers not being filled in." Not in the evidence — invented.
- Data trust is a ranking with no action: no "schedule visit", no visit outcome.
- **"Send test report"** ([App.tsx:473](../../frontend/src/App.tsx)): demo-mode button that picks a *healthy* facility
  the user may report for (admin = anywhere in the country) and writes a real low ORS reading so it
  visibly turns red. Not confined to the Nashik sandbox. Screenshot: "Port Blair PHC 1 · ORS 4".
- **Field reports tab** (officer console) = the SMS/WhatsApp/IVR simulator ("send a message as one of
  its registered handsets") + a footer feed of this session's reports. Nothing says these are
  messages *centres send in*; an officer "sending" reports makes no sense.

### Fix #74 — Only the centre itself reports facts about the centre  · Tier 0 · status: DONE 1503640
Receipt confirmation, stock readings, **staff check-ins** and **ward/bed reports** only by users of
that facility or its registered handsets. Live audit: the officer facility panel offers "Check in from
the facility" / "Check in by phone call" (proxy attendance — exactly what attendance checks exist to
stop) and drawn ward photos. Officers/admin: view, **"Chase"** (reminder via the channel simulator),
escalate. Keep the labelled Nashik-sandbox drill exception. Tests: admin/state/district confirm,
check in, or report beds for a centre → 403. Review every `can_submit_reading` caller.

### Fix #75 — Blind receipt  · Tier 1 · small · status: TODO
Receiver's quantity field starts **empty** (a real procurement control: count before seeing the
challan figure). Dispatched quantity revealed after submission, with the difference. PHC Orders,
#9 scan flow and any other receipt form.

### Fix #76 — Movements the way an official reads it  · Tier 1 · status: TODO  (supersedes #61)
Summary first: by warehouse/route — % short, units unaccounted, unconfirmed by age bucket (3–7 / 7–14
/ 14+ days), trend; by district. Cards become drill-down. Plain header: "Warehouse dispatch records
are simulated in this prototype; in deployment they come from the state's e-Aushadhi/DVDMS system."

### Fix #77 — Data governance tiers, enforced (the premise of "federated")  · Tier 1 · status: TODO  (upgrades #4)
- The challenge asks for national visibility, so **aggregates at national level are legitimate**
  (states already report to MoHFW). What stays in the state: facility-level rows and raw histories.
- **API enforcement:** admin/national → state and district aggregates + model outputs only; state
  officer → own state's facility-level data; another state → aggregates; district officer → own
  district. Opening a facility outside scope shows "Held in <State>'s store — the national view sees
  district summaries only."
- **#77b (strong demo):** enforce in Postgres too — per-state schemas or row-level security + a
  national DB role with no SELECT on facility-level tables; show the database itself refusing.
- Until #77 lands, the Federation tab must not imply "states can't see each other's data".

### Fix #78 — Data trust becomes a loop  · Tier 1 · status: TODO
1. Gemini explanation restates evidence and says **what to check on the visit**; never guesses a
   cause. Add to the grounding check: reject cause-speculation phrases.
2. Actions: "Schedule visit" → "Visited — numbers confirmed / problem found / couldn't verify"
   (small table); outcome shown as evidence and feeds the score.
3. Live: an action at the PHC (confirm an overdue consignment, answer a spot check #71/#19) →
   trust score before → after in both views via events.
4. Evidence thresholds from #60.

### Fix #79 — "Send test report" must not paint real centres red  · Tier 0 · status: DONE cbc8011 (code) + b0e2eb6 (repair script `scripts/repair_readings.py test-reports`; Aditya runs it — docs/RENDER_OPS.md step 4)
Remove it from the officer console, or confine it to the Nashik sandbox with a label ("Demo: sends
a low ORS count from a Nashik centre"). Never pick a healthy centre elsewhere in India to make it
critical. Check what's already been written on Render (e.g. Port Blair PHC 1 ORS 4) — repair only
with Aditya's OK.

### Fix #80 — "Incoming field reports", not a send button  · Tier 1 · status: TODO
Officer tab becomes a live feed of every inbound SMS/IVR/WhatsApp/photo report in their area: masked
sender, raw message, parsed result, accepted / held / rejected + reason, effect (stock/beds/
attendance changed, event id). One line: "Messages centres send in. Nothing is sent from here."
The handset simulator moves to the PHC (#24) or a labelled "Demo handset" drawer.

---

## FINAL LIVE AUDIT (2026-09-29) — #81 … #95

Tested on https://swasthsetu-m4x5.onrender.com as Administrator and as Pharmacist, Nashik PHC 1, at
1366×768 and 375×812. Read-only except one Gemini briefing call. Not clicked: Re-run plan, Simulate
emergency, Send test report, Confirm receipt, Approve, Request stock.

### Findings (each maps to a fix below or to an existing one)
- **The federated forecast is used nowhere on the live site today.** `forecast_max_age_days = 8`
  ([config.py:276](../../backend/app/config.py)); forecasts were published ~20–21 Sept → all stale → every centre falls
  back to burn rate. Nashik PHC 1 (a training state) shows no "forecast" badge and every card reads
  "from the last 28 days of readings". Republishing is size-flat (`ON CONFLICT … DO UPDATE`,
  [publish_forecast.py:110-116](../../backend/federation/publish_forecast.py)). → #81, #84
- **Forecast is nearly invisible even when fresh:** a tiny "forecast" badge with a tooltip
  ([panels.tsx:589-591](../../frontend/src/panels.tsx)) and one phrase on the PHC card. → #84
- **Officer console has no Beds and no Staff view.** Modes are stock / transfers / movements / trust /
  federation / field ([App.tsx:44](../../frontend/src/App.tsx)). Beds and attendance appear only inside one facility's
  panel. Two of the three resources in the challenge have no national/district visibility. → #83
- **Officers write facts for a centre beyond deliveries:** facility panel offers "Check in from the
  facility" / "Check in by phone call" (proxy attendance) and drawn ward photos to an admin. → #74
  (extended)
- **Two trust numbers for one facility at the same time:** PHC card "0.58", officer panel "64 out of
  100". → #89
- **Three bed figures across two views:** PHC header 3/7, PHC reports 13/20 and 2/7, officer panel
  "2 of 7 … Unverified, last verified 10 days ago". → #68 (extended)
- **Every PHC card contradicts itself:** e.g. Zinc "5.4 days … At risk … runs out about 29 Sept"
  (today) — projection runs from the count date. → #26 (confirmed, wider than Amoxicillin)
- **The test bill is live on Render:** Paracetamol "10 tablet · via photo · 3 h ago" → Critical; and it
  is labelled **"Counted by hand, today · Read from a photographed register"** although it was a
  bill. → #11 (provenance label), #11r
- **Gemini daily briefing** (live, `gemini-3.5-flash-lite`, EN/हिं toggle exists): one sentence —
  "Order more Paracetamol 500mg immediately as stock is critically low at only 10 tablets." Repeats the
  rules line; built on the corrupted reading. Decorative. → #85
- **Officer console on a phone is broken:** at 375 px the map collapses to **0 px wide**, the side
  panel overflows, tabs and KPI tiles are cut off. → #87
- **Deep links don't open where they point:** a link to Nashik (zoom 11) lands on the front door; after
  "Continue" it opens Maharashtra's state view, not Nashik. → #88
- **Breadcrumb exists but truncates** ("India › An…"); facility panel has "← Maharashtra"; browser Back
  still leaves the site. → #47 (facts corrected)
- **Public demo admin can do heavy/destructive writes** anywhere: Re-run plan per state (delete +
  insert), Send test report (paints centres red), officer proxy writes — on a 90%-full 1 GB disk, during
  judging, by anyone with the link. → #82
- **Landing copy overclaims / misleads:** "Every figure on this page is read from the live database"
  (the 660 is stale stored status); How-it-works step 2 "the national model… predicts when each shelf
  runs out" (stale, only 4 states); step 3 "An officer approves or rejects it" (donors decide);
  "Authorised health department staff only. Activity is recorded." next to "no password needed";
  email placeholder `name@health.gov.in` (a real government domain). → #90
- **No government-website basics:** no footer, privacy notice (DPDP Act 2023), accessibility statement,
  help/contact, data-sources page, "last updated", full language switch, text-size control, sitemap
  (compare NCDC's site: Skip to Main Content, Screen Reader Access, English/Hindi, Accessibility
  Options, Privacy Policy, Disclaimer, Last Updated On). → #91, #94
- **No "who did what" view** backing "Activity is recorded" — approvals/events exist, nothing shows the
  chain AI suggested → human decided → logged. → #86
- **Real supply chain missing its main channel:** PHCs are replenished mainly by **indent to the
  district drug warehouse**; the app models only centre-to-centre transfers and a seeded warehouse
  dispatch ledger; no warehouse stock, no indent. Hierarchy also lacks sub-centres/HWCs and the district
  hospital. → #92
- **No report export** for the officer's monthly review meeting. → #93
- Maharashtra shows "8 districts" (synthetic subset; real state has 36) — fold into #62's scale line.
- Officer bed panel already labels the drawn board ("drawn in this browser … sent to Gemini") → #67 is
  partly done; remaining: PHC side + real photo primary.
- Officer facility panel already shows "Staff on duty 0 of 5" → #20 partly exists at facility level.

### Fix #81 — Fresh forecasts on Render  · Tier 0 · NEEDS ADITYA'S OK · status: DONE 1de1dec (forecast age on the card and badge; the republish is docs/RENDER_OPS.md step 2, run by Aditya in the PyTorch env. Freshness: manual before judging; re-run if judging is more than 8 days later)
Re-run `publish_forecast.py` against Render (size-flat upsert) after #30's roll-forward, so every
training-state centre shows "from the shared model's forecast". Show forecast age everywhere it's used
("forecast published 29 Sept"). Decide how it stays fresh without a scheduler: manual before judging,
or check-on-use like #57. Record dashboard % before/after.

### Fix #82 — Public demo roles can't vandalise the database  · Tier 0 · status: DONE 4043656 + 7da99ee
Outside the Nashik sandbox, demo roles are read-only for heavy/destructive actions (Re-run plan,
test reports, proxy writes); inside it they work, labelled. Rate-limit plan runs (one per state per N
minutes). The global request cap stays. Tests for each blocked action.

### Fix #83 — Beds and Staff views in the officer console  · Tier 1 · status: TODO
Map layer switch **Medicines | Beds | Staff**. Beds = #65 aggregates. Staff = per district/state:
centres with verified staff present today, share verified vs unverified vs no check-in, "no one
verified present" list — counts only, never names (district officer sees per-centre counts, #20).

### Fix #84 — Make the forecast visible  · Tier 1 · status: DONE a722728
Facility panel and PHC medicine card: small chart of the last 28 days' use + next 7 days forecast vs
burn-rate line, model version and age, "trained across 4 states". For the other 32 states and 6 SKUs the
model never saw: "burn rate — this state/medicine is not in the shared model yet" (#55). Feeds #45.

### Fix #85 — A daily briefing worth reading  · Tier 1 · status: TODO
Gemini writes a prioritised to-do for today from real rows only: medicines to order (forecast-based),
deliveries arriving and overdue, requests awaiting reply, counts that are stale ("recount ORS"), outbreak
nearby (#58), today's bed photo/code, check-in not done. English, Hindi, and the state language
(Marathi for Nashik). Grounded (existing ungrounded-figure check); rules fallback when Gemini is off.

### Fix #86 — "Who did what" log  · Tier 1 · small · status: TODO
Per transfer / delivery / facility: suggested by solver or Gemini → decided by <role> → dispatched →
confirmed, each with time and masked actor, from existing approvals/events/movements rows. Backs the
landing line "Activity is recorded".

### Fix #87 — Officer console on a phone  · Tier 1 · status: TODO  (pulls #52's mobile item forward)
Below 768 px: tabs collapse into a menu; panel and map stack (map full width, panel as bottom sheet or
second screen); no horizontal overflow. Test at 375×812.

### Fix #88 — Deep links open where they point  · Tier 1 · small · status: TODO
A URL with view/state/facility/`at` params survives the front door: after "Continue" (or straight away
for an existing session arriving via a deep link) open exactly that view and zoom.

### Fix #89 — One trust number, one scale  · Tier 0 · small · status: DONE d2fb665
(Cause: the PHC card read the national map's materialised trust copy; the drawer scored live. Every
single-centre view — drawer, workspace, briefing — now scores live; the card shows N out of 100 with the
drawer's band words.)
PHC card and officer panel show the same score from the same computation, on one scale (0–100), with
the same band words. Find why they differ (materialized copy vs live?) and make one source.

### Fix #90 — Landing copy tells the truth  · Tier 1 · small · status: DONE a2d0d48
"Figures read from the live database; stock status as of each centre's last report" (until #29/#30);
step 2 "Four states train a shared forecasting model today"; step 3 "The donor centre accepts; an
officer handles exceptions" (#39); replace "Authorised health department staff only" and the
`@health.gov.in` placeholder with "Prototype — not an official government system" and a neutral
placeholder.

### Fix #91 — Government-website basics (without impersonating government)  · Tier 1 small / Tier 2 rest · status: TODO
Tier 1: footer with Privacy notice (DPDP Act 2023: what data, why, retention; no patient data),
Accessibility statement, Data sources & citations, Help/contact, "Last updated", skip-to-content link.
Tier 2: text-size control, high-contrast mode, sitemap, feedback form. No national emblem, no
"Government of India" branding.

### Fix #92 — The warehouse channel  · Tier 1 honesty line / Tier 2 build · status: TODO
Tier 1: one line where supply is shown — "Most replenishment in practice is by indent to the district
drug warehouse; this prototype models centre-to-centre transfers and the warehouse dispatch ledger."
Tier 2: district warehouses as nodes with stock; PHC indent → warehouse dispatch → receipt (ledger
exists); solver uses the warehouse as a donor of first resort; sub-centre/HWC and district hospital in
the hierarchy.

### Fix #93 — Report export for review meetings  · Tier 2 · status: TODO
District/state monthly report (CSV + printable page): stock-outs, deliveries short/unconfirmed,
requests answered, trust flags visited, bed and staff availability. Labelled synthetic.

### Fix #94 — Language switch  · Tier 1 (Hindi) / Tier 2 (state languages) · status: TODO
A whole-UI English/हिंदी switch (the briefing already has one); state language for the demo district
(Marathi) on the PHC workspace. Scores on "Depth & reach across India".

### Fix #96 — Refresh the always-loaded status lines  · Tier 0 · docs · FIRST · NEEDS ADITYA'S OK · status: DONE de675a4
CLAUDE.md says "`main` is at `542d8dc`" and that Phase C/D code "was deliberately removed"; SPEC_DIGEST
§5 says the same. On 2026-09-29 `main` is `e09fb0c` and `app/ingest.py`, `frontend/src/field.tsx`,
`app/idsp.py` (IDSP outbreaks), the PHC workspace and the live loop all exist. A fresh session loads
CLAUDE.md first and may believe features are missing. Update only the status lines (CLAUDE.md is
Aditya's instruction file — show the diff and get a yes). Until then: trust git and this file's
"Verified facts", not those status lines.

### Fix #95 — Submission deliverables match the fixed product  · Tier 1 · last · status: TODO
README "what works, with a proof link" rewritten after the fixes (no stale claims); 2–3 line
description; 10–12 slide deck (problem, solution, AI approach, who it serves, deployability, scale);
3–5 min demo video script following the spine (#41 → #45 → #39/#40 → #9 → #18 → #1); the
live / recorded / future table (#63); deployed link checked cold (#53).

---

## LIVE vs RECORDED vs FUTURE — the Render answer

Render's limit is **storage for bulk writes**, not live computation. Rule: **live where cheap,
recorded where heavy, future only for non-core — and label which is which.**

| Category | What | Why |
|---|---|---|
| Live on Render | IDSP check (#57), outbreak surge (#41, computed at read), warnings, check-in, bill/challan scans, requests, receipts, register, trust scoring, bed reports | Each action writes a handful of small rows — the "growth only from someone using the app" CLAUDE.md allows |
| Recorded, with proof | Federated training (4 SuperNodes on a laptop, hashes recorded), full reseeds | Heavy compute / bulk writes. Recorded run + split-screen video of it running |
| Future, said plainly | Real SMS/IVR carrier (DLT registration), one database per state, differential privacy / secure aggregation | Not built — deck roadmap |

Never move a **core** item (outbreak → warning → redistribution, verified capture) to "future".
Storage for judging stays flat via #30 (roll-forward with equal deletion); dropping the redundant
25 MB index is Aditya's call (CLAUDE.md option 2); a new instance resets the 30-day clock.

### Fix #63 — "What runs live, what's recorded, what's next" table  · Tier 1 · small · status: DONE a8c8f93
The table above, in plain words, on the landing page and in the README. Answers the judge's
first suspicious question before it's asked; scores on deployability.

---

## BEDS — #64 … #67

### Verified facts (code, 2026-09-29; live Beds tab NOT opened — browser permission check failing)
- Backend is strong ([beds.py](../../backend/app/beds.py)): every report runs three checks stored beside the
  number — **the code** (today's 4-char rotating code, read back out of the photo), **the place**
  (geofence vs registered coordinates), **the paper** (admission register same day). Failed
  reports are stored, not dropped, and feed trust.py's `verification_quality`.
- `verification_codes`: issued server-side per facility per day; `delivered_at` exists (SMS
  delivery is simulated while `COMMS_MODE=simulator`).
- **PHC Beds tab is read-only** ([Beds.tsx](../../frontend/src/workspace/Beds.tsx)): shows today's code and past reports.
  Its own comment: "A ward photo is taken on the **officer console's** bed panel." Capture sits
  with the wrong person — the PHC holds the camera.
- Officer console bed panel ([bedpanel.tsx](../../frontend/src/bedpanel.tsx)) has demo submissions that **draw a
  synthetic ward whiteboard in the browser** (`wardboard.ts`) and send it to Gemini
  ([ward_photo.py](../../backend/scripts/ward_photo.py) draws the same board offline).
- **What Gemini actually does:** reads the numbers and the code written on a whiteboard (OCR +
  code). It does **not** count beds in a ward photo, although data-trust-layer.md §2 pitches
  "count total beds, classify occupied vs empty".
- **No bed visibility above facility level:** the map and national/state panels have no beds
  layer or aggregate; beds appear only inside one facility's panel. The challenge names "bed
  availability across the PHC network".
- Seeded bed reports end at the reseed → "verified" reports are days old.

### From the PHC Beds screenshot (Nashik PHC 1, 2026-09-29)
- **Three different numbers on one screen:** header "3 of 7 beds occupied" (no time, no
  source); latest report 23 Sept "13/20 occupied · Not verified"; latest verified 19 Sept
  "2/7". The header matches neither.
- **No capacity check:** `facilities.beds_total` = 7 (registered capacity exists,
  [models.py:72](../../backend/app/models.py)), yet a report of **20 beds** was accepted.
- **"Read by mock"** on seeded rows while Render runs `LLM_MODE=live` — true for seed rows, but a
  judge reads "mock" as "fake".
- Code card: "not delivered yet today, but it is on this screen regardless" (SMS simulated).
- Latest report is 6 days old; nothing says the header figure is stale.
- SMS/IVR `BEDS 12` already exists ([ingest.py:366](../../backend/app/ingest.py)) → stored as **unverified** with
  "send a ward photo with today's code to verify". So IVR bed updates exist in the backend.

### Design decision — privacy: never photograph patients
Aditya's idea: Gemini sees an actual bed with a patient on it. **Rejected on privacy:** a photo of a
patient is patient-level data (face, condition) — breaks the standing invariant "no patient-level
data at all" and DPDP Act 2023. **Replacement that proves more:** photograph **free beds only**.
Availability is what a referral needs, and a false "free bed" is the failure that hurts patients.
Occupied = registered capacity − verified free beds. Gemini also checks **no person is in frame**;
if one is, the photo is discarded and the pharmacist is asked to retake it.

### Real-life verification layers for beds (costly to fake, and faking leaves a trace)
1. **Registered capacity** (`beds_total`, from HFR/IPHS; officer-changed only) — no report can
   exceed it.
2. **Permanent bed tag** — each registered bed gets a printed label ("NSK-PHC1 · Bed 3"). Gemini
   reads which bed it is → one empty bed photographed four times counts once.
3. **Daily rotating code** (exists) — written on a card placed on the bed → freshness.
4. **Location** (exists) — distance-vs-accuracy rule from #18.
5. **Gemini checks per photo:** a bed is visible · it is empty · bed tag read · today's code read ·
   no person in frame.
6. **Duplicates:** same bed tag twice today → counted once; same image SHA-256 → refused.
7. **Register cross-check** (exists) — admissions/discharges vs occupancy.
8. **Cross-signal** (trust.py exists) — occupancy vs staff present vs medicine use (e.g. beds full,
   no IV fluid or paracetamol used).
9. **Random spot check** — officer (or the system) asks "Photograph Bed 3 now"; answer within
   15 minutes with today's code → a live check that's hard to stage. Best demo moment.
10. **IVR/SMS reports** stay "unverified" until a photo or spot check confirms them.
Camera capture on phones uses the camera directly (`capture` attribute); on the web this can't
fully block gallery uploads — say so; duplicates/SHA/code still catch reuse.

### Fix #64 — Capture moves to the PHC  · Tier 1 · status: TODO  (see #68–#71 for the redesign)
"Photograph the ward" on the PHC Beds tab (camera/upload, the existing pipeline); today's code
shown large; result shows the three checks + a live trace like #9 (Gemini read → code check →
location check using #18's distance-vs-accuracy rule → register → verdict → row id → event →
district view updates). Officer console keeps viewing; its drawn-board demo stays as a labelled
fallback (#67).

### Fix #65 — Bed availability above facility level  · Tier 1 · status: TODO
District/state aggregates: free beds now, split verified / unverified / stale (as-of-now age —
a report older than N hours is not counted as available). "Beds" map layer toggle. From a PHC:
"nearest CHC with a verified free bed" (facility-level only, no patient data).

### Fix #66 — Make Gemini's bed job match the pitch  · Tier 1 · spike first · status: TODO
SUPERSEDED in part by #69: Gemini's job becomes per-bed free-bed verification (bed visible,
empty, tag, code, no person). Spike on realistic photos first; if unreliable, keep the whiteboard
read and **correct any copy/deck line that says "Gemini counts beds"** until it does.

### Fix #67 — Label the drawn test board  · Tier 1 · small · status: TODO
(Partly done already: the officer bed panel says "The board below is drawn in this browser with that
code and sent to Gemini as an image." Remaining: the PHC side, and the real-photo path as primary.)
The app-drawn whiteboard is labelled "Generated test photo" wherever it's used; the real-photo
path (#64) is the primary one.

### Fix #68 — One bed figure, with its source and age  · Tier 0 · small · status: DONE 4532cc2
("3 of 7" was the seeded `bed_status` series; the snapshot now reads the latest verified bed report. N = 24 h
(`bed_stale_hours`, the code rotates daily). A capacity mismatch is stored as **rejected** with the reason,
not a new "held" state. Seeded rows read "Seeded demonstration report (no photo)".)
(Live audit adds a fourth figure: officer panel "2 of 7 beds counted in the photo · 7 registered ·
Unverified · last verified 10 days ago". All views must show the same figure from the same rule.)
Header shows exactly one figure: the latest verified count, "as of <time>, verified by photo",
with the latest unverified report shown separately beneath it. Older than N hours → "stale, not
counted as available". Find and remove whatever produces "3 of 7" if it matches no report
(suspect `bed_status`). **Capacity check:** reject/hold any report with total ≠ registered
`beds_total` or occupied > capacity. Seeded rows: "Seeded demonstration report (no photo)", never
"Read by mock".

### Fix #69 — Free-bed photo verification (privacy-safe)  · Tier 1 · spike first · status: TODO
Per-bed photo: bed tag + today's code card on an empty bed. Gemini returns: bed visible, empty,
tag read, code read, person in frame. Server: tag belongs to this facility and capacity; not
already counted today; image SHA new; code valid; location verdict. Person in frame → discard,
ask to retake (never stored). Occupied = capacity − verified free. Printable bed-tag sheet per
facility (reuse the print stylesheet). Demo: a printed tag + code card on a demo cot, labelled.

### Fix #70 — Bed Register (same register as medicines)  · Tier 1 · status: TODO
#13a's register gets sub-tabs **Medicines | Beds**. Bed rows: time · source chip (photo read by
Gemini / IVR / SMS / seeded) · claimed free/occupied · each check passed/failed/couldn't run ·
verdict · effect (district free beds before → after, event id). Camera/upload button on the Beds
tab (#64). IVR/SMS rows show "unverified — confirm with a photo".

### Fix #71 — Random bed spot check  · Tier 1 · status: TODO
Officer (or labelled demo trigger) requests "Photograph Bed N now"; PHC gets it on the Beds tab
(+ simulated SMS); 15-minute window; outcome confirmed / wrong bed / no reply recorded and shown in
the Bed Register and the facility's trust evidence. Same pattern as attendance #19.

Stale bed reports/codes: include in #30's roll-forward.

---

## LANDING PAGE — #72, #73

### Verified facts (screenshots + code, 2026-09-29)
- Role cards: "Administrator · Platform Admin", "State officer · Maharashtra NHM Officer · MH",
  "District officer · Nashik District Logistics Officer · Nashik, MH", "Facility staff ·
  Pharmacist, Nashik PHC 1". They say who, not what you will see.
- Landing map ("Where it is running"): OSM raster tiles, one dot per state sized by facility
  count and coloured by share short; "Sign in to zoom". **Labels in foreign scripts** (中国,
  兰州市, اسلام آباد, Burmese) and **the basemap's own depiction of India's borders**.
- **Google basemap already built**: [googleTiles.ts:23-24](../../frontend/src/googleTiles.ts) requests `region: "IN"`,
  `language: "en-IN"` (Google renders India's boundaries for region IN). Off because
  `MAPS_MODE=osm` — [render.yaml](../../render.yaml) comment: "Stays osm until Google's own attribution
  logo asset is in the build" (expected at `/google-attribution/google_on_white.png`).
  Env names: `GOOGLE_MAPS_BROWSER_KEY` (referrer-restricted), `GOOGLE_MAPS_SERVER_KEY`.
  UNVERIFIED: whether `LandingMap.tsx` uses Google tiles when `MAPS_MODE=google`.

### Fix #72 — Role cards say what you'll see  · Tier 1 · small · status: DONE b23fa19
- Administrator — "Every state: federation, outbreaks, redistribution oversight, data trust"
- State officer — "Maharashtra: recommendations, outbreak warnings, which centres to visit"
- District officer — "Nashik: its centres, deliveries in transit, spot checks"
- Facility staff — "Pharmacist, Nashik PHC 1 (demo PHC interface): stock, orders, beds and
  attendance on a phone-style screen"
- **Add** Facility staff — "Pharmacist, Nashik PHC 13 (the neighbouring centre, for the two-screen
  demo)" (#36).
- Mark each "(demo role)". "NHM officer" stays a role title, never implying NHM operates the site.
- Under the cards: the 5-step demo script (#36) and the live / recorded / future table (#63).

### Fix #73 — A map that looks official and shows the network  · Tier 1 · first impression · status: TODO
1. **Basemap compliance + quality:** switch to the already-built Google Map Tiles
   (`region: IN`, `language: en-IN`, Hindi option) so India's boundaries render as India's
   official view and labels are in English/Hindi, not foreign scripts. Unblock: add Google's
   attribution logo asset (download from Google's brand resources — **Aditya's OK to
   download**), set `MAPS_MODE=google` on Render, **Aditya supplies `GOOGLE_MAPS_BROWSER_KEY`**
   (HTTP-referrer-restricted to the Render domain). Verify the landing map uses it too.
   Fallback if Google is unavailable: an India-compliant basemap (Bhuvan/ISRO or Mappls) — needs
   its own check; never ship a basemap that draws India's borders differently.
2. **HD:** request 2× (retina) tiles; draw facilities as crisp SVG/vector markers; flat gov
   palette; no blurry raster scaling.
3. **Show the network, not just dots:** national view keeps state rings; add a district inset
   (Nashik) on the landing page showing **district warehouse → CHCs → PHCs**:
   - warehouse and facility nodes with distinct shapes by type (PHC / CHC / warehouse);
   - lines for **real** movements in the last 14 days (warehouse → facility, from
     `medicine_movements`) and approved transfers (facility → facility);
   - referral link PHC → nearest CHC, labelled "derived: nearest CHC" (no referral table exists);
   - legend + "Synthetic demonstration data".
4. **Satellite photo view:** optional toggle at district zoom (Google satellite); roadmap stays
   default for legibility.
5. Same basemap + network layer inside the signed-in console at district zoom.

---

## CARRIED OVER — raised earlier in the brainstorm, not yet elsewhere in this file

### Fix #53 — Live link survives a judge's first click  · Tier 0 check · status: DONE 8318c2b (checked; avoiding the cold start is Aditya's call)
Render free web services sleep when idle → first click can hit a long cold start. Verify the
plan and cold-start time; document the wake-up in the demo script (#36). Pair with the DB
30-day deletion date (blocked on Aditya).
**Checked 2026-09-29:** `render.yaml` says `plan: free`, one instance. After 17 idle minutes the
first request (`GET /api/health`, 09:44 UTC) took **42.6 s to first byte**; the next one 0.38 s.
The landing page is served by the same service, so a cold first click shows nothing for ~43 s.
→ #36's demo script must say: open the link at least a minute before judging. Options that avoid it
(Aditya's call, not built): Render's paid Starter plan (does not sleep), or an outside uptime pinger
on `/api/health` every 10 minutes (it only pings the database and writes nothing).

### Fix #54 — Is the recorded federation run from the data Render holds?  · Tier 1 check · status: TODO
Rounds are dated 20 Sept; Render was reseeded 21 Sept. Verify the run's windows/trust match
the current DB (e.g. compare per-silo windows and trust against a fresh `load_state` count).
If not, say so on the Federation tab or re-run on a laptop and republish (Aditya's OK).
Also check `final_model.pt`'s hash against the last recorded round's `weights_sha256`.

### Fix #55 — Forecasts for the 32 regions that don't train  · Tier 1 check · status: TODO
Only MH, KL, BR, UP are silos. Verify what `publish_forecast.py` writes for the other 32
(global model applied, or burn rate). State it on screen truthfully ("forecast from the
4-state model" vs "burn rate"); if the global model is applied to non-participants, show a
held-out-state check before claiming it works there.

### Fix #56 — Lead-time backtest (what a minister understands)  · Tier 2 · status: TODO
"Warns N days earlier than burn rate, at X% false alarms" on held-out weeks, labelled
synthetic. The removed eval harness can't regenerate it — needs a small new script.

---

## SCOPE CHECK (2026-09-29) — why the list is tiered

35+ fixes, ~26 of them PHC-workspace items; federation had only #1/#2; the officer side
had not been examined. Challenge asks vs coverage:

| Challenge asks for | Where it lives | Coverage so far |
|---|---|---|
| Real-time visibility: stock, beds, attendance | national map + PHC workspace | heavy (PHC trust work) |
| Demand forecasting | federated model + `forecasts` | #1, #2 |
| Early stock-out warnings during health emergencies | IDSP outbreaks (`idsp.py`, `outbreaks.tsx`) | NOT examined |
| Automated cross-district redistribution | officer tab, OR-Tools solver | NOT examined |
| Shared predictive modelling across states | federation | #1–#3, #6, #15b |
| Across India's PHC network | national map, 36 regions | NOT examined |

**The spine a judge follows:** outbreak signal (IDSP) → surge in forecast demand → early
warning (named district + medicine + date) → solver proposes cross-district transfers →
officer approves → donor dispatches → receiver confirms (verified) → trust → federation
weighting. PHC work so far is the last third of this line.

Officer solver facts ([redistribution.py](../../backend/app/redistribution.py)): OR-Tools min-cost flow +
greedy fallback; donor floor; controlled SKUs excluded; cold-chain distance cap; max km;
**same state, cross-district** by design (cross-state needs state sign-off); unmet needs
reported as "escalate to state warehouse"; officer approval under row lock.
UNVERIFIED: whether the live plan actually shows a cross-district trip (straight-line
distance favours same-district donors).

**PHC section is FROZEN** — no new PHC items unless they sit on the spine.

## TIERS

**Tier 0 — broken, corrupting, contradictory, or a risk to the live database. Must ship.**
#96 (stale CLAUDE.md status lines, docs) · #53 (check: cold start on first click) · #82 (public demo roles can vandalise the DB) ·
#11 (bill overwrites stock; wrong provenance label) · #74 (officers report facts for a centre:
deliveries, check-ins, bed reports) · #79 (test report paints real centres red) · #37 (re-plan wipes
requests / donor work) · #31 (request lockout + race guard) · #24 (dead simulator link) ·
#89 (two trust numbers for one centre) · #68 (four bed figures, no capacity check) ·
#26 (cards contradict their own dates).
**Tier 0 ops, timed before judging, each on Aditya's OK with dashboard % before/after:**
#30 (roll-forward, size-flat) → #81 (republish forecasts, size-flat) · repairs #11r, #79.
(#26/#29/#34/#84 are deployed only after #30 has run.)

**Tier 1 — the judged spine.**
Foundations & first impression: #52, #87, #88, #47, #90, #63, #72, #91 (Tier 1 part), #92 (honesty
line), #62, #73 · Emergency: #41, #42, #57, #43, #58, #59, #44 · Governance & forecasting: #77
(upgrades #4), #84, #55, #45 (absorbs #2), #60 (threshold part), #1, #54 · Officer: #39, #40, #38,
#46, #48, #49, #86 · Beds & staff: #64, #70, #69, #65, #71, #67, #66, #20, #83 · Trust, movements,
field: #76, #78, #80, #75 · PHC on the spine: #7, #8, #13a, #9, #17 minimum, #18, #85, #94 (Hindi) ·
Judge alone & submission: #36, #95.

**Tier 2 — polish, only if time.**
#12, #13b, #14, #15a (+#25), #15b, #16, #17 rest, #19, #21, #22, #23, #27, #28, #29,
#32, #33, #34, #35, #50, #51, #56, #60 (rest), #9b, #3, #5, #6, #77b, #91 (rest), #92 (build),
#93, #94 (state languages).
(#4 is upgraded into #77, Tier 1. #61 is superseded by #76. #2 is merged into #45.)
**Parked (not scheduled):** #10 real climate input (IMD) — needs a feasibility check first.
(#29 needs #30; #34 needs #26 + #30; #45 needs #41 + #81/#84.)

**Budget rule:** no Tier 2 item starts until every Tier 0 and Tier 1 item is done and
deployed.

## NEXT BRAINSTORMS (in order)
1. ~~Officer Redistribution tab~~ — done (#37–#40, #46–#52).
2. ~~Emergency early warning~~ — done (#41–#44).
3. ~~National map + overview~~ — done (#45, #59–#62; live-tested 2026-09-29).
4. ~~Beds~~ — done (#64–#71; PHC Beds tab reviewed from Aditya's screenshot).
5. ~~Final live audit~~ — done (#81–#95, plus corrections to #11, #20, #47, #62, #67, #68, #74).
Brainstorming is closed. New ideas go to the end of the list and into Tier 2 unless they are bugs.

## BUILD ORDER (current)
**Tier 0:** #96 (docs, with OK) → #53 → #82 → #11 → #74 → #79 → #37 → #31 → #24 → #89 → #68 → #26
**Tier 0 ops (Aditya's OK, dashboard % before/after):** #30 → #81 → #11r → #79 repair
**Tier 1:**
- A. Foundations & first impression: #52 → #87 → #88 → #47 → #90 → #63 → #72 → #91(T1) → #92(line)
  → #62 → #73 (when the Google key + logo arrive)
- B. Emergency spine: #41 → #42 → #57 → #43 → #58 → #59 → #44
- C. Governance & forecasting: #77 → #84 → #55 → #45 → #60(threshold) → #1 → #54
- D. Officer: #39 → #40 → #38 → #46 → #48 → #49 → #86
- E. Beds & staff: #64 → #70 → #69 → #65 → #71 → #67 → #66 → #20 → #83
- F. Trust, movements, field: #76 → #78 → #80 → #75
- G. PHC spine: #7 → #8 → #13a → #9 → #17(min) → #18 → #85 → #94(Hindi)
- H. Judge alone & submission: #36 → #95
**Tier 2:** only after everything above is done and deployed.

## BLOCKED ON ADITYA (all, in one place)
- Render DB creation date (30-day deletion) and when judging opens.
- Dashboard % before/after every Render write: #11r, #17(b), #30, #36 seeds, #79 repair, #81.
- Keys: `GOOGLE_MAPS_BROWSER_KEY` (#73, referrer-restricted), `CHALLAN_SIGNING_KEY` (#9).
- Downloads: Google attribution logo (#73), latest IDSP PDFs (#43).
- Decisions: IDSP check trigger (#57: check-on-use + button recommended); approval model confirmation
  (#39: donor decides, officers see exceptions); forecast refresh cadence (#81); new dependencies
  (#52 virtualization, #21 WebAuthn); dropping the redundant 25 MB index (storage).
- Spec conflicts flagged in CLAUDE.md (v3 vs FINAL) — ask before treating either as canonical.

