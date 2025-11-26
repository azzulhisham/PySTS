///////////////////
//     mapbox
///////////////////
mapboxgl.accessToken = 'pk.eyJ1IjoiYXp6dWxoaXNoYW0iLCJhIjoiY2s5bjR1NDBqMDJqNDNubjdveXdiOGswYyJ9.SYlfXRzRtpbFoM2PHskvBg';
const map = new mapboxgl.Map({
    container: 'map', // container ID
    // Choose from Mapbox's core styles, or make your own style with Mapbox Studio
    style: 'mapbox://styles/mapbox/navigation-night-v1', // style URL
    center: [108.9227098, 4.1569843], // starting position [lng, lat]  
    zoom: 6 // starting zoom
});

// Add zoom and rotation controls to the map.
map.addControl(new mapboxgl.NavigationControl());

map.on('load', ()=> {
    map.setFog();

    map.addSource('TSS-Northbound', {
        'type': 'geojson',
        'data': {
            'type': 'Feature',
            'geometry': {
                'type': 'Polygon',
                // These coordinates outline Maine.
                'coordinates': [
                    [
                        [100.81183434, 3.04546854],
                        [100.78857422, 3.01186991],
                        [100.92864990, 2.89426660],
                        [100.99096298, 2.82448741],
                        [101.16983414, 2.73207069],
                        [101.22819901, 2.69983461],
                        [101.43934250, 2.58854501],
                        [101.44878387, 2.58357190],
                        [101.61975861, 2.45152024],
                        [101.68893814, 2.39800985],
                        [101.83811188, 2.28069134],
                        [101.87999725, 2.25015950],
                        [101.99157715, 2.16267711],
                        [102.07672119, 2.08977126],
                        [102.25542068, 1.92902351],
                        [102.41214752, 1.85404837],
                        [102.73057938, 1.70168649],
                        [102.80336380, 1.66428046],
                        [102.99957275, 1.53077984],
                        [103.17449570, 1.40859717],
                        [103.20505142, 1.38783233],
                        [103.39817047, 1.22960188],
                        [103.47301483, 1.20351523],
                        [103.49387169, 1.23831167],
                        [103.42082977, 1.25427241],
                        [103.33379745, 1.34085335],
                        [103.28358650, 1.39109294],
                        [103.28144073, 1.39606965],
                        [103.24710846, 1.42936183],
                        [103.17449570, 1.49182627],
                        [103.09656143, 1.56063806],
                        [103.00008774, 1.64386126],
                        [102.74688721, 1.75624996],
                        [102.62483597, 1.81115498],
                        [102.49214172, 1.87069073],
                        [102.27653503, 1.96711031],
                        [102.10521698, 2.12185026],
                        [102.10298538, 2.11996328],
                        [102.01698303, 2.19372537],
                        [101.99209213, 2.21671096],
                        [101.94917679, 2.25015950],
                        [101.94660187, 2.25067409],
                        [101.79759979, 2.36748054],
                        [101.71588898, 2.43162560],
                        [101.71382904, 2.42973902],
                        [101.64567947, 2.48290515],
                        [101.64430618, 2.48513466],
                        [101.52122498, 2.58065662],
                        [101.52122498, 2.58271446],
                        [101.46646500, 2.62558549],
                        [101.42389297, 2.64856375],
                        [101.40689850, 2.65559433],
                        [101.24879837, 2.73961518],
                        [101.24811172, 2.73858639],
                        [101.18974686, 2.77236458],
                        [100.99868774, 2.87540778],
                        [100.99473953, 2.87832234],
                        [100.81183434, 3.04546854]
                    ]
                ]
            }
        }
    });
         
    // Add a new layer to visualize the polygon.
    map.addLayer({
        'id': 'TSS-Northbound',
        'type': 'fill',
        'source': 'TSS-Northbound', // reference the data source
        'layout': {},
        'paint': {
            'fill-color': '#D18DFF', // blue color fill
            'fill-opacity': 0.3
        }
    });
        // Add a black outline around the polygon.
    map.addLayer({
        'id': 'outline-TSS-Northbound',
        'type': 'line',
        'source': 'TSS-Northbound',
        'layout': {},
        'paint': {
            'line-color': '#5D5D5D',
            'line-width': 1
        }
    });

    map.addSource('TSS-Southbound', {
        'type': 'geojson',
        'data': {
            'type': 'Feature',
            'geometry': {
                'type': 'Polygon',
                // These coordinates outline Maine.
                'coordinates': [
                    [
                        [100.78385353, 3.00488435],
                        [100.90719223, 2.90236723],
                        [100.91860771, 2.89276648],
                        [100.92882156, 2.88247987],
                        [100.98993301, 2.81741496],
                        [101.11541748, 2.74994592],
                        [101.16545677, 2.72319728],
                        [101.22510910, 2.69370451],
                        [101.29875183, 2.65503703],
                        [101.30235672, 2.65220765],
                        [101.37248039, 2.61508222],
                        [101.42766953, 2.58738747],
                        [101.53521538, 2.50232741],
                        [101.61194801, 2.44110132],
                        [101.67975426, 2.38638993],
                        [101.85819626, 2.24844421],
                        [101.98282242, 2.15049779],
                        [102.06418991, 2.07604743],
                        [102.24666595, 1.91358267],
                        [102.54690170, 1.77169216],
                        [102.79975891, 1.65141124],
                        [102.99991608, 1.52031221],
                        [103.20505142, 1.38680267],
                        [103.38752747, 1.22119240],
                        [103.45636368, 1.17502558],
                        [103.44451904, 1.15408722],
                        [103.37757111, 1.19390430],
                        [103.23337555, 1.32244764],
                        [103.18239212, 1.36775376],
                        [102.98103333, 1.49011024],
                        [102.77812958, 1.61417585],
                        [102.53213882, 1.72999792],
                        [102.29764938, 1.84015102],
                        [102.22297668, 1.87532311],
                        [102.03826904, 2.04748431],
                        [101.95724487, 2.11936288],
                        [101.65477753, 2.35744689],
                        [101.38732910, 2.56702330],
                        [101.14528656, 2.68740291],
                        [100.89706421, 2.81771500],
                        [100.71613312, 2.91269651],
                        [100.78385353, 3.00488435]
                    ]
                ]
            }
        }
    });
         
    // Add a new layer to visualize the polygon.
    map.addLayer({
        'id': 'TSS-Southbound',
        'type': 'fill',
        'source': 'TSS-Southbound', // reference the data source
        'layout': {},
        'paint': {
            'fill-color': '#6F31FF', // blue color fill
            'fill-opacity': 0.3
        }
    });
        // Add a black outline around the polygon.
    map.addLayer({
        'id': 'outline-TSS-Southbound',
        'type': 'line',
        'source': 'TSS-Southbound',
        'layout': {},
        'paint': {
            'line-color': '#5D5D5D',
            'line-width': 1
        }
    });    
});


let inp_mmsi = document.getElementById('inp_mmsi').value
let inp_startDateTime = document.getElementById('inp_startDateTime').value

if(inp_mmsi == 'None') inp_mmsi = ""
if(inp_startDateTime == 'None') inp_startDateTime = ""

inp_startDateTime = inp_startDateTime.replace('%20', ' ')

prev_ts = 0
prev_mmsi = 0
last_lng = 0
last_lat = 0
first_load = 1

fetchData()
map.setStyle("mapbox://styles/mapbox/satellite-streets-v12")

// Set the interval to 5000 milliseconds (5 seconds)
var interval = setInterval(fetchData, 5000);


function fetchData() {
    // The endpoint URL
    const apiHost = "http://aec45b1e527ea46d394dacdbe444e19c-806738025.ap-southeast-1.elb.amazonaws.com:3838";
    let url = apiHost + "/getLastPosition";

    if(inp_startDateTime != ""){
        url = apiHost + "/getHistoricalData/" + inp_startDateTime;

        if(inp_mmsi != ""){
            url = apiHost + "/getHistoricalData/" + inp_mmsi + "/" + inp_startDateTime;
        }

        inp_startDateTime = ""
    }
    else {
        if(inp_mmsi != ""){
            url += '/' + inp_mmsi
        }
    }

    // The fetch options
    const options = {
        method: "GET",
        headers: {
            "Content-Type": "application/json"
        }
    };

    // The fetch call
    fetch(url, options)
    .then(response => {
        // Check if the response is ok
        if (response.ok) {
        // Parse the response as JSON
        return response.json();
        } else {
        // Throw an error
        throw new Error(`HTTP error: ${response.status}`);
        }
    })
    .then(data => {
        for (i=0; i<data.rows.length; i++){
            ts = data.rows[i][0];
            mmsi = data.rows[i][2];
            cog = data.rows[i][4];
            lat = data.rows[i][7];
            lng = data.rows[i][8];
    
            if(ts != prev_ts || mmsi != prev_mmsi){
                const el = document.createElement('div');        
                el.id = 'marker-green';
        
                const marker1 = new mapboxgl.Marker({element:el, rotation: cog})
                .setLngLat([lng, lat])
                .addTo(map);  
                
                prev_ts = ts
                prev_mmsi = mmsi
                last_lat = lat
                last_lng = lng
               
            }
            else {
                //console.log("repeat data")
            }
        }

        if (first_load == 1){
            map.flyTo({
                center: [last_lng, last_lat],
                zoom: 16,
                speed: 3
            })
            
            first_load = 0
        }
        else
        {
            map.flyTo({
                center: [last_lng, last_lat],
                speed: 3
            })            
        }
    })
    .catch(error => {
        // Handle the error
        console.error(error);
    });

}