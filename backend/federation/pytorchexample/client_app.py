"""SwasthSetu: the state-level client.

One ClientApp instance per state. It receives the national model's weights,
trains on that state's own facilities, and returns weights — never rows. The
only numbers that travel back beside them are the loss and the count used for
weighting.
"""

import torch
from flwr.app import ArrayRecord, Context, Message, MetricRecord, RecordDict
from flwr.clientapp import ClientApp

from pytorchexample.task import STATE_NAMES, DemandLSTM, load_data
from pytorchexample.task import test as test_fn
from pytorchexample.task import train as train_fn

# Flower ClientApp
app = ClientApp()


def _contribution(n_samples: int, trust: float) -> int:
    """How much this silo counts for when the server averages.

    The trust figure is computed from the ledger at the moment the round
    starts — never cached, never precomputed — so a state whose consignments
    stop being confirmed loses weight in the very round that happens.

    FedAvg weights each silo by `num-examples`. Scaling that count by the
    state's mean data confidence (SwasthSetu's trust layer, spec 12.6) is what
    makes the aggregation trust-weighted without hand-writing any aggregation:
    a state whose facilities' own signals contradict each other gets
    proportionally less say in the national model. The unweighted count still
    travels back as `samples` so nothing is hidden.
    """
    return max(1, int(round(n_samples * trust)))


@app.train()
def train(msg: Message, context: Context):
    """Train the national model on this state's local data."""

    model = DemandLSTM()
    model.load_state_dict(msg.content["arrays"].to_torch_state_dict())
    device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")
    model.to(device)

    partition_id = context.node_config["partition-id"]
    num_partitions = context.node_config["num-partitions"]
    batch_size = context.run_config["batch-size"]
    trainloader, _, trust, state, evidence = load_data(
        partition_id, num_partitions, batch_size
    )

    # FedProx sends its proximal weight in the train config; with FedAvg the key
    # is simply absent and this reduces to ordinary local training.
    proximal_mu = float(msg.content["config"].get("proximal-mu", 0.0))

    train_loss = train_fn(
        model,
        trainloader,
        context.run_config["local-epochs"],
        msg.content["config"]["lr"],
        device,
        proximal_mu=proximal_mu,
    )

    samples = len(trainloader.dataset)
    weight = _contribution(samples, trust)
    # The trust figure is recomputed from the ledger at the start of this
    # round, and the evidence beside it is the count of rows that produced it —
    # the same rows an officer sees in the movement tab.
    print(
        f"  [{state}] {STATE_NAMES[state]:<14} trained on {samples:>6,} windows  "
        f"loss {train_loss:.4f}  trust {trust:.3f}  counts as {weight:,}\n"
        f"        ledger now: {evidence['overdue']} unconfirmed and "
        f"{evidence['short']} short of {evidence['total']} consignments "
        f"({evidence['flagged_pct']}% flagged)"
    )

    model_record = ArrayRecord(model.state_dict())
    metrics = {
        "train_loss": train_loss,
        # Which silo this is. A metric record carries numbers only — which is
        # precisely the constraint the inspector enforces — so the state
        # travels as its partition index and the server maps it back.
        "partition-id": float(partition_id),
        # What the strategy weights by (trust-adjusted), and the true count.
        "num-examples": weight,
        "samples": samples,
        "trust": trust,
        # Carried back so the aggregator's log shows what each silo's weight
        # was actually based on this round, not a number taken on faith.
        "flagged_pct": float(evidence["flagged_pct"]),
    }
    content = RecordDict({"arrays": model_record, "metrics": MetricRecord(metrics)})
    return Message(content=content, reply_to=msg)


@app.evaluate()
def evaluate(msg: Message, context: Context):
    """Evaluate the national model on this state's held-out weeks."""

    model = DemandLSTM()
    model.load_state_dict(msg.content["arrays"].to_torch_state_dict())
    device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")
    model.to(device)

    partition_id = context.node_config["partition-id"]
    num_partitions = context.node_config["num-partitions"]
    batch_size = context.run_config["batch-size"]
    _, valloader, trust, state, _ = load_data(
        partition_id, num_partitions, batch_size
    )

    eval_loss, eval_mae, baseline_mae, last_value_mae, week_ago_mae = test_fn(
        model, valloader, device
    )

    metrics = {
        "eval_loss": eval_loss,
        "eval_mae": eval_mae,
        "partition-id": float(partition_id),
        # The rule this model replaces: "next week looks like the last four".
        "baseline_mae": baseline_mae,
        # The other two naive rules, on the same windows (spec 19.2).
        "last_value_mae": last_value_mae,
        "week_ago_mae": week_ago_mae,
        "num-examples": _contribution(len(valloader.dataset), trust),
        "samples": len(valloader.dataset),
    }
    print(
        f"  [{state}] local MAE {eval_mae:.4f} vs burn-rate baseline "
        f"{baseline_mae:.4f}  ({(1 - eval_mae / baseline_mae) * 100:+.1f}%)"
    )
    content = RecordDict({"metrics": MetricRecord(metrics)})
    return Message(content=content, reply_to=msg)
