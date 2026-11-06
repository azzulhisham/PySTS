"""Additive harbour context + confirmed-stop elapsed for suspected anchoring."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest
from shapely.geometry import mapping, shape

from illegal_anchoring import (
    POLYGON_PROVENANCE,
    annotate_suspected_anchoring_harbour,
    classify_illegal_anchoring,
    confirmed_stop_elapsed,
    drop_port_area_candidates,
    vessels_to_payload,
)
from port_areas import (
    PortArea,
    PortAreaIndex,
    annotate_harbour_location,
    get_port_area_index,
    reset_port_areas,
)

POLY_DIR = Path(__file__).resolve().parents[2] / "data" / "polygons" / "src"
WESTPORT_LON, WESTPORT_LAT = 101.29255516361354, 2.925089795917814
KLANG_ANCH_LON, KLANG_ANCH_LAT = 101.25, 2.75
OUTSIDE_LON, OUTSIDE_LAT = 100.0, 1.0


@pytest.fixture(autouse=True)
def _reset_port_cache():
    reset_port_areas(None)
    yield
    reset_port_areas(None)


def _stopped_row(**overrides):
    base = {
        "mmsi": 533000100,
        "curlongitude": KLANG_ANCH_LON,
        "curlatitude": KLANG_ANCH_LAT,
        "tsstop": pd.Timestamp("2026-11-06T00:00:00Z"),
        "tscurrent": pd.Timestamp("2026-11-06T03:00:00Z"),
        "ts": pd.Timestamp("2026-11-05T22:00:00Z"),
        "sog": 0.1,
        "cog": 10.0,
        "navstatusdesc": "At anchor",
        "shiptype": 70,
        "shiptypedesc": "Cargo",
        "shipname": "TEST CARGO",
        "imo": None,
        "rowcount": 40,
        "in_restricted": False,
        "in_watch_polygon": True,
        "in_port_limit": False,
        "watch_polygon_name": "Port Klang Anchorage",
        "port_limit_name": None,
        "reason": "in_watch_polygon",
        "detected_at": "2026-11-06T04:00:00+00:00",
    }
    base.update(overrides)
    return base


def test_polygon_files_present_under_pysts():
    for name in ("ports.geojson", "terminals.geojson", "berths.geojson"):
        path = POLY_DIR / name
        assert path.is_file(), path
        assert path.stat().st_size > 0


def test_confirmed_stop_elapsed_known():
    out = confirmed_stop_elapsed(
        pd.Timestamp("2026-11-06T00:00:00Z"),
        pd.Timestamp("2026-11-06T02:30:00Z"),
    )
    assert out["stopElapsedStatus"] == "known"
    assert out["stopElapsedReason"] is None
    assert out["stoppedElapsedHours"] == 2.5
    assert out["stoppedElapsedSeconds"] == 9000.0
    assert out["stopStartedAt"].startswith("2026-11-06T00:00:00")
    assert out["lastObservedAt"].startswith("2026-11-06T02:30:00")


def test_confirmed_stop_elapsed_unknown_without_tsstop():
    out = confirmed_stop_elapsed(None, pd.Timestamp("2026-11-06T02:00:00Z"))
    assert out["stopElapsedStatus"] == "unknown"
    assert out["stoppedElapsedHours"] is None
    assert out["stopStartedAt"] is None
    assert "tsstop is null" in out["stopElapsedReason"]
    assert out["lastObservedAt"] is not None


def test_confirmed_stop_elapsed_does_not_use_naive_now():
    """Stale AIS: elapsed ends at tscurrent, not wall clock."""
    out = confirmed_stop_elapsed(
        pd.Timestamp("2026-11-01T00:00:00Z"),
        pd.Timestamp("2026-11-01T01:00:00Z"),
    )
    assert out["stoppedElapsedHours"] == 1.0


def test_confirmed_stop_elapsed_inconsistent_order():
    out = confirmed_stop_elapsed(
        pd.Timestamp("2026-11-06T03:00:00Z"),
        pd.Timestamp("2026-11-06T01:00:00Z"),
    )
    assert out["stopElapsedStatus"] == "unknown"
    assert out["stoppedElapsedHours"] is None
    assert "inconsistent_timestamps" in out["stopElapsedReason"]


def test_confirmed_stop_elapsed_naive_timestamps_treated_as_utc():
    out = confirmed_stop_elapsed(
        pd.Timestamp("2026-11-06T00:00:00"),
        pd.Timestamp("2026-11-06T01:00:00"),
    )
    assert out["stopElapsedStatus"] == "known"
    assert out["stoppedElapsedHours"] == 1.0
    assert out["stopStartedAt"].endswith("+00:00") or "Z" in out["stopStartedAt"] or "+00:00" in out["stopStartedAt"]


def test_reopened_stop_does_not_inherit_prior_elapsed():
    """
    After movement resumes and a new open activity is confirmed, elapsed must
    use only the new tsstop→tscurrent pair — not the previous stop's duration.
    """
    prior = confirmed_stop_elapsed(
        pd.Timestamp("2026-11-05T00:00:00Z"),
        pd.Timestamp("2026-11-05T10:00:00Z"),
    )
    assert prior["stoppedElapsedHours"] == 10.0

    # New open row after departure (tsout closed the prior activity) and re-stop.
    reopened = confirmed_stop_elapsed(
        pd.Timestamp("2026-11-06T12:00:00Z"),
        pd.Timestamp("2026-11-06T13:00:00Z"),
    )
    assert reopened["stopElapsedStatus"] == "known"
    assert reopened["stoppedElapsedHours"] == 1.0
    assert reopened["stoppedElapsedHours"] != prior["stoppedElapsedHours"]
    assert reopened["stopStartedAt"].startswith("2026-11-06T12:00:00")
    assert not reopened["stopStartedAt"].startswith("2026-11-05")


def test_payload_reopened_stop_isolates_elapsed_per_row():
    """Two candidate rows (prior long stop vs new short stop) stay independent."""
    frame = pd.DataFrame([
        _stopped_row(
            mmsi=533000201,
            tsstop=pd.Timestamp("2026-11-05T00:00:00Z"),
            tscurrent=pd.Timestamp("2026-11-05T08:00:00Z"),
            ts=pd.Timestamp("2026-11-04T20:00:00Z"),
        ),
        _stopped_row(
            mmsi=533000202,
            tsstop=pd.Timestamp("2026-11-06T12:00:00Z"),
            tscurrent=pd.Timestamp("2026-11-06T12:30:00Z"),
            ts=pd.Timestamp("2026-11-06T10:00:00Z"),
        ),
    ])
    payload = {row["mmsi"]: row for row in vessels_to_payload(frame)}
    assert payload[533000201]["stoppedElapsedHours"] == 8.0
    assert payload[533000202]["stoppedElapsedHours"] == 0.5
    # Short stop must not pick up the long prior duration.
    assert payload[533000202]["stoppedElapsedHours"] < payload[533000201]["stoppedElapsedHours"]


def test_movement_resumption_without_new_tsstop_is_unknown():
    """
    After departure, a stale-only reopen before confirmation has null tsstop —
    elapsed must be unknown, not the previous stop's hours.
    """
    prior_hours = confirmed_stop_elapsed(
        pd.Timestamp("2026-11-05T00:00:00Z"),
        pd.Timestamp("2026-11-05T06:00:00Z"),
    )["stoppedElapsedHours"]
    assert prior_hours == 6.0

    after_resume = confirmed_stop_elapsed(
        None,
        pd.Timestamp("2026-11-06T14:00:00Z"),
    )
    assert after_resume["stopElapsedStatus"] == "unknown"
    assert after_resume["stoppedElapsedHours"] is None
    assert after_resume["stoppedElapsedHours"] != prior_hours


def test_payload_preserves_legacy_duration_and_adds_elapsed():
    frame = pd.DataFrame([_stopped_row()])
    payload = vessels_to_payload(frame)[0]
    assert payload["durationHours"] == 3.0
    assert payload["stoppedElapsedHours"] == 3.0
    assert payload["stopElapsedStatus"] == "known"
    # Existing fields remain.
    for key in (
        "mmsi",
        "tsStop",
        "tsCurrent",
        "lastSeenAt",
        "durationSeconds",
        "durationHours",
        "durationLabel",
        "inRestrictedZone",
        "inWatchPolygon",
        "watchPolygonName",
        "reason",
    ):
        assert key in payload


def test_payload_unknown_elapsed_when_stale_only():
    frame = pd.DataFrame([
        _stopped_row(
            tsstop=None,
            tscurrent=pd.Timestamp("2026-11-06T03:00:00Z"),
            ts=pd.Timestamp("2026-11-06T00:00:00Z"),
        )
    ])
    payload = vessels_to_payload(frame)[0]
    # Legacy duration may still fall back to activity ts — preserved behaviour.
    assert payload["durationHours"] == 3.0
    assert payload["stopElapsedStatus"] == "unknown"
    assert payload["stoppedElapsedHours"] is None
    assert payload["stopStartedAt"] is None


def test_harbour_label_outside_point():
    get_port_area_index(POLY_DIR)
    frame = pd.DataFrame([
        _stopped_row(curlongitude=OUTSIDE_LON, curlatitude=OUTSIDE_LAT, mmsi=1)
    ])
    annotated = annotate_suspected_anchoring_harbour(frame)
    row = annotated.iloc[0]
    assert bool(row["in_port_area"]) is False
    assert row["port_area_name"] is None
    assert bool(row["at_berth"]) is False


def test_harbour_label_westport_match():
    get_port_area_index(POLY_DIR)
    frame = pd.DataFrame([
        _stopped_row(mmsi=1, curlongitude=WESTPORT_LON, curlatitude=WESTPORT_LAT)
    ])
    annotated = annotate_suspected_anchoring_harbour(frame)
    row = annotated.iloc[0]
    assert bool(row["in_port_area"]) is True
    assert "Klang" in str(row["port_area_name"])


def test_harbour_label_keeps_port_name_when_anchorage_overlaps():
    """Label path uses anchorage_wins=False so watch+port overlap still names the port."""
    get_port_area_index(POLY_DIR)
    # Synthetic: inject a tiny port covering the Klang anchorage test point.
    box = shape(
        {
            "type": "Polygon",
            "coordinates": [[
                [KLANG_ANCH_LON - 0.05, KLANG_ANCH_LAT - 0.05],
                [KLANG_ANCH_LON + 0.05, KLANG_ANCH_LAT - 0.05],
                [KLANG_ANCH_LON + 0.05, KLANG_ANCH_LAT + 0.05],
                [KLANG_ANCH_LON - 0.05, KLANG_ANCH_LAT + 0.05],
                [KLANG_ANCH_LON - 0.05, KLANG_ANCH_LAT - 0.05],
            ]],
        }
    )
    reset_port_areas(
        PortAreaIndex(
            areas=[
                PortArea(
                    port_name="Synthetic Overlap Port",
                    terminal_name="Synthetic Terminal",
                    geojson=json_dumps_geom(box),
                    area_km2=1.0,
                )
            ],
            berths=[],
            ports_loaded=1,
            terminals_loaded=1,
            berths_loaded=0,
            poly_dir=Path("/tmp"),
        )
    )
    pts = pd.DataFrame([
        {"mmsi": 1, "curlongitude": KLANG_ANCH_LON, "curlatitude": KLANG_ANCH_LAT}
    ])
    exclusion = annotate_harbour_location(
        pts, lon_col="curlongitude", lat_col="curlatitude", anchorage_wins=True
    )
    labels = annotate_harbour_location(
        pts, lon_col="curlongitude", lat_col="curlatitude", anchorage_wins=False
    )
    # Parent anchorage wins for exclusion semantics.
    assert bool(exclusion.iloc[0]["in_port_area"]) is False
    # Label path still reports the operational port name.
    assert bool(labels.iloc[0]["in_port_area"]) is True
    assert labels.iloc[0]["port_area_name"] == "Synthetic Overlap Port"
    assert labels.iloc[0]["port_area_terminal_name"] == "Synthetic Terminal"


def json_dumps_geom(geom) -> str:
    import json

    return json.dumps(mapping(geom))


def test_candidate_selection_unchanged_excl_and_port_drop():
    get_port_area_index(POLY_DIR)
    stopped = pd.DataFrame([
        {
            "mmsi": 533000002,
            "curlongitude": KLANG_ANCH_LON,
            "curlatitude": KLANG_ANCH_LAT,
            "tsstop": pd.Timestamp("2026-10-20T00:00:00Z"),
            "tscurrent": pd.Timestamp("2026-10-20T02:00:00Z"),
            "ts": pd.Timestamp("2026-10-20T00:00:00Z"),
            "sog": 0.1,
            "cog": 0.0,
            "navstatusdesc": "At anchor",
            "shiptype": 70,
            "shiptypedesc": "Cargo",
            "shipname": "ANCHORAGE TEST",
            "imo": None,
        },
    ])
    classified = classify_illegal_anchoring(stopped)
    assert 533000002 in set(classified["mmsi"].astype(int))
    kept, excluded_count, _ = drop_port_area_candidates(classified)
    assert 533000002 in set(kept["mmsi"].astype(int))
    assert excluded_count == 0

    candidates = pd.DataFrame([
        {
            "mmsi": 533000001,
            "curlongitude": WESTPORT_LON,
            "curlatitude": WESTPORT_LAT,
            "reason": "in_watch_polygon",
            "shipname": "WESTPORT TEST",
        },
        {
            "mmsi": 533000002,
            "curlongitude": KLANG_ANCH_LON,
            "curlatitude": KLANG_ANCH_LAT,
            "reason": "in_watch_polygon",
            "shipname": "ANCHORAGE TEST",
        },
    ])
    kept2, excl2, _ = drop_port_area_candidates(candidates)
    assert 533000001 not in set(kept2["mmsi"].astype(int))
    assert 533000002 in set(kept2["mmsi"].astype(int))
    assert excl2 == 1


def test_missing_geometry_index_safe():
    reset_port_areas(
        PortAreaIndex(
            areas=[],
            berths=[],
            ports_loaded=0,
            terminals_loaded=0,
            berths_loaded=0,
            poly_dir=Path("/tmp"),
        )
    )
    frame = pd.DataFrame([_stopped_row()])
    annotated = annotate_suspected_anchoring_harbour(frame)
    assert bool(annotated.iloc[0]["in_port_area"]) is False
    assert annotated.iloc[0]["berth_name"] is None


def test_invalid_coordinates_do_not_raise():
    get_port_area_index(POLY_DIR)
    frame = pd.DataFrame([
        _stopped_row(mmsi=1, curlongitude=float("nan"), curlatitude=float("nan")),
        _stopped_row(mmsi=2, curlongitude=None, curlatitude=None),
    ])
    annotated = annotate_suspected_anchoring_harbour(frame)
    assert len(annotated) == 2
    assert annotated["in_port_area"].fillna(False).astype(bool).sum() == 0


def test_provenance_flags():
    assert POLYGON_PROVENANCE["validatedLegalPortLimits"] is False
    assert POLYGON_PROVENANCE["designatedAnchorageBoundaries"] is False
    assert "operational" in POLYGON_PROVENANCE["role"]
