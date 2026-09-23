"""Does this Twilio account actually work? — run before trusting COMMS_MODE=live.

    python -m scripts.check_twilio
    python -m scripts.check_twilio --send +919812345678

Written because "the credentials are in .env" and "a message can be sent" are
different claims, and the gap between them is where a demo dies. An account can
authenticate perfectly and still be unable to send a single message: a trial
account may only send to numbers it has verified, and an account that owns no
phone number has nothing to send *from*.

Everything here is read-only unless `--send` is passed, and `--send` is the one
thing that costs money. Nothing prints a credential: numbers are masked, and a
failure reports Twilio's own error code so it can be looked up.
"""

from __future__ import annotations

import argparse
import asyncio

import httpx

from app.config import settings

API = "https://api.twilio.com/2010-04-01"
OK, BAD, WARN = "  OK  ", " FAIL ", " WARN "


def mask(number: str) -> str:
    """Enough to recognise a number, not enough to publish one."""
    return number[:4] + "…" + number[-4:] if number and len(number) > 8 else number


async def main(send_to: str | None) -> int:
    sid = settings.twilio_account_sid
    problems = settings.comms_credential_problems
    failures = 0

    print("SwasthSetu — Twilio preflight\n")
    print(f"COMMS_MODE = {settings.comms_mode}")
    if settings.comms_mode != "live":
        print(
            f"{WARN} Not in live mode. Nothing below changes that — set "
            "COMMS_MODE=live in .env and on the host to actually send."
        )
    if problems:
        for p in problems:
            print(f"{BAD} {p}")
        return 1
    if not sid:
        print(f"{BAD} TWILIO_ACCOUNT_SID is empty")
        return 1

    root = f"{API}/Accounts/{sid}"
    async with httpx.AsyncClient(timeout=20) as client:

        # 1. Which credential actually authenticates. Both are tried, because a
        # wrong API key fails in a way that looks identical to a wrong token.
        print("\n--- credentials ---")
        working: tuple[str, str] | None = None
        pairs = [("account SID + auth token", (sid, settings.twilio_auth_token))]
        if settings.twilio_api_key_sid:
            pairs.insert(
                0,
                (
                    "API key SID + secret",
                    (settings.twilio_api_key_sid, settings.twilio_api_key_secret),
                ),
            )
        for label, auth in pairs:
            reply = await client.get(f"{root}.json", auth=auth)
            if reply.status_code == 200:
                print(f"{OK} {label}")
                working = working or auth
            else:
                code = reply.json().get("code", reply.status_code)
                print(f"{BAD} {label} — Twilio {code}")
                failures += 1
        if working is None:
            print("\nNo credential authenticates. Nothing else can be checked.")
            return 1
        if settings.twilio_api_key_sid and working != (
            settings.twilio_api_key_sid,
            settings.twilio_api_key_secret,
        ):
            print(
                f"{WARN} The API key is configured but does not work, and sending "
                "prefers it. Either regenerate the key in the Twilio console, or "
                "remove TWILIO_API_KEY_SID and TWILIO_API_KEY_SECRET so the "
                "account token is used."
            )
            failures += 1

        account = (await client.get(f"{root}.json", auth=working)).json()
        kind = account.get("type", "")
        print(f"\n--- account ---\n{OK} {account.get('friendly_name')} · {kind} · {account.get('status')}")

        # 2. A trial account is the single most common reason a correct
        # integration sends nothing.
        trial = kind.lower() == "trial"
        if trial:
            print(
                f"{WARN} Trial account: messages may only be sent to numbers "
                "verified in the console, and each one carries a "
                '"Sent from your Twilio trial account" prefix.'
            )

        balance = (await client.get(f"{root}/Balance.json", auth=working)).json()
        amount = float(balance.get("balance", 0) or 0)
        line = f"{balance.get('balance')} {balance.get('currency')}"
        print(f"{OK if amount > 0 else WARN} balance: {line}")
        if amount <= 0 and not trial:
            print("       A paid account with no balance cannot send.")
            failures += 1

        # 3. Something to send FROM.
        print("\n--- sender numbers ---")
        owned = (
            await client.get(f"{root}/IncomingPhoneNumbers.json", auth=working)
        ).json().get("incoming_phone_numbers", [])
        if not owned:
            print(f"{BAD} This account owns no phone numbers.")
            print("       Buy or claim one in the Twilio console, then set")
            print("       TWILIO_SMS_NUMBER and TWILIO_VOICE_NUMBER to it.")
            failures += 1
        owned_numbers = {n.get("phone_number") for n in owned}
        for n in owned:
            caps = ",".join(k for k, v in (n.get("capabilities") or {}).items() if v)
            print(f"{OK} {mask(n.get('phone_number', ''))} · {caps}")
            # A number with no webhook cannot receive anything, which is half
            # the omnichannel story.
            for field, label in (("sms_url", "SMS"), ("voice_url", "Voice")):
                url = n.get(field)
                print(f"       {label:5} webhook: {url or '(NOT SET — inbound will not arrive)'}")

        configured = settings.twilio_sms_number
        if configured and owned_numbers and configured not in owned_numbers:
            print(
                f"{BAD} TWILIO_SMS_NUMBER ({mask(configured)}) is not a number "
                "this account owns. Sending from it will be rejected."
            )
            failures += 1

        wa = settings.twilio_whatsapp_number
        if wa:
            sandbox = "14155238886" in wa
            print(
                f"{OK if sandbox else WARN} WhatsApp from {mask(wa)}"
                + (
                    " · Twilio's shared sandbox — works on a trial account, but "
                    "each recipient must first send the sandbox join code."
                    if sandbox
                    else " · a registered sender, which needs business approval."
                )
            )

        # 4. Where it may send TO.
        if trial:
            print("\n--- verified destinations (trial only) ---")
            ids = (
                await client.get(f"{root}/OutgoingCallerIds.json", auth=working)
            ).json().get("outgoing_caller_ids", [])
            if not ids:
                print(f"{BAD} No verified numbers. A trial account can send to nobody.")
                print("       Verify a handset in the Twilio console first.")
                failures += 1
            for i in ids:
                print(f"{OK} {mask(i.get('phone_number', ''))}")

        # 5. The only step that spends money.
        if send_to:
            print(f"\n--- sending to {mask(send_to)} ---")
            reply = await client.post(
                f"{root}/Messages.json",
                data={
                    "To": send_to,
                    "From": settings.twilio_sms_number,
                    "Body": "SwasthSetu preflight — if you can read this, live SMS works.",
                },
                auth=working,
            )
            if reply.status_code < 400:
                body = reply.json()
                print(f"{OK} accepted · sid {body.get('sid')} · status {body.get('status')}")
            else:
                body = reply.json()
                print(f"{BAD} Twilio {body.get('code')}: {body.get('message')}")
                print(f"       {body.get('more_info', '')}")
                failures += 1

    print("\n" + ("Ready to send." if not failures else f"{failures} problem(s) above."))
    return 1 if failures else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--send",
        metavar="E164",
        help="actually send one SMS to this number (costs money; trial "
        "accounts must have verified it first)",
    )
    raise SystemExit(asyncio.run(main(parser.parse_args().send)))
