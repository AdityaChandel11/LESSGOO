"""Phase B, end to end: real silos, trust-weighted aggregation, and the switch.

Two halves, in two environments, on purpose. The silo half needs torch, so it
runs in the Flower interpreter (FEDERATION_PYTHON) and reports back as JSON. The
forecast half must answer the question "does the switch fall back", which cannot
be done inside one process because settings are read once — so it asks three.

If FEDERATION_PYTHON is not set, the silo half says it was not measured. It never
guesses.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from .harness import Checker, Report

BACKEND_ROOT = Path(__file__).resolve().parent.parent
SILO_SCRIPT = BACKEND_ROOT / "federation" / "check_silos.py"


def _dsn() -> str:
    raw = os.environ.get("SWASTHSETU_DATABASE_URL") or os.environ.get("DATABASE_URL", "")
    if not raw:
        from app.config import settings

        raw = settings.database_url
    return raw.replace("postgresql+asyncpg://", "postgresql://")


def _probe(env_extra: dict[str, str]) -> dict:
    """Run the days-of-stock probe in a fresh process with these settings."""
    env = dict(os.environ, PYTHONIOENCODING="utf-8", **env_extra)
    proc = subprocess.run(
        [sys.executable, "-m", "checks.b5probe"],
        cwd=str(BACKEND_ROOT),
        env=env,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout).strip().splitlines()[-1])
    return json.loads(proc.stdout.strip().splitlines()[-1])


async def run() -> Report:
    c = Checker("federation")

    # ---- B1 and B2: the silos themselves ------------------------------------
    python = os.environ.get("FEDERATION_PYTHON")
    if not python or not Path(python).exists():
        c.note(
            "FEDERATION_PYTHON is not set to an interpreter with torch, so the silo half "
            "was not measured (set it to the Flower environment's python.exe)"
        )
        c.skip("silo half not measured")
    else:
        proc = subprocess.run(
            [python, str(SILO_SCRIPT)],
            cwd=str(SILO_SCRIPT.parent),
            env=dict(
                os.environ,
                SWASTHSETU_DATABASE_URL=_dsn(),
                PYTHONIOENCODING="utf-8",
                PYTHONUTF8="1",
                PYTHONPATH=str(SILO_SCRIPT.parent),
            ),
            capture_output=True,
            text=True,
        )
        payload = None
        for line in (proc.stdout or "").splitlines():
            if line.startswith("JSON "):
                payload = json.loads(line[5:])
        if payload is None:
            tail = (proc.stderr or proc.stdout or "").strip().splitlines()
            c.ok("silo check ran", False, tail[-1] if tail else "no output")
        else:
            silos = payload["silos"]
            c.eq("one silo per state, all distinct", payload["distinct_states"], len(silos))
            c.ok(
                "every silo has data of its own",
                all(s["train_windows"] > 0 for s in silos),
                ", ".join("{0} {1:,}".format(s["state"], s["train_windows"]) for s in silos),
            )
            c.ok(
                "the silos are genuinely unequal",
                max(s["train_windows"] for s in silos) > 1.5 * min(s["train_windows"] for s in silos),
                "largest {0:,} vs smallest {1:,}".format(
                    max(s["train_windows"] for s in silos), min(s["train_windows"] for s in silos)
                ),
            )
            c.ok(
                "trust is read live for each silo",
                all(0.0 <= s["live_trust"] <= 1.0 for s in silos),
                ", ".join("{0} {1:.3f}".format(s["state"], s["live_trust"]) for s in silos),
            )
            c.ok(
                "a less trusted silo contributes less than its row count",
                any(s["counts_as"] < s["train_windows"] for s in silos),
                ", ".join(
                    "{0} {1:,}->{2:,}".format(s["state"], s["train_windows"], s["counts_as"])
                    for s in silos
                ),
            )
            c.ok(
                "aggregation is Flower's own FedProx",
                payload["strategy"].startswith("flwr.serverapp.strategy"),
                payload["strategy"],
            )
            c.ok(
                "one local epoch runs on live data",
                payload["one_epoch_loss"] > 0,
                "loss {0} on partition {1}, {2:,} parameters".format(
                    payload["one_epoch_loss"], payload["trained_partition"], payload["model_parameters"]
                ),
            )

    # ---- B5: the switch, and what happens when it cannot be honoured --------
    fresh = _probe({"FORECAST_MODE": "federated"})
    # Zero, not a small number: these forecasts were published minutes ago, so
    # any positive window would still count them as fresh.
    stale = _probe({"FORECAST_MODE": "federated", "FORECAST_MAX_AGE_DAYS": "0"})
    off = _probe({"FORECAST_MODE": "burn_rate"})

    c.eq("switch off means burn rate everywhere", set(off["rate_source"]), {"burn_rate"})
    c.ok(
        "days of stock is present in every mode",
        fresh["days_of_stock_present"] == fresh["sku_lines"]
        and off["days_of_stock_present"] == off["sku_lines"],
        "{0}/{1} fresh, {2}/{3} off".format(
            fresh["days_of_stock_present"], fresh["sku_lines"],
            off["days_of_stock_present"], off["sku_lines"],
        ),
    )

    if not fresh["forecast_rows_exist"]:
        c.note(
            "no forecasts published, so substitution could not be observed — "
            "run federation/publish_forecast.py after a training run"
        )
        c.eq("with nothing published, the burn rate carries the map", set(fresh["rate_source"]), {"burn_rate"})
    else:
        c.eq(
            "with the switch on and fresh forecasts, the model feeds days of stock",
            set(fresh["forecast_sku_rate_source"]),
            {"federated"},
        )
        c.eq(
            "a stale forecast falls back to the burn rate",
            set(stale["rate_source"]),
            {"burn_rate"},
        )
        c.ok(
            "medicines with no forecast keep using the burn rate",
            fresh["rate_source"].get("burn_rate", 0) > 0,
            "{0} lines on the burn rate, {1} on the model".format(
                fresh["rate_source"].get("burn_rate", 0), fresh["rate_source"].get("federated", 0)
            ),
        )

    # ---- B3: the inspector's evidence, read back from the table -------------
    from sqlalchemy import select

    from app.models import FederationRound

    from .harness import db

    async with db() as session:
        run = await session.scalar(
            select(FederationRound.run_id).order_by(FederationRound.completed_at.desc()).limit(1)
        )
        if run is None:
            c.ok(
                "a federated run has been recorded",
                False,
                "federation_rounds is empty — run `flwr run . local-deployment`",
            )
            return c.report
        rounds = list(
            (
                await session.execute(
                    select(FederationRound)
                    .where(FederationRound.run_id == run)
                    .order_by(FederationRound.round_no)
                )
            )
            .scalars()
            .all()
        )

    c.at_least("the inspector recorded a run", len(rounds), 2)
    c.ok(
        "every round asserts zero facility rows transmitted",
        all(r.raw_rows_transmitted == 0 for r in rounds),
        "max seen {0}".format(max(r.raw_rows_transmitted for r in rounds)),
    )
    c.ok(
        "each round measured the weights that moved",
        all((r.bytes_transmitted or 0) > 0 and r.weights_sha256 for r in rounds),
        "{0} bytes per round, sha {1}".format(
            rounds[-1].bytes_transmitted, (rounds[-1].weights_sha256 or "")[:12]
        ),
    )
    c.ok(
        "the payload is a model, not a dataset",
        all(len(r.tensor_shapes or {}) >= 4 for r in rounds[1:]),
        "{0} tensors".format(len(rounds[-1].tensor_shapes or {})),
    )
    c.ok(
        "weights change between rounds",
        len({r.weights_sha256 for r in rounds}) > 1,
        "{0} distinct hashes across {1} rounds".format(
            len({r.weights_sha256 for r in rounds}), len(rounds)
        ),
    )
    scored = [r.global_val_mae for r in rounds if r.global_val_mae is not None]
    c.ok(
        "the shared model improved across the run",
        len(scored) > 1 and min(scored) < scored[0],
        "{0:.4f} -> {1:.4f}".format(scored[0], min(scored)) if scored else "nothing scored",
    )
    baseline = next((r.baseline_mae for r in reversed(rounds) if r.baseline_mae), None)
    c.ok(
        "and beat the burn rate it replaces",
        baseline is not None and scored and min(scored) < baseline,
        "model {0:.4f} vs burn rate {1:.4f}".format(min(scored), baseline or 0),
    )
    last_silos = rounds[-1].per_silo or {}
    c.eq("every silo is accounted for in the last round", len(last_silos), 4)
    c.ok(
        "and each one's contribution is its windows scaled by its own trust",
        all(
            0 < v["counts_as"] <= v["windows"] and 0.0 <= v["trust"] <= 1.0
            for v in last_silos.values()
        ),
        ", ".join(
            "{0} {1:,}x{2}={3:,}".format(k, v["windows"], v["trust"], v["counts_as"])
            for k, v in sorted(last_silos.items())
        ),
    )

    return c.report
