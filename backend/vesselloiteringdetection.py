"""
Loitering processor.

Reads cargo/tanker tracks from ClickHouse, keeps a vessel that stays inside a
small circle for hours without making a passage, and writes one open row per
MMSI to Postgres. Labels:

- outside_anchorage — centre is not inside a parent anchorage
- near_restricted — centre is inside the restricted limit or within 5 NM
- before_sts — an STS observation for this MMSI starts during the loiter or
  within 6 hours after it, and the STS centroid is within 5 NM

The API only reads ais_vesselloiteractivity. It does not scan ClickHouse.
"""

from __future__ import annotations

import json
import logging
import math
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import quote

import clickhouse_connect
import duckdb
import pandas as pd
from sqlalchemy import Column, BigInteger, text
from sqlalchemy.engine import Engine
from sqlmodel import Field, SQLModel, create_engine

# RESTAPI_DIR = Path(__file__).resolve().parent.parent / "restapi"
# if str(RESTAPI_DIR) not in sys.path:
#     sys.path.insert(0, str(RESTAPI_DIR))

from ais_static_sql import CURRENT_STATIC_ROW_ORDER
from polygons import anchorage_areas, is_excl_name, restricted_limit  # noqa: E402

# Shared restapi helpers. Repo: <root>/backend/… → <root>/restapi; deploy: …/sts/restapi.
_HERE = Path(__file__).resolve().parent
for _restapi in (_HERE / "restapi", _HERE.parent / "restapi"):
    if _restapi.is_dir():
        if str(_restapi) not in sys.path:
            sys.path.insert(0, str(_restapi))
        break
try:
    from ais_identity import VALID_SHIP_MMSI_SQL, is_valid_ship_mmsi  # noqa: E402
except ImportError:  # pragma: no cover - deployed layout with restapi beside sts/
    VALID_SHIP_MMSI_SQL = """
  AND mmsi >= 201000000
  AND mmsi <= 775999999
  AND mod(mmsi, 1000000) <> 0
"""

    def is_valid_ship_mmsi(mmsi) -> bool:  # type: ignore[misc]
        try:
            n = int(mmsi)
        except (TypeError, ValueError):
            return False
        text = str(n)
        return (
            len(text) == 9
            and not text.endswith("000000")
            and 201 <= int(text[:3]) <= 775
        )

try:
    from port_areas import (  # noqa: E402
        annotate_in_port_area,
        apply_port_area_to_outside_anchorage,
        get_port_area_index,
    )
except ImportError:  # pragma: no cover
    annotate_in_port_area = None  # type: ignore[assignment]
    apply_port_area_to_outside_anchorage = None  # type: ignore[assignment]
    get_port_area_index = None  # type: ignore[assignment]


logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# Eager-load port areas so the startup log line is visible (and import fallback is obvious).
if get_port_area_index is not None:
    get_port_area_index()
else:
    logging.error(
        "port_areas import failed; loitering will not apply harbour exclusions"
    )

DETECTION_VERSION = "1.2-loiter-motion-metrics"
LOOP_INTERVAL_SECONDS = int(os.environ.get("loiter_loop_seconds", "900"))
LOOKBACK_HOURS = 2
MIN_SPAN_SECONDS = 2 * 3600
MIN_SAMPLES = 8
MIN_RADIUS_M = 100.0
MAX_RADIUS_M = 6 * 1852.0  # 6 NM — storage ceiling; the API narrows radius, path, and net
MAX_NET_M = 926.0  # 0.5 NM
MAX_NET_PATH_RATIO = 0.20
RESTRICTED_BUFFER_M = 5 * 1852.0
STS_LOOKAHEAD_HOURS = 6
STS_MAX_DISTANCE_M = 5 * 1852.0
PLACE_MATCH_M = 2 * 1852.0
MMSI_CHUNK = 1500

# Motion evidence (informational only — does not change which events are detected).
SOG_UNAVAILABLE_KN = 102.0  # AIS "not available" is 102.3; treat >= 102 as missing
HEADING_UNAVAILABLE = 511
SOG_CHANGE_THRESHOLD_KN = 0.5  # consecutive 5-min buckets
HEADING_CHANGE_THRESHOLD_DEG = 45.0  # mid of 30–45° band; counts a deliberate turn
NET_EPS_M = 1.0  # loiter_ratio null when net displacement ~0

# Nullable columns added by sql/loiter_motion_metrics.sql (not applied by this job).
MOTION_METRIC_COLUMNS = (
    "avg_sog_kn",
    "max_sog_kn",
    "sog_std_kn",
    "sog_change_count",
    "heading_change_count",
    "loiter_ratio",
)

def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if value is None or str(value).strip() == "":
        raise RuntimeError(
            f"Environment variable {name} is required for loitering detection "
            f"(no hard-coded credentials)."
        )
    return value


def _database_url() -> str:
    password = _require_env("pnav_db_password")
    host = os.environ.get(
        "pnav_db_host",
        "marineai2.cxwk8yige5f2.ap-southeast-5.rds.amazonaws.com",
    )
    user = os.environ.get("pnav_db_user", "postgresadmin")
    name = os.environ.get("pnav_db_name", "pnav")
    return f"postgresql://{user}:{quote(password)}@{host}:5432/{name}"


CLICKHOUSE_USER = os.environ.get("clickhouse_user", "default")


class Ais_VesselLoiterActivity(SQLModel, table=True):
    id: Optional[int] = Field(default=None, sa_column=Column(BigInteger, primary_key=True))
    mmsi: int = Field(sa_column=Column(BigInteger, index=True))
    place_key: str
    started_at: datetime
    ended_at: datetime
    centre_latitude: float
    centre_longitude: float
    radius_m: float
    path_m: float
    net_m: float
    sample_count: int
    outside_anchorage: bool = Field(default=False)
    near_restricted: bool = Field(default=False)
    before_sts: bool = Field(default=False)
    anchorage_name: Optional[str] = Field(default=None)
    restricted_distance_m: Optional[float] = Field(default=None)
    sts_observation_id: Optional[int] = Field(default=None, sa_column=Column(BigInteger))
    tsout: Optional[datetime] = Field(default=None)
    detection_version: Optional[str] = Field(default=None)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


_engine = None
PG_STATEMENT_TIMEOUT_MS = 120000
PG_LOCK_TIMEOUT_MS = 5000


def get_pg_engine() -> Engine:
    global _engine
    if _engine is None:
        _engine = create_engine(
            _database_url(),
            pool_size=1,
            max_overflow=0,
            pool_timeout=30,
            pool_pre_ping=True,
            pool_recycle=1800,
            connect_args={
                "application_name": "sts_loiter",
                "options": (
                    f"-c statement_timeout={PG_STATEMENT_TIMEOUT_MS}"
                    f" -c lock_timeout={PG_LOCK_TIMEOUT_MS}"
                ),
            },
        )
    return _engine


def get_clickhouse_client():
    kwargs = {
        "host": _require_env("clickhouse_host"),
        "user": CLICKHOUSE_USER,
        "password": _require_env("clickhouse_password"),
    }
    return clickhouse_connect.get_client(**kwargs)


def create_db_and_tables(engine: Engine) -> None:
    SQLModel.metadata.create_all(engine)
    with engine.connect() as conn:
        conn.execute(text("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_loiter_open_mmsi
            ON ais_vesselloiteractivity (mmsi)
            WHERE tsout IS NULL
        """))
        conn.commit()


def _ch_ts(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def load_cargo_tanker_mmsis(engine: Engine) -> list[int]:
    df = pd.read_sql(
        f"""
        SELECT DISTINCT mmsi
        FROM (
            SELECT mmsi, "shipType",
                   row_number() OVER (
                       PARTITION BY mmsi ORDER BY {CURRENT_STATIC_ROW_ORDER}
                   ) AS rn
            FROM public.ais_static
        ) s
        WHERE rn = 1
{VALID_SHIP_MMSI_SQL}
          AND "shipType" >= 70 AND "shipType" < 90
        """,
        con=engine,
    )
    if df.empty:
        return []
    return [int(v) for v in df["mmsi"].tolist() if is_valid_ship_mmsi(v)]


def fetch_track_samples(client, mmsis: list[int], start: datetime, end: datetime) -> pd.DataFrame:
    """
    5-minute track buckets for the lookback window (same filters as before).

    Also returns SOG / COG / trueHeading via argMax(..., ts) so motion metrics
    use the same sample points without a second ClickHouse scan.
    """
    frames = []
    start_s = _ch_ts(start)
    end_s = _ch_ts(end)
    empty_cols = ["mmsi", "ts", "latitude", "longitude", "sog", "cog", "true_heading"]
    for offset in range(0, len(mmsis), MMSI_CHUNK):
        chunk = mmsis[offset: offset + MMSI_CHUNK]
        ids = ",".join(str(m) for m in chunk)
        query = f"""
        SELECT
            mmsi,
            bucket AS ts,
            argMax(latitude, ts) AS latitude,
            argMax(longitude, ts) AS longitude,
            argMax(sog, ts) AS sog,
            argMax(cog, ts) AS cog,
            argMax(trueHeading, ts) AS true_heading
        FROM (
            SELECT
                mmsi,
                ts,
                latitude,
                longitude,
                sog,
                cog,
                trueHeading,
                toStartOfFiveMinutes(ts) AS bucket
            FROM pnav.ais_position
            WHERE ts >= toDateTime64('{start_s}', 3)
              AND ts <= toDateTime64('{end_s}', 3)
              AND mmsi IN ({ids})
              AND latitude BETWEEN -90 AND 90
              AND longitude BETWEEN -180 AND 180
              AND latitude != 91
              AND longitude != 181
        )
        GROUP BY mmsi, bucket
        """
        result = client.query(query)
        if result.result_rows:
            frames.append(pd.DataFrame(result.result_rows, columns=result.column_names))
    if not frames:
        return pd.DataFrame(columns=empty_cols)
    out = pd.concat(frames, ignore_index=True)
    out["ts"] = pd.to_datetime(out["ts"], utc=True)
    return out


def _usable_sog(value) -> float | None:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    try:
        sog = float(value)
    except (TypeError, ValueError):
        return None
    if sog < 0 or sog >= SOG_UNAVAILABLE_KN:
        return None
    return sog


def _usable_heading(true_heading, cog) -> float | None:
    """Prefer trueHeading; fall back to COG. Skip 511 / invalid."""
    for raw in (true_heading, cog):
        if raw is None or (isinstance(raw, float) and math.isnan(raw)):
            continue
        try:
            hdg = float(raw)
        except (TypeError, ValueError):
            continue
        if hdg < 0 or hdg >= HEADING_UNAVAILABLE:
            continue
        if hdg > 360:
            continue
        return hdg % 360.0
    return None


def _heading_delta_deg(a: float, b: float) -> float:
    """Smallest turn from heading a to b (degrees), handling 0/360 wrap."""
    d = (b - a + 180.0) % 360.0 - 180.0
    return abs(d)


def compute_motion_metrics(samples: pd.DataFrame) -> pd.DataFrame:
    """
    Per-MMSI SOG / heading behaviour over the detection window samples.

    Informational only — callers merge onto loiter rows; detection filters unchanged.
    """
    cols = list(MOTION_METRIC_COLUMNS)
    if samples is None or samples.empty:
        return pd.DataFrame(columns=["mmsi", *cols])

    rows: list[dict] = []
    for mmsi, group in samples.sort_values("ts").groupby("mmsi", sort=False):
        sogs: list[float] = []
        headings: list[float] = []
        sog_changes = 0
        heading_changes = 0
        prev_sog: float | None = None
        prev_hdg: float | None = None
        for rec in group.itertuples(index=False):
            sog = _usable_sog(getattr(rec, "sog", None))
            hdg = _usable_heading(
                getattr(rec, "true_heading", None),
                getattr(rec, "cog", None),
            )
            if sog is not None:
                sogs.append(sog)
                if prev_sog is not None and abs(sog - prev_sog) >= SOG_CHANGE_THRESHOLD_KN:
                    sog_changes += 1
                prev_sog = sog
            if hdg is not None:
                headings.append(hdg)
                if prev_hdg is not None and _heading_delta_deg(prev_hdg, hdg) >= HEADING_CHANGE_THRESHOLD_DEG:
                    heading_changes += 1
                prev_hdg = hdg

        if sogs:
            avg_sog = float(sum(sogs) / len(sogs))
            max_sog = float(max(sogs))
            if len(sogs) >= 2:
                mean = avg_sog
                var = sum((x - mean) ** 2 for x in sogs) / (len(sogs) - 1)
                sog_std = float(math.sqrt(var))
            else:
                sog_std = 0.0
        else:
            avg_sog = max_sog = sog_std = None

        rows.append({
            "mmsi": int(mmsi),
            "avg_sog_kn": avg_sog,
            "max_sog_kn": max_sog,
            "sog_std_kn": sog_std,
            "sog_change_count": int(sog_changes),
            "heading_change_count": int(heading_changes),
            # loiter_ratio filled after path/net known
            "loiter_ratio": None,
        })
    return pd.DataFrame(rows)


def attach_loiter_ratio(loiters: pd.DataFrame) -> pd.DataFrame:
    """path_m / net_m; null when net is missing or <= NET_EPS_M."""
    out = loiters.copy()
    if out.empty:
        if "loiter_ratio" not in out.columns:
            out["loiter_ratio"] = pd.Series(dtype="float")
        return out
    ratios = []
    for row in out.itertuples(index=False):
        try:
            net = float(row.net_m)
            path = float(row.path_m)
        except (TypeError, ValueError, AttributeError):
            ratios.append(None)
            continue
        if net <= NET_EPS_M or math.isnan(net) or math.isnan(path):
            ratios.append(None)
        else:
            ratios.append(round(path / net, 4))
    out["loiter_ratio"] = ratios
    return out


def _table_columns(conn, table: str = "ais_vesselloiteractivity") -> set[str]:
    rows = conn.execute(
        text(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = :table
            """
        ),
        {"table": table},
    ).fetchall()
    return {str(r[0]) for r in rows}


def motion_columns_present(engine: Engine) -> bool:
    """True when migration sql/loiter_motion_metrics.sql has been applied."""
    with engine.connect() as conn:
        cols = _table_columns(conn)
    return all(c in cols for c in MOTION_METRIC_COLUMNS)


def _spatial():
    conn = duckdb.connect()
    conn.execute("INSTALL spatial")
    conn.execute("LOAD spatial")
    return conn


def _duckdb_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """Coerce pandas StringDtype ('str') columns to object for DuckDB 1.4."""
    if frame is None or frame.empty:
        return frame
    str_cols = [
        col for col in frame.columns
        if str(frame[col].dtype) == "str" or str(frame[col].dtype).startswith("string")
    ]
    if not str_cols:
        return frame
    out = frame.copy()
    for col in str_cols:
        out[col] = out[col].astype(object)
    return out


def detect_loiters(samples: pd.DataFrame) -> pd.DataFrame:
    empty_cols = [
        "mmsi", "started_at", "ended_at", "centre_latitude", "centre_longitude",
        "radius_m", "path_m", "net_m", "sample_count",
    ]
    if samples.empty:
        return pd.DataFrame(columns=empty_cols)

    conn = _spatial()
    try:
        conn.register("samples", _duckdb_frame(samples))
        found = conn.sql(
            f"""
            WITH stats AS (
                SELECT
                    mmsi,
                    min(ts) AS started_at,
                    max(ts) AS ended_at,
                    count(*) AS sample_count,
                    avg(latitude) AS centre_latitude,
                    avg(longitude) AS centre_longitude,
                    arg_min(latitude, ts) AS first_lat,
                    arg_min(longitude, ts) AS first_lon,
                    arg_max(latitude, ts) AS last_lat,
                    arg_max(longitude, ts) AS last_lon
                FROM samples
                GROUP BY mmsi
            ),
            radii AS (
                SELECT
                    s.mmsi,
                    max(ST_Distance_Sphere(
                        ST_Point(w.longitude, w.latitude),
                        ST_Point(s.centre_longitude, s.centre_latitude)
                    )) AS radius_m
                FROM samples w
                JOIN stats s ON s.mmsi = w.mmsi
                GROUP BY s.mmsi
            ),
            ordered AS (
                SELECT
                    mmsi, ts, latitude, longitude,
                    lag(latitude) OVER (PARTITION BY mmsi ORDER BY ts) AS prev_lat,
                    lag(longitude) OVER (PARTITION BY mmsi ORDER BY ts) AS prev_lon
                FROM samples
            ),
            paths AS (
                SELECT
                    mmsi,
                    sum(
                        CASE
                            WHEN prev_lat IS NULL THEN 0
                            ELSE ST_Distance_Sphere(
                                ST_Point(prev_lon, prev_lat),
                                ST_Point(longitude, latitude)
                            )
                        END
                    ) AS path_m
                FROM ordered
                GROUP BY mmsi
            )
            SELECT
                st.mmsi,
                st.started_at,
                st.ended_at,
                st.centre_latitude,
                st.centre_longitude,
                r.radius_m,
                p.path_m,
                ST_Distance_Sphere(
                    ST_Point(st.first_lon, st.first_lat),
                    ST_Point(st.last_lon, st.last_lat)
                ) AS net_m,
                st.sample_count
            FROM stats st
            JOIN radii r ON r.mmsi = st.mmsi
            JOIN paths p ON p.mmsi = st.mmsi
            WHERE date_diff('second', st.started_at, st.ended_at) >= {MIN_SPAN_SECONDS}
              AND st.sample_count >= {MIN_SAMPLES}
              AND r.radius_m > {MIN_RADIUS_M}
              AND r.radius_m <= {MAX_RADIUS_M}
              AND ST_Distance_Sphere(
                    ST_Point(st.first_lon, st.first_lat),
                    ST_Point(st.last_lon, st.last_lat)
                  ) < {MAX_NET_M}
              AND (
                    p.path_m <= 0
                    OR ST_Distance_Sphere(
                        ST_Point(st.first_lon, st.first_lat),
                        ST_Point(st.last_lon, st.last_lat)
                    ) < {MAX_NET_PATH_RATIO} * p.path_m
                  )
            """
        ).df()
    finally:
        conn.close()
    return found


def _haversine_m(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    radius = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * radius * math.asin(min(1.0, math.sqrt(a)))


def _distance_to_ring_m(lon: float, lat: float, ring: list[list[float]]) -> float:
    """Metres from a point to a polygon ring. 0 when the point is inside."""
    if _point_in_ring(lon, lat, ring):
        return 0.0
    best = None
    for i in range(len(ring) - 1):
        dist = _distance_to_segment_m(lon, lat, ring[i], ring[i + 1])
        if best is None or dist < best:
            best = dist
    return float(best or 0.0)


def _point_in_ring(lon: float, lat: float, ring: list[list[float]]) -> bool:
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        intersects = ((yi > lat) != (yj > lat)) and (
            lon < (xj - xi) * (lat - yi) / ((yj - yi) or 1e-12) + xi
        )
        if intersects:
            inside = not inside
        j = i
    return inside


def _distance_to_segment_m(lon: float, lat: float, a: list[float], b: list[float]) -> float:
    # Local equirectangular projection around the point, good enough for a few NM.
    lat0 = math.radians(lat)
    mx = math.cos(lat0) * 6_371_000.0
    my = 6_371_000.0
    px, py = 0.0, 0.0
    ax = math.radians(a[0] - lon) * mx
    ay = math.radians(a[1] - lat) * my
    bx = math.radians(b[0] - lon) * mx
    by = math.radians(b[1] - lat) * my
    dx, dy = bx - ax, by - ay
    denom = dx * dx + dy * dy
    if denom == 0:
        return math.hypot(ax - px, ay - py)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / denom))
    return math.hypot(ax + t * dx - px, ay + t * dy - py)


def _parent_polygons() -> pd.DataFrame:
    rows = []
    for area in anchorage_areas:
        if is_excl_name(area["name"]):
            continue
        ring = [[float(lon), float(lat)] for lon, lat in area["polygon"]]
        if ring[0] != ring[-1]:
            ring.append(ring[0])
        rows.append({
            "anchorage_name": area["name"],
            "geojson": json.dumps({"type": "Polygon", "coordinates": [ring]}),
        })
    return pd.DataFrame(rows)


def label_geography(loiters: pd.DataFrame) -> pd.DataFrame:
    out = loiters.copy()
    out["outside_anchorage"] = True
    out["anchorage_name"] = None
    out["near_restricted"] = False
    out["restricted_distance_m"] = None
    out["place_key"] = [
        f"{round(float(r.centre_latitude), 2)}_{round(float(r.centre_longitude), 2)}"
        for r in out.itertuples(index=False)
    ]
    if out.empty:
        return out

    parents = _parent_polygons()
    conn = _spatial()
    try:
        conn.register(
            "loiters",
            _duckdb_frame(out[["mmsi", "centre_longitude", "centre_latitude"]]),
        )
        conn.register("parents", _duckdb_frame(parents))
        named = conn.sql(
            """
            SELECT l.mmsi, min(p.anchorage_name) AS anchorage_name
            FROM loiters l
            JOIN parents p
              ON ST_Within(
                    ST_Point(l.centre_longitude, l.centre_latitude),
                    ST_GeomFromGeoJSON(p.geojson)
                 )
            GROUP BY l.mmsi
            """
        ).df()
    finally:
        conn.close()

    if not named.empty:
        out = out.drop(columns=["anchorage_name"]).merge(named, on="mmsi", how="left")
        out["outside_anchorage"] = out["anchorage_name"].isna()

    ring = [[float(lon), float(lat)] for lon, lat in restricted_limit["polygon"]]
    distances = [
        round(_distance_to_ring_m(float(r.centre_longitude), float(r.centre_latitude), ring), 1)
        for r in out.itertuples(index=False)
    ]
    out["restricted_distance_m"] = distances
    out["near_restricted"] = [d <= RESTRICTED_BUFFER_M for d in distances]

    # Port-area centres are not "outside anchorage" for the default list.
    # No new DB column — only clears outside_anchorage when in a port area.
    if annotate_in_port_area is not None and apply_port_area_to_outside_anchorage is not None:
        if get_port_area_index is not None:
            get_port_area_index()
        out = annotate_in_port_area(
            out,
            lon_col="centre_longitude",
            lat_col="centre_latitude",
        )
        out = apply_port_area_to_outside_anchorage(out)
    return out


def refresh_before_sts(engine: Engine) -> int:
    loiters = pd.read_sql(
        """
        SELECT id, mmsi, started_at, ended_at, centre_latitude, centre_longitude
        FROM public.ais_vesselloiteractivity
        WHERE ended_at >= now() - interval '6 hours'
        """,
        con=engine,
    )
    if loiters.empty:
        return 0
    sts = pd.read_sql(
        """
        SELECT
            o.id AS sts_observation_id,
            o.first_detected_at,
            o.centroid_longitude,
            o.centroid_latitude,
            m.mmsi
        FROM public.ais_vesselproximityobservation o
        JOIN public.ais_vesselproximitymember m ON m.observation_id = o.id
        WHERE o.first_detected_at >= now() - interval '12 hours'
          AND o.centroid_longitude IS NOT NULL
          AND o.centroid_latitude IS NOT NULL
        """,
        con=engine,
    )
    if sts.empty:
        return 0

    conn = _spatial()
    try:
        conn.register("loiters", _duckdb_frame(loiters))
        conn.register("sts", _duckdb_frame(sts))
        matched = conn.sql(
            f"""
            SELECT loiter_id, sts_observation_id
            FROM (
                SELECT
                    l.id AS loiter_id,
                    s.sts_observation_id,
                    row_number() OVER (
                        PARTITION BY l.id
                        ORDER BY ST_Distance_Sphere(
                            ST_Point(l.centre_longitude, l.centre_latitude),
                            ST_Point(s.centroid_longitude, s.centroid_latitude)
                        )
                    ) AS rn
                FROM loiters l
                JOIN sts s ON s.mmsi = l.mmsi
                WHERE s.first_detected_at >= l.started_at
                  AND s.first_detected_at <= l.ended_at + INTERVAL {STS_LOOKAHEAD_HOURS} HOUR
                  AND ST_Distance_Sphere(
                        ST_Point(l.centre_longitude, l.centre_latitude),
                        ST_Point(s.centroid_longitude, s.centroid_latitude)
                      ) <= {STS_MAX_DISTANCE_M}
            )
            WHERE rn = 1
            """
        ).df()
    finally:
        conn.close()

    if matched.empty:
        return 0
    with engine.begin() as conn:
        for row in matched.itertuples(index=False):
            conn.execute(
                text("""
                    UPDATE public.ais_vesselloiteractivity
                    SET before_sts = TRUE,
                        sts_observation_id = :sts_id,
                        updated_at = now()
                    WHERE id = :id
                """),
                {"id": int(row.loiter_id), "sts_id": int(row.sts_observation_id)},
            )
    return int(len(matched))


def _place_key(lat: float, lon: float) -> str:
    return f"{round(lat, 2)}_{round(lon, 2)}"


def upsert_loiters(engine: Engine, loiters: pd.DataFrame, now: datetime) -> tuple[int, int]:
    open_rows = pd.read_sql(
        """
        SELECT id, mmsi, centre_latitude, centre_longitude
        FROM public.ais_vesselloiteractivity
        WHERE tsout IS NULL
        """,
        con=engine,
    )
    open_by_mmsi = {int(r.mmsi): r for r in open_rows.itertuples(index=False)} if not open_rows.empty else {}
    seen: set[int] = set()
    inserted = 0
    updated = 0
    write_motion = motion_columns_present(engine)

    motion_insert_cols = ""
    motion_insert_vals = ""
    motion_update_set = ""
    if write_motion:
        motion_insert_cols = ",\n                            " + ", ".join(MOTION_METRIC_COLUMNS)
        motion_insert_vals = ",\n                            " + ", ".join(f":{c}" for c in MOTION_METRIC_COLUMNS)
        motion_update_set = ",\n                            " + ",\n                            ".join(
            f"{c} = :{c}" for c in MOTION_METRIC_COLUMNS
        )

    with engine.begin() as conn:
        for row in loiters.itertuples(index=False):
            mmsi = int(row.mmsi)
            seen.add(mmsi)
            existing = open_by_mmsi.get(mmsi)
            moved = False
            if existing is not None:
                moved = _haversine_m(
                    float(existing.centre_longitude),
                    float(existing.centre_latitude),
                    float(row.centre_longitude),
                    float(row.centre_latitude),
                ) > PLACE_MATCH_M
                if moved:
                    conn.execute(
                        text("""
                            UPDATE public.ais_vesselloiteractivity
                            SET tsout = :now, updated_at = :now
                            WHERE id = :id
                        """),
                        {"id": int(existing.id), "now": now},
                    )
            params = _row_params(row, now, include_motion=write_motion)
            if existing is None or moved:
                conn.execute(
                    text(f"""
                        INSERT INTO public.ais_vesselloiteractivity (
                            mmsi, place_key, started_at, ended_at,
                            centre_latitude, centre_longitude, radius_m, path_m, net_m,
                            sample_count, outside_anchorage, near_restricted, before_sts,
                            anchorage_name, restricted_distance_m, tsout,
                            detection_version, created_at, updated_at
                            {motion_insert_cols}
                        ) VALUES (
                            :mmsi, :place_key, :started_at, :ended_at,
                            :centre_latitude, :centre_longitude, :radius_m, :path_m, :net_m,
                            :sample_count, :outside_anchorage, :near_restricted, FALSE,
                            :anchorage_name, :restricted_distance_m, NULL,
                            :detection_version, :now, :now
                            {motion_insert_vals}
                        )
                    """),
                    params,
                )
                inserted += 1
            else:
                conn.execute(
                    text(f"""
                        UPDATE public.ais_vesselloiteractivity
                        SET ended_at = :ended_at,
                            centre_latitude = :centre_latitude,
                            centre_longitude = :centre_longitude,
                            place_key = :place_key,
                            radius_m = :radius_m,
                            path_m = :path_m,
                            net_m = :net_m,
                            sample_count = :sample_count,
                            outside_anchorage = :outside_anchorage,
                            near_restricted = :near_restricted,
                            anchorage_name = :anchorage_name,
                            restricted_distance_m = :restricted_distance_m,
                            detection_version = :detection_version,
                            updated_at = :now
                            {motion_update_set}
                        WHERE id = :id
                    """),
                    {**params, "id": int(existing.id)},
                )
                updated += 1

        for mmsi, existing in open_by_mmsi.items():
            if mmsi in seen:
                continue
            conn.execute(
                text("""
                    UPDATE public.ais_vesselloiteractivity
                    SET tsout = :now, updated_at = :now
                    WHERE id = :id
                """),
                {"id": int(existing.id), "now": now},
            )
    return inserted, updated


def _optional_float(row, name: str):
    if not hasattr(row, name):
        return None
    value = getattr(row, name)
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _optional_int(row, name: str):
    value = _optional_float(row, name)
    if value is None:
        return None
    return int(value)


def _row_params(row, now: datetime, *, include_motion: bool = False) -> dict:
    anchorage = row.anchorage_name
    if anchorage is not None and (isinstance(anchorage, float) and math.isnan(anchorage)):
        anchorage = None
    if pd.isna(anchorage):
        anchorage = None
    params = {
        "mmsi": int(row.mmsi),
        "place_key": row.place_key,
        "started_at": row.started_at,
        "ended_at": row.ended_at,
        "centre_latitude": float(row.centre_latitude),
        "centre_longitude": float(row.centre_longitude),
        "radius_m": float(row.radius_m),
        "path_m": float(row.path_m),
        "net_m": float(row.net_m),
        "sample_count": int(row.sample_count),
        "outside_anchorage": bool(row.outside_anchorage),
        "near_restricted": bool(row.near_restricted),
        "anchorage_name": None if anchorage is None else str(anchorage),
        "restricted_distance_m": None if pd.isna(row.restricted_distance_m) else float(row.restricted_distance_m),
        "detection_version": DETECTION_VERSION,
        "now": now,
    }
    if include_motion:
        params["avg_sog_kn"] = _optional_float(row, "avg_sog_kn")
        params["max_sog_kn"] = _optional_float(row, "max_sog_kn")
        params["sog_std_kn"] = _optional_float(row, "sog_std_kn")
        params["sog_change_count"] = _optional_int(row, "sog_change_count")
        params["heading_change_count"] = _optional_int(row, "heading_change_count")
        params["loiter_ratio"] = _optional_float(row, "loiter_ratio")
    return params


def run_once(engine: Engine, client) -> None:
    now = datetime.now(timezone.utc)
    start = now - timedelta(hours=LOOKBACK_HOURS)
    mmsis = load_cargo_tanker_mmsis(engine)
    logging.info("[loiter] cargo/tanker MMSIs=%s window=%s to %s", len(mmsis), start, now)
    samples = fetch_track_samples(client, mmsis, start, now) if mmsis else pd.DataFrame()
    logging.info("[loiter] track samples=%s", len(samples))
    loiters = detect_loiters(samples)
    # Motion metrics are informational; merge after geometry detection.
    metrics = compute_motion_metrics(samples)
    if not loiters.empty and not metrics.empty:
        loiters = loiters.merge(metrics, on="mmsi", how="left")
    elif not loiters.empty:
        for col in MOTION_METRIC_COLUMNS:
            if col not in loiters.columns:
                loiters[col] = None
    loiters = attach_loiter_ratio(loiters)
    loiters = label_geography(loiters)
    write_motion = motion_columns_present(engine)
    if not write_motion:
        logging.info(
            "[loiter] motion metric columns absent — skipping persist "
            "(apply sql/loiter_motion_metrics.sql when ready)"
        )
    inserted, updated = upsert_loiters(engine, loiters, now)
    linked = refresh_before_sts(engine)
    logging.info(
        "[loiter] detected=%s inserted=%s updated=%s before_sts_links=%s motion_persist=%s",
        len(loiters), inserted, updated, linked, write_motion,
    )


def main() -> None:
    engine = get_pg_engine()
    create_db_and_tables(engine)
    client = get_clickhouse_client()
    while True:
        try:
            run_once(engine, client)
        except Exception:
            logging.exception("[loiter] cycle failed")
        time.sleep(LOOP_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
