"""Scaffolding shared by the integration checks.

These checks talk to the real database and drive the real FastAPI app through an
in-process ASGI transport, so there is nothing to start first and no port to
collide with — but every query, migration, middleware and permission rule is the
production one. That is the difference between these and the unit tests.

Two rules every check follows:
  * it creates its own data, with an obvious prefix, and deletes it again in a
    `finally`, so running the checks twice is the same as running them once;
  * it asserts on what the product would show a user, not on internals.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import SessionLocal

BASE_URL = "http://checks.local"
# Anything this prefix touches is the checks' own and safe to delete.
PREFIX = "CHK"


@dataclass
class Outcome:
    label: str
    passed: bool
    detail: str = ""


@dataclass
class Report:
    name: str
    outcomes: list[Outcome] = field(default_factory=list)
    error: str | None = None
    skipped: str | None = None

    @property
    def passed(self) -> bool:
        return self.error is None and all(o.passed for o in self.outcomes)

    @property
    def tally(self) -> tuple[int, int]:
        return sum(1 for o in self.outcomes if o.passed), len(self.outcomes)


class Checker:
    """Records and prints one check's assertions as they happen."""

    def __init__(self, name: str) -> None:
        self.report = Report(name)

    def ok(self, label: str, condition: Any, detail: str = "") -> bool:
        passed = bool(condition)
        self.report.outcomes.append(Outcome(label, passed, detail))
        mark = "pass" if passed else "FAIL"
        suffix = ""
        if detail and not passed:
            suffix = "  <- " + detail
        elif detail:
            suffix = "  (" + detail + ")"
        print("    {0}  {1}{2}".format(mark, label, suffix))
        return passed

    def eq(self, label: str, got: Any, want: Any) -> bool:
        return self.ok(label, got == want, "got {0!r}, expected {1!r}".format(got, want))

    def near(self, label: str, got: float | None, want: float, tol: float) -> bool:
        close = got is not None and abs(got - want) <= tol
        return self.ok(label, close, "got {0!r}, expected {1} +/- {2}".format(got, want, tol))

    def at_least(self, label: str, got: Any, floor: Any) -> bool:
        return self.ok(label, got is not None and got >= floor, "got {0!r}, need >= {1}".format(got, floor))

    def note(self, text: str) -> None:
        print("          " + text)

    def skip(self, reason: str) -> None:
        self.report.skipped = reason
        print("    skipped  " + reason)


@contextlib.asynccontextmanager
async def db() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session


@contextlib.asynccontextmanager
async def client(demo_email: str | None = None) -> AsyncIterator[httpx.AsyncClient]:
    """The real app over an in-process transport, optionally signed in.

    Sign-in goes through the same demo endpoint the dashboard uses, so the
    session cookie, its flags and the role scoping are all the product's own.
    """
    from app.main import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url=BASE_URL, headers={"Origin": BASE_URL}, timeout=60.0
    ) as http:
        if demo_email:
            r = await http.post("/api/auth/demo", json={"email": demo_email})
            if r.status_code != 200:
                raise RuntimeError(
                    "could not sign in as {0}: {1} {2}".format(demo_email, r.status_code, r.text[:200])
                )
        yield http


async def demo_emails(http: httpx.AsyncClient) -> dict[str, str]:
    """Role -> demo account email, as the sign-in screen offers them."""
    r = await http.get("/api/auth/demo-accounts")
    r.raise_for_status()
    return {row["role"]: row["email"] for row in r.json()}
