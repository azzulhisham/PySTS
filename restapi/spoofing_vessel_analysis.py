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

from ais_identity import VALID_SHIP_MMSI_SQL_CH, clean_ship_name, mid_from_mmsi
from api_timestamps import format_last_seen_at
from sanctions import attach_sanctions, payload_fields
from ship_risk_attach import attach_risk_ratings, risk_payload_fields
from spoofing import (
    BAD_DATA_KNOTS,
    DATA_QUALITY_REASONS,
    HIGH_SPEED_MIN_KN,
    LONG_JUMP_DIST_M,
    LONG_JUMP_MIN_KN,
    MAX_DT_S,
    MIN_DIST_M,
    MIN_DT_S,
    PAIR_WINDOW_SQL,
    RULE_VERSION,
    SNAP_BACK_COND_SQL,
    SNAP_BACK_MAX_M,
    SPIKE_SELECT_SQL,
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

_VESSEL_BAD_DATA_WHERE = (
    f"OR (dt_s >= {MIN_DT_S} AND implied_kn > {BAD_DATA_KNOTS})"
    if BAD_DATA_KNOTS > 0
    else ""
)

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
    multiIf(
        dt_s < {MIN_DT_S},
        'bad_position_data',
        {SNAP_BACK_COND_SQL},
        'bad_position_data',
        {BAD_DATA_KNOTS} > 0 AND implied_kn > {BAD_DATA_KNOTS},
        'bad_position_data',
        dist_m >= {LONG_JUMP_DIST_M} AND implied_kn > {LONG_JUMP_MIN_KN},
        'teleport',
        'high_speed'
    ) AS reason
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
        ) / greatest(dateDiff('second', lagInFrame(ts) OVER w, ts), 1)) * 1.94384 AS implied_kn,
{SPIKE_SELECT_SQL}
    FROM (
        SELECT mmsi, ts, latitude, longitude, sog
        FROM pnav.ais_position
        WHERE mmsi = {{mmsi}}
          AND ts >= toDateTime64({{date_from}}, 3)
          AND ts <= toDateTime64({{date_to}}, 3)
{VALID_SHIP_MMSI_SQL_CH}
          {VALID_POSITION_SQL.strip()}
        LIMIT 1 BY mmsi, toStartOfSecond(ts), latitude, longitude
    )
    WINDOW w AS ({PAIR_WINDOW_SQL})
)
WHERE prev_ts IS NOT NULL
  AND dt_s BETWEEN 0 AND {MAX_DT_S}
  AND dist_m >= {MIN_DIST_M}
  AND (
        (dt_s < {MIN_DT_S} AND dist_m >= {LONG_JUMP_DIST_M})
        OR {SNAP_BACK_COND_SQL}
        {_VESSEL_BAD_DATA_WHERE}
        OR (dt_s >= {MIN_DT_S} AND dist_m >= {LONG_JUMP_DIST_M} AND implied_kn > {LONG_JUMP_MIN_KN})
        OR (dt_s >= {MIN_DT_S} AND implied_kn > {HIGH_SPEED_MIN_KN})
  )
ORDER BY ts ASC
SETTINGS max_execution_time = 60
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
    engine: Engine | None = None,
) -> pd.DataFrame:
    """Stored daily hits for one MMSI. The chart track is still loaded separately."""
    engine = engine or get_pg_engine()
    frame = pd.read_sql(
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
                NULL::double precision AS prev_sog,
                NULL::double precision AS curr_sog,
                dist_m,
                dt_s,
                implied_kn,
                reason
            FROM public.ais_positionanomaly
            WHERE mmsi = :mmsi
              AND ts >= :date_from
              AND ts <= :date_to
            ORDER BY ts ASC
            """
        ),
        con=engine,
        params={"mmsi": int(mmsi), "date_from": date_from, "date_to": date_to},
    )
    return frame


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
    if reason in DATA_QUALITY_REASONS:
        return "data quality"
    if reason == "teleport" or implied_kn >= HIGH_SPEED_MIN_KN:
        return "critical"
    if implied_kn >= LONG_JUMP_MIN_KN:
        return "high"
    return "medium"


def _assessment(reason: str, implied_kn: float) -> str:
    if reason == "bad_position_data":
        return "Implausible AIS position data (short-gap jump or single-point snap-back)"
    if reason == "duplicate_mmsi":
        return "MMSI appears to alternate between two distant clusters (shared / duplicate identity)"
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


def _usable_position(lat: Any, lon: Any) -> bool:
    """Drop AIS not-available sentinels (lat 91 / lon 181) and out-of-range coords."""
    try:
        if lat is None or lon is None or pd.isna(lat) or pd.isna(lon):
            return False
        la, lo = float(lat), float(lon)
    except (TypeError, ValueError):
        return False
    if abs(la) > 90 or abs(lo) > 180:
        return False
    if la == 91.0 or lo == 181.0:
        return False
    return True


def build_speed_series(track: pd.DataFrame) -> list[dict[str, Any]]:
    """One row per fix: reported SOG and implied speed from the previous fix.

    Exact same-second same-position duplicates are dropped first. Implied speed
    is only computed when the gap is at least MIN_DT_S (same floor as spoofing).
    """
    if track.empty or "ts" not in track.columns:
        return []

    work = track.copy()
    work = work[work.apply(
        lambda r: _usable_position(r.get("latitude"), r.get("longitude")), axis=1
    )]
    if work.empty:
        return []

    work["_ts"] = pd.to_datetime(work["ts"], utc=True)
    work["_sec"] = work["_ts"].dt.floor("s")
    work = (
        work.sort_values("_ts")
        .drop_duplicates(subset=["mmsi", "_sec", "latitude", "longitude"], keep="first")
        if "mmsi" in work.columns
        else work.sort_values("_ts").drop_duplicates(
            subset=["_sec", "latitude", "longitude"], keep="first"
        )
    )
    work = work.reset_index(drop=True)

    series: list[dict[str, Any]] = []
    prev_lat = prev_lon = prev_ts = None

    for _, row in work.iterrows():
        lat = row.get("latitude")
        lon = row.get("longitude")
        ts = row.get("ts")
        implied = None
        if prev_lat is not None and prev_lon is not None and prev_ts is not None:
            dt_s = (pd.Timestamp(ts) - pd.Timestamp(prev_ts)).total_seconds()
            # Same floor as TELEPORT_HIT_SQL — sub-10s gaps produced million-kn spikes.
            if dt_s is not None and dt_s >= MIN_DT_S:
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
    risk_row: pd.Series | None = None,
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
        raw_name = static_row.get("shipname") if pd.notna(static_row.get("shipname")) else None
        header["shipName"] = clean_ship_name(raw_name)
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
    # Identity risk after sanctions; GREEN/0/none when no static or attach failed.
    header.update(risk_payload_fields(risk_row if risk_row is not None else {}))
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
    risk_row = None
    if static_row is not None:
        labelled = attach_sanctions(static_df.copy(), engine)
        labelled = attach_risk_ratings(labelled, engine)
        sanctions_row = labelled.iloc[0]
        risk_row = labelled.iloc[0]
    else:
        # Still emit GREEN defaults on the vessel header when static is missing.
        risk_row = None

    hits = load_vessel_teleport_hits(mmsi, dt_from, dt_to, engine=engine)
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
            "badDataKnots": BAD_DATA_KNOTS,
            "snapBackMaxM": SNAP_BACK_MAX_M,
        },
        "vessel": _vessel_header(mmsi, static_row, mid, flag_name, sanctions_row, risk_row),
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
