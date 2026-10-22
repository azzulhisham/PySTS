"""UK FCDO sanctions parser: Ship IMO check digit, alias dedupe, load guards."""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from uk_sanctions_ingest import (  # noqa: E402
    extract_ship_imo,
    imo_check_digit_ok,
    parse_uk_xml,
    ship_count_drop_too_large,
    should_skip_unchanged_date,
    vessel_count,
)

SAMPLE = BACKEND / "tests" / "data" / "uk_sanctions_sample.xml"


def test_imo_check_digit_and_extract():
    assert imo_check_digit_ok("9562233")
    assert not imo_check_digit_ok("5571322")
    assert extract_ship_imo("IMO9562233") == "9562233"
    assert extract_ship_imo("IMO5571322") is None
    assert extract_ship_imo("not-an-imo") is None


def test_parse_ship_imo_alias_dedupe_and_entity_no_imo():
    publish_date, count, entries = parse_uk_xml(SAMPLE)
    assert publish_date == "16/10/2026"
    # Duplicate UniqueID DPR0075 dropped from count of unique entries.
    assert count == 3
    by_uid = {e["uid"]: e for e in entries}

    ship = by_uid["DPR0075"]
    assert ship["entry_type"] == "Ship"
    assert ship["imo"] == "9562233"
    assert ship["vessel_flag"] == "Comoros"
    assert ship["vessel_type"] == "Bulk Carrier"
    assert ship["vessel_owner"] == "Sample Owner"
    # Duplicate "Jal Vahini" collapsed; Jal_Vahini kept as distinct string.
    assert [a["alias_name"] for a in ship["akas"]] == ["Jal Vahini", "Jal_Vahini"]
    assert any(i["id_type"] == "IMO" and i["id_number"] == "9562233" for i in ship["ids"])

    bad = by_uid["DPR0999"]
    assert bad["entry_type"] == "Ship"
    assert bad["imo"] is None
    assert not any(i["id_type"] == "IMO" for i in bad["ids"])

    entity = by_uid["AFG0001"]
    assert entity["entry_type"] == "Entity"
    assert entity["imo"] is None
    assert entity["name"] == "SAMPLE ENTITY"
    assert [a["alias_name"] for a in entity["akas"]] == ["Entity Alias"]

    assert vessel_count(entries) == 1


def test_skip_unchanged_date_generated():
    assert should_skip_unchanged_date("16/10/2026", "16/10/2026") is True
    assert should_skip_unchanged_date("16/10/2026", "15/10/2026") is False
    assert should_skip_unchanged_date("16/10/2026", None) is False
    assert should_skip_unchanged_date(None, "16/10/2026") is False


def test_ship_count_10pct_drop_guard():
    assert ship_count_drop_too_large(682, 682) is False
    assert ship_count_drop_too_large(620, 682) is False  # ~9.1%
    assert ship_count_drop_too_large(610, 682) is True  # ~10.6%
    assert ship_count_drop_too_large(0, 100) is True
    assert ship_count_drop_too_large(50, None) is False
    assert ship_count_drop_too_large(50, 0) is False
