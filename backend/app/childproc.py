"""Running a child process without losing its output or leaking a credential.

Two defects this repository had at four call sites each, and both are the kind
that pass code review because the wrong half of the pair is present.

**Decoding.** Every site already set `PYTHONIOENCODING=utf-8` on the child, so
the child encoded its output as UTF-8 — and then read it back with
`subprocess.run(..., text=True)` and no `encoding=`, which decodes using the
parent's locale. On Windows that is cp1252. The failure is not a crash: it is
raised inside subprocess's reader thread, `run()` returns normally with
`returncode == 0`, and `proc.stdout` is silently `None`. The caller then
crashes on `None.strip()` or reports "no output" and blames the child. A check
that says the wrong thing is worse than one that fails.

**Credentials.** `_block_live_calls` is a module global and `settings` is an
in-process singleton; neither crosses a process boundary. A child built with
`dict(os.environ, ...)` inherits the real key and re-reads the repository's
`.env`, which sets `LLM_MODE=live`. So an automated parent that had carefully
blocked its own live calls would hand a child everything it needed to make
one.

Both are fixed here rather than at each call site, because four call sites is
already the evidence that per-site discipline does not hold.
"""

from __future__ import annotations

import os
import subprocess
from typing import Any

# Told to the child so it *emits* UTF-8, and used by us so we *read* UTF-8.
# Setting one without the other is the bug this module exists to prevent.
UTF8_VARS = {"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}

# Anything that could let a child spend real quota or reach a paid API. Dropped
# by default: a child of an automated run has no business making a live call,
# and `.env` would otherwise hand it one.
CREDENTIAL_VARS = (
    "GEMINI_API_KEY",
    "GOOGLE_MAPS_SERVER_KEY",
    "TWILIO_AUTH_TOKEN",
    "TWILIO_API_KEY_SECRET",
)


def inherit_env(*, keep_credentials: bool = False, **extra: str) -> dict[str, str]:
    """This process's environment, made safe to hand to a child.

    UTF-8 is forced so the child can print any text this system holds — which
    matters most for `backend/federation/`, whose modules do not import `app`
    and therefore never run `force_utf8_console()`.

    Credentials are removed and the mode switches pinned to their local
    branches unless a caller explicitly asks otherwise, so a child cannot make
    a live call its parent had already decided not to make.
    """
    env = dict(os.environ, **UTF8_VARS, **extra)
    if not keep_credentials:
        for name in CREDENTIAL_VARS:
            env.pop(name, None)
        # Explicit rather than merely absent: the child re-reads the repo's
        # .env, where these are set, so popping the variable is not enough.
        env.setdefault("LLM_MODE", "mock")
        env["LLM_MODE"] = "mock"
        env["MAPS_MODE"] = "osm"
        env["COMMS_MODE"] = "simulator"
    return env


def run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
    """`subprocess.run` that can actually read what the child said.

    Always decodes as UTF-8 with `errors="replace"`. Replacement is deliberate:
    an undecodable byte should cost one character, not the whole stream and not
    the run. `text=True`/`encoding=`/`errors=` passed by a caller are ignored
    in favour of these, because getting them wrong is the entire defect.

    If no `env` is given, a de-credentialled UTF-8 environment is built. A
    caller that passes its own `env` gets the UTF-8 variables merged in but
    keeps everything else it chose — `checks/platform.py` builds a deliberately
    empty environment to prove the app starts with no configuration at all, and
    this must not quietly refill it.
    """
    kwargs.pop("text", None)
    kwargs.pop("universal_newlines", None)
    env = kwargs.pop("env", None)
    env = inherit_env() if env is None else {**env, **UTF8_VARS}
    return subprocess.run(
        cmd, env=env, encoding="utf-8", errors="replace", **kwargs
    )
