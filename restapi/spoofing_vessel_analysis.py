"""
Per-vessel spoofing drill-down (companion to GET /mantis/spoofing fleet view).

GET /mantis/spoofing/vessel-analysis — one MMSI, all teleport hits in window
(no daily dedupe), flag from public.ais_flagname (MID from MMSI), optional
track and implied-vs-SOG series for charts/maps.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any
import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

from api_timestamps import format_last_seen_at
from sanctions import attach_sanctions, payload_fields
from spoofing import (
    HIGH_SPEED_MIN_KN,
    LONG_JUMP_DIST_M,
    LONG_JUMP_MIN_KN,
    MAX_DT_S,
    MIN_DIST_M,
    MIN_DT_S,
    RULE_VERSION,
    get_pg_engine,
)
from timelineplayback import (
    VALID_POSITION_SQL,
    _ch_literal_ts,
    get_clickhouse_client,
    load_vessel_track_replay,
    resolve_track_range,
    track_to_payload,
)
from vessel_size import (
    CURRENT_STATIC_ROW_ORDER,
    DIM_SELECT,
    class_b_join,
    dimension_fields,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

VESSEL_ANALYSIS_RULE = "All consecutive-fix teleport hits for one MMSI (no daily dedupe)."

VESSEL_TELEPORT_HIT_SQL = f"""
SELECT
    mmsi,
    ts,
    prev_ts,
    prev_lat,
    prev_lon,
    latitude,
    longitude,
    prev_sog,
    curr_sog,
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
        lagInFrame(sog) OVER w AS prev_sog,
        sog AS curr_sog,
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
    WHERE mmsi = {{mmsi}}
      AND ts >= toDateTime64({{date_from}}, 3)
      AND ts <= toDateTime64({{date_to}}, 3)
      {VALID_POSITION_SQL.strip()}
    WINDOW w AS (PARTITION BY mmsi ORDER BY ts ROWS BETWEEN 1 PRECEDING AND CURRENT ROW)
)
WHERE prev_ts IS NOT NULL
  AND dt_s BETWEEN {MIN_DT_S} AND {MAX_DT_S}
  AND dist_m >= {MIN_DIST_M}
  AND ((dist_m >= {LONG_JUMP_DIST_M} AND implied_kn > {LONG_JUMP_MIN_KN}) OR implied_kn > {HIGH_SPEED_MIN_KN})
ORDER BY ts ASC
"""

VESSEL_STATIC_SQL = text(f"""
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
    WHERE mmsi = :mmsi
) s
{class_b_join("s.mmsi")}
WHERE s.rowcount_static = 1
""")

FLAGNAME_SQL = text("""
    SELECT mid, flagname
    FROM public.ais_flagname
    WHERE mid = :mid
    LIMIT 1
""")


def mid_from_mmsi(mmsi: int) -> int | None:
    """Maritime identification digits from a 9-digit MMSI (VT Explorer MID rules)."""
    mmsi9 = str(int(mmsi)).strip()
    if len(mmsi9) < 9:
        mmsi9 = mmsi9.zfill(9)
    elif len(mmsi9) > 9:
        mmsi9 = mmsi9[-9:]
    if len(mmsi9) != 9 or not mmsi9.isdigit():
        return None
    if mmsi9[:2] in ("00", "99", "96"):
        return int(mmsi9[2:5])
    return int(mmsi9[:3])


def _usable_sog(value: Any) -> float | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    try:
        sog = float(value)
    except (TypeError, ValueError):
        return None
    if sog >= 102.0:
        return None
    return sog


def load_flagname(engine: Engine, mmsi: int) -> tuple[int | None, str | None]:
    mid = mid_from_mmsi(mmsi)
    if mid is None:
        return None, None
    row = pd.read_sql(FLAGNAME_SQL, con=engine, params={"mid": mid})
    if row.empty:
        return mid, None
    name = row.iloc[0]["flagname"]
    if pd.isna(name) or not str(name).strip():
        return mid, None
    return mid, str(name).strip()


def load_vessel_static(engine: Engine, mmsi: int) -> pd.DataFrame:
    return pd.read_sql(VESSEL_STATIC_SQL, con=engine, params={"mmsi": int(mmsi)})


def load_vessel_teleport_hits(
    mmsi: int,
    date_from: datetime,
    date_to: datetime,
    client=None,
) -> pd.DataFrame:
    client = client or get_clickhouse_client()
    query = VESSEL_TELEPORT_HIT_SQL.format(
        mmsi=int(mmsi),
        date_from=f"'{_ch_literal_ts(date_from)}'",
        date_to=f"'{_ch_literal_ts(date_to)}'",
    )
    result = client.query(query)
    if not result.row_count:
        return pd.DataFrame(columns=result.column_names)
    return pd.DataFrame(result.result_rows, columns=result.column_names)


def count_ais_positions(
    mmsi: int,
    date_from: datetime,
    date_to: datetime,
    client=None,
) -> int:
    client = client or get_clickhouse_client()
    query = f"""
        SELECT count() AS cnt
        FROM pnav.ais_position
        WHERE mmsi = {int(mmsi)}
          AND ts >= toDateTime64('{_ch_literal_ts(date_from)}', 3)
          AND ts <= toDateTime64('{_ch_literal_ts(date_to)}', 3)
          {VALID_POSITION_SQL.strip()}
    """
    result = client.query(query)
    if not result.result_rows:
        return 0
    return int(result.result_rows[0][0])


def _risk_level(implied_kn: float, reason: str) -> str:
    if reason == "teleport" or implied_kn >= HIGH_SPEED_MIN_KN:
        return "critical"
    if implied_kn >= LONG_JUMP_MIN_KN:
        return "high"
    return "medium"


def _assessment(reason: str, implied_kn: float) -> str:
    if reason == "teleport":
        return "Possible spoofing / position jump"
    if implied_kn >= HIGH_SPEED_MIN_KN:
        return "Unrealistic implied speed between consecutive fixes"
    return "Elevated implied speed between consecutive fixes"


def events_to_payload(events: pd.DataFrame) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    if events.empty:
        return records

    for event_index, (_, row) in enumerate(events.iterrows()):
        implied = float(row["implied_kn"]) if pd.notna(row.get("implied_kn")) else None
        curr_sog = _usable_sog(row.get("curr_sog"))
        prev_sog = _usable_sog(row.get("prev_sog"))
        deviation = None
        deviation_pct = None
        if implied is not None and curr_sog is not None:
            deviation = round(implied - curr_sog, 1)
            if curr_sog > 0:
                deviation_pct = round(100.0 * (implied - curr_sog) / curr_sog, 1)

        reason = str(row.get("reason") or "")
        risk = _risk_level(implied, reason) if implied is not None else "medium"

        records.append({
            "eventIndex": event_index,
            "mmsi": int(row["mmsi"]),
            "reason": reason,
            "riskLevel": risk,
            "assessment": _assessment(reason, implied or 0.0),
            "startAt": format_last_seen_at(row.get("prev_ts")),
            "endAt": format_last_seen_at(row.get("ts")),
            "prevLatitude": float(row["prev_lat"]) if pd.notna(row.get("prev_lat")) else None,
            "prevLongitude": float(row["prev_lon"]) if pd.notna(row.get("prev_lon")) else None,
            "latitude": float(row["latitude"]) if pd.notna(row.get("latitude")) else None,
            "longitude": float(row["longitude"]) if pd.notna(row.get("longitude")) else None,
            "distanceM": round(float(row["dist_m"]), 1) if pd.notna(row.get("dist_m")) else None,
            "distanceNm": round(float(row["dist_m"]) / 1852.0, 2) if pd.notna(row.get("dist_m")) else None,
            "deltaSeconds": int(row["dt_s"]) if pd.notna(row.get("dt_s")) else None,
            "impliedSpeedKn": round(implied, 1) if implied is not None else None,
            "aisSogKn": round(curr_sog, 1) if curr_sog is not None else None,
            "prevAisSogKn": round(prev_sog, 1) if prev_sog is not None else None,
            "deviationKn": deviation,
            "deviationPct": deviation_pct,
        })
    return records


def build_speed_series(track: pd.DataFrame) -> list[dict[str, Any]]:
    """One row per fix: reported SOG and implied speed from the previous fix."""
    if track.empty or "ts" not in track.columns:
        return []

    work = track.sort_values("ts").reset_index(drop=True)
    series: list[dict[str, Any]] = []
    prev_lat = prev_lon = prev_ts = None

    for _, row in work.iterrows():
        lat = row.get("latitude")
        lon = row.get("longitude")
        ts = row.get("ts")
        implied = None
        if prev_lat is not None and prev_lon is not None and prev_ts is not None:
            dt_s = (pd.Timestamp(ts) - pd.Timestamp(prev_ts)).total_seconds()
            if dt_s and dt_s > 0:
                duckdb_implied = _implied_kn_haversine(
                    float(prev_lon), float(prev_lat), float(lon), float(lat), dt_s
                )
                implied = round(duckdb_implied, 1) if duckdb_implied is not None else None

        sog = _usable_sog(row.get("sog"))
        series.append({
            "ts": format_last_seen_at(ts),
            "sog": round(sog, 1) if sog is not None else None,
            "impliedSpeedKn": implied,
        })
        prev_lat, prev_lon, prev_ts = lat, lon, ts

    return series


def _implied_kn_haversine(lon1: float, lat1: float, lon2: float, lat2: float, dt_s: float) -> float | None:
    import math

    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    dist_m = 2 * r * math.asin(min(1.0, math.sqrt(a)))
    return (dist_m / dt_s) * 1.94384


def _vessel_header(
    mmsi: int,
    static_row: pd.Series | None,
    mid: int | None,
    flag_name: str | None,
    sanctions_row: pd.Series | None,
) -> dict[str, Any]:
    header: dict[str, Any] = {
        "mmsi": mmsi,
        "mid": mid,
        "flagName": flag_name,
        "shipName": None,
        "shipType": None,
        "shipTypeDesc": None,
        "imo": None,
        "cargoTankerEligible": False,
    }
    if static_row is not None:
        header["shipName"] = static_row.get("shipname") if pd.notna(static_row.get("shipname")) else None
        st = static_row.get("shiptype")
        header["shipType"] = int(st) if pd.notna(st) else None
        header["shipTypeDesc"] = (
            static_row.get("shiptypedesc") if pd.notna(static_row.get("shiptypedesc")) else None
        )
        header["imo"] = static_row.get("imo") if pd.notna(static_row.get("imo")) else None
        if header["shipType"] is not None and 70 <= header["shipType"] < 90:
            header["cargoTankerEligible"] = True
        header.update(dimension_fields(static_row))
    if sanctions_row is not None:
        header.update(payload_fields(sanctions_row))
    return header


def detect_vessel_spoofing_analysis(
    mmsi: int,
    date_from: datetime | str | None = None,
    date_to: datetime | str | None = None,
    *,
    include_track: bool = True,
    include_speed_series: bool = True,
    engine: Engine | None = None,
    client=None,
) -> dict[str, Any]:
    engine = engine or get_pg_engine()
    client = client or get_clickhouse_client()
    dt_from, dt_to, range_meta = resolve_track_range(date_from, date_to)

    mid, flag_name = load_flagname(engine, mmsi)
    static_df = load_vessel_static(engine, mmsi)
    static_row = static_df.iloc[0] if not static_df.empty else None

    sanctions_row = None
    if static_row is not None:
        labelled = attach_sanctions(static_df.copy(), engine)
        sanctions_row = labelled.iloc[0]

    hits = load_vessel_teleport_hits(mmsi, dt_from, dt_to, client=client)
    position_count = count_ais_positions(mmsi, dt_from, dt_to, client=client)

    events_payload = events_to_payload(hits)
    max_implied = None
    max_deviation = None
    if not hits.empty:
        max_implied = round(float(hits["implied_kn"].max()), 1)
        deviations = []
        for _, row in hits.iterrows():
            implied = float(row["implied_kn"])
            sog = _usable_sog(row.get("curr_sog"))
            if sog is not None:
                deviations.append(implied - sog)
        if deviations:
            max_deviation = round(max(deviations), 1)

    track_payload: list[dict[str, Any]] = []
    speed_series: list[dict[str, Any]] = []
    reference_max_sog = None
    if include_track or include_speed_series:
        track_df = load_vessel_track_replay(mmsi, dt_from, dt_to, client=client)
        if include_track:
            track_payload = track_to_payload(track_df)
        if include_speed_series:
            speed_series = build_speed_series(track_df)
        sogs = [_usable_sog(v) for v in track_df.get("sog", pd.Series(dtype=float))]
        sogs = [s for s in sogs if s is not None]
        if sogs:
            reference_max_sog = round(max(sogs), 1)

    by_reason: dict[str, int] = {}
    if not hits.empty:
        for reason, cnt in hits["reason"].value_counts().items():
            by_reason[str(reason)] = int(cnt)

    return {
        "rule_version": RULE_VERSION,
        "analysis_rule": VESSEL_ANALYSIS_RULE,
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
        "vessel": _vessel_header(mmsi, static_row, mid, flag_name, sanctions_row),
        "summary": {
            "totalAisPositions": position_count,
            "eventCount": len(events_payload),
            "maxImpliedSpeedKn": max_implied,
            "maxDeviationKn": max_deviation,
            "referenceMaxSogKn": reference_max_sog,
            "byReason": by_reason,
        },
        "events_payload": events_payload,
        "track_payload": track_payload,
        "speed_series_payload": speed_series,
    }
