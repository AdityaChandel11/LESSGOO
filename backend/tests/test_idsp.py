"""IDSP Weekly Outbreak Report parsing, against lines copied from real reports."""

from __future__ import annotations

import asyncio

from app import api, idsp

STATES = ["Maharashtra", "Karnataka", "Kerala", "Assam"]

# Verbatim text as pypdf extracts it from week 53/2020 and week 24/2023.
WEEK53 = """MH/AMR/2020/53/528 Maharashtra Amravati
Acute
Diarrheal
Disease
53 00 28-12-20 31-12-20 Under
Surveillance
Cases reported from Village Shingnapur, PHC
Chandrapur, Block Daryapur .
MH/BED/2020/53/536 Maharashtra Beed Fever 41 00 07-12-20 31-12-20
Cases reported from Babhuldara tanda PHC Chaklamba Tq
"""
WEEK24 = """KN/UDU/2023/24/537 Karnataka Udupi Suspected Human Rabies 01 01 06-06-2023 Under
Surveillance
KL/ERN/2023/24/539 Kerala Ernakulam Dengue  17 00 06-05-23 Under
Surveillance
"""


def test_a_wrapped_row_is_read_column_by_column():
    row = idsp.parse_report(WEEK53, STATES)[0]
    assert row["unique_id"] == "MH/AMR/2020/53/528"
    assert (row["state"], row["district"], row["disease"]) == ("Maharashtra", "Amravati", "Acute Diarrheal Disease")
    assert (row["cases"], row["deaths"]) == (53, 0)
    assert row["start_date"] == "2020-12-28" and row["reported_date"] == "2020-12-31"
    assert row["status"] == "Under Surveillance"


def test_a_late_report_row_without_status_keeps_status_empty():
    row = idsp.parse_report(WEEK53, STATES)[1]
    assert (row["district"], row["disease"], row["cases"]) == ("Beed", "Fever", 41)
    assert row["status"] is None


def test_four_digit_years_and_a_missing_reporting_date():
    rows = idsp.parse_report(WEEK24, STATES)
    assert (rows[0]["district"], rows[0]["disease"]) == ("Udupi", "Suspected Human Rabies")
    assert rows[0]["start_date"] == "2023-06-06"
    assert rows[1]["reported_date"] is None and rows[1]["status"] == "Under Surveillance"


def test_comments_are_discarded():
    for row in idsp.parse_report(WEEK53, STATES):
        assert "Shingnapur" not in str(row)


def test_the_committed_file_keeps_only_structured_fields():
    data = idsp.load()
    assert data["source"] == "IDSP Weekly Outbreak Report"
    assert data["columns"] == [
        "Disease/Illness", "No. of Cases", "No. of Deaths", "Date of Start of Outbreak", "Current Status",
    ]
    keys = {"unique_id", "year", "week", "state", "state_code", "district", "disease",
            "cases", "deaths", "start_date", "reported_date", "status"}
    assert data["rows"] and all(set(r) == keys for r in data["rows"])


# The endpoint's state filter is pinned in tests/test_outbreak_panel.py, with
# the facility counts and active outbreaks it now reads (fix #59).


# --------------------------------------------------- the 2026 layout (#43) ---
# Verbatim text as pypdf extracts it from week 32/2026: the date cells break
# around their hyphens, and the diseases include names older reports lacked.
WEEK32_2026 = """AP/KAK/2026/32/1290 Andhra Pradesh Kakinada Hepatitis A 32 0 07-08- 2026 08-08-
2026 Under Surveillance Cases of fever, abdominal pain and dark coloured urine
AP/VIS/2026/32/1291 Andhra Pradesh Visakhapatnam Acute Gastroenteritis 25 0 08-08-
2026 08-08- 2026 Under Surveillance Cases of loose motion and abdominal pain
BR/PAS/2026/32/1293 Bihar Pashchim Champaran Measles 6 0 01- 08- 2026 03- 08- 2026 Under
Surveillance Cases of fever with rash were reported, 3 -16 years
"""
STATES_2026 = STATES + ["Andhra Pradesh", "Bihar"]


def test_dates_broken_across_lines_are_read_whole():
    rows = idsp.parse_report(WEEK32_2026, STATES_2026)
    assert [r["unique_id"] for r in rows] == [
        "AP/KAK/2026/32/1290", "AP/VIS/2026/32/1291", "BR/PAS/2026/32/1293",
    ]
    assert (rows[0]["start_date"], rows[0]["reported_date"]) == ("2026-08-07", "2026-08-08")
    assert (rows[2]["start_date"], rows[2]["reported_date"]) == ("2026-08-01", "2026-08-03")
    assert rows[2]["status"] == "Under Surveillance"


def test_acute_gastroenteritis_is_read_and_drives_rehydration_medicines():
    rows = idsp.parse_report(WEEK32_2026, STATES_2026)
    assert rows[1]["disease"] == "Acute Gastroenteritis"
    assert idsp.DISEASE_MEDICINES["Acute Gastroenteritis"] == ["ORS", "ZINC", "IVFLUID"]


def test_the_upload_day_comes_from_ncdc_s_file_name():
    from datetime import date

    assert idsp.uploaded_on("week32_1790680151.pdf") == date(2026, 9, 29)
    assert idsp.uploaded_on("week53.pdf") is None


def test_the_committed_file_is_the_latest_published_reports():
    weeks = [(r["year"], r["week"]) for r in idsp.load()["reports"]]
    assert weeks == [(2026, 31), (2026, 32)]
    assert all(r["uploaded_on"] for r in idsp.load()["reports"])
