"""
GET /mantis/suspected-bunkering — read-only ranking of STS shadow bunker candidates.

Sources public.ais_sts_shadow_episode (possible_bunker / bunker_hub_*). No writes,
no DDL, no ClickHouse. Enrichment is batched by MMSI with existing indexes only.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

from ais_identity import clean_ship_name
from api_timestamps import format_last_seen_at
from pg_engine import get_pg_engine
from polygons import anchorage_areas, is_excl_name
from sanctions import attach_sanctions_pair_sides, payload_fields
from ship_risk_attach import attach_risk_pair_sides, risk_payload_fields
from vessel_size import (
    CURRENT_STATIC_ROW_ORDER,
    DIM_SELECT,
    class_b_join,
    dimension_fields,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# --- Tunable scoring (interim) -------------------------------------------------
SCORE_POSSIBLE_BUNKER = 2.0
SCORE_BUNKER_HUB = 2.0
SCORE_OUTSIDE_KNOWN_AREA = 1.5
SCORE_AT_NIGHT = 1.0
SCORE_SHORT_DURATION = 1.0
SCORE_AIS_ANOMALY = 1.5
SCORE_LOITERING = 1.0
SCORE_IDENTITY_RISK = 1.5
SCORE_SANCTIONS = 2.0

BAND_HIGH_MIN = 7.0
BAND_MEDIUM_MIN = 4.0
SCORE_CAP = 10.0

SHORT_DURATION_H = 4.0
NIGHT_LOCAL_START = 19  # inclusive hour
NIGHT_LOCAL_END = 7  # exclusive end of morning window
LOCAL_TZ = ZoneInfo("Asia/Kuala_Lumpur")
ANOMALY_PAD = timedelta(hours=6)
LOITER_PAD = timedelta(hours=6)
IDENTITY_PAD = timedelta(hours=24)

DEFAULT_HOURS = 24
MAX_HOURS = 168
DEFAULT_PAGE_SIZE = 100
MAX_PAGE_SIZE = 1000
CACHE_TTL_S = 300
STATEMENT_TIMEOUT = "30s"
LOCK_TIMEOUT = "5s"

RULE_VERSION = "suspected-bunker-1.0"

# Index names required for optional enrichment signals (skip signal if missing).
REQUIRED_INDEXES = {
    "episodes": "idx_sts_shadow_open",
    "anomaly": "idx_positionanomaly_ts",
    "loiter": "ix_ais_vesselloiteractivity_mmsi",
    "identity": "idx_identity_event_mmsi_ts",
    "static": "ix_ais_static_mmsi_ts",
}

_cache_lock = threading.Lock()
_response_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_index_cache: dict[str, bool] | None = None
_skipped_signals: list[str] = []


def clear_caches() -> None:
    """Test helper."""
    global _response_cache, _index_cache, _skipped_signals
    with _cache_lock:
        _response_cache = {}
    _index_cache = None
    _skipped_signals = []


# --- Licensed-bunker placeholder (JKPTG list later) ---------------------------

def lookup_licensed_status(mmsi_a: int, mmsi_b: int, engine: Engine | None = None) -> str:
    """
    Placeholder for the coming JKPTG licensed-bunker register.

    Return values intended later: "yes" | "no" | "unknown".
    Plug in a SELECT against a future lookup table here; callers stay unchanged.
    """
    _ = (mmsi_a, mmsi_b, engine)
    return "unknown"


# --- Geography / time helpers -------------------------------------------------

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


def outside_known_anchorage_or_port(
    lat: float | None,
    lon: float | None,
    *,
    zone_name: str | None,
    harbour_excl: bool,
    in_excl: bool,
) -> bool:
    """True when the centroid is not inside a parent anchorage / harbour / excl."""
    if harbour_excl or in_excl:
        return False
    if zone_name:
        return False
    if lat is None or lon is None or (isinstance(lat, float) and math.isnan(lat)):
        return True
    for area in anchorage_areas:
        ring = [[float(x), float(y)] for x, y in area["polygon"]]
        if ring[0] != ring[-1]:
            ring.append(ring[0])
        if _point_in_ring(float(lon), float(lat), ring):
            return False
    return True


def episode_at_night(ts_start, ts_last) -> bool:
    """True if any part of [ts_start, ts_last] falls in local 19:00–07:00."""
    if ts_start is None or ts_last is None:
        return False
    start = pd.Timestamp(ts_start)
    end = pd.Timestamp(ts_last)
    if start.tzinfo is None:
        start = start.tz_localize("UTC")
    if end.tzinfo is None:
        end = end.tz_localize("UTC")
    if end < start:
        start, end = end, start
    # Sample every hour across the span (bounded).
    cur = start
    step = timedelta(hours=1)
    while cur <= end:
        local = cur.tz_convert(LOCAL_TZ)
        h = local.hour
        if h >= NIGHT_LOCAL_START or h < NIGHT_LOCAL_END:
            return True
        cur += step
    # Also check end point
    local_end = end.tz_convert(LOCAL_TZ)
    return local_end.hour >= NIGHT_LOCAL_START or local_end.hour < NIGHT_LOCAL_END


def score_episode(
    *,
    possible_bunker: bool,
    bunker_hub: bool,
    outside_area: bool,
    at_night: bool,
    short_duration: bool,
    ais_anomaly: bool,
    loitering: bool,
    identity_risk: bool,
    sanctions: bool,
) -> tuple[float, str, list[str]]:
    """Return (score 0-10, band, reasons). Pure — unit-tested."""
    reasons: list[str] = []
    score = 0.0
    if possible_bunker:
        score += SCORE_POSSIBLE_BUNKER
        reasons.append("possible_bunker")
    if bunker_hub:
        score += SCORE_BUNKER_HUB
        reasons.append("bunker_hub")
    if outside_area:
        score += SCORE_OUTSIDE_KNOWN_AREA
        reasons.append("outside_known_area")
    if at_night:
        score += SCORE_AT_NIGHT
        reasons.append("at_night")
    if short_duration:
        score += SCORE_SHORT_DURATION
        reasons.append("short_duration")
    if ais_anomaly:
        score += SCORE_AIS_ANOMALY
        reasons.append("ais_anomaly")
    if loitering:
        score += SCORE_LOITERING
        reasons.append("loitering")
    if identity_risk:
        score += SCORE_IDENTITY_RISK
        reasons.append("identity_risk")
    if sanctions:
        score += SCORE_SANCTIONS
        reasons.append("sanctions")
    score = min(SCORE_CAP, round(score, 2))
    if score >= BAND_HIGH_MIN:
        band = "HIGH"
    elif score >= BAND_MEDIUM_MIN:
        band = "MEDIUM"
    else:
        band = "LOW"
    return score, band, reasons


# --- Index / connection helpers ----------------------------------------------

def _load_index_flags(engine: Engine) -> dict[str, bool]:
    global _index_cache, _skipped_signals
    if _index_cache is not None:
        return _index_cache
    names = list(REQUIRED_INDEXES.values())
    df = pd.read_sql(
        text(
            """
            SELECT indexname
            FROM pg_indexes
            WHERE schemaname = 'public'
              AND indexname = ANY(:names)
            """
        ),
        con=engine,
        params={"names": names},
    )
    present = set(df["indexname"].tolist()) if not df.empty else set()
    flags = {key: name in present for key, name in REQUIRED_INDEXES.items()}
    skipped = [f"{key}:{name}" for key, name in REQUIRED_INDEXES.items() if not flags[key]]
    _skipped_signals = skipped
    if skipped:
        logger.warning("[suspected-bunkering] missing indexes — signals skipped: %s", skipped)
    _index_cache = flags
    return flags


def _connect(engine: Engine):
    conn = engine.connect()
    conn.execute(text(f"SET LOCAL statement_timeout = '{STATEMENT_TIMEOUT}'"))
    conn.execute(text(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'"))
    return conn


def _timed_sql(engine: Engine, sql: str, params: dict) -> tuple[pd.DataFrame, float]:
    t0 = time.perf_counter()
    with engine.connect() as conn:
        conn.execute(text(f"SET LOCAL statement_timeout = '{STATEMENT_TIMEOUT}'"))
        conn.execute(text(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'"))
        df = pd.read_sql(text(sql), con=conn, params=params)
    return df, (time.perf_counter() - t0) * 1000.0


# --- SQL ---------------------------------------------------------------------

EPISODES_SQL = """
SELECT
    e.id,
    e.mmsi_a,
    e.mmsi_b,
    e.ship_name_a,
    e.ship_name_b,
    e.ship_type_a,
    e.ship_type_b,
    e.length_m_a,
    e.length_m_b,
    e.ts_start,
    e.ts_last,
    e.duration_h,
    e.min_distance_m,
    e.median_distance_m,
    e.max_distance_m,
    e.median_sog_kn,
    e.rule_band,
    e.possible_bunker,
    e.bunker_hub_a,
    e.bunker_hub_b,
    e.centroid_latitude,
    e.centroid_longitude,
    e.zone_name,
    e.in_excl,
    e.harbour_excl,
    e.is_open,
    e.detection_version
FROM public.ais_sts_shadow_episode e
WHERE (e.possible_bunker OR e.bunker_hub_a OR e.bunker_hub_b)
  AND (
        e.is_open
     OR e.ts_last >= :since
  )
ORDER BY e.ts_last DESC
"""

# Same one-row-per-MMSI rule as vessel_size.class_a_join, but filtered with
# mmsi = ANY(:mmsis) so we do not DISTINCT ON the whole ais_static table.
STATIC_BATCH_SQL = f"""
SELECT
    s.mmsi,
    s."shipName" AS ship_name,
    s."shipType" AS ship_type,
    s."shipTypeDesc" AS ship_type_desc,
    s."imo" AS imo,
    s.callsign,
{DIM_SELECT}
FROM (
    SELECT DISTINCT ON (mmsi) *
    FROM public.ais_static
    WHERE mmsi = ANY(:mmsis)
    ORDER BY mmsi, {CURRENT_STATIC_ROW_ORDER}
) s
{class_b_join("s.mmsi")}
"""

ANOMALY_SQL = """
SELECT DISTINCT a.mmsi
FROM public.ais_positionanomaly a
WHERE a.mmsi = ANY(:mmsis)
  AND a.ts >= :t_min
  AND a.ts <= :t_max
"""

LOITER_SQL = """
SELECT DISTINCT l.mmsi
FROM public.ais_vesselloiteractivity l
WHERE l.mmsi = ANY(:mmsis)
  AND l.started_at <= :t_max
  AND (l.ended_at IS NULL OR l.ended_at >= :t_min)
"""

IDENTITY_SQL = """
SELECT DISTINCT i.mmsi
FROM public.ais_identity_event i
WHERE i.mmsi = ANY(:mmsis)
  AND i.ts_start >= :t_min
  AND i.ts_start <= :t_max
"""


# --- Core --------------------------------------------------------------------

def _fmt_ts(value: Any) -> str | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return format_last_seen_at(value) or str(value)


def _opt_float(value: Any, digits: int | None = None) -> float | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if digits is not None:
        return round(out, digits)
    return out


def _vessel_payload(row: pd.Series, side: str) -> dict[str, Any]:
    length = _opt_float(row.get(f"length_m_{side}"), 1)
    if length is None:
        dims = dimension_fields(row, side=side)
        length = dims.get("lengthM")
    else:
        dims = dimension_fields(row, side=side)
    flag = row.get(f"vessel_flag_{side}")
    if flag is not None and isinstance(flag, float) and pd.isna(flag):
        flag = None
    return {
        "mmsi": int(row[f"mmsi_{side}"]),
        "shipName": clean_ship_name(row.get(f"ship_name_{side}")),
        "shipType": int(row[f"ship_type_{side}"]) if pd.notna(row.get(f"ship_type_{side}")) else None,
        "lengthM": length,
        "flag": None if flag is None else str(flag),
        **{k: dims[k] for k in ("toBow", "toStern", "toPort", "toStarboard", "beamM")},
        **payload_fields(row, side=side),
        **risk_payload_fields(row, side=side),
    }


def detect_suspected_bunkering(
    *,
    hours: int = DEFAULT_HOURS,
    band: str | None = None,
    min_score: float | None = None,
    mmsi: int | None = None,
    page: int = 1,
    page_size: int = DEFAULT_PAGE_SIZE,
    engine: Engine | None = None,
    use_cache: bool = True,
) -> dict[str, Any]:
    if hours < 1 or hours > MAX_HOURS:
        raise ValueError(f"hours must be 1..{MAX_HOURS}")
    if page < 1:
        raise ValueError("page must be >= 1")
    if page_size < 1 or page_size > MAX_PAGE_SIZE:
        raise ValueError(f"pageSize must be 1..{MAX_PAGE_SIZE}")
    if band is not None:
        band = band.upper()
        if band not in {"HIGH", "MEDIUM", "LOW"}:
            raise ValueError("band must be HIGH, MEDIUM, or LOW")

    cache_key = f"h={hours}|band={band}|min={min_score}|mmsi={mmsi}|p={page}|ps={page_size}"
    now_m = time.monotonic()
    if use_cache:
        with _cache_lock:
            hit = _response_cache.get(cache_key)
            if hit and now_m - hit[0] < CACHE_TTL_S:
                out = dict(hit[1])
                out["cacheHit"] = True
                return out

    engine = engine or get_pg_engine()
    t_wall0 = time.perf_counter()
    timings: dict[str, float] = {}
    indexes = _load_index_flags(engine)
    if not indexes.get("episodes"):
        raise RuntimeError("idx_sts_shadow_open missing — cannot query shadow episodes safely")

    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    episodes, timings["episodes_ms"] = _timed_sql(
        engine, EPISODES_SQL, {"since": since}
    )

    if mmsi is not None:
        episodes = episodes[
            (episodes["mmsi_a"] == int(mmsi)) | (episodes["mmsi_b"] == int(mmsi))
        ]

    query_notes: list[str] = list(_skipped_signals)

    anomaly_mmsis: set[int] = set()
    loiter_mmsis: set[int] = set()
    identity_mmsis: set[int] = set()

    if not episodes.empty:
        mmsis = sorted({
            int(m)
            for m in list(episodes["mmsi_a"]) + list(episodes["mmsi_b"])
            if pd.notna(m)
        })
        t_min = pd.Timestamp(episodes["ts_start"].min())
        t_max = pd.Timestamp(episodes["ts_last"].max())
        if t_min.tzinfo is None:
            t_min = t_min.tz_localize("UTC")
        if t_max.tzinfo is None:
            t_max = t_max.tz_localize("UTC")
        t_min_a = (t_min - ANOMALY_PAD).to_pydatetime()
        t_max_a = (t_max + ANOMALY_PAD).to_pydatetime()
        t_min_l = (t_min - LOITER_PAD).to_pydatetime()
        t_max_l = (t_max + LOITER_PAD).to_pydatetime()
        t_min_i = (t_min - IDENTITY_PAD).to_pydatetime()
        t_max_i = (t_max + IDENTITY_PAD).to_pydatetime()

        if indexes.get("static") and mmsis:
            static_df, timings["static_ms"] = _timed_sql(
                engine, STATIC_BATCH_SQL, {"mmsis": mmsis}
            )
            if not static_df.empty:
                static_df = static_df.drop_duplicates(subset=["mmsi"], keep="first")
                by_mmsi = static_df.set_index("mmsi")
                for side, col in (("a", "mmsi_a"), ("b", "mmsi_b")):
                    for field_src, field_dst in (
                        ("ship_name", f"ship_name_{side}"),
                        ("ship_type", f"ship_type_{side}"),
                        ("imo", f"imo_{side}"),
                        ("to_bow", f"to_bow_{side}"),
                        ("to_stern", f"to_stern_{side}"),
                        ("to_port", f"to_port_{side}"),
                        ("to_starboard", f"to_starboard_{side}"),
                    ):
                        if field_src in by_mmsi.columns:
                            episodes[field_dst] = episodes[col].map(
                                lambda m, fs=field_src: (
                                    by_mmsi.loc[int(m), fs]
                                    if pd.notna(m) and int(m) in by_mmsi.index
                                    else None
                                )
                            )
        else:
            timings["static_ms"] = 0.0
            if not indexes.get("static"):
                query_notes.append("static_skipped:missing_ix_ais_static_mmsi_ts")

        if indexes.get("anomaly") and mmsis:
            adf, timings["anomaly_ms"] = _timed_sql(
                engine,
                ANOMALY_SQL,
                {"mmsis": mmsis, "t_min": t_min_a, "t_max": t_max_a},
            )
            anomaly_mmsis = {int(x) for x in adf["mmsi"].tolist()} if not adf.empty else set()
        else:
            timings["anomaly_ms"] = 0.0
            if not indexes.get("anomaly"):
                query_notes.append("anomaly_skipped:missing_idx_positionanomaly_ts")

        if indexes.get("loiter") and mmsis:
            ldf, timings["loiter_ms"] = _timed_sql(
                engine,
                LOITER_SQL,
                {"mmsis": mmsis, "t_min": t_min_l, "t_max": t_max_l},
            )
            loiter_mmsis = {int(x) for x in ldf["mmsi"].tolist()} if not ldf.empty else set()
        else:
            timings["loiter_ms"] = 0.0
            if not indexes.get("loiter"):
                query_notes.append("loiter_skipped:missing_ix_ais_vesselloiteractivity_mmsi")

        if indexes.get("identity") and mmsis:
            idf, timings["identity_ms"] = _timed_sql(
                engine,
                IDENTITY_SQL,
                {"mmsis": mmsis, "t_min": t_min_i, "t_max": t_max_i},
            )
            identity_mmsis = {int(x) for x in idf["mmsi"].tolist()} if not idf.empty else set()
        else:
            timings["identity_ms"] = 0.0
            if not indexes.get("identity"):
                query_notes.append("identity_skipped:missing_idx_identity_event_mmsi_ts")

        t_s0 = time.perf_counter()
        episodes = attach_sanctions_pair_sides(episodes, engine)
        episodes = attach_risk_pair_sides(episodes, engine)
        timings["sanctions_risk_ms"] = (time.perf_counter() - t_s0) * 1000.0
    else:
        for k in ("static_ms", "anomaly_ms", "loiter_ms", "identity_ms", "sanctions_risk_ms"):
            timings[k] = 0.0

    items: list[dict[str, Any]] = []
    for _, row in episodes.iterrows():
        ma, mb = int(row["mmsi_a"]), int(row["mmsi_b"])
        hub = bool(row.get("bunker_hub_a") or row.get("bunker_hub_b"))
        outside = outside_known_anchorage_or_port(
            _opt_float(row.get("centroid_latitude")),
            _opt_float(row.get("centroid_longitude")),
            zone_name=None if pd.isna(row.get("zone_name")) else str(row.get("zone_name")),
            harbour_excl=bool(row.get("harbour_excl")),
            in_excl=bool(row.get("in_excl")),
        )
        duration_h = float(row["duration_h"]) if pd.notna(row.get("duration_h")) else 0.0
        identity_flag = (
            ma in identity_mmsis
            or mb in identity_mmsis
            or str(row.get("risk_rating_a") or "GREEN") != "GREEN"
            or str(row.get("risk_rating_b") or "GREEN") != "GREEN"
        )
        score, band_v, reasons = score_episode(
            possible_bunker=bool(row.get("possible_bunker")),
            bunker_hub=hub,
            outside_area=outside,
            at_night=episode_at_night(row.get("ts_start"), row.get("ts_last")),
            short_duration=duration_h < SHORT_DURATION_H,
            ais_anomaly=(ma in anomaly_mmsis) or (mb in anomaly_mmsis),
            loitering=(ma in loiter_mmsis) or (mb in loiter_mmsis),
            identity_risk=identity_flag,
            sanctions=bool(row.get("sanctions_match")),
        )
        if band is not None and band_v != band:
            continue
        if min_score is not None and score < float(min_score):
            continue
        items.append({
            "id": int(row["id"]),
            "vesselA": _vessel_payload(row, "a"),
            "vesselB": _vessel_payload(row, "b"),
            "centroidLatitude": _opt_float(row.get("centroid_latitude"), 6),
            "centroidLongitude": _opt_float(row.get("centroid_longitude"), 6),
            "zoneName": None if pd.isna(row.get("zone_name")) else str(row.get("zone_name")),
            "tsStart": _fmt_ts(row.get("ts_start")),
            "tsLast": _fmt_ts(row.get("ts_last")),
            "durationH": round(duration_h, 4),
            "minDistanceM": _opt_float(row.get("min_distance_m"), 3),
            "medianDistanceM": _opt_float(row.get("median_distance_m"), 3),
            "medianSogKn": _opt_float(row.get("median_sog_kn"), 3),
            "ruleBand": str(row.get("rule_band")),
            "isOpen": bool(row.get("is_open")),
            "score": score,
            "band": band_v,
            "reasons": reasons,
            "licensed": lookup_licensed_status(ma, mb, engine),
            "sanctionsMatch": bool(row.get("sanctions_match")),
        })

    items.sort(key=lambda r: (-r["score"], r["tsLast"] or ""), reverse=False)
    # sort score desc
    items.sort(key=lambda r: (-float(r["score"]), str(r.get("tsLast") or "")))

    total = len(items)
    by_band = {"HIGH": 0, "MEDIUM": 0, "LOW": 0}
    for it in items:
        by_band[it["band"]] = by_band.get(it["band"], 0) + 1

    start = (page - 1) * page_size
    page_items = items[start: start + page_size]
    total_pages = (total + page_size - 1) // page_size if total else 0
    wall_ms = (time.perf_counter() - t_wall0) * 1000.0

    result = {
        "ruleVersion": RULE_VERSION,
        "hours": hours,
        "episodeCount": total,
        "byBand": by_band,
        "case_count": total,  # for _page_payload
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
        "returned_count": len(page_items),
        "episodes": page_items,
        "queryMs": round(wall_ms, 1),
        "pnavQueryMs": {k: round(v, 1) for k, v in timings.items()},
        "skippedSignals": query_notes,
        "cacheHit": False,
    }

    if use_cache:
        with _cache_lock:
            _response_cache[cache_key] = (time.monotonic(), dict(result))
    return result
