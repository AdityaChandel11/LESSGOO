"""Phone channels — the single boundary to Twilio, spec 14 and 15.

`COMMS_MODE=simulator` records what would have been sent and returns. Nothing
leaves the machine, no credential is read, and the whole omnichannel path is
demonstrable with a blank `.env`.
`COMMS_MODE=live` sends through Twilio.

Two rules shape this file.

**Every inbound webhook validates its provider signature before processing.
No exceptions** (spec 1.9). That includes simulator mode: with no auth token
configured there is no signature that can validate, so the webhook refuses
everything. That is the correct behaviour rather than an awkward one — a
webhook is a door onto the public internet, and a door that opens for anyone
while "in simulator mode" is a door. The simulator drives
`/api/ingest/simulate` instead, which sits behind a session like every other
authenticated route.

**The signature is checked by hand, not by the SDK.** Twilio's algorithm is
published and small: HMAC-SHA1 over the exact URL plus every POST parameter
sorted by name and concatenated, compared in constant time against
`X-Twilio-Signature`. Implementing it here means the validation is unit-tested
with a known token and no network, no SDK and no account — which is what lets
Stage C be built and proved before any credential exists.

The URL must be the one Twilio actually signed, scheme and query included. A
service behind a proxy sees `http` and its internal host unless the proxy's
forwarded headers are honoured; that mismatch is the classic cause of a 403
on a webhook that is otherwise correct (spec 23).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import settings
from .models import CallLog, OutboundMessage

log = logging.getLogger(__name__)

# Channels this adapter can send on. Kept separate from ingest.CHANNELS, which
# is about what can arrive.
OUTBOUND_CHANNELS = ("sms", "whatsapp", "ivr")

# `outbound_messages` is a log, not a record: it exists so a demo can show what
# the field would have received. Capped for the same reason every other table
# here is capped — a judge clicking through must not be able to grow a 1 GB
# volume. Oldest first, because the newest message is the one being looked at.
MAX_OUTBOUND_ROWS = 500


class CommsError(Exception):
    """Sending failed. The caller decides whether that is fatal; for an
    inbound reply it never is, because the reading is already committed."""


@dataclass(frozen=True)
class Sent:
    channel: str
    to_ref: str
    body: str
    provider_sid: str | None
    status: str
    simulated: bool


def signature_payload(url: str, params: dict[str, str]) -> bytes:
    """Exactly what Twilio signs: the URL, then each parameter name and value
    concatenated in sorted order, with no separators.

    Built as its own function so the test suite can pin the shape against
    Twilio's published example without going near the network.
    """
    buf = url
    for key in sorted(params):
        buf += key + (params[key] or "")
    return buf.encode("utf-8")


def expected_signature(url: str, params: dict[str, str], auth_token: str) -> str:
    digest = hmac.new(
        auth_token.encode("utf-8"), signature_payload(url, params), hashlib.sha1
    ).digest()
    return base64.b64encode(digest).decode("ascii")


def validate_signature(
    url: str, params: dict[str, str], signature: str | None, *, auth_token: str | None = None
) -> bool:
    """Whether this request really came from Twilio.

    Returns False rather than raising, and refuses in every ambiguous case: no
    token configured, no signature header, or a mismatch. `compare_digest`
    keeps the comparison constant-time, so a wrong signature cannot be guessed
    one character at a time from response timing.
    """
    token = settings.twilio_auth_token if auth_token is None else auth_token
    if not token or not signature:
        return False
    try:
        return hmac.compare_digest(expected_signature(url, params, token), signature)
    except (TypeError, ValueError):
        return False


def webhook_url(scheme: str, host: str, path: str, query: str = "") -> str:
    """The URL Twilio signed, rebuilt from what the request reports.

    Twilio signs the URL it was configured with, so this has to match it
    exactly — including https when the service sits behind a proxy that
    terminates TLS. uvicorn is run with --proxy-headers so `request.url`
    already reflects X-Forwarded-Proto; this function exists to make the
    reconstruction explicit and testable rather than implicit in a route.
    """
    url = "{0}://{1}{2}".format(scheme, host, path)
    return url + "?" + query if query else url


async def record_outbound(
    session: AsyncSession,
    *,
    channel: str,
    to_ref: str,
    body: str,
    provider_sid: str | None,
    status: str,
) -> OutboundMessage:
    """Append to the outbound log and hold it to its ceiling."""
    row = OutboundMessage(
        channel=channel,
        to_ref=to_ref,
        body=body,
        provider_sid=provider_sid,
        status=status,
        sent_at=datetime.now(timezone.utc),
    )
    session.add(row)
    await session.flush()

    total = await session.scalar(select(func.count()).select_from(OutboundMessage))
    excess = int(total or 0) - MAX_OUTBOUND_ROWS
    if excess > 0:
        doomed = select(OutboundMessage.id).order_by(OutboundMessage.sent_at).limit(excess)
        ids = [r[0] for r in (await session.execute(doomed)).all()]
        await session.execute(delete(OutboundMessage).where(OutboundMessage.id.in_(ids)))
    return row


async def send(
    session: AsyncSession, *, channel: str, to_ref: str, body: str
) -> Sent:
    """Send one message, or record what would have been sent.

    `to_ref` is a masked number or a hash, never a raw one: this log is
    readable from a demo screen, and a table of health workers' phone numbers
    is precisely what the salted-hash design exists to prevent.

    A failure to send is logged and returned, never raised past the caller
    that is replying to an inbound message — the reading it is acknowledging
    has already been committed, and losing the acknowledgement must not undo it.
    """
    if channel not in OUTBOUND_CHANNELS:
        raise CommsError("Unknown outbound channel: {0}".format(channel))

    if settings.comms_mode != "live":
        row = await record_outbound(
            session, channel=channel, to_ref=to_ref, body=body,
            provider_sid=None, status="simulated",
        )
        return Sent(channel, to_ref, body, None, "simulated", simulated=True)

    # Live sending is deliberately not implemented yet: it needs credentials
    # that Stage C is being built without, and a half-written live path is
    # worse than an honest absence. When it lands it belongs here and nowhere
    # else, so that test_api_boundary keeps holding.
    raise CommsError(
        "COMMS_MODE=live is not wired yet. Set COMMS_MODE=simulator, or "
        "implement the Twilio send in app/comms.py — the only module allowed "
        "to hold Twilio credentials."
    )


# ================================================================ calls ===
# Everything this system chooses to know about a voice call. Twilio keeps the
# record and any recording; what is kept here is a reference, an outcome, a
# time, a facility and a direction — and nothing else, so that this database
# never becomes a log of who rang whom.


async def record_call(
    session: AsyncSession,
    *,
    call_ref: str,
    outcome: str,
    facility_id: str | None = None,
    direction: str = "inbound",
) -> CallLog:
    """Upsert one call reference and hold the log to its ceiling.

    Upsert rather than insert: Twilio delivers a status callback more than
    once on retry, and a call that rang, connected and completed produces
    three callbacks for the same CallSid. One row per call, last status wins.
    """
    row = await session.get(CallLog, call_ref)
    if row is None:
        row = CallLog(call_ref=call_ref, direction=direction)
        session.add(row)
    row.outcome = outcome
    if facility_id is not None:
        row.facility_id = facility_id
    row.created_at = datetime.now(timezone.utc)
    await session.flush()

    total = await session.scalar(select(func.count()).select_from(CallLog))
    excess = int(total or 0) - settings.max_call_log_rows
    if excess > 0:
        doomed = select(CallLog.call_ref).order_by(CallLog.created_at).limit(excess)
        refs = [r[0] for r in (await session.execute(doomed)).all()]
        await session.execute(delete(CallLog).where(CallLog.call_ref.in_(refs)))
    return row
