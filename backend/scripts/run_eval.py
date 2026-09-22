"""Run the evaluation harness and write a dated report.

    python -m scripts.run_eval                    # trust, solver, stock-outs
    python -m scripts.run_eval --federation       # also re-score the model

Every number printed comes from `app/evaluation.py`, which reads the live
tables. The report is written to `backend/eval_reports/` so a claim made in a
deck can be traced to the run that produced it, including the seed.

The federated model is scored by `backend/federation/evaluate_model.py`, which
needs torch and therefore a different environment. Point FEDERATION_PYTHON at
that interpreter to have this script run it; without it the federation section
says it was not measured rather than guessing.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app import childproc, evaluation, groundtruth, vision
from app.config import settings as _settings

# A third automated entry point, alongside pytest and `python -m checks`. It is
# neither, so neither of their guards loads — and it imports `app`, which reads
# the repository .env, where LLM_MODE is live and the key is real. Arm the same
# block here: producing the numbers quoted in the deck must not cost a model
# request.
_settings.llm_mode = "mock"
vision.block_live_calls(True)
from app.db import SessionLocal, engine

BACKEND_ROOT = Path(__file__).resolve().parent.parent
REPORT_DIR = BACKEND_ROOT / "eval_reports"
FEDERATION_JSON = REPORT_DIR / "federation.json"
FEDERATION_SCRIPT = BACKEND_ROOT / "federation" / "evaluate_model.py"


def federation_python() -> str | None:
    """The interpreter that has torch, if we have been told where it is."""
    named = os.environ.get("FEDERATION_PYTHON")
    if named and Path(named).exists():
        return named
    return None


def run_federation_eval() -> tuple[dict[str, Any] | None, str | None]:
    """Re-score the model in the Flower environment. Returns (payload, error)."""
    python = federation_python()
    if not python:
        return None, "FEDERATION_PYTHON is not set to an interpreter with torch installed"
    # The database URL normally lives in .env, which only settings reads, so fall
    # back to it rather than handing the child process an empty string.
    from app.config import settings

    dsn = (
        os.environ.get("SWASTHSETU_DATABASE_URL")
        or os.environ.get("DATABASE_URL")
        or settings.database_url
    )
    env = childproc.inherit_env(
        SWASTHSETU_DATABASE_URL=dsn.replace("postgresql+asyncpg://", "postgresql://"),
        PYTHONPATH=str(FEDERATION_SCRIPT.parent),
    )
    proc = childproc.run(
        [python, str(FEDERATION_SCRIPT), "--out", str(FEDERATION_JSON)],
        cwd=str(FEDERATION_SCRIPT.parent),
        env=env,
        capture_output=True,
    )
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()
        return None, "evaluate_model.py failed: " + (tail[-1] if tail else "no output")
    return read_federation(), None


def read_federation() -> dict[str, Any] | None:
    if not FEDERATION_JSON.is_file():
        return None
    return json.loads(FEDERATION_JSON.read_text(encoding="utf-8"))


def _num(value: Any) -> str:
    if value is None:
        return "not measured"
    if isinstance(value, float):
        return "{0:.3f}".format(value)
    if isinstance(value, int):
        return "{0:,}".format(value)
    return str(value)


def _line(label: str, value: Any) -> None:
    print("  {0:<44} {1}".format(label, _num(value)))


def show(report: dict[str, Any]) -> None:
    s = report["sections"]
    print("\nSwasthSetu evaluation — {0}".format(report["run_at"]))
    print("  {0}".format(report["data"]))
    if report["dataset_seed"] is not None:
        print("  dataset seed {0}".format(report["dataset_seed"]))

    trust = s["trust"]
    print("\nTrust layer, against the generator's ground truth")
    if not trust.get("available"):
        print("  not measured — {0}".format(trust.get("reason")))
    else:
        _line("facilities scored", trust["scored"])
        _line("deliberately dishonest", trust["deliberately_dishonest"])
        _line("flagged by the layer", trust["flagged"])
        _line("recall (dishonest facilities caught)", trust["recall"])
        for k, v in (trust.get("precision_at_k") or {}).items():
            _line("precision, {0} worst-ranked".format(k.replace("top_", "top ")), v)
        _line("precision, every flag", trust["precision"])
        _line("false-positive rate", trust["false_positive_rate"])
        print("  {0}".format(trust["note"]))

    red = s["redistribution"]
    print("\nRedistribution, against its own safety rules")
    _line("states planned", ", ".join(red["states"]))
    _line("sku plans solved", red["sku_plans"])
    _line("proposals", red["proposals"])
    _line("units moved", red["units_moved"])
    _line("constraint violations", red["constraint_violations"])
    for rule in red["rules_checked"]:
        print("    ok  {0}".format(rule))
    for bad in red["violations"]:
        print("    VIOLATION  {0}: {1}".format(bad["rule"], bad["detail"]))

    out = s["stockouts"]
    print("\nStock-outs, counted from the readings")
    _line("state and window", "{0}, {1} days".format(out["state"], out["window_days"]))
    _line("facility-days with nothing on the shelf", out["facility_days_out_of_stock"])
    _line("facilities affected", out["facilities_affected"])
    _line("shortages standing now", out["shortages_now"])
    _line("of those, spare stock within reach", out["spare_stock_within_reach_now"])
    _line("share within reach", None if out["within_reach_pct"] is None else "{0}%".format(out["within_reach_pct"]))
    print("  {0}".format(out["note"]))

    fed = s["federation"]
    print("\nFederated forecasting, against the rules it replaces")
    if not fed.get("available"):
        print("  not measured — {0}".format(fed.get("reason")))
    else:
        _line("validation windows scored", fed["windows_scored"])
        _line("model MAE", fed["model_mae"])
        for name, value in (fed.get("baselines") or {}).items():
            _line("baseline: {0}".format(name.replace("_", " ")), value)
        for name, value in (fed.get("improvement_pct") or {}).items():
            _line(
                "better than {0} by".format(name.replace("_", " ")),
                None if value is None else "{0}%".format(value),
            )
        print("  {0}".format(fed["note"]))


async def main_async(args: argparse.Namespace) -> int:
    federation: dict[str, Any] | None = None
    federation_error: str | None = None
    if args.federation:
        federation, federation_error = run_federation_eval()
    else:
        federation = read_federation()

    truth = groundtruth.read()
    async with SessionLocal() as session:
        report = await evaluation.report(
            session,
            truth=truth,
            federation=federation,
            states=tuple(args.solver_states),
            stockout_state=args.state,
            stockout_days=args.days,
        )
    await engine.dispose()

    if federation_error:
        report["sections"]["federation"] = {"available": False, "reason": federation_error}

    show(report)

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = REPORT_DIR / "eval-{0}.json".format(stamp)
    path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print("\nreport written to {0}".format(path))

    failures = []
    if report["sections"]["redistribution"]["constraint_violations"]:
        failures.append("the solver broke one of its own rules")
    if failures:
        print("FAILED: " + "; ".join(failures))
        return 1
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--state", default=evaluation.DEFAULT_STOCKOUT_STATE, help="state for the stock-out count")
    ap.add_argument("--days", type=int, default=evaluation.DEFAULT_STOCKOUT_DAYS)
    ap.add_argument(
        "--solver-states",
        nargs="+",
        default=list(evaluation.DEFAULT_SOLVER_STATES),
        help="states to plan and check for constraint violations",
    )
    ap.add_argument(
        "--federation",
        action="store_true",
        help="re-score the model with FEDERATION_PYTHON instead of reading the last run",
    )
    args = ap.parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(main())
