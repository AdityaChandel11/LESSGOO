"""The live Twilio send — what it refuses, and what it says when it fails.

The signature-verification half of this adapter has been testable since before
any credential existed, because Twilio's algorithm is published and can be
checked against a known token with no network. The send half could not be:
until now it raised on sight.

What is tested here is everything around the one HTTP call — the credential
guard, the channel rules, the address rewriting, and the error text — with the
transport injected. A test that actually reached Twilio would cost money and
could ring a real handset, which is why `conftest` blocks live sends for the
whole session and these tests unblock only inside a `with` that puts it back.

The failure text matters more than it looks. Two Twilio errors are the ones a
demo actually hits: 21608, an unverified destination on a trial account, and
63007, a WhatsApp sender that is not registered. Both are fixable in a minute
by the person running the demo *if* the screen says which one happened, and
mysterious for an hour if it says "failed".
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass

import httpx
import pytest

from app import comms
from app.config import settings


@contextlib.contextmanager
def live_mode(**overrides):
    """Put the adapter in live mode with credentials, then put it back.

    Values here are obviously fake and never leave the process: the transport
    is injected, so nothing is dialled and nothing is authenticated.
    """
    before = {
        "comms_mode": settings.comms_mode,
        "twilio_account_sid": settings.twilio_account_sid,
        "twilio_auth_token": settings.twilio_auth_token,
        "twilio_api_key_sid": settings.twilio_api_key_sid,
        "twilio_api_key_secret": settings.twilio_api_key_secret,
        "twilio_sms_number": settings.twilio_sms_number,
        "twilio_whatsapp_number": settings.twilio_whatsapp_number,
    }
    settings.comms_mode = "live"
    settings.twilio_account_sid = "ACtest"
    settings.twilio_auth_token = "token-for-tests"
    settings.twilio_api_key_sid = ""
    settings.twilio_api_key_secret = ""
    settings.twilio_sms_number = "+15550001111"
    settings.twilio_whatsapp_number = "+15550002222"
    for k, v in overrides.items():
        setattr(settings, k, v)
    comms.block_live_calls(False)
    try:
        yield
    finally:
        comms.block_live_calls(True)
        for k, v in before.items():
            setattr(settings, k, v)


@dataclass
class Recorder:
    """Captures the one request the adapter makes, and answers it."""

    status: int = 201
    payload: dict | None = None
    seen: dict | None = None
    url: str = ""
    auth: tuple | None = None

    def transport(self) -> httpx.MockTransport:
        def handle(request: httpx.Request) -> httpx.Response:
            self.url = str(request.url)
            self.seen = dict(httpx.QueryParams(request.content.decode()))
            self.auth = request.headers.get("authorization")
            body = self.payload or {"sid": "SM123", "status": "queued"}
            return httpx.Response(self.status, json=body)

        return httpx.MockTransport(handle)


@pytest.fixture
def recorder(monkeypatch):
    """Point the adapter's client factory at a transport that records."""
    rec = Recorder()
    real_client = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs["transport"] = rec.transport()
        return real_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", factory)
    return rec


# ------------------------------------------------------------ the guardrails ---


def test_simulator_mode_never_reaches_the_network(recorder):
    """The default path stays credential-free and offline."""

    class FakeSession:
        def add(self, row):
            pass

        async def flush(self):
            pass

        async def scalar(self, *a, **k):
            return 0

        async def execute(self, *a, **k):
            return None

    sent = asyncio.run(
        comms.send(FakeSession(), channel="sms", to_ref="+9198…210", body="hello")
    )
    assert sent.simulated is True
    assert sent.status == "simulated"
    assert sent.provider_sid is None
    assert recorder.seen is None, "simulator mode made an HTTP request"


def test_live_mode_without_credentials_refuses_before_the_request(recorder):
    """A missing credential is named, not discovered as a 401."""
    with live_mode(twilio_account_sid="", twilio_auth_token=""):
        problems = settings.comms_credential_problems
    assert any("TWILIO_ACCOUNT_SID" in p for p in problems)
    assert any("TWILIO_AUTH_TOKEN" in p for p in problems)
    assert recorder.seen is None


def test_a_half_configured_api_key_is_refused():
    """Setting one of the pair would silently fall back to the auth token."""
    with live_mode(twilio_api_key_sid="SKtest", twilio_api_key_secret=""):
        problems = settings.comms_credential_problems
    assert any("must be set together" in p for p in problems)


def test_the_api_key_is_preferred_over_the_account_credential():
    with live_mode(twilio_api_key_sid="SKtest", twilio_api_key_secret="shh"):
        assert settings.twilio_send_auth == ("SKtest", "shh")
    with live_mode():
        assert settings.twilio_send_auth == ("ACtest", "token-for-tests")


def test_the_auth_token_is_required_even_when_an_api_key_is_set():
    """It is what inbound webhook signatures are verified against."""
    with live_mode(
        twilio_auth_token="", twilio_api_key_sid="SKtest", twilio_api_key_secret="shh"
    ):
        problems = settings.comms_credential_problems
    assert any("TWILIO_AUTH_TOKEN" in p for p in problems)


# ------------------------------------------------------------------ sending ---


def test_sms_posts_to_the_account_messages_endpoint(recorder):
    with live_mode():
        sid, status = asyncio.run(
            comms._twilio_send(channel="sms", to_ref="+919812345678", body="ORS 60 ok")
        )
    assert sid == "SM123" and status == "queued"
    assert recorder.url.endswith("/Accounts/ACtest/Messages.json")
    assert recorder.seen == {
        "To": "+919812345678",
        "From": "+15550001111",
        "Body": "ORS 60 ok",
    }


def test_whatsapp_prefixes_both_parties(recorder):
    """Same REST endpoint, a different address space (spec 14.2)."""
    with live_mode():
        asyncio.run(
            comms._twilio_send(
                channel="whatsapp", to_ref="+919812345678", body="received"
            )
        )
    assert recorder.seen["To"] == "whatsapp:+919812345678"
    assert recorder.seen["From"] == "whatsapp:+15550002222"


def test_an_already_prefixed_whatsapp_number_is_not_doubled(recorder):
    with live_mode(twilio_whatsapp_number="whatsapp:+15550002222"):
        asyncio.run(
            comms._twilio_send(channel="whatsapp", to_ref="+91981", body="x")
        )
    assert recorder.seen["From"] == "whatsapp:+15550002222"


def test_ivr_is_refused_as_a_message(recorder):
    """A call is placed through the Calls resource, not sent as a message."""
    with live_mode():
        with pytest.raises(comms.CommsError, match="placed as a call"):
            asyncio.run(comms._twilio_send(channel="ivr", to_ref="+91981", body="x"))
    assert recorder.seen is None


def test_a_missing_sender_number_is_named(recorder):
    with live_mode(twilio_sms_number=""):
        with pytest.raises(comms.CommsError, match="TWILIO_SMS_NUMBER"):
            asyncio.run(comms._twilio_send(channel="sms", to_ref="+91981", body="x"))


# ------------------------------------------------------------- what it says ---


def test_a_twilio_error_carries_its_code_and_message(recorder):
    """21608 is an unverified trial destination — a minute to fix, if said."""
    recorder.status = 400
    recorder.payload = {
        "code": 21608,
        "message": "The number +919812345678 is unverified. Trial accounts may "
        "only send messages to verified numbers.",
    }
    with live_mode():
        with pytest.raises(comms.CommsError) as exc:
            asyncio.run(
                comms._twilio_send(channel="sms", to_ref="+919812345678", body="x")
            )
    assert "21608" in str(exc.value)
    assert "unverified" in str(exc.value)


def test_an_unparseable_error_still_reports_its_status(recorder):
    recorder.status = 502
    recorder.payload = None

    def handle(request):
        return httpx.Response(502, text="<html>bad gateway</html>")

    recorder.transport = lambda: httpx.MockTransport(handle)
    with live_mode():
        with pytest.raises(comms.CommsError, match="502"):
            asyncio.run(comms._twilio_send(channel="sms", to_ref="+91981", body="x"))


def test_the_test_guard_blocks_a_live_send():
    """conftest arms this for the whole session; it must actually bite."""
    comms.block_live_calls(True)
    before = settings.comms_mode
    settings.comms_mode = "live"
    try:
        with pytest.raises(comms.CommsError, match="blocked"):
            asyncio.run(comms._twilio_send(channel="sms", to_ref="+91981", body="x"))
    finally:
        settings.comms_mode = before
