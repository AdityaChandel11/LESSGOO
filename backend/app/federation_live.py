"""One more federated round, started from the Federation page — where Flower runs.

The web service still never trains and never imports torch (v3 28 B,
"publishing, not serving"). This only starts `flwr run` in the federation's
own interpreter against a SuperLink that is already running, and reads what
the ServerApp prints so the page can show those phases as they happen.

The round is the same ServerApp as any other run. It resumes from the saved
weights only if they hash to what the run's last recorded round wrote, trains
on the four silos, and records its row through the inspector — so the new row
on the page has its own timestamp, its own MAE and its own weights hash
because a round really produced them.

Available only where the federation processes run: never in production, and
not until FEDERATION_PYTHON, a saved model and a listening SuperLink exist.
"""

from __future__ import annotations

import asyncio
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import childproc
from .config import settings

FED_DIR = Path(__file__).resolve().parent.parent / "federation"
MODEL_PATH = FED_DIR / "runs" / "latest_global.pt"
SUPERLINK = ("127.0.0.1", 9093)
ROUND_TIMEOUT_S = 600

# A fragment of a line the ServerApp or Flower prints, and what it means to a
# reader. Matched in order; a phase is shown only once its line has appeared.
PHASES: tuple[tuple[str, str], ...] = (
    ("Starting logstream", "Run accepted by the SuperLink"),
    ("uv sync took", "Aggregator environment ready"),
    ("resuming", "Last round's weights loaded; their hash matches the table"),
    ("[ROUND ", "Round started"),
    ("configure_train", "Model sent to the four state silos to train locally"),
    ("aggregate_train", "All silos replied; replies checked for weights only, then averaged"),
    ("configure_evaluate", "Silos scoring the new model on their own held-out weeks"),
    ("[national] round", "Scored on held-out weeks from every state"),
    ("inspector: round", "Round recorded"),
)

# These describe the round itself. A resumed run first re-scores the model it
# loaded and prints a "[national] round 0" line for it; that is not this
# round's score, so these only count once "Round started" has been seen.
ROUND_PHASES = frozenset(meaning for fragment, meaning in PHASES[4:])

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


@dataclass
class LiveRound:
    run_id: str
    round_no: int
    started_at: datetime
    status: str = "running"  # running | done | failed
    phases: list[tuple[str, float]] = field(default_factory=list)
    finished_at: datetime | None = None
    error: str | None = None


_current: LiveRound | None = None
_task: asyncio.Task | None = None


class Unavailable(Exception):
    pass


class AlreadyRunning(Exception):
    pass


def flwr_executable() -> Path | None:
    if not settings.federation_python:
        return None
    exe = Path(settings.federation_python).parent / "flwr"
    return exe if exe.exists() else None


async def _superlink_listening() -> bool:
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(*SUPERLINK), timeout=0.5)
    except (OSError, asyncio.TimeoutError):
        return False
    writer.close()
    return True


async def unavailable_reason() -> str | None:
    """Why the button is off here, in a sentence, or None when it can run."""
    if settings.is_production:
        return (
            "Live training runs where the federation processes run, such as a laptop "
            "or a worker. This deployment shows the rounds they recorded."
        )
    if flwr_executable() is None:
        return "FEDERATION_PYTHON is not set to the federation's interpreter."
    if not MODEL_PATH.exists():
        return (
            "There is no saved model to continue from yet. Run a full training with "
            "model-path set (backend/federation/README.md)."
        )
    if not await _superlink_listening():
        return "No SuperLink is listening on 127.0.0.1:9093."
    return None


def current() -> LiveRound | None:
    return _current


def phase_for(line: str) -> str | None:
    clean = _ANSI.sub("", line)
    for fragment, meaning in PHASES:
        if fragment in clean:
            return meaning
    return None


def failure_line(lines: list[str]) -> str:
    """The most useful last line of a failed run, never a whole traceback."""
    for line in reversed(lines):
        clean = _ANSI.sub("", line).strip()
        if "ResumeRefused" in clean or "Error" in clean or "error" in clean:
            return clean[-300:]
    return (_ANSI.sub("", lines[-1]).strip()[-300:] if lines else "the run printed nothing")


async def _follow(proc: asyncio.subprocess.Process, job: LiveRound) -> None:
    started = time.monotonic()
    seen: set[str] = set()
    lines: list[str] = []
    in_round = False
    try:
        async with asyncio.timeout(ROUND_TIMEOUT_S):
            assert proc.stdout is not None
            async for raw in proc.stdout:
                line = raw.decode("utf-8", errors="replace").rstrip()
                lines.append(line)
                meaning = phase_for(line)
                if meaning == "Round started":
                    in_round = True
                elif meaning in ROUND_PHASES and not in_round:
                    continue
                if meaning and meaning not in seen:
                    seen.add(meaning)
                    job.phases.append((meaning, round(time.monotonic() - started, 1)))
            code = await proc.wait()
    except TimeoutError:
        proc.kill()
        job.status, job.error = "failed", f"the round did not finish within {ROUND_TIMEOUT_S}s"
    else:
        if code == 0 and "Round recorded" in seen:
            job.status = "done"
        else:
            job.status, job.error = "failed", failure_line(lines)
    job.finished_at = datetime.now(timezone.utc)


async def start(*, run_id: str, last_round: int) -> LiveRound:
    """Continue `run_id` by exactly one round, after `last_round`."""
    global _current, _task
    if _current is not None and _current.status == "running":
        raise AlreadyRunning()
    reason = await unavailable_reason()
    if reason:
        raise Unavailable(reason)
    # Both go inside a TOML string on a command line built here, so neither
    # may carry a quote. A run id is digits (or a timestamp); the path is ours.
    if not re.fullmatch(r"[0-9A-Za-z]+", run_id):
        raise Unavailable("the recorded run id is not one this button can continue")
    if "'" in str(MODEL_PATH):
        raise Unavailable("the model path cannot be passed safely")

    exe = flwr_executable()
    assert exe is not None
    run_config = (
        f"num-server-rounds=1 resume-run-id='{run_id}' start-round={int(last_round)} "
        f"model-path='{MODEL_PATH}'"
    )
    env = childproc.inherit_env(PATH=f"{exe.parent}{os.pathsep}{os.environ.get('PATH', '')}")
    proc = await asyncio.create_subprocess_exec(
        str(exe), "run", str(FED_DIR), "local-deployment", "--stream",
        "--run-config", run_config,
        cwd=str(FED_DIR),
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    _current = LiveRound(run_id=run_id, round_no=last_round + 1, started_at=datetime.now(timezone.utc))
    _task = asyncio.create_task(_follow(proc, _current))
    return _current
