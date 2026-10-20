"""Shared AIS identity helpers: MMSI plausibility and display ship names.

Used by spoofing, dark vessels, loitering and payload builders. No DB access.
"""

from __future__ import annotations

from typing import Any

# ITU ship-station MID range used for Class-A cargo/tanker filtering.
MID_MIN = 201
MID_MAX = 775

# 9-digit MMSI, MID 201-775, not xxx000000. Leading AND — embed after WHERE.
# Use mod()/modulo() so SQLAlchemy does not treat '%' as a bind placeholder.
VALID_SHIP_MMSI_SQL = f"""
  AND mmsi >= {MID_MIN}000000
  AND mmsi <= {MID_MAX}999999
  AND mod(mmsi, 1000000) <> 0
"""

# ClickHouse (clickhouse_connect raw queries — '%' is fine here).
VALID_SHIP_MMSI_SQL_CH = f"""
  AND mmsi >= {MID_MIN}000000
  AND mmsi <= {MID_MAX}999999
  AND (mmsi % 1000000) != 0
"""


def mid_from_mmsi(mmsi: int) -> int | None:
    """Maritime identification digits from a 9-digit MMSI (VT Explorer MID rules)."""
    try:
        mmsi9 = str(int(mmsi)).strip()
    except (TypeError, ValueError):
        return None
    if len(mmsi9) < 9:
        mmsi9 = mmsi9.zfill(9)
    elif len(mmsi9) > 9:
        mmsi9 = mmsi9[-9:]
    if len(mmsi9) != 9 or not mmsi9.isdigit():
        return None
    if mmsi9[:2] in ("00", "99", "96"):
        return int(mmsi9[2:5])
    return int(mmsi9[:3])


def is_valid_ship_mmsi(mmsi: Any) -> bool:
    """True when MMSI is 9 digits, MID in 201-775, and not ending in 000000."""
    if mmsi is None:
        return False
    try:
        if isinstance(mmsi, float) and mmsi != mmsi:  # NaN
            return False
        n = int(mmsi)
    except (TypeError, ValueError):
        return False
    text = str(n)
    if len(text) != 9:
        return False
    if text.endswith("000000"):
        return False
    mid = mid_from_mmsi(n)
    if mid is None:
        return False
    return MID_MIN <= mid <= MID_MAX


def clean_ship_name(value: Any) -> str | None:
    """Strip AIS '@' padding, trim, and collapse repeated spaces. Keeps case."""
    if value is None:
        return None
    try:
        if value != value:  # NaN
            return None
    except Exception:
        pass
    try:
        import pandas as pd

        if pd.isna(value):
            return None
    except Exception:
        pass
    # Same cleaning as identity_conflict._normalize_identity_text, without uppercasing
    # and without the minimum-length drop (payloads may still show short names).
    text = " ".join(str(value).replace("@", " ").split())
    return text or None
