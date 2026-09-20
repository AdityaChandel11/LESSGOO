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

from datetime import datetime, timezone

import torch
from flwr.app import ArrayRecord, ConfigRecord, Context, MetricRecord
from flwr.serverapp import Grid, ServerApp
from flwr.serverapp.strategy import FedProx

from flwr.serverapp.strategy.strategy_utils import aggregate_metricrecords

from pytorchexample import inspector
from pytorchexample.task import SILOS, DemandLSTM, load_centralized_dataset, test

# Create ServerApp
app = ServerApp()

# What the current round has learned about itself, filled in as the replies
# arrive and written out once the round is scored. Module state because
# Flower's evaluate callback takes only a round number and the weights.
_ROUND: dict = {"run_id": "", "strategy": "", "per_silo": {}, "raw_rows": 0}


def _capture_train(records: list, weighting_metric_name: str):
    """Inspect every reply, note who sent what, then aggregate as usual.

    The inspection happens before aggregation on purpose: a reply carrying
    anything but weights and scalars stops the round instead of being averaged
    into the national model (spec 12.2).
    """
    _ROUND["raw_rows"] = inspector.assert_weights_only(records)
    per_silo: dict[str, dict] = {}
    for record in records:
        metrics = next(iter(record.metric_records.values()))
        index = int(metrics.get("partition-id", -1))
        state = SILOS[index] if 0 <= index < len(SILOS) else "silo-{0}".format(index)
        per_silo[state] = {
            "windows": int(metrics.get("samples", 0)),
            "counts_as": int(metrics.get("num-examples", 0)),
            "trust": round(float(metrics.get("trust", 0.0)), 3),
            "flagged_pct": round(float(metrics.get("flagged_pct", 0.0)), 1),
            "train_loss": round(float(metrics.get("train_loss", 0.0)), 4),
        }
    _ROUND["per_silo"] = per_silo
    return aggregate_metricrecords(records, weighting_metric_name)


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
    strategy = FedProx(
        fraction_evaluate=fraction_evaluate,
        proximal_mu=proximal_mu,
        train_metrics_aggr_fn=_capture_train,
    )
    _ROUND["run_id"] = str(getattr(context, "run_id", "") or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    _ROUND["strategy"] = "FedProx(mu={0})".format(proximal_mu)

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
    mse, mae, baseline_mae = test(model, test_dataloader, device)

    # The number that decides whether any of this was worth doing: the error of
    # the burn-rate rule the platform uses today, against the error of the
    # federated model, on exactly the same weeks.
    improvement = (1 - mae / baseline_mae) * 100 if baseline_mae else 0.0
    print(
        f"  [national] round {server_round:>2}  MAE {mae:.4f}  "
        f"burn-rate baseline {baseline_mae:.4f}  ({improvement:+.1f}%)"
    )

    # One row per round: the accuracy, and the evidence for what crossed the
    # wire to produce it. Written here because this is the only place that has
    # both the round number and the aggregated weights.
    inspector.record_round(
        run_id=_ROUND["run_id"],
        round_no=server_round,
        strategy=_ROUND["strategy"],
        arrays=arrays,
        global_val_mae=mae,
        baseline_mae=baseline_mae,
        per_silo=_ROUND["per_silo"],
        raw_rows=_ROUND["raw_rows"],
    )

    return MetricRecord(
        {
            "loss": mse,
            "mae": mae,
            "baseline_mae": baseline_mae,
            "improvement_pct": improvement,
        }
    )
