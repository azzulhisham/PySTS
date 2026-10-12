"""In-memory cache of in-force dredge licences from jkptg_prod.shipprojects.

Read at most once every 12 hours on a connection that is not the pnav pool.
A failure leaves the previous cache in place, or an empty cache on the first
failure, so the proximity loop keeps running on cargo and tanker only.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime, timezone
from urllib.parse import quote

import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

log = logging.getLogger(__name__)

REFRESH_SECONDS = int(os.environ.get("dredge_licence_refresh_seconds", str(12 * 3600)))

# Fishing, towing, tug, sailing, pleasure, pilot, SAR, port tender,
# high-speed craft, and passenger. A licence does not pull these types in.
EXCLUDED_SHIP_TYPES = frozenset(
    [30, 31, 32, 36, 37, 50, 51, 52, 53] + list(range(40, 50)) + list(range(60, 70))
)

_INACTIVE_STATUS = frozenset({
    "inactive", "expired", "cancelled", "canceled", "deleted", "closed", "rejected",
})

_lock = threading.Lock()
_loaded_at = 0.0
_by_mmsi: dict[int, list[dict]] = {}
_by_imo: dict[str, list[dict]] = {}
_engine: Engine | None = None


def _jkptg_engine() -> Engine:
    global _engine
    if _engine is None:
        password = os.environ.get("pnav_db_password", "m4r1t1m3")
        host = os.environ.get(
            "jkptg_db_host",
            "marineai.cxwk8yige5f2.ap-southeast-5.rds.amazonaws.com",
        )
        url = (
            f"postgresql://postgresadmin:{quote(password)}"
            f"@{host}:5432/jkptg_prod"
        )
        _engine = create_engine(
            url,
            pool_size=1,
            max_overflow=0,
            pool_timeout=15,
            pool_pre_ping=True,
            connect_args={
                "application_name": "sts_dredge_licence",
                "options": "-c statement_timeout=15000 -c lock_timeout=5000",
            },
        )
    return _engine


def _parse_when(value) -> datetime | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    text_value = str(value).strip()
    if not text_value or text_value.lower() in {"none", "null", "nan"}:
        return None
    parsed = pd.to_datetime(text_value, errors="coerce", utc=True)
    if pd.isna(parsed):
        return None
    return parsed.to_pydatetime()


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _norm_imo(value) -> str | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    digits = "".join(ch for ch in str(value) if ch.isdigit())
    if len(digits) < 7:
        return None
    return digits[-7:]


def _norm_mmsi(value) -> int | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    digits = "".join(ch for ch in str(value) if ch.isdigit())
    if not digits:
        return None
    number = int(digits)
    if number <= 0:
        return None
    return number


def _refresh(force: bool = False) -> None:
    global _loaded_at, _by_mmsi, _by_imo
    now = time.monotonic()
    if not force and _loaded_at and (now - _loaded_at) < REFRESH_SECONDS:
        return
    with _lock:
        now = time.monotonic()
        if not force and _loaded_at and (now - _loaded_at) < REFRESH_SECONDS:
            return
        try:
            frame = pd.read_sql(
                text(
                    """
                    SELECT id, mmsi, imo, "shipName" AS ship_name, status,
                           "licensePeriodStart" AS licence_start,
                           "licensePeriodEnd" AS licence_end
                    FROM public.shipprojects
                    """
                ),
                con=_jkptg_engine(),
            )
        except Exception:
            log.exception("Dredge licence cache refresh failed; keeping the previous cache")
            if not _loaded_at:
                _loaded_at = now
            return

        by_mmsi: dict[int, list[dict]] = {}
        by_imo: dict[str, list[dict]] = {}
        kept = 0
        for _, row in frame.iterrows():
            status = None if pd.isna(row.get("status")) else str(row.get("status")).strip().lower()
            if status in _INACTIVE_STATUS:
                continue
            start = _parse_when(row.get("licence_start"))
            end = _parse_when(row.get("licence_end"))
            if start is None or end is None:
                continue
            item = {
                "project_id": int(row["id"]),
                "start": start,
                "end": end,
                "ship_name": None if pd.isna(row.get("ship_name")) else str(row.get("ship_name")),
            }
            mmsi = _norm_mmsi(row.get("mmsi"))
            imo = _norm_imo(row.get("imo"))
            if mmsi is not None:
                by_mmsi.setdefault(mmsi, []).append(item)
                kept += 1
            elif imo is not None:
                by_imo.setdefault(imo, []).append(item)
                kept += 1
        _by_mmsi = by_mmsi
        _by_imo = by_imo
        _loaded_at = now
        log.info(
            "Dredge licence cache: %s MMSI keys, %s IMO-only keys, %s usable rows",
            len(by_mmsi), len(by_imo), kept,
        )


def _matches(items: list[dict] | None, start: datetime, end: datetime) -> dict | None:
    if not items:
        return None
    start = _as_utc(start)
    end = _as_utc(end)
    for item in items:
        if item["start"] <= end and item["end"] >= start:
            return item
    return None


def matching_project(mmsi: int | None, imo, start: datetime, end: datetime) -> dict | None:
    """Return one in-force project whose licence overlaps [start, end]."""
    _refresh()
    hit = _matches(_by_mmsi.get(int(mmsi)) if mmsi else None, start, end)
    if hit is not None:
        return hit
    return _matches(_by_imo.get(_norm_imo(imo) or ""), start, end)


def licensed_mmsis(when: datetime | None = None) -> set[int]:
    """MMSIs with a licence covering `when` (default now). IMO-only rows are not included."""
    _refresh()
    when = _as_utc(when or datetime.now(timezone.utc))
    found = set()
    for mmsi, items in _by_mmsi.items():
        if _matches(items, when, when) is not None:
            found.add(mmsi)
    return found
