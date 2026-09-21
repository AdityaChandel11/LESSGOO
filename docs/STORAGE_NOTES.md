# Storage on the deployed database

Render's free Postgres is 1 GB, on a volume shared with everything else the
server keeps. On 2026-09-21 it reached 90% and Render warned that the account
could be suspended. These are the measurements behind the rules in CLAUDE.md,
so the rules can be argued with rather than just obeyed.

## Two numbers that are both right

| | reads | counts |
|---|---|---|
| `pg_database_size` | 667 MB | one database's relations |
| Render's dashboard | 83.87% (~860 MB) | the filesystem |

The difference is not an error. The volume also carries the other databases on
the instance (`template1` + `postgres`, ~15 MB), the write-ahead log
(`max_wal_size` 128 MB, `min_wal_size` 80 MB, no archiving, no replication
slots), temporary files while queries hold them, and the server's own logs.

**Render's percentage is the authority.** A working conversion, from the one
time both numbers were read together: `dashboard ≈ measured × 1.29`.

## Where the 667 MB is

| relation | total | heap | indexes |
|---|---|---|---|
| `stock_readings` | 585 MB | 253 MB | **332 MB** |
| `bed_reports` | 18 MB | 14 MB | 4 MB |
| `bed_status` | 15 MB | 7 MB | 8 MB |
| everything else | ~49 MB | | |

2,478,909 readings spanning 2026-05-25 to 09-21; 1,769,709 in the last 60 days,
1,179,369 in the last 28. Dead tuples are negligible, so `VACUUM` has nothing
to reclaim and `VACUUM FULL` would need 585 MB of free space that does not
exist.

## A read can fill the disk

`work_mem` is **1703 kB** and `temp_file_limit` is **-1 (uncapped)**. A sort
larger than `work_mem` spills to a temporary file, which lives as long as its
query does. Measured against the deployed database:

| path | time | temp spilled |
|---|---|---|
| map summary, state/district rollups | 1.0–1.8s | none |
| facilities in view (2,500 pins) | 2.2s | none |
| facility panel, one facility | 2.4s | none |
| federation inspector | 1.1s | none |
| trust queue, national | 46.2s | none |
| transfer plan, MH (read half) | 5.0s | none |
| **days of stock, every facility** | **never finished (>300s)** | **~70 MB per attempt** |

The last one is `services.get_snapshots(session)` with no facility filter,
behind `GET /api/facilities`. It reads every reading in a 29-day window for all
3,510 facilities — about 1.18M rows — and only then filters by district, state
or status in Python. Two concurrent calls would put ~140 MB of temp on a volume
with ~100 MB free. It never returns, so a caller retries, which is how the
concurrency arrives.

Two things now stand between that and a full disk: the query resolves its
filters before reading (`hotfix/facilities-filter`), and every connection
carries `statement_timeout = 30s` so a runaway statement is cancelled by the
server — which is what releases its temp files. A client-side timeout would
abandon the request and leave the query running.

`temp_file_limit` cannot be set: it is superuser-only, and the deployed role
(`swasthsetu_db_wonv_user`) is not one — `pg_ls_waldir()` is refused for the
same reason.

## What frees space, cheapest first

1. **Wait.** WAL recycles toward `min_wal_size` once writes stop, and no
   replication slot is pinning it. Costs nothing.
2. **`DROP INDEX`.** Releases the file at once and needs no free space.
   `ix_stock_readings_facility_id` was a strict prefix of
   `ix_readings_facility_sku_time` (6 scans against 293) — 25 MB, dropped in
   migration `e4d7a9c31b52`.
3. **`TRUNCATE` then reseed smaller.** TRUNCATE releases the file immediately,
   so the low point comes *before* the rebuild, not after. That is what makes
   it safe on a nearly-full disk — but the rebuild is the whole deploy path
   again, and failing halfway leaves a broken demo.
4. **A new instance.** Half an hour, and resets the 30-day deletion clock.

Never `DELETE` and hope: it does not shrink a Postgres file. Never reload the
deployed database from the local one: local predates `1ddbedd` at 34 regions
and 3,496 facilities, against Render's correct 36 and 3,510.
