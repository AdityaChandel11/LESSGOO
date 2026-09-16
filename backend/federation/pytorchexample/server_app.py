"""SwasthSetu: the national aggregator.

Broadcasts the shared model, collects weight updates from the four state
silos, averages them, and scores the result on a held-out set drawn from every
state. Aggregation is Flower's own FedProx — no hand-written averaging here.

Why FedProx and not plain FedAvg: the silos are strongly non-IID. Kerala's
monsoon runs June to November against Maharashtra's June to September, the
disease mix differs, and the partitions differ in size by a factor of three. A
silo that drifts a long way during its local epochs drags the average with it,
and the proximal term is what holds it near the model the server sent.
`proximal-mu = 0` turns this back into exact FedAvg, so the two are one config
value apart and the difference can be measured rather than argued about.
"""

import torch
from flwr.app import ArrayRecord, ConfigRecord, Context, MetricRecord, RecordDict
from flwr.serverapp import Grid, ServerApp
from flwr.serverapp.strategy import FedProx
from flwr.serverapp.strategy.strategy_utils import aggregate_metricrecords

from pytorchexample import inspector
from pytorchexample.task import SILOS, STATE_NAMES, DemandLSTM, load_centralized_dataset, test

# Create ServerApp
app = ServerApp()

# What each silo reported in the round currently in flight. Filled by the two
# metric hooks below and flushed to `federation_rounds` when the round's
# global evaluation runs.
_pending: dict[str, dict] = {}
_run = {"id": "", "strategy": "", "raw_rows": 0}


def _silo_row(metrics) -> dict:
    state = SILOS[int(metrics["partition-id"])]
    row = _pending.setdefault(state, {"state": state, "name": STATE_NAMES[state]})
    return row


def _capture_train(records: list[RecordDict], weighting_key: str) -> MetricRecord:
    """Record what each silo sent, then aggregate exactly as Flower would.

    The assertion runs here, on the replies themselves, before anything is
    averaged: if a silo ever returned more than weights and numbers, the round
    stops rather than the dashboard quietly printing a zero it did not earn.
    """
    _run["raw_rows"] = inspector.assert_weights_only(records)
    for record in records:
        for metrics in record.metric_records.values():
            row = _silo_row(metrics)
            row.update(
                windows=int(metrics["samples"]),
                trust=round(float(metrics["trust"]), 3),
                weight=int(metrics[weighting_key]),
                train_loss=round(float(metrics["train_loss"]), 5),
                flagged_pct=round(float(metrics.get("flagged_pct", 0.0)), 1),
            )
    return aggregate_metricrecords(records, weighting_key)


def _capture_evaluate(records: list[RecordDict], weighting_key: str) -> MetricRecord:
    inspector.assert_weights_only(records)
    for record in records:
        for metrics in record.metric_records.values():
            row = _silo_row(metrics)
            row.update(
                mae=round(float(metrics["eval_mae"]), 5),
                baseline_mae=round(float(metrics["baseline_mae"]), 5),
                last_value_mae=round(float(metrics.get("last_value_mae", 0.0)), 5),
                week_ago_mae=round(float(metrics.get("week_ago_mae", 0.0)), 5),
            )
    return aggregate_metricrecords(records, weighting_key)


@app.main()
def main(grid: Grid, context: Context) -> None:
    """Main entry point for the ServerApp."""

    # Read run config
    fraction_evaluate: float = context.run_config["fraction-evaluate"]
    num_rounds: int = context.run_config["num-server-rounds"]
    lr: float = context.run_config["learning-rate"]
    proximal_mu: float = context.run_config["proximal-mu"]

    # Load global model
    global_model = DemandLSTM()
    arrays = ArrayRecord(global_model.state_dict())

    weights = sum(p.numel() for p in global_model.parameters())
    print(
        f"\nSwasthSetu federated demand forecasting\n"
        f"  silos          : {len(SILOS)} states ({', '.join(SILOS)})\n"
        f"  model          : LSTM, {weights:,} parameters\n"
        f"  strategy       : FedProx (proximal-mu={proximal_mu})\n"
        f"  rounds         : {num_rounds}\n"
        f"  leaving a silo : model weights only — no facility rows, ever\n"
    )

    # Flower's own FedProx. It weights each silo by the `num-examples` its
    # client reports, which those clients scale by their data-confidence score,
    # so the aggregation is trust-weighted without any custom averaging code.
    _run["id"] = str(context.run_id)
    _run["strategy"] = f"FedProx(mu={proximal_mu})" if proximal_mu else "FedAvg"

    strategy = FedProx(
        fraction_evaluate=fraction_evaluate,
        proximal_mu=proximal_mu,
        # Metric aggregation only — the weights are still averaged by Flower's
        # own FedProx. These hooks exist so the inspector can see what each
        # silo sent before it is summed away.
        train_metrics_aggr_fn=_capture_train,
        evaluate_metrics_aggr_fn=_capture_evaluate,
    )

    result = strategy.start(
        grid=grid,
        initial_arrays=arrays,
        train_config=ConfigRecord({"lr": lr}),
        num_rounds=num_rounds,
        evaluate_fn=global_evaluate,
    )

    if context.run_config["save-model"]:
        print("\nSaving final model to disk...")
        state_dict = result.arrays.to_torch_state_dict()
        torch.save(state_dict, "final_model.pt")


def global_evaluate(server_round: int, arrays: ArrayRecord) -> MetricRecord:
    """Score the national model on held-out weeks from every state."""

    model = DemandLSTM()
    model.load_state_dict(arrays.to_torch_state_dict())
    device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")
    model.to(device)

    test_dataloader = load_centralized_dataset()
    mse, mae, baseline_mae, last_value_mae, week_ago_mae = test(
        model, test_dataloader, device
    )

    # What actually crossed the wire this round, measured rather than claimed.
    total_bytes, shapes, digest = inspector.weight_summary(arrays)
    per_silo = sorted(_pending.values(), key=lambda r: r["state"])
    inspector.record_round(
        run_id=_run["id"],
        round_no=server_round,
        strategy=_run["strategy"],
        mae=mae,
        baseline_mae=baseline_mae,
        per_silo=per_silo,
        # Down to every silo and back again: the real traffic of a round.
        bytes_transmitted=total_bytes * max(len(per_silo), 1) * 2,
        tensor_shapes=shapes,
        weights_sha256=digest,
        raw_rows=_run["raw_rows"],
    )
    if server_round == 1:
        print(inspector.summarise(shapes, total_bytes, len(per_silo)))
    _pending.clear()

    # The number that decides whether any of this was worth doing: the error of
    # the burn-rate rule the platform uses today, against the error of the
    # federated model, on exactly the same weeks.
    improvement = (1 - mae / baseline_mae) * 100 if baseline_mae else 0.0
    print(
        f"  [national] round {server_round:>2}  MAE {mae:.4f}  "
        f"burn-rate baseline {baseline_mae:.4f}  ({improvement:+.1f}%)"
    )

    return MetricRecord(
        {
            "loss": mse,
            "mae": mae,
            "baseline_mae": baseline_mae,
            "last_value_mae": last_value_mae,
            "week_ago_mae": week_ago_mae,
            "improvement_pct": improvement,
        }
    )
