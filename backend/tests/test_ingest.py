"""The parsing and identity rules of the spine, without a database.

Spec 13. These are the parts that decide whether a message from a numeric
keypad becomes a reading or a refusal, so they are worth pinning precisely:
the grammar is deliberately forgiving, and the fuzzy matcher is deliberately
not, because a wrong medicine committed quietly is worse than a refusal.
"""

from __future__ import annotations

import pytest

from app import ingest


def test_a_phone_number_is_never_stored_in_the_clear():
    number = "+91 98765 43210"
    hashed = ingest.hash_phone(number)
    assert len(hashed) == 64
    assert "9876543210" not in hashed
    # The same handset however it is written: a keypad, a provider and a demo
    # card will all spell it differently.
    assert hashed == ingest.hash_phone("09876543210") == ingest.hash_phone("9876543210")


def test_only_the_last_four_digits_are_ever_shown():
    masked = ingest.mask_phone("+919876543210")
    assert masked.endswith("3210")
    assert "987654" not in masked


@pytest.mark.parametrize(
    "text, expected",
    [
        ("ORS 60", [("ORS", 60.0)]),
        ("ORS60", [("ORS", 60.0)]),
        ("ORS-60", [("ORS", 60.0)]),
        ("ORS: 60", [("ORS", 60.0)]),
        ("ORS 60 ZINC 20", [("ORS", 60.0), ("ZINC", 20.0)]),
        ("ओआरएस 60", [("ओआरएस", 60.0)]),
        # A code that carries its own dose. PARA500 is a real SKU code and
        # "Paracetamol 500mg" is a real SKU name, so the digits inside the
        # medicine are not the quantity — the number after it is. Reading
        # them the other way round wrote 500 to the shelf when the message
        # said 120, which is worse than refusing the message, because a
        # wrong figure is one the reorder threshold and the forecast both
        # believe.
        ("PARA500 120", [("PARA500", 120.0)]),
        ("ORS 60 PARA500 120", [("ORS", 60.0), ("PARA500", 120.0)]),
        ("Paracetamol 500mg 120", [("Paracetamol 500mg", 120.0)]),
        ("AMOX250 75", [("AMOX250", 75.0)]),
        # Still true, and the reason this cannot be fixed by loosening the
        # pattern: glued digits with nothing after them are a quantity.
        ("ORS60 ZINC 20", [("ORS", 60.0), ("ZINC", 20.0)]),
    ],
)
def test_the_grammar_forgives_how_a_keypad_types(text, expected):
    assert ingest.parse_pairs(text) == expected


def test_nothing_numeric_means_nothing_parsed():
    assert ingest.parse_pairs("hello?") == []
    assert ingest.parse_pairs("") == []


LOOKUP = {"ors": "ORS", "oral rehydration salts": "ORS", "zinc": "ZINC", "para500": "PARA500"}


def test_an_exact_name_resolves_outright():
    assert ingest.resolve_sku("ORS", LOOKUP) == ("ORS", 100)


def test_a_misspelling_still_resolves():
    code, score = ingest.resolve_sku("zink", LOOKUP)
    assert code == "ZINC" and score >= ingest.SKU_MATCH_FLOOR


def test_an_unrelated_word_is_refused_rather_than_guessed():
    # The bug this pins: a partial-ratio scorer matched 'tractor' to ORS well
    # enough to commit a reading against the wrong medicine.
    code, score = ingest.resolve_sku("tractor", LOOKUP)
    assert code is None
    assert score < ingest.SKU_MATCH_FLOOR


def test_the_dedupe_key_separates_medicines_within_one_message():
    # channel_msg_id is unique, but one SMS can carry several medicines.
    first = ingest.reading_key("SM123", "ORS")
    second = ingest.reading_key("SM123", "ZINC")
    assert first != second
    assert first.startswith("SM123") and second.startswith("SM123")


def test_a_demo_number_is_stable_and_role_specific():
    a = ingest.demo_number("HFR-MH-PHC-00001", "reporter")
    b = ingest.demo_number("HFR-MH-PHC-00001", "supervisor")
    assert a == ingest.demo_number("HFR-MH-PHC-00001", "reporter")
    assert a != b and len(a) == 10
