"""The geography the demo claims to cover.

"Depth and reach across India" is a judged criterion, so the number of regions
in the seed is a claim, not a detail. India is 28 states and 8 union
territories; an earlier list held 34 regions, missing Lakshadweep and the
merged Dadra & Nagar Haveli and Daman & Diu. These tests make that
countable instead of remembered.
"""

from app.geo import (
    INDIA_STATES,
    STATE_CODES,
    TOTAL_SEEDED_FACILITIES,
    UNION_TERRITORY_CODES,
)

# Every union territory, by ISO 3166-2:IN code, as of the 2019 reorganisation
# of Jammu & Kashmir and the 2020 merger that created DH.
EXPECTED_UTS = {"AN", "CH", "DH", "DL", "JK", "LA", "LD", "PY"}


def test_the_seed_covers_all_twenty_eight_states_and_eight_union_territories():
    assert len(STATE_CODES) == 28, sorted(STATE_CODES)
    assert UNION_TERRITORY_CODES == EXPECTED_UTS
    assert len(INDIA_STATES) == 36


def test_every_region_is_classified_as_one_or_the_other():
    assert STATE_CODES.isdisjoint(UNION_TERRITORY_CODES)
    assert len(STATE_CODES | UNION_TERRITORY_CODES) == len(INDIA_STATES)


def test_region_codes_are_unique_because_facility_ids_are_built_from_them():
    # Facility ids are f"HFR-{code}-{kind}-{i:05d}", so a duplicated code would
    # silently collide two regions' facilities onto the same primary keys.
    codes = [s.code for s in INDIA_STATES]
    assert len(codes) == len(set(codes))


def test_every_region_seeds_at_least_one_facility():
    empty = [s.code for s in INDIA_STATES if s.facilities < 1]
    assert not empty, empty


def test_every_region_has_an_anchor_to_scatter_around():
    assert all(s.anchors for s in INDIA_STATES)


def test_the_documented_facility_total_matches_the_list():
    assert TOTAL_SEEDED_FACILITIES == sum(s.facilities for s in INDIA_STATES) == 3510


def test_island_territories_keep_their_scatter_tight_enough_to_stay_on_land():
    # A degree is roughly 111 km. Lakshadweep's islands are a few km across, so
    # the default 0.45 degree jitter would drop facilities into the sea.
    islands = {s.code: s for s in INDIA_STATES}
    assert islands["LD"].spread <= 0.05
    assert islands["AN"].spread <= 0.25
