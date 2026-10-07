from typing import Optional
from urllib.parse import quote
from datetime import datetime, timedelta, timezone

from sqlmodel import Field, SQLModel, create_engine, Session, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy import text, Column, BigInteger
from sqlalchemy.engine import Engine

import gc
import os
import time
import pandas as pd
import duckdb
import psycopg2
import math
import json
import platform
import logging
from psycopg2.extras import execute_values



# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


STALE_TRANSPONDER_MINUTES = 30
STALE_TRANSPONDER_MIN_ROWCOUNT = 1


# install duckdb extensions
# wget http://extensions.duckdb.org/v1.2.0/linux_amd64_gcc4/spatial.duckdb_extension.gz
duckdb.sql("INSTALL spatial")

# loading spatial extension
duckdb.sql("LOAD spatial")



pswd = 'm4r1t1m3'
encoded_password = quote(pswd)
DATABASE_URL = f"postgresql://postgresadmin:{encoded_password}@marineai2.cxwk8yige5f2.ap-southeast-5.rds.amazonaws.com:5432/pnav"


class Ais_VesselSlowMoveActivities(SQLModel, table=True):
    # id: Optional[int] = Field(default=None, primary_key=True)
    id: Optional[int] = Field(
        default=None,
        sa_column=Column(BigInteger, primary_key=True)
    )

    ts: datetime

    # mmsi: BigInteger = Field(index=True)
    mmsi: int = Field(
        sa_column=Column(BigInteger, index=True)
    )

    navstatus: int
    navstatusdesc: str = Field(default=None)

    longitude: float
    latitude: float 
    cog: float
    sog: float

    rowcount: int = Field(
        sa_column=Column(BigInteger, index=True)
    )
    rowcount2: int = Field(
        sa_column=Column(BigInteger, index=True)
    )    

    distance: float
    tsstop: Optional[datetime] = Field(default=None)
    tsout: Optional[datetime] = Field(default=None)
    tscurrent: Optional[datetime] = Field(default=None)

    curlongitude: Optional[float] = Field(default=None)
    curlatitude: Optional[float] = Field(default=None)
    cursog: Optional[float] = Field(default=None)
    curcog: Optional[float] = Field(default=None) 
 


_engine = None
PG_STATEMENT_TIMEOUT_MS = 60000
PG_LOCK_TIMEOUT_MS = 5000


def get_pgEngine():
    global _engine
    if _engine is None:
        _engine = create_engine(
            DATABASE_URL,
            pool_size=2,
            max_overflow=0,
            pool_timeout=30,
            pool_pre_ping=True,
            pool_recycle=1800,
            connect_args={
                "application_name": "sts_slowspeed",
                "options": (
                    f"-c statement_timeout={PG_STATEMENT_TIMEOUT_MS}"
                    f" -c lock_timeout={PG_LOCK_TIMEOUT_MS}"
                ),
            },
        )
    return _engine


def create_db_and_tables(engine: Engine):
    SQLModel.metadata.create_all(engine)


def get_ais_position_data(engine: Engine) -> pd.DataFrame:
    """Latest AIS fixes used by this detector.

    ais_position is one row per MMSI; the 3-day ts filter is a freshness gate
    (same predicate as before). Only columns the detector reads are selected.
    """
    query = text("""
        SELECT ts, mmsi, "navStatus", "navStatusDesc",
               longitude, latitude, cog, sog
        FROM public.ais_position
        WHERE latitude >= :lat_min AND latitude <= :lat_max AND ts >= :ts_min
        ORDER BY "ts"
    """)

    params = {"lat_min": -90, "lat_max": 90, "ts_min": datetime.now(timezone.utc) - timedelta(days=3)}
    df = pd.read_sql(query, con=engine, params=params)

    return df


def get_cur_activities_data(engine: Engine) -> pd.DataFrame:
    query = text("""
        SELECT *
        FROM public.ais_vesselslowmoveactivities
        WHERE tsout IS NULL
        ORDER BY "ts"
    """)

    df = pd.read_sql(query, con=engine)  

    return df


def _sphere_distances_duckdb(pairs: pd.DataFrame) -> list[float]:
    """Vectorized ST_Distance_Sphere — same function the old per-row DuckDB calls used."""
    if pairs.empty:
        return []
    duckdb.register("_slowmove_dist_pairs", pairs)
    out = duckdb.sql("""
        SELECT ST_Distance_Sphere(
            ST_Point(lon1, lat1),
            ST_Point(lon2, lat2)
        ) AS distance_m
        FROM _slowmove_dist_pairs
    """).fetchdf()
    return [0.0 if (x is None or (isinstance(x, float) and math.isnan(x))) else float(x) for x in out["distance_m"].tolist()]


def _load_all_open_activities(session: Session) -> list[Ais_VesselSlowMoveActivities]:
    """All open rows ordered by ts (same set get_cur_activities_data would return)."""
    return list(
        session.execute(
            select(Ais_VesselSlowMoveActivities)
            .where(Ais_VesselSlowMoveActivities.tsout == None)
            .order_by(Ais_VesselSlowMoveActivities.ts)
        ).scalars()
    )


def _open_by_mmsi_earliest(
    open_rows: list[Ais_VesselSlowMoveActivities],
) -> dict[int, Ais_VesselSlowMoveActivities]:
    """One open row per MMSI (earliest ts first if duplicates exist in data)."""
    open_by_mmsi: dict[int, Ais_VesselSlowMoveActivities] = {}
    for activity in open_rows:
        if activity.mmsi not in open_by_mmsi:
            open_by_mmsi[activity.mmsi] = activity
    return open_by_mmsi


def _apply_stale_tsstop_in_memory(
    open_rows: list[Ais_VesselSlowMoveActivities],
    stale_cutoff: datetime,
    min_rowcount: int,
) -> None:
    """Mirror the bulk stale UPDATE onto already-loaded objects (no second table read)."""
    for activity in open_rows:
        if activity.tsout is not None:
            continue
        if activity.tsstop is not None:
            continue
        if activity.tscurrent is None:
            continue
        if activity.rowcount is None or activity.rowcount < min_rowcount:
            continue
        ts_cur = activity.tscurrent
        if getattr(ts_cur, "tzinfo", None) is None and stale_cutoff.tzinfo is not None:
            # Compare naive DB timestamps to aware cutoff in UTC terms.
            ts_cmp = ts_cur.replace(tzinfo=timezone.utc) if hasattr(ts_cur, "replace") else ts_cur
        else:
            ts_cmp = ts_cur
        try:
            is_stale = ts_cmp < stale_cutoff
        except TypeError:
            # Fallback: strip tz from cutoff
            is_stale = ts_cur < stale_cutoff.replace(tzinfo=None)
        if is_stale:
            activity.tsstop = activity.tscurrent


def _first_high_speed_row_by_mmsi(high_speed_df: pd.DataFrame) -> dict[int, pd.Series]:
    """First high-speed fix per MMSI (dataframe already ordered by ts)."""
    if high_speed_df.empty:
        return {}
    grouped: dict[int, pd.Series] = {}
    for mmsi, group in high_speed_df.groupby("mmsi", sort=False):
        grouped[int(mmsi)] = group.iloc[0]
    return grouped


def _batch_high_speed_updates(engine: Engine, rows: list[tuple]) -> None:
    """Apply high-speed exit updates in one statement (same assignments as before)."""
    if not rows:
        return
    sql = """
        UPDATE public.ais_vesselslowmoveactivities AS t SET
            tsout = v.tsout::timestamp,
            rowcount = v.rowcount::bigint,
            rowcount2 = v.rowcount2::bigint,
            distance = v.distance::double precision
        FROM (VALUES %s) AS v(id, tsout, rowcount, rowcount2, distance)
        WHERE t.id = v.id::bigint
    """
    connection = engine.raw_connection()
    try:
        with connection.cursor() as cursor:
            execute_values(cursor, sql, rows, page_size=len(rows))
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def estimate_latlng(init_lat, init_lng, cog):
    # Earth radius in meters
    R = 6371000  

    # Initial position (example)
    lat0_deg = init_lat         # degrees
    lon0_deg = init_lng         # degrees

    # Convert to radians for calculation
    lat0 = math.radians(lat0_deg)
    lon0 = math.radians(lon0_deg)

    # Heading (bearing in degrees → radians)
    theta = math.radians(cog)  # example: due east

    # Distance traveled during deceleration (approx. 540 m)
    d = 540.0

    # Latitude change (radians)
    delta_lat = (d * math.cos(theta)) / R

    # Longitude change (radians)
    delta_lon = (d * math.sin(theta)) / (R * math.cos(lat0))

    # Final position in radians
    lat1 = lat0 + delta_lat
    lon1 = lon0 + delta_lon

    # Convert back to degrees for mapping
    lat1_deg = math.degrees(lat1)
    lon1_deg = math.degrees(lon1)

    print("Final latitude (degrees):", lat1_deg)
    print("Final longitude (degrees):", lon1_deg)

    return lat1_deg, lon1_deg


def upsert_vessel_activities(engine: Engine):
    df = get_ais_position_data(engine)
    df["navStatusDesc"] = df["navStatusDesc"].astype("object")

    if df.empty:
        logging.info("No new AIS position data to process.")
        return 0

    # Process the data using DuckDB
    duckdb.register("ais_position", df)
    vessel_in_low_speed_df = duckdb.query("""
        SELECT *
        FROM ais_position
        WHERE sog <= 3.0
        ORDER BY "ts"
    """).to_df()

    vessel_in_high_speed_df = duckdb.query("""
        SELECT *
        FROM ais_position
        WHERE sog > 3.0
        ORDER BY "ts"
    """).to_df()

    high_speed_by_mmsi = _first_high_speed_row_by_mmsi(vessel_in_high_speed_df)
    low_speed_seen = 0
    low_speed_skipped_not_newer = 0
    low_speed_updated = 0
    low_speed_inserted = 0

    # Upsert into PostgreSQL for low speed vessels.
    # expire_on_commit=False: open_rows are reused after commit for the high-speed
    # pass (same data get_cur_activities_data would reload, without a second scan).
    with Session(engine, expire_on_commit=False) as session:
        open_rows = _load_all_open_activities(session)
        open_by_mmsi = _open_by_mmsi_earliest(open_rows)

        # ais_position is one row per MMSI, so compare points are fixed for this cycle
        # and distances can be computed once with the same ST_Distance_Sphere as before.
        dist_pairs = []
        for _, row in vessel_in_low_speed_df.iterrows():
            existing_activity = open_by_mmsi.get(int(row["mmsi"]))
            compare_lon = row["longitude"] if existing_activity is None else existing_activity.curlongitude
            compare_lat = row["latitude"] if existing_activity is None else existing_activity.curlatitude
            dist_pairs.append(
                {
                    "lon1": row["longitude"],
                    "lat1": row["latitude"],
                    "lon2": compare_lon,
                    "lat2": compare_lat,
                }
            )
        low_speed_distances = _sphere_distances_duckdb(pd.DataFrame(dist_pairs))

        for row_idx, (_, row) in enumerate(vessel_in_low_speed_df.iterrows()):
            low_speed_seen += 1
            mmsi = int(row["mmsi"])
            logging.debug("Processing low-speed MMSI %s at %s", mmsi, row["ts"])

            existing_activity = open_by_mmsi.get(mmsi)
            distance = low_speed_distances[row_idx] if row_idx < len(low_speed_distances) else 0.0

            if existing_activity:
                if existing_activity.tsout is None:
                    # Same predicate as before: tscurrent is None or row ts is strictly newer.
                    is_newer_position = (
                        existing_activity.tscurrent is None
                        or row["ts"] > existing_activity.tscurrent
                    )

                    if not is_newer_position:
                        low_speed_skipped_not_newer += 1
                        continue

                    has_position_changed = (
                        float(distance) > 0
                        and existing_activity.tsstop is None
                        and row["longitude"] != existing_activity.curlongitude
                        and row["latitude"] != existing_activity.curlatitude
                    )

                    existing_activity.navstatus = row["navStatus"]
                    existing_activity.navstatusdesc = row["navStatusDesc"]
                    existing_activity.rowcount += 1 if has_position_changed else 0
                    existing_activity.tsstop = row["ts"] if existing_activity.rowcount >= 30 and float(distance) < 30 and existing_activity.tsstop is None else (None if existing_activity.tsstop is None else existing_activity.tsstop)
                    existing_activity.tscurrent = row["ts"]
                    existing_activity.curlongitude = row["longitude"]
                    existing_activity.curlatitude = row["latitude"]
                    existing_activity.cursog = row["sog"]
                    existing_activity.curcog = row["cog"]
                    existing_activity.distance = float(distance)
                    low_speed_updated += 1

            else:
                new_activity = Ais_VesselSlowMoveActivities(
                    ts=row["ts"],
                    mmsi=mmsi,
                    navstatus=row["navStatus"],
                    navstatusdesc=row["navStatusDesc"],
                    longitude=row["longitude"],
                    latitude=row["latitude"],
                    sog=row["sog"],
                    cog=row["cog"],
                    rowcount=1,
                    rowcount2=0,
                    tsstop=None,
                    tsout=None,
                    tscurrent=row["ts"],
                    curlongitude=row["longitude"],
                    curlatitude=row["latitude"],
                    cursog=row["sog"],
                    curcog=row["cog"],
                    distance=float(distance),
                )
                session.add(new_activity)
                open_by_mmsi[mmsi] = new_activity
                open_rows.append(new_activity)
                low_speed_inserted += 1

        session.commit()

    logging.info(
        "Low-speed pass: seen=%s skipped_not_newer=%s updated=%s inserted=%s",
        low_speed_seen,
        low_speed_skipped_not_newer,
        low_speed_updated,
        low_speed_inserted,
    )

    stale_cutoff = datetime.now(timezone.utc) - timedelta(minutes=STALE_TRANSPONDER_MINUTES)

    with Session(engine) as session:
        stale_stmt = text("""
            UPDATE ais_vesselslowmoveactivities
            SET tsstop = tscurrent
            WHERE tsstop IS NULL
              AND tsout IS NULL
              AND tscurrent IS NOT NULL
              AND tscurrent < :stale_cutoff
              AND rowcount >= :min_rowcount
        """)
        stale_result = session.execute(stale_stmt, {
            "stale_cutoff": stale_cutoff,
            "min_rowcount": STALE_TRANSPONDER_MIN_ROWCOUNT,
        })
        session.commit()

    logging.info(f"Marked {stale_result.rowcount} stale slow-speed activities as suspected stopped/dark.")

    # Keep in-memory open rows aligned with the stale UPDATE (avoids a second full read).
    _apply_stale_tsstop_in_memory(open_rows, stale_cutoff, STALE_TRANSPONDER_MIN_ROWCOUNT)

    # High-speed exit pass — only MMSIs with sog > 3 in this cycle's AIS batch
    open_count = len(open_rows)
    high_speed_candidate_count = len(high_speed_by_mmsi)
    cnt = 0
    high_speed_skipped_no_fix = 0
    high_speed_updates: list[tuple] = []

    candidates: list[tuple] = []  # (activity, high_speed_fix)
    for activity in open_rows:
        if activity.tsout is not None:
            continue
        mmsi = int(activity.mmsi)
        high_speed_fix = high_speed_by_mmsi.get(mmsi)
        if high_speed_fix is None:
            high_speed_skipped_no_fix += 1
            continue
        candidates.append((activity, high_speed_fix))

    if candidates:
        pair_rows = []
        for activity, high_speed_fix in candidates:
            pair_rows.append(
                {
                    "lon1": activity.longitude,
                    "lat1": activity.latitude,
                    "lon2": high_speed_fix["longitude"],
                    "lat2": high_speed_fix["latitude"],
                }
            )
        distances = _sphere_distances_duckdb(pd.DataFrame(pair_rows))

        for (activity, high_speed_fix), distance in zip(candidates, distances):
            mmsi = int(activity.mmsi)
            logging.debug("Checking high-speed exit for MMSI %s", mmsi)

            rowcount = activity.rowcount
            rowcount2 = activity.rowcount2
            tsstop = activity.tsstop

            # Identical to the previous pandas/SQLAlchemy parameter expression.
            tsout = (
                None
                if rowcount2 >= -10
                else (high_speed_fix["ts"] if float(distance) >= 100 else None)
            )
            new_rowcount = rowcount if tsstop is not None else (1 if rowcount <= 1 else rowcount - 1)
            new_rowcount2 = -1 if rowcount2 is None or rowcount2 >= 1 else rowcount2 - 1

            tsout_param = None
            if tsout is not None:
                tsout_param = pd.Timestamp(tsout).to_pydatetime()

            if activity.id is None:
                logging.warning(
                    "Skipping high-speed update for MMSI %s: missing activity id after commit",
                    mmsi,
                )
                continue

            high_speed_updates.append(
                (activity.id, tsout_param, new_rowcount, new_rowcount2, float(distance))
            )
            cnt += 1

    _batch_high_speed_updates(engine, high_speed_updates)

    logging.info(
        "High-speed pass: open_rows=%s skipped_no_high_speed_fix=%s candidates=%s updates=%s",
        open_count,
        high_speed_skipped_no_fix,
        high_speed_candidate_count,
        cnt,
    )
    logging.info(f"Processed {len(vessel_in_low_speed_df)} low-speed AIS rows; {cnt} high-speed exit updates.")

    return len(vessel_in_low_speed_df)



if __name__ == "__main__":
    runFlg = True

    pg_engine = get_pgEngine()
    create_db_and_tables(pg_engine)    

    # df = get_ais_position_data(pg_engine)
    # print(df.info())

    while runFlg:
        try:
            logging.info(f'Fetching data....')
            rslt = upsert_vessel_activities(pg_engine)
            gc.collect()

        except KeyboardInterrupt:
            runFlg = False

        except Exception as e:
            logging.info(f"Exception :: {e}")  


        logging.info(f'System sleep....')
        time.sleep(20)  
