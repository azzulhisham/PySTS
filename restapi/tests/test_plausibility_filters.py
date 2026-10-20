"""Unit tests for Action item 1 plausibility filters (no DB / ClickHouse)."""

from __future__ import annotations

import math
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest

RESTAPI = Path(__file__).resolve().parents[1]
if str(RESTAPI) not in sys.path:
    sys.path.insert(0, str(RESTAPI))

from ais_identity import clean_ship_name, is_valid_ship_mmsi, mid_from_mmsi  # noqa: E402
from spoofing import (  # noqa: E402
    BAD_DATA_KNOTS,
    DATA_QUALITY_REASONS,
    DUP_OUTLIER_FRAC,
    HIGH_SPEED_MIN_KN,
    LONG_JUMP_DIST_M,
    LONG_JUMP_MIN_KN,
    MIN_DT_S,
    classify_pair_reason,
    is_duplicate_mmsi_track,
    relabel_duplicate_mmsi,
    should_replace_stored_anomaly,
)
from spoofing_vessel_analysis import (  # noqa: E402
    _assessment,
    _risk_level,
    build_speed_series,
)


def _offset(lat: float, lon: float, north_m: float, east_m: float) -> tuple[float, float]:
    """Approximate local offset in metres → degrees."""
    dlat = north_m / 111_320.0
    dlon = east_m / (111_320.0 * max(0.2, abs(math.cos(math.radians(lat)))))
    return lat + dlat, lon + dlon


class TestMmsiPlausibility:
    def test_valid_malaysian_mmsi(self):
        assert is_valid_ship_mmsi(533000757) is True
        assert mid_from_mmsi(533000757) == 533

    def test_mmsi_ending_000000_excluded(self):
        assert is_valid_ship_mmsi(533000000) is False

    def test_short_and_out_of_mid_excluded(self):
        assert is_valid_ship_mmsi(219) is False
        assert is_valid_ship_mmsi(199000123) is False
        assert is_valid_ship_mmsi(800000123) is False


class TestCleanShipName:
    def test_strip_at_and_spaces(self):
        assert clean_ship_name("ABC@@@@  ") == "ABC"

    def test_collapse_double_spaces(self):
        assert clean_ship_name("  ALPHA   BRAVO@@ ") == "ALPHA BRAVO"

    def test_none_and_empty(self):
        assert clean_ship_name(None) is None
        assert clean_ship_name("@@@@") is None


class TestSpeedSeries:
    def test_exact_duplicates_removed_before_speed(self):
        ts0 = datetime(2026, 9, 28, 10, 0, 0, tzinfo=timezone.utc)
        rows = []
        for i in range(3):
            rows.append({
                "mmsi": 533000111,
                "ts": ts0 + timedelta(seconds=i * 30),
                "latitude": 1.26,
                "longitude": 103.85,
                "sog": 8.0,
            })
        # Exact duplicate of the middle fix (same second, same lat/lon).
        rows.append({
            "mmsi": 533000111,
            "ts": ts0 + timedelta(seconds=30, milliseconds=5),
            "latitude": 1.26,
            "longitude": 103.85,
            "sog": 8.0,
        })
        series = build_speed_series(pd.DataFrame(rows))
        assert len(series) == 3

    def test_sub_min_dt_does_not_produce_million_kn(self):
        ts0 = datetime(2026, 9, 28, 10, 0, 0, tzinfo=timezone.utc)
        lat2, lon2 = _offset(1.26, 103.85, 365_000, 0)  # ~365 km north
        track = pd.DataFrame([
            {"mmsi": 219019094, "ts": ts0, "latitude": 1.26, "longitude": 103.85, "sog": 10.0},
            {
                "mmsi": 219019094,
                "ts": ts0 + timedelta(milliseconds=10),
                "latitude": lat2,
                "longitude": lon2,
                "sog": 10.0,
            },
            {
                "mmsi": 219019094,
                "ts": ts0 + timedelta(seconds=60),
                "latitude": lat2,
                "longitude": lon2,
                "sog": 10.0,
            },
        ])
        series = build_speed_series(track)
        implied = [r["impliedSpeedKn"] for r in series if r["impliedSpeedKn"] is not None]
        assert all(v < 100_000 for v in implied)
        # First gap is < MIN_DT_S so no implied; second gap is same position → ~0.
        assert series[1]["impliedSpeedKn"] is None

    def test_drops_not_available_position(self):
        ts0 = datetime(2026, 9, 28, 10, 0, 0, tzinfo=timezone.utc)
        track = pd.DataFrame([
            {"mmsi": 1, "ts": ts0, "latitude": 1.0, "longitude": 103.0, "sog": 5.0},
            {"mmsi": 1, "ts": ts0 + timedelta(seconds=30), "latitude": 91.0, "longitude": 181.0, "sog": 102.3},
            {"mmsi": 1, "ts": ts0 + timedelta(seconds=60), "latitude": 1.01, "longitude": 103.01, "sog": 5.0},
        ])
        series = build_speed_series(track)
        assert len(series) == 2


class TestDuplicateMmsi:
    def test_533000757_two_clusters_is_duplicate_not_teleport(self):
        """Two units ~211 km apart, near-simultaneous fixes → duplicate_mmsi."""
        ts0 = datetime(2026, 9, 28, 12, 0, 0, tzinfo=timezone.utc)
        a_lat, a_lon = 1.20, 103.50
        b_lat, b_lon = _offset(a_lat, a_lon, 211_000, 0)
        points = []
        hits = []
        for i in range(4):
            use_b = i % 2 == 1
            lat, lon = (b_lat, b_lon) if use_b else (a_lat, a_lon)
            prev_lat, prev_lon = (a_lat, a_lon) if use_b else (b_lat, b_lon)
            ts = ts0 + timedelta(seconds=i, milliseconds=i)  # 0–1 ms style gaps between pairs
            points.append({"ts": ts, "latitude": lat, "longitude": lon})
            if i > 0:
                hits.append({
                    "mmsi": 533000757,
                    "ts": ts,
                    "prev_ts": ts0 + timedelta(seconds=i - 1),
                    "prev_lat": prev_lat,
                    "prev_lon": prev_lon,
                    "latitude": lat,
                    "longitude": lon,
                    "dist_m": 211_000.0,
                    "dt_s": 0,
                    "implied_kn": 50_000.0,
                    "reason": "teleport",
                })
        assert is_duplicate_mmsi_track(pd.DataFrame(points)) is True
        relabelled = relabel_duplicate_mmsi(pd.DataFrame(hits))
        assert (relabelled["reason"] == "duplicate_mmsi").all()
        assert "teleport" not in set(relabelled["reason"])

    def test_allows_up_to_10pct_outliers(self):
        """RF ghosts outside the two clusters (≤10%) still label duplicate_mmsi."""
        ts0 = datetime(2026, 9, 28, 12, 0, 0, tzinfo=timezone.utc)
        a_lat, a_lon = 1.20, 103.50
        b_lat, b_lon = _offset(a_lat, a_lon, 211_000, 0)
        ghost_lat, ghost_lon = _offset(a_lat, a_lon, 50_000, 50_000)
        points = []
        # 10 in-cluster alternating points + 1 outlier = 1/11 < 10%? 1/11≈9.1% OK
        # Need ≥3 switches among in-cluster labels.
        for i in range(10):
            use_b = i % 2 == 1
            lat, lon = (b_lat, b_lon) if use_b else (a_lat, a_lon)
            points.append({
                "ts": ts0 + timedelta(seconds=i),
                "latitude": lat,
                "longitude": lon,
            })
        points.append({
            "ts": ts0 + timedelta(seconds=10),
            "latitude": ghost_lat,
            "longitude": ghost_lon,
        })
        assert len(points) == 11
        assert 1 / 11 <= DUP_OUTLIER_FRAC + 1e-9
        assert is_duplicate_mmsi_track(pd.DataFrame(points)) is True

    def test_too_many_outliers_rejects(self):
        ts0 = datetime(2026, 9, 28, 12, 0, 0, tzinfo=timezone.utc)
        a_lat, a_lon = 1.20, 103.50
        b_lat, b_lon = _offset(a_lat, a_lon, 211_000, 0)
        ghost_lat, ghost_lon = _offset(a_lat, a_lon, 50_000, 50_000)
        points = []
        for i in range(4):
            use_b = i % 2 == 1
            lat, lon = (b_lat, b_lon) if use_b else (a_lat, a_lon)
            points.append({
                "ts": ts0 + timedelta(seconds=i),
                "latitude": lat,
                "longitude": lon,
            })
        # 2 outliers / 6 = 33% > 10%
        for j in range(2):
            points.append({
                "ts": ts0 + timedelta(seconds=10 + j),
                "latitude": ghost_lat,
                "longitude": ghost_lon,
            })
        assert is_duplicate_mmsi_track(pd.DataFrame(points)) is False

    def test_duplicate_not_in_alert_reasons_by_default(self):
        assert "duplicate_mmsi" in DATA_QUALITY_REASONS
        assert "bad_position_data" in DATA_QUALITY_REASONS


class TestBadPositionData:
    def test_sustained_25km_jump_is_teleport_alert(self):
        """Sustained 25 km jump, dt ≥ 10 s → teleport, not bad_position_data."""
        dist_m = 25_000.0
        dt_s = 30.0
        implied = (dist_m / dt_s) * 1.94384
        assert dist_m >= LONG_JUMP_DIST_M
        assert implied > LONG_JUMP_MIN_KN
        assert dt_s >= MIN_DT_S
        reason = classify_pair_reason(
            dt_s=dt_s, dist_m=dist_m, implied_kn=implied, snap_back=False,
        )
        assert reason == "teleport"
        assert reason not in DATA_QUALITY_REASONS
        assert _risk_level(implied, reason) == "critical"

    def test_219019094_ghost_snapback_is_bad_data(self):
        """MMSI 219019094 style: 365 km jump then snap-back → bad_position_data."""
        dist_m = 365_000.0
        dt_s = 10.0
        implied = (dist_m / dt_s) * 1.94384
        assert implied > HIGH_SPEED_MIN_KN
        # Speed alone must NOT force bad_position_data when optional cap is off.
        assert BAD_DATA_KNOTS == 0.0
        sustained = classify_pair_reason(
            dt_s=dt_s, dist_m=dist_m, implied_kn=implied, snap_back=False,
        )
        assert sustained == "teleport"
        ghost = classify_pair_reason(
            dt_s=dt_s, dist_m=dist_m, implied_kn=implied, snap_back=True,
        )
        assert ghost == "bad_position_data"
        assert _risk_level(implied, ghost) == "data quality"
        assert "snap-back" in _assessment(ghost, implied).lower() or "short-gap" in _assessment(ghost, implied).lower()

    def test_short_gap_far_jump_is_bad_data(self):
        dist_m = 50_000.0
        dt_s = 1.0
        implied = (dist_m / max(dt_s, 1)) * 1.94384
        reason = classify_pair_reason(
            dt_s=dt_s, dist_m=dist_m, implied_kn=implied, snap_back=False,
        )
        assert reason == "bad_position_data"


class TestDarkMaxDaysLogic:
    """Pure classification mirror of the SQL CASE (no DB)."""

    @staticmethod
    def classify(silence_days: float, rowcount: int = 10, sog: float = 0.0,
                 coverage_exit_days: int = 3, dark_max_days: int = 7) -> str:
        if silence_days >= dark_max_days:
            return "possible_coverage_exit"
        if (
            rowcount >= 5
            and rowcount < 30
            and silence_days < coverage_exit_days
        ):
            return "suspected_dark_after_slowdown"
        if silence_days >= coverage_exit_days or sog > 3.0:
            return "possible_coverage_exit"
        return "low_evidence_ais_gap"

    def test_61_day_gap_is_coverage_exit(self):
        assert self.classify(61) == "possible_coverage_exit"

    def test_coverage_exit_hidden_when_flag_false(self):
        reason = self.classify(61)
        include_coverage_exit = False
        shown = reason != "possible_coverage_exit" or include_coverage_exit
        assert shown is False

    def test_short_slowdown_still_suspected(self):
        assert self.classify(1.0, rowcount=10) == "suspected_dark_after_slowdown"


class TestUpsertPriority:
    """Mirrors ais_positionanomaly ON CONFLICT WHERE (no DDL / no live DB)."""

    def test_ghost_then_teleport_stores_teleport(self):
        """Ghost 2,000,000 kn at 07:00, then teleport 40,000 kn at 15:00 → teleport."""
        assert should_replace_stored_anomaly(
            stored_reason="bad_position_data",
            stored_implied_kn=2_000_000.0,
            new_reason="teleport",
            new_implied_kn=40_000.0,
        ) is True

    def test_teleport_then_ghost_keeps_teleport(self):
        """Reverse order: teleport stored first; later ghost must not replace it."""
        assert should_replace_stored_anomaly(
            stored_reason="teleport",
            stored_implied_kn=40_000.0,
            new_reason="bad_position_data",
            new_implied_kn=2_000_000.0,
        ) is False

    def test_same_class_keeps_higher_implied_kn(self):
        assert should_replace_stored_anomaly(
            stored_reason="teleport",
            stored_implied_kn=100.0,
            new_reason="high_speed",
            new_implied_kn=200.0,
        ) is True
        assert should_replace_stored_anomaly(
            stored_reason="teleport",
            stored_implied_kn=200.0,
            new_reason="high_speed",
            new_implied_kn=100.0,
        ) is False
        assert should_replace_stored_anomaly(
            stored_reason="bad_position_data",
            stored_implied_kn=1_000.0,
            new_reason="duplicate_mmsi",
            new_implied_kn=5_000.0,
        ) is True


class TestRiskAssessment:
    def test_duplicate_mmsi_is_data_quality(self):
        assert _risk_level(1000.0, "duplicate_mmsi") == "data quality"
        assert "duplicate" in _assessment("duplicate_mmsi", 1000.0).lower()

    def test_teleport_still_critical(self):
        assert _risk_level(120.0, "teleport") == "critical"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
