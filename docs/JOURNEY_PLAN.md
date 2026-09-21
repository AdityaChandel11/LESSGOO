# Judge journey — remaining plan

Stage 1 (README, cold-start gate, synthetic label): `4e9b29c`, `1813056`.
Stage 2 (public landing at `/`): `f4b0d1f`.
Stage 3 (live loop, bed photo, federation replay, two bugs): `84d682a`,
`06a85b8`, `eb67323`, `5855b41`, `c23ebe9`.

**Open on Aditya: the Gemini free tier allows 20 requests per day per model**
(`GenerateRequestsPerDayPerProjectPerModel-FreeTier`, limit 20, confirmed from
Google's own 429 body). Rehearsals, the demo recording and judges clicking the
deployed link all draw on the same 20. Either enable billing on the AI Studio
project, or run the deployed link on `LLM_MODE=mock` and show the live path
only in the video.
Branch `feature/judge-journey`. Nothing merges to `main` without "ship it".

## Decisions (Aditya, 2026-09-21)

- **Tour writes stay in the Nashik sandbox** (32 facilities, MH/Nashik), with a
  one-command reset. Drift is confined to one district.
- **No limited tour role**; **D2 outbreak skipped**; **Hindi is the briefing
  toggle only** — there is no i18n layer and 121 Devanagari strings are inline.
- **Do not reseed the local database.** It predates `1ddbedd` and reports 34
  states; the demo is recorded on Render, which is seeded correctly.
- Government palette is fixed: navy/gray, flat, one type family, no gradients.

## Stage 3 — the live loop (replaces the guided tour)

A judge picks a Nashik facility, clicks **Simulate a stock-out**, and watches
the whole chain run. Every step carries a timestamp, the HTTP method and path
actually called, and the value that came back. No step is staged or hard-coded;
the quantity that triggers the stock-out is computed from that facility's own
burn rate so it lands under the 3-day line rather than being a magic number.

| # | Step | Endpoint (all existing) |
|---|---|---|
| 1 | Reading committed | `POST /api/stock/readings` |
| 2 | Days of stock recomputed | same response: `days_of_stock` |
| 3 | Map dot flips to critical | `status_before → status_after`, map re-reads `GET /api/map/facilities` |
| 4 | Optimiser proposes a transfer | `POST /api/transfers/plan` → donor, `route_km`, ETA, solver, rationale |
| 5 | Judge approves | `POST /api/transfers/{id}/approve` |
| 6 | Stock moves | `GET /api/facilities/{id}` — donor debited, batch dispatched |
| 7 | Activity feed logs each step | `GET /api/events` (already polled) |

`backend/scripts/reset_nashik.py` — refuses any scope but MH/Nashik, deletes
the readings, bed reports, transfers, approvals and movements the demo created,
recomputes `facility_sku_states` through `services.refresh_facility_state`, and
re-runs the MH plan. Prints what it removed.

## Stage 3b — bed photo through live Gemini

`LLM_MODE=live` currently **rejects** the bed panel's `simulate` payload (422),
so the panel is dead in live mode. The browser will instead draw the ward
whiteboard on a canvas — the same board `scripts/ward_photo.py` already draws,
carrying today's real rotating code — and post it as `image_base64` to the
existing `POST /api/facilities/{id}/bed-reports`. Gemini reads the bed counts
*and* the code back out of the image; the panel shows exactly what the model
returned: model name, counts, `code_read`, confidence, notes, and which checks
passed. Labelled as a synthetic board, never as a photograph. `/client-config`
gains `llm_mode` so the UI knows which path it is on; the mock buttons stay for
`LLM_MODE=mock`, per the both-paths-tested rule (§1.4).

## Stage 3c — federation replay

"Replay the run" steps through the 9 recorded rounds of run
`5699775115329553857` (FedProx, mu=0.01): the error curve draws round by round,
the per-silo table updates, and facility rows transmitted stays 0 throughout.
Labelled **"Replay of the recorded run, not a new training."** Reduced-motion
jumps straight to the end. The four silos are highlighted on the map (BR, KL,
MH, UP), and the down-weighting note is derived, not written in: Bihar's trust
is 0.389 against Kerala's 0.863, so its 33,918 windows count as 13,194.

## Stage 3d — two bugs

- **Audit queue scope.** `api.trustQueue()` sends no `state`, so an admin gets a
  national list while the heading names whatever state the map is over — hence
  "Gujarat" above Patna, Coimbatore and Berhampur. Pass the active state and
  title it from the same value; "India" when there is no active state.
- **Sticky Field-reports footer.** `ActivityFeed`'s root has no `shrink-0`, so
  the flex column crushes it and its content overlaps the bed panel's
  "Reported by phone call" button. Confirm in the browser before fixing.

## Stage 4 — Gemini district briefings (~5h)

One call returns `{"en": …, "hi": …}`, cached in a `district_briefings` table
keyed by district and a hash of the inputs, lazily generated, precomputed for
the demo districts, with a deterministic fallback. Text call lives in
`vision.py`, the only file allowed to reach Gemini.

## Stage 5 — activity feed and "last updated" (~3h)

Read-only over the existing `events` table, which self-prunes (2 days, swept
every 500 rows). No background writer. ~6 KB per judge session.
