"""Check the Gemini key before relying on it.

    python -m scripts.check_gemini

Sends one small generated image and asks for the same strict JSON the ward-photo
pipeline asks for. It proves the key, the model name, and the JSON response
path all work; it does not measure how well the model counts beds, which only a
real photograph can show.
"""

from __future__ import annotations

import asyncio
import struct
import zlib

from app import vision
from app.config import settings


def tiny_png(size: int = 64) -> bytes:
    """A plain grey square, built here so the check needs no image library."""
    raw = b"".join(b"\x00" + bytes([200] * size * 3) for _ in range(size))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


async def main() -> int:
    print(f"LLM_MODE={settings.llm_mode}  model={settings.gemini_model}")
    if not settings.gemini_api_key:
        print("  FAIL  GEMINI_API_KEY is not set")
        return 1
    if settings.llm_mode != "live":
        print("  note  LLM_MODE is not 'live'; checking the key anyway")

    try:
        # Bypass the mode switch: the point here is to test the live path.
        result = await vision._call_gemini(tiny_png(), "image/png")
    except vision.VisionError as exc:
        print(f"  FAIL  {exc}")
        print("        Check that the key is valid, the Generative Language API is")
        print("        enabled for its project, and billing is active.")
        return 1

    print(f"  OK    model answered as {result.model}")
    print(f"        beds_total={result.beds_total} beds_occupied={result.beds_occupied} "
          f"code={result.code_read} confidence={result.confidence}")
    print("\nThe key works. Set LLM_MODE=live to use it for ward photos.")
    print("On a blank image the counts will be nonsense — that is expected.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
