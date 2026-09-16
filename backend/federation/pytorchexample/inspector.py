"""The silo inspector — spec 12.2 and 27 (B3).

The whole platform rests on one claim: no facility data leaves a state, only
model weights do. That claim is worth exactly as much as the evidence behind
it, so this module measures what actually crossed the wire and writes it to
`federation_rounds`, which the dashboard reads.

What is measured, per round:

  bytes         the real size of the tensors the aggregator received, summed
                from each array's own byte count
  shapes        every tensor's name and dimensions, so a reader can see that
                what arrived is a 5,697-parameter model and not a dataset
  hash          SHA-256 over the serialised weights, so two runs can be
                compared and a round's output identified
  raw rows      asserted zero — `assert_weights_only()` inspects every reply
                before it counts, and raises if anything but arrays and scalar
                metrics is present. The table stores a zero it earned.
"""

from __future__ import annotations

import hashlib
import os

import psycopg
import torch

# What a client is allowed to send back. Anything else — a dataframe, a list of
# readings, a base64 blob smuggled into a metric — fails the assertion below
# and stops the round rather than being quietly averaged in.
ALLOWED_RECORDS = {"arrays", "metrics"}
ALLOWED_METRIC_TYPES = (int, float, bool)


class RawDataLeak(AssertionError):
    """A client sent something other than weights and scalar metrics."""


def assert_weights_only(records) -> int:
    """Check every reply carries weights and numbers, nothing else.

    Returns the number of raw data rows transmitted, which is zero or this
    raises. The point is that the dashboard's "0 rows" is the result of a
    check, not a constant someone typed.
    """
    for record in records:
        unexpected = set(record.keys()) - ALLOWED_RECORDS
        if unexpected:
            raise RawDataLeak(
                f"A silo returned unexpected records: {sorted(unexpected)}. "
                "Only model weights and scalar metrics may leave a state."
            )
        for metrics in record.metric_records.values():
            for key, value in metrics.items():
                if not isinstance(value, ALLOWED_METRIC_TYPES):
                    raise RawDataLeak(
                        f"Metric {key!r} is a {type(value).__name__}, not a number. "
                        "Bulk data must not travel inside a metric record."
                    )
    return 0


def weight_summary(arrays) -> tuple[int, list[dict], str]:
    """Bytes, tensor shapes, and a hash of the weights that were exchanged."""
    state = arrays.to_torch_state_dict()
    shapes, total = [], 0
    digest = hashlib.sha256()
    for name, tensor in state.items():
        raw = tensor.detach().cpu().contiguous()
        nbytes = raw.numel() * raw.element_size()
        total += nbytes
        shapes.append(
            {
                "name": name,
                "shape": list(raw.shape),
                "params": int(raw.numel()),
                "bytes": int(nbytes),
            }
        )
        digest.update(name.encode())
        digest.update(raw.numpy().tobytes())
    return total, shapes, digest.hexdigest()


def dsn() -> str:
    raw = os.environ.get("SWASTHSETU_DATABASE_URL")
    if not raw:
        raise RuntimeError("SWASTHSETU_DATABASE_URL is not set")
    return raw.replace("postgresql+asyncpg://", "postgresql://")


def record_round(
    *,
    run_id: str,
    round_no: int,
    strategy: str,
    mae: float | None,
    baseline_mae: float | None,
    per_silo: list[dict],
    bytes_transmitted: int,
    tensor_shapes: list[dict],
    weights_sha256: str,
    raw_rows: int,
) -> None:
    """Write one round's evidence where the dashboard can read it.

    Failing to record must never fail the training run: the inspector is
    evidence about the work, not the work itself.
    """
    try:
        with psycopg.connect(dsn(), connect_timeout=10) as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO federation_rounds
                    (run_id, round_no, strategy, global_val_mae, baseline_mae,
                     per_silo, bytes_transmitted, tensor_shapes, weights_sha256,
                     raw_rows_transmitted, silos_reporting, completed_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now())
                ON CONFLICT (run_id, round_no) DO UPDATE SET
                    global_val_mae = EXCLUDED.global_val_mae,
                    baseline_mae = EXCLUDED.baseline_mae,
                    per_silo = EXCLUDED.per_silo,
                    bytes_transmitted = EXCLUDED.bytes_transmitted,
                    tensor_shapes = EXCLUDED.tensor_shapes,
                    weights_sha256 = EXCLUDED.weights_sha256,
                    raw_rows_transmitted = EXCLUDED.raw_rows_transmitted,
                    silos_reporting = EXCLUDED.silos_reporting,
                    completed_at = now()
                """,
                (
                    run_id, round_no, strategy, mae, baseline_mae,
                    psycopg.types.json.Jsonb(per_silo),
                    bytes_transmitted,
                    psycopg.types.json.Jsonb(tensor_shapes),
                    weights_sha256, raw_rows, len(per_silo),
                ),
            )
            conn.commit()
    except Exception as exc:  # noqa: BLE001 - evidence must not break training
        print(f"  [inspector] could not record round {round_no}: {exc}")


def summarise(shapes: list[dict], total_bytes: int, silos: int) -> str:
    params = sum(s["params"] for s in shapes)
    return (
        f"  [inspector] {params:,} parameters in {len(shapes)} tensors, "
        f"{total_bytes / 1024:.1f} KB per silo, "
        f"{total_bytes * silos * 2 / 1024:.1f} KB across the round "
        f"(down and back) — 0 facility rows"
    )
