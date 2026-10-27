"""pnav-tuning for ais_sts_vesselzone — detection-stable, pool/retry/cleanup."""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

import pandas as pd
import pytest
from sqlalchemy.exc import OperationalError

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

import vesselzone as vz  # noqa: E402


SAMPLE_OPEN = [
    {
        "id": 1,
        "tsDetected": datetime(2026, 10, 1, 12, 0, 0),
        "mmsi": 533000001,
        "navStatus": 0,
        "navStatusDesc": "Under way",
        "longitude": 101.0,
        "latitude": 2.5,
        "rot": 0.0,
        "cog": 10.0,
        "sog": 5.0,
        "trueHeading": 10.0,
        "tsCurrent": datetime(2026, 10, 1, 12, 0, 0),
        "tsOut": None,
        "zone": 0,
    },
    {
        "id": 2,
        "tsDetected": datetime(2026, 10, 1, 11, 0, 0),
        "mmsi": 533000002,
        "navStatus": 0,
        "navStatusDesc": "Under way",
        "longitude": 101.1,
        "latitude": 2.6,
        "rot": 0.0,
        "cog": 20.0,
        "sog": 6.0,
        "trueHeading": 20.0,
        "tsCurrent": datetime(2026, 10, 1, 11, 0, 0),
        "tsOut": None,
        "zone": 0,
    },
]

SAMPLE_DATA = [
    {
        "ts": datetime(2026, 10, 1, 12, 5, 0),
        "mmsi": 533000001,
        "navStatus": 0,
        "navStatusDesc": "Under way",
        "longitude": 101.01,
        "latitude": 2.51,
        "rot": 0.1,
        "cog": 11.0,
        "sog": 5.1,
        "trueHeading": 11.0,
    },
    {
        # outside zone -> exit
        "ts": datetime(2026, 10, 1, 12, 6, 0),
        "mmsi": 533000002,
        "navStatus": 0,
        "navStatusDesc": "Under way",
        "longitude": 99.0,
        "latitude": 1.0,
        "rot": 0.0,
        "cog": 0.0,
        "sog": 0.0,
        "trueHeading": 0.0,
    },
    {
        # new enter
        "ts": datetime(2026, 10, 1, 12, 7, 0),
        "mmsi": 533000003,
        "navStatus": 1,
        "navStatusDesc": "At anchor",
        "longitude": 101.02,
        "latitude": 2.52,
        "rot": 0.0,
        "cog": 0.0,
        "sog": 0.0,
        "trueHeading": 511.0,
    },
]


def _legacy_decide(data, current_vessels_zone, point_in_zone):
    """Byte-for-byte logic of the pre-tuning decision loop (deployed copy)."""
    items_to_update = []
    items_to_insert = []
    zones = vz.zones

    for cnt, i in enumerate(data):
        for idx, zone in enumerate(zones):
            in_zone = point_in_zone(i["longitude"], i["latitude"], zone)
            existing_vessel_zone = False

            try:
                existing_vessel_zone = next(
                    filter(
                        lambda x: x["mmsi"] == i["mmsi"]
                        and x["zone"] == idx
                        and pd.isnull(x["tsOut"]),
                        current_vessels_zone,
                    ),
                    None,
                )
            except Exception:
                continue

            if in_zone:
                if existing_vessel_zone:
                    payload = existing_vessel_zone.copy()
                    payload["longitude"] = i["longitude"]
                    payload["latitude"] = i["latitude"]
                    payload["sog"] = i["sog"]
                    payload["cog"] = i["cog"]
                    payload["rot"] = i["rot"]
                    payload["trueHeading"] = i["trueHeading"]
                    payload["tsCurrent"] = i["ts"]
                    items_to_update.append(payload)
                else:
                    items_to_insert.append(
                        {
                            "tsDetected": i["ts"],
                            "mmsi": i["mmsi"],
                            "navStatus": i["navStatus"],
                            "navStatusDesc": i["navStatusDesc"],
                            "longitude": i["longitude"],
                            "latitude": i["latitude"],
                            "sog": i["sog"],
                            "cog": i["cog"],
                            "rot": i["rot"],
                            "trueHeading": i["trueHeading"],
                            "tsCurrent": i["ts"],
                            "tsOut": None,
                            "zone": idx,
                        }
                    )
            else:
                if existing_vessel_zone:
                    payload = existing_vessel_zone
                    payload["tsOut"] = i["ts"]
                    items_to_update.append(payload)

    return items_to_update, items_to_insert


def _fake_within(lon, lat, zone):
    # Inside the sample restricted bbox-ish area used above.
    return 100.5 <= lon <= 102.0 and 2.0 <= lat <= 3.0


def test_decide_zone_changes_matches_legacy_replay():
    open_rows = [dict(r) for r in SAMPLE_OPEN]
    open_legacy = [dict(r) for r in SAMPLE_OPEN]
    data = [dict(r) for r in SAMPLE_DATA]

    new_u, new_i = vz.decide_zone_changes(data, open_rows, point_in_zone=_fake_within)
    old_u, old_i = _legacy_decide(data, open_legacy, _fake_within)

    assert new_i == old_i
    assert len(new_u) == len(old_u)
    # Compare field-wise; exit path mutates open row in place on both sides.
    for a, b in zip(new_u, old_u):
        assert a["mmsi"] == b["mmsi"]
        assert a.get("tsOut") == b.get("tsOut")
        assert a.get("tsCurrent") == b.get("tsCurrent")
        assert a.get("longitude") == b.get("longitude")
        assert a.get("latitude") == b.get("latitude")

    # One stay-update, one exit, one insert.
    assert len(new_i) == 1 and new_i[0]["mmsi"] == 533000003
    exits = [u for u in new_u if u.get("tsOut") is not None]
    stays = [u for u in new_u if u.get("tsOut") is None]
    assert len(exits) == 1 and exits[0]["mmsi"] == 533000002
    assert len(stays) == 1 and stays[0]["mmsi"] == 533000001


def test_retry_once_then_succeeds(monkeypatch):
    calls = {"n": 0}

    def boom_then_ok():
        calls["n"] += 1
        if calls["n"] == 1:
            raise OperationalError("SELECT 1", {}, Exception("connection refused"))
        return 0

    resets = {"n": 0}
    monkeypatch.setattr(vz, "run_cycle", boom_then_ok)
    monkeypatch.setattr(vz, "_reset_engine", lambda: resets.__setitem__("n", resets["n"] + 1))

    assert vz.run_cycle_with_retry() == 0
    assert calls["n"] == 2
    assert resets["n"] == 1


def test_retry_once_then_skips(monkeypatch):
    calls = {"n": 0}

    def always_boom():
        calls["n"] += 1
        raise OperationalError("SELECT 1", {}, Exception("connection refused"))

    monkeypatch.setattr(vz, "run_cycle", always_boom)
    monkeypatch.setattr(vz, "_reset_engine", lambda: None)

    assert vz.run_cycle_with_retry() is None
    assert calls["n"] == 2  # original + one retry, never a loop


def test_cleanup_hourly_constant():
    assert vz.CLEANUP_INTERVAL_MINUTES == 60


def test_cleanup_uses_one_transaction(monkeypatch):
    """Outside MMSIs are closed with a single begin() batch UPDATE."""
    outside_mmsi = 533009999
    df = pd.DataFrame(
        [
            {
                "mmsi": outside_mmsi,
                "longitude": 0.0,
                "latitude": 0.0,
            }
        ]
    )

    monkeypatch.setattr(pd, "read_sql", lambda *a, **k: df)
    monkeypatch.setattr(
        vz.duckdb,
        "sql",
        lambda q: MagicMock(fetchall=lambda: [(False,)]),
    )

    executed = {"sql": None, "params": None}
    begin_calls = {"n": 0}

    class FakeConn:
        def execute(self, qry, params=None):
            executed["sql"] = str(qry)
            executed["params"] = params

        def __enter__(self):
            begin_calls["n"] += 1
            return self

        def __exit__(self, *exc):
            return False

    class FakeEngine:
        def begin(self):
            return FakeConn()

    monkeypatch.setattr(vz, "get_pgEngine", lambda: FakeEngine())

    assert vz.chk_invalid_data() == 0
    assert begin_calls["n"] == 1
    assert executed["params"] == {"mmsis": [outside_mmsi]}
    assert "UPDATE" in executed["sql"].upper()
    assert "ais_vesselinrestrictzone" in executed["sql"]


def test_engine_pool_settings(monkeypatch):
    vz._engine = None
    created = {}

    def fake_create_engine(url, **kwargs):
        created.update(kwargs)
        return MagicMock()

    monkeypatch.setattr(vz, "create_engine", fake_create_engine)
    eng = vz.get_pgEngine()
    assert eng is vz.get_pgEngine()  # cached
    assert created["pool_size"] == 2
    assert created["max_overflow"] == 2
    assert created["pool_pre_ping"] is True
    assert created["pool_recycle"] == 1800
    assert created["connect_args"]["application_name"] == "sts_vesselzone"
    assert created["connect_args"]["connect_timeout"] == 10
    assert created["connect_args"]["keepalives"] == 1
    opts = created["connect_args"]["options"]
    assert "statement_timeout=60000" in opts
    assert "lock_timeout=5000" in opts


def test_write_batches_chunk_size():
    rows = list(range(650))
    chunks = list(vz._chunks(rows, vz.COMMIT_BATCH_SIZE))
    assert vz.COMMIT_BATCH_SIZE == 300
    assert [len(c) for c in chunks] == [300, 300, 50]


def test_bbox_from_outer_region():
    assert vz.LON_MIN < vz.LON_MAX
    assert vz.LAT_MIN < vz.LAT_MAX
    # Padded outer restricted bbox (finite SE-Asia waters).
    assert 90.0 < vz.LON_MIN < 120.0
    assert 90.0 < vz.LON_MAX < 120.0
    assert -10.0 < vz.LAT_MIN < 20.0
    assert -10.0 < vz.LAT_MAX < 20.0
