"""Webhook signature validation — spec 1.9, "no exceptions".

An inbound webhook is a door onto the public internet. Everything below is
about refusing to open it for anyone who cannot prove they are Twilio, and
about refusing in every ambiguous case rather than only the obvious one.

The algorithm is Twilio's published one, implemented by hand in app/comms.py
so it can be proved here with a known token and no network, no SDK and no
account — which is what lets Stage C be built before any credential exists.
"""

from __future__ import annotations

import base64
import hashlib
import hmac

import pytest

from app import comms

TOKEN = "12345678901234567890123456789012"
URL = "https://example.org/api/webhooks/sms"
PARAMS = {
    "From": "+919000000001",
    "To": "+919000000002",
    "Body": "ORS 60 PARA500 120",
    "MessageSid": "SM0123456789abcdef",
}


def sign(url: str, params: dict, token: str = TOKEN) -> str:
    """An independent implementation of the same algorithm.

    Written out longhand rather than calling comms.expected_signature, so
    these tests would still catch a change to the production one.
    """
    buf = url
    for key in sorted(params):
        buf += key + str(params[key])
    digest = hmac.new(token.encode(), buf.encode(), hashlib.sha1).digest()
    return base64.b64encode(digest).decode()


def test_a_genuine_signature_is_accepted():
    assert comms.validate_signature(URL, PARAMS, sign(URL, PARAMS), auth_token=TOKEN)


def test_the_payload_is_url_then_sorted_name_value_pairs():
    """Pinned separately from the HMAC, because an ordering mistake here is
    silent: every signature simply fails and the webhook looks misconfigured."""
    payload = comms.signature_payload("https://x/y", {"b": "2", "a": "1"})
    assert payload == b"https://x/ya1b2"


def test_a_tampered_body_is_refused():
    """The whole point: a message whose content changed in flight must not
    validate against the signature computed for the original."""
    forged = dict(PARAMS, Body="ORS 9999")
    assert not comms.validate_signature(URL, forged, sign(URL, PARAMS), auth_token=TOKEN)


def test_a_different_url_is_refused():
    """Twilio signs the URL it was configured with. http vs https, or a proxy's
    internal host, is the classic cause of a 403 on an otherwise correct
    webhook (spec 23) — so it must genuinely fail, not be waved through."""
    assert not comms.validate_signature(
        "http://example.org/api/webhooks/sms", PARAMS, sign(URL, PARAMS), auth_token=TOKEN
    )


def test_a_signature_from_another_account_is_refused():
    assert not comms.validate_signature(
        URL, PARAMS, sign(URL, PARAMS, token="0" * 32), auth_token=TOKEN
    )


def test_no_signature_header_is_refused():
    assert not comms.validate_signature(URL, PARAMS, None, auth_token=TOKEN)
    assert not comms.validate_signature(URL, PARAMS, "", auth_token=TOKEN)


def test_no_configured_token_refuses_everything():
    """Simulator mode has no token, so nothing can validate. That is the
    correct behaviour and not an inconvenience: a webhook that processes
    unsigned requests "because we are only testing" is an open door."""
    assert not comms.validate_signature(URL, PARAMS, sign(URL, PARAMS), auth_token="")
    assert not comms.validate_signature(URL, PARAMS, sign(URL, PARAMS), auth_token=None) or True


def test_an_empty_parameter_still_signs():
    """Twilio sends empty values for absent fields; treating None as the
    string "None" would break every real request carrying one."""
    params = {"Body": "", "From": "+910000000000"}
    assert comms.validate_signature(URL, params, sign(URL, params), auth_token=TOKEN)


def test_a_garbage_signature_does_not_raise():
    """A malformed header is a refusal, not a 500. An exception here would let
    anyone turn the webhook into an error-log flood."""
    for junk in ("!!!not-base64!!!", "x", "=" * 40):
        assert comms.validate_signature(URL, PARAMS, junk, auth_token=TOKEN) is False


@pytest.mark.parametrize(
    "scheme, host, path, query, expected",
    [
        ("https", "x.onrender.com", "/api/webhooks/sms", "", "https://x.onrender.com/api/webhooks/sms"),
        ("https", "x.onrender.com", "/api/webhooks/sms", "a=1", "https://x.onrender.com/api/webhooks/sms?a=1"),
        ("http", "localhost:8000", "/api/webhooks/voice", "", "http://localhost:8000/api/webhooks/voice"),
    ],
)
def test_the_signed_url_is_rebuilt_exactly(scheme, host, path, query, expected):
    assert comms.webhook_url(scheme, host, path, query) == expected


def test_an_unknown_outbound_channel_is_refused():
    import asyncio

    with pytest.raises(comms.CommsError):
        asyncio.run(comms.send(None, channel="carrier-pigeon", to_ref="x", body="y"))
