# Implementation Map — Build Order, Informed by the Past Winners

*Companion to the War Room doc. That document answers "what to build and why." This one answers "in what order, and how." Reference: your four modules from the War Room — Redistribution VRP Engine, FedStock Core, Outbreak Pre-Positioning, Misreporting Trust Layer.*

---

## 0. What the past winners actually change

| | HealthGrid AI (winner) | GHC (runner-up) | Smart Health (2nd runner-up) |
|---|---|---|---|
| Visibility | Live district map, traffic-light status per facility | Unified admissions+beds+attendance+inventory | 60-second phone reports feed live status |
| Forecast + act | Predicts stockouts/demand, recommends transfers | Forecasts demand, **days-of-stock**, alerts, redistribution | Every update triggers instant recompute + redistribution |
| Accessibility | Hindi/Marathi/English voice or forms + Copilot | 7-language voice assistant | Photograph an invoice → Gemini extracts the data |
| Data honesty check | Not mentioned | Not mentioned | Not mentioned |
| True federation (data never pooled) | Not mentioned | Not mentioned | Not mentioned |

Three conclusions, and they reorder your build priority from the War Room:

1. **A live, colour-coded, unified dashboard is not a nice-to-have — it's the entry ticket.** All three winners open with it. It gets promoted to its own first-class layer below (it was previously just "plumbing" feeding the four modules).
2. **Voice/multilingual/low-friction reporting is not a side feature — it's a headline one.** Every single winner has it as one of their three bullets. Your War Room scoring ranked this idea 9th of 16 in the abstract; the real competitive record says otherwise. It's promoted too.
3. **Nobody else checked whether the data was true, and nobody else built real federation.** Both are still in your build — they're the actual whitespace. Keep them, but build them *after* the table-stakes layers are solid, per the roadmap below, so you're never caught with the differentiator half-built and the basics missing.

One more environmental fact worth designing around: this is a Google-affiliated hackathon run through Hack2skill. That's almost certainly why Smart Health reached for Gemini specifically, and it means your tool choices below lean Google-native on purpose — including OR-Tools, which is itself a Google open-source project, so it's legitimate "we used your stack" credit, not just a coincidence. **Confirm the exact tech/eligibility rules on your event's Hack2skill page before you lock the stack** — these programs often score bonus points for specific Google technologies.

---

## 1. Revised layer order

```
Layer 0 — Live Command Dashboard        (NEW — the hero screen, build first)
Layer 1 — Days-of-Stock + Forecasting   (Reorder floor, then FedStock Core)
Layer 2 — Redistribution Engine         (Redistribution VRP Engine)
Layer 3 — Voice / Photo / Copilot       (elevated from a minor idea to a full layer)
Layer 4 — Differentiators               (Outbreak Pre-Positioning + Misreporting Trust Layer)
```

## 2. Core data model (sketch — enough to start, not a full spec)

```
Facility      { id (=ABDM HFR id), name, lat, lng, type (PHC/CHC), state_silo }
StockItem     { facility_id, sku, qty_on_hand, daily_burn_rate, days_of_stock }
StockReading  { facility_id, sku, qty, source (form/voice/photo), reported_at }
BedStatus     { facility_id, beds_total, beds_occupied }
StaffCheckin  { facility_id, staff_id, checked_in_at }
Transfer      { from_facility, to_facility, sku, qty, route_km, eta, status }
TrustFlag     { facility_id, sku, z_score, reason, reviewed }
OutbreakEvent { district, disease_category, radius_km, triggered_at }
```

Everything else — the forecast model, the FL weights, the routing solver's constraints, the anomaly z-scores — reads from and writes to this backbone. Build the backbone in hour one; nothing else can start without it.

---

## 3. Build phases

### Phase 1 — Foundation & data
**Goal:** a seeded, seasonally-honest dataset before anyone writes a UI.
- [ ] Pull real facility coordinates + counts for one real state from open government data (Health Dynamics of India / Rural Health Statistics state tables) — real coordinates with synthetic stock numbers reads as far more credible than fictional District A/B/C.
- [ ] Pick a small, real essential-medicine basket (8–12 SKUs: ORS, zinc, paracetamol, one antibiotic, IV fluids, one TB drug, a couple more) rather than faking full coverage of hundreds of drugs.
- [ ] Generate synthetic consumption series per facility per SKU with genuine seasonal shape — a monsoon-diarrhoeal spike, a heatwave spike, a festival-week reporting dip. This is what Layer 1's forecast and Layer 4's outbreak trigger both need to have something real to react to.
- [ ] Deliberately inject a handful of "gaming" facilities (implausible flat zero-stockout streaks) into the seed data now — Layer 4's trust check needs a planted ground truth to prove it catches.
- [ ] Stand up the schema above in Postgres and seed it.

### Phase 2 — Layer 0: Live Command Dashboard
**Goal:** HealthGrid AI's opening beat, reproduced — because it's what a judge sees in the first fifteen seconds, and every winner led with it.
- [ ] One status endpoint computing healthy/at-risk/critical per facility from stock vs. threshold + bed occupancy % + staff check-in rate — three inputs, one traffic light, kept simple enough to explain in one sentence.
- [ ] Map view (Leaflet/Mapbox) plotting every facility at its real coordinate, coloured by status; click through to stock/beds/personnel/patients/tests.
- [ ] A single "days-of-stock remaining" number per facility per SKU (qty on hand ÷ daily burn rate) — this is GHC's specific metric, and it's the right one: simple, explainable, and it's the number every later layer either reads or moves.
- **Checkpoint:** open the map, see three colours, click a facility, read a days-of-stock number. This alone is already a coherent, submittable project.

### Phase 3 — Layer 1: Forecasting
**Goal:** the deterministic floor first, the smart layer after — never let the ML model *be* the safety net.
- [ ] Ship the reorder-point/safety-stock threshold first (this is the TNMSC passbook logic from the War Room) — a facility under N days of cover is "at-risk" even before any model runs. This alone makes Phase 2 correct from hour one.
- [ ] Bring in FedStock Core: 4–6 simulated state silos on the Phase 1 seasonal data, each training a local model, sharing only weight updates via an open FL framework (Flower) rather than hand-rolled FedAvg.
- [ ] Build the specific "accuracy before federation vs. after" number and a chart for it. This was the one weak spot the War Room's own red team flagged (a loss curve doesn't win a demo) — and since none of the three real winners built anything like federation at all, making this one number *visible* is your cleanest point of differentiation.
- **Checkpoint:** pick a low-data state, trigger a federation round, watch its forecast sharpen with a before/after accuracy number on screen.

### Phase 4 — Layer 2: Redistribution Engine
**Goal:** HealthGrid AI's "recommends smart transfers" line, as real constrained optimisation, not a sentence.
- [ ] Distance matrix from Phase 1's real coordinates — haversine plus a road-factor multiplier is enough; don't burn hours self-hosting a routing engine unless time genuinely allows.
- [ ] Formulate donor/recipient transfers and solve with OR-Tools. Say out loud in the pitch that it's a Google project — free, relevant stack credit.
- [ ] Approve/reject officer UI wired to Phase 2's days-of-stock number, so an approved transfer visibly flips a facility from amber back to green on the live map.
- [ ] Hard-exclude scheduled/controlled substances from automated recommendations; route them to a manual-only queue.
- **Checkpoint:** simulate a stock-out, watch a route with ETA appear, click approve, watch the dashboard update live.

### Phase 5 — Layer 3: Voice, Photo Capture & Copilot
**Goal:** the single most validated pattern across all three real winners, and the one the War Room's abstract scoring under-rated. Build it properly, not as an afterthought.
- [ ] **Input, photo:** a "photograph the register/invoice" flow → Gemini's multimodal API extracts structured stock counts. This is Smart Health's exact mechanism, and Gemini is the sponsor-aligned choice here.
- [ ] **Input, voice:** a short voice note ("60 ORS left, 20 patients today") in Hindi/Marathi/English → transcribed and parsed into the same structured update. Lead with Google Cloud Speech-to-Text or Gemini's audio input (sponsor-aligned); optionally add Bhashini — India's own free government speech/translation API, purpose-built for exactly this — as a value-add that also tells judges you plugged into India's own language-AI mission, not just Google's.
- [ ] **Output, Copilot:** a chat panel where an official asks "which districts risk an ORS stock-out this week" in plain language, answered via Gemini function-calling against your real structured data. Keep the boundary hard: the Copilot explains and summarises, it never decides a transfer — that's Layer 2's job. This mirrors Smart Health's own "algorithms decide, AI explains" pattern; state that boundary explicitly in the pitch.
- **Checkpoint:** photograph a mock invoice and watch stock update; speak a report in Hindi and watch it land as structured data; ask the Copilot a question and get an answer grounded in your real data.

### Phase 6 — Layer 4: The differentiators
**Goal:** the thing none of the three real winners built. Build it after Layers 0–3 are solid — a working dashboard beats a half-built differentiator with nothing else finished.
- [ ] **Misreporting Trust Layer:** run the anomaly check against Phase 1's seeded gaming facilities; a flagged-facility view with a plain-language reason ("187 days flat against above-average footfall").
- [ ] **Outbreak Pre-Positioning:** fire one simulated outbreak event against a disease-to-commodity table; show the pre-position recommendation land *before* the facility's own days-of-stock number would have crossed threshold, with an honest "simulated — here's exactly where the real IHIP feed plugs in" line ready.
- **Checkpoint:** flip a facility from green to amber via the trust layer's flag; fire a simulated outbreak and watch a pre-position beat the reactive timeline.

### Phase 7 — Demo engineering & rehearsal
- [ ] Pre-compute anything that calls a live external API on stage — the distance matrix, a federation round, a sample Gemini/speech response — so venue wifi can't sink you. You now have three more live-API dependencies than the War Room draft assumed (Gemini, speech, Copilot); cache all of them.
- [ ] Rehearse the 15-second opening hook (the 2024 Tamil Nadu TB stock-out story from the War Room doc) and the exact click-path for every checkpoint above, timed.
- [ ] Pre-write one-line answers to "how is this different from what NHA/IIT Kanpur are already building" and "is this really federated or did you just fake it" — have the local-train-then-share-weights code on hand for the second one.
- [ ] Re-check the Hack2skill event page for this specific edition's tech/eligibility rules before locking the stack.

---

**If time runs out, stop after Phase 5.** A dashboard that's accurate, a redistribution engine that's real, and an accessibility layer that matches what actually won last time is a complete, defensible submission on its own. Phase 6 is the upside, not the floor.
