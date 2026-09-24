# Federated forecasting

Four state silos train one demand model without any facility row leaving the
state it belongs to. This directory began as Flower's PyTorch quickstart and
keeps its shape — `client_app.py`, `server_app.py`, `task.py` — but nothing of
the original example survives: there is no CIFAR-10, no image classifier, and
no simulation.

## What it actually does

Each SuperNode reads **one state** out of the platform's own PostgreSQL
database and builds 28-day consumption windows per facility and medicine, then
predicts the next seven days' mean daily use, normalised by the window's own
28-day mean. That normalisation is deliberate: a model that predicts `1.0` is
exactly the burn-rate rule the dashboard already uses, so "better than 1.0" is
the only claim worth making.

| File | What it is |
|---|---|
| `pytorchexample/silo.py` | The state partition. Reads `stock_readings`, derives consumption, builds windows, and computes that state's live trust from the medicine ledger. Requires `SWASTHSETU_DATABASE_URL`; there is no default. |
| `pytorchexample/task.py` | `DemandLSTM` (LSTM(1→32) over the sequence plus four calendar features, 5,697 parameters), the train and test loops, and `SILOS` — the four states, one per SuperNode. |
| `pytorchexample/client_app.py` | Flower `ClientApp`. Re-reads its silo every round, trains locally, and reports `num-examples` scaled by that state's live trust. |
| `pytorchexample/server_app.py` | Flower `ServerApp`. Aggregation is Flower's own `FedProx` — no averaging is written here. |
| `publish_forecast.py` | Writes predictions into the `forecasts` table. The web service reads rows and never loads torch, so a bad run degrades to the burn rate instead of breaking the map. |
| `evaluate_model.py` | Scores a trained model against three naive rules on the held-out windows. Feeds the `federation` section of `scripts/run_eval.py`. |
| `check_silos.py` | Proves B1 and B2 for `python -m checks federation`: partitions are per-state and unequal, trust is read live, and contributions are weighted by it. |

## Why FedProx, and why trust-weighted

The silos are strongly non-IID — Kerala's consumption is not Bihar's, and the
histories differ by a factor of three in size. FedProx anchors each local
epoch to the model the server sent, so one state's unusual season cannot drag
the average during its local steps. Setting `proximal-mu = 0` turns it back
into exact FedAvg, so the two are one config value apart.

Flower weights each silo by the `num-examples` its client reports. Scaling that
count by the state's data-confidence score — computed from the same medicine
ledger rows the trust panel shows — is what makes the aggregation
trust-weighted without hand-writing any aggregation. A state whose
consignments repeatedly fail to reconcile contributes proportionally less.

## Deployment engine, not simulation

This runs a real SuperLink and four real SuperNode processes, each loading only
its own state. That is a stronger claim than a simulation, and it is also the
only option here: Flower's simulation engine needs Ray, which has no wheel for
this platform.

```bash
# one terminal: the aggregator
SWASTHSETU_DATABASE_URL=postgresql://USER:PASSWORD@localhost:5432/phc \
  flower-superlink --insecure

# four more: one per state, ports 9094-9097
SWASTHSETU_DATABASE_URL=postgresql://USER:PASSWORD@localhost:5432/phc \
  flower-supernode --insecure --superlink 127.0.0.1:9092 \
  --node-config "partition-id=0 num-partitions=4" --host 127.0.0.1 --port 9094

# then, from this directory
flwr run . local-deployment
python publish_forecast.py
```

The environment variable must be set **before** the SuperLink starts: the
SuperLink spawns the ServerApp, which inherits its environment.

flwr 1.37 serves its control API over HTTP on `127.0.0.1:8000` by default,
which is also the backend's port. Start the SuperLink with `--port 9093` to
keep them apart; `~/.flwr/config.toml` then points `local-deployment` at
`127.0.0.1:9093`. The SuperLink also spawns `flower-superexec` by name, so the
federation interpreter's `bin/` has to be on `PATH` when it starts.

## Run next round, from the Federation page

The page can continue the latest recorded run by one real round. The web
service does not train: it starts `flwr run` with the federation interpreter
and shows the phases the aggregator prints. It is off in production and
until all of these hold locally:

- `FEDERATION_PYTHON` in `.env` points at this directory's interpreter;
- the SuperLink and the four SuperNodes are running as above;
- a full training saved its weights where the button resumes from:

```bash
flwr run . local-deployment --stream \
  --run-config "model-path='$PWD/runs/latest_global.pt'"
```

Each press passes `resume-run-id`, `start-round` and that `model-path`. The
ServerApp refuses to resume unless the saved weights hash to exactly what the
run's last recorded round wrote, so a new row always continues the row above
it. `runs/` is gitignored.

`local-deployment` lives in `~/.flwr/config.toml`, not in `pyproject.toml` —
flwr 1.37 moved federation configuration out of the project file.

## Environment

This directory runs in its own Python environment, because torch belongs
nowhere near the web service. `FEDERATION_PYTHON` points the checks and the
eval harness at that interpreter; without it they report the federation half
as not measured rather than guessing.

## What is not built

`federation_rounds` exists as a table, but the per-round inspector that writes
measured bytes, tensor shapes and a weights hash to it (spec §28 B3) was built
and then removed in the 2026-09-20 rollback. `SPEC_DIGEST.md` §5 is the status
of record.
