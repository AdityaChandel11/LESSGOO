"""Parse IDSP Weekly Outbreak Report PDFs into app/data/idsp_outbreaks.json.

    pip install pypdf        # dev-only; the web service never reads a PDF
    python -m scripts.idsp_ingest path/to/week53.pdf path/to/week24.pdf

Runs once, offline. Keeps only the structured columns of each outbreak row
(state, district, disease, cases, deaths, dates, status) and discards the rest
of the PDF, including the free-text comments. The output is a small committed
file, not a database table, so loading it costs the deployed database nothing.
"""

from __future__ import annotations

import argparse
import json
import sys

from app import geo, idsp


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("pdfs", nargs="+")
    args = p.parse_args()
    try:
        from pypdf import PdfReader
    except ImportError:
        raise SystemExit("pypdf is needed to read the PDFs: pip install pypdf")

    names = {s.name: s.code for s in geo.INDIA_STATES}
    aliases = {"Jammu and Kashmir": "Jammu & Kashmir", "Orissa": "Odisha"}
    state_names = list(names) + list(aliases)

    rows, reports = [], []
    for path in args.pdfs:
        text = "\n".join(page.extract_text() or "" for page in PdfReader(path).pages)
        parsed = idsp.parse_report(text, state_names)
        for r in parsed:
            canonical = aliases.get(r["state"], r["state"])
            r["state"] = canonical
            r["state_code"] = names.get(canonical)
        rows.extend(parsed)
        found = len(idsp.UNIQUE_ID.findall(text))
        if parsed:
            reports.append({"year": parsed[0]["year"], "week": parsed[0]["week"], "rows": len(parsed)})
        print(f"{path}: {len(parsed)} of {found} outbreak rows parsed", file=sys.stderr)

    idsp.DATA_FILE.write_text(
        json.dumps(
            {"source": idsp.SOURCE, "source_url": idsp.SOURCE_URL, "columns": list(idsp.COLUMNS),
             "reports": reports, "rows": rows},
            indent=1, ensure_ascii=False,
        ) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {len(rows)} rows to {idsp.DATA_FILE}", file=sys.stderr)


if __name__ == "__main__":
    main()
