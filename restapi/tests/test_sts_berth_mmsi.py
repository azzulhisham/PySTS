"""Round-2 fixes: review flag-only, STRtree harbour lookup, activities drops."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import pytest

RESTAPI = Path(__file__).resolve().parents[1]
ROOT = RESTAPI.parent
if str(RESTAPI) not in sys.path:
    sys.path.insert(0, str(RESTAPI))

from ais_identity import filter_valid_ship_mmsi_frame, is_valid_ship_mmsi  # noqa: E402
from illegal_anchoring import drop_port_area_candidates  # noqa: E402
from port_areas import (  # noqa: E402
    annotate_harbour_location,
    annotate_harbour_location_duckdb,
    get_port_area_index,
    harbour_exclude_mask,
    reset_port_areas,
)
from sts_review import (  # noqa: E402
    _annotate_and_filter_cases,
    _annotate_cases,
    case_has_invalid_mmsi,
    cases_to_payload,
    is_notifiable_case,
)

POLY_DIR = ROOT / "data" / "polygons" / "src"

WESTPORT_BERTH_LON, WESTPORT_BERTH_LAT = 101.29264, 2.93216
KLANG_ANCH_LON, KLANG_ANCH_LAT = 101.25, 2.75
# Pengerang terminal / berth area (approx from live exclusions).
PENGERANG_LON, PENGERANG_LAT = 104.18, 1.37
# Singapore South Anchorage Excl1 centroid.
EXCL_LON, EXCL_LAT = 103.75982420635715, 1.233616376985714
OPEN_SEA_LON, OPEN_SEA_LAT = 105.0, 5.0

VALID_MMSI = 533000757
INVALID_MMSIS = (0, 111111111, 199000123, 800000123)


@pytest.fixture(autouse=True)
def _reset_port_cache():
    reset_port_areas(None)
    yield
    reset_port_areas(None)


def _sts_case(
    obs_id: int,
    lon: float,
    lat: float,
    *,
    mmsi_a: int = VALID_MMSI,
    mmsi_b: int = 533001234,
    case_id: int | None = None,
) -> dict:
    return {
        "id": case_id if case_id is not None else obs_id,
        "observation_id": obs_id,
        "centroid_longitude": lon,
        "centroid_latitude": lat,
        "members": json.dumps([
            {"mmsi": mmsi_a, "shipName": "A"},
            {"mmsi": mmsi_b, "shipName": "B"},
        ]),
        "status": "unreviewed",
        "suspicion_score": 5.0,
        "outside_anchorage": True,
        "sanctions_match": False,
        "dredge_licence_match": False,
        "bunker_list_match": False,
        "dark_overlap": False,
        "illegal_anchor_overlap": False,
        "loiter_overlap": False,
        "anchor_swing_overlap": False,
        "spoof_overlap": False,
    }


class TestBerthAnnotation:
    def test_berths_loaded(self):
        index = get_port_area_index(POLY_DIR)
        assert index.berths_loaded == 1737
        assert index.trees_ready is True
        assert index.port_tree is not None
        assert index.berth_tree is not None

    def test_westport_berth_flags(self):
        get_port_area_index(POLY_DIR)
        pts = pd.DataFrame([{
            "observation_id": 1,
            "centroid_longitude": WESTPORT_BERTH_LON,
            "centroid_latitude": WESTPORT_BERTH_LAT,
        }])
        hit = annotate_harbour_location(
            pts, lon_col="centroid_longitude", lat_col="centroid_latitude"
        )
        row = hit.iloc[0]
        assert bool(row["at_berth"]) is True
        assert row["berth_name"] == "Berth No.14"
        assert bool(row["in_port_area"]) is True
        assert "Klang" in str(row["port_area_name"])

    def test_klang_anchorage_not_port_not_berth(self):
        get_port_area_index(POLY_DIR)
        pts = pd.DataFrame([{
            "observation_id": 2,
            "centroid_longitude": KLANG_ANCH_LON,
            "centroid_latitude": KLANG_ANCH_LAT,
        }])
        hit = annotate_harbour_location(
            pts, lon_col="centroid_longitude", lat_col="centroid_latitude"
        )
        row = hit.iloc[0]
        assert bool(row["in_port_area"]) is False
        assert bool(row["at_berth"]) is False


class TestStrtreeMatchesDuckdb:
    def test_fixed_sample_parity(self):
        get_port_area_index(POLY_DIR)
        pts = pd.DataFrame([
            {"id": "westport", "lon": WESTPORT_BERTH_LON, "lat": WESTPORT_BERTH_LAT},
            {"id": "klang_anch", "lon": KLANG_ANCH_LON, "lat": KLANG_ANCH_LAT},
            {"id": "pengerang", "lon": PENGERANG_LON, "lat": PENGERANG_LAT},
            {"id": "excl", "lon": EXCL_LON, "lat": EXCL_LAT},
            {"id": "open_sea", "lon": OPEN_SEA_LON, "lat": OPEN_SEA_LAT},
        ])
        tree = annotate_harbour_location(pts, lon_col="lon", lat_col="lat")
        duck = annotate_harbour_location_duckdb(pts, lon_col="lon", lat_col="lat")
        for col in ("in_port_area", "at_berth"):
            assert list(tree[col].astype(bool)) == list(duck[col].astype(bool)), col
        for col in ("port_area_name", "port_area_terminal_name", "berth_name"):
            left = [None if pd.isna(v) else v for v in tree[col].tolist()]
            right = [None if pd.isna(v) else v for v in duck[col].tolist()]
            assert left == right, col


class TestReviewFlagOnly:
    def test_page_keeps_all_rows_with_flags(self):
        get_port_area_index(POLY_DIR)
        frame = pd.DataFrame([
            _sts_case(1, WESTPORT_BERTH_LON, WESTPORT_BERTH_LAT, case_id=101),
            _sts_case(2, KLANG_ANCH_LON, KLANG_ANCH_LAT, case_id=102),
            _sts_case(3, KLANG_ANCH_LON, KLANG_ANCH_LAT, mmsi_a=0, case_id=103),
        ])
        # Preserve input order (same as SQL page order).
        annotated, stats = _annotate_cases(frame)
        assert list(annotated["id"].astype(int)) == [101, 102, 103]
        assert len(annotated) == 3
        assert stats["at_berth_count"] >= 1
        assert stats["in_port_area_count"] >= 1
        assert stats["invalid_mmsi_count"] == 1

        west = annotated.loc[annotated["id"] == 101].iloc[0]
        assert bool(west["at_berth"]) is True
        assert west["berth_name"] == "Berth No.14"
        bad = annotated.loc[annotated["id"] == 103].iloc[0]
        assert bool(bad["invalid_mmsi"]) is True

        payload = cases_to_payload(annotated)
        assert len(payload) == 3
        assert [c["caseId"] for c in payload] == [101, 102, 103]
        by_id = {c["caseId"]: c for c in payload}
        assert by_id[101]["atBerth"] is True
        assert by_id[101]["berthName"] == "Berth No.14"
        assert by_id[103]["invalidMmsi"] is True
        assert by_id[102]["invalidMmsi"] is False

    def test_notifications_still_drop_harbour_and_invalid(self):
        get_port_area_index(POLY_DIR)
        frame = pd.DataFrame([
            _sts_case(1, WESTPORT_BERTH_LON, WESTPORT_BERTH_LAT),
            _sts_case(2, KLANG_ANCH_LON, KLANG_ANCH_LAT),
            _sts_case(3, KLANG_ANCH_LON, KLANG_ANCH_LAT, mmsi_a=111111111),
        ])
        kept, stats, _ = _annotate_and_filter_cases(frame, include_in_port=False)
        ids = set(kept["observation_id"].astype(int))
        assert 1 not in ids
        assert 3 not in ids
        assert 2 in ids
        assert stats["at_berth_excluded_count"] >= 1
        assert stats["invalid_mmsi_excluded_count"] == 1

        shown, stats_in, _ = _annotate_and_filter_cases(frame, include_in_port=True)
        shown_ids = set(shown["observation_id"].astype(int))
        assert 1 in shown_ids and 2 in shown_ids
        assert 3 not in shown_ids  # invalid MMSI always dropped
        assert stats_in["invalid_mmsi_excluded_count"] == 1

    def test_notifiable_skips_port_berth_invalid(self):
        base = {
            "suspicion_score": 5.0,
            "dredge_licence_match": False,
            "sanctions_match": False,
            "outside_anchorage": True,
            "dark_overlap": False,
            "illegal_anchor_overlap": False,
            "members": [{"mmsi": VALID_MMSI}],
        }
        assert is_notifiable_case(base) is True
        assert is_notifiable_case({**base, "in_port_area": True}) is False
        assert is_notifiable_case({**base, "at_berth": True}) is False
        assert is_notifiable_case({**base, "members": [{"mmsi": 0}]}) is False


class TestActivitiesStyleDrops:
    def test_harbour_exclude_mask(self):
        frame = pd.DataFrame({
            "in_port_area": [True, False, False],
            "at_berth": [False, True, False],
        })
        assert list(harbour_exclude_mask(frame)) == [True, True, False]

    def test_invalid_mmsi_filter(self):
        for mmsi in INVALID_MMSIS:
            assert is_valid_ship_mmsi(mmsi) is False
        frame = pd.DataFrame({"mmsi": [VALID_MMSI, 0, 111111111]})
        kept, excluded = filter_valid_ship_mmsi_frame(frame, "mmsi")
        assert excluded == 2
        assert list(kept["mmsi"]) == [VALID_MMSI]

    def test_illegal_path_drops_invalid_mmsi(self):
        stopped = pd.DataFrame({
            "mmsi": [VALID_MMSI, 0, 111111111],
            "curlongitude": [KLANG_ANCH_LON] * 3,
            "curlatitude": [KLANG_ANCH_LAT] * 3,
        })
        kept, excluded = filter_valid_ship_mmsi_frame(stopped, "mmsi")
        assert excluded == 2
        annotated = kept.assign(reason="in_watch_polygon", shipname="OK")
        after_port, port_n, _ = drop_port_area_candidates(annotated)
        assert port_n == 0
        assert list(after_port["mmsi"]) == [VALID_MMSI]

    def test_case_has_invalid_mmsi(self):
        assert case_has_invalid_mmsi(_sts_case(1, 0, 0, mmsi_a=0)) is True
        assert case_has_invalid_mmsi(_sts_case(1, 0, 0, mmsi_a=VALID_MMSI)) is False
