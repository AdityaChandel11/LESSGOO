"""Fix #45: the "Next 14 days" strip.

District × medicine pairs with a projected run-out date inside the horizon,
earliest first. The date is counted from each centre's last count (fix #26's
rule) at the shared model's forecast where one is fresh, else the burn rate,
and at the outbreak rate where an outbreak is active (fix #41). The database
read is one aggregate; everything it is turned into is tested here.
"""

from __future__ import annotations

from datetime import date

from app import earlywarning as ew


def pair(district="Nashik", sku="ORS", first=date(2026, 10, 4), centres=3, by_forecast=0, **kw):
    base = dict(
        state="MH", district=district, sku_code=sku, sku_name="Oral Rehydration Salts",
        centres=centres, first_on=first, first_centre="Nashik PHC 9", by_forecast=by_forecast,
    )
    base.update(kw)
    return ew.Pair(**base)


def warning(name, sku, on, without, basis="assumption"):
    return {
        "facility_name": name, "sku_code": sku, "sku_name": "Oral Rehydration Salts",
        "runs_out_on": on, "runs_out_without": without, "basis": basis, "district": "Nashik",
    }


def test_the_earliest_run_out_comes_first_then_the_most_centres():
    ranked = ew.rank(
        [
            pair("Pune", first=date(2026, 10, 9)),
            pair("Nagpur", first=date(2026, 10, 3), centres=1),
            pair("Nashik", first=date(2026, 10, 3), centres=4),
        ],
        limit=2,
    )
    assert [p.district for p in ranked] == ["Nashik", "Nagpur"]


def test_a_pair_says_which_rule_its_dates_rest_on():
    assert pair(by_forecast=3).source == "forecast"
    assert pair(by_forecast=1).source == "mixed"
    assert pair(by_forecast=0).source == "burn_rate"
    surged = pair(outbreak=ew.OutbreakNote("Cholera", "assumption", date(2026, 10, 19)))
    assert surged.source == "outbreak"


def test_an_outbreaks_warnings_become_one_figure_per_medicine():
    figures = ew.outbreak_figures(
        state="MH", district="Nashik", disease="Cholera",
        warnings=[
            warning("Nashik PHC 9", "ORS", date(2026, 10, 4), date(2026, 10, 19)),
            warning("Nashik PHC 2", "ORS", date(2026, 10, 6), date(2026, 10, 12)),
            warning("Nashik PHC 2", "ZINC", date(2026, 10, 8), date(2026, 10, 20), basis="observed"),
        ],
    )
    ors = figures[("MH", "Nashik", "ORS")]
    assert (ors.centres, ors.first_on, ors.first_centre) == (2, date(2026, 10, 4), "Nashik PHC 9")
    assert ors.outbreak == ew.OutbreakNote("Cholera", "assumption", date(2026, 10, 19))
    assert figures[("MH", "Nashik", "ZINC")].outbreak.basis == "observed"


def test_the_outbreak_rate_replaces_the_ordinary_figure_where_an_outbreak_is():
    base = [pair("Nashik", "ORS", first=date(2026, 10, 12), centres=1), pair("Pune", "ORS")]
    surged = ew.outbreak_figures(
        state="MH", district="Nashik", disease="Cholera",
        warnings=[
            warning("Nashik PHC 9", "ORS", date(2026, 10, 4), date(2026, 10, 19)),
            warning("Nashik PHC 2", "ZINC", date(2026, 10, 8), date(2026, 10, 20)),
        ],
    )
    merged = {p.key: p for p in ew.merge_outbreak(base, surged)}
    assert merged[("MH", "Nashik", "ORS")].first_on == date(2026, 10, 4)
    assert merged[("MH", "Nashik", "ORS")].source == "outbreak"
    # A pair only the outbreak produces is added; one it does not touch is kept.
    assert ("MH", "Nashik", "ZINC") in merged
    assert merged[("MH", "Pune", "ORS")].source == "burn_rate"


def test_the_line_names_the_district_the_medicine_and_the_date():
    assert ew.line(pair(centres=3)) == (
        "Nashik · Oral Rehydration Salts · 3 centres run out within 14 days, the first "
        "(Nashik PHC 9) on 4 Oct"
    )
    one = ew.line(pair(centres=1))
    assert one == "Nashik · Oral Rehydration Salts · Nashik PHC 9 runs out on 4 Oct"
    surged = ew.line(pair(centres=1, outbreak=ew.OutbreakNote("Cholera", "observed", date(2026, 10, 19))))
    assert surged.endswith("(19 Oct without the Cholera outbreak)")


def test_the_read_is_one_bounded_aggregate():
    # facility_sku_state is the map's own table (one row per centre and
    # medicine); the reading history is never touched, and the result is capped.
    sql = ew.PAIRS_SQL.lower()
    assert "stock_readings" not in sql
    assert "facility_sku_state" in sql and "group by" in sql and "limit :limit" in sql
