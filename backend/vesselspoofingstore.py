"""Write daily teleport hits into pnav.ais_positionanomaly.

ClickHouse is read in this process only. The cargo/tanker MMSI list is cached
for 12 hours so the static-table window is not repeated every cycle. The
upsert runs after the ClickHouse read has finished.
"""

from __future__ import annotations

import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

BACKEND_DIR = Path(__file__).resolve().parent


def _restapi_dir() -> Path:
    for candidate in (BACKEND_DIR.parent / "restapi", BACKEND_DIR / "restapi"):
        if (candidate / "duckdb_spatial.py").exists():
            return candidate
    return BACKEND_DIR.parent / "restapi"


RESTAPI_DIR = _restapi_dir()
sys.path.insert(0, str(BACKEND_DIR))
sys.path.insert(0, str(RESTAPI_DIR))

from mantis_tables import ensure_tables  # noqa: E402
from spoofing import (  # noqa: E402
    RULE_VERSION,
    _split_alert_and_quality,
    dedupe_daily,
    load_cargo_tanker_static,
    load_teleport_hits,
)
from timelineplayback import resolve_track_range  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

LOOP_SECONDS = int(os.environ.get("spoof_store_loop_seconds", "900"))
CARGO_REFRESH_SECONDS = int(os.environ.get("spoof_cargo_refresh_seconds", str(12 * 3600)))

def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if value is None or str(value).strip() == "":
        raise RuntimeError(
            f"Environment variable {name} is required for the spoofing store "
            f"(no hard-coded credentials)."
        )
    return value


def _database_url() -> str:
    password = _require_env("pnav_db_password")
    host = os.environ.get(
        "pnav_db_host",
        "marineai2.cxwk8yige5f2.ap-southeast-5.rds.amazonaws.com",
    )
    user = os.environ.get("pnav_db_user", "postgresadmin")
    name = os.environ.get("pnav_db_name", "pnav")
    return f"postgresql://{user}:{quote(password)}@{host}:5432/{name}"


_engine: Engine | None = None
_cargo_mmsis: list[int] = []
_cargo_loaded_at = 0.0


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        _engine = create_engine(
            _database_url(),
            pool_size=1,
            max_overflow=0,
            pool_timeout=30,
            pool_pre_ping=True,
            connect_args={
                "application_name": "sts_spoof_store",
                "options": "-c statement_timeout=30000 -c lock_timeout=5000",
            },
        )
    return _engine


def cargo_mmsis() -> list[int]:
    global _cargo_mmsis, _cargo_loaded_at
    now = time.monotonic()
    if _cargo_mmsis and (now - _cargo_loaded_at) < CARGO_REFRESH_SECONDS:
        return _cargo_mmsis
    static = load_cargo_tanker_static(get_engine())
    _cargo_mmsis = static["mmsi"].astype(int).tolist() if not static.empty else []
    _cargo_loaded_at = now
    logging.info("Cargo/tanker MMSI cache: %s", len(_cargo_mmsis))
    return _cargo_mmsis


def _ts(value) -> datetime | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    parsed = pd.Timestamp(value)
    if parsed.tzinfo is None:
        parsed = parsed.tz_localize("UTC")
    else:
        parsed = parsed.tz_convert("UTC")
    return parsed.to_pydatetime()


# Conflict rule mirrors spoofing.should_replace_stored_anomaly (no DDL):
# alert replaces data-quality; data-quality never replaces alert; same class → higher kn.
UPSERT = text(
    """
    INSERT INTO public.ais_positionanomaly (
        mmsi, event_day, ts, prev_ts, latitude, longitude,
        prev_latitude, prev_longitude, dist_m, dt_s, implied_kn,
        reason, rule_version, created_at, updated_at
    ) VALUES (
        :mmsi, :event_day, :ts, :prev_ts, :latitude, :longitude,
        :prev_latitude, :prev_longitude, :dist_m, :dt_s, :implied_kn,
        :reason, :rule_version, now(), now()
    )
    ON CONFLICT (mmsi, event_day) DO UPDATE SET
        ts = EXCLUDED.ts,
        prev_ts = EXCLUDED.prev_ts,
        latitude = EXCLUDED.latitude,
        longitude = EXCLUDED.longitude,
        prev_latitude = EXCLUDED.prev_latitude,
        prev_longitude = EXCLUDED.prev_longitude,
        dist_m = EXCLUDED.dist_m,
        dt_s = EXCLUDED.dt_s,
        implied_kn = EXCLUDED.implied_kn,
        reason = EXCLUDED.reason,
        rule_version = EXCLUDED.rule_version,
        updated_at = now()
    WHERE (
            EXCLUDED.reason NOT IN ('bad_position_data', 'duplicate_mmsi')
            AND ais_positionanomaly.reason IN ('bad_position_data', 'duplicate_mmsi')
        )
        OR (
            (
                (EXCLUDED.reason IN ('bad_position_data', 'duplicate_mmsi'))
                = (ais_positionanomaly.reason IN ('bad_position_data', 'duplicate_mmsi'))
            )
            AND ais_positionanomaly.implied_kn < EXCLUDED.implied_kn
        )
    """
)


def upsert_hits(frame: pd.DataFrame) -> int:
    if frame.empty:
        return 0
    rows = []
    for _, row in frame.iterrows():
        event_day = row.get("event_day")
        if event_day is None or (isinstance(event_day, float) and pd.isna(event_day)):
            ts = _ts(row.get("ts"))
            event_day = None if ts is None else ts.date()
        elif hasattr(event_day, "date") and not isinstance(event_day, datetime):
            event_day = event_day
        else:
            event_day = pd.Timestamp(event_day).date()
        rows.append({
            "mmsi": int(row["mmsi"]),
            "event_day": event_day,
            "ts": _ts(row.get("ts")),
            "prev_ts": _ts(row.get("prev_ts")),
            "latitude": float(row["latitude"]),
            "longitude": float(row["longitude"]),
            "prev_latitude": float(row["prev_lat"]),
            "prev_longitude": float(row["prev_lon"]),
            "dist_m": float(row["dist_m"]),
            "dt_s": int(row["dt_s"]),
            "implied_kn": float(row["implied_kn"]),
            "reason": str(row["reason"]),
            "rule_version": RULE_VERSION,
        })
    with get_engine().begin() as conn:
        conn.execute(UPSERT, rows)
    return len(rows)


def run_once() -> int:
    date_from, date_to, _meta = resolve_track_range(None, None)
    hits = load_teleport_hits(date_from, date_to, mmsi_list=cargo_mmsis())
    # Split before daily dedupe so a high-implied ghost does not displace a
    # teleport alert. UNIQUE (mmsi, event_day): prefer the alert row.
    alerts, quality = _split_alert_and_quality(hits)
    alerts = dedupe_daily(alerts)
    quality = dedupe_daily(quality)
    if not alerts.empty and not quality.empty:
        alert_keys = set(
            zip(alerts["mmsi"].astype(int), alerts["event_day"].astype(str))
        )
        quality = quality[
            ~quality.apply(
                lambda r: (int(r["mmsi"]), str(r["event_day"])) in alert_keys,
                axis=1,
            )
        ]
    deduped = pd.concat([alerts, quality], ignore_index=True) if len(quality) or len(alerts) else hits.iloc[0:0]
    written = upsert_hits(deduped)
    logging.info(
        "Spoofing store: raw=%s alerts=%s quality=%s kept=%s",
        len(hits),
        len(alerts),
        len(quality),
        written,
    )
    return written


def main() -> None:
    ensure_tables(get_engine())
    logging.info("Spoofing store started (interval=%ss)", LOOP_SECONDS)
    while True:
        try:
            run_once()
        except KeyboardInterrupt:
            logging.info("Stopping spoofing store.")
            return
        except Exception:
            logging.exception("Spoofing store cycle failed")
        time.sleep(LOOP_SECONDS)


if __name__ == "__main__":
    main()
