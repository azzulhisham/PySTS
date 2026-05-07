import json
import gc
import time
import logging
import requests

import duckdb
import pandas as pd

from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import HumanMessage, SystemMessage


GEMINI_APIKEY = 'AIzaSyBnFObRT-cQCCUeLlzsOuefVXeatvbVkaM'   # billing to az.zulhisham@gmail.com account - https://aistudio.google.com/u/0/api-keys?project=gen-lang-client-0447268578
MAPBOX_APIKEY = 'pk.eyJ1IjoiYXp6dWxoaXNoYW0iLCJhIjoiY2s5bjR1NDBqMDJqNDNubjdveXdiOGswYyJ9.SYlfXRzRtpbFoM2PHskvBg'

def llm_ask_port_location(longitude: float, latitude: float) -> str:
    llm = ChatGoogleGenerativeAI(
        model = 'gemini-3-flash-preview',               #'gemini-2.0-flash',
        temperature = 0,
        google_api_key = GEMINI_APIKEY    
    ) 
	
    geopoint = f'''
        longitude = {longitude}
        latitude = {latitude}
    
    '''

    prompt = geopoint + '''
        You are a professional pilot of a vessel.
        Based on the longitude and latitude given above, 
        find the closest known port that the geolocation coordinate is closest to the boundary of the known port.

        {
            "Country": give the country name of the port here,
            "Port Name": give the exact port name you know,
            "Location Name": give the location's name of the known port
        }   

        For unknown port just give a valid null value for port name in the json payload.
        Answer in a valid json payload with the entity stated above ONLY, not more than that.        
    '''

    message = HumanMessage(
        content=[
            {
                "type": "text",
                "text": prompt
            }
        ]
    )
 

    response = llm.invoke([message])    
    result = response.content
    
    try:
        if isinstance(result, list):
            content = result[0]['text'].replace('```json\n', '').replace('```', '')
            payload = json.loads(content)

            return payload

        else:
            return None

    except:
        return None  


def get_port_location_api(longitude: float, latitude: float):
    url = f"https://api.mapbox.com/geocoding/v5/mapbox.places/{longitude},{latitude}.json?access_token={MAPBOX_APIKEY}"

    try: 
        response = requests.get(url, timeout=60)
        resp_status = response.status_code
        resp_content = None

        if resp_status == 200:
            resp_content = response.json() 
            features = resp_content['features']
            feature = features[0]
            place_names = feature['place_name'].split(',')

            location_names = []

            for i in place_names:
                try:
                    tmp = int(i)
                    continue

                except:
                    location_names.append(i)


            payload = {
                "Country": location_names[len(location_names) - 1].strip(),
                "Port Name": location_names[0].strip()  if len(location_names) > 2 else '',
                "Location Name": (location_names[0].strip() + f', {location_names[1].strip()}')  if len(location_names) > 2 else location_names[0].strip()
            } 


        return resp_status, payload

    except:
        return 500, None



def main():
    # result = llm_ask_port_location(101.283535, 2.788971666666667)
    result = get_port_location_api(113.99700833333333, 4.42368)
    print(result)
    


if __name__ == '__main__':
    main()