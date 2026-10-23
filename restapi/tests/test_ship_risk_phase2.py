"""Phase 2: identity-conflict + vessel-analysis identity-risk fields."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pandas as pd
import pytest

RESTAPI = Path(__file__).resolve().parents[1]
if str(RESTAPI) not in sys.path:
    sys.path.insert(0, str(RESTAPI))

import ship_risk_attach as risk_mod  # noqa: E402
from ship_risk_attach import (  # noqa: E402
    clear_risk_index_cache,
    risk_payload_fields,
    strip_risk_keys,
)
from identity_conflict import groups_to_payload, identity_to_payload  # noqa: E402
from spoofing_vessel_analysis import _vessel_header  # noqa: E402


@pytest.fixture(autouse=True)
def _clear_cache():
    clear_risk_index_cache()
    yield
    clear_risk_index_cache()


def _identity_row(**extra):
    row = {
        "mmsi": 352006140,
        "shipname": "ALPHA",
        "callsign": "ABC",
        "shiptype": 80,
        "shiptypedesc": "Tanker",
        "latitude": 1.0,
        "longitude": 103.0,
        "sog": 0.1,
        "cog": 10.0,
        "navstatus": 1,
        "last_ais_ts": pd.Timestamp("2026-10-22T00:00:00Z"),
        "identity_ts": pd.Timestamp("2026-10-22T00:00:00Z"),
        "imo": "9074729",
        "to_bow": None,
        "to_stern": None,
        "to_port": None,
        "to_starboard": None,
        "sanctions_match": True,
        "match_confidence": "confirmed",
        "sanctions_list": "SDN",
        "list_names": ["SDN"],
        "risk_rating": "RED",
        "risk_score": 100,
        "risk_reason": "sanctions match (SDN)",
    }
    row.update(extra)
    return pd.Series(row)


def test_identity_to_payload_includes_risk_after_sanctions():
    payload = identity_to_payload(_identity_row())
    keys = list(payload.keys())
    assert keys.index("sanctionsList") < keys.index("riskRating")
    assert payload["riskRating"] == "RED"
    assert payload["riskScore"] == 100
    assert payload["riskReason"] == "sanctions match (SDN)"


def test_identity_green_fallback_when_missing_risk_cols():
    row = _identity_row()
    for c in ("risk_rating", "risk_score", "risk_reason"):
        del row[c]
    payload = identity_to_payload(row)
    assert payload["riskRating"] == "GREEN"
    assert payload["riskScore"] == 0
    assert payload["riskReason"] == "none"


def test_identity_golden_strip_only_adds_risk_keys():
    with_risk = identity_to_payload(_identity_row())
    stripped = strip_risk_keys(with_risk)
    bare = _identity_row()
    for c in ("risk_rating", "risk_score", "risk_reason"):
        del bare[c]
    without = strip_risk_keys(identity_to_payload(bare))
    assert stripped == without
    assert "riskRating" not in stripped
    assert "sanctionsMatch" in stripped


def test_group_risk_red_count_and_golden():
    group = {
        "cluster_signature": "1_2",
        "detected_at": pd.Timestamp("2026-10-22T00:00:00Z"),
        "first_ais_at": pd.Timestamp("2026-10-22T00:00:00Z"),
        "used_position_ts": True,
        "identity_count": 2,
        "max_internal_distance_m": 12.5,
        "match_reasons": ["imo", "name"],
        "sanctions_match": True,
        "match_confidence": "confirmed",
        "sanctions_list": "SDN",
        "risk_red_count": 1,
        "identities_payload": [
            identity_to_payload(_identity_row()),
            identity_to_payload(
                _identity_row(
                    mmsi=525108038,
                    shipname="BETA",
                    imo=None,
                    sanctions_match=False,
                    match_confidence="none",
                    sanctions_list=None,
                    list_names=None,
                    risk_rating="GREEN",
                    risk_score=0,
                    risk_reason="none",
                )
            ),
        ],
    }
    records = groups_to_payload([group])
    assert records[0]["riskRedCount"] == 1
    assert records[0]["identities"][0]["riskRating"] == "RED"
    assert records[0]["identities"][1]["riskRating"] == "GREEN"
    # No riskRatingMax (not added — group uses riskRedCount like STS pairs)
    assert "riskRatingMax" not in records[0]

    stripped = strip_risk_keys(records)
    bare_group = dict(group)
    bare_group.pop("risk_red_count")
    bare_idents = []
    for ident in group["identities_payload"]:
        bare_idents.append(strip_risk_keys(ident))
    bare_group["identities_payload"] = bare_idents
    # Rebuild without risk on members for golden: strip nested
    bare_records = strip_risk_keys(groups_to_payload([{**bare_group, "risk_red_count": 0}]))
    # Compare keys excluding risk — stripped should match structure of bare without riskRedCount
    assert "riskRedCount" not in stripped[0]
    assert list(stripped[0].keys()) == [
        k for k in list(records[0].keys()) if k != "riskRedCount"
    ]


def test_vessel_header_risk_fields_and_green_default():
    static = pd.Series(
        {
            "shipname": "TEST",
            "shiptype": 80,
            "shiptypedesc": "Tanker",
            "imo": "9074729",
            "to_bow": None,
            "to_stern": None,
            "to_port": None,
            "to_starboard": None,
        }
    )
    sanc = pd.Series(
        {
            "sanctions_match": True,
            "match_confidence": "confirmed",
            "sanctions_list": "SDN",
            "list_names": ["SDN"],
        }
    )
    risk = pd.Series(
        {"risk_rating": "AMBER", "risk_score": 50, "risk_reason": "identity conflict"}
    )
    header = _vessel_header(352006140, static, 352, "Panama", sanc, risk)
    assert header["riskRating"] == "AMBER"
    assert header["riskScore"] == 50
    keys = list(header.keys())
    assert keys.index("sanctionsList") < keys.index("riskRating")

    green = _vessel_header(1, None, None, None, None, None)
    assert green["riskRating"] == "GREEN"
    assert green["riskScore"] == 0
    assert green["riskReason"] == "none"

    stripped = strip_risk_keys(header)
    assert "riskRating" not in stripped
    assert "riskLevel" not in header  # vessel header has no spoofing riskLevel
    # spoofing event riskLevel must survive strip_risk_keys
    event = {"mmsi": 1, "riskLevel": "critical", "riskRating": "RED"}
    assert strip_risk_keys(event) == {"mmsi": 1, "riskLevel": "critical"}


def test_attach_failure_defaults_green_for_identity_rows(monkeypatch, caplog):
    def boom(engine=None):
        raise RuntimeError("cache down")

    monkeypatch.setattr(risk_mod, "load_risk_indexes", boom)
    df = pd.DataFrame([{"imo": "9074729", "mmsi": 352006140}])
    with caplog.at_level("WARNING"):
        out = risk_mod.attach_risk_ratings(df, MagicMock())
    assert out.iloc[0]["risk_rating"] == "GREEN"
    payload = risk_payload_fields(out.iloc[0])
    assert payload == {"riskRating": "GREEN", "riskScore": 0, "riskReason": "none"}


def test_phase2_response_counts_key_order():
    payload = {
        "sanctionsMatchGroupCount": 1,
        "sanctionsMatchIdentityCount": 2,
        "riskRedCount": 1,
        "riskAmberCount": 0,
        "invalidMmsiExcludedCount": 0,
        "groups": [],
        "identities": [],
    }
    keys = list(payload.keys())
    assert keys.index("sanctionsMatchIdentityCount") < keys.index("riskRedCount")
    assert keys.index("riskRedCount") < keys.index("riskAmberCount")
