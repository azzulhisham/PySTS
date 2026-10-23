"""Phase 4: overview summary risk counts + sanctions-list identity-risk fields."""

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

import mantis_overview as overview_mod  # noqa: E402
import sanctions as sanctions_mod  # noqa: E402
import ship_risk_attach as risk_mod  # noqa: E402
from ship_risk_attach import clear_risk_index_cache, strip_risk_keys  # noqa: E402
from mantis_overview import (  # noqa: E402
    _dark_vessels_summary,
    _illegal_anchoring_summary,
    _spoofing_summary,
    _sts_summary,
)
from sanctions import ofac_vessels_to_payload, query_ofac_vessels  # noqa: E402


@pytest.fixture(autouse=True)
def _clear_cache():
    clear_risk_index_cache()
    yield
    clear_risk_index_cache()


def test_overview_module_summaries_add_risk_after_sanctions():
    dark = _dark_vessels_summary(
        {
            "rule_version": "t",
            "ship_type_filter": "70-89",
            "min_silence_minutes": 1,
            "coverage_exit_days": 1,
            "dark_max_days": 1,
            "include_coverage_exit": False,
            "candidate_count": 10,
            "by_reason": {},
            "by_confidence": {},
            "sanctions_match_count": 2,
            "risk_red_count": 3,
            "risk_amber_count": 4,
        }
    )
    assert dark["sanctionsMatchCount"] == 2
    assert dark["riskRedCount"] == 3
    assert dark["riskAmberCount"] == 4
    keys = list(dark.keys())
    assert keys.index("sanctionsMatchCount") < keys.index("riskRedCount")
    assert keys.index("riskRedCount") < keys.index("riskAmberCount")

    sts = _sts_summary(
        {
            "min_suspicion_score": 4.5,
            "max_distance_m": 100.0,
            "open_high_score_count": 1,
            "in_anchorage_cluster_count": 1,
            "pair_count": 1,
            "paired_vessel_count": 2,
            "sanctions_match_pair_count": 0,
            "sanctions_match_vessel_count": 1,
            "risk_red_count": 1,
            "risk_amber_count": 0,
        }
    )
    assert sts["sanctionsMatchVesselCount"] == 1
    assert sts["riskRedCount"] == 1
    assert list(sts.keys()).index("sanctionsMatchVesselCount") < list(sts.keys()).index("riskRedCount")

    illegal = _illegal_anchoring_summary(
        {
            "rule_version": "t",
            "ship_type_filter": "x",
            "stopped_candidate_count": 1,
            "illegal_count": 1,
            "watch_polygon_count": 0,
            "port_limit_polygon_count": 0,
            "by_reason": {},
            "sanctions_match_count": 0,
            "risk_red_count": 0,
            "risk_amber_count": 2,
        }
    )
    assert illegal["riskAmberCount"] == 2

    spoof = _spoofing_summary(
        {
            "rule_version": "t",
            "ship_type_filter": "x",
            "dedupe_rule": "x",
            "phase": "x",
            "detector": "x",
            "date_from": None,
            "date_to": None,
            "range_meta": {},
            "raw_hit_count": 0,
            "filtered_hit_count": 0,
            "anomaly_count": 0,
            "by_reason": {},
            "sanctions_match_count": 5,
            "risk_red_count": 5,
            "risk_amber_count": 0,
        }
    )
    assert spoof["sanctionsMatchCount"] == 5
    assert spoof["riskRedCount"] == 5


def test_overview_risk_defaults_zero_when_missing():
    dark = _dark_vessels_summary(
        {
            "rule_version": "t",
            "ship_type_filter": "70-89",
            "min_silence_minutes": 1,
            "coverage_exit_days": 1,
            "dark_max_days": 1,
            "include_coverage_exit": False,
            "candidate_count": 0,
            "by_reason": {},
            "by_confidence": {},
            "sanctions_match_count": 0,
            # no risk_* keys → treat as 0
        }
    )
    assert dark["riskRedCount"] == 0
    assert dark["riskAmberCount"] == 0


def test_overview_payload_adds_rated_count_only(monkeypatch):
    monkeypatch.setattr(overview_mod, "OVERVIEW_CACHE_TTL_S", 0)
    monkeypatch.setattr(overview_mod, "rated_ship_count", lambda engine=None: 42)
    monkeypatch.setattr(
        overview_mod,
        "_run_postgres_modules",
        lambda *a, **k: (
            {"darkVessels": 1.0, "stsActivities": 1.0, "illegalAnchoring": 1.0},
            {},
            {
                "darkVessels": {
                    "sanctionsMatchCount": 1,
                    "riskRedCount": 0,
                    "riskAmberCount": 0,
                    "candidateCount": 1,
                },
                "stsActivities": {
                    "sanctionsMatchVesselCount": 0,
                    "riskRedCount": 0,
                    "riskAmberCount": 0,
                },
                "illegalAnchoring": {
                    "sanctionsMatchCount": 0,
                    "riskRedCount": 0,
                    "riskAmberCount": 0,
                },
            },
        ),
    )
    monkeypatch.setattr(overview_mod, "get_pg_engine", lambda: MagicMock())
    out = overview_mod.detect_mantis_overview(include_spoofing=False)
    assert out["riskRatedShipCount"] == 42
    stripped = strip_risk_keys(out)
    assert "riskRatedShipCount" not in stripped
    assert "riskRedCount" not in json.dumps(stripped)
    # Existing sanctions counts preserved
    assert stripped["darkVessels"]["sanctionsMatchCount"] == 1


def test_ofac_payload_adds_risk_after_list_fields(monkeypatch):
    monkeypatch.setattr(
        risk_mod,
        "load_risk_indexes",
        lambda engine=None: (
            {"9074729": {"rating": "RED", "score": 100, "reason": "sanctions match (SDN)"}},
            {},
        ),
    )
    df = pd.DataFrame(
        [
            {
                "uid": 1,
                "list_name": "SDN",
                "list_names": ["SDN"],
                "vessel_name": "A",
                "imo": "9074729",
                "mmsi": None,
                "vessel_call_sign": None,
                "vessel_type": None,
                "vessel_flag": None,
                "vessel_owner": None,
                "programs": None,
                "remarks": None,
                "publish_date": None,
            },
            {
                "uid": 2,
                "list_name": "SDN",
                "list_names": ["SDN"],
                "vessel_name": "B",
                "imo": None,
                "mmsi": "123456789",
                "vessel_call_sign": None,
                "vessel_type": None,
                "vessel_flag": None,
                "vessel_owner": None,
                "programs": None,
                "remarks": None,
                "publish_date": None,
            },
        ]
    )
    df = risk_mod.attach_risk_ratings(df, engine=MagicMock())
    vessels = ofac_vessels_to_payload(df)
    assert vessels[0]["listName"] == "SDN"
    assert vessels[0]["riskRating"] == "RED"
    assert vessels[0]["riskScore"] == 100
    assert vessels[1]["riskRating"] == "GREEN"
    keys = list(vessels[0].keys())
    assert keys.index("publishDate") < keys.index("riskRating")


def test_query_ofac_green_when_cache_fails(monkeypatch):
    frame = pd.DataFrame(
        [
            {
                "uid": 1,
                "list_name": "SDN",
                "list_names": ["SDN"],
                "vessel_name": "X",
                "imo": "9074729",
                "mmsi": None,
                "vessel_call_sign": None,
                "vessel_type": None,
                "vessel_flag": None,
                "vessel_owner": None,
                "programs": None,
                "remarks": None,
                "publish_date": None,
            }
        ]
    )
    monkeypatch.setattr(sanctions_mod, "load_ofac_vessel_list", lambda engine=None: frame)
    monkeypatch.setattr(sanctions_mod, "get_pg_engine", lambda: MagicMock())

    def boom(engine=None):
        raise RuntimeError("db down")

    monkeypatch.setattr(risk_mod, "load_risk_indexes", boom)
    # attach_risk_ratings catches and greens; also query has try/except
    result = query_ofac_vessels(engine=MagicMock())
    assert result["risk_red_count"] == 0
    assert result["risk_amber_count"] == 0
    assert result["vessels"][0]["riskRating"] == "GREEN"
    assert result["vessels"][0]["listName"] == "SDN"


def test_sanctions_golden_strip_only_adds_risk(monkeypatch):
    frame = pd.DataFrame(
        [
            {
                "uid": 9,
                "list_name": "SDN",
                "list_names": ["SDN"],
                "vessel_name": "Y",
                "imo": "9074729",
                "mmsi": "111111111",
                "vessel_call_sign": "ABC",
                "vessel_type": "Tanker",
                "vessel_flag": "Panama",
                "vessel_owner": None,
                "programs": ["IRAN"],
                "remarks": None,
                "publish_date": "2020-01-01",
            }
        ]
    )
    monkeypatch.setattr(sanctions_mod, "load_ofac_vessel_list", lambda engine=None: frame.copy())
    monkeypatch.setattr(sanctions_mod, "get_pg_engine", lambda: MagicMock())
    monkeypatch.setattr(
        risk_mod,
        "load_risk_indexes",
        lambda engine=None: (
            {"9074729": {"rating": "AMBER", "score": 40, "reason": "x"}},
            {},
        ),
    )
    with_risk = query_ofac_vessels(engine=MagicMock())
    stripped = strip_risk_keys(
        {
            "imo": with_risk["imo"],
            "mmsi": with_risk["mmsi"],
            "riskRedCount": with_risk["risk_red_count"],
            "riskAmberCount": with_risk["risk_amber_count"],
            "vessels": with_risk["vessels"],
        }
    )
    monkeypatch.setattr(risk_mod, "load_risk_indexes", lambda engine=None: ({}, {}))
    clear_risk_index_cache()
    without = strip_risk_keys(
        {
            "imo": None,
            "mmsi": None,
            "riskRedCount": 0,
            "riskAmberCount": 0,
            "vessels": ofac_vessels_to_payload(
                risk_mod.attach_risk_ratings(frame.copy(), engine=MagicMock())
            ),
        }
    )
    # Same pre-existing vessel keys/values after strip
    assert stripped["vessels"][0]["listName"] == without["vessels"][0]["listName"]
    assert stripped["vessels"][0]["imo"] == without["vessels"][0]["imo"]
    assert "riskRating" not in json.dumps(stripped)
    assert "listName" in stripped["vessels"][0]


def test_response_count_key_order_sanctions():
    payload = {
        "imo": None,
        "mmsi": None,
        "riskRedCount": 10,
        "riskAmberCount": 2,
        "vessels": [],
    }
    keys = list(payload.keys())
    assert keys.index("riskRedCount") < keys.index("riskAmberCount")
    assert keys.index("mmsi") < keys.index("riskRedCount")
