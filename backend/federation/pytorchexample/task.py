"""SwasthSetu: federated medicine-demand forecasting with Flower / PyTorch.

Replaces the quickstart's CIFAR-10 image classifier with the forecasting
problem the platform actually needs.

The task
--------
For one facility and one medicine: given the last 28 days of daily consumption,
predict the average daily consumption of the *next* 7 days. Stock on hand
divided by that number is "days of cover", which is what every stockout warning
and every redistribution decision in SwasthSetu rests on.

Each window is divided by its own 28-day mean, so the model learns the *shape*
of demand rather than the size of the facility. A target of 1.0 therefore means
"next week looks like the last four weeks" — which is exactly what the
platform's current burn-rate calculation assumes. That gives us a free and
brutally fair baseline: any model that cannot beat a constant 1.0 has learned
nothing worth deploying, and `test()` reports both numbers side by side.

The partitions
--------------
Read live from the platform's own database, one state per SuperNode, at the
start of every round (see `silo.py`). There is exactly one medicine-stock
database in this system: these training windows, the trust score on the
dashboard and the rows in the movement tab are the same tables. A delivery
confirmed in the browser while a run is in progress is in the next round's
training data.

The federated boundary is the query. Every statement a node issues is filtered
to its own state, so "no raw data leaves the state" is a property of what that
node can ask for. In a real deployment each state runs its own database and
only the connection string differs.
"""

import numpy as np
import torch
import torch.nn as nn
from torch.utils.data import DataLoader, TensorDataset

from pytorchexample import silo

# One silo per state, in partition order. Deliberately unalike: Kerala's
# monsoon runs June-November against Maharashtra's June-September, Bihar's
# burden peaks with the flood season, and the silos differ in size by a factor
# of three. Averaging models across partitions that were all the same would
# prove nothing about federation.
SILOS = ["MH", "KL", "BR", "UP"]
STATE_NAMES = {
    "MH": "Maharashtra",
    "KL": "Kerala",
    "BR": "Bihar",
    "UP": "Uttar Pradesh",
}


SEQ_LEN = 28
CALENDAR_FEATURES = 4  # sin/cos day-of-year, monsoon, festival


class DemandLSTM(nn.Module):
    """A small LSTM over the consumption history, with the calendar alongside.

    Deliberately small. The federated result has to be reproducible on a laptop
    in a few minutes, and a larger model would spend the demo overfitting 15k
    samples rather than showing the rounds converge.
    """

    def __init__(self, hidden: int = 32):
        super().__init__()
        self.lstm = nn.LSTM(input_size=1, hidden_size=hidden, batch_first=True)
        self.head = nn.Sequential(
            nn.Linear(hidden + CALENDAR_FEATURES, 32),
            nn.ReLU(),
            nn.Linear(32, 1),
        )

    def forward(self, seq: torch.Tensor, cal: torch.Tensor) -> torch.Tensor:
        # seq: (batch, 28) daily consumption -> (batch, 28, 1) for the LSTM
        _, (hidden, _) = self.lstm(seq.unsqueeze(-1))
        return self.head(torch.cat([hidden[-1], cal], dim=1)).squeeze(-1)


def _loaders(data: dict, batch_size: int) -> tuple[DataLoader, DataLoader]:
    train = TensorDataset(
        torch.from_numpy(data["x_seq_train"]),
        torch.from_numpy(data["x_cal_train"]),
        torch.from_numpy(data["y_train"]),
    )
    val = TensorDataset(
        torch.from_numpy(data["x_seq_val"]),
        torch.from_numpy(data["x_cal_val"]),
        torch.from_numpy(data["y_val"]),
    )
    return (
        DataLoader(train, batch_size=batch_size, shuffle=True),
        DataLoader(val, batch_size=max(batch_size, 256)),
    )


def load_data(partition_id: int, num_partitions: int, batch_size: int):
    """Read this node's state from the live database. Re-read every round.

    Deliberately not cached across rounds: the point of reading the platform's
    own tables is that each round trains on the data as it stands when that
    round begins — degradation, corrections and all.
    """
    if num_partitions != len(SILOS):
        raise ValueError(
            f"This app federates {len(SILOS)} states ({', '.join(SILOS)}), "
            f"but the federation declares {num_partitions} supernodes."
        )
    state = SILOS[partition_id]
    data = silo.load_state(state)
    trust, evidence = silo.live_trust(state)
    trainloader, valloader = _loaders(data, batch_size)
    return trainloader, valloader, trust, state, evidence


def load_centralized_dataset(batch_size: int = 512) -> DataLoader:
    """The server's held-out set: every silo's validation weeks together.

    The aggregator is the one place entitled to see all four states, and it
    sees only held-out windows, never anything it trains on. Scoring on the
    union rather than one state stops a model that happens to suit the largest
    silo from looking like a national success.
    """
    seqs, cals, ys = [], [], []
    for state in SILOS:
        data = silo.load_state(state)
        seqs.append(data["x_seq_val"])
        cals.append(data["x_cal_val"])
        ys.append(data["y_val"])
    dataset = TensorDataset(
        torch.from_numpy(np.concatenate(seqs)),
        torch.from_numpy(np.concatenate(cals)),
        torch.from_numpy(np.concatenate(ys)),
    )
    return DataLoader(dataset, batch_size=batch_size)


def train(
    net: nn.Module,
    trainloader: DataLoader,
    epochs: int,
    lr: float,
    device: torch.device,
    proximal_mu: float = 0.0,
) -> float:
    """Train locally. Only this silo's own data is ever touched here."""
    net.to(device)
    net.train()
    criterion = nn.MSELoss().to(device)
    optimizer = torch.optim.Adam(net.parameters(), lr=lr)

    # FedProx: keep local training anchored to the model the server sent, so a
    # silo with an unusual season cannot drag the average a long way during its
    # local epochs. mu = 0 reproduces plain FedAvg exactly.
    global_params = [p.detach().clone() for p in net.parameters()] if proximal_mu else []

    running, batches = 0.0, 0
    for _ in range(epochs):
        for seq, cal, y in trainloader:
            seq, cal, y = seq.to(device), cal.to(device), y.to(device)
            optimizer.zero_grad()
            loss = criterion(net(seq, cal), y)
            if proximal_mu:
                proximal = sum(
                    ((local - global_w) ** 2).sum()
                    for local, global_w in zip(net.parameters(), global_params)
                )
                loss = loss + (proximal_mu / 2) * proximal
            loss.backward()
            optimizer.step()
            running += loss.item()
            batches += 1
    return running / max(batches, 1)


def test(
    net: nn.Module, loader: DataLoader, device: torch.device
) -> tuple[float, float, float]:
    """Evaluate, against the method this model is meant to replace.

    Returns (mse, mae, baseline_mae). The baseline predicts 1.0 for every
    window — "next week will look like the last four weeks" — which is the
    burn-rate rule SwasthSetu uses today. The model is only worth deploying if
    its MAE is below that number.
    """
    net.to(device)
    net.eval()
    criterion = nn.MSELoss(reduction="sum")
    total_se, total_ae, baseline_ae, n = 0.0, 0.0, 0.0, 0
    with torch.no_grad():
        for seq, cal, y in loader:
            seq, cal, y = seq.to(device), cal.to(device), y.to(device)
            pred = net(seq, cal)
            total_se += criterion(pred, y).item()
            total_ae += (pred - y).abs().sum().item()
            baseline_ae += (y - 1.0).abs().sum().item()
            n += y.numel()
    n = max(n, 1)
    return total_se / n, total_ae / n, baseline_ae / n
