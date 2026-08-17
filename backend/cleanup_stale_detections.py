"""
One-off cleanup for detections that can never resolve themselves.

Three problems are fixed, all read-only until --apply is passed:

1. Movement / slow-move activities left open forever. Both detectors can only set
   `tsout` when a *fresh* high-speed fix arrives for that MMSI, and both read only
   the last 2-3 days of `ais_position`. A vessel that stops transmitting therefore
   falls out of its own detector's input and stays open indefinitely, keeping it a
   permanent STS candidate. Closed here with `tsout = tscurrent` (last known contact).

2. Proximity observations still open on fixes older than the cutoff. Closed with
   reason 'stale_source'.

3. Proximity observations whose members are all one hull (a re-flagged vessel next
   to its retired identity). Closed with reason 'same_vessel'.

Usage:
    python cleanup_stale_detections.py                      # dry run, 7-day cutoff
    python cleanup_stale_detections.py --stale-days 14      # dry run, other cutoff
    python cleanup_stale_detections.py --apply              # write changes
"""

from argparse import ArgumentParser
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

import logging
import pandas as pd

from vesselproximitydetection import build_identity_map, signature_is_one_vessel


logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

DEFAULT_STALE_DAYS = 7
SAMPLE_ROWS = 10

pswd = "m4r1t1m3"
encoded_password = quote(pswd)
DATABASE_URL = (
    f"postgresql://postgresadmin:{encoded_password}"
    f"@marineai2.cxwk8yige5f2.ap-southeast-5.rds.amazonaws.com:5432/pnav"
)

ACTIVITY_TABLES = ("ais_vesselmovementactivities", "ais_vesselslowmoveactivities")


def get_pgEngine() -> Engine:
    """Create and return a pooled SQLAlchemy engine for PostgreSQL."""
    return create_engine(DATABASE_URL, pool_size=5, max_overflow=5, pool_timeout=30)



def _log_frame(title: str, df: pd.DataFrame):
    """Log a sample of rows that a step would touch."""
    if df.empty:
        logging.info("%s: nothing to do.", title)
        return

    logging.info(
        "%s: %s row(s). Sample:\n%s",
        title, len(df), df.head(SAMPLE_ROWS).to_string(index=False),
    )



def preview_stale_activities(engine: Engine, table: str, cutoff: datetime) -> pd.DataFrame:
    """List open activity rows whose last AIS fix is older than the cutoff."""
    query = text(f"""
        SELECT id, mmsi, tscurrent, tsstop, rowcount,
               now() - tscurrent AS fix_age
        FROM public.{table}
        WHERE tsout IS NULL
          AND tscurrent IS NOT NULL
          AND tscurrent < :cutoff
        ORDER BY tscurrent
    """)

    return pd.read_sql(query, con=engine, params={"cutoff": cutoff})



def close_stale_activities(engine: Engine, table: str, cutoff: datetime) -> int:
    """Close open activity rows by setting tsout to their last known contact time."""
    stmt = text(f"""
        UPDATE public.{table}
        SET tsout = tscurrent
        WHERE tsout IS NULL
          AND tscurrent IS NOT NULL
          AND tscurrent < :cutoff
    """)

    with engine.begin() as conn:
        return conn.execute(stmt, {"cutoff": cutoff}).rowcount



def preview_stale_observations(engine: Engine, cutoff: datetime) -> pd.DataFrame:
    """List open proximity observations whose newest member fix is older than the cutoff."""
    query = text("""
        SELECT o.id, o.cluster_signature, o.first_detected_at, o.run_count,
               o.duration_seconds / 86400.0 AS duration_days, o.suspicion_score,
               max(m.tscurrent) AS newest_member_fix,
               now() - max(m.tscurrent) AS fix_age
        FROM public.ais_vesselproximityobservation o
        JOIN public.ais_vesselproximitymember m ON m.observation_id = o.id
        WHERE o.is_open = TRUE
        GROUP BY o.id
        HAVING max(m.tscurrent) < :cutoff
        ORDER BY max(m.tscurrent)
    """)

    return pd.read_sql(query, con=engine, params={"cutoff": cutoff})



def preview_same_vessel_observations(engine: Engine) -> pd.DataFrame:
    """List open proximity observations whose members all resolve to one hull."""
    open_obs = pd.read_sql(text("""
        SELECT o.id, o.cluster_signature, o.suspicion_score, o.run_count,
               o.duration_seconds / 86400.0 AS duration_days
        FROM public.ais_vesselproximityobservation o
        WHERE o.is_open = TRUE AND o.cluster_signature IS NOT NULL
    """), con=engine)

    if open_obs.empty:
        return open_obs

    identities = build_identity_map(pd.read_sql(text("""
        SELECT mmsi, "shipName", callsign, imo, to_bow, to_stern, to_port, to_starboard
        FROM (
            SELECT *, row_number() OVER (PARTITION BY mmsi ORDER BY ts DESC) AS rowcount_static
            FROM public.ais_static
        ) sub
        WHERE rowcount_static = 1
    """), con=engine))

    matches = open_obs["cluster_signature"].apply(
        lambda sig: signature_is_one_vessel(sig, identities)
    )

    return open_obs[matches].reset_index(drop=True)



def close_observations(engine: Engine, observation_ids: list[int], reason: str) -> int:
    """Close the given observations and freeze duration from their detection window."""
    if not observation_ids:
        return 0

    stmt = text("""
        UPDATE public.ais_vesselproximityobservation
        SET is_open = FALSE,
            closed_at = :closed_at,
            close_reason = :reason,
            duration_seconds = GREATEST(
                EXTRACT(EPOCH FROM (last_detected_at - first_detected_at)), 0
            )
        WHERE id = ANY(:ids) AND is_open = TRUE
    """)

    with engine.begin() as conn:
        return conn.execute(stmt, {
            "closed_at": datetime.now(timezone.utc),
            "reason": reason,
            "ids": [int(i) for i in observation_ids],
        }).rowcount



def run_cleanup(engine: Engine, stale_days: int, apply: bool) -> dict:
    """Preview (and optionally apply) every cleanup step; return per-step counts."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=stale_days)
    counts: dict[str, int] = {}

    logging.info(
        "Cleanup %s: fixes older than %s day(s), i.e. before %s.",
        "APPLY" if apply else "DRY RUN", stale_days, cutoff.isoformat(),
    )

    for table in ACTIVITY_TABLES:
        stale = preview_stale_activities(engine, table, cutoff)
        _log_frame(f"Open activities to close in {table}", stale)

        counts[table] = close_stale_activities(engine, table, cutoff) if apply else len(stale)

    # Same-hull first: it is the more specific reason for observations that are
    # both stale and duplicated identities.
    same_vessel_obs = preview_same_vessel_observations(engine)
    _log_frame("Open observations that are one hull (same_vessel)", same_vessel_obs)

    counts["observations_same_vessel"] = (
        close_observations(engine, same_vessel_obs["id"].tolist(), "same_vessel")
        if apply else len(same_vessel_obs)
    )

    stale_obs = preview_stale_observations(engine, cutoff)

    if not same_vessel_obs.empty:
        stale_obs = stale_obs[~stale_obs["id"].isin(same_vessel_obs["id"])]

    _log_frame("Open observations on stale fixes (stale_source)", stale_obs)

    counts["observations_stale_source"] = (
        close_observations(engine, stale_obs["id"].tolist(), "stale_source")
        if apply else len(stale_obs)
    )

    logging.info(
        "%s summary: %s",
        "Applied" if apply else "Dry run",
        ", ".join(f"{k}={v}" for k, v in counts.items()),
    )

    if not apply:
        logging.info("Nothing was written. Re-run with --apply to commit these changes.")

    return counts



def main():
    """CLI entry point: preview by default, write only with --apply."""
    parser = ArgumentParser(description="Close detections that can never resolve themselves.")
    parser.add_argument(
        "--stale-days", type=int, default=DEFAULT_STALE_DAYS,
        help=f"treat AIS fixes older than this as dead (default {DEFAULT_STALE_DAYS})",
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="write the changes; without it the script only reports",
    )
    args = parser.parse_args()

    run_cleanup(get_pgEngine(), args.stale_days, args.apply)



if __name__ == "__main__":
    main()
