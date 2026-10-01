"""The "How this demo data is generated" page says what seed.py does — pinned.

Fix list #28. The page (frontend/src/DemoData.tsx) takes every number it shows
from frontend/src/seedRules.ts. Each of those is a copy of a literal in
backend/scripts/seed.py (or app/geo.py, or the README's deploy step), and a
copy drifts silently the first time somebody tunes the generator. A page that
explains synthetic data and gets its own rules wrong would be worse than no
page, so these tests read both sides and fail on any disagreement.

When seed.py changes on purpose: change seedRules.ts to match, then update the
snippet here. The snippet is the review — it names the exact line the page is
describing.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from app.geo import INDIA_STATES, TOTAL_SEEDED_FACILITIES
from scripts import seed

ROOT = Path(__file__).resolve().parents[2]
RULES_TS = ROOT / "frontend" / "src" / "seedRules.ts"
PAGE_TSX = ROOT / "frontend" / "src" / "DemoData.tsx"
SEED_SRC = (ROOT / "backend" / "scripts" / "seed.py").read_text(encoding="utf-8")
README = (ROOT / "README.md").read_text(encoding="utf-8")


def _ts_rules() -> dict[str, float]:
    text = RULES_TS.read_text(encoding="utf-8")
    body = text.split("export const SEED_RULES = {", 1)[1].split("} as const", 1)[0]
    found = re.findall(r"(\w+):\s*(-?\d[\d_]*(?:\.\d+)?)\s*,", body)
    return {k: float(v.replace("_", "")) for k, v in found}


def _argparse_defaults() -> dict[str, object]:
    out: dict[str, object] = {}
    for node in ast.walk(ast.parse(SEED_SRC)):
        if (
            isinstance(node, ast.Call)
            and getattr(node.func, "attr", "") == "add_argument"
            and node.args
            and isinstance(node.args[0], ast.Constant)
        ):
            for kw in node.keywords:
                if kw.arg == "default" and isinstance(kw.value, ast.Constant):
                    out[node.args[0].value] = kw.value.value
    return out


# Values that are module constants or argparse defaults: compared to the real
# objects, not to text, so a reformat of seed.py cannot fool them.
def _from_code() -> dict[str, float]:
    args = _argparse_defaults()
    focus = [s for s in INDIA_STATES if "focus" in s.tags]
    return {
        "randomSeed": seed.DEFAULT_SEED,
        "seededCentres": TOTAL_SEEDED_FACILITIES,
        "regions": len(INDIA_STATES),
        "focusStates": len(focus),
        "historyDays": args["--days"],
        "focusHistoryDaysDefault": args["--focus-days"],
        "gamingShare": args["--gaming-pct"],
        "supplyFailureShare": args["--supply-failure-pct"],
        "outbreakMultiplier": seed.OUTBREAK_MULTIPLIER,
        "outbreakDaysAgo": seed.OUTBREAK_DAYS_AGO,
        "outbreakLengthDays": seed.OUTBREAK_LENGTH_DAYS,
        "reorderPointDays": seed.REORDER_POINT_DAYS,
        "resupplyCoverDays": seed.RESUPPLY_COVER_DAYS,
        "leadTimeMin": seed.LEAD_TIME_RANGE[0],
        "leadTimeMax": seed.LEAD_TIME_RANGE[1],
        "attendanceDays": seed.ATTENDANCE_DAYS,
        "pingChance": seed.PING_CHANCE,
        "medicines": len(seed.SKUS),
        "monsoonBoostMin": min(s.monsoon_boost for s in INDIA_STATES),
        "monsoonBoostMax": max(s.monsoon_boost for s in INDIA_STATES),
    }


# Values written inline inside an expression. Each is pinned to the exact
# source text it describes; if that text changes, the page must be re-read.
FROM_SOURCE: dict[str, tuple[float, str]] = {
    "chcEvery": (5, "is_chc = i % 5 == 4"),
    "phcBedsMin": (6, '"beds_total": (30 if is_chc else 6) + rng.randint(0, 6)'),
    "chcBedsMin": (30, '"beds_total": (30 if is_chc else 6) + rng.randint(0, 6)'),
    "bedsSpread": (6, '"beds_total": (30 if is_chc else 6) + rng.randint(0, 6)'),
    "centreMultiplierMin": (0.6, 'base_rate = sku["base"] * scale * rng.uniform(0.6, 1.5)'),
    "centreMultiplierMax": (1.5, 'base_rate = sku["base"] * scale * rng.uniform(0.6, 1.5)'),
    "chcScale": (2.2, 'scale = 2.2 if facility["type"] == "CHC" else 1.0'),
    "seasonalSwing": (0.4, "seasonal = 1 + 0.4 * math.sin(2 * math.pi * doy / 365 - phase)"),
    "festivalFactor": (0.75, "festival = 0.75 if _is_festival(day) else 1.0"),
    "festivalHalfWidth": (3, "abs(day.day - d) <= 3"),
    "noiseSd": (0.12, "rng.gauss(0, base_rate * 0.12)"),
    "supplyFailureDays": (12, "if not (supply_failure and days_ago < 12):"),
    "fullWardDays": (10, "if supply_failure and (days - 1 - d) < 10:"),
    "fullWardMin": (0.88, "ratio = rng.uniform(0.88, 1.0)"),
    "bedOccupancyMin": (0.35, "ratio = rng.uniform(0.35, 0.8)"),
    "bedOccupancyMax": (0.8, "ratio = rng.uniform(0.35, 0.8)"),
    "weakFactor": (3.0, "quality = {deep[0]: 3.0, deep[-1]: 0.4}"),
    "strongFactor": (0.4, "quality = {deep[0]: 3.0, deep[-1]: 0.4}"),
    "consignmentsMin": (2, "for _ in range(rng.randint(2, 6)):"),
    "consignmentsMax": (6, "for _ in range(rng.randint(2, 6)):"),
    "consignmentDays": (45, "days: int = 45,"),
    "unconfirmedRate": (0.18, "draw < min(0.5, 0.18 * slip) and settled_late and age_days <= 12"),
    "unconfirmedCap": (0.5, "draw < min(0.5, 0.18 * slip) and settled_late and age_days <= 12"),
    "recentDays": (12, "draw < min(0.5, 0.18 * slip) and settled_late and age_days <= 12"),
    "shortRate": (0.11, "if draw < min(0.35, 0.11 * slip):"),
    "shortCap": (0.35, "if draw < min(0.35, 0.11 * slip):"),
    "overRate": (0.13, "elif draw < min(0.4, 0.13 * slip):"),
    "overCap": (0.4, "elif draw < min(0.4, 0.13 * slip):"),
    "gamingMinHistory": (70, "if is_gaming and days > 70:"),
    "gamingWindowMin": (60, "g_len = rng.randint(60, min(200, days - 10))"),
    "gamingWindowMax": (200, "g_len = rng.randint(60, min(200, days - 10))"),
    "gamingZeroFootfall": (0.6, "footfall = 0 if is_gaming and rng.random() < 0.6"),
    "gamingNoReply": (0.45, "if rng.random() < (0.45 if is_gaming else 0.08):"),
    "normalNoReply": (0.08, "if rng.random() < (0.45 if is_gaming else 0.08):"),
    "phcRoster": (4, 'roster = 8 if fac["type"] == "CHC" else 4'),
    "chcRoster": (8, 'roster = 8 if fac["type"] == "CHC" else 4'),
    "gpsShare": (0.7, "weights=[0.7, 0.18, 0.12],"),
    "ussdShare": (0.18, "weights=[0.7, 0.18, 0.12],"),
    "ivrShare": (0.12, "weights=[0.7, 0.18, 0.12],"),
    "attendRateMin": (0.7, "rng.choice([0.93, 0.9, 0.87, 0.82, 0.7])"),
    "attendRateMax": (0.93, "rng.choice([0.93, 0.9, 0.87, 0.82, 0.7])"),
    "absenceRunShare": (0.45, "if not is_gaming and rng.random() < 0.45"),
    "absenceRunMin": (2, "block_len = rng.randint(2, 5)"),
    "absenceRunMax": (5, "block_len = rng.randint(2, 5)"),
    "wardPhotoDays": (21, "facilities: list[dict], rng: random.Random, days: int = 21"),
    "wardReportRateMin": (0.6, "reporting_rate = rng.choice([0.9, 0.85, 0.95, 0.6])"),
    "wardReportRateMax": (0.95, "reporting_rate = rng.choice([0.9, 0.85, 0.95, 0.6])"),
    "wardOldCodeBelow": (0.04, "if draw < 0.04:"),
    "wardIllegibleBelow": (0.07, "elif draw < 0.07:"),
    "wardElsewhereBelow": (0.1, "elif draw < 0.10:"),
    "wardRegisterBelow": (0.14, "elif draw < 0.14:"),
}

# The deployed demo's history depth is a fact about how it was seeded, which
# the README's deploy step records; the page quotes it, so it is pinned there.
FROM_README: dict[str, tuple[float, str]] = {
    "focusHistoryDaysDeployed": (120, "-m scripts.seed --days 35 --focus-days 120"),
}


def test_every_rule_on_the_page_is_pinned_to_its_source():
    rules = _ts_rules()
    pinned = set(_from_code()) | set(FROM_SOURCE) | set(FROM_README)
    assert set(rules) == pinned, (
        f"unpinned on the page: {sorted(set(rules) - pinned)}; "
        f"pinned but missing from the page: {sorted(pinned - set(rules))}"
    )


@pytest.mark.parametrize("key", sorted(_from_code()))
def test_constants_match_the_generator(key):
    assert _ts_rules()[key] == pytest.approx(float(_from_code()[key])), key


@pytest.mark.parametrize("key", sorted(FROM_SOURCE))
def test_inline_rules_match_the_generator_source(key):
    value, snippet = FROM_SOURCE[key]
    assert snippet in SEED_SRC, f"seed.py no longer contains {snippet!r}; re-read the page"
    assert _ts_rules()[key] == pytest.approx(value), key


@pytest.mark.parametrize("key", sorted(FROM_README))
def test_deployed_history_matches_the_readme(key):
    value, snippet = FROM_README[key]
    assert snippet in README
    assert _ts_rules()[key] == pytest.approx(value)


def test_the_deployed_history_is_too_short_to_hold_the_seeded_outbreak():
    # The page says the Nashik outbreak is absent from the deployed data. That
    # is only true while the outbreak starts further back than the history.
    rules = _ts_rules()
    earliest_outbreak_day = rules["outbreakDaysAgo"] - rules["outbreakLengthDays"]
    assert rules["focusHistoryDaysDeployed"] - 1 < earliest_outbreak_day
    assert rules["focusHistoryDaysDefault"] - 1 >= rules["outbreakDaysAgo"]


def test_festival_dates_on_the_page_are_the_generators():
    assert seed.FESTIVAL_ANCHORS == ((10, 20), (11, 5))
    page = PAGE_TSX.read_text(encoding="utf-8")
    assert "20 October" in page and "5 November" in page


def test_the_page_labels_itself_synthetic_and_never_names_a_person():
    page = PAGE_TSX.read_text(encoding="utf-8")
    assert "synthetic" in page.lower()
    assert "NHM" not in page
    # The weak state is a random draw; the page must say so, not name one.
    assert "at random" in page
