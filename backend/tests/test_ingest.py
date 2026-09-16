"""The ingestion spine's grammar and matcher — spec Section 13.

These run on a numeric keypad in a room with one bar of signal. Every test here
is a message someone would plausibly send, and the standard is that a real
report survives a typo rather than that a tidy report parses.
"""

import pytest

from app import ingest

CATALOGUE = {
    "ORS": ["ors", "o r s", "oral rehydration salts", "ओआरएस", "orz"],
    "ZINC": ["zinc", "zinc sulphate", "जिंक"],
    "PARA500": ["paracetamol", "para", "pcm", "dolo"],
    "AMOX": ["amoxicillin", "amox", "amoxy"],
}


def kinds(text: str) -> list[str]:
    return [c.kind for c in ingest.parse(text)]


def readings(text: str) -> list[tuple[str, float]]:
    return [(c.sku_text, c.qty) for c in ingest.parse(text) if c.kind == "reading"]


# ---------------------------------------------------------------- stock ---


def test_the_ordinary_message():
    assert readings("ORS 60 ZINC 20") == [("ORS", 60.0), ("ZINC", 20.0)]


@pytest.mark.parametrize(
    "text",
    ["ORS60", "ors 60", "ORS-60", "ORS:60", "  ORS   60  ", "ors=60"],
)
def test_the_same_report_typed_six_ways(text):
    # A keypad has no shift key worth using and no patience for punctuation.
    assert readings(text) == [("ORS", 60.0)] or readings(text) == [("ors", 60.0)]


def test_hindi_aliases_are_read():
    parsed = readings("ओआरएस 60")
    assert parsed and parsed[0][1] == 60.0
    code, confidence = ingest.resolve_sku(parsed[0][0], CATALOGUE)
    assert code == "ORS" and confidence == 1.0


def test_decimal_quantities_survive():
    assert readings("IVFLUID 2.5") == [("IVFLUID", 2.5)]


# -------------------------------------------------------------- commands ---


def test_the_other_commands_are_recognised():
    assert kinds("HELP") == ["help"]
    assert kinds("BEDS 12") == ["beds"]
    assert kinds("IN") == ["checkin"]
    assert kinds("OUT") == ["checkin"]
    assert kinds("APPROVE 143") == ["approve"]
    assert ingest.parse("APPROVE 143")[0].target_id == 143


def test_a_delivery_is_confirmed_by_batch():
    command = ingest.parse("GOT B2609-004176 480")[0]
    assert command.kind == "receipt"
    assert command.sku_text == "B2609-004176"
    assert command.qty == 480.0


def test_beds_without_a_number_still_parses_so_it_can_be_asked_about():
    assert ingest.parse("BEDS")[0].qty is None


def test_nothing_parseable_is_reported_as_unknown_never_dropped():
    # The pipeline turns this into a reply with an example. Silence is the one
    # outcome that teaches a field worker to stop reporting.
    assert kinds("") == ["unknown"]
    assert kinds("?!?") == ["unknown"]


# --------------------------------------------------------------- matching ---


@pytest.mark.parametrize("typed", ["ORS", "ors", "o r s", "Oral Rehydration Salts"])
def test_exact_names_and_aliases_are_certain(typed):
    assert ingest.resolve_sku(typed, CATALOGUE) == ("ORS", 1.0)


@pytest.mark.parametrize(
    "typed, expected",
    [("paracetmol", "PARA500"), ("amoxicilin", "AMOX"), ("zink", "ZINC"), ("dolo", "PARA500")],
)
def test_misspellings_still_find_the_medicine(typed, expected):
    code, confidence = ingest.resolve_sku(typed, CATALOGUE)
    assert code == expected
    assert 0 < confidence <= 1.0


def test_something_that_is_not_a_medicine_is_refused_not_guessed():
    # Committing the wrong medicine is worse than sending one more SMS.
    code, _ = ingest.resolve_sku("tractor", CATALOGUE)
    assert code is None


def test_an_empty_name_matches_nothing():
    assert ingest.resolve_sku("", CATALOGUE) == (None, 0.0)


# ----------------------------------------------------------------- phones ---


@pytest.mark.parametrize(
    "raw", ["9876543210", "+91 98765 43210", "919876543210", "+91-98765-43210"]
)
def test_one_handset_is_one_identity_however_it_is_written(raw):
    assert ingest.normalise_phone(raw) == "9876543210"
    assert ingest.hash_phone(raw) == ingest.hash_phone("9876543210")


def test_the_number_itself_is_never_recoverable():
    number = "9876543210"
    digest = ingest.hash_phone(number)
    assert number not in digest
    assert len(digest) == 64
    # What a screen may show instead.
    assert ingest.mask_phone(number) == "+91••••3210"


def test_different_handsets_hash_differently():
    assert ingest.hash_phone("9876543210") != ingest.hash_phone("9876543211")


def test_demo_numbers_are_stable_per_facility_and_role():
    a = ingest.demo_number("HFR-MH-PHC-00001", "reporter")
    assert a == ingest.demo_number("HFR-MH-PHC-00001", "reporter")
    assert a != ingest.demo_number("HFR-MH-PHC-00001", "supervisor")
    assert a != ingest.demo_number("HFR-MH-PHC-00002", "reporter")
    assert len(ingest.normalise_phone(a)) == 10
