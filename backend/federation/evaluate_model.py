"""Score the federated model against the rules it is meant to replace.

Runs in the Flower environment, not the web service's: it is the only part of
the evaluation that needs torch, which is exactly why it lives here and writes
its answer to a file. `app/evaluation.py` merges that file. Nothing in the API
imports torch, and nothing here imports the API.

    SWASTHSETU_DATABASE_URL=postgresql://... python evaluate_model.py

Each silo's held-out validation windows are scored three ways, all on the same
windows and the same normalisation, so the comparison is honest:

    28-day mean          predict 1.0 — the burn rate the dashboard uses today,
                         because targets are normalised by the window's own mean
    last value           predict yesterday's consumption
    same day last week   predict the value seven days back

A model that cannot beat the first number is not worth deploying, and saying so
in the open is the point of measuring it.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import torch

from pytorchexample import task
from pytorchexample.task import SILOS, DemandLSTM

DEFAULT_MODEL = Path(__file__).resolve().parent / "final_model.pt"
DEFAULT_OUT = Path(__file__).resolve().parent.parent / "eval_reports" / "federation.json"
BATCH = 512


def _mae(pred: torch.Tensor, target: torch.Tensor) -> float:
    return float((pred - target).abs().sum().item())


def evaluate(model_path: Path) -> dict:
    net = DemandLSTM()
    net.load_state_dict(torch.load(model_path, map_location="cpu"))
    net.eval()

    totals = {"model": 0.0, "28_day_mean": 0.0, "last_value": 0.0, "same_day_last_week": 0.0}
    windows = 0
    per_silo: dict[str, dict] = {}

    with torch.no_grad():
        for pid, state in enumerate(SILOS):
            _, valloader, trust, name, _ = task.load_data(pid, len(SILOS), BATCH)
            silo = {"model": 0.0, "28_day_mean": 0.0, "last_value": 0.0, "same_day_last_week": 0.0}
            count = 0
            for seq, cal, y in valloader:
                pred = net(seq, cal)
                silo["model"] += _mae(pred, y)
                # Every baseline is computed on the same window, so none of them
                # gets an easier problem than the model.
                silo["28_day_mean"] += _mae(torch.ones_like(y), y)
                silo["last_value"] += _mae(seq[:, -1].reshape(y.shape), y)
                silo["same_day_last_week"] += _mae(seq[:, -7].reshape(y.shape), y)
                count += int(y.numel())
            for key in totals:
                totals[key] += silo[key]
            windows += count
            per_silo[name] = {
                "windows": count,
                "live_trust": trust,
                "model_mae": None if count == 0 else round(silo["model"] / count, 4),
                "burn_rate_mae": None if count == 0 else round(silo["28_day_mean"] / count, 4),
            }
            print(
                "  {0}: {1:,} validation windows, model MAE {2:.4f} vs burn rate {3:.4f}".format(
                    name,
                    count,
                    silo["model"] / max(count, 1),
                    silo["28_day_mean"] / max(count, 1),
                )
            )

    n = max(windows, 1)
    return {
        "model": str(model_path.name),
        "measured_at": datetime.now(timezone.utc).isoformat(),
        "silos": list(SILOS),
        "windows": windows,
        "model_mae": round(totals["model"] / n, 4),
        "baselines": {
            "28_day_mean": round(totals["28_day_mean"] / n, 4),
            "last_value": round(totals["last_value"] / n, 4),
            "same_day_last_week": round(totals["same_day_last_week"] / n, 4),
        },
        "per_silo": per_silo,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--model", type=Path, default=DEFAULT_MODEL)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = ap.parse_args()

    if not args.model.is_file():
        print("no model at {0} — train one first (flwr run .)".format(args.model))
        return 1

    print("scoring {0} against the naive rules, silo by silo".format(args.model.name))
    result = evaluate(args.model)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")

    best_naive = min(v for v in result["baselines"].values() if v)
    verdict = "better" if result["model_mae"] < best_naive else "NOT better"
    print(
        "\nmodel MAE {0:.4f} over {1:,} windows — {2} than the best naive rule ({3:.4f})".format(
            result["model_mae"], result["windows"], verdict, best_naive
        )
    )
    print("written to {0}".format(args.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
