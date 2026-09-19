# SPEC\_DIGEST.md

Digest of `masterbuildspec-FINAL.md`, `masterbuildspec-v3.md`, `research.md`, `implementation.md`. Load this at the start of every session. Go to the source file for anything not here.

## NEEDS MY DECISION — v3 vs FINAL conflicts, unresolved

Neither file carries a calendar date. `v3` opens "Supersedes v2"; `FINAL` self-identifies as "v2 (FINAL)" — so v3 is later by self-declaration, but these are real contradictions, not just additions, and are listed both ways rather than auto-resolved.

**1. FL delivery mechanism — reliability-first vs. Flower's real deployment engine.**
FINAL: "Hand-rolled FedAvg loop... no Flower dependency required... one fewer dependency chain that can fail, for a team that has already lost time to a dependency failing." `\[FINAL, Revision Pass / §2]`
v3: reverses this — Flower's deployment engine (a real SuperLink + four SuperNode processes) is now primary; the in-process parity runner is marked "superseded." `\[v3, §27 / §28 Phase B4]`
→ This reintroduces exactly the external-process dependency FINAL's rule existed to eliminate. Decide: Flower's engine as primary with the hand-rolled loop as a tested fallback, or the reverse.

**2. Hosting — optional vs. required remaining phase.**
FINAL: "Hosting (optional)... only if a shareable link is needed; localhost is fine for the demo." `\[FINAL, §2]`
v3: Cloud Run/Cloud SQL/Firebase in the locked stack with no "optional" qualifier; Phase E is a required remaining phase. `\[v3, §4 / §28 Phase E]`
→ **Hackathon correction, confirmed this session against the actual rules: Cloud Run is NOT mandated. The only hosting requirement is any live deployed link. The mandatory item is Google AI integration.** Both specs overstate Cloud Run's necessity.

**3. Auth — explicit non-goal vs. fully built.**
FINAL: "Real auth/login — one hardcoded officer role is enough." (Non-goal) `\[FINAL, §14]`
v3: four real roles, JWT, database-backed sign-in limits — logged as already built. `\[v3, §3 / §28 "Already built"]`
→ Decide: keep it, or treat it as scope the hackathon rules never asked for.

**4. Map library — key-free-by-design vs. Google-primary.**
FINAL: Leaflet + OSM only, "No API key required." `\[FINAL, §2]`
v3: Google Maps JS API primary, Leaflet/OSM demoted to `MAPS\_MODE=osm` fallback. `\[v3, §4]`
→ Not hackathon-mandated either way (see #2) — decide which is genuinely primary for the demo.

**5. Frontend language.** FINAL: plain JS `\[FINAL, §2]`. v3: TypeScript `\[v3, §4]`. Low-stakes — check what the repo is actually written in.

\---

## 1\. Non-negotiable rules and invariants

* Read the full spec before writing code; build in the exact order of the live Build Sequence, one checkpoint at a time. `\[v3, §1.1–2 / §28]`
* **Steps 1–9 may import no LLM/Twilio/Maps SDK and require zero API keys.** The app must start, seed, and demo fully with every credential blank. `\[v3, §1.3 / §6]`
* `LLM\_MODE`, `MAPS\_MODE`, `COMMS\_MODE` are first-class; every external dependency ships a tested fallback, and **both paths stay tested**. `\[v3, §1.4]`
* `services/llm.py` is the only file that may import `google.genai`; `services/comms.py` is the only file that may import the Twilio SDK; `services/maps.py` is the only file that may call the Routes API. `\[v3, §14/§15/§17; FINAL §11.1]`
* Federated learning keeps each silo's raw data genuinely local. Only `state\_dict()` (weights) may leave the training loop. `\[v3, §1.5 / §12.2]`
* Redistribution/outbreak always ends in human approval. **Nothing auto-executes.** Controlled-substance SKUs are excluded from the solver entirely, routed to a manual-only queue. `\[v3, §1.6 / §12.3; research.md Red Team §A]`
* Never propose taking a donor below its own safety stock, in either solver implementation. `\[v3, §12.3]`
* Trust and outbreak logic must be explainable statistics — **never an opaque ML model.** `\[v3, §1.7]`
* Trust flags attach to **facilities and patterns, never a named individual**; staff data aggregates to facility level only; never described as fraud detection. `\[v3, §1.8 / §12.6; research.md Red Team §D]`
* **All inbound webhooks validate their provider signature before processing. No exceptions.** `\[v3, §1.9]`
* The trust score is computed live from the same tables everything else reads — never cached, never a parallel dataset, except one materialized copy for the national map, itself refreshed from the same `trust.compute()`. `\[v3, §12.6]`

\---

## 2\. Architecture decisions already made, with reasons

* **Three mode switches, not one** (`LLM\_MODE`/`MAPS\_MODE`/`COMMS\_MODE`), each defaulting to zero-credential — this project already lost its visible layer once to an external dependency. `\[v3, Revision Pass v2→v3, §6]`
* **Hand-rolled FedAvg → FedProx → Flower deployment engine.** FedProx anchors local training against non-IID state data; Flower's real deployment engine was chosen over simulation as stronger, more verifiable demo proof. `\[v3, §27, §28 Phase B — see Conflict #1]`
* **One ingestion pipeline, five thin channel adapters** — every channel normalizes into one `StockReading` through one dedupe→identify→extract→resolve→validate→score→commit→recompute→emit→confirm spine. `\[v3, §8, §13]`
* **Two-sided medicine ledger** — dispatch logged at the warehouse independently of the facility (mirrors e-Aushadhi), receipt confirmed separately; a mismatch surfaces itself. `\[v3, §26.3]`
* **Trust-weighted federation contributions** — a heavily-flagged silo contributes proportionally less to the global average, recomputed from the same live rows the trust score reads. `\[v3, §27, §28 Phase B]`
* **Publishing, not serving** — the trained model never runs inside the web service; a script writes predictions to a table and the API reads rows, so a bad training run degrades to burn rate instead of breaking the dashboard. `\[v3, §28 Phase B]`
* **Outbreak pre-positioning is a temporary multiplier into the existing redistribution solver, not a new subsystem.** `\[FINAL, Revision Pass; v3, §12.5]`
* **Facility identity keys off ABDM's HFR id format** rather than a new scheme. `\[research.md, Phase 1; v3, §9]`

\---

## 3\. Known risks / gotchas documented in the specs — status

|Risk|Status|Pointer|
|-|-|-|
|Cloud Run/Cloud SQL scripts written, never run against a real project|**unverified**|v3, §28|
|SSE breaks past 1 Cloud Run instance|needs `REDIS\_URL` or pinned instance count — **undecided**|v3, §16|
|WhatsApp outbound blocked outside 24h session window|**mitigated by design** — SMS for critical alerts|v3, §14.2|
|`import ortools` can fail natively|**mitigated by design** — greedy fallback is the sanctioned default|v3, §7 / §12.3; FINAL §5|
|Federation accuracy can appear flat if silos are accidentally IID|**mitigated by design** — per-facility variance required in the generator|v3, §10 / §23|
|Twilio webhook 403s on signature mismatch|**documented fix** — validate against the exact URL called, incl. scheme/query|v3, §23|

\---

## 4\. Hard constraints

* **No patient-level data exists in this system at all.** `\[v3, §20]`
* India's **DPDP Act 2023** is the relevant frame, not HIPAA. `\[v3, §20]`
* Phone/staff identifiers: salted-hash only; UI shows masked forms. `\[v3, §9.1 / §20]`
* Server-side keys never reach the client bundle; only the referrer-restricted Maps browser key does. `\[v3, §6 / §20]`
* **Hackathon compliance (verified this session, not in the uploaded specs):** only hosting requirement is any live deployed link — Cloud Run not mandated. Mandatory item is Google AI integration (GenAI/predictive/vision). `\[confirmed against actual event rules — see Conflict #2]`
* Federated learning is **privacy-enhancing, not privacy-guaranteed** — name DP/secure aggregation as future work, don't overclaim. `\[v3, §20]`

\---

## 5\. Build order — done vs. pending 

*Verified against the working tree on 2026-09-20 at commit `542d8dc`. B3 and all of Phases C and D were built on 2026-09-16, then deliberately removed by resetting `main` to `542d8dc` to restart that work differently; the removed code survives only in the local branch `backup-before-phase-c-removal`, and the database was rolled back to migration `3de61cc073d9` to match. This section, not memory, is the status of record.*

**Done (core):** scaffold, schema, migrations, national synthetic seed (3,496 facilities/34 states), reorder floor, zoomable map, dashboard, redistribution solver (both fallbacks + rationale), approve/reject with row locking, real auth (4 roles), live updates, Routes caching with honest fallback, production security posture. `\[v3, §28 "Already built"]`

**Phase A — Capture \& trust: done.** Ledger, movement tab, bed capture (code + Gemini Vision), attendance geofence, trust score + audit queue, trust wired into early-warning. `\[v3, §28 Phase A]`

**Phase B — Federated forecasting: B1, B2, B5 done — B3 not built.** Live per-state silo partitions, FedProx with trust-weighted contributions on Flower's deployment engine, and forecasts feeding days-of-stock behind `FORECAST_MODE` — all three re-verified live on 2026-09-20. B3 (the `federation_rounds` inspector columns and the silo inspector UI) was built and then removed; the table exists without those columns. B4 stays superseded by the deployment engine. `\[v3, §28 Phase B]`

**Phase C — Omnichannel: not started.** The ingestion spine, field client and 70/30 split view were built on 2026-09-16 and removed; there is no `app/ingest.py`, no `field.tsx` and no phone registry in the tree. Real SMS/WhatsApp/IVR still need credentials. `\[v3, §28 Phase C]`

**Phase D — Differentiators: not started.** Outbreak pre-positioning and the impact-replay/eval harness were built and removed; copilot, what-if twin and Jan Aushadhi redirect were never built. `\[v3, §28 Phase D]`

**Phase E — Deployment: not started.** Scripts written, never run. `\[v3, §28 Phase E]` **Before any deploy:** generate a real password for the least-privilege `swasthsetu_app` role and put it in `DATABASE_URL`. Production mode refuses to start while the URL carries the `postgres:postgres` development credentials — and the local Postgres password is currently exactly that, which is why the local server runs in development mode. Not set yet.

## 6. Google integration — standing instruction

Hackathon rule: mandatory is at least one of GenAI/predictive/vision — confirmed, not "as many as possible." Treat broader integration as a good-faith strategic bet, not a scoring checkbox: never at the cost of the federated-learning differentiator, never by retrofitting already-shipped code.



Already integrated: Gemini bed-photo vision behind `LLM_MODE` (plain-language briefings were never built), and Google Maps as Routes API road distances plus the **Map Tiles API** basemap rendered in Leaflet — not the Maps JavaScript API widget.

Legitimate remaining opportunities, only where they add real value to pending work: Cloud Speech-to-Text as the primary voice-input path (Bhashini as the value-add layer on top, per §26.4 — don't reverse this), BigQuery for the national dataset if analytics work is still pending, Dialogflow only if it's a genuine upgrade over Gemini function-calling for the Copilot.

Never: Vertex AI/AutoML as a replacement for the FedProx/Flower core — that pools data centrally and contradicts the federation guarantee. Never: swapping already-built infra (Postgres/SSE, current auth) for a Google equivalent with no new capability.

Whenever a pending build step has a real choice between a Google tool and a non-Google one, default Google and say why in one line. When you spot a genuine, additive integration opportunity, propose it — don't wait to be asked.


\---

## Read before you build

|Area|Read first|
|-|-|
|Trust score|v3 §12.6, §9.1, §26; research.md Red Team §D|
|FL training|v3 §12.2, §27, §28 Phase B; FINAL §9.2 (original baseline)|
|Dashboard|v3 §8, §16; FINAL §12 (page spec, still valid)|
|Gemini integration|v3 §17, §26.2, §26.4|
|Deploy|v3 §28 Phase E, §16 (SSE caveat); FINAL §2 — read Conflict #2 first|
|Data seeding|v3 §10, §9.1; FINAL §7 (original version)|
|Twilio / ingestion|v3 §13, §14, §28 Phase C|
|Redistribution / OR-Tools|v3 §12.3; FINAL §9.3|
|Medicine movement ledger|v3 §26.3|

\---

## Didn't fit — go to the source for these

* Full API contract tables (v3 §11; FINAL §8)
* Full demo scripts, beat by beat (v3 §24; FINAL §16)
* Competitive recon (eVIN/HMIS/IHIP/ABDM/TNMSC) and the 16-concept ideation scoring table (research.md, Phases 1–3)
* Full Red Team objection/mitigation tables for all four modules (research.md, Phase 5)
* Exact synthetic-data consumption formula and SKU alias lists (v3 §10)
* SMS grammar syntax and IVR DTMF fallback wording (v3 §13, §14.3)
* Measured eval-harness numbers — MAE, precision/recall, stock-out-days: **the harness and its recorded runs were removed with Phase D, so nothing in the tree can regenerate them** (see §5)

