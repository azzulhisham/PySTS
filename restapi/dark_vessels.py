"""
Suspected dark / AIS-transponder-off vessels (v1).

Reads ais_vesselslowmoveactivities produced by vesselslowspeeddetection.py.

Candidates are never dropped because of polygons. When the last position
falls inside a polygon from polygons.py, polygonName is attached. If the
point is inside both a parent and an Excl hole, the Excl name is used
(more specific). inExclPolygon is true in that case.

Labels are operational heuristics — intentional dark cannot be proven from AIS alone.
SEA coverage exit is a competing explanation and is returned as a separate reason.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any
import pandas as pd
from sqlalchemy.engine import Engine

from ais_identity import VALID_SHIP_MMSI_SQL, clean_ship_name, is_valid_ship_mmsi
from duckdb_spatial import spatial_df
from pg_engine import get_pg_engine
from polygons import anchorage_areas, is_excl_name
from sanctions import attach_sanctions, payload_fields, sort_listed_first
from ship_risk_attach import attach_risk_ratings, risk_counts, risk_payload_fields
from vessel_size import DIM_SELECT, class_a_join, class_b_join, dimension_fields
from api_timestamps import format_last_seen_at

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

MIN_SILENCE_MINUTES = 30
COVERAGE_EXIT_DAYS = 3
# Gaps longer than this are always treated as coverage exit (not suspected dark).
DARK_MAX_DAYS = int(os.environ.get("DARK_MAX_DAYS", "7"))
# Optional GeoJSON Polygon/MultiPolygon path. When set, last position outside it
# is labelled possible_coverage_exit (applied in Python on already-fetched rows).
DARK_COVERAGE_GEOJSON = os.environ.get("DARK_COVERAGE_GEOJSON", "").strip()
CONFIRMED_STOP_ROWCOUNT = 30
MIN_SLOWDOWN_ROWCOUNT = 5

# Latest activity per MMSI (same as row_number()…=1), then dark filters.
# DISTINCT ON avoids windowing the entire history table on every HTTP call.
DARK_VESSEL_SQL = f"""
SELECT
    a.id AS activity_id,
    a.mmsi,
    a.ts,
    a.tscurrent,
    a.tsstop,
    a.tsout,
    a.longitude,
    a.latitude,
    a.curlongitude,
    a.curlatitude,
    a.sog,
    a.cog,
    a.cursog,
    a.curcog,
    a.rowcount,
    a.distance,
    a.navstatus,
    a.navstatusdesc,
    s."shipName" AS shipname,
    s."shipType" AS shiptype,
    s."shipTypeDesc" AS shiptypedesc,
    s."imo" AS imo,
{DIM_SELECT},
    EXTRACT(EPOCH FROM (now() - a.tscurrent)) AS silence_seconds,
    CASE
        WHEN a.tscurrent <= now() - interval '{DARK_MAX_DAYS} days'
            THEN 'possible_coverage_exit'
        WHEN a.rowcount >= {MIN_SLOWDOWN_ROWCOUNT}
             AND a.rowcount < {CONFIRMED_STOP_ROWCOUNT}
             AND a.tscurrent > now() - interval '{COVERAGE_EXIT_DAYS} days'
            THEN 'suspected_dark_after_slowdown'
        WHEN a.tscurrent <= now() - interval '{COVERAGE_EXIT_DAYS} days'
             OR COALESCE(a.cursog, a.sog, 0) > 3.0
            THEN 'possible_coverage_exit'
        ELSE 'low_evidence_ais_gap'
    END AS dark_reason,
    CASE
        WHEN a.tscurrent <= now() - interval '{DARK_MAX_DAYS} days'
            THEN 'low'
        WHEN a.rowcount >= {MIN_SLOWDOWN_ROWCOUNT}
             AND a.rowcount < {CONFIRMED_STOP_ROWCOUNT}
             AND a.tscurrent > now() - interval '{COVERAGE_EXIT_DAYS} days'
            THEN 'high'
        WHEN a.tscurrent <= now() - interval '{COVERAGE_EXIT_DAYS} days'
             OR COALESCE(a.cursog, a.sog, 0) > 3.0
            THEN 'low'
        ELSE 'medium'
    END AS confidence
FROM (
    SELECT DISTINCT ON (mmsi) *
    FROM public.ais_vesselslowmoveactivities
    ORDER BY mmsi, ts DESC
) a
{class_a_join("a.mmsi")}
{class_b_join("a.mmsi")}
WHERE a.tsout IS NULL
  AND a.tsstop IS NOT NULL
  AND a.rowcount < {CONFIRMED_STOP_ROWCOUNT}
  AND a.tscurrent IS NOT NULL
  AND a.tscurrent <= now() - interval '{MIN_SILENCE_MINUTES} minutes'
{VALID_SHIP_MMSI_SQL.replace("mmsi", "a.mmsi")}
  AND s."shipType" >= 70 AND s."shipType" < 90
ORDER BY a.tscurrent ASC
"""


def polygon_to_geojson(coords_lonlat: list[list[float]]) -> dict:
    ring = [[float(lon), float(lat)] for lon, lat in coords_lonlat]
    if ring[0] != ring[-1]:
        ring.append(ring[0])
    return {"type": "Polygon", "coordinates": [ring]}


def attach_polygon_names(
    vessels: pd.DataFrame,
    areas: list[dict] | None = None,
) -> pd.DataFrame:
    """
    Label vessels with the polygon they last sat in. Never filters rows.
    Prefers an Excl name when the point is inside a hole.
    """
    empty = dict(
        polygon_name=pd.Series(dtype="object"),
        in_excl_polygon=pd.Series(dtype=bool),
    )
    if vessels.empty:
        return vessels.assign(**empty)

    areas = areas or anchorage_areas
    work = vessels.copy()
    lon = work["curlongitude"] if "curlongitude" in work.columns else work["longitude"]
    lat = work["curlatitude"] if "curlatitude" in work.columns else work["latitude"]
    if "curlongitude" in work.columns and "longitude" in work.columns:
        lon = work["curlongitude"].where(work["curlongitude"].notna(), work["longitude"])
    if "curlatitude" in work.columns and "latitude" in work.columns:
        lat = work["curlatitude"].where(work["curlatitude"].notna(), work["latitude"])
    work["_lon"] = lon
    work["_lat"] = lat

    df_poly = pd.DataFrame([
        {
            "anchorage_name": area["name"],
            "is_excl": is_excl_name(area["name"]),
            "geojson": json.dumps(polygon_to_geojson(area["polygon"])),
        }
        for area in areas
    ])

    located = spatial_df(
        """
        WITH hits AS (
            SELECT
                v.mmsi,
                p.anchorage_name,
                p.is_excl
            FROM dark_vessels v
            CROSS JOIN anchorage_polys p
            WHERE v._lon IS NOT NULL
              AND v._lat IS NOT NULL
              AND ST_Within(
                    ST_Point(v._lon, v._lat),
                    ST_GeomFromGeoJSON(p.geojson)
                  )
        ),
        picked AS (
            SELECT
                mmsi,
                COALESCE(
                    MIN(CASE WHEN is_excl THEN anchorage_name END),
                    MIN(CASE WHEN NOT is_excl THEN anchorage_name END)
                ) AS polygon_name,
                BOOL_OR(is_excl) AS in_excl_polygon
            FROM hits
            GROUP BY mmsi
        )
        SELECT
            v.* EXCLUDE (_lon, _lat),
            p.polygon_name,
            COALESCE(p.in_excl_polygon, FALSE) AS in_excl_polygon
        FROM dark_vessels v
        LEFT JOIN picked p ON p.mmsi = v.mmsi
        """,
        {"dark_vessels": work, "anchorage_polys": df_poly},
    )

    return located


def _fmt_ts(value: Any) -> str | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if pd.isna(value):
        return None
    return str(value)


def _fmt_duration(seconds: Any) -> dict[str, Any]:
    if seconds is None or (isinstance(seconds, float) and pd.isna(seconds)):
        return {"silenceSeconds": None, "silenceHours": None, "silenceLabel": None}
    sec = max(float(seconds), 0.0)
    hours = sec / 3600.0
    h = int(hours)
    m = int((sec % 3600) // 60)
    return {
        "silenceSeconds": round(sec, 3),
        "silenceHours": round(hours, 4),
        "silenceLabel": f"{h}h {m}m",
    }


def load_dark_candidates(engine: Engine | None = None) -> pd.DataFrame:
    engine = engine or get_pg_engine()
    return pd.read_sql(DARK_VESSEL_SQL, con=engine)


def vessels_to_payload(vessels: pd.DataFrame) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    if vessels.empty:
        return records

    for _, v in vessels.iterrows():
        silence = _fmt_duration(v.get("silence_seconds"))
        lon = v.get("curlongitude") if pd.notna(v.get("curlongitude")) else v.get("longitude")
        lat = v.get("curlatitude") if pd.notna(v.get("curlatitude")) else v.get("latitude")
        sog = v.get("cursog") if pd.notna(v.get("cursog")) else v.get("sog")
        cog = v.get("curcog") if pd.notna(v.get("curcog")) else v.get("cog")

        records.append({
            "activityId": int(v["activity_id"]),
            "mmsi": int(v["mmsi"]),
            "shipName": clean_ship_name(
                v.get("shipname") if pd.notna(v.get("shipname")) else None
            ),
            "shipType": int(v["shiptype"]) if pd.notna(v.get("shiptype")) else None,
            "shipTypeDesc": v.get("shiptypedesc") if pd.notna(v.get("shiptypedesc")) else None,
            "latitude": float(lat) if pd.notna(lat) else None,
            "longitude": float(lon) if pd.notna(lon) else None,
            "sog": float(sog) if pd.notna(sog) else None,
            "cog": float(cog) if pd.notna(cog) else None,
            "navStatusDesc": v.get("navstatusdesc") if pd.notna(v.get("navstatusdesc")) else None,
            "rowCount": int(v["rowcount"]) if pd.notna(v.get("rowcount")) else None,
            "distanceM": float(v["distance"]) if pd.notna(v.get("distance")) else None,
            "ts": _fmt_ts(v.get("ts")),
            "tsStop": _fmt_ts(v.get("tsstop")),
            "tsCurrent": _fmt_ts(v.get("tscurrent")),
            "lastSeenAt": format_last_seen_at(v.get("tscurrent")),
            "silenceSeconds": silence["silenceSeconds"],
            "silenceHours": silence["silenceHours"],
            "silenceLabel": silence["silenceLabel"],
            "darkReason": v.get("dark_reason"),
            "confidence": v.get("confidence"),
            "polygonName": None if v.get("polygon_name") is None or pd.isna(v.get("polygon_name")) else v.get("polygon_name"),
            "inExclPolygon": bool(v.get("in_excl_polygon")) if pd.notna(v.get("in_excl_polygon")) else False,
            **dimension_fields(v),
            **payload_fields(v),
            **risk_payload_fields(v),
        })
    return records


def _load_coverage_geom() -> dict | None:
    if not DARK_COVERAGE_GEOJSON:
        return None
    path = Path(DARK_COVERAGE_GEOJSON)
    if not path.is_file():
        logging.warning("DARK_COVERAGE_GEOJSON not found: %s", path)
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logging.exception("Failed to read DARK_COVERAGE_GEOJSON %s", path)
        return None
    if data.get("type") == "Feature":
        return data.get("geometry")
    if data.get("type") == "FeatureCollection":
        features = data.get("features") or []
        return features[0].get("geometry") if features else None
    return data


def apply_coverage_polygon(vessels: pd.DataFrame) -> pd.DataFrame:
    """Relabel last position outside the optional coverage polygon as coverage exit."""
    geom = _load_coverage_geom()
    if geom is None or vessels.empty:
        return vessels

    work = vessels.copy()
    lon = work["curlongitude"] if "curlongitude" in work.columns else work["longitude"]
    lat = work["curlatitude"] if "curlatitude" in work.columns else work["latitude"]
    if "curlongitude" in work.columns and "longitude" in work.columns:
        lon = work["curlongitude"].where(work["curlongitude"].notna(), work["longitude"])
    if "curlatitude" in work.columns and "latitude" in work.columns:
        lat = work["curlatitude"].where(work["curlatitude"].notna(), work["latitude"])
    work["_lon"] = lon
    work["_lat"] = lat

    # Trusted env-configured path; embed GeoJSON so DuckDB can ST_Within without
    # an extra Postgres round-trip.
    geo_lit = json.dumps(geom).replace("'", "''")
    located = spatial_df(
        f"""
        SELECT
            v.* EXCLUDE (_lon, _lat),
            CASE
                WHEN v._lon IS NULL OR v._lat IS NULL THEN FALSE
                ELSE ST_Within(
                    ST_Point(v._lon, v._lat),
                    ST_GeomFromGeoJSON('{geo_lit}')
                )
            END AS _in_coverage
        FROM dark_vessels v
        """,
        {"dark_vessels": work},
    )
    outside = ~located["_in_coverage"].fillna(False)
    located.loc[outside, "dark_reason"] = "possible_coverage_exit"
    located.loc[outside, "confidence"] = "low"
    return located.drop(columns=["_in_coverage"], errors="ignore")


def detect_dark_vessels(
    engine: Engine | None = None,
    include_coverage_exit: bool = False,
    *,
    summary_only: bool = False,
) -> dict[str, Any]:
    """
    Return suspected dark vessels from slow-move activities.

    include_coverage_exit defaults to False (ops list). Pass True for the
    previous view that also lists possible_coverage_exit.
    """
    engine = engine or get_pg_engine()
    df = load_dark_candidates(engine)
    if not df.empty:
        df = df[df["mmsi"].map(is_valid_ship_mmsi)].reset_index(drop=True)

    df = apply_coverage_polygon(df)

    if not include_coverage_exit and not df.empty:
        df = df[df["dark_reason"] != "possible_coverage_exit"].reset_index(drop=True)

    if not summary_only:
        df = attach_polygon_names(df)
    df = attach_sanctions(df, engine)
    df = attach_risk_ratings(df, engine)
    if not summary_only:
        extra = ["tscurrent"] if "tscurrent" in df.columns else []
        df = sort_listed_first(df, extra_sort=extra)

    by_reason: dict[str, int] = {}
    by_confidence: dict[str, int] = {}
    sanctions_match_count = 0
    if not df.empty:
        for reason, cnt in df["dark_reason"].value_counts().items():
            by_reason[str(reason)] = int(cnt)
        for conf, cnt in df["confidence"].value_counts().items():
            by_confidence[str(conf)] = int(cnt)
        sanctions_match_count = int(df["sanctions_match"].sum())
    risk = risk_counts(df)

    return {
        "candidate_count": int(len(df)),
        "by_reason": by_reason,
        "by_confidence": by_confidence,
        "sanctions_match_count": sanctions_match_count,
        "risk_red_count": risk["risk_red_count"],
        "risk_amber_count": risk["risk_amber_count"],
        "min_silence_minutes": MIN_SILENCE_MINUTES,
        "coverage_exit_days": COVERAGE_EXIT_DAYS,
        "dark_max_days": DARK_MAX_DAYS,
        "ship_type_filter": "70-89 (cargo/tanker/container Class-A large vessels)",
        "include_coverage_exit": include_coverage_exit,
        "rule_version": "v1.3-dark-max-days-coverage-polygon",
        "vessels": df if not summary_only else df.iloc[0:0],
        "vessels_payload": [] if summary_only else vessels_to_payload(df),
        "sql": DARK_VESSEL_SQL,
    }
