# SwasthSetu — राष्ट्रीय स्वास्थ्य आपूर्ति सेतु

**A federated AI platform for medicine, bed and staffing visibility across India's PHC and CHC
network. Four state health departments train one shared demand-forecasting model without a single
facility record leaving the state it belongs to, and the platform turns those forecasts into
stock-out warnings and cross-district transfer proposals that a human must approve.**

---

## ⚠️ All data here is synthetic

Every facility, stock figure, bed count, staff check-in and consignment in this repository and in
the live demo is **generated**. Districts and coordinates are real places so the map is
geographically honest, but **no real patient or facility record exists anywhere in this system**,
and none ever has. The platform holds no patient-level data at all, by design — see
[Privacy](#privacy-and-honest-limits).

---

## Try it

**<https://swasthsetu-m4x5.onrender.com>**

| | |
|---|---|
| Email | `admin@demo.swasthsetu.in` |
| Password | `SwasthSetu-Demo-2026` |

Or press any **Continue →** button on the sign-in page to enter as a state officer, a district
logistics officer or a facility pharmacist — no password needed. Each role sees only what that
person is allowed to see.

> **The first load takes about a minute.** The demo runs on a free instance that stops when nobody
> is using it, and the container applies its database migrations before it answers. A "Waking the
> server" screen shows the progress. Every page after the first is immediate.

---

## What it does

Three steps, and the whole product is the loop between them.

**1 · A facility reports.** Stock, beds or attendance arrive through one ingestion pipeline,
whatever the channel — the web app, an SMS in a forgiving grammar (`ORS 60 ZINC 20`), or a ward
photograph. Every channel normalises into the same `StockReading` through one
dedupe → identify → extract → resolve → validate → score → commit → recompute → emit → confirm
spine, so a new channel adds an adapter and nothing else.

**2 · A shared model forecasts the run-out.** Four state silos train one LSTM on their own rows.
Only weights move. The dashboard reads pre-computed predictions from a table — the trained model
never runs inside the web service, so a bad training run degrades to the burn-rate rule instead of
taking the site down.

**3 · A transfer is proposed, and a human approves it.** An OR-Tools min-cost-flow solver matches
surplus to deficit over road distances, never proposing to take a donor below its own safety
stock. **Nothing auto-executes.** Controlled substances are excluded from the solver entirely and
routed to a manual queue.

Underneath all three sits a **trust layer**: six weighted, explainable signals — never an opaque
model — that cross-reference attendance against patient footfall, dispatches against receipts, and
bed photos against the register. A facility whose numbers disagree with each other is flagged for
audit, and its silo contributes proportionally less to the national model in the very round its
paperwork degrades.

---

## Where Google AI does real work

**Gemini vision is load-bearing, not decorative.** Remove it and bed capture stops working.

A facility photographs its ward whiteboard, which carries a **rotating four-character code** issued
that morning and expiring at midnight. Gemini reads the photograph and returns, in one pass: the
total beds, how many are occupied, and the code it can see written on the board. The platform then
checks that code against the one it issued, and the photograph's location against the facility's
registered coordinates. A report only reaches `verified` if both halves pass — an old photograph
re-uploaded fails on the code, and a photograph taken elsewhere fails on the geofence.

That is genuine multimodal reasoning turning an unstructured image into a verifiable database row,
not OCR for its own sake. It runs live in production (`LLM_MODE=live`). Vision uses
`gemini-3.1-flash-lite` and the plain-language briefings use `gemini-3.5-flash-lite` — two
different models on purpose, because the free tier counts requests per day *per model*, so a
day of ward photographs cannot exhaust the briefings or the reverse.

**Google Maps** does two separate jobs when `GOOGLE_MAPS_SERVER_KEY` is set: road distances from
the Routes API feed the redistribution solver's cost function, because straight-line distance is a
poor proxy on rural road networks; and geofence verification checks where a check-in or
photograph actually happened.

The public demo runs **without** a Maps key, on the tested `MAPS_MODE=osm` fallback: a haversine
distance multiplied by 1.3 to approximate a road route. Every screen that shows one of those
distances says "straight-line estimate" next to it and draws the route as a dashed line, because a
check that did not run must never be displayed as one that passed. That is the same rule the
geofence, the rotating code and the bill reader all follow.

---

## What works, and what does not

Measured against the track's stated use case. Nothing below is aspirational.

| Capability | Status |
|---|---|
| Real-time medicine stock visibility | **Working** — 3,510 facilities, 2.48M readings |
| Bed availability | **Working** — Gemini vision, rotating code, geofence |
| Personnel attendance | **Working** — cell-ID/GPS geofence, footfall cross-check |
| Demand forecasting | **Working** — 21,019 published forecasts |
| Early stock-out warning | **Working** — reorder floor, trust-widened thresholds |
| Cross-district redistribution | **Working** — OR-Tools, human approval, safety floors |
| Shared modelling across states | **Working** — 4 silos, 0 raw rows transmitted |
| Scale across India | **Working** — 28 states + 8 union territories, 157 districts |
| **Emergency demand surge** | **Roadmap** — outbreak pre-positioning is designed but not built |
| **Multilingual / voice** | **Partial** — Hindi labels throughout; no voice input yet |

### The federated result

Nine rounds, four silos, `FedProx(mu=0.01)` on Flower's real deployment engine — a live SuperLink
and four SuperNode processes, not a simulation.

| | |
|---|---|
| Model error (MAE) | **1.0674 → 0.1106** |
| Burn-rate rule, same held-out weeks | 0.1494 |
| Weights per round | 22,788 bytes (8 tensors, 5,697 parameters) |
| **Facility rows transmitted** | **0** |

That zero is **asserted in code, not claimed in a slide**. The aggregator inspects every reply
before aggregating and raises if anything but model weights and scalar metrics arrives, so the
round stops rather than quietly averaging a leak. See
[`backend/federation/`](backend/federation/README.md) and the Federation tab in the app, which
shows the measured bytes, every tensor shape and a SHA-256 of the weights.

---

## Architecture

```
                    ┌───────────────────────────────────────┐
  SMS / WhatsApp ──▶ │  one ingestion spine  (app/ingest.py) │
  web app ─────────▶ │  dedupe → identify → extract →        │
  ward photo ──────▶ │  resolve → validate → score → commit  │
                    └──────────────────┬────────────────────┘
                                       ▼
                          ┌────────────────────────┐
                          │   PostgreSQL 17        │
                          │   (no PostGIS —        │
                          │    haversine in SQL)   │
                          └──┬─────────────┬───────┘
         reads rows only     │             │   one state per SuperNode
                             ▼             ▼
              ┌──────────────────┐   ┌──────────────────────────┐
              │  FastAPI + React │   │  Flower SuperLink        │
              │  one origin,     │   │  + 4 SuperNodes (FedProx)│
              │  no torch        │   │  weights only ───────────┼──┐
              └──────────────────┘   └──────────────────────────┘  │
                       ▲                                           │
                       └────────  forecasts table  ◀───────────────┘
                                  (publish_forecast.py)
```

**"Publishing, not serving."** The web image carries no PyTorch. Training writes predictions into
`forecasts`; the API only ever reads rows.

| Layer | Choice |
|---|---|
| API | FastAPI, SQLAlchemy 2 async, asyncpg, Alembic |
| Database | PostgreSQL 17, no PostGIS (haversine in SQL) |
| Frontend | React 19, Vite 6, TypeScript, Tailwind v4, Leaflet directly |
| Federation | Flower 1.37 deployment engine, PyTorch `DemandLSTM` |
| Optimiser | OR-Tools `SimpleMinCostFlow`, greedy fallback |
| AI | Gemini `gemini-3.1-flash-lite` (vision) and `gemini-3.5-flash-lite` (briefings) behind `LLM_MODE` |

**Every external service has a zero-credential fallback, and both paths stay tested.**
`LLM_MODE`, `MAPS_MODE` and `COMMS_MODE` each default to a mode that needs no API key, so the
platform starts, seeds and demonstrates fully with every credential blank. A test enforces this by
booting the app in a subprocess with an empty configuration.

---

## Run it locally

Needs Python 3.13, Node 20 and PostgreSQL 17.

```bash
git clone https://github.com/AdityaChandel11/LESSGOO.git && cd LESSGOO
cp .env.example .env          # then edit DATABASE_URL to point at your Postgres
```

```bash
cd backend
python -m venv .venv && .venv/Scripts/pip install -r requirements.txt
.venv/Scripts/alembic upgrade head
.venv/Scripts/python -m scripts.seed --yes
.venv/Scripts/python -m scripts.trust
.venv/Scripts/python -m scripts.users demo
.venv/Scripts/uvicorn app.main:app --reload --port 8000
```

```bash
cd frontend && npm install && npm run dev      # http://localhost:5173
```

On Linux or macOS use `.venv/bin/` in place of `.venv/Scripts/`. The seed takes a few minutes and
prints the demo password it created.

Training the federated model is optional — the dashboard falls back to the burn rate without it.
Its four-process setup is documented in [`backend/federation/README.md`](backend/federation/README.md).

### Tests

```bash
cd backend && .venv/Scripts/python -m pytest -q     # 207 unit tests
.venv/Scripts/python -m checks                      # 137 integration assertions
```

The `checks` suite runs against a seeded database and asserts behaviour end to end — that a
provider retry cannot double-count, that a stale bed photo is rejected, that a donor is never
drained below its safety stock, and that no facility row ever crossed a silo boundary.

---

## Deploy it

One service serves the API and the built site from a single origin — which is not incidental: the
session cookie is `SameSite=Lax`, so splitting the frontend onto another host is precisely how
sign-in would break.

1. Create a **PostgreSQL** instance. Note its region.
2. Create a **Web Service** from this repository. Runtime **Docker**, same region as the database.
3. Set the environment variables below in the dashboard.
4. Deploy. The container runs `alembic upgrade head` before serving, because free tiers have no
   pre-deploy hook.
5. Seed it, through the guarded runner — it prints the target host and refuses a local one:

```bash
cd backend
.venv/Scripts/python -m scripts.remote --confirm -- -m scripts.seed --days 35 --focus-days 120 --yes
.venv/Scripts/python -m scripts.remote --confirm -- -m scripts.trust
.venv/Scripts/python -m scripts.remote --confirm -- -m scripts.users demo
```

`render.yaml` describes the service. It deliberately does **not** define a database: a managed
database is not something a config file should be able to replace on a redeploy.

### Environment

Secrets live only in `.env` (gitignored) or the host's secret settings — never in the repository,
never in frontend code. `.env.example` lists every variable you need to set; the remaining
settings are tuning constants with defaults in [`backend/app/config.py`](backend/app/config.py).

| Variable | Needed | Notes |
|---|---|---|
| `DATABASE_URL` | yes | Any Postgres URL; the app corrects the driver prefix itself |
| `JWT_SECRET` | production | 32+ random characters; production refuses to start without it |
| `PHONE_HASH_SALT` | production | What stops the registry becoming a list of phone numbers |
| `GEMINI_API_KEY` | for vision | Only with `LLM_MODE=live`; free tier is enough |
| `GOOGLE_MAPS_SERVER_KEY` | optional | Routes API road distances |
| `GOOGLE_MAPS_BROWSER_KEY` | optional | Referrer-restricted; the only key that reaches a browser |
| `TWILIO_*` | optional | Only with `COMMS_MODE=live` |

Production refuses to boot with a weak signing secret, insecure cookies, a development database
password, or demo mode left on without `ALLOW_PUBLIC_DEMO=true`.

---

## Privacy and honest limits

- **No patient-level data exists in this system at all**, and the schema has nowhere to put any.
- India's **DPDP Act 2023** is the relevant frame, not HIPAA.
- Phone numbers and staff identifiers are stored as **salted hashes only**; the interface shows
  masked forms. The raw number is never written down.
- Trust flags attach to **facilities and patterns, never to a named individual**. Staff data
  aggregates to facility level. This is a data-quality signal, not fraud detection.
- **Federated learning is privacy-enhancing, not privacy-guaranteeing.** This implementation keeps
  raw rows local and proves it, but it does not yet add differential privacy or secure
  aggregation. Both are future work, and we would rather say so than overclaim.

### Two things the demo shows that deserve a caveat

- **Lakshadweep and Dadra & Nagar Haveli and Daman & Diu have forecasts, but were never trained
  on.** The four silos are Maharashtra, Kerala, Bihar and Uttar Pradesh. The shared model is
  state-agnostic — it reads a 28-day consumption window and calendar features, not a state
  identifier — so it **generalises** to regions it has never seen. That is a real property of the
  model and arguably a strength, but it is generalisation, not training, and it is labelled as such
  wherever those two territories appear.
- **Emergency demand surge is roadmap.** Stock-out early warning works today. Weighting it by an
  active outbreak — pre-positioning supply ahead of a cluster — is designed as a temporary
  multiplier into the existing solver, but it is not built.

---

## Repository

| Path | What is in it |
|---|---|
| `backend/app/` | API, ingestion spine, trust, redistribution, vision, ledger |
| `backend/federation/` | Flower silos, FedProx, the inspector, forecast publishing |
| `backend/checks/` | Integration checks — behaviour, not unit tests |
| `backend/scripts/` | Seed, trust recompute, users, eval harness, guarded remote runner |
| `frontend/src/` | Map, dashboard, panels, federation inspector |
| `CLAUDE.md` | Working agreement, invariants and operational rules |
