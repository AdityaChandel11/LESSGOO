# War Room: National PHC Supply Chain & Resource Platform

*Ten voices, one decision. Assumptions stated up front, nothing carried forward on faith.*

**Working assumptions (stated, not stalled on):**
1. We searched for an official, numbered problem statement matching this brief. We could not find one — as of this week the SIH 2026 problem-statement list itself is reportedly still unpublished, and no other named challenge matched the wording exactly. The brief reads as SIH-style (the Appendix format requested is SIH's standard submission template almost verbatim), so we treat it as SIH-adjacent and flag: **confirm your actual PS number and sponsoring body the moment it's published** — sponsor identity changes what a judge rewards.
2. We assume a ~36-hour build window and a ~5-minute stage pitch, because that's the standard format for this class of national hackathon and nothing in the brief contradicts it.
3. We assume the team can reach any open-source ML/optimization stack (Python, standard FL and OR libraries, a map frontend) — nothing in the brief signals a hardware or offline-device constraint, though Phase 0 argues connectivity should be treated as *if* offline, not *whether*.

---

## Phase 0 — Interrogate the Brief

**What's literally being asked:** a platform giving real-time visibility into three things (medicine stock, bed availability, personnel attendance) across India's Primary Health Centre network; a forecasting layer; early-warning for emergency stock-outs; a redistribution-recommendation layer; and a federated modelling layer so states share predictive power without necessarily sharing raw data.

**The Product Strategist, first:** "Before anyone touches an architecture diagram — who is the *user*? Not 'PHC staff,' not 'the government.' The brief actually names three distinct professionals with three distinct moments of pain: the pharmacist who discovers a shelf is empty when a patient is standing in front of them, the Block Medical Officer who has to find stock *somewhere* in the next six hours, and the state NHM program officer who finds out about a stock-out from a newspaper instead of a dashboard. Three different products, potentially. We need to pick one to be *great* at and let the others be secondary."

**The real problem behind the stated one:** the brief is written like a modelling problem — forecast demand, recommend redistribution. It isn't. Every existing Indian health-data system we found in Recon (below) already collects *some* version of this data; the documented failure mode across all of them is late, incomplete, or quietly gamed reporting from an overloaded last mile, not a shortage of forecasting sophistication sitting on top of clean data. A widely cited assessment of India's HMIS explicitly found under-reported deaths and inflated delivery counts, and a Harvard-affiliated field study of India's health information systems clocked frontline staff spending roughly a third of their available working time on manual registers and monthly returns. **The Domain Expert:** "Give a Data Entry Operator a smarter model and she still has ninety minutes of paper registers to get through before she can feed it. You haven't removed her problem, you've added a dashboard to look at while she's still stuck in it."

**Stakeholders, and who resists:**
- *Primary:* PHC pharmacists/store staff, ANMs, Medical Officers, Block/District Drug Logistics Officers.
- *Secondary:* State NHM program officers, state medical-services corporations (TNMSC and its ~15 state-level siblings), District Health Officers, ASHA workers, patients.
- *Indirect:* private pharma distributors, Jan Aushadhi Kendra operators, the Union Ministry of Health & Family Welfare / National Health Authority.
- *Resisters — named explicitly, not glossed over:* states with real, documented institutional reluctance to hand operational data to a Delhi-controlled "national" system (this resistance is on the record even for proven models like TNMSC's); staff whose current incentive is to *not* report a stock-out because it reflects on them; and, per the Domain Expert again, health-worker unions who will read a "personnel attendance" module as surveillance unless it's framed and scoped carefully.

**The Founder, cutting in here:** "That resistance point isn't a footnote, it's the whole ballgame three years out. A platform that requires 36 states to surrender data sovereignty dies in committee before it ever ships. A platform designed so each state keeps its data and only *shares learning* is the only version of this that has a chance of actually getting adopted. That's not a nice-to-have architecture choice — that's the product."

**What the sponsor is likely hoping to see:** the phrase "federated" in the brief isn't decorative. We found that India's National Health Authority, ICMR's digital-health research arm, and IIT Kanpur ran an actual national **Federated Intelligence Hackathon for Health AI** in January 2026, explicitly building federated, privacy-preserving benchmarking infrastructure as a pre-event to the India AI Impact Summit, with the NHA's own CEO publicly framing "trusted, federated AI ecosystem for healthcare" as current government direction. Whoever wrote this brief is very likely echoing that exact policy language. **The Judge, unimpressed:** "Which means 'we used federated learning' is not a differentiator this cycle — it's the entry fee. I will have heard it from a dozen teams by lunch. Show me the thing that proves you didn't just fake it."

**Five ideas every other team in the room will pitch — name them to beat them:**
1. A single dashboard with stock-level charts and a generic time-series forecast (Prophet/LSTM) bolted on.
2. A chatbot for PHC staff to "ask about stock."
3. A blockchain-for-supply-chain pitch that never explains what the chain actually verifies.
4. An RFID/IoT smart-shelf pitch that assumes hardware budgets and connectivity no real PHC has.
5. "AI predicts stock-outs" as a single undifferentiated sentence, with redistribution hand-waved as "the system recommends transfers."

**Hidden constraints in the wording:** "national scale" means roughly 31,882 PHCs, ~1.7 lakh Sub-Centres and ~6,359 CHCs (latest published Health Dynamics of India / erstwhile Rural Health Statistics figures, as of March 2023 — current counts will be close to this order of magnitude). Only around 13% of PHCs meet the government's own Indian Public Health Standards, and independent field studies describe patchy, sometimes absent internet in exactly the facilities this platform most needs data from. "Federated... across India's states" means treating this as **cross-silo** federated learning — roughly 36 large, heterogeneous institutional nodes (28 states + 8 UTs), not thousands of small identical devices — which is a materially different and, frankly, more tractable engineering problem than the consumer cross-device FL most teams have half-heard of.

---

## Phase 1 — Recon

**The Systems Engineer:** "Before anyone designs anything, I want the full list of what's already live, because half the room is about to propose rebuilding something that has existed since 2015."

| System | What it actually does | Where it breaks down |
|---|---|---|
| **eVIN** (Electronic Vaccine Intelligence Network) | Real-time stock + cold-chain temperature visibility, live in all 28 states/8 UTs, WHO-recognised best practice, built with UNDP/Gavi support since 2014–17 | Scoped to **vaccines only** — general essential-medicine stock (antibiotics, ORS, IV fluids, analgesics) is a separate, far less mature problem |
| **HMIS** (Health Management Information System) | ~2.17 lakh facilities report 300+ service and 400+ infrastructure data points, monthly, since 2008 | Monthly cadence, not real-time; documented data-quality problems (under-reported deaths, inflated delivery counts in the published literature); frontline staff report losing ~2 hrs/day to manual registers |
| **IHIP / IDSP** (Integrated Health Information Platform) | Near-real-time disease-outbreak early-warning, replacing weekly paper "S/P/L" forms since 2021 | Not connected to the supply chain at all — an outbreak can be flagged here and a PHC 10km away can still stock out with zero automated link between the two; hard-to-reach areas still cite connectivity gaps |
| **ABDM / Health Facility Registry** | National facility-ID and consent-based "federated-with-consent" architecture (explicitly modelled on the Account Aggregator pattern used in Indian fintech) — 3.6+ lakh facilities registered, 1.5+ lakh live on ABDM-enabled software | Built for *clinical records*, not stock/beds/personnel — but its facility-ID layer is exactly what any national platform should key off rather than invent |
| **TNMSC and ~15 state-level siblings** (Kerala's KMSCL, Odisha's, UP's, etc.) | Centralised procurement + a physical "passbook" reorder system that cuts costs ~30% and is the acknowledged national benchmark | Each state built its **own** IT system — a "national" platform isn't a blank slate, it's an integration problem against 15+ already-live, non-identical systems, several of whose states are on record resisting a centrally-imposed replacement |
| **PMBJP / Jan Aushadhi Kendras** | 20,149 generic-medicine outlets (as of June 2026) across 776 of 784 districts, with their own digitised demand-forecasting supply chain for ~400 fast-moving products | A separate retail network, invisible to PHC stock systems — a stocked-out PHC patient has no automated way to know a Kendra 2km away has the same molecule |

**Where they concretely fail (the recent, real example, not a hypothetical one):** in 2024, Tamil Nadu — the state literally used as the *national benchmark* for this exact problem — had roughly ten days of TB drug supply left, nationwide, because of central-procurement delays. The reason TNMSC couldn't simply re-tender wasn't technology, connectivity, or forecasting: the Election Commission's Model Code of Conduct was in force and froze new procurement tenders. **The Domain Expert, again:** "That's the constraint a generalist team will never find on their own, and it's exactly the kind of thing a judge who knows this space will test you on. A pure-tech pitch that can't say the word 'election' anywhere in its risk section hasn't done its homework."

**The table-stakes bar this idea has to clear:** don't rebuild eVIN (vaccines: solved), don't rebuild IHIP (outbreak detection: solved), don't invent a parallel facility-ID scheme (ABDM's HFR already exists), and don't pretend federated learning for Indian health AI is a novel idea this season — the NHA/IIT Kanpur initiative from January 2026 is the bar, and it's aimed at clinical models, not supply chains. The open, under-served seam is **connecting** these already-solved pieces to the specific, still-unsolved slice: essential-medicine (not vaccine) stock, beds, and personnel, forecast and *acted on* automatically, with the last-mile data-honesty problem treated as a first-class design constraint rather than an assumption.

---

## Phase 2 — Divergent Ideation

Sixteen distinct concepts, one line each, tagged by the angle they were forced from. Nobody falls in love yet.

| # | Concept | Angle | Mechanism, one line |
|---|---|---|---|
| 1 | **FedStock Core** | Obvious extension, done well | Federated demand-forecasting layer that reads from existing eVIN/HMIS/state feeds and shares only model weights across ~36 state "silos," never raw data |
| 2 | **Reorder-Point Backbone** | Wins on systems thinking, not AI | Ditch ML; implement TNMSC's proven safety-stock/reorder-point logic nationally as the boring, bulletproof floor everything else sits on |
| 3 | **USSD/IVR Offline Reporting** | Removes a constraint (no smartphone/internet) | Feature-phone voice/USSD stock and attendance reporting with store-and-forward sync for dead-zone PHCs |
| 4 | **ASHA Demand Cross-Check** | Inverts an assumption | Use community health worker check-ins and patient-footfall polling as an independent signal that validates (or contradicts) official facility reports |
| 5 | **Misreporting Trust Layer** | Domain-expert-only idea | Statistically flags facilities whose self-reported stock consumption doesn't match footfall, prescriptions, or their own history — surfaces likely data gaming instead of assuming clean input |
| 6 | **Redistribution VRP Engine** | Obvious clause, executed properly | Treats "recommend cross-district redistribution" as a real constrained vehicle-routing problem (expiry, cold-chain, distance, vehicle capacity), not a vague AI sentence |
| 7 | **Outbreak-Triggered Pre-Positioning** | Borrowed mechanism (military/logistics pre-positioning) | Consumes IHIP-style outbreak early-warning signals to push relevant stock toward a district *before* local demand data shows the spike |
| 8 | **Geofenced Attendance Hotspots** | Domain-expert-documented problem | Lightweight geofenced check-in cross-referenced with patient load to surface likely absenteeism patterns (a problem with real academic literature behind it) |
| 9 | **eVIN Cold-Chain Extension** | Obvious extension | Extend eVIN's proven cold-chain model to non-vaccine temperature-sensitive stock |
| 10 | **TB/DOTS Continuity Tracker** | Extreme edge-case, highest stakes | Flags per-patient TB treatment-continuity risk from facility-level drug stock, since interrupted courses risk drug resistance |
| 11 | **Cross-State Transfer-Learning Cold-Start** | Technical-depth angle | Uses transfer learning so data-poor states/UTs get usable forecasts by borrowing structure from data-rich states inside the federation |
| 12 | **Public Transparency Map** | Inverts the audience | Citizen-facing public stock-status map, using public accountability pressure as the actual lever that improves reporting compliance |
| 13 | **Digital Twin / What-If Simulator** | Wins on systems thinking | Discrete-event simulation letting officials run "what happens if a cyclone cuts off District X" scenarios against the real network graph |
| 14 | **Bed-Surge + Ambulance Routing** | Extreme edge-case user (mass-casualty event) | Real-time CHC/District Hospital bed capacity tied to 108 ambulance dispatch, routing patients to facilities that actually have room |
| 15 | **Seasonality-Aware Forecasting** | Obvious extension, done with real domain features | Bakes monsoon/heatwave/festival-calendar and known disease-seasonality patterns into the forecast as explicit features, not a black box |
| 16 | **Jan Aushadhi Overflow Redirect** | Removes a "given" (PHC as sole channel) | When a PHC is out, redirect the *patient* to the nearest Jan Aushadhi Kendra carrying the same molecule, using the substitute-supply network instead of just measuring the gap |

---

## Phase 3 — Convergence & Scoring

Six criteria, 1–10, one honest line per score. No idea gets a pass because it sounds good on a slide.

| # | Concept | Novelty | Tech depth | Feasibility | Real-world | Demo power | Judging fit | **Total** |
|---|---|---|---|---|---|---|---|---|
| 6 | Redistribution VRP | 6 – common clause, rare to solve as real OR | 8 – genuine constrained optimisation | 6 – OR-Tools + approximate routing, buildable | 8 – the actual operational bottleneck | 8 – animated map is a strong visual | 9 – matches the brief almost verbatim | **45** |
| 1 | FedStock Core | 7 – rare framing in-room, common in research | 8 – real cross-silo, non-IID problem | 6 – nontrivial but doable with Flower-style tooling | 8 – solves the real data-sovereignty blocker | 5 – training curves are hard to *show* | 9 – literally the brief's headline word | **43** |
| 7 | Outbreak Pre-Positioning | 7 – "push before the spike" is a sharp reframe | 7 – real disease-to-commodity modelling | 5 – real IHIP access is unrealistic for a team | 8 – high long-term value if truly integrated | 7 – strong before/after narrative | 8 – matches "early warnings during emergencies" | **42** |
| 5 | Misreporting Trust Layer | 7 – most teams assume clean data | 7 – legitimate applied anomaly detection | 7 – buildable on synthetic injected-anomaly data | 8 – matches documented literature, not speculation | 7 – "187 days, zero stock-outs" is a vivid beat | 5 – doesn't hit federated/redistribution directly | **41** |
| 10 | TB/DOTS Tracker | 8 – nobody else will think of it | 7 – real but privacy-heavy (per-patient linkage) | 6 – needs synthetic patient data, DPDP-sensitive | 6 – high-stakes but narrow, one disease programme | 8 – emotionally compelling single-patient story | 5 – covers one vertical slice, not the platform | **40** |
| 13 | Digital Twin Simulator | 7 – rarely attempted in student hackathons | 7 – real discrete-event/agent-based modelling | 5 – nontrivial to build convincingly in time | 6 – valuable planning tool, not an ops system | 8 – "simulate a cyclone" is a great live beat | 6 – partial fit, strong on "respond when it matters" | **39** |
| 14 | Bed-Surge + Ambulance | 6 – bed-trackers existed ad hoc in 2021 | 7 – real-time capacity matching + routing | 5 – real 108 API access unlikely | 7 – clear institutional buyer in a real crisis | 8 – dramatic live rerouting visual | 6 – partial fit vs. the PHC-level brief | **39** |
| 3 | USSD/IVR Reporting | 4 – feature-phone data collection already exists elsewhere | 6 – unglamorous but real distributed-systems work | 5 – real USSD gateway access is hard to get for a demo | 9 – the single highest-leverage root-cause fix | 7 – a live phone call updating a dashboard is a genuine hook | 5 – it's an input layer, not "the platform" | **36** |
| 4 | ASHA Cross-Check | 6 – sharpens an existing mHealth pattern | 5 – mostly comparison logic, not deep | 7 – straightforward to build | 7 – directly addresses trust | 6 – clear "these don't match" screen | 5 – doesn't hit federated/forecast/redistribution | **36** |
| 11 | Transfer-Learning Cold-Start | 7 – genuinely addresses the non-IID problem | 8 – real ML-research territory | 5 – hard to demo without jargon | 6 – valuable but abstract | 4 – "does better with less data" doesn't visualise | 6 – strong technical match, narrow scope | **36** |
| 12 | Public Transparency Map | 6 – pattern exists elsewhere, reframed as primary lever | 3 – mostly a public API + map UI | 8 – straightforward | 6 – real governance value, real political risk | 7 – judges can interact with it on their own phone | 5 – weak on the technical asks | **35** |
| 15 | Seasonality Features | 4 – standard practice, not really a stand-alone "idea" | 5 – solid, not groundbreaking | 8 – straightforward | 7 – genuinely how you'd want it built | 5 – decent chart, no knockout beat | 5 – a sub-component, not a platform | **34** |
| 16 | Jan Aushadhi Redirect | 6 – nobody else routes the *patient* | 4 – mostly a locate-nearest feature | 7 – public Kendra location data exists | 7 – reduces harm in the stock-out window itself | 6 – relatable, clear | 4 – nice bolt-on, weak as the core pitch | **34** |
| 2 | Reorder-Point Backbone | 3 – deliberately "boring" | 3 – undergrad operations research alone | 9 – trivially buildable | 8 – actually the highest real-world track record (it's what TNMSC runs) | 4 – dull to watch on its own | 4 – risks reading as "no AI" against an "AI platform" brief | **31** |
| 8 | Geofenced Attendance | 4 – similar schemes already exist for govt staff | 3 – mostly CRUD + geofencing | 8 – straightforward | 5 – real problem, politically sensitive framing | 4 – a check-in map isn't thrilling | 5 – hits the clause, feels like a minor feature | **29** |
| 9 | eVIN Extension | 3 – eVIN already does this for vaccines | 4 – shallow unless genuinely extended | 7 – straightforward if scoped to vaccines | 5 – marginal value-add over what exists | 5 – nothing new to show | 4 – misses beds/personnel/redistribution/federated entirely | **28** |

**Two scores worth arguing over out loud.** The **Reorder-Point Backbone** scored lowest of the "serious" ideas, and the **AI/ML Researcher** wants that on the record as a *warning*, not a dismissal: "Every one of the top four ideas needs a safety-stock threshold to trigger against. If you skip building this properly and let your ML forecast *be* the threshold, one bad prediction empties a shelf for real. This idea isn't a finalist — it's the floor the finalists stand on." The **Hackathon Veteran** pushes back on **FedStock Core**'s demo-power score of 5: "A five is generous. Nobody claps for a loss curve. If federation is going in the final build, someone owns making it *visible* in fifteen seconds, or cut it." Both objections get carried into Phase 4 as hard requirements, not suggestions.

**Carried forward (top 4 by score):** #6 Redistribution VRP Engine, #1 FedStock Core, #7 Outbreak Pre-Positioning, #5 Misreporting Trust Layer.

One more thing the scoring surfaced and nobody should ignore: these four aren't four competing ideas. Mapped against the brief's own four asks — visibility, forecasting/early-warning, redistribution, federated modelling — they land one-to-one. That's not a coincidence; it's the scoring exercise finding that a *complete* answer to this brief needs all four working together. Phase 4 deep-dives them separately, as instructed, but Phase 6's verdict treats them as one platform, and says exactly why.

---

## Phase 4 — Deep Dive on Finalists

### A. Redistribution VRP Engine — *the platform's hands*

- **One-liner:** When a facility is sitting on soon-to-expire ORS and another 40km away is about to run out, the system doesn't just alert someone — it computes the cheapest, cold-chain-safe transfer route and hands a logistics officer a ready-to-approve dispatch order.
- **The hook:** click "simulate stock-out" on a live map; in under two seconds the system draws the optimal transfer from among three candidate donor facilities, with ETA and cost attached — not a slide that says "AI recommends a transfer."
- **Target user & scenario:** a District Drug Logistics Officer, Tuesday morning, a sub-centre nurse flags ORS running critically low during a post-monsoon diarrhoeal spike; the officer needs to know *right now* which of six nearby facilities can spare stock without going short themselves.
- **Core mechanism:** *input* — near-real-time stock per facility per SKU (from existing state feeds + offline sync fallback), facility geocoordinates keyed off ABDM's Health Facility Registry IDs, a safety-stock threshold per facility (fed by module B), a road-network distance/time matrix. *Processing* — formulated as a constrained transportation/vehicle-routing problem (donors with surplus above their own threshold vs. recipients below it, minimising distance/time/spoilage-risk subject to vehicle capacity and cold-chain compatibility), solved with an open solver such as Google's OR-Tools, or a fast greedy nearest-surplus heuristic when solver latency matters more than optimality on stage. *Output* — a ranked transfer list with route, ETA, and a human-approval button; it never auto-executes above a configurable value/quantity/controlled-substance threshold.
- **MVP scope:** one state, 50–100 facilities using real district geocoordinates with synthetic-but-plausible stock numbers, a handful of SKUs, one approve/reject officer UI.
- **Stretch/wow layer:** multi-hop chained transfers when no single donor covers the gap; a live "remove this district from the graph" disaster simulation; a cost/CO2-saved framing to sell efficiency alongside availability.
- **Rough architecture:** Python/FastAPI service, OR-Tools (or a lightweight custom solver), PostgreSQL+PostGIS, a self-hosted routing engine over an OpenStreetMap India extract (or a haversine-plus-road-factor approximation if that's too heavy for the clock), React/Leaflet map frontend.
- **What makes it different:** the default pitch is one sentence — "AI recommends redistribution" — with no real routing logic behind it. This has an actual constraint set specific to essential medicines (expiry, cold-chain, controlled-substance exclusion), a real human-approval gate, and keys facility identity off ABDM's existing HFR IDs instead of inventing a parallel database.

### B. FedStock Core — *the platform's brain*

- **One-liner:** every state keeps its consumption data exactly where it already lives — inside TNMSC, Kerala's KMSCL, UP's system, wherever — and only the *learning* travels between states, not the data.
- **The hook:** a federation map where clicking a state shows its local forecast visibly sharpen as it receives a global model update, paired with a live "accuracy before federation vs. after" number — because an invisible loss curve doesn't win a demo, a number that visibly moves does.
- **Target user & scenario:** a state NHM program officer in a smaller UT with too few years of clean local history to trust a model trained on their own numbers alone.
- **Core mechanism:** *input* — per-facility, per-SKU weekly consumption series held locally per state/UT (~36 silos: genuinely cross-silo FL, not the consumer cross-device kind). Each silo trains a local model (gradient-boosted trees or a small temporal model) on its own data plus shared engineered features (monsoon/festival calendar, the outbreak signal from module C). *Processing* — periodically, silos share only model weight updates via a standard algorithm (FedAvg-style averaging, or a personalised variant like FedProx to handle the fact that Kerala's seasonality genuinely differs from Rajasthan's — non-IID data is named and handled, not hidden). *Output* — a per-facility 7/14/30-day stock-out-risk score feeding modules A and D.
- **MVP scope:** 4–6 simulated state silos with deliberately different synthetic seasonal patterns (monsoon-diarrhoeal, post-monsoon dengue, heatwave), built on an existing open FL framework such as Flower rather than hand-rolled FedAvg — reimplementing federation from scratch is a bad use of 36 hours.
- **Stretch/wow layer:** a visible differential-privacy "budget" dial showing the accuracy/privacy trade-off live; a "new state joins" demo showing a cold-start silo benefit immediately via transfer learning before it has its own history.
- **Rough architecture:** Flower (or equivalent) orchestration, per-silo forecasting model, central aggregator, a round-by-round accuracy dashboard per silo and globally.
- **What makes it different:** most "federated AI" pitches this season will be fake federation — one shared database with a buzzword on the slide. This one can be asked, live, "prove the raw data never left the state boundary" and answer by showing the local-train-then-share-weights code path, which is exactly the bar the NHA/IIT Kanpur initiative set for this space in January.

### C. Outbreak-Triggered Pre-Positioning — *the platform's reflex*

- **One-liner:** the system doesn't wait for a facility to report "we're nearly out" — it watches an outbreak early-warning signal and starts moving stock toward the danger zone before the local shelf is even visibly thinning.
- **The hook:** a simulated cluster alert appears on the map; within seconds a pre-position plan appears — stock already routed toward the affected district, days before those facilities would have crossed their own reorder threshold — shown side-by-side against the reactive timeline of what would have happened without it.
- **Target user & scenario:** a District Health Officer during a monsoon week, an acute diarrhoeal-disease cluster starts appearing across three blocks; that signal already exists in India's disease-surveillance system, but nothing currently wires it to the supply chain.
- **Core mechanism:** *input* — an outbreak-cluster signal (in the MVP, honestly simulated from historical/synthetic case data shaped like real IHIP-style reporting, since live government surveillance data isn't accessible to a student team) plus a clinician-reviewed disease-to-commodity mapping table (a diarrhoeal cluster implies an ORS/zinc/IV-fluid demand multiplier; a suspected dengue cluster implies a different one) plus current facility stock. *Processing* — translate cluster severity/radius into a projected demand multiplier for the relevant commodities at facilities in range, and feed that as an elevated-priority request into module A — *before* consumption data shows the spike. *Output* — a ranked pre-position recommendation with a visible "why," naming the triggering signal and confidence.
- **MVP scope:** a small library of 3–4 disease-to-commodity mappings, a clearly-labelled simulated outbreak feed, wired into module A as a priority flag.
- **Stretch/wow layer:** an honestly-labelled integration stub showing exactly which real IHIP field this would consume in production, rather than claiming a live connection that doesn't exist.
- **Rough architecture:** deliberately a simple, explainable rules/lookup service in front of module A — no model needed here. **The AI/ML Researcher, unprompted:** "This is exactly the spot to *not* reach for a neural net. A clinician-reviewed table an auditor can actually read beats a black box nobody can defend in front of a health ministry."
- **What makes it different:** the naive version reinvents outbreak detection from scratch. This one is explicitly reactive to a *named, already-solved* government system, and its only real job is the wiring nobody has done yet.

### D. Misreporting Trust Layer — *the platform's immune system*

- **One-liner:** before the system trusts a "zero stock-outs for six months" report, it checks that claim against independent signals — patient footfall, prescriptions issued, the facility's own history — instead of assuming every number typed into a form is true.
- **The hook:** a facility glowing green on the map ("healthy") flips to amber the instant the cross-check runs: "this facility has reported zero paediatric ORS stock-outs for 187 straight days while treating an above-average number of under-5 diarrhoea cases — that's not good news, that's a flag."
- **Target user & scenario:** a state-level data-quality officer who currently has no way to tell a genuinely well-run facility from one that's simply not reporting bad news — a documented, published problem in Indian HMIS data, not a hypothetical one.
- **Core mechanism:** *input* — self-reported stock/consumption data plus independent cross-check signals (OPD footfall, dispensing counts where available) plus each facility's own historical baseline. *Processing* — statistical anomaly detection (comparing a reported stock-out-free streak against what footfall and historical variance would predict) rather than a black-box classifier — explainable by design, because a flag needs to survive being challenged by the facility it names. *Output* — a facility-level trust score that down-weights suspect self-reports inside modules A and B, plus a human review queue.
- **MVP scope:** a synthetic dataset with a known set of injected "gaming" facilities mixed into honest noise, demonstrating the detector recovers them at reasonable precision — explicitly labelled as synthetic validation in the pitch, not overclaimed.
- **Stretch/wow layer:** a plain-language explanation generator ("this facility's numbers are 4.2 standard deviations from what its footfall predicts") and a facility trust-trend view over time.
- **Rough architecture:** a scheduled batch comparison job using a simple, explainable statistical model — deliberately not deep learning, for the same defensibility reason as module C.
- **What makes it different:** the default pitch assumes clean data and builds a pretty dashboard on top of it. This treats the *data itself* as the thing that needs verifying, which is exactly what the published record on Indian HMIS says is the real failure mode.

---

## Phase 5 — Red Team

### A. Redistribution VRP Engine

| Objection | Attack | Mitigation / honest weakness |
|---|---|---|
| Judge | "A map with routes" blurs into every other logistics-optimisation demo by pitch #30 | Say the differentiator out loud in the first 15 seconds — this optimises under an essential-medicine-specific constraint set, keyed to real facility IDs, not a generic parcel router |
| Critic | Who in real India actually has authority to approve a cross-district transfer, and would they click a button on a hackathon app? | Honest weakness: real sign-off chains and budget codes are more complex than this can model in 36 hours. Scope the narrative explicitly to *decision support*, not automation |
| Engineer | Single point of failure: the live routing/distance-matrix service dies on bad venue wifi | Pre-compute and cache the distance matrix for the demo dataset; degrade to straight-line-distance-times-road-factor if the live service is unreachable |
| Domain Expert | Controlled substances (opioids, some TB/psychiatric drugs) are governed by strict narcotics-control rules a generic "move medicine" engine would violate if it tried to execute them | Explicitly exclude scheduled substances from automated recommendations in v1; route them to a manual-only workflow, and say so unprompted |
| Ethics | Could this incentivise under-reporting to "attract" a transfer, or drain poorer/remote facilities to serve better-connected ones? | Real risk. Module D partially guards the under-reporting angle; make equity an explicit routing constraint (never drain a donor below its own floor), not just distance-minimisation |

### B. FedStock Core

| Objection | Attack | Mitigation / honest weakness |
|---|---|---|
| Judge | A judge who knows the NHA/IIT Kanpur initiative will ask "how is this different?" — and punish a team that doesn't know it exists | Know that landscape cold; volunteer the answer — that initiative targets clinical/diagnostic model federation, this targets supply-chain forecasting specifically, a narrower and less-covered slice |
| Critic | Is the accuracy gain from federation big enough to justify the complexity, versus one pooled model on combined synthetic data? | Honest weakness: with ~36 silos and hackathon-scale data, the demo's own accuracy delta may be modest. Show the real number; frame the value as adoptability (states that would refuse to share raw data will accept this), not accuracy alone |
| Engineer | Central aggregator is a single point of failure; live multi-round training is slow and boring to watch | Pre-run federation rounds, replay the accuracy-improving trend fast-forwarded; keep a cached last-known-good global model |
| Domain Expert | Federated learning solves the *technical* data-sovereignty problem, not the *political* one of who controls the aggregator — states have documented reasons to resist a Delhi-run version of exactly this | No purely technical fix exists. Propose the aggregator be positioned as neutral infrastructure (mirroring how ABDM is positioned), not a single ministry's tool — and say that's a political choice, not a solved one |
| Ethics | Model updates can still leak information about local data under known attacks — FL is privacy-*enhancing*, not privacy-*guaranteed* | Say this honestly rather than oversell "fully private"; name differential privacy/secure aggregation as the next hardening step, consistent with what the current FL-in-healthcare literature actually says |

### C. Outbreak-Triggered Pre-Positioning

| Objection | Attack | Mitigation / honest weakness |
|---|---|---|
| Judge | Risks reading as a small bolted-on feature rather than a headline idea | Keep it visually and narratively distinct in the demo — its own map state, its own "why" panel |
| Critic | Real IHIP data access is realistically out of reach for a student team | Say so plainly in the pitch — this runs on historical/synthetic data for the demo and is architected as a named integration point for the real feed. A caught false claim of live government-data access is far more damaging than an honestly-scoped one |
| Engineer | False-positive outbreak signals could trigger costly, unnecessary pre-positioning and erode trust | Keep the trigger a human-reviewed elevated-priority flag, not an autonomous shipment, until the mapping table has a track record |
| Domain Expert | A generic disease-to-commodity table will sometimes be wrong — not every cluster of the same label needs the same response | Keep the table clinician-reviewed and versioned; its output still funnels through the same human-approved workflow as everything else |
| Ethics | Could over-indexing toward "flagged" districts starve quieter ones, especially if alerting itself has geographic/reporting bias? | Same equity concern as module A, same fix: a hard safety-stock floor constraint that can't be routed below |

### D. Misreporting Trust Layer

| Objection | Attack | Mitigation / honest weakness |
|---|---|---|
| Judge | "Anomaly detection" can sound abstract and fail to land in three minutes | The "187 days, zero stock-outs" framing exists specifically to solve this — keep it front and centre, not buried in a methods slide |
| Critic | Could this just flag a genuinely well-run, well-stocked facility as "suspicious" — punishing the people doing it right? | Real, serious risk. Frame every output strictly as "worth a second look," never an accusation or automatic penalty; report a calibrated false-positive rate honestly, out loud, in the demo |
| Engineer | The detector's power is capped by how good the independent cross-check signal is, and that data may itself be missing for many facilities | Honest weakness the team doesn't fully control. Degrade gracefully to flagging pure internal-consistency implausibilities (stock numbers that never move) when no good secondary signal exists |
| Domain Expert | This is the module most likely to trigger real pushback — pointing even a statistical finger at overworked, under-resourced frontline staff risks blaming the most powerless people in the chain for a system-level failure | Position outputs as system-level, supervisory/support signals — "does this area need help" — never as an individual staff-performance metric, and say that design choice out loud unprompted |
| Ethics | The sharpest flag of the four: a "trust score" attached to a facility (or informally to the person entering data) could be misused punitively against workers already doing their best with bad tools | Attach scores to facilities and patterns, never to named individuals; keep them explicitly supervisory, never auto-punitive; state this as a deliberate design decision in the pitch, because a good judge will specifically probe for it |

---

## Phase 6 — Verdict

**The pick:** build all four modules as one platform, not one module alone. The single sharpest reason: the brief names four distinct requirements — visibility, forecasting/early-warning, redistribution, federated modelling — and the top four scoring concepts from an independently-run ideation pass map onto those four requirements one-to-one. That's the scoring exercise finding, on its own, that a complete answer needs all four; picking just one and calling it "the platform" would be exactly the kind of padded, hand-waved scope the brief explicitly warns against.

**Runner-up:** not a fifth concept, but a leaner alternative architecture — ship *only* FedStock Core (module B) at full depth, forecasting without any redistribution action-layer. It scored second-highest standing alone and is the lowest-engineering-risk path. **The Founder's** one-line reason it loses: "A forecast that doesn't cause anything to happen is a very smart way of telling someone they have a problem and then doing nothing about it — it doesn't survive contact with a real user next week." The **TB/DOTS Continuity Tracker** (5th place, score 40) is the strongest single *feature* to fold in as a stretch layer — its per-patient framing gives the demo's closing beat real emotional weight — but it's a vertical slice of one disease programme, not a platform, and shouldn't be scoped as a core module.

**Judging-criteria map for the fused platform:**

| Criterion | Score | Why |
|---|---|---|
| Novelty | 8/10 | Real federation you can prove isn't faked, plus a trust layer almost nobody else will think to build |
| Technical depth | 8/10 | Constrained optimisation, cross-silo non-IID federated learning, explainable anomaly detection — no step is hand-waved |
| Feasibility | 6/10 | Honest 6, not a 9 — four modules is real scope for 36 hours; the roadmap below exists specifically to protect against this |
| Real-world viability | 8/10 | Every module is grounded in a documented, current gap, not a guess |
| Demo power | 7/10 (up from FedStock alone's 5) | Fixed by making federation *visibly* measurable and giving the redistribution engine a live map as the emotional anchor |
| Judging fit | 9/10 | Matches the brief's own four requirements point for point |

**Build roadmap — a working demo exists at every checkpoint:**
- **Core:** module A alone — single-state synthetic data, safety-stock thresholds (no ML yet), live map, approve/reject UI. This must be bulletproof before anything else is touched.
- **Enhanced:** add module B (federated forecasting across simulated multi-state silos, feeding A's thresholds) and module D (trust layer flagging obviously implausible facilities).
- **Stretch/Wow:** add module C (outbreak pre-positioning) and the before/after federation accuracy visual; multi-hop chained transfers if time allows.
- **Polish:** rehearse the 15-second hook, test the offline/degraded-mode fallback for real, prepare a one-line answer to "how is this different from the NHA hackathon."

**Kill list:**
1. *Federated learning is invisible and unconvincing on stage.* Defuse by building the before/after accuracy visual early, not as an afterthought — rehearse the fifteen-second version of "here's the number that proves federation actually happened."
2. *Four modules built to 25% depth each instead of two built to 100%.* Defuse with roadmap discipline — module A ships perfect even if C never ships at all.
3. *Getting caught claiming a live government-data connection (IHIP, real HMIS) that isn't real.* Defuse by stating plainly, in the pitch itself, what's live versus synthetic-and-architected-for. An honestly-scoped demo beats a caught exaggeration every time a Ruthless-Critic-type judge is in the room.

**Demo narrative:**
- *Opening hook (15 sec):* in 2024, Tamil Nadu — the state used nationally as the benchmark for exactly this problem — had about ten days of TB drug stock left, and the cause had nothing to do with technology at all: an election froze procurement. That's the world this platform has to survive in.
- *Demo beat 1:* a facility approaches stock-out; the trust layer confirms the report is real, the redistribution engine proposes a transfer with route and ETA, the officer approves it live.
- *Demo beat 2:* switch to the federation view; pick a low-data state; watch its forecast sharpen as it benefits from the federation while zero raw data from any other state ever appears on screen.
- *Technical highlight:* show the actual code path proving no raw data crossed a state boundary, and the routing engine's constraint set, for one beat each.
- *Impact close:* return to the opening hook — show the reactive timeline (what would have happened without this) against the pre-positioned one, and end on the specific number of stock-out-days avoided.

---

## Appendix — Submission-Ready Brief

**Title:** SwasthSetu — A Federated AI Platform for Real-Time PHC Resource & Supply Chain Management *(working title — confirm it isn't already in use before final submission)*

**Problem:** India's ~32,000 Primary Health Centres and their surrounding network lack real-time, trustworthy visibility into medicine stock, bed availability, and personnel attendance, causing preventable stock-outs during exactly the emergencies when supply matters most — a gap that persists even in states held up as national best-practice models.

**Proposed Solution:** a four-module platform — a redistribution engine that turns cross-district transfers into a real routing/optimisation problem with human approval; a federated forecasting core where states share only model learning, never raw data; an outbreak-triggered pre-positioning layer wired to existing disease-surveillance early warning; and a misreporting trust layer that checks self-reported stock against independent signals before trusting it.

**Innovation & Uniqueness:** unlike single-dashboard or generic-forecast pitches, this treats redistribution as constrained optimisation (not a slogan), treats data honesty as a first-class problem instead of an assumption, and implements verifiable federation — provable on demand that raw state data never leaves its own boundary — rather than a centrally-pooled database wearing the word "federated."

**Technical Approach:** Python/FastAPI services; OR-Tools (or equivalent) for routing; an open federated-learning framework (e.g., Flower) coordinating per-state models with FedAvg-style or personalised aggregation; explainable statistical anomaly detection (not a black box) for the trust layer; PostgreSQL+PostGIS and a map-based frontend; facility identity keyed off ABDM's existing Health Facility Registry rather than a new parallel ID scheme.

**Feasibility & Viability:** every module builds *on top of* live government infrastructure (ABDM/HFR for identity, IHIP-style signals for outbreaks, existing state procurement systems for stock feeds) rather than replacing it, which is both the fastest path to a working hackathon build and the only realistic path to real adoption, given states' documented reluctance to hand over raw operational data to a centrally-run alternative.

**Impact & Benefits:** faster, evidence-based cross-district redistribution before shelves empty; forecasting that improves for data-poor states without compromising any state's data sovereignty; earlier action on disease-driven demand spikes; and a defensible, auditable way to tell a genuinely well-run facility from one that's simply not reporting bad news.
