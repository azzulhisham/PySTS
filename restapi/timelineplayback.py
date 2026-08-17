"""
Vessel activity timeline (PostgreSQL) and track replay (ClickHouse).

Designed for use from restapi/main.py endpoints:

    GET /mantis/vessel-timeline?mmsi=...&from=...&to=...
    GET /mantis/vessel-track?mmsi=...&from=...&to=...   (from/to optional; max 3 days; NDJSON stream)

PostgreSQL holds derived activity segments (zones, stops, slow speed, static changes).
ClickHouse holds the full AIS position history used for map replay.

Track replay only returns rows with a usable fix: AIS "not available" positions
(latitude 91 / longitude 181) are filtered out in ClickHouse. See
VALID_POSITION_SQL.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Iterator
from urllib.parse import quote

import clickhouse_connect
import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

_pswd = os.environ.get("pnav_db_password", "m4r1t1m3")
DATABASE_URL = (
    f"postgresql://postgresadmin:{quote(_pswd)}"
    f"@marineai2.cxwk8yige5f2.ap-southeast-5.rds.amazonaws.com:5432/pnav"
)

CLICKHOUSE_HOST = os.environ.get("clickhouse_host", "43.216.85.155")
CLICKHOUSE_USER = os.environ.get("clickhouse_user", "default")
CLICKHOUSE_PASSWORD = os.environ.get("clickhouse_password", "")

MAX_TRACK_RANGE = timedelta(days=3)
TRACK_CHUNK_MINUTES = 20

# ---------------------------------------------------------------------------
# SQL 1 — "What happened to vessel X between date A and date B?"
# PostgreSQL: union of derived activity / event tables, ordered by time.
# ---------------------------------------------------------------------------
VESSEL_ACTIVITY_TIMELINE_SQL = """
WITH params AS (
    SELECT
        CAST(:mmsi AS bigint) AS mmsi,
        CAST(:date_from AS timestamptz) AS date_from,
        CAST(:date_to AS timestamptz) AS date_to
),
zone_events AS (
    SELECT
        vz."tsDetected" AS event_time,
        COALESCE(vz."tsOut", vz."tsCurrent") AS event_end,
        'ZONE_VISIT' AS event_type,
        'ais_vesselinzone' AS event_source,
        vz.mmsi,
        format('Zone %s visit', vz.zone) AS title,
        vz.latitude,
        vz.longitude,
        jsonb_build_object(
            'zone', vz.zone,
            'navStatus', vz."navStatus",
            'navStatusDesc', vz."navStatusDesc",
            'shipName', vz."shipName",
            'destination', vz.destination,
            'draught', vz.draught,
            'tsCurrent', vz."tsCurrent",
            'tsOut', vz."tsOut"
        ) AS details
    FROM public.ais_vesselinzone vz
    CROSS JOIN params p
    WHERE vz.mmsi = p.mmsi
      AND vz."tsDetected" <= p.date_to
      AND COALESCE(vz."tsOut", vz."tsCurrent", NOW()) >= p.date_from
),
restrict_zone_events AS (
    SELECT
        rz."tsDetected" AS event_time,
        COALESCE(rz."tsOut", rz."tsCurrent") AS event_end,
        'RESTRICTED_ZONE_VISIT' AS event_type,
        'ais_vesselinrestrictzone' AS event_source,
        rz.mmsi,
        format('Restricted zone %s visit', rz.zone) AS title,
        rz.latitude,
        rz.longitude,
        jsonb_build_object(
            'zone', rz.zone,
            'navStatus', rz."navStatus",
            'navStatusDesc', rz."navStatusDesc",
            'sog', rz.sog,
            'cog', rz.cog,
            'tsCurrent', rz."tsCurrent",
            'tsOut', rz."tsOut"
        ) AS details
    FROM public.ais_vesselinrestrictzone rz
    CROSS JOIN params p
    WHERE rz.mmsi = p.mmsi
      AND rz."tsDetected" <= p.date_to
      AND COALESCE(rz."tsOut", rz."tsCurrent", NOW()) >= p.date_from
),
stop_events AS (
    SELECT
        a.ts AS event_time,
        COALESCE(a.tsout, a.tscurrent) AS event_end,
        CASE
            WHEN a.tsstop IS NOT NULL THEN 'STOPPED'
            ELSE 'LOW_SPEED_OR_STOPPING'
        END AS event_type,
        'ais_vesselmovementactivities' AS event_source,
        a.mmsi,
        CASE
            WHEN a.tsstop IS NOT NULL THEN 'Vessel stopped / anchored'
            ELSE 'Vessel low speed (<= 0.5 kn)'
        END AS title,
        COALESCE(a.curlatitude, a.latitude) AS latitude,
        COALESCE(a.curlongitude, a.longitude) AS longitude,
        jsonb_build_object(
            'navStatus', a.navstatus,
            'navStatusDesc', a.navstatusdesc,
            'sog', COALESCE(a.cursog, a.sog),
            'cog', COALESCE(a.curcog, a.cog),
            'rowCount', a.rowcount,
            'distanceM', a.distance,
            'tsStop', a.tsstop,
            'tsCurrent', a.tscurrent,
            'tsOut', a.tsout
        ) AS details
    FROM public.ais_vesselmovementactivities a
    CROSS JOIN params p
    WHERE a.mmsi = p.mmsi
      AND a.ts <= p.date_to
      AND COALESCE(a.tsout, a.tscurrent, NOW()) >= p.date_from
),
slow_move_events AS (
    SELECT
        a.ts AS event_time,
        COALESCE(a.tsout, a.tscurrent) AS event_end,
        CASE
            WHEN a.tsstop IS NOT NULL THEN 'SLOW_MOVE_STOPPED'
            ELSE 'SLOW_SPEED'
        END AS event_type,
        'ais_vesselslowmoveactivities' AS event_source,
        a.mmsi,
        CASE
            WHEN a.tsstop IS NOT NULL THEN 'Slow speed then stopped (<= 3 kn)'
            ELSE 'Slow speed period (<= 3 kn)'
        END AS title,
        COALESCE(a.curlatitude, a.latitude) AS latitude,
        COALESCE(a.curlongitude, a.longitude) AS longitude,
        jsonb_build_object(
            'navStatus', a.navstatus,
            'navStatusDesc', a.navstatusdesc,
            'sog', COALESCE(a.cursog, a.sog),
            'cog', COALESCE(a.curcog, a.cog),
            'rowCount', a.rowcount,
            'distanceM', a.distance,
            'tsStop', a.tsstop,
            'tsCurrent', a.tscurrent,
            'tsOut', a.tsout
        ) AS details
    FROM public.ais_vesselslowmoveactivities a
    CROSS JOIN params p
    WHERE a.mmsi = p.mmsi
      AND a.ts <= p.date_to
      AND COALESCE(a.tsout, a.tscurrent, NOW()) >= p.date_from
),
static_events AS (
    SELECT
        e.ts AS event_time,
        NULL::timestamptz AS event_end,
        upper(e.detchg) || '_CHANGED' AS event_type,
        'ais_static_evt' AS event_source,
        p.mmsi,
        format('%s changed: %s -> %s', e.detchg, e.prev, e.cur) AS title,
        NULL::double precision AS latitude,
        NULL::double precision AS longitude,
        jsonb_build_object(
            'imo', e.imo,
            'field', e.detchg,
            'previousValue', e.prev,
            'currentValue', e.cur
        ) AS details
    FROM public.ais_static_evt e
    CROSS JOIN params p
    LEFT JOIN public.ais_static s ON s.mmsi = p.mmsi
    WHERE e.ts >= p.date_from
      AND e.ts <= p.date_to
      AND (
          (s.imo IS NOT NULL AND e.imo = s.imo)
          OR (e.detchg = 'mmsi' AND (e.prev = p.mmsi::text OR e.cur = p.mmsi::text))
      )
)
SELECT
    event_time,
    event_end,
    event_type,
    event_source,
    mmsi,
    title,
    latitude,
    longitude,
    details
FROM zone_events
UNION ALL
SELECT * FROM restrict_zone_events
UNION ALL
SELECT * FROM stop_events
UNION ALL
SELECT * FROM slow_move_events
UNION ALL
SELECT * FROM static_events
ORDER BY event_time ASC, event_type ASC
"""

# ---------------------------------------------------------------------------
# SQL 2 — "Show me the full track and replay movement over that period"
# ClickHouse: ordered AIS position reports for map playback.
# Class A (ais_position). Optional Class B (ais_type18) via include_class_b.
#
# Raw AIS encodes "position not available" as latitude 91 / longitude 181, and
# those rows are stored verbatim (~1.3% of ais_position). They cannot be plotted
# on any map, so the track endpoints drop them here rather than leaving every
# client to recognise the sentinels. The range test also discards NaN and +/-Inf,
# which ClickHouse treats as outside any BETWEEN.
# ---------------------------------------------------------------------------
VALID_POSITION_SQL = """
  AND latitude BETWEEN -90 AND 90
  AND longitude BETWEEN -180 AND 180
"""

VESSEL_TRACK_REPLAY_SQL_CLASS_A = """
SELECT
    ts,
    mmsi,
    navStatus,
    navStatusDesc,
    longitude,
    latitude,
    rot,
    cog,
    sog,
    trueHeading,
    'class_a' AS ais_class
FROM pnav.ais_position
WHERE mmsi = {mmsi}
  AND ts >= toDateTime64({date_from}, 3)
  AND ts {ts_end_op} toDateTime64({date_to}, 3)
  {position_filter}
ORDER BY ts ASC
"""

VESSEL_TRACK_REPLAY_SQL_CLASS_B = """
SELECT
    ts,
    mmsi,
    0 AS navStatus,
    '' AS navStatusDesc,
    longitude,
    latitude,
    0 AS rot,
    cog,
    sog,
    trueHeading,
    'class_b' AS ais_class
FROM pnav.ais_type18
WHERE mmsi = {mmsi}
  AND ts >= toDateTime64({date_from}, 3)
  AND ts {ts_end_op} toDateTime64({date_to}, 3)
  {position_filter}
ORDER BY ts ASC
"""


def get_pg_engine() -> Engine:
    return create_engine(
        DATABASE_URL,
        pool_size=5,
        max_overflow=10,
        pool_timeout=30,
    )


def get_clickhouse_client():
    kwargs: dict[str, Any] = {
        "host": CLICKHOUSE_HOST,
        "user": CLICKHOUSE_USER,
    }
    if CLICKHOUSE_PASSWORD:
        kwargs["password"] = CLICKHOUSE_PASSWORD
    return clickhouse_connect.get_client(**kwargs)


def parse_datetime(value: str | datetime) -> datetime:
    """Parse API datetime input; assume UTC when timezone is omitted."""
    if isinstance(value, datetime):
        dt = value
    else:
        raw = value.strip()
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        dt = datetime.fromisoformat(raw)

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _fmt_ts(value: Any) -> str | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if pd.isna(value):
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()
    return str(value)


def _ch_literal_ts(dt: datetime) -> str:
    """ClickHouse toDateTime64 literal (UTC, millisecond precision)."""
    dt = parse_datetime(dt)
    return dt.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def load_vessel_activity_timeline(
    mmsi: int,
    date_from: datetime,
    date_to: datetime,
    engine: Engine | None = None,
) -> pd.DataFrame:
    engine = engine or get_pg_engine()
    params = {
        "mmsi": int(mmsi),
        "date_from": parse_datetime(date_from),
        "date_to": parse_datetime(date_to),
    }
    return pd.read_sql(
        text(VESSEL_ACTIVITY_TIMELINE_SQL),
        con=engine,
        params=params,
    )


def _is_blank(value: datetime | str | None) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    return False


def resolve_track_range(
    date_from: datetime | str | None = None,
    date_to: datetime | str | None = None,
) -> tuple[datetime, datetime, dict[str, Any]]:
    """
    Effective [from, to] for track replay. Both bounds are optional and the
    window is never wider than 3 days.

    both omitted    → the last 3 days, ending now (UTC)
    to omitted      → now, or from + 3 days when that is earlier (rangeCapped)
    from omitted    → 3 days before the effective to
    both given      → to clamped to from + 3 days when the span is longer

    A `from` in the future is left alone and paired with from + 3 days; there is
    no data there, but it is not an error.
    """
    now = datetime.now(timezone.utc)
    from_omitted = _is_blank(date_from)
    to_omitted = _is_blank(date_to)

    dt_from = None if from_omitted else parse_datetime(date_from)
    dt_to = None if to_omitted else parse_datetime(date_to)
    requested_from = None if dt_from is None else dt_from.isoformat()
    requested_to = None if dt_to is None else dt_to.isoformat()
    range_capped = False

    if dt_from is None and dt_to is None:
        dt_to = now
        dt_from = dt_to - MAX_TRACK_RANGE
    elif dt_to is None:
        capped_to = dt_from + MAX_TRACK_RANGE
        if capped_to <= now:
            # The 3-day cap ends the window before now; more data exists after.
            dt_to = capped_to
            range_capped = True
        elif dt_from < now:
            dt_to = now
        else:
            dt_to = capped_to
    elif dt_from is None:
        dt_from = dt_to - MAX_TRACK_RANGE
    else:
        if dt_from > dt_to:
            raise ValueError("date_from must be before or equal to date_to")
        if dt_to - dt_from > MAX_TRACK_RANGE:
            dt_to = dt_from + MAX_TRACK_RANGE
            range_capped = True

    return dt_from, dt_to, {
        "fromOmitted": from_omitted,
        "toOmitted": to_omitted,
        "rangeCapped": range_capped,
        "maxRangeDays": MAX_TRACK_RANGE.days,
        "chunkMinutes": TRACK_CHUNK_MINUTES,
        "requestedDateFrom": requested_from,
        "requestedDateTo": requested_to,
    }


def _track_window_bounds(dt_from: datetime, dt_to: datetime) -> Iterator[tuple[datetime, datetime, bool]]:
    """Yield (start, end, end_exclusive) 20-minute windows covering [dt_from, dt_to]."""
    chunk = timedelta(minutes=TRACK_CHUNK_MINUTES)
    cursor = dt_from
    while cursor < dt_to:
        nxt = min(cursor + chunk, dt_to)
        yield cursor, nxt, nxt < dt_to
        cursor = nxt


def load_vessel_track_replay(
    mmsi: int,
    date_from: datetime,
    date_to: datetime,
    include_class_b: bool = False,
    client=None,
    end_exclusive: bool = False,
) -> pd.DataFrame:
    client = client or get_clickhouse_client()
    mmsi_int = int(mmsi)
    ts_from = _ch_literal_ts(date_from)
    ts_to = _ch_literal_ts(date_to)
    ts_end_op = "<" if end_exclusive else "<="

    qry_a = VESSEL_TRACK_REPLAY_SQL_CLASS_A.format(
        mmsi=mmsi_int,
        date_from=f"'{ts_from}'",
        date_to=f"'{ts_to}'",
        ts_end_op=ts_end_op,
        position_filter=VALID_POSITION_SQL.strip(),
    )
    result_a = client.query(qry_a)

    rows: list[tuple[Any, ...]] = list(result_a.result_rows)
    columns = list(result_a.column_names)

    if include_class_b:
        qry_b = VESSEL_TRACK_REPLAY_SQL_CLASS_B.format(
            mmsi=mmsi_int,
            date_from=f"'{ts_from}'",
            date_to=f"'{ts_to}'",
            ts_end_op=ts_end_op,
            position_filter=VALID_POSITION_SQL.strip(),
        )
        result_b = client.query(qry_b)
        rows.extend(result_b.result_rows)

    if not rows:
        return pd.DataFrame(columns=columns)

    df = pd.DataFrame(rows, columns=columns)
    if "ts" in df.columns:
        df["ts"] = pd.to_datetime(df["ts"], utc=True)
    if include_class_b and not df.empty:
        df = df.sort_values("ts").reset_index(drop=True)
    return df


def timeline_to_payload(events: pd.DataFrame) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    if events.empty:
        return records

    for _, row in events.iterrows():
        details = row.get("details")
        if isinstance(details, str):
            try:
                details = json.loads(details)
            except json.JSONDecodeError:
                pass

        records.append({
            "eventTime": _fmt_ts(row.get("event_time")),
            "eventEnd": _fmt_ts(row.get("event_end")),
            "eventType": row.get("event_type"),
            "eventSource": row.get("event_source"),
            "mmsi": int(row["mmsi"]) if pd.notna(row.get("mmsi")) else None,
            "title": row.get("title"),
            "latitude": float(row["latitude"]) if pd.notna(row.get("latitude")) else None,
            "longitude": float(row["longitude"]) if pd.notna(row.get("longitude")) else None,
            "details": details if isinstance(details, dict) else row.get("details"),
        })
    return records


def track_to_payload(track: pd.DataFrame) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    if track.empty:
        return records

    for _, row in track.iterrows():
        records.append({
            "ts": _fmt_ts(row.get("ts")),
            "mmsi": int(row["mmsi"]) if pd.notna(row.get("mmsi")) else None,
            "latitude": float(row["latitude"]) if pd.notna(row.get("latitude")) else None,
            "longitude": float(row["longitude"]) if pd.notna(row.get("longitude")) else None,
            "sog": float(row["sog"]) if pd.notna(row.get("sog")) else None,
            "cog": float(row["cog"]) if pd.notna(row.get("cog")) else None,
            "rot": float(row["rot"]) if pd.notna(row.get("rot")) else None,
            "trueHeading": float(row["trueHeading"]) if pd.notna(row.get("trueHeading")) else None,
            "navStatus": int(row["navStatus"]) if pd.notna(row.get("navStatus")) else None,
            "navStatusDesc": row.get("navStatusDesc") if pd.notna(row.get("navStatusDesc")) else None,
            "aisClass": row.get("ais_class"),
        })
    return records


def get_vessel_activity_timeline(
    mmsi: int,
    date_from: datetime | str,
    date_to: datetime | str,
    engine: Engine | None = None,
) -> dict[str, Any]:
    """
    Return derived activity/events for one vessel in [date_from, date_to].

    Intended for:
        GET /mantis/vessel-timeline?mmsi=533000123&from=2026-06-10T00:00:00Z&to=2026-06-12T00:00:00Z
    """
    dt_from = parse_datetime(date_from)
    dt_to = parse_datetime(date_to)
    if dt_from > dt_to:
        raise ValueError("date_from must be before or equal to date_to")

    df = load_vessel_activity_timeline(mmsi, dt_from, dt_to, engine=engine)
    events = timeline_to_payload(df)

    by_type: dict[str, int] = {}
    for event in events:
        key = str(event.get("eventType") or "UNKNOWN")
        by_type[key] = by_type.get(key, 0) + 1

    return {
        "mmsi": int(mmsi),
        "dateFrom": dt_from.isoformat(),
        "dateTo": dt_to.isoformat(),
        "eventCount": len(events),
        "byEventType": by_type,
        "events": events,
    }


def get_vessel_track_replay(
    mmsi: int,
    date_from: datetime | str | None = None,
    date_to: datetime | str | None = None,
    include_class_b: bool = False,
    client=None,
    max_points: int | None = None,
) -> dict[str, Any]:
    """
    Assemble a JSON track payload from 20-minute ClickHouse windows.

    Used for Swagger's 20-point sample. Production clients should use
    iter_vessel_track_ndjson so points are not held in memory at once.
    """
    dt_from, dt_to, range_meta = resolve_track_range(date_from, date_to)
    own_client = client is None
    client = client or get_clickhouse_client()
    points: list[dict[str, Any]] = []
    try:
        for start, end, exclusive in _track_window_bounds(dt_from, dt_to):
            df = load_vessel_track_replay(
                mmsi,
                start,
                end,
                include_class_b=include_class_b,
                client=client,
                end_exclusive=exclusive,
            )
            chunk_points = track_to_payload(df)
            del df
            if not chunk_points:
                continue
            remaining = None if max_points is None else max_points - len(points)
            if remaining is not None:
                points.extend(chunk_points[:remaining])
                if len(points) >= max_points:
                    break
            else:
                points.extend(chunk_points)
    finally:
        if own_client:
            try:
                client.close()
            except Exception:
                pass

    duration_seconds = None
    if len(points) >= 2:
        first_ts = parse_datetime(points[0]["ts"])
        last_ts = parse_datetime(points[-1]["ts"])
        duration_seconds = max((last_ts - first_ts).total_seconds(), 0.0)

    return {
        "mmsi": int(mmsi),
        "dateFrom": dt_from.isoformat(),
        "dateTo": dt_to.isoformat(),
        "includeClassB": include_class_b,
        "pointCount": len(points),
        "durationSeconds": duration_seconds,
        "track": points,
        **range_meta,
    }


def iter_vessel_track_ndjson(
    mmsi: int,
    date_from: datetime | str | None = None,
    date_to: datetime | str | None = None,
    include_class_b: bool = False,
) -> Iterator[str]:
    """
    Yield NDJSON lines: one meta record, then one chunk per 20-minute
    ClickHouse window that has points, then a done record.

    Each ClickHouse window is discarded after it is yielded so the process
    does not hold the full 3-day track.
    """
    dt_from, dt_to, range_meta = resolve_track_range(date_from, date_to)
    client = get_clickhouse_client()
    point_count = 0
    chunk_index = 0
    windows_scanned = 0
    try:
        yield json.dumps({
            "type": "meta",
            "mmsi": int(mmsi),
            "dateFrom": dt_from.isoformat(),
            "dateTo": dt_to.isoformat(),
            "includeClassB": include_class_b,
            **range_meta,
        }, separators=(",", ":")) + "\n"

        for start, end, exclusive in _track_window_bounds(dt_from, dt_to):
            windows_scanned += 1
            df = load_vessel_track_replay(
                mmsi,
                start,
                end,
                include_class_b=include_class_b,
                client=client,
                end_exclusive=exclusive,
            )
            points = track_to_payload(df)
            del df
            if not points:
                continue
            point_count += len(points)
            yield json.dumps({
                "type": "chunk",
                "chunkIndex": chunk_index,
                "chunkFrom": start.isoformat(),
                "chunkTo": end.isoformat(),
                "pointCount": len(points),
                "points": points,
            }, separators=(",", ":")) + "\n"
            chunk_index += 1
            del points

        yield json.dumps({
            "type": "done",
            "pointCount": point_count,
            "chunkCount": chunk_index,
            "windowsScanned": windows_scanned,
        }, separators=(",", ":")) + "\n"
    except Exception:
        logging.exception("[iter_vessel_track_ndjson] ClickHouse/stream error")
        yield json.dumps({
            "type": "error",
            "message": "Internal server error",
        }, separators=(",", ":")) + "\n"
    finally:
        try:
            client.close()
        except Exception:
            pass

