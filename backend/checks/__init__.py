"""Integration checks that live in the repository rather than in a scrollback.

Six checks, one per claim the platform makes about itself:

    platform      it stands up with no credentials, and refuses what it should
    ledger        dispatch against receipt, and overdue derived from the clock
    trust         a score computed live from the rows it links to
    beds          a code, a geofence, and a check that never fakes a pass
    attendance    honest about what each channel can prove
    federation    real per-state silos, trust weighting, and the forecast switch

    python -m checks                 run all of them
    python -m checks ledger trust    run some of them

They use the real database and the real app. They create their own rows, with a
CHK prefix, and delete them again, so running them twice leaves nothing behind.
"""

from . import attendance, bedreports, federation, ledger, platform, trustlayer

CHECKS = {
    "platform": platform.run,
    "ledger": ledger.run,
    "trust": trustlayer.run,
    "beds": bedreports.run,
    "attendance": attendance.run,
    "federation": federation.run,
}

__all__ = ["CHECKS"]
