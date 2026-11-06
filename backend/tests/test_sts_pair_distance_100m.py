"""Live STS pair distance gate at 100 m (strict <). No network."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
RESTAPI = ROOT / "restapi"
for path in (BACKEND, RESTAPI):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from vesselproximitydetection import (  # noqa: E402
    DETECTION_VERSION,
    MAX_DISTANCE_M as DETECT_MAX_M,
    find_close_pairs,
)
from sts_detection import (  # noqa: E402
    MAX_DISTANCE_M as API_MAX_M,
    find_pairs_within_observations,
)


LAT0 = 1.25
LON0 = 103.80
# Live code uses ST_Point(longitude, latitude). DuckDB spatial ST_Distance_Sphere
# reads Point as (lat, lon), so a longitude delta maps to a meridian arc ≈ this many m/°.
_DUCKDB_M_PER_DEG = 111_194.92664455874


def _offset_for_duckdb_distance_m(distance_m: float) -> tuple[float, float]:
    """Place B so production ST_Point(lon,lat) sphere distance ≈ distance_m."""
    return LAT0, LON0 + (distance_m / _DUCKDB_M_PER_DEG)


def _vessel_frame(distance_m: float) -> pd.DataFrame:
    lat_b, lon_b = _offset_for_duckdb_distance_m(distance_m)
    return pd.DataFrame(
        [
            {"mmsi": 111000001, "curlatitude": LAT0, "curlongitude": LON0},
            {"mmsi": 111000002, "curlatitude": lat_b, "curlongitude": lon_b},
        ]
    )


def test_constants_aligned_at_100():
    assert DETECT_MAX_M == 100.0
    assert API_MAX_M == 100.0
    assert DETECT_MAX_M == API_MAX_M
    assert "100" in DETECTION_VERSION


@pytest.mark.parametrize(
    "distance_m, expect_pair",
    [
        (29.0, True),
        (30.0, True),
        (35.0, True),
        (99.0, True),
        (99.9, True),
        (100.0, False),  # strict <
        (100.1, False),
        (150.0, False),
    ],
)
def test_detection_find_close_pairs_boundary(distance_m, expect_pair):
    pairs = find_close_pairs(_vessel_frame(distance_m), max_distance_m=DETECT_MAX_M)
    assert (not pairs.empty) is expect_pair
    if expect_pair:
        assert float(pairs.iloc[0]["distance_m"]) < DETECT_MAX_M


def _obs_and_members(distance_m: float) -> tuple[pd.DataFrame, pd.DataFrame]:
    lat_b, lon_b = _offset_for_duckdb_distance_m(distance_m)
    observations = pd.DataFrame(
        [
            {
                "observation_id": 1,
                "anchorage_name": "test",
                "suspicion_score": 5.0,
                "duration_seconds": 7200,
                "first_detected_at": None,
                "last_detected_at": None,
                "vessel_count": 2,
                "cargo_count": 1,
                "tanker_count": 1,
            }
        ]
    )
    members = pd.DataFrame(
        [
            {
                "observation_id": 1,
                "mmsi": 111000001,
                "shipname": "A",
                "shiptype": 70,
                "curlongitude": LON0,
                "curlatitude": LAT0,
                "sog": 0.1,
                "cog": 10.0,
                "tscurrent": None,
                "tsstop": None,
                "last_seen_at": None,
                "imo": 1,
                "to_bow": 40,
                "to_stern": 40,
                "to_port": 5,
                "to_starboard": 5,
            },
            {
                "observation_id": 1,
                "mmsi": 111000002,
                "shipname": "B",
                "shiptype": 80,
                "curlongitude": lon_b,
                "curlatitude": lat_b,
                "sog": 0.1,
                "cog": 20.0,
                "tscurrent": None,
                "tsstop": None,
                "last_seen_at": None,
                "imo": 2,
                "to_bow": 50,
                "to_stern": 50,
                "to_port": 5,
                "to_starboard": 5,
            },
        ]
    )
    return observations, members


@pytest.mark.parametrize(
    "distance_m, expect_pair",
    [
        (29.0, True),
        (30.0, True),
        (35.0, True),
        (99.0, True),
        (100.0, False),
        (101.0, False),
    ],
)
def test_api_presentation_pairs_boundary(distance_m, expect_pair):
    obs, members = _obs_and_members(distance_m)
    pairs = find_pairs_within_observations(obs, members, max_distance_m=API_MAX_M)
    assert (len(pairs) == 1) is expect_pair


def test_former_30_to_100_band_now_visible_in_both_layers():
    """Pairs that failed the old 30 m gate must now pass detection and API."""
    for d in (30.0, 50.0, 80.0, 99.0):
        assert not find_close_pairs(_vessel_frame(d)).empty
        obs, members = _obs_and_members(d)
        assert len(find_pairs_within_observations(obs, members)) == 1


def test_old_35_api_only_gap_closed():
    """50 m was past pipeline 30 m and past old API 35 m — both accept now."""
    assert not find_close_pairs(_vessel_frame(50.0)).empty
    obs, members = _obs_and_members(50.0)
    assert len(find_pairs_within_observations(obs, members)) == 1


def test_explicit_old_30_limit_still_excludes_50m():
    """Preserving the strict-< convention: at 30 m max, 50 m must fail."""
    pairs = find_close_pairs(_vessel_frame(50.0), max_distance_m=30.0)
    assert pairs.empty


def test_same_hull_identity_still_available():
    from vesselproximitydetection import is_same_vessel

    a = {"imo": "1234567", "name": "ALPHA", "callsign": "X", "dims": (1, 2, 3, 4)}
    b = {"imo": "1234567", "name": "ALPHA", "callsign": "Y", "dims": (9, 9, 9, 9)}
    assert is_same_vessel(a, b) is True


def test_candidate_sql_still_requires_stopped_or_stale():
    """Non-distance eligibility must stay in load_candidate_vessels SQL."""
    from vesselproximitydetection import load_candidate_vessels
    import inspect

    src = inspect.getsource(load_candidate_vessels)
    assert "tsstop" in src
    assert "1 HOURS" in src or "1 hours" in src.lower() or "interval '1" in src.lower()
    assert "30 MINUTES" in src or "30 minutes" in src.lower()
    assert "tsout IS NULL" in src
