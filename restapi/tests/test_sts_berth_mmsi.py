"""Round-2: STS berth / port exclusion and MMSI filter on remaining endpoints."""

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
    get_port_area_index,
    harbour_exclude_mask,
    reset_port_areas,
)
from sts_review import (  # noqa: E402
    _annotate_and_filter_cases,
    case_has_invalid_mmsi,
    is_notifiable_case,
)

POLY_DIR = ROOT / "data" / "polygons" / "src"

# Westport Berth No.14 centroid (inside berth + Port Klang terminal footprint).
WESTPORT_BERTH_LON, WESTPORT_BERTH_LAT = 101.29264, 2.93216
# Inside Port Klang parent anchorage (not a berth).
KLANG_ANCH_LON, KLANG_ANCH_LAT = 101.25, 2.75

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
) -> dict:
    return {
        "id": obs_id,
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
        assert len(index.berths) == 1737

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


class TestStsHarbourFilter:
    def test_westport_pair_excluded_by_default_included_with_flag(self):
        get_port_area_index(POLY_DIR)
        frame = pd.DataFrame([
            _sts_case(1, WESTPORT_BERTH_LON, WESTPORT_BERTH_LAT),
            _sts_case(2, KLANG_ANCH_LON, KLANG_ANCH_LAT),
        ])
        kept, stats, _ = _annotate_and_filter_cases(frame, include_in_port=False)
        kept_ids = set(kept["observation_id"].astype(int))
        assert 1 not in kept_ids
        assert 2 in kept_ids
        assert stats["at_berth_excluded_count"] >= 1
        assert stats["port_area_excluded_count"] >= 1
        assert stats["invalid_mmsi_excluded_count"] == 0

        shown, stats_in, _ = _annotate_and_filter_cases(frame, include_in_port=True)
        shown_ids = set(shown["observation_id"].astype(int))
        assert 1 in shown_ids and 2 in shown_ids
        west = shown.loc[shown["observation_id"] == 1].iloc[0]
        assert bool(west["at_berth"]) is True
        assert west["berth_name"] == "Berth No.14"
        assert stats_in["port_area_excluded_count"] == 0
        assert stats_in["at_berth_excluded_count"] == 0

    def test_notifiable_skips_port_and_berth(self):
        base = {
            "suspicion_score": 5.0,
            "dredge_licence_match": False,
            "sanctions_match": False,
            "outside_anchorage": True,
            "dark_overlap": False,
            "illegal_anchor_overlap": False,
        }
        assert is_notifiable_case(base) is True
        assert is_notifiable_case({**base, "in_port_area": True}) is False
        assert is_notifiable_case({**base, "at_berth": True}) is False


class TestInvalidMmsiFilter:
    def test_invalid_examples_rejected(self):
        for mmsi in INVALID_MMSIS:
            assert is_valid_ship_mmsi(mmsi) is False
        assert is_valid_ship_mmsi(VALID_MMSI) is True

    def test_frame_filter_drops_invalid(self):
        frame = pd.DataFrame({
            "mmsi": [VALID_MMSI, 0, 111111111, 199000123],
        })
        kept, excluded = filter_valid_ship_mmsi_frame(frame, "mmsi")
        assert excluded == 3
        assert list(kept["mmsi"]) == [VALID_MMSI]

    def test_sts_case_with_invalid_member_dropped(self):
        get_port_area_index(POLY_DIR)
        frame = pd.DataFrame([
            _sts_case(1, KLANG_ANCH_LON, KLANG_ANCH_LAT, mmsi_a=0),
            _sts_case(2, KLANG_ANCH_LON, KLANG_ANCH_LAT, mmsi_a=111111111),
            _sts_case(3, KLANG_ANCH_LON, KLANG_ANCH_LAT, mmsi_a=VALID_MMSI),
        ])
        assert case_has_invalid_mmsi(frame.iloc[0]) is True
        kept, stats, _ = _annotate_and_filter_cases(frame, include_in_port=False)
        assert stats["invalid_mmsi_excluded_count"] == 2
        assert set(kept["observation_id"].astype(int)) == {3}

    def test_illegal_path_drops_invalid_mmsi(self):
        """Same MMSI gate used by illegal_anchoring before classify."""
        stopped = pd.DataFrame({
            "mmsi": [VALID_MMSI, 0, 111111111, 199000123],
            "curlongitude": [KLANG_ANCH_LON] * 4,
            "curlatitude": [KLANG_ANCH_LAT] * 4,
        })
        kept, excluded = filter_valid_ship_mmsi_frame(stopped, "mmsi")
        assert excluded == 3
        assert list(kept["mmsi"]) == [VALID_MMSI]
        # Port drop still works on remaining valid rows.
        annotated = kept.assign(reason="in_watch_polygon", shipname="OK")
        after_port, port_n, _ = drop_port_area_candidates(annotated)
        assert port_n == 0
        assert list(after_port["mmsi"]) == [VALID_MMSI]

    def test_harbour_exclude_mask_vectorised(self):
        frame = pd.DataFrame({
            "in_port_area": [True, False, False],
            "at_berth": [False, True, False],
        })
        mask = harbour_exclude_mask(frame)
        assert list(mask) == [True, True, False]
