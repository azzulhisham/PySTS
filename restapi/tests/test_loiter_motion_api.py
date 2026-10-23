"""API add-only motion fields + optional filters for GET /mantis/loitering."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
RESTAPI = ROOT / "restapi"
if str(RESTAPI) not in sys.path:
    sys.path.insert(0, str(RESTAPI))

from loitering import (  # noqa: E402
    _apply_motion_filters,
    _duration_h,
    events_to_payload,
)


MOTION_KEYS = (
    "durationH",
    "avgSogKn",
    "maxSogKn",
    "sogStdKn",
    "sogChangeCount",
    "headingChangeCount",
    "loiterRatio",
)

LEGACY_CORE_KEYS = (
    "loiterId",
    "mmsi",
    "shipName",
    "shipType",
    "shipTypeDesc",
    "latitude",
    "longitude",
    "startedAt",
    "endedAt",
    "lastSeenAt",
    "radiusM",
    "pathM",
    "netM",
    "netOverPath",
    "sampleCount",
    "outsideAnchorage",
    "nearRestricted",
    "beforeSts",
    "inPortArea",
    "portAreaName",
    "portAreaTerminalName",
    "flags",
    "anchorageName",
    "restrictedDistanceM",
    "stsObservationId",
)


def _base_row(**overrides):
    row = {
        "loiter_id": 7,
        "mmsi": 533130001,
        "imo": None,
        "shipname": "LOITER",
        "shiptype": 70,
        "shiptypedesc": "Cargo",
        "centre_latitude": 1.0,
        "centre_longitude": 103.0,
        "started_at": pd.Timestamp("2026-01-01T00:00:00Z"),
        "ended_at": pd.Timestamp("2026-01-01T02:00:00Z"),
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
        "sanctions_match": False,
        "match_confidence": "none",
        "sanctions_list": None,
        "risk_rating": "GREEN",
        "risk_score": 0,
        "risk_reason": "none",
        "to_bow": None,
        "to_stern": None,
        "to_port": None,
        "to_starboard": None,
        "avg_sog_kn": 1.25,
        "max_sog_kn": 2.0,
        "sog_std_kn": 0.4,
        "sog_change_count": 4,
        "heading_change_count": 9,
        "loiter_ratio": 10.0,
    }
    row.update(overrides)
    return row


def test_duration_h_from_started_to_ended():
    assert _duration_h(
        pd.Timestamp("2026-01-01T00:00:00Z"),
        pd.Timestamp("2026-01-01T02:30:00Z"),
    ) == 2.5


def test_payload_includes_motion_fields():
    payload = events_to_payload(pd.DataFrame([_base_row()]))[0]
    assert payload["durationH"] == 2.0
    assert payload["avgSogKn"] == 1.25
    assert payload["maxSogKn"] == 2.0
    assert payload["sogStdKn"] == 0.4
    assert payload["sogChangeCount"] == 4
    assert payload["headingChangeCount"] == 9
    assert payload["loiterRatio"] == 10.0


def test_payload_null_motion_when_columns_absent():
    row = _base_row()
    for k in (
        "avg_sog_kn",
        "max_sog_kn",
        "sog_std_kn",
        "sog_change_count",
        "heading_change_count",
        "loiter_ratio",
    ):
        row.pop(k)
    payload = events_to_payload(pd.DataFrame([row]))[0]
    assert payload["avgSogKn"] is None
    assert payload["headingChangeCount"] is None
    assert payload["loiterRatio"] is None
    assert payload["durationH"] == 2.0


def test_golden_legacy_keys_unchanged():
    """Existing core keys/values identical after stripping new motion fields."""
    payload = events_to_payload(pd.DataFrame([_base_row()]))[0]
    legacy = {k: payload[k] for k in LEGACY_CORE_KEYS}
    # Frozen expected values for this synthetic row (pre-motion-field shape).
    assert legacy["loiterId"] == 7
    assert legacy["mmsi"] == 533130001
    assert legacy["shipName"] == "LOITER"
    assert legacy["shipType"] == 70
    assert legacy["latitude"] == 1.0
    assert legacy["longitude"] == 103.0
    assert legacy["radiusM"] == 200.0
    assert legacy["pathM"] == 500.0
    assert legacy["netM"] == 50.0
    assert legacy["netOverPath"] == 0.1
    assert legacy["sampleCount"] == 10
    assert legacy["outsideAnchorage"] is True
    assert legacy["nearRestricted"] is False
    assert legacy["beforeSts"] is False
    assert legacy["flags"] == ["outside_anchorage"]
    assert legacy["stsObservationId"] is None
    # New keys present but do not alter legacy mapping
    for k in MOTION_KEYS:
        assert k in payload
    stripped = {k: v for k, v in payload.items() if k not in MOTION_KEYS}
    # Re-build without motion columns → same non-motion keys/values
    bare = _base_row()
    for k in (
        "avg_sog_kn",
        "max_sog_kn",
        "sog_std_kn",
        "sog_change_count",
        "heading_change_count",
        "loiter_ratio",
    ):
        bare[k] = None
    bare_payload = events_to_payload(pd.DataFrame([bare]))[0]
    bare_stripped = {k: v for k, v in bare_payload.items() if k not in MOTION_KEYS}
    # durationH is a new field; exclude it from equality of "existing"
    assert {k: v for k, v in stripped.items() if k not in MOTION_KEYS} == bare_stripped
    assert json.dumps(legacy, sort_keys=True, default=str)


def test_optional_filters_default_off_keeps_all():
    df = pd.DataFrame([
        _base_row(mmsi=1, heading_change_count=1, avg_sog_kn=5.0),
        _base_row(mmsi=2, heading_change_count=20, avg_sog_kn=0.5),
    ])
    out = _apply_motion_filters(
        df, min_heading_changes=None, max_avg_sog_kn=None, min_duration_h=None
    )
    assert len(out) == 2


def test_optional_filters_opt_in():
    df = pd.DataFrame([
        _base_row(
            mmsi=1,
            heading_change_count=1,
            avg_sog_kn=5.0,
            started_at=pd.Timestamp("2026-01-01T00:00:00Z"),
            ended_at=pd.Timestamp("2026-01-01T01:00:00Z"),
        ),
        _base_row(
            mmsi=2,
            heading_change_count=20,
            avg_sog_kn=0.5,
            started_at=pd.Timestamp("2026-01-01T00:00:00Z"),
            ended_at=pd.Timestamp("2026-01-01T03:00:00Z"),
        ),
    ])
    by_hdg = _apply_motion_filters(
        df, min_heading_changes=10, max_avg_sog_kn=None, min_duration_h=None
    )
    assert list(by_hdg["mmsi"]) == [2]
    by_sog = _apply_motion_filters(
        df, min_heading_changes=None, max_avg_sog_kn=1.0, min_duration_h=None
    )
    assert list(by_sog["mmsi"]) == [2]
    by_dur = _apply_motion_filters(
        df, min_heading_changes=None, max_avg_sog_kn=None, min_duration_h=2.0
    )
    assert list(by_dur["mmsi"]) == [2]
