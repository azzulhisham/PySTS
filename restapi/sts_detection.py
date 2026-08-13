"""
STS (ship-to-ship) proximity inside restapi anchorage polygons.

Uses active proximity clusters from ais_vesselproximityobservation
(is_open = TRUE) with high suspicion_score, keeps those whose centroid
falls inside a parent / watch polygon from polygons.py, drops those whose
centroid is inside an Excl carve-out (hole), recomputes pairs at
MAX_DISTANCE_M, and returns only paired vessels with sog/cog enriched
from movement activities.
"""

from __future__ import annotations

import json
import logging
from typing import Any
from urllib.parse import quote

import duckdb
import pandas as pd
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

from polygons import anchorage_areas, is_excl_name

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

MAX_DISTANCE_M = 35.0
MIN_SUSPICION_SCORE = 4.5

pswd = "m4r1t1m3"
DATABASE_URL = (
    f"postgresql://postgresadmin:{quote(pswd)}"
    f"@marineai2.cxwk8yige5f2.ap-southeast-5.rds.amazonaws.com:5432/pnav"
)

ACTIVE_HIGH_SCORE_SQL = """
SELECT
    o.id AS observation_id,
    o.cluster_signature,
    o.is_open,
    o.vessel_count,
    o.centroid_longitude,
    o.centroid_latitude,
    o.max_internal_distance_m,
    o.threshold_m,
    o.cargo_count,
    o.tanker_count,
    o.suspicion_score,
    o.duration_seconds,
    o.first_detected_at,
    o.last_detected_at,
    o.is_anomaly
FROM public.ais_vesselproximityobservation o
WHERE o.is_open = TRUE
  AND o.suspicion_score IS NOT NULL
  AND o.suspicion_score >= %(min_score)s
  AND o.centroid_longitude IS NOT NULL
  AND o.centroid_latitude IS NOT NULL
ORDER BY o.suspicion_score DESC
"""

MEMBERS_SQL = """
SELECT
    m.observation_id,
    m.mmsi,
    m.ship_name AS shipname,
    m.ship_type AS shiptype,
    m.ship_type_desc AS shiptypedesc,
    m.longitude AS curlongitude,
    m.latitude AS curlatitude,
    m.distance_to_centroid_m,
    m.tscurrent,
    m.tsstop,
    m.activity_id,
    a.cursog AS sog,
    a.curcog AS cog
FROM public.ais_vesselproximitymember m
LEFT JOIN public.ais_vesselmovementactivities a ON a.id = m.activity_id
WHERE m.observation_id = ANY(%(obs_ids)s)
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


def polygon_to_geojson(area: dict) -> dict:
    """Convert anchorage_areas entry to a GeoJSON Polygon geometry."""
    ring = [[lon, lat] for lon, lat in area["polygon"]]
    if ring[0] != ring[-1]:
        ring.append(ring[0])
    return {"type": "Polygon", "coordinates": [ring]}


def _fmt_ts(value: Any) -> str | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if pd.isna(value):
        return None
    return str(value)


def _fmt_duration(seconds: Any) -> dict[str, Any]:
    if seconds is None or (isinstance(seconds, float) and pd.isna(seconds)):
        return {"durationSeconds": None, "durationHours": None, "durationLabel": None}
    sec = float(seconds)
    hours = sec / 3600.0
    h = int(hours)
    m = int((sec % 3600) // 60)
    return {
        "durationSeconds": round(sec, 3),
        "durationHours": round(hours, 4),
        "durationLabel": f"{h}h {m}m",
    }


def load_active_high_score_observations(
    engine: Engine | None = None,
    min_suspicion_score: float = MIN_SUSPICION_SCORE,
) -> pd.DataFrame:
    engine = engine or get_pg_engine()
    return pd.read_sql(
        ACTIVE_HIGH_SCORE_SQL,
        con=engine,
        params={"min_score": float(min_suspicion_score)},
    )


def load_members(
    observation_ids: list[int],
    engine: Engine | None = None,
) -> pd.DataFrame:
    engine = engine or get_pg_engine()
    if not observation_ids:
        return pd.DataFrame()

    return pd.read_sql(
        MEMBERS_SQL,
        con=engine,
        params={"obs_ids": observation_ids},
    )


def filter_observations_in_anchorages(
    observations: pd.DataFrame,
    areas: list[dict] | None = None,
) -> pd.DataFrame:
    """
    Keep open clusters whose centroid falls inside a parent polygon.
    Drop clusters whose centroid is also inside an Excl carve-out.
    """
    areas = areas or anchorage_areas
    if observations.empty:
        return observations.assign(anchorage_name=pd.Series(dtype="object"))

    _ensure_duckdb_spatial()
    df_poly = pd.DataFrame([
        {
            "anchorage_name": area["name"],
            "is_excl": is_excl_name(area["name"]),
            "geojson": json.dumps(polygon_to_geojson(area)),
        }
        for area in areas
    ])

    duckdb.register("observations", observations)
    duckdb.register("anchorage_polys", df_poly)

    out = duckdb.sql(
        """
        WITH hits AS (
            SELECT
                o.*,
                p.anchorage_name,
                p.is_excl
            FROM observations o
            CROSS JOIN anchorage_polys p
            WHERE ST_Within(
                ST_Point(o.centroid_longitude, o.centroid_latitude),
                ST_GeomFromGeoJSON(p.geojson)
            )
        ),
        excl_ids AS (
            SELECT DISTINCT observation_id
            FROM hits
            WHERE is_excl
        )
        SELECT h.* EXCLUDE (is_excl)
        FROM hits h
        LEFT JOIN excl_ids e ON e.observation_id = h.observation_id
        WHERE e.observation_id IS NULL
          AND NOT h.is_excl
        """
    ).df()

    if out.empty:
        return observations.iloc[0:0].assign(anchorage_name=pd.Series(dtype="object"))

    return out.drop_duplicates(subset=["observation_id"], keep="first")


def find_pairs_within_observations(
    observations: pd.DataFrame,
    members: pd.DataFrame,
    max_distance_m: float = MAX_DISTANCE_M,
) -> pd.DataFrame:
    """
    Recompute unordered pairs among members of the same observation
    with distance < max_distance_m (default 35 m).
    """
    empty_cols = [
        "observation_id", "anchorage_name", "suspicion_score", "duration_seconds",
        "first_detected_at", "last_detected_at", "mmsi_a", "mmsi_b", "distance_m",
        "shipname_a", "shipname_b", "shiptype_a", "shiptype_b",
        "lon_a", "lat_a", "lon_b", "lat_b", "sog_a", "sog_b", "cog_a", "cog_b",
        "tscurrent_a", "tscurrent_b", "tsstop_a", "tsstop_b",
    ]
    if observations.empty or members.empty or len(members) < 2:
        return pd.DataFrame(columns=empty_cols)

    _ensure_duckdb_spatial()

    obs_cols = observations[
        [
            "observation_id",
            "anchorage_name",
            "suspicion_score",
            "duration_seconds",
            "first_detected_at",
            "last_detected_at",
            "vessel_count",
            "cargo_count",
            "tanker_count",
        ]
    ]
    mem = members.rename(columns={
        "curlongitude": "lon",
        "curlatitude": "lat",
    })

    duckdb.register("members_for_pairs", mem)
    raw_pairs = duckdb.sql(
        f"""
        SELECT
            a.observation_id AS observation_id,
            a.mmsi AS mmsi_a,
            b.mmsi AS mmsi_b,
            ST_Distance_Sphere(
                ST_Point(a.lon, a.lat),
                ST_Point(b.lon, b.lat)
            ) AS distance_m,
            a.shipname AS shipname_a,
            b.shipname AS shipname_b,
            a.shiptype AS shiptype_a,
            b.shiptype AS shiptype_b,
            a.lon AS lon_a,
            a.lat AS lat_a,
            b.lon AS lon_b,
            b.lat AS lat_b,
            a.sog AS sog_a,
            b.sog AS sog_b,
            a.cog AS cog_a,
            b.cog AS cog_b,
            a.tscurrent AS tscurrent_a,
            b.tscurrent AS tscurrent_b,
            a.tsstop AS tsstop_a,
            b.tsstop AS tsstop_b
        FROM members_for_pairs a
        INNER JOIN members_for_pairs b
          ON a.observation_id = b.observation_id
         AND a.mmsi < b.mmsi
        WHERE ST_Distance_Sphere(
                ST_Point(a.lon, a.lat),
                ST_Point(b.lon, b.lat)
              ) < {max_distance_m}
        """
    ).df()

    if raw_pairs.empty:
        return pd.DataFrame(columns=empty_cols)

    pairs = raw_pairs.merge(obs_cols, on="observation_id", how="inner")
    return pairs.sort_values(
        ["suspicion_score", "distance_m"],
        ascending=[False, True],
    ).reset_index(drop=True)


def paired_vessels_only(pairs: pd.DataFrame, members: pd.DataFrame) -> pd.DataFrame:
    """Return member rows that appear in at least one STS pair (for map markers)."""
    if pairs.empty or members.empty:
        return members.iloc[0:0].copy() if not members.empty else members

    paired_mmsis = set(pairs["mmsi_a"].tolist()) | set(pairs["mmsi_b"].tolist())
    obs_ids = set(pairs["observation_id"].tolist())

    out = members[
        members["observation_id"].isin(obs_ids) & members["mmsi"].isin(paired_mmsis)
    ].copy()

    meta = pairs[
        ["observation_id", "anchorage_name", "suspicion_score", "duration_seconds",
         "first_detected_at", "last_detected_at"]
    ].drop_duplicates("observation_id")
    out = out.merge(meta, on="observation_id", how="left")
    return out.drop_duplicates(subset=["observation_id", "mmsi"], keep="first")


def pairs_to_payload(pairs: pd.DataFrame) -> list[dict[str, Any]]:
    """API-shaped pair records with the fields requested for visualization/clients."""
    records: list[dict[str, Any]] = []
    if pairs.empty:
        return records

    for _, p in pairs.iterrows():
        duration = _fmt_duration(p.get("duration_seconds"))
        records.append({
            "observationId": int(p["observation_id"]),
            "anchorageName": p.get("anchorage_name"),
            "suspicionScore": float(p["suspicion_score"]) if pd.notna(p.get("suspicion_score")) else None,
            "distanceM": round(float(p["distance_m"]), 3),
            "durationSeconds": duration["durationSeconds"],
            "durationHours": duration["durationHours"],
            "durationLabel": duration["durationLabel"],
            "pairedAt": _fmt_ts(p.get("last_detected_at")),
            "firstDetectedAt": _fmt_ts(p.get("first_detected_at")),
            "vesselA": {
                "mmsi": int(p["mmsi_a"]),
                "shipName": p.get("shipname_a"),
                "latitude": float(p["lat_a"]),
                "longitude": float(p["lon_a"]),
                "sog": float(p["sog_a"]) if pd.notna(p.get("sog_a")) else None,
                "cog": float(p["cog_a"]) if pd.notna(p.get("cog_a")) else None,
            },
            "vesselB": {
                "mmsi": int(p["mmsi_b"]),
                "shipName": p.get("shipname_b"),
                "latitude": float(p["lat_b"]),
                "longitude": float(p["lon_b"]),
                "sog": float(p["sog_b"]) if pd.notna(p.get("sog_b")) else None,
                "cog": float(p["cog_b"]) if pd.notna(p.get("cog_b")) else None,
            },
        })
    return records


def vessels_to_payload(vessels: pd.DataFrame) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    if vessels.empty:
        return records

    for _, v in vessels.iterrows():
        duration = _fmt_duration(v.get("duration_seconds"))
        records.append({
            "observationId": int(v["observation_id"]),
            "mmsi": int(v["mmsi"]),
            "shipName": v.get("shipname"),
            "latitude": float(v["curlatitude"]),
            "longitude": float(v["curlongitude"]),
            "sog": float(v["sog"]) if pd.notna(v.get("sog")) else None,
            "cog": float(v["cog"]) if pd.notna(v.get("cog")) else None,
            "anchorageName": v.get("anchorage_name"),
            "suspicionScore": float(v["suspicion_score"]) if pd.notna(v.get("suspicion_score")) else None,
            "durationSeconds": duration["durationSeconds"],
            "durationHours": duration["durationHours"],
            "durationLabel": duration["durationLabel"],
            "pairedAt": _fmt_ts(v.get("last_detected_at")),
            "firstDetectedAt": _fmt_ts(v.get("first_detected_at")),
        })
    return records


def detect_sts_in_anchorages(
    engine: Engine | None = None,
    min_suspicion_score: float = MIN_SUSPICION_SCORE,
    max_distance_m: float = MAX_DISTANCE_M,
) -> dict[str, Any]:
    """
    Active high-suspicion proximity clusters inside anchorage polygons.

    Returns only pairing vessels (not all vessels in the anchorage).
    """
    engine = engine or get_pg_engine()
    observations = load_active_high_score_observations(engine, min_suspicion_score)
    in_anchorage = filter_observations_in_anchorages(observations)

    obs_ids = in_anchorage["observation_id"].astype(int).tolist() if not in_anchorage.empty else []
    members = load_members(obs_ids, engine)
    pairs = find_pairs_within_observations(in_anchorage, members, max_distance_m)
    paired_vessels = paired_vessels_only(pairs, members)

    return {
        "open_high_score_count": int(len(observations)),
        "in_anchorage_cluster_count": int(len(in_anchorage)),
        "pair_count": int(len(pairs)),
        "paired_vessel_count": int(len(paired_vessels)),
        "max_distance_m": float(max_distance_m),
        "min_suspicion_score": float(min_suspicion_score),
        "observations": in_anchorage,
        "pairs": pairs,
        "paired_vessels": paired_vessels,
        "pairs_payload": pairs_to_payload(pairs),
        "paired_vessels_payload": vessels_to_payload(paired_vessels),
        # Back-compat keys
        "candidate_count": int(len(observations)),
        "in_anchorage_count": int(len(paired_vessels)),
        "vessels_in_anchorage": paired_vessels,
    }


# Kept for callers that still import the old helpers
def pairs_to_records(pairs: pd.DataFrame) -> list[dict[str, Any]]:
    return pairs_to_payload(pairs)


def vessels_to_records(vessels: pd.DataFrame) -> list[dict[str, Any]]:
    return vessels_to_payload(vessels)
