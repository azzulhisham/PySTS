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
from datetime import datetime, timezone
from typing import Any
import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

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

RULE_VERSION = "v1.1-teleport-snapback-filter"
SHIP_TYPE_FILTER = "70-89 (cargo/tanker/container Class-A large vessels)"
DEDUPE_RULE = "One row per MMSI per UTC day; keep the hit with highest impliedSpeedKn."

MIN_DT_S = 10
MAX_DT_S = 900
MIN_DIST_M = 1000
LONG_JUMP_DIST_M = 20_000
LONG_JUMP_MIN_KN = 50.0
HIGH_SPEED_MIN_KN = 80.0
# One bad fix between two reports that are still near each other. 2 km covers
# a cargo ship moving during the jump; the return must also be under 10% of
# the jump so a real course change is kept.
SNAP_BACK_MAX_M = 2_000

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

SPIKE_EXCLUSION_SQL = f"""
  AND NOT (
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
  AND s.mmsi > 0
  AND s."shipType" >= 70 AND s."shipType" < 90
"""

TELEPORT_HIT_SQL = f"""
SELECT
    mmsi,
    ts,
    prev_ts,
    prev_lat,
    prev_lon,
    latitude,
    longitude,
    dist_m,
    dt_s,
    implied_kn,
    reason
FROM (
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
        ) / dateDiff('second', lagInFrame(ts) OVER w, ts)) * 1.94384 AS implied_kn,
        multiIf(
            geoDistance(
                lagInFrame(longitude) OVER w, lagInFrame(latitude) OVER w,
                longitude, latitude
            ) >= {LONG_JUMP_DIST_M}
            AND (geoDistance(
                lagInFrame(longitude) OVER w, lagInFrame(latitude) OVER w,
                longitude, latitude
            ) / dateDiff('second', lagInFrame(ts) OVER w, ts)) * 1.94384 > {LONG_JUMP_MIN_KN},
            'teleport',
            'high_speed'
        ) AS reason,
{SPIKE_SELECT_SQL}
    FROM pnav.ais_position
    WHERE ts >= toDateTime64({{date_from}}, 3)
      AND ts <= toDateTime64({{date_to}}, 3)
      AND mmsi > 0
      {{mmsi_filter}}
      {VALID_POSITION_SQL.strip()}
    WINDOW w AS ({PAIR_WINDOW_SQL})
)
WHERE prev_ts IS NOT NULL
  AND dt_s BETWEEN {MIN_DT_S} AND {MAX_DT_S}
  AND dist_m >= {MIN_DIST_M}
  AND ((dist_m >= {LONG_JUMP_DIST_M} AND implied_kn > {LONG_JUMP_MIN_KN}) OR implied_kn > {HIGH_SPEED_MIN_KN})
{SPIKE_EXCLUSION_SQL}
"""


def load_cargo_tanker_static(engine: Engine | None = None) -> pd.DataFrame:
    engine = engine or get_pg_engine()
    return pd.read_sql(CARGO_TANKER_STATIC_SQL, con=engine)


def _mmsi_in_clause(mmsi_list: list[int] | None) -> str:
    if not mmsi_list:
        return ""
    return "AND mmsi IN (" + ",".join(str(int(m)) for m in mmsi_list) + ")"


def load_teleport_hits(
    date_from: datetime,
    date_to: datetime,
    client=None,
    *,
    mmsi_list: list[int] | None = None,
) -> pd.DataFrame:
    if mmsi_list is not None and len(mmsi_list) == 0:
        return pd.DataFrame(
            columns=[
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
        )
    client = client or get_clickhouse_client()
    query = TELEPORT_HIT_SQL.format(
        date_from=f"'{_ch_literal_ts(date_from)}'",
        date_to=f"'{_ch_literal_ts(date_to)}'",
        mmsi_filter=_mmsi_in_clause(mmsi_list),
    )
    result = client.query(query)
    if not result.result_rows:
        return pd.DataFrame(columns=result.column_names)
    return pd.DataFrame(result.result_rows, columns=result.column_names)


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
        records.append({
            "mmsi": int(row["mmsi"]),
            "shipName": row.get("shipname") if pd.notna(row.get("shipname")) else None,
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


def detect_spoofing(
    date_from: datetime | str | None = None,
    date_to: datetime | str | None = None,
    engine: Engine | None = None,
    *,
    summary_only: bool = False,
) -> dict[str, Any]:
    """
    Position anomalies stored in ais_positionanomaly.

    Date window follows the same 3-day cap as GET /mantis/vessel-track.
    """
    engine = engine or get_pg_engine()
    dt_from, dt_to, range_meta = resolve_track_range(date_from, date_to)

    raw_hits = load_stored_anomalies(engine, dt_from, dt_to)
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
    deduped = dedupe_daily(merged)

    deduped = attach_sanctions(deduped, engine)
    if not summary_only:
        extra = ["implied_kn"] if "implied_kn" in deduped.columns else []
        deduped = sort_listed_first(deduped, extra_sort=extra, extra_ascending=[False])

    by_reason: dict[str, int] = {}
    sanctions_match_count = 0
    if not deduped.empty:
        for reason, cnt in deduped["reason"].value_counts().items():
            by_reason[str(reason)] = int(cnt)
        sanctions_match_count = int(deduped["sanctions_match"].sum())

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
            "snapBackMaxM": SNAP_BACK_MAX_M,
        },
        "raw_hit_count": raw_hit_count,
        "filtered_hit_count": filtered_hit_count,
        "anomaly_count": int(len(deduped)),
        "by_reason": by_reason,
        "sanctions_match_count": sanctions_match_count,
        "anomalies_payload": [] if summary_only else anomalies_to_payload(deduped),
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
            "snapBackMaxM": SNAP_BACK_MAX_M,
        },
        "raw_hit_count": raw_hit_count,
        "filtered_hit_count": 0,
        "anomaly_count": 0,
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
        f"by_reason={result['by_reason']}"
    )
