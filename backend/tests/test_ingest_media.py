"""A photograph must never land in the database.

Twilio hosts inbound media and hands over a URL. The bytes are fetched, read
once by the model, and thrown away; only the provider's reference is stored.

That is not a stylistic preference. One WhatsApp ward photo is roughly 200 KB,
and base64 in a JSONB column is a third larger again. A handful of them would
put more into `raw_payload` than the entire pharmacist workspace and Stage C
were estimated to cost together, on a volume with under 200 MB free — and it
would arrive invisibly, because nothing about a JSONB column announces its
size.

The guarantee is asserted structurally rather than by probing one happy path,
because the failure mode is a *future* handler inlining the bytes "just for
debugging". A runtime test only covers the paths somebody remembered to write;
reading the source covers the ones they have not written yet. This is the same
technique test_api_boundary.py and test_reset_nashik.py use, for the same
reason.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
INGEST = BACKEND / "app" / "ingest.py"
SOURCE = INGEST.read_text(encoding="utf-8")
TREE = ast.parse(SOURCE, filename=str(INGEST))

# The only thing allowed to receive the raw bytes. Anything else holding them
# is a step towards storing them.
ALLOWED_SINKS = {"read_ward_photo"}


def _media_attribute_uses() -> list[ast.AST]:
    """Every `<something>.media` read in the module."""
    return [
        node
        for node in ast.walk(TREE)
        if isinstance(node, ast.Attribute) and node.attr == "media"
    ]


def test_the_bytes_reach_only_the_model():
    """`submission.media` may be truth-tested and handed to the vision
    adapter. It may not be assigned, stored, formatted or logged."""
    uses = _media_attribute_uses()
    assert uses, "submission.media vanished — this guard is now testing nothing"

    for node in uses:
        parent = _parent_of(node)
        if isinstance(parent, ast.If):
            continue                      # `if submission.media:` — a truth test
        if isinstance(parent, ast.Call):
            called = parent.func
            name = getattr(called, "attr", None) or getattr(called, "id", None)
            assert name in ALLOWED_SINKS, (
                "submission.media is passed to {0}(); only {1} may receive the "
                "raw bytes".format(name, " or ".join(sorted(ALLOWED_SINKS)))
            )
            continue
        raise AssertionError(
            "submission.media is used at line {0} in a {1}. The bytes must not "
            "be assigned, stored or formatted anywhere.".format(
                node.lineno, type(parent).__name__
            )
        )


def test_the_stored_reference_is_the_reference_and_not_the_bytes():
    """Read from the syntax tree, not the text.

    A substring assertion cannot do this job: "media_ref=submission.media" is
    a prefix of "media_ref=submission.media_ref", so the obvious negative
    check can never pass. The tree knows the difference.
    """
    passed = []
    for node in ast.walk(TREE):
        if not isinstance(node, ast.Call):
            continue
        for kw in node.keywords:
            if kw.arg != "media_ref":
                continue
            # `media_ref=None` is correct and common: the typed BEDS branch
            # stores no media reference because there was no media.
            if isinstance(kw.value, ast.Constant) and kw.value.value is None:
                passed.append(node.lineno)
                continue
            assert isinstance(kw.value, ast.Attribute), (
                "media_ref at line {0} is neither None nor an attribute — the "
                "only safe values are the provider's reference or nothing at "
                "all".format(node.lineno)
            )
            assert kw.value.attr == "media_ref", (
                "media_ref at line {0} is set from .{1} — that is the bytes, "
                "not the reference".format(node.lineno, kw.value.attr)
            )
            passed.append(node.lineno)
    assert passed, "nothing passes media_ref any more; this guard is testing nothing"


def test_the_spine_does_no_base64_at_all():
    """There is no reason for the ingestion spine to encode anything.

    Checked against the tree rather than the text, because the prose in this
    module legitimately contains the word "base64" while explaining why it
    must never appear in the code.
    """
    offenders = []
    for node in ast.walk(TREE):
        if isinstance(node, ast.Import):
            offenders += [a.name for a in node.names if a.name.startswith("base64")]
        elif isinstance(node, ast.ImportFrom) and (node.module or "").startswith("base64"):
            offenders.append(node.module)
        elif isinstance(node, ast.Name) and node.id == "base64":
            offenders.append("base64 reference at line {0}".format(node.lineno))
        elif isinstance(node, ast.Attribute) and node.attr in ("b64encode", "b64decode"):
            offenders.append("{0} at line {1}".format(node.attr, node.lineno))
    assert not offenders, "the spine encodes something: {0}".format(offenders)


def test_no_raw_payload_is_built_from_media():
    """Belt and braces on the specific column. Any dict literal that mentions
    both a payload key and the media attribute is the exact defect."""
    for node in ast.walk(TREE):
        if not isinstance(node, ast.Dict):
            continue
        rendered = ast.dump(node)
        assert not (
            "raw_payload" in rendered and "'media'" in rendered
        ), "a dict at line {0} puts media into a payload".format(node.lineno)


def _parent_of(target: ast.AST) -> ast.AST | None:
    for node in ast.walk(TREE):
        for child in ast.iter_child_nodes(node):
            if child is target:
                return node
    return None
