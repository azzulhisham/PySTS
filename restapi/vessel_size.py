"""AIS Class A/B static dimensions for API vessel objects.

Class A lives in ais_static; Class B in ais_staticb. Prefer Class A when
both exist. lengthM / beamM are the overall hull size from the GPS
antenna offsets (to_bow + to_stern, to_port + to_starboard).
"""

from __future__ import annotations

from typing import Any

import pandas as pd

DIM_SELECT = """
    COALESCE(s.to_bow, sb.to_bow) AS to_bow,
    COALESCE(s.to_stern, sb.to_stern) AS to_stern,
    COALESCE(s.to_port, sb.to_port) AS to_port,
    COALESCE(s.to_starboard, sb.to_starboard) AS to_starboard
"""


def class_b_join(mmsi_expr: str) -> str:
    return f"LEFT JOIN public.ais_staticb sb ON sb.mmsi = {mmsi_expr}"


def _nullable_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return float(value)


def dimension_fields(row: Any, *, side: str | None = None) -> dict[str, Any]:
    suffix = f"_{side}" if side else ""
    getter = row.get if hasattr(row, "get") else lambda _key, default=None: default
    to_bow = _nullable_float(getter(f"to_bow{suffix}"))
    to_stern = _nullable_float(getter(f"to_stern{suffix}"))
    to_port = _nullable_float(getter(f"to_port{suffix}"))
    to_starboard = _nullable_float(getter(f"to_starboard{suffix}"))
    return {
        "toBow": to_bow,
        "toStern": to_stern,
        "toPort": to_port,
        "toStarboard": to_starboard,
        "lengthM": None if to_bow is None or to_stern is None else to_bow + to_stern,
        "beamM": None if to_port is None or to_starboard is None else to_port + to_starboard,
    }
