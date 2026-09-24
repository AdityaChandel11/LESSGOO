"""The live round button: off where it cannot be real, honest about progress.

Nothing here starts Flower. The phases are matched against lines copied from
a real `flwr run --stream` of this repository's ServerApp.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

from app import api, federation_live
from app.auth import Principal

REAL_LINES = [
    "\x1b[92mINFO \x1b[0m:      Starting logstream for run_id `8986275195216079992`",
    "\x1b[92mINFO \x1b[0m:      uv sync took 69.1 seconds.",
    "  resuming      : run 8986275195216079992 after round 3 (sha 5c3e1a90b2d4)",
    "\x1b[92mINFO \x1b[0m:      [ROUND 1/1]",
    "\x1b[92mINFO \x1b[0m:      configure_train: Sampled 4 nodes (out of 4)",
    "\x1b[92mINFO \x1b[0m:      aggregate_train: Received 4 results and 0 failures",
    "\x1b[92mINFO \x1b[0m:      configure_evaluate: Sampled 4 nodes (out of 4)",
    "  [national] round  1  MAE 0.0795  burn-rate baseline 0.1108  (+28.2%)",
    "  inspector: round 4 — 22,788 bytes of weights, 8 tensors, sha d3830b9973ca, 0 facility rows transmitted",
]


def test_every_phase_comes_from_a_line_flower_actually_prints():
    phases = [federation_live.phase_for(line) for line in REAL_LINES]
    assert all(phases), [line for line, p in zip(REAL_LINES, phases) if not p]
    assert phases[-1] == "Round recorded"
    assert len(set(phases)) == len(phases)


class _FakeProc:
    """Stands in for `flwr run --stream`: yields lines, then exits."""

    def __init__(self, lines: list[str], code: int = 0):
        self._lines = lines
        self._code = code
        self.stdout = self

    def __aiter__(self):
        async def gen():
            for line in self._lines:
                yield (line + "\n").encode()
        return gen()

    async def wait(self):
        return self._code

    def kill(self):
        pass


def _follow(lines: list[str], code: int = 0) -> federation_live.LiveRound:
    job = federation_live.LiveRound(run_id="1", round_no=4, started_at=datetime.now(timezone.utc))
    asyncio.run(federation_live._follow(_FakeProc(lines, code), job))
    return job


def test_a_resumed_runs_rescore_of_its_old_model_is_not_this_rounds_score():
    # The order a resumed run really prints: its loaded model is re-scored as
    # "round 0" before the round begins.
    lines = REAL_LINES[:3] + ["  [national] round  0  MAE 0.0791  burn-rate baseline 0.1108"] + REAL_LINES[3:]
    job = _follow(lines)
    labels = [label for label, _ in job.phases]
    assert labels.index("Round started") < labels.index("Scored on held-out weeks from every state")
    assert job.status == "done"


def test_a_run_that_never_records_its_round_is_a_failure():
    job = _follow(REAL_LINES[:-1])
    assert job.status == "failed"


def test_an_unrelated_line_is_not_a_phase():
    assert federation_live.phase_for("INFO : Created env for run in: /root/.flwr/x") is None


def test_a_refused_resume_is_reported_by_its_reason():
    lines = [
        "Traceback (most recent call last):",
        "  File server_app.py, line 80, in main",
        "pytorchexample.server_app.ResumeRefused: saved model hashes 1a2b, but round 3 of run 9 recorded 3c4d",
        "INFO : Run finished",
    ]
    assert "ResumeRefused" in federation_live.failure_line(lines)


def test_production_never_offers_the_button(monkeypatch):
    monkeypatch.setattr(federation_live.settings, "environment", "production")
    reason = asyncio.run(federation_live.unavailable_reason())
    assert reason and "deployment" in reason


def test_without_the_federation_interpreter_it_says_so(monkeypatch):
    monkeypatch.setattr(federation_live.settings, "environment", "development")
    monkeypatch.setattr(federation_live.settings, "federation_python", "")
    reason = asyncio.run(federation_live.unavailable_reason())
    assert reason and "FEDERATION_PYTHON" in reason


def test_a_second_round_cannot_start_while_one_runs(monkeypatch):
    monkeypatch.setattr(
        federation_live, "_current",
        federation_live.LiveRound(run_id="1", round_no=4, started_at=datetime.now(timezone.utc)),
    )
    with pytest.raises(federation_live.AlreadyRunning):
        asyncio.run(federation_live.start(run_id="1", last_round=3))


def _user(role: str) -> Principal:
    return Principal(
        id=1, email="u@example.test", name="U", role=role,
        state_silo="MH" if role != "admin" else None, district=None,
        facility_id=None, staff_ref=None,
    )


def test_only_an_administrator_may_start_a_round():
    with pytest.raises(HTTPException) as exc:
        asyncio.run(api.federation_live_start(session=None, user=_user("state_officer")))
    assert exc.value.status_code == 403
