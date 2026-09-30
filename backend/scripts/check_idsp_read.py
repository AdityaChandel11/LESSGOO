"""Spike for fix #42: does Gemini read a real IDSP weekly report correctly?

    set LLM_MODE=live and GEMINI_API_KEY in backend/.env, then
    python -m scripts.check_idsp_read path\\to\\week38.pdf

One model request (the free tier allows 20 a day). Nothing is written to any
database. Prints every row the model read, the regex parser's verdict on each
(it re-reads the row from the printed text the model transcribed), and the
totals. With pypdf installed (requirements-dev.txt) it also parses the PDF's
own text independently and reports rows the model missed — the stronger
check, and the one to read before trusting the pipeline.
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from app import idsp, vision
from app.config import settings


def independent_ids(pdf: Path) -> set[str] | None:
    try:
        from pypdf import PdfReader
    except ImportError:
        return None
    text = "\n".join(page.extract_text() or "" for page in PdfReader(str(pdf)).pages)
    return {r["unique_id"] for r in idsp.parse_report(text, idsp.state_names())}


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("pdf", type=Path)
    args = ap.parse_args()
    if settings.llm_mode != "live" or not settings.gemini_api_key:
        print("Set LLM_MODE=live and GEMINI_API_KEY first; nothing was sent.")
        return 1

    read = await vision.read_idsp_report(args.pdf.read_bytes())
    print(f"model {read.model} · week {read.week}/{read.year} · {len(read.rows)} rows "
          f"({read.dropped} dropped as unreadable)\n")
    counts = {"agrees": 0, "disagrees": 0, "unparsed": 0}
    for row in read.rows:
        check = idsp.cross_check(row)
        counts[check["verdict"]] += 1
        flag = check["verdict"] + (f" [{', '.join(check['fields'])}]" if check["fields"] else "")
        print(f"  {row['unique_id']:<22} {row['district']:<18} {row['disease']:<26} "
              f"{row['cases']:>5} {row['deaths']:>4}  {flag}")
    print(f"\nparser agrees {counts['agrees']} · disagrees {counts['disagrees']} · "
          f"could not read {counts['unparsed']}")

    ids = independent_ids(args.pdf)
    if ids is None:
        print("pypdf not installed: no independent check of rows the model missed.")
    else:
        model_ids = {r["unique_id"] for r in read.rows}
        missed = sorted(ids - model_ids)
        extra = sorted(model_ids - ids)
        print(f"independent parse of the PDF text: {len(ids)} rows · model missed {len(missed)} · "
              f"model found {len(extra)} the parser did not")
        for uid in missed:
            print(f"  missed by the model: {uid}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
