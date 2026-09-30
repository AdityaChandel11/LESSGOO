"""No unsourced numbers on screen (standing rule; fix list, Aditya 2026-10-01).

The map's "Simulated data" tooltip and the demo-data page said the seed covers
"roughly 12%" of the national PHC network. No edition of Rural Health
Statistics is cited for the per-state counts, and the code's own figures
(3,510 centres, CHCs included, against ~31,900 PHCs) do not give 12%. A share
of the real network is a claim about the real network, so it needs a source;
until it has one, the pages say what is known: the count, and that it is a
sample.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SURFACES = [
    *sorted((ROOT / "frontend" / "src").rglob("*.tsx")),
    ROOT / "backend" / "scripts" / "seed.py",
    ROOT / "backend" / "app" / "geo.py",
]
CLAIM = re.compile(r"(roughly|~)\s*12\s*%|12\s*%\s*(proportional|of (the|its|each))", re.I)


def test_no_page_or_pitch_note_claims_a_share_of_the_real_network():
    offenders = [
        f"{path.relative_to(ROOT)}:{n}"
        for path in SURFACES
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if CLAIM.search(line)
    ]
    assert offenders == []
