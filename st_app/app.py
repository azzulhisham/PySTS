import streamlit as st
import plotly.express as px
import plotly.io as pio
import streamlit.components.v1 as components
from streamlit_card import card

import uuid
import time
import math
import gc
import pydeck as pdk
import pandas as pd
import psycopg2
import duckdb

from geopy.distance import geodesic
from geopy.point import Point

from typing import Optional
from urllib.parse import quote
from datetime import datetime, timedelta, UTC

from sqlmodel import Field, SQLModel, create_engine, Session, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy import and_, or_, desc, text
from pydeck.types import String

from polygons import *


# install duckdb extensions
# wget http://extensions.duckdb.org/v1.2.0/linux_amd64_gcc4/spatial.duckdb_extension.gz
duckdb.sql("INSTALL spatial")

# loading spatial extension
# duckdb.sql("LOAD './app/spatial.duckdb_extension'")
duckdb.sql("LOAD spatial")


# Database URL (adjust username, password, host, port, database name)
# pswd = 'Az@HoePinc0615'
# encoded_password = quote(pswd)
# DATABASE_URL = f"postgresql://postgres:{encoded_password}@localhost:5432/pnav"

pswd = 'm4r1t1m3'
encoded_password = quote(pswd)
DATABASE_URL = f"postgresql://postgresadmin:{encoded_password}@marineai2.cxwk8yige5f2.ap-southeast-5.rds.amazonaws.com:5432/pnav"

menu = ['All', 'STS', 'Bunkering', 'Others']

if 'reload' not in st.session_state: st.session_state.reload = False
st.set_page_config(layout="wide", page_title="TSS")


lats = 0
lngs = 0

for lat, lng in restrictedlimit:
    lats += lat
    lngs += lng


MAPBOX_TOKEN = 'pk.eyJ1IjoiYXp6dWxoaXNoYW0iLCJhIjoiY2s5bjR1NDBqMDJqNDNubjdveXdiOGswYyJ9.SYlfXRzRtpbFoM2PHskvBg'

INITIAL_VIEW_STATE = pdk.ViewState(
    latitude=lats/len(restrictedlimit),
    longitude=lngs/len(restrictedlimit),
    zoom=9,
    pitch=0
)

with open('./st_app/style.css', 'r') as f:
    style = f.read()


def get_pgEngine():
    engine = create_engine(
        DATABASE_URL, 
        pool_size=10,
        max_overflow=20,
        pool_timeout=30,  # seconds    
        # echo=True
    )  # echo=True for logging SQL

    return engine


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
    SQLModel.metadata.create_all(get_pgEngine(), checkfirst=True)


def gen_qry_summary_board():
    qry = ''

    for idxs, sec in enumerate(opt_sector):
        sub_qry = ''
        main_qry = ''
        
        for idxt, tss in enumerate(opt_tss):
            output_col_name = f'Sector{(opt_sector.index(sec)) + 1}_{tss[0:5]}'

            # sector selected
            if sec in selected_sector:
                sub_qry = f'''
                    SELECT mmsi
                    FROM public.ais_vesselinzone
                    WHERE zone = {(opt_sector.index(sec)) + 1} AND "tsOut" IS NULL             
                '''

                # tss selected
                if tss in selected_tss:
                    main_qry = f'''
                        SELECT COUNT(*) AS "{output_col_name}"
                        FROM public.ais_vesselinzone
                        WHERE zone = {(opt_tss.index(tss)) + 10} AND "tsOut" IS NULL          
                    ''' 

                    qry += f'''
                        {sec.lower()[0:1]}{(opt_sector.index(sec)) + 1}{tss[0:1]} AS (
                            {main_qry} {'AND mmsi IN (' if sub_qry != '' else ''}
                                {sub_qry}
                            {')' if sub_qry != '' else ''}
                        ){',' if idxt == 0 or qry != '' else ''}
                    ''' 
                else:
                    qry += f'''
                        {sec.lower()[0:1]}{(opt_sector.index(sec)) + 1}{tss[0:1]} AS (
                            SELECT 0 AS "{output_col_name}"
                        ){',' if idxt == 0 or qry != '' else ''}
                    '''                 

            else:
                qry += f'''
                    {sec.lower()[0:1]}{(opt_sector.index(sec)) + 1}{tss[0:1]} AS (
                        SELECT 0 AS "{output_col_name}"
                    ){',' if idxt == 0 or qry != '' else ''}
                '''


    
    selc = '''
        SELECT *,
            ("Sector1_North" + "Sector1_South") AS "total_sector1",
            ("Sector2_North" + "Sector2_South") AS "total_sector2",
            ("Sector3_North" + "Sector3_South") AS "total_sector3",
            ("Sector4_North" + "Sector4_South") AS "total_sector4",
            ("Sector5_North" + "Sector5_South") AS "total_sector5",
            ("Sector6_North" + "Sector6_South") AS "total_sector6",
            ("Sector1_North" + "Sector2_North" + "Sector3_North" + "Sector4_North" + "Sector5_North" + "Sector6_North") AS "total_tss_north",
            ("Sector1_South" + "Sector2_South" + "Sector3_South" + "Sector4_South" + "Sector5_South" + "Sector6_South") AS "total_tss_south",
            ("Sector1_North" + "Sector1_South" + "Sector2_North" + "Sector2_South" + "Sector3_North" + "Sector3_South" + "Sector4_North" + "Sector4_South" + "Sector5_North" + "Sector5_South" + "Sector6_North" + "Sector6_South") AS "total"           
        FROM s1N, s1S,
            s2N, s2S,
            s3N, s3S,
            s4N, s4S,
            s5N, s5S,
            s6N, s6S
    '''

    qry = qry.strip()
    query = 'WITH ' + qry[:len(qry)-1] + selc
    return query


def get_ais_counting_data():
    results = None
    qry = gen_qry_summary_board()

    query = text(qry)

    df = pd.read_sql(query, con=get_pgEngine())  
    results = df.to_dict(orient='records')  

    del df
    gc.collect()        
    
    return results


def gen_qry_vessel_zone_static():
    qry = ''

    for idxs, sec in enumerate(opt_sector):
        sub_qry = ''
        main_qry = ''

        union = '''
            UNION
        '''
        data_order = '''
            ORDER BY "ShipType"      
        '''

        
        for idxt, tss in enumerate(opt_tss):
            output_col_name = f'Sec{(opt_sector.index(sec)) + 1}_{tss[0:5]}'

            # sector selected
            if sec in selected_sector:
                sub_qry = f'''
                    SELECT mmsi
                    FROM public.ais_vesselinzone
                    WHERE zone = {(opt_sector.index(sec)) + 1} AND "tsOut" IS NULL                    
                '''

                # tss selected
                if tss in selected_tss:
                    main_qry = f'''
                        SELECT COUNT(*) AS "Count", 
                            s."shipType" AS "ShipTypeNo", 
                            CASE
                                WHEN s."shipTypeDesc" IS NULL THEN 'Not Available'
                                ELSE s."shipTypeDesc"
                            END AS "ShipType"
                        FROM public.ais_vesselinzone vz
                        LEFT JOIN public.ais_static s on s.mmsi = vz.mmsi
                        WHERE vz.zone = {(opt_tss.index(tss)) + 10} AND vz."tsOut" IS NULL AND vz.mmsi IN (
                            {sub_qry}
                        )
                        GROUP BY s."shipType", s."shipTypeDesc"        
                    ''' 

                    qry += (union if qry != '' else '') + main_qry
                

    return qry + data_order


def ais_vessel_zone_static():
    qry = gen_qry_vessel_zone_static()
    query = text(qry)

    df = pd.read_sql(query, con=get_pgEngine())  
    results = df.to_dict(orient='records')  

    del df
    gc.collect()        
    
    return results    


def get_ais_position_data():
    results = None

    # query all vessel on its latest position
    query = text("""
        SELECT p.*, s."shipType", s."shipTypeDesc", s."shipName", s."callsign", s."imo", s.to_bow, s.to_stern, s.to_port, s.to_starboard, s."destination",
			CASE
				WHEN s."shipType" >= 40 AND s."shipType" < 50 THEN 'hs_craft'
				WHEN s."shipType" >= 50 AND s."shipType" < 60 THEN 'tug'
				WHEN s."shipType" >= 60 AND s."shipType" < 70 THEN 'passenger'
				WHEN s."shipType" >= 70 AND s."shipType" < 80 THEN 'cargo'
				WHEN s."shipType" >= 80 AND s."shipType" < 90 THEN 'tanker'
				ELSE 'others'
			END AS "shipcatagory",
            (s.to_bow + s.to_stern) AS length_m,
            (s.to_port + s.to_starboard) AS beam_m
        FROM public.ais_position p
        LEFT JOIN public.ais_static s on s.mmsi = p.mmsi
        WHERE p.latitude >= :lat_min AND p.latitude <= :lat_max AND p.ts >= :ts_min
            AND p.mmsi != 0
        --ORDER BY p."ts"

		UNION ALL
		
		SELECT p.*, s."shipType", s."shipTypeDesc", s."shipName", s."callsign", s."imo", s.to_bow, s.to_stern, s.to_port, s.to_starboard, s."destination",
			CASE
				WHEN s."shipType" >= 40 AND s."shipType" < 50 THEN 'hs_craft'
				WHEN s."shipType" >= 50 AND s."shipType" < 60 THEN 'tug'
				WHEN s."shipType" >= 60 AND s."shipType" < 70 THEN 'passenger'
				WHEN s."shipType" >= 70 AND s."shipType" < 80 THEN 'cargo'
				WHEN s."shipType" >= 80 AND s."shipType" < 90 THEN 'tanker'
				ELSE 'others'
			END AS "shipcatagory",
            (s.to_bow + s.to_stern) AS length_m,
            (s.to_port + s.to_starboard) AS beam_m
        FROM public.ais_positionb p
        LEFT JOIN public.ais_staticb s on s.mmsi = p.mmsi
        WHERE p.latitude >= -90 AND p.latitude <= 90 
            AND p.mmsi != 0
		ORDER BY "ts"        
    """)

    # Define parameters
    params = {"lat_min": -90, "lat_max": 90, "ts_min": datetime.now(UTC) - timedelta(days=10)}

    df = pd.read_sql(query, con=get_pgEngine(), params=params)  

    category_color_map = {
        "hs_craft": [253, 119, 3, 200],         # orange
        "tug": [253, 248, 3, 200],              # yellow
        "passenger": [3, 236, 253, 200],        # cyan
        "cargo": [182, 2, 254, 200],            # violet
        "tanker": [3, 253, 69, 200],            # lime
        "others": [222, 222, 222, 200],         # gray
    }  

    df["shipcolor"] = df["shipcatagory"].map(category_color_map)
    results = df.to_dict(orient='records')  

    # query all vessel in restricted area for more that 2 hours
    query = text("""
        SELECT p.*, s."shipType", s."shipTypeDesc", s."shipName", s."callsign", s."imo", s.to_bow, s.to_stern, s.to_port, s.to_starboard, s."destination",
            CASE
                WHEN s."shipType" >= 40 AND s."shipType" < 50 THEN 'hs_craft'
                WHEN s."shipType" >= 50 AND s."shipType" < 60 THEN 'tug'
                WHEN s."shipType" >= 60 AND s."shipType" < 70 THEN 'passenger'
                WHEN s."shipType" >= 70 AND s."shipType" < 80 THEN 'cargo'
                WHEN s."shipType" >= 80 AND s."shipType" < 90 THEN 'tanker'
                ELSE 'others'
            END AS "shipcatagory",
            CASE
                WHEN p.cog = 360 THEN 0
                ELSE p.cog
            END AS cog_v,            
            (s.to_bow + s.to_stern) AS length_m,
            (s.to_port + s.to_starboard) AS beam_m,
            CASE
                WHEN NOW() - "tsCurrent" > INTERVAL '2.0 hours' THEN 50
                WHEN NOW() - "tsCurrent" < INTERVAL '0.5 hours' THEN 10
                WHEN NOW() - "tsCurrent" < INTERVAL '0.1 hours' THEN 2.5
                ELSE 30
            END AS ts_margin,
            CASE
                WHEN NOW() - "tsCurrent" > INTERVAL '1.0 hours' THEN 1.0
                WHEN NOW() - "tsCurrent" < INTERVAL '0.5 hours' THEN 0.5
                WHEN NOW() - "tsCurrent" < INTERVAL '0.1 hours' THEN 0.3
                ELSE 1
            END AS ts_sog                              
        FROM public.ais_vesselinrestrictzone p
        LEFT JOIN public.ais_static s on s.mmsi = p.mmsi
        WHERE "tsOut" IS NULL AND "latitude" >= :lat_min AND "latitude" <= :lat_max
            AND NOW() - "tsCurrent" > INTERVAL '0.5 hours'
            --AND ("navStatus" = 1)
            AND ("sog" <= 0.7)
        ORDER BY "tsDetected"      
    """)

    # Define parameters
    params = {"lat_min": -90, "lat_max": 90}

    restrict_df = pd.read_sql(query, con=get_pgEngine(), params=params)   
    mmsi_lst = restrict_df['mmsi'].tolist()

    sts = []
    sts_det = []

    # Intelligent Detection
    for mmsi in mmsi_lst:
        if mmsi not in sts:
            rec = restrict_df[restrict_df['mmsi'] == mmsi]

            if rec.iloc[0]['shipType'] >= 80 and rec.iloc[0]['shipType'] < 90: # and rec.iloc[0]['cog'] != 0 and rec.iloc[0]['cog'] != 360:
                dist_nm = duckdb.sql(f'''
                    SELECT *, 
                    CASE
                        WHEN cog = 360 THEN 0
                        ELSE cog
                    END AS cog_v,
                    ST_Distance_Sphere(ST_Point(latitude, longitude), ST_Point({rec.iloc[0]['latitude']}, {rec.iloc[0]['longitude']})) AS dist
                    FROM restrict_df
                    WHERE ST_Distance_Sphere(ST_Point(latitude, longitude), ST_Point({rec.iloc[0]['latitude']}, {rec.iloc[0]['longitude']})) <= CASE
                            WHEN ABS(cog_v - {rec.iloc[0]['cog_v']}) <= 30 OR (ABS(cog_v - {rec.iloc[0]['cog_v']}) >= 150 AND ABS(cog_v - {rec.iloc[0]['cog_v']}) <= 210) THEN (beam_m + {rec.iloc[0]['beam_m']})/2 + ts_margin
                            WHEN ABS(cog_v - {rec.iloc[0]['cog_v']}) > 30 AND ABS(cog_v - {rec.iloc[0]['cog_v']}) < 80 THEN beam_m + {rec.iloc[0]['to_stern']}  
                            WHEN ABS(cog_v - {rec.iloc[0]['cog_v']}) >= 80 AND ABS(cog_v - {rec.iloc[0]['cog_v']}) <= 110 THEN beam_m + {rec.iloc[0]['to_stern']} 
                            WHEN ABS(cog_v - {rec.iloc[0]['cog_v']}) >= 260 AND ABS(cog_v - {rec.iloc[0]['cog_v']}) <= 280 THEN beam_m + {rec.iloc[0]['to_stern']} 
                            WHEN ABS(cog_v - {rec.iloc[0]['cog_v']}) > 300 AND ABS(cog_v - {rec.iloc[0]['cog_v']}) < 350 THEN beam_m + {rec.iloc[0]['to_stern']}   
                            WHEN ABS(cog_v - {rec.iloc[0]['cog_v']}) >= 350 THEN (beam_m + {rec.iloc[0]['beam_m']})/2 + ts_margin                        
                            ELSE (beam_m + {rec.iloc[0]['beam_m']})/2 + ts_margin
                        END
                        AND mmsi != {rec.iloc[0]['mmsi']}
                        AND mmsi != 0
                        AND sog <= ts_sog
                        --AND ((shipType >= 70 AND shipType < 80) OR (shipType >= 80 AND shipType < 90))
                        --AND cog != 0 AND cog != 360
                        --AND (ABS(cog_v - {rec.iloc[0]['cog_v']}) <= 90 OR ((ABS(cog_v - {rec.iloc[0]['cog_v']}) >= 150) AND ABS(cog_v - {rec.iloc[0]['cog_v']}) <= 210))
                ''').fetchdf()
                
                if dist_nm.shape[0] > 0:
                    # print(dist_nm)
                    mmsi_dist_nm = dist_nm['mmsi'].tolist()
                    ts_dist_nm = dist_nm['tsDetected'].tolist()

                    mean_lng = df['longitude'].mean()
                    mean_lat = df['latitude'].mean()

                    if mmsi not in sts:
                        sts.append(mmsi)


                    all_not_in_lst = True
                    all_suspect = True

                    for idx, i in enumerate(mmsi_dist_nm):
                        suspect_time = (datetime.now() - timedelta(hours=8)) - ts_dist_nm[idx]
                        
                        if suspect_time.total_seconds() < 3600:
                            all_suspect = False

                        if i not in sts:
                            sts.append(i)
                        else:
                            all_not_in_lst = False


                    if all_not_in_lst and all_suspect:
                        mmsi_dist_nm.append(mmsi)
                        mmsi_list = sorted(mmsi_dist_nm)

                        sts_info = {
                                "mmsi":  "|".join(str(n) for n in mmsi_list),
                                "mmsi_": rec.iloc[0]['mmsi'],
                                "shipName": rec.iloc[0]['shipName'],
                                "shipType": rec.iloc[0]['shipcatagory'],
                                "shipTypeDesc": rec.iloc[0]['shipTypeDesc'],
                                "navStatusDesc": rec.iloc[0]['navStatusDesc'],
                                "imo": rec.iloc[0]['imo'],
                                "callsign": rec.iloc[0]['callsign'],
                                "length_m": rec.iloc[0]['length_m'],
                                "beam_m": rec.iloc[0]['beam_m'],
                                "sog": rec.iloc[0]['sog'],
                                "cog": rec.iloc[0]['cog'],
                                "longitude": dist_nm['longitude'].mean(),
                                "latitude": dist_nm['latitude'].mean(),
                                "destination": rec.iloc[0]['destination'],
                                "lcltime": (rec.iloc[0]['tsDetected'] + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S"),
                                "utctime": rec.iloc[0]['tsDetected'].strftime("%Y-%m-%d %H:%M:%S"),
                                "color": [255, 0, 0, 150] if rec.iloc[0]['length_m'] == 0 or (dist_nm["length_m"] == 0).any() else ([66, 247, 255, 150] if rec.iloc[0]['length_m'] <= 68 or (dist_nm["length_m"] <= 68).any() else [255, 0, 0, 150]),
                                "radius": 2000
                            }                        
                        sts_det.append(sts_info)
                        # print(sts_info)


    for i in results:
        area_loc = 0
        clr = i['shipcolor'].copy()

        if i['mmsi'] in sts:
            dist_nm = duckdb.sql(f'''
                SELECT ST_Distance_Sphere(ST_Point({1.8672}, {104.71428333333333}), ST_Point({1.863315}, {104.71052833333333})) AS geom
            ''').fetchone()            

            area_loc = 1 
            clr[3] = 300

        else:
            area_loc = 0
            clr[3] = 70 


        i['area_loc'] = area_loc
        i['shipcolor2'] = clr


    del df
    del restrict_df
    gc.collect()        

    return results, sts_det


def get_vessel_data():
    data, sts_det = get_ais_position_data()
    if data != None:
        vessels_data = []

        for i in data:
            addcols = {
                "lcltime": (i['ts'] + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S"),
                "utctime": i['ts'].strftime("%Y-%m-%d %H:%M:%S")
            }

            i.update(addcols)
            vessels_data.append(i)
  

    # df = pd.DataFrame(data)
    df = pd.DataFrame.from_dict(vessels_data)
    return df, sts_det

def calculate_triangle_vertices(latitude, longitude, heading, size_km):
    heading_rad = math.radians(heading)

    point_front = (0, size_km)
    point_left = (-size_km/4, -size_km/4)
    point_right = (size_km/4, -size_km/4)

    cos_theta = math.cos(heading_rad)
    sin_theta = math.sin(heading_rad)

    def rotate_point(x, y):
        x_rotated = x * cos_theta - y * sin_theta
        y_rotated = x * sin_theta + y * cos_theta

        return x_rotated, y_rotated


    rotated_point_front_km = rotate_point(point_front[0], point_front[1])
    rotated_point_left_km = rotate_point(point_left[0], point_left[1])
    rotated_point_right_km = rotate_point(point_right[0], point_right[1])
    center_point = Point(latitude, longitude)

    def translate_point(km_north, km_east):
        new_point = geodesic(kilometers=km_east).destination(center_point, -90)
        new_point = geodesic(kilometers=km_north).destination(Point(new_point.latitude, new_point.longitude), 0)

        return [new_point.longitude, new_point.latitude]


    vertex1 = translate_point(rotated_point_front_km[1], rotated_point_front_km[0])
    vertex2 = translate_point(rotated_point_left_km[1], rotated_point_left_km[0])
    vertex3 = translate_point(rotated_point_right_km[1], rotated_point_right_km[0])

    return [vertex1, vertex2, vertex3]



def load_streamlit_page():
    # Design page layout with 2 columns: File uploader on the left, and other interactions on the right.
    col1, col2 = st.columns([0.15, 0.85], gap="small")


    return col1, col2


def show_summary_board():
    counting_data = get_ais_counting_data()
    show_col1 = True

    with st.sidebar:
        with summary_placeholder:
            df = pd.DataFrame.from_dict(counting_data)
            df = df.transpose()
            df.columns = ['Total']
            st.dataframe(df.iloc[::-1], height=210)

    if show_col1:
        with col1:
            card_placeholder.markdown(f"""
                <br/>
                <br/>

                <div class="card card1-color">
                    <div class="card-title">Sector 1</div>
                    <div class="card-det">
                        <div class="card-label">North</div>
                        <div class="card-label">South</div>
                    </div>                    
                    <div class="card-det">
                        <div class="card-text">{counting_data[0]['Sector1_North']}</div>
                        <div class="card-text">{counting_data[0]['Sector1_South']}</div>
                    </div>
                </div>   
                
                <div class="card card2-color">
                    <div class="card-title">Sector 2</div>
                    <div class="card-det">
                        <div class="card-label">North</div>
                        <div class="card-label">South</div>
                    </div> 
                    <div class="card-det">
                        <div class="card-text">{counting_data[0]['Sector2_North']}</div>
                        <div class="card-text">{counting_data[0]['Sector2_South']}</div>
                    </div>
                </div> 

                <div class="card card3-color">
                    <div class="card-title">Sector 3</div>
                    <div class="card-det">
                        <div class="card-label">North</div>
                        <div class="card-label">South</div>
                    </div> 
                    <div class="card-det">
                        <div class="card-text">{counting_data[0]['Sector3_North']}</div>
                        <div class="card-text">{counting_data[0]['Sector3_South']}</div>
                    </div>
                </div> 

                <div class="card card4-color">
                    <div class="card-title">Sector 4</div>
                    <div class="card-det">
                        <div class="card-label">North</div>
                        <div class="card-label">South</div>
                    </div> 
                    <div class="card-det">
                        <div class="card-text">{counting_data[0]['Sector4_North']}</div>
                        <div class="card-text">{counting_data[0]['Sector4_South']}</div>
                    </div>
                </div> 

                <div class="card card5-color">
                    <div class="card-title">Sector 5</div>
                    <div class="card-det">
                        <div class="card-label">North</div>
                        <div class="card-label">South</div>
                    </div> 
                    <div class="card-det">
                        <div class="card-text">{counting_data[0]['Sector5_North']}</div>
                        <div class="card-text">{counting_data[0]['Sector5_South']}</div>
                    </div>
                </div> 

                <div class="card card6-color">
                    <div class="card-title">Sector 6</div>
                    <div class="card-det">
                        <div class="card-label">North</div>
                        <div class="card-label">South</div>
                    </div> 
                    <div class="card-det">
                        <div class="card-text">{counting_data[0]['Sector6_North']}</div>
                        <div class="card-text">{counting_data[0]['Sector6_South']}</div>
                    </div>
                </div>                                                                                                       
            """, unsafe_allow_html=True)    



# -----  App Start Here  -----
st.markdown(f"""
    <style>
        {style}
    </style>
""", unsafe_allow_html=True)


# st.title('TSS')
# create_db_and_tables()


fg_run = True
fg_search = False

# if st.button('Stop...'):
#     fg_run = False


with st.sidebar:
    st.title('STS')

    with st.container():
        # schBoxCol1, schBoxCol2 = st.columns([0.65, 0.35], gap="small")

        # with schBoxCol1:
        searchMMSI_input = st.text_input('Search', value='', placeholder='Enter MMSI')




    opt_sector = ["Sector 1", "Sector 2", "Sector 3", "Sector 4", "Sector 5", "Sector 6", "Sector 7", "Sector 8", "Sector 9"]
    selected_sector = st.multiselect("Selected Sector:", opt_sector, default=["Sector 1", "Sector 2", "Sector 3", "Sector 4", "Sector 5", "Sector 6"])

    opt_tss = ["Northbound", "Southbound"]
    selected_tss = st.multiselect("Selected TSS:", opt_tss, default=["Northbound", "Southbound"])

    show_chart = st.checkbox("Show Chart")

    st.text('Summary Board')


 

# Make a streamlit page
col1, col2 = load_streamlit_page()
 

with st.sidebar:
    summary_placeholder = st.empty()


with col1:
    card_placeholder = st.empty()

    if not show_chart:
        pass
    else:
        show_summary_board()


with col2:
    if not show_chart:
        map_placeholder = st.empty()  

        st.markdown(f'''
            <br/>
            <br/>
            <br/>
            <br/>
            <br/>
            <br/> 
            <div class="empty-div"></div>                       
            <br/>
            <br/>
            <br/>
        ''', unsafe_allow_html=True)           

        # with st.container(height=60, border=True):
        st.markdown(f'''
            <div class="legend-area">
                <div id="legend-title">Legend:</div>
                <div id="legend-items">
                    <div class="legend-item">
                        <div id="legend-cargo-color"></div> 
                        <div class="legend-area-text">CARGO</div>
                    </div>   
                    <div class="legend-item">
                        <div id="legend-tanker-color"></div> 
                        <div class="legend-area-text">TANKER</div>
                    </div>  
                    <div class="legend-item">
                        <div id="legend-tug-color"></div> 
                        <div class="legend-area-text">TUG</div>
                    </div>   
                    <div class="legend-item">
                        <div id="legend-passenger-color"></div> 
                        <div class="legend-area-text">PASSENGER</div>
                    </div> 
                    <div class="legend-item">
                        <div id="legend-hs-color"></div> 
                        <div class="legend-area-text">HIGH SPEED VESSEL</div>
                    </div>                                          
                    <div class="legend-item">
                        <div id="legend-others-color"></div> 
                        <div class="legend-area-text">OTHERS</div>
                    </div>                                                         
                </div>
            </div>
        ''', unsafe_allow_html=True)          

    else:
        chart_data = ais_vessel_zone_static()
        fig = px.bar(
            chart_data,
            y="Count",
            x="ShipType",
            color='ShipType',
            color_discrete_sequence=px.colors.qualitative.Alphabet,
            height=820
        )

        fig.update_layout(showlegend=True)
        st.plotly_chart(fig)
    

if not show_chart:
    while fg_run:
        show_summary_board()
            
        with col2:
            vessel_data, sts_details = get_vessel_data()

            vessel_data['triangle_vertices'] = vessel_data.apply(
                lambda row: calculate_triangle_vertices(
                    row['latitude'], row['longitude'], row['cog'], 2
                ), axis=1
            )

            if searchMMSI_input:
                search_result = vessel_data[vessel_data["mmsi"] == int(searchMMSI_input)]

                if not search_result.empty:
                    fg_search = True

                    INITIAL_VIEW_STATE = pdk.ViewState(
                        latitude=search_result['latitude'].values[0],
                        longitude=search_result['longitude'].values[0],
                        zoom=11,
                        pitch=0
                    )


            tss_north_layer = pdk.Layer(
                "PolygonLayer",
                tss_northbound,
                get_polygon="polygon",
                get_fill_color=[209, 141, 255, 100],   
                get_line_color=[0, 0, 0],
                line_width_min_pixels=1,
                pickable=False
            )

            tss_south_layer = pdk.Layer(
                "PolygonLayer",
                tss_southbound,
                get_polygon="polygon",
                get_fill_color=[111, 49, 255, 100],  
                get_line_color=[0, 0, 0],
                line_width_min_pixels=1,
                pickable=False
            )    

            restricted_limit_layer = pdk.Layer(
                "PolygonLayer",
                restricted_limit,
                get_polygon="polygon",
                get_fill_color=[228, 166, 76, 100],  
                get_line_color=[0, 0, 0],
                line_width_min_pixels=1,
                pickable=False
            )       

            sector1_limit_layer = pdk.Layer(
                "PolygonLayer",
                sector1_limit,
                get_polygon="polygon",
                get_fill_color=[228, 228, 76, 100],  
                get_line_color=[0, 0, 0],
                line_width_min_pixels=1,
                pickable=False       
            )     

            sector2_limit_layer = pdk.Layer(
                "PolygonLayer",
                sector2_limit,
                get_polygon="polygon",
                get_fill_color=[84, 192, 255, 100], 
                get_line_color=[0, 0, 0],
                line_width_min_pixels=1,
                pickable=False
            )         

            sector3_limit_layer = pdk.Layer(
                "PolygonLayer",
                sector3_limit,
                get_polygon="polygon",
                get_fill_color=[149, 228, 76, 100], 
                get_line_color=[0, 0, 0],
                line_width_min_pixels=1,
                pickable=False
            ) 

            sector4_limit_layer = pdk.Layer(
                "PolygonLayer",
                sector4_limit,
                get_polygon="polygon",
                get_fill_color=[223, 197, 123, 100], 
                get_line_color=[0, 0, 0],
                line_width_min_pixels=1,
                pickable=False
            )     

            sector5_limit_layer = pdk.Layer(
                "PolygonLayer",
                sector5_limit,
                get_polygon="polygon",
                get_fill_color=[239, 195, 202, 100], 
                get_line_color=[0, 0, 0],
                line_width_min_pixels=1,
                pickable=False
            )  

            sector6_limit_layer = pdk.Layer(
                "PolygonLayer",
                sector6_limit,
                get_polygon="polygon",
                get_fill_color=[206, 106, 206, 100], 
                get_line_color=[0, 0, 0],
                line_width_min_pixels=1,
                pickable=False
            )  

            sector7_limit_layer = pdk.Layer(
                "PolygonLayer",
                sector7_limit,
                get_polygon="polygon",
                get_fill_color=[141, 111, 100, 100], 
                get_line_color=[0, 0, 0],
                line_width_min_pixels=1,
                pickable=False
            )      

            sector8_limit_layer = pdk.Layer(
                "PolygonLayer",
                sector8_limit,
                get_polygon="polygon",
                get_fill_color=[234, 255, 117, 100], 
                get_line_color=[0, 0, 0],
                line_width_min_pixels=1,
                pickable=False
            )    

            sector9_limit_layer = pdk.Layer(
                "PolygonLayer",
                sector9_limit,
                get_polygon="polygon",
                get_fill_color=[225, 117, 172, 100], 
                get_line_color=[0, 0, 0],
                line_width_min_pixels=1,
                pickable=False
            )       

            # TextLayer for labels
            sector1_text_layer = pdk.Layer(
                "TextLayer",
                sector1_limit,
                pickable=False,
                get_position="coordinates",
                get_text="name",
                get_size=14,
                get_color=[255, 255, 255],
                get_angle=0,
                # Note that string constants in pydeck are explicitly passed as strings
                # This distinguishes them from columns in a data set
                get_text_anchor=String("middle"),
                get_alignment_baseline=String("center"),
            )   

            # TextLayer for labels
            sector2_text_layer = pdk.Layer(
                "TextLayer",
                sector2_limit,
                pickable=False,
                get_position="coordinates",
                get_text="name",
                get_size=14,
                get_color=[255, 255, 255],
                get_angle=0,
                # Note that string constants in pydeck are explicitly passed as strings
                # This distinguishes them from columns in a data set
                get_text_anchor=String("middle"),
                get_alignment_baseline=String("center"),
            )                        

            # TextLayer for labels
            sector3_text_layer = pdk.Layer(
                "TextLayer",
                sector3_limit,
                pickable=False,
                get_position="coordinates",
                get_text="name",
                get_size=14,
                get_color=[255, 255, 255],
                get_angle=0,
                # Note that string constants in pydeck are explicitly passed as strings
                # This distinguishes them from columns in a data set
                get_text_anchor=String("middle"),
                get_alignment_baseline=String("center"),
            )  

            # TextLayer for labels
            sector4_text_layer = pdk.Layer(
                "TextLayer",
                sector4_limit,
                pickable=False,
                get_position="coordinates",
                get_text="name",
                get_size=14,
                get_color=[255, 255, 255],
                get_angle=0,
                # Note that string constants in pydeck are explicitly passed as strings
                # This distinguishes them from columns in a data set
                get_text_anchor=String("middle"),
                get_alignment_baseline=String("center"),
            )  

            # TextLayer for labels
            sector5_text_layer = pdk.Layer(
                "TextLayer",
                sector5_limit,
                pickable=False,
                get_position="coordinates",
                get_text="name",
                get_size=14,
                get_color=[255, 255, 255],
                get_angle=0,
                # Note that string constants in pydeck are explicitly passed as strings
                # This distinguishes them from columns in a data set
                get_text_anchor=String("middle"),
                get_alignment_baseline=String("center"),
            )  

            # TextLayer for labels
            sector6_text_layer = pdk.Layer(
                "TextLayer",
                sector6_limit,
                pickable=False,
                get_position="coordinates",
                get_text="name",
                get_size=14,
                get_color=[255, 255, 255],
                get_angle=0,
                # Note that string constants in pydeck are explicitly passed as strings
                # This distinguishes them from columns in a data set
                get_text_anchor=String("middle"),
                get_alignment_baseline=String("center"),
            )                                      

            # scatterplot_layer = pdk.Layer(
            #     "ScatterplotLayer",
            #     data = vessel_data,
            #     get_position = ['longitude', 'latitude'],
            #     get_color = [0, 255, 0, 200],
            #     get_radius = 500,
            #     highlight_color = [0, 0, 255, 200],
            #     pickable = True,
            #     auto_highlight = True
            # )

          

            triangle_layer = pdk.Layer(
                "PolygonLayer",
                data = vessel_data,
                get_polygon = 'triangle_vertices',
                get_fill_color = 'shipcolor2',
                get_line_color = [0, 0, 0],
                get_line_width = 1,
                highlight_color = [0, 0, 255, 200],
                pickable = True,
                auto_highlight = True, 
            )    


            # Define the tooltip
            tooltip = {
                "html": '''
                    <b>{mmsi}</b><br/>
                    Vessel Name: {shipName}<br/>
                    Vessel Type: {shipType}<br/>
                    Vessel Type Desc: {shipTypeDesc}<br/>
                    IMO: {imo}<br/>
                    CallSign: {callsign}<br/>
                    Length: {length_m}<br/>
                    Beam: {beam_m}<br/>
                    Lat: {latitude}<br/>
                    Lon: {longitude}<br/>
                    Sog: {sog}<br/>
                    Cog: {cog}<br/>
                    Status: {navStatusDesc}<br/>
                    Destination: {destination}<br/>
                    Last Seen (lcl): {lcltime}<br/>
                    Last Seen (utc): {utctime}<br/>

                ''',
                "style": {
                    "backgroundColor": "steelblue",
                    "color": "white"
                }
            }    

            layerList = []

            if  "Northbound" in selected_tss:  
                layerList.append(tss_north_layer)
                
            if "Southbound" in selected_tss:
                layerList.append(tss_south_layer)

            if "Sector 2" in selected_sector:
                layerList.append(sector2_limit_layer) 
                layerList.append(sector2_text_layer)

            if "Sector 3" in selected_sector:
                layerList.append(sector3_limit_layer) 
                layerList.append(sector3_text_layer)                

            if "Sector 4" in selected_sector:
                layerList.append(sector4_limit_layer) 
                layerList.append(sector4_text_layer)   

            if "Sector 5" in selected_sector:
                layerList.append(sector5_limit_layer) 
                layerList.append(sector5_text_layer)   

            if "Sector 6" in selected_sector:
                layerList.append(sector6_limit_layer) 
                layerList.append(sector6_text_layer)

            if "Sector 7" in selected_sector:
                layerList.append(sector7_limit_layer)  

            if "Sector 8" in selected_sector:    
                layerList.append(sector8_limit_layer)  

            if "Sector 9" in selected_sector:    
                layerList.append(sector9_limit_layer)  

            if "Sector 1" in selected_sector:
                layerList.append(sector1_limit_layer) 
                layerList.append(sector1_text_layer)                


            if sts_details != None and len(sts_details) > 0:
                sts_det_df = pd.DataFrame.from_dict(sts_details)

                circle_layer = pdk.Layer(
                    "ScatterplotLayer",
                    sts_det_df,
                    get_position=['longitude', 'latitude'],
                    get_radius='radius',
                    get_fill_color='color',
                    pickable=True,
                    opacity=0.5,
                    stroked=False
                )

                layerList.append(circle_layer)


            layerList.append(triangle_layer)
            layerList.append(restricted_limit_layer)
            

            # --- show entire region ---
            # sectorX_limit_layer = pdk.Layer(
            #     "PolygonLayer",
            #     [{'name': 'X', 'polygon': [[[100.57166666666667, 3.161666666666667], [103.595, 3.161666666666667], [103.595, 1.05908722], [100.57166666666667, 1.05908722], [100.57166666666667, 3.161666666666667]]]}],
            #     get_polygon="polygon",
            #     get_fill_color=[255, 255, 255, 100],  
            #     get_line_color=[0, 0, 0],
            #     line_width_min_pixels=1,
            #     pickable=False       
            # ) 

            # layerList.append(sectorX_limit_layer)



            if fg_search and not search_result.empty:
                search_limit = [{
                    "name": "+",
                    "coordinates": [search_result['longitude'].values[0], search_result['latitude'].values[0]]
                }]

                # TextLayer for labels
                search_mmsi_layer = pdk.Layer(
                    "TextLayer",
                    search_limit,
                    pickable=False,
                    get_position='coordinates',
                    get_text="name",
                    get_size=32,
                    get_color=[255, 0, 0],
                    get_angle=0,
                    # Note that string constants in pydeck are explicitly passed as strings
                    # This distinguishes them from columns in a data set
                    get_text_anchor=String("middle"),
                    get_alignment_baseline=String("center"),
                )    

                layerList.append(search_mmsi_layer)


            deck = pdk.Deck(
                layers = layerList,
                initial_view_state = INITIAL_VIEW_STATE,
                tooltip=tooltip,
                map_style = 'mapbox://styles/mapbox/satellite-streets-v12',
                api_keys = {"mapbox" : MAPBOX_TOKEN},
                views=[pdk.View(type="MapView", controller=True)]
            )

            if st.session_state.reload == False:
                map_placeholder.pydeck_chart(deck)   

            else:  
                st.session_state.reload = False


            time.sleep(100)
    


