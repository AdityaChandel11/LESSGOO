"""Authentication and authorisation.

Sessions are signed JWTs in an httpOnly, SameSite=Lax cookie, so page scripts
can never read them and a cross-site form cannot post with them. The user row
is re-read on every request, so deactivating an account takes effect at once
rather than when its token expires.

Roles and what they may *change* (every signed-in user may *view* everything):

    admin          everything
    state_officer  plans and decisions for transfers inside their state;
                   stock corrections for facilities in their state
    block_mo       decisions for transfers where both facilities are in their
                   district; stock reports for facilities in their district
    facility_user  stock reports for their own facility only
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pwdlib import PasswordHash
from pydantic import BaseModel
from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from .config import settings
from .db import get_session
from .models import LoginFailure, User

DEMO_EMAIL_DOMAIN = "@demo.swasthsetu.in"

_hasher = PasswordHash.recommended()
# Verified against when the email is unknown, so a failed sign-in takes the
# same time whether or not the account exists (no user enumeration by timing).
_DUMMY_HASH = _hasher.hash("timing-equaliser-not-a-real-password")

MIN_PASSWORD_LENGTH = 12


def hash_password(password: str) -> str:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"Password must be at least {MIN_PASSWORD_LENGTH} characters")
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _hasher.verify(password, password_hash)
    except Exception:
        return False


# ================================================================ principal ===


@dataclass(frozen=True)
class Principal:
    id: int
    email: str
    name: str
    role: str
    state_silo: str | None
    district: str | None
    facility_id: str | None
    # Read only on the server, to scope /me/attendance to the reader's own
    # rows. Never serialised to the client — UserOut carries a boolean.
    staff_ref: str | None

    @classmethod
    def from_user(cls, u: User) -> "Principal":
        return cls(
            u.id, u.email, u.name, u.role, u.state_silo, u.district,
            u.facility_id, u.staff_ref,
        )


# ------------------------------------------------------- permission rules ---
# Pure functions: the whole access policy in one place, testable without a DB.


def can_plan_state(p: Principal, state: str) -> bool:
    return p.role == "admin" or (p.role == "state_officer" and p.state_silo == state)


def can_decide_transfer(
    p: Principal,
    *,
    from_state: str,
    from_district: str,
    to_state: str,
    to_district: str,
    from_facility: str | None = None,
) -> bool:
    """Who accepts or declines a transfer: the facility giving the stock.

    Stock leaves a shelf only when the staff of the centre that holds it say
    yes, so the human approval of spec 1.6 sits with the donor. District and
    state officers see every transfer on the dashboard but do not decide them.
    The platform administrator can act on any transfer.
    """
    if p.role == "admin":
        return True
    if p.role == "facility_user":
        return from_facility is not None and p.facility_id == from_facility
    return False


def can_submit_reading(
    p: Principal, *, facility_id: str, facility_state: str, facility_district: str
) -> bool:
    if p.role == "admin":
        return True
    if p.role == "state_officer":
        return p.state_silo == facility_state
    if p.role == "block_mo":
        return p.state_silo == facility_state and p.district == facility_district
    if p.role == "facility_user":
        return p.facility_id == facility_id
    return False


def can_view_facility(
    p: Principal, *, facility_id: str, facility_state: str, facility_district: str
) -> bool:
    """Whether this person may open a facility's own workspace.

    The same scope as reporting for it, deliberately. The national picture
    stays visible to every signed-in user through the map — scope narrows what
    you may change, never what you may see — but the workspace is not the
    national picture: it is one centre's working screen, with its open
    requests and its delivery queue on it, and that belongs to the people
    responsible for that centre.
    """
    return can_submit_reading(
        p,
        facility_id=facility_id,
        facility_state=facility_state,
        facility_district=facility_district,
    )


# ==================================================================== tokens ===


def create_session_token(user: User) -> str:
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {
            "sub": str(user.id),
            "role": user.role,
            "iat": now,
            "exp": now + timedelta(hours=settings.session_hours),
        },
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )


def _token_from_request(request: Request) -> str | None:
    cookie = request.cookies.get(settings.session_cookie_name)
    if cookie:
        return cookie
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip() or None
    return None


async def current_user(
    request: Request, session: AsyncSession = Depends(get_session)
) -> Principal:
    token = _token_from_request(request)
    unauthorised = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Sign in required",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if not token:
        raise unauthorised
    try:
        claims = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
            options={"require": ["sub", "exp", "iat"]},
        )
        user_id = int(claims["sub"])
    except (jwt.PyJWTError, ValueError, KeyError):
        raise unauthorised
    user = await session.get(User, user_id)
    if user is None or not user.is_active:
        raise unauthorised
    return Principal.from_user(user)


def require_roles(*roles: str):
    async def dependency(user: Principal = Depends(current_user)) -> Principal:
        if user.role not in roles:
            raise HTTPException(status_code=403, detail="Your role cannot do this")
        return user

    return dependency


# ============================================================ rate limiting ===


@dataclass(frozen=True)
class FailureWindow:
    """Failed sign-ins seen for one key inside the current window."""

    count: int
    oldest: datetime | None


@dataclass(frozen=True)
class LimitRule:
    name: str
    attempts: int


# Three independent limits, all over the same window:
#   pair   - one address guessing one account: the everyday case
#   email  - one account guessed from anywhere. This is the limit that holds
#            even if a client forges its forwarded IP address, because it
#            ignores the address entirely.
#   ip     - one address spraying many accounts
LOGIN_RULES = (
    LimitRule("pair", settings.login_attempts_per_window),
    LimitRule("email", settings.login_attempts_per_window * 3),
    LimitRule("ip", settings.login_attempts_per_window * 8),
)


def login_retry_after(
    windows: dict[str, FailureWindow],
    now: datetime,
    window: timedelta,
    rules: tuple[LimitRule, ...] = LOGIN_RULES,
) -> int | None:
    """Seconds until sign-in is allowed again, or None if allowed now. Pure."""
    waits = []
    for rule in rules:
        w = windows.get(rule.name)
        if w and w.count >= rule.attempts and w.oldest is not None:
            waits.append(max(1, int((w.oldest + window - now).total_seconds())))
    return max(waits) if waits else None


async def _failure_windows(
    session: AsyncSession, email: str, ip: str, since: datetime
) -> dict[str, FailureWindow]:
    row = (
        await session.execute(
            text("""
                SELECT count(*) FILTER (WHERE email = :email AND ip = :ip),
                       min(at)  FILTER (WHERE email = :email AND ip = :ip),
                       count(*) FILTER (WHERE email = :email),
                       min(at)  FILTER (WHERE email = :email),
                       count(*) FILTER (WHERE ip = :ip),
                       min(at)  FILTER (WHERE ip = :ip)
                FROM login_failures
                WHERE at > :since AND (email = :email OR ip = :ip)
            """),
            {"email": email, "ip": ip, "since": since},
        )
    ).one()
    return {
        "pair": FailureWindow(row[0], row[1]),
        "email": FailureWindow(row[2], row[3]),
        "ip": FailureWindow(row[4], row[5]),
    }


async def _record_failure(session: AsyncSession, email: str, ip: str) -> None:
    failure = LoginFailure(email=email, ip=ip)
    session.add(failure)
    await session.flush()
    if failure.id % 200 == 0:
        await session.execute(
            delete(LoginFailure).where(
                LoginFailure.at < datetime.now(timezone.utc) - timedelta(days=1)
            )
        )
    await session.commit()


# ==================================================================== routes ===

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginIn(BaseModel):
    email: str
    password: str


class UserOut(BaseModel):
    id: int
    email: str
    name: str
    role: str
    state_silo: str | None
    district: str | None
    facility_id: str | None
    # Whether this account is linked to an attendance record, so the app can
    # decide whether to offer the tab. The pseudonymous reference itself stays
    # on the server: the client has no use for it and nothing to do with it.
    has_staff_record: bool = False


class SessionOut(BaseModel):
    user: UserOut
    demo_mode: bool
    environment: str


def _session_out(p: Principal) -> SessionOut:
    fields = {k: v for k, v in p.__dict__.items() if k != "staff_ref"}
    return SessionOut(
        user=UserOut(
            **fields,
            # In demo mode a facility account without a linked staff member is
            # shown a synthetic, labelled record (attendance.synthetic_record).
            has_staff_record=p.staff_ref is not None
            or (settings.demo_mode and p.role == "facility_user" and p.facility_id is not None),
        ),
        demo_mode=settings.demo_mode,
        environment=settings.environment,
    )


def _client_ip(request: Request) -> str:
    # Best effort: uvicorn's --proxy-headers derives this from X-Forwarded-For,
    # which a client can partly influence. The per-email limit does not use it.
    return request.client.host if request.client else "unknown"


async def _start_session(session: AsyncSession, response: Response, user: User) -> SessionOut:
    user.last_login_at = datetime.now(timezone.utc)
    await session.commit()
    response.set_cookie(
        key=settings.session_cookie_name,
        value=create_session_token(user),
        max_age=settings.session_hours * 3600,
        httponly=True,
        secure=settings.secure_cookies,
        samesite="lax",
        path="/",
    )
    return _session_out(Principal.from_user(user))


@router.post("/login", response_model=SessionOut)
async def login(
    payload: LoginIn,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
) -> SessionOut:
    email = payload.email.strip().lower()
    ip = _client_ip(request)
    now = datetime.now(timezone.utc)
    window = timedelta(minutes=settings.login_window_minutes)

    wait = login_retry_after(await _failure_windows(session, email, ip, now - window), now, window)
    if wait is not None:
        raise HTTPException(
            status_code=429,
            detail=f"Too many sign-in attempts. Try again in {max(1, round(wait / 60))} min.",
            headers={"Retry-After": str(wait)},
        )

    user = await session.scalar(select(User).where(func.lower(User.email) == email))
    ok = verify_password(payload.password, user.password_hash if user else _DUMMY_HASH)
    if not user or not ok or not user.is_active:
        await _record_failure(session, email, ip)
        # One message for every failure: never reveal whether the email exists.
        raise HTTPException(status_code=401, detail="Email or password is incorrect")

    # The owner has proved the password; earlier guesses against this account
    # should not keep counting toward a lockout.
    await session.execute(delete(LoginFailure).where(LoginFailure.email == email))
    return await _start_session(session, response, user)


# ---------------------------------------------------------------- demo ---
# Public demo deployments let visitors enter as one of the demo accounts
# without a password. Both endpoints disappear (404) when DEMO_MODE is off,
# and they only ever act on accounts under the demo domain.


class DemoAccountOut(BaseModel):
    email: str
    name: str
    role: str
    scope: str


class DemoLoginIn(BaseModel):
    email: str


def _require_demo() -> None:
    if not settings.demo_mode:
        raise HTTPException(status_code=404, detail="Not found")


@router.get("/demo-accounts", response_model=list[DemoAccountOut])
async def demo_accounts(session: AsyncSession = Depends(get_session)) -> list[DemoAccountOut]:
    _require_demo()
    order = {"admin": 0, "state_officer": 1, "block_mo": 2, "facility_user": 3}
    users = (
        await session.execute(
            select(User).where(User.email.like(f"%{DEMO_EMAIL_DOMAIN}"), User.is_active.is_(True))
        )
    ).scalars().all()
    out = []
    for u in sorted(users, key=lambda u: order.get(u.role, 9)):
        scope = (
            "All of India" if u.role == "admin"
            else u.state_silo if u.role == "state_officer"
            else f"{u.district}, {u.state_silo}" if u.role == "block_mo"
            else u.facility_id or ""
        )
        out.append(DemoAccountOut(email=u.email, name=u.name, role=u.role, scope=scope or ""))
    return out


@router.post("/demo", response_model=SessionOut)
async def demo_login(
    payload: DemoLoginIn,
    response: Response,
    session: AsyncSession = Depends(get_session),
) -> SessionOut:
    _require_demo()
    email = payload.email.strip().lower()
    if not email.endswith(DEMO_EMAIL_DOMAIN):
        raise HTTPException(status_code=403, detail="Only demo accounts can be entered this way")
    user = await session.scalar(select(User).where(User.email == email))
    if user is None or not user.is_active:
        raise HTTPException(status_code=404, detail="That demo account has not been set up")
    return await _start_session(session, response, user)


@router.post("/logout", status_code=204)
async def logout(response: Response) -> Response:
    response.delete_cookie(
        settings.session_cookie_name,
        path="/",
        httponly=True,
        secure=settings.secure_cookies,
        samesite="lax",
    )
    response.status_code = 204
    return response


@router.get("/me", response_model=SessionOut)
async def me(user: Principal = Depends(current_user)) -> SessionOut:
    return _session_out(user)
