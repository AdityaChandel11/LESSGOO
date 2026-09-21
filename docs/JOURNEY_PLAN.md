# Judge journey — remaining plan

Stage 1 (README, cold-start gate, synthetic label) is done: `4e9b29c`, `1813056`.
Branch `feature/judge-journey`. Nothing merges to `main` without "ship it".

## Decisions (Aditya, 2026-09-21)

- **Tour writes are scoped to a Nashik sandbox.** Every action the tour takes
  touches Maharashtra / Nashik only, so drift stays in one district.
- **Transfers are pre-created as intra-Nashik plans.** `can_plan_state` refuses
  `block_mo`, so `nashik.ddlo` cannot generate a plan — but `can_decide_transfer`
  lets it approve one where donor and recipient are both in Nashik. The tour
  therefore approves a plan prepared in advance rather than solving one live.
- **No limited tour role.** `nashik.ddlo@demo.swasthsetu.in` is enough.
- **A reset script restores the sandbox** in one command (Stage 3).
- **D2 outbreak is skipped.** Tour step 5 shows the real early stock-out
  warning instead. Docs say "emergency surge = roadmap".
- **Hindi is the briefing toggle only.** No full-UI i18n: there is no i18n
  layer and 121 Devanagari strings are hardcoded inline.

## Stage 2 — landing at `/` (~4h)

Unauthenticated route. Live map hero pulling real `/api/map/summary`, so the
headline number is never hardcoded. Three numbered steps (report → forecast →
approve) — numbering earns its place because it is a real sequence. "Try the
demo" uses the existing `/auth/demo`, which already works. Sign-in demoted to a
secondary link. Palette stays fixed: navy/gray, flat, one type family.

## Stage 3 — guided tour (~6h)

Dismissible rail, five steps, each firing a real request and naming the
endpoint it called. No fake or hard-coded results.

1. SMS ingest — `POST /api/ingest/simulate`
2. Ward photo through Gemini — `POST /api/facilities/{id}/bed-reports`, needs
   today's rotating code from `/api/facilities/{id}/bed-code`
3. Early stock-out warning — the real 653 critical facilities
4. Approve a pre-created intra-Nashik transfer — `POST /api/transfers/{id}/approve`
5. Federation inspector — `GET /api/federation/inspector`, read-only

Plus `scripts/reset_tour.py`: one command, Nashik only, restores stock, removes
tour readings and bed reports, and re-creates the pending transfers.

## Stage 4 — Gemini district briefings (~5h)

New text path in `vision.py` — the only file allowed to reach Gemini (spec
§1.3). One call returns `{"en": …, "hi": …}`, halving the calls to ~157 for a
full precompute; ~6 for the demo districts. New `district_briefings` table plus
migration, keyed by district and a hash of the inputs, so a cached briefing
expires exactly when the numbers behind it change. Deterministic non-LLM
fallback when Gemini fails, per the §1.4 both-paths-tested rule. Precompute the
demo districts so the tour never waits on a model.

## Stage 5 — activity feed and "last updated" (~3h)

Read-only over the existing `events` table, which already self-prunes
(`RETENTION = 2 days`, sweeping every 500 rows). No background writer. Adds a
couple of event kinds on tour actions. Growth is about 6 KB per judge session,
so a thousand sessions cost ~6 MB against 333 MB of headroom.
