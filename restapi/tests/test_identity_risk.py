"""Identity risk: filters, rating rules, evt dedupe, API shapes."""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

RESTAPI = Path(__file__).resolve().parents[1]
BACKEND = RESTAPI.parent / "backend"
for p in (RESTAPI, BACKEND):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from identity_risk import (  # noqa: E402
    classify_lloyds_conflict,
    format_sanctions_reason,
    normalize_imo_text,
    score_ship,
)
from identity_risk_job import (  # noqa: E402
    apply_sanctions_to_ratings,
    dedupe_static_evt_mmsi_rows,
    primary_event_ship_key,
    sanctioned_ship_key,
)
from ais_identity import is_valid_ship_mmsi  # noqa: E402


def test_placeholder_and_invalid_imo_filters():
    assert normalize_imo_text("1234567") is None
    assert normalize_imo_text("1111111") is None
    assert normalize_imo_text("533456789") is None  # not 7 digits
    assert normalize_imo_text("5571322") is None  # bad check digit
    assert normalize_imo_text("9058713") == "9058713"
    assert is_valid_ship_mmsi(533133348) is True
    assert is_valid_ship_mmsi(111111111) is False  # MID 111 < 201
    assert is_valid_ship_mmsi(123456789) is False  # MID 123 < 201
    assert is_valid_ship_mmsi(0) is False


def test_dedupe_static_evt_rows():
    ts = datetime(2026, 10, 21, 8, 55, 23)
    rows = [
        {"ts": ts, "imo": 9831854, "prev": "477539800", "cur": "477539000"},
        {"ts": ts, "imo": 9831854, "prev": "477539800", "cur": "477539000"},
        {"ts": ts + timedelta(seconds=1), "imo": 9831854, "prev": "477539800", "cur": "477539000"},
    ]
    # Same calendar day + same prev/cur/imo → one row
    out = dedupe_static_evt_mmsi_rows(rows)
    assert len(out) == 1


def test_rating_red_high_misuse_cargo():
    now = datetime(2026, 10, 22, tzinfo=timezone.utc)
    events = [
        {
            "event_type": "lloyds_mmsi_imo_conflict",
            "confidence": "high",
            "ts_start": now - timedelta(days=10),
            "sanctions_match": False,
            "sanctions_lists": None,
        }
    ]
    scored = score_ship(events, lookback_months=24, is_cargo_tanker=True, now=now)
    assert scored["rating"] == "RED"
    assert scored["score"] == 100
    assert scored["mmsi_misuse"] is True


def test_rating_red_sanctions():
    now = datetime(2026, 10, 22, tzinfo=timezone.utc)
    events = [
        {
            "event_type": "reflag_dual_mmsi",
            "confidence": "reflag",
            "ts_start": now - timedelta(days=1),
            "sanctions_match": True,
            "sanctions_lists": ["SDN"],
        }
    ]
    scored = score_ship(events, lookback_months=24, is_cargo_tanker=False, now=now)
    assert scored["rating"] == "RED"
    assert scored["score"] == 100
    assert scored["sanctions_match"] is True
    assert scored["factors"]["reason"] == "sanctions match (SDN)"


def test_sanctioned_ship_no_events_gets_red_100():
    now = datetime(2026, 10, 22, tzinfo=timezone.utc)
    ratings: dict = {}
    stats = apply_sanctions_to_ratings(
        ratings,
        [
            {
                "ship_key": "imo:9224275",
                "imo": "9224275",
                "mmsi": None,
                "ship_name": "SEA OPERA",
                "sanctions_lists": ["SDN"],
            }
        ],
        lookback_months=24,
        now=now,
    )
    assert stats["sanctions_ratings_added"] == 1
    row = ratings["imo:9224275"]
    assert row["rating"] == "RED"
    assert row["score"] == 100
    assert row["sanctions_match"] is True
    assert row["event_count_total"] == 0
    assert row["factors"]["reason"] == "sanctions match (SDN)"


def test_sanctioned_ship_existing_rating_not_duplicated_or_downgraded():
    now = datetime(2026, 10, 22, tzinfo=timezone.utc)
    ratings = {
        "imo:9058713": {
            "ship_key": "imo:9058713",
            "imo": "9058713",
            "mmsi": 538090000,
            "ship_name": "ALREADY RATED",
            "rating": "RED",
            "score": 100,
            "lookback_months": 24,
            "mmsi_misuse": True,
            "sanctions_match": False,
            "sanctions_lists": None,
            "event_count_window": 2,
            "event_count_total": 2,
            "latest_event_at": now,
            "factors": {"reason": "high_mmsi_misuse", "sanctionsLists": []},
            "rule_version": "v1",
            "computed_at": now,
        }
    }
    stats = apply_sanctions_to_ratings(
        ratings,
        [
            {
                "ship_key": "imo:9058713",
                "imo": "9058713",
                "mmsi": 538090000,
                "ship_name": "ALREADY RATED",
                "sanctions_lists": ["SDN", "UK"],
            }
        ],
        lookback_months=24,
        now=now,
    )
    assert stats["sanctions_ratings_added"] == 0
    assert len(ratings) == 1
    row = ratings["imo:9058713"]
    assert row["rating"] == "RED"
    assert row["score"] == 100
    assert row["sanctions_match"] is True
    assert row["sanctions_lists"] == ["SDN", "UK"]
    assert row["event_count_total"] == 2  # preserved
    assert "high_mmsi_misuse" in row["factors"]["reason"]
    assert "sanctions match (SDN, UK)" in row["factors"]["reason"]


def test_sanctioned_amber_upgraded_not_downgraded():
    now = datetime(2026, 10, 22, tzinfo=timezone.utc)
    ratings = {
        "imo:9058713": {
            "ship_key": "imo:9058713",
            "imo": "9058713",
            "mmsi": None,
            "ship_name": "AMBER SHIP",
            "rating": "AMBER",
            "score": 60,
            "lookback_months": 24,
            "mmsi_misuse": False,
            "sanctions_match": False,
            "sanctions_lists": None,
            "event_count_window": 1,
            "event_count_total": 1,
            "latest_event_at": now,
            "factors": {"reason": "reflag_dual_mmsi", "sanctionsLists": []},
            "rule_version": "v1",
            "computed_at": now,
        }
    }
    stats = apply_sanctions_to_ratings(
        ratings,
        [
            {
                "ship_key": "imo:9058713",
                "imo": "9058713",
                "mmsi": None,
                "ship_name": "AMBER SHIP",
                "sanctions_lists": ["UK"],
            }
        ],
        lookback_months=24,
        now=now,
    )
    assert stats["sanctions_ratings_upgraded"] == 1
    assert ratings["imo:9058713"]["rating"] == "RED"
    assert ratings["imo:9058713"]["score"] == 100


def test_sanctions_imo_mmsi_dedupe_consolidates():
    now = datetime(2026, 10, 22, tzinfo=timezone.utc)
    ratings = {
        "mmsi:538090000": {
            "ship_key": "mmsi:538090000",
            "imo": None,
            "mmsi": 538090000,
            "ship_name": "MMSI ONLY",
            "rating": "AMBER",
            "score": 40,
            "lookback_months": 24,
            "mmsi_misuse": True,
            "sanctions_match": False,
            "sanctions_lists": None,
            "event_count_window": 1,
            "event_count_total": 1,
            "latest_event_at": now,
            "factors": {"reason": "medium_mmsi_misuse", "sanctionsLists": []},
            "rule_version": "v1",
            "computed_at": now,
        }
    }
    apply_sanctions_to_ratings(
        ratings,
        [
            {
                "ship_key": "imo:9058713",
                "imo": "9058713",
                "mmsi": 538090000,
                "ship_name": "HAS IMO",
                "sanctions_lists": ["CONS"],
            }
        ],
        lookback_months=24,
        now=now,
    )
    assert "mmsi:538090000" not in ratings
    assert "imo:9058713" in ratings
    assert len(ratings) == 1
    assert ratings["imo:9058713"]["rating"] == "RED"
    assert ratings["imo:9058713"]["score"] == 100
    assert ratings["imo:9058713"]["imo"] == "9058713"


def test_sanctioned_ship_key_prefers_imo():
    assert sanctioned_ship_key("9058713", "538090000") == "imo:9058713"
    assert sanctioned_ship_key(None, "538090000") == "mmsi:538090000"
    assert sanctioned_ship_key(None, None) is None
    assert format_sanctions_reason(["SDN", "UK"]) == "sanctions match (SDN, UK)"


def test_rating_amber_reflag_only():
    now = datetime(2026, 10, 22, tzinfo=timezone.utc)
    events = [
        {
            "event_type": "reflag_dual_mmsi",
            "confidence": "reflag",
            "ts_start": now - timedelta(days=1),
            "sanctions_match": False,
            "sanctions_lists": None,
        }
    ]
    scored = score_ship(events, lookback_months=24, is_cargo_tanker=True, now=now)
    assert scored["rating"] == "AMBER"
    assert scored["score"] == 60
    assert scored["factors"]["reflagCount"] == 1
    assert scored["factors"]["highMisuseCount"] == 0


def test_stale_lloyds_conflict_not_red():
    now = datetime(2026, 10, 22, tzinfo=timezone.utc)
    assert (
        classify_lloyds_conflict(
            mmsi=205396000,
            ais_imo="1029314",
            lloyds_imo="9280392",
            agree_row_exists=True,
            ais_imo_lloyds_mmsi=205396000,
            lloyds_imo_live_mmsi=None,
            lloyds_imo_status="In Service/Commission",
            ais_imo_lloyds_mmsi_live=True,
        )
        == "stale_registry"
    )
    assert (
        classify_lloyds_conflict(
            mmsi=240542000,
            ais_imo="1029912",
            lloyds_imo="9312145",
            agree_row_exists=False,
            ais_imo_lloyds_mmsi=None,
            lloyds_imo_live_mmsi=None,
            lloyds_imo_status="In Casualty Or Repairing",
            ais_imo_lloyds_mmsi_live=False,
        )
        == "stale_registry"
    )
    assert (
        classify_lloyds_conflict(
            mmsi=352003715,
            ais_imo="9245249",
            lloyds_imo="9224673",
            agree_row_exists=False,
            ais_imo_lloyds_mmsi=352003771,
            lloyds_imo_live_mmsi=352003771,
            lloyds_imo_status="In Service/Commission",
            ais_imo_lloyds_mmsi_live=True,
        )
        == "true_conflict"
    )
    events = [
        {
            "event_type": "lloyds_mmsi_imo_conflict",
            "confidence": "low",
            "ts_start": now - timedelta(days=1),
            "sanctions_match": False,
            "sanctions_lists": None,
        }
    ]
    scored = score_ship(events, lookback_months=24, is_cargo_tanker=True, now=now)
    assert scored["rating"] == "AMBER"
    assert scored["score"] == 30


def test_primary_ship_key_prefers_imo():
    assert primary_event_ship_key({"imo": "1029314", "mmsi": 205396000}) == "imo:1029314"
    assert primary_event_ship_key({"imo": "", "mmsi": 205396000}) == "mmsi:205396000"


def test_out_of_window_info_only():
    now = datetime(2026, 10, 22, tzinfo=timezone.utc)
    events = [
        {
            "event_type": "lloyds_mmsi_imo_conflict",
            "confidence": "high",
            "ts_start": now - timedelta(days=900),
            "sanctions_match": False,
            "sanctions_lists": None,
        }
    ]
    scored = score_ship(events, lookback_months=24, is_cargo_tanker=True, now=now)
    assert scored["rating"] == "GREEN"
    assert scored["score"] == 0
    assert scored["event_count_window"] == 0
    assert scored["event_count_total"] == 1
    assert len(scored["info_events"]) == 1


def test_high_misuse_non_cargo_amber():
    now = datetime(2026, 10, 22, tzinfo=timezone.utc)
    events = [
        {
            "event_type": "dual_track_mmsi",
            "confidence": "high",
            "ts_start": now - timedelta(days=2),
            "sanctions_match": False,
            "sanctions_lists": None,
        }
    ]
    scored = score_ship(events, lookback_months=24, is_cargo_tanker=False, now=now)
    assert scored["rating"] == "AMBER"
    assert scored["score"] == 40


def test_api_shape_helpers():
    from identity_risk import _event_row_to_api

    api = _event_row_to_api(
        {
            "id": 1,
            "event_type": "dual_track_mmsi",
            "confidence": "high",
            "mmsi": 533133348,
            "imo": "8619132",
            "imo_other": "",
            "ship_name": "SILVER HARMONY",
            "ship_name_other": None,
            "ts_start": datetime(2026, 10, 19, tzinfo=timezone.utc),
            "ts_end": None,
            "distance_m": 100.0,
            "evidence": {"x": 1},
            "sanctions_match": False,
            "sanctions_lists": None,
            "rule_version": "v1",
        }
    )
    assert api["eventType"] == "dual_track_mmsi"
    assert api["mmsi"] == 533133348
    assert api["imoOther"] is None
    assert "tsStart" in api
