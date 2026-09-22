"""SwasthSetu backend application package.

Importing this package forces stdout and stderr to UTF-8. That is a side
effect at import time, which is normally worth avoiding — but the alternative
here is worse, and the bug it fixes is one this project has already hit.

This system carries Hindi throughout: facility names, the bilingual briefing,
the labels on every screen. On Windows a console is usually cp1252, and
`print()` of a Devanagari string against cp1252 raises UnicodeEncodeError. That
is not a cosmetic failure. It aborts whatever was running, mid-way, *after* the
work has already happened — a release check that has passed fifteen assertions
reports ERROR, a paid model call comes back and is lost on the way to the
screen, a seed script dies between two writes. In every one of those the
program was correct and the terminal was the only thing that failed.

Every entry point in this repository — `python -m checks`, `python -m
scripts.*`, pytest, uvicorn — imports something from this package before it
prints anything. So this is the one place that fixes it for all of them at
once, including entry points nobody has written yet.

`errors="backslashreplace"` rather than `strict` or `ignore`: a terminal that
genuinely cannot render a character should show an escape, not silently drop
it and not bring the process down. Losing the glyph is acceptable; losing the
run is not.
"""

import sys

__version__ = "0.1.0"


def force_utf8_console() -> None:
    """Make stdout and stderr able to carry any text this system holds.

    Idempotent, and safe to call when the streams are not real consoles.
    pytest and some CI runners replace them with capture objects that have no
    `reconfigure`, and a detached process may have no stdout at all — neither
    is an error, and neither should stop the application starting.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="backslashreplace")
        except (ValueError, OSError):
            # A closed or non-reconfigurable stream. Printing is not this
            # function's job to guarantee; not crashing is.
            continue


force_utf8_console()
