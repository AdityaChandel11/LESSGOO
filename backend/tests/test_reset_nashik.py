"""The sandbox reset must stay a sandbox reset.

`scripts/reset_nashik.py` is the one script in the tree whose whole job is to
delete rows. It is safe today because every DELETE it issues is bounded by the
list of facility ids in one district. That is a property of how the statements
happen to be written, and a single dropped `.where(...)` during a later edit
would turn a district cleanup into a national one with no error and no test
failure — the script would simply report a much larger number and run.

So the bound is asserted here rather than trusted: every DELETE in the file has
to name the sandbox id list, directly or through the transfer ids derived from
it. These read the source the way test_api_boundary.py does, because the thing
being protected is the shape of the code, not a value it computes.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "reset_nashik.py"
SOURCE = SCRIPT.read_text(encoding="utf-8")
TREE = ast.parse(SOURCE, filename=str(SCRIPT))

# Names that carry the sandbox bound. `ids` is the district's facility ids;
# `transfer_ids` is selected using `ids` and nothing else.
BOUNDING_NAMES = {"ids", "transfer_ids"}


def _delete_statements() -> list[ast.stmt]:
    """Every expression statement that issues a `delete(...)`.

    Expression statements only, never the `if` or the function that encloses
    them: a compound statement contains the whole surrounding body, so checking
    one for the sandbox names would pass on any file that mentions them
    anywhere and assert nothing about the deletion itself.
    """
    found: list[ast.stmt] = []
    for node in ast.walk(TREE):
        if not isinstance(node, ast.Expr):
            continue
        if any(
            isinstance(inner, ast.Call)
            and isinstance(inner.func, ast.Name)
            and inner.func.id == "delete"
            for inner in ast.walk(node)
        ):
            found.append(node)
    return found


def test_the_script_actually_deletes_something() -> None:
    """Guards the guard: a rename that hid every DELETE would pass silently."""
    assert _delete_statements(), (
        "no delete(...) found in reset_nashik.py — if the script was rewritten to "
        "clean up another way, rewrite this test to bound that way instead"
    )


@pytest.mark.parametrize("index", range(len(_delete_statements())))
def test_every_delete_is_bounded_by_the_sandbox(index: int) -> None:
    statement = _delete_statements()[index]
    names = {n.id for n in ast.walk(statement) if isinstance(n, ast.Name)}
    snippet = ast.get_source_segment(SOURCE, statement) or ""
    assert names & BOUNDING_NAMES, (
        "a DELETE in reset_nashik.py is not bounded by the sandbox facility list. "
        "Every deletion must be constrained by one of {0}:\n\n{1}".format(
            sorted(BOUNDING_NAMES), snippet
        )
    )


def test_transfer_ids_are_themselves_bounded() -> None:
    """`transfer_ids` only counts as a bound while it is selected from `ids`."""
    for node in ast.walk(TREE):
        if (
            isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "transfer_ids" for t in node.targets)
        ):
            names = {n.id for n in ast.walk(node.value) if isinstance(n, ast.Name)}
            assert "ids" in names, (
                "transfer_ids is no longer derived from the sandbox facility list, so "
                "the deletes bounded by it are no longer bounded by the district"
            )
            return
    pytest.fail("transfer_ids is not assigned in reset_nashik.py")


def test_the_district_is_named_once_and_is_the_sandbox() -> None:
    from scripts.reset_nashik import DISTRICT, STATE

    assert (STATE, DISTRICT) == ("MH", "Nashik"), (
        "the sandbox moved. That is a decision, not a refactor: the live loop's "
        "UI names this district too (frontend/src/liveloop.tsx, SANDBOX)"
    )
