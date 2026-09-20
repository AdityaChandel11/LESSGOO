"""Run the integration checks and report honestly on the result.

    python -m checks
    python -m checks ledger federation

Exit code is 1 if any assertion failed, so this can gate a release. A check that
could not run at all (a missing interpreter, an empty database) is reported as
skipped and does not silently count as a pass.
"""

from __future__ import annotations

import asyncio
import sys
import time

from app.config import settings

from . import CHECKS
from .harness import Report


async def run_named(names: list[str]) -> list[Report]:
    reports: list[Report] = []
    for name in names:
        print("\n== {0} ==".format(name))
        started = time.monotonic()
        try:
            report = await CHECKS[name]()
        except Exception as exc:  # a check that breaks is a failure, not a crash
            report = Report(name=name, error="{0}: {1}".format(type(exc).__name__, exc))
            print("    ERROR  {0}".format(report.error))
        report.seconds = round(time.monotonic() - started, 1)  # type: ignore[attr-defined]
        reports.append(report)
    return reports


def summarise(reports: list[Report]) -> int:
    print("\n" + "=" * 64)
    failed = 0
    for report in reports:
        passed, total = report.tally
        seconds = getattr(report, "seconds", 0.0)
        if report.error:
            state = "ERROR"
        elif not report.passed:
            state = "FAILED"
        elif report.skipped:
            state = "PASS (partly skipped)"
        else:
            state = "PASS"
        if state.startswith(("FAILED", "ERROR")):
            failed += 1
        print("  {0:<12} {1:>3}/{2:<3} {3:<22} {4}s".format(report.name, passed, total, state, seconds))
        if report.skipped:
            print("               skipped: {0}".format(report.skipped))
        for outcome in report.outcomes:
            if not outcome.passed:
                print("               FAIL: {0} — {1}".format(outcome.label, outcome.detail))
    total_assertions = sum(len(r.outcomes) for r in reports)
    total_passed = sum(r.tally[0] for r in reports)
    print(
        "\n  {0}/{1} assertions passed across {2} checks, environment={3}".format(
            total_passed, total_assertions, len(reports), settings.environment
        )
    )
    return 1 if failed else 0


def main() -> int:
    names = sys.argv[1:] or list(CHECKS)
    unknown = [n for n in names if n not in CHECKS]
    if unknown:
        print("unknown check(s): {0}\nknown: {1}".format(", ".join(unknown), ", ".join(CHECKS)))
        return 2
    return summarise(asyncio.run(run_named(names)))


if __name__ == "__main__":
    sys.exit(main())
