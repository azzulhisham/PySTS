import calendar
import json
import numpy as np
import pandas as pd
import duckdb

from datetime import datetime, timedelta, timezone
from dateutil.relativedelta import relativedelta
from sqlmodel import SQLModel, create_engine
from sqlalchemy import text
from sqlalchemy.engine import Engine
from urllib.parse import quote



# install duckdb extensions
# wget http://extensions.duckdb.org/v1.2.0/linux_amd64_gcc4/spatial.duckdb_extension.gz
duckdb.sql("INSTALL spatial")

# loading spatial extension
# duckdb.sql("LOAD './app/spatial.duckdb_extension'")
duckdb.sql("LOAD spatial")


pswd = 'm4r1t1m3'
encoded_password = quote(pswd)
DATABASE_URL = f"postgresql://postgresadmin:{encoded_password}@marineai2.cxwk8yige5f2.ap-southeast-5.rds.amazonaws.com:5432/pnav"


# -----------------------------
#    create postgres engine
# -----------------------------
def get_pgEngine():
    engine = create_engine(
        DATABASE_URL, 
        pool_size=10,
        max_overflow=20,
        pool_timeout=30,  # seconds    
        # echo=True
    )  # echo=True for logging SQL

    return engine


def main():
    engine = get_pgEngine()

    query = text(f"""
        SELECT *
        FROM public.ais_static
    """)

    df_static = pd.read_sql(query, con=engine) 
    df_static["shipTypeDesc"] = df_static["shipTypeDesc"].astype("object")
    df_static["shipName"] = df_static["shipName"].astype("object")
    df_static["callsign"] = df_static["callsign"].astype("object")
    df_static["destination"] = df_static["destination"].astype("object")


    query = text(f"""
        SELECT *
        FROM (
            SELECT *,  row_number() OVER (PARTITION BY mmsi ORDER BY ts ) AS rowcount_mmsi
            FROM public.ais_vesselmovementactivities	
        )
        WHERE tsstop is not null 
            AND tsout is null
            AND tsstop >= now() - interval '4 HOURS'
        ORDER BY curlongitude, curlatitude
    """)

    df = pd.read_sql(query, con=engine)  
    df["navstatusdesc"] = df["navstatusdesc"].astype("object")

    df_shift = duckdb.sql(f'''
        SELECT *,
            COALESCE(LEAD(curlongitude) OVER (ORDER BY curlongitude, curlatitude), curlongitude) AS next_curlong,
            COALESCE(LEAD(curlatitude) OVER (ORDER BY curlongitude, curlatitude), curlatitude) AS next_curlat
        FROM df
    ''').to_df()

    df_shift["navstatusdesc"] = df_shift["navstatusdesc"].astype("object")
    df_dist = duckdb.sql(f'''
        SELECT *,
            ST_Distance_Sphere(
                ST_Point(next_curlat, next_curlong),
                ST_Point(curlatitude, curlongitude)
            ) AS distance_m
        FROM df_shift
    ''').to_df()


    selected_df = pd.DataFrame()  

    for i, row in df_dist.iterrows(): 
        if float(row['distance_m']) > 0 and float(row['distance_m']) <= 50:
            if i < len(df_dist) - 1:
                selected_df = df_dist.iloc[[i, i + 1]]
                selected_df["navstatusdesc"] = selected_df["navstatusdesc"].astype("object")

                if len(selected_df) >= 2:
                    df_anal = duckdb.sql(f'''
                        SELECT sel.*, st.shipTypeDesc, st.shipName
                        FROM selected_df sel
                        JOIN df_static st ON st.mmsi = sel.mmsi 
                        ORDER BY sel.tsstop            
                    ''').to_df()    

                    print(df_anal)

                    selected_df = pd.DataFrame()  
                    df_anal = pd.DataFrame()  



if __name__ == "__main__":
    main()