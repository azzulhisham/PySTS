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
    engine = create_engine(
        DATABASE_URL, 
        pool_size=10,
        max_overflow=20,
        pool_timeout=30,  # seconds    
        # echo=True
    )  # echo=True for logging SQL

    return engine


def create_db_and_tables(engine: Engine):
    SQLModel.metadata.create_all(engine)


def get_ais_position_data(engine: Engine) -> pd.DataFrame:
    query = text("""
        SELECT *
        FROM public.ais_position
        WHERE latitude >= :lat_min AND latitude <= :lat_max AND ts >= :ts_min
        ORDER BY "ts"
    """)

    # Define parameters
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


def _sphere_distance_m(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    df_dist = duckdb.sql(f"""
        SELECT ST_Distance_Sphere(
            ST_Point({lon1}, {lat1}),
            ST_Point({lon2}, {lat2})
        ) AS distance_m
    """).fetchdf()
    return float(df_dist["distance_m"][0])


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

    # Upsert into PostgreSQL for low speed vessels
    with Session(engine) as session:
        open_by_mmsi = _load_open_activities(session)

        for _, row in vessel_in_low_speed_df.iterrows():
            low_speed_seen += 1
            mmsi = int(row["mmsi"])
            logging.debug("Processing vessel with MMSI: %s at timestamp: %s", mmsi, row["ts"])

            existing_activity = open_by_mmsi.get(mmsi)

            compare_lon = row["longitude"] if existing_activity is None else existing_activity.curlongitude
            compare_lat = row["latitude"] if existing_activity is None else existing_activity.curlatitude
            distance = _sphere_distance_m(row["longitude"], row["latitude"], compare_lon, compare_lat)

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
                low_speed_inserted += 1

        session.commit()

    logging.info(
        "Low-speed pass: seen=%s updated=%s inserted=%s",
        low_speed_seen,
        low_speed_updated,
        low_speed_inserted,
    )

    # Upsert into PostgreSQL for high speed vessels
    current_activities_df = get_cur_activities_data(engine)
    current_activities_df["navstatusdesc"] = current_activities_df["navstatusdesc"].astype("object")
    open_count = len(current_activities_df)
    high_speed_candidate_count = len(high_speed_by_mmsi)
    cnt = 0
    high_speed_skipped_no_fix = 0

    with Session(engine) as session:
        for _, row in current_activities_df.iterrows():
            if row["tsout"] is not None:
                continue

            mmsi = int(row["mmsi"])
            high_speed_fix = high_speed_by_mmsi.get(mmsi)
            if high_speed_fix is None:
                high_speed_skipped_no_fix += 1
                continue

            logging.debug("Checking high speed activity for vessel with MMSI: %s at timestamp: %s", mmsi, row["ts"])

            distance = _sphere_distance_m(
                row["longitude"],
                row["latitude"],
                high_speed_fix["longitude"],
                high_speed_fix["latitude"],
            )

            stmt = text("""
                UPDATE ais_vesselmovementactivities
                SET tsout = :tsout, rowcount = :rowcount, rowcount2 = :rowcount2, distance = :distance
                WHERE id = :id
            """)

            session.execute(stmt, {
                "tsout": None if row["rowcount2"] >= -10 else (high_speed_fix["ts"] if float(distance) >= 30 else None),
                "rowcount": row["rowcount"] if row["tsstop"] is not None else (1 if row["rowcount"] <= 1 else row["rowcount"] - 1),
                "rowcount2": -1 if row["rowcount2"] is None or row["rowcount2"] >= 1 else row["rowcount2"] - 1,
                "distance": float(distance),
                "id": row["id"],
            })

            cnt += 1

        session.commit()

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
