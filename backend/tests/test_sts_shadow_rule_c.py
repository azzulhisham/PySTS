"""Unit tests for STS shadow rule C (no live DB / ClickHouse)."""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
RESTAPI = ROOT / "restapi"
for path in (BACKEND, RESTAPI):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from sts_shadow_detection import (  # noqa: E402
    BucketHit,
    EpisodeCandidate,
    MIN_EPISODE,
    build_pair_hits,
    can_extend_open,
    enrich_candidates,
    group_bucket_hits_into_runs,
    is_same_vessel,
    pair_qualifies,
    possible_bunker,
    select_persistable_candidates,
)


def _ts(hours: float = 0) -> datetime:
    return datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc) + timedelta(hours=hours)


def _hit(ts: datetime, dist: float = 80.0, band: str = "100m") -> BucketHit:
    return BucketHit(
        ts=ts,
        distance_m=dist,
        sog_a=1.0,
        sog_b=1.0,
        lat_a=1.0,
        lon_a=103.0,
        lat_b=1.0005,
        lon_b=103.0,
        rule_band=band,
    )


def test_pair_100m_cargo():
    ok, band = pair_qualifies(80.0, 70, 70, 100.0, 100.0, 1.0, 1.0)
    assert ok and band == "100m"


def test_pair_tanker200m_both_long():
    ok, band = pair_qualifies(150.0, 80, 82, 160.0, 170.0, 1.0, 2.0)
    assert ok and band == "tanker200m"


def test_pair_tanker200m_unknown_length_rejected():
    ok, band = pair_qualifies(150.0, 80, 82, None, None, 1.0, 1.0)
    assert not ok and band is None


def test_pair_tanker200m_one_long_ok():
    ok, band = pair_qualifies(150.0, 80, 82, 160.0, None, 1.0, 1.0)
    assert ok and band == "tanker200m"


def test_pair_sog_too_fast():
    ok, band = pair_qualifies(50.0, 70, 70, 100.0, 100.0, 6.0, 1.0)
    assert not ok


def test_episode_with_one_missed_bucket():
    hits = [_hit(_ts(i * 5 / 60.0)) for i in range(0, 30)]
    # Drop one interior bucket (index 10)
    del hits[10]
    runs = group_bucket_hits_into_runs({(111, 222): hits})
    assert len(runs) == 1
    assert (runs[0].ts_last - runs[0].ts_start) >= MIN_EPISODE


def test_same_hull_suppressed():
    a = {"imo": "1234567", "name": "ALPHA", "callsign": "X", "dims": (1, 2, 3, 4)}
    b = {"imo": "1234567", "name": "ALPHA", "callsign": "Y", "dims": (9, 9, 9, 9)}
    assert is_same_vessel(a, b)


def test_bunker_hub_three_partners():
    # Three partners for mmsi 1 via four ships in one bucket near each other
    base = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    static = pd.DataFrame(
        [
            {"mmsi": 1, "ship_name": "A", "ship_type": 80, "callsign": "A", "imo": 1,
             "to_bow": 40, "to_stern": 40, "to_port": 5, "to_starboard": 5,
             "length_m": 80.0, "name": "A", "dims": (40, 40, 5, 5)},
            {"mmsi": 2, "ship_name": "B", "ship_type": 70, "callsign": "B", "imo": 2,
             "to_bow": 50, "to_stern": 50, "to_port": 5, "to_starboard": 5,
             "length_m": 100.0, "name": "B", "dims": (50, 50, 5, 5)},
            {"mmsi": 3, "ship_name": "C", "ship_type": 70, "callsign": "C", "imo": 3,
             "to_bow": 50, "to_stern": 50, "to_port": 5, "to_starboard": 5,
             "length_m": 100.0, "name": "C", "dims": (50, 50, 5, 5)},
            {"mmsi": 4, "ship_name": "D", "ship_type": 70, "callsign": "D", "imo": 4,
             "to_bow": 50, "to_stern": 50, "to_port": 5, "to_starboard": 5,
             "length_m": 100.0, "name": "D", "dims": (50, 50, 5, 5)},
        ]
    )
    # Place all within ~50 m of (1.0, 103.0)
    samples = pd.DataFrame(
        [
            {"mmsi": 1, "ts": base, "latitude": 1.0, "longitude": 103.0, "sog": 0.5},
            {"mmsi": 2, "ts": base, "latitude": 1.0003, "longitude": 103.0, "sog": 0.5},
            {"mmsi": 3, "ts": base, "latitude": 1.0000, "longitude": 103.0003, "sog": 0.5},
            {"mmsi": 4, "ts": base, "latitude": 0.9997, "longitude": 103.0, "sog": 0.5},
        ]
    )
    hits = build_pair_hits(samples, static)
    runs = enrich_candidates(group_bucket_hits_into_runs(hits))
    hub = [c for c in runs if c.mmsi_a == 1 or c.mmsi_b == 1]
    assert hub
    assert any(c.bunker_hub_a or c.bunker_hub_b for c in hub)
    assert all(not (c.mmsi_a == 1 and c.mmsi_b == 1) for c in runs)  # kept pairs


@pytest.mark.parametrize(
    "stype,length,expect",
    [
        (80, 80.0, True),
        (80, None, False),
        (80, 120.0, False),
        (70, 80.0, False),
    ],
)
def test_possible_bunker(stype, length, expect):
    assert possible_bunker(stype, length, 70, 200.0) is expect


def test_10h_episode_one_row_across_cycles():
    """6 h lookback windows; open-row continuity keeps a single episode."""
    pair = (100, 200)
    t0 = _ts(0)
    # Cycle 1: 2.5 h run → insert
    run1 = EpisodeCandidate(
        mmsi_a=pair[0],
        mmsi_b=pair[1],
        ts_start=t0,
        ts_last=t0 + timedelta(hours=2.5),
        hits=[_hit(t0 + timedelta(minutes=5 * i)) for i in range(30)],
    )
    actions, close = select_persistable_candidates([run1], {})
    assert len(actions) == 1 and actions[0][0] == "insert"
    assert close == []

    open_rows = {
        pair: {
            "id": 1,
            "mmsi_a": pair[0],
            "mmsi_b": pair[1],
            "ts_start": t0,
            "ts_last": t0 + timedelta(hours=2.5),
            "min_distance_m": 50.0,
            "max_distance_m": 90.0,
            "median_distance_m": 70.0,
            "median_sog_kn": 1.0,
            "rule_band": "100m",
            "possible_bunker": False,
            "bunker_hub_a": False,
            "bunker_hub_b": False,
        }
    }
    # Cycle 2: window slides; run starts near previous ts_last, ends at +5 h
    run2_start = t0 + timedelta(hours=2.5)
    run2 = EpisodeCandidate(
        mmsi_a=pair[0],
        mmsi_b=pair[1],
        ts_start=run2_start,
        ts_last=t0 + timedelta(hours=5.0),
        hits=[_hit(run2_start + timedelta(minutes=5 * i)) for i in range(30)],
    )
    assert can_extend_open(open_rows[pair]["ts_last"], run2.ts_start)
    actions, close = select_persistable_candidates([run2], open_rows)
    assert len(actions) == 1 and actions[0][0] == "extend"
    assert close == []

    # Advance open ts_last; cycle near 10 h
    open_rows[pair]["ts_last"] = t0 + timedelta(hours=5.0)
    run3 = EpisodeCandidate(
        mmsi_a=pair[0],
        mmsi_b=pair[1],
        ts_start=t0 + timedelta(hours=5.0),
        ts_last=t0 + timedelta(hours=10.0),
        hits=[_hit(t0 + timedelta(hours=5) + timedelta(minutes=5 * i)) for i in range(60)],
    )
    actions, close = select_persistable_candidates([run3], open_rows)
    assert len(actions) == 1 and actions[0][0] == "extend"
    assert actions[0][2]["ts_start"] == t0
    assert close == []
    # Still one conceptual row (id=1); ts_start unchanged
    assert open_rows[pair]["id"] == 1
    assert open_rows[pair]["ts_start"] == t0


def test_build_pair_hits_100m():
    base = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    static = pd.DataFrame(
        [
            {"mmsi": 10, "ship_name": "A", "ship_type": 70, "callsign": "A", "imo": 10,
             "to_bow": 40, "to_stern": 40, "to_port": 5, "to_starboard": 5,
             "length_m": 80.0, "name": "A", "dims": (40, 40, 5, 5)},
            {"mmsi": 20, "ship_name": "B", "ship_type": 70, "callsign": "B", "imo": 20,
             "to_bow": 40, "to_stern": 40, "to_port": 5, "to_starboard": 5,
             "length_m": 80.0, "name": "B", "dims": (40, 40, 5, 5)},
        ]
    )
    # ~55 m apart
    samples = pd.DataFrame(
        [
            {"mmsi": 10, "ts": base, "latitude": 1.0, "longitude": 103.0, "sog": 1.0},
            {"mmsi": 20, "ts": base, "latitude": 1.0005, "longitude": 103.0, "sog": 1.0},
        ]
    )
    hits = build_pair_hits(samples, static)
    assert (10, 20) in hits
    assert hits[(10, 20)][0].rule_band == "100m"
