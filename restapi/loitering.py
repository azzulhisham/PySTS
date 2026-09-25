"""
Loitering events written by backend/vesselloiteringdetection.py.

GET /mantis/loitering reads Postgres only. ClickHouse is not touched here.
Default list is open events that are outside an anchorage, near the restricted
limit, or just before an STS observation.
"""

from __future__ import annotations

import logging
from typing import Any

import pandas as pd
from sqlalchemy.engine import Engine

from api_timestamps import format_last_seen_at
from pg_engine import get_pg_engine
from sanctions import attach_sanctions, payload_fields, sort_listed_first
from vessel_size import DIM_SELECT, class_a_join, class_b_join, dimension_fields

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

RULE_VERSION = "v1.1-loiter-circle-6nm"
SHIP_TYPE_FILTER = "70-89 (cargo/tanker/container Class-A large vessels)"
NM_M = 1852.0
HULL_RADIUS_M = (
    "GREATEST(COALESCE(COALESCE(s.to_bow, sb.to_bow), 0)"
    " + COALESCE(COALESCE(s.to_stern, sb.to_stern), 0), 300)"
)

# Storage keeps net/path <= 0.20 and radius <= 6 NM. These presets narrow that set.
PRESETS = {
    "loitering": {
        "min_path_m": 2 * NM_M,
        "max_path_m": None,
        "max_net_m": 0.5 * NM_M,
        "max_net_over_path": 0.10,
        "min_radius_m": None,
        "min_radius_hull": True,
        "max_radius_m": 6 * NM_M,
        "max_radius_hull": False,
    },
    "anchorSwing": {
        "min_path_m": None,
        "max_path_m": 1 * NM_M,
        "max_net_m": None,
        "max_net_over_path": 0.20,
        "min_radius_m": 100.0,
        "min_radius_hull": False,
        "max_radius_m": None,
        "max_radius_hull": True,
    },
}

LOITER_SQL = f"""
SELECT
    l.id AS loiter_id,
    l.mmsi,
    l.place_key,
    l.started_at,
    l.ended_at,
    l.centre_latitude,
    l.centre_longitude,
    l.radius_m,
    l.path_m,
    l.net_m,
    l.sample_count,
    l.outside_anchorage,
    l.near_restricted,
    l.before_sts,
    l.anchorage_name,
    l.restricted_distance_m,
    l.sts_observation_id,
    l.tsout,
    l.detection_version,
    s."shipName" AS shipname,
    s."shipType" AS shiptype,
    s."shipTypeDesc" AS shiptypedesc,
    s."imo" AS imo,
{DIM_SELECT}
FROM public.ais_vesselloiteractivity l
{class_a_join("l.mmsi")}
{class_b_join("l.mmsi")}
WHERE l.tsout IS NULL
  AND ({{flag_filter}})
  AND ({{geo_filter}})
"""


def _fmt_ts(value: Any) -> str | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return format_last_seen_at(value) or str(value)


def _flags(row) -> list[str]:
    flags = []
    if bool(row.get("outside_anchorage")):
        flags.append("outside_anchorage")
    if bool(row.get("near_restricted")):
        flags.append("near_restricted")
    if bool(row.get("before_sts")):
        flags.append("before_sts")
    return flags


def events_to_payload(events: pd.DataFrame) -> list[dict[str, Any]]:
    records = []
    if events.empty:
        return records
    for _, row in events.iterrows():
        anchorage = row.get("anchorage_name")
        if anchorage is not None and pd.isna(anchorage):
            anchorage = None
        records.append({
            "loiterId": int(row["loiter_id"]),
            "mmsi": int(row["mmsi"]),
            "shipName": row.get("shipname") if pd.notna(row.get("shipname")) else None,
            "shipType": int(row["shiptype"]) if pd.notna(row.get("shiptype")) else None,
            "shipTypeDesc": row.get("shiptypedesc") if pd.notna(row.get("shiptypedesc")) else None,
            "latitude": float(row["centre_latitude"]),
            "longitude": float(row["centre_longitude"]),
            "startedAt": _fmt_ts(row.get("started_at")),
            "endedAt": _fmt_ts(row.get("ended_at")),
            "lastSeenAt": _fmt_ts(row.get("ended_at")),
            "radiusM": round(float(row["radius_m"]), 1) if pd.notna(row.get("radius_m")) else None,
            "pathM": round(float(row["path_m"]), 1) if pd.notna(row.get("path_m")) else None,
            "netM": round(float(row["net_m"]), 1) if pd.notna(row.get("net_m")) else None,
            "netOverPath": _net_over_path(row.get("net_m"), row.get("path_m")),
            "sampleCount": int(row["sample_count"]) if pd.notna(row.get("sample_count")) else None,
            "outsideAnchorage": bool(row.get("outside_anchorage")),
            "nearRestricted": bool(row.get("near_restricted")),
            "beforeSts": bool(row.get("before_sts")),
            "flags": _flags(row),
            "anchorageName": anchorage,
            "restrictedDistanceM": (
                float(row["restricted_distance_m"])
                if pd.notna(row.get("restricted_distance_m")) else None
            ),
            "stsObservationId": (
                int(row["sts_observation_id"])
                if pd.notna(row.get("sts_observation_id")) else None
            ),
            **dimension_fields(row),
            **payload_fields(row),
        })
    return records


def _net_over_path(net_m: Any, path_m: Any) -> float | None:
    try:
        if net_m is None or path_m is None or pd.isna(net_m) or pd.isna(path_m):
            return None
        path = float(path_m)
        if path <= 0:
            return None
        return round(float(net_m) / path, 4)
    except (TypeError, ValueError):
        return None


def _resolve_filters(
    pattern: str | None,
    *,
    min_path_m: float | None,
    max_path_m: float | None,
    max_net_m: float | None,
    max_net_over_path: float | None,
    min_radius_m: float | None,
    max_radius_m: float | None,
) -> dict[str, Any]:
    chosen = dict(PRESETS.get(pattern) or {
        "min_path_m": None,
        "max_path_m": None,
        "max_net_m": None,
        "max_net_over_path": None,
        "min_radius_m": None,
        "min_radius_hull": False,
        "max_radius_m": None,
        "max_radius_hull": False,
    })
    # An explicit number replaces the preset, including the hull-relative radius.
    if min_path_m is not None:
        chosen["min_path_m"] = min_path_m
    if max_path_m is not None:
        chosen["max_path_m"] = max_path_m
    if max_net_m is not None:
        chosen["max_net_m"] = max_net_m
    if max_net_over_path is not None:
        chosen["max_net_over_path"] = max_net_over_path
    if min_radius_m is not None:
        chosen["min_radius_m"] = min_radius_m
        chosen["min_radius_hull"] = False
    if max_radius_m is not None:
        chosen["max_radius_m"] = max_radius_m
        chosen["max_radius_hull"] = False
    chosen["pattern"] = pattern
    return chosen


def _geo_filter(chosen: dict[str, Any]) -> tuple[str, dict[str, float]]:
    clauses: list[str] = []
    params: dict[str, float] = {}
    if chosen.get("min_path_m") is not None:
        clauses.append("l.path_m >= %(min_path_m)s")
        params["min_path_m"] = float(chosen["min_path_m"])
    if chosen.get("max_path_m") is not None:
        clauses.append("l.path_m <= %(max_path_m)s")
        params["max_path_m"] = float(chosen["max_path_m"])
    if chosen.get("max_net_m") is not None:
        clauses.append("l.net_m <= %(max_net_m)s")
        params["max_net_m"] = float(chosen["max_net_m"])
    if chosen.get("max_net_over_path") is not None:
        clauses.append(
            "(l.path_m <= 0 OR (l.net_m / NULLIF(l.path_m, 0)) <= %(max_net_over_path)s)"
        )
        params["max_net_over_path"] = float(chosen["max_net_over_path"])
    if chosen.get("min_radius_hull"):
        clauses.append(f"l.radius_m >= {HULL_RADIUS_M}")
    elif chosen.get("min_radius_m") is not None:
        clauses.append("l.radius_m >= %(min_radius_m)s")
        params["min_radius_m"] = float(chosen["min_radius_m"])
    if chosen.get("max_radius_hull"):
        clauses.append(f"l.radius_m <= {HULL_RADIUS_M}")
    elif chosen.get("max_radius_m") is not None:
        clauses.append("l.radius_m <= %(max_radius_m)s")
        params["max_radius_m"] = float(chosen["max_radius_m"])
    if not clauses:
        return "TRUE", {}
    return " AND ".join(clauses), params


def _filters_payload(chosen: dict[str, Any]) -> dict[str, Any]:
    return {
        "pattern": chosen.get("pattern"),
        "minPathM": chosen.get("min_path_m"),
        "maxPathM": chosen.get("max_path_m"),
        "maxNetM": chosen.get("max_net_m"),
        "maxNetOverPath": chosen.get("max_net_over_path"),
        "minRadiusM": "max(lengthM, 300)" if chosen.get("min_radius_hull") else chosen.get("min_radius_m"),
        "maxRadiusM": "max(lengthM, 300)" if chosen.get("max_radius_hull") else chosen.get("max_radius_m"),
    }


def detect_loitering(
    engine: Engine | None = None,
    *,
    pattern: str | None = None,
    outside_anchorage: bool | None = None,
    near_restricted: bool | None = None,
    before_sts: bool | None = None,
    min_path_m: float | None = None,
    max_path_m: float | None = None,
    max_net_m: float | None = None,
    max_net_over_path: float | None = None,
    min_radius_m: float | None = None,
    max_radius_m: float | None = None,
) -> dict[str, Any]:
    if pattern is not None and pattern not in PRESETS:
        raise ValueError("pattern must be loitering or anchorSwing")
    engine = engine or get_pg_engine()
    clauses = []
    if outside_anchorage:
        clauses.append("l.outside_anchorage")
    if near_restricted:
        clauses.append("l.near_restricted")
    if before_sts:
        clauses.append("l.before_sts")
    if clauses:
        flag_filter = " OR ".join(clauses)
    elif pattern:
        flag_filter = "TRUE"
    else:
        flag_filter = "l.outside_anchorage OR l.near_restricted OR l.before_sts"
    chosen = _resolve_filters(
        pattern,
        min_path_m=min_path_m,
        max_path_m=max_path_m,
        max_net_m=max_net_m,
        max_net_over_path=max_net_over_path,
        min_radius_m=min_radius_m,
        max_radius_m=max_radius_m,
    )
    geo_filter, params = _geo_filter(chosen)
    events = pd.read_sql(
        LOITER_SQL.format(flag_filter=flag_filter, geo_filter=geo_filter),
        con=engine,
        params=params or None,
    )
    events = attach_sanctions(events, engine)
    events = sort_listed_first(events)

    by_flag = {
        "outside_anchorage": int(events["outside_anchorage"].sum()) if not events.empty else 0,
        "near_restricted": int(events["near_restricted"].sum()) if not events.empty else 0,
        "before_sts": int(events["before_sts"].sum()) if not events.empty else 0,
    }
    sanctions_match_count = int(events["sanctions_match"].sum()) if not events.empty else 0
    return {
        "rule_version": RULE_VERSION,
        "ship_type_filter": SHIP_TYPE_FILTER,
        "filters": _filters_payload(chosen),
        "event_count": int(len(events)),
        "by_flag": by_flag,
        "sanctions_match_count": sanctions_match_count,
        "events_payload": events_to_payload(events),
    }
