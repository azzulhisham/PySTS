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
import time
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

BACKEND = Path(__file__).resolve().parent
ROOT = BACKEND.parent
# Laptop: PySTS/backend/job.py → PySTS/restapi
# srv-dp: /home/srvadmin/sts/job.py → /home/srvadmin/sts/restapi
_RESTAPI_CANDIDATES = (ROOT / "restapi", BACKEND / "restapi")
RESTAPI = next((p for p in _RESTAPI_CANDIDATES if p.is_dir()), _RESTAPI_CANDIDATES[0])
if str(RESTAPI) not in sys.path:
    sys.path.insert(0, str(RESTAPI))
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from ais_identity import is_valid_ship_mmsi  # noqa: E402
from identity_risk import (  # noqa: E402
    DEFAULT_LOOKBACK_MONTHS,
    RULE_VERSION,
    classify_lloyds_conflict,
    format_sanctions_reason,
    is_cargo_tanker_type,
    merge_sanctions_lists,
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

# When OFAC/UK/UN ingest still holds table locks (TRUNCATE+INSERT), wait and retry.
# lock_timeout GUC stays at 5s — we do not raise it; we retry the whole run.
LOCK_RETRY_WAIT_S = 120
LOCK_RETRY_MAX_S = 30 * 60


def is_lock_timeout_error(exc: BaseException) -> bool:
    """True for Postgres LockNotAvailable / lock_timeout OperationalErrors."""
    try:
        from psycopg2 import errors as pg_errors
        from psycopg2 import OperationalError as PsycopgOperationalError
    except ImportError:  # pragma: no cover
        pg_errors = None  # type: ignore[assignment]
        PsycopgOperationalError = ()  # type: ignore[misc, assignment]

    seen: set[int] = set()
    stack: list[BaseException] = [exc]
    while stack:
        cur = stack.pop()
        if id(cur) in seen:
            continue
        seen.add(id(cur))
        if pg_errors is not None and isinstance(cur, pg_errors.LockNotAvailable):
            return True
        name = type(cur).__name__
        if name == "LockNotAvailable":
            return True
        msg = str(cur).lower()
        if "lock timeout" in msg or "locknotavailable" in msg.lower():
            return True
        if isinstance(cur, PsycopgOperationalError) and "canceling statement due to lock timeout" in msg:
            return True
        # SQLAlchemy wraps driver errors
        orig = getattr(cur, "orig", None)
        if isinstance(orig, BaseException):
            stack.append(orig)
        cause = cur.__cause__
        if isinstance(cause, BaseException):
            stack.append(cause)
        context = cur.__context__
        if isinstance(context, BaseException):
            stack.append(context)
    return False


def run_once_with_lock_retry(
    *,
    lookback_months: int = DEFAULT_LOOKBACK_MONTHS,
    sleep_fn=time.sleep,
    monotonic_fn=time.monotonic,
    run_fn=None,
) -> dict[str, Any]:
    """
    Call run_once; on lock timeout / LockNotAvailable, roll back (via exception
    unwind), wait LOCK_RETRY_WAIT_S, and retry until LOCK_RETRY_MAX_S elapses.
    """
    run_fn = run_fn or run_once
    deadline = monotonic_fn() + LOCK_RETRY_MAX_S
    attempt = 0
    while True:
        attempt += 1
        try:
            if attempt > 1:
                logging.info(
                    "identity_risk_job lock-retry attempt %s (lookback=%s)",
                    attempt,
                    lookback_months,
                )
            return run_fn(lookback_months=lookback_months)
        except Exception as exc:
            if not is_lock_timeout_error(exc):
                raise
            now = monotonic_fn()
            if now >= deadline:
                logging.error(
                    "identity_risk_job giving up after %s lock-timeout attempt(s) "
                    "over %ss: %s",
                    attempt,
                    LOCK_RETRY_MAX_S,
                    exc,
                )
                raise RuntimeError(
                    f"identity_risk_job failed: lock timeout after {attempt} attempt(s) "
                    f"over {LOCK_RETRY_MAX_S}s"
                ) from exc
            wait_s = min(LOCK_RETRY_WAIT_S, max(0.0, deadline - now))
            logging.warning(
                "identity_risk_job lock timeout on attempt %s (%s); "
                "rolling back and retrying in %ss (%.0fs of retry window left)",
                attempt,
                type(exc).__name__,
                int(wait_s) if wait_s >= 1 else round(wait_s, 1),
                max(0.0, deadline - now),
            )
            sleep_fn(wait_s)


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
    """
    Attribute sanctions to one ship identity only.

    If any IMO is present, match IMO keys only (never inherit via shared MMSI
    or a conflicting imo_other). If no IMO, match MMSI only.
    """
    lists: list[str] = []
    primary_imos = [imo for imo in imos if imo]
    if primary_imos:
        for imo in primary_imos:
            for n in by_imo.get(imo, []):
                if n not in lists:
                    lists.append(n)
    elif mmsi is not None:
        for n in by_mmsi.get(str(int(mmsi)), []):
            if n not in lists:
                lists.append(n)
    lists = sorted(lists, key=lambda n: LIST_PRIORITY.get(n, 99))
    return bool(lists), lists


def snapshot_lloyds_mismatches(engine, snap_day: date) -> list[dict[str, Any]]:
    """
    Snapshot ais_static vs ais_lloyds MMSI mismatches and classify each as
    stale_registry (low) or true_conflict (high).

    ais_lloyds may hold multiple rows per MMSI (old + new delivery). An agreeing
    row for the AIS IMO means the disagreeing row is a stale registry hold.
    """
    from collections import defaultdict

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
           l.imoshipno AS lloyds_imo, l.shipname AS lloyds_name,
           l.shipstatus AS lloyds_status, l.flagname AS lloyds_flag,
           l.updated AS lloyds_updated
    FROM s
    JOIN ais_lloyds l
      ON l.maritimemobileserviceidentitymmsinumber = s.mmsi
    WHERE l.imoshipno IS NOT NULL
      AND l.imoshipno <> s.imo
    """
    with engine.connect() as conn:
        rows = conn.execute(text(sql)).mappings().all()

    candidates: list[dict[str, Any]] = []
    mmsis: set[int] = set()
    imos: set[int] = set()
    for r in rows:
        mmsi = int(r["mmsi"])
        if not is_valid_ship_mmsi(mmsi):
            continue
        ais_imo = normalize_imo_text(r["ais_imo"])
        lloyds_imo = normalize_imo_text(r["lloyds_imo"])
        if not ais_imo or not lloyds_imo or ais_imo == lloyds_imo:
            continue
        candidates.append(
            {
                "snap_date": snap_day,
                "mmsi": mmsi,
                "ais_imo": ais_imo,
                "lloyds_imo": lloyds_imo,
                "ais_name": normalize_name(r["ais_name"]),
                "lloyds_name": normalize_name(r["lloyds_name"]),
                "name_agree": names_agree(r["ais_name"], r["lloyds_name"]),
                "shiptype": r.get("shiptype"),
                "lloyds_status": r.get("lloyds_status"),
                "lloyds_flag": r.get("lloyds_flag"),
                "lloyds_updated": r.get("lloyds_updated"),
            }
        )
        mmsis.add(mmsi)
        imos.add(int(ais_imo))
        imos.add(int(lloyds_imo))

    if not candidates:
        return []

    lloyds_by_mmsi: dict[int, list[int]] = defaultdict(list)
    lloyds_by_imo: dict[int, dict[str, Any]] = {}
    live_by_imo: dict[int, int] = {}
    live_mmsi_set: set[int] = set()

    with engine.connect() as conn:
        for r in conn.execute(
            text(
                """
                SELECT maritimemobileserviceidentitymmsinumber AS mmsi,
                       imoshipno AS imo
                FROM ais_lloyds
                WHERE maritimemobileserviceidentitymmsinumber = ANY(:m)
                  AND imoshipno IS NOT NULL
                """
            ),
            {"m": sorted(mmsis)},
        ).mappings():
            lloyds_by_mmsi[int(r["mmsi"])].append(int(r["imo"]))

        for r in conn.execute(
            text(
                """
                SELECT imoshipno AS imo,
                       maritimemobileserviceidentitymmsinumber AS mmsi,
                       shipstatus, shipname, updated, flagname
                FROM ais_lloyds
                WHERE imoshipno = ANY(:imos)
                """
            ),
            {"imos": sorted(imos)},
        ).mappings():
            lloyds_by_imo[int(r["imo"])] = dict(r)

        for r in conn.execute(
            text(
                """
                SELECT DISTINCT ON (imo) imo, mmsi
                FROM ais_static
                WHERE imo = ANY(:imos)
                ORDER BY imo, ts DESC NULLS LAST
                """
            ),
            {"imos": sorted(imos)},
        ).mappings():
            if r.get("mmsi") is not None:
                live_by_imo[int(r["imo"])] = int(r["mmsi"])

        own_mmsis = sorted(
            {
                int(lloyds_by_imo[i]["mmsi"])
                for i in imos
                if i in lloyds_by_imo and lloyds_by_imo[i].get("mmsi") is not None
            }
        )
        if own_mmsis:
            for m in conn.execute(
                text("SELECT DISTINCT mmsi FROM ais_static WHERE mmsi = ANY(:m)"),
                {"m": own_mmsis},
            ).scalars():
                live_mmsi_set.add(int(m))

    snaps: list[dict[str, Any]] = []
    # Emit every disagreeing Lloyd's row so prior high-confidence rows get
    # reclassified on upsert; duplicates per MMSI are ok (ratings key by AIS IMO).
    for c in candidates:
        mmsi = c["mmsi"]
        ais_imo = c["ais_imo"]
        lloyds_imo = c["lloyds_imo"]
        imos_on_mmsi = lloyds_by_mmsi.get(mmsi, [])
        agree_row = int(ais_imo) in imos_on_mmsi
        own = lloyds_by_imo.get(int(ais_imo))
        other = lloyds_by_imo.get(int(lloyds_imo))
        own_mmsi = int(own["mmsi"]) if own and own.get("mmsi") is not None else None
        other_live = live_by_imo.get(int(lloyds_imo))
        own_live = own_mmsi is not None and own_mmsi in live_mmsi_set
        conflict_class = classify_lloyds_conflict(
            mmsi=mmsi,
            ais_imo=ais_imo,
            lloyds_imo=lloyds_imo,
            agree_row_exists=agree_row,
            ais_imo_lloyds_mmsi=own_mmsi,
            lloyds_imo_live_mmsi=other_live,
            lloyds_imo_status=(other or {}).get("shipstatus") or c.get("lloyds_status"),
            ais_imo_lloyds_mmsi_live=own_live,
        )
        row = dict(c)
        row["conflict_class"] = conflict_class
        row["ais_imo_lloyds_mmsi"] = own_mmsi
        row["lloyds_imo_live_mmsi"] = other_live
        row["agree_row_exists"] = agree_row
        snaps.append(row)
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
        conflict_class = s.get("conflict_class") or "stale_registry"
        # HIGH only for live true conflicts; stale registry / same-name → low (info).
        if conflict_class == "true_conflict" and not agree:
            conf = "high"
        else:
            conf = "low"
        # Primary ship only (AIS IMO). Do not inherit lists from lloyds_imo / imo_other.
        match, lists = sanctions_for(
            by_imo, by_mmsi, imos=[s["ais_imo"]], mmsi=s["mmsi"]
        )
        # Anchor event time at start of snap day UTC for idempotent key.
        ts = datetime(s["snap_date"].year, s["snap_date"].month, s["snap_date"].day, tzinfo=timezone.utc)
        updated = s.get("lloyds_updated")
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
                    "conflictClass": conflict_class,
                    "agreeRowExists": bool(s.get("agree_row_exists")),
                    "aisImoLloydsMmsi": s.get("ais_imo_lloyds_mmsi"),
                    "lloydsImoLiveMmsi": s.get("lloyds_imo_live_mmsi"),
                    "lloydsFlag": s.get("lloyds_flag"),
                    "lloydsStatus": s.get("lloyds_status"),
                    "lloydsUpdated": updated.isoformat() if hasattr(updated, "isoformat") else (str(updated) if updated else None),
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
        match, lists = sanctions_for(by_imo, by_mmsi, imos=[imos[0]], mmsi=mmsi)
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
        match, lists = sanctions_for(by_imo, by_mmsi, imos=[imos[0]], mmsi=mmsi)
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
        # Attribute to primary IMO only (first identity); not every IMO in the group.
        match, lists = sanctions_for(by_imo, by_mmsi, imos=[imo], mmsi=mmsi)
        # Group-level flag only when it refers to this primary ship.
        if group.get("sanctionsMatch") and not match and imo:
            group_imo = normalize_imo_text(group.get("imo")) or imo
            if group_imo == imo:
                match = True
                primary = group.get("sanctionsList")
                if primary and primary not in lists:
                    lists = [primary] + lists
        events.append(
            {
                "event_type": "reflag_dual_mmsi",
                # Distinct from high MMSI-misuse confidence so re-flag is never "high misuse".
                "confidence": "reflag",
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
                    "category": "reflag_dual_mmsi",
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


def primary_event_ship_key(ev: dict[str, Any]) -> str | None:
    """One ship key per event: IMO when known (AIS/primary), else MMSI."""
    imo = normalize_imo_text(ev.get("imo"))
    mmsi = int(ev["mmsi"]) if ev.get("mmsi") is not None else None
    return ship_key(imo, mmsi)


def sanctioned_ship_key(imo: Any, mmsi: Any) -> str | None:
    """Same key rule as /mantis/sanctions: IMO first, else MMSI (no row: fallback)."""
    imo_key = normalize_imo(imo)
    if imo_key:
        return f"imo:{imo_key}"
    mmsi_key = normalize_mmsi(mmsi)
    if mmsi_key:
        return f"mmsi:{int(mmsi_key)}"
    return None


def iter_sanctioned_ships(df) -> list[dict[str, Any]]:
    """Deduped sanctions vessels with a usable IMO or MMSI ship_key."""
    if df is None or getattr(df, "empty", True):
        return []
    out: list[dict[str, Any]] = []
    for _, row in df.iterrows():
        key = sanctioned_ship_key(row.get("imo"), row.get("mmsi"))
        if not key:
            continue
        lists: list[str] = []
        names = row.get("list_names")
        if isinstance(names, list) and names:
            lists = [str(n) for n in names if n]
        elif row.get("list_name"):
            lists = [str(row.get("list_name"))]
        lists = sorted(set(lists), key=lambda n: LIST_PRIORITY.get(n, 99))
        imo = normalize_imo(row.get("imo"))
        mmsi_s = normalize_mmsi(row.get("mmsi"))
        mmsi = int(mmsi_s) if mmsi_s else None
        name = normalize_name(row.get("vessel_name"))
        out.append(
            {
                "ship_key": key,
                "imo": imo,
                "mmsi": mmsi,
                "ship_name": name,
                "sanctions_lists": lists,
            }
        )
    return out


def _find_rating_key_for_sanctions(
    ratings: dict[str, dict[str, Any]],
    *,
    preferred_key: str,
    imo: str | None,
    mmsi: int | None,
) -> tuple[str | None, bool]:
    """
    Resolve where a sanctioned ship should land.

    Returns (existing_or_preferred_key, rekey_to_imo).

    - IMO-keyed sanctioned ships always keep imo:<their IMO>. Never merge onto
      another IMO because of a shared MMSI.
    - The only MMSI merge is consolidating an existing mmsi:<same> row onto this
      IMO (same vessel, previously MMSI-only).
    - MMSI-only sanctioned ships (no IMO) use mmsi:<MMSI> and do not merge onto
      any imo:* row that happens to share the MMSI.
    """
    if preferred_key in ratings:
        return preferred_key, False
    if preferred_key.startswith("imo:") and mmsi is not None:
        mmsi_key = f"mmsi:{int(mmsi)}"
        if mmsi_key in ratings:
            return mmsi_key, True
        # Do not absorb into another ship's imo:* row.
        return preferred_key, False
    if preferred_key.startswith("mmsi:"):
        # Own MMSI key only — never fold into an imo:* neighbour.
        return preferred_key, False
    return preferred_key, False


def apply_sanctions_to_ratings(
    ratings: dict[str, dict[str, Any]],
    sanctioned: list[dict[str, Any]],
    *,
    lookback_months: int,
    now: datetime,
) -> dict[str, int]:
    """
    Ensure every sanctioned ship has a RED 100 rating on its own ship_key.

    - Missing rating → insert RED 100, reason "sanctions match (LISTS)"
    - Existing rating for the same key → force RED 100 (never downgrade)
    - IMO ships never share a row with a different IMO via MMSI
    """
    added = 0
    upgraded = 0
    for vessel in sanctioned:
        preferred = vessel["ship_key"]
        imo = vessel.get("imo")
        mmsi = vessel.get("mmsi")
        lists = list(vessel.get("sanctions_lists") or [])
        target_key, rekey = _find_rating_key_for_sanctions(
            ratings, preferred_key=preferred, imo=imo, mmsi=mmsi
        )
        if target_key is None:
            continue

        if target_key in ratings:
            row = ratings[target_key]
            if rekey and preferred.startswith("imo:") and preferred != target_key:
                ratings.pop(target_key)
                row["ship_key"] = preferred
                row["imo"] = imo or row.get("imo")
                target_key = preferred
                ratings[target_key] = row
            prev_rating, prev_score = row.get("rating"), int(row.get("score") or 0)
            row["sanctions_match"] = True
            row["sanctions_lists"] = merge_sanctions_lists(row.get("sanctions_lists"), lists)
            # Never downgrade below RED 100.
            row["rating"] = "RED"
            row["score"] = 100
            if row.get("imo") is None and imo:
                row["imo"] = imo
            if row.get("mmsi") is None and mmsi is not None:
                row["mmsi"] = mmsi
            if not row.get("ship_name") and vessel.get("ship_name"):
                row["ship_name"] = vessel.get("ship_name")
            factors = dict(row.get("factors") or {})
            sanc_reason = format_sanctions_reason(row["sanctions_lists"])
            prev_reason = str(factors.get("reason") or "none")
            if prev_reason in {"none", "sanctions_match"} or prev_reason.startswith(
                "sanctions match"
            ):
                factors["reason"] = sanc_reason
            elif "sanctions match" not in prev_reason:
                factors["reason"] = f"{prev_reason}; {sanc_reason}"
            else:
                factors["reason"] = sanc_reason
            factors["sanctionsLists"] = list(row["sanctions_lists"] or [])
            row["factors"] = factors
            if prev_rating != "RED" or prev_score < 100:
                upgraded += 1
        else:
            sanc_reason = format_sanctions_reason(lists)
            ratings[preferred] = {
                "ship_key": preferred,
                "imo": imo,
                "mmsi": mmsi,
                "ship_name": vessel.get("ship_name"),
                "rating": "RED",
                "score": 100,
                "lookback_months": lookback_months,
                "mmsi_misuse": False,
                "sanctions_match": True,
                "sanctions_lists": lists or None,
                "event_count_window": 0,
                "event_count_total": 0,
                "latest_event_at": None,
                "factors": {
                    "reason": sanc_reason,
                    "lookbackMonths": lookback_months,
                    "cutoffUtc": None,
                    "isCargoTanker": False,
                    "highMisuseCount": 0,
                    "mediumMisuseCount": 0,
                    "lowMisuseCount": 0,
                    "reflagCount": 0,
                    "sanctionsLists": lists,
                },
                "rule_version": RULE_VERSION,
                "computed_at": now,
            }
            added += 1
    return {"sanctions_ratings_added": added, "sanctions_ratings_upgraded": upgraded}


def recompute_ratings(
    engine,
    conn,
    *,
    lookback_months: int,
    sanctioned_ships: list[dict[str, Any]] | None = None,
) -> dict[str, int]:
    """
    Recompute ratings: one row per ship (IMO key when known, else MMSI).
    Then ensure every merged-sanctions ship has a RED 100 row.
    Drops previous lookback rows so legacy mmsi:/imo_other duplicates disappear.
    """
    from sqlalchemy import text

    events = conn.execute(text("SELECT * FROM ais_identity_event")).mappings().all()
    by_ship: dict[str, list[dict[str, Any]]] = defaultdict(list)
    meta: dict[str, dict[str, Any]] = {}
    ship_types = load_ship_types(engine)

    legacy_key_count = 0
    for r in events:
        ev = dict(r)
        if ev.get("imo") == "":
            ev["imo"] = None
        if ev.get("imo_other") == "":
            ev["imo_other"] = None
        # Count how many extra keys the v1.0 logic would have created.
        imo = normalize_imo_text(ev.get("imo"))
        mmsi = int(ev["mmsi"]) if ev.get("mmsi") is not None else None
        legacy_keys = set()
        if imo:
            legacy_keys.add(f"imo:{imo}")
        other = normalize_imo_text(ev.get("imo_other"))
        if other:
            legacy_keys.add(f"imo:{other}")
        if mmsi is not None:
            legacy_keys.add(f"mmsi:{mmsi}")
        primary = primary_event_ship_key(ev)
        if primary:
            legacy_key_count += max(0, len(legacy_keys) - 1)
        if not primary:
            continue
        by_ship[primary].append(ev)
        meta.setdefault(primary, {"imo": None, "mmsi": None, "ship_name": None})
        if primary.startswith("imo:"):
            meta[primary]["imo"] = primary.split(":", 1)[1]
        if primary.startswith("mmsi:"):
            meta[primary]["mmsi"] = int(primary.split(":", 1)[1])
        if ev.get("ship_name") and not meta[primary]["ship_name"]:
            meta[primary]["ship_name"] = ev.get("ship_name")
        if mmsi is not None and meta[primary]["mmsi"] is None:
            meta[primary]["mmsi"] = mmsi
        if imo and meta[primary]["imo"] is None:
            meta[primary]["imo"] = imo

    ratings: dict[str, dict[str, Any]] = {}
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
            own_imo=m.get("imo"),
            own_mmsi=m.get("mmsi"),
        )
        ratings[key] = {
            "ship_key": key,
            "imo": m.get("imo"),
            "mmsi": m.get("mmsi"),
            "ship_name": m.get("ship_name"),
            "rating": scored["rating"],
            "score": scored["score"],
            "lookback_months": lookback_months,
            "mmsi_misuse": bool(scored["mmsi_misuse"]),
            "sanctions_match": bool(scored["sanctions_match"]),
            "sanctions_lists": scored["sanctions_lists"],
            "event_count_window": scored["event_count_window"],
            "event_count_total": scored["event_count_total"],
            "latest_event_at": scored["latest_event_at"],
            "factors": scored["factors"],
            "rule_version": RULE_VERSION,
            "computed_at": now,
        }

    sanc_stats = apply_sanctions_to_ratings(
        ratings,
        sanctioned_ships or [],
        lookback_months=lookback_months,
        now=now,
    )

    rating_rows = [
        (
            row["ship_key"],
            row.get("imo"),
            row.get("mmsi"),
            row.get("ship_name"),
            row["rating"],
            row["score"],
            lookback_months,
            bool(row.get("mmsi_misuse")),
            bool(row.get("sanctions_match")),
            row.get("sanctions_lists"),
            int(row.get("event_count_window") or 0),
            int(row.get("event_count_total") or 0),
            row.get("latest_event_at"),
            json.dumps(row.get("factors") or {}),
            RULE_VERSION,
            now,
        )
        for row in ratings.values()
    ]

    # Replace lookback slice so removed duplicate keys do not linger.
    conn.execute(
        text("DELETE FROM ais_ship_risk_rating WHERE lookback_months = :lb"),
        {"lb": lookback_months},
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
        """,
        rating_rows,
    )
    return {
        "ratings_upserted": len(rating_rows),
        "legacy_duplicate_keys_avoided": legacy_key_count,
        **sanc_stats,
    }


def run_once(*, lookback_months: int = DEFAULT_LOOKBACK_MONTHS) -> dict[str, Any]:
    from sqlalchemy import text

    t0 = datetime.now(timezone.utc)
    snap_day = t0.date()
    query_engine = get_engine(statement_timeout_ms=QUERY_TIMEOUT_MS)
    write_engine = get_engine(statement_timeout_ms=INGEST_TIMEOUT_MS)

    logging.info("Loading sanctions indexes…")
    by_imo, by_mmsi = load_sanctions_indexes(query_engine)
    logging.info("Loading merged sanctions vessel list…")
    sanctioned_ships = iter_sanctioned_ships(load_ofac_vessel_list(query_engine))
    logging.info("Sanctioned ships with IMO/MMSI key: %s", len(sanctioned_ships))

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

    lloyds_class_counts: dict[str, int] = defaultdict(int)
    for s in snaps:
        lloyds_class_counts[s.get("conflict_class") or "unknown"] += 1

    with write_engine.begin() as conn:
        conn.execute(text(f"SET LOCAL statement_timeout = '{INGEST_TIMEOUT_MS}'"))
        snap_n = persist_lloyds_snap(conn, snaps)
        ev_n = upsert_events(conn, events)
        rating_stats = recompute_ratings(
            query_engine,
            conn,
            lookback_months=lookback_months,
            sanctioned_ships=sanctioned_ships,
        )

    elapsed = (datetime.now(timezone.utc) - t0).total_seconds()
    stats = {
        "snap_date": snap_day.isoformat(),
        "lloyds_snap_rows": snap_n,
        "lloyds_by_conflict_class": dict(lloyds_class_counts),
        "events_upserted": ev_n,
        "events_by_type": dict(by_type),
        "events_by_confidence": dict(by_conf),
        "ratings_upserted": rating_stats["ratings_upserted"],
        "legacy_duplicate_keys_avoided": rating_stats["legacy_duplicate_keys_avoided"],
        "sanctions_ratings_added": rating_stats.get("sanctions_ratings_added", 0),
        "sanctions_ratings_upgraded": rating_stats.get("sanctions_ratings_upgraded", 0),
        "sanctioned_ships_considered": len(sanctioned_ships),
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
        run_once_with_lock_retry(lookback_months=args.lookback_months)
        return 0
    except Exception:
        logging.exception("identity_risk_job failed")
        return 1


if __name__ == "__main__":
    sys.exit(main())
