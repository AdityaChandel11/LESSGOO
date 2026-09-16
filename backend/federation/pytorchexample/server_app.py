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
from flwr.app import ArrayRecord, ConfigRecord, Context, MetricRecord
from flwr.serverapp import Grid, ServerApp
from flwr.serverapp.strategy import FedProx

from pytorchexample.task import SILOS, DemandLSTM, load_centralized_dataset, test

# Create ServerApp
app = ServerApp()


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
    strategy = FedProx(fraction_evaluate=fraction_evaluate, proximal_mu=proximal_mu)

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

    return MetricRecord(
        {
            "loss": mse,
            "mae": mae,
            "baseline_mae": baseline_mae,
            "improvement_pct": improvement,
        }
    )
