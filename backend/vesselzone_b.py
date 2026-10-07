# pip install sqlmodel psycopg2

from typing import Optional
from urllib.parse import quote
from datetime import datetime, timedelta

from sqlmodel import Field, SQLModel, create_engine, Session, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy import and_, or_, desc, text, bindparam

import gc
import os
import time
import clickhouse_connect
import pandas as pd
import duckdb
import psycopg2
import platform
import logging

from polygons import *


# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


# install duckdb extensions
# wget http://extensions.duckdb.org/v1.2.0/linux_amd64_gcc4/spatial.duckdb_extension.gz
duckdb.sql("INSTALL spatial")

# loading spatial extension
duckdb.sql("LOAD spatial")


# if platform.processor().lower() == 'arm':
#     duckdb.sql("LOAD './analyzer/spatial.duckdb_extension_osx_arm64'")     # for MacOS
# else:
#     duckdb.sql("LOAD './analyzer/spatial.duckdb_extension'")     # for Linux



zones = [
    restrictedlimit_db
]


entire_tss_region = get_entire_tss_region_setting()
entire_sector789_region = get_entire_sector789_region_setting()
outter_restricted_region = get_outter_restricted_region_setting()



class Ais_Position(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    ts: datetime
    mmsi: int = Field(index=True)
    navStatus: int
    navStatusDesc: str
    longitude: float
    latitude: float
    rot: float
    cog: float
    sog: float
    trueHeading: float


class Ais_VesselInRestrictZone(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    tsDetected: datetime
    mmsi: int = Field(index=True)
    navStatus: int
    navStatusDesc: str
    longitude: float
    latitude: float 
    rot: float
    cog: float
    sog: float
    trueHeading: float    
    tsCurrent: Optional[datetime] = Field(default=None)
    tsOut: Optional[datetime] = Field(default=None)
    zone: Optional[int] = Field(default=None)



# Database URL (adjust username, password, host, port, database name)
# pswd = 'Az@HoePinc0615'
# encoded_password = quote(pswd)
# DATABASE_URL = f"postgresql://postgres:{encoded_password}@localhost:5432/pnav"

pswd = 'm4r1t1m3'
encoded_password = quote(pswd)
DATABASE_URL = f"postgresql://postgresadmin:{encoded_password}@marineai2.cxwk8yige5f2.ap-southeast-5.rds.amazonaws.com:5432/pnav"

COMMIT_BATCH_SIZE = 300

# The stale-record sweep closes zones older than 5 days whose vessel has left the
# restricted area. Running it every cycle meant a full scan of the whole
# ais_vesselinrestrictzone table every 30s to find rows that can only change once
# a day. An hourly sweep is still far more frequent than the 5-day threshold it
# enforces, so what it detects is unchanged.
CLEANUP_INTERVAL_MINUTES = 60

# A blocked write fails fast instead of pinning a backend behind a lock holder.
PG_STATEMENT_TIMEOUT_MS = 60000
PG_LOCK_TIMEOUT_MS = 5000

_engine = None


def get_pgEngine():
    # Previously this built a new Engine on every call, so each cycle created
    # several independent pools of up to 30 connections. One cached engine is
    # reused instead.
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
                # Named so these connections are attributable in pg_stat_activity
                # instead of showing up as a blank application_name.
                "application_name": "sts_vesselzone_b",
                "options": (
                    f"-c statement_timeout={PG_STATEMENT_TIMEOUT_MS}"
                    f" -c lock_timeout={PG_LOCK_TIMEOUT_MS}"
                ),
            },
        )

    return _engine


def _chunks(rows, size):
    for start in range(0, len(rows), size):
        yield rows[start : start + size]


def _write_batches(items_to_update, items_to_insert):
    """Write the decided changes in short, self-contained transactions.

    The zone decisions are made in pure Python first and only then written. The
    previous shape kept one Session open across the whole per-vessel loop and
    committed every 300 vessels, so between commits the connection sat in
    'idle in transaction' while Python did spatial work. Any such session older
    than 60s is enough on its own to report the database as degraded.
    """
    engine = get_pgEngine()

    for chunk in _chunks(items_to_update, COMMIT_BATCH_SIZE):
        with Session(engine) as session:
            session.bulk_update_mappings(Ais_VesselInRestrictZone, chunk)
            session.commit()

    for chunk in _chunks(items_to_insert, COMMIT_BATCH_SIZE):
        with Session(engine) as session:
            session.bulk_insert_mappings(Ais_VesselInRestrictZone, chunk)
            session.commit()


def get_pgConn():
    conn = psycopg2.connect(
        dbname="pnav",
        user="postgresadmin",
        password="m4r1t1m3",
        host="marineai2.cxwk8yige5f2.ap-southeast-5.rds.amazonaws.com",
        port="5432"
    )

    return conn


def create_db_and_tables():
    SQLModel.metadata.create_all(get_pgEngine())


def get_ais_position_data():
    # results = None
    # with Session(engine) as session:
    #     statement = (
    #         select(Ais_Position)
    #         .where(and_(Ais_Position.latitude >= -90, Ais_Position.latitude <= 90))
    #         .order_by(Ais_Position.ts)  
    #         # .limit(1000)  
    #     )

    #     results = session.exec(statement).all()

    query = text("""
        SELECT *
        FROM public.ais_positionb
        WHERE latitude >= :lat_min AND latitude <= :lat_max AND ts >= :ts_min
        ORDER BY "ts"
    """)

    # Define parameters
    params = {"lat_min": -90, "lat_max": 90, "ts_min": datetime.utcnow() - timedelta(hours=96)}
    df = pd.read_sql(query, con=get_pgEngine(), params=params)  

    return df


def get_vessel_data():
    ais_data = get_ais_position_data()

    df = duckdb.sql(f'''
        SELECT *
        FROM ais_data
        WHERE ST_Within(ST_Point(longitude, latitude), ST_GeomFromGeoJSON({outter_restricted_region})) 
            -- OR ST_Within(ST_Point(longitude, latitude), ST_GeomFromGeoJSON({entire_sector789_region}))
            -- OR ST_Within(ST_Point(longitude, latitude), ST_GeomFromGeoJSON({entire_sector789_region}))
    ''').fetchdf()

    del ais_data
    gc.collect()

    return df.to_dict(orient='records') 


def upsert_ais_position(data):
    logging.info(f'Upserting data....{len(data)}')

    items_to_update = []
    items_to_insert = []
    current_vessels_zone = []

    # Only the MMSIs in this batch are ever looked up below, so loading every open
    # record in the table read the whole 318k-row table to use a few hundred rows,
    # and without a predicate on mmsi it was a sequential scan. The tsDetected
    # DESC order is kept because the lookup below takes the first match.
    batch_mmsis = sorted({int(i['mmsi']) for i in data})

    query = text("""
        SELECT *
        FROM public.ais_vesselinrestrictzone
        WHERE "tsOut" IS NULL
          AND mmsi IN :mmsis
        ORDER BY "tsDetected" DESC
    """).bindparams(bindparam("mmsis", expanding=True))

    try:
        df = pd.read_sql(query, con=get_pgEngine(), params={"mmsis": batch_mmsis})
        current_vessels_zone = df.to_dict(orient='records')   

        del df
        gc.collect()  

    except:
        logging.info(f'Error reading database....')
        return 0


    logging.info(f'Loading data....{len(current_vessels_zone)}')

    # The zone decisions below are pure Python with no transaction open; only
    # after the loop is anything written.
    for cnt, i in enumerate(data):
        # ais_position = Ais_Position(**i)   

        for idx, zone in enumerate(zones):
            rslt = duckdb.sql(f'''
                SELECT ST_Within(ST_Point({i['longitude']}, {i['latitude']}), ST_GeomFromGeoJSON({zone})) as within_area
            ''').fetchall()       

            in_zone = rslt[0][0] 
            existing_vessel_zone = False

            try:
                existing_vessel_zone = next(filter(lambda x: x["mmsi"] == i['mmsi'] and x["zone"] == idx and pd.isnull(x['tsOut']), current_vessels_zone), None)      
            except:
                continue


            if in_zone:
                if existing_vessel_zone:
                    logging.info(f"[UPDATE] :: vessel {i['mmsi']} in zone {existing_vessel_zone['zone']}")    
                    payload = existing_vessel_zone.copy()      #.model_dump()

                    payload["longitude"] = i['longitude']
                    payload["latitude"] = i['latitude'] 
                    payload["sog"] = i['sog'] 
                    payload["cog"] = i['cog'] 
                    payload["rot"] = i['rot'] 
                    payload["trueHeading"] = i['trueHeading'] 
                    payload["tsCurrent"] = i['ts']                                     
                    items_to_update.append(payload)                         

                else:
                    logging.info(f"[INSERT] :: vessel {i['mmsi']} entered zone {idx}")
                    new_vessel_zone = {
                        "tsDetected": i['ts'],
                        "mmsi": i['mmsi'],
                        "navStatus": i['navStatus'],
                        "navStatusDesc": i['navStatusDesc'],
                        "longitude": i['longitude'],
                        "latitude": i['latitude'], 
                        "sog": i['sog'], 
                        "cog": i['cog'], 
                        "rot": i['rot'], 
                        "trueHeading": i['trueHeading'],
                        "tsCurrent": i['ts'],
                        "tsOut": None,
                        "zone": idx                       
                    }

                    items_to_insert.append(new_vessel_zone)
            else:                    
                if existing_vessel_zone:
                    logging.info(f"[UPDATE] :: vessel {i['mmsi']} exit zone {existing_vessel_zone['zone']}")
                    payload = existing_vessel_zone      #.model_dump()

                    payload["tsOut"] = i['ts']                 
                    items_to_update.append(payload)                


    _write_batches(items_to_update, items_to_insert)

    logging.info(f'Upserting data done....')
    
    del data
    gc.collect()

    return 0 


def chk_invalid_data():
    logging.info(f'Clearing invalid data....')

    current_vessels_inrec = []

    query = text("""
        SELECT *
        FROM public.ais_positionb
        WHERE mmsi in (
            SELECT mmsi
            FROM public.ais_vesselinrestrictzone
            where "tsOut" is null
            AND "tsDetected" < NOW() - INTERVAL '5 days'
        )
    """)

    try:
        df = pd.read_sql(query, con=get_pgEngine())  
        current_vessels_inrec = df.to_dict(orient='records')   

        outside = []

        for idx, itm in enumerate(current_vessels_inrec):
            rslt = duckdb.sql(f'''
                SELECT ST_Within(ST_Point({itm['longitude']}, {itm['latitude']}), ST_GeomFromGeoJSON({outter_restricted_region})) as within_area
            ''').fetchall()       

            in_zone = rslt[0][0]  

            if not in_zone:
                logging.info(f'Updating data for mmsi: {itm["mmsi"]}')
                outside.append(int(itm['mmsi']))

        # One short transaction instead of opening a fresh connection, running one
        # statement and committing, once per vessel. The predicate is unchanged,
        # so the same open records are closed; now() is the transaction timestamp
        # and was already identical within each of the old single-statement
        # transactions.
        if outside:
            update_qry = text("""
                UPDATE public.ais_vesselinrestrictzone
                SET "tsOut" = now()
                WHERE mmsi IN :mmsis
            """).bindparams(bindparam("mmsis", expanding=True))

            with get_pgEngine().begin() as conn:
                conn.execute(update_qry, {"mmsis": outside})

        del df
        gc.collect()  

    except Exception as e:
        # Was a bare 'except: pass', so a sweep that never worked would have been
        # invisible.
        logging.info(f'Error clearing invalid data....{e}')


    return 0


if __name__ == "__main__":
    runFlg = True
    create_db_and_tables()    

    # Sweep once on startup, then only every CLEANUP_INTERVAL_MINUTES.
    next_cleanup = 0.0

    while runFlg:
        try:
            logging.info(f'Fetching data....')
            vessels_data = get_vessel_data()
            rslt = upsert_ais_position(vessels_data)

            del vessels_data
            gc.collect()

            if time.monotonic() >= next_cleanup:
                next_cleanup = time.monotonic() + CLEANUP_INTERVAL_MINUTES * 60
                chk_invalid_data()

        except KeyboardInterrupt:
            runFlg = False

        except Exception as e:
            logging.info(f"Exception :: {e}")  


        logging.info(f'System sleep....')
        time.sleep(30)     
       









