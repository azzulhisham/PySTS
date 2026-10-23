"""Phase 5: vessel-timeline top-level risk + vessel-track X-Risk-* headers."""

from __future__ import annotations

import inspect
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock
from urllib.parse import unquote

import pandas as pd
import pytest

RESTAPI = Path(__file__).resolve().parents[1]
if str(RESTAPI) not in sys.path:
    sys.path.insert(0, str(RESTAPI))

import ship_risk_attach as risk_mod  # noqa: E402
import timelineplayback as tp  # noqa: E402
from ship_risk_attach import (  # noqa: E402
    clear_risk_index_cache,
    encode_risk_reason_header,
    risk_http_headers,
    risk_payload_for_mmsi,
    strip_risk_keys,
)


@pytest.fixture(autouse=True)
def _clear_cache():
    clear_risk_index_cache()
    yield
    clear_risk_index_cache()


def test_timeline_adds_top_level_risk_not_on_events(monkeypatch):
    monkeypatch.setattr(
        tp,
        "load_vessel_activity_timeline",
        lambda *a, **k: pd.DataFrame(
            [
                {
                    "event_ts": pd.Timestamp("2026-06-10T01:00:00Z"),
                    "event_type": "stop",
                    "mmsi": 533000123,
                    "title": "stop",
                    "details": {},
                }
            ]
        ),
    )
    monkeypatch.setattr(
        tp,
        "timeline_to_payload",
        lambda df: [
            {
                "eventType": "stop",
                "ts": "2026-06-10T01:00:00+00:00",
                "mmsi": 533000123,
                "title": "stop",
            }
        ],
    )
    monkeypatch.setattr(
        risk_mod,
        "lookup_imo_for_mmsi",
        lambda mmsi, engine=None: "9074729",
    )
    monkeypatch.setattr(
        risk_mod,
        "load_risk_indexes",
        lambda engine=None: (
            {"9074729": {"rating": "RED", "score": 100, "reason": "sanctions match (SDN)"}},
            {},
        ),
    )
    payload = tp.get_vessel_activity_timeline(
        533000123,
        "2026-06-10T00:00:00Z",
        "2026-06-12T00:00:00Z",
        engine=MagicMock(),
    )
    assert payload["riskRating"] == "RED"
    assert payload["riskScore"] == 100
    assert payload["riskReason"] == "sanctions match (SDN)"
    assert payload["mmsi"] == 533000123
    assert payload["eventCount"] == 1
    assert "riskRating" not in payload["events"][0]
    keys = list(payload.keys())
    assert keys.index("byEventType") < keys.index("riskRating")
    assert keys.index("riskRating") < keys.index("events")


def test_timeline_green_when_risk_load_fails(monkeypatch):
    monkeypatch.setattr(
        tp,
        "load_vessel_activity_timeline",
        lambda *a, **k: pd.DataFrame(),
    )
    monkeypatch.setattr(tp, "timeline_to_payload", lambda df: [])
    monkeypatch.setattr(
        risk_mod,
        "load_risk_indexes",
        lambda engine=None: (_ for _ in ()).throw(RuntimeError("db down")),
    )
    payload = tp.get_vessel_activity_timeline(
        1,
        "2026-06-10T00:00:00Z",
        "2026-06-11T00:00:00Z",
        engine=MagicMock(),
    )
    assert payload["riskRating"] == "GREEN"
    assert payload["riskScore"] == 0
    assert payload["riskReason"] == "none"
    assert payload["eventCount"] == 0


def test_timeline_golden_strip_preserves_existing(monkeypatch):
    monkeypatch.setattr(
        tp,
        "load_vessel_activity_timeline",
        lambda *a, **k: pd.DataFrame(),
    )
    monkeypatch.setattr(
        tp,
        "timeline_to_payload",
        lambda df: [{"eventType": "zone", "ts": "2026-01-01T00:00:00+00:00"}],
    )
    monkeypatch.setattr(risk_mod, "lookup_imo_for_mmsi", lambda *a, **k: None)
    monkeypatch.setattr(
        risk_mod,
        "load_risk_indexes",
        lambda engine=None: ({}, {"999": {"rating": "AMBER", "score": 40, "reason": "x"}}),
    )
    # normalize_mmsi for 999999999 etc — use mmsi that matches index after normalize
    monkeypatch.setattr(
        risk_mod,
        "match_risk",
        lambda imo, mmsi, by_imo, by_mmsi: {"rating": "AMBER", "score": 40, "reason": "x"},
    )
    with_risk = tp.get_vessel_activity_timeline(
        533000123,
        "2026-06-10T00:00:00Z",
        "2026-06-11T00:00:00Z",
        engine=MagicMock(),
    )
    stripped = strip_risk_keys(with_risk)
    assert "riskRating" not in json.dumps(stripped)
    assert stripped["mmsi"] == with_risk["mmsi"]
    assert stripped["eventCount"] == with_risk["eventCount"]
    assert stripped["events"] == with_risk["events"]
    assert stripped["byEventType"] == with_risk["byEventType"]


def test_track_headers_green_on_failure(monkeypatch):
    monkeypatch.setattr(
        risk_mod,
        "load_risk_indexes",
        lambda engine=None: (_ for _ in ()).throw(RuntimeError("down")),
    )
    headers = risk_http_headers(533000123, engine=MagicMock())
    assert headers["X-Risk-Rating"] == "GREEN"
    assert headers["X-Risk-Score"] == "0"
    assert unquote(headers["X-Risk-Reason"]) == "none"
    assert "X-Risk-Rating" in headers["Access-Control-Expose-Headers"]


def test_track_reason_header_encoded_and_capped():
    long = "sanctions match (SDN); " + ("x" * 500)
    enc = encode_risk_reason_header(long)
    assert all(ord(c) < 128 for c in enc)
    assert "%" in enc or enc.isascii()
    decoded = unquote(enc)
    assert len(decoded) <= risk_mod.RISK_REASON_HEADER_MAX_LEN
    assert decoded.endswith("...")


def test_track_stream_body_unchanged_source():
    """iter_vessel_track_ndjson must not mention risk (body lines stay point/meta only)."""
    src = inspect.getsource(tp.iter_vessel_track_ndjson)
    assert "risk" not in src.lower()
    assert "X-Risk" not in src


def test_track_headers_match_payload(monkeypatch):
    monkeypatch.setattr(risk_mod, "lookup_imo_for_mmsi", lambda *a, **k: "9074729")
    monkeypatch.setattr(
        risk_mod,
        "load_risk_indexes",
        lambda engine=None: (
            {"9074729": {"rating": "RED", "score": 100, "reason": "sanctions match (SDN)"}},
            {},
        ),
    )
    payload = risk_payload_for_mmsi(1, engine=MagicMock())
    headers = risk_http_headers(1, engine=MagicMock())
    assert headers["X-Risk-Rating"] == payload["riskRating"]
    assert headers["X-Risk-Score"] == str(payload["riskScore"])
    assert unquote(headers["X-Risk-Reason"]) == payload["riskReason"]


def test_stream_fixture_byte_identical_with_headers_layer(monkeypatch):
    """
    Fixed NDJSON fixture: applying risk only via headers must leave body bytes equal.
    Simulates main.py pattern (headers separate from generator).
    """
    monkeypatch.setattr(risk_mod, "lookup_imo_for_mmsi", lambda *a, **k: None)
    monkeypatch.setattr(risk_mod, "load_risk_indexes", lambda engine=None: ({}, {}))
    fixture = (
        b'{"type":"meta","mmsi":1}\n'
        b'{"type":"chunk","chunkIndex":0,"pointCount":1,"points":[{"ts":"2026-01-01T00:00:00+00:00","mmsi":1}]}\n'
        b'{"type":"done","pointCount":1,"chunkCount":1}\n'
    )

    def generate():
        yield fixture.decode()

    body_before = "".join(generate()).encode()
    headers = {
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
        **risk_http_headers(1, engine=MagicMock()),
    }
    body_after = "".join(generate()).encode()
    assert body_before == body_after == fixture
    assert "X-Risk-Rating" in headers
    assert b"riskRating" not in body_after
