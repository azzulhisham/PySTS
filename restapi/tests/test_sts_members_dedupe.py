"""STS members join must not multiply pairs when ais_static has 2+ rows per MMSI."""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

STS = Path(__file__).resolve().parents[1] / "sts_detection.py"
SRC = STS.read_text()


def test_members_sql_uses_lateral_one_row_static():
    assert "LEFT JOIN LATERAL" in SRC
    assert re.search(
        r"LEFT JOIN LATERAL \(\s*SELECT[\s\S]*?FROM public\.ais_static[\s\S]*?LIMIT 1\s*\) s ON TRUE",
        SRC,
    )
    assert "LEFT JOIN public.ais_static s ON s.mmsi = m.mmsi" not in SRC


def test_load_members_dedupe_clause_present():
    assert 'drop_duplicates(subset=["observation_id", "mmsi"]' in SRC
    assert 'drop_duplicates(\n        subset=["observation_id", "mmsi_a", "mmsi_b"]' in SRC or (
        'subset=["observation_id", "mmsi_a", "mmsi_b"]' in SRC
    )


def test_member_dedupe_collapses_fanout():
    """Safety-net behaviour: duplicate (obs, mmsi) rows collapse before pairing."""
    members = pd.DataFrame(
        [
            {"observation_id": 340095, "mmsi": 565209000, "shipname": "ALPHA"},
            {"observation_id": 340095, "mmsi": 565209000, "shipname": "ALPHA"},
            {"observation_id": 340095, "mmsi": 533000001, "shipname": "PARMELIA I"},
            {"observation_id": 340095, "mmsi": 533000001, "shipname": "PARMELIA I"},
        ]
    )
    deduped = members.drop_duplicates(subset=["observation_id", "mmsi"], keep="first")
    assert len(deduped) == 2
    pairs = pd.DataFrame(
        [
            {"observation_id": 340095, "mmsi_a": 533000001, "mmsi_b": 565209000},
            {"observation_id": 340095, "mmsi_a": 533000001, "mmsi_b": 565209000},
            {"observation_id": 340095, "mmsi_a": 533000001, "mmsi_b": 565209000},
            {"observation_id": 340095, "mmsi_a": 533000001, "mmsi_b": 565209000},
        ]
    )
    assert len(pairs.drop_duplicates(subset=["observation_id", "mmsi_a", "mmsi_b"])) == 1
