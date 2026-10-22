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
    dedupe_vessel_list,
    load_ofac_vessel_list,
    match_vessel,
    ofac_vessels_to_payload,
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
    assert len(out) == 2
    assert set(out["list_name"]) == {"SDN", "UK"}
    uk_row = out.loc[out["list_name"] == "UK"].iloc[0]
    assert str(uk_row["imo"]) == "9562233"
    assert uk_row["list_names"] == ["UK"]


def test_dedupe_sdn_plus_uk_one_row_fills_flag():
    df = pd.DataFrame(
        [
            {
                "uid": 99,
                "vessel_name": "PETREL SDN",
                "vessel_call_sign": None,
                "vessel_type": None,
                "vessel_flag": None,
                "vessel_owner": None,
                "imo": "9562233",
                "mmsi": None,
                "programs": ["DPRK"],
                "remarks": None,
                "publish_date": "2024-01-01",
                "list_name": "SDN",
            },
            {
                "uid": "DPR0075",
                "vessel_name": "Petrel 8",
                "vessel_call_sign": None,
                "vessel_type": "Bulk Carrier",
                "vessel_flag": "Comoros",
                "vessel_owner": "Sample Owner",
                "imo": "9562233",
                "mmsi": None,
                "programs": ["DPRK regime"],
                "remarks": "UK ref 99901",
                "publish_date": "16/10/2026",
                "list_name": "UK",
            },
        ]
    )
    out = dedupe_vessel_list(df)
    assert len(out) == 1
    row = out.iloc[0]
    assert row["list_name"] == "SDN"
    assert row["list_names"] == ["SDN", "UK"]
    assert row["uid"] == 99
    assert row["vessel_flag"] == "Comoros"
    assert row["vessel_type"] == "Bulk Carrier"
    assert row["vessel_owner"] == "Sample Owner"
    payload = ofac_vessels_to_payload(out)[0]
    assert payload["listName"] == "SDN"
    assert payload["listNames"] == ["SDN", "UK"]


def test_dedupe_uk_only_ship():
    df = pd.DataFrame(
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
                "programs": None,
                "remarks": None,
                "publish_date": "16/10/2026",
                "list_name": "UK",
            }
        ]
    )
    out = dedupe_vessel_list(df)
    assert len(out) == 1
    assert out.iloc[0]["list_name"] == "UK"
    assert out.iloc[0]["list_names"] == ["UK"]


def test_dedupe_ofac_un_only_unchanged_shape():
    """Distinct OFAC/UN IMOs stay one row each with single-list membership."""
    df = pd.DataFrame(
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
            },
            {
                "uid": 2,
                "vessel_name": "AVAX",
                "vessel_call_sign": None,
                "vessel_type": None,
                "vessel_flag": None,
                "vessel_owner": None,
                "imo": "9058713",
                "mmsi": None,
                "programs": ["Libya"],
                "remarks": "UN ref LYe.006",
                "publish_date": "2026-01-15",
                "list_name": "UN",
            },
        ]
    )
    out = dedupe_vessel_list(df)
    assert len(out) == 2
    by_list = {r["list_name"]: r for _, r in out.iterrows()}
    assert by_list["SDN"]["list_names"] == ["SDN"]
    assert by_list["UN"]["list_names"] == ["UN"]
    assert str(by_list["UN"]["imo"]) == "9058713"
