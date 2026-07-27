"""
Illegal anchoring detection (v2) using stop activities + watch polygons.

Rule:
- Load stopped / stale Class-A large vessels (shipType 70–89).
- KEEP vessels whose last position is inside:
    1) the restricted-limit polygon, OR
    2) a watch polygon from restapi/polygons.py
- EXCLUDE vessels inside Singapore port-limit polygons
  (Singapore East / Western OPL / South + Excl* carve-outs).

This is an operational heuristic for map/API study, not a legal determination.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

import duckdb
import pandas as pd
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

from polygons import anchorage_areas, restricted_limit

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# Restricted limit ring (lon/lat) — single source in polygons.py
RESTRICTED_LIMIT_LONLAT = restricted_limit["polygon"]

# Singapore port waters drawn from polygons.py — exclude these from illegal candidates.
# (Matches the dense green clusters around Jurong / Singapore South on the study map.)
PORT_LIMIT_NAME_MARKERS = (
    "singapore east anchorage",
    "singapore western opl",
    "singapore south anchorage",
)

pswd = "m4r1t1m3"
DATABASE_URL = (
    f"postgresql://postgresadmin:{quote(pswd)}"
    f"@marineai2.cxwk8yige5f2.ap-southeast-5.rds.amazonaws.com:5432/pnav"
)

STOPPED_VESSEL_SQL = """
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
    s."shipName" AS shipname
FROM (
    SELECT *,
           row_number() OVER (PARTITION BY mmsi ORDER BY ts DESC) AS rowcount_mmsi
    FROM public.ais_vesselmovementactivities
) a
INNER JOIN public.ais_static s ON s.mmsi = a.mmsi
WHERE a.tsout IS NULL
  AND (
        (a.tsstop IS NOT NULL AND a.tsstop <= now() - interval '1 HOURS')
        OR a.tscurrent <= now() - interval '30 MINUTES'
      )
  AND a.rowcount_mmsi = 1
  AND a.curlongitude IS NOT NULL
  AND a.curlatitude IS NOT NULL
  -- Class-A large commercial vessels: cargo (70-79), tanker (80-89)
  AND s."shipType" >= 70 AND s."shipType" < 90
"""


def get_pg_engine() -> Engine:
    return create_engine(
        DATABASE_URL,
        pool_size=5,
        max_overflow=10,
        pool_timeout=30,
    )


def _ensure_duckdb_spatial() -> None:
    duckdb.sql("INSTALL spatial")
    duckdb.sql("LOAD spatial")


def polygon_to_geojson(coords_lonlat: list[list[float]]) -> dict:
    ring = [[float(lon), float(lat)] for lon, lat in coords_lonlat]
    if ring[0] != ring[-1]:
        ring.append(ring[0])
    return {"type": "Polygon", "coordinates": [ring]}


def is_port_limit_name(name: str) -> bool:
    lower = name.lower()
    return any(marker in lower for marker in PORT_LIMIT_NAME_MARKERS)


def split_anchorage_polygons(areas: list[dict] | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Split polygons.py into:
    - watch polygons (keep candidates that fall inside)
    - port-limit polygons (exclude candidates that fall inside)
    """
    areas = areas or anchorage_areas
    watch_rows = []
    port_rows = []
    for area in areas:
        row = {
            "anchorage_name": area["name"],
            "is_excl": "excl" in area["name"].lower(),
            "geojson": json.dumps(polygon_to_geojson(area["polygon"])),
        }
        if is_port_limit_name(area["name"]):
            port_rows.append(row)
        else:
            watch_rows.append(row)
    return pd.DataFrame(watch_rows), pd.DataFrame(port_rows)


def port_limit_polygons(areas: list[dict] | None = None) -> list[dict]:
    """Polygon dicts treated as Singapore port limit (for maps / docs)."""
    areas = areas or anchorage_areas
    return [a for a in areas if is_port_limit_name(a["name"])]


def watch_polygons(areas: list[dict] | None = None) -> list[dict]:
    """Polygon dicts used as illegal-anchoring watch areas (non–port-limit)."""
    areas = areas or anchorage_areas
    return [a for a in areas if not is_port_limit_name(a["name"])]


def _fmt_ts(value: Any) -> str | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if pd.isna(value):
        return None
    return str(value)


def _duration_from_stop(tsstop: Any, tscurrent: Any, ts: Any) -> dict[str, Any]:
    """How long the vessel has been considered stopped / stale."""
    start = tsstop if pd.notna(tsstop) else ts
    end = tscurrent if pd.notna(tscurrent) else None
    if start is None or end is None or pd.isna(start) or pd.isna(end):
        return {"durationSeconds": None, "durationHours": None, "durationLabel": None}

    start_ts = pd.Timestamp(start)
    end_ts = pd.Timestamp(end)
    if start_ts.tzinfo is None:
        start_ts = start_ts.tz_localize("UTC")
    if end_ts.tzinfo is None:
        end_ts = end_ts.tz_localize("UTC")
    sec = max((end_ts - start_ts).total_seconds(), 0.0)
    hours = sec / 3600.0
    h = int(hours)
    m = int((sec % 3600) // 60)
    return {
        "durationSeconds": round(sec, 3),
        "durationHours": round(hours, 4),
        "durationLabel": f"{h}h {m}m",
    }


def load_stopped_vessels(engine: Engine | None = None) -> pd.DataFrame:
    engine = engine or get_pg_engine()
    df = pd.read_sql(STOPPED_VESSEL_SQL, con=engine)
    if df.empty:
        return df
    return df.drop_duplicates(subset="mmsi", keep="first")


def classify_illegal_anchoring(stopped: pd.DataFrame) -> pd.DataFrame:
    """
    Keep stopped vessels inside restricted OR watch polygons,
    excluding those inside Singapore port-limit polygons.
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

    _ensure_duckdb_spatial()
    watch_df, port_df = split_anchorage_polygons()
    restricted = json.dumps(polygon_to_geojson(RESTRICTED_LIMIT_LONLAT))

    duckdb.register("stopped_vessels", stopped)
    duckdb.register("watch_polygons", watch_df if not watch_df.empty else pd.DataFrame(
        columns=["anchorage_name", "is_excl", "geojson"]
    ))
    duckdb.register("port_limit_polygons", port_df if not port_df.empty else pd.DataFrame(
        columns=["anchorage_name", "is_excl", "geojson"]
    ))

    located = duckdb.sql(
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
        """
    ).df()

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


def vessels_to_payload(vessels: pd.DataFrame) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    if vessels.empty:
        return records

    for _, v in vessels.iterrows():
        duration = _duration_from_stop(v.get("tsstop"), v.get("tscurrent"), v.get("ts"))
        watch_name = v.get("watch_polygon_name")
        if watch_name is not None and pd.isna(watch_name):
            watch_name = None
        port_name = v.get("port_limit_name")
        if port_name is not None and pd.isna(port_name):
            port_name = None
        records.append({
            "mmsi": int(v["mmsi"]),
            "shipName": v.get("shipname") if pd.notna(v.get("shipname")) else None,
            "shipType": int(v["shiptype"]) if pd.notna(v.get("shiptype")) else None,
            "shipTypeDesc": v.get("shiptypedesc") if pd.notna(v.get("shiptypedesc")) else None,
            "latitude": float(v["curlatitude"]),
            "longitude": float(v["curlongitude"]),
            "sog": float(v["sog"]) if pd.notna(v.get("sog")) else None,
            "cog": float(v["cog"]) if pd.notna(v.get("cog")) else None,
            "navStatusDesc": v.get("navstatusdesc") if pd.notna(v.get("navstatusdesc")) else None,
            "tsStop": _fmt_ts(v.get("tsstop")),
            "tsCurrent": _fmt_ts(v.get("tscurrent")),
            "durationSeconds": duration["durationSeconds"],
            "durationHours": duration["durationHours"],
            "durationLabel": duration["durationLabel"],
            "inRestrictedZone": bool(v.get("in_restricted")),
            "inWatchPolygon": bool(v.get("in_watch_polygon")),
            "inPortLimit": bool(v.get("in_port_limit")),
            "watchPolygonName": watch_name,
            "portLimitName": port_name,
            "reason": v.get("reason"),
            "detectedAt": v.get("detected_at"),
        })
    return records


def detect_illegal_anchoring(engine: Engine | None = None) -> dict[str, Any]:
    engine = engine or get_pg_engine()
    stopped = load_stopped_vessels(engine)
    illegal = classify_illegal_anchoring(stopped)

    by_reason: dict[str, int] = {}
    if not illegal.empty:
        for reason, cnt in illegal["reason"].value_counts().items():
            by_reason[str(reason)] = int(cnt)

    watch = watch_polygons()
    port = port_limit_polygons()

    return {
        "stopped_candidate_count": int(len(stopped)),
        "illegal_count": int(len(illegal)),
        "by_reason": by_reason,
        "watch_polygon_count": len(watch),
        "port_limit_polygon_count": len(port),
        "ship_type_filter": "70-89 (cargo/tanker/container Class-A large vessels)",
        "vessels": illegal,
        "vessels_payload": vessels_to_payload(illegal),
        "rule_version": "v2-in-restricted-or-watch-exclude-port-limit",
    }
