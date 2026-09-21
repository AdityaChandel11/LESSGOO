"""The facilities listing must not read the country to answer about a district.

`GET /api/facilities` used to score every facility and then filter the result
in Python. Scoring a facility means reading its stock history over the burn-rate
window, so asking for one district cost exactly what asking for the whole
country cost — about 1.18M rows on the deployed database. Measured there, the
call never returned inside 300 seconds and left roughly 70 MB of temporary
files behind on each attempt, against a volume with about 100 MB free.

The fix is ordering: resolve the filter to a bounded set of facility ids in
SQL, then read only those. These tests hold that ordering in place, because it
is the kind of thing a later refactor undoes without noticing — the endpoint
returns the same shape either way, and on a small local database both versions
feel instant.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from app import api

API_SOURCE = Path(api.__file__)


def _endpoint() -> ast.AsyncFunctionDef:
    tree = ast.parse(API_SOURCE.read_text(encoding="utf-8"), filename=str(API_SOURCE))
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "list_facilities":
            return node
    pytest.fail("list_facilities is gone; if it was renamed, rename it here too")


def _calls(node: ast.AST) -> list[str]:
    """Every call in source order, as dotted names."""
    found: list[str] = []
    for inner in ast.walk(node):
        if not isinstance(inner, ast.Call):
            continue
        target = inner.func
        parts: list[str] = []
        while isinstance(target, ast.Attribute):
            parts.append(target.attr)
            target = target.value
        if isinstance(target, ast.Name):
            parts.append(target.id)
        if parts:
            found.append(".".join(reversed(parts)))
    # ast.walk is breadth-first over the tree, not source order, so sort by
    # the position each call actually appears at.
    positions = [
        (inner.lineno, inner.col_offset)
        for inner in ast.walk(node)
        if isinstance(inner, ast.Call)
    ]
    return [name for _, name in sorted(zip(positions, found), key=lambda p: p[0])]


def test_the_filter_is_resolved_before_any_reading_is_read() -> None:
    calls = _calls(_endpoint())
    assert "aggregates.find_facilities" in calls, (
        "the listing no longer resolves its filter in SQL; it is back to scoring "
        "every facility and filtering the result"
    )
    assert "services.get_snapshots" in calls, "the listing still needs per-SKU detail"
    assert calls.index("aggregates.find_facilities") < calls.index(
        "services.get_snapshots"
    ), (
        "get_snapshots runs before the filter is resolved, so it is reading more "
        "facilities than the caller asked for — the exact bug this endpoint had"
    )


def test_get_snapshots_is_given_an_explicit_set_of_ids() -> None:
    """Calling it with no ids means 'every facility', which is the bug."""
    for node in ast.walk(_endpoint()):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get_snapshots"
        ):
            assert len(node.args) >= 2 or node.keywords, (
                "get_snapshots was called without a facility list, which scores "
                "every facility in the database"
            )
            return
    pytest.fail("no get_snapshots call found in list_facilities")


def test_the_page_is_bounded_and_the_bound_is_sane() -> None:
    signature = inspect.signature(api.list_facilities)
    assert "limit" in signature.parameters, (
        "an unbounded listing is what filled the disk; keep the page bound"
    )
    query = signature.parameters["limit"].default
    assert query.default == api.FACILITY_PAGE_DEFAULT
    # The ceiling lives in the field's constraint metadata, not on the Query
    # object. A default without a ceiling is not a bound at all, because the
    # caller is the one who picks the number.
    ceilings = [m.le for m in query.metadata if hasattr(m, "le")]
    assert ceilings == [api.FACILITY_PAGE_MAX], (
        "the page has no upper bound, so a caller can still ask for every facility"
    )
    assert 1 <= api.FACILITY_PAGE_DEFAULT <= api.FACILITY_PAGE_MAX <= 1000


def test_the_status_filter_is_not_applied_after_the_page_is_cut() -> None:
    """Filtering status in Python after a LIMIT would silently change what the
    endpoint means: you would ask for critical facilities and get however many
    of the first N happened to be critical."""
    source = ast.get_source_segment(
        API_SOURCE.read_text(encoding="utf-8"), _endpoint()
    ) or ""
    assert "s.status == status" not in source, (
        "status is being filtered in Python again; it belongs in the query, "
        "where it applies to the whole scope rather than to one page"
    )


def test_the_listing_still_orders_worst_first() -> None:
    source = ast.get_source_segment(
        API_SOURCE.read_text(encoding="utf-8"), _endpoint()
    ) or ""
    assert ".sort(" in source, (
        "the listing must still present the most urgent facility first; the "
        "resolver returns that order and it has to survive the second read"
    )
