"""The silo inspector: evidence that nothing but weights left a state.

Spec 12.2 asks for `raw_rows_transmitted = 0` to be *asserted in code, not just
claimed in a slide*. That is what this module is for. Every reply a silo sends
is inspected before it is aggregated: an `ArrayRecord` of model weights and a
`MetricRecord` of scalars are the only things allowed through, and anything
else stops the round rather than being quietly averaged in.

So the zero on the Federation page is a result. If a future change started
shipping facility rows back to the aggregator, this raises instead of
rendering a comfortable number.

What each round records (spec 12.2, 27):
  * measured bytes on the wire — summed from the arrays themselves, not estimated
  * every tensor's shape, so the payload can be recognised as a model
  * a SHA-256 of the serialised weights
  * the per-silo table: windows held, live trust, trust-weighted contribution
  * what the naive rule scored on the same held-out windows
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Iterable

import psycopg

from . import silo

# Record types a silo may return. Anything else is a leak until proven
# otherwise, which is the right default for this particular claim.
ALLOWED_RECORD_TYPES = ("ArrayRecord", "MetricRecord", "ConfigRecord")
SCALAR_TYPES = (int, float, bool, str)


class RawDataLeak(Exception):
    """A silo returned something that was not weights or a scalar metric."""


def assert_weights_only(replies: Iterable[Any]) -> int:
    """Inspect every reply. Returns the count of raw facility rows seen: zero.

    Raises RawDataLeak the moment anything arrives that is not model weights or
    a scalar number, which is the only way the zero this returns can be trusted.
    """
    rows = 0
    for reply in replies:
        content = getattr(reply, "content", reply)
        items = content.items() if hasattr(content, "items") else []
        for name, record in items:
            kind = type(record).__name__
            if kind not in ALLOWED_RECORD_TYPES:
                raise RawDataLeak(
                    "silo returned {0!r} as {1}; only {2} may cross a silo boundary".format(
                        kind, name, " or ".join(ALLOWED_RECORD_TYPES)
                    )
                )
            if kind == "ArrayRecord":
                continue
            for key, value in dict(record).items():
                if isinstance(value, SCALAR_TYPES):
                    continue
                if isinstance(value, (list, tuple)) and all(
                    isinstance(v, SCALAR_TYPES) for v in value
                ):
                    # A short list of numbers is a metric; a long one is data.
                    if len(value) <= 16:
                        continue
                    rows += len(value)
                    raise RawDataLeak(
                        "metric {0!r} carries {1} values — that is a dataset, not a measurement".format(
                            key, len(value)
                        )
                    )
                raise RawDataLeak(
                    "metric {0!r} is a {1}; metrics must be scalars".format(key, type(value).__name__)
                )
    return rows


def weight_summary(arrays: Any) -> tuple[int, dict[str, list[int]], str]:
    """Measured bytes, tensor shapes and a hash of the weights on the wire."""
    state_dict = arrays.to_torch_state_dict()
    shapes: dict[str, list[int]] = {}
    total_bytes = 0
    digest = hashlib.sha256()
    for name in sorted(state_dict):
        tensor = state_dict[name]
        shapes[name] = list(tensor.shape)
        buffer = tensor.detach().cpu().numpy()
        total_bytes += int(buffer.nbytes)
        digest.update(name.encode())
        digest.update(buffer.tobytes())
    return total_bytes, shapes, digest.hexdigest()


def record_round(
    *,
    run_id: str,
    round_no: int,
    strategy: str,
    arrays: Any,
    global_val_mae: float | None,
    baseline_mae: float | None,
    per_silo: dict[str, dict],
    raw_rows: int,
) -> None:
    """Write one round's evidence. Never guesses: absent stays absent."""
    total_bytes, shapes, sha = weight_summary(arrays)
    with psycopg.connect(silo.dsn(), connect_timeout=10) as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO federation_rounds
                (run_id, round_no, strategy, global_val_mae, baseline_mae, per_silo,
                 silos_reporting, bytes_transmitted, tensor_shapes, weights_sha256,
                 raw_rows_transmitted, completed_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (run_id, round_no) DO UPDATE SET
                global_val_mae = EXCLUDED.global_val_mae,
                baseline_mae   = EXCLUDED.baseline_mae,
                per_silo       = EXCLUDED.per_silo,
                silos_reporting = EXCLUDED.silos_reporting,
                bytes_transmitted = EXCLUDED.bytes_transmitted,
                tensor_shapes  = EXCLUDED.tensor_shapes,
                weights_sha256 = EXCLUDED.weights_sha256,
                raw_rows_transmitted = EXCLUDED.raw_rows_transmitted,
                completed_at   = EXCLUDED.completed_at
            """,
            (
                run_id,
                round_no,
                strategy,
                global_val_mae,
                baseline_mae,
                json.dumps(per_silo),
                len(per_silo) or None,
                total_bytes,
                json.dumps(shapes),
                sha,
                raw_rows,
                datetime.now(timezone.utc),
            ),
        )
        conn.commit()
    print(
        "  inspector: round {0} — {1:,} bytes of weights, {2} tensors, sha {3}, "
        "{4} facility rows transmitted".format(
            round_no, total_bytes, len(shapes), sha[:12], raw_rows
        )
    )
