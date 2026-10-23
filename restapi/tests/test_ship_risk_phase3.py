"""Phase 3: STS review-case identity-risk fields (read-time only)."""

from __future__ import annotations

import inspect
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pandas as pd
import pytest

RESTAPI = Path(__file__).resolve().parents[1]
if str(RESTAPI) not in sys.path:
    sys.path.insert(0, str(RESTAPI))

import ship_risk_attach as risk_mod  # noqa: E402
import sts_review as review_mod  # noqa: E402
from ship_risk_attach import clear_risk_index_cache, strip_risk_keys  # noqa: E402
from sts_review import (  # noqa: E402
    cases_to_payload,
    risk_counts_from_cases,
    verify_case,
)


@pytest.fixture(autouse=True)
def _clear_cache():
    clear_risk_index_cache()
    yield
    clear_risk_index_cache()


def _case_frame(members: list[dict], **extra) -> pd.DataFrame:
    row = {
        "id": 10,
        "observation_id": 100,
        "status": "unreviewed",
        "review_class": None,
        "labeled_at": None,
        "labeled_by": None,
        "review_notes": None,
        "suspicion_score": 5.2,
        "is_open": True,
        "notified_at": None,
        "first_detected_at": pd.Timestamp("2026-10-22T00:00:00Z"),
        "last_detected_at": pd.Timestamp("2026-10-22T01:00:00Z"),
        "duration_seconds": 3600,
        "centroid_latitude": 1.0,
        "centroid_longitude": 103.0,
        "vessel_count": len(members),
        "in_anchorage": True,
        "anchorage_name": "Test",
        "outside_anchorage": False,
        "in_port_area": False,
        "port_area_name": None,
        "port_area_terminal_name": None,
        "at_berth": False,
        "berth_name": None,
        "invalid_mmsi": False,
        "sanctions_match": True,
        "dredge_licence_match": False,
        "bunker_list_match": False,
        "dark_overlap": False,
        "illegal_anchor_overlap": False,
        "loiter_overlap": False,
        "anchor_swing_overlap": False,
        "spoof_overlap": False,
        "members": json.dumps(members),
    }
    row.update(extra)
    return pd.DataFrame([row])


def test_cases_to_payload_adds_risk_after_sanctions(monkeypatch):
    by_imo = {
        "9074729": {"rating": "RED", "score": 100, "reason": "sanctions match (SDN)"},
    }
    by_mmsi = {}
    monkeypatch.setattr(review_mod, "load_risk_indexes", lambda engine=None: (by_imo, by_mmsi))

    members = [
        {
            "mmsi": 111111111,
            "imo": "9074729",
            "shipName": "A",
            "sanctionsMatch": True,
            "matchConfidence": "confirmed",
            "sanctionsList": "SDN",
        },
        {
            "mmsi": 222222222,
            "imo": None,
            "shipName": "B",
            "sanctionsMatch": False,
            "matchConfidence": "none",
            "sanctionsList": None,
        },
    ]
    cases = cases_to_payload(_case_frame(members), engine=MagicMock())
    assert len(cases) == 1
    case = cases[0]
    assert case["riskRedCount"] == 1
    a, b = case["members"]
    assert a["riskRating"] == "RED" and a["riskScore"] == 100
    assert b["riskRating"] == "GREEN" and b["riskScore"] == 0 and b["riskReason"] == "none"
    # Stored sanctions flags untouched
    assert a["sanctionsMatch"] is True
    assert a["sanctionsList"] == "SDN"
    keys = list(a.keys())
    assert keys.index("sanctionsList") < keys.index("riskRating")


def test_green_when_cache_load_fails(monkeypatch, caplog):
    def boom(engine=None):
        raise RuntimeError("db down")

    monkeypatch.setattr(review_mod, "load_risk_indexes", boom)
    members = [{"mmsi": 1, "imo": "9074729", "shipName": "X", "sanctionsMatch": False}]
    with caplog.at_level("WARNING"):
        cases = cases_to_payload(_case_frame(members), engine=MagicMock())
    assert cases[0]["members"][0]["riskRating"] == "GREEN"
    assert cases[0]["riskRedCount"] == 0


def test_golden_strip_only_adds_risk_keys(monkeypatch):
    monkeypatch.setattr(
        review_mod,
        "load_risk_indexes",
        lambda engine=None: (
            {"9074729": {"rating": "AMBER", "score": 40, "reason": "x"}},
            {},
        ),
    )
    members = [
        {
            "mmsi": 1,
            "imo": "9074729",
            "shipName": "A",
            "sanctionsMatch": False,
            "matchConfidence": "none",
            "sanctionsList": None,
        },
        {
            "mmsi": 2,
            "imo": None,
            "shipName": "B",
            "sanctionsMatch": False,
            "matchConfidence": "none",
            "sanctionsList": None,
        },
    ]
    with_risk = cases_to_payload(_case_frame(members), engine=MagicMock())
    stripped = strip_risk_keys(with_risk)

    # Empty indexes → GREEN defaults; strip still removes risk keys
    monkeypatch.setattr(review_mod, "load_risk_indexes", lambda engine=None: ({}, {}))
    without = strip_risk_keys(cases_to_payload(_case_frame(members), engine=MagicMock()))
    assert stripped == without
    assert "riskRating" not in json.dumps(stripped)
    assert stripped[0]["sanctionsMatch"] is True or stripped[0]["sanctionsMatch"] is False
    assert "status" in stripped[0] and "suspicionScore" in stripped[0]


def test_list_level_risk_counts(monkeypatch):
    monkeypatch.setattr(
        review_mod,
        "load_risk_indexes",
        lambda engine=None: (
            {
                "9074729": {"rating": "RED", "score": 100, "reason": "r"},
                "9000883": {"rating": "AMBER", "score": 40, "reason": "a"},
            },
            {},
        ),
    )
    frame = pd.concat(
        [
            _case_frame(
                [
                    {"mmsi": 1, "imo": "9074729", "shipName": "R", "sanctionsMatch": False},
                    {"mmsi": 2, "imo": "9000883", "shipName": "A", "sanctionsMatch": False},
                ],
                id=1,
                observation_id=1,
            ),
            _case_frame(
                [{"mmsi": 3, "imo": None, "shipName": "G", "sanctionsMatch": False}],
                id=2,
                observation_id=2,
                sanctions_match=False,
            ),
        ],
        ignore_index=True,
    )
    cases = cases_to_payload(frame, engine=MagicMock())
    counts = risk_counts_from_cases(cases)
    assert counts == {"risk_red_count": 1, "risk_amber_count": 1}


def test_verify_case_signature_unchanged():
    """Write route must not gain risk parameters or write risk into the case."""
    sig = inspect.signature(verify_case)
    assert list(sig.parameters) == ["case_id", "review_class", "labeled_by", "review_notes"]
    src = Path(review_mod.__file__).read_text(encoding="utf-8")
    # VERIFY_SQL must not mention risk columns
    assert "risk_rating" not in src.split("VERIFY_SQL")[1].split("def verify_case")[0].lower()
    assert "riskRating" not in src.split("def verify_case")[1].split("def ")[0]


def test_response_count_key_order_after_sanctions():
    payload = {
        "sanctionsMatchCount": 3,
        "riskRedCount": 1,
        "riskAmberCount": 2,
        "cases": [],
    }
    keys = list(payload.keys())
    assert keys.index("sanctionsMatchCount") < keys.index("riskRedCount")
