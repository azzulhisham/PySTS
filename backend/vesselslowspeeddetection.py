from typing import Optional
from urllib.parse import quote
from datetime import datetime, timedelta, timezone

from sqlmodel import Field, SQLModel, create_engine, Session, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy import and_, or_, desc, text, Column, BigInteger
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


    # Upsert into PostgreSQL for low speed vessels
    with Session(engine) as session:
        for _, row in vessel_in_low_speed_df.iterrows():
            logging.info(f"Processing vessel with MMSI: {row['mmsi']} at timestamp: {row['ts']}")

            existing_activity = session.execute(
                select(Ais_VesselSlowMoveActivities)
                    .where(
                        and_(
                            Ais_VesselSlowMoveActivities.mmsi == int(row["mmsi"]),
                            Ais_VesselSlowMoveActivities.tsout == None
                        )                        
                    )
            ).scalar_one_or_none()

            # # find the distance between 2 points
            df_dist = duckdb.sql(f"""
                SELECT
                    ST_Distance_Sphere(
                        ST_Point({row["longitude"]}, {row["latitude"]}),
                        ST_Point({row["longitude"] if existing_activity is None else existing_activity.curlongitude}, {row["latitude"] if existing_activity is None else existing_activity.curlatitude})
                    ) AS distance_m
            """).fetchdf()            

            # distance
            distance = df_dist['distance_m'][0]

            if existing_activity:
                if existing_activity.tsout is None:
                    # update existing row
                    is_newer_position = existing_activity.tscurrent is None or row["ts"] > existing_activity.tscurrent

                    if is_newer_position:
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
          
            else:
                # insert new row, id will be auto-generated
                new_activity = Ais_VesselSlowMoveActivities(
                    ts=row["ts"],
                    mmsi=int(row["mmsi"]),
                    navstatus=row["navStatus"],
                    navstatusdesc=row["navStatusDesc"],
                    longitude=row["longitude"],
                    latitude=row["latitude"],
                    sog=row["sog"],
                    cog=row["cog"],
                    rowcount=1 if existing_activity is None else existing_activity.rowcount + 1,
                    rowcount2=0 if existing_activity is None else existing_activity.rowcount2,
                    tsstop=None if existing_activity is None else (row["ts"] if existing_activity.rowcount >= 30 and float(distance) < 30 else None),
                    tsout=None,
                    tscurrent=row["ts"],
                    curlongitude=row["longitude"],
                    curlatitude=row["latitude"],
                    cursog=row["sog"],
                    curcog=row["cog"],
                    distance=float(distance)                   
                )
                session.add(new_activity)

        session.commit()

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

    # Upsert into PostgreSQL for high speed vessels
    current_activities_df = get_cur_activities_data(engine)
    current_activities_df["navstatusdesc"] = current_activities_df["navstatusdesc"].astype("object")
    cnt = 0 

    with Session(engine) as session:
        for _, row in current_activities_df.iterrows():  
            logging.info(f"Checking high speed activity for vessel with MMSI: {row['mmsi']} at timestamp: {row['ts']}")
            
            vessel_in_high_speed_df["navStatusDesc"] = vessel_in_high_speed_df["navStatusDesc"].astype("object")
            duckdb.register("vessel_in_high_speed_df", vessel_in_high_speed_df)

            vessel_in_high_speed = duckdb.query(f"""
                SELECT *
                FROM vessel_in_high_speed_df
                WHERE mmsi = {row['mmsi']}
            """).to_df()         

            if row["tsout"] is None and not vessel_in_high_speed.empty:
            # # find the distance between 2 points
                df_dist = duckdb.sql(f"""
                    SELECT
                        ST_Distance_Sphere(
                            ST_Point({row["longitude"]}, {row["latitude"]}),
                            ST_Point({row["longitude"] if vessel_in_high_speed is None else vessel_in_high_speed["longitude"].iloc[0]}, {row["latitude"] if vessel_in_high_speed is None else vessel_in_high_speed["latitude"].iloc[0]})
                        ) AS distance_m
                """).fetchdf()            

                # distance
                distance = df_dist['distance_m'][0]    

                stmt = text("""
                    UPDATE ais_vesselslowmoveactivities
                    SET tsout = :tsout, rowcount = :rowcount, rowcount2 = :rowcount2, distance = :distance
                    WHERE id = :id
                """)

                session.execute(stmt, {
                    "tsout": None if row['rowcount2'] >= -10 else (vessel_in_high_speed["ts"].iloc[0] if not vessel_in_high_speed.empty and float(distance) >= 100 else None),
                    "rowcount": row["rowcount"] if row["tsstop"] is not None else (1 if row["rowcount"] <= 1 else row["rowcount"] - 1),
                    "rowcount2": -1 if row["rowcount2"] is None or row["rowcount2"] >= 1 else row["rowcount2"] - 1,
                    "distance": float(distance),
                    "id": row["id"]
                })

                cnt += 1

        session.commit() 


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

