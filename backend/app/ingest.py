"""The ingestion spine — spec 13.

Every channel produces a `RawSubmission`, and exactly one pipeline processes
it: dedupe, identify, extract, resolve, validate, score, commit, recompute,
emit, confirm. SMS, WhatsApp, IVR and the field client are thin adapters that
build a submission and hand it here; none of them may write a reading
themselves, or the provenance and the trust signals would differ by channel.

Two rules from the spec drive most of the design:

  * "Anything unparseable gets a helpful reply with an example, never a silent
    drop." A worker on a numeric keypad cannot be expected to match a grammar,
    so the parser is forgiving and the reply teaches the format.

  * "Confirmation is mandatory on every channel." Someone who reports into
    silence stops reporting, and that failure mode kills deployments more
    reliably than model error does. Every path out of here produces a reply
    that states what was recorded and how many days of cover it leaves.

The number itself is never stored: a salted HMAC to match on and a masked form
to show, per spec 9.1.
"""

from __future__ import annotations

import hashlib
import hmac
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal

from rapidfuzz import fuzz
from rapidfuzz import process as fuzzy
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from . import attendance, beds, events, movements, redistribution, services, vision
from .config import settings
from .models import Facility, FacilityContact, MedicineMovement, Sku, StockReading

CHANNELS = ("sms", "whatsapp", "ivr", "form", "voice")

# Above this a fuzzy SKU match is accepted. Tuned with `fuzz.ratio`, not
# WRatio: WRatio's partial matching scored unrelated words like "tractor"
# against ORS highly enough to commit a reading against the wrong medicine.
SKU_MATCH_FLOOR = 72

# "ORS 60", "ORS60", "ORS-60", "ओआरएस 60" all mean the same thing on a keypad.
#
# This is read token by token rather than by one pattern, because a single
# pattern cannot tell "ORS60" from "PARA500". Both are letters followed by
# digits, but the first is a medicine and a quantity while the second is a
# medicine whose own name carries its dose — PARA500 is a real SKU code, and
# "Paracetamol 500mg" is a real SKU name. What separates them is what comes
# next, so the parser has to be able to look.
_TOKENS = re.compile(r"[\s:=\-_,;]+")
_NUMBER = re.compile(r"^\d+(?:\.\d+)?$")
_GLUED = re.compile(r"^(.*[A-Za-zऀ-ॿ.])(\d+(?:\.\d+)?)$")

HELP_TEXT = (
    "Send: medicine and quantity, e.g. ORS 60 ZINC 20. "
    "Also BEDS 12, IN or OUT for attendance, GOT <batch> <qty> to confirm a delivery."
)


def normalise_phone(raw: str) -> str:
    """Strip everything a keypad or a provider might add, keep the digits."""
    digits = re.sub(r"\D", "", raw or "")
    # Indian numbers arrive as 10 digits, +91-prefixed, or 0-prefixed.
    if len(digits) > 10:
        digits = digits[-10:]
    return digits


def hash_phone(raw: str) -> str:
    return hmac.new(
        settings.phone_salt.encode(), normalise_phone(raw).encode(), hashlib.sha256
    ).hexdigest()


def mask_phone(raw: str) -> str:
    digits = normalise_phone(raw)
    return "+91" + "•" * max(0, len(digits) - 4) + digits[-4:] if digits else "unknown"


def demo_number(facility_id: str, role: str) -> str:
    """A stable fake handset per facility, so the demo has numbers to type.

    Derived, never stored: the registry holds only the hash of this.
    """
    seed = hashlib.sha256(f"{facility_id}:{role}".encode()).hexdigest()
    return "9" + str(int(seed[:12], 16))[:9].rjust(9, "7")


@dataclass
class RawSubmission:
    """What every channel adapter builds, and the only thing the spine reads."""

    channel: str
    sender_ref: str
    external_id: str
    text: str | None = None
    media: bytes | None = None
    media_mime: str = "image/jpeg"
    # The provider's reference to its own copy of the media. The bytes are
    # read once, in memory, and never stored: only this string is.
    media_ref: str | None = None
    received_at: datetime | None = None


@dataclass
class Reading:
    sku_code: str
    sku_name: str
    qty: float
    days_of_stock: float | None = None
    status: str | None = None


@dataclass
class Outcome:
    """What happened, and what the sender is told about it."""

    accepted: bool
    reply: str
    stage: str
    facility_id: str | None = None
    masked_sender: str | None = None
    readings: list[Reading] = field(default_factory=list)
    actions: list[str] = field(default_factory=list)
    duplicate: bool = False


# ----------------------------------------------------------- 1. dedupe ---


def reading_key(external_id: str, sku_code: str) -> str:
    """The dedupe key stored on a reading.

    One message can carry several medicines, and `channel_msg_id` is unique,
    so the provider's id alone cannot be the key. Suffixing the SKU keeps one
    row per medicine per message while still making a provider retry
    impossible to double-count.
    """
    return "{0}#{1}".format(external_id, sku_code)


async def _already_seen(session: AsyncSession, external_id: str) -> bool:
    if not external_id:
        return False
    found = await session.scalar(
        select(StockReading.id)
        .where(
            (StockReading.channel_msg_id == external_id)
            | StockReading.channel_msg_id.like(external_id + "#%")
        )
        .limit(1)
    )
    return found is not None


# --------------------------------------------------------- 2. identify ---


async def identify(session: AsyncSession, sender_ref: str) -> FacilityContact | None:
    return await session.scalar(
        select(FacilityContact).where(
            FacilityContact.phone_hash == hash_phone(sender_ref),
            FacilityContact.is_active.is_(True),
        )
    )


# ------------------------------------------------- 3 & 4. extract, resolve ---


def parse_pairs(text: str) -> list[tuple[str, float]]:
    """Every name-and-number pair in the message, in the order they appear.

    One rule, applied left to right: a token that is only digits is the
    quantity for whatever name has been collected so far. A token that ends in
    digits is split into name and quantity *only when nothing numeric follows
    it* — because if a number does follow, those digits belong to the medicine
    and the number after them is the quantity.

    That is what makes "ORS60" sixty sachets of ORS and "PARA500 120" a
    hundred and twenty of PARA500, from the same reader.
    """
    tokens = [t for t in _TOKENS.split(text or "") if t]
    pairs: list[tuple[str, float]] = []
    name: list[str] = []

    for i, token in enumerate(tokens):
        following = tokens[i + 1] if i + 1 < len(tokens) else None

        if _NUMBER.match(token):
            if name:
                pairs.append((" ".join(name).strip(), float(token)))
                name = []
            continue

        glued = _GLUED.match(token)
        if glued and not (following and _NUMBER.match(following)):
            name.append(glued.group(1))
            pairs.append((" ".join(name).strip(), float(glued.group(2))))
            name = []
            continue

        name.append(token)

    return pairs


def resolve_sku(needle: str, lookup: dict[str, str]) -> tuple[str | None, int]:
    """Fuzzy-match a typed medicine name to a SKU code, with its score."""
    if not needle:
        return None, 0
    key = needle.strip().lower()
    if key in lookup:
        return lookup[key], 100
    match = fuzzy.extractOne(key, list(lookup), scorer=fuzz.ratio)
    if match and match[1] >= SKU_MATCH_FLOOR:
        return lookup[match[0]], int(match[1])
    return None, int(match[1]) if match else 0


async def sku_lookup(session: AsyncSession) -> dict[str, str]:
    """Every code, name and alias, lowercased, pointing at its SKU code."""
    lookup: dict[str, str] = {}
    for sku in (await session.execute(select(Sku))).scalars():
        lookup[sku.code.lower()] = sku.code
        lookup[sku.name.lower()] = sku.code
        for alias in sku.aliases or []:
            lookup[str(alias).lower()] = sku.code
    return lookup


# ------------------------------------------------------ the spine itself ---


async def process(session: AsyncSession, submission: RawSubmission) -> Outcome:
    """Run one submission through all ten stages. Always replies."""
    now = submission.received_at or datetime.now(timezone.utc)
    masked = mask_phone(submission.sender_ref)

    if submission.channel not in CHANNELS:
        return Outcome(False, "Unknown channel.", "channel", masked_sender=masked)

    # 1. dedupe — a provider retry must not double-count a reading.
    if await _already_seen(session, submission.external_id):
        return Outcome(
            True,
            "Already recorded — thank you.",
            "dedupe",
            masked_sender=masked,
            duplicate=True,
        )

    # 2. identify
    contact = await identify(session, submission.sender_ref)
    if contact is None:
        return Outcome(
            False,
            "This number is not registered to a facility. Ask your district officer to add it.",
            "identify",
            masked_sender=masked,
        )
    facility_id = contact.facility_id
    contact.last_seen_at = now

    # 3. a photograph, before the text check — a WhatsApp message can carry a
    # ward photo and no words at all, and refusing it for being wordless would
    # throw away the one submission that can actually verify itself.
    if submission.media:
        facility = await session.get(Facility, facility_id)
        try:
            extraction = await vision.read_ward_photo(
                submission.media, submission.media_mime
            )
        except vision.VisionError as exc:
            # The photo is not stored and nothing is recorded. An unread photo
            # is not a bed count, and inventing one from the caption would be
            # exactly the fabrication this pipeline exists to prevent.
            return Outcome(
                False,
                "Could not read that photo — {0}. Send it again with the day's "
                "code clearly visible.".format(exc),
                "vision",
                facility_id,
                masked,
            )
        report, checks = await beds.record_report(
            session,
            facility,
            extraction,
            ward="general",
            source=submission.channel,
            # A phone channel proves no location. A check that could not run
            # must never be stored as one that passed.
            loc_method=None,
            loc_lat=None,
            loc_lng=None,
            loc_accuracy_m=None,
            register_admissions=None,
            # A reference to the provider's copy, never the bytes. Twilio hosts
            # the media; one inbound photo inlined here would be ~200 KB of
            # base64 in a JSONB column, on a 1 GB volume.
            media_ref=submission.media_ref,
            reported_at=now,
        )
        await session.commit()
        verdict = {
            "verified": "verified against today's code",
            "unverified": "recorded but not verified",
            "rejected": "rejected",
        }.get(report.verification, report.verification)
        return Outcome(
            report.verification != "rejected",
            "Ward photo {0}: {1} of {2} beds occupied.".format(
                verdict, extraction.beds_occupied, extraction.beds_total
            ),
            "photo",
            facility_id,
            masked,
            actions=["photo:{0}".format(report.verification)],
        )
    text = (submission.text or "").strip()
    if not text:
        return Outcome(False, HELP_TEXT, "extract", facility_id, masked)

    upper = text.upper()
    if upper.startswith("HELP"):
        return Outcome(True, HELP_TEXT, "help", facility_id, masked)

    # --- commands that are not stock readings ---------------------------
    if upper in ("IN", "OUT"):
        action = upper.lower()
        facility = await session.get(Facility, facility_id)
        # A phone channel carries no location it can prove, so the check-in is
        # stored with loc_method set to the channel and no geofence result —
        # a check that could not run must never look like one that passed.
        await attendance.record_checkin(
            session,
            facility,
            staff_ref=contact.phone_hash,
            action=action,
            shift=None,
            source=submission.channel,
            lat=None,
            lng=None,
            loc_method=submission.channel,
            cell_id=None,
            at=now,
        )
        await session.commit()
        return Outcome(
            True,
            f"Checked {action} at {facility.name}. Location not verified on this channel.",
            "attendance",
            facility_id,
            masked,
            actions=[f"checkin:{action}"],
        )

    if upper.startswith("BEDS"):
        pairs = parse_pairs(text)
        if not pairs:
            return Outcome(False, "Send BEDS followed by the occupied count, e.g. BEDS 12.",
                           "extract", facility_id, masked)
        occupied = int(pairs[0][1])
        facility = await session.get(Facility, facility_id)
        # Typed, not photographed: no code to read back and no location, so
        # this cannot verify. It is stored saying exactly that.
        extraction = vision.BedExtraction(
            beds_total=facility.beds_total,
            beds_occupied=occupied,
            code_read=None,
            confidence=settings.channel_confidence_floor,
            notes=f"typed over {submission.channel}",
            model="typed",
        )
        report, checks = await beds.record_report(
            session,
            facility,
            extraction,
            ward="general",
            source=submission.channel,
            loc_method=None,
            loc_lat=None,
            loc_lng=None,
            loc_accuracy_m=None,
            register_admissions=None,
            media_ref=None,
            reported_at=now,
        )
        await session.commit()
        return Outcome(
            True,
            f"Bed occupancy {occupied} of {facility.beds_total} recorded, "
            f"unverified — send a ward photo with today's code to verify.",
            "beds",
            facility_id,
            masked,
            actions=[f"beds:{occupied}:{report.verification}"],
        )

    if upper.startswith("GOT"):
        parts = text.split()
        if len(parts) < 3:
            return Outcome(False, "Send GOT <batch id> <quantity received>.",
                           "extract", facility_id, masked)
        batch, qty_text = parts[1], parts[2]
        try:
            qty = Decimal(qty_text)
        except Exception:
            return Outcome(False, "Send GOT <batch id> <quantity received>.",
                           "extract", facility_id, masked)
        movement = await session.scalar(
            select(MedicineMovement).where(
                MedicineMovement.batch_id == batch,
                MedicineMovement.to_facility == facility_id,
            )
        )
        if movement is None:
            return Outcome(
                False,
                f"No consignment {batch} is expected at this facility.",
                "receipt",
                facility_id,
                masked,
            )
        try:
            row, _ = await movements.confirm_receipt(
                session,
                movement.id,
                qty_received=qty,
                via=submission.channel,
                by_ref=contact.phone_hash,
                received_at=now,
            )
        except movements.ReceiptError as exc:
            await session.rollback()
            return Outcome(False, str(exc), "receipt", facility_id, masked)
        await session.commit()
        settled = {
            movements.RECEIVED: "matches what was sent",
            movements.SHORT: "is short of what was sent",
            movements.OVER: "is more than was sent",
        }.get(row.status, row.status)
        return Outcome(
            True,
            f"Receipt for {batch} recorded: {qty_text} {settled}. Stock updated.",
            "receipt",
            facility_id,
            masked,
            actions=[f"receipt:{batch}:{row.status}"],
        )

    if upper.startswith("APPROVE"):
        parts = text.split()
        if contact.role != "supervisor":
            return Outcome(
                False,
                "Only a supervisor's number can approve a transfer.",
                "authorise",
                facility_id,
                masked,
            )
        if len(parts) < 2 or not parts[1].isdigit():
            return Outcome(False, "Send APPROVE followed by the transfer number.",
                           "extract", facility_id, masked)
        try:
            transfer = await redistribution.decide_transfer(
                session,
                int(parts[1]),
                "approved",
                actor_ref=f"phone:{contact.phone_hash[:12]}",
                actor_role="supervisor",
                channel=submission.channel,
            )
        except redistribution.TransferConflict as exc:
            await session.rollback()
            return Outcome(False, str(exc), "approve", facility_id, masked)
        except Exception:
            await session.rollback()
            return Outcome(
                False,
                f"Transfer {parts[1]} could not be approved — check the number.",
                "approve",
                facility_id,
                masked,
            )
        await session.commit()
        return Outcome(
            True,
            f"Transfer {parts[1]} approved. Dispatch can proceed.",
            "approve",
            facility_id,
            masked,
            actions=[f"approve:{parts[1]}:{getattr(transfer, 'status', 'approved')}"],
        )

    # --- 3, 4, 5: stock readings ----------------------------------------
    pairs = parse_pairs(text)
    if not pairs:
        return Outcome(
            False,
            "Could not read that. " + HELP_TEXT,
            "extract",
            facility_id,
            masked,
        )

    lookup = await sku_lookup(session)
    committed: list[Reading] = []
    unknown: list[str] = []
    for name, qty in pairs:
        code, score = resolve_sku(name, lookup)
        if code is None:
            unknown.append(name)
            continue
        if qty < 0:
            unknown.append(name)
            continue
        # 6 & 7: commit with its provenance, so the trust layer can see which
        # channel said what and how sure the match was.
        session.add(
            StockReading(
                facility_id=facility_id,
                sku_code=code,
                qty_on_hand=Decimal(str(qty)),
                reported_at=now,
                source=submission.channel,
                reporter_ref=contact.phone_hash,
                channel_msg_id=(
                    reading_key(submission.external_id, code)
                    if submission.external_id
                    else None
                ),
                confidence=Decimal(str(round(score / 100, 2))),
                raw_payload={"text": text, "matched": name, "score": score},
            )
        )
        committed.append(Reading(sku_code=code, sku_name=name, qty=qty))

    if not committed:
        return Outcome(
            False,
            "Medicine not recognised: " + ", ".join(unknown[:3]) + ". " + HELP_TEXT,
            "resolve",
            facility_id,
            masked,
        )

    await session.flush()

    # 8. recompute — the map moves because this reading landed, not on a timer.
    await services.refresh_facility_state(session, facility_id)
    snapshots = await services.get_snapshots(session, [facility_id])
    cover = {
        line.sku_code: (line.days_of_stock, line.status)
        for snap in snapshots
        for line in snap.skus
    }
    for reading in committed:
        days, status = cover.get(reading.sku_code, (None, None))
        reading.days_of_stock = days
        reading.status = status

    # 9. emit — the dashboard repaints from the same event log everything else
    # polls, rather than this channel getting a private notification path.
    await events.record(
        session,
        events.READING_COMMITTED,
        {
            "facility_id": facility_id,
            "channel": submission.channel,
            "sender": masked,
            "skus": [r.sku_code for r in committed],
        },
    )

    # 10. confirm — days of cover, because that is the number that tells
    # someone whether to act, and a bare "ok" does not.
    parts = []
    for r in committed:
        if r.days_of_stock is None:
            parts.append(f"{r.sku_code} {r.qty:g}")
        else:
            parts.append(f"{r.sku_code} {r.qty:g} ({r.days_of_stock:.0f} days)")
    reply = "Recorded: " + ", ".join(parts) + "."
    if unknown:
        reply += " Not recognised: " + ", ".join(unknown[:2]) + "."
    return Outcome(True, reply, "committed", facility_id, masked, readings=committed)
