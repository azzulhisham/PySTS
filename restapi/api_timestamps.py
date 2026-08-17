"""Shared AIS timestamp formatting for MANTIS API payloads."""

from __future__ import annotations

from typing import Any

import pandas as pd


def format_last_seen_at(value: Any) -> str | None:
    """
    ISO 8601 UTC string for the last AIS fix (`tscurrent` / `ais_position.ts`).

    Use as the anchor for GET /mantis/vessel-track (not pairedAt / detectedAt,
    which are pipeline wall-clock times on some endpoints).
    """
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass

    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    else:
        ts = ts.tz_convert("UTC")
    return ts.isoformat()


def last_seen_field(row: Any, *, col: str = "tscurrent") -> dict[str, str | None]:
    """Return {\"lastSeenAt\": ...} from a dataframe row or mapping."""
    getter = row.get if hasattr(row, "get") else lambda _key, default=None: default
    return {"lastSeenAt": format_last_seen_at(getter(col))}
