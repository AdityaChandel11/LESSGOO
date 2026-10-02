"""Whether a row on screen is verified, and why — from the rows themselves.

A row is verified when a second, independent record agrees with it: the
receiving centre's confirmation against the dispatch, or enough observations
behind every signal of a trust score. Anything else is unverified, and the
reason says which record is missing or which two disagree. Nothing here
estimates: each sentence is built from fields already stored on the row.
"""

from __future__ import annotations


def _n(value: float) -> str:
    return "{0:,.0f}".format(value) if float(value).is_integer() else "{0:,.1f}".format(value)


def verdict(verified: bool, reason: str) -> dict:
    return {"verified": verified, "reason": reason}


def movement(
    *,
    status: str,
    qty_dispatched: float,
    qty_received: float | None,
    received_via: str | None = None,
    days_outstanding: float | None = None,
) -> dict:
    """One consignment: the dispatch record against the centre's confirmation.
    `status` is the displayed one (movements.display_status)."""
    sent = _n(qty_dispatched)
    if status == "received" and qty_received is not None:
        via = " by {0}".format(received_via) if received_via else ""
        return verdict(
            True,
            "The centre confirmed {0} of {1} sent{2}; both records agree.".format(
                _n(qty_received), sent, via
            ),
        )
    if status in ("short", "over") and qty_received is not None:
        gap = abs(qty_received - qty_dispatched)
        return verdict(
            False,
            "The two records disagree: {0} sent, {1} counted by the centre ({2} {3}).".format(
                sent, _n(qty_received), _n(gap), "missing" if status == "short" else "extra"
            ),
        )
    if status == "overdue":
        late = (
            " It is {0} days past the delivery window.".format(_n(round(days_outstanding)))
            if days_outstanding is not None and days_outstanding >= 1
            else " It is past the delivery window."
        )
        return verdict(
            False, "Only the dispatch record exists; the centre has not confirmed." + late
        )
    if status == "cancelled":
        return verdict(False, "Cancelled before the centre confirmed anything.")
    return verdict(
        False, "Only the dispatch record exists; the centre has not confirmed what arrived yet."
    )


def transfer(*, status: str, movement_row: dict | None) -> dict:
    """One recommended transfer: verified only once its consignment is.
    `movement_row` is the ledger row dispatched for it, as keyword arguments
    for `movement`, or None when nothing was dispatched."""
    if status == "proposed":
        return verdict(False, "Awaiting the donor centre's answer; nothing has been dispatched.")
    if status == "rejected":
        return verdict(False, "Declined by the donor; nothing was dispatched.")
    if status == "cancelled":
        return verdict(False, "Withdrawn by the centre that asked; nothing was dispatched.")
    if movement_row is None:
        return verdict(False, "Accepted, but no dispatch record is on the ledger.")
    return movement(**movement_row)


def trust_row(components: list[dict]) -> dict:
    """One audit-queue row: whether every signal behind the score had enough
    observations to be scored (trust.MIN_OBSERVATIONS)."""
    scored = [c for c in components if c.get("scored", True)]
    thin = [c for c in components if not c.get("scored", True)]
    observations = sum(c.get("sample") or 0 for c in scored)
    if not thin:
        return verdict(
            True,
            "All {0} signals were scored, from {1} observations in the centre's own records.".format(
                len(scored), _n(observations)
            ),
        )
    return verdict(
        False,
        "{0} of {1} signals had too few observations to score: {2}.".format(
            len(thin),
            len(components),
            ", ".join(c["signal"].replace("_", " ") for c in thin),
        ),
    )
