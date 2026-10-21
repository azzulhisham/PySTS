"""Port-area exclusions for illegal anchoring and loitering."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest
from shapely.geometry import mapping, shape

from illegal_anchoring import classify_illegal_anchoring, drop_port_area_candidates
from loitering import _matches_flag_filter
from port_areas import (
    PortArea,
    PortAreaIndex,
    annotate_in_port_area,
    apply_port_area_to_outside_anchorage,
    area_km2,
    build_port_areas,
    get_port_area_index,
    port_test_geometry,
    reset_port_areas,
)

POLY_DIR = Path(__file__).resolve().parents[2] / "data" / "polygons" / "src"

# Westport Container Terminal centroid (Port Klang / Pelabuhan Klang).
WESTPORT_LON, WESTPORT_LAT = 101.29255516361354, 2.925089795917814
# Inside Port Klang anchorage polygon (parent), not a berth.
KLANG_ANCH_LON, KLANG_ANCH_LAT = 101.25, 2.75
# Singapore South Anchorage (Excl1) approximate centroid.
EXCL_LON, EXCL_LAT = 103.75982420635715, 1.233616376985714


def _box(lon: float, lat: float, size: float):
    return shape(
        {
            "type": "Polygon",
            "coordinates": [[
                [lon, lat],
                [lon + size, lat],
                [lon + size, lat + size],
                [lon, lat + size],
                [lon, lat],
            ]],
        }
    )


@pytest.fixture(autouse=True)
def _reset_port_cache():
    reset_port_areas(None)
    yield
    reset_port_areas(None)


def test_oversized_port_uses_terminal_union_synthetic():
    huge = _box(101.0, 2.5, 1.0)
    assert area_km2(huge) > 50
    terminal = _box(101.2, 2.7, 0.02)
    used = port_test_geometry(huge, [terminal])
    assert used is not None
    assert area_km2(used) < 50
    assert used.covers(terminal.centroid)
    assert port_test_geometry(huge, []) is None


def test_bintulu_oversized_skipped_without_terminals():
    index = build_port_areas(POLY_DIR)
    assert any("Bintulu" in name for name in index.ports_skipped)
    assert not any(a.port_name.startswith("Bintulu") for a in index.areas)


def test_port_klang_westport_uses_terminals():
    index = get_port_area_index(POLY_DIR)
    west_areas = [
        a for a in index.areas
        if a.port_name == "Port Klang (Pelabuhan Klang)"
    ]
    assert west_areas, "expected terminal-backed areas for oversized Port Klang"
    assert any(
        a.terminal_name and "Westport Container" in a.terminal_name
        for a in west_areas
    )


def test_westport_berth_in_port_area_klang_anchorage_not():
    get_port_area_index(POLY_DIR)
    points = pd.DataFrame([
        {"mmsi": 1, "longitude": WESTPORT_LON, "latitude": WESTPORT_LAT},
        {"mmsi": 2, "longitude": KLANG_ANCH_LON, "latitude": KLANG_ANCH_LAT},
    ])
    hit = annotate_in_port_area(points, lon_col="longitude", lat_col="latitude")
    by_mmsi = hit.set_index("mmsi")
    assert bool(by_mmsi.loc[1, "in_port_area"]) is True
    assert "Klang" in str(by_mmsi.loc[1, "port_area_name"])
    assert bool(by_mmsi.loc[2, "in_port_area"]) is False


def test_illegal_anchoring_excl_and_anchorage_still_work():
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
        {
            "mmsi": 533000003,
            "curlongitude": EXCL_LON,
            "curlatitude": EXCL_LAT,
            "tsstop": pd.Timestamp("2026-10-20T00:00:00Z"),
            "tscurrent": pd.Timestamp("2026-10-20T02:00:00Z"),
            "ts": pd.Timestamp("2026-10-20T00:00:00Z"),
            "sog": 0.1,
            "cog": 0.0,
            "navstatusdesc": "At anchor",
            "shiptype": 80,
            "shiptypedesc": "Tanker",
            "shipname": "EXCL TEST",
            "imo": None,
        },
    ])
    classified = classify_illegal_anchoring(stopped)
    mmsis = set(classified["mmsi"].astype(int))
    # Excl carve-out still excluded (unchanged).
    assert 533000003 not in mmsis
    # Klang anchorage ship still counted.
    assert 533000002 in mmsis
    kept, excluded_count, _ = drop_port_area_candidates(classified)
    assert 533000002 in set(kept["mmsi"].astype(int))
    # Anchorage wins over any overlapping port footprint.
    assert excluded_count == 0


def test_illegal_anchoring_drops_westport_berth_candidate():
    """Westport is outside the Klang watch polygon, so simulate a candidate row."""
    get_port_area_index(POLY_DIR)
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
    kept, excluded_count, examples = drop_port_area_candidates(candidates)
    kept_mmsis = set(kept["mmsi"].astype(int))
    assert 533000001 not in kept_mmsis
    assert 533000002 in kept_mmsis
    assert excluded_count == 1
    assert examples[0]["mmsi"] == 533000001
    assert "Klang" in str(examples[0]["portName"])


def test_loitering_outside_anchorage_cleared_in_port():
    get_port_area_index(POLY_DIR)
    events = pd.DataFrame([
        {
            "mmsi": 1,
            "centre_longitude": WESTPORT_LON,
            "centre_latitude": WESTPORT_LAT,
            "outside_anchorage": True,
            "near_restricted": False,
            "before_sts": False,
        },
        {
            "mmsi": 2,
            "centre_longitude": KLANG_ANCH_LON,
            "centre_latitude": KLANG_ANCH_LAT,
            "outside_anchorage": True,
            "near_restricted": False,
            "before_sts": False,
        },
    ])
    annotated = annotate_in_port_area(
        events, lon_col="centre_longitude", lat_col="centre_latitude"
    )
    cleared = apply_port_area_to_outside_anchorage(annotated)
    by = cleared.set_index("mmsi")
    assert bool(by.loc[1, "in_port_area"]) is True
    assert bool(by.loc[1, "outside_anchorage"]) is False
    assert bool(by.loc[2, "in_port_area"]) is False
    # Anchorage point is not outside_anchorage after annotate? It may still be
    # True in this synthetic frame (no anchorage join on the flag itself).
    # Port exclusion must not clear it.
    assert bool(by.loc[2, "outside_anchorage"]) is True
    assert _matches_flag_filter(
        by.loc[1],
        outside_anchorage=None,
        near_restricted=None,
        before_sts=None,
        pattern=None,
    ) is False
    assert _matches_flag_filter(
        by.loc[2],
        outside_anchorage=None,
        near_restricted=None,
        before_sts=None,
        pattern=None,
    ) is True


def test_inject_synthetic_index_for_union_rule():
    terminal = _box(110.0, 1.0, 0.02)
    areas = [
        PortArea(
            port_name="Synthetic Huge Port",
            terminal_name="Synthetic Terminal",
            geojson=json.dumps(mapping(terminal)),
            area_km2=area_km2(terminal),
        )
    ]
    reset_port_areas(
        PortAreaIndex(
            areas=areas,
            ports_loaded=1,
            terminals_loaded=1,
            ports_skipped=["Synthetic Huge Port"],
            poly_dir=Path("/tmp"),
        )
    )
    pt = pd.DataFrame([{"mmsi": 9, "longitude": 110.01, "latitude": 1.01}])
    hit = annotate_in_port_area(pt, lon_col="longitude", lat_col="latitude")
    assert bool(hit.iloc[0]["in_port_area"]) is True
    assert hit.iloc[0]["port_area_name"] == "Synthetic Huge Port"
