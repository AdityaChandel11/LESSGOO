"""Run the integration checks and report honestly on the result.

    python -m checks
    python -m checks ledger federation
    python -m checks beds --live-llm      # spends real Gemini quota, on purpose

Exit code is 1 if any assertion failed, so this can gate a release. A check that
could not run at all (a missing interpreter, an empty database) is reported as
skipped and does not silently count as a pass.

**Live model calls are blocked unless --live-llm is passed.** These checks are
a release gate and run constantly; the Gemini free tier allows twenty generate
requests a day per model. A gate that spends one on every run exhausts the
quota by lunchtime and then starts failing for a reason that has nothing to do
with the code — which is what the ward-photo check did, on every single run,
until this flag existed. Blocking is enforced inside `vision` itself rather
than trusted to each check, and it raises rather than silently downgrading,
because a quiet fallback would hide the mistake it exists to catch.
"""

from __future__ import annotations

import asyncio
import sys
import time
import traceback

from app import vision
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
            # The traceback is printed, not just the message. A check that fails
            # once and then passes is the hardest kind to explain, and the type
            # and message alone do not say which line gave way.
            report = Report(name=name, error="{0}: {1}".format(type(exc).__name__, exc))
            print("    ERROR  {0}".format(report.error))
            traceback.print_exc()
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
    argv = sys.argv[1:]
    live_llm = "--live-llm" in argv
    names = [a for a in argv if not a.startswith("--")] or list(CHECKS)

    unknown = [n for n in names if n not in CHECKS]
    if unknown:
        print("unknown check(s): {0}".format(", ".join(unknown)))
        print("known: {0}".format(", ".join(CHECKS)))
        return 2

    # Two belts. The mode switch sends every mode-aware path down its local
    # branch, and the guard makes the attempt itself raise at the one place
    # that can open a connection to Google — so a check that ignores the
    # mode still cannot spend a request.
    if live_llm:
        print("  --live-llm: real model calls are ENABLED and will spend quota.")
    else:
        settings.llm_mode = "mock"
        vision.block_live_calls(True)
        print("  live model calls blocked (pass --live-llm to spend real quota)")

    return summarise(asyncio.run(run_named(names)))


if __name__ == "__main__":
    sys.exit(main())
