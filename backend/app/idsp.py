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
from datetime import date, datetime
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
        # Names the 2026 reports use (#43).
        "Acute Gastroenteritis", "Acute Diarrhoeal", "Acute Encephalitic Syndrome",
        "Chandipura Virus", "Hepatitis A & E", "Hand Foot and Mouth Disease",
        "Meningococcal Meningitis",
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


# pypdf breaks a date cell around its hyphens when the column wraps
# ("07-08- 2026", "01- 08- 2026"); rejoin before matching (2026 reports, #43).
BROKEN_DATE = re.compile(r"\b(\d{1,2})\s*-\s*(\d{1,2})\s*-\s*(\d{4}|\d{2})\b")

# NCDC names each upload "week32_<unix time>.pdf": the time is the upload.
UPLOAD_STAMP = re.compile(r"_(\d{10})\.pdf$", re.IGNORECASE)


def uploaded_on(filename: str):
    """The day NCDC uploaded a report, from its file name, or None."""
    from datetime import timezone

    found = UPLOAD_STAMP.search(filename)
    if not found:
        return None
    return datetime.fromtimestamp(int(found.group(1)), timezone.utc).date()


def parse_report(text: str, state_names: list[str]) -> list[dict]:
    """Structured rows from one report's text. Rows it cannot read are skipped."""
    flat = BROKEN_DATE.sub(r"\1-\2-\3", re.sub(r"\s+", " ", text))
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


# =============================================== reading a new report (#42) ===
# Gemini reads the rows out of the PDF (vision.read_idsp_report). Each row
# comes back with the printed text it was read from, and the regex parser
# above re-reads that text: a field the two disagree on is flagged, and only
# rows they agree on may become active outbreaks (#41).

STATE_ALIASES = {"Jammu and Kashmir": "Jammu & Kashmir", "Orissa": "Odisha"}
CHECKED_FIELDS = (
    "unique_id", "state", "district", "disease", "cases", "deaths", "start_date", "status",
)


def state_names() -> list[str]:
    from . import geo

    return [s.name for s in geo.INDIA_STATES] + list(STATE_ALIASES)


def canonical_state(name: str) -> tuple[str, str | None]:
    from . import geo

    canonical = STATE_ALIASES.get(name.strip(), name.strip())
    codes = {s.name.lower(): s.code for s in geo.INDIA_STATES}
    return canonical, codes.get(canonical.lower())


def _as_count(value) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value >= 0:
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _as_date(value) -> str | None:
    if not value or not isinstance(value, str):
        return None
    text = value.strip()
    try:
        return datetime.fromisoformat(text).date().isoformat()
    except ValueError:
        pass
    try:
        return _date(text)
    except ValueError:
        return None


def _status(value) -> str | None:
    if not value or not isinstance(value, str):
        return None
    return re.sub(r"\s+", " ", value).strip().title() or None


def normalise_row(raw: dict) -> dict | None:
    """One model row in the committed file's shape, or None when a field that
    matters is missing or implausible. A row is dropped, never repaired: a
    guessed case count is worse than a missing row."""
    uid = str(raw.get("unique_id") or "").strip().upper()
    found = UNIQUE_ID.fullmatch(uid)
    cases, deaths = _as_count(raw.get("cases")), _as_count(raw.get("deaths"))
    state_raw = str(raw.get("state") or "").strip()
    district = re.sub(r"\s+", " ", str(raw.get("district") or "")).strip()
    disease = re.sub(r"\s+", " ", str(raw.get("disease") or "")).strip()
    if not found or cases is None or deaths is None or not state_raw or not district or not disease:
        return None
    state, code = canonical_state(state_raw)
    return {
        "unique_id": uid,
        "year": int(found.group(3)),
        "week": int(found.group(4)),
        "state": state,
        "state_code": code,
        "district": district,
        "disease": disease,
        "cases": cases,
        "deaths": deaths,
        "start_date": _as_date(raw.get("start_date")),
        "reported_date": _as_date(raw.get("reported_date")),
        "status": _status(raw.get("status")),
        "row_text": re.sub(r"\s+", " ", str(raw.get("row_text") or "")).strip(),
    }


def _same(field: str, a, b) -> bool:
    if isinstance(a, str) or isinstance(b, str):
        return re.sub(r"\s+", " ", str(a or "")).strip().lower() == re.sub(
            r"\s+", " ", str(b or "")
        ).strip().lower()
    return a == b


def cross_check(row: dict) -> dict:
    """The regex parser's reading of the printed row, against the model's."""
    parsed = parse_report(row.get("row_text") or "", state_names())
    if not parsed:
        return {"verdict": "unparsed", "fields": []}
    p = parsed[0]
    p["state"] = STATE_ALIASES.get(p["state"], p["state"])
    fields = [f for f in CHECKED_FIELDS if not _same(f, p.get(f), row.get(f))]
    return {"verdict": "agrees" if not fields else "disagrees", "fields": fields}


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


def panel_rows(
    rows: list[dict],
    *,
    ours: set[tuple[str, str]],
    facility_counts: dict[tuple[str, str], int],
    today: date,
    ttl_days: int,
    active: set[tuple[str, str]],
) -> list[dict]:
    """Report rows as the panel shows them (fix #59): a warning, not an archive.

    Each row says how long ago the outbreak began, whether this network has
    centres in its district and how many, which medicines its disease drives,
    and whether an outbreak is active there now. Rows that began inside the
    outbreak window come first; a row older than that, or with no start date,
    is historical — the report is real, but it is not a current warning.
    Within each group: the newest report, then districts in the network, then
    the latest start and the most cases.
    """
    counts = {(s, d.lower()): n for (s, d), n in facility_counts.items()}
    out: list[dict] = []
    for r in rows:
        key = (r["state_code"], r["district"].lower())
        age = (today - date.fromisoformat(r["start_date"])).days if r["start_date"] else None
        out.append({
            **r,
            "in_network": key in ours,
            "facilities": counts.get(key, 0) if key in ours else 0,
            "medicines": [MEDICINE_NAMES[c] for c in DISEASE_MEDICINES.get(r["disease"], [])],
            "age_days": age,
            "historical": age is None or age > ttl_days,
            "active": key in active,
        })
    out.sort(key=lambda r: r["cases"], reverse=True)
    out.sort(key=lambda r: r["start_date"] or "", reverse=True)
    out.sort(key=lambda r: not r["in_network"])
    out.sort(key=lambda r: (r["year"], r["week"]), reverse=True)
    out.sort(key=lambda r: r["historical"])
    return out


# ======================================================== stocking advice ===
# How an outbreak turns into supply action: the IDSP row names the disease and
# district, the disease-to-medicine map below names what it drives, and the
# state's monsoon calendar says whether the season amplifies it. How much
# demand rises is not guessed here: declaring the outbreak active (#41,
# app/outbreak.py) measures it from the district's own readings, or takes the
# officer's stated expectation, labelled as an assumption.

DISEASE_MEDICINES: dict[str, list[str]] = {
    "Acute Diarrheal Disease": ["ORS", "ZINC", "IVFLUID"],
    "Acute Diarrhoeal Disease": ["ORS", "ZINC", "IVFLUID"],
    # The 2026 reports' names for the same rehydration need (#43).
    "Acute Diarrhoeal": ["ORS", "ZINC", "IVFLUID"],
    "Acute Gastroenteritis": ["ORS", "ZINC", "IVFLUID"],
    "Cholera": ["ORS", "IVFLUID", "ZINC"],
    "Food Poisoning": ["ORS", "IVFLUID"],
    "Food Borne Illness": ["ORS", "IVFLUID"],
    "Dengue": ["PARA500", "IVFLUID"],
    "Chikungunya": ["PARA500"],
    "Malaria": ["ACT", "PARA500"],
    "Fever": ["PARA500"],
    "Viral Fever": ["PARA500"],
    "Fever with Rash": ["PARA500"],
    "Enteric Fever": ["AMOX", "PARA500"],
    "Leptospirosis": ["AMOX", "PARA500"],
    "Measles": ["PARA500"],
    "Chickenpox": ["PARA500"],
}
MEDICINE_NAMES = {
    "ORS": "Oral Rehydration Salts", "ZINC": "Zinc Sulphate 20mg", "IVFLUID": "IV Fluid Ringer Lactate",
    "PARA500": "Paracetamol 500mg", "ACT": "Artemisinin Combination Therapy", "AMOX": "Amoxicillin 250mg",
}
MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()


def stocking_advice(state_code: str | None, today_month: int) -> list[dict]:
    from . import geo

    calendar = {s.code: s.monsoon_months for s in geo.INDIA_STATES}
    out = []
    for r in outbreaks(state_code):
        codes = DISEASE_MEDICINES.get(r["disease"])
        if not codes or r["state_code"] is None:
            continue
        months = calendar.get(r["state_code"], ())
        start_month = int(r["start_date"][5:7]) if r["start_date"] else None
        signals = [
            f"IDSP week {r['week']}/{r['year']}: {r['cases']} cases, {r['deaths']} deaths "
            f"({r['status'] or 'status not stated'})"
        ]
        if start_month in months or today_month in months:
            signals.append(f"Monsoon months in {r['state']}: {', '.join(MONTHS[m - 1] for m in months)}")
        medicines = [MEDICINE_NAMES[c] for c in codes]
        out.append({
            "unique_id": r["unique_id"], "district": r["district"], "state_code": r["state_code"],
            "disease": r["disease"], "medicines": medicines, "signals": signals,
            "action": f"Declare it active to pre-position {', '.join(medicines[:2])} in "
                      f"{r['district']}. The rise comes from {r['district']}'s own readings, or "
                      "from the surge you expect, labelled as your assumption.",
        })
    return out
