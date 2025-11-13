import websockets
import asyncio
import json
import pandas as pd
import duckdb
import clickhouse_connect
from geojson import LineString

import time
from datetime import datetime, timedelta


def access_db(mmsi):
    df = duckdb.sql(f'''
        SELECT *
        FROM './data/{mmsi}.parquet'
        ORDER BY ts
    ''').fetchdf()

    return df


def access_db_static(mmsi):
    #engine = sa.create_engine('postgresql://postgres:postgres@localhost:5432/creative')
    # engine = sa.create_engine('crate://localhost:4200')

    # conn = engine.connect()
    # query = f"SELECT * FROM ais_static2023 WHERE mmsi=:mmsi order by ts desc limit 1"
    # query = sa.text(query)
    # params = {'mmsi': mmsi}
    # df = pd.read_sql(query, conn, params=params)
    # conn.close()

    # return df 
    pass


def access_db_allVessel():
    #engine = sa.create_engine('postgresql://postgres:postgres@localhost:5432/creative')
    # uri = 'clickhouse://default:@10.10.20.50:8123/default'
    # engine = sa.create_engine(uri)

    # conn = engine.connect()
    # query = """
    #     with r as (
    #         SELECT  ts, row_number() over (partition by mmsi order by ts desc) as rn,             
    #         mmsi, latitude, longitude, cog 
    #         FROM pnav.ais_position where ts>:startDate and ts<:endDate and mmsi>0
    #     )
    #     select * from r 
    #     where r.rn=1;
    # """
    # query = sa.text(query)
    # params = {'startDate': '2024-04-03', 'endDate': '2024-04-04'}
    # df = pd.read_sql(query, conn, params=params)
    # conn.close()

    # return df
    pass


# def access_db_gps(mmsi):
    # conn = client.connect("https://pinc-zultan.aks1.eastus2.azure.cratedb.net:4200", username="admin", password=")eswlb4oW(7xjKQ3g3bv^yW&", verify_ssl_cert=True)

    # with conn:
    #     cursor = conn.cursor()
    #     query = f"SELECT * FROM ais2023 WHERE mmsi={mmsi} and sog<>0 order by ts"
    #     cursor.execute(query)
    #     result = cursor.fetchall()
    #     return result


    # dburi = "crate://admin:)eswlb4oW(7xjKQ3g3bv^yW&@pinc-zultan.aks1.eastus2.azure.cratedb.net:4200?ssl=true"
    # engine = sa.create_engine(dburi, echo=True)

    # with engine.connect() as conn:
    #     query = f"SELECT * FROM ais2023 WHERE mmsi=:mmsi and sog<>0 order by ts"
    #     query = sa.text(query)
    #     params = {'mmsi': mmsi}
    #     df = pd.read_sql(sql=query, con=conn, params=params)

    # return df

def access_db_gps_clickhouse(mmsi):
    client = clickhouse_connect.get_client(host='localhost', port=8123)
    result = client.query(f'SELECT ts, msgtype, mmsi, sog, cog, rot, heading, lat, lng FROM ais2023_gps WHERE mmsi={mmsi} and sog<>0 order by ts')
    return result.result_rows    



HEARTBEAT_INTERVAL = 30

async def handler(websocket):
    # Start a task for sending ping messages periodically
    ping_task = asyncio.create_task(send_ping(websocket))

    try:
        # Receive messages from the client
        async for message in websocket:
            print(f"[MEESSAGE]:: {message}")
            msg = message.split(':')

            if msg[0] == "playback":
                df = access_db(mmsi=int(msg[1]))

                if df.shape[0] > 0:

                    points = []

                    #[ts, mmsi, messageType, sog, cog, rot, trueHeading, latitude, longitude]
                    for i in range(df.shape[0]):
                        color = 0  
                        
                        data = {
                            'm_ts': (df.iloc[i][0] + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S"),
                            'm_mmsi': f'''{str(df.iloc[i][1])}''',
                            'm_long': df.iloc[i][8],
                            'm_lat': df.iloc[i][7],
                            'm_sog': df.iloc[i][3],
                            'm_cog': df.iloc[i][4],
                            'cmap': color,
                            'payload': 'playback'
                        }
                        
                        playback_point = (df.iloc[i][8], df.iloc[i][7])
                        points.append(playback_point)

                        await websocket.send(json.dumps(data))
                        # time.sleep(0.005)


                    geoSourceData = {
                        'type': 'geojson',
                        'data': {
                            'type': 'Feature',
                            'properties': {},
                            'geometry': LineString(points)
                        }
                    } 

                    playback_data = {
                        'payload': 'playback-data-geojson',
                        'mmsi': f'{msg[1]}',
                        'geoSourceData': geoSourceData
                    }

                    await websocket.send('playback-data-done')
                    # time.sleep(1)
                    await websocket.send(json.dumps(playback_data))
            elif msg[0] == "playbackGps":  
                df = access_db_gps_clickhouse(mmsi=int(msg[1]))
                # df = access_db_gps(mmsi=int(msg[1]))

                if len(df) > 0:

                    points = []

                    #['ts', 'msgtype', 'mmsi', 'sog', 'cog', 'rot', 'heading', 'lat', 'lng']
                    for i in range(len(df)):
                        color = 0  
                        
                        data = {
                            'm_ts': f'''{datetime.fromtimestamp(df[i][0]/1000) - timedelta(hours=8)}''',
                            'm_mmsi': f'''{str(df[i][2])}''',
                            'm_long': df[i][8],
                            'm_lat': df[i][7],
                            'm_rot': df[i][5],
                            'm_cog': df[i][4],
                            'cmap': color,
                            'payload': 'playback'
                        }
                        
                        playback_point = (df[i][8], df[i][7])
                        points.append(playback_point)

                        await websocket.send(json.dumps(data))
                        # time.sleep(0.005)


                    geoSourceData = {
                        'type': 'geojson',
                        'data': {
                            'type': 'Feature',
                            'properties': {},
                            'geometry': LineString(points)
                        }
                    } 

                    playback_data = {
                        'payload': 'playback-data-geojson',
                        'mmsi': f'{msg[1]}',
                        'geoSourceData': geoSourceData
                    }

                    await websocket.send('playback-data-done')
                    # time.sleep(1)
                    await websocket.send(json.dumps(playback_data))                

            elif msg[0] == "vessel-info":
                df = access_db_static(mmsi=int(msg[1]))

                if df.shape[0] > 0:
                    for i in range(df.shape[0]):
                        data = {
                            'm_ts': f'''{datetime.fromtimestamp(df.iloc[i][0]/1000) - timedelta(hours=8)}''',
                            'm_mmsi': f'''{str(df.iloc[i][2])}''',
                            'm_shiptype': f'''{str(df.iloc[i][3])}''',
                            'm_shipname': df.iloc[i][4],
                            'm_imo': f'''{str(df.iloc[i][5])}''',
                            'm_callsign': df.iloc[i][6],
                            'm_toStarboard': f'''{str(df.iloc[i][8])}''',
                            'm_toPort': f'''{str(df.iloc[i][9])}''',
                            'm_toStern': f'''{str(df.iloc[i][10])}''',
                            'm_toBow': f'''{str(df.iloc[i][11])}''',
                            'payload': 'vessel-info'
                        }

                        await websocket.send(json.dumps(data))

            elif msg[0] == "all-vessel":
                df = access_db_allVessel()

                if df != None and df.shape[0] > 0:

                    for i in range(df.shape[0]): 
                        data = {
                            'm_ts': (df.iloc[i][0] + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S"),
                            'm_mmsi': f'''{str(df.iloc[i][2])}''',
                            'm_long': df.iloc[i][4],
                            'm_lat': df.iloc[i][3],
                            'm_cog': df.iloc[i][5],
                            'payload': 'all-vessel'
                        }
                        
                        await websocket.send(json.dumps(data))
                        time.sleep(0.005)

                    await websocket.send('all-vessel-data-done')

                
    finally:
        # Cancel the ping task when the connection is closed
        ping_task.cancel()


async def send_ping(websocket):
    # Send ping messages periodically
    while True:
        # Wait for the heartbeat interval
        await asyncio.sleep(HEARTBEAT_INTERVAL)
        # Send a ping message and wait for a pong response
        await websocket.ping()
        print("Send a ping message!")


# Create a WebSocket server using the handler function
# server = websockets.serve(handler, "0.0.0.0", 3838)

# Run the server using the asyncio event loop
# asyncio.get_event_loop().run_until_complete(server)
# asyncio.get_event_loop().run_forever()


async def main():
    async with websockets.serve(handler, "0.0.0.0", 38381):
        await asyncio.Future()  # run forever



asyncio.run(main())