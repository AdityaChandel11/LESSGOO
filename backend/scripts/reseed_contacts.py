"""Re-hash the phone registry against the salt this process is using.

Why this exists. `facility_contacts` stores only a salted hash of each demo
handset, never the number. The hash is computed with `PHONE_HASH_SALT`, so a
registry seeded under one salt is unreadable by a service running under
another: every inbound message resolves to nobody and the ingestion spine
answers "this number is not registered to a facility". That is precisely what
happened — the deployed database was seeded from a laptop whose `.env` carried
no salt, so the rows were hashed with `DEV_PHONE_SALT` while the deployed
service hashes with its own. `render.yaml` warns about this in as many words:
rotating the salt orphans every registered handset.

**Scope: `facility_contacts` and nothing else.** Two rows per facility, the
same two this table has always held. No readings, no movements, no forecasts,
no facilities — the tables a full reseed truncates are not touched and not
read. There is nothing here that a failure halfway could corrupt: the table is
derived entirely from `facilities.id`, so re-running it is safe and produces
the identical result.

**Size: net zero.** The same row count is written back, roughly 2.4 MB of
table and index on the deployed database. The delete leaves dead tuples that
autovacuum reclaims; it allocates no new pages beyond what the table already
has.

    python -m scripts.remote --confirm -- -m scripts.reseed_contacts --dry-run
    python -m scripts.remote --confirm -- -m scripts.reseed_contacts --write

`--dry-run` reads and counts and changes nothing, so the target and the numbers
can be checked before anything is written. `--write` is the only mode that
touches a row.

The salt is never printed. Its fingerprint is — a short hash of the salt — so
that the value used here can be compared against the value the service uses
without either being revealed.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib

from sqlalchemy import delete, func, select, text

from app import ingest
from app.config import DEV_PHONE_SALT, settings
from app.db import SessionLocal
from app.models import Facility, FacilityContact

ROLES = ("reporter", "supervisor")


def salt_fingerprint() -> str:
    """Eight characters that identify a salt without disclosing it."""
    return hashlib.sha256(settings.phone_salt.encode("utf-8")).hexdigest()[:8]


async def main_async(write: bool) -> int:
    using_dev = settings.phone_salt == DEV_PHONE_SALT
    print("  salt fingerprint : {0}".format(salt_fingerprint()))
    print("  salt source      : {0}".format(
        "DEV_PHONE_SALT (PHONE_HASH_SALT is not set)" if using_dev
        else "PHONE_HASH_SALT from the environment"
    ))
    if using_dev:
        print()
        print("  REFUSING: this process is using the development salt.")
        print("  Re-hashing the deployed registry with it would orphan every")
        print("  handset all over again. Set PHONE_HASH_SALT to the value the")
        print("  deployed service uses and run this again.")
        return 2

    async with SessionLocal() as session:
        facility_ids = [
            r[0] for r in (await session.execute(select(Facility.id).order_by(Facility.id))).all()
        ]
        before = int(await session.scalar(select(func.count()).select_from(FacilityContact)) or 0)

        expected = len(facility_ids) * len(ROLES)
        print()
        print("  facilities       : {0:,}".format(len(facility_ids)))
        print("  contacts now     : {0:,}".format(before))
        print("  contacts after   : {0:,}  ({1} per facility)".format(expected, len(ROLES)))

        # A sample, so the fingerprints can be eyeballed against the service.
        if facility_ids:
            sample = facility_ids[0]
            number = ingest.demo_number(sample, "reporter")
            print("  sample facility  : {0}".format(sample))
            print("  sample masked    : {0}".format(ingest.mask_phone(number)))
            print("  sample hash head : {0}...".format(ingest.hash_phone(number)[:12]))

        if not write:
            print()
            print("  --dry-run: nothing was written.")
            return 0

        rows = [
            FacilityContact(
                phone_hash=ingest.hash_phone(ingest.demo_number(fid, role)),
                facility_id=fid,
                masked=ingest.mask_phone(ingest.demo_number(fid, role)),
                role=role,
                language="en",
                is_active=True,
            )
            for fid in facility_ids
            for role in ROLES
        ]

        # One statement, one table, no cascade. `facility_contacts` has no
        # dependents, so this cannot reach anything else.
        deleted = (await session.execute(delete(FacilityContact))).rowcount
        session.add_all(rows)
        await session.commit()

        after = int(await session.scalar(select(func.count()).select_from(FacilityContact)) or 0)
        print()
        print("  deleted          : {0:,}".format(deleted or 0))
        print("  written          : {0:,}".format(len(rows)))
        print("  contacts now     : {0:,}".format(after))

        # Prove the registry answers the numbers the simulator offers.
        checked = 0
        for fid in facility_ids[:3]:
            for role in ROLES:
                found = await ingest.identify(session, ingest.demo_number(fid, role))
                ok = found is not None and found.facility_id == fid
                print("  resolves {0:<26} {1:<11} {2}".format(fid, role, "yes" if ok else "NO"))
                checked += 1 if ok else 0
        print("  verified         : {0} of {1} sampled handsets resolve".format(
            checked, min(3, len(facility_ids)) * len(ROLES)
        ))
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dry-run", action="store_true", help="read and report; write nothing")
    group.add_argument("--write", action="store_true", help="replace facility_contacts")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main_async(write=args.write)))


if __name__ == "__main__":
    main()
