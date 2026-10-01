"""The demo role cards say what each role will see, and the two-screen demo
has its second centre.

Fix list #72 and #36. The cards said who ("Maharashtra NHM Officer · MH"),
never what you would see behind them, and there was no donor-centre role, so
the request flow could be shown from one side only.
"""

from __future__ import annotations

from types import SimpleNamespace

from app import auth
from scripts import users

PHC1 = SimpleNamespace(id="HFR-MH-PHC-00001", name="Nashik PHC 1")
PHC13 = SimpleNamespace(id="HFR-MH-PHC-00097", name="Nashik PHC 13")


def sees(role, email="x@demo.swasthsetu.in", name="n", state=None, district=None):
    return auth.demo_account_sees(role=role, email=email, name=name, state=state, district=district)


# ------------------------------------------------------------- the cards ---


def test_the_admin_card_says_it_sees_every_state():
    assert sees("admin").startswith("Every state:")


def test_the_state_officer_card_names_the_state_in_words():
    text = sees("state_officer", state="MH")
    assert text.startswith("Maharashtra:")
    assert "outbreak warnings" in text


def test_the_district_officer_card_names_the_district():
    assert sees("block_mo", state="MH", district="Nashik").startswith("Nashik:")


def test_the_pharmacist_card_says_it_is_a_phone_style_screen():
    assert "phone-style screen" in sees("facility_user", email="pharmacist@demo.swasthsetu.in")


def test_the_donor_card_says_it_is_the_second_screen():
    assert "two-screen demo" in sees("facility_user", email=auth.DEMO_DONOR_EMAIL)


def test_the_primary_pharmacist_is_listed_before_the_donor():
    a = SimpleNamespace(role="facility_user", email=auth.DEMO_DONOR_EMAIL, name="Pharmacist, Nashik PHC 13")
    b = SimpleNamespace(role="facility_user", email="pharmacist@demo.swasthsetu.in", name="Pharmacist, Nashik PHC 1")
    c = SimpleNamespace(role="admin", email="admin@demo.swasthsetu.in", name="Platform Admin")
    assert [u.email for u in sorted([a, b, c], key=auth.demo_account_order)] == [
        c.email, b.email, a.email,
    ]


# ---------------------------------------------------------- the accounts ---


def test_the_demo_creates_a_donor_centre_account_beside_the_pharmacist():
    specs = users.demo_account_specs(PHC1, PHC13)
    donor = [s for s in specs if s["email"] == auth.DEMO_DONOR_EMAIL]
    assert len(specs) == 5
    assert donor == [dict(
        email=auth.DEMO_DONOR_EMAIL, name="Pharmacist, Nashik PHC 13",
        role="facility_user", facility=PHC13.id,
    )]


def test_without_a_second_centre_the_other_four_are_still_created():
    specs = users.demo_account_specs(PHC1, None)
    assert len(specs) == 4
    assert auth.DEMO_DONOR_EMAIL not in {s["email"] for s in specs}


def test_the_donor_is_never_the_pharmacist_s_own_centre():
    assert len(users.demo_account_specs(PHC1, PHC1)) == 4
