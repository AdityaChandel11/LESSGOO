"""Draw the synthetic ward whiteboard that the live vision check reads.

    python -m scripts.ward_photo --total 12 --occupied 7 --code 4PAW --out ward.png

A real ward reports by photographing the whiteboard beside the door: the bed
count, the occupied count, and the day's four-character verification code
written on it by hand. This draws that board so the live Gemini path can be
exercised without a real photograph, and so the same image can be regenerated
exactly when a demo needs it.

Pillow is a development dependency only (requirements-dev.txt). Nothing in the
application imports this module.
"""

from __future__ import annotations

import argparse
import io
import random
from datetime import date
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

WIDTH, HEIGHT = 900, 620
BOARD = (250, 250, 248)
INK = (26, 32, 44)
MARKER = (37, 60, 108)
FONT_CANDIDATES = ("segoeui.ttf", "arial.ttf", "DejaVuSans.ttf")


def _font(size: int) -> ImageFont.ImageFont:
    for name in FONT_CANDIDATES:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


def draw(total: int, occupied: int, code: str, *, ward: str = "GENERAL", seed: int = 7) -> bytes:
    """A whiteboard photograph, as close as a drawing gets to one."""
    rng = random.Random(seed)
    image = Image.new("RGB", (WIDTH, HEIGHT), (120, 124, 130))
    d = ImageDraw.Draw(image)

    # The board itself, inset, so the image looks photographed rather than drawn.
    d.rounded_rectangle((40, 40, WIDTH - 40, HEIGHT - 40), radius=10, fill=BOARD, outline=(90, 94, 100), width=6)

    d.text((80, 80), "WARD BED STATUS", font=_font(46), fill=INK)
    d.line((80, 140, WIDTH - 80, 140), fill=(180, 184, 190), width=3)

    d.text((80, 175), "WARD", font=_font(28), fill=(90, 96, 108))
    d.text((300, 170), ward.upper(), font=_font(38), fill=MARKER)

    d.text((80, 255), "TOTAL BEDS", font=_font(28), fill=(90, 96, 108))
    d.text((300, 245), str(total), font=_font(56), fill=MARKER)

    d.text((80, 340), "OCCUPIED", font=_font(28), fill=(90, 96, 108))
    d.text((300, 330), str(occupied), font=_font(56), fill=MARKER)

    d.text((80, 435), "CODE", font=_font(28), fill=(90, 96, 108))
    d.text((300, 420), code.upper(), font=_font(64), fill=(150, 30, 40))

    d.text((80, HEIGHT - 110), date.today().isoformat(), font=_font(26), fill=(120, 126, 136))
    d.text(
        (330, HEIGHT - 110),
        "synthetic board, generated for testing",
        font=_font(22),
        fill=(150, 156, 166),
    )

    # A little marker wobble, so the model is not reading a perfect render.
    for _ in range(90):
        x, y = rng.randint(50, WIDTH - 50), rng.randint(50, HEIGHT - 50)
        d.point((x, y), fill=(200, 202, 206))

    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--total", type=int, required=True)
    ap.add_argument("--occupied", type=int, required=True)
    ap.add_argument("--code", required=True)
    ap.add_argument("--ward", default="GENERAL")
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_bytes(draw(args.total, args.occupied, args.code, ward=args.ward))
    print("wrote {0} ({1:,} bytes)".format(args.out, args.out.stat().st_size))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
