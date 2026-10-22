#!/usr/bin/env python3
"""
AIS identity risk job: Lloyd's mismatch snapshot, identity events, ship ratings.

  python3 backend/identity_risk_job.py
  python3 backend/identity_risk_job.py --lookback-months 24

Idempotent: re-running with unchanged inputs upserts without duplicate rows.
No full AIS position scans. Reads use 15s timeout; write txn may use 120s.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

BACKEND = Path(__file__).resolve().parent
ROOT = BACKEND.parent
RESTAPI = ROOT / "restapi"
if str(RESTAPI) not in sys.path:
    sys.path.insert(0, str(RESTAPI))
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from ais_identity import is_valid_ship_mmsi  # noqa: E402
from identity_risk import (  # noqa: E402
    DEFAULT_LOOKBACK_MONTHS,
    RULE_VERSION,
    is_cargo_tanker_type,
    names_agree,
    normalize_imo_text,
    normalize_name,
    score_ship,
    ship_key,
)
from sanctions import LIST_PRIORITY, load_ofac_vessel_list, normalize_imo, normalize_mmsi  # noqa: E402

pswd = "m4r1t1m3"
DATABASE_URL = (
    f"postgresql://postgresadmin:{quote(pswd)}"
    f"@marineai2.cxwk8yige5f2.ap-southeast-5.rds.amazonaws.com:5432/pnav"
)

QUERY_TIMEOUT_MS = 15_000
INGEST_TIMEOUT_MS = 120_000


def get_engine(*, statement_timeout_ms: int = QUERY_TIMEOUT_MS):
    from sqlalchemy import create_engine

    return create_engine(
        DATABASE_URL,
        pool_size=2,
        max_overflow=0,
        pool_timeout=30,
        connect_args={
            "options": (
                f"-c statement_timeout={int(statement_timeout_ms)} "
                f"-c lock_timeout=5000"
            )
        },
    )


def _execute_values(conn, sql: str, rows: list[tuple], *, page_size: int = 500) -> None:
    if not rows:
        return
    from psycopg2.extras import execute_values

    raw = conn.connection.driver_connection
    with raw.cursor() as cur:
        execute_values(cur, sql, rows, page_size=page_size)


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text_value = str(value).strip()
    return text_value or None


def _norm_key_text(value: str | None) -> str:
    """Empty string for UNIQUE key slots (Postgres UNIQUE treats NULLs as distinct)."""
    return value or ""


def load_sanctions_indexes(engine) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    df = load_ofac_vessel_list(engine)
    by_imo: dict[str, list[str]] = defaultdict(list)
    by_mmsi: dict[str, list[str]] = defaultdict(list)
    if df is None or df.empty:
        return {}, {}
    for _, row in df.iterrows():
        lists = []
        primary = row.get("list_name")
        names = row.get("list_names")
        if isinstance(names, list) and names:
            lists = [str(n) for n in names if n]
        elif primary:
            lists = [str(primary)]
        lists = sorted(set(lists), key=lambda n: LIST_PRIORITY.get(n, 99))
        imo = normalize_imo(row.get("imo"))
        mmsi = normalize_mmsi(row.get("mmsi"))
        if imo:
            for n in lists:
                if n not in by_imo[imo]:
                    by_imo[imo].append(n)
        if mmsi:
            for n in lists:
                if n not in by_mmsi[mmsi]:
                    by_mmsi[mmsi].append(n)
    return dict(by_imo), dict(by_mmsi)


def sanctions_for(
    by_imo: dict[str, list[str]],
    by_mmsi: dict[str, list[str]],
    *,
    imos: list[str | None],
    mmsi: int | None,
) -> tuple[bool, list[str]]:
    lists: list[str] = []
    for imo in imos:
        if not imo:
            continue
        for n in by_imo.get(imo, []):
            if n not in lists:
                lists.append(n)
    if mmsi is not None:
        for n in by_mmsi.get(str(int(mmsi)), []):
            if n not in lists:
                lists.append(n)
    lists = sorted(lists, key=lambda n: LIST_PRIORITY.get(n, 99))
    return bool(lists), lists


def snapshot_lloyds_mismatches(engine, snap_day: date) -> list[dict[str, Any]]:
    from sqlalchemy import text

    sql = """
    WITH s AS (
        SELECT DISTINCT ON (mmsi)
            mmsi, imo, "shipName" AS shipname, "shipType" AS shiptype, ts
        FROM ais_static
        WHERE mmsi IS NOT NULL AND imo IS NOT NULL
        ORDER BY mmsi, ts DESC NULLS LAST
    )
    SELECT s.mmsi, s.imo AS ais_imo, s.shipname AS ais_name, s.shiptype,
           l.imoshipno AS lloyds_imo, l.shipname AS lloyds_name
    FROM s
    JOIN ais_lloyds l
      ON l.maritimemobileserviceidentitymmsinumber = s.mmsi
    WHERE l.imoshipno IS NOT NULL
      AND l.imoshipno <> s.imo
    """
    with engine.connect() as conn:
        rows = conn.execute(text(sql)).mappings().all()

    snaps: list[dict[str, Any]] = []
    for r in rows:
        mmsi = int(r["mmsi"])
        if not is_valid_ship_mmsi(mmsi):
            continue
        ais_imo = normalize_imo_text(r["ais_imo"])
        lloyds_imo = normalize_imo_text(r["lloyds_imo"])
        if not ais_imo or not lloyds_imo or ais_imo == lloyds_imo:
            continue
        agree = names_agree(r["ais_name"], r["lloyds_name"])
        snaps.append(
            {
                "snap_date": snap_day,
                "mmsi": mmsi,
                "ais_imo": ais_imo,
                "lloyds_imo": lloyds_imo,
                "ais_name": normalize_name(r["ais_name"]),
                "lloyds_name": normalize_name(r["lloyds_name"]),
                "name_agree": agree,
                "shiptype": r.get("shiptype"),
            }
        )
    return snaps


def persist_lloyds_snap(conn, snaps: list[dict[str, Any]]) -> int:
    rows = [
        (
            s["snap_date"],
            s["mmsi"],
            s["ais_imo"],
            s["lloyds_imo"],
            s["ais_name"],
            s["lloyds_name"],
            s["name_agree"],
        )
        for s in snaps
    ]
    _execute_values(
        conn,
        """
        INSERT INTO ais_lloyds_mmsi_conflict_snap (
            snap_date, mmsi, ais_imo, lloyds_imo, ais_name, lloyds_name, name_agree
        ) VALUES %s
        ON CONFLICT (snap_date, mmsi, ais_imo, lloyds_imo) DO UPDATE SET
            ais_name = EXCLUDED.ais_name,
            lloyds_name = EXCLUDED.lloyds_name,
            name_agree = EXCLUDED.name_agree
        """,
        rows,
    )
    return len(rows)


def collect_dual_track(engine, by_imo, by_mmsi) -> list[dict[str, Any]]:
    from sqlalchemy import text

    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT a.id, a.mmsi, a.event_day, a.ts, a.prev_ts, a.dist_m,
                       s.imo, s."shipName" AS shipname, s."shipType" AS shiptype
                FROM ais_positionanomaly a
                LEFT JOIN LATERAL (
                    SELECT imo, "shipName", "shipType"
                    FROM ais_static st WHERE st.mmsi = a.mmsi
                    ORDER BY st.ts DESC NULLS LAST LIMIT 1
                ) s ON TRUE
                WHERE a.reason = 'duplicate_mmsi'
                """
            )
        ).mappings().all()
    events = []
    for r in rows:
        mmsi = int(r["mmsi"])
        if not is_valid_ship_mmsi(mmsi):
            continue
        imo = normalize_imo_text(r.get("imo"))
        match, lists = sanctions_for(by_imo, by_mmsi, imos=[imo], mmsi=mmsi)
        ts = r["ts"]
        events.append(
            {
                "event_type": "dual_track_mmsi",
                "confidence": "high",
                "mmsi": mmsi,
                "imo": _norm_key_text(imo),
                "imo_other": "",
                "ship_name": normalize_name(r.get("shipname")),
                "ship_name_other": None,
                "ts_start": ts,
                "ts_end": r.get("prev_ts"),
                "distance_m": float(r["dist_m"]) if r.get("dist_m") is not None else None,
                "evidence": {
                    "eventDay": str(r["event_day"]),
                    "anomalyId": r["id"],
                    "shipType": r.get("shiptype"),
                },
                "source_table": "ais_positionanomaly",
                "source_ref": f"anomaly:{r['id']}",
                "sanctions_match": match,
                "sanctions_lists": lists,
            }
        )
    return events


def collect_lloyds_events(snaps: list[dict[str, Any]], by_imo, by_mmsi) -> list[dict[str, Any]]:
    events = []
    for s in snaps:
        agree = bool(s["name_agree"])
        conf = "low" if agree else "high"
        match, lists = sanctions_for(
            by_imo, by_mmsi, imos=[s["ais_imo"], s["lloyds_imo"]], mmsi=s["mmsi"]
        )
        # Anchor event time at start of snap day UTC for idempotent key.
        ts = datetime(s["snap_date"].year, s["snap_date"].month, s["snap_date"].day, tzinfo=timezone.utc)
        events.append(
            {
                "event_type": "lloyds_mmsi_imo_conflict",
                "confidence": conf,
                "mmsi": s["mmsi"],
                "imo": _norm_key_text(s["ais_imo"]),
                "imo_other": _norm_key_text(s["lloyds_imo"]),
                "ship_name": s["ais_name"],
                "ship_name_other": s["lloyds_name"],
                "ts_start": ts,
                "ts_end": None,
                "distance_m": None,
                "evidence": {
                    "nameAgree": agree,
                    "snapDate": s["snap_date"].isoformat(),
                    "shipType": s.get("shiptype"),
                },
                "source_table": "ais_lloyds_mmsi_conflict_snap",
                "source_ref": f"lloyds:{s['snap_date']}:{s['mmsi']}:{s['ais_imo']}:{s['lloyds_imo']}",
                "sanctions_match": match,
                "sanctions_lists": lists,
            }
        )
    return events


def collect_multi_imo_static(engine, by_imo, by_mmsi) -> list[dict[str, Any]]:
    from sqlalchemy import text

    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT mmsi, imo, "shipName" AS shipname, "shipType" AS shiptype, ts
                FROM ais_static
                WHERE mmsi IS NOT NULL AND imo IS NOT NULL
                """
            )
        ).mappings().all()
    by_mmsi_rows: dict[int, list] = defaultdict(list)
    for r in rows:
        mmsi = int(r["mmsi"])
        if not is_valid_ship_mmsi(mmsi):
            continue
        imo = normalize_imo_text(r["imo"])
        if not imo:
            continue
        by_mmsi_rows[mmsi].append(r)

    events = []
    for mmsi, items in by_mmsi_rows.items():
        imos = sorted({normalize_imo_text(i["imo"]) for i in items if normalize_imo_text(i["imo"])})
        if len(imos) < 2:
            continue
        # Prefer latest row's name/type; earliest/latest ts for window.
        items_sorted = sorted(items, key=lambda x: x["ts"] or datetime.min)
        latest = items_sorted[-1]
        earliest_ts = items_sorted[0]["ts"]
        latest_ts = items_sorted[-1]["ts"]
        # Idempotent ts_start: earliest observation
        ts_start = earliest_ts
        if getattr(ts_start, "tzinfo", None) is None:
            ts_start = ts_start.replace(tzinfo=timezone.utc)
        match, lists = sanctions_for(by_imo, by_mmsi, imos=imos, mmsi=mmsi)
        events.append(
            {
                "event_type": "mmsi_multi_imo_static",
                "confidence": "medium",
                "mmsi": mmsi,
                "imo": _norm_key_text(imos[0]),
                "imo_other": _norm_key_text(imos[1]),
                "ship_name": normalize_name(latest.get("shipname")),
                "ship_name_other": None,
                "ts_start": ts_start,
                "ts_end": latest_ts.replace(tzinfo=timezone.utc)
                if latest_ts is not None and getattr(latest_ts, "tzinfo", None) is None
                else latest_ts,
                "distance_m": None,
                "evidence": {"imos": imos, "shipType": latest.get("shiptype")},
                "source_table": "ais_static",
                "source_ref": f"static-multi:{mmsi}:{','.join(imos)}",
                "sanctions_match": match,
                "sanctions_lists": lists,
            }
        )
    return events


def dedupe_static_evt_mmsi_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse duplicate ais_static_evt mmsi-change rows to unique (imo, prev, cur, day)."""
    seen: set[tuple] = set()
    out: list[dict[str, Any]] = []
    for r in rows:
        ts = r["ts"]
        day = ts.date() if hasattr(ts, "date") else ts
        key = (r["imo"], r["prev"], r["cur"], day)
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


def collect_static_evt_reuse(engine, by_imo, by_mmsi) -> list[dict[str, Any]]:
    from sqlalchemy import text

    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT ts, imo, prev, cur
                FROM ais_static_evt
                WHERE detchg = 'mmsi'
                  AND prev ~ '^[0-9]{9}$'
                  AND cur ~ '^[0-9]{9}$'
                """
            )
        ).mappings().all()
    raw = [dict(r) for r in rows]
    deduped = dedupe_static_evt_mmsi_rows(raw)

    # MMSI → list of (imo, ts)
    usage: dict[int, list[tuple[str, Any]]] = defaultdict(list)
    for r in deduped:
        imo = normalize_imo_text(r["imo"])
        if not imo:
            continue
        for raw_m in (r["prev"], r["cur"]):
            try:
                mmsi = int(raw_m)
            except (TypeError, ValueError):
                continue
            if not is_valid_ship_mmsi(mmsi):
                continue
            usage[mmsi].append((imo, r["ts"]))

    events = []
    for mmsi, pairs in usage.items():
        imos = sorted({imo for imo, _ in pairs})
        if len(imos) < 2:
            continue
        times = [ts for _, ts in pairs if ts is not None]
        if not times:
            continue
        ts_start = min(times)
        ts_end = max(times)
        if getattr(ts_start, "tzinfo", None) is None:
            ts_start = ts_start.replace(tzinfo=timezone.utc)
        if ts_end is not None and getattr(ts_end, "tzinfo", None) is None:
            ts_end = ts_end.replace(tzinfo=timezone.utc)
        match, lists = sanctions_for(by_imo, by_mmsi, imos=imos, mmsi=mmsi)
        events.append(
            {
                "event_type": "mmsi_reused_static_evt",
                "confidence": "medium",
                "mmsi": mmsi,
                "imo": _norm_key_text(imos[0]),
                "imo_other": _norm_key_text(imos[1]),
                "ship_name": None,
                "ship_name_other": None,
                "ts_start": ts_start,
                "ts_end": ts_end,
                "distance_m": None,
                "evidence": {"imos": imos, "changeCount": len(pairs)},
                "source_table": "ais_static_evt",
                "source_ref": f"evt-reuse:{mmsi}:{','.join(imos)}",
                "sanctions_match": match,
                "sanctions_lists": lists,
            }
        )
    return events


def collect_reflag_events(engine, by_imo, by_mmsi) -> list[dict[str, Any]]:
    from identity_conflict import detect_identity_conflicts

    result = detect_identity_conflicts(engine=engine)
    events = []
    for group in result.get("groups_payload") or []:
        identities = group.get("identities") or []
        if len(identities) < 2:
            continue
        mmsis = []
        imos = []
        names = []
        for ident in identities:
            m = ident.get("mmsi")
            if m is not None and is_valid_ship_mmsi(m):
                mmsis.append(int(m))
            imo = normalize_imo_text(ident.get("imo"))
            if imo:
                imos.append(imo)
            nm = normalize_name(ident.get("shipName"))
            if nm:
                names.append(nm)
        if len(mmsis) < 2:
            continue
        mmsi = min(mmsis)
        imo = imos[0] if imos else None
        imo_other = imos[1] if len(imos) > 1 else None
        detected = group.get("detectedAt") or group.get("detected_at")
        if detected is None:
            continue
        ts_full = pd_timestamp(detected)
        # Day-bucket for idempotent unique key across re-runs.
        ts = datetime(ts_full.year, ts_full.month, ts_full.day, tzinfo=timezone.utc)
        match, lists = sanctions_for(by_imo, by_mmsi, imos=imos, mmsi=mmsi)
        # Also inherit group-level sanctions from payload if present
        if group.get("sanctionsMatch") and not match:
            match = True
            primary = group.get("sanctionsList")
            if primary and primary not in lists:
                lists = [primary] + lists
        events.append(
            {
                "event_type": "reflag_dual_mmsi",
                "confidence": "high",
                "mmsi": mmsi,
                "imo": _norm_key_text(imo),
                "imo_other": _norm_key_text(imo_other),
                "ship_name": names[0] if names else None,
                "ship_name_other": names[1] if len(names) > 1 else None,
                "ts_start": ts,
                "ts_end": None,
                "distance_m": group.get("maxInternalDistanceM") or group.get("distanceM"),
                "evidence": {
                    "mmsis": mmsis,
                    "imos": imos,
                    "clusterSignature": group.get("clusterSignature"),
                    "matchReasons": group.get("matchReasons"),
                },
                "source_table": "live_identity_conflict",
                "source_ref": f"reflag:{group.get('clusterSignature')}:{ts.date().isoformat()}",
                "sanctions_match": match,
                "sanctions_lists": lists,
            }
        )
    return events


def pd_timestamp(value: Any) -> datetime:
    import pandas as pd

    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    else:
        ts = ts.tz_convert("UTC")
    return ts.to_pydatetime()


def upsert_events(conn, events: list[dict[str, Any]]) -> int:
    rows = []
    for e in events:
        rows.append(
            (
                e["event_type"],
                e["confidence"],
                e["mmsi"],
                e["imo"],
                e["imo_other"],
                e.get("ship_name"),
                e.get("ship_name_other"),
                e["ts_start"],
                e.get("ts_end"),
                e.get("distance_m"),
                json.dumps(e.get("evidence") or {}),
                e.get("source_table"),
                e.get("source_ref"),
                bool(e.get("sanctions_match")),
                e.get("sanctions_lists") or None,
                RULE_VERSION,
            )
        )
    _execute_values(
        conn,
        """
        INSERT INTO ais_identity_event (
            event_type, confidence, mmsi, imo, imo_other, ship_name, ship_name_other,
            ts_start, ts_end, distance_m, evidence, source_table, source_ref,
            sanctions_match, sanctions_lists, rule_version
        ) VALUES %s
        ON CONFLICT (event_type, mmsi, imo, imo_other, ts_start) DO UPDATE SET
            confidence = EXCLUDED.confidence,
            ship_name = EXCLUDED.ship_name,
            ship_name_other = EXCLUDED.ship_name_other,
            ts_end = EXCLUDED.ts_end,
            distance_m = EXCLUDED.distance_m,
            evidence = EXCLUDED.evidence,
            source_table = EXCLUDED.source_table,
            source_ref = EXCLUDED.source_ref,
            sanctions_match = EXCLUDED.sanctions_match,
            sanctions_lists = EXCLUDED.sanctions_lists,
            rule_version = EXCLUDED.rule_version
        """,
        rows,
    )
    return len(rows)


def load_ship_types(engine) -> dict[int, Any]:
    from sqlalchemy import text

    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT DISTINCT ON (mmsi) mmsi, "shipType" AS shiptype
                FROM ais_static
                WHERE mmsi IS NOT NULL
                ORDER BY mmsi, ts DESC NULLS LAST
                """
            )
        ).mappings().all()
    return {int(r["mmsi"]): r.get("shiptype") for r in rows}


def recompute_ratings(engine, conn, *, lookback_months: int) -> int:
    from sqlalchemy import text

    events = conn.execute(text("SELECT * FROM ais_identity_event")).mappings().all()
    by_ship: dict[str, list[dict[str, Any]]] = defaultdict(list)
    meta: dict[str, dict[str, Any]] = {}
    ship_types = load_ship_types(engine)

    for r in events:
        ev = dict(r)
        # Restore empty unique keys to None for scoring display
        if ev.get("imo") == "":
            ev["imo"] = None
        if ev.get("imo_other") == "":
            ev["imo_other"] = None
        keys = set()
        imo = normalize_imo_text(ev.get("imo")) or normalize_imo_text(ev.get("imo_other"))
        mmsi = int(ev["mmsi"]) if ev.get("mmsi") is not None else None
        k = ship_key(imo, mmsi)
        if k:
            keys.add(k)
        # Also rate each IMO mentioned
        for cand in (ev.get("imo"), ev.get("imo_other")):
            ci = normalize_imo_text(cand)
            if ci:
                keys.add(f"imo:{ci}")
        if mmsi is not None:
            keys.add(f"mmsi:{mmsi}")
        for k in keys:
            by_ship[k].append(ev)
            meta.setdefault(k, {"imo": None, "mmsi": None, "ship_name": None})
            if k.startswith("imo:"):
                meta[k]["imo"] = k.split(":", 1)[1]
            if k.startswith("mmsi:"):
                meta[k]["mmsi"] = int(k.split(":", 1)[1])
            if ev.get("ship_name") and not meta[k]["ship_name"]:
                meta[k]["ship_name"] = ev.get("ship_name")
            if mmsi is not None and meta[k]["mmsi"] is None:
                meta[k]["mmsi"] = mmsi
            if imo and meta[k]["imo"] is None:
                meta[k]["imo"] = imo

    rating_rows = []
    now = datetime.now(timezone.utc)
    for key, evs in by_ship.items():
        m = meta[key]
        mmsi = m.get("mmsi")
        cargo = is_cargo_tanker_type(ship_types.get(mmsi)) if mmsi is not None else False
        scored = score_ship(
            evs,
            lookback_months=lookback_months,
            is_cargo_tanker=cargo,
            now=now,
        )
        rating_rows.append(
            (
                key,
                m.get("imo"),
                m.get("mmsi"),
                m.get("ship_name"),
                scored["rating"],
                scored["score"],
                lookback_months,
                bool(scored["mmsi_misuse"]),
                bool(scored["sanctions_match"]),
                scored["sanctions_lists"],
                scored["event_count_window"],
                scored["event_count_total"],
                scored["latest_event_at"],
                json.dumps(scored["factors"]),
                RULE_VERSION,
                now,
            )
        )

    _execute_values(
        conn,
        """
        INSERT INTO ais_ship_risk_rating (
            ship_key, imo, mmsi, ship_name, rating, score, lookback_months,
            mmsi_misuse, sanctions_match, sanctions_lists,
            event_count_window, event_count_total, latest_event_at,
            factors, rule_version, computed_at
        ) VALUES %s
        ON CONFLICT (ship_key, lookback_months) DO UPDATE SET
            imo = EXCLUDED.imo,
            mmsi = EXCLUDED.mmsi,
            ship_name = EXCLUDED.ship_name,
            rating = EXCLUDED.rating,
            score = EXCLUDED.score,
            mmsi_misuse = EXCLUDED.mmsi_misuse,
            sanctions_match = EXCLUDED.sanctions_match,
            sanctions_lists = EXCLUDED.sanctions_lists,
            event_count_window = EXCLUDED.event_count_window,
            event_count_total = EXCLUDED.event_count_total,
            latest_event_at = EXCLUDED.latest_event_at,
            factors = EXCLUDED.factors,
            rule_version = EXCLUDED.rule_version,
            computed_at = EXCLUDED.computed_at
        """,
        rating_rows,
    )
    return len(rating_rows)


def run_once(*, lookback_months: int = DEFAULT_LOOKBACK_MONTHS) -> dict[str, Any]:
    from sqlalchemy import text

    t0 = datetime.now(timezone.utc)
    snap_day = t0.date()
    query_engine = get_engine(statement_timeout_ms=QUERY_TIMEOUT_MS)
    write_engine = get_engine(statement_timeout_ms=INGEST_TIMEOUT_MS)

    logging.info("Loading sanctions indexes…")
    by_imo, by_mmsi = load_sanctions_indexes(query_engine)

    logging.info("Snapshotting Lloyd's MMSI mismatches for %s…", snap_day)
    snaps = snapshot_lloyds_mismatches(query_engine, snap_day)
    logging.info("Lloyd's mismatches kept after filters: %s", len(snaps))

    logging.info("Collecting identity events…")
    events: list[dict[str, Any]] = []
    events.extend(collect_dual_track(query_engine, by_imo, by_mmsi))
    events.extend(collect_lloyds_events(snaps, by_imo, by_mmsi))
    events.extend(collect_multi_imo_static(query_engine, by_imo, by_mmsi))
    events.extend(collect_static_evt_reuse(query_engine, by_imo, by_mmsi))
    events.extend(collect_reflag_events(query_engine, by_imo, by_mmsi))

    by_type: dict[str, int] = defaultdict(int)
    by_conf: dict[str, int] = defaultdict(int)
    for e in events:
        by_type[e["event_type"]] += 1
        by_conf[e["confidence"]] += 1

    with write_engine.begin() as conn:
        conn.execute(text(f"SET LOCAL statement_timeout = '{INGEST_TIMEOUT_MS}'"))
        snap_n = persist_lloyds_snap(conn, snaps)
        ev_n = upsert_events(conn, events)
        rating_n = recompute_ratings(query_engine, conn, lookback_months=lookback_months)

    elapsed = (datetime.now(timezone.utc) - t0).total_seconds()
    stats = {
        "snap_date": snap_day.isoformat(),
        "lloyds_snap_rows": snap_n,
        "events_upserted": ev_n,
        "events_by_type": dict(by_type),
        "events_by_confidence": dict(by_conf),
        "ratings_upserted": rating_n,
        "lookback_months": lookback_months,
        "elapsed_sec": round(elapsed, 3),
        "rule_version": RULE_VERSION,
    }
    logging.info("identity_risk_job done: %s", json.dumps(stats, default=str))
    return stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build AIS identity risk events and ratings")
    parser.add_argument("--lookback-months", type=int, default=DEFAULT_LOOKBACK_MONTHS)
    args = parser.parse_args(argv)
    try:
        run_once(lookback_months=args.lookback_months)
        return 0
    except Exception:
        logging.exception("identity_risk_job failed")
        return 1


if __name__ == "__main__":
    sys.exit(main())
