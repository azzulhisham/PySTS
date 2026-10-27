"""Unit tests for suspected bunkering scoring and auth."""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import jwt
import pytest

RESTAPI = Path(__file__).resolve().parents[1]
if str(RESTAPI) not in sys.path:
    sys.path.insert(0, str(RESTAPI))

from suspected_bunkering import (  # noqa: E402
    BAND_HIGH_MIN,
    SCORE_CAP,
    clear_caches,
    episode_at_night,
    lookup_licensed_status,
    score_episode,
)


def test_score_possible_bunker_and_hub_medium():
    score, band, reasons = score_episode(
        possible_bunker=True,
        bunker_hub=True,
        outside_area=False,
        at_night=False,
        short_duration=False,
        ais_anomaly=False,
        loitering=False,
        identity_risk=False,
        sanctions=False,
    )
    assert score == 4.0
    assert band == "MEDIUM"
    assert reasons == ["possible_bunker", "bunker_hub"]


def test_score_high_with_many_signals():
    score, band, reasons = score_episode(
        possible_bunker=True,
        bunker_hub=True,
        outside_area=True,
        at_night=True,
        short_duration=True,
        ais_anomaly=True,
        loitering=True,
        identity_risk=True,
        sanctions=True,
    )
    assert score == SCORE_CAP
    assert band == "HIGH"
    assert score >= BAND_HIGH_MIN
    assert "sanctions" in reasons
    assert "outside_known_area" in reasons


def test_score_low_base_only_false():
    score, band, reasons = score_episode(
        possible_bunker=False,
        bunker_hub=False,
        outside_area=False,
        at_night=False,
        short_duration=False,
        ais_anomaly=False,
        loitering=False,
        identity_risk=False,
        sanctions=False,
    )
    assert score == 0.0
    assert band == "LOW"
    assert reasons == []


def test_night_local_malaysia():
    # 12:00 UTC = 20:00 MYT → night
    assert episode_at_night(
        datetime(2026, 10, 27, 12, 0, tzinfo=timezone.utc),
        datetime(2026, 10, 27, 12, 30, tzinfo=timezone.utc),
    )
    # 02:00 UTC = 10:00 MYT → day
    assert not episode_at_night(
        datetime(2026, 10, 27, 2, 0, tzinfo=timezone.utc),
        datetime(2026, 10, 27, 3, 0, tzinfo=timezone.utc),
    )


def test_licensed_placeholder():
    assert lookup_licensed_status(1, 2) == "unknown"


def test_endpoint_401_and_200():
    pytest.importorskip("flask")
    pytest.importorskip("flask_cors")
    clear_caches()
    # Import app late so pure scoring tests stay lightweight.
    from main import ACCESS_KEY, JWT_SECRET, USER_ID, app  # noqa: WPS433

    client = app.test_client()
    r401 = client.get("/mantis/suspected-bunkering")
    assert r401.status_code == 401

    token = jwt.encode(
        {
            "name": USER_ID,
            "id": USER_ID,
            "tenant": "sts",
            "role": "sts-usr",
            "expiredDate": "2099-01-01 00:00:00",
        },
        JWT_SECRET,
        algorithm="HS256",
    )
    if isinstance(token, bytes):
        token = token.decode("utf-8")

    fake = {
        "ruleVersion": "suspected-bunker-1.0",
        "hours": 24,
        "episodeCount": 0,
        "byBand": {"HIGH": 0, "MEDIUM": 0, "LOW": 0},
        "case_count": 0,
        "page": 1,
        "page_size": 100,
        "total_pages": 0,
        "returned_count": 0,
        "episodes": [],
        "queryMs": 1.0,
        "pnavQueryMs": {},
        "skippedSignals": [],
        "cacheHit": False,
    }
    with patch("main.detect_suspected_bunkering", return_value=fake):
        r200 = client.get(
            "/mantis/suspected-bunkering",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert r200.status_code == 200
    body = r200.get_json()
    assert "episodes" in body
    assert body["ruleVersion"] == "suspected-bunker-1.0"
    _ = ACCESS_KEY  # referenced for clarity that auth env is the live default
