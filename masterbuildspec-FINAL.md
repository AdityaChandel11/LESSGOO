# PHC Platform — Master Build Specification v2 (FINAL)

*This supersedes the earlier build spec. It is fully self-contained — hand this document to Opus alone; nothing else needs to be attached. It opens with a short revision pass (what changed since v1 and why) so the reasoning behind each decision travels with the spec, then gives the complete, standalone build plan.*

---

## Revision Pass — re-run through the War Room protocol with everything now known

**Phase 1 Recon, updated — the competitive bar is no longer hypothetical.** Three real past winners of this challenge are now known: a unified traffic-light dashboard, forecast-and-redistribute, and multilingual voice/photo reporting appeared in **all three**. None of them checked whether their input data was honest, and none built genuine federation. That's unchanged from the last revision — it's still the real whitespace — but it's now treated as *confirmed*, not inferred.

**Phase 5 Red Team, new finding — a predicted failure already happened.** The original Red Team flagged "single point of failure: a live external API on stage" as a risk to defuse *before* the demo. It has already surfaced *during development* — the build's visible layer went dark because of an ingestion-path dependency, exactly the failure mode predicted. That upgrades this from a pre-demo checklist item to a **structural requirement**: the visible core of the app must be provably independent of any external API key, not just cached before showtime. Three concrete fixes follow from this, detailed in the spec below:
1. A first-class `LLM_MODE=mock|live` switch, built on day one, not bolted on at the end.
2. Federated learning implemented as a small hand-rolled FedAvg loop (pure PyTorch/NumPy) instead of the Flower framework — one fewer dependency chain that can fail, for a team that has already lost time to a dependency failing.
3. A dependency-free greedy fallback for the redistribution solver, in case OR-Tools' native binary doesn't install cleanly in the build environment.

**Phase 6 Verdict, sharpened — same pick, leaner build.** The architecture is unchanged: a Command Dashboard, a forecasting core with genuine federation, a redistribution engine, a low-friction multilingual ingestion + explain-only Copilot layer, and the two differentiators past winners lack (trust layer, outbreak pre-positioning). What changes is *how much new code each one costs*: outbreak pre-positioning is no longer a separate subsystem — it's a temporary demand multiplier fed into the *existing* redistribution solver, cutting a meaningful slice of net-new code for the same demo payoff. The final ranked build order:

| Priority | Layer | Why it's ranked here |
|---|---|---|
| 1 | Command Dashboard | Every past winner led with it; zero external dependencies; the floor of a submittable project |
| 2 | Reorder floor + Redistribution engine | The actual operational payoff; OR-Tools with a dependency-free fallback |
| 3 | Forecasting (hand-rolled FedAvg) | The genuine differentiator vs. past winners; now the lowest-risk implementation of it |
| 4 | Multilingual ingestion + Copilot | Matches the single most-validated winner pattern; built mock-first so it can never take the whole demo down |
| 5 | Trust layer | Differentiator #2; cheapest of all five layers to build (one statistical query) |
| 6 | Outbreak pre-positioning | Differentiator #3; now reuses layer 2's solver instead of being new code — cut first if time runs out |

---

## Read this first (non-negotiable rules)

1. Read this entire document before writing any code.
2. Build in the exact order in **Section 10 — Build Sequence**. Each step has a checkpoint; don't start the next until the current one demoably works.
3. **Nothing in Steps 1–5 of the build sequence may import an LLM SDK or require an API key.** The dashboard, reorder floor, and redistribution engine must run and look complete with zero external credentials configured. This is not a style preference — it's the direct fix for a failure this exact project has already hit once.
4. Federated learning must keep each silo's raw data genuinely local — never pool it into one training set "for now." The hand-rolled loop in Section 9.2 makes this inspectable in ~25 lines; that inspectability is the point, keep it that way even if you refactor.
5. Redistribution and outbreak pre-positioning always end in a human-approved recommendation. Nothing auto-executes a transfer. Controlled-substance SKUs are excluded from the solver entirely.
6. The trust layer and outbreak layer use explainable, statistical logic — formulas and lookup tables, not opaque ML models.
7. If a required external credential is genuinely missing when you reach Section 11 (ingestion/Copilot), say so and ask for it — don't silently stub it forever. But note: with `LLM_MODE=mock` (Section 11.1), you can build and fully demo the ingestion UI *before* a real key ever exists.
8. Code snippets below are illustrative patterns. Confirm exact current library syntax against docs where noted — SDKs move fast — but keep the architecture and intent intact regardless of syntax drift.

---

## 1. Product brief (condensed)

Real-time visibility (stock, beds, personnel) across a network of PHCs, with a live status dashboard, demand forecasting that genuinely improves via cross-state federated learning, a human-approved redistribution engine, low-friction multilingual reporting (voice + photographed registers), an explainable trust layer that flags implausible self-reports, and an outbreak-triggered pre-positioning layer. This is a Google-affiliated hackathon (Hack2skill), so the stack leans Google-native (Gemini, OR-Tools — itself a Google project) where that's a genuine fit, not a forced one.

**Demo arc:** open on the live map → trigger a stock-out → watch the trust layer confirm the report is real → watch the redistribution engine propose and let you approve a transfer → switch to the Federation view and prove, in the code, that no raw state data left its silo → show a flagged "too-good-to-be-true" facility → fire a simulated outbreak and show a pre-position beat the reactive timeline.

---

## 2. Locked tech stack

| Layer | Choice | Why |
|---|---|---|
| Backend | Python 3.11+, FastAPI, Uvicorn | Fast to write, async-capable |
| DB | PostgreSQL + PostGIS, SQLAlchemy (async) | Geo queries for distance/radius work |
| Frontend | React + Vite, plain JS, Tailwind CSS | Fastest path to a working UI under time pressure |
| Map | Leaflet + react-leaflet, OpenStreetMap tiles | No API key required |
| Redistribution solver | OR-Tools `min_cost_flow`, **with a pure-Python greedy fallback** (Section 9.3) | Correct tool for the problem shape; fallback removes a native-install risk |
| Federated learning | **Hand-rolled FedAvg loop, PyTorch + NumPy only** — no Flower dependency required (Section 9.2) | Same real guarantee (data never leaves the loop), far fewer moving parts; Flower is an optional later upgrade, not a requirement |
| Forecasting model | Small PyTorch MLP (2 hidden layers) per silo | Continuous parameters average cleanly under FedAvg |
| Multimodal ingestion | Gemini API (`google-genai` SDK), behind an `LLM_MODE=mock\|live` switch (Section 11.1) | Sponsor-aligned; mock mode makes the rest of the app immune to its availability |
| Multilingual voice (stretch only) | Bhashini (India's free government ASR API) | Optional pre-processing step if Gemini's coverage of a specific language proves weak |
| Copilot | Gemini function-calling, read-only tools, same mock switch | Explains and queries; never decides or writes |
| Hosting (optional) | Google Cloud Run + Firebase Hosting | Only if a shareable link is needed; `localhost` is fine for the demo |

---

## 3. Repository structure

```
phc-platform/
├── backend/
│   ├── app/
│   │   ├── main.py
│   │   ├── config.py                 # env vars, incl. LLM_MODE
│   │   ├── db.py
│   │   ├── models/                   # SQLAlchemy ORM (Section 6 schema)
│   │   ├── schemas/                  # Pydantic request/response models
│   │   ├── routers/
│   │   │   ├── facilities.py, stock.py, transfers.py, forecast.py
│   │   │   ├── federation.py, trust.py, outbreak.py
│   │   │   └── ingest.py, copilot.py
│   │   └── services/
│   │       ├── reorder.py                  # 9.1
│   │       ├── federated_train.py          # 9.2 — hand-rolled FedAvg
│   │       ├── routing.py                  # 9.3 — OR-Tools + greedy fallback
│   │       ├── anomaly.py                  # 9.4
│   │       ├── outbreak_map.py             # 9.5 — reuses routing.py
│   │       └── llm.py                      # 11.1 — mock/live switch
│   ├── scripts/generate_synthetic_data.py  # Section 7
│   └── requirements.txt
├── frontend/
│   ├── src/
│   │   ├── pages/ (Dashboard, FacilityDetail, Transfers, Federation, TrustReview, Ingest, Copilot)
│   │   ├── components/ (MapView, StatusBadge, StockCard, VoiceRecorder, PhotoUploader)
│   │   └── api/client.js
│   └── package.json
├── .env.example
└── README.md
```

---

## 4. Environment & secrets

```
DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/phc
LLM_MODE=mock                         # "mock" while building; flip to "live" only to validate the real call path
GEMINI_API_KEY=                       # required only once LLM_MODE=live
BHASHINI_API_KEY=                     # optional, stretch only
GOOGLE_CLOUD_PROJECT=                 # optional, deployment only
```
Ship `.env.example` with `LLM_MODE=mock` as the checked-in default. The app must start and demo fully with every other field blank.

---

## 5. Environment sanity check (run this before anything else)

```bash
# backend
python --version         # 3.11+
psql --version           # Postgres reachable at DATABASE_URL
python -c "import ortools" || echo "OR-Tools failed — use the greedy fallback in 9.3 from the start"
python -c "import torch"  # needed for 9.2, no Flower import needed at all

# frontend
node --version
npm create vite@latest --version
```
If `ortools` fails to import, don't spend build time debugging the native install — switch `routing.py` to the greedy fallback immediately and move on. This is a deliberate, sanctioned shortcut, not a compromise.

---

## 6. Data model (PostgreSQL DDL — unchanged from v1, still correct)

```sql
CREATE TABLE facilities (
    id            TEXT PRIMARY KEY,      -- ABDM HFR-style id, e.g. 'HFR-MH-PHC-00231'
    name          TEXT NOT NULL,
    type          TEXT NOT NULL CHECK (type IN ('PHC','CHC','SUBCENTRE')),
    state_silo    TEXT NOT NULL,         -- FL silo key, e.g. 'MH', 'KL'
    district      TEXT NOT NULL,
    lat           DOUBLE PRECISION NOT NULL,
    lng           DOUBLE PRECISION NOT NULL,
    beds_total    INT DEFAULT 0
);

CREATE TABLE skus (
    code          TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    is_controlled BOOLEAN DEFAULT FALSE,
    cold_chain    BOOLEAN DEFAULT FALSE
);

CREATE TABLE stock_readings (
    id                SERIAL PRIMARY KEY,
    facility_id       TEXT REFERENCES facilities(id),
    sku_code          TEXT REFERENCES skus(code),
    qty_on_hand       NUMERIC NOT NULL,
    reported_at       TIMESTAMP NOT NULL,
    source            TEXT CHECK (source IN ('form','voice','photo','seed')),
    footfall_same_day INT
);

CREATE TABLE bed_status (
    facility_id    TEXT REFERENCES facilities(id),
    beds_occupied  INT,
    recorded_at    TIMESTAMP
);

CREATE TABLE staff_checkins (
    facility_id     TEXT REFERENCES facilities(id),
    staff_id        TEXT,
    checked_in_at   TIMESTAMP
);

CREATE TABLE transfers (
    id             SERIAL PRIMARY KEY,
    from_facility  TEXT REFERENCES facilities(id),
    to_facility    TEXT REFERENCES facilities(id),
    sku_code       TEXT REFERENCES skus(code),
    qty            NUMERIC,
    route_km       NUMERIC,
    eta_hours      NUMERIC,
    status         TEXT CHECK (status IN ('proposed','approved','rejected','completed')) DEFAULT 'proposed',
    triggered_by   TEXT CHECK (triggered_by IN ('threshold','outbreak')),
    created_at     TIMESTAMP DEFAULT now()
);

CREATE TABLE trust_flags (
    id            SERIAL PRIMARY KEY,
    facility_id   TEXT REFERENCES facilities(id),
    sku_code      TEXT REFERENCES skus(code),
    z_score       NUMERIC,
    reason        TEXT,
    flagged_at    TIMESTAMP DEFAULT now(),
    reviewed      BOOLEAN DEFAULT FALSE
);

CREATE TABLE outbreak_events (
    id                SERIAL PRIMARY KEY,
    district          TEXT,
    disease_category  TEXT,
    radius_km         NUMERIC,
    severity          NUMERIC,
    triggered_at      TIMESTAMP DEFAULT now()
);
```

---

## 7. Synthetic data generation

1. **Facilities:** one real Indian state, real district coordinates from open government data (Health Dynamics of India tables), 80–120 facilities.
2. **SKU basket:** 8–12 items — ORS, zinc, paracetamol, one antibiotic, IV fluids, one TB (DOTS) drug, one antimalarial, one antihypertensive.
3. **Consumption series** (~2 years, daily, per facility × SKU):
   ```
   consumption(day) = base_rate
                     * (1 + 0.4 * sin(2π * day_of_year/365 - seasonal_phase))
                     * (1 + monsoon_boost if in monsoon months else 1)
                     * (1 + festival_dip if in a major festival week else 1)
                     + gaussian_noise
   ```
   Vary `base_rate`, `seasonal_phase`, `monsoon_boost` per facility — genuine non-IID silos are what make the federation demo mean something.
4. **Gaming facilities:** ~8% of facilities get a frozen/implausibly flat `qty_on_hand` for a random 60–200 day window — the ground truth the trust layer must recover.
5. **Historical outbreak shape:** inject one 2–3 week consumption spike concentrated in one district, shaped like a diarrheal-disease cluster, to validate 9.5 before the live demo.
6. **Footfall:** a correlated, noisy OPD count per facility per day, used by the trust layer's expected-consumption model.

---

## 8. API contract

| Method | Path | Purpose |
|---|---|---|
| GET | `/facilities` | List all facilities with computed status |
| GET | `/facilities/{id}` | Detail: stock, beds, staff, days-of-stock per SKU |
| POST | `/stock/readings` | Submit a stock reading (form/voice/photo) |
| POST | `/ingest/photo` | Upload image → structured reading(s) |
| POST | `/ingest/voice` | Upload audio → structured reading(s) |
| GET | `/transfers/recommendations` | Ranked proposed transfers |
| POST | `/transfers/{id}/approve` \| `/reject` | Officer decision |
| GET | `/forecast/{facility_id}/{sku_code}` | Forecasted daily burn rate |
| POST | `/federation/round` | Trigger one FedAvg round |
| GET | `/federation/accuracy` | Per-silo + global accuracy history (the before/after chart) |
| GET | `/trust/flags` | Current trust flags |
| POST | `/trust/evaluate` | Run the anomaly batch job |
| POST | `/outbreak/simulate` | Fire a simulated event → triggered pre-position recommendations |
| POST | `/copilot/ask` | Natural-language Q&A, read-only |

---

## 9. Core algorithms

### 9.1 Reorder floor & days-of-stock — build first, no ML, no dependency
```
days_of_stock = qty_on_hand / max(daily_burn_rate, epsilon)
status = 'critical' if days_of_stock < 3 else 'at_risk' if days_of_stock < 7 else 'healthy'
# escalate one level if bed_occupancy > 90% or staff_checkin_rate < 50%
```
`daily_burn_rate` starts as a trailing 28-day average — no model exists yet at this point, and the dashboard must already be correct.

### 9.2 Forecasting — hand-rolled FedAvg (the primary path; Flower is optional, not required)
Features per facility-day: sin/cos of day-of-year, is-monsoon flag, is-festival flag, 7-day and 28-day lagged consumption average. Model: a 2-hidden-layer MLP regressing next-7-day average daily consumption.

```python
import torch, copy

def federated_train(silo_datasets: dict, model_fn, rounds=10, local_epochs=3, lr=1e-3):
    global_model = model_fn()
    history = []

    for rnd in range(rounds):
        local_states, sample_counts = [], []

        for silo_id, dataset in silo_datasets.items():
            local_model = copy.deepcopy(global_model)
            optimizer = torch.optim.Adam(local_model.parameters(), lr=lr)
            for _ in range(local_epochs):
                for x, y in dataset.loader():              # trains ONLY on this silo's own data
                    optimizer.zero_grad()
                    loss = torch.nn.functional.mse_loss(local_model(x), y)
                    loss.backward()
                    optimizer.step()
            local_states.append(local_model.state_dict())
            sample_counts.append(len(dataset))
            # Only local_model.state_dict() — weights — leaves this loop. Not x, not y.
            # This line is the entire "prove it's not fake federation" answer for a judge.

        total = sum(sample_counts)
        avg_state = {
            k: sum(s[k] * (n / total) for s, n in zip(local_states, sample_counts))
            for k in local_states[0]
        }
        global_model.load_state_dict(avg_state)
        history.append({"round": rnd, "val_mae": evaluate(global_model, held_out_validation_set)})

    return global_model, history
```
`/federation/round` calls one iteration of this loop; `/federation/accuracy` serves `history`. This is your single most important demo asset — build and log it early. *(Optional upgrade, only if time allows: re-implement this same loop as Flower `NumPyClient`s with a `FedAvg` strategy for the "we used a named FL framework" credential — check flower.ai for current API, it changes fast. Not required; the hand-rolled version is equally real federation and has no extra dependency.)*

### 9.3 Redistribution — min-cost flow, with a dependency-free fallback
```python
from ortools.graph.python import min_cost_flow

def solve_redistribution_ortools(donors, recipients, distance_fn):
    smcf = min_cost_flow.SimpleMinCostFlow()
    for donor in donors:
        for recipient in recipients:
            cost = int(distance_fn(donor, recipient) * 100)   # OR-Tools requires integer costs
            capacity = min(donor.surplus, recipient.deficit)
            if capacity > 0:
                smcf.add_arc_with_capacity_and_unit_cost(donor.node_id, recipient.node_id, capacity, cost)
    for donor in donors:
        smcf.set_node_supply(donor.node_id, donor.surplus)
    for recipient in recipients:
        smcf.set_node_supply(recipient.node_id, -recipient.deficit)
    return extract_flows(smcf) if smcf.solve() == smcf.OPTIMAL else []


def solve_redistribution_greedy(donors, recipients, distance_fn):
    """Zero-dependency fallback — use this from the start if `import ortools` fails (Section 5)."""
    proposals = []
    for recipient in sorted(recipients, key=lambda r: r.deficit, reverse=True):
        need = recipient.deficit
        for donor in sorted(donors, key=lambda d: distance_fn(d, recipient)):
            if need <= 0 or donor.surplus <= 0:
                continue
            qty = min(donor.surplus, need)
            proposals.append({"from": donor.id, "to": recipient.id, "qty": qty,
                               "distance_km": distance_fn(donor, recipient)})
            donor.surplus -= qty
            need -= qty
    return proposals
```
`distance_fn` = haversine × a 1.3 road-factor multiplier. **Hard rule, both implementations:** never propose taking a donor below its own safety stock; never include a SKU where `is_controlled = true` — route those to a manual-only queue.

### 9.4 Misreporting trust layer — explainable, not ML
```
expected_consumption = a * footfall_same_day + b     # simple linear fit, per facility or facility-type
z = (reported_consumption - expected_consumption) / historical_std
flag if abs(z) > 2.5

# separate check, catches a different gaming pattern:
flag "no movement" if qty_on_hand unchanged for > 30 days AND footfall_same_day > 0 throughout
```
Store the z-score and a plain-language reason ("187 days flat against above-average footfall") in `trust_flags`. Attach flags to facilities/patterns, never to a named individual.

### 9.5 Outbreak pre-positioning — a multiplier into 9.3, not a new subsystem
```python
OUTBREAK_COMMODITY_MAP = {
    "acute_diarrheal_disease": {"ORS": 3.0, "ZINC": 2.5, "IVFLUID": 2.0},
    "dengue_suspected":        {"IVFLUID": 2.5, "PARA500": 1.5},
    "heatstroke":              {"IVFLUID": 3.0, "ORS": 2.0},
}

def simulate_outbreak(district, disease_category, radius_km, severity):
    for facility in facilities_within_radius(district, radius_km):
        for sku, mult in OUTBREAK_COMMODITY_MAP[disease_category].items():
            base_rate = get_forecast(facility, sku).daily_burn_rate
            set_temporary_forecast(facility, sku, base_rate * (1 + severity * (mult - 1)), ttl_days=14)
    return solve_redistribution(triggered_by="outbreak")   # same function as 9.3 — no new solver needed
```

---

## 10. Build sequence (follow in order; each has a checkpoint)

1. **Scaffold + env sanity check** (Sections 3–5). Deliverable: `GET /health` returns 200; `ortools`/`torch` import check has already told you which solver path to use.
2. **Schema + migrations** (Section 6). Deliverable: can insert/query a dummy facility.
3. **Synthetic data generator** (Section 7). Deliverable: 80–120 seeded facilities, 2 years of seasonal `stock_readings`, seeded gaming facilities, one historical outbreak cluster.
4. **Facilities + stock endpoints + reorder floor** (9.1). Deliverable: `GET /facilities` returns correct status for every seeded facility. **No LLM import anywhere in this step.**
5. **Dashboard frontend.** Deliverable: map opens, colours match the backend, click-through works, and it works with `.env` completely blank. **→ Checkpoint 1: a submittable, fully API-key-free project.**
6. **Redistribution engine** (9.3, OR-Tools or greedy per Step 1's sanity check). Deliverable: `/transfers/recommendations` returns a solved list; approve/reject wired into the dashboard so approving visibly changes a facility's colour. **→ Checkpoint 2.**
7. **Forecasting — hand-rolled FedAvg** (9.2). Deliverable: `/federation/round` runs a real round; `/federation/accuracy` shows a real, logged before/after number. **→ Checkpoint 3.**
8. **Ingestion + Copilot, mock mode first** (Section 11). Build and fully demo the photo/voice/Copilot UI against `LLM_MODE=mock`. Only after that works end-to-end, flip to `live`, add a real `GEMINI_API_KEY`, and confirm the real call path once. Deliverable: both modes work; mock mode never touches the network. **→ Checkpoint 4.**
9. **Trust layer** (9.4). Deliverable: seeded gaming facilities get flagged with a correct z-score and reason. **→ Checkpoint 5.**
10. **Outbreak layer** (9.5). Deliverable: a simulated event's recommendation is timestamped before the reactive threshold would have fired. **→ Checkpoint 6.**
11. **Demo hardening.** Set `LLM_MODE=mock` for the actual stage demo unless you've specifically stress-tested venue wifi against the live path. Pre-compute the distance matrix. Run the full click-path twice without failure.

**If time runs out, stop after step 8.** Checkpoints 1–4 alone — a working dashboard, a real redistribution engine, real (if simple) federation, and the accessibility layer that matches every past winner — is a complete, defensible, fully-functioning submission. Steps 9–10 are upside, not floor, and step 10 specifically costs almost no new code because it reuses step 6's solver.

---

## 11. LLM integration (mock-first by design)

### 11.1 The mock/live switch — build this before either photo or voice ingestion
```python
# services/llm.py
import os

LLM_MODE = os.getenv("LLM_MODE", "mock")

MOCK_PHOTO_RESPONSE = {"readings": [{"sku_name": "ORS", "qty": 42}, {"sku_name": "Zinc", "qty": 15}]}
MOCK_VOICE_RESPONSE  = {"readings": [{"sku_name": "Paracetamol", "qty": 60}], "patients_today": 20}
MOCK_COPILOT_ANSWER  = "3 facilities in the district are below 3 days of ORS stock: ..."

def extract_stock_from_photo(image_bytes: bytes) -> dict:
    return MOCK_PHOTO_RESPONSE if LLM_MODE == "mock" else _call_gemini_photo(image_bytes)

def extract_stock_from_voice(audio_bytes: bytes) -> dict:
    return MOCK_VOICE_RESPONSE if LLM_MODE == "mock" else _call_gemini_voice(audio_bytes)

def ask_copilot(question: str) -> str:
    return MOCK_COPILOT_ANSWER if LLM_MODE == "mock" else _call_gemini_copilot(question)
```
Every route in `ingest.py` and `copilot.py` calls these three functions and nothing else — they never import `google.genai` directly. That keeps the live/mock boundary in exactly one file.

### 11.2 The real calls, isolated behind that boundary
```python
from google import genai
from google.genai import types

client = genai.Client()  # reads GEMINI_API_KEY

def _call_gemini_photo(image_bytes: bytes) -> dict:
    prompt = (
        'You are reading a medicine stock register from an Indian PHC. Extract every '
        'medicine name and quantity on hand. Return ONLY valid JSON: '
        '{"readings": [{"sku_name": str, "qty": number}]}. Omit unclear lines rather than guessing.'
    )
    response = client.models.generate_content(
        model="gemini-flash-latest",   # confirm current recommended flash model at ai.google.dev
        contents=[types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg"), prompt],
    )
    return json.loads(response.text)

def _call_gemini_voice(audio_bytes: bytes) -> dict:
    # Same pattern, mime_type="audio/wav". Ask for Hindi/Marathi/English support explicitly
    # in the prompt — Gemini transcribes AND extracts structured data in one call.
    ...

def _call_gemini_copilot(question: str) -> str:
    # Function-calling against read-only backend queries only (e.g. get_facilities_at_risk).
    # Never give this function a tool that writes to `transfers` — decisions stay in 9.3 plus a human click.
    ...
```
Map extracted `sku_name` strings to `skus.code` with a fuzzy-match step before writing to `stock_readings` — handwriting/speech variants won't match exact strings.

---

## 12. Frontend spec

| Page | Shows | Interaction |
|---|---|---|
| Dashboard | Map, all facilities coloured by status | Click → side panel |
| FacilityDetail | Stock/beds/staff/days-of-stock, history | — |
| Transfers | Proposed routes, ETA, cost | Approve / reject |
| Federation | Per-silo + global accuracy over rounds | "Run a round" button |
| TrustReview | Flagged facilities, plain-language reasons | Mark reviewed |
| Ingest | Photo upload + voice recorder | Shows extracted JSON before commit |
| Copilot | Chat panel | Shows the answer + which read-only tool it called |

Keep state management simple — `fetch` + `useState`/`useEffect` or React Query. No Redux for a hackathon-scale app.

---

## 13. Acceptance criteria

- Dashboard renders correctly with **every env var blank except `DATABASE_URL`**.
- Redistribution never drops a donor below its own safety stock; never includes a controlled SKU.
- `/federation/accuracy` shows a real, logged, improving number after 5+ rounds — not a hand-typed one.
- Ingestion and Copilot both work correctly under `LLM_MODE=mock` with zero network calls, and both work under `LLM_MODE=live` with a real key.
- Trust layer recovers a stated majority (pick a number, e.g. 80%+, before the demo) of the seeded gaming facilities.
- Outbreak recommendation is timestamped earlier than the reactive threshold date it's compared against, and that comparison is visible in the UI, not just in a log.

---

## 14. Non-goals

- Real auth/login — one hardcoded officer role is enough.
- CI/production deployment pipeline.
- Real IHIP/HMIS/ABDM API access — stay honest that these are simulated.
- Multi-tenant state onboarding UI.
- FL robustness features (client dropout, async rounds, secure aggregation, DP) — mention as future work if asked, don't build them.
- Full essential-medicines coverage — 8–12 SKUs is the right scope.
- **Debugging a live external API mid-build instead of switching to `LLM_MODE=mock` and moving on** — this is the specific anti-pattern this revision exists to prevent.

---

## 15. Troubleshooting quick reference

| Symptom | Likely cause | Fix |
|---|---|---|
| Dashboard blank / no facilities | Synthetic data generator hasn't been run | Run Section 7's script; check `GET /facilities` directly |
| "Not looking live" anywhere in ingestion/Copilot | Missing/invalid `GEMINI_API_KEY`, or `LLM_MODE` unset | Set `LLM_MODE=mock` to keep building; only needed live to validate the real path once |
| Dashboard/forecast/redistribution broken | These should **never** depend on an API key — if one does, an import boundary was violated | Check `routers/facilities.py`, `forecast.py`, `transfers.py` for a stray `google.genai` import |
| `import ortools` fails | Native binary install issue in this environment | Switch `routing.py` to `solve_redistribution_greedy` (9.3) immediately, don't debug the install |
| Federation accuracy number not moving | Silos too similar (accidentally IID data) or too few rounds | Check Section 7's per-facility `seasonal_phase`/`base_rate` variation; run more rounds |

---

## 16. Demo script

1. **Hook (15s):** the 2024 Tamil Nadu TB drug stock-out — the national benchmark state, ~10 days of stock left, caused by an election freezing procurement, not a technology gap.
2. **Beat 1:** a facility nears stock-out; trust layer confirms the report is real; redistribution proposes a transfer with route + ETA; approve it live.
3. **Beat 2:** Federation page — run a round, watch the accuracy number move, show the `federated_train` loop's code to prove raw data never left a silo.
4. **Beat 3:** a flagged facility on TrustReview with its plain-language reason.
5. **Close:** fire the simulated outbreak, show the pre-position beat the reactive timeline, end on a concrete number of stock-out-days avoided.
