"""SOG / heading motion metrics for loitering (informational; detection unchanged)."""

from __future__ import annotations

import math
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
RESTAPI = ROOT / "restapi"
for path in (BACKEND, RESTAPI):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from vesselloiteringdetection import (  # noqa: E402
    HEADING_CHANGE_THRESHOLD_DEG,
    SOG_CHANGE_THRESHOLD_KN,
    _heading_delta_deg,
    _row_params,
    _usable_heading,
    _usable_sog,
    attach_loiter_ratio,
    compute_motion_metrics,
    motion_columns_present,
)


def _ts(hours: float = 0) -> datetime:
    return datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc) + timedelta(hours=hours)


def _circle_track(mmsi: int = 533130001, n: int = 24, radius_deg: float = 0.01) -> pd.DataFrame:
    """Ship circling: many heading changes, high path/net."""
    rows = []
    for i in range(n):
        angle = 2 * math.pi * i / n
        rows.append({
            "mmsi": mmsi,
            "ts": _ts(i * (2.0 / n)),
            "latitude": 1.0 + radius_deg * math.sin(angle),
            "longitude": 103.0 + radius_deg * math.cos(angle),
            "sog": 3.0 + 0.1 * (i % 3),
            "cog": (i * (360.0 / n)) % 360.0,
            "true_heading": (i * (360.0 / n)) % 360.0,
        })
    return pd.DataFrame(rows)


def _anchor_swing_track(mmsi: int = 533130002, n: int = 24) -> pd.DataFrame:
    """At anchor: low SOG, small heading wobble under threshold."""
    rows = []
    for i in range(n):
        rows.append({
            "mmsi": mmsi,
            "ts": _ts(i * (2.0 / n)),
            "latitude": 1.2 + 0.00005 * math.sin(i / 3),
            "longitude": 103.2 + 0.00005 * math.cos(i / 3),
            "sog": 0.1 + 0.05 * (i % 2),  # changes < 0.5 kn
            "cog": 90.0 + 5.0 * math.sin(i / 4),  # < 45° turns
            "true_heading": 90.0 + 5.0 * math.sin(i / 4),
        })
    return pd.DataFrame(rows)


def test_heading_wrap_359_to_1_is_two_degrees():
    assert abs(_heading_delta_deg(359.0, 1.0) - 2.0) < 1e-9
    assert abs(_heading_delta_deg(1.0, 359.0) - 2.0) < 1e-9
    assert abs(_heading_delta_deg(10.0, 100.0) - 90.0) < 1e-9


def test_heading_511_and_null_skipped():
    assert _usable_heading(511, 90.0) == 90.0
    assert _usable_heading(511, 511) is None
    assert _usable_heading(None, None) is None
    assert _usable_heading(float("nan"), 180.0) == 180.0
    assert _usable_sog(102.3) is None
    assert _usable_sog(101.9) == pytest.approx(101.9)
    assert _usable_sog(None) is None


def test_circling_ship_high_heading_changes_and_loiter_ratio():
    # 50° steps so each consecutive pair exceeds the 45° threshold.
    big = _circle_track(n=12)
    for i, idx in enumerate(big.index):
        big.at[idx, "true_heading"] = (i * 50.0) % 360.0
        big.at[idx, "cog"] = (i * 50.0) % 360.0
    metrics = compute_motion_metrics(big)
    row = metrics.iloc[0]
    assert int(row["heading_change_count"]) >= 8
    assert float(row["avg_sog_kn"]) > 0

    loiters = pd.DataFrame([{
        "mmsi": int(row["mmsi"]),
        "path_m": 5000.0,
        "net_m": 100.0,
        **{c: row[c] for c in metrics.columns if c != "mmsi"},
    }])
    loiters = attach_loiter_ratio(loiters)
    assert float(loiters.iloc[0]["loiter_ratio"]) == pytest.approx(50.0)
    assert float(loiters.iloc[0]["loiter_ratio"]) > 5.0


def test_anchor_swing_low_sog_few_changes():
    metrics = compute_motion_metrics(_anchor_swing_track())
    row = metrics.iloc[0]
    assert float(row["avg_sog_kn"]) < 1.0
    assert float(row["max_sog_kn"]) < 1.0
    assert int(row["sog_change_count"]) == 0
    assert int(row["heading_change_count"]) == 0


def test_loiter_ratio_null_when_net_near_zero():
    df = pd.DataFrame([{"mmsi": 1, "path_m": 100.0, "net_m": 0.5}])
    out = attach_loiter_ratio(df)
    val = out.iloc[0]["loiter_ratio"]
    assert val is None or (isinstance(val, float) and math.isnan(val))


def test_thresholds_documented():
    assert SOG_CHANGE_THRESHOLD_KN == 0.5
    assert HEADING_CHANGE_THRESHOLD_DEG == 45.0


def test_row_params_omit_motion_when_flag_false():
    row = pd.Series({
        "mmsi": 533130001,
        "place_key": "1.0_103.0",
        "started_at": _ts(),
        "ended_at": _ts(2),
        "centre_latitude": 1.0,
        "centre_longitude": 103.0,
        "radius_m": 200.0,
        "path_m": 500.0,
        "net_m": 40.0,
        "sample_count": 10,
        "outside_anchorage": True,
        "near_restricted": False,
        "anchorage_name": None,
        "restricted_distance_m": None,
        "avg_sog_kn": 1.5,
        "max_sog_kn": 2.0,
        "sog_std_kn": 0.2,
        "sog_change_count": 3,
        "heading_change_count": 5,
        "loiter_ratio": 12.5,
    })
    now = _ts(3)
    without = _row_params(row, now, include_motion=False)
    assert "avg_sog_kn" not in without
    with_m = _row_params(row, now, include_motion=True)
    assert with_m["avg_sog_kn"] == 1.5
    assert with_m["heading_change_count"] == 5


def test_motion_columns_present_false_when_missing():
    engine = MagicMock()
    conn = MagicMock()
    engine.connect.return_value.__enter__.return_value = conn
    conn.execute.return_value.fetchall.return_value = [
        ("mmsi",),
        ("path_m",),
        ("net_m",),
    ]
    assert motion_columns_present(engine) is False


def test_motion_columns_present_true_when_all_exist():
    engine = MagicMock()
    conn = MagicMock()
    engine.connect.return_value.__enter__.return_value = conn
    cols = [
        "mmsi",
        "avg_sog_kn",
        "max_sog_kn",
        "sog_std_kn",
        "sog_change_count",
        "heading_change_count",
        "loiter_ratio",
    ]
    conn.execute.return_value.fetchall.return_value = [(c,) for c in cols]
    assert motion_columns_present(engine) is True
