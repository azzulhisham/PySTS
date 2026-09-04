"""
Position anomalies (Phase 1: impossible AIS jumps / teleport).

Scans ClickHouse `ais_position` for consecutive fixes on the same MMSI whose
implied speed exceeds physical limits. Keeps Class-A cargo/tanker only
(shipType 70-89 from latest `ais_static`) and dedupes to one row per MMSI
per UTC day (worst implied speed that day).

Also referred to in product docs as GET /mantis/position-anomaly; the served
path is GET /mantis/spoofing.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from api_timestamps import format_last_seen_at
from sanctions import attach_sanctions, payload_fields, sort_listed_first
from timelineplayback import (
    VALID_POSITION_SQL,
    _ch_literal_ts,
    get_clickhouse_client,
    parse_datetime,
    resolve_track_range,
)
from vessel_size import DIM_SELECT, class_b_join, dimension_fields

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

RULE_VERSION = "v1.0-teleport-cargo-tanker-daily-dedupe-ofac"
SHIP_TYPE_FILTER = "70-89 (cargo/tanker/container Class-A large vessels)"
DEDUPE_RULE = "One row per MMSI per UTC day; keep the hit with highest impliedSpeedKn."

MIN_DT_S = 10
MAX_DT_S = 900
MIN_DIST_M = 1000
LONG_JUMP_DIST_M = 20_000
LONG_JUMP_MIN_KN = 50.0
HIGH_SPEED_MIN_KN = 80.0

pswd = "m4r1t1m3"
DATABASE_URL = (
    f"postgresql://postgresadmin:{quote(pswd)}"
    f"@marineai2.cxwk8yige5f2.ap-southeast-5.rds.amazonaws.com:5432/pnav"
)

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
           row_number() OVER (PARTITION BY mmsi ORDER BY ts DESC) AS rowcount_static
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
        ) AS reason
    FROM pnav.ais_position
    WHERE ts >= toDateTime64({{date_from}}, 3)
      AND ts <= toDateTime64({{date_to}}, 3)
      AND mmsi > 0
      {VALID_POSITION_SQL.strip()}
    WINDOW w AS (PARTITION BY mmsi ORDER BY ts ROWS BETWEEN 1 PRECEDING AND CURRENT ROW)
)
WHERE prev_ts IS NOT NULL
  AND dt_s BETWEEN {MIN_DT_S} AND {MAX_DT_S}
  AND dist_m >= {MIN_DIST_M}
  AND ((dist_m >= {LONG_JUMP_DIST_M} AND implied_kn > {LONG_JUMP_MIN_KN}) OR implied_kn > {HIGH_SPEED_MIN_KN})
"""


def get_pg_engine() -> Engine:
    return create_engine(
        DATABASE_URL,
        pool_size=5,
        max_overflow=10,
        pool_timeout=30,
    )


def load_cargo_tanker_static(engine: Engine | None = None) -> pd.DataFrame:
    engine = engine or get_pg_engine()
    return pd.read_sql(CARGO_TANKER_STATIC_SQL, con=engine)


def load_teleport_hits(
    date_from: datetime,
    date_to: datetime,
    client=None,
) -> pd.DataFrame:
    client = client or get_clickhouse_client()
    query = TELEPORT_HIT_SQL.format(
        date_from=f"'{_ch_literal_ts(date_from)}'",
        date_to=f"'{_ch_literal_ts(date_to)}'",
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


def detect_spoofing(
    date_from: datetime | str | None = None,
    date_to: datetime | str | None = None,
    engine: Engine | None = None,
) -> dict[str, Any]:
    """
    Phase 1 position-anomaly scan: teleport / impossible jumps.

    Date window follows the same 3-day cap as GET /mantis/vessel-track.
    """
    engine = engine or get_pg_engine()
    dt_from, dt_to, range_meta = resolve_track_range(date_from, date_to)

    static = load_cargo_tanker_static(engine)
    raw_hits = load_teleport_hits(dt_from, dt_to)

    raw_hit_count = int(len(raw_hits))
    if raw_hits.empty or static.empty:
        return _empty_result(dt_from, dt_to, range_meta, raw_hit_count)

    merged = raw_hits.merge(static, on="mmsi", how="inner")
    filtered_hit_count = int(len(merged))
    deduped = dedupe_daily(merged)

    deduped = attach_sanctions(deduped, engine)
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
        },
        "raw_hit_count": raw_hit_count,
        "filtered_hit_count": filtered_hit_count,
        "anomaly_count": int(len(deduped)),
        "by_reason": by_reason,
        "sanctions_match_count": sanctions_match_count,
        "anomalies_payload": anomalies_to_payload(deduped),
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
