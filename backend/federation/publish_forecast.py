"""Publish the federated model's predictions into the platform — spec 27 (B5).

    python publish_forecast.py                 # after a run has saved final_model.pt
    python publish_forecast.py --dry-run       # print what it would write

The trained model never runs inside the web service. This job loads the
weights the federation produced, reads the last 28 days for every facility and
medicine from the same database the dashboard reads, and writes one row per
pair into `forecasts`. The API then reads rows — so the image serving the
dashboard carries no torch, and a bad training run can never take the site
down. It simply stops publishing, the rows go stale, and days-of-cover falls
back to the burn rate on its own.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone

import numpy as np
import psycopg
import torch

from pytorchexample import silo
from pytorchexample.task import SEQ_LEN, SILOS, DemandLSTM

MODEL_PATH = "final_model.pt"
# Only these are forecast: the medicines the federation actually trained on.
# Publishing a prediction for a medicine no silo ever saw would be inventing a
# number, and the burn rate covers those perfectly well.
SKUS = silo.SKUS


def latest_windows(conn, states: list[str]) -> list[tuple[str, str, np.ndarray, float]]:
    """The last 28 days of consumption per facility and medicine."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT r.facility_id, r.sku_code, r.reported_at, r.qty_on_hand, r.source
            FROM stock_readings r
            JOIN facilities f ON f.id = r.facility_id
            WHERE f.state_silo = ANY(%s)
              AND r.sku_code = ANY(%s)
              AND r.reported_at >= now() - interval '60 days'
            ORDER BY r.facility_id, r.sku_code, r.reported_at
            """,
            (states, list(SKUS)),
        )
        series: dict[tuple[str, str], list[tuple]] = defaultdict(list)
        for facility_id, sku, at, qty, source in cur.fetchall():
            series[(facility_id, sku)].append((at, qty, source))

    out = []
    for (facility_id, sku), rows in series.items():
        days, used = silo._consumption(rows)
        if len(used) < SEQ_LEN:
            continue
        seq = used[-SEQ_LEN:]
        scale = float(seq.mean())
        if scale < silo.MIN_DAILY_USE:
            # Mostly stocked out: there is no rate here to forecast, and the
            # burn rate already says so.
            continue
        cal = np.asarray(silo.calendar_features(days[-1]), dtype=np.float32)
        out.append((facility_id, sku, seq / scale, scale, cal))
    return out


def main() -> int:
    p = argparse.ArgumentParser(description="Publish federated forecasts")
    p.add_argument("--model", default=MODEL_PATH)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--states", default=",".join(SILOS))
    args = p.parse_args()

    states = [s.strip() for s in args.states.split(",") if s.strip()]
    model = DemandLSTM()
    model.load_state_dict(torch.load(args.model, map_location="cpu"))
    model.eval()
    version = f"fedprox-{datetime.now(timezone.utc):%Y%m%d-%H%M}"

    with psycopg.connect(silo.dsn(), connect_timeout=10) as conn:
        windows = latest_windows(conn, states)
        if not windows:
            print("No series long enough to forecast. Nothing published.")
            return 1

        seqs = torch.from_numpy(np.stack([w[2] for w in windows]).astype(np.float32))
        cals = torch.from_numpy(np.stack([w[4] for w in windows]).astype(np.float32))
        with torch.no_grad():
            ratios = model(seqs, cals).clamp(min=0.05, max=5.0).numpy()

        rows = [
            (facility_id, sku, float(ratio) * scale, float(ratio), version)
            for (facility_id, sku, _seq, scale, _cal), ratio in zip(windows, ratios)
        ]

        print(f"{len(rows):,} forecasts across {len(states)} states")
        sample = rows[:5]
        for facility_id, sku, daily, ratio, _ in sample:
            print(f"  {facility_id} {sku:<8} {daily:8.1f}/day  ({ratio:.2f}x the last 28 days)")
        if args.dry_run:
            print("\n--dry-run: nothing written")
            return 0

        with conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO forecasts
                    (facility_id, sku_code, predicted_daily_use, ratio,
                     model_version, computed_at)
                VALUES (%s, %s, %s, %s, %s, now())
                ON CONFLICT (facility_id, sku_code) DO UPDATE SET
                    predicted_daily_use = EXCLUDED.predicted_daily_use,
                    ratio = EXCLUDED.ratio,
                    model_version = EXCLUDED.model_version,
                    computed_at = EXCLUDED.computed_at
                """,
                rows,
            )
        conn.commit()

    print(f"\npublished as {version}")
    print("The dashboard uses these only when FORECAST_MODE=federated;")
    print("otherwise, and whenever they go stale, it falls back to the burn rate.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
