from typing import Optional
from urllib.parse import quote, urlencode
from urllib.request import urlopen
from datetime import datetime, timezone
from collections import defaultdict
from pathlib import Path

from sqlmodel import Field, SQLModel, create_engine, Session, select
from sqlalchemy import Column, BigInteger, Text, text
from sqlalchemy.engine import Engine

import json
import logging
import math
import sys
import time
import pandas as pd
import duckdb

"""
Detect cargo/tanker vessels within MAX_DISTANCE_M, group them into proximity clusters,
track open/close lifecycle with duration and suspicion scoring, and persist to PostgreSQL.

Runs on a fixed interval; each cluster is one open observation until it disappears
beyond CLOSE_GRACE_SECONDS.
"""


logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

DETECTION_VERSION = "2.0"
MAX_DISTANCE_M = 30.0
LOOP_INTERVAL_SECONDS = 30
CLOSE_GRACE_SECONDS = 60
GEOCODE_URL = "https://api.bigdatacloud.net/data/reverse-geocode-client"

EXPORT_DIR = Path(__file__).resolve().parent / "data" / "ml_export"
MIN_EXPORT_DURATION_SECONDS = 0.0
EXPORT_CLOSED_ONLY_DEFAULT = True
EXPORT_LABELED_ONLY_DEFAULT = False


duckdb.sql("INSTALL spatial")
duckdb.sql("LOAD spatial")
duckdb.sql("PRAGMA threads=2")


pswd = "m4r1t1m3"
encoded_password = quote(pswd)
DATABASE_URL = (
    f"postgresql://postgresadmin:{encoded_password}"
    f"@marineai2.cxwk8yige5f2.ap-southeast-5.rds.amazonaws.com:5432/pnav"
)


# ---------------------------------------------------------------------------
# SQLModel tables
# ---------------------------------------------------------------------------

class Ais_VesselProximityObservation(SQLModel, table=True):
    """One proximity cluster event: lifecycle, location, scoring, and ML labels."""
    id: Optional[int] = Field(default=None, sa_column=Column(BigInteger, primary_key=True))
    cluster_signature: Optional[str] = Field(default=None, index=True)

    detected_at: datetime
    first_detected_at: Optional[datetime] = Field(default=None)
    last_detected_at: Optional[datetime] = Field(default=None)
    duration_seconds: Optional[float] = Field(default=None)

    is_open: bool = Field(default=True, index=True)
    closed_at: Optional[datetime] = Field(default=None)
    close_reason: Optional[str] = Field(default=None)

    suspicion_score: Optional[float] = Field(default=None)
    run_count: int = Field(default=1)

    city: Optional[str] = Field(default=None)
    locality: Optional[str] = Field(default=None)
    locality_code: Optional[str] = Field(default=None)
    zone_id: Optional[int] = Field(default=None, sa_column=Column(BigInteger))

    centroid_longitude: Optional[float] = Field(default=None)
    centroid_latitude: Optional[float] = Field(default=None)
    vessel_count: int
    max_internal_distance_m: Optional[float] = Field(default=None)
    threshold_m: float = Field(default=MAX_DISTANCE_M)
    cargo_count: Optional[int] = Field(default=None)
    tanker_count: Optional[int] = Field(default=None)

    is_anomaly: Optional[bool] = Field(default=None)
    anomaly_source: Optional[str] = Field(default=None)
    anomaly_notes: Optional[str] = Field(default=None, sa_column=Column(Text))
    labeled_at: Optional[datetime] = Field(default=None)
    labeled_by: Optional[str] = Field(default=None)

    detection_version: Optional[str] = Field(default=None)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class Ais_VesselProximityMember(SQLModel, table=True):
    """Per-vessel snapshot belonging to a proximity observation."""
    id: Optional[int] = Field(default=None, sa_column=Column(BigInteger, primary_key=True))
    observation_id: int = Field(sa_column=Column(BigInteger, index=True))
    mmsi: int = Field(sa_column=Column(BigInteger, index=True))

    ship_name: Optional[str] = Field(default=None)
    ship_type: Optional[int] = Field(default=None)
    ship_type_desc: Optional[str] = Field(default=None)

    longitude: float
    latitude: float
    activity_id: Optional[int] = Field(default=None, sa_column=Column(BigInteger))
    tscurrent: Optional[datetime] = Field(default=None)
    tsstop: Optional[datetime] = Field(default=None)
    distance_to_centroid_m: Optional[float] = Field(default=None)


class Ais_VesselProximityEdge(SQLModel, table=True):
    """Pairwise distance between two vessels inside the same cluster."""
    observation_id: int = Field(sa_column=Column(BigInteger, primary_key=True))
    mmsi_a: int = Field(sa_column=Column(BigInteger, primary_key=True))
    mmsi_b: int = Field(sa_column=Column(BigInteger, primary_key=True))
    distance_m: float


OBSERVATION_MIGRATION_COLUMNS = [
    # Columns added after initial deploy; applied via ALTER TABLE IF NOT EXISTS
    ("cluster_signature", "VARCHAR(255)"),
    ("first_detected_at", "TIMESTAMPTZ"),
    ("last_detected_at", "TIMESTAMPTZ"),
    ("duration_seconds", "DOUBLE PRECISION"),
    ("is_open", "BOOLEAN DEFAULT TRUE"),
    ("closed_at", "TIMESTAMPTZ"),
    ("close_reason", "VARCHAR(50)"),
    ("suspicion_score", "DOUBLE PRECISION"),
    ("run_count", "INTEGER DEFAULT 1"),
]


# ---------------------------------------------------------------------------
# Database setup
# ---------------------------------------------------------------------------

def get_pgEngine() -> Engine:
    """Create and return a pooled SQLAlchemy engine for PostgreSQL."""
    return create_engine(
        DATABASE_URL,
        pool_size=10,
        max_overflow=20,
        pool_timeout=30,
    )



def create_db_and_tables(engine: Engine):
    """Create proximity tables, migrate new columns, and ensure indexes exist."""
    SQLModel.metadata.create_all(engine)

    with engine.connect() as conn:
        for col, col_type in OBSERVATION_MIGRATION_COLUMNS:
            conn.execute(text(
                f"ALTER TABLE ais_vesselproximityobservation "
                f"ADD COLUMN IF NOT EXISTS {col} {col_type}"
            ))

        conn.execute(text("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_prox_obs_open_signature
            ON ais_vesselproximityobservation (cluster_signature)
            WHERE is_open = TRUE
        """))

        conn.execute(text("""
            UPDATE ais_vesselproximityobservation
            SET is_open = FALSE, close_reason = 'legacy_append'
            WHERE cluster_signature IS NULL
              AND (is_open IS NULL OR is_open = TRUE)
        """))

        conn.commit()


# ---------------------------------------------------------------------------
# Cluster detection (pairs → connected components)
# ---------------------------------------------------------------------------

class UnionFind:
    """Disjoint-set structure to merge vessels linked by close pairs into clusters."""

    def __init__(self):
        self.parent: dict[int, int] = {}


    def find(self, x: int) -> int:
        """Return the root representative for MMSI x, with path compression."""
        if x not in self.parent:
            self.parent[x] = x

        if self.parent[x] != x:
            self.parent[x] = self.find(self.parent[x])

        return self.parent[x]


    def union(self, x: int, y: int):
        """Merge the sets containing MMSI x and MMSI y."""
        px, py = self.find(x), self.find(y)

        if px != py:
            self.parent[px] = py



def cluster_signature(mmsi_list: list[int]) -> str:
    """Build a stable cluster id from sorted MMSI values (e.g. 123_456)."""
    return "_".join(str(m) for m in sorted(mmsi_list))



def compute_suspicion_score(duration_seconds: float, vessel_count: int, cargo_count: int, tanker_count: int) -> float:
    """Score cluster suspiciousness from duration, size, and cargo/tanker mix."""
    duration_hours = duration_seconds / 3600.0
    mixed_cargo_tanker = 1 if cargo_count > 0 and tanker_count > 0 else 0

    score = (
        2.0 * math.log1p(duration_hours)
        + 1.0 * (vessel_count - 1)
        + 1.5 * mixed_cargo_tanker
    )

    return round(score, 4)



def find_close_pairs(df: pd.DataFrame, max_distance_m: float = MAX_DISTANCE_M) -> pd.DataFrame:
    """Return all unordered vessel pairs closer than max_distance_m using DuckDB."""
    # Register only numeric columns; tz-aware timestamp columns can break DuckDB's scan.
    slim = df[["mmsi", "curlongitude", "curlatitude"]].copy()
    duckdb.register("vessels", slim)

    return duckdb.sql(f"""
        SELECT
            a.mmsi AS mmsi_a,
            b.mmsi AS mmsi_b,
            ST_Distance_Sphere(
                ST_Point(a.curlongitude, a.curlatitude),
                ST_Point(b.curlongitude, b.curlatitude)
            ) AS distance_m
        FROM vessels a
        INNER JOIN vessels b ON a.mmsi < b.mmsi
        WHERE ST_Distance_Sphere(
                ST_Point(a.curlongitude, a.curlatitude),
                ST_Point(b.curlongitude, b.curlatitude)
            ) < {max_distance_m}
    """).df()



def build_clusters(pairs: pd.DataFrame) -> list[list[int]]:
    """Group close pairs into connected components (clusters of 2+ vessels)."""
    if pairs.empty:
        return []

    uf = UnionFind()

    for _, row in pairs.iterrows():
        uf.union(int(row["mmsi_a"]), int(row["mmsi_b"]))

    groups: dict[int, set[int]] = defaultdict(set)

    for _, row in pairs.iterrows():
        for mmsi in (int(row["mmsi_a"]), int(row["mmsi_b"])):
            groups[uf.find(mmsi)].add(mmsi)

    return [sorted(mmsis) for mmsis in groups.values() if len(mmsis) >= 2]


# ---------------------------------------------------------------------------
# Geocoding
# ---------------------------------------------------------------------------

def reverse_geocode(latitude: float, longitude: float) -> dict:
    """Call BigDataCloud API to resolve city and locality from coordinates."""
    params = urlencode({
        "latitude": latitude,
        "longitude": longitude,
        "localityLanguage": "en",
    })
    url = f"{GEOCODE_URL}?{params}"

    with urlopen(url, timeout=15) as resp:
        return json.loads(resp.read().decode())



def make_locality_code(data: dict) -> str:
    """Build a stable locality code from reverse-geocode response fields."""
    parts = []

    if data.get("countryCode"):
        parts.append(data["countryCode"])

    if data.get("principalSubdivisionCode"):
        parts.append(data["principalSubdivisionCode"])

    city = (data.get("city") or "").strip()
    locality = (data.get("locality") or "").strip()

    if city:
        parts.append(city.replace(" ", "_").upper()[:40])
    elif locality:
        parts.append(locality.replace(" ", "_").upper()[:40])

    if parts:
        return "_".join(parts)

    if data.get("plusCode"):
        return data["plusCode"]

    return "UNKNOWN"



def geocode_centroid(lat: float, lon: float, cache: dict[tuple[float, float], dict]) -> dict:
    """Reverse-geocode a centroid; cache by coords rounded to 4 decimal places."""
    key = (round(lat, 4), round(lon, 4))

    if key not in cache:
        try:
            # cache[key] = reverse_geocode(lat, lon)
            cache[key] = {}
            
        except Exception as e:
            logging.warning("Reverse geocode failed for (%s, %s): %s", lat, lon, e)
            cache[key] = {}

    return cache[key]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def sphere_distance_m(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    """Compute great-circle distance in meters between two lon/lat points."""
    result = duckdb.sql(f"""
        SELECT ST_Distance_Sphere(
            ST_Point({lon1}, {lat1}),
            ST_Point({lon2}, {lat2})
        ) AS d
    """).fetchone()

    return float(result[0])



def _clean_optional(value):
    """Convert pandas NA/NaN to None for optional DB fields."""
    if value is None:
        return None

    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass

    return value



def _as_utc(dt: datetime) -> datetime:
    """Normalize a datetime to timezone-aware UTC."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)

    return dt.astimezone(timezone.utc)



def count_ship_types(members: pd.DataFrame) -> tuple[int, int]:
    """Count cargo (70-79) and tanker (80-89) vessels in a member dataframe."""
    cargo = int(((members["shipType"] >= 70) & (members["shipType"] < 80)).sum())
    tanker = int(((members["shipType"] >= 80) & (members["shipType"] < 90)).sum())

    return cargo, tanker


# ---------------------------------------------------------------------------
# Persist open clusters (upsert + close)
# ---------------------------------------------------------------------------

def _max_internal_distance_m(members: pd.DataFrame) -> float:
    """Largest pairwise distance among cluster members (includes transitive clusters)."""
    if len(members) < 2:
        return 0.0

    duckdb.register("cluster_members", members[["mmsi", "curlongitude", "curlatitude"]])

    result = duckdb.sql("""
        SELECT MAX(
            ST_Distance_Sphere(
                ST_Point(a.curlongitude, a.curlatitude),
                ST_Point(b.curlongitude, b.curlatitude)
            )
        ) AS max_dist
        FROM cluster_members a
        INNER JOIN cluster_members b ON a.mmsi < b.mmsi
    """).fetchone()

    return float(result[0] or 0.0)



def _compute_cluster_metrics(mmsi_list: list[int], pairs: pd.DataFrame, vessels: pd.DataFrame, geocode_cache: dict) -> dict:
    """Compute centroid, spread, locality, and member data for one cluster."""
    members = vessels.drop_duplicates(subset="mmsi", keep="first").set_index("mmsi").loc[mmsi_list].reset_index()

    centroid_lon = float(members["curlongitude"].mean())
    centroid_lat = float(members["curlatitude"].mean())

    cluster_pairs = pairs[
        pairs["mmsi_a"].isin(mmsi_list) & pairs["mmsi_b"].isin(mmsi_list)
    ]
    max_dist = _max_internal_distance_m(members)

    geo = geocode_centroid(centroid_lat, centroid_lon, geocode_cache)
    cargo_count, tanker_count = count_ship_types(members)

    return {
        "members": members,
        "cluster_pairs": cluster_pairs,
        "centroid_lon": centroid_lon,
        "centroid_lat": centroid_lat,
        "max_dist": max_dist,
        "city": (geo.get("city") or "").strip() or None,
        "locality": (geo.get("locality") or "").strip() or None,
        "locality_code": make_locality_code(geo) if geo else None,
        "cargo_count": cargo_count,
        "tanker_count": tanker_count,
    }



def _replace_members_and_edges(session: Session, observation_id: int, members: pd.DataFrame, cluster_pairs: pd.DataFrame, centroid_lon: float, centroid_lat: float):
    """Replace member and edge rows with the latest snapshot for an observation."""
    for row in session.exec(
        select(Ais_VesselProximityMember).where(
            Ais_VesselProximityMember.observation_id == observation_id
        )
    ).all():
        session.delete(row)

    for row in session.exec(
        select(Ais_VesselProximityEdge).where(
            Ais_VesselProximityEdge.observation_id == observation_id
        )
    ).all():
        session.delete(row)

    for _, row in members.iterrows():
        dist_to_centroid = sphere_distance_m(
            centroid_lon, centroid_lat,
            float(row["curlongitude"]), float(row["curlatitude"]),
        )

        session.add(Ais_VesselProximityMember(
            observation_id=observation_id,
            mmsi=int(row["mmsi"]),
            ship_name=_clean_optional(row.get("shipName")),
            ship_type=int(row["shipType"]) if pd.notna(row.get("shipType")) else None,
            ship_type_desc=_clean_optional(row.get("shipTypeDesc")),
            longitude=float(row["curlongitude"]),
            latitude=float(row["curlatitude"]),
            activity_id=int(row["id"]) if pd.notna(row.get("id")) else None,
            tscurrent=_clean_optional(row.get("tscurrent")),
            tsstop=_clean_optional(row.get("tsstop")),
            distance_to_centroid_m=dist_to_centroid,
        ))

    for _, edge in cluster_pairs.iterrows():
        session.add(Ais_VesselProximityEdge(
            observation_id=observation_id,
            mmsi_a=int(edge["mmsi_a"]),
            mmsi_b=int(edge["mmsi_b"]),
            distance_m=float(edge["distance_m"]),
        ))



def _apply_cluster_metrics_to_obs(obs: Ais_VesselProximityObservation, metrics: dict, detected_at: datetime, max_distance_m: float):
    """Update observation fields, duration, and suspicion score from cluster metrics."""
    if obs.first_detected_at is None:
        obs.first_detected_at = detected_at

    duration = (_as_utc(detected_at) - _as_utc(obs.first_detected_at)).total_seconds()

    obs.last_detected_at = detected_at
    obs.duration_seconds = max(duration, 0.0)

    obs.city = metrics["city"]
    obs.locality = metrics["locality"]
    obs.locality_code = metrics["locality_code"]

    obs.centroid_longitude = metrics["centroid_lon"]
    obs.centroid_latitude = metrics["centroid_lat"]
    obs.vessel_count = len(metrics["members"])
    obs.max_internal_distance_m = metrics["max_dist"]
    obs.threshold_m = max_distance_m
    obs.cargo_count = metrics["cargo_count"]
    obs.tanker_count = metrics["tanker_count"]
    obs.detection_version = DETECTION_VERSION

    obs.suspicion_score = compute_suspicion_score(
        obs.duration_seconds,
        obs.vessel_count,
        metrics["cargo_count"],
        metrics["tanker_count"],
    )



def _close_observation(obs: Ais_VesselProximityObservation, closed_at: datetime, reason: str):
    """Mark an observation closed and freeze its final duration."""
    obs.is_open = False
    obs.closed_at = closed_at
    obs.close_reason = reason

    if obs.first_detected_at and obs.last_detected_at:
        obs.duration_seconds = (
            _as_utc(obs.last_detected_at) - _as_utc(obs.first_detected_at)
        ).total_seconds()



def upsert_open_clusters(engine: Engine, clusters: list[list[int]], pairs: pd.DataFrame, vessels: pd.DataFrame, detected_at: datetime, max_distance_m: float = MAX_DISTANCE_M) -> dict:
    """Open or update active clusters; close stale ones missing beyond the grace period."""
    geocode_cache: dict[tuple[float, float], dict] = {}
    current_signatures = {cluster_signature(m) for m in clusters}

    touched_ids: list[int] = []
    opened = 0
    updated = 0
    closed = 0

    with Session(engine) as session:
        open_by_sig = {
            obs.cluster_signature: obs
            for obs in session.exec(
                select(Ais_VesselProximityObservation).where(
                    Ais_VesselProximityObservation.is_open == True  # noqa: E712
                )
            ).all()
            if obs.cluster_signature
        }

        for mmsi_list in clusters:
            sig = cluster_signature(mmsi_list)
            metrics = _compute_cluster_metrics(mmsi_list, pairs, vessels, geocode_cache)
            existing = open_by_sig.get(sig)

            if existing:
                existing.run_count += 1
                _apply_cluster_metrics_to_obs(existing, metrics, detected_at, max_distance_m)
                _replace_members_and_edges(
                    session, existing.id, metrics["members"],
                    metrics["cluster_pairs"], metrics["centroid_lon"], metrics["centroid_lat"],
                )

                touched_ids.append(existing.id)
                updated += 1

                logging.info(
                    "Updated cluster %s: %s vessels, duration %.0fs, score %.2f",
                    sig, existing.vessel_count, existing.duration_seconds or 0,
                    existing.suspicion_score or 0,
                )

            else:
                obs = Ais_VesselProximityObservation(
                    cluster_signature=sig,
                    detected_at=detected_at,
                    first_detected_at=detected_at,
                    last_detected_at=detected_at,
                    duration_seconds=0.0,
                    is_open=True,
                    run_count=1,
                    vessel_count=len(mmsi_list),
                    threshold_m=max_distance_m,
                    detection_version=DETECTION_VERSION,
                )

                _apply_cluster_metrics_to_obs(obs, metrics, detected_at, max_distance_m)
                session.add(obs)
                session.flush()

                _replace_members_and_edges(
                    session, obs.id, metrics["members"],
                    metrics["cluster_pairs"], metrics["centroid_lon"], metrics["centroid_lat"],
                )

                open_by_sig[sig] = obs
                touched_ids.append(obs.id)
                opened += 1

                logging.info(
                    "Opened cluster %s: %s vessels, score %.2f",
                    sig, obs.vessel_count, obs.suspicion_score or 0,
                )

        for sig, obs in open_by_sig.items():
            if sig in current_signatures:
                continue

            last_seen = obs.last_detected_at or obs.first_detected_at or detected_at
            elapsed = (_as_utc(detected_at) - _as_utc(last_seen)).total_seconds()

            if elapsed >= CLOSE_GRACE_SECONDS:
                _close_observation(obs, detected_at, "not_seen")
                closed += 1

                logging.info(
                    "Closed cluster %s after %.0fs unseen (duration %.0fs, score %.2f)",
                    sig, elapsed, obs.duration_seconds or 0, obs.suspicion_score or 0,
                )

        session.commit()

    return {
        "observation_ids": touched_ids,
        "opened": opened,
        "updated": updated,
        "closed": closed,
    }


# ---------------------------------------------------------------------------
# Data loading and main loop
# ---------------------------------------------------------------------------

def load_candidate_vessels(engine: Engine) -> pd.DataFrame:
    """Load stopped/stale cargo and tanker vessels eligible for proximity detection."""
    static_query = """
        SELECT mmsi, "shipType", "shipTypeDesc", "shipName"
        FROM public.ais_static
    """
    df_static = pd.read_sql(static_query, con=engine)
    df_static = df_static.drop_duplicates(subset="mmsi", keep="first")

    for col in ("shipTypeDesc", "shipName"):
        df_static[col] = df_static[col].astype("object")

    activity_query = """
        SELECT *
        FROM (
            SELECT *, row_number() OVER (PARTITION BY mmsi ORDER BY ts DESC) AS rowcount_mmsi
            FROM public.ais_vesselmovementactivities
        ) sub
        WHERE tsout IS NULL
          AND (
                (tsstop IS NOT NULL AND tsstop <= now() - interval '1 HOURS')
                OR tscurrent <= now() - interval '30 MINUTES'
              )
          AND rowcount_mmsi = 1
    """
    df = pd.read_sql(activity_query, con=engine)
    df["navstatusdesc"] = df["navstatusdesc"].astype("object")

    df = df.merge(df_static, on="mmsi", how="inner")
    df = df[(df["shipType"] >= 70) & (df["shipType"] < 90)]
    df = df.dropna(subset=["curlongitude", "curlatitude"])
    df = df.drop_duplicates(subset="mmsi", keep="first")

    return df



def run_proximity_detection(engine: Engine | None = None) -> dict:
    """Run one detection cycle: find clusters, upsert DB rows, return run stats."""
    engine = engine or get_pgEngine()

    detected_at = datetime.now(timezone.utc)
    df = load_candidate_vessels(engine)

    if df.empty:
        result = upsert_open_clusters(engine, [], pd.DataFrame(), df, detected_at)

        logging.info(
            "No cargo/tanker candidate vessels found. Closed %s stale cluster(s).",
            result["closed"],
        )

        return {
            "vessels_checked": 0,
            "pairs": 0,
            "clusters": 0,
            **result,
        }

    pairs = find_close_pairs(df, MAX_DISTANCE_M)
    clusters = build_clusters(pairs)

    if not clusters:
        result = upsert_open_clusters(engine, [], pairs, df, detected_at)

        logging.info(
            "No proximity clusters within %s m (%s vessels checked). Closed %s stale cluster(s).",
            MAX_DISTANCE_M, len(df), result["closed"],
        )

        return {
            "vessels_checked": len(df),
            "pairs": 0,
            "clusters": 0,
            **result,
        }

    result = upsert_open_clusters(engine, clusters, pairs, df, detected_at, MAX_DISTANCE_M)

    logging.info(
        "Clusters: %s open/updated, %s new, %s closed (%s pair edges, %s vessels checked).",
        result["updated"], result["opened"], result["closed"], len(pairs), len(df),
    )

    return {
        "vessels_checked": len(df),
        "pairs": len(pairs),
        "clusters": len(clusters),
        **result,
    }



def log_cluster_summary(engine: Engine, observation_ids: list[int]):
    """Log a readable summary of observations touched in the latest cycle."""
    if not observation_ids:
        return

    safe_ids = [int(i) for i in observation_ids]
    ids_csv = ",".join(str(i) for i in safe_ids)

    summary = pd.read_sql(f"""
        SELECT
            o.id,
            o.is_open,
            o.first_detected_at,
            o.last_detected_at,
            o.duration_seconds,
            o.suspicion_score,
            o.run_count,
            o.city,
            o.locality,
            o.vessel_count,
            o.cargo_count,
            o.tanker_count,
            array_agg(m.mmsi ORDER BY m.mmsi) AS mmsi_list,
            array_agg(m.ship_name ORDER BY m.mmsi) AS name_list
        FROM ais_vesselproximityobservation o
        JOIN ais_vesselproximitymember m ON m.observation_id = o.id
        WHERE o.id IN ({ids_csv})
        GROUP BY o.id
        ORDER BY o.suspicion_score DESC NULLS LAST, o.duration_seconds DESC
    """, con=engine)

    logging.info(
        "Active/updated %s cluster observation(s):\n%s",
        len(summary),
        summary.to_string(index=False),
    )



# ---------------------------------------------------------------------------
# ML dataset export (Parquet)
# ---------------------------------------------------------------------------

def _build_export_filters(closed_only: bool, labeled_only: bool, min_duration_seconds: float) -> tuple[str, dict]:
    """Build SQL WHERE clause and bind parameters for ML export queries."""
    conditions = ["1=1"]
    params: dict = {}

    if closed_only:
        conditions.append("o.is_open = FALSE")

    if labeled_only:
        conditions.append("o.is_anomaly IS NOT NULL")

    if min_duration_seconds > 0:
        conditions.append("o.duration_seconds >= :min_duration_seconds")
        params["min_duration_seconds"] = min_duration_seconds

    return " AND ".join(conditions), params



def load_ml_observations(engine: Engine, closed_only: bool = EXPORT_CLOSED_ONLY_DEFAULT, labeled_only: bool = EXPORT_LABELED_ONLY_DEFAULT, min_duration_seconds: float = MIN_EXPORT_DURATION_SECONDS) -> pd.DataFrame:
    """Load cluster-level rows for ML (one row per observation)."""
    where_sql, params = _build_export_filters(closed_only, labeled_only, min_duration_seconds)

    query = text(f"""
        SELECT
            o.id,
            o.cluster_signature,
            o.detected_at,
            o.first_detected_at,
            o.last_detected_at,
            o.duration_seconds,
            o.is_open,
            o.closed_at,
            o.close_reason,
            o.suspicion_score,
            o.run_count,
            o.city,
            o.locality,
            o.locality_code,
            o.zone_id,
            o.centroid_longitude,
            o.centroid_latitude,
            o.vessel_count,
            o.max_internal_distance_m,
            o.threshold_m,
            o.cargo_count,
            o.tanker_count,
            o.is_anomaly,
            o.anomaly_source,
            o.anomaly_notes,
            o.labeled_at,
            o.labeled_by,
            o.detection_version,
            o.created_at,
            array_agg(m.mmsi ORDER BY m.mmsi) AS mmsi_list,
            array_agg(m.ship_name ORDER BY m.mmsi) AS ship_name_list,
            array_agg(m.ship_type ORDER BY m.mmsi) AS ship_type_list,
            array_agg(m.ship_type_desc ORDER BY m.mmsi) AS ship_type_desc_list
        FROM ais_vesselproximityobservation o
        JOIN ais_vesselproximitymember m ON m.observation_id = o.id
        WHERE {where_sql}
        GROUP BY o.id
        ORDER BY o.duration_seconds DESC NULLS LAST, o.id DESC
    """)

    return pd.read_sql(query, con=engine, params=params)



def load_ml_members(engine: Engine, closed_only: bool = EXPORT_CLOSED_ONLY_DEFAULT, labeled_only: bool = EXPORT_LABELED_ONLY_DEFAULT, min_duration_seconds: float = MIN_EXPORT_DURATION_SECONDS) -> pd.DataFrame:
    """Load per-vessel member rows for observations matching export filters."""
    where_sql, params = _build_export_filters(closed_only, labeled_only, min_duration_seconds)

    query = text(f"""
        SELECT
            m.id,
            m.observation_id,
            m.mmsi,
            m.ship_name,
            m.ship_type,
            m.ship_type_desc,
            m.longitude,
            m.latitude,
            m.activity_id,
            m.tscurrent,
            m.tsstop,
            m.distance_to_centroid_m
        FROM ais_vesselproximitymember m
        JOIN ais_vesselproximityobservation o ON o.id = m.observation_id
        WHERE {where_sql}
        ORDER BY m.observation_id, m.mmsi
    """)

    return pd.read_sql(query, con=engine, params=params)



def load_ml_edges(engine: Engine, closed_only: bool = EXPORT_CLOSED_ONLY_DEFAULT, labeled_only: bool = EXPORT_LABELED_ONLY_DEFAULT, min_duration_seconds: float = MIN_EXPORT_DURATION_SECONDS) -> pd.DataFrame:
    """Load pairwise edge rows inside clusters for observations matching export filters."""
    where_sql, params = _build_export_filters(closed_only, labeled_only, min_duration_seconds)

    query = text(f"""
        SELECT
            e.observation_id,
            e.mmsi_a,
            e.mmsi_b,
            e.distance_m
        FROM ais_vesselproximityedge e
        JOIN ais_vesselproximityobservation o ON o.id = e.observation_id
        WHERE {where_sql}
        ORDER BY e.observation_id, e.mmsi_a, e.mmsi_b
    """)

    return pd.read_sql(query, con=engine, params=params)



def export_ml_dataset_parquet(engine: Engine | None = None, output_dir: Path | None = None, closed_only: bool = EXPORT_CLOSED_ONLY_DEFAULT, labeled_only: bool = EXPORT_LABELED_ONLY_DEFAULT, min_duration_seconds: float = MIN_EXPORT_DURATION_SECONDS) -> dict:
    """Export observations, members, and edges to Parquet files for ML training."""
    engine = engine or get_pgEngine()
    output_dir = output_dir or EXPORT_DIR
    output_dir.mkdir(parents=True, exist_ok=True)

    df_obs = load_ml_observations(engine, closed_only, labeled_only, min_duration_seconds)
    df_members = load_ml_members(engine, closed_only, labeled_only, min_duration_seconds)
    df_edges = load_ml_edges(engine, closed_only, labeled_only, min_duration_seconds)

    path_obs = output_dir / "proximity_observations.parquet"
    path_members = output_dir / "proximity_members.parquet"
    path_edges = output_dir / "proximity_edges.parquet"

    df_obs.to_parquet(path_obs, index=False, engine="pyarrow")
    df_members.to_parquet(path_members, index=False, engine="pyarrow")
    df_edges.to_parquet(path_edges, index=False, engine="pyarrow")

    result = {
        "output_dir": str(output_dir),
        "observations_rows": len(df_obs),
        "members_rows": len(df_members),
        "edges_rows": len(df_edges),
        "paths": {
            "observations": str(path_obs),
            "members": str(path_members),
            "edges": str(path_edges),
        },
        "closed_only": closed_only,
        "labeled_only": labeled_only,
        "min_duration_seconds": min_duration_seconds,
    }

    logging.info(
        "ML Parquet export: %s observations, %s members, %s edges -> %s",
        len(df_obs), len(df_members), len(df_edges), output_dir,
    )

    return result



def export_main():
    """CLI entry point: export ML dataset to Parquet (see sys.argv for options)."""
    output_dir = EXPORT_DIR
    closed_only = EXPORT_CLOSED_ONLY_DEFAULT
    labeled_only = EXPORT_LABELED_ONLY_DEFAULT
    min_duration_seconds = MIN_EXPORT_DURATION_SECONDS

    args = sys.argv[2:]

    i = 0
    while i < len(args):
        arg = args[i]

        if arg == "--labeled-only":
            labeled_only = True
            i += 1
            continue

        if arg == "--include-open":
            closed_only = False
            i += 1
            continue

        if arg == "--min-duration" and i + 1 < len(args):
            min_duration_seconds = float(args[i + 1])
            i += 2
            continue

        if arg == "--output-dir" and i + 1 < len(args):
            output_dir = Path(args[i + 1])
            i += 2
            continue

        output_dir = Path(arg)
        i += 1

    try:
        export_ml_dataset_parquet(
            output_dir=output_dir,
            closed_only=closed_only,
            labeled_only=labeled_only,
            min_duration_seconds=min_duration_seconds,
        )
    except ImportError as e:
        logging.error(
            "Parquet export requires pyarrow: pip install pyarrow (%s)", e,
        )
        raise



def main():
    """Run proximity detection continuously every LOOP_INTERVAL_SECONDS until interrupted."""
    engine = get_pgEngine()
    create_db_and_tables(engine)
    run_flg = True

    logging.info(
        "Proximity detection started (interval=%ss, grace=%ss, threshold=%sm).",
        LOOP_INTERVAL_SECONDS, CLOSE_GRACE_SECONDS, MAX_DISTANCE_M,
    )

    while run_flg:
        try:
            result = run_proximity_detection(engine)
            log_cluster_summary(engine, result["observation_ids"])

        except KeyboardInterrupt:
            run_flg = False
            logging.info("Stopping proximity detection.")

        except Exception as e:
            logging.exception("Proximity detection error: %s", e)

        if run_flg:
            logging.info(f'~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~')
            logging.info(f'System sleep....to be resume...')
            logging.info(f'~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~')

            time.sleep(LOOP_INTERVAL_SECONDS)



if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "export":
        export_main()
    else:
        main()
