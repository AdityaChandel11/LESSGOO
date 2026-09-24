"""Outbreak warnings from the IDSP Weekly Outbreak Report.

The Integrated Disease Surveillance Programme (NCDC, ncdc.mohfw.gov.in)
publishes a weekly PDF listing every outbreak states reported. Each row
carries a unique ID such as ``MH/AMR/2020/53/528`` followed by the report's
own columns: Name of State/UT, Name of District, Disease/Illness, No. of
Cases, No. of Deaths, Date of Start of Outbreak, Date of Reporting, Current
Status, Comments/Action Taken.

`parse_report` turns the text of one report into small structured rows and
discards the rest (the free-text comments included). `scripts/idsp_ingest.py`
runs it once over downloaded PDFs and writes `data/idsp_outbreaks.json`; the
API reads that file. Nothing is stored in the database and nothing polls.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from functools import lru_cache
from pathlib import Path

DATA_FILE = Path(__file__).resolve().parent / "data" / "idsp_outbreaks.json"
SOURCE = "IDSP Weekly Outbreak Report"
SOURCE_URL = "https://ncdc.mohfw.gov.in"

# Column headers exactly as the report prints them.
COLUMNS = (
    "Disease/Illness",
    "No. of Cases",
    "No. of Deaths",
    "Date of Start of Outbreak",
    "Current Status",
)

UNIQUE_ID = re.compile(r"\b([A-Z]{2})/([A-Z]{3,4})/(20\d{2})/(\d{1,2})/(\d+)\b")

# Longest names first, so "Acute Diarrheal Disease" wins over a shorter match.
DISEASES = sorted(
    [
        "Acute Diarrheal Disease", "Acute Diarrhoeal Disease", "Acute Encephalitis Syndrome",
        "Food Poisoning", "Food Borne Illness", "Chikungunya", "Leptospirosis", "Chickenpox",
        "Measles", "Dengue", "Cholera", "Viral Hepatitis", "Hepatitis A", "Hepatitis E",
        "Mumps", "Fever with Rash", "Malaria", "Enteric Fever", "Typhoid", "Anthrax",
        "Scrub Typhus", "Diphtheria", "Rubella", "Japanese Encephalitis", "Viral Fever",
        "Kyasanur Forest Disease", "Mushroom Poisoning", "Pertussis", "Zika", "Rabies",
        "Human Rabies", "Suspected Human Rabies", "Suspected Measles", "Jaundice", "Fever",
    ],
    key=len,
    reverse=True,
)

STATUSES = ("Under Control", "Under Surveillance", "Controlled", "Contained")

ROW = re.compile(
    r"^(?P<body>.+?)\s+(?P<cases>\d{1,5})\s+(?P<deaths>\d{1,4})\s+"
    r"(?P<start>\d{2}-\d{2}-\d{2,4})(?:\s+(?P<reported>\d{2}-\d{2}-\d{2,4}))?\s+"
    # The "reported late" section of a report has no Current Status column.
    r"(?:(?P<status>" + "|".join(s.replace(" ", r"\s+") for s in STATUSES) + r")\b)?",
    re.IGNORECASE,
)


def _date(text: str | None) -> str | None:
    if not text:
        return None
    fmt = "%d-%m-%Y" if len(text.split("-")[-1]) == 4 else "%d-%m-%y"
    return datetime.strptime(text, fmt).date().isoformat()


def _split_body(body: str, state_names: list[str]) -> tuple[str, str, str] | None:
    """'Maharashtra Amravati Acute Diarrheal Disease' -> (state, district, disease)."""
    state = next((s for s in sorted(state_names, key=len, reverse=True)
                  if body.lower().startswith(s.lower())), None)
    if state is None:
        return None
    rest = body[len(state):].strip()
    for disease in DISEASES:
        if rest.lower().endswith(disease.lower()):
            district = rest[: -len(disease)].strip()
            if district:
                return state, district, disease
    return None


def parse_report(text: str, state_names: list[str]) -> list[dict]:
    """Structured rows from one report's text. Rows it cannot read are skipped."""
    flat = re.sub(r"\s+", " ", text)
    starts = list(UNIQUE_ID.finditer(flat))
    rows = []
    for i, m in enumerate(starts):
        chunk = flat[m.end(): starts[i + 1].start() if i + 1 < len(starts) else len(flat)].strip()
        found = ROW.match(chunk)
        if not found:
            continue
        parts = _split_body(found["body"], state_names)
        if not parts:
            continue
        state, district, disease = parts
        rows.append({
            "unique_id": m.group(0),
            "year": int(m.group(3)),
            "week": int(m.group(4)),
            "state": state,
            "district": district,
            "disease": disease,
            "cases": int(found["cases"]),
            "deaths": int(found["deaths"]),
            "start_date": _date(found["start"]),
            "reported_date": _date(found["reported"]),
            "status": re.sub(r"\s+", " ", found["status"]).title() if found["status"] else None,
        })
    return rows


@lru_cache(maxsize=1)
def load() -> dict:
    """The committed rows, read once per process."""
    return json.loads(DATA_FILE.read_text(encoding="utf-8"))


def outbreaks(state_code: str | None = None) -> list[dict]:
    rows = load()["rows"]
    if state_code:
        rows = [r for r in rows if r["state_code"] == state_code]
    return sorted(rows, key=lambda r: (r["start_date"] or "", r["cases"]), reverse=True)


def network_districts() -> set[tuple[str, str]]:
    """(state code, district) pairs where this network has facilities."""
    from . import geo

    return {
        (s.code, (a[0] if isinstance(a, (tuple, list)) else getattr(a, "name", str(a))).lower())
        for s in geo.INDIA_STATES
        for a in s.anchors
    }
