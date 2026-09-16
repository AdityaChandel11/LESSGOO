## 1. Problem statement, broken down

Hackathon ask: a federated AI platform for national-scale health resource and supply-chain management across India's PHC network — real-time visibility into medicine stock, bed availability, and staff attendance; demand forecasting; early stockout warnings; automated cross-district redistribution recommendations; shared predictive modelling across states.

Core components:
1. **Real-time data layer** — ingest stock/bed/attendance data from PHCs with inconsistent formats and poor connectivity.
2. **Demand forecasting** — predict per-facility medicine/resource consumption (seasonality, population, history).
3. **Early warning system** — flag predicted stockouts with enough lead time to act.
4. **Redistribution optimizer** — given a surplus/deficit map, recommend transfers between districts (an optimization problem, not a prediction problem).
5. **Federated learning layer** — shared model training across states without centralizing raw data.
6. **Aggregation/orchestration** — national coordinator combining state-level model updates.
7. **Security/governance** — auth, encryption, ABDM/FHIR alignment (lower priority for hackathon scope).
8. **Dashboard** — what health officials actually see and act on.

## 2. Why federated learning, and target architecture

Why FL fits:
- States/health departments generally can't or shouldn't centralize raw patient/facility data.
- Many PHCs have weak connectivity — sending small model updates is far lighter than streaming raw records.
- Regional demand patterns are non-IID (Kerala's burden ≠ Bihar's) — a shared model with local personalization beats one blindly-centralized model.

Architecture:
- **Clients** = states (or districts within a state), each holding local PHC time-series data.
- **Server** = national aggregator.
- Standard **FedAvg** loop: server broadcasts the global model → each client trains a few local epochs → clients return only weight updates (never raw data) → server aggregates (weighted by local data volume) → repeat over rounds.
- For non-IID regional variation, prefer **FedProx** over vanilla FedAvg, or a **shared backbone + per-state fine-tuned head**.

## 3. Tooling: Flower (`flwr`)

- Repo: `github.com/adap/flower` — actively maintained, used in real federated health deployments (NHS among others), safe to cite as prior art in a pitch.
- Install: `pip install flwr`
- Scaffold: `flwr new my-fl-app --framework PyTorch` (also has TensorFlow, sklearn, JAX, HuggingFace, XGBoost templates)
- Run: `flwr run .` — runs a full federated **simulation** locally (all "clients" as separate data partitions/processes in one script) — no real servers or network setup needed.
- This simulation mode is what makes FL tractable in a hackathon: "5 states" = 5 data partitions run locally, not 5 deployed machines.

## 4. Suggested build order

1. Generate synthetic multi-state PHC data (stock, beds, attendance) with realistic seasonal and regional variation.
2. Build the real-time visibility dashboard on top of that synthetic data.
3. Stand up a Flower simulation: 3–5 simulated state clients, one shared forecasting model (a simple LSTM or gradient-boosted model is enough).
4. Add stockout early-warning as a threshold check: forecast vs. safety stock level.
5. Add redistribution as a separate (non-federated) optimization step over the surplus/deficit vector — `scipy.optimize.linprog` or PuLP solving a transportation problem.
6. Wire data → forecasting → warnings → redistribution → dashboard into one coherent pipeline.

## 5. Why this scope is feasible in a hackathon timebox

- Federated learning here should be **simulated, not deployed** — Flower's simulation mode makes this a legitimate, standard way to demonstrate FL, not a shortcut. Nobody expects real cross-state server infrastructure in a hackathon window.
- Don't spend time on: real auth/encryption infra, actual network deployment across machines, ABDM/FHIR integration, production-grade data pipelines. These are correctly out of scope for the time-box.
- Realistic target: 3–5 simulated clients, a handful of FedAvg rounds (a visibly decreasing loss across rounds is enough — full convergence isn't necessary), one modest model (LSTM or gradient boosting, not a large architecture).
- The redistribution optimizer and dashboard are **not** federated — they run centrally on the forecast outputs and are comparatively easy engineering. Most of the effort budget should go to making the FL loop and forecasting genuinely work, since that's the actual hard/novel part of the ask.
