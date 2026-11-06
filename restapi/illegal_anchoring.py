"""
Suspected illegal-anchoring candidates (heuristic) using stop activities +
watch polygons.

Rule:
- Load stopped / stale Class-A large vessels (shipType 70–89).
- KEEP vessels whose last position is inside:
    1) the restricted-limit polygon, OR
    2) a parent / watch polygon from restapi/polygons.py
- EXCLUDE vessels inside any polygon whose name contains "Excl"
  (carve-outs / holes inside a parent polygon).
- After keep/drop, drop candidates inside operational port-area footprints
  (anchorage still wins for that exclusion). Additive harbour labels may still
  name overlapping port/terminal/berth reference polygons on kept rows.

Position and stop duration do not prove illegality. Port/terminal/berth files
under data/polygons/src are operational reference geometry, not verified legal
port limits or designated anchorage boundaries.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any
import pandas as pd
from sqlalchemy.engine import Engine

from duckdb_spatial import spatial_df
from pg_engine import get_pg_engine
from ais_identity import clean_ship_name, filter_valid_ship_mmsi_frame
from polygons import anchorage_areas, is_excl_name, restricted_limit
from port_areas import (
    annotate_harbour_location,
    annotate_in_port_area,
    get_port_area_index,
)
from sanctions import attach_sanctions, payload_fields, sort_listed_first
from ship_risk_attach import attach_risk_ratings, risk_counts, risk_payload_fields
from vessel_size import DIM_SELECT, class_a_join, class_b_join, dimension_fields
from api_timestamps import format_last_seen_at

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# Restricted limit ring (lon/lat) — single source in polygons.py
RESTRICTED_LIMIT_LONLAT = restricted_limit["polygon"]

# Latest activity per MMSI (same as row_number()…=1), then stopped/stale filters.
# DISTINCT ON avoids windowing the entire movement-history table on every HTTP call.
STOPPED_VESSEL_SQL = f"""
SELECT
    a.id AS activity_id,
    a.mmsi,
    a.ts,
    a.tscurrent,
    a.tsstop,
    a.tsout,
    a.curlongitude,
    a.curlatitude,
    a.cursog AS sog,
    a.curcog AS cog,
    a.rowcount,
    a.navstatusdesc,
    s."shipType" AS shiptype,
    s."shipTypeDesc" AS shiptypedesc,
    s."shipName" AS shipname,
    s."imo" AS imo,
{DIM_SELECT}
FROM (
    SELECT DISTINCT ON (mmsi) *
    FROM public.ais_vesselmovementactivities
    ORDER BY mmsi, ts DESC
) a
{class_a_join("a.mmsi")}
{class_b_join("a.mmsi")}
WHERE a.tsout IS NULL
  AND (
        (a.tsstop IS NOT NULL AND a.tsstop <= now() - interval '1 HOURS')
        OR a.tscurrent <= now() - interval '30 MINUTES'
      )
  AND a.curlongitude IS NOT NULL
  AND a.curlatitude IS NOT NULL
  -- Class-A large commercial vessels: cargo (70-79), tanker (80-89)
  AND s."shipType" >= 70 AND s."shipType" < 90
"""


def polygon_to_geojson(coords_lonlat: list[list[float]]) -> dict:
    ring = [[float(lon), float(lat)] for lon, lat in coords_lonlat]
    if ring[0] != ring[-1]:
        ring.append(ring[0])
    return {"type": "Polygon", "coordinates": [ring]}


def is_port_limit_name(name: str) -> bool:
    """Back-compat alias: Excl carve-outs are the exclusion polygons."""
    return is_excl_name(name)


def split_anchorage_polygons(areas: list[dict] | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Split polygons.py into:
    - watch / parent polygons (keep candidates that fall inside)
    - Excl carve-outs (exclude candidates that fall inside a hole)
    """
    areas = areas or anchorage_areas
    watch_rows = []
    port_rows = []
    for area in areas:
        row = {
            "anchorage_name": area["name"],
            "is_excl": is_excl_name(area["name"]),
            "geojson": json.dumps(polygon_to_geojson(area["polygon"])),
        }
        if is_port_limit_name(area["name"]):
            port_rows.append(row)
        else:
            watch_rows.append(row)
    return pd.DataFrame(watch_rows), pd.DataFrame(port_rows)


def port_limit_polygons(areas: list[dict] | None = None) -> list[dict]:
    """Excl carve-out polygons (API key kept as port_limit for compatibility)."""
    areas = areas or anchorage_areas
    return [a for a in areas if is_port_limit_name(a["name"])]


def watch_polygons(areas: list[dict] | None = None) -> list[dict]:
    """Parent / watch polygons used as illegal-anchoring detection zones."""
    areas = areas or anchorage_areas
    return [a for a in areas if not is_port_limit_name(a["name"])]


def _fmt_ts(value: Any) -> str | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if pd.isna(value):
        return None
    return str(value)


def _as_utc_timestamp(value: Any) -> pd.Timestamp | None:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        return None
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        return ts.tz_localize("UTC")
    return ts.tz_convert("UTC")


def _duration_from_stop(tsstop: Any, tscurrent: Any, ts: Any) -> dict[str, Any]:
    """
    Legacy duration fields (unchanged semantics).

    Prefers tsstop, else falls back to activity `ts`. Kept for backward
    compatibility; prefer stoppedElapsed* for confirmed-stop elapsed time.
    """
    start = tsstop if pd.notna(tsstop) else ts
    end = tscurrent if pd.notna(tscurrent) else None
    start_ts = _as_utc_timestamp(start)
    end_ts = _as_utc_timestamp(end)
    if start_ts is None or end_ts is None:
        return {"durationSeconds": None, "durationHours": None, "durationLabel": None}

    sec = max((end_ts - start_ts).total_seconds(), 0.0)
    hours = sec / 3600.0
    h = int(hours)
    m = int((sec % 3600) // 60)
    return {
        "durationSeconds": round(sec, 3),
        "durationHours": round(hours, 4),
        "durationLabel": f"{h}h {m}m",
    }


def confirmed_stop_elapsed(tsstop: Any, tscurrent: Any) -> dict[str, Any]:
    """
    Elapsed time since stop *confirmation* on the current open activity row.

    This is NOT exact total time stationary. stopStartedAt is
    ais_vesselmovementactivities.tsstop — AIS time when the trajectory job
    first confirmed the stop (rowcount >= 20 and distance < 30 m). It is not
    the first low-speed fix (`ts`) and must not be invented from a lookback
    window or from rowcount.

    lastObservedAt is tscurrent — last AIS fix on this open activity. Elapsed
    time never extends past this instant (stale AIS does not grow with wall
    clock). Each call uses only the timestamps passed in; a prior closed
    stop's duration cannot leak into a later reopened stop.

    When tsstop is missing (stale-only candidates), or timestamps are unusable,
    elapsed fields are null with stopElapsedStatus=unknown.
    """
    last_observed = format_last_seen_at(tscurrent)
    start_ts = _as_utc_timestamp(tsstop)
    end_ts = _as_utc_timestamp(tscurrent)

    if start_ts is None:
        return {
            "stopStartedAt": None,
            "lastObservedAt": last_observed,
            "stoppedElapsedSeconds": None,
            "stoppedElapsedHours": None,
            "stopElapsedStatus": "unknown",
            "stopElapsedReason": (
                "confirmed_stop_start_unavailable: tsstop is null "
                "(stale AIS candidate or stop not yet confirmed)"
            ),
        }

    stop_started = format_last_seen_at(tsstop)
    if end_ts is None:
        return {
            "stopStartedAt": stop_started,
            "lastObservedAt": None,
            "stoppedElapsedSeconds": None,
            "stoppedElapsedHours": None,
            "stopElapsedStatus": "unknown",
            "stopElapsedReason": "last_observation_unavailable: tscurrent is null",
        }

    if end_ts < start_ts:
        return {
            "stopStartedAt": stop_started,
            "lastObservedAt": last_observed,
            "stoppedElapsedSeconds": None,
            "stoppedElapsedHours": None,
            "stopElapsedStatus": "unknown",
            "stopElapsedReason": "inconsistent_timestamps: tscurrent is before tsstop",
        }

    sec = (end_ts - start_ts).total_seconds()
    return {
        "stopStartedAt": stop_started,
        "lastObservedAt": last_observed,
        "stoppedElapsedSeconds": round(sec, 3),
        "stoppedElapsedHours": round(sec / 3600.0, 4),
        "stopElapsedStatus": "known",
        "stopElapsedReason": None,
    }


def _harbour_payload_fields(row: Any) -> dict[str, Any]:
    def _flag(value: Any) -> bool:
        if value is None:
            return False
        try:
            if pd.isna(value):
                return False
        except (TypeError, ValueError):
            pass
        return bool(value)

    def _opt_str(value: Any) -> str | None:
        if value is None:
            return None
        try:
            if pd.isna(value):
                return None
        except (TypeError, ValueError):
            pass
        text = str(value).strip()
        return text or None

    return {
        "inPortArea": _flag(row.get("in_port_area")),
        "portAreaName": _opt_str(row.get("port_area_name")),
        "portAreaTerminalName": _opt_str(row.get("port_area_terminal_name")),
        "atBerth": _flag(row.get("at_berth")),
        "berthName": _opt_str(row.get("berth_name")),
    }


POLYGON_PROVENANCE = {
    "source": "data/polygons/src/{ports,terminals,berths}.geojson",
    "role": "operational_reference",
    "validatedLegalPortLimits": False,
    "designatedAnchorageBoundaries": False,
    "note": (
        "Port, terminal and berth polygons are operational reference geometry "
        "copied for local MANTIS use. They are not verified legal port limits "
        "and must not be treated as designated anchorage boundaries. "
        "Parent/watch, Restricted Limit and Excl-hole rules are unchanged; "
        "official anchorage-boundary validation remains pending."
    ),
}


def load_stopped_vessels(engine: Engine | None = None) -> pd.DataFrame:
    engine = engine or get_pg_engine()
    df = pd.read_sql(STOPPED_VESSEL_SQL, con=engine)
    if df.empty:
        return df
    return df.drop_duplicates(subset="mmsi", keep="first")


def classify_illegal_anchoring(stopped: pd.DataFrame) -> pd.DataFrame:
    """
    Keep stopped vessels inside restricted OR parent / watch polygons,
    excluding those inside any Excl carve-out.
    """
    empty_cols = dict(
        in_restricted=pd.Series(dtype=bool),
        in_watch_polygon=pd.Series(dtype=bool),
        in_port_limit=pd.Series(dtype=bool),
        watch_polygon_name=pd.Series(dtype="object"),
        port_limit_name=pd.Series(dtype="object"),
        reason=pd.Series(dtype="object"),
    )
    if stopped.empty:
        return stopped.assign(**empty_cols)

    watch_df, port_df = split_anchorage_polygons()
    restricted = json.dumps(polygon_to_geojson(RESTRICTED_LIMIT_LONLAT))

    located = spatial_df(
        f"""
        WITH base AS (
            SELECT
                v.*,
                ST_Within(
                    ST_Point(v.curlongitude, v.curlatitude),
                    ST_GeomFromGeoJSON('{restricted}')
                ) AS in_restricted
            FROM stopped_vessels v
        ),
        watch_hit AS (
            SELECT
                b.mmsi,
                MIN(w.anchorage_name) AS watch_polygon_name
            FROM base b
            CROSS JOIN watch_polygons w
            WHERE ST_Within(
                ST_Point(b.curlongitude, b.curlatitude),
                ST_GeomFromGeoJSON(w.geojson)
            )
            GROUP BY b.mmsi
        ),
        port_hit AS (
            SELECT
                b.mmsi,
                MIN(p.anchorage_name) AS port_limit_name
            FROM base b
            CROSS JOIN port_limit_polygons p
            WHERE ST_Within(
                ST_Point(b.curlongitude, b.curlatitude),
                ST_GeomFromGeoJSON(p.geojson)
            )
            GROUP BY b.mmsi
        )
        SELECT
            b.*,
            (wh.mmsi IS NOT NULL) AS in_watch_polygon,
            wh.watch_polygon_name,
            (ph.mmsi IS NOT NULL) AS in_port_limit,
            ph.port_limit_name
        FROM base b
        LEFT JOIN watch_hit wh ON wh.mmsi = b.mmsi
        LEFT JOIN port_hit ph ON ph.mmsi = b.mmsi
        """,
        {
            "stopped_vessels": stopped,
            "watch_polygons": watch_df if not watch_df.empty else pd.DataFrame(
                columns=["anchorage_name", "is_excl", "geojson"]
            ),
            "port_limit_polygons": port_df if not port_df.empty else pd.DataFrame(
                columns=["anchorage_name", "is_excl", "geojson"]
            ),
        },
    )

    reasons = []
    keep = []
    for _, row in located.iterrows():
        if bool(row["in_port_limit"]):
            keep.append(False)
            reasons.append(None)
            continue

        reason_parts = []
        if bool(row["in_restricted"]):
            reason_parts.append("in_restricted_zone")
        if bool(row["in_watch_polygon"]):
            reason_parts.append("in_watch_polygon")

        if reason_parts:
            keep.append(True)
            reasons.append("+".join(reason_parts))
        else:
            keep.append(False)
            reasons.append(None)

    located["reason"] = reasons
    located["detected_at"] = datetime.now(timezone.utc).isoformat()
    return located.loc[keep].reset_index(drop=True)


def drop_port_area_candidates(
    candidates: pd.DataFrame,
) -> tuple[pd.DataFrame, int, list[dict[str, Any]]]:
    """
    After restricted/watch/Excl filtering, drop ships whose last position is
    inside a port area. Anchorage wins (those stay). Returns kept frame,
    excluded count, and up to 10 example dicts for reporting.
    """
    if candidates.empty:
        return candidates, 0, []
    annotated = annotate_in_port_area(
        candidates,
        lon_col="curlongitude",
        lat_col="curlatitude",
    )
    mask = annotated["in_port_area"].fillna(False).astype(bool)
    excluded = annotated.loc[mask]
    examples: list[dict[str, Any]] = []
    for _, row in excluded.head(10).iterrows():
        examples.append({
            "mmsi": int(row["mmsi"]),
            "portName": row.get("port_area_name"),
            "terminalName": row.get("port_area_terminal_name"),
            "latitude": float(row["curlatitude"]),
            "longitude": float(row["curlongitude"]),
        })
    kept = annotated.loc[~mask].reset_index(drop=True)
    return kept, int(mask.sum()), examples


def vessels_to_payload(vessels: pd.DataFrame) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    if vessels.empty:
        return records

    for _, v in vessels.iterrows():
        duration = _duration_from_stop(v.get("tsstop"), v.get("tscurrent"), v.get("ts"))
        elapsed = confirmed_stop_elapsed(v.get("tsstop"), v.get("tscurrent"))
        watch_name = v.get("watch_polygon_name")
        if watch_name is not None and pd.isna(watch_name):
            watch_name = None
        port_name = v.get("port_limit_name")
        if port_name is not None and pd.isna(port_name):
            port_name = None
        records.append({
            "mmsi": int(v["mmsi"]),
            "shipName": clean_ship_name(
                v.get("shipname") if pd.notna(v.get("shipname")) else None
            ),
            "shipType": int(v["shiptype"]) if pd.notna(v.get("shiptype")) else None,
            "shipTypeDesc": v.get("shiptypedesc") if pd.notna(v.get("shiptypedesc")) else None,
            "latitude": float(v["curlatitude"]),
            "longitude": float(v["curlongitude"]),
            "sog": float(v["sog"]) if pd.notna(v.get("sog")) else None,
            "cog": float(v["cog"]) if pd.notna(v.get("cog")) else None,
            "navStatusDesc": v.get("navstatusdesc") if pd.notna(v.get("navstatusdesc")) else None,
            "tsStop": _fmt_ts(v.get("tsstop")),
            "tsCurrent": _fmt_ts(v.get("tscurrent")),
            "lastSeenAt": format_last_seen_at(v.get("tscurrent")),
            "durationSeconds": duration["durationSeconds"],
            "durationHours": duration["durationHours"],
            "durationLabel": duration["durationLabel"],
            **elapsed,
            "inRestrictedZone": bool(v.get("in_restricted")),
            "inWatchPolygon": bool(v.get("in_watch_polygon")),
            "inPortLimit": bool(v.get("in_port_limit")),
            "watchPolygonName": watch_name,
            "portLimitName": port_name,
            **_harbour_payload_fields(v),
            "reason": v.get("reason"),
            "detectedAt": v.get("detected_at"),
            **dimension_fields(v),
            **payload_fields(v),
            **risk_payload_fields(v),
        })
    return records


def annotate_suspected_anchoring_harbour(candidates: pd.DataFrame) -> pd.DataFrame:
    """
    Label-only harbour context on already-selected candidates.

    Does not change keep/drop. Uses anchorage_wins=False so overlapping
    operational port/terminal names remain visible when a watch polygon
    also covers the point. Berth names are attached independently.
    """
    if candidates.empty:
        return candidates.assign(
            in_port_area=pd.Series(dtype=bool),
            port_area_name=pd.Series(dtype="object"),
            port_area_terminal_name=pd.Series(dtype="object"),
            at_berth=pd.Series(dtype=bool),
            berth_name=pd.Series(dtype="object"),
        )
    return annotate_harbour_location(
        candidates,
        lon_col="curlongitude",
        lat_col="curlatitude",
        include_berths=True,
        anchorage_wins=False,
    )


def detect_illegal_anchoring(
    engine: Engine | None = None,
    *,
    summary_only: bool = False,
) -> dict[str, Any]:
    engine = engine or get_pg_engine()
    # Ensure port polygons are loaded (and startup logs emitted) once.
    get_port_area_index()
    stopped = load_stopped_vessels(engine)
    stopped, invalid_mmsi_excluded_count = filter_valid_ship_mmsi_frame(stopped, "mmsi")
    illegal = classify_illegal_anchoring(stopped)
    illegal, port_area_excluded_count, port_area_excluded_examples = drop_port_area_candidates(
        illegal
    )
    # Additive labels only — selection already final.
    illegal = annotate_suspected_anchoring_harbour(illegal)
    illegal = attach_sanctions(illegal, engine)
    illegal = attach_risk_ratings(illegal, engine)
    if not summary_only:
        illegal = sort_listed_first(illegal)

    by_reason: dict[str, int] = {}
    sanctions_match_count = 0
    if not illegal.empty:
        for reason, cnt in illegal["reason"].value_counts().items():
            by_reason[str(reason)] = int(cnt)
        sanctions_match_count = int(illegal["sanctions_match"].sum())
    risk = risk_counts(illegal)

    watch = watch_polygons()
    port = port_limit_polygons()
    port_index = get_port_area_index()

    return {
        "stopped_candidate_count": int(len(stopped)) + invalid_mmsi_excluded_count,
        "illegal_count": int(len(illegal)),
        "port_area_excluded_count": port_area_excluded_count,
        "port_area_excluded_examples": port_area_excluded_examples,
        "invalid_mmsi_excluded_count": invalid_mmsi_excluded_count,
        "port_area_count": len(port_index.areas),
        "berth_count": port_index.berths_loaded,
        "sanctions_match_count": sanctions_match_count,
        "risk_red_count": risk["risk_red_count"],
        "risk_amber_count": risk["risk_amber_count"],
        "by_reason": by_reason,
        "watch_polygon_count": len(watch),
        "port_limit_polygon_count": len(port),
        "ship_type_filter": "70-89 (cargo/tanker/container Class-A large vessels)",
        "polygon_provenance": POLYGON_PROVENANCE,
        "vessels": illegal if not summary_only else illegal.iloc[0:0],
        "vessels_payload": [] if summary_only else vessels_to_payload(illegal),
        "rule_version": (
            "v3.3-suspected-anchoring-harbour-context-confirmed-stop-elapsed"
        ),
    }
