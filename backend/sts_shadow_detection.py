"""
STS shadow detector — rule C (test only).

Writes public.ais_sts_shadow_episode. Does not touch live proximity tables or API.
"""

from __future__ import annotations

import logging
import math
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote

import clickhouse_connect
import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from ais_static_sql import CURRENT_STATIC_ROW_ORDER

_HERE = Path(__file__).resolve().parent
for _restapi in (_HERE / "restapi", _HERE.parent / "restapi"):
    if _restapi.is_dir() and str(_restapi) not in sys.path:
        sys.path.insert(0, str(_restapi))
        break

try:
    from ais_identity import VALID_SHIP_MMSI_SQL, is_valid_ship_mmsi
except ImportError:  # pragma: no cover
    VALID_SHIP_MMSI_SQL = """
  AND mmsi >= 201000000
  AND mmsi <= 775999999
  AND mod(mmsi, 1000000) <> 0
"""

    def is_valid_ship_mmsi(mmsi) -> bool:
        try:
            n = int(mmsi)
        except (TypeError, ValueError):
            return False
        s = str(n)
        return len(s) == 9 and not s.endswith("000000") and 201 <= int(s[:3]) <= 775

from polygons import anchorage_areas, is_excl_name  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")


def _port_areas():
    from port_areas import annotate_harbour_location, get_port_area_index  # noqa: E402

    return annotate_harbour_location, get_port_area_index

DETECTION_VERSION = "shadow-C-1.0"
LOOKBACK_HOURS = int(os.environ.get("sts_shadow_lookback_hours", "6"))
LOOP_SECONDS = int(os.environ.get("sts_shadow_loop_seconds", "900"))
MMSI_CHUNK = 1500
BUCKET_SECONDS = 300
MAX_GAP = timedelta(seconds=BUCKET_SECONDS * 2)  # one missed 5-min bucket
MIN_EPISODE = timedelta(hours=2)
SOG_MAX_KN = 5.0
DIST_100_M = 100.0
DIST_200_M = 200.0
LENGTH_LONG_M = 150.0
LENGTH_BUNKER_M = 100.0
STATIC_CACHE_TTL_S = 3600
CH_MAX_MEMORY = int(os.environ.get("sts_shadow_ch_max_memory", str(4 * 1024**3)))
CH_MAX_EXEC_S = int(os.environ.get("sts_shadow_ch_max_execution_s", "60"))
PG_STATEMENT_TIMEOUT_MS = 30000
PG_LOCK_TIMEOUT_MS = 5000
CLICKHOUSE_USER = os.environ.get("clickhouse_user", "default")

# in_mantis lookups require an existing index on ais_vesselproximitymember(mmsi).
# Verified at process start; disabled (always false) if no suitable index.
IN_MANTIS_ENABLED = True
IN_MANTIS_INDEX_HINT = "ix_ais_vesselproximitymember_mmsi"


def verify_in_mantis_index(engine: Engine) -> bool:
    """Return True if a usable index on vesselproximitymember.mmsi exists."""
    global IN_MANTIS_ENABLED
    df = pd.read_sql(
        text(
            """
            SELECT indexname, indexdef
            FROM pg_indexes
            WHERE schemaname = 'public'
              AND tablename = 'ais_vesselproximitymember'
              AND (
                    indexdef ILIKE '%(mmsi%'
                 OR indexdef ILIKE '% mmsi%'
              )
            """
        ),
        con=engine,
    )
    if df.empty:
        logging.warning(
            "[sts-shadow] no mmsi index on ais_vesselproximitymember — "
            "in_mantis disabled (always false)"
        )
        IN_MANTIS_ENABLED = False
        return False
    logging.info(
        "[sts-shadow] in_mantis index ok: %s",
        ", ".join(f"{r.indexname}" for r in df.itertuples(index=False)),
    )
    IN_MANTIS_ENABLED = True
    return True


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if value is None or str(value).strip() == "":
        raise RuntimeError(f"Environment variable {name} is required for STS shadow.")
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


_engine: Engine | None = None
_static_cache: tuple[float, pd.DataFrame] | None = None


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
                "application_name": "sts_shadow",
                "options": (
                    f"-c statement_timeout={PG_STATEMENT_TIMEOUT_MS}"
                    f" -c lock_timeout={PG_LOCK_TIMEOUT_MS}"
                ),
            },
        )
    return _engine


def get_clickhouse_client():
    return clickhouse_connect.get_client(
        host=_require_env("clickhouse_host"),
        user=CLICKHOUSE_USER,
        password=_require_env("clickhouse_password"),
    )


def _ch_ts(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def haversine_m(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    r = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def ship_length_m(to_bow, to_stern) -> float | None:
    try:
        if to_bow is None or to_stern is None or pd.isna(to_bow) or pd.isna(to_stern):
            return None
        total = float(to_bow) + float(to_stern)
    except (TypeError, ValueError):
        return None
    if total <= 0:
        return None
    return total


def is_same_vessel(a: dict, b: dict) -> bool:
    """Same hull rule as vesselproximitydetection.is_same_vessel (copied; do not edit live file)."""
    imo_match = a.get("imo") is not None and a.get("imo") == b.get("imo")
    name_match = a.get("name") is not None and a.get("name") == b.get("name")
    callsign_match = a.get("callsign") is not None and a.get("callsign") == b.get("callsign")
    dims_match = a.get("dims") is not None and a.get("dims") == b.get("dims")
    if imo_match and (name_match or callsign_match or dims_match):
        return True
    return bool(name_match and (callsign_match or dims_match))


def possible_bunker(ship_type_a, length_a, ship_type_b, length_b) -> bool:
    """True if at least one ship is tanker 80-89 with known length < 100 m."""
    for st, ln in ((ship_type_a, length_a), (ship_type_b, length_b)):
        try:
            st_i = int(st)
        except (TypeError, ValueError):
            continue
        if 80 <= st_i < 90 and ln is not None and not (isinstance(ln, float) and math.isnan(ln)):
            if float(ln) < LENGTH_BUNKER_M:
                return True
    return False


def pair_qualifies(
    dist_m: float,
    type_a: int,
    type_b: int,
    length_a: float | None,
    length_b: float | None,
    sog_a: float,
    sog_b: float,
) -> tuple[bool, str | None]:
    if sog_a >= SOG_MAX_KN or sog_b >= SOG_MAX_KN:
        return False, None
    if dist_m <= DIST_100_M:
        return True, "100m"
    if dist_m <= DIST_200_M:
        both_tanker = 80 <= type_a < 90 and 80 <= type_b < 90
        lengths = [x for x in (length_a, length_b) if x is not None]
        long_enough = any(x > LENGTH_LONG_M for x in lengths)
        if both_tanker and long_enough:
            return True, "tanker200m"
    return False, None


def _ordered_pair(m1: int, m2: int) -> tuple[int, int]:
    return (m1, m2) if m1 < m2 else (m2, m1)


@dataclass
class BucketHit:
    ts: datetime
    distance_m: float
    sog_a: float
    sog_b: float
    lat_a: float
    lon_a: float
    lat_b: float
    lon_b: float
    rule_band: str


@dataclass
class EpisodeCandidate:
    mmsi_a: int
    mmsi_b: int
    ts_start: datetime
    ts_last: datetime
    hits: list[BucketHit] = field(default_factory=list)
    rule_band: str = "100m"
    possible_bunker: bool = False
    bunker_hub_a: bool = False
    bunker_hub_b: bool = False
    ship_name_a: str | None = None
    ship_name_b: str | None = None
    ship_type_a: int | None = None
    ship_type_b: int | None = None
    length_m_a: float | None = None
    length_m_b: float | None = None

    @property
    def duration_h(self) -> float:
        return (self.ts_last - self.ts_start).total_seconds() / 3600.0

    def aggregates(self) -> dict[str, Any]:
        dists = [h.distance_m for h in self.hits]
        sogs = [(h.sog_a + h.sog_b) / 2.0 for h in self.hits]
        last = self.hits[-1]
        bands = {h.rule_band for h in self.hits}
        rule = "tanker200m" if "tanker200m" in bands and "100m" not in bands else (
            "100m" if "100m" in bands else self.rule_band
        )
        # Prefer 100m if any hit was in 100m band
        if "100m" in bands:
            rule = "100m"
        elif "tanker200m" in bands:
            rule = "tanker200m"
        return {
            "min_distance_m": min(dists) if dists else None,
            "median_distance_m": float(pd.Series(dists).median()) if dists else None,
            "max_distance_m": max(dists) if dists else None,
            "median_sog_kn": float(pd.Series(sogs).median()) if sogs else None,
            "centroid_latitude": (last.lat_a + last.lat_b) / 2.0,
            "centroid_longitude": (last.lon_a + last.lon_b) / 2.0,
            "rule_band": rule,
            "duration_h": round(self.duration_h, 4),
        }


def group_bucket_hits_into_runs(
    hits_by_pair: dict[tuple[int, int], list[BucketHit]],
) -> list[EpisodeCandidate]:
    """Split per-pair hits into continuous runs (allow one missed 5-min bucket)."""
    out: list[EpisodeCandidate] = []
    for (ma, mb), hits in hits_by_pair.items():
        hits = sorted(hits, key=lambda h: h.ts)
        if not hits:
            continue
        run: list[BucketHit] = [hits[0]]
        for h in hits[1:]:
            if h.ts - run[-1].ts <= MAX_GAP:
                run.append(h)
            else:
                out.append(_candidate_from_run(ma, mb, run))
                run = [h]
        out.append(_candidate_from_run(ma, mb, run))
    return out


def _candidate_from_run(ma: int, mb: int, run: list[BucketHit]) -> EpisodeCandidate:
    return EpisodeCandidate(
        mmsi_a=ma,
        mmsi_b=mb,
        ts_start=run[0].ts,
        ts_last=run[-1].ts,
        hits=list(run),
        rule_band=run[0].rule_band,
    )


def _as_utc(ts) -> datetime:
    t = pd.Timestamp(ts)
    if t.tzinfo is None:
        t = t.tz_localize("UTC")
    return t.to_pydatetime()


def can_extend_open(open_ts_last, cand_ts_start) -> bool:
    """True when candidate continues an open episode (≤ one missed 5-min bucket)."""
    open_last = _as_utc(open_ts_last)
    cand_start = _as_utc(cand_ts_start)
    return cand_start <= open_last + MAX_GAP


def select_persistable_candidates(
    runs: list[EpisodeCandidate],
    open_rows: dict[tuple[int, int], dict],
) -> tuple[list[tuple[str, EpisodeCandidate, dict | None]], list[int]]:
    """
    Decide extend vs insert vs ignore.

    Returns list of (action, candidate, open_row_or_none) where action in
    ('extend', 'insert'), and open ids to close (not extended this cycle).
    """
    actions: list[tuple[str, EpisodeCandidate, dict | None]] = []
    latest: dict[tuple[int, int], EpisodeCandidate] = {}
    for c in runs:
        key = (c.mmsi_a, c.mmsi_b)
        prev = latest.get(key)
        if prev is None or c.ts_last >= prev.ts_last:
            latest[key] = c

    for key, cand in latest.items():
        open_row = open_rows.get(key)
        if open_row is not None and can_extend_open(open_row["ts_last"], cand.ts_start):
            actions.append(("extend", cand, open_row))
            continue
        if (cand.ts_last - cand.ts_start) >= MIN_EPISODE:
            actions.append(("insert", cand, None))

    extended_keys = {(c.mmsi_a, c.mmsi_b) for act, c, _ in actions if act == "extend"}
    close_ids = [
        int(row["id"])
        for key, row in open_rows.items()
        if key not in extended_keys
    ]
    return actions, close_ids


def load_static_cache(engine: Engine, *, force: bool = False) -> pd.DataFrame:
    """Latest ais_static cargo/tanker rows; refresh at most once an hour."""
    global _static_cache
    now = time.time()
    if not force and _static_cache is not None and (now - _static_cache[0]) < STATIC_CACHE_TTL_S:
        return _static_cache[1]
    df = pd.read_sql(
        f"""
        SELECT mmsi, "shipName" AS ship_name, "shipType" AS ship_type,
               "shipTypeDesc" AS ship_type_desc, callsign, imo,
               to_bow, to_stern, to_port, to_starboard
        FROM (
            SELECT mmsi, "shipName", "shipType", "shipTypeDesc", callsign, imo,
                   to_bow, to_stern, to_port, to_starboard,
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
    if not df.empty:
        df["mmsi"] = df["mmsi"].astype(int)
        df["length_m"] = [
            ship_length_m(r.to_bow, r.to_stern) for r in df.itertuples(index=False)
        ]
        df["name"] = df["ship_name"].where(df["ship_name"].notna(), None)
        df["dims"] = list(
            zip(
                df["to_bow"].fillna(-1),
                df["to_stern"].fillna(-1),
                df["to_port"].fillna(-1),
                df["to_starboard"].fillna(-1),
            )
        )
        df.loc[
            (df["to_bow"].isna())
            & (df["to_stern"].isna())
            & (df["to_port"].isna())
            & (df["to_starboard"].isna()),
            "dims",
        ] = None
    _static_cache = (now, df)
    return df


def fetch_samples(client, mmsis: list[int], start: datetime, end: datetime) -> tuple[pd.DataFrame, dict]:
    """CH 5-min buckets; returns (df, timing dict). Raises on CH limit errors."""
    frames = []
    start_s, end_s = _ch_ts(start), _ch_ts(end)
    t0 = time.perf_counter()
    settings = {
        "max_memory_usage": CH_MAX_MEMORY,
        "max_execution_time": CH_MAX_EXEC_S,
    }
    for offset in range(0, len(mmsis), MMSI_CHUNK):
        chunk = mmsis[offset: offset + MMSI_CHUNK]
        ids = ",".join(str(m) for m in chunk)
        query = f"""
        SELECT
            mmsi,
            bucket AS ts,
            argMax(latitude, ts) AS latitude,
            argMax(longitude, ts) AS longitude,
            argMax(sog, ts) AS sog
        FROM (
            SELECT
                mmsi, ts, latitude, longitude, sog,
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
        result = client.query(query, settings=settings)
        if result.result_rows:
            frames.append(pd.DataFrame(result.result_rows, columns=result.column_names))
    elapsed = time.perf_counter() - t0
    summary = dict(getattr(result, "summary", None) or {}) if frames else {}
    if not frames:
        return pd.DataFrame(columns=["mmsi", "ts", "latitude", "longitude", "sog"]), {
            "ch_wall_s": elapsed,
            "ch_summary": summary,
        }
    out = pd.concat(frames, ignore_index=True)
    out["ts"] = pd.to_datetime(out["ts"], utc=True)
    out["mmsi"] = out["mmsi"].astype(int)
    return out, {"ch_wall_s": elapsed, "ch_summary": summary}


# ~250 m cells so 200 m pairs land in the same cell or an 8-neighbour.
_PAIR_CELL_DEG = 0.0025


def _cell_key(lon: float, lat: float) -> tuple[int, int]:
    return (int(math.floor(lon / _PAIR_CELL_DEG)), int(math.floor(lat / _PAIR_CELL_DEG)))


def build_pair_hits(
    samples: pd.DataFrame,
    static_df: pd.DataFrame,
) -> dict[tuple[int, int], list[BucketHit]]:
    if samples.empty or static_df.empty:
        return {}
    identities = {
        int(r.mmsi): {
            "imo": None if pd.isna(r.imo) else str(r.imo).strip() or None,
            "name": None if pd.isna(r.ship_name) else str(r.ship_name).strip() or None,
            "callsign": None if pd.isna(r.callsign) else str(r.callsign).strip() or None,
            "dims": r.dims if hasattr(r, "dims") else None,
            "ship_type": int(r.ship_type) if pd.notna(r.ship_type) else None,
            "length_m": r.length_m if pd.notna(getattr(r, "length_m", None)) else None,
            "ship_name": None if pd.isna(r.ship_name) else str(r.ship_name),
        }
        for r in static_df.itertuples(index=False)
    }
    hits: dict[tuple[int, int], list[BucketHit]] = {}
    partners: dict[int, set[int]] = {}
    seen_pair_ts: set[tuple[int, int, Any]] = set()

    for ts, group in samples.groupby("ts", sort=True):
        rows = []
        for rec in group.itertuples(index=False):
            mmsi = int(rec.mmsi)
            if mmsi not in identities:
                continue
            ida = identities[mmsi]
            if ida.get("ship_type") is None:
                continue
            try:
                sog = float(rec.sog)
            except (TypeError, ValueError):
                continue
            if sog < 0 or sog >= 102 or sog >= SOG_MAX_KN:
                continue
            rows.append(rec)
        cells: dict[tuple[int, int], list] = {}
        for rec in rows:
            cells.setdefault(
                _cell_key(float(rec.longitude), float(rec.latitude)), []
            ).append(rec)
        for (cx, cy), cell_rows in cells.items():
            candidates = list(cell_rows)
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    if dx == 0 and dy == 0:
                        continue
                    candidates.extend(cells.get((cx + dx, cy + dy), ()))
            for a in cell_rows:
                ma = int(a.mmsi)
                for b in candidates:
                    mb = int(b.mmsi)
                    if ma >= mb:
                        continue
                    pair_ts_key = (ma, mb, ts)
                    if pair_ts_key in seen_pair_ts:
                        continue
                    ida, idb = identities[ma], identities[mb]
                    if is_same_vessel(ida, idb):
                        continue
                    dist = haversine_m(
                        float(a.longitude), float(a.latitude),
                        float(b.longitude), float(b.latitude),
                    )
                    ok, band = pair_qualifies(
                        dist,
                        int(ida["ship_type"]),
                        int(idb["ship_type"]),
                        ida.get("length_m"),
                        idb.get("length_m"),
                        float(a.sog),
                        float(b.sog),
                    )
                    if not ok or band is None:
                        continue
                    seen_pair_ts.add(pair_ts_key)
                    key = (ma, mb)
                    hit = BucketHit(
                        ts=pd.Timestamp(ts).to_pydatetime(),
                        distance_m=dist,
                        sog_a=float(a.sog),
                        sog_b=float(b.sog),
                        lat_a=float(a.latitude),
                        lon_a=float(a.longitude),
                        lat_b=float(b.latitude),
                        lon_b=float(b.longitude),
                        rule_band=band,
                    )
                    hits.setdefault(key, []).append(hit)
                    partners.setdefault(ma, set()).add(mb)
                    partners.setdefault(mb, set()).add(ma)

    build_pair_hits._partners = partners  # type: ignore[attr-defined]
    build_pair_hits._identities = identities  # type: ignore[attr-defined]
    return hits


def enrich_candidates(cands: list[EpisodeCandidate]) -> list[EpisodeCandidate]:
    partners = getattr(build_pair_hits, "_partners", {})
    identities = getattr(build_pair_hits, "_identities", {})
    for c in cands:
        ia, ib = identities.get(c.mmsi_a, {}), identities.get(c.mmsi_b, {})
        c.ship_name_a = ia.get("ship_name") or ia.get("name")
        c.ship_name_b = ib.get("ship_name") or ib.get("name")
        c.ship_type_a = ia.get("ship_type")
        c.ship_type_b = ib.get("ship_type")
        c.length_m_a = ia.get("length_m")
        c.length_m_b = ib.get("length_m")
        c.possible_bunker = possible_bunker(
            c.ship_type_a, c.length_m_a, c.ship_type_b, c.length_m_b
        )
        c.bunker_hub_a = len(partners.get(c.mmsi_a, ())) >= 3
        c.bunker_hub_b = len(partners.get(c.mmsi_b, ())) >= 3
        agg = c.aggregates()
        c.rule_band = agg["rule_band"]
    return cands


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


def label_geography(cands: list[EpisodeCandidate]) -> list[dict[str, Any]]:
    annotate_harbour_location, get_port_area_index = _port_areas()
    get_port_area_index()
    rows = []
    for c in cands:
        agg = c.aggregates()
        lat, lon = agg["centroid_latitude"], agg["centroid_longitude"]
        zone_name = None
        in_excl = False
        for area in anchorage_areas:
            ring = [[float(x), float(y)] for x, y in area["polygon"]]
            if ring[0] != ring[-1]:
                ring.append(ring[0])
            if _point_in_ring(lon, lat, ring):
                if is_excl_name(area["name"]):
                    in_excl = True
                elif zone_name is None:
                    zone_name = area["name"]
        frame = pd.DataFrame([{"lon": lon, "lat": lat}])
        frame = annotate_harbour_location(frame, lon_col="lon", lat_col="lat", include_berths=False)
        harbour_excl = bool(frame.iloc[0]["in_port_area"])
        rows.append({
            **agg,
            "mmsi_a": c.mmsi_a,
            "mmsi_b": c.mmsi_b,
            "ts_start": c.ts_start,
            "ts_last": c.ts_last,
            "ship_name_a": c.ship_name_a,
            "ship_name_b": c.ship_name_b,
            "ship_type_a": c.ship_type_a,
            "ship_type_b": c.ship_type_b,
            "length_m_a": c.length_m_a,
            "length_m_b": c.length_m_b,
            "possible_bunker": c.possible_bunker,
            "bunker_hub_a": c.bunker_hub_a,
            "bunker_hub_b": c.bunker_hub_b,
            "zone_name": zone_name,
            "in_excl": in_excl,
            "harbour_excl": harbour_excl,
            "candidate": c,
        })
    return rows


def load_open_episodes(engine: Engine) -> dict[tuple[int, int], dict]:
    df = pd.read_sql(
        """
        SELECT id, mmsi_a, mmsi_b, ts_start, ts_last, min_distance_m, max_distance_m,
               median_distance_m, median_sog_kn, rule_band, possible_bunker,
               bunker_hub_a, bunker_hub_b
        FROM public.ais_sts_shadow_episode
        WHERE is_open
        """,
        con=engine,
    )
    out: dict[tuple[int, int], dict] = {}
    for _, row in df.iterrows():
        out[(int(row["mmsi_a"]), int(row["mmsi_b"]))] = {
            "id": int(row["id"]),
            "mmsi_a": int(row["mmsi_a"]),
            "mmsi_b": int(row["mmsi_b"]),
            "ts_start": row["ts_start"],
            "ts_last": row["ts_last"],
            "min_distance_m": row["min_distance_m"],
            "max_distance_m": row["max_distance_m"],
            "median_distance_m": row["median_distance_m"],
            "median_sog_kn": row["median_sog_kn"],
            "rule_band": row["rule_band"],
            "possible_bunker": bool(row["possible_bunker"]),
            "bunker_hub_a": bool(row["bunker_hub_a"]),
            "bunker_hub_b": bool(row["bunker_hub_b"]),
        }
    return out


def lookup_in_mantis(
    engine: Engine,
    pairs: list[tuple[int, int, datetime, datetime]],
) -> dict[tuple[int, int], bool]:
    """
    Uses ix_ais_vesselproximitymember_mmsi. Returns pair -> overlapping obs.
    If disabled or empty, all False.
    """
    result = {(a, b): False for a, b, _, _ in pairs}
    if not IN_MANTIS_ENABLED or not pairs:
        return result
    mmsis = sorted({m for a, b, _, _ in pairs for m in (a, b)})
    t_min = min(s for _, _, s, _ in pairs) - timedelta(hours=1)
    df = pd.read_sql(
        text(
            """
            SELECT m.mmsi, m.observation_id,
                   o.first_detected_at, o.last_detected_at, o.closed_at, o.is_open
            FROM public.ais_vesselproximitymember m
            JOIN public.ais_vesselproximityobservation o ON o.id = m.observation_id
            WHERE m.mmsi = ANY(:mmsis)
              AND o.last_detected_at >= :t_min
            """
        ),
        con=engine,
        params={"mmsis": mmsis, "t_min": t_min},
    )
    if df.empty:
        return result
    by_obs: dict[int, list] = {}
    for _, row in df.iterrows():
        by_obs.setdefault(int(row.observation_id), []).append(row)
    for obs_id, members in by_obs.items():
        mm = {int(r.mmsi) for r in members}
        first = members[0]
        o_start = pd.Timestamp(first.first_detected_at)
        o_end = pd.Timestamp(first.closed_at) if pd.notna(first.closed_at) else pd.Timestamp(first.last_detected_at)
        if o_start.tzinfo is None:
            o_start = o_start.tz_localize("UTC")
        if o_end.tzinfo is None:
            o_end = o_end.tz_localize("UTC")
        for a, b, s, e in pairs:
            if a in mm and b in mm:
                s_ts = pd.Timestamp(s)
                e_ts = pd.Timestamp(e)
                if s_ts.tzinfo is None:
                    s_ts = s_ts.tz_localize("UTC")
                if e_ts.tzinfo is None:
                    e_ts = e_ts.tz_localize("UTC")
                if s_ts <= o_end and e_ts >= o_start:
                    result[(a, b)] = True
    return result


def persist_cycle(
    engine: Engine,
    labeled: list[dict[str, Any]],
    open_rows: dict[tuple[int, int], dict],
) -> dict[str, int]:
    actions, close_ids = select_persistable_candidates(
        [row["candidate"] for row in labeled],
        open_rows,
    )
    # Map candidates back to labeled rows
    by_pair = {(r["mmsi_a"], r["mmsi_b"]): r for r in labeled}
    pairs_for_mantis = []
    work: list[tuple[str, dict, dict | None]] = []
    for act, cand, open_row in actions:
        row = by_pair[(cand.mmsi_a, cand.mmsi_b)]
        if act == "extend" and open_row is not None:
            # Keep original ts_start from open_row
            ts_start = open_row["ts_start"]
            ts_last = row["ts_last"]
            duration_h = (pd.Timestamp(ts_last) - pd.Timestamp(ts_start)).total_seconds() / 3600.0
            row = {**row, "ts_start": ts_start, "ts_last": ts_last, "duration_h": round(duration_h, 4)}
            # merge distance extremes
            for k, fn in (
                ("min_distance_m", min),
                ("max_distance_m", max),
            ):
                old = open_row.get(k)
                new = row.get(k)
                if old is not None and new is not None and not pd.isna(old):
                    row[k] = fn(float(old), float(new))
            row["possible_bunker"] = bool(row["possible_bunker"] or open_row.get("possible_bunker"))
            row["bunker_hub_a"] = bool(row["bunker_hub_a"] or open_row.get("bunker_hub_a"))
            row["bunker_hub_b"] = bool(row["bunker_hub_b"] or open_row.get("bunker_hub_b"))
        pairs_for_mantis.append((row["mmsi_a"], row["mmsi_b"], row["ts_start"], row["ts_last"]))
        work.append((act, row, open_row))

    mantis = lookup_in_mantis(engine, pairs_for_mantis) if pairs_for_mantis else {}

    inserted = updated = closed = 0
    in_mantis_n = 0
    with engine.begin() as conn:
        conn.execute(text("SET LOCAL statement_timeout = '30s'"))
        conn.execute(text("SET LOCAL lock_timeout = '5s'"))
        # Close stale opens first so a new insert for the same pair can open.
        if close_ids:
            conn.execute(
                text("""
                    UPDATE public.ais_sts_shadow_episode
                    SET is_open = FALSE, updated_at = now()
                    WHERE id = ANY(:ids)
                """),
                {"ids": close_ids},
            )
            closed = len(close_ids)
        for act, row, open_row in work:
            in_m = bool(mantis.get((row["mmsi_a"], row["mmsi_b"]), False))
            if in_m:
                in_mantis_n += 1
            params = {
                "mmsi_a": row["mmsi_a"],
                "mmsi_b": row["mmsi_b"],
                "ship_name_a": row["ship_name_a"],
                "ship_name_b": row["ship_name_b"],
                "ship_type_a": row["ship_type_a"],
                "ship_type_b": row["ship_type_b"],
                "length_m_a": row["length_m_a"],
                "length_m_b": row["length_m_b"],
                "ts_start": row["ts_start"],
                "ts_last": row["ts_last"],
                "duration_h": row["duration_h"],
                "min_distance_m": row["min_distance_m"],
                "median_distance_m": row["median_distance_m"],
                "max_distance_m": row["max_distance_m"],
                "median_sog_kn": row["median_sog_kn"],
                "rule_band": row["rule_band"],
                "possible_bunker": row["possible_bunker"],
                "bunker_hub_a": row["bunker_hub_a"],
                "bunker_hub_b": row["bunker_hub_b"],
                "centroid_latitude": row["centroid_latitude"],
                "centroid_longitude": row["centroid_longitude"],
                "zone_name": row["zone_name"],
                "in_excl": row["in_excl"],
                "harbour_excl": row["harbour_excl"],
                "in_mantis": in_m,
                "detection_version": DETECTION_VERSION,
            }
            if act == "insert":
                conn.execute(
                    text("""
                        INSERT INTO public.ais_sts_shadow_episode (
                            mmsi_a, mmsi_b, ship_name_a, ship_name_b,
                            ship_type_a, ship_type_b, length_m_a, length_m_b,
                            ts_start, ts_last, duration_h,
                            min_distance_m, median_distance_m, max_distance_m, median_sog_kn,
                            rule_band, possible_bunker, bunker_hub_a, bunker_hub_b,
                            centroid_latitude, centroid_longitude,
                            zone_name, in_excl, harbour_excl, in_mantis, is_open,
                            detection_version, created_at, updated_at
                        ) VALUES (
                            :mmsi_a, :mmsi_b, :ship_name_a, :ship_name_b,
                            :ship_type_a, :ship_type_b, :length_m_a, :length_m_b,
                            :ts_start, :ts_last, :duration_h,
                            :min_distance_m, :median_distance_m, :max_distance_m, :median_sog_kn,
                            :rule_band, :possible_bunker, :bunker_hub_a, :bunker_hub_b,
                            :centroid_latitude, :centroid_longitude,
                            :zone_name, :in_excl, :harbour_excl, :in_mantis, TRUE,
                            :detection_version, now(), now()
                        )
                        ON CONFLICT (mmsi_a, mmsi_b, ts_start) DO UPDATE SET
                            ts_last = EXCLUDED.ts_last,
                            duration_h = EXCLUDED.duration_h,
                            min_distance_m = LEAST(ais_sts_shadow_episode.min_distance_m, EXCLUDED.min_distance_m),
                            max_distance_m = GREATEST(ais_sts_shadow_episode.max_distance_m, EXCLUDED.max_distance_m),
                            median_distance_m = EXCLUDED.median_distance_m,
                            median_sog_kn = EXCLUDED.median_sog_kn,
                            rule_band = EXCLUDED.rule_band,
                            possible_bunker = ais_sts_shadow_episode.possible_bunker OR EXCLUDED.possible_bunker,
                            bunker_hub_a = ais_sts_shadow_episode.bunker_hub_a OR EXCLUDED.bunker_hub_a,
                            bunker_hub_b = ais_sts_shadow_episode.bunker_hub_b OR EXCLUDED.bunker_hub_b,
                            centroid_latitude = EXCLUDED.centroid_latitude,
                            centroid_longitude = EXCLUDED.centroid_longitude,
                            zone_name = EXCLUDED.zone_name,
                            in_excl = EXCLUDED.in_excl,
                            harbour_excl = EXCLUDED.harbour_excl,
                            in_mantis = EXCLUDED.in_mantis,
                            is_open = TRUE,
                            detection_version = EXCLUDED.detection_version,
                            updated_at = now()
                    """),
                    params,
                )
                inserted += 1
            else:
                assert open_row is not None
                params["id"] = int(open_row["id"])
                conn.execute(
                    text("""
                        UPDATE public.ais_sts_shadow_episode SET
                            ts_last = :ts_last,
                            duration_h = :duration_h,
                            min_distance_m = :min_distance_m,
                            median_distance_m = :median_distance_m,
                            max_distance_m = :max_distance_m,
                            median_sog_kn = :median_sog_kn,
                            rule_band = :rule_band,
                            possible_bunker = :possible_bunker,
                            bunker_hub_a = :bunker_hub_a,
                            bunker_hub_b = :bunker_hub_b,
                            centroid_latitude = :centroid_latitude,
                            centroid_longitude = :centroid_longitude,
                            zone_name = :zone_name,
                            in_excl = :in_excl,
                            harbour_excl = :harbour_excl,
                            in_mantis = :in_mantis,
                            ship_name_a = :ship_name_a,
                            ship_name_b = :ship_name_b,
                            ship_type_a = :ship_type_a,
                            ship_type_b = :ship_type_b,
                            length_m_a = :length_m_a,
                            length_m_b = :length_m_b,
                            detection_version = :detection_version,
                            updated_at = now()
                        WHERE id = :id
                    """),
                    params,
                )
                updated += 1
    return {
        "inserted": inserted,
        "extended": updated,
        "closed": closed,
        "in_mantis": in_mantis_n,
    }


def run_once(engine: Engine, client) -> None:
    now = datetime.now(timezone.utc)
    start = now - timedelta(hours=LOOKBACK_HOURS)
    t_pg0 = time.perf_counter()
    static_df = load_static_cache(engine)
    mmsis = [int(m) for m in static_df["mmsi"].tolist()] if not static_df.empty else []
    open_rows = load_open_episodes(engine)
    pg_static_ms = (time.perf_counter() - t_pg0) * 1000

    logging.info(
        "[sts-shadow] static_mmsis=%s open_eps=%s window=%s..%s",
        len(mmsis), len(open_rows), start.isoformat(), now.isoformat(),
    )
    if not mmsis:
        logging.info("[sts-shadow] detected=0 inserted=0 extended=0 closed=0 skipped=no_mmsis")
        return

    try:
        samples, ch_timing = fetch_samples(client, mmsis, start, now)
    except Exception as e:
        msg = str(e).lower()
        if "memory" in msg or "timeout" in msg or "max_execution" in msg or "code: 241" in msg or "code: 159" in msg:
            logging.error("[sts-shadow] CH limit hit — skip cycle: %s", e)
            return
        raise

    hits = build_pair_hits(samples, static_df)
    runs = enrich_candidates(group_bucket_hits_into_runs(hits))
    latest: dict[tuple[int, int], EpisodeCandidate] = {}
    for c in runs:
        key = (c.mmsi_a, c.mmsi_b)
        if key not in latest or c.ts_last >= latest[key].ts_last:
            latest[key] = c
    preview, _ = select_persistable_candidates(list(latest.values()), open_rows)
    to_label = [c for _, c, _ in preview]
    labeled = label_geography(to_label) if to_label else []

    t_pg1 = time.perf_counter()
    stats = persist_cycle(engine, labeled, open_rows)
    pg_write_ms = (time.perf_counter() - t_pg1) * 1000
    pg_total_ms = pg_static_ms + pg_write_ms

    ch_mem = (ch_timing.get("ch_summary") or {}).get("memory_usage")
    logging.info(
        "[sts-shadow] detected=%s open=%s inserted=%s extended=%s closed=%s "
        "in_mantis=%s samples=%s ch_s=%.2f ch_mem=%s pg_ms=%.0f version=%s",
        len(labeled),
        len(open_rows),
        stats["inserted"],
        stats["extended"],
        stats["closed"],
        stats.get("in_mantis", 0),
        len(samples),
        ch_timing.get("ch_wall_s", 0),
        ch_mem,
        pg_total_ms,
        DETECTION_VERSION,
    )


def main() -> None:
    engine = get_pg_engine()
    verify_in_mantis_index(engine)
    _, get_port_area_index = _port_areas()
    get_port_area_index()  # disk GeoJSON once at start
    client = get_clickhouse_client()
    while True:
        try:
            run_once(engine, client)
        except Exception:
            logging.exception("[sts-shadow] cycle failed")
        time.sleep(LOOP_SECONDS)


if __name__ == "__main__":
    main()
