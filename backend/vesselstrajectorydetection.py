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
import json
import platform
import logging
import math
from psycopg2.extras import execute_values



# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


# install duckdb extensions
# wget http://extensions.duckdb.org/v1.2.0/linux_amd64_gcc4/spatial.duckdb_extension.gz
duckdb.sql("INSTALL spatial")

# loading spatial extension
duckdb.sql("LOAD spatial")



pswd = 'm4r1t1m3'
encoded_password = quote(pswd)
DATABASE_URL = f"postgresql://postgresadmin:{encoded_password}@marineai2.cxwk8yige5f2.ap-southeast-5.rds.amazonaws.com:5432/pnav"

PG_STATEMENT_TIMEOUT_MS = 60000
PG_LOCK_TIMEOUT_MS = 5000

_engine = None


class Ais_VesselMovementActivities(SQLModel, table=True):
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
 

def get_pgEngine():
    # One cached engine. Previously every call built a new pool of up to 30
    # connections with no application_name, so these sessions showed up blank in
    # pg_stat_activity and could pin a backend for the whole Python loop.
    global _engine
    if _engine is None:
        _engine = create_engine(
            DATABASE_URL,
            pool_size=2,
            max_overflow=2,
            pool_timeout=30,
            pool_pre_ping=True,
            pool_recycle=1800,
            connect_args={
                "application_name": "sts_trajectory",
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
    query = text("""
        SELECT ts, mmsi, "navStatus", "navStatusDesc",
               longitude, latitude, cog, sog
        FROM public.ais_position
        WHERE latitude >= :lat_min AND latitude <= :lat_max AND ts >= :ts_min
        ORDER BY "ts"
    """)

    params = {"lat_min": -90, "lat_max": 90, "ts_min": datetime.now(timezone.utc) - timedelta(days=2)}
    df = pd.read_sql(query, con=engine, params=params)

    return df


def get_cur_activities_data(engine: Engine) -> pd.DataFrame:
    query = text("""
        SELECT *
        FROM public.ais_vesselmovementactivities
        WHERE tsout IS NULL
        ORDER BY "ts"
    """)

    df = pd.read_sql(query, con=engine)  

    return df


def _sphere_distances_duckdb(pairs: pd.DataFrame) -> list[float]:
    """Vectorized ST_Distance_Sphere — same function the old per-row DuckDB calls used."""
    if pairs.empty:
        return []
    duckdb.register("_traj_dist_pairs", pairs)
    out = duckdb.sql("""
        SELECT ST_Distance_Sphere(
            ST_Point(lon1, lat1),
            ST_Point(lon2, lat2)
        ) AS distance_m
        FROM _traj_dist_pairs
    """).fetchdf()
    return [
        0.0 if (x is None or (isinstance(x, float) and math.isnan(x))) else float(x)
        for x in out["distance_m"].tolist()
    ]


def _sphere_distance_m(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    return _sphere_distances_duckdb(pd.DataFrame([{
        "lon1": lon1, "lat1": lat1, "lon2": lon2, "lat2": lat2,
    }]))[0]


def _load_open_activities(session: Session) -> dict[int, Ais_VesselMovementActivities]:
    """One open row per MMSI (earliest ts first if duplicates exist in data)."""
    open_by_mmsi: dict[int, Ais_VesselMovementActivities] = {}
    rows = session.execute(
        select(Ais_VesselMovementActivities)
        .where(Ais_VesselMovementActivities.tsout == None)
        .order_by(Ais_VesselMovementActivities.ts)
    ).scalars()
    for activity in rows:
        if activity.mmsi not in open_by_mmsi:
            open_by_mmsi[activity.mmsi] = activity
    return open_by_mmsi


def _batch_high_speed_updates(engine: Engine, rows: list[tuple]) -> None:
    """Apply high-speed exit updates in one statement (same assignments as before)."""
    if not rows:
        return
    sql = """
        UPDATE public.ais_vesselmovementactivities AS t SET
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


def _first_high_speed_row_by_mmsi(high_speed_df: pd.DataFrame) -> dict[int, pd.Series]:
    """First high-speed fix per MMSI (dataframe already ordered by ts)."""
    if high_speed_df.empty:
        return {}
    grouped: dict[int, pd.Series] = {}
    for mmsi, group in high_speed_df.groupby("mmsi", sort=False):
        grouped[int(mmsi)] = group.iloc[0]
    return grouped


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
        WHERE sog <= 0.5
        ORDER BY "ts"
    """).to_df()

    vessel_in_high_speed_df = duckdb.query("""
        SELECT *
        FROM ais_position
        WHERE sog > 0.5
        ORDER BY "ts"
    """).to_df()

    high_speed_by_mmsi = _first_high_speed_row_by_mmsi(vessel_in_high_speed_df)
    low_speed_seen = 0
    low_speed_updated = 0
    low_speed_inserted = 0

    # expire_on_commit=False: open rows are reused after commit for the high-speed
    # pass (same data get_cur_activities_data would reload, without a second scan).
    with Session(engine, expire_on_commit=False) as session:
        open_by_mmsi = _load_open_activities(session)
        open_rows = list(open_by_mmsi.values())

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
            logging.debug("Processing vessel with MMSI: %s at timestamp: %s", mmsi, row["ts"])

            existing_activity = open_by_mmsi.get(mmsi)
            distance = low_speed_distances[row_idx] if row_idx < len(low_speed_distances) else 0.0

            if existing_activity:
                if existing_activity.tsout is None:
                    # update existing row
                    existing_activity.navstatus = row["navStatus"]
                    existing_activity.navstatusdesc = row["navStatusDesc"]
                    existing_activity.rowcount += 1 if existing_activity.tsstop is None and row["ts"] > existing_activity.tscurrent and float(distance) > 0 and row["longitude"] != existing_activity.curlongitude and row["latitude"] != existing_activity.curlatitude else 0
                    existing_activity.tsstop = row["ts"] if existing_activity.rowcount >= 20 and float(distance) < 30 and existing_activity.tsstop is None else (None if existing_activity.tsstop is None else existing_activity.tsstop)
                    existing_activity.tscurrent = row["ts"]
                    existing_activity.curlongitude = row["longitude"]
                    existing_activity.curlatitude = row["latitude"]
                    existing_activity.cursog = row["sog"]
                    existing_activity.curcog = row["cog"]
                    existing_activity.distance = float(distance)
                    low_speed_updated += 1

            else:
                # insert new row, id will be auto-generated
                new_activity = Ais_VesselMovementActivities(
                    ts=row["ts"],
                    mmsi=mmsi,
                    navstatus=row["navStatus"],
                    navstatusdesc=row["navStatusDesc"],
                    longitude=row["longitude"],
                    latitude=row["latitude"],
                    sog=row["sog"],
                    cog=row["cog"],
                    rowcount=1 if existing_activity is None else existing_activity.rowcount + 1,
                    rowcount2=0 if existing_activity is None else existing_activity.rowcount2,
                    tsstop=None if existing_activity is None else (row["ts"] if existing_activity.rowcount >= 20 and float(distance) < 30 else None),
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
        "Low-speed pass: seen=%s updated=%s inserted=%s",
        low_speed_seen,
        low_speed_updated,
        low_speed_inserted,
    )

    # High-speed exit pass — same predicates as the previous per-row UPDATE.
    open_count = len(open_rows)
    high_speed_candidate_count = len(high_speed_by_mmsi)
    cnt = 0
    high_speed_skipped_no_fix = 0
    high_speed_updates: list[tuple] = []

    candidates: list[tuple] = []
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
            logging.debug("Checking high speed activity for vessel with MMSI: %s at timestamp: %s", mmsi, activity.ts)

            tsout = (
                None
                if activity.rowcount2 >= -10
                else (high_speed_fix["ts"] if float(distance) >= 30 else None)
            )
            new_rowcount = activity.rowcount if activity.tsstop is not None else (1 if activity.rowcount <= 1 else activity.rowcount - 1)
            new_rowcount2 = -1 if activity.rowcount2 is None or activity.rowcount2 >= 1 else activity.rowcount2 - 1

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
    logging.info(f"Upserted {len(vessel_in_low_speed_df)} vessel low speed activity records.")
    logging.info(f"Upserted {cnt} vessel high speed activity records.")

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
