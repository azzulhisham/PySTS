"""UN sanctions index merge + graceful missing un_vessel."""

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
    attach_sanctions,
    clear_ofac_index_cache,
    load_ofac_vessel_list,
    match_vessel,
    payload_fields,
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


def test_merge_sdn_wins_primary_keeps_all_lists():
    by_imo: dict = {}
    _merge_index_hit(by_imo, "9058713", {"list_name": "UN", "uid": 1, "vessel_name": "AVAX"})
    _merge_index_hit(by_imo, "9058713", {"list_name": "SDN", "uid": 99, "vessel_name": "AVAX SDN"})
    hit = by_imo["9058713"]
    assert hit["list_name"] == "SDN"
    assert hit["list_names"] == ["UN", "SDN"]
    assert hit["uid"] == 99

    matched = match_vessel("9058713", None, by_imo, {})
    assert matched["sanctions_match"] is True
    assert matched["sanctions_list"] == "SDN"
    assert matched["list_names"] == ["UN", "SDN"]


def test_match_un_only_and_sdn_only():
    by_imo = {
        "1111111": {
            "list_name": "UN",
            "list_names": ["UN"],
            "uid": 1,
            "vessel_name": "U",
        },
        "2222222": {
            "list_name": "SDN",
            "list_names": ["SDN"],
            "uid": 2,
            "vessel_name": "S",
        },
    }
    # Force valid-looking keys without real check digits for unit index tests.
    un = match_vessel("1111111", None, by_imo, {})
    assert un["sanctions_list"] == "UN"
    assert un["list_names"] == ["UN"]
    sdn = match_vessel("2222222", None, by_imo, {})
    assert sdn["sanctions_list"] == "SDN"
    assert sdn["list_names"] == ["SDN"]


def test_build_indexes_merges_sources(monkeypatch):
    frames = {
        "ofac_cons_vessel": _frame([], "CONS"),
        "un_vessel": _frame(
            [{"uid": 1, "vessel_name": "AVAX", "imo": "9058713", "mmsi": None}],
            "UN",
        ),
        "uk_vessel": _frame([], "UK"),
        "ofac_sdn_vessel": _frame(
            [{"uid": 9, "vessel_name": "AVAX SDN", "imo": "9058713", "mmsi": None}],
            "SDN",
        ),
    }

    def fake_load(engine, view_name, list_name):
        return frames[view_name]

    monkeypatch.setattr(sanctions_mod, "_load_view", fake_load)
    by_imo, by_mmsi = _build_ofac_indexes(MagicMock())
    assert by_imo["9058713"]["list_name"] == "SDN"
    assert by_imo["9058713"]["list_names"] == ["UN", "SDN"]
    assert by_mmsi == {}


def test_api_unchanged_when_un_vessel_missing(monkeypatch, caplog):
    """Missing un_vessel: warn like OFAC, SDN still labels; no crash."""

    def view_exists(engine, view_name):
        return view_name != "un_vessel"

    real_load_view = sanctions_mod._load_view

    def load_view(engine, view_name, list_name):
        if view_name == "un_vessel":
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
    assert any("un_vessel" in r.message for r in caplog.records)
    assert by_imo["9074729"]["list_name"] == "SDN"
    assert by_imo["9074729"]["list_names"] == ["SDN"]

    df = attach_sanctions(pd.DataFrame([{"imo": "9074729", "mmsi": None}]), MagicMock())
    assert bool(df.iloc[0]["sanctions_match"]) is True
    assert df.iloc[0]["sanctions_list"] == "SDN"
    payload = payload_fields(df.iloc[0])
    assert payload["sanctionsList"] == "SDN"
    assert payload["sanctionsMatch"] is True


def test_load_ofac_vessel_list_includes_un(monkeypatch):
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
    un = pd.DataFrame(
        [
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
            }
        ]
    )

    monkeypatch.setattr(
        sanctions_mod,
        "_load_ofac_vessel_table",
        lambda engine, table, list_name: ofac if list_name == "SDN" else pd.DataFrame(),
    )
    monkeypatch.setattr(sanctions_mod, "_load_un_vessel_table", lambda engine: un)
    monkeypatch.setattr(sanctions_mod, "_load_uk_vessel_table", lambda engine: pd.DataFrame())

    out = load_ofac_vessel_list(MagicMock())
    assert set(out["list_name"]) == {"SDN", "UN"}
    assert "9058713" in set(out["imo"].map(str))
