# Render operations — run by hand, in this order

Fix list Tier 0 ops: #30 → #81 → #11r → #79 repair. Aditya runs these; an agent never
does. Every step is a dry run first. Run from `backend/` on Windows.

`scripts.remote` refuses to start a child unless `PHONE_HASH_SALT` and `JWT_SECRET` are set
to the deployed service's values (shell or `.env`), and prints only their fingerprints.
Its `--confirm` only lets the child reach the deployed database; each script below writes
nothing without its **own** `--confirm`.

**Size guard (CLAUDE.md):** read Render's dashboard % before step 1 and after step 4, and
record both. Stop at 90%.

## 1. Roll Nashik forward to today (#30)

```
.venv\Scripts\python -m scripts.remote --confirm -- -m scripts.roll_forward
.venv\Scripts\python -m scripts.remote --confirm -- -m scripts.roll_forward --confirm
```

The dry run prints the days it will move, the rows per table, and the expected growth as
a share of the gauge. Readings: the last G seeded days are copied forward and the oldest G
deleted in one transaction (flat). Ward reports, check-ins, pings, warehouse dispatches and
codes shift G days. A second run the same day does nothing. Checked against the local
database inside a rolled-back transaction: 2,688 readings copied / 2,688 trimmed, ≈ 2.1 MB,
no row lands in the future.

Seeded ward reports end the day before the seed, so after the roll-forward Nashik's bed
figure is 1–2 days old and reads "Stale" (#68). A fresh verified ward photo fixes that; it
is the demo, not a defect.

## 2. Republish the forecasts (#81)

Needs the PyTorch environment. Run after step 1, so Nashik's windows end today.

```
.venv\Scripts\python -m scripts.remote --python C:\Users\prath\phc-fl\.venv\Scripts\python.exe --cwd federation --env-name SWASTHSETU_DATABASE_URL --confirm -- publish_forecast.py --dry-run
.venv\Scripts\python -m scripts.remote --python C:\Users\prath\phc-fl\.venv\Scripts\python.exe --cwd federation --env-name SWASTHSETU_DATABASE_URL --confirm -- publish_forecast.py
```

Size-flat: one upsert per facility × medicine (`ON CONFLICT … DO UPDATE`). A published
forecast is used for `forecast_max_age_days` (8) days, then every card falls back to the
burn rate on its own. **Freshness decision: manual, before judging** — no scheduler exists
or may exist. Re-run this step if judging is more than 8 days after it.

## 3. Withdraw the photographed bill at Nashik PHC 1 (#11r)

```
.venv\Scripts\python -m scripts.remote --confirm -- -m scripts.repair_readings bill
.venv\Scripts\python -m scripts.remote --confirm -- -m scripts.repair_readings bill --confirm
```

Writes only when exactly one reading matches (Paracetamol, 10, via photo, not yet
withdrawn). The reading is superseded, not deleted, with a note naming the fix.

## 4. Withdraw test reports written outside the sandbox (#79)

```
.venv\Scripts\python -m scripts.remote --confirm -- -m scripts.repair_readings test-reports
.venv\Scripts\python -m scripts.remote --confirm -- -m scripts.repair_readings test-reports --confirm
```

ORS 4 over SMS from a `test_…` reporter, since the 21 Sept reseed, anywhere but Nashik
(e.g. Port Blair PHC 1). Superseded, not deleted; the map's rows are rebuilt for those
centres only.

## Then deploy

Deploy the code only after step 1: #26 (as-of-now cover) would otherwise show Nashik's
cards as "Count overdue".
