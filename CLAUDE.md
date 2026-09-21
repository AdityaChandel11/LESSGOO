# SwasthSetu — working instructions

## PROJECT
Federated AI platform for medicine, bed and staffing visibility across India's PHC/CHC network: live national map, demand forecasting, stock-out early warning, cross-district redistribution with human approval, and shared model training across state silos without moving raw rows.
Stack actually in use: FastAPI + SQLAlchemy 2 async + asyncpg on PostgreSQL 17 (no PostGIS), Alembic; React 19 + Vite + TypeScript + Tailwind v4 with direct Leaflet; Flower 1.37 (FedProx, deployment engine) + PyTorch LSTM, published to a `forecasts` table the API only reads; OR-Tools min-cost-flow with a greedy fallback; Gemini behind `LLM_MODE`.
Five deliverables to submit: public repo · 3–5 min demo video · 10–12 slide deck · 2–3 line description · any live deployed link.

## ALWAYS-LOADED CONTEXT
@SPEC_DIGEST.md
@HACKATHON_RULES.md
@federated-health-brief.md
@data-trust-layer.md

## JUDGING
Weights live in HACKATHON_RULES.md → "ADDENDUM: OFFICIAL JUDGING CRITERIA" (25 / 20 / 20 / 20 / 15). Read it before any scope decision; do not restate the weights elsewhere.

## READ BEFORE YOU BUILD
Trigger table. These files are big — do **not** `@`-import them. Load only the listed section, only when the trigger applies.

| Before working on | Read |
|---|---|
| FL training, silos, aggregation | masterbuildspec-v3.md §12.2, §27, §28 Phase B; masterbuildspec-FINAL.md §9.2 (hand-rolled baseline) |
| Trust score, anomaly detection, audit queue | v3 §12.6, §9.1, §26; research.md Red Team §D |
| Medicine movement ledger (dispatch vs receipt) | v3 §26.3 |
| Dashboard, map, UI, live updates | v3 §8, §16; FINAL §12 (page spec) |
| Gemini integration (bed photo, briefings) | v3 §17, §26.2, §26.4 |
| Maps / Twilio / SMS / WhatsApp / IVR / voice | v3 §13, §14, §14.3, §15, §17, §28 Phase C; HACKATHON_RULES.md §1 (Maps and Twilio are optional, not compliance) |
| Data seeding, synthetic generator | v3 §10, §9.1; FINAL §7 (original version) |
| Redistribution / OR-Tools / outbreak surge | v3 §12.3, §12.5; FINAL §9.3 |
| Deploy, hosting, infra | HACKATHON_RULES.md §1 **first** (Cloud Run is not mandated), then v3 §28 Phase E, §16 |
| Testing, eval harness, demo rehearsal | v3 §23, §28 Phase D; implementation.md (build order and checkpoints) |

- **KEYS.md**: holds variable names and setup steps only. Never `@`-import it, never quote from it, never copy a value out of it.
- **Which spec is current**: v3 opens "Supersedes v2"; FINAL self-identifies as "v2 (FINAL)", so v3 reads as later. **Not confirmed canonical — ask Aditya before treating either as authoritative**, and read SPEC_DIGEST.md's conflict list first.
- **Spec vs code drift, verified**: v3 §4 specifies SSE (`sse-starlette`, `/stream`); the code polls a durable `events` table instead, because a stream dies at the 60-second proxy timeout. FINAL specifies PostGIS, react-leaflet and plain JS; the code uses haversine SQL, direct Leaflet and TypeScript. The code is right; ask before "fixing" it to match a spec.
- **Status lines are stale**: `main` is at `542d8dc`. SPEC_DIGEST.md §5 and v3 §28 report Phase B complete and Phase C/D partly built; that code was deliberately removed. Verify against git before trusting any "done".

## SKILLS & TOOLS
- **Superpowers (v6.4.1, enabled)** — use it for all non-trivial work: `brainstorming` / `writing-plans` before multi-file changes, `test-driven-development` for backend logic, `systematic-debugging` the moment a test fails, `requesting-code-review` and `verification-before-completion` before calling any stage done.
- **Project skills in `.claude/skills/` (15)**: python-patterns, python-testing, fastapi-patterns, api-design, contract-first, database-migrations, postgres-patterns, react-patterns, react-performance, react-testing, accessibility, error-handling, git-workflow, healthcare-phi-compliance, healthcare-cdss-patterns.
  Use the two healthcare skills for anything touching patient or clinical data. Note the standing invariant: this system holds **no patient-level data at all** — keep it that way.
- **UI look** — official Indian government health portal: navy/gray palette, plain type, flat surfaces. No gradients, no glow, no purple. Accessible contrast (see the `accessibility` skill), English plus Hindi labels. `frontend-design` (plugin, enabled) is available for UI work, but this palette and tone are fixed and override its default "make it distinctive" instinct.
- The full ECC plugin is **disabled** for this project; only the 15 skills above are in play. claude-mem is not installed. Install nothing new without asking.
- **State which skill you are using at the start of each task.**

## WORKING AGREEMENT
- Before implementing any feature: name the spec section that governs it, read that section, and quote the rule being applied. If the spec is silent or self-contradicting, **stop and ask** — do not pick a reading and proceed.
- Describe the plan before any multi-file change, unless told "go ahead directly".
- Run the relevant tests before calling anything complete. `cd backend && .venv/Scripts/python -m pytest -q`.
- Touch only what was asked for. No opportunistic refactors, renames or "while I was in there" fixes.
- **Keys protocol** (full version: HACKATHON_RULES.md §3): before each stage, list every key, credential, project ID, endpoint and URL that stage needs, with exact env var names, and ask for all of them up front. If something unanticipated is needed mid-task, stop that task at that step and ask. Never substitute a placeholder, fake value or silent mock and present it as real. Never mark a stage complete if any part of it leaned on a stand-in — say plainly which part did.
- Never print, log or commit a secret. Secrets live only in `.env` (gitignored) or the host's secret settings, never in frontend code. If a key is pasted in chat, write it to `.env` and do not repeat it back.
- Label synthetic data as synthetic — in the README, the deck and the demo. Impact numbers are either cited from a public source or labelled an estimate; never invented.
- Google AI must do load-bearing work: if removing Gemini would not break or visibly weaken a core feature, say so and propose how to make it load-bearing.

## THE DEPLOYED DATABASE — SIZE GUARD
Render's free Postgres is 1 GB and is **deleted 30 days after creation**. The
deployed database is reached only through `RENDER_DATABASE_URL_EXTERNAL`, and
only through `python -m scripts.remote`, which prints the target host, refuses
a local one, and needs `--confirm` to write. `--show`, `--counts` and
`--diagnose` are read-only and need no confirmation.

- **Render's dashboard percentage is the authority. Stop at 90%.**
- **Bulk writes to Render are forbidden.** No seed, no backfill, no batch
  insert, no reindex, without Aditya asking for that specific run. The
  2026-09-21 reseed was the last one; it hit 90% and a suspension warning.
- **Never run an unbounded read path against Render.** A query with no
  facility, district or date bound can spill tens of megabytes of temp
  files for as long as it runs, on a volume that has ~100 MB free. Full
  measurements in docs/STORAGE_NOTES.md.
- Conversion, when only one number is in hand: `dashboard ≈ measured × 1.29`.
- **`pg_database_size` is not the number.** It measured 667 MB on 2026-09-21
  while the dashboard read 83.87%. It counts one database's relations; the
  gauge counts a filesystem, which also carries the other databases on the
  instance (~15 MB), the write-ahead log (`max_wal_size` 128 MB, `min_wal_size`
  80 MB, no archiving, no replication slots — so it is recyclable, not
  permanent), temp files during index builds, and the server's own logs.
  Treat `--counts` as a floor, never as the reading.
- **You cannot see the dashboard. Ask Aditya for the percentage** before any
  remote write and again after it, and record both. Never infer it.
- **Stop and ask** if a step would take it over 90%, and say which option you
  would take and what it frees.
- **Never reseed Render without asking first.** The seed truncates.
- Nothing on the deployed app writes continuously: no background task, no
  scheduler, no cron service, no auto-seed. Growth only comes from someone
  using the app. Keep it that way — if a background writer is ever added, it
  needs a retention policy in the same commit.
- The app reads a **short window**: 28 days for the burn rate, 14 for trust, 60
  for movements. Forecasts are pre-computed rows. Nothing reads deep history.
- **What actually frees space, cheapest first.** Verified 2026-09-21:
  1. **Wait.** WAL recycles toward `min_wal_size` once writes stop, and no
     replication slot is pinning it. Costs nothing, risks nothing, and may be
     the whole gap. `CHECKPOINT` needs a role we do not have.
  2. **`DROP INDEX`.** Frees the file immediately, needs no free space, and is
     reversible. `stock_readings` carries 332 MB of indexes against 253 MB of
     table; `ix_stock_readings_facility_id` (25 MB) is a strict prefix of
     `ix_readings_facility_sku_time` and therefore redundant.
  3. **`TRUNCATE` then reseed smaller** (`--days 28 --focus-days 60`).
     TRUNCATE releases the file at once, so the low point comes before the
     rebuild, not after — but the rebuild is the whole deploy path again
     (seed, publish forecasts for 36 regions, trust, federation rounds,
     `scripts.users`) and a failure halfway leaves a broken demo.
  4. **A new instance.** Half an hour, and resets the 30-day clock.
- **What does not work here.** `DELETE` alone never shrinks a Postgres file,
  and `VACUUM FULL` rewrites the table, so it needs free space equal to the
  table it is rewriting — 585 MB for `stock_readings`, which does not exist on
  a disk at 85%. Dead tuples are currently negligible, so plain `VACUUM` has
  nothing to reclaim either.
- **Never reload the deployed database from the local one.** Local predates
  `1ddbedd`: 34 regions and 3,496 facilities against Render's correct 36 and
  3,510. Copying rows across would replace right data with stale data and
  break facility foreign keys.

## AT THE END OF EVERY FINISHED STAGE
In this order, no exceptions:
1. Run the full check suite and every unit test. A stage with a failing test is not finished.
2. Scan the diff and the tree for keys, tokens, passwords, DSNs and salts. If a real one is found, stop and say so — never print the value.
3. Commit: one commit per feature, unrelated changes kept apart, nothing half-done included. Never `.env`, `KEYS.md`, database dumps or any real secret.
4. Tag the stage (`phase-b-complete`, `phase-c-complete`, …) and push the commits and the tag. Normal push, never `--force`.
5. `backup-before-phase-c-removal` stays local and is never pushed.

## AFTER EVERY STAGE, REPORT
1. Status of each of the 5 submission items.
2. Whether Google AI is live in the real end-to-end flow and demo-able (not decorative).
3. Which tests were run, with results.
4. What is blocked on Aditya.
5. One-line self-score — weak / okay / strong — against each of the five judging criteria, with the biggest gap for each.
