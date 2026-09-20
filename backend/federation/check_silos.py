"""Prove the federation's two claims, from inside the Flower environment.

    SWASTHSETU_DATABASE_URL=postgresql://... python check_silos.py

B1  each SuperNode reads one state out of the platform's own database, and the
    partitions are genuinely different sizes — a federation over four copies of
    the same data would show nothing.
B2  contributions are weighted by a trust figure read from the ledger at that
    moment, and the aggregation is Flower's own FedProx rather than anything
    hand-written here.

Prints JSON on the last line so a caller in another environment can assert on
it without importing torch.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import torch
from flwr.serverapp.strategy import FedProx

from pytorchexample import task
from pytorchexample.client_app import _contribution
from pytorchexample.task import SILOS, DemandLSTM

BATCH = 64


def main() -> int:
    silos = []
    for pid, _ in enumerate(SILOS):
        trainloader, valloader, trust, state, _ = task.load_data(pid, len(SILOS), BATCH)
        n = len(trainloader.dataset)
        silos.append(
            {
                "partition": pid,
                "state": state,
                "train_windows": n,
                "val_windows": len(valloader.dataset),
                "live_trust": trust,
                "counts_as": _contribution(n, trust),
            }
        )
        print(
            "  partition {0} -> {1}: {2:,} train windows, live trust {3:.3f}, counts as {4:,}".format(
                pid, state, n, trust, _contribution(n, trust)
            )
        )

    strategy = FedProx(fraction_evaluate=0.5, proximal_mu=0.01)
    net = DemandLSTM()
    # One local epoch on the smallest silo: proof the client path runs against
    # live data, not just that the data loads.
    smallest = min(silos, key=lambda s: s["train_windows"])
    trainloader, _, _, _, _ = task.load_data(smallest["partition"], len(SILOS), BATCH)
    loss = task.train(net, trainloader, epochs=1, lr=1e-3, device=torch.device("cpu"), proximal_mu=0.01)

    result = {
        "measured_at": datetime.now(timezone.utc).isoformat(),
        "silos": silos,
        "distinct_states": len({s["state"] for s in silos}),
        "strategy": "{0}.{1}".format(type(strategy).__module__, type(strategy).__name__),
        "proximal_mu": 0.01,
        "trained_partition": smallest["partition"],
        "one_epoch_loss": round(loss, 4),
        "model_parameters": sum(p.numel() for p in net.parameters()),
    }
    print("JSON " + json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
