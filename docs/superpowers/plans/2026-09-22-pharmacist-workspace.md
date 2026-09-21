# PHC Pharmacist Workspace — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A signed-in `facility_user` lands on their own facility, on a phone, and can see what they hold, when it runs out, where to get more, and can request it, track it and confirm it arrived.

**Architecture:** A second front-end shell (`frontend/src/workspace/`) mounted from `AuthGate` for `role === "facility_user"`, rendering at 360px first. Driven entirely by `session.user.facility_id`; no facility is named in code. The backend adds one module of bounded queries and pure functions, four read routes and one write route. The write route creates an ordinary `Transfer`, so the existing approve → dispatch → confirm chain carries the rest.

**Tech Stack:** FastAPI + SQLAlchemy 2 async + asyncpg, PostgreSQL 17. React 19 + Vite + TypeScript + Tailwind v4, direct Leaflet.

**Spec:** `docs/JOURNEY_PLAN.md`; `masterbuildspec-v3.md` §12.3 (redistribution, human approval), §26.3 (two-sided ledger), §12.6 (trust).

---

## Scope, as approved 2026-09-22

**In:** Tasks 1–7, plus Task 7b (storage receipt). **Using existing data only.**

**No schema change of any kind.** Tasks 1–7 need no Alembic migration: facility requests ride on `Transfer.triggered_by`, an existing nullable `Text` column (`models.py:420`) that today only ever holds `"threshold"`. The first Render deploy is therefore code-only as a consequence of the scope, not as a workaround — there is no migration to hold back, no table to create, no seed to run.

**Parked until Aditya says go:** expiry (`medicine_movements.expiry_date`), the `facility_briefings` table, Task 8 (Gemini "what to do today"), Task 9 (batch seeding, Render push script). See "Parked" at the end — nothing in Tasks 1–7 depends on any of it.

**Sandbox reset needs no change.** `reset_nashik.py:123` already deletes every `Transfer` where `to_facility` **or** `from_facility` is a Nashik facility, regardless of `triggered_by`, along with its approvals and movements. Facility requests are already covered, including one sourced from a donor outside the district. The reset extension in the old Task 9 was only ever needed for briefings.

---

## Testing strategy — corrected against the actual suite

The original plan assumed `client` / `session` / `facility` pytest fixtures. **They do not exist.** `backend/tests/` has no `conftest.py` and no database or HTTP fixture anywhere. The suite is two things:

- **Pure-function unit tests** — `test_redistribution.py` builds `StockNode`s by hand and calls `split_roles` directly; `test_movements.py` calls `settle_status` directly.
- **AST structural tests** — `test_api_boundary.py` and `test_reset_nashik.py` parse the source and assert on its shape.

End-to-end coverage lives in **`backend/checks/`**: the real database and the real FastAPI app over an in-process ASGI transport, signing in through the real demo endpoint, creating its own `CHK`-prefixed rows and deleting them in a `finally` (`checks/harness.py`). Run with `python -m checks`.

So this stage follows both existing patterns and **adds no test dependency**:

| Layer | Where | How |
|---|---|---|
| Decisions and arithmetic | `backend/tests/test_workspace.py` | Pure functions over `StockNode` / plain args, as `test_redistribution.py` does |
| Routes, permissions, the whole loop | `backend/checks/workspace.py` | Real DB, real app, signed in as the real demo pharmacist |
| Frontend | Browser, via the preview tools | See below |

**The frontend has no test runner** — `frontend/package.json` has no vitest, no @testing-library, no `test` script, and `CLAUDE.md` says "Install nothing new without asking." So there are no frontend unit tests in this stage; the UI is verified in the browser at 360px and the logic it depends on is pure and tested in Python. Adding vitest + @testing-library + jsdom is a separate ask, flagged not taken.

**This shapes the code:** every decision goes in a pure function in `workspace.py` and the route stays a thin shim, exactly as `redistribution.py` splits `split_roles` (pure, tested) from `load_state_nodes` (DB, not unit-tested).

---

## Global Constraints

Copied verbatim from `CLAUDE.md`, `SPEC_DIGEST.md` and the approved brief. Every task implicitly includes this section.

- **No patient-level data exists in this system at all.**
- **Redistribution always ends in human approval. Nothing auto-executes.** A request creates a `proposed` transfer and nothing more.
- **Controlled-substance SKUs are excluded from the solver entirely, routed to a manual-only queue.** `MORPH` must never appear as a donor option or be requestable.
- **Never propose taking a donor below its own safety stock.** `donor_floor_days` = 14.0, applied to Find supply exactly as `split_roles` applies it.
- **The UI must never imply a real road route when it is showing a straight-line estimate.** `maps_mode` is `osm` and no Maps key is in `.env`: every distance is haversine × `road_factor` (1.3), labelled, drawn dashed.
- **Never run an unbounded read path.** Every query added here is bounded by one `facility_id` or one `(state_silo, sku_code)`. `services.get_snapshots()` with no `facility_ids` is the >300 s / ~70 MB path in `docs/STORAGE_NOTES.md` — never call it from this stage.
- **No Render writes and no deploy inside this stage. Local only.**
- **Do not alter `stock_readings`** — no columns, no indexes. (The approve path writes rows to it; that is existing behaviour, untouched.)
- **Label synthetic data as synthetic** — `DataNotice` visible on every workspace screen.
- **Government palette, existing tokens only:** `--color-brand #0b3d5c`, `--color-ink #16191d`, `--color-ink-2 #4a525c`, `--color-ink-3 #7d858f`, `--color-line #e3e6ea`, `--color-canvas #f4f5f7`, `--color-panel #ffffff`, `--color-crit #d92d20`, `--color-risk #e0900e`, `--color-ok #1b9150`. No gradients, no glow, no purple. English plus Hindi labels.
- **Never print, log, echo or commit a key.** `.env` is gitignored (`.gitignore:2`, verified). Never touch `RENDER_DATABASE_URL_EXTERNAL`.
- **No magic numbers.** Every delivery-estimate constant is a `Settings` field in `backend/app/config.py`, echoed to the client so the screen shows its own assumptions.
- **Two caps, both server-side:** `max_open_requests_per_facility` = 3 **and** `max_open_facility_requests_global` = 100.
- **Install nothing new.** No new runtime or dev dependency in this stage.

---

## Review Focus

Five conditions the brief implies but no happy path exercises. Each has its test pinned to the task that owns it.

1. **A SKU with no burn rate.** Days of cover is undefined; the screen says "not enough readings to estimate" and there is no stock-out date — not `Infinity`, not today. Task 3.
2. **A pharmacist reading a facility that is not theirs.** 403, and the other facility's name must not appear in the response. Task 2.
3. **A controlled SKU reaching Find supply.** `MORPH` goes critical like anything else; it must return zero donors and a manual-escalation reason. Task 4.
4. **The caps.** The 4th request for a facility and the 101st overall must both be refused with a message naming the limit, and both must count **only** `triggered_by='facility_request'` so the solver's own proposals never lock a pharmacist out. Task 5.
5. **A donor whose stock fell between planning and asking.** The request must be refused at creation with the floor named, not left for the officer to discover at approval. Task 5.

---

## File structure

**Backend — new**
- `backend/app/workspace.py` — pure decision functions plus bounded queries for one facility. Kept out of `services.py` (national rollups) and `redistribution.py` (the solver) so neither grows a second audience.
- `backend/tests/test_workspace.py` — pure-function tests.
- `backend/checks/workspace.py` — the end-to-end loop.

**Backend — modified**
- `backend/app/config.py` — delivery constants, the two caps.
- `backend/app/api.py` — five thin routes.
- `backend/app/auth.py` — `can_view_facility`.
- `backend/checks/__init__.py` — register the new check.

**Frontend — new**, all under `frontend/src/workspace/`
`Shell.tsx`, `Medicines.tsx`, `FindSupply.tsx`, `RequestStock.tsx`, `Orders.tsx`, `Receipt.tsx`, `labels.ts`.

**Frontend — modified**
`AuthGate.tsx:364` (one routing decision), `api.ts` (typed clients), `index.css` (one `@media print` block).

---

## Task 1: Workspace shell and role routing

**Governing rule** (`CLAUDE.md` → UI look): "official Indian government health portal: navy/gray palette, plain type, flat surfaces. No gradients, no glow, no purple." **REQUIRED SUB-SKILL: `frontend-design`**, constrained by that palette, which overrides its default instinct.

`App.tsx` is a fixed desktop layout — `<aside className="… w-[400px] shrink-0 …">` beside `<section className="relative min-w-0 flex-1">` (`App.tsx:604`, `:753`) — with one breakpoint in 894 lines (`xl:block`, `:129`). At 360px it is unusable, and retrofitting it would put a national map and five view tabs behind a phone-sized pharmacist screen.

**Files:** Create `frontend/src/workspace/Shell.tsx`, `frontend/src/workspace/labels.ts`. Modify `frontend/src/AuthGate.tsx:364`.

**Interfaces:** Produces `export default function Workspace({ session, onSignOut }: { session: Session; onSignOut: () => void })` and `export type Tab = "medicines" | "orders"`.

- [ ] **Step 1: Write `labels.ts`** — EN/HI pairs inline; there is no i18n layer (decided 2026-09-21).
- [ ] **Step 2: Write `Shell.tsx`** — single column, 16px gutter, no horizontal scroll at 360px, tap targets ≥44px. `<h1>` is the facility name from `api.facility(session.user.facility_id)`. Two-tab `role="tablist"`. `DataNotice` under the header on every tab. If `facility_id` is null (the server forbids it for this role, the type allows it), render a plain explanation and sign-out rather than crashing.
- [ ] **Step 3: Route to it** in `AuthGate.tsx`:

```tsx
  if (session.user.role === "facility_user" && session.user.facility_id) {
    return <Workspace session={session} onSignOut={signOut} />;
  }
  return <App session={session} onSignOut={signOut} />;
```

- [ ] **Step 4: Verify in the browser at 360px** — sign in as `pharmacist@demo.swasthsetu.in`, emulate 360px, confirm no horizontal scroll, the heading is the facility, the notice is visible, and an officer account still gets `App`.
- [ ] **Step 5: Commit** — `git commit -m "A pharmacist signs in to their own centre, on a phone"`

---

## Task 2: The medicine list, with how each figure was verified

**Governing rule** (`SPEC_DIGEST.md` §1): "The trust score is computed live from the same tables everything else reads — never cached, never a parallel dataset." This task reads; it computes no new trust.

`SkuStockOut` (`api.py:150`) already carries `qty_on_hand`, `days_of_stock`, `status`, `rate_source`, `last_reported_at`, `last_source`, `last_confidence`. Three of the four verification kinds are therefore already on the wire: a physical count is `last_source in ("form","voice")`, a phone report is `("sms","ivr","whatsapp")`, and trust comes from the facility payload. The missing one is **delivery confirmation** — the last settled `medicine_movements` row per SKU. Ward photos verify beds, not medicines, and stay on the facility header where `bed_occupancy_pct` already is.

**Files:** Create `backend/app/workspace.py`, `backend/tests/test_workspace.py`, `frontend/src/workspace/Medicines.tsx`. Modify `backend/app/api.py`, `backend/app/auth.py`, `frontend/src/api.ts`.

**Interfaces:**
- `GET /api/facilities/{facility_id}/workspace` → `WorkspaceOut`: the facility, plus per SKU everything in `SkuStockOut` plus `last_receipt: LastReceiptOut | None` and `stockout_on: date | None` (Task 3).
- `auth.can_view_facility(p, *, facility_id, facility_state, facility_district) -> bool` — same rules as `can_submit_reading`.
- `workspace.provenance(last_source, last_reported_at, last_receipt, now) -> Provenance` — **pure**, returns `{kind, at, detail}` where `kind` is `"counted" | "delivery" | "phone" | "none"`.

- [ ] **Step 1: Write the failing pure tests**

```python
from datetime import datetime, timedelta, timezone
from app import workspace

NOW = datetime(2026, 9, 22, 12, 0, tzinfo=timezone.utc)


def test_a_hand_count_is_reported_as_a_hand_count():
    p = workspace.provenance("form", NOW - timedelta(days=2), None, NOW)
    assert p.kind == "counted"
    assert p.days_ago == 2


def test_a_newer_delivery_outranks_an_older_count():
    receipt = workspace.LastReceipt(
        batch_id="TRF-412", qty_received=400.0,
        received_at=NOW - timedelta(days=1), received_via="form",
    )
    p = workspace.provenance("form", NOW - timedelta(days=6), receipt, NOW)
    assert p.kind == "delivery"
    assert "TRF-412" in p.detail


def test_an_older_delivery_does_not_outrank_a_newer_count():
    receipt = workspace.LastReceipt(
        batch_id="TRF-412", qty_received=400.0,
        received_at=NOW - timedelta(days=9), received_via="form",
    )
    p = workspace.provenance("form", NOW - timedelta(days=1), receipt, NOW)
    assert p.kind == "counted"


def test_a_phone_report_is_never_called_a_count():
    assert workspace.provenance("ivr", NOW, None, NOW).kind == "phone"
    assert workspace.provenance("sms", NOW, None, NOW).kind == "phone"


def test_nothing_reported_says_so_rather_than_guessing():
    assert workspace.provenance(None, None, None, NOW).kind == "none"
```

- [ ] **Step 2: Run them and watch them fail**

Run: `cd backend && .venv/Scripts/python -m pytest tests/test_workspace.py -q`
Expected: FAIL, `ModuleNotFoundError: No module named 'app.workspace'`.

- [ ] **Step 3: Add `can_view_facility` to `auth.py`**

```python
def can_view_facility(
    p: Principal, *, facility_id: str, facility_state: str, facility_district: str
) -> bool:
    """Opening a facility's own workspace carries the same scope as reporting
    for it. The national picture stays visible to every signed-in user through
    the map; this is the one screen that is somebody's own."""
    return can_submit_reading(
        p, facility_id=facility_id,
        facility_state=facility_state, facility_district=facility_district,
    )
```

- [ ] **Step 4: Write `workspace.provenance` and the bounded receipt query**

```python
async def last_receipts(session: AsyncSession, facility_id: str) -> dict[str, LastReceipt]:
    """Most recent settled delivery per SKU for one facility.

    DISTINCT ON keeps this to one index scan over ix_movements_facility_time
    rather than a query per SKU. Bounded by one facility on purpose: the
    national equivalent is the read that never returns (docs/STORAGE_NOTES.md).
    """
    rows = await session.execute(
        text("""
            SELECT DISTINCT ON (sku_code)
                   sku_code, batch_id, qty_received, received_at, received_via
            FROM medicine_movements
            WHERE to_facility = :fid AND received_at IS NOT NULL
            ORDER BY sku_code, received_at DESC
        """),
        {"fid": facility_id},
    )
    return {r[0]: LastReceipt(r[1], float(r[2]), r[3], r[4]) for r in rows.all()}
```

- [ ] **Step 5: Add the thin route to `api.py`** — resolve the facility, check `can_view_facility`, call `services.get_snapshots(session, facility_ids=[facility_id])`, merge `last_receipts`, return. On refusal: 403 "You can only open the workspace for your own facility", and **never echo the other facility's name** (Review Focus 2).
- [ ] **Step 6: Run the tests** — Expected: PASS.
- [ ] **Step 7: Write `Medicines.tsx`** — one card per medicine, worst first (same sort as `panels.tsx:431`). Name and unit, quantity, days of cover via `formatDays`, a `StatusPill` (reuse `panels.tsx:58`), and one provenance line: "Counted by hand, 2 days ago" / "Confirmed on delivery TRF-412, 1 day ago" / "Reported by phone, 4 days ago" / "Not yet reported". Trust score and band render once at the top, linking to the existing evidence drawer — not per card.
- [ ] **Step 8: Commit** — `git commit -m "Each medicine says how its number was checked, and when"`

---

## Task 3: Predicted stock-out date

**Governing rule** (the brief): "Never invent unlabelled numbers." `days_of_stock` already comes from either the 28-day burn rate or the published federated forecast, and `rate_source` already says which; the date is `last_reported_at + days_of_stock` days and the screen names the rule.

**Expiry is parked** — it needs `medicine_movements.expiry_date`, which is a migration. Nothing else in this task depends on it.

**Files:** Modify `backend/app/workspace.py`, `backend/tests/test_workspace.py`, `frontend/src/workspace/Medicines.tsx`.

**Interfaces:** `workspace.stockout_date(days_of_stock: float | None, last_reported_at: datetime | None) -> date | None` — pure.

- [ ] **Step 1: Write the failing tests**

```python
from datetime import date

def test_stockout_date_is_the_last_reading_plus_its_cover():
    at = datetime(2026, 9, 20, 6, 0, tzinfo=timezone.utc)
    assert workspace.stockout_date(4.0, at) == date(2026, 9, 24)


# Review Focus 1
def test_no_burn_rate_means_no_stockout_date():
    at = datetime(2026, 9, 20, 6, 0, tzinfo=timezone.utc)
    assert workspace.stockout_date(None, at) is None


def test_a_cover_figure_with_nothing_to_count_from_gives_no_date():
    assert workspace.stockout_date(4.0, None) is None


def test_stock_already_out_dates_to_the_reading_itself():
    at = datetime(2026, 9, 20, 6, 0, tzinfo=timezone.utc)
    assert workspace.stockout_date(0.0, at) == date(2026, 9, 20)
```

- [ ] **Step 2: Run them and watch them fail** — Expected: `AttributeError: … has no attribute 'stockout_date'`.
- [ ] **Step 3: Implement**

```python
def stockout_date(
    days_of_stock: float | None, last_reported_at: datetime | None
) -> date | None:
    """The day cover runs out, counted from the reading it was measured at.

    Absent when either input is missing. A facility with no measured usage has
    no stock-out date — not today, and not never. Absence of evidence must not
    manufacture a date, the same rule `services.classify` follows for status.
    """
    if days_of_stock is None or last_reported_at is None:
        return None
    return (last_reported_at + timedelta(days=days_of_stock)).date()
```

- [ ] **Step 4: Run the tests** — Expected: PASS.
- [ ] **Step 5: Render it** — "Runs out about 26 Sep — from the shared model's forecast" or "— from the last 28 days of readings", switched on `rate_source`. Never a bare date. When absent: "Not enough readings to estimate."
- [ ] **Step 6: Commit** — `git commit -m "When it runs out, and which rule said so"`

---

## Task 4: Find supply

**Governing rule** (`SPEC_DIGEST.md` §1): "Never propose taking a donor below its own safety stock, in either solver implementation," and "Controlled-substance SKUs are excluded from the solver entirely, routed to a manual-only queue."

This is `split_roles` read from the recipient's side: same `StockNode` list the solver uses (`load_state_nodes(session, state, sku)` — bounded to one state and one SKU, 250 rows for MH), same split, ranked by `road_km`, returning the nearest three with the cover each donor keeps.

**Files:** Modify `backend/app/workspace.py`, `backend/app/api.py`, `backend/tests/test_workspace.py`, `frontend/src/api.ts`. Create `frontend/src/workspace/FindSupply.tsx`.

**Interfaces:**
- `workspace.rank_donors(nodes, rules, *, recipient_id, sku, limit=3) -> Supply` — **pure**, over `StockNode`s exactly as `test_redistribution.py` builds them.
- `GET /api/facilities/{facility_id}/supply?sku={code}` → `SupplyOut` with `donors: list[DonorOut]{facility_id, name, district, lat, lng, km, distance_basis, spare_units, days_kept}`, `units_needed`, `manual_only`, `reason`.

- [ ] **Step 1: Write the failing tests** (reusing `RULES` and `node()` from `test_redistribution.py`'s style)

```python
# Review Focus 3
def test_a_controlled_medicine_is_never_offered_a_donor():
    s = workspace.rank_donors(
        [node("A", 10, 900, 10), node("B", 20, 900, 10), node("ME", 0, 5, 10)],
        RULES, recipient_id="ME", sku=SKU_CONTROLLED,
    )
    assert s.donors == []
    assert s.manual_only is True
    assert "controlled" in s.reason.lower()


def test_donors_are_the_nearest_three_in_distance_order():
    nodes = [node(f"D{i}", km, 900, 10) for i, km in enumerate([50, 10, 30, 20, 40])]
    nodes.append(node("ME", 0, 5, 10))
    s = workspace.rank_donors(nodes, RULES, recipient_id="ME", sku=SKU_PLAIN)
    assert [d.facility_id for d in s.donors] == ["D1", "D3", "D2"]


def test_no_donor_is_offered_below_its_own_floor():
    nodes = [node("TIGHT", 10, 140, 10), node("ME", 0, 5, 10)]   # exactly 14 days
    s = workspace.rank_donors(nodes, RULES, recipient_id="ME", sku=SKU_PLAIN)
    assert s.donors == []


def test_a_donor_reports_the_cover_it_keeps():
    nodes = [node("D", 10, 900, 10), node("ME", 0, 5, 10)]
    d = workspace.rank_donors(nodes, RULES, recipient_id="ME", sku=SKU_PLAIN).donors[0]
    assert d.spare_units == 760                      # 900 - 14*10
    assert d.days_kept == pytest.approx(14.0)
    assert d.distance_basis == "straight_line_x1.3"


def test_the_asking_facility_is_never_its_own_donor():
    nodes = [node("ME", 0, 900, 10)]
    s = workspace.rank_donors(nodes, RULES, recipient_id="ME", sku=SKU_PLAIN)
    assert all(d.facility_id != "ME" for d in s.donors)


def test_a_cold_chain_medicine_respects_the_shorter_road_limit():
    nodes = [node("FAR", 100, 900, 10), node("ME", 0, 5, 10)]   # > 60 km
    s = workspace.rank_donors(nodes, RULES, recipient_id="ME", sku=SKU_COLD)
    assert s.donors == []
```

- [ ] **Step 2: Run them and watch them fail.**
- [ ] **Step 3: Implement `rank_donors`** — controlled check first and return early; then `split_roles(nodes, rules)`; find the recipient for `units_needed`; drop self from donors; haversine × `road_factor`; apply `cold_chain_max_km` (60) for cold-chain SKUs and `max_transfer_km` (150) otherwise; sort by km; take `limit`; `days_kept = (qty - spare) / burn`.
- [ ] **Step 4: Run the tests** — Expected: PASS.
- [ ] **Step 5: Add the thin route**, bounded by `load_state_nodes(session, facility.state_silo, sku)`.
- [ ] **Step 6: Write `FindSupply.tsx`** — a sheet over the card. Three rows: name, district, "42 km — straight-line estimate, not a road route", "can spare 380 sachets and still hold 16 days". A 180px Leaflet map **below** the list at 360px, with a **dashed** line donor→facility, because a dashed line does not read as a road. No fly animation under reduced motion.
- [ ] **Step 7: Commit** — `git commit -m "Where the medicine actually is, and what the giver keeps"`

---

## Task 5: Request stock, and the two caps

**Governing rule** (`SPEC_DIGEST.md` §1): "Redistribution/outbreak always ends in human approval. **Nothing auto-executes.**"

A request creates a `Transfer` with `status="proposed"` and `triggered_by="facility_request"` — existing column, no migration. The existing chain carries it from there.

**Worth knowing before the demo:** `can_decide_transfer` (`auth.py:83`) lets a `block_mo` decide only when `from_district == to_district`. A same-district donor is approved by `nashik.ddlo@`; a cross-district donor needs `mh.officer@`. Both are existing demo accounts, so both paths are demo-able — the screen names the right approver rather than guessing.

**Files:** Modify `backend/app/config.py`, `backend/app/workspace.py`, `backend/app/api.py`, `backend/tests/test_workspace.py`, `frontend/src/api.ts`. Create `frontend/src/workspace/RequestStock.tsx`.

**Interfaces:**
- `workspace.check_caps(open_here: int, open_global: int, limits: CapLimits) -> CapVerdict` — **pure**, returns `{allowed, reason}`.
- `workspace.validate_request(donor, qty, *, sku, rules) -> None`, raising `RequestRefused(detail)` — **pure**.
- `POST /api/facilities/{facility_id}/requests` `{sku_code, from_facility, qty}` → 201 `RequestOut{transfer_id, reference, status, approver_role, km, distance_basis, eta_hours, estimated_delivery, assumptions}`.

- [ ] **Step 1: Add both caps to `config.py`**

```python
    # ---- facility-originated requests (pharmacist workspace) ----
    # Two caps, because they stop different things. The per-facility one keeps
    # any single pharmacist's queue reviewable; the global one is the database
    # size guard — a judge clicking "Request stock" is otherwise an unbounded
    # writer. Both count only triggered_by='facility_request', so the solver's
    # own proposals can never lock a pharmacist out of asking.
    max_open_requests_per_facility: int = 3
    max_open_facility_requests_global: int = 100
```

- [ ] **Step 2: Write the failing tests**

```python
LIMITS = workspace.CapLimits(per_facility=3, global_=100)

def test_a_request_is_allowed_below_both_caps():
    assert workspace.check_caps(2, 99, LIMITS).allowed is True


# Review Focus 4
def test_the_fourth_request_for_a_facility_is_refused_by_name():
    v = workspace.check_caps(3, 10, LIMITS)
    assert v.allowed is False
    assert "3" in v.reason
    assert "this centre" in v.reason.lower()


def test_the_hundred_and_first_request_overall_is_refused_by_name():
    v = workspace.check_caps(0, 100, LIMITS)
    assert v.allowed is False
    assert "100" in v.reason
    assert "limit reached" in v.reason.lower()


def test_the_per_facility_limit_is_reported_before_the_global_one():
    # Both breached: the pharmacist can act on their own queue, not the nation's.
    v = workspace.check_caps(3, 100, LIMITS)
    assert "this centre" in v.reason.lower()


# Review Focus 5
def test_a_request_that_would_breach_the_donor_floor_is_refused_at_creation():
    donor = node("D", 10, 200, 10)          # 200 units, floor is 140
    with pytest.raises(workspace.RequestRefused) as e:
        workspace.validate_request(donor, 100, sku=SKU_PLAIN, rules=RULES)
    assert "floor" in str(e.value).lower()
    assert "140" in str(e.value)


def test_a_request_at_exactly_the_floor_is_allowed():
    donor = node("D", 10, 200, 10)
    workspace.validate_request(donor, 60, sku=SKU_PLAIN, rules=RULES)   # leaves 140


def test_a_controlled_medicine_cannot_be_requested():
    donor = node("D", 10, 900, 10)
    with pytest.raises(workspace.RequestRefused):
        workspace.validate_request(donor, 10, sku=SKU_CONTROLLED, rules=RULES)


def test_a_request_below_the_minimum_transfer_is_refused():
    donor = node("D", 10, 900, 10)
    with pytest.raises(workspace.RequestRefused):
        workspace.validate_request(donor, 2, sku=SKU_PLAIN, rules=RULES)   # min is 5
```

- [ ] **Step 3: Run them and watch them fail.**
- [ ] **Step 4: Implement `check_caps` and `validate_request`**, then the route: count open requests for this facility and globally (two bounded `SELECT count(*) WHERE status='proposed' AND triggered_by='facility_request'`), check caps → 409, validate → 422, insert the `Transfer` with `route_km`, `eta_hours`, `route_source="haversine"`, `triggered_by="facility_request"`, and a `rationale` JSONB carrying the requester, the assumptions and the units needed.
- [ ] **Step 5: Run the tests** — Expected: PASS.
- [ ] **Step 6: Write `RequestStock.tsx`** — quantity pre-filled with `units_needed`, ceilinged at the donor's spare. The button says what it will and will not do: "This asks the district officer to approve. No stock moves until they do." On 409, show the cap message plainly with a link to Orders. On success: the reference and the approver's role in words.
- [ ] **Step 7: Commit** — `git commit -m "A pharmacist asks; an officer still decides"`

---

## Task 6: The delivery estimate and the printable receipt

**Governing rule** (the brief): "estimated delivery date computed from distance, labelled 'estimate', assumptions visible (constants in one config file, no magic numbers)."

Three constants already exist and are already used by the solver: `avg_speed_kmh` (35.0), `handling_hours` (0.5), `road_factor` (1.3). Two more are needed to turn hours into a **date**, and they go in the same file.

**Files:** Modify `backend/app/config.py`, `backend/app/workspace.py`, `backend/tests/test_workspace.py`, `frontend/src/index.css`. Create `frontend/src/workspace/Receipt.tsx`.

- [ ] **Step 1: Add the two constants**

```python
    # ---- delivery estimate (pharmacist workspace) ----
    # A dispatch raised after the cutoff leaves the next morning; district
    # stores do not run at night. Both are assumptions, shown on screen as
    # assumptions, never presented as a scheduled time.
    dispatch_cutoff_hour: int = 14          # local time, 24h
    working_hours_per_day: float = 8.0
```

- [ ] **Step 2: Write the failing tests**

```python
def test_the_estimate_shows_every_constant_it_used():
    est = workspace.delivery_estimate(km=42.0, raised_at=datetime(2026, 9, 22, 9, 0, tzinfo=IST))
    assert est.assumptions == {
        "avg_speed_kmh": 35.0, "handling_hours": 0.5, "road_factor": 1.3,
        "dispatch_cutoff_hour": 14, "working_hours_per_day": 8.0,
    }
    assert est.basis == "straight_line_x1.3"
    assert est.label == "estimate"


def test_a_request_raised_after_the_cutoff_leaves_the_next_day():
    early = workspace.delivery_estimate(km=42.0, raised_at=datetime(2026, 9, 22, 9, 0, tzinfo=IST))
    late = workspace.delivery_estimate(km=42.0, raised_at=datetime(2026, 9, 22, 16, 0, tzinfo=IST))
    assert late.expected_on > early.expected_on


def test_a_longer_road_never_arrives_earlier():
    near = workspace.delivery_estimate(km=20.0, raised_at=datetime(2026, 9, 22, 9, 0, tzinfo=IST))
    far = workspace.delivery_estimate(km=400.0, raised_at=datetime(2026, 9, 22, 9, 0, tzinfo=IST))
    assert far.expected_on >= near.expected_on
```

- [ ] **Step 3: Run them, implement, run them again** — Expected: FAIL, then PASS.
- [ ] **Step 4: Write `Receipt.tsx`** at `?receipt=SS-000412` — reference; both facilities with district and state; medicine and quantity; the raised / approved / dispatched timestamps that actually exist on the row; the estimate with its assumptions listed beneath it; the synthetic-data notice. A "Print" button calling `window.print()` and nothing more.
- [ ] **Step 5: Add the print stylesheet**

```css
@media print {
  .no-print { display: none !important; }
  body { background: #ffffff; }
  /* The palette is already flat and high-contrast; nothing else needs
     overriding for paper. */
}
```

- [ ] **Step 6: Commit** — `git commit -m "An estimate that shows its arithmetic, on a page that prints"`

---

## Task 7: Orders and confirm receipt

No new backend work. `GET /api/movements?to_facility=…` and `POST /api/movements/{id}/receipt` already exist and already carry the right permission (`can_submit_reading`, `api.py:919`), which a `facility_user` passes for their own facility.

**Files:** Create `frontend/src/workspace/Orders.tsx`.

- [ ] **Step 1: Write `Orders.tsx`** — open movements newest first with status, expected-by, and an overdue line derived from `expected_by` (never stored — `models.py:239`). The confirm control reuses `api.confirmReceipt` (`api.ts:325`) unchanged. A short delivery must display as short, not be silently accepted.
- [ ] **Step 2: Verify in the browser at 360px** — request → approve in a second session → confirm, and watch the quantity land.
- [ ] **Step 3: Commit** — `git commit -m "The order the pharmacist raised, tracked to the door"`

---

## Task 7b: The end-to-end check, and the storage receipt

Two deliverables from one harness. `checks/` already drives the real app against the real database and cleans up after itself, which is exactly what a storage measurement needs: a known set of writes, bracketed by two measurements.

**Files:** Create `backend/checks/workspace.py`, `backend/scripts/storage_receipt.py`. Modify `backend/checks/__init__.py`.

- [ ] **Step 1: Write `checks/workspace.py`** — sign in through the real demo endpoint as the `facility_user`; assert the workspace lists that facility's medicines with provenance; assert Find supply returns donors that clear the floor and none for a controlled SKU; assert a pharmacist gets 403 on another facility; raise a request; assert **nothing moved** (donor stock unchanged, status still `proposed`); sign in as the officer the response names and approve; assert the donor is debited and a movement exists; confirm receipt; assert the recipient is credited. Delete every row it created in a `finally`.
- [ ] **Step 2: Register it** in `checks/__init__.py` as `workspace`, and add it to the module docstring's list.
- [ ] **Step 3: Run it** — `cd backend && .venv/Scripts/python -m checks workspace`. Expected: all assertions pass, nothing left behind.
- [ ] **Step 4: Write `scripts/storage_receipt.py`** — read-only apart from the run it brackets. For each table a demo run touches (`transfers`, `approvals`, `medicine_movements`, `stock_readings`, `events`, `facility_sku_state`), record `count(*)` and `pg_total_relation_size` before and after, and print a table of rows and bytes added. **Local only; it must refuse a non-local host**, following the guard in `scripts/remote.py`.
- [ ] **Step 5: Measure and record** — run the full loop once, capture the receipt, and write the numbers into `docs/STORAGE_NOTES.md` under a new heading with the date and the commit.
- [ ] **Step 6: Run the whole suite** — `cd backend && .venv/Scripts/python -m pytest -q && .venv/Scripts/python -m checks`
- [ ] **Step 7: Scan the diff for secrets, commit, tag**

```bash
git diff --stat
git commit -m "The loop, proved end to end, with the bytes it costs"
git tag pharmacist-workspace-complete
```

---

## Parked — not built until Aditya says go

| Item | Why it is parked | What unparks it |
|---|---|---|
| `medicine_movements.expiry_date` + expiry panel | Needs a migration; first Render deploy is code-only | A go, plus a migration window |
| `facility_briefings` table | New table; needs a migration | A go on the table specifically |
| Task 8 — Gemini "what to do today" | Depends on the briefings cache; also **blocked** on a confirmed text model id (vision uses `gemini-3.6-flash`; a second model is needed for its own 20/day quota, and `gemini-2.5-flash` is recorded as 404ing, so it must not be guessed) | A model id, or 1 dev call to probe one |
| Task 9 — batch seeding, Render push script | Both are schema/seed work | A go |

Gemini remains load-bearing through bed-photo vision, which is untouched, so nothing parked here affects the mandatory Google AI integration.

---

## Self-review

**Spec coverage.** User-story item 1 → Tasks 2, 3. Item 2 → Task 3 (stock-out date; expiry parked by decision). Item 3 → Task 4. Item 4 → Tasks 5, 6, 7. Mobile-first 360px → Task 1, verified Step 4. Generic on `facility_id` → Task 1 Step 3 reads it from the session; no task names a facility. Global cap of 100 → Task 5 Steps 1, 2. Storage receipt → Task 7b. Code-only first deploy → no migration exists in Tasks 1–7.

**Placeholders.** None: every constant carries its value, every test its assertion, every route its schema.

**Type consistency.** `distance_basis` is the literal `"straight_line_x1.3"` in Task 4's `DonorOut` and Task 6's estimate. `triggered_by="facility_request"` is the same literal in Task 5's insert, both cap queries, and Task 7b's cleanup. `LastReceipt` has the same four fields in Task 2's test, its query and `Medicines.tsx`.

**Review Focus.** 1 → Task 3 Step 1; 2 → Task 2 Step 5 (403, asserted in Task 7b Step 1 against the real app); 3 → Task 4 Step 1; 4 → Task 5 Step 2; 5 → Task 5 Step 2.
