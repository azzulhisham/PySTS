"""UK sanctions index merge + graceful missing uk_vessel."""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pandas as pd
import pytest

RESTAPI = Path(__file__).resolve().parents[1]
if str(RESTAPI) not in sys.path:
    sys.path.insert(0, str(RESTAPI))

import sanctions as sanctions_mod  # noqa: E402
from sanctions import (  # noqa: E402
    _build_ofac_indexes,
    _merge_index_hit,
    clear_ofac_index_cache,
    load_ofac_vessel_list,
    match_vessel,
)


@pytest.fixture(autouse=True)
def _clear_cache():
    clear_ofac_index_cache()
    yield
    clear_ofac_index_cache()


def _frame(rows: list[dict], list_name: str) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df["list_name"] = list_name
    return df


def test_uk_only_ship_primary_uk():
    by_imo: dict = {}
    _merge_index_hit(
        by_imo,
        "9562233",
        {"list_name": "UK", "uid": "DPR0075", "vessel_name": "Petrel 8"},
    )
    hit = by_imo["9562233"]
    assert hit["list_name"] == "UK"
    assert hit["list_names"] == ["UK"]
    matched = match_vessel("9562233", None, by_imo, {})
    assert matched["sanctions_match"] is True
    assert matched["sanctions_list"] == "UK"
    assert matched["list_names"] == ["UK"]


def test_sdn_plus_uk_keeps_sdn_primary():
    by_imo: dict = {}
    _merge_index_hit(
        by_imo,
        "9562233",
        {"list_name": "UK", "uid": "DPR0075", "vessel_name": "Petrel 8"},
    )
    _merge_index_hit(
        by_imo,
        "9562233",
        {"list_name": "SDN", "uid": 99, "vessel_name": "PETREL SDN"},
    )
    hit = by_imo["9562233"]
    assert hit["list_name"] == "SDN"
    assert hit["list_names"] == ["UK", "SDN"]
    assert hit["uid"] == 99
    matched = match_vessel("9562233", None, by_imo, {})
    assert matched["sanctions_list"] == "SDN"
    assert matched["list_names"] == ["UK", "SDN"]


def test_build_indexes_includes_uk(monkeypatch):
    frames = {
        "ofac_cons_vessel": _frame([], "CONS"),
        "un_vessel": _frame([], "UN"),
        "uk_vessel": _frame(
            [{"uid": "DPR0075", "vessel_name": "Petrel 8", "imo": "9562233", "mmsi": None}],
            "UK",
        ),
        "ofac_sdn_vessel": _frame(
            [{"uid": 9, "vessel_name": "PETREL SDN", "imo": "9562233", "mmsi": None}],
            "SDN",
        ),
    }

    def fake_load(engine, view_name, list_name):
        return frames[view_name]

    monkeypatch.setattr(sanctions_mod, "_load_view", fake_load)
    by_imo, by_mmsi = _build_ofac_indexes(MagicMock())
    assert by_imo["9562233"]["list_name"] == "SDN"
    assert by_imo["9562233"]["list_names"] == ["UK", "SDN"]
    assert by_mmsi == {}


def test_api_warns_when_uk_vessel_missing(monkeypatch, caplog):
    def view_exists(engine, view_name):
        return view_name != "uk_vessel"

    real_load_view = sanctions_mod._load_view

    def load_view(engine, view_name, list_name):
        if view_name == "uk_vessel":
            return real_load_view(engine, view_name, list_name)
        if view_name == "ofac_sdn_vessel":
            return _frame(
                [{"uid": 9, "vessel_name": "SHIP", "imo": "9074729", "mmsi": None}],
                "SDN",
            )
        return pd.DataFrame()

    monkeypatch.setattr(sanctions_mod, "_view_exists", view_exists)
    monkeypatch.setattr(sanctions_mod, "_load_view", load_view)

    with caplog.at_level(logging.WARNING):
        by_imo, _ = _build_ofac_indexes(MagicMock())
    assert any("uk_vessel" in r.message for r in caplog.records)
    assert by_imo["9074729"]["list_name"] == "SDN"
    assert by_imo["9074729"]["list_names"] == ["SDN"]


def test_load_ofac_vessel_list_includes_uk(monkeypatch):
    ofac = pd.DataFrame(
        [
            {
                "uid": 1,
                "vessel_name": "OFAC SHIP",
                "vessel_call_sign": None,
                "vessel_type": None,
                "vessel_flag": None,
                "vessel_owner": None,
                "imo": "9074729",
                "mmsi": None,
                "programs": ["SDNTK"],
                "remarks": None,
                "publish_date": "2024-01-01",
                "list_name": "SDN",
            }
        ]
    )
    uk = pd.DataFrame(
        [
            {
                "uid": "DPR0075",
                "vessel_name": "Petrel 8",
                "vessel_call_sign": None,
                "vessel_type": "Bulk Carrier",
                "vessel_flag": "Comoros",
                "vessel_owner": "Sample Owner",
                "imo": "9562233",
                "mmsi": None,
                "programs": ["DPRK"],
                "remarks": "UK ref 99901",
                "publish_date": "16/10/2026",
                "list_name": "UK",
            }
        ]
    )

    monkeypatch.setattr(
        sanctions_mod,
        "_load_ofac_vessel_table",
        lambda engine, table, list_name: ofac if list_name == "SDN" else pd.DataFrame(),
    )
    monkeypatch.setattr(sanctions_mod, "_load_un_vessel_table", lambda engine: pd.DataFrame())
    monkeypatch.setattr(sanctions_mod, "_load_uk_vessel_table", lambda engine: uk)

    out = load_ofac_vessel_list(MagicMock())
    assert set(out["list_name"]) == {"SDN", "UK"}
    assert "9562233" in set(out["imo"].map(str))
