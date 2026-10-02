# SwasthSetu · स्वस्थसेतु

**A federated AI platform for medicine, bed and staff visibility across India's primary health centres:** a live stock map, demand forecasting, early stock-out warnings, and redistribution that a person approves.

Four states train one medicine-demand model without sharing a single facility record. The platform turns that model's forecasts, and the outbreaks NCDC reports, into stock-out warnings and transfer proposals, and a person decides each one.

**Live demo:** https://swasthsetu-m4x5.onrender.com

**Brief description:** [docs/submission/DESCRIPTION.md](docs/submission/DESCRIPTION.md)

This is a working prototype on synthetic data. Every facility, stock figure, bed count, check-in and consignment is generated. District names and coordinates are real, so the map is honest about geography, but no real patient or facility record exists anywhere in the system, and the schema has nowhere to put patient data. The outbreak rows are the one real dataset: they are parsed from NCDC's published IDSP weekly outbreak reports.

## Tech stack

- **Backend:** Python, FastAPI, SQLAlchemy 2 (async), asyncpg, PostgreSQL 17, Alembic, pytest
- **Frontend:** React 19, TypeScript, Vite, Tailwind CSS 4, Leaflet
- **Machine learning:** PyTorch LSTM demand forecasting; federated learning with Flower (FedProx) across four state silos
- **Optimisation:** Google OR-Tools min-cost flow, with a greedy fallback
- **Google AI:** Gemini API for vision (ward boards, stock documents) and text (briefings, explanations); Google Maps Routes and Map Tiles, optional
- **Infrastructure:** Docker, Render, REST API, role-based access control, JWT sessions

## Quick facts

| | |
|---|---|
| Challenge | A federated AI platform for national-scale health resource and supply chain management across India's PHC network |
| Scale | 3,510 PHC/CHC facilities in 157 districts, across all 28 states and 8 union territories (synthetic, a sample) |
| Federated learning | Flower 1.37 deployment engine, FedProx, PyTorch DemandLSTM, four state silos: Maharashtra, Kerala, Bihar, Uttar Pradesh |
| Sent per round | 22,788 bytes (5,697 weights × 4 bytes). 0 facility rows |
| Google AI | Gemini vision (`gemini-3.1-flash-lite`), Gemini text (`gemini-3.5-flash-lite`), Google Maps Routes and Map Tiles (optional, off on the demo) |
| Optimiser | OR-Tools SimpleMinCostFlow, greedy fallback |
| Tests | 891 unit tests, 13 integration check suites |
| Hosting | One Docker service on Render |
| Languages | English, with Hindi labels on the main screens and Hindi briefings |

## What works, with proof

Each line of the challenge, what the prototype does about it today, and where to check. "Partial" lines say what is missing.

| The challenge asks for | Status | What works today | Proof |
|---|---|---|---|
| Real-time visibility: medicine stock | Working | National map → state → district → facility. A pharmacist's cards count cover down from the last count and ask for a recount when it has run out; the map shows status as of each centre's last report and says so | `tests/test_cover_as_of_now.py`, `tests/test_workspace.py` |
| Real-time visibility: beds | Partial | One figure per centre: the latest verified report, with its source and age; a report that does not match the registered capacity is rejected. No district or state view of beds yet | `tests/test_one_bed_figure.py`, `tests/test_beds.py` |
| Real-time visibility: staff attendance | Partial | Geofenced check-ins and a per-centre count on the facility panel. No district or state roll-up yet; the demo pharmacist's own record is generated and labelled synthetic | `tests/test_self_attendance.py`, `tests/test_team_card.py` |
| Demand forecasting | Working for four states and six medicines | Each medicine shows its last 28 days of use, its burn rate and the shared model's next-7-day rate, with the date the forecast was published. Elsewhere, and when a forecast is over 8 days old, the burn rate is used and the card says so | `tests/test_forecast_visible.py`, `tests/test_forecast_age.py` |
| Early stock-out warnings during health emergencies | Working | A "Next 14 days" strip names the districts and medicines projected to run short, earliest first, and opens their recommendations. An active outbreak raises expected use of the medicines its disease drives, in its district, for 14 days; warnings give the run-out date with and without the outbreak. Outbreak rows come from NCDC's IDSP weekly reports | `tests/test_next_warnings.py`, `tests/test_outbreak_surge.py`, `tests/test_outbreak_api.py`, `tests/test_idsp.py`, `tests/test_ncdc.py` |
| Automated cross-district redistribution | Working within a state | The same solver pre-positions stock for an outbreak. It may cross districts inside a state; the trips seen so far stay inside one district, and no cross-district trip has been demonstrated. Nothing moves until the donor centre accepts | `tests/test_redistribution.py`, `tests/test_replan.py`, `tests/test_oversight.py` |
| Shared predictive modelling across states | Recorded run | One Flower SuperLink and four SuperNodes; every round's bytes and weights hash are stored and replayed on the Federation tab | `backend/checks/federation.py`, `tests/test_federation_live.py`, Federation tab |

## What it does

SwasthSetu runs one loop through all of it:

1. A centre reports stock, beds or attendance by web form, a photographed bill or ward board, or the SMS / WhatsApp / IVR simulator. Only the centre itself states facts about the centre.
2. Trust rules cross-check the facility's signals against each other.
3. The demand model forecasts days of stock; an active outbreak raises expected use where it is.
4. A facility that drops under the critical line raises a warning.
5. A min-cost-flow solver proposes a donor facility.
6. The donor facility accepts or declines. The receiver confirms arrival and counts what came.

## Who it is for

- **Pharmacists and staff at a PHC or CHC**: today's to-do list, stock by photo, SMS or call, requests to and from neighbouring centres, deliveries to confirm.
- **District and state health officers**: where stock runs out next, which transfers the system recommends, which deliveries did not reconcile, which centres to visit first.
- **A national administrator**: district-level summaries for every state, outbreak warnings and the federated training record. A national account reads no individual centre outside the public demo's sandbox.

## Data sources

| Data | Source | Real or synthetic |
|---|---|---|
| Facilities, stock, beds, attendance, consignments | `backend/scripts/seed.py`, fixed random seed; the rules are on the site's "How this data is generated" page | Synthetic |
| District names and coordinates | Real places; each centre is scattered around one | Real places, synthetic positions |
| Outbreak rows | NCDC's published IDSP weekly outbreak reports, parsed by `backend/app/idsp.py` | Real, public |
| Road distances | Google Maps Routes when `MAPS_MODE=google`; straight-line estimate otherwise (the demo) | Estimate on the demo |

No figure in this README describes a real facility, and no impact number is claimed.

## Try it

Open https://swasthsetu-m4x5.onrender.com.

The first load takes about a minute (43 seconds measured after an idle spell). The demo runs on a free instance that sleeps when idle and applies database migrations before answering. A "Waking the server" screen shows progress, and every page after that is immediate.

The sign-in page has demo roles, no password needed: Platform Admin, State NHM Officer (Maharashtra), District Logistics Officer (Nashik), Pharmacist at Nashik PHC 1, and, where that account has been created, Pharmacist at Nashik PHC 13 (the neighbouring centre). Each card says what that role sees. A public demo account changes nothing outside the Nashik sandbox; the server enforces that, not the interface.

**The emergency, in one click (state officer).** Continue as Maharashtra NHM Officer and press Simulate emergency. It declares an acute diarrhoeal outbreak in Nashik, labelled a scripted scenario, and carries it through the real endpoints: expected use raised for ORS, zinc and IV fluid → early warnings → the optimiser pre-positions stock → Gemini explains the transfer → the donor centre accepts → dispatch → the receiver confirms arrival.

**Two centres, two windows.** The five steps on the landing page:

1. Open the link a minute before you start.
2. Continue as Pharmacist, Nashik PHC 1. On Medicines, open a medicine running short, press Find supply and request stock.
3. In a private window, continue as Pharmacist, Nashik PHC 13. Orders → Requests for your stock: accept and send, or decline.
4. Back as PHC 1: the medicine card shows the reply, and Orders confirms the delivery when it arrives.
5. Continue as the Maharashtra state officer: Redistribution, Movements and Data trust show the same rows.

Then open Federation to replay the recorded training run.

## What runs live, what's recorded, what's next

The deployed database is a small free instance, so the rule is: live where an action writes a handful of rows, recorded where the work is heavy, and future only for what is not core, each labelled as such. The same table is on the landing page.

| Category | What | Why |
|---|---|---|
| Live on the deployed site | Stock counts, bill and ward photos read by Gemini, requests between centres, dispatches and confirmed receipts, check-ins, days-of-stock warnings, the outbreak surge, the solver, trust scores, the daily briefing | Each action writes a handful of small rows |
| Recorded, with proof | Federated training (one Flower SuperLink and four SuperNodes on a laptop; every round's bytes and weights hash stored and shown on the Federation tab), the forecasts published from it, the synthetic seed, outbreak rows parsed from NCDC's weekly IDSP reports | Heavy compute or bulk writes, run once and kept |
| Next, said plainly | A real SMS, WhatsApp and voice carrier (DLT registration), one database per state, district warehouse stock and indents, differential privacy and secure aggregation | Not built; the roadmap |

## What is live and what is not

| Part | Status | Detail |
|---|---|---|
| Facility, stock, bed and attendance data | Synthetic | Generated by `scripts/seed`, with planted anomalies to exercise the trust rules. The site's "How this demo data is generated" page lists the rules, pinned to the generator by a test |
| Dashboard, solver, ledger, trust scoring | Live | Run against the deployed database |
| Gemini vision and text | Live on click | Needs `LLM_MODE=live`. The free tier allows 20 requests a day per model; past that the screen falls back to rule-based text and says so |
| Forecasts | Four states, six medicines | Published for Maharashtra, Kerala, Bihar and Uttar Pradesh and the six medicines the model trained on. A forecast is used for 8 days after it is published; after that, and everywhere else, days of stock come from the last 28 days of readings. Every card says which it used and when the forecast was published |
| Federated rounds on the deployed site | Recorded | The site replays stored rounds. Run next round works only locally, because it needs the Flower processes and PyTorch |
| Outbreak data | Real, and old | 95 rows from NCDC's IDSP reports for weeks 31 and 32 of 2026. NCDC publishes weeks late, so every row is past the 14-day window and the panel says so; an officer can declare an outbreak, and the site checks NCDC for a newer report when the panel is opened (at most once a day) or on request, never on a schedule |
| Outbreak pre-positioning | Live | A multiplier into the same solver. The size of the rise is the district's own 14-day rise in use where its readings show one of at least 10%, otherwise the declaring officer's expected surge, labelled an assumption |
| Gemini reading a new IDSP report | Built, not verified live | The reader and its row-by-row check against the parser are tested with a stand-in transport. It has not been run against a real NCDC PDF with a live key |
| Road distances | Estimated | Straight line × 1.3 on the demo, labelled on screen. Google Maps Routes is behind `MAPS_MODE=google` |
| SMS, WhatsApp, IVR | Simulated | A handset simulator: inside the pharmacist's workspace it sends only as that centre's own registered numbers. The Twilio adapters are implemented and signature-checked, but live sending needs an account |
| Warehouse supply | Not modelled | Most replenishment in practice is by indent to the district drug warehouse. The prototype models centre-to-centre transfers and a seeded warehouse dispatch ledger, and says so where supply is shown |
| Voice input | Not built | Hindi labels and Gemini's Hindi briefings are live. There is no speech-to-text path |
| Differential privacy, secure aggregation | Not built | See Privacy |

## Judging criteria

| Criterion | What is in the prototype | Evidence |
|---|---|---|
| AI / Technical Execution (25%) | Gemini reads a ward board into a bed report whose rotating code is checked, reads a bill, slip or register page into stock changes that depend on which document it is, and writes the reason behind each transfer and trust flag. A PyTorch LSTM trains across four state silos on Flower's deployment engine | `backend/app/vision.py`, `tests/test_explanations.py`, `tests/test_beds.py`, `tests/test_stock_photo.py`, `checks/federation.py` |
| Problem-Solution Fit (20%) | One loop covers the track: stock, beds and attendance in, forecast, outbreak-driven early warning, pre-positioning, donor acceptance, confirmed receipt | `frontend/src/liveloop.tsx`, `backend/app/outbreak.py`, `backend/app/redistribution.py`, `tests/test_outbreak_surge.py` |
| Depth & Reach (20%) | 3,510 synthetic facilities in 157 districts across 28 states and 8 union territories. Four state silos train the shared model. The officer console works on a phone | `tests/test_seed_geography.py`, `backend/app/geo.py`, Federation tab |
| Deployability (20%) | One Docker service that runs migrations at start. Every external service has a fallback that needs no credentials. Production refuses to boot with unsafe settings. Facility-level rows readable only by the state and district that hold them; public demo accounts confined to a sandbox | `render.yaml`, `checks/platform.py`, `tests/test_api_boundary.py`, `tests/test_governance_tiers.py`, `tests/test_demo_sandbox.py` |
| Impact (15%) | Officers see a stock-out days before it happens, and audit visits go where the records disagree instead of at random. No figure for lives or money saved is claimed, because synthetic data cannot support one | `backend/app/trust.py`, `tests/test_trust.py`, Data trust tab |

## Who sees what

| Role | Sees | Can do |
|---|---|---|
| Pharmacist (facility) | A mobile workspace: Medicines, Orders, Beds, Attendance, plus today's to-do list in English and Hindi (and, written by Gemini, the state's language) and an alert when an outbreak is active in the district. No national map | Submit counts and photographed documents, ask a nearby centre for stock and withdraw the request, accept or decline requests for its stock, confirm receipts |
| District officer | The centres of their own district, with its movements ledger and audit queue; district summaries everywhere else | Watch transfers; chase a centre for a report. Cannot report a count, a check-in or a bed figure on a centre's behalf |
| State officer | The centres of their own state, with its ledger, audit queue and federation view; district summaries for other states | Update recommendations, declare an outbreak, run the Nashik drill |
| National admin | State and district summaries, outbreak and early-warning figures, the ledger's totals and the full Federation inspector. No centre's own rows | Read the national picture; update a state's recommendations |

**Facility-level rows follow one rule.** A state officer reads the centres of their own state, a district officer those of their own district, a centre itself; asking for a centre outside that scope returns where the rows are held, from the server. The national role reads every centre's rows. Map pins, the facility panel, trips, ledger rows, the audit queue, named outbreak warnings, the call log and event details all follow `auth.can_read_facility_rows`, and a test classifies every read route as aggregate or row-level. Each trip, consignment and audit-queue row carries a verified or unverified status with its reason, built from the row's own records (`verification.py`): a consignment is verified only when the receiving centre's count matches the dispatch.

## Features

### Live map

Every facility is coloured by its worst medicine, with state and district roll-ups. Days of stock come from the last 28 days of readings, or from the federated forecast when one is fresh (`FORECAST_MODE`). Under 3 days is critical, and 3 to 7 is at risk. The "Live" badge says how long ago the last change was. Browser Back returns to the previous place, and a link to a state, facility or view opens exactly there.

### Next 14 days

The national and state panels open with the district × medicine pairs whose centres are projected to run out within 14 days, earliest first. Each date is a centre's last count carried forward at the shared model's forecast where one is fresh, otherwise the burn rate, and at the outbreak rate where an outbreak is active; every row says which, and links to that state's recommendations for that medicine. Counts already past their own run-out date are reported as a number: a count is overdue there. The Federation tab shows the same strip limited to dates that rest on the forecast.

### Outbreak early warning

An outbreak that is active in a district — declared by a state officer, or a recent IDSP row — raises expected daily use of the medicines its disease drives (for example ORS, zinc and IV fluid for acute diarrhoeal disease) for 14 days. Days of cover are recomputed at that rate, warnings read "runs out on this date, against that date without the outbreak", and the redistribution solver proposes pre-positioning trips, which the donor centre accepts like any other. A pharmacist in that district sees the outbreak and the centre's own cover at the outbreak rate.

The size of the rise is never invented: it is the district's own observed rise in use, or the officer's stated expectation labelled as an assumption. With neither, nothing is multiplied and the screen says so.

### Emergency drill

Simulate emergency declares a scripted acute diarrhoeal outbreak in Nashik and carries it through the real endpoints:

1. The outbreak is declared and expected use is raised for its medicines.
2. Early warnings are computed.
3. OR-Tools pre-positions stock, and Gemini explains the transfer.
4. The donor centre accepts (in the drill, you accept for it) and dispatches.
5. The receiver confirms arrival.

Stock writes stay inside the Nashik sandbox. The plan step also re-solves Maharashtra's proposals for the outbreak's medicines. `reset_nashik.py` restores the sandbox. A facility's own panel still offers the plain stock-out drill.

### Redistribution

A min-cost-flow solver (OR-Tools, with a greedy fallback) matches surplus to deficit. It never takes a donor below its own 14-day floor, and it never plans controlled substances, which go to a manual review list. Trips are grouped by vehicle run.

Nothing executes until the donor facility accepts, and acceptance re-checks the donor's stock at that moment. The Redistribution tab is oversight: officers see how many recommendations are waiting, accepted, dispatched and received, and handle exceptions. Updating the recommendations leaves pharmacists' own requests and anything a donor has already answered untouched, and subtracts stock already promised.

### Requests between centres

A pharmacist can ask a nearby centre for stock. A request nobody answers lapses on its own, the requester can withdraw it, and nobody can accept it after either. The donor's Orders tab shows peer requests and the state plan's recommendations, labelled as such.

### Two-sided medicine ledger

A dispatch is logged at the source and the receipt is confirmed at the destination, as two separate records. A short or unconfirmed delivery shows up on the Movements tab instead of waiting for an audit. A receiver's stock rises on confirmation, not when the vehicle leaves. Warehouse dispatch records are seeded in this prototype.

### Data trust

Six weighted rules check each facility's own signals against each other:

- attendance against patients seen
- stock movement against footfall
- the bed report against the admission register
- delivery confirmations
- implausibly smooth figures
- how much of what the facility reports can be verified

Scores are computed live. A centre has one trust number, out of 100, with the same band words on the pharmacist's card and the officer's panel. Each rule says how many observations it rests on ("Based on 21 shifts in the last 14 days"), and a rule with too few is listed as not scored instead of moving the score. A low score makes that facility's stock warning trip earlier and ranks a searchable audit queue. The wording is a reason to visit, never an accusation, and flags attach to facilities, never to people.

### Bed capture

A ward photo must show that day's rotating code. Gemini returns the total beds, the occupied beds and the code it reads. A report is marked verified only if the code matches and the photo's location passes the facility geofence, and it is rejected if its total does not match the centre's registered capacity. On the demo the photo is a ward whiteboard drawn in the browser, labelled as such, and Gemini reads the figures and the code written on it. Counting beds in a photograph of a real ward has not been validated.

### Photographed stock documents

Gemini reads a bill, delivery slip, issue record or stock-count page and says which it is. A delivery slip adds, an issue record subtracts and a count sets the shelf; a slip that matches a delivery already confirmed is not added twice, and a line with the wrong unit, a stale date or a negative result is held rather than applied. The photo is never stored.

### Federation tab

A header in plain words (how many training examples stayed in their states, how much each state sent), a flat diagram of the states and the Aggregator, and the model's error against the burn-rate rule on a chart scaled to the trained rounds. Each round's row shows when it finished, its error, the exact bytes sent and a weights hash; the aggregator asserts 0 facility records before each round is written. A Plain / Technical switch renames the same fields, and a closing section says what is not shown. Each state's recorded weight is shown beside the same score from today's ledger, and the panel says so when the run describes an earlier dataset. Run next round trains one real round on the four silos, locally only.

### Ingestion

The web form, SMS in a forgiving keypad grammar (`ORS 60 ZINC 20`), WhatsApp and IVR all normalise into the same stock reading. On the demo, phone channels run through the simulator.

## Google AI

| Service | What it does | Used in |
|---|---|---|
| Gemini vision (`gemini-3.1-flash-lite`) | Reads a ward photo into total beds, occupied beds and the rotating code in one pass; reads a photographed bill, slip or register page into stock lines and the document's type | Bed capture and the Medicines tab's photo path. Both depend on it |
| Gemini text (`gemini-3.5-flash-lite`) | Rewrites a facility's computed to-do list in English, Hindi and the state's language (Marathi in Maharashtra) — an answer with an extra line or a figure the list does not hold is discarded — and writes the reason behind a proposed trip or a trust flag. The new list prompt has not yet been run with a live key | Emergency drill, trip cards, Data trust, the pharmacist's Today card |
| Gemini reading the IDSP weekly report | Extracts outbreak rows from NCDC's PDF; a regex parser checks every row and only agreed rows become outbreaks | Built and tested with a stand-in transport; not yet run live |
| Google Maps Routes API, Map Tiles | Road distances for the solver, and the basemap | `MAPS_MODE=google`. The public demo uses the tested OSM fallback |

Gemini is called server-side with a key from the environment. The prompts and the only Gemini client live in `backend/app/vision.py`, and `tests/test_api_boundary.py` enforces that no other module reaches Gemini. The service runs on Render; nothing is deployed on Google Cloud.

Three rules keep generated text in check, all enforced in `vision.py` and tested in `tests/test_explanations.py`:

- An answer containing any number that is not in its inputs is discarded.
- Trust wording that reads as an accusation is discarded.
- The screen shows "⚡ Gemini" only when the model wrote the text, and "Rule-based" otherwise.

Gemini runs on click, never on page load, and identical requests are cached. Gemini never writes the database: it reads, the server checks, a person confirms, the server writes.

## Architecture

The web image carries no PyTorch. Training writes predictions into the `forecasts` table and the API only reads rows, so a bad training run falls back to the burn-rate rule instead of breaking the dashboard.

| Layer | Used |
|---|---|
| API | FastAPI, SQLAlchemy 2 (async), asyncpg, Alembic, Pydantic 2 |
| Database | PostgreSQL 17 (no PostGIS; haversine in SQL) |
| Frontend | React 19, Vite 6, TypeScript, Tailwind CSS 4, Leaflet |
| Federated learning | Flower 1.37 (SuperLink + 4 SuperNodes), FedProx, PyTorch DemandLSTM |
| Optimiser | OR-Tools SimpleMinCostFlow, greedy fallback |
| Google AI | Gemini vision and text via `LLM_MODE`; Maps Routes and Map Tiles via `MAPS_MODE` |
| Messaging | Twilio SMS / WhatsApp / voice via `COMMS_MODE` (simulator on the demo) |

`LLM_MODE`, `MAPS_MODE` and `COMMS_MODE` each default to a mode that needs no key, so the app starts, seeds and demos with every credential blank. Both paths are tested.

## Federated result

The model reads a 28-day consumption window plus calendar features (sine and cosine of day of year, monsoon, festival) and predicts the next 7 days' average daily demand as a ratio. MAE below is on that ratio, on held-out weeks, against a burn-rate rule scored on the same weeks. The aggregator uses FedProx with mu = 0.01.

**Deployed run.** Stored in the deployed database and replayed in the Federation tab. Recorded on 2026-09-20 in SPEC_DIGEST.md §5: nine rounds, MAE 1.0674 to 0.1106 against a 0.1494 burn rate (about 26% lower, derived).

Each silo's example count is scaled by its trust score before aggregation, so clean data outweighs noisy data. The trust used here is receipt discipline only: the share of a state's warehouse consignments in the last 60 days that went unconfirmed or arrived short.

| Silo | Training windows | Counted as | Trust | Consignments flagged |
|---|---|---|---|---|
| Bihar | 33,918 | 13,194 | 0.389 | 37.1% |
| Kerala | 15,624 | 13,484 | 0.863 | 7.2% |

The four silos hold 139,427 windows in total (the sum of the per-silo counts). The anomalies are planted in the synthetic data to exercise the trust layer: the seed picks the weak state at random, so this says nothing about Bihar.

**This run describes the dataset as it stood on 20 September.** The deployed data was reloaded on 21 September and the reload re-drew the weak state: today's ledger there shows Maharashtra, not Bihar, with the weak paperwork (checked 1 October). The committed `final_model.pt` is the model this run produced — its hash, `73488fa9…bfb2`, is the one recorded for the run's last round — but the per-state weights above are the run's, not today's. The Federation tab shows both and says so. The training has not been re-run on the current data.

**Reproduction.** A real run on Flower's deployment engine, with one SuperLink and four SuperNode processes, each reading only its own state:

| Round | MAE | Burn-rate rule, same held-out weeks | Bytes sent | Facility rows sent |
|---|---|---|---|---|
| 0 (untrained) | 1.0914 | 0.1108 | 22,788 | 0 |
| 1 | 0.0795 | 0.1108 | 22,788 | 0 |
| 3 | 0.0791 | 0.1108 | 22,788 | 0 |
| 5 | 0.0782 | 0.1108 | 22,788 | 0 |

Rounds 4 and 5 were started from the Run next round button. Each continued only after the saved weights hashed to what the previous row recorded. By round 5 the error is about 29% below the burn-rate rule (derived).

Every round sends 22,788 bytes because the model is 5,697 numbers at 4 bytes each. The weights hash is what changes.

No comparison against a model trained on one state alone has been run, so nothing here claims the shared model beats a single state's.

## Run it locally

Verified on Linux with Python 3.11, Node 22 and PostgreSQL 16. The project targets Python 3.13 and PostgreSQL 17. On Windows, use `.venv\Scripts\` in place of `.venv/bin/`.

```bash
git clone https://github.com/AdityaChandel11/LESSGOO.git && cd LESSGOO
cp .env.example .env    # set the password in DATABASE_URL to your local Postgres password
createdb phc            # the database the default DATABASE_URL points at
```

Backend, in one terminal:

```bash
cd backend
python -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/alembic upgrade head
.venv/bin/python -m scripts.seed --days 28 --focus-days 60 --yes   # smaller seed, about a minute
.venv/bin/python -m scripts.trust
.venv/bin/python -m scripts.users demo      # prints the demo password
.venv/bin/uvicorn app.main:app --port 8000
```

Frontend, in a second terminal, from the repository root:

```bash
cd frontend && npm ci && npm run dev        # http://localhost:5173
```

To see Gemini live, set `LLM_MODE=live` and `GEMINI_API_KEY` in `.env`, then check the key with `python -m scripts.check_gemini`. Federated training is optional, and its four-process setup is in `backend/federation/README.md`.

## Tests

```bash
cd backend
.venv/bin/python -m pytest -q     # 867 unit tests
.venv/bin/python -m checks        # 13 integration suites, against the seeded database
cd ../frontend && npx tsc -b && npm run build
```

The check suites assert behaviour end to end, including that:

- a provider retry cannot double-count
- a stale bed photo is rejected
- a donor is never drained below its floor
- a controlled substance is never offered a donor
- no facility row crosses a silo boundary

The federation suite needs `FEDERATION_PYTHON` set to the Flower environment's interpreter, and forecasts published once.

## Deploy

These steps match how the live demo was deployed. They have not been re-run from a clean account.

1. Create a PostgreSQL instance and a Docker Web Service from this repository in the same region. `render.yaml` describes the service.
2. Set the variables below in the host's dashboard. Secrets never go in the repository.
3. Deploy. The container runs `alembic upgrade head` before serving.
4. Seed through the guarded runner, which prints the target host and refuses a local one:

   ```bash
   cd backend
   .venv/bin/python -m scripts.remote --confirm -- -m scripts.seed --days 35 --focus-days 120 --yes
   .venv/bin/python -m scripts.remote --confirm -- -m scripts.trust
   .venv/bin/python -m scripts.remote --confirm -- -m scripts.users demo
   ```

| Variable | Needed | Notes |
|---|---|---|
| `DATABASE_URL` | yes | Any Postgres URL; the app corrects the driver prefix |
| `JWT_SECRET` | production | 32+ random characters; production refuses to start without it |
| `PHONE_HASH_SALT` | production | Keeps the phone registry from becoming a list of numbers |
| `GEMINI_API_KEY` | for Gemini | With `LLM_MODE=live` |
| `GOOGLE_MAPS_SERVER_KEY`, `GOOGLE_MAPS_BROWSER_KEY` | optional | With `MAPS_MODE=google` |
| `TWILIO_*` | optional | With `COMMS_MODE=live` |

The full list is in `.env.example`. Rolling the demo district forward (`scripts/roll_forward.py`) and republishing forecasts (`federation/publish_forecast.py`) are run by hand through the same guarded runner; nothing writes to the deployed database on a schedule.

### Piloting it in a state

What a state health department would need, in order:

1. **Hosting:** one container and one PostgreSQL database inside the state's own data centre or cloud account.
2. **Facility list:** the state's PHC/CHC register keyed by ABDM Health Facility Registry id, with coordinates and registered bed capacity.
3. **Stock data:** dispatch records from the state's e-Aushadhi / DVDMS system in place of the seeded warehouse ledger; facility counts through the web form or, with a carrier account and DLT registration, SMS and IVR.
4. **Roles:** accounts for state officers, district officers and facility staff (`scripts.users`).
5. **Federation:** one Flower SuperNode beside the state's database, pointed at the aggregator. Only model weights leave.

Not yet built for a pilot: one database per state, warehouse stock and indents, TLS and node authentication for Flower.

## Privacy

- No patient-level data exists in the system. India's DPDP Act 2023 is the frame.
- Phone numbers and staff identifiers are stored only as salted hashes, and the interface shows masked forms.
- Trust flags attach to facilities and patterns, never to a named person. They are a data-quality signal, not fraud detection.
- Federated learning here is privacy-enhancing, not privacy-guaranteed. Raw rows stay local and the aggregator asserts it, but there is no differential privacy or secure aggregation yet.
- The site's footer carries a privacy notice, an accessibility statement, data sources and a help page. It is a prototype, not an official government system, and says so.

## Known gaps and roadmap

Gaps in the current build:

- In this prototype the four silos are separated by state inside one PostgreSQL instance, not in four databases. Each SuperNode reads only its own state's rows.
- The separation between states is enforced in the API and in the training queries, not yet in the database itself: one database role reads every table.
- Beds and staff attendance are visible one facility at a time; there is no district or state view of either.
- The map and national totals show each centre's status as of its last report; only the pharmacist's cards count cover down to today.
- The recorded federated run was trained on the dataset before the last reload; its per-state weights no longer match today's ledger (see Federated result). The tab says so; the training has not been re-run.
- A forecast is used only for the four training states and six medicines. No held-out-state check has been run, so nothing claims the model works anywhere else.
- The SuperLink and SuperNodes run with `--insecure`. Production would need TLS and SuperNode authentication.
- Gemini's read of a real IDSP report has not been run with a live key.
- No cross-district trip has been demonstrated, although the solver allows one within a state.

Planned:

- District and state views of beds and staff
- Per-state database roles or row-level security, so the database itself refuses a cross-state read
- Speech-to-text for Hindi voice input
- Differential privacy and secure aggregation
- Warehouse stock and indents

## Repository layout

| Path | Contents |
|---|---|
| `backend/app/` | API, ingestion pipeline, trust rules, redistribution solver, outbreak surge, ledger, Gemini adapter (`vision.py`) |
| `backend/federation/` | Flower ServerApp and ClientApp, the silos, the inspector, forecast publishing |
| `backend/tests/` | Unit tests (pytest) |
| `backend/checks/` | Integration checks against a seeded database |
| `backend/scripts/` | Seed, trust, demo users, key checks, the guarded remote runner, hand-run Render operations |
| `backend/alembic/` | Database migrations |
| `frontend/src/` | Map, dashboard panels, emergency drill, federation inspector, the pharmacist's workspace |
| `docs/submission/` | The 2–3 line description |
| `render.yaml`, `Dockerfile` | The deployed service |
