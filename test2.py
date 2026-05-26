from typing import Optional
from urllib.parse import quote
from datetime import datetime, timedelta, timezone

from sqlmodel import Field, SQLModel, create_engine, Session, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy import and_, or_, desc, text, Column, BigInteger
from sqlalchemy.engine import Engine

from backend.polygons import *

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


if __name__ == "__main__":
    pg_engine = get_pgEngine()

    pos_data = get_ais_position_data(pg_engine)
    pos_data["navStatusDesc"] = pos_data["navStatusDesc"].astype("object")

    df = duckdb.sql(f'''
        SELECT ts, mmsi, sog, longitude, latitude
        FROM pos_data
        WHERE ST_Within(ST_Point(longitude, latitude), ST_GeomFromGeoJSON({restrictedlimit_db})) 
    ''').fetchdf()


    print(df['sog'].max())
    print(df['sog'].min())
    print(df['sog'].mean())
    print(df['sog'].median())