"""Integration checks that live in the repository rather than in a scrollback.

Nine checks, one per claim the platform makes about itself:

    platform      it stands up with no credentials, and refuses what it should
    ledger        dispatch against receipt, and overdue derived from the clock
    trust         a score computed live from the rows it links to
    beds          a code, a geofence, and a check that never fakes a pass
    attendance    honest about what each channel can prove
    ingestion     one spine for every channel, and a reply on every path
    federation    real per-state silos, trust weighting, and the forecast switch
    workspace     a pharmacist's loop: ask, approve, dispatch, confirm
    comms         the phone channels, the signed door, and what is not kept

    python -m checks                 run all of them
    python -m checks ledger trust    run some of them

They use the real database and the real app. They create their own rows, with a
CHK prefix, and delete them again, so running them twice leaves nothing behind.
"""

from . import (
    attendance,
    comms,
    bedreports,
    federation,
    ingestion,
    ledger,
    platform,
    trustlayer,
    workspace,
)

CHECKS = {
    "platform": platform.run,
    "ledger": ledger.run,
    "trust": trustlayer.run,
    "beds": bedreports.run,
    "attendance": attendance.run,
    "ingestion": ingestion.run,
    "federation": federation.run,
    "workspace": workspace.run,
    "comms": comms.run,
}

__all__ = ["CHECKS"]
