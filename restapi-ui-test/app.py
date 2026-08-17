#!/usr/bin/env python3
"""MANTIS vessel-track player — Plotly Dash UI test.

Hardcoded to the same request as the working curl. Does not modify restapi/.
"""

from __future__ import annotations

import json
import math
import threading
from datetime import datetime, timezone

import plotly.graph_objects as go
import plotly.io as pio
import requests
from dash import Dash, Input, Output, Patch, State, ctx, dcc, html, no_update

# ---------------------------------------------------------------------------
# Hardcoded request (same shape as the curl that already works)
# ---------------------------------------------------------------------------
API_BASE = "http://localhost:8080"
TRACK_PATH = "/mantis/vessel-track"
MMSI = 414752000
DATE_FROM = "2026-08-17T00:00:00Z"
INCLUDE_CLASS_B = False
BEARER_TOKEN = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJuYW1lIjoidXNlckBzdHMubXkiLCJpZCI6InVzZXJAc3RzLm15IiwidGVuYW50Ijoic3RzIiwicm9sZSI6InN0cy11c3IiLCJleHBpcmVkRGF0ZSI6IjIwMjctMDItMTMgMTQ6MjY6MzIifQ."
    "yRdUoE24k9XDlSgs2PPkanAheeR9PYjr8j8XaUaO4iA"
)

# Same Mapbox token used by PyATON-Summary / PyMANTIS.
MAPBOX_TOKEN = (
    "pk.eyJ1IjoiYXp6dWxoaXNoYW0iLCJhIjoiY2s5bjR1NDBqMDJqNDNubjdveXdiOGswYyJ9."
    "SYlfXRzRtpbFoM2PHskvBg"
)
pio.templates.default = "none"

# Plotly 6 draws layout.map with MapLibre, so Mapbox is usable as a tile source
# but not as an engine. Raster tilesets work; Mapbox vector styles do not,
# because their sprite / glyph URLs use the mapbox:// protocol MapLibre dropped.
MAPBOX_SATELLITE_STYLE = {
    "version": 8,
    "sources": {
        "mapbox-satellite": {
            "type": "raster",
            "tiles": [
                "https://api.mapbox.com/v4/mapbox.satellite/{z}/{x}/{y}@2x.jpg90"
                f"?access_token={MAPBOX_TOKEN}"
            ],
            "tileSize": 256,
            "maxzoom": 22,
            "attribution": "© Mapbox © Maxar",
        }
    },
    "layers": [{"id": "mapbox-satellite", "type": "raster", "source": "mapbox-satellite"}],
}

# Built-in MapLibre style names, plus our own Mapbox raster style.
CUSTOM_BASEMAPS = {"mapbox-satellite": MAPBOX_SATELLITE_STYLE}

BASEMAP_OPTIONS = [
    {"label": "Satellite (built-in)", "value": "satellite"},
    {"label": "Satellite (Mapbox token)", "value": "mapbox-satellite"},
    {"label": "Dark", "value": "carto-darkmatter"},
    {"label": "Streets", "value": "open-street-map"},
    {"label": "Light", "value": "carto-positron"},
]
DEFAULT_BASEMAP = "satellite"

ZOOM_OPTIONS = [
    {"label": "Fit whole track", "value": "fit"},
    {"label": "Region (z8)", "value": 8},
    {"label": "Wide (z10)", "value": 10},
    {"label": "Close (z12)", "value": 12},
    {"label": "Very close (z14)", "value": 14},
]
DEFAULT_ZOOM = "fit"

INTERVAL_MS = 200
# At 300-600 pts/s each tick jumps 60-120 points, so a short trail would be
# swallowed by a single step. This keeps a 1-2 second tail behind the marker.
TRAIL_POINTS = 500
PATH_MAX_POINTS = 400
HEADING_M = 600.0
DEFAULT_CENTER = {"lat": 1.26, "lon": 103.85}

# Viewport assumptions for "fit whole track" — the map div is 620px tall and
# roughly full-window wide. MapLibre serves 512px tiles.
MAP_TILE_PX = 512.0
MAP_WIDTH_PX = 1100.0
MAP_HEIGHT_PX = 620.0
FIT_MARGIN = 1.3

# AIS "not available" sentinels. Raw feeds carry these verbatim, and a vessel
# that never reported a fix returns 1026 points of lat 91 / lon 181.
LAT_UNAVAILABLE = 91.0
LON_UNAVAILABLE = 181.0
SOG_UNAVAILABLE = 102.3
COG_UNAVAILABLE = 360.0
HEADING_UNAVAILABLE = 511.0

SPEED_MIN = 300
SPEED_MAX = 600
SPEED_DEFAULT = 500
SPEED_OPTIONS = [
    {"label": f"{pts} pts/s", "value": pts}
    for pts in range(SPEED_MIN, SPEED_MAX + 1, 50)
]


def _clean_point(point: dict) -> dict | None:
    """Drop AIS sentinel values; return None when the fix is unusable."""
    lat = point.get("latitude")
    lon = point.get("longitude")
    if lat is None or lon is None:
        return None
    try:
        lat = float(lat)
        lon = float(lon)
    except (TypeError, ValueError):
        return None
    if not (-90.0 <= lat <= 90.0) or not (-180.0 <= lon <= 180.0):
        return None

    cleaned = dict(point)
    cleaned["latitude"] = lat
    cleaned["longitude"] = lon
    for key, sentinel in (
        ("sog", SOG_UNAVAILABLE),
        ("cog", COG_UNAVAILABLE),
        ("trueHeading", HEADING_UNAVAILABLE),
    ):
        value = cleaned.get(key)
        if value is not None and float(value) >= sentinel:
            cleaned[key] = None
    return cleaned


class TrackBuffer:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.status = "idle"
        self.error: str | None = None
        self.meta: dict = {}
        self.points: list[dict] = []
        self.chunks = 0
        self.dropped = 0
        self._thread: threading.Thread | None = None

    def snapshot(self) -> dict:
        with self.lock:
            return {
                "status": self.status,
                "error": self.error,
                "meta": dict(self.meta),
                "n": len(self.points),
                "chunks": self.chunks,
                "dropped": self.dropped,
            }

    def point(self, index: int) -> dict | None:
        with self.lock:
            if not self.points:
                return None
            index = max(0, min(index, len(self.points) - 1))
            return self.points[index]

    def slice_for_map(self, index: int) -> tuple[list[dict], list[dict], dict | None]:
        with self.lock:
            if not self.points:
                return [], [], None
            index = max(0, min(index, len(self.points) - 1))
            path = _downsample(self.points, PATH_MAX_POINTS)
            start = max(0, index - TRAIL_POINTS)
            trail = self.points[start : index + 1]
            return path, trail, self.points[index]

    def bounds(self) -> tuple[dict[str, float], float, float] | None:
        with self.lock:
            if not self.points:
                return None
            lats = [p["latitude"] for p in self.points]
            lons = [p["longitude"] for p in self.points]
        center = {"lat": (max(lats) + min(lats)) / 2, "lon": (max(lons) + min(lons)) / 2}
        return center, max(lats) - min(lats), max(lons) - min(lons)

    def start(self, force: bool = False) -> None:
        with self.lock:
            if self._thread is not None and self._thread.is_alive() and not force:
                return
            self.status = "loading"
            self.error = None
            self.meta = {}
            self.points = []
            self.chunks = 0
            self.dropped = 0
            self._thread = threading.Thread(target=self._fetch, daemon=True)
            self._thread.start()

    def _fetch(self) -> None:
        params = {
            "mmsi": MMSI,
            "from": DATE_FROM,
            "includeClassB": "true" if INCLUDE_CLASS_B else "false",
        }
        headers = {
            "accept": "application/x-ndjson",
            "Authorization": f"Bearer {BEARER_TOKEN}",
        }
        url = f"{API_BASE}{TRACK_PATH}"
        try:
            with requests.get(
                url, headers=headers, params=params, stream=True, timeout=(15, 300)
            ) as response:
                response.raise_for_status()
                for raw in response.iter_lines(decode_unicode=True):
                    if not raw:
                        continue
                    msg = json.loads(raw)
                    kind = msg.get("type")
                    with self.lock:
                        if kind == "meta":
                            self.meta = msg
                        elif kind == "chunk":
                            for point in msg.get("points") or []:
                                cleaned = _clean_point(point)
                                if cleaned is None:
                                    self.dropped += 1
                                else:
                                    self.points.append(cleaned)
                            self.chunks += 1
                        elif kind == "done":
                            self.status = "ready"
                        elif kind == "error":
                            self.status = "error"
                            self.error = str(msg.get("message") or "stream error")
            with self.lock:
                if self.status == "loading":
                    self.status = "ready"
                if not self.points and not self.error:
                    self.status = "error"
                    if self.dropped:
                        self.error = (
                            f"{self.dropped:,} points returned, none plottable — "
                            "every fix is the AIS 'not available' sentinel "
                            f"(lat {LAT_UNAVAILABLE:g}, lon {LON_UNAVAILABLE:g}). "
                            "This MMSI never reported a position."
                        )
                    else:
                        self.error = "Stream ended with no positions"
        except Exception as exc:
            with self.lock:
                self.status = "error"
                self.error = str(exc)


def _downsample(points: list[dict], limit: int) -> list[dict]:
    n = len(points)
    if n <= limit:
        return points
    step = max(1, n // limit)
    out = points[::step]
    if out[-1] is not points[-1]:
        out.append(points[-1])
    return out


def _heading_line(lat: float, lon: float, cog: float | None) -> tuple[list[float], list[float]]:
    if cog is None:
        return [lat], [lon]
    rad = math.radians(cog)
    dlat = HEADING_M * math.cos(rad) / 111_111.0
    denom = 111_111.0 * max(0.2, math.cos(math.radians(lat)))
    dlon = HEADING_M * math.sin(rad) / denom
    return [lat, lat + dlat], [lon, lon + dlon]


def _zoom_to_fit(lat_span: float, lon_span: float) -> float:
    """MapLibre zoom that shows the given span in the map viewport, plus margin."""
    def level(span_deg: float, viewport_px: float) -> float:
        span = max(span_deg * FIT_MARGIN, 0.004)
        return math.log2(360.0 * viewport_px / (MAP_TILE_PX * span))

    fit = min(level(lat_span, MAP_HEIGHT_PX), level(lon_span, MAP_WIDTH_PX))
    return max(2.0, min(15.0, fit))


def _fmt_ts(value: str | None) -> str:
    if not value:
        return "—"
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    except ValueError:
        return str(value)


def _map_layout(center: dict[str, float], zoom: float, basemap: str, uirevision: str) -> dict:
    return dict(
        map=dict(
            style=CUSTOM_BASEMAPS.get(basemap, basemap),
            center=dict(lat=float(center["lat"]), lon=float(center["lon"])),
            zoom=float(zoom),
        ),
        margin=dict(l=0, r=0, t=0, b=0),
        paper_bgcolor="#070d18",
        showlegend=False,
        autosize=True,
        height=620,
        uirevision=uirevision,
    )


def _resolve_view(
    lat: float, lon: float, zoom_mode: str | float, has_vessel: bool
) -> tuple[dict[str, float], float]:
    """"fit" frames the whole track; a fixed level centres on the current vessel."""
    fitted = TRACK.bounds()
    if zoom_mode == "fit":
        if fitted:
            return fitted[0], _zoom_to_fit(fitted[1], fitted[2])
        return DEFAULT_CENTER, 7.0
    zoom = float(zoom_mode)
    if has_vessel:
        return {"lat": lat, "lon": lon}, zoom
    return (fitted[0] if fitted else DEFAULT_CENTER), zoom


def view_revision(snap: dict, zoom_mode: str | float, basemap: str, fit_clicks: int) -> str:
    """`layout.uirevision` — Plotly keeps the user's pan / zoom while this is unchanged.

    It varies while the stream grows so the auto-fit keeps up with incoming data,
    then freezes once the track is loaded, handing the view over to the user.
    Changing zoom / basemap or hitting "Fit track" deliberately breaks it.
    """
    phase = f"loading-{snap['n']}" if snap["status"] == "loading" else "loaded"
    return f"{basemap}|{zoom_mode}|{phase}|{snap['n']}|{fit_clicks}"


def build_figure(index: int, basemap: str, zoom_mode: str | float, uirevision: str) -> go.Figure:
    path, trail, vessel = TRACK.slice_for_map(index)
    if vessel:
        lat = vessel["latitude"]
        lon = vessel["longitude"]
        cog = vessel.get("cog")
        hover = [_hover_row(vessel)]
    else:
        lat = DEFAULT_CENTER["lat"]
        lon = DEFAULT_CENTER["lon"]
        cog = None
        hover = [["", "—", None, None]]

    path_lat = [p["latitude"] for p in path] or [lat]
    path_lon = [p["longitude"] for p in path] or [lon]
    trail_lat = [p["latitude"] for p in trail] or [lat]
    trail_lon = [p["longitude"] for p in trail] or [lon]
    hlats, hlons = _heading_line(lat, lon, None if cog is None else float(cog))

    fig = go.Figure()
    fig.add_trace(go.Scattermap(
        lat=path_lat, lon=path_lon, mode="lines",
        line=dict(width=2, color="#94a3b8"), hoverinfo="skip", name="track",
    ))
    fig.add_trace(go.Scattermap(
        lat=trail_lat, lon=trail_lon, mode="lines",
        line=dict(width=4, color="#22d3ee"), hoverinfo="skip", name="trail",
    ))
    fig.add_trace(go.Scattermap(
        lat=hlats, lon=hlons, mode="lines",
        line=dict(width=3, color="#fbbf24"), hoverinfo="skip", name="heading",
    ))
    fig.add_trace(go.Scattermap(
        lat=[lat], lon=[lon], mode="markers",
        marker=dict(size=15, color="#22d3ee"),
        hovertemplate=(
            "MMSI %{customdata[0]}<br>%{customdata[1]}<br>"
            "lat %{lat:.5f}<br>lon %{lon:.5f}<br>"
            "sog %{customdata[2]} kn<br>cog %{customdata[3]}°<extra></extra>"
        ),
        customdata=hover, name="vessel",
    ))

    center, zoom = _resolve_view(lat, lon, zoom_mode, vessel is not None)
    fig.update_layout(**_map_layout(center, zoom, basemap, uirevision))
    return fig


def _tick_off(playing: bool, status: str) -> bool:
    """Stop the interval when nothing is moving and nothing is still arriving."""
    return (not playing) and status not in ("loading", "idle")


def _hover_row(vessel: dict) -> list:
    return [
        vessel.get("mmsi"),
        _fmt_ts(vessel.get("ts")),
        vessel.get("sog"),
        vessel.get("cog"),
    ]


def patch_playback(index: int):
    """Move trail + vessel only. The map view is never touched, so it stays put."""
    _path, trail, vessel = TRACK.slice_for_map(index)
    if not vessel:
        return no_update
    lat = vessel["latitude"]
    lon = vessel["longitude"]
    cog = vessel.get("cog")
    hlats, hlons = _heading_line(lat, lon, None if cog is None else float(cog))

    patched = Patch()
    patched["data"][1]["lat"] = [p["latitude"] for p in trail] or [lat]
    patched["data"][1]["lon"] = [p["longitude"] for p in trail] or [lon]
    patched["data"][2]["lat"] = hlats
    patched["data"][2]["lon"] = hlons
    patched["data"][3]["lat"] = [lat]
    patched["data"][3]["lon"] = [lon]
    patched["data"][3]["customdata"] = [_hover_row(vessel)]
    return patched


TRACK = TrackBuffer()
TRACK.start()

# update_title=None keeps the browser tab from flashing "Updating..." on every
# playback tick and while the stream is being consumed.
app = Dash(__name__, title="MANTIS track player", update_title=None)
app.layout = html.Div(
    className="app",
    children=[
        html.Div(
            className="header",
            children=[
                html.Div([
                    html.H1("MANTIS track player"),
                    html.Div(
                        className="sub",
                        children=(
                            f"GET {TRACK_PATH}?mmsi={MMSI}&from={DATE_FROM}"
                            f"&includeClassB={str(INCLUDE_CLASS_B).lower()}"
                        ),
                    ),
                ]),
                html.Div(id="status", className="status"),
            ],
        ),
        html.Div(
            className="map-wrap",
            children=dcc.Graph(
                id="map",
                figure=build_figure(0, DEFAULT_BASEMAP, DEFAULT_ZOOM, "init"),
                config={
                    "displayModeBar": True,
                    "scrollZoom": True,
                    "responsive": True,
                },
                style={"height": "620px", "width": "100%"},
            ),
        ),
        html.Div(
            className="player",
            children=[
                html.Div(
                    className="player-row",
                    children=[
                        html.Button("Load track", id="btn-load", n_clicks=0),
                        html.Button("Play", id="btn-play", n_clicks=0, className="primary"),
                        html.Button("Pause", id="btn-pause", n_clicks=0),
                        html.Button("Restart", id="btn-restart", n_clicks=0),
                        html.Label("Speed"),
                        dcc.Dropdown(
                            id="speed", options=SPEED_OPTIONS, value=SPEED_DEFAULT,
                            clearable=False, style={"width": "130px"},
                        ),
                        html.Label("Zoom"),
                        dcc.Dropdown(
                            id="zoom", options=ZOOM_OPTIONS, value=DEFAULT_ZOOM,
                            clearable=False, style={"width": "160px"},
                        ),
                        html.Label("Basemap"),
                        dcc.Dropdown(
                            id="basemap", options=BASEMAP_OPTIONS, value=DEFAULT_BASEMAP,
                            clearable=False, style={"width": "150px"},
                        ),
                        html.Button("Fit track", id="btn-fit", n_clicks=0),
                        html.Div(id="clock", className="clock"),
                    ],
                ),
                html.Div(
                    className="slider",
                    children=dcc.Slider(
                        id="scrubber", min=0, max=1, step=1, value=0, marks=None,
                        tooltip={"placement": "top", "always_visible": False},
                        updatemode="drag",
                    ),
                ),
            ],
        ),
        dcc.Interval(id="tick", interval=INTERVAL_MS, n_intervals=0, disabled=False),
        dcc.Store(
            id="playback",
            data={"playing": False, "index": 0, "n": 0, "status": "idle",
                  "rebuild": True, "src": None},
        ),
    ],
)


@app.callback(
    Output("playback", "data"),
    Output("tick", "disabled"),
    Input("tick", "n_intervals"),
    Input("btn-play", "n_clicks"),
    Input("btn-pause", "n_clicks"),
    Input("btn-restart", "n_clicks"),
    Input("btn-load", "n_clicks"),
    Input("scrubber", "drag_value"),
    State("playback", "data"),
    State("speed", "value"),
    prevent_initial_call=False,
)
def update_playback(n_tick, n_play, n_pause, n_restart, n_load, slider, playback, speed):
    playback = playback or {"playing": False, "index": 0, "n": 0, "status": "idle"}
    snap = TRACK.snapshot()
    index = int(playback.get("index") or 0)
    playing = bool(playback.get("playing"))
    rebuild = False
    triggered = ctx.triggered_id

    if triggered == "btn-load" and n_load:
        TRACK.start(force=True)
        return {"playing": False, "index": 0, "n": 0, "status": "loading",
                "rebuild": True, "src": "btn-load"}, False

    if triggered == "btn-play" and n_play:
        playing = snap["n"] > 0
        if playing and index >= snap["n"] - 1:
            index = 0
        rebuild = True
    elif triggered == "btn-pause" and n_pause:
        playing = False
        rebuild = True
    elif triggered == "btn-restart" and n_restart:
        index = 0
        playing = snap["n"] > 0
        rebuild = True
    elif triggered == "scrubber":
        if slider is None or int(slider) == index:
            # Moving the handle during playback makes the slider report the
            # position straight back to us. Treating that echo as a seek would
            # pause playback after a single step, so ignore no-op reports.
            return no_update, _tick_off(playing, snap["status"])
        # A real drag fires continuously, so only the marker / trail need
        # moving. A full rebuild is reserved for when the track itself grew.
        index = int(slider)
        playing = False
        rebuild = snap["n"] != playback.get("n")
    elif triggered == "tick" and playing and snap["n"] > 0:
        step = max(1, int(round((speed or SPEED_DEFAULT) * INTERVAL_MS / 1000.0)))
        index = min(index + step, snap["n"] - 1)
        # Rebuild once when the stream finishes so the final auto-fit lands here
        # rather than surprising the user on their next pause or scrub.
        if snap["status"] != playback.get("status"):
            rebuild = True
        if index >= snap["n"] - 1 and snap["status"] == "ready":
            playing = False
            rebuild = True
    else:
        rebuild = snap["n"] != playback.get("n") or snap["status"] != playback.get("status")

    index = max(0, min(index, snap["n"] - 1)) if snap["n"] > 0 else 0
    new_state = {
        "playing": playing,
        "index": index,
        "n": snap["n"],
        "status": snap["status"],
        "rebuild": rebuild,
        "src": triggered,
    }
    tick_off = _tick_off(playing, snap["status"])
    if triggered == "tick" and new_state == playback:
        return no_update, tick_off
    return new_state, tick_off


@app.callback(
    Output("scrubber", "value"),
    Output("scrubber", "max"),
    Input("playback", "data"),
)
def sync_slider(playback):
    """Keep the handle under the marker, including while playing."""
    snap = TRACK.snapshot()
    slider_max = max(snap["n"] - 1, 1)
    playback = playback or {"playing": False, "index": 0}
    # Never write the handle back while the user is dragging it, or a late
    # response would fight the drag and make the handle jump.
    if playback.get("src") == "scrubber":
        return no_update, slider_max
    return int(playback.get("index") or 0), slider_max


@app.callback(
    Output("map", "figure"),
    Output("status", "children"),
    Output("clock", "children"),
    Input("playback", "data"),
    Input("basemap", "value"),
    Input("zoom", "value"),
    Input("btn-fit", "n_clicks"),
)
def render(playback, basemap, zoom_mode, fit_clicks):
    playback = playback or {"playing": False, "index": 0}
    index = int(playback.get("index") or 0)
    basemap = basemap or DEFAULT_BASEMAP
    zoom_mode = zoom_mode if zoom_mode is not None else DEFAULT_ZOOM
    snap = TRACK.snapshot()
    vessel = TRACK.point(index) if snap["n"] else None

    if snap["status"] == "idle":
        status = "Idle"
    elif snap["status"] == "loading":
        status = f"Loading stream… {snap['chunks']} chunks, {snap['n']:,} points"
    elif snap["status"] == "error":
        status = f"Error: {snap['error']}"
    else:
        status = f"Ready — {snap['chunks']} chunks, {snap['n']:,} points"
        if snap["dropped"]:
            status += f" ({snap['dropped']:,} skipped, no valid fix)"

    if vessel:
        sog = vessel.get("sog")
        cog = vessel.get("cog")
        clock = (
            f"{_fmt_ts(vessel.get('ts'))}   "
            f"point {index + 1:,} / {snap['n']:,}   "
            f"sog {'—' if sog is None else f'{sog:.1f} kn'}   "
            f"cog {'—' if cog is None else f'{cog:.0f}°'}"
        )
    else:
        clock = "No positions yet"

    # Patch whenever only the marker moved — during playback and while scrubbing.
    view_changed = ctx.triggered_id in ("basemap", "zoom", "btn-fit")
    if snap["n"] and not playback.get("rebuild") and not view_changed:
        return patch_playback(index), status, clock

    uirevision = view_revision(snap, zoom_mode, basemap, int(fit_clicks or 0))
    return build_figure(index, basemap, zoom_mode, uirevision), status, clock


if __name__ == "__main__":
    print("MANTIS track player → http://127.0.0.1:8051")
    print(f"Streaming {API_BASE}{TRACK_PATH}?mmsi={MMSI}&from={DATE_FROM}")
    app.run(debug=False, host="127.0.0.1", port=8051)
