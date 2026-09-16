"""The ingestion spine — spec Section 13.

Every channel produces a `RawSubmission` and exactly one pipeline processes it.
SMS, WhatsApp, IVR and the web form differ in how a message arrives and how the
answer is delivered; they do not differ in what a report means, what may be
committed, or who is allowed to say it. Duplicating that logic per channel is
how three channels become three subtly different systems.

    dedupe -> identify -> extract -> resolve -> validate -> commit -> confirm

Two rules run through all of it.

**Nothing is ever silently dropped.** An unparseable message gets a reply with
an example. A number nobody recognises gets a reply saying how to register. A
field worker who sends a report into silence concludes the system is broken and
stops reporting — and that, not model accuracy, is what kills deployments.

**The phone number is never stored.** It is normalised, hashed with the
server's salt, and only the hash and a masked form (+91••••1234) are kept.
"""

from __future__ import annotations

import hashlib
import hmac
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal

# Aliased: this module defines its own `process()` for the pipeline, and the
# collision would silently shadow the matcher.
from rapidfuzz import fuzz
from rapidfuzz import process as fuzzy
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from . import events, movements, services
from .config import settings
from .models import (
    Facility,
    FacilityContact,
    MedicineMovement,
    OutboundMessage,
    Sku,
    StockReading,
)

CHANNELS = ("sms", "whatsapp", "ivr", "ussd")
# Below this a fuzzy match is a guess, not a match, and the sender is asked
# rather than having a medicine chosen for them. 72 catches "zink" for ZINC
# and "paracetmol" for PARA500, which are the misspellings that actually
# arrive, while leaving an unrelated word unmatched.
SKU_MATCH_FLOOR = 72


def normalise_phone(raw: str) -> str:
    """Strip everything a keypad or a gateway might add, keep the digits."""
    digits = re.sub(r"\D", "", raw or "")
    # Indian numbers arrive as 9876543210, 919876543210 or +91 98765 43210.
    if len(digits) > 10 and digits.startswith("91"):
        digits = digits[2:]
    return digits


def hash_phone(raw: str) -> str:
    return hmac.new(
        settings.phone_salt.encode(), normalise_phone(raw).encode(), hashlib.sha256
    ).hexdigest()


def mask_phone(raw: str) -> str:
    digits = normalise_phone(raw)
    return f"+91{'•' * 4}{digits[-4:]}" if len(digits) >= 4 else "+91••••"


def demo_number(facility_id: str, role: str = "reporter") -> str:
    """A stable, fake handset for a facility, derived from its id.

    Demo only. It means the simulator can offer a number to type without the
    platform ever having stored one, and the same facility always gets the
    same number so a demo can be rehearsed.
    """
    digest = hashlib.sha256(f"{facility_id}:{role}".encode()).hexdigest()
    return "9" + str(int(digest[:12], 16))[:9].zfill(9)


# ============================================================== the message ===


@dataclass(frozen=True)
class RawSubmission:
    """What arrived, before anyone has decided what it means."""

    channel: str
    sender_ref: str          # raw from the gateway; hashed immediately below
    external_id: str | None  # Twilio SID and the like — the idempotency key
    text: str | None = None
    media_url: str | None = None
    audio_url: str | None = None
    received_at: datetime | None = None


@dataclass
class Command:
    """One instruction parsed out of a message."""

    kind: str  # reading | beds | checkin | receipt | approve | help | unknown
    sku_text: str | None = None
    qty: float | None = None
    target_id: int | None = None
    raw: str = ""


@dataclass
class Outcome:
    """What the pipeline did, and what the sender is told."""

    accepted: bool
    reply: str
    facility_id: str | None = None
    facility_name: str | None = None
    committed: list[dict] = field(default_factory=list)
    duplicate: bool = False
    needs_registration: bool = False


# ================================================================= grammar ===

_PAIR = re.compile(
    r"([A-Za-zऀ-ॿ][A-Za-zऀ-ॿ .]*?)[\s:=\-_]*(\d+(?:\.\d+)?)"
)


def parse(text: str) -> list[Command]:
    """Read a message typed on a numeric keypad.

    Deliberately forgiving: `ORS 60 ZINC 20`, `ORS60`, `ors-60` and the Hindi
    alias all mean the same thing. Strictness here costs reports, and a report
    that never arrives is worth less than one that needed a guess.
    """
    raw = (text or "").strip()
    if not raw:
        return [Command(kind="unknown", raw=raw)]

    upper = raw.upper()
    first = upper.split()[0] if upper.split() else ""

    if first in ("HELP", "?", "MENU"):
        return [Command(kind="help", raw=raw)]
    if first in ("IN", "OUT"):
        return [Command(kind="checkin", sku_text=first.lower(), raw=raw)]
    if first == "BEDS":
        digits = re.findall(r"\d+", raw)
        return [Command(kind="beds", qty=float(digits[0]) if digits else None, raw=raw)]
    if first in ("APPROVE", "REJECT"):
        digits = re.findall(r"\d+", raw)
        return [
            Command(
                kind="approve" if first == "APPROVE" else "reject",
                target_id=int(digits[0]) if digits else None,
                raw=raw,
            )
        ]
    if first in ("GOT", "RECEIVED", "RECD"):
        # GOT B2609-004176 480  — confirming a delivery from the ledger.
        batch = re.search(r"\b([A-Z]{1,4}\d{2,6}-\d{3,8})\b", upper)
        digits = re.findall(r"\b(\d+(?:\.\d+)?)\b", raw)
        return [
            Command(
                kind="receipt",
                sku_text=batch.group(1) if batch else None,
                qty=float(digits[-1]) if digits else None,
                raw=raw,
            )
        ]

    pairs = [
        Command(kind="reading", sku_text=name.strip(" .:-"), qty=float(qty), raw=raw)
        for name, qty in _PAIR.findall(raw)
        if name.strip(" .:-")
    ]
    return pairs or [Command(kind="unknown", raw=raw)]


def resolve_sku(text: str, catalogue: dict[str, list[str]]) -> tuple[str | None, float]:
    """Match what someone typed against the medicine list and its aliases.

    Returns (sku_code, confidence 0-1). Below the floor the caller asks rather
    than guesses: committing the wrong medicine is worse than one more SMS.
    """
    if not text:
        return None, 0.0
    needle = text.strip().lower()
    for code, aliases in catalogue.items():
        if needle == code.lower() or needle in {a.lower() for a in aliases}:
            return code, 1.0

    lookup: dict[str, str] = {}
    for code, aliases in catalogue.items():
        lookup[code.lower()] = code
        for alias in aliases:
            lookup[alias.lower()] = code

    # fuzz.ratio, deliberately, not WRatio. WRatio scores partial matches
    # generously, which means a short alias hiding inside an unrelated word
    # scores well — "tractor" came out as ORS at 72. Whole-string similarity
    # still forgives the misspellings people actually send and refuses words
    # that merely happen to share letters.
    match = fuzzy.extractOne(needle, list(lookup), scorer=fuzz.ratio)
    if match and match[1] >= SKU_MATCH_FLOOR:
        return lookup[match[0]], round(match[1] / 100, 2)
    return None, round((match[1] / 100) if match else 0.0, 2)


# ================================================================ pipeline ===


HELP_TEXT = (
    "Send stock like: ORS 60 ZINC 20. "
    "Other commands: BEDS 12 · IN · OUT · GOT <batch> <qty> · APPROVE <id> · HELP"
)


async def _catalogue(session: AsyncSession) -> dict[str, list[str]]:
    rows = (await session.execute(select(Sku.code, Sku.aliases))).all()
    return {code: list(aliases or []) for code, aliases in rows}


async def _already_seen(session: AsyncSession, external_id: str | None) -> bool:
    """A retried webhook must not double-count a facility's stock."""
    if not external_id:
        return False
    return (
        await session.scalar(
            select(StockReading.id).where(StockReading.channel_msg_id == external_id)
        )
    ) is not None


async def _reply(
    session: AsyncSession, submission: RawSubmission, body: str, masked: str
) -> str:
    """Queue the answer on the channel it came from.

    Recorded even in simulator mode: the confirmation is part of the report,
    and a channel that cannot show what it told the sender cannot be audited.
    """
    session.add(
        OutboundMessage(
            to_ref=masked, channel=submission.channel, body=body, status="queued"
        )
    )
    return body


async def process(session: AsyncSession, submission: RawSubmission) -> Outcome:
    """Run one inbound message through the spine."""
    now = submission.received_at or datetime.now(timezone.utc)
    masked = mask_phone(submission.sender_ref)
    phone_hash = hash_phone(submission.sender_ref)

    # 1. dedupe
    if await _already_seen(session, submission.external_id):
        return Outcome(
            accepted=True,
            duplicate=True,
            reply=await _reply(
                session, submission, "Already recorded — thank you.", masked
            ),
        )

    # 2. identify
    contact = await session.get(FacilityContact, phone_hash)
    if contact is None or not contact.is_active:
        return Outcome(
            accepted=False,
            needs_registration=True,
            reply=await _reply(
                session,
                submission,
                "This number is not registered for a health centre. "
                "Ask your district officer to register it, then send HELP.",
                masked,
            ),
        )
    facility = await session.get(Facility, contact.facility_id)
    if facility is None:
        return Outcome(
            accepted=False,
            reply=await _reply(
                session, submission, "Your facility record is missing. Contact your district officer.",
                masked,
            ),
        )
    contact.last_seen_at = now

    # 3. extract
    commands = parse(submission.text or "")
    if commands and commands[0].kind == "help":
        return Outcome(
            accepted=True,
            facility_id=facility.id,
            facility_name=facility.name,
            reply=await _reply(session, submission, HELP_TEXT, masked),
        )
    if commands and commands[0].kind == "unknown":
        return Outcome(
            accepted=False,
            facility_id=facility.id,
            facility_name=facility.name,
            reply=await _reply(
                session,
                submission,
                f"Sorry, could not read that. {HELP_TEXT}",
                masked,
            ),
        )

    catalogue = await _catalogue(session)
    committed: list[dict] = []
    problems: list[str] = []

    for index, command in enumerate(commands):
        if command.kind == "reading":
            # 4. resolve
            code, confidence = resolve_sku(command.sku_text or "", catalogue)
            if code is None:
                problems.append(
                    f"'{command.sku_text}' is not a medicine I recognise"
                )
                continue
            # 5. validate
            if command.qty is None or command.qty < 0:
                problems.append(f"{code} needs a quantity")
                continue
            if confidence < settings.channel_confidence_floor:
                problems.append(
                    f"did you mean {code} for '{command.sku_text}'? Send it again spelled that way"
                )
                continue

            # 7. commit — one reading, with the provenance of how it arrived
            session.add(
                StockReading(
                    facility_id=facility.id,
                    sku_code=code,
                    qty_on_hand=Decimal(str(command.qty)),
                    reported_at=now,
                    source=submission.channel if submission.channel != "ussd" else "sms",
                    reporter_ref=f"phone:{phone_hash[:16]}",
                    # Only the first command carries the gateway's id: it is one
                    # message, and the unique index must not reject the rest.
                    channel_msg_id=submission.external_id if index == 0 else None,
                    confidence=Decimal(str(confidence)),
                    raw_payload={"text": submission.text, "matched": command.sku_text},
                )
            )
            committed.append({"kind": "reading", "sku_code": code, "qty": command.qty})

        elif command.kind == "receipt":
            outcome = await _confirm_delivery(session, facility, command, phone_hash, submission)
            (committed if outcome["ok"] else problems).append(
                outcome["detail"] if outcome["ok"] else outcome["detail"]
            )

        else:
            problems.append(
                f"'{command.raw.split()[0]}' is not ready yet on this channel"
            )

    if not committed:
        return Outcome(
            accepted=False,
            facility_id=facility.id,
            facility_name=facility.name,
            reply=await _reply(
                session,
                submission,
                f"Nothing recorded: {'; '.join(problems)}. {HELP_TEXT}",
                masked,
            ),
        )

    # 8. recompute, 9. emit
    await session.flush()
    await services.refresh_facility_state(session, facility.id)
    snapshots = await services.get_snapshots(session, [facility.id])
    snapshot = snapshots[0] if snapshots else None

    readings = [c for c in committed if isinstance(c, dict) and c.get("kind") == "reading"]
    for item in readings:
        days = next(
            (
                s.days_of_stock
                for s in (snapshot.skus if snapshot else [])
                if s.sku_code == item["sku_code"]
            ),
            None,
        )
        item["days_of_stock"] = days
        await events.record(
            session,
            events.READING_COMMITTED,
            {
                "facility_id": facility.id,
                "facility_name": facility.name,
                "sku_code": item["sku_code"],
                "qty_on_hand": item["qty"],
                "source": submission.channel,
                "confidence": 1.0,
                "days_of_stock": days,
                "reporter_ref": masked,
            },
            state_silo=facility.state_silo,
        )

    # 10. confirm — on the same channel, always
    return Outcome(
        accepted=True,
        facility_id=facility.id,
        facility_name=facility.name,
        committed=[c for c in committed if isinstance(c, dict)],
        reply=await _reply(session, submission, _confirmation(committed, problems), masked),
    )


def _confirmation(committed: list, problems: list[str]) -> str:
    """The sentence the sender actually receives.

    Days of cover, not just an echo of the number: the point of reporting is to
    learn something back, and "3 days left" is what makes the next report feel
    worth sending.
    """
    parts = []
    for item in committed:
        if isinstance(item, str):
            parts.append(item)
            continue
        days = item.get("days_of_stock")
        cover = f", {days:.0f} days left" if isinstance(days, (int, float)) else ""
        parts.append(f"{item['sku_code']} {item['qty']:g}{cover}")
    line = "Recorded: " + "; ".join(parts)
    if problems:
        line += f". Not recorded: {'; '.join(problems)}"
    return line[:320]


async def _confirm_delivery(
    session: AsyncSession,
    facility: Facility,
    command: Command,
    phone_hash: str,
    submission: RawSubmission,
) -> dict:
    """GOT <batch> <qty> — the receipt half of the ledger, over a phone."""
    if not command.sku_text:
        return {"ok": False, "detail": "which batch? Send GOT <batch> <qty>"}
    movement = await session.scalar(
        select(MedicineMovement).where(
            MedicineMovement.batch_id == command.sku_text,
            MedicineMovement.to_facility == facility.id,
        )
    )
    if movement is None:
        return {"ok": False, "detail": f"batch {command.sku_text} is not expected here"}
    if command.qty is None:
        return {"ok": False, "detail": f"how many arrived for {command.sku_text}?"}
    try:
        row, _ = await movements.confirm_receipt(
            session,
            movement.id,
            qty_received=command.qty,
            via=submission.channel if submission.channel != "ussd" else "sms",
            by_ref=f"phone:{phone_hash[:16]}",
        )
    except movements.ReceiptError as exc:
        return {"ok": False, "detail": str(exc)}
    return {
        "ok": True,
        "detail": f"{row.batch_id} {command.qty:g} {row.unit} received ({row.status})",
    }
