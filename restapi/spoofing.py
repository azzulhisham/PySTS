"""
Position anomalies (Phase 1: impossible AIS jumps / teleport).

Scans ClickHouse `ais_position` for consecutive fixes on the same MMSI whose
implied speed exceeds physical limits. A one-point spike is dropped when the
fixes on either side of it are still close together. Keeps Class-A cargo/tanker
only (shipType 70-89 from the current `ais_static` row per MMSI) and dedupes to
one row per MMSI per UTC day (worst implied speed that day).

Also referred to in product docs as GET /mantis/position-anomaly; the served
path is GET /mantis/spoofing.
"""

from __future__ import annotations

import logging
import math
import os
from datetime import datetime, timezone
from typing import Any
import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

from ais_identity import (
    VALID_SHIP_MMSI_SQL,
    VALID_SHIP_MMSI_SQL_CH,
    clean_ship_name,
    is_valid_ship_mmsi,
)
from api_timestamps import format_last_seen_at
from pg_engine import get_pg_engine  # re-used by spoofing_vessel_analysis
from sanctions import attach_sanctions, payload_fields, sort_listed_first
from timelineplayback import (
    VALID_POSITION_SQL,
    _ch_literal_ts,
    get_clickhouse_client,
    parse_datetime,
    resolve_track_range,
)
from vessel_size import (
    CURRENT_STATIC_ROW_ORDER,
    DIM_SELECT,
    class_b_join,
    dimension_fields,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

RULE_VERSION = "v1.3-snapback-bad-data"
SHIP_TYPE_FILTER = "70-89 (cargo/tanker/container Class-A large vessels)"
DEDUPE_RULE = (
    "Split alert vs data-quality reasons, then one row per MMSI per UTC day "
    "in each group (highest impliedSpeedKn). data_quality_count is per MMSI-day."
)

MIN_DT_S = 10
MAX_DT_S = 900
MIN_DIST_M = 1000
LONG_JUMP_DIST_M = 20_000
LONG_JUMP_MIN_KN = 50.0
HIGH_SPEED_MIN_KN = 80.0
# Optional extra ceiling only. Empty / 0 / unset = off. bad_position_data is
# primarily short-gap (dt < MIN_DT_S) or single-point snap-back — not speed.
_bad_data_raw = os.environ.get("BAD_DATA_KNOTS", "").strip()
BAD_DATA_KNOTS = float(_bad_data_raw) if _bad_data_raw else 0.0
# LIMIT 1 BY before the window roughly doubles ClickHouse wall time on a
# 3-day fleet scan. Default off so cost stays within ~10% of the prior plan;
# set SPOOF_CH_LIMIT_BY=1 to enable the exact-dup dedup in CH (required for
# short-gap duplicate_mmsi detection on the store path when combined with
# SHORT_GAP_HIT_SQL). Speed-series dedup still runs in Python either way.
SPOOF_CH_LIMIT_BY = os.environ.get("SPOOF_CH_LIMIT_BY", "0").lower() not in (
    "0", "false", "no", "",
)
# One bad fix between two reports that are still near each other. 2 km covers
# a cargo ship moving during the jump; the return must also be under 10% of
# the jump so a real course change is kept. Snap-backs are stored as
# bad_position_data (not dropped). Sustained jumps (next fix stays far) stay teleport.
SNAP_BACK_MAX_M = 2_000

# duplicate_mmsi: two clusters more than this apart, each stable within DUP_CLUSTER_SPREAD_M.
DUP_CLUSTER_MIN_M = 20_000
DUP_CLUSTER_SPREAD_M = 2_000
DUP_MIN_SWITCHES = 3
# Allow up to this fraction of points outside the two clusters (RF ghosts).
DUP_OUTLIER_FRAC = 0.10

DATA_QUALITY_REASONS = frozenset({"bad_position_data", "duplicate_mmsi"})


def should_replace_stored_anomaly(
    *,
    stored_reason: str,
    stored_implied_kn: float,
    new_reason: str,
    new_implied_kn: float,
) -> bool:
    """
    Upsert priority for ais_positionanomaly (mmsi, event_day).

    - Alert (teleport / high_speed) always replaces a data-quality row.
    - Data-quality never replaces an alert row.
    - Within the same class, keep the higher implied_kn.
    """
    stored_dq = stored_reason in DATA_QUALITY_REASONS
    new_dq = new_reason in DATA_QUALITY_REASONS
    if not new_dq and stored_dq:
        return True
    if new_dq == stored_dq and stored_implied_kn < new_implied_kn:
        return True
    return False

PAIR_WINDOW_SQL = "PARTITION BY mmsi ORDER BY ts ROWS BETWEEN 2 PRECEDING AND 1 FOLLOWING"

# prev2 = the fix before the previous point. next = the fix after this point.
# snap_prev_m: this fix vs the one before the previous (previous point was the spike).
# snap_curr_m: the next fix vs the previous (this point was the spike).
SPIKE_SELECT_SQL = """
        lagInFrame(ts, 2) OVER w AS prev2_ts,
        leadInFrame(ts, 1) OVER w AS next_ts,
        geoDistance(
            lagInFrame(longitude, 2) OVER w, lagInFrame(latitude, 2) OVER w,
            longitude, latitude
        ) AS snap_prev_m,
        geoDistance(
            lagInFrame(longitude) OVER w, lagInFrame(latitude) OVER w,
            leadInFrame(longitude, 1) OVER w, leadInFrame(latitude, 1) OVER w
        ) AS snap_curr_m"""

# True when this jump is a single-point ghost (returns / skips back within 2 km).
SNAP_BACK_COND_SQL = f"""(
        (
            prev2_ts > toDateTime64('2000-01-01', 3)
            AND snap_prev_m <= {SNAP_BACK_MAX_M}
            AND snap_prev_m * 10 < dist_m
        )
        OR (
            next_ts > toDateTime64('2000-01-01', 3)
            AND snap_curr_m <= {SNAP_BACK_MAX_M}
            AND snap_curr_m * 10 < dist_m
        )
)"""

# Legacy name: callers that still exclude snap-backs (none in the main path).
SPIKE_EXCLUSION_SQL = f"""
  AND NOT {SNAP_BACK_COND_SQL}"""

CARGO_TANKER_STATIC_SQL = f"""
SELECT
    s.mmsi,
    s."shipName" AS shipname,
    s."shipType" AS shiptype,
    s."shipTypeDesc" AS shiptypedesc,
    s.imo,
{DIM_SELECT}
FROM (
    SELECT *,
           row_number() OVER (
               PARTITION BY mmsi ORDER BY {CURRENT_STATIC_ROW_ORDER}
           ) AS rowcount_static
    FROM public.ais_static
) s
{class_b_join("s.mmsi")}
WHERE s.rowcount_static = 1
{VALID_SHIP_MMSI_SQL}
  AND s."shipType" >= 70 AND s."shipType" < 90
"""

# Reason assigned in the outer SELECT so SNAP_BACK_COND can use spike aliases.
# snap-back → bad_position_data; optional BAD_DATA_KNOTS (0=off);
# sustained long jump → teleport; else high_speed.
_REASON_OUTER = f"""
    multiIf(
        {SNAP_BACK_COND_SQL},
        'bad_position_data',
        {BAD_DATA_KNOTS} > 0 AND implied_kn > {BAD_DATA_KNOTS},
        'bad_position_data',
        dist_m >= {LONG_JUMP_DIST_M} AND implied_kn > {LONG_JUMP_MIN_KN},
        'teleport',
        'high_speed'
    ) AS reason"""

# Dedup exact same-second same-position rows before the lag window.
# Main path keeps MIN_DT_S (same as before) so ClickHouse cost stays close to
# the prior plan; sub-second / sub-MIN_DT far jumps are handled by SHORT_GAP_HIT_SQL.
_HIT_INNER = f"""
    SELECT
        mmsi,
        ts,
        lagInFrame(ts) OVER w AS prev_ts,
        lagInFrame(latitude) OVER w AS prev_lat,
        lagInFrame(longitude) OVER w AS prev_lon,
        latitude,
        longitude,
        geoDistance(
            lagInFrame(longitude) OVER w, lagInFrame(latitude) OVER w,
            longitude, latitude
        ) AS dist_m,
        dateDiff('second', lagInFrame(ts) OVER w, ts) AS dt_s,
        (geoDistance(
            lagInFrame(longitude) OVER w, lagInFrame(latitude) OVER w,
            longitude, latitude
        ) / greatest(dateDiff('second', lagInFrame(ts) OVER w, ts), 1)) * 1.94384 AS implied_kn,
{SPIKE_SELECT_SQL}
    FROM (
        SELECT
            mmsi,
            ts,
            latitude,
            longitude
        FROM pnav.ais_position
        WHERE ts >= toDateTime64({{date_from}}, 3)
          AND ts <= toDateTime64({{date_to}}, 3)
{VALID_SHIP_MMSI_SQL_CH}
          {{mmsi_filter}}
          {VALID_POSITION_SQL.strip()}
        {{limit_by}}
    )
    WINDOW w AS ({PAIR_WINDOW_SQL})
"""

_LIMIT_BY_SQL = "LIMIT 1 BY mmsi, toStartOfSecond(ts), latitude, longitude"

# Optional speed-cap predicate (empty when BAD_DATA_KNOTS is off).
_BAD_DATA_WHERE = (
    f"OR implied_kn > {BAD_DATA_KNOTS}" if BAD_DATA_KNOTS > 0 else ""
)

TELEPORT_HIT_SQL = f"""
SELECT
    mmsi, ts, prev_ts, prev_lat, prev_lon, latitude, longitude,
    dist_m, dt_s, implied_kn,
{_REASON_OUTER}
FROM (
{_HIT_INNER}
)
WHERE prev_ts IS NOT NULL
  AND dt_s BETWEEN {MIN_DT_S} AND {MAX_DT_S}
  AND dist_m >= {MIN_DIST_M}
  AND (
        {SNAP_BACK_COND_SQL}
        {_BAD_DATA_WHERE}
        OR (dist_m >= {LONG_JUMP_DIST_M} AND implied_kn > {LONG_JUMP_MIN_KN})
        OR implied_kn > {HIGH_SPEED_MIN_KN}
  )
SETTINGS max_execution_time = 60
"""

# Far jumps with dt < MIN_DT_S → always bad_position_data (short-gap rule).
# Same ts window and LIMIT 1 BY as the main query; only when MMSI list is set.
SHORT_GAP_HIT_SQL = f"""
SELECT
    mmsi, ts, prev_ts, prev_lat, prev_lon, latitude, longitude,
    dist_m, dt_s, implied_kn,
    'bad_position_data' AS reason
FROM (
{_HIT_INNER}
)
WHERE prev_ts IS NOT NULL
  AND dt_s >= 0 AND dt_s < {MIN_DT_S}
  AND dist_m >= {LONG_JUMP_DIST_M}
SETTINGS max_execution_time = 60
"""


def load_cargo_tanker_static(engine: Engine | None = None) -> pd.DataFrame:
    engine = engine or get_pg_engine()
    return pd.read_sql(CARGO_TANKER_STATIC_SQL, con=engine)


def _mmsi_in_clause(mmsi_list: list[int] | None) -> str:
    if not mmsi_list:
        return ""
    valid = [int(m) for m in mmsi_list if is_valid_ship_mmsi(m)]
    if not valid:
        return "AND 1 = 0"
    return "AND mmsi IN (" + ",".join(str(m) for m in valid) + ")"


def load_teleport_hits(
    date_from: datetime,
    date_to: datetime,
    client=None,
    *,
    mmsi_list: list[int] | None = None,
) -> pd.DataFrame:
    empty_cols = [
        "mmsi",
        "ts",
        "prev_ts",
        "prev_lat",
        "prev_lon",
        "latitude",
        "longitude",
        "dist_m",
        "dt_s",
        "implied_kn",
        "reason",
    ]
    if mmsi_list is not None and len(mmsi_list) == 0:
        return pd.DataFrame(columns=empty_cols)
    client = client or get_clickhouse_client()
    # Store path (mmsi_list set) always uses LIMIT 1 BY so short-gap duplicate
    # detection sees one row per second/position. Open fleet scans leave it off
    # unless SPOOF_CH_LIMIT_BY=1 (see module docstring / report).
    use_limit_by = SPOOF_CH_LIMIT_BY or bool(mmsi_list)
    params = dict(
        date_from=f"'{_ch_literal_ts(date_from)}'",
        date_to=f"'{_ch_literal_ts(date_to)}'",
        mmsi_filter=_mmsi_in_clause(mmsi_list),
        limit_by=_LIMIT_BY_SQL if use_limit_by else "",
    )
    frames: list[pd.DataFrame] = []
    result = client.query(TELEPORT_HIT_SQL.format(**params))
    if result.result_rows:
        frames.append(pd.DataFrame(result.result_rows, columns=result.column_names))
    # Short-gap far jumps → bad_position_data. Only with an MMSI list + LIMIT 1 BY.
    if mmsi_list and use_limit_by:
        result = client.query(SHORT_GAP_HIT_SQL.format(**params))
        if result.result_rows:
            frames.append(pd.DataFrame(result.result_rows, columns=result.column_names))
    if not frames:
        return pd.DataFrame(columns=empty_cols)
    hits = pd.concat(frames, ignore_index=True)
    hits = hits.drop_duplicates(subset=["mmsi", "ts", "prev_ts", "reason"], keep="first")
    return relabel_duplicate_mmsi(hits)


def classify_pair_reason(
    *,
    dt_s: float,
    dist_m: float,
    implied_kn: float,
    snap_back: bool,
) -> str | None:
    """
    Mirror of the ClickHouse reason rules for unit tests / Python post-pass.

    Returns None when the pair is not an anomaly hit.
    bad_position_data: dt < MIN_DT_S (far jump) or snap_back, or optional BAD_DATA_KNOTS.
    Sustained jump (dt >= MIN_DT_S, not snap-back) stays teleport / high_speed.
    """
    if dist_m < MIN_DIST_M:
        return None
    if dt_s < 0 or dt_s > MAX_DT_S:
        return None
    if dt_s < MIN_DT_S:
        return "bad_position_data" if dist_m >= LONG_JUMP_DIST_M else None
    if snap_back:
        return "bad_position_data"
    if BAD_DATA_KNOTS > 0 and implied_kn > BAD_DATA_KNOTS:
        return "bad_position_data"
    if dist_m >= LONG_JUMP_DIST_M and implied_kn > LONG_JUMP_MIN_KN:
        return "teleport"
    if implied_kn > HIGH_SPEED_MIN_KN:
        return "high_speed"
    return None


def _haversine_m(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def _cluster_label(lat: float, lon: float, centres: list[tuple[float, float]]) -> int | None:
    """Return cluster index if within DUP_CLUSTER_SPREAD_M of a centre, else None."""
    for idx, (clat, clon) in enumerate(centres):
        if _haversine_m(clon, clat, lon, lat) <= DUP_CLUSTER_SPREAD_M:
            return idx
    return None


def is_duplicate_mmsi_track(points: pd.DataFrame) -> bool:
    """
    True when the MMSI track alternates between two stable clusters more than
    20 km apart (each spread < 2 km) with at least DUP_MIN_SWITCHES switches.

    Up to DUP_OUTLIER_FRAC of points may sit outside both clusters (RF ghosts).
    """
    if points is None or len(points) < 4:
        return False
    work = points.dropna(subset=["latitude", "longitude"]).sort_values("ts")
    if len(work) < 4:
        return False

    first = work.iloc[0]
    centres: list[tuple[float, float]] = [(float(first["latitude"]), float(first["longitude"]))]
    for _, row in work.iterrows():
        lat, lon = float(row["latitude"]), float(row["longitude"])
        label = _cluster_label(lat, lon, centres)
        if label is not None:
            continue
        if len(centres) == 1:
            dist = _haversine_m(centres[0][1], centres[0][0], lon, lat)
            if dist >= DUP_CLUSTER_MIN_M:
                centres.append((lat, lon))
            # else: treat as outlier while discovering the second cluster
        # once two centres exist, further far points are counted as outliers below

    if len(centres) != 2:
        return False

    if _haversine_m(centres[0][1], centres[0][0], centres[1][1], centres[1][0]) < DUP_CLUSTER_MIN_M:
        return False

    labels: list[int | None] = []
    outliers = 0
    for _, row in work.iterrows():
        label = _cluster_label(float(row["latitude"]), float(row["longitude"]), centres)
        if label is None:
            outliers += 1
            labels.append(None)
        else:
            labels.append(label)

    if outliers / len(labels) > DUP_OUTLIER_FRAC:
        return False

    in_cluster = [lab for lab in labels if lab is not None]
    if len(in_cluster) < 4:
        return False
    switches = sum(1 for a, b in zip(in_cluster, in_cluster[1:]) if a != b)
    return switches >= DUP_MIN_SWITCHES


def relabel_duplicate_mmsi(hits: pd.DataFrame) -> pd.DataFrame:
    """Reclassify long-jump / bad-data hits as duplicate_mmsi when the track oscillates."""
    if hits.empty or "mmsi" not in hits.columns:
        return hits
    out = hits.copy()
    for mmsi, group in out.groupby("mmsi"):
        # Build a point list from both ends of each jump for clustering.
        rows = []
        for _, row in group.iterrows():
            if pd.notna(row.get("prev_lat")) and pd.notna(row.get("prev_lon")) and pd.notna(row.get("prev_ts")):
                rows.append({
                    "ts": row["prev_ts"],
                    "latitude": row["prev_lat"],
                    "longitude": row["prev_lon"],
                })
            rows.append({
                "ts": row["ts"],
                "latitude": row["latitude"],
                "longitude": row["longitude"],
            })
        if is_duplicate_mmsi_track(pd.DataFrame(rows)):
            out.loc[out["mmsi"] == mmsi, "reason"] = "duplicate_mmsi"
    return out.reset_index(drop=True)


def dedupe_daily(hits: pd.DataFrame) -> pd.DataFrame:
    """Keep one hit per MMSI per UTC day — highest implied_kn."""
    if hits.empty:
        return hits
    work = hits.copy()
    work["event_day"] = pd.to_datetime(work["ts"], utc=True).dt.date
    work = work.sort_values("implied_kn", ascending=False)
    return work.drop_duplicates(subset=["mmsi", "event_day"], keep="first").reset_index(drop=True)


def anomalies_to_payload(anomalies: pd.DataFrame) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    if anomalies.empty:
        return records

    for _, row in anomalies.iterrows():
        detected = format_last_seen_at(row.get("ts"))
        ship = row.get("shipname") if pd.notna(row.get("shipname")) else None
        records.append({
            "mmsi": int(row["mmsi"]),
            "shipName": clean_ship_name(ship),
            "shipType": int(row["shiptype"]) if pd.notna(row.get("shiptype")) else None,
            "shipTypeDesc": row.get("shiptypedesc") if pd.notna(row.get("shiptypedesc")) else None,
            "reason": row.get("reason"),
            "phase": 1,
            "detector": "teleport",
            "eventDay": str(row.get("event_day")) if pd.notna(row.get("event_day")) else None,
            "detectedAt": detected,
            "lastSeenAt": detected,
            "prevTs": format_last_seen_at(row.get("prev_ts")),
            "prevLatitude": float(row["prev_lat"]) if pd.notna(row.get("prev_lat")) else None,
            "prevLongitude": float(row["prev_lon"]) if pd.notna(row.get("prev_lon")) else None,
            "latitude": float(row["latitude"]) if pd.notna(row.get("latitude")) else None,
            "longitude": float(row["longitude"]) if pd.notna(row.get("longitude")) else None,
            "distanceM": round(float(row["dist_m"]), 1) if pd.notna(row.get("dist_m")) else None,
            "deltaSeconds": int(row["dt_s"]) if pd.notna(row.get("dt_s")) else None,
            "impliedSpeedKn": round(float(row["implied_kn"]), 1) if pd.notna(row.get("implied_kn")) else None,
            **dimension_fields(row),
            **payload_fields(row),
        })
    return records


def load_stored_anomalies(engine: Engine, date_from: datetime, date_to: datetime) -> pd.DataFrame:
    """Hits already written by the spoofing store. No ClickHouse on this path."""
    return pd.read_sql(
        text(
            """
            SELECT
                mmsi,
                ts,
                prev_ts,
                prev_latitude AS prev_lat,
                prev_longitude AS prev_lon,
                latitude,
                longitude,
                dist_m,
                dt_s,
                implied_kn,
                reason,
                event_day
            FROM public.ais_positionanomaly
            WHERE ts >= :date_from AND ts <= :date_to
            """
        ),
        con=engine,
        params={"date_from": date_from, "date_to": date_to},
    )


def load_static_for_mmsis(engine: Engine, mmsi_list: list[int]) -> pd.DataFrame:
    """Current static row for a small MMSI list. Does not window the whole history."""
    if not mmsi_list:
        return pd.DataFrame()
    return pd.read_sql(
        text(
            f"""
            SELECT DISTINCT ON (mmsi)
                mmsi,
                "shipName" AS shipname,
                "shipType" AS shiptype,
                "shipTypeDesc" AS shiptypedesc,
                imo,
                to_bow,
                to_stern,
                to_port,
                to_starboard
            FROM public.ais_static
            WHERE mmsi = ANY(:mmsis)
            ORDER BY mmsi, {CURRENT_STATIC_ROW_ORDER}
            """
        ),
        con=engine,
        params={"mmsis": [int(m) for m in mmsi_list]},
    )


def _split_alert_and_quality(frame: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    if frame.empty or "reason" not in frame.columns:
        return frame, frame.iloc[0:0]
    quality = frame[frame["reason"].isin(DATA_QUALITY_REASONS)].reset_index(drop=True)
    alerts = frame[~frame["reason"].isin(DATA_QUALITY_REASONS)].reset_index(drop=True)
    return alerts, quality


def detect_spoofing(
    date_from: datetime | str | None = None,
    date_to: datetime | str | None = None,
    engine: Engine | None = None,
    *,
    summary_only: bool = False,
    include_data_quality: bool = False,
) -> dict[str, Any]:
    """
    Position anomalies stored in ais_positionanomaly.

    Date window follows the same 3-day cap as GET /mantis/vessel-track.
    Data-quality reasons (bad_position_data, duplicate_mmsi) are stored but
    left out of the alert list and anomaly_count unless include_data_quality.
    """
    engine = engine or get_pg_engine()
    dt_from, dt_to, range_meta = resolve_track_range(date_from, date_to)

    raw_hits = load_stored_anomalies(engine, dt_from, dt_to)
    if not raw_hits.empty:
        raw_hits = raw_hits[raw_hits["mmsi"].map(is_valid_ship_mmsi)].reset_index(drop=True)

    static = load_static_for_mmsis(
        engine,
        raw_hits["mmsi"].astype(int).tolist() if not raw_hits.empty else [],
    )

    raw_hit_count = int(len(raw_hits))
    if raw_hits.empty:
        return _empty_result(dt_from, dt_to, range_meta, raw_hit_count)

    if static.empty:
        merged = raw_hits
    else:
        merged = raw_hits.merge(static, on="mmsi", how="left")
    filtered_hit_count = int(len(merged))
    # Split first so a same-day ghost (high implied_kn) cannot displace a teleport
    # alert in daily dedupe. data_quality_count = rows after per-MMSI-day dedupe.
    alerts, quality = _split_alert_and_quality(merged)
    alerts = dedupe_daily(alerts)
    quality = dedupe_daily(quality)
    data_quality_count = int(len(quality))
    shown = (
        pd.concat([alerts, quality], ignore_index=True)
        if include_data_quality
        else alerts
    )

    shown = attach_sanctions(shown, engine)
    if not summary_only:
        extra = ["implied_kn"] if "implied_kn" in shown.columns else []
        shown = sort_listed_first(shown, extra_sort=extra, extra_ascending=[False])

    by_reason: dict[str, int] = {}
    sanctions_match_count = 0
    if not shown.empty:
        for reason, cnt in shown["reason"].value_counts().items():
            by_reason[str(reason)] = int(cnt)
        sanctions_match_count = int(shown["sanctions_match"].sum())

    return {
        "rule_version": RULE_VERSION,
        "ship_type_filter": SHIP_TYPE_FILTER,
        "dedupe_rule": DEDUPE_RULE,
        "phase": 1,
        "detector": "teleport",
        "date_from": dt_from.astimezone(timezone.utc).isoformat(),
        "date_to": dt_to.astimezone(timezone.utc).isoformat(),
        "range_meta": range_meta,
        "thresholds": {
            "minDeltaSeconds": MIN_DT_S,
            "maxDeltaSeconds": MAX_DT_S,
            "minDistanceM": MIN_DIST_M,
            "longJumpDistanceM": LONG_JUMP_DIST_M,
            "longJumpMinImpliedKn": LONG_JUMP_MIN_KN,
            "highSpeedMinImpliedKn": HIGH_SPEED_MIN_KN,
            "badDataKnots": BAD_DATA_KNOTS,
            "snapBackMaxM": SNAP_BACK_MAX_M,
        },
        # raw_hit_count / filtered_hit_count: pre-dedupe diagnostics (all reasons).
        # anomaly_count: alert rows after daily dedupe (excludes data quality by default).
        "raw_hit_count": raw_hit_count,
        "filtered_hit_count": filtered_hit_count,
        "anomaly_count": int(len(shown)),
        "data_quality_count": data_quality_count,
        "include_data_quality": include_data_quality,
        "by_reason": by_reason,
        "sanctions_match_count": sanctions_match_count,
        "anomalies_payload": [] if summary_only else anomalies_to_payload(shown),
    }


def _empty_result(
    dt_from: datetime,
    dt_to: datetime,
    range_meta: dict[str, Any],
    raw_hit_count: int,
) -> dict[str, Any]:
    return {
        "rule_version": RULE_VERSION,
        "ship_type_filter": SHIP_TYPE_FILTER,
        "dedupe_rule": DEDUPE_RULE,
        "phase": 1,
        "detector": "teleport",
        "date_from": dt_from.astimezone(timezone.utc).isoformat(),
        "date_to": dt_to.astimezone(timezone.utc).isoformat(),
        "range_meta": range_meta,
        "thresholds": {
            "minDeltaSeconds": MIN_DT_S,
            "maxDeltaSeconds": MAX_DT_S,
            "minDistanceM": MIN_DIST_M,
            "longJumpDistanceM": LONG_JUMP_DIST_M,
            "longJumpMinImpliedKn": LONG_JUMP_MIN_KN,
            "highSpeedMinImpliedKn": HIGH_SPEED_MIN_KN,
            "badDataKnots": BAD_DATA_KNOTS,
            "snapBackMaxM": SNAP_BACK_MAX_M,
        },
        "raw_hit_count": raw_hit_count,
        "filtered_hit_count": 0,
        "anomaly_count": 0,
        "data_quality_count": 0,
        "include_data_quality": False,
        "by_reason": {},
        "sanctions_match_count": 0,
        "anomalies_payload": [],
    }


if __name__ == "__main__":
    result = detect_spoofing()
    print(
        f"raw={result['raw_hit_count']} "
        f"filtered={result['filtered_hit_count']} "
        f"deduped={result['anomaly_count']} "
        f"data_quality={result['data_quality_count']} "
        f"by_reason={result['by_reason']}"
    )
