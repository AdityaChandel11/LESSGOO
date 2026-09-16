# PHC Platform — Master Build Specification v3 (FINAL)

*Supersedes v2. Fully self-contained — this document alone is enough to execute the build. It opens with a revision pass (what changed since v2 and why) so the reasoning travels with the spec, then gives the complete standalone plan.*

**Working name:** SwasthSetu — National PHC Resource & Supply Chain Platform.

---

## Revision Pass v3.1 — the capture layer moves to the front

Two source documents arrived after v3 was written: `federated-health-brief.md` (the federated architecture in depth) and `data trust layer.md` (how data is actually captured from facilities with no internet and no biometric hardware, and how it is verified). They are not a bolt-on. They describe the **input layer this spec assumed already existed**, and they change the order of the build:

1. **Trust gates forecasting, not the other way round.** v3 scheduled the trust layer in Week 4, after federation. Wrong order: a handful of facilities reporting bad numbers pushes bad gradients into the shared model *every round*. Flag first, then forecast. The trust layer moves ahead of federated training — see the rebuilt Section 18.
2. **Verification is a data-model concern, not a screen.** Rotating codes, dispatch↔receipt pairing, and geofence results have to exist as columns from the first synthetic row, so they are added to Section 9 (→ **9.1**) rather than retro-fitted.
3. **A medicine movement ledger is missing from v3 entirely.** v3 tracks stock *levels* (a self-reported number). The brief's dispatch-side pattern — log the batch when it leaves the warehouse, independently of the facility — makes the ledger two-sided and self-checking. New Section 26.3.
4. **Gemini gets real work.** Bed-photo analysis (count beds, classify occupied, read the day's rotating code back out of the image) and bill OCR for consumption. This is multimodal reasoning producing verifiable database rows, not an API checkbox. New Section 26.2.
5. **Maps gets a second job.** Beyond road distances for the optimizer (already built), geofence verification of check-ins and photos. New Section 26.1.
6. **Federation sharpens.** FedProx over vanilla FedAvg for non-IID state data, and Flower (`flwr`) as a real, citable simulation path alongside the in-process trainer. New Section 27.

**Honesty rule carried into all of it:** every verification result records *which* check actually ran and on what evidence (`gps`, `cell_id`, `simulated`, `none`). A simulated signal is never displayed as a verified one. See 26.1 for what is genuinely obtainable over a phone call and what is not.

---

## Revision Pass — what changed since v2, and why

**Runway changed: ~36 hours → several weeks, open-ended.** v2 was written under hackathon time pressure and correctly cut auth, deployment, and proof tooling as non-goals. With weeks available, those cuts stop being prudent and start being the gap between a demo and a product. Role-based access, provenance/audit trails, a computed impact number, an eval harness, and real deployment are all now **in scope**.

**Credentials changed: none → Gemini, Google Maps Platform, and Twilio all live.** v2's `LLM_MODE=mock|live` switch stays — but its purpose is upgraded. It is no longer scaffolding to be replaced by the real thing; it is **stage insurance that gets tested continuously alongside the live path**. Insurance you never exercise is not insurance. Every external dependency in this build ships with a tested fallback, because this project has already lost its visible layer once to an external dependency.

**Three new capability areas, each earning its place against the competitive record:**

1. **Omnichannel ingestion via Twilio (SMS + WhatsApp + IVR).** All three past winners assumed a smartphone and a browser. The recon in `research.md` says the facilities that most need this platform have patchy-to-absent connectivity and staff losing ~2h/day to paper registers. Feature-phone reporting scored **9/10 on real-world value — the highest of all sixteen concepts in the ideation pass** — and was ranked down only because it was judged hard to demo. Twilio removes that objection entirely: a judge can text or call the number from their own handset and watch the dashboard move.

2. **Google Maps Platform (Routes API).** Replaces haversine×1.3 with true road distance, traffic-aware ETA, and a rendered road polyline. Converts "AI recommends a transfer" into something a District Drug Logistics Officer could actually dispatch — and it is verifiable by a judge with a phone.

3. **The split-screen Command + Field view.** A two-sided system is normally impossible to demo because a judge can only watch one side at a time. Putting the government command view (~70%) and the live field client (~30%) in one frame makes cause-and-effect visible in a single glance.

**What did NOT change.** The layer architecture, the hand-rolled FedAvg loop, the human-approval gate on every transfer, controlled-substance exclusion, explainable-not-ML trust and outbreak logic, and the rule that the core must run with a blank `.env`. Those were right in v2 and are right now.

**Ranked build order (unchanged in priority, expanded in depth):**

| Priority | Layer | Why ranked here |
|---|---|---|
| 1 | Command Dashboard + realtime | Every past winner led with it; zero external dependencies; the floor of a submittable project |
| 2 | Reorder floor + Redistribution | The operational payoff; OR-Tools with a dependency-free fallback; real routes as an upgrade |
| 3 | Federated forecasting | The genuine differentiator vs. past winners; lowest-risk implementation of it |
| 4 | Omnichannel ingestion + Field client | The most-validated winner pattern, extended to the users the winners excluded |
| 5 | Copilot | Explains, never decides |
| 6 | Trust layer | Differentiator #2; cheapest layer to build |
| 7 | Outbreak pre-positioning | Differentiator #3; reuses layer 2's solver |
| 8 | Proof layer (impact + eval) | Turns claims into computed numbers |

---

## 1. Read this first — non-negotiable rules

1. Read this entire document before writing code.
2. Build in the exact order in **Section 18 — Build Sequence**. Each step has a checkpoint; do not start the next until the current one demonstrably works.
3. **Nothing in Steps 1–9 may import an LLM SDK, a Twilio SDK, or require any API key.** The dashboard, reorder floor, redistribution engine, and federation must run and look complete with zero credentials configured. This is the direct fix for a failure this project has already hit.
4. **Every external dependency ships with a tested fallback, and both paths stay tested.** `LLM_MODE`, `MAPS_MODE`, `COMMS_MODE` are first-class, not afterthoughts.
5. Federated learning keeps each silo's raw data genuinely local — never pool it into one training set "for now." The hand-rolled loop in Section 12.2 makes this inspectable in ~25 lines; that inspectability is the point.
6. Redistribution and outbreak pre-positioning always end in a human-approved recommendation. Nothing auto-executes. Controlled-substance SKUs are excluded from the solver entirely.
7. Trust and outbreak layers use explainable statistical logic — formulas and lookup tables, never opaque models.
8. Trust flags attach to **facilities and patterns, never to named individuals.** Staff data is aggregated to facility level only. This is a design decision to state out loud in the pitch, because a good judge will probe for it.
9. All inbound webhooks validate their provider signature before processing. No exceptions.
10. Code snippets here are illustrative patterns. Confirm exact current library syntax against vendor docs where noted — SDKs move fast — but keep architecture and intent intact.

---

## 2. Product brief

Real-time visibility into medicine stock, bed availability, and personnel attendance across a network of PHCs/CHCs, with:

- a live traffic-light command dashboard for government officials,
- demand forecasting that genuinely improves through cross-state federated learning without raw data ever leaving a state,
- a human-approved redistribution engine solving real constrained optimisation over real road routes,
- **omnichannel field reporting that works on a feature phone with no internet** (SMS, IVR voice, WhatsApp) as well as a full offline-capable smartphone app,
- an explainable trust layer that flags implausible self-reports,
- outbreak-triggered pre-positioning,
- and a computed impact number rather than a claimed one.

This is a Google-affiliated hackathon (Hack2skill), so the stack leans Google-native — Gemini, Maps Platform, OR-Tools (itself a Google open-source project), Cloud Run — where that is a genuine fit rather than a forced one.

**Demo arc:** open the split view → a reading arrives from a judge's own phone by SMS and the map repaints live → trust layer confirms the report is plausible → redistribution proposes a real road route with ETA → approve it and watch the facility flip green → switch to Federation and prove in the code that no raw state data left its silo → show a flagged too-good-to-be-true facility → fire a simulated outbreak and show the pre-position beat the reactive timeline → close on computed stock-out-days avoided.

---

## 3. Target users and roles

| Role | Who | Primary surface | Can do |
|---|---|---|---|
| `facility_user` | PHC/CHC pharmacist, ANM, store staff | Field client (app / SMS / IVR / WhatsApp) | Submit stock, beds, check-in; see own facility status |
| `block_mo` | Block Medical Officer, District Drug Logistics Officer | Command dashboard | Approve/reject transfers; view district; receive critical alerts |
| `state_officer` | State NHM program officer | Command dashboard | Federation view, trust review queue, what-if simulator, impact ledger |
| `admin` | Platform operator | All | Seed, replay, configure |

Auth: lightweight JWT with role claims. No external identity provider. Officer approval actions are recorded in `approvals` with actor, channel, and timestamp.

---

## 4. Locked tech stack

| Layer | Choice | Why |
|---|---|---|
| Backend | Python 3.11+, FastAPI, Uvicorn | Fast to write, async-capable |
| DB | PostgreSQL 15+ with PostGIS, SQLAlchemy 2.x (async) | Geo queries for radius/distance work |
| Migrations | Alembic | Weeks-long runway means schema will move |
| Frontend | React + Vite, TypeScript, Tailwind CSS | TS is worth it now that the runway allows |
| Data fetching | TanStack Query | Cache + realtime invalidation, no Redux |
| Map (primary) | Google Maps JavaScript API — Advanced Markers, clustering, heatmap | Sponsor-aligned; real routes rendered |
| Map (fallback) | Leaflet + OpenStreetMap | `MAPS_MODE=osm`, requires no key |
| Routing/distance | Google **Routes API** (`computeRoutes`, `computeRouteMatrix`), cached to Postgres | Real road km + traffic ETA |
| Routing fallback | Haversine × 1.3 road factor | Zero dependency |
| Solver | OR-Tools `min_cost_flow` | Correct shape for donor→recipient allocation |
| Solver fallback | Pure-Python greedy (Section 12.3) | Removes native-install risk |
| Federated learning | **Hand-rolled FedAvg, PyTorch + NumPy only** | Same real guarantee, far fewer moving parts; inspectable |
| Forecast model | Small PyTorch MLP (2 hidden layers) per silo | Continuous params average cleanly under FedAvg |
| Realtime | Server-Sent Events (`sse-starlette`) | One-way push, proxy-friendly, auto-reconnect built into `EventSource` |
| Realtime broker | Redis pub/sub (only when >1 backend instance) | See Section 16 caveat |
| Telephony/messaging | **Twilio** — Programmable Messaging (SMS + WhatsApp), Programmable Voice (IVR) | Reaches feature phones with no data |
| Multimodal AI | Gemini API (`google-genai` SDK) behind `LLM_MODE` | Photo/audio → structured data |
| Language (optional) | Bhashini (Govt. of India ASR/translation) | 22-language coverage; also a credible "India's own AI mission" story |
| Offline | PWA — service worker (Workbox) + IndexedDB queue | Connectivity treated as absent, not uncertain |
| Hosting | Cloud Run (backend) + Cloud SQL + Firebase Hosting (frontend) + Secret Manager | Shareable live link |

---

## 5. Repository structure

```
phc-platform/
├── backend/
│   ├── app/
│   │   ├── main.py
│   │   ├── config.py                    # env + all three mode switches
│   │   ├── db.py
│   │   ├── auth.py                      # JWT + role guards
│   │   ├── events.py                    # SSE bus / Redis pub-sub
│   │   ├── models/                      # SQLAlchemy ORM (Section 9)
│   │   ├── schemas/                     # Pydantic
│   │   ├── routers/
│   │   │   ├── facilities.py stock.py transfers.py forecast.py
│   │   │   ├── federation.py trust.py outbreak.py
│   │   │   ├── ingest.py copilot.py
│   │   │   ├── webhooks_twilio.py       # SMS / WhatsApp / Voice
│   │   │   ├── stream.py                # SSE endpoint
│   │   │   └── impact.py eval.py
│   │   └── services/
│   │       ├── reorder.py               # 12.1
│   │       ├── federated_train.py       # 12.2 — hand-rolled FedAvg
│   │       ├── routing.py               # 12.3 — OR-Tools + greedy
│   │       ├── anomaly.py               # 12.4
│   │       ├── outbreak_map.py          # 12.5 — reuses routing.py
│   │       ├── ingestion.py             # 13 — the unified spine
│   │       ├── sku_match.py             # fuzzy SKU resolution
│   │       ├── comms.py                 # 14 — Twilio mock/live boundary
│   │       ├── maps.py                  # 15 — Routes API mock/live boundary
│   │       ├── llm.py                   # 17 — Gemini mock/live boundary
│   │       ├── impact.py                # 19.1 — counterfactual replay
│   │       └── evaluation.py            # 19.2 — eval harness
│   ├── scripts/
│   │   ├── generate_synthetic_data.py   # Section 10
│   │   ├── precompute_matrix.py         # caches the route matrix
│   │   └── run_eval.py
│   ├── alembic/
│   └── requirements.txt
├── frontend/
│   ├── src/
│   │   ├── pages/  Dashboard FacilityDetail Transfers Federation
│   │   │           TrustReview WhatIf Impact Copilot
│   │   ├── field/                       # the mobile PWA surface
│   │   │   ├── FieldApp.tsx  StockForm  PhotoCapture  VoiceNote
│   │   │   ├── SmsSimulator  IvrSimulator
│   │   │   └── offlineQueue.ts
│   │   ├── components/ MapView StatusBadge ProvenanceChip
│   │   │              FieldPanel ChannelSwitch SiloInspector
│   │   ├── hooks/ useEventStream.ts useCapability.ts
│   │   └── api/client.ts
│   ├── public/ manifest.json  sw.js
│   └── package.json
├── .env.example
└── README.md
```

---

## 6. Environment & secrets

```
# --- core (only this is required for Steps 1-9) ---
DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/phc
JWT_SECRET=dev-only-change-me

# --- mode switches: all default to the safe path ---
LLM_MODE=mock                 # mock | live
MAPS_MODE=osm                 # osm  | google
COMMS_MODE=simulator          # simulator | live

# --- credentials, required only when the matching switch is flipped ---
GEMINI_API_KEY=
GOOGLE_MAPS_SERVER_KEY=       # Routes API — server-side, IP-restricted
VITE_GOOGLE_MAPS_BROWSER_KEY= # Maps JS API — referrer-restricted, client-safe
TWILIO_ACCOUNT_SID=
TWILIO_AUTH_TOKEN=
TWILIO_SMS_NUMBER=
TWILIO_WHATSAPP_NUMBER=
TWILIO_VOICE_NUMBER=
PUBLIC_WEBHOOK_BASE_URL=      # tunnel URL in dev, Cloud Run URL in prod
BHASHINI_API_KEY=             # optional
REDIS_URL=                    # only when running >1 backend instance
```

Ship `.env.example` with all three switches on their safe defaults. **The app must start, seed, and demo fully with every credential field blank.**

**Key hygiene:** the browser Maps key and the server Routes key are different keys with different restrictions. The server key must never reach the client bundle. Twilio's auth token must never leave the backend.

---

## 7. Environment sanity check

```bash
python --version                  # 3.11+
psql --version                    # reachable at DATABASE_URL
python -c "import ortools"        # if this fails → use greedy fallback from the start, do not debug
python -c "import torch"          # required for 12.2
node --version                    # 20+
```

If `ortools` fails to import, switch `routing.py` to the greedy fallback immediately and move on. This is a sanctioned shortcut, not a compromise.

For Twilio webhooks in local development you need a public URL: `cloudflared tunnel --url http://localhost:8000` (or ngrok). Set `PUBLIC_WEBHOOK_BASE_URL` to it and point the Twilio console's webhook fields at `{base}/webhooks/twilio/sms|whatsapp|voice`.

---

## 8. Architecture at a glance

```
FIELD SURFACES                    SPINE                        COMMAND SURFACE
─────────────────                 ─────                        ───────────────
Smartphone PWA  ─┐
SMS (Twilio)    ─┤                                         ┌─ Live map + status
WhatsApp photo  ─┼─→  ingestion.py  →  normalize  →  DB  ──┼─ Transfers + approve
IVR voice call  ─┤    (one pipeline,     + provenance   │  ├─ Federation + inspector
Offline queue   ─┘     five adapters)    + trust score  │  ├─ Trust review
                                                         │  ├─ What-if / Impact
                                         SSE event bus ──┴─→ live repaint
```

The single most important architectural decision in this build: **every channel normalizes into one `StockReading` and flows through one pipeline** — parse → fuzzy-map SKU → validate → score → commit → recompute status → emit event. Build that spine once and each additional channel is a thin adapter rather than a parallel system.

---

## 9. Data model

v2's DDL, extended. New tables and columns are marked.

```sql
CREATE TABLE facilities (
    id            TEXT PRIMARY KEY,          -- ABDM HFR-style id, e.g. 'HFR-MH-PHC-00231'
    name          TEXT NOT NULL,
    type          TEXT NOT NULL CHECK (type IN ('PHC','CHC','SUBCENTRE')),
    state_silo    TEXT NOT NULL,             -- FL silo key, e.g. 'MH','KL'
    district      TEXT NOT NULL,
    lat           DOUBLE PRECISION NOT NULL,
    lng           DOUBLE PRECISION NOT NULL,
    geom          GEOGRAPHY(POINT, 4326),    -- NEW: PostGIS, for radius queries
    beds_total    INT DEFAULT 0
);
CREATE INDEX ON facilities USING GIST (geom);

CREATE TABLE skus (
    code          TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    aliases       TEXT[],                    -- NEW: fuzzy-match targets for speech/OCR variants
    unit          TEXT DEFAULT 'unit',
    is_controlled BOOLEAN DEFAULT FALSE,
    cold_chain    BOOLEAN DEFAULT FALSE
);

CREATE TABLE stock_readings (
    id                SERIAL PRIMARY KEY,
    facility_id       TEXT REFERENCES facilities(id),
    sku_code          TEXT REFERENCES skus(code),
    qty_on_hand       NUMERIC NOT NULL,
    reported_at       TIMESTAMPTZ NOT NULL,
    source            TEXT CHECK (source IN ('form','voice','photo','sms','ivr','whatsapp','seed')),
    footfall_same_day INT,
    -- NEW: provenance
    reporter_ref      TEXT,                  -- hashed phone or user id, never raw
    channel_msg_id    TEXT UNIQUE,           -- Twilio MessageSid/CallSid — idempotency key
    confidence        NUMERIC,               -- 1.0 for typed, <1.0 for OCR/ASR extraction
    raw_payload       JSONB,                 -- original text/transcript for audit
    superseded_by     INT REFERENCES stock_readings(id)
);

CREATE TABLE bed_status (
    facility_id    TEXT REFERENCES facilities(id),
    beds_occupied  INT,
    recorded_at    TIMESTAMPTZ
);

CREATE TABLE staff_checkins (
    facility_id     TEXT REFERENCES facilities(id),
    staff_ref       TEXT,                    -- pseudonymous; never displayed individually
    checked_in_at   TIMESTAMPTZ
);

CREATE TABLE transfers (
    id             SERIAL PRIMARY KEY,
    from_facility  TEXT REFERENCES facilities(id),
    to_facility    TEXT REFERENCES facilities(id),
    sku_code       TEXT REFERENCES skus(code),
    qty            NUMERIC,
    route_km       NUMERIC,
    eta_hours      NUMERIC,
    route_polyline TEXT,                     -- NEW: encoded polyline from Routes API
    route_source   TEXT,                     -- NEW: 'google_routes' | 'haversine'
    status         TEXT CHECK (status IN ('proposed','approved','rejected','completed')) DEFAULT 'proposed',
    triggered_by   TEXT CHECK (triggered_by IN ('threshold','outbreak','whatif')),
    rationale      JSONB,                    -- NEW: which constraint bound, why this donor
    created_at     TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE approvals (                     -- NEW
    id            SERIAL PRIMARY KEY,
    transfer_id   INT REFERENCES transfers(id),
    actor_ref     TEXT,
    actor_role    TEXT,
    decision      TEXT CHECK (decision IN ('approved','rejected')),
    channel       TEXT CHECK (channel IN ('web','sms')),
    decided_at    TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE trust_flags (
    id            SERIAL PRIMARY KEY,
    facility_id   TEXT REFERENCES facilities(id),
    sku_code      TEXT REFERENCES skus(code),
    z_score       NUMERIC,
    rule          TEXT,                      -- NEW: 'zscore' | 'no_movement'
    reason        TEXT,
    flagged_at    TIMESTAMPTZ DEFAULT now(),
    reviewed      BOOLEAN DEFAULT FALSE
);

CREATE TABLE outbreak_events (
    id                SERIAL PRIMARY KEY,
    district          TEXT,
    disease_category  TEXT,
    radius_km         NUMERIC,
    severity          NUMERIC,
    triggered_at      TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE route_matrix_cache (            -- NEW
    origin_id      TEXT,
    dest_id        TEXT,
    distance_km    NUMERIC,
    duration_min   NUMERIC,
    polyline       TEXT,
    source         TEXT,
    computed_at    TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (origin_id, dest_id)
);

CREATE TABLE outbound_messages (             -- NEW
    id            SERIAL PRIMARY KEY,
    to_ref        TEXT,
    channel       TEXT CHECK (channel IN ('sms','whatsapp','voice')),
    body          TEXT,
    provider_sid  TEXT,
    status        TEXT,
    sent_at       TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE impact_ledger (                 -- NEW
    id                     SERIAL PRIMARY KEY,
    scenario               TEXT,             -- 'baseline' | 'with_system'
    run_at                 TIMESTAMPTZ DEFAULT now(),
    stockout_days          INT,
    facilities_affected    INT,
    transfers_executed     INT,
    total_km               NUMERIC,
    params                 JSONB
);

CREATE TABLE federation_rounds (             -- NEW
    id               SERIAL PRIMARY KEY,
    round_no         INT,
    global_val_mae   NUMERIC,
    per_silo_mae     JSONB,
    bytes_transmitted BIGINT,                -- the inspector's evidence
    tensor_shapes    JSONB,
    weights_sha256   TEXT,
    raw_rows_transmitted INT DEFAULT 0,      -- asserted zero, displayed as zero
    completed_at     TIMESTAMPTZ DEFAULT now()
);
```

**Privacy note:** `reporter_ref` and `staff_ref` store a salted hash, never a raw phone number or name. The UI displays masked forms (`+91••••1234`). This system holds **no patient-level data at all** — that is a deliberate scope decision and a strong answer to any compliance question.

### 9.1 Capture and verification tables *(v3.1 — from `data trust layer.md`)*

These exist from the first synthetic row, not as a later migration. Every verified fact carries *how* it was verified.

```sql
-- Two-sided medicine ledger. The dispatch row is written at the source
-- (warehouse / e-Aushadhi-style feed, or our own approved transfer), the
-- receipt is confirmed independently by the receiving facility. The pair is
-- the cross-check: an unconfirmed batch or a quantity mismatch surfaces by
-- itself instead of waiting for a manual audit.
CREATE TABLE medicine_movements (
    id               BIGSERIAL PRIMARY KEY,
    batch_id         TEXT NOT NULL,
    sku_code         TEXT REFERENCES skus(code),
    from_ref         TEXT NOT NULL,            -- warehouse code, or a facility id for transfers
    to_facility      TEXT REFERENCES facilities(id),
    qty_dispatched   NUMERIC NOT NULL CHECK (qty_dispatched > 0),
    dispatched_at    TIMESTAMPTZ NOT NULL,
    dispatch_source  TEXT NOT NULL,            -- 'warehouse' | 'transfer' | 'seed'
    transfer_id      INT REFERENCES transfers(id),   -- set when this came from our own plan
    qty_received     NUMERIC,
    received_at      TIMESTAMPTZ,
    received_via     TEXT,                     -- form | sms | ivr | whatsapp | photo
    received_by_ref  TEXT,                     -- hashed reporter
    status           TEXT NOT NULL,            -- in_transit | received | short | over | overdue
    expected_by      TIMESTAMPTZ NOT NULL,     -- dispatch + transit allowance; drives 'overdue'
    UNIQUE (batch_id, to_facility, sku_code)
);
CREATE INDEX ON medicine_movements (to_facility, status);
CREATE INDEX ON medicine_movements (status, expected_by);

-- Daily rotating code per facility: generated server-side each morning,
-- delivered by SMS/IVR, must appear in the ward photo. Unpredictable and
-- single-day, so yesterday's photo cannot be re-submitted.
CREATE TABLE verification_codes (
    facility_id   TEXT REFERENCES facilities(id),
    for_date      DATE NOT NULL,
    code          TEXT NOT NULL,
    issued_at     TIMESTAMPTZ DEFAULT now(),
    delivered_at  TIMESTAMPTZ,
    PRIMARY KEY (facility_id, for_date)
);

-- Bed occupancy as a verified observation, not a typed number.
CREATE TABLE bed_reports (
    id                BIGSERIAL PRIMARY KEY,
    facility_id       TEXT REFERENCES facilities(id),
    ward              TEXT DEFAULT 'general',
    beds_total        INT,
    beds_occupied     INT,
    reported_at       TIMESTAMPTZ NOT NULL,
    source            TEXT NOT NULL,           -- photo | form | ivr
    code_expected     TEXT,
    code_read         TEXT,                    -- what Gemini read out of the image
    code_ok           BOOLEAN,
    loc_method        TEXT,                    -- gps | cell_id | none | simulated
    loc_lat           DOUBLE PRECISION,
    loc_lng           DOUBLE PRECISION,
    loc_accuracy_m    NUMERIC,
    geofence_km       NUMERIC,                 -- distance from registered coordinates
    geofence_ok       BOOLEAN,
    register_admissions INT,                   -- admission/discharge register, same period
    model_confidence  NUMERIC,
    media_ref         TEXT,
    raw_payload       JSONB,
    verification      TEXT NOT NULL            -- verified | unverified | rejected
);

-- staff_checkins (Section 9) gains the capture provenance it needs.
ALTER TABLE staff_checkins
    ADD COLUMN checked_out_at TIMESTAMPTZ,
    ADD COLUMN shift          TEXT,            -- morning | evening | night
    ADD COLUMN source         TEXT,            -- ivr | ussd | sms | form
    ADD COLUMN cell_id        TEXT,            -- serving tower, when the channel supplies it
    ADD COLUMN loc_method     TEXT,            -- gps | cell_id | none | simulated
    ADD COLUMN geofence_km    NUMERIC,
    ADD COLUMN geofence_ok    BOOLEAN,
    ADD COLUMN footfall_same_period INT;       -- OPD cross-check input

-- Rolling per-facility data-confidence score, recomputed nightly and after
-- each flag. Components are stored so the UI can explain the number.
CREATE TABLE facility_trust (
    facility_id   TEXT PRIMARY KEY REFERENCES facilities(id),
    score         NUMERIC NOT NULL,            -- 0.0 .. 1.0
    band          TEXT NOT NULL,               -- good | watch | audit
    components    JSONB NOT NULL,              -- [{signal, penalty, reason}]
    computed_at   TIMESTAMPTZ DEFAULT now()
);
```

`trust_flags` (Section 9) already carries rule, z-score, and a plain-language reason; the new cross-signal rules write into the same table so the review queue stays one queue.

---

## 10. Synthetic data generation

1. **Facilities:** one real Indian state, real district coordinates from open government data (Health Dynamics of India / Rural Health Statistics tables), 80–120 facilities. Real coordinates with synthetic stock reads far more credible than fictional District A/B/C.
2. **SKU basket:** 8–12 items — ORS, zinc, paracetamol, amoxicillin, IV fluids, one TB/DOTS drug, one antimalarial, one antihypertensive. Populate `aliases` generously (e.g. `ORS` → `oral rehydration salts`, `ओआरएस`, `o r s`) — this is what makes speech and OCR matching work.
3. **Consumption series** (~2 years, daily, per facility × SKU):
   ```
   consumption(day) = base_rate
                    * (1 + 0.4 * sin(2π * day_of_year/365 - seasonal_phase))
                    * (monsoon_boost if in monsoon months else 1)
                    * (festival_dip  if in a festival week else 1)
                    + gaussian_noise
   ```
   **Vary `base_rate`, `seasonal_phase`, and `monsoon_boost` per facility and per silo.** Genuinely non-IID silos are what make the federation result mean something — if the silos are accidentally IID, the federation accuracy number will not move and the central demo beat dies.
4. **Gaming facilities:** ~8% get a frozen/implausibly flat `qty_on_hand` for a random 60–200 day window. This is the ground truth the trust layer must recover, and the denominator for its precision/recall.
5. **Historical outbreak:** one 2–3 week consumption spike concentrated in a single district, shaped like a diarrhoeal cluster.
6. **Footfall:** correlated, noisy daily OPD count per facility — the trust layer's expected-consumption input.
7. **Determinism:** seed the RNG and record the seed. The impact replay and eval harness must be reproducible.

---

## 11. API contract

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | Liveness |
| GET | `/facilities` | All facilities with computed status |
| GET | `/facilities/{id}` | Detail: stock, beds, staff, days-of-stock per SKU |
| POST | `/stock/readings` | Submit a reading (web form path) |
| POST | `/ingest/photo` | Image → structured reading(s) |
| POST | `/ingest/voice` | Audio → structured reading(s) |
| POST | `/ingest/simulate` | **NEW** — inject a reading as if from any channel (no Twilio needed) |
| POST | `/webhooks/twilio/sms` | **NEW** — inbound SMS |
| POST | `/webhooks/twilio/whatsapp` | **NEW** — inbound WhatsApp (text or media) |
| POST | `/webhooks/twilio/voice` | **NEW** — inbound call, returns TwiML |
| POST | `/webhooks/twilio/voice/handle` | **NEW** — IVR step handler |
| POST | `/webhooks/twilio/status` | **NEW** — delivery status callbacks |
| GET | `/stream` | **NEW** — SSE event stream |
| GET | `/transfers/recommendations` | Ranked proposed transfers |
| POST | `/transfers/{id}/approve` \| `/reject` | Officer decision |
| GET | `/forecast/{facility_id}/{sku_code}` | Forecasted daily burn rate |
| POST | `/federation/round` | Run one FedAvg round |
| GET | `/federation/accuracy` | Per-silo + global accuracy history |
| GET | `/federation/inspector` | **NEW** — bytes, shapes, hashes, raw-rows=0 |
| GET | `/trust/flags` · POST `/trust/evaluate` | Trust layer |
| POST | `/outbreak/simulate` | Fire a simulated event → pre-position recommendations |
| POST | `/whatif/simulate` | **NEW** — remove a district/route, re-solve, report resilience |
| GET | `/pharmacy/nearby` | **NEW** — Jan Aushadhi / pharmacy redirect for a stocked-out SKU |
| POST | `/copilot/ask` | Natural-language Q&A, read-only |
| POST | `/impact/replay` · GET `/impact/summary` | **NEW** — counterfactual stock-out-days avoided |
| GET | `/eval/report` | **NEW** — forecast MAE vs baselines; trust precision/recall |

---

## 12. Core algorithms

### 12.1 Reorder floor & days-of-stock — build first, no ML, no dependency
```
days_of_stock = qty_on_hand / max(daily_burn_rate, epsilon)
status = 'critical' if days_of_stock < 3
       else 'at_risk' if days_of_stock < 7
       else 'healthy'
# escalate one level if bed_occupancy > 90% or staff_checkin_rate < 50%
```
`daily_burn_rate` starts as a trailing 28-day average. No model exists at this point and the dashboard must already be correct.

### 12.2 Forecasting — hand-rolled FedAvg

Features per facility-day: sin/cos of day-of-year, is-monsoon, is-festival, 7-day and 28-day lagged consumption averages. Model: 2-hidden-layer MLP regressing next-7-day average daily consumption.

```python
import torch, copy

def federated_train(silo_datasets: dict, model_fn, rounds=10, local_epochs=3, lr=1e-3):
    global_model, history = model_fn(), []
    for rnd in range(rounds):
        local_states, sample_counts = [], []
        for silo_id, dataset in silo_datasets.items():
            local_model = copy.deepcopy(global_model)
            opt = torch.optim.Adam(local_model.parameters(), lr=lr)
            for _ in range(local_epochs):
                for x, y in dataset.loader():          # ONLY this silo's own data
                    opt.zero_grad()
                    torch.nn.functional.mse_loss(local_model(x), y).backward()
                    opt.step()
            local_states.append(local_model.state_dict())
            sample_counts.append(len(dataset))
            # Only state_dict() — weights — leaves this loop. Not x, not y.
            # This line is the entire "prove it isn't fake federation" answer.
        total = sum(sample_counts)
        avg = {k: sum(s[k] * (n/total) for s, n in zip(local_states, sample_counts))
               for k in local_states[0]}
        global_model.load_state_dict(avg)
        history.append({"round": rnd, "val_mae": evaluate(global_model, held_out)})
    return global_model, history
```

**The silo inspector (new in v3).** Each round writes to `federation_rounds`: total bytes transmitted (sum of tensor `nbytes`), tensor shapes, SHA-256 of the serialized state dict, and `raw_rows_transmitted = 0` — asserted in code, not just claimed in a slide. `/federation/inspector` serves it and the Federation page renders it beside the accuracy chart. This is the single most important demo asset in the build; log it from the first round.

### 12.3 Redistribution — min-cost flow with a dependency-free fallback
```python
from ortools.graph.python import min_cost_flow

def solve_redistribution_ortools(donors, recipients, distance_fn):
    smcf = min_cost_flow.SimpleMinCostFlow()
    for d in donors:
        for r in recipients:
            cost = int(distance_fn(d, r) * 100)            # OR-Tools needs integer costs
            cap = min(d.surplus, r.deficit)
            if cap > 0:
                smcf.add_arc_with_capacity_and_unit_cost(d.node_id, r.node_id, cap, cost)
    for d in donors:     smcf.set_node_supply(d.node_id,  d.surplus)
    for r in recipients: smcf.set_node_supply(r.node_id, -r.deficit)
    return extract_flows(smcf) if smcf.solve() == smcf.OPTIMAL else []

def solve_redistribution_greedy(donors, recipients, distance_fn):
    """Zero-dependency fallback — use from the start if `import ortools` fails."""
    proposals = []
    for r in sorted(recipients, key=lambda r: r.deficit, reverse=True):
        need = r.deficit
        for d in sorted(donors, key=lambda d: distance_fn(d, r)):
            if need <= 0 or d.surplus <= 0: continue
            qty = min(d.surplus, need)
            proposals.append({"from": d.id, "to": r.id, "qty": qty,
                              "distance_km": distance_fn(d, r)})
            d.surplus -= qty; need -= qty
    return proposals
```

`distance_fn` reads `route_matrix_cache` first; falls back to haversine × 1.3 on a miss.

**Hard rules, both implementations:**
- Never propose taking a donor below its own safety stock (the equity constraint).
- Never include a SKU where `is_controlled = true` — route those to a manual-only queue.
- Write the binding constraint into `transfers.rationale` so the UI can explain *why this donor*.

**Multi-hop:** when no single donor covers a deficit, chain transfers (A→B→C) and present them as one plan.

### 12.4 Trust layer — explainable, not ML
```
expected_consumption = a * footfall_same_day + b          # linear fit per facility or facility-type
z = (reported_consumption - expected_consumption) / historical_std
flag if abs(z) > 2.5                                       # rule = 'zscore'

flag if qty_on_hand unchanged for > 30 days
        AND footfall_same_day > 0 throughout               # rule = 'no_movement'
```
Store z-score, rule, and a plain-language reason ("187 days flat against above-average footfall"). Flags attach to facilities and patterns, never to individuals, and are framed strictly as "worth a second look."

### 12.5 Outbreak pre-positioning — a multiplier into 12.3, not a new subsystem
```python
OUTBREAK_COMMODITY_MAP = {
    "acute_diarrheal_disease": {"ORS": 3.0, "ZINC": 2.5, "IVFLUID": 2.0},
    "dengue_suspected":        {"IVFLUID": 2.5, "PARA500": 1.5},
    "heatstroke":              {"IVFLUID": 3.0, "ORS": 2.0},
}

def simulate_outbreak(district, disease_category, radius_km, severity):
    for f in facilities_within_radius(district, radius_km):      # PostGIS ST_DWithin
        for sku, mult in OUTBREAK_COMMODITY_MAP[disease_category].items():
            base = get_forecast(f, sku).daily_burn_rate
            set_temporary_forecast(f, sku, base * (1 + severity * (mult - 1)), ttl_days=14)
    return solve_redistribution(triggered_by="outbreak")          # same solver, no new code
```
The commodity table is clinician-reviewable and versioned. Its output still funnels through the same human-approval gate.

### 12.6 Cross-signal trust score — rules, deliberately not a model *(v3.1)*

12.4 flags one signal against itself. This scores a facility across *independent* signals, which is the part that is genuinely hard to fake: keeping attendance, footfall, bed occupancy, consumption, and receipt confirmations mutually consistent takes far more effort than inflating any one of them.

Kept rules-based on purpose — per `data trust layer.md`, a second trained model would compete with federated forecasting for build time and add an unexplainable number to a screen whose whole value is explainability.

```python
# Window: trailing 14 days, per facility. Each rule returns 0.0 (clean) to 1.0
# (flatly contradictory) plus the sentence the officer will read.
SIGNALS = (
    ("attendance_vs_footfall",  0.25),  # staff present, no patients logged for hours
    ("consumption_vs_footfall", 0.20),  # 12.4's z-score, reused
    ("beds_vs_admissions",      0.15),  # Gemini's bed count vs the admission register
    ("receipt_discipline",      0.20),  # batches overdue or short-received
    ("implausible_smoothness",  0.10),  # never a zero, suspiciously round, zero variance
    ("verification_quality",    0.10),  # rotating-code and geofence pass rate
)

score = max(0.0, 1.0 - sum(weight * rule(facility) for rule, weight in SIGNALS))
band  = "good" if score >= 0.75 else "watch" if score >= 0.5 else "audit"
```

**Two consumers, and no third:**

1. **Early warning widens for low-trust facilities — it never silently discounts them.** A facility whose numbers cannot be trusted is *more* dangerous, not less, so it is warned about sooner:
   `trigger_days = base_trigger_days * (1 + max(0, 0.75 - score))` — a `score = 0.4` facility trips at 7 → 9.5 days of stock. The widening starts exactly where the "good" band ends, so a facility shown as consistent is never quietly being warned early. Redistribution quantities are unchanged; only the alert timing moves.
2. **A District Health Officer audit queue**, ordered by `score` ascending and facilities-served descending, replacing blind trust and random sampling with a ranked list of where a physical visit is worth the trip.

**One source of truth, no exceptions.** The score is computed from the live tables at the moment it is asked for — never cached, never precomputed, never a parallel dataset. The federated trainer, the trust score, the evidence panel and the movement tab all read the same rows, so the sentence beside a score ("2 short of 3 recent consignments") is a link that opens exactly the rows it counted, because it is the same query. The single exception is the national map, which cannot re-derive six signals for thousands of facilities on every pan: it reads a materialised copy refreshed by `scripts.trust` from that same `trust.compute()` — a copy of the one calculation, never a second one.

**What it must never do:** exclude a facility from redistribution, attach to a named individual, or be described as fraud detection. Every screen says *worth a second look*, with the contributing sentences shown. A flag is an invitation to check, and the most common true explanation is a broken process, not a dishonest one.

---

## 13. The ingestion spine

Every channel produces a `RawSubmission`, and exactly one pipeline processes it:

```
RawSubmission { channel, sender_ref, external_id, text?, media?, audio?, received_at }
        │
        ├─ 1. dedupe        external_id (Twilio SID) already in stock_readings? → drop
        ├─ 2. identify      sender_ref → facility (phone registry); unknown → reply asking to register
        ├─ 3. extract       typed → parser | photo/audio → llm.py | SMS text → grammar parser
        ├─ 4. resolve SKU   fuzzy match against skus.code + skus.aliases (rapidfuzz)
        ├─ 5. validate      qty ≥ 0, plausible vs history, confidence threshold
        ├─ 6. score         trust signal attached
        ├─ 7. commit        stock_readings + provenance
        ├─ 8. recompute     days-of-stock, status, transfer candidacy
        ├─ 9. emit          SSE event → dashboard repaints
        └─ 10. confirm      reply on the same channel ("Recorded: ORS 42. Stock now 3 days.")
```

**SMS grammar** — deliberately forgiving, because this runs on a numeric keypad:
```
ORS 60 ZINC 20          → two readings
ORS60                   → tolerated
ओआरएस 60                → tolerated via aliases
HELP                    → returns the format
BEDS 12                 → bed occupancy
IN                      → staff check-in
APPROVE 143             → officer approves transfer #143 (role-checked)
```
Anything unparseable gets a helpful reply with an example, never a silent drop.

**Confirmation loop is mandatory on every channel.** A field worker who gets no acknowledgement does not trust the system and stops using it — and that failure mode, not model accuracy, is what kills real deployments.

---

## 14. Twilio integration

`services/comms.py` is the only file that imports the Twilio SDK. Everything else calls its three functions, exactly as `llm.py` does for Gemini.

```python
COMMS_MODE = os.getenv("COMMS_MODE", "simulator")

def send_sms(to_ref, body): ...          # simulator → writes to outbound_messages + SSE, no network
def send_whatsapp(to_ref, body): ...
def place_voice_callback(to_ref, msg): ...
```

In `simulator` mode, outbound messages are written to `outbound_messages` and pushed over SSE, so the Field panel renders a realistic SMS thread with **zero network calls**. Inbound is simulated through `POST /ingest/simulate`, which hits the identical pipeline. This means the entire omnichannel demo works on a plane.

### 14.1 SMS
Inbound webhook receives form-encoded `From`, `Body`, `MessageSid`. **Validate `X-Twilio-Signature` before doing anything else** (`twilio.request_validator.RequestValidator`). Respond with TwiML `<Response><Message>…</Message></Response>` for the confirmation, or `204` if replying asynchronously.

### 14.2 WhatsApp
Same Messaging API; `From` arrives as `whatsapp:+91…`. Media arrives as `NumMedia` + `MediaUrl0` + `MediaContentType0`; fetch the media with HTTP basic auth (Account SID / Auth Token) before passing bytes to Gemini.

**Constraint to design around:** outside a 24-hour user-initiated session window, outbound WhatsApp requires a pre-approved template. Critical alerts must therefore go out over **SMS**, with WhatsApp reserved for replies inside an active session. Do not discover this on stage.

### 14.3 IVR voice
Inbound call webhook returns TwiML. **Primary path:** `<Say>` a prompt, `<Record maxLength="30" playBeep="true">`, then in the handler fetch `RecordingUrl` and send the audio to Gemini for transcription *and* structured extraction in a single call — this handles Hindi/Marathi/English code-mixing far better than a transcription-then-parse split.

**Mandatory DTMF fallback:** if extraction confidence is low or the caller says nothing, fall back to keypad entry — "Press 1 for ORS, then enter the quantity, then press hash." Deterministic, immune to line noise and accent variation, and it works on the worst rural connection. A judge with domain knowledge will specifically ask what happens when speech recognition fails.

**Language prompts:** Twilio `<Say>` covers Hindi reasonably; for Marathi and others, pre-generate audio with Google Cloud TTS and serve it via `<Play>`. Cache those files — never generate TTS live during a call.

Always end a call with a spoken read-back of what was recorded, then an SMS confirmation to the same number.

---

## 15. Google Maps integration

`services/maps.py` is the only file that talks to Routes API.

- **Matrix:** `POST https://routes.googleapis.com/distanceMatrix/v2:computeRouteMatrix` with a `X-Goog-FieldMask` header. Batch within the documented per-request element limits (check current docs; they differ by routing preference) and **write every result into `route_matrix_cache`.**
- **Single route with geometry:** `v2:computeRoutes` returns `distanceMeters`, `duration`, and `polyline.encodedPolyline` — store the polyline on the transfer and render it with `google.maps.geometry.encoding.decodePath`.
- **`routingPreference: TRAFFIC_AWARE`** for ETA credibility.
- **Cost and stage discipline:** run `scripts/precompute_matrix.py` once after seeding. **Nothing calls Routes API live during a demo.** A cache miss falls back to haversine × 1.3 and marks `route_source='haversine'` so the UI is never dishonest about which number it is showing.
- **Frontend:** Maps JS API with Advanced Markers, `@googlemaps/markerclusterer` at state zoom, and the visualization library's heatmap for outbreak density. `MAPS_MODE=osm` swaps the whole component for Leaflet + OSM tiles.
- **Jan Aushadhi redirect:** prefer the official PMBJP Kendra dataset loaded into Postgres; use Places API `searchNearby` only as a supplementary generic-pharmacy lookup.

---

## 16. Realtime

SSE endpoint at `/stream`, served with `sse-starlette`. Event taxonomy:

| Event | Payload | Consumer |
|---|---|---|
| `reading.committed` | facility, sku, qty, source, confidence, latency_ms | Map badge, provenance chip |
| `status.changed` | facility, from, to | Map recolour |
| `transfer.proposed` / `.decided` | transfer id, route, actor | Transfers page |
| `federation.round` | round no, mae, bytes | Federation page |
| `trust.flagged` | facility, rule, reason | Trust review |
| `outbreak.simulated` | district, radius, affected | Map overlay |

Clients use `EventSource` (auto-reconnect) and invalidate the matching TanStack Query keys.

**Deployment caveat that will bite if ignored:** an in-process event bus breaks the moment Cloud Run scales past one instance — a reading committed on instance A never reaches a browser connected to instance B. Either set `REDIS_URL` and publish through Redis pub/sub, or pin `min-instances=1, max-instances=1` for the demo. Decide before deploying, not during.

---

## 17. LLM integration

`services/llm.py` holds the entire mock/live boundary; `ingest.py` and `copilot.py` import from it and never touch `google.genai` directly.

```python
LLM_MODE = os.getenv("LLM_MODE", "mock")

def extract_stock_from_photo(image_bytes) -> dict: ...
def extract_stock_from_audio(audio_bytes, lang_hint) -> dict: ...
def ask_copilot(question, context) -> dict: ...
```

Live calls use the `google-genai` SDK with a strict JSON contract:

> "You are reading a medicine stock register from an Indian PHC. Extract every medicine name and quantity on hand. Return ONLY valid JSON: `{"readings":[{"sku_name":str,"qty":number,"confidence":number}]}`. Omit unclear lines rather than guessing."

Confirm the current recommended Flash model id at ai.google.dev before locking it. Always run extracted `sku_name` strings through `sku_match.py` — handwriting and speech variants will not match exact strings. Anything below the confidence threshold goes to a human confirmation step rather than being committed silently.

**Bhashini (optional):** use as a pre-processing ASR step when Gemini's coverage of a specific Indian language proves weak. Registration takes calendar time — start it early if you want it.

---

## 18. Build sequence

**Superseded by Section 28 (v3.1).** The phases below record the original plan and remain accurate for everything already built; Section 28 carries the live order of work, with the capture and trust layer moved ahead of federated training.

### Week 1 — Foundation and the hero screen *(zero credentials)*

1. **Scaffold + sanity check.** Backend, frontend, Alembic, all three mode switches wired on day one, `/health` returns 200. Solver path already decided by the `import ortools` check.
2. **Schema + migrations** (Section 9). Insert and query a dummy facility; PostGIS `geom` populated.
3. **Synthetic data generator** (Section 10). 80–120 facilities on real coordinates, 2 years of seasonal readings, seeded gaming facilities, one historical outbreak cluster, recorded RNG seed.
4. **Facilities + stock endpoints + reorder floor** (12.1). `GET /facilities` returns correct status for every seeded facility. **No LLM, Twilio, or Maps import anywhere in this step.**
5. **SSE bus + `/stream`** (Section 16) with the event taxonomy stubbed.
6. **Command dashboard frontend.** Leaflet/OSM map, traffic-light colours, facility drawer, live repaint on SSE. Runs with a blank `.env`.
   > **✅ Checkpoint 1 — a complete, submittable, fully credential-free project.**

### Week 2 — Redistribution and federation *(still credential-free at the core)*

7. **Donor/recipient computation + constraints.** Safety-stock floor enforced, controlled SKUs routed to the manual queue, `rationale` populated.
8. **Solver** (12.3) with multi-hop chaining. `/transfers/recommendations` returns a solved, explained list.
9. **Approve/reject flow.** Officer decision writes `approvals`, emits `transfer.decided`, and the facility visibly flips colour on the live map.
   > **✅ Checkpoint 2 — simulate a stock-out, get a ranked transfer, approve it, watch the map change.**
10. **Maps upgrade.** `MAPS_MODE=google`: precompute the route matrix into `route_matrix_cache`, render real polylines and traffic-aware ETAs, keep the haversine fallback labelled honestly.
    > **✅ Checkpoint 3 — the same flow, now over real road routes, with the OSM fallback still passing.**
11. **Federated forecasting** (12.2). Per-silo non-IID datasets, hand-rolled FedAvg, accuracy history logged per round.
12. **Silo inspector.** Bytes, tensor shapes, weight hash, `raw_rows_transmitted = 0`, beside the before/after accuracy chart.
    > **✅ Checkpoint 4 — run a round, the accuracy number moves, and the inspector proves the claim.**

### Week 3 — Omnichannel and the field client

13. **Ingestion spine + simulators** (Section 13). `POST /ingest/simulate` drives the full pipeline for every channel with no Twilio account involved. SMS grammar parser, fuzzy SKU matcher, confirmation loop.
14. **Field PWA + capability detection.** Stock form, photo capture, voice note, offline IndexedDB queue with background sync, `useCapability` degrading on `navigator.onLine` / `effectiveType` / measured latency.
15. **Split view.** Command view ~70%, dockable Field panel ~30% with the channel switch (smartphone / weak-signal / feature-phone), provenance chip showing `source · masked sender · latency`, QR code to open the field client on a real device.
    > **✅ Checkpoint 5 — submit from the right panel, watch the left panel repaint in under two seconds.**
16. **Twilio SMS live** (14.1) — signature validation, idempotency on `MessageSid`, inbound readings, outbound critical alerts, `APPROVE <id>` for officers.
17. **Twilio WhatsApp live** (14.2) — media fetch, Gemini photo extraction, session-window handling.
18. **Twilio IVR live** (14.3) — record → Gemini extraction, DTMF fallback, cached TTS prompts, spoken read-back plus SMS confirmation.
    > **✅ Checkpoint 6 — a judge texts, WhatsApps a register photo, and calls the number from their own handset; all three land on the dashboard live.**
19. **Copilot** (Section 17) — read-only function calling; the UI shows which tool it called. It explains; it never decides.

### Week 4 — Differentiators, proof, deployment

20. **Trust layer** (12.4) + review queue with plain-language reasons.
21. **Outbreak pre-positioning** (12.5) + heatmap overlay; recommendation timestamped against the reactive threshold it beat.
22. **What-if twin** — remove a district or corridor from the graph, re-solve, report resilience.
23. **Jan Aushadhi patient redirect** — nearest Kendra carrying the molecule when a facility is out.
24. **Impact replay** (19.1) and **eval harness** (19.2) — the numbers for the pitch.
25. **Deployment** — Cloud Run + Cloud SQL + Firebase Hosting + Secret Manager; resolve the SSE multi-instance decision from Section 16.
26. **Demo hardening** — all switches to their safe paths, caches warm, full click-path rehearsed twice, and one deliberate failure drill with the network physically unplugged.

**If the runway compresses, stop after Checkpoint 6.** A live dashboard, a real redistribution engine over real roads, provable federation, and omnichannel reporting that reaches feature phones already exceeds every past winner.

---

## 19. Proof layer

### 19.1 Impact replay (counterfactual)
Replay the seeded history day by day through two worlds:
- **baseline** — no forecasting, no redistribution; stock-outs occur as they would naturally,
- **with_system** — forecast + reorder floor + redistribution + outbreak pre-positioning.

Count stock-out days (`days_of_stock = 0` while `footfall > 0`) in each, write both to `impact_ledger`, and report the difference as **stock-out days avoided**. Deterministic, reproducible from the recorded seed, and auditable.

State plainly in the pitch that this is a simulation over synthetic data. A computed, honestly-labelled number beats a confident claim, and survives a Ruthless-Critic judge that a claim does not.

### 19.2 Eval harness
| Target | Metric | Baseline to beat |
|---|---|---|
| Forecast | MAE / MAPE, per-silo and global | last-value, 28-day mean, seasonal naive |
| Federation | global MAE before vs after N rounds | single-silo local model |
| Trust layer | precision / recall / F1 against the seeded gaming set | — (report calibrated FP rate out loud) |
| Redistribution | stock-outs prevented, total km, constraint violations | violations must be **zero** |

`scripts/run_eval.py` writes a report; `/eval/report` serves it. Run it before every rehearsal — these are the numbers you quote on stage, and they must be current.

---

## 20. Security & privacy

- **No patient-level data exists in this system.** Stock, beds, and aggregate staffing only. Say this explicitly when asked about compliance.
- Phone numbers and staff identifiers are stored salted-hashed; UIs display masked forms.
- Staff attendance is reported at facility aggregate level, never as an individual performance metric. Trust flags attach to facilities and patterns, never to a named person.
- India's DPDP Act 2023 is the relevant frame, not HIPAA.
- Every inbound webhook validates its provider signature before processing.
- Server-side keys (Routes, Gemini, Twilio auth token) never reach the client bundle; only the referrer-restricted Maps browser key does.
- Federated learning is privacy-*enhancing*, not privacy-*guaranteed*. Say so, and name differential privacy and secure aggregation as the next hardening step rather than overclaiming.

---

## 21. Acceptance criteria

- Dashboard renders correctly with **every env var blank except `DATABASE_URL` and `JWT_SECRET`**.
- All three mode switches work in both positions, and both positions are exercised in testing.
- Redistribution never drops a donor below its safety stock and never includes a controlled SKU — zero violations in the eval report.
- `/federation/accuracy` shows a real, logged, improving number after 5+ rounds; `/federation/inspector` reports `raw_rows_transmitted = 0`.
- A reading submitted from any of the five channels appears on the command dashboard in **under two seconds**, with its correct provenance chip.
- The field client queues submissions while offline and syncs them on reconnect.
- Trust layer recovers a stated majority of seeded gaming facilities (pick and publish the number before the demo).
- Outbreak recommendation is timestamped earlier than the reactive threshold it is compared against, and that comparison is visible in the UI.
- Impact replay produces a computed stock-out-days-avoided figure from a recorded seed.
- The full demo runs end to end with the network disconnected.

---

## 22. Non-goals

- Real IHIP / HMIS / ABDM / eVIN API integration — architected for, honestly labelled as simulated.
- Multi-tenant state onboarding UI.
- FL robustness features (client dropout, async rounds, secure aggregation, DP) — future work if asked.
- Full essential-medicines coverage — 8–12 SKUs is the right scope.
- Native mobile apps — the PWA is the mobile surface.
- **Debugging a live external API mid-build instead of flipping to the safe mode and moving on.** This is the specific anti-pattern the v2 revision existed to prevent, and it still applies.

---

## 23. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Dashboard blank | Generator not run | Run Section 10 script; check `GET /facilities` |
| Core layer needs a key | An import boundary was violated | Grep `routers/facilities.py`, `forecast.py`, `transfers.py` for `genai`, `twilio`, `maps` |
| `import ortools` fails | Native binary issue | Switch to `solve_redistribution_greedy`; do not debug the install |
| Federation MAE flat | Silos accidentally IID, or too few rounds | Check per-facility `seasonal_phase` / `base_rate` variation |
| Twilio webhook never fires | Tunnel down, or console URL stale | Re-check `PUBLIC_WEBHOOK_BASE_URL` and the Twilio console webhook fields |
| Webhook 403 | Signature validated against the wrong URL | Validate against the exact public URL Twilio called, including scheme and query |
| Duplicate readings | Twilio retried on timeout | Enforce the `channel_msg_id` unique constraint; return 200 fast, process async |
| WhatsApp outbound rejected | Outside the 24-hour session window | Send critical alerts over SMS; WhatsApp only inside an active session |
| SSE works locally, not deployed | Multiple Cloud Run instances | Redis pub/sub, or pin to a single instance |
| Routes API 403 / billing error | Wrong key, or restriction mismatch | Server key is IP-restricted; browser key is referrer-restricted; they are not interchangeable |
| Map blank after deploy | Browser key referrer list missing the Firebase domain | Add the hosting domain to the key's allowed referrers |

---

## 24. Demo script

1. **Hook (15s).** In 2024 Tamil Nadu — the state used nationally as the benchmark for exactly this problem — had roughly ten days of TB drug stock left. The cause was not technology: the Election Commission's Model Code of Conduct froze procurement. That is the world this platform has to survive in.
2. **Beat 1 — the judge's own phone.** Hand over the number. They text `ORS 8`. The provenance chip appears on the left panel in about a second; the facility goes red. *Nobody else will do this.*
3. **Beat 2 — the system responds.** Trust layer confirms the report is plausible; the engine proposes a transfer over a real road route with a traffic-aware ETA; approve it live and watch the facility flip back to green.
4. **Beat 3 — prove the federation.** Run a round; the accuracy number moves; open the inspector: weights out, bytes counted, raw rows zero. Show the ~25-line loop.
5. **Beat 4 — the honesty layer.** A facility glowing green flips amber: 187 days of zero reported stock-outs against above-average under-5 diarrhoea footfall. That is not good news; that is a flag.
6. **Beat 5 — get ahead of it.** Fire the simulated outbreak; the pre-position lands days before the reactive threshold would have fired, shown side by side.
7. **Close.** The computed number of stock-out days avoided — and the one sentence that ties it together: *the winners built a dashboard for people who already have internet; this one reaches the facilities that don't.*

---

## 25. Parallel tracks — start these on day one

These consume calendar time rather than build time, and they block later phases if left late:

- [ ] Source and clean real facility coordinates for the chosen state (Health Dynamics of India / Rural Health Statistics).
- [ ] Obtain the official PMBJP Jan Aushadhi Kendra dataset.
- [ ] Bhashini registration, if its language coverage is wanted.
- [ ] Twilio WhatsApp sender approval (the sandbox works immediately; an approved production sender does not).
- [ ] GCP project: enable Routes API, Maps JavaScript API, Places API; create and restrict both keys; enable billing.
- [ ] Re-check the Hack2skill event page for this edition's tech and eligibility rules before locking anything.
- [ ] Confirm the working title is not already in use before final submission.

---

## 26. Data capture and verification *(v3.1 — from `data trust layer.md`)*

The layer v3 assumed existed. Three data types, each captured over whatever channel the facility actually has, each arriving with evidence of how it was verified.

### 26.1 Personnel attendance — geofence, not biometrics

Biometric hardware across ~1.6 lakh facilities is not a realistic assumption (procurement, maintenance, and a device that fails takes a facility's attendance offline entirely). Location does the same job with hardware that already exists.

| Channel | What we can actually obtain | Geofence radius |
|---|---|---|
| Smartphone PWA | Browser Geolocation (GPS), accuracy in metres | 250 m |
| IVR / SMS (Twilio) | Caller identity only — **not** the serving tower | no geofence; `loc_method = 'none'` |
| USSD or operator gateway | Serving Cell Tower ID over plain 2G | ~2 km (cell-ID is coarse) |
| Demo data | Synthetic tower ids | `loc_method = 'simulated'` |

**Be precise about this, because it is the one place the brief overreaches.** Cell Tower ID is available to the *network operator* and to a native handset app; it does **not** arrive with an inbound Twilio call or SMS, so no amount of IVR work will produce it. Obtaining it needs either a USSD gateway contracted through an operator or a native Android client — both real, both procurement, neither a hackathon afternoon. So: implement the geofence against `loc_method`, ship GPS as the working path, ship USSD/cell-ID as a documented adapter with a simulated feed, and **label every row with the method that produced it**. A simulated check is displayed as simulated. Claiming tower-verified attendance we cannot obtain would poison the one thing this layer sells, which is trustworthiness.

Cell ID to coordinates, when a gateway does supply it, resolves through the **Google Geolocation API** (`POST /geolocation/v1/geolocate` with `cellTowers[]`, returning a point and an accuracy radius) — the same platform account as Routes and Tiles, a third genuine Maps job.

**Cross-check:** attendance against OPD footfall for the same period. Staff marked present with zero patients logged for six hours is an anomaly worth a look, never proof of anything — it feeds `attendance_vs_footfall` in 12.6 and nothing else.

### 26.2 Bed occupancy — rotating code plus Gemini Vision

One photo per ward per day, with the day's server-issued code visible in frame on the ward whiteboard.

```
06:00  server generates a 4-character code per facility, delivers by SMS/IVR
       |
       staff writes it on the ward whiteboard, photographs the ward
       |
       Gemini Vision, one pass, returns strict JSON:
         { beds_total, beds_occupied, code_read, confidence, notes }
       |
       three independent checks, all recorded on the row:
         code_read == code_expected           -> the photo is from today
         geofence(photo loc, facility)        -> the photo is from here
         beds_occupied vs register_admissions -> it matches the paper trail
       |
       verification = verified | unverified | rejected  -> bed_reports
```

The rotating code is what makes re-submitting last week's photo fail: it is unpredictable and expires at midnight. Reading it back out of the image is the same Gemini call that counts the beds — one inference, two jobs, no OCR pass bolted on.

`LLM_MODE=mock` returns a deterministic, plausible extraction from a fixture so the whole pipeline — including a deliberately failing code and a deliberately out-of-geofence photo — is testable and demonstrable with no key and no network.

**Rejected does not mean deleted.** A rejected report is stored, shown to the facility as *needs re-submission*, and counted in `verification_quality`. Silently dropping data teaches staff the system is broken.

### 26.3 Medicine movement — a two-sided ledger

v3 tracked stock *levels*: a single self-reported number per facility per SKU, which is exactly the number a facility under pressure has reason to round. The fix is not to distrust the facility, it is to have a second, independent record of the same event.

**Dispatch side.** When a batch leaves a state warehouse it is logged at the warehouse — medicine, batch, quantity, destination, timestamp — independently of the receiving facility. This is how e-Aushadhi already works in production; we mirror the pattern rather than invent one. Our own approved transfers write the same row (`dispatch_source = 'transfer'`), so an approved plan becomes a tracked shipment instead of a decision that vanishes into a spreadsheet.

**Receipt side.** The facility confirms arrival and quantity over any channel it has — web form, SMS, IVR, WhatsApp photo of the challan.

The pair is self-checking, and the failures surface without anyone auditing:

| Condition | Status | What the officer sees |
|---|---|---|
| received within window, quantities match | `received` | nothing — the normal case |
| `qty_received < qty_dispatched` | `short` | the gap, in units and as a percentage |
| `qty_received > qty_dispatched` | `over` | likely a recording error at one end |
| `now() > expected_by`, no receipt | `overdue` | days outstanding, ordered oldest first |

**Movement tab** (new dashboard view, one row per movement event): batch, medicine, quantity dispatched, source to destination, dispatch time, receipt time, quantity received, discrepancy, status. Filterable by state/district/status, defaulting to everything not in the `received` state — because the whole point is that the exceptions find the officer rather than the officer finding them.

`receipt_discipline` in 12.6 is computed from this table.

### 26.4 Consumption capture

- **Connected facility:** photograph the bill or register page, **Gemini** extracts medicine, quantity and date, and the ledger is deducted. Confidence below threshold routes to human confirmation instead of writing silently.
- **Unconnected facility:** report by voice through **Bhashini / VoiceERA** (~15 Indian languages), transcript into the same SKU fuzzy-matcher and the same confirmation loop as SMS (Section 13). One pipeline, many doors — no channel gets its own private path into the ledger.

Both write `stock_readings` with `source`, `confidence`, and `raw_payload` already specified in Section 9, so provenance and audit come for free.

---

## 27. Federated forecasting, revised *(v3.1 — from `federated-health-brief.md`)*

v3's hand-rolled FedAvg (12.2) stands as the serving path. Three changes.

**1. FedProx, not plain FedAvg.** State demand is strongly non-IID — Kerala's disease burden is not Bihar's — and a silo that drifts far from the global model during local epochs drags the average around. A proximal term keeps local training anchored:

```python
loss = mse(model(x), y) + (mu / 2) * sum(
    ((p - g.detach()) ** 2).sum() for p, g in zip(model.parameters(), global_params)
)   # mu = 0.01; mu = 0 reproduces FedAvg exactly, so both are one config apart
```

Report both in the eval harness (19.2): FedProx vs FedAvg vs per-state local-only vs one centralized model. "Federated beats local-only and approaches centralized" is a measured claim on our own data, which is worth more than a cited one.

**2. Flower (`flwr`) as a real, citable path.** The brief is right that Flower is the standard tool and is deployed in real federated health work. It is also a separate process model that does not belong inside a Cloud Run request. So both, with one implementation:

- The trainer exposes Flower's client shape — `get_parameters` / `fit` / `evaluate` / `client_fn` — over the same silo partitions.
- `python -m scripts.federate` runs it in-process for the live demo and writes `federation_rounds`.
- `flwr run backend/federation` runs the identical model and partitions under Flower's simulation for the judges who ask.

Same weights, same rounds, same inspector output. The Flower claim is demonstrable rather than decorative, and the site does not grow a second runtime.

**3. Forecasts feed the layers below.** Federated output replaces the naive burn rate in days-of-stock (12.1) and in redistribution deficits (12.3), behind a config switch with the burn-rate calculation as the fallback, so a bad training run degrades to today's behaviour instead of breaking the dashboard.

**Silo inspector stays the demo centrepiece,** now with one addition: per-silo trust-weighted sample counts, so a silo whose facilities are heavily flagged (12.6) contributes proportionally less to the average. That is the "flag before you forecast" rule made mechanical, and it is a genuinely novel line in this build — bad data at a few facilities no longer gets equal voting rights in a national model.

---

## 28. Build sequence v3.1 — live order of work

Replaces Section 18. Every phase ends in something demonstrable.

### Already built and verified

| | Status |
|---|---|
| Scaffold, Postgres schema, async Alembic migrations | done |
| National synthetic seed — 3,496 facilities, 34 states, 35 days (400 for the focus state) | done |
| Status engine, days-of-stock, reorder floor (12.1) | done |
| Zoomable national map: state to district to facility tiers, viewport-fetched dots | done |
| Command dashboard, facility drawer, activity feed | done |
| Redistribution: OR-Tools min-cost flow, greedy fallback, safety floors, controlled-SKU exclusion, per-transfer *and* whole-plan rationale (12.3) | done |
| Approve / reject with row locking, stale-donor conflict handling, audit rows | done |
| Real auth: sessions, four roles, permission functions, database-backed sign-in limits | done |
| Live updates by polling a durable event log (Firebase-proxy safe) | done |
| Google Maps: Routes road distances with permanent cache, Map Tiles basemap, honest haversine fallback, rejected-key circuit breaker (Section 15) | done |
| Production posture: CSP, headers, Origin check, hidden docs, unsafe-config refusal | done |
| Deployment: Dockerfile, Cloud Run + Cloud SQL + Firebase Hosting scripts | written, not yet run against a project |

### Phase A — Capture and trust *(complete)*

A1. **Schema 9.1 + migration** — movements, verification codes, bed reports, staff-checkin provenance, facility trust. — **done**
A2. **Two-sided ledger + movement tab** (26.3). Seed dispatch/receipt history including deliberate shorts and overdues; approved transfers write dispatch rows; receipt confirmation endpoint; the tab defaults to exceptions. — **done.** Approval now *dispatches*: stock leaves the donor, and the recipient is credited only when the delivery is confirmed.
A3. **Bed capture** (26.2) — rotating code issue and delivery, Gemini Vision extraction behind `LLM_MODE`, code + geofence + register checks, verification states on the facility drawer. — **done.** Verified requires both halves (today, and here), so a channel with no location can never reach it; that limit is stated on the report rather than hidden.
A4. **Attendance capture** (26.1) — check-in/out endpoints per channel, geofence by `loc_method`, footfall cross-check, honest labelling of simulated signals. — **done**
A5. **Trust score + audit queue** (12.6) — nightly recompute (`python -m scripts.trust`), components stored, District Health Officer queue ranked by score. — **done**
A6. **Wire trust into early warning** — threshold widening, stated on the facility drawer as the reason it moved. — **done.** The widening is anchored to the "good" band so a facility shown as consistent is never quietly warned early.

> **Checkpoint A — a short-received batch, a stale bed photo, and a facility whose attendance contradicts its footfall all surface without anyone going looking.**

### Phase B — Federated forecasting *(complete)*

B1. Live silo partitions read from the platform's own database, one state per SuperNode. — **done**
B2. FedProx with trust-weighted contributions, using Flower's built-in strategy and its `num-examples` weighting — no hand-written aggregation. — **done**
B3. `federation_rounds` and the silo inspector UI. — **done.** The aggregator writes one row per round: measured bytes, every tensor's shape, a SHA-256 of the weights, the per-silo table, and a raw-row count it *asserts* before writing — `assert_weights_only()` inspects every reply and stops the round if anything but weights and scalar numbers arrives, so the zero on screen is a result rather than a constant. Because a metric record may hold only numbers, a silo identifies itself by partition index.
B4. Flower parity runner. — **superseded, and better:** the app runs on Flower's **deployment** engine, a real SuperLink and four real SuperNode processes, not a simulation.
B5. Forecast feeds days-of-stock behind `FORECAST_MODE`, burn rate as fallback. — **done**

**How the pieces connect, and why it is not staged.** The seed injects genuine damage into one silo's ledger rows — consignments left unconfirmed, quantities arriving short — rather than assigning that state a low score. The trust score is then computed from those rows; the FL client recomputes it from the same rows at the start of every round and trains on the same tables, so a silo whose paperwork degrades loses weight in the very round it degrades. Measured on the current data: Bihar at 36.1% flagged consignments scored 0.408 and its 33,782 windows counted as 13,783, against Kerala at 5.2% flagged scoring 0.908.

**Publishing, not serving.** The trained model never runs inside the web service. `publish_forecast.py` writes predictions into `forecasts` and the API reads rows, so the dashboard image carries no torch and a failed training run degrades to the burn rate instead of taking the site down.

> **Checkpoint B — met.** 9 rounds on the deployment engine: error fell from 1.009 (untrained) to a best of 0.109 against the burn rate's 0.150, a 27.7% improvement on the same held-out weeks. 5,697 parameters in 8 tensors, 178 KB per round in both directions, 1.47 MB across the whole run, and 0 facility rows — asserted every round.

### Phase C — Omnichannel (Twilio) and the field client *(current)*

C1. Ingestion spine and simulators (Section 13). — **done.** One pipeline —
dedupe, identify, extract, resolve, validate, commit, confirm — with
`POST /api/ingest/simulate` driving all of it and no Twilio account in
existence. A phone registry (`facility_contacts`) that stores a salted hash and
a masked form, never a number. SMS grammar: medicines with quantities however
they are typed, plus `BEDS`, `IN`/`OUT`, `GOT <batch> <qty>`, `APPROVE`, `HELP`.
Confirmed by SMS, a delivery settles in the very ledger the dashboard reads.
C2. SMS over Twilio — webhook, signature validation, idempotency on MessageSid. — *next, needs credentials*
C3. WhatsApp with photo extraction. C4. IVR, with Bhashini voice for consumption (26.4). — *both need credentials*
C5. Field client with an offline queue and capability detection. — **done**
C6. The 70/30 split view. — **done.** The command view and the handset sit in
one frame: the panel follows whatever facility is selected, so both halves are
always discussing the same place, and a report sent on the right reaches the
left through the same live feed every other change uses — no private channel
between the two panels.

**Three channels, one pipeline.** Smartphone posts a form; weak signal writes
to the device first and sends itself when a bar returns, so a dead connection
costs a delay and never a report; feature phone is an SMS keypad running the
exact pipeline a Twilio message will. The channel changes what a person can
send, never what the platform does with it. The offline queue survives a reload
because it lives in the device's own storage, and the panel carries a labelled
"simulate losing signal" switch so the queue can be demonstrated without
anyone unplugging the wifi.

**What C2-C4 add, and what they do not.** The channels add a webhook, a
signature check and a way to deliver the reply. They add no parsing, no
matching, no permissions and no commit path — those are C1's, already built and
tested, which is the point of a spine. Until the Twilio credentials exist,
`/api/ingest/simulate` exercises the identical code.

> **Checkpoint C — a judge texts, WhatsApps a photo, and calls from their own handset; the left panel moves.**

### Phase D — Differentiators and proof

D2. **Outbreak pre-positioning** (12.5). — **done.** Declaring an outbreak
raises expected demand for the commodities that disease consumes, inside a
radius, for a fixed window — and nothing else changes. Days of cover fall, the
map turns, and the same solver proposes the same kind of transfer, now before a
facility has reported running out. Withdrawing the declaration restores
everything.

**What the measurements actually showed**, on Nashik at 60 km and 0.8 severity:
cover at one facility fell 4.5 → 1.7 days; facilities reading at risk in the
district went 6 → 19; the worst-affected became visible as at risk 17.2 days
sooner than its old rate implied.

**And one result worth keeping because it corrects the naive expectation.** The
transfer count into the district did not rise — an outbreak across a whole
district destroys the local spare capacity a plan would have drawn on. What
changed is *where supply comes from*: transfers into Nashik sourced from
outside the district went 0 → 10, and 20 shortfalls escalated with "no facility
within 150 km has ORS to spare — escalate to the state warehouse" rather than
being quietly unmet. Pre-positioning reaches past the affected area, and says
so when it cannot.

D5. **Impact replay and eval harness** (19.1, 19.2). — **done.**
`python -m scripts.run_eval` writes a dated report and `/api/eval/report`
serves it to a Proof tab, so the screen and the spoken pitch cannot drift
apart. Measured on the current seed:

| | |
|---|---|
| Stock-out facility-days (MH, 120 days) | 5,004 across 250 facilities |
| Days the medicine existed within 150 km | **99.9%** |
| Trust layer recall | **0.947** — precision@20 and @50 both **1.00**, @100 0.99 |
| Trust layer precision across every flag | 0.246, false-positive rate 0.239 |
| Redistribution constraint violations | **0** |
| Federated model error | **0.111** against 0.150 burn rate, 0.188 last value, 0.211 same-day-last-week |

**Two numbers are reported in pairs on purpose.** Flag-level precision of 0.246
looks bad and is true; the layer is tuned to miss almost nothing and then rank,
so what an officer experiences is precision@k, which is 1.00 for the worst
fifty. Quoting only one of those would be the trick this panel exists to
refuse. Likewise the 99.9% is labelled as reachability, not prevention: it says
the medicine was already within a few hours' drive, not that the platform would
have moved it in time.

D1. Copilot (read-only). D3. What-if twin. D4. Jan Aushadhi redirect. — *remaining*

### Phase E — Deployment and hardening

E1. Run the Cloud Run and Cloud SQL scripts against the real project. E2. Live Maps and Twilio keys verified end to end. E3. Demo rehearsal with one deliberate failure drill.
