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


# Tie-break for choosing the one current static row per MMSI. The ingest job keys
# on IMO when the transponder sends a usable one and on MMSI otherwise, so a
# vessel can hold more than one row. Newest-first alone can land on an empty
# Message 5 shell, which reports a blank name and shipType 0 — enough to drop the
# vessel from a shipType filter entirely. Score the populated row ahead of it.
# Applies to ais_static and ais_staticb alike (same columns, same duplication).
STATIC_ROW_PREFERENCE = """
    CASE WHEN NULLIF(NULLIF(TRIM(REPLACE("shipName", '@', '')), ''), '0')
              IS NOT NULL THEN 4 ELSE 0 END
  + CASE WHEN NULLIF(NULLIF(TRIM(REPLACE(callsign, '@', '')), ''), '0')
              IS NOT NULL THEN 2 ELSE 0 END
  + CASE WHEN COALESCE(to_bow, 0) + COALESCE(to_stern, 0)
              + COALESCE(to_port, 0) + COALESCE(to_starboard, 0) > 0
         THEN 1 ELSE 0 END
"""

CURRENT_STATIC_ROW_ORDER = f"({STATIC_ROW_PREFERENCE}) DESC, ts DESC NULLS LAST, id DESC"


def class_a_join(mmsi_expr: str, how: str = "INNER") -> str:
    """JOIN the one current Class-A row per MMSI, aliased s.

    ais_static holds more than one row for ~930 MMSIs, so joining it directly on
    mmsi multiplies the caller's rows.
    """
    return (
        f"{how} JOIN (\n"
        "    SELECT DISTINCT ON (mmsi) *\n"
        "    FROM public.ais_static\n"
        f"    ORDER BY mmsi, {CURRENT_STATIC_ROW_ORDER}\n"
        f") s ON s.mmsi = {mmsi_expr}"
    )


def class_b_join(mmsi_expr: str) -> str:
    """LEFT JOIN the one current Class-B row per MMSI, aliased sb.

    ais_staticb holds two rows for some MMSIs, so joining it directly on mmsi
    multiplies the caller's rows.
    """
    return (
        "LEFT JOIN (\n"
        "    SELECT DISTINCT ON (mmsi) *\n"
        "    FROM public.ais_staticb\n"
        f"    ORDER BY mmsi, {CURRENT_STATIC_ROW_ORDER}\n"
        f") sb ON sb.mmsi = {mmsi_expr}"
    )


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
