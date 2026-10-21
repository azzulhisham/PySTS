"""Backend label_geography clears outside_anchorage inside port areas."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
RESTAPI = ROOT / "restapi"
BACKEND = ROOT / "backend"
for path in (RESTAPI, BACKEND):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from port_areas import get_port_area_index, reset_port_areas  # noqa: E402
from vesselloiteringdetection import label_geography  # noqa: E402

WESTPORT_LON, WESTPORT_LAT = 101.29255516361354, 2.925089795917814
KLANG_ANCH_LON, KLANG_ANCH_LAT = 101.25, 2.75


@pytest.fixture(autouse=True)
def _reset():
    reset_port_areas(None)
    yield
    reset_port_areas(None)


def test_label_geography_westport_not_outside_anchorage():
    get_port_area_index(ROOT / "data" / "polygons" / "src")
    loiters = pd.DataFrame([
        {
            "mmsi": 533111001,
            "centre_longitude": WESTPORT_LON,
            "centre_latitude": WESTPORT_LAT,
        },
        {
            "mmsi": 533111002,
            "centre_longitude": KLANG_ANCH_LON,
            "centre_latitude": KLANG_ANCH_LAT,
        },
    ])
    labeled = label_geography(loiters)
    by = labeled.set_index("mmsi")
    assert bool(by.loc[533111001, "outside_anchorage"]) is False
    assert bool(by.loc[533111001, "in_port_area"]) is True
    # Anchorage centre is inside the Port Klang parent polygon.
    assert bool(by.loc[533111002, "outside_anchorage"]) is False
    assert by.loc[533111002, "anchorage_name"] is not None
