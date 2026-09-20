"""Ward-photo verification — spec 26.2.

The rule under test throughout: rejection needs a contradiction. A check that
could not run leaves a report unverified, which asks for a better photo; it
never accuses a facility of anything.
"""

import asyncio

import pytest

from app import beds, vision
from app.beds import REJECTED, UNVERIFIED, VERIFIED
from app.vision import BedExtraction

FACILITY = (19.9975, 73.7898)  # Nashik


def extraction(**kw) -> BedExtraction:
    return BedExtraction(
        beds_total=kw.get("beds_total", 20),
        beds_occupied=kw.get("beds_occupied", 14),
        code_read=kw.get("code_read", "AB12"),
        confidence=kw.get("confidence", 0.85),
        notes=None,
        model=kw.get("model", "mock"),
    )


def verify(**kw):
    return beds.verify(
        kw.pop("extraction", extraction()),
        expected_code=kw.pop("expected_code", "AB12"),
        loc_method=kw.pop("loc_method", "gps"),
        distance=kw.pop("distance", 0.02),
        register_admissions=kw.pop("register_admissions", 14),
    )


# ------------------------------------------------------------ the code ---


def test_todays_code_from_the_right_place_verifies():
    checks = verify()
    assert checks.verification == VERIFIED
    assert checks.code_ok and checks.geofence_ok and checks.register_ok


def test_yesterdays_photo_is_rejected():
    checks = verify(extraction=extraction(code_read="ZZ99"))
    assert checks.verification == REJECTED
    assert checks.code_ok is False
    assert "not from today" in " ".join(checks.reasons)


def test_code_matching_is_case_insensitive():
    # The code is written by hand on a whiteboard; case is not evidence.
    assert verify(extraction=extraction(code_read="ab12")).code_ok is True


def test_an_illegible_code_asks_again_rather_than_accusing():
    checks = verify(extraction=extraction(code_read=None))
    assert checks.verification == UNVERIFIED
    assert checks.code_ok is None


# ----------------------------------------------------------- the place ---


def test_a_gps_photo_from_elsewhere_is_rejected():
    checks = verify(distance=4.0)
    assert checks.verification == REJECTED
    assert checks.geofence_ok is False


def test_cell_id_gets_a_radius_that_matches_its_accuracy():
    # 1.2 km would be damning for GPS and is unremarkable for a cell tower.
    assert verify(loc_method="cell_id", distance=1.2).geofence_ok is True
    assert verify(loc_method="gps", distance=1.2).geofence_ok is False


def test_a_coarse_location_failure_does_not_reject_on_its_own():
    # Cell-ID is kilometres-vague, so a miss is a question, not a verdict.
    checks = verify(loc_method="cell_id", distance=9.0)
    assert checks.geofence_ok is False
    assert checks.verification == UNVERIFIED


def test_a_channel_with_no_location_cannot_be_geofenced():
    checks = verify(loc_method="none", distance=None)
    assert checks.geofence_ok is None
    assert checks.verification == UNVERIFIED
    assert "no location" in " ".join(checks.reasons).lower()


def test_geofence_radius_is_none_where_no_location_exists():
    assert beds.geofence_radius_km("none") is None
    assert beds.geofence_radius_km(None) is None
    assert beds.geofence_radius_km("gps") < beds.geofence_radius_km("cell_id")


def test_distance_is_measured_on_the_ground():
    # Nashik to Sinnar, ~24 km apart.
    km = beds.distance_km(*FACILITY, 19.8467, 73.9976)
    assert 20 < km < 30


# ---------------------------------------------------------- the paper ---


def test_a_count_the_register_disputes_is_flagged_but_not_rejected():
    checks = verify(extraction=extraction(beds_occupied=18), register_admissions=4)
    assert checks.register_ok is False
    # Two independent signals disagreeing is worth a look, not a verdict: the
    # register is handwritten and the photo is one moment of the day.
    assert checks.verification == VERIFIED
    assert "register" in " ".join(checks.reasons)


def test_small_register_differences_are_agreement():
    assert verify(extraction=extraction(beds_occupied=15), register_admissions=14).register_ok


def test_an_empty_ward_matching_an_empty_register_agrees():
    checks = verify(extraction=extraction(beds_occupied=0), register_admissions=0)
    assert checks.register_ok is True


# ------------------------------------------------------- the extraction ---


def test_a_low_confidence_count_is_not_recorded_as_verified():
    checks = verify(extraction=extraction(confidence=0.2))
    assert checks.verification == UNVERIFIED
    assert "confidence" in " ".join(checks.reasons).lower()


def test_more_beds_occupied_than_exist_is_a_misread_not_a_finding():
    parsed = vision.parse_extraction(
        {"beds_total": 10, "beds_occupied": 40, "confidence": 0.9}, model="test"
    )
    assert parsed.beds_total == 10
    assert parsed.beds_occupied is None


@pytest.mark.parametrize(
    "payload",
    [
        {"beds_total": "twelve", "beds_occupied": 4, "confidence": 0.9},
        {"beds_total": -3, "beds_occupied": 4, "confidence": 0.9},
        {"beds_total": True, "beds_occupied": 4, "confidence": 0.9},
    ],
)
def test_implausible_counts_become_absent_rather_than_wrong(payload):
    assert vision.parse_extraction(payload, model="test").beds_total is None


def test_confidence_outside_the_range_is_not_trusted():
    assert vision.parse_extraction({"confidence": 7}, model="test").confidence == 0.0


def test_a_blank_code_reads_as_no_code():
    assert vision.parse_extraction({"code_read": "   "}, model="test").code_read is None
    assert vision.parse_extraction({"code_read": " ab12 "}, model="test").code_read == "AB12"


def test_fenced_json_from_the_model_is_still_read():
    parsed = vision._first_json_object('```json\n{"beds_total": 8}\n```')
    assert parsed == {"beds_total": 8}


def test_unreadable_model_output_raises_rather_than_inventing_numbers():
    with pytest.raises(vision.VisionError):
        vision._first_json_object("I counted about eight beds")


def test_mock_mode_needs_no_photo_and_labels_itself(monkeypatch):
    # Pinned rather than inherited: this asserts what the mock path does, and it
    # must keep asserting that on a machine where a real key is configured.
    monkeypatch.setattr(vision.settings, "llm_mode", "mock")
    result = asyncio.run(vision.read_ward_photo(None, simulate={"beds_occupied": 9}))
    assert result.beds_occupied == 9
    assert result.is_mock and result.model == "mock"


def test_live_mode_refuses_to_invent_a_photo(monkeypatch):
    # The other half of the same rule: with a key configured, a caller cannot
    # ask for a simulated reading and be handed one that looks real.
    monkeypatch.setattr(vision.settings, "llm_mode", "live")
    monkeypatch.setattr(vision.settings, "gemini_api_key", "not-a-real-key")
    with pytest.raises(vision.VisionError):
        asyncio.run(vision.read_ward_photo(None, simulate={"beds_occupied": 9}))


# ------------------------------------------------------------- the code ---


def test_generated_codes_avoid_ambiguous_characters():
    # O/0 and I/1 are the difference between a rejected photo and a verified
    # one when a code is handwritten and read back from an image.
    codes = {beds.generate_code() for _ in range(200)}
    assert all(len(c) == beds.CODE_LENGTH for c in codes)
    assert not set("".join(codes)) & set("O0I1S5")
    # Unpredictable: 200 draws from a 30^4 space should not repeat much.
    assert len(codes) > 190
