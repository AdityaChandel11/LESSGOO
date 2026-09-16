"""Manage sign-in accounts.

Create or update one account (password prompted, or read from $NEW_USER_PASSWORD
so deploy automation never puts it on a command line or in shell history):

    python -m scripts.users create --email officer@state.gov.in --name "A. Officer" \
        --role state_officer --state MH

    python -m scripts.users create --email ddlo@nashik.gov.in --name "DDLO Nashik" \
        --role block_mo --state MH --district Nashik

    python -m scripts.users create --email pharma@phc.in --name "PHC Pharmacist" \
        --role facility_user --facility HFR-MH-PHC-00012

    python -m scripts.users deactivate --email someone@example.org

Demo accounts, one per role (only while DEMO_MODE is on):

    python -m scripts.users demo
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import secrets
import sys

from sqlalchemy import select

from app.auth import MIN_PASSWORD_LENGTH, hash_password
from app.config import settings
from app.db import SessionLocal
from app.geo import STATE_BY_CODE
from app.models import USER_ROLES, Facility, User


def _read_password(confirm: bool = True) -> str:
    env = os.environ.get("NEW_USER_PASSWORD")
    if env:
        return env
    if not sys.stdin.isatty():
        raise SystemExit("No terminal to prompt on: set NEW_USER_PASSWORD instead.")
    first = getpass.getpass(f"Password (min {MIN_PASSWORD_LENGTH} characters): ")
    if confirm and getpass.getpass("Repeat password: ") != first:
        raise SystemExit("Passwords did not match.")
    return first


async def _resolve_scope(
    session, role: str, state: str | None, district: str | None, facility: str | None
) -> tuple[str | None, str | None, str | None]:
    """Validate that a role has exactly the scope it needs, derived from real data."""
    if role == "admin":
        return None, None, None

    if role == "facility_user":
        if not facility:
            raise SystemExit("facility_user needs --facility")
        f = await session.get(Facility, facility)
        if f is None:
            raise SystemExit(f"No facility with id {facility}")
        return f.state_silo, f.district, f.id

    if not state or state not in STATE_BY_CODE:
        raise SystemExit(f"{role} needs --state with a valid code, e.g. MH")
    if role == "state_officer":
        return state, None, None

    # block_mo
    if not district:
        raise SystemExit("block_mo needs --district")
    exists = await session.scalar(
        select(Facility.id).where(Facility.state_silo == state, Facility.district == district).limit(1)
    )
    if exists is None:
        raise SystemExit(f"No facilities in district '{district}' of {state}")
    return state, district, None


async def upsert_user(
    *, email: str, name: str, role: str, password: str,
    state: str | None = None, district: str | None = None, facility: str | None = None,
) -> tuple[User, bool]:
    email = email.strip().lower()
    if role not in USER_ROLES:
        raise SystemExit(f"role must be one of {', '.join(USER_ROLES)}")
    async with SessionLocal() as session:
        state, district, facility = await _resolve_scope(session, role, state, district, facility)
        user = await session.scalar(select(User).where(User.email == email))
        created = user is None
        if created:
            user = User(email=email)
            session.add(user)
        user.name = name
        user.role = role
        user.state_silo = state
        user.district = district
        user.facility_id = facility
        user.password_hash = hash_password(password)
        user.is_active = True
        await session.commit()
        return user, created


async def cmd_create(args: argparse.Namespace) -> None:
    user, created = await upsert_user(
        email=args.email, name=args.name, role=args.role, password=_read_password(),
        state=args.state, district=args.district, facility=args.facility,
    )
    scope = user.facility_id or ":".join(filter(None, [user.state_silo, user.district])) or "national"
    print(f"{'Created' if created else 'Updated'} {user.email} ({user.role}, {scope})")


async def cmd_deactivate(args: argparse.Namespace) -> None:
    async with SessionLocal() as session:
        user = await session.scalar(select(User).where(User.email == args.email.strip().lower()))
        if user is None:
            raise SystemExit("No such account")
        user.is_active = False
        await session.commit()
    print(f"Deactivated {args.email}. Existing sessions stop working on their next request.")


async def cmd_demo(args: argparse.Namespace) -> None:
    if not settings.demo_mode:
        raise SystemExit("Demo accounts are only created while DEMO_MODE is on.")
    async with SessionLocal() as session:
        facility = await session.scalar(
            select(Facility).where(Facility.state_silo == "MH", Facility.district == "Nashik").limit(1)
        )
    if facility is None:
        raise SystemExit("Seed facility data first (python -m scripts.seed).")

    password = args.password or os.environ.get("DEMO_PASSWORD") or secrets.token_urlsafe(12)
    accounts = [
        dict(email="admin@demo.swasthsetu.in", name="Platform Admin", role="admin"),
        dict(email="mh.officer@demo.swasthsetu.in", name="Maharashtra NHM Officer",
             role="state_officer", state="MH"),
        dict(email="nashik.ddlo@demo.swasthsetu.in", name="Nashik District Logistics Officer",
             role="block_mo", state="MH", district="Nashik"),
        dict(email="pharmacist@demo.swasthsetu.in", name=f"Pharmacist, {facility.name}",
             role="facility_user", facility=facility.id),
    ]
    for a in accounts:
        user, created = await upsert_user(password=password, **a)
        print(f"  {'created' if created else 'updated'}  {user.email:34} {user.role}")
    print(f"\nDemo password for all four: {password}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Manage SwasthSetu accounts")
    sub = parser.add_subparsers(dest="command", required=True)

    c = sub.add_parser("create", help="create or update an account")
    c.add_argument("--email", required=True)
    c.add_argument("--name", required=True)
    c.add_argument("--role", required=True, choices=USER_ROLES)
    c.add_argument("--state")
    c.add_argument("--district")
    c.add_argument("--facility")

    d = sub.add_parser("deactivate", help="block an account from signing in")
    d.add_argument("--email", required=True)

    demo = sub.add_parser("demo", help="create one demo account per role")
    demo.add_argument("--password", help="defaults to $DEMO_PASSWORD or a random value")

    args = parser.parse_args()
    handler = {"create": cmd_create, "deactivate": cmd_deactivate, "demo": cmd_demo}[args.command]
    asyncio.run(handler(args))


if __name__ == "__main__":
    main()
