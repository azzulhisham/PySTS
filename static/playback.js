
///////////////////
//     mapbox
///////////////////
mapboxgl.accessToken = 'pk.eyJ1IjoiYXp6dWxoaXNoYW0iLCJhIjoiY2s5bjR1NDBqMDJqNDNubjdveXdiOGswYyJ9.SYlfXRzRtpbFoM2PHskvBg';
const map = new mapboxgl.Map({
    container: 'map', // container ID
    // Choose from Mapbox's core styles, or make your own style with Mapbox Studio
    style: 'mapbox://styles/mapbox/navigation-night-v1', // style URL
    center: [104.731318, 1.859047], // starting position [lng, lat]  
    zoom: 9 // starting zoom
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
        'id': 'outline',
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

    map.addSource('restrictedlimit', {
        'type': 'geojson',
        'data': {
            'type': 'Feature',
            'geometry': {
                'type': 'Polygon',
                // These coordinates outline Maine.
                'coordinates': [
                    [
                        [104.565, 2.283333333333333],
                        [104.95033277777777, 2.2838644444444443],  
                        [104.95033277777777, 1.690276111111111],
                        [104.56306444444444, 1.6895897222222223], 
                        [104.565, 2.283333333333333] 
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
        'id': 'restrictedlimit',
        'type': 'fill',
        'source': 'restrictedlimit',
        'layout': {},
        'paint': {
            'fill-color': '#6F3100',
            'fill-opacity': 0.3
        }
    }); 
    
    map.addLayer({
        'id': 'outline1',
        'type': 'line',
        'source': 'TSS-Southbound',
        'layout': {},
        'paint': {
            'line-color': '#5D5D5D',
            'line-width': 1
        }
    });      
});


// Listen for zoom or move events
map.on('zoomend', () => {
    const zoom = map.getZoom();
    const center = map.getCenter();
    const bounds = map.getBounds();
  
    console.log('Zoom level:', zoom);
    console.log('Center:', center);
    console.log('Bounds:', bounds);
  });
  
  map.on('moveend', () => {
    const zoom = map.getZoom();
    const center = map.getCenter();
    const bounds = map.getBounds();
  
    console.log('Zoom level:', zoom);
    console.log('Center:', center);
    console.log('Bounds:', bounds);
  });

map.on('mousemove', (e) => {
  // e.lngLat contains the longitude and latitude of the mouse position
  const lng = e.lngLat.lng;
  const lat = e.lngLat.lat;

  console.log('Longitude:', lng, 'Latitude:', lat);


});

const navbar = document.querySelector('navbar')
const menuBtn = document.getElementById("menubtn")
const playbackPopup = document.getElementById('playback-popup')

const dashboardBtn = document.getElementById('dashboard-btn')
const vesselMonBtn = document.getElementById('ves-mon-btn')
const playbackGpsBtn = document.getElementById('playbackGps-btn')
const closePlaybackPopup = document.getElementById("closePlaybackPopup");

const playbackSlider = document.getElementById('playback-slider')
const sliderValue = document.getElementById('slider-value')
const sliderMmsi = document.getElementById('slider-mmsi')
const sliderInput = document.getElementById('slider-input')
const closePlaybackSlider = document.getElementById("closePlaybackSlider")

const slider_time1 = document.getElementById('slider-time1')
const slider_time2 = document.getElementById('slider-time2')
const slider_time3 = document.getElementById('slider-time3')

const vesselInfo = document.querySelector('.vessel-info')
const closeVesselInfo = document.getElementById("closeVesselInfo")


let sliderVal = [10000]
let mapMarkers = [];
let vesslePlaybacks = []

/////////////////////////////////////////////////////////
// JavaScript example using WebSocket object
// Create a WebSocket object with the URL of the server
/////////////////////////////////////////////////////////
let ws = new WebSocket("ws://127.0.0.1:38381");

// Define a heartbeat interval in milliseconds
const HEARTBEAT_INTERVAL = 30000;

// Define a variable to store the heartbeat timeout ID
let heartbeatTimeout;

// Add an event listener for when the connection is opened
ws.addEventListener("open", function(event) {
    // Send a message to the server
    console.log(">>> websocket connected ! >>>");
    // ws.send("Hello Server!");    

    // Start the heartbeat timeout
    startHeartbeat();
});


ct = 0;
sliderCnt = 0;

let sliderObj;
let elemObj;

function addVesselRadicalPopupMenu(mmsi){

    const p_nav = document.createElement('nav')
    const p_div = document.createElement('div')
    const p_divBtn = document.createElement('div')
    const p_divI = document.createElement('i')

    p_divI.className = "fa-solid fa-plus"
    p_divBtn.className = "toggle-btn"
    p_div.className = 'nav-content'

    const p_spanI1 = document.createElement('i')
    p_spanI1.className = "fa-solid fa-earth-americas"
    const p_span1 = document.createElement('span')
    p_span1.className = "menu-item"
    p_span1.setAttribute("style", "--i:1;")
    p_span1.appendChild(p_spanI1)
    p_span1.id = "sp1_" + mmsi

    const p_spanI2 = document.createElement('i')
    p_spanI2.className = "fa-solid fa-database"
    const p_span2 = document.createElement('span')
    p_span2.className = "menu-item"
    p_span2.setAttribute("style", "--i:2;")
    p_span2.appendChild(p_spanI2)
    p_span2.id = "sp2_" + mmsi

    const p_spanI3 = document.createElement('i')
    p_spanI3.className = "fa-solid fa-calendar-day"
    const p_span3 = document.createElement('span')
    p_span3.className = "menu-item"
    p_span3.setAttribute("style", "--i:3;")
    p_span3.appendChild(p_spanI3)
    p_span3.id = "sp3_" + mmsi

    const p_spanI4 = document.createElement('i')
    p_spanI4.className = "fa-solid fa-magnifying-glass"
    const p_span4 = document.createElement('span')
    p_span4.className = "menu-item"
    p_span4.setAttribute("style", "--i:4;")
    p_span4.appendChild(p_spanI4)
    p_span4.id = "sp4_" + mmsi

    const p_spanI5 = document.createElement('i')
    p_spanI5.className = "fa-regular fa-images"
    const p_span5 = document.createElement('span')
    p_span5.className = "menu-item"
    p_span5.setAttribute("style", "--i:5;")
    p_span5.appendChild(p_spanI5)
    p_span5.id = "sp5_" + mmsi

    const p_spanI6 = document.createElement('i')
    p_spanI6.className = "fa-solid fa-timeline"
    const p_span6 = document.createElement('span')
    p_span6.className = "menu-item"
    p_span6.setAttribute("style", "--i:6;")
    p_span6.appendChild(p_spanI6)
    p_span6.id = "sp6_" + mmsi

    const p_spanI7 = document.createElement('i')
    p_spanI7.className = "fa-solid fa-expand"
    const p_span7 = document.createElement('span')
    p_span7.className = "menu-item"
    p_span7.setAttribute("style", "--i:7;")
    p_span7.appendChild(p_spanI7)
    p_span7.id = "sp7_" + mmsi

    const p_spanI8 = document.createElement('i')
    p_spanI8.className = "fa-solid fa-play"
    const p_span8 = document.createElement('span')
    p_span8.className = "menu-item"
    p_span8.setAttribute("style", "--i:8;")
    p_span8.appendChild(p_spanI8)
    p_span8.id = "sp8_" + mmsi

    const p_spanI9 = document.createElement('i')
    p_spanI9.className = "fa-solid fa-clock-rotate-left"
    const p_span9 = document.createElement('span')
    p_span9.className = "menu-item"
    p_span9.setAttribute("style", "--i:9;")
    p_span9.appendChild(p_spanI9)
    p_span9.id = "sp9_" + mmsi

    const p_spanI10 = document.createElement('i')
    p_spanI10.className = "fa-solid fa-gears"
    const p_span10 = document.createElement('span')
    p_span10.className = "menu-item"
    p_span10.setAttribute("style", "--i:10;")
    p_span10.appendChild(p_spanI10)
    p_span10.id = "sp10_" + mmsi

    const p_spanI11 = document.createElement('i')
    p_spanI11.className = "fa-solid fa-map-location-dot"
    const p_span11 = document.createElement('span')
    p_span11.className = "menu-item"
    p_span11.setAttribute("style", "--i:11;")
    p_span11.appendChild(p_spanI11)
    p_span11.id = "sp11_" + mmsi

    const p_spanI12 = document.createElement('i')
    p_spanI12.className = "fa-regular fa-floppy-disk"
    const p_span12 = document.createElement('span')
    p_span12.className = "menu-item"
    p_span12.setAttribute("style", "--i:12;")
    p_span12.appendChild(p_spanI12)
    p_span12.id = "sp12_" + mmsi

    p_divBtn.appendChild(p_divI)
    p_div.appendChild(p_divBtn)

    p_div.appendChild(p_span1)
    p_div.appendChild(p_span2)
    p_div.appendChild(p_span3)
    p_div.appendChild(p_span4)
    p_div.appendChild(p_span5)
    p_div.appendChild(p_span6)
    p_div.appendChild(p_span7)
    p_div.appendChild(p_span8)
    p_div.appendChild(p_span9)
    p_div.appendChild(p_span10)
    p_div.appendChild(p_span11)
    p_div.appendChild(p_span12)

    p_nav.appendChild(p_div)

    return p_nav
}


// Add an event listener for when a message is received from the server
ws.addEventListener("message", function(event) {
    // Log the message from the server
    console.log("Message from server: " + event.data);

    if(event.data === 'playback-data-done'){
        sliderInput.max = sliderCnt-1
        sliderInput.value = 0

        playbackSlider.classList.remove("playback-slider-active")
        sliderCnt = 0

        const el = document.createElement('div');
        el.id = 'marker-red'
        el.setAttribute("style", "--mmsi:" + sliderVal[0].mmsi + ";")
        el.style.zIndex = 100001

        const markerSlider = new mapboxgl.Marker({element:el, rotation: sliderVal[0].cog})
        .setLngLat([sliderVal[0].lng, sliderVal[0].lat])
        .addTo(map);

        sliderObj = markerSlider
        elemObj = el

        let midSliderTimerVal = Math.ceil(sliderInput.max / 2)

        sliderMmsi.innerText = parseInt(sliderVal[0].mmsi)
        sliderValue.innerText = sliderVal[0].ts
        slider_time1.innerText = sliderVal[0].ts
        slider_time2.innerText = sliderVal[midSliderTimerVal].ts
        slider_time3.innerText = sliderVal[sliderInput.max].ts

        sliderData = {
            mmsi: parseInt(sliderVal[0].mmsi),
            currentSliderVal: sliderVal[0].ts,
            slider_time1: sliderVal[0].ts,
            slider_time2: sliderVal[midSliderTimerVal].ts,
            slider_time3: sliderVal[sliderInput.max].ts,
            slider_max: sliderInput.max,
            slider_value: 0,
            sliderObj: markerSlider,
            data: [...sliderVal]
        }

        vesslePlaybacks.push(sliderData)

        // testing radial popup menu
        const vessel = document.getElementById('marker-red');

        el.addEventListener('click', (data) => {
            let styles = data.target.outerHTML.match(/style="([^"]*)"/)[1]
            let m_mmsi = styles.match(/--mmsi:([^;]*)/)[1];
            m_mmsi = parseInt(m_mmsi)

            vesslePlaybacks.forEach((i) => {
                if (i.mmsi === m_mmsi) {
                    sliderInput.max = i.slider_max
                    sliderInput.value = i.slider_value

                    sliderMmsi.innerText = i.mmsi
                    sliderValue.innerText = i.slider_time1
                    slider_time1.innerText = i.slider_time1
                    slider_time2.innerText = i.slider_time2
                    slider_time3.innerText = i.slider_time3
                    sliderObj = i.sliderObj
                    sliderVal = [...i.data]
                } 
            })

            //alert(this.style.getPropertyValue('--mmsi'))
            playbackSlider.classList.remove("playback-slider-active")
        })
    }
    else if (event.data === 'all-vessel-data-done'){
        const vesselList = document.querySelectorAll('.vessels-on-sea')

        function vesselListClick(e) {
        
            if (e.which === 1 && e.srcElement.classList.contains('fa-plus')) {
                const selectedVessel = this.parentElement
                ws.send('vessel-info:' + parseInt(selectedVessel.style.getPropertyValue('--mmsi')))

                const vesselInfo = document.querySelector('.vessel-info')
                vesselInfo.classList.add('open')
            }
            else if (e.which === 3 ){
                // Radical popup menu
                //const nav = this.querySelector("nav"),
                const toggleBtn = this.querySelector(".toggle-btn")

                this.classList.toggle("open")
                toggleBtn.classList.toggle("open")
            }
        }

        function vesselMenuListClick() {
            elemId = this.id
            elemid_text = elemId.split('_')
            ws.send('playback:' + parseInt(elemid_text[1]))
        }

        vesselList.forEach((item) => {
            const nav = item.querySelector("nav")
            nav.addEventListener('mouseup', vesselListClick)

            const menuList = nav.querySelectorAll('.menu-item')

            menuList.forEach((itm) => {
                itm.addEventListener('click', vesselMenuListClick)
            })            
        })            
    }      
    else {
        let obj = JSON.parse(event.data);

        if (obj['payload'] === 'playback') {
            lat = obj['m_lat']
            lng = obj['m_long']
            cog = obj['m_cog']
            cmap = obj['cmap']
        
            // slideLat[sliderCnt] = lat
            // slideLng[sliderCnt] = lng
            // slideCog[sliderCnt] = cog
            sliderVal[sliderCnt] = {
                lat: lat,
                lng: lng,
                cog: cog,
                ts: obj['m_ts'],
                mmsi: obj['m_mmsi']
            }
    
            ct += 1;
            sliderCnt += 1
            
            if(ct>=20){
                // create DOM element for the marker
                const el = document.createElement('div');
                
                el.id = 'marker-green';
                //el.id = 'marker-round'
        
                // const marker1 = new mapboxgl.Marker({element:el, rotation: cog})
                // .setLngLat([lng, lat])
                // .addTo(map);
        
                ct=0;
            }            
        }
        else if (obj['payload'] === 'vessel-info'){
            const detShiptype = document.getElementById('det-shiptype')
            const detShipname = document.getElementById('det-shipname')
            const detMMSI = document.getElementById('det-mmsi')
            const detIMO = document.getElementById('det-imo')
            const detCallsign = document.getElementById('det-callsign')
            const detMMSI_title = document.getElementById('det-mmsi-title')

            detShiptype.innerText = ": " + obj['m_shiptype']
            detShipname.innerText = ": " + obj['m_shipname']
            detMMSI.innerText = ": " + obj['m_mmsi']
            detIMO.innerText = ": " + obj['m_imo']
            detCallsign.innerText = ": " + obj['m_callsign']
            detMMSI_title.innerText = ": " + obj['m_mmsi']
        }
        else if (obj['payload'] === 'playback-data-geojson'){
            geoSourceData = obj['geoSourceData']

            map.addSource(obj['mmsi'], geoSourceData)
            map.addLayer({
                'id': obj['mmsi'],
                'type': 'line',
                'source': obj['mmsi'],
                'layout': {
                    'line-join': 'round',
                    'line-cap': 'round'
                },
                'paint': {
                    'line-color': '#00FF00',
                    'line-width': 3.5,
                    'line-dasharray': [2, 4]
                }
            });
        }        
        else if (obj['payload'] === 'all-vessel'){
            lat = obj['m_lat']
            lng = obj['m_long']
            cog = obj['m_cog']
            mmsi = obj['m_mmsi']
    
            if (mmsi.startsWith('533')){
                // create DOM element for the marker
                const el = document.createElement('div');
        
                el.id = 'marker-blue';
                //el.id = 'marker-round'
        
                const marker1 = new mapboxgl.Marker({element:el, rotation: cog})
                .setLngLat([lng, lat])
                .addTo(map);

                const el_vessel = document.createElement('div')
                el_vessel.className = 'vessels-on-sea'
                el_vessel.setAttribute('style', '--mmsi:' + mmsi);

                const nav = addVesselRadicalPopupMenu(mmsi)
                nav.style.transform = "rotate(-" + cog + "deg)"
                el_vessel.appendChild(nav)
                el.appendChild(el_vessel)
                el.style.zIndex = 100000

                let mapObj = {mmsi: mmsi, marker: marker1}
                mapMarkers.push(mapObj)
            }
        }
    }

    // Reset the heartbeat timeout
    resetHeartbeat();
});

// Add an event listener for when an error occurs
ws.addEventListener("error", function(event) {
    // Log the error
    console.log("Error: " + event.message);
});

// Add an event listener for when the connection is closed
ws.addEventListener("close", function(event) {
    // Log the close code and reason
    console.log("Connection closed: " + event.code + " " + event.reason);
    // Clear the heartbeat timeout
    clearHeartbeat();
});


ws.addEventListener("open", function(event) {
    // ws.send('all-vessel:---')
    ws.send('playback:533133481')
    ws.send('playback:312272000')
    // ws.send('playback:533045400')
});


function startHeartbeat() {
    // Set a timeout to send a ping message after the heartbeat interval
    heartbeatTimeout = setTimeout(function() {
        // Send a ping message and log it
        ws.send("ping");
        console.log("Send a ping message!");
        // Start the heartbeat timeout again
        startHeartbeat();
    }, HEARTBEAT_INTERVAL);
}

// Define a function to reset the heartbeat timeout
function resetHeartbeat() {
    // Clear the existing heartbeat timeout
    clearHeartbeat();
    // Start the heartbeat timeout again
    startHeartbeat();
}

// Define a function to clear the heartbeat timeout
function clearHeartbeat() {
    // Clear the existing heartbeat timeout if any
    if (heartbeatTimeout) {
        clearTimeout(heartbeatTimeout);
        heartbeatTimeout = null;
    }
}



// Get the element by its id
const btn_seek = document.getElementById("seek");

let menuBtnFg = false
sliderInput.value = 0

menuBtn.addEventListener('click', () => {
    if(menuBtnFg === false) {
        navbar.classList.add("menuActive")
        menuBtn.classList.add("menuRotate")
        menuBtnFg = true
    }
    else {
        navbar.classList.remove("menuActive")
        menuBtn.classList.remove("menuRotate")
        menuBtnFg = false
    }
    
})

dashboardBtn.addEventListener('click', () => {
    //playbackPopup.classList.remove("playback-popup-active")
    window.location.href = "http://localhost:3838/";
})

vesselMonBtn.addEventListener('click', () => {
    window.location.href = "http://localhost:3838/realtime_monitoring";
})

closePlaybackPopup.addEventListener('click', () => {
    playbackPopup.classList.add("playback-popup-active")
})

btn_seek.addEventListener('click', () => {
    playbackPopup.classList.add("playback-popup-active")

    const input_mmsi = document.getElementById("mmsi");
    ws.send("playback:" + input_mmsi.value);
})

playbackGpsBtn.addEventListener('click', () => {
    ws.send("playbackGps:" + '2820574');
})

sliderInput.addEventListener('input', ()=> {
    const m_mmsi = sliderMmsi.innerText 

    let value = sliderInput.value
    sliderValue.innerText = sliderVal[value].ts

    //elemObj.style.transform = elemObj.style.transform + " rotate(" + slideCog[value] + "deg)"
    sliderObj.setRotation(sliderVal[value].cog);
    sliderObj.setLngLat([sliderVal[value].lng, sliderVal[value].lat])

    const nav = document.querySelector('nav')
    nav.style.transform = "rotate(-" + sliderVal[value].cog + "deg)"

    vesslePlaybacks.forEach((i) => {
        if (i.mmsi === parseInt(m_mmsi)) {
            i.slider_value = value
        }
    })
})

closePlaybackSlider.addEventListener('click', () => {
    playbackSlider.classList.add("playback-slider-active")
})

closeVesselInfo.addEventListener('click', () => {
    vesselInfo.classList.remove('open')
})




    