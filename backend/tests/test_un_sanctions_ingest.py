"""UN consolidated list parser: ship vs company IMO, check digit, aliases."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from un_sanctions_ingest import (  # noqa: E402
    extract_company_imos,
    extract_imos_from_text,
    extract_mmsi_from_text,
    extract_ship_imos,
    imo_check_digit_ok,
    parse_un_xml,
)

SAMPLE = BACKEND / "tests" / "data" / "un_consolidated_sample.xml"


def test_imo_check_digit():
    assert imo_check_digit_ok("9058713")
    assert imo_check_digit_ok("1790183")
    assert not imo_check_digit_ok("5571322")
    assert not imo_check_digit_ok("1234568")
    assert not imo_check_digit_ok("905871")


def test_ship_imo_accepted_company_imo_skipped():
    assert extract_ship_imos("IMO: 9058713 Flag State: Cameroon.") == ["9058713"]
    assert extract_imos_from_text("IMO: 9058713") == ["9058713"]
    # Owner/manager company figures — not vessel keys.
    assert extract_ship_imos("IMO number: 5571322.") == []
    assert extract_ship_imos(
        "International Maritime Organization (IMO) Number: 1790183."
    ) == []
    assert extract_ship_imos("Company Number IMO 1790183") == []
    assert extract_company_imos(
        "International Maritime Organization (IMO) Number: 1790183."
    ) == ["1790183"]
    assert extract_company_imos("IMO number: 5571322.") == ["5571322"]
    assert extract_mmsi_from_text("MMSI: 445123456.") == "445123456"


def test_omm_excluded_avax_included():
    """OMM KPe.020 company number must not be a vessel; AVAX LYe.006 must."""
    _publish, count, entries = parse_un_xml(SAMPLE)
    assert count == 4
    by_ref = {e["reference_number"]: e for e in entries}

    avax = by_ref["LYe.006"]
    assert avax["imo"] == "9058713"
    assert avax["name"] == "AVAX"
    assert any(i["id_type"] == "IMO" and i["id_number"] == "9058713" for i in avax["ids"])

    omm = by_ref["KPe.020"]
    assert omm["name"].startswith("OCEAN MARITIME MANAGEMENT")
    assert omm["imo"] is None
    assert omm["mmsi"] is None
    assert any(
        i["id_type"] == "IMO company number" and i["id_number"] == "1790183"
        for i in omm["ids"]
    )

    vessels = [e for e in entries if e["imo"] or e["mmsi"]]
    assert len(vessels) == 1
    assert vessels[0]["reference_number"] == "LYe.006"


def test_parse_sample_xml_aliases_and_invalid_company():
    publish_date, count, entries = parse_un_xml(SAMPLE)
    assert publish_date == "2026-01-15T12:00:00.000Z"
    assert count == 4
    by_ref = {e["reference_number"]: e for e in entries}

    bad = by_ref["KPe.056"]
    assert bad["imo"] is None
    assert any(
        i["id_type"] == "IMO company number" and i["id_number"] == "5571322"
        for i in bad["ids"]
    )
    assert [a["alias_name"] for a in bad["akas"]] == [
        "CHON MYONG SHIPPING COMPANY LIMITED"
    ]

    person = by_ref["CDi.999"]
    assert person["entry_type"] == "Individual"
    assert person["imo"] is None
    assert person["akas"][0]["alias_name"] == "ALIAS PERSON"


@pytest.mark.parametrize(
    "text,ship,company",
    [
        ("IMO: 9058713", ["9058713"], []),
        ("IMO Number: 9058713", [], ["9058713"]),
        ("Company Number IMO 1790183", [], ["1790183"]),
        ("no identifiers", [], []),
    ],
)
def test_imo_patterns(text, ship, company):
    assert extract_ship_imos(text) == ship
    assert extract_company_imos(text) == company
