"""Phase 1 identity-risk attach: defaults, IMO>MMSI, cache TTL, golden strip."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock

import pandas as pd
import pytest

RESTAPI = Path(__file__).resolve().parents[1]
if str(RESTAPI) not in sys.path:
    sys.path.insert(0, str(RESTAPI))

import ship_risk_attach as risk_mod  # noqa: E402
from ship_risk_attach import (  # noqa: E402
    attach_risk_pair_sides,
    attach_risk_ratings,
    clear_risk_index_cache,
    load_risk_indexes,
    match_risk,
    risk_counts,
    risk_payload_fields,
    strip_risk_keys,
)
from dark_vessels import vessels_to_payload as dark_vessels_to_payload  # noqa: E402
from illegal_anchoring import vessels_to_payload as illegal_vessels_to_payload  # noqa: E402
from loitering import events_to_payload as loitering_events_to_payload  # noqa: E402
from spoofing import anomalies_to_payload  # noqa: E402
from sts_detection import pairs_to_payload, vessels_to_payload as sts_vessels_to_payload  # noqa: E402


@pytest.fixture(autouse=True)
def _clear_cache():
    clear_risk_index_cache()
    yield
    clear_risk_index_cache()


def _indexes():
    by_imo = {
        "9074729": {"rating": "RED", "score": 100, "reason": "sanctions match (SDN)"},
        "9000883": {"rating": "AMBER", "score": 50, "reason": "identity conflict"},
    }
    by_mmsi = {
        "538004567": {"rating": "RED", "score": 90, "reason": "mmsi-only row"},
        "123456789": {"rating": "AMBER", "score": 40, "reason": "mmsi amber"},
    }
    return by_imo, by_mmsi


def test_missing_row_defaults_green():
    by_imo, by_mmsi = _indexes()
    hit = match_risk("9999999", "999999999", by_imo, by_mmsi)
    assert hit == {"rating": "GREEN", "score": 0, "reason": "none"}
    hit2 = match_risk(None, None, by_imo, by_mmsi)
    assert hit2 == {"rating": "GREEN", "score": 0, "reason": "none"}


def test_imo_preferred_over_mmsi():
    by_imo, by_mmsi = _indexes()
    # IMO hit even when MMSI would also match a different rating
    hit = match_risk("9074729", "538004567", by_imo, by_mmsi)
    assert hit["rating"] == "RED"
    assert hit["score"] == 100
    assert "sanctions" in hit["reason"]

    # No IMO → MMSI
    hit_m = match_risk(None, "538004567", by_imo, by_mmsi)
    assert hit_m["rating"] == "RED"
    assert hit_m["score"] == 90

    # IMO present but missing from index → GREEN (do not fall back to MMSI)
    hit_miss = match_risk("1111111", "538004567", by_imo, by_mmsi)
    assert hit_miss == {"rating": "GREEN", "score": 0, "reason": "none"}


def test_attach_risk_ratings_and_counts(monkeypatch):
    by_imo, by_mmsi = _indexes()
    monkeypatch.setattr(risk_mod, "load_risk_indexes", lambda engine=None: (by_imo, by_mmsi))
    df = pd.DataFrame(
        [
            {"imo": "9074729", "mmsi": 111},
            {"imo": None, "mmsi": 123456789},
            {"imo": "0000000", "mmsi": 999},
        ]
    )
    out = attach_risk_ratings(df, MagicMock())
    assert out["risk_rating"].tolist() == ["RED", "AMBER", "GREEN"]
    assert out["risk_score"].tolist() == [100, 40, 0]
    assert out["risk_reason"].tolist()[2] == "none"
    counts = risk_counts(out)
    assert counts == {"risk_red_count": 1, "risk_amber_count": 1}


def test_attach_risk_pair_sides_red_count(monkeypatch):
    by_imo, by_mmsi = _indexes()
    monkeypatch.setattr(risk_mod, "load_risk_indexes", lambda engine=None: (by_imo, by_mmsi))
    pairs = pd.DataFrame(
        [
            {
                "imo_a": "9074729",
                "mmsi_a": 1,
                "imo_b": "9000883",
                "mmsi_b": 2,
            },
            {
                "imo_a": None,
                "mmsi_a": 538004567,
                "imo_b": None,
                "mmsi_b": 999999999,
            },
        ]
    )
    out = attach_risk_pair_sides(pairs, MagicMock())
    assert out["risk_red_count"].tolist() == [1, 1]  # RED+AMBER; RED+GREEN
    assert out["risk_rating_a"].tolist() == ["RED", "RED"]
    assert out["risk_rating_b"].tolist() == ["AMBER", "GREEN"]


def test_cache_ttl(monkeypatch):
    calls = {"n": 0}

    def fake_build(engine):
        calls["n"] += 1
        return {"9074729": {"rating": "RED", "score": 100, "reason": "x"}}, {}

    monkeypatch.setattr(risk_mod, "_build_risk_indexes", fake_build)
    monkeypatch.setattr(risk_mod, "RISK_CACHE_TTL_S", 1)
    eng = MagicMock()
    load_risk_indexes(eng)
    load_risk_indexes(eng)
    assert calls["n"] == 1
    time.sleep(1.05)
    load_risk_indexes(eng)
    assert calls["n"] == 2


def test_cache_load_failure_returns_defaults(monkeypatch, caplog):
    def boom(engine):
        raise RuntimeError("db down")

    monkeypatch.setattr(risk_mod, "_build_risk_indexes", boom)
    with caplog.at_level("WARNING"):
        by_imo, by_mmsi = load_risk_indexes(MagicMock())
    assert by_imo == {} and by_mmsi == {}
    assert any("failed" in r.message.lower() for r in caplog.records)

    df = attach_risk_ratings(pd.DataFrame([{"imo": "9074729", "mmsi": 1}]), MagicMock())
    assert df.iloc[0]["risk_rating"] == "GREEN"
    assert df.iloc[0]["risk_score"] == 0
    assert df.iloc[0]["risk_reason"] == "none"
    payload = risk_payload_fields(df.iloc[0])
    assert payload == {"riskRating": "GREEN", "riskScore": 0, "riskReason": "none"}


def test_attach_failure_never_raises(monkeypatch, caplog):
    def boom(engine=None):
        raise RuntimeError("attach boom")

    monkeypatch.setattr(risk_mod, "load_risk_indexes", boom)
    with caplog.at_level("WARNING"):
        out = attach_risk_ratings(pd.DataFrame([{"imo": "1", "mmsi": 2}]), MagicMock())
        out2 = attach_risk_pair_sides(
            pd.DataFrame([{"imo_a": "1", "mmsi_a": 2, "imo_b": "3", "mmsi_b": 4}]),
            MagicMock(),
        )
    assert out.iloc[0]["risk_rating"] == "GREEN"
    assert int(out2.iloc[0]["risk_red_count"]) == 0


def _sanctions_cols(**kwargs):
    base = {
        "sanctions_match": False,
        "match_confidence": "none",
        "sanctions_list": None,
        "list_names": None,
    }
    base.update(kwargs)
    return base


def _sts_pair_row(**extra):
    row = {
        "observation_id": 1,
        "anchorage_name": "Test",
        "suspicion_score": 5.0,
        "distance_m": 50.0,
        "duration_seconds": 3600,
        "last_detected_at": pd.Timestamp("2026-01-01T00:00:00Z"),
        "first_detected_at": pd.Timestamp("2026-01-01T00:00:00Z"),
        "mmsi_a": 111111111,
        "shipname_a": "A",
        "lat_a": 1.0,
        "lon_a": 103.0,
        "sog_a": 0.1,
        "cog_a": 10.0,
        "tscurrent_a": pd.Timestamp("2026-01-01T00:00:00Z"),
        "last_seen_at_a": pd.Timestamp("2026-01-01T00:00:00Z"),
        "imo_a": "9074729",
        "mmsi_b": 222222222,
        "shipname_b": "B",
        "lat_b": 1.01,
        "lon_b": 103.01,
        "sog_b": 0.2,
        "cog_b": 20.0,
        "tscurrent_b": pd.Timestamp("2026-01-01T00:00:00Z"),
        "last_seen_at_b": pd.Timestamp("2026-01-01T00:00:00Z"),
        "imo_b": None,
        "sanctions_match": True,
        "match_confidence": "confirmed",
        "sanctions_match_a": True,
        "match_confidence_a": "confirmed",
        "sanctions_list_a": "SDN",
        "list_names_a": ["SDN"],
        "sanctions_match_b": False,
        "match_confidence_b": "none",
        "sanctions_list_b": None,
        "list_names_b": None,
        "risk_rating_a": "RED",
        "risk_score_a": 100,
        "risk_reason_a": "sanctions match (SDN)",
        "risk_rating_b": "GREEN",
        "risk_score_b": 0,
        "risk_reason_b": "none",
        "risk_red_count": 1,
        "in_port_area": False,
        "port_area_name": None,
        "port_area_terminal_name": None,
        "at_berth": False,
        "berth_name": None,
    }
    row.update(extra)
    return row


def test_golden_strip_sts_pair_identical():
    pairs = pd.DataFrame([_sts_pair_row()])
    with_risk = pairs_to_payload(pairs)
    stripped = strip_risk_keys(with_risk)

    # Rebuild without risk columns → payload must match stripped
    bare = pairs.drop(
        columns=[
            c
            for c in pairs.columns
            if c.startswith("risk_")
        ]
    )
    without = pairs_to_payload(bare)
    # Without risk cols, risk_payload_fields emits GREEN defaults; strip those too
    without_stripped = strip_risk_keys(without)
    assert json.dumps(stripped, sort_keys=False) == json.dumps(without_stripped, sort_keys=False)
    # And the stripped payload has no risk* keys
    blob = json.dumps(stripped)
    assert "riskRating" not in blob
    assert "riskRedCount" not in blob
    # Existing sanctions fields preserved
    assert stripped[0]["sanctionsMatch"] is True
    assert stripped[0]["vesselA"]["sanctionsMatch"] is True
    assert list(stripped[0].keys()) == list(without_stripped[0].keys())


def test_golden_strip_list_endpoints():
    """Strip risk* from each Phase 1 vessel/event payload → same as no-risk shape."""

    dark_row = {
        "activity_id": 42,
        "mmsi": 111111111,
        "imo": "9074729",
        "shipname": "DARK",
        "shiptype": 80,
        "shiptypedesc": "Tanker",
        "curlatitude": 1.0,
        "curlongitude": 103.0,
        "cursog": 0.0,
        "curcog": 0.0,
        "navstatusdesc": "At anchor",
        "rowcount": 3,
        "distance": 10.0,
        "silence_seconds": 7200,
        "ts": pd.Timestamp("2026-01-01T00:00:00Z"),
        "tsstop": pd.Timestamp("2026-01-01T00:00:00Z"),
        "tscurrent": pd.Timestamp("2026-01-01T00:00:00Z"),
        "dark_reason": "prolonged_silence",
        "confidence": "high",
        "polygon_name": None,
        "in_excl_polygon": False,
        **_sanctions_cols(sanctions_match=True, match_confidence="confirmed", sanctions_list="SDN"),
        "risk_rating": "RED",
        "risk_score": 100,
        "risk_reason": "sanctions match (SDN)",
        "to_bow": None,
        "to_stern": None,
        "to_port": None,
        "to_starboard": None,
    }
    illegal_row = {
        "mmsi": 111111111,
        "imo": "9074729",
        "shipname": "ILLEGAL",
        "shiptype": 80,
        "shiptypedesc": "Tanker",
        "curlatitude": 1.0,
        "curlongitude": 103.0,
        "sog": 0.0,
        "cog": 0.0,
        "navstatusdesc": "At anchor",
        "ts": pd.Timestamp("2026-01-01T00:00:00Z"),
        "tsstop": pd.Timestamp("2026-01-01T00:00:00Z"),
        "tscurrent": pd.Timestamp("2026-01-01T00:00:00Z"),
        "in_restricted": True,
        "in_watch_polygon": False,
        "in_port_limit": False,
        "watch_polygon_name": None,
        "port_limit_name": None,
        "reason": "in_restricted",
        "detected_at": "2026-01-01T00:00:00Z",
        **_sanctions_cols(),
        "risk_rating": "GREEN",
        "risk_score": 0,
        "risk_reason": "none",
        "to_bow": None,
        "to_stern": None,
        "to_port": None,
        "to_starboard": None,
    }
    loiter_row = {
        "loiter_id": 7,
        "mmsi": 111111111,
        "imo": None,
        "shipname": "LOITER",
        "shiptype": 70,
        "shiptypedesc": "Cargo",
        "centre_latitude": 1.0,
        "centre_longitude": 103.0,
        "started_at": pd.Timestamp("2026-01-01T00:00:00Z"),
        "ended_at": pd.Timestamp("2026-01-01T01:00:00Z"),
        "radius_m": 200.0,
        "path_m": 500.0,
        "net_m": 50.0,
        "sample_count": 10,
        "outside_anchorage": True,
        "near_restricted": False,
        "before_sts": False,
        "in_port_area": False,
        "port_area_name": None,
        "port_area_terminal_name": None,
        "anchorage_name": None,
        "restricted_distance_m": None,
        "sts_observation_id": None,
        **_sanctions_cols(),
        "risk_rating": "AMBER",
        "risk_score": 40,
        "risk_reason": "mmsi amber",
        "to_bow": None,
        "to_stern": None,
        "to_port": None,
        "to_starboard": None,
    }
    spoof_row = {
        "mmsi": 111111111,
        "imo": "9000883",
        "shipname": "SPOOF",
        "shiptype": 70,
        "shiptypedesc": "Cargo",
        "reason": "teleport",
        "event_day": "2026-01-01",
        "ts": pd.Timestamp("2026-01-01T00:00:00Z"),
        "prev_ts": pd.Timestamp("2026-01-01T00:00:00Z"),
        "prev_lat": 1.0,
        "prev_lon": 103.0,
        "latitude": 2.0,
        "longitude": 104.0,
        "dist_m": 100000.0,
        "dt_s": 60,
        "implied_kn": 200.0,
        **_sanctions_cols(),
        "risk_rating": "AMBER",
        "risk_score": 50,
        "risk_reason": "identity conflict",
        "to_bow": None,
        "to_stern": None,
        "to_port": None,
        "to_starboard": None,
    }
    sts_vessel = {
        "observation_id": 1,
        "mmsi": 111111111,
        "imo": "9074729",
        "shipname": "STS",
        "curlatitude": 1.0,
        "curlongitude": 103.0,
        "sog": 0.1,
        "cog": 10.0,
        "anchorage_name": "A",
        "suspicion_score": 5.0,
        "duration_seconds": 3600,
        "last_detected_at": pd.Timestamp("2026-01-01T00:00:00Z"),
        "first_detected_at": pd.Timestamp("2026-01-01T00:00:00Z"),
        "tscurrent": pd.Timestamp("2026-01-01T00:00:00Z"),
        "last_seen_at": pd.Timestamp("2026-01-01T00:00:00Z"),
        **_sanctions_cols(sanctions_match=True, match_confidence="confirmed", sanctions_list="SDN"),
        "risk_rating": "RED",
        "risk_score": 100,
        "risk_reason": "sanctions match (SDN)",
        "in_port_area": False,
        "port_area_name": None,
        "port_area_terminal_name": None,
        "at_berth": False,
        "berth_name": None,
        "to_bow": None,
        "to_stern": None,
        "to_port": None,
        "to_starboard": None,
    }

    cases = [
        ("dark", dark_vessels_to_payload, dark_row),
        ("illegal", illegal_vessels_to_payload, illegal_row),
        ("loiter", loitering_events_to_payload, loiter_row),
        ("spoof", anomalies_to_payload, spoof_row),
        ("sts_vessel", sts_vessels_to_payload, sts_vessel),
    ]
    for name, to_payload, row in cases:
        df = pd.DataFrame([row])
        with_risk = to_payload(df)
        stripped = strip_risk_keys(with_risk)
        bare = df.drop(columns=[c for c in df.columns if c.startswith("risk_")])
        without_stripped = strip_risk_keys(to_payload(bare))
        assert stripped == without_stripped, name
        assert "riskRating" not in json.dumps(stripped)
        # sanctions / core keys still present in same order
        assert list(stripped[0].keys()) == list(without_stripped[0].keys())


def test_risk_fields_appended_after_sanctions():
    row = pd.Series(
        {
            **_sanctions_cols(sanctions_match=True, match_confidence="confirmed", sanctions_list="SDN"),
            "risk_rating": "RED",
            "risk_score": 100,
            "risk_reason": "x",
        }
    )
    # Simulate vessel dict key order used by detectors
    vessel = {
        "mmsi": 1,
        "sanctionsMatch": True,
        "matchConfidence": "confirmed",
        "sanctionsList": "SDN",
        **risk_payload_fields(row),
    }
    keys = list(vessel.keys())
    assert keys.index("sanctionsList") < keys.index("riskRating")
    assert keys.index("riskRating") < keys.index("riskScore") < keys.index("riskReason")


def test_top_level_response_shape_after_sanctions_counts():
    """riskRed/Amber counts sit after sanctionsMatchCount* (dict insertion order)."""
    payload = {
        "candidateCount": 1,
        "sanctionsMatchCount": 0,
        "riskRedCount": 0,
        "riskAmberCount": 0,
        "vessels": [],
    }
    keys = list(payload.keys())
    assert keys.index("sanctionsMatchCount") < keys.index("riskRedCount")
    assert keys.index("riskRedCount") < keys.index("riskAmberCount")
    stripped = strip_risk_keys(payload)
    assert list(stripped.keys()) == ["candidateCount", "sanctionsMatchCount", "vessels"]
