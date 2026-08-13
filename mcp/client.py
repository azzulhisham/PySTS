"""
HTTP helper for the MANTIS REST API.

This is normal Python — not MCP-specific.
The MCP server (server.py) calls these functions from each tool.
"""

from __future__ import annotations

import os
from typing import Any

import httpx
from dotenv import load_dotenv

# Load PySTS/mcp/.env if present
load_dotenv()

DEFAULT_BASE_URL = "http://127.0.0.1:8080"
DEFAULT_USER_ID = "user@sts.my"
DEFAULT_ACCESS_KEY = "vZOODBrmB3cc0nvMiLwXtssAnchorageuj15dNSohbDgldkW_NI"


class MantisClient:
    """Tiny client: login once, then call protected GET endpoints."""

    def __init__(
        self,
        base_url: str | None = None,
        user_id: str | None = None,
        access_key: str | None = None,
        timeout: float = 120.0,
    ) -> None:
        self.base_url = (base_url or os.getenv("MANTIS_BASE_URL", DEFAULT_BASE_URL)).rstrip("/")
        self.user_id = user_id or os.getenv("MANTIS_USER_ID", DEFAULT_USER_ID)
        self.access_key = access_key or os.getenv("MANTIS_ACCESS_KEY", DEFAULT_ACCESS_KEY)
        self.timeout = timeout
        self._token: str | None = None

    def get_token(self, force_refresh: bool = False) -> dict[str, Any]:
        """
        POST /authentication/token

        Stores the access token for later authenticated calls.
        """
        if self._token and not force_refresh:
            return {
                "ok": True,
                "message": "Using cached token",
                "accessToken": self._token[:24] + "...",
                "cached": True,
            }

        url = f"{self.base_url}/authentication/token"
        payload = {"userId": self.user_id, "accessKey": self.access_key}

        with httpx.Client(timeout=self.timeout) as client:
            response = client.post(url, json=payload)

        if response.status_code != 200:
            return {
                "ok": False,
                "statusCode": response.status_code,
                "error": _safe_json(response),
            }

        data = response.json()
        self._token = data.get("accessToken")
        return {
            "ok": True,
            "cached": False,
            "expiredDate": data.get("expiredDate"),
            # Do not return the full token to the chat unless needed —
            # tools that call the API use the in-memory cache.
            "accessTokenPreview": (self._token[:24] + "...") if self._token else None,
            "message": "Token obtained and cached inside the MCP process",
        }

    def _auth_headers(self) -> dict[str, str]:
        if not self._token:
            # Auto-login so other tools work even if get_token was not called first
            result = self.get_token(force_refresh=True)
            if not result.get("ok"):
                raise RuntimeError(f"Could not authenticate: {result}")
        return {"Authorization": f"Bearer {self._token}"}

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        with httpx.Client(timeout=self.timeout) as client:
            response = client.get(url, headers=self._auth_headers(), params=params or {})

        # If token expired, refresh once and retry
        if response.status_code == 401:
            self.get_token(force_refresh=True)
            with httpx.Client(timeout=self.timeout) as client:
                response = client.get(url, headers=self._auth_headers(), params=params or {})

        body = _safe_json(response)
        if response.status_code != 200:
            return {
                "ok": False,
                "statusCode": response.status_code,
                "path": path,
                "error": body,
            }
        return {"ok": True, "path": path, "data": body}

    def get_polygons(self) -> dict[str, Any]:
        """GET /mantis/polygons"""
        return self._get("/mantis/polygons")

    def get_sts_activities(
        self,
        min_suspicion_score: float | None = None,
        max_distance_m: float | None = None,
    ) -> dict[str, Any]:
        """GET /mantis/sts-activities"""
        params: dict[str, Any] = {}
        if min_suspicion_score is not None:
            params["minSuspicionScore"] = min_suspicion_score
        if max_distance_m is not None:
            params["maxDistanceM"] = max_distance_m
        return self._get("/mantis/sts-activities", params=params)

    def get_illegal_anchoring(self) -> dict[str, Any]:
        """GET /mantis/illegal-anchoring"""
        return self._get("/mantis/illegal-anchoring")

    def get_dark_vessels(self, include_coverage_exit: bool = True) -> dict[str, Any]:
        """GET /mantis/darkvessels"""
        return self._get(
            "/mantis/darkvessels",
            params={"includeCoverageExit": str(include_coverage_exit).lower()},
        )

    def get_sanctions_list(
        self,
        imo: str | None = None,
        mmsi: str | None = None,
    ) -> dict[str, Any]:
        """GET /mantis/sanctions — full OFAC vessel list, or search by IMO / MMSI."""
        params: dict[str, Any] = {}
        if imo:
            params["imo"] = imo
        if mmsi:
            params["mmsi"] = mmsi
        return self._get("/mantis/sanctions", params=params or None)


def _safe_json(response: httpx.Response) -> Any:
    try:
        return response.json()
    except Exception:
        return {"raw": response.text[:1000]}


# One shared client for the MCP process
client = MantisClient()
