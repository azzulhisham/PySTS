# MANTIS track player (UI test)

Standalone Plotly Dash app that streams `GET /mantis/vessel-track` and plays
longitude / latitude on a Mapbox map like a media player.

It does **not** change `PySTS/restapi`. The API must already be running.

## Setup

```bash
cd PySTS/restapi-ui-test
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

Open http://127.0.0.1:8051

The MANTIS API should be up on `http://localhost:8080` first:

```bash
cd PySTS/restapi
source venv/bin/activate
gunicorn -c gunicorn_config.py main:app
```

## Hardcoded request

Same shape as the curl used to test the stream, with the Bearer token and Mapbox
token at the top of `app.py`:

| Constant | Value |
| --- | --- |
| `MMSI` | `414752000` |
| `DATE_FROM` | `2026-08-17T00:00:00Z` (no `to`, so the API caps it at 3 days) |
| `INCLUDE_CLASS_B` | `false` |

## Controls

Play / Pause / Restart, playback speed in points per second, a scrubber, a zoom
picker, a basemap picker and "Fit track".

The scrubber and the marker stay in sync both ways: the handle advances with the
vessel during playback, and dragging it moves the marker live rather than only
on release. Dragging pauses playback.

Those two directions have to be kept apart, or writing the handle position on
every tick comes straight back in as a seek and pauses playback after a single
step. Two guards handle it: `update_playback` ignores any scrubber report whose
value already equals the current index (that is our own echo, not a drag), and
`sync_slider` skips the write-back while `src == "scrubber"` so a late response
cannot fight your thumb mid-drag.

Speed runs from 300 to 600 points per second in 50-point steps, default 500.
With a 200 ms tick that is 60 to 120 points per frame, so a ~6,000 point
three-day track plays through in roughly 20 to 10 seconds.

The app is created with `update_title=None`, otherwise Dash would rewrite the
browser tab to "Updating..." on every callback — several times a second during
playback and throughout the stream.

**The map is static during playback** — only the vessel marker, its trail and
the heading line move. The view auto-fits while the stream is still arriving, so
the growing track stays framed, and then freezes once loading finishes. From
that point the view is yours: scroll-zoom, drag and the modebar all persist, and
playback will not yank the map back.

This works through `layout.uirevision` (see `view_revision()`): Plotly keeps
user-driven pan / zoom for as long as that string is unchanged, so after loading
we hold it constant. Playback and scrubbing updates are `Patch` operations
against the trace coordinates only and never touch `layout`, which is both why
the view holds still and why dragging stays smooth on a 6,000-point track.

Four things deliberately re-apply the view, by changing that revision:

- **Fit track** — reframe the whole path after you have zoomed around
- **Zoom** — `Fit whole track` frames the entire path; the fixed levels
  (z8 region, z10 wide, z12 close, z14 very close) centre on the vessel's
  current position
- **Basemap** — switching tiles
- **Load track** — a new stream auto-fits again

Playback starts as soon as the first chunk arrives — you do not have to wait for
the full stream. While playing, only the trail, heading and vessel marker are
patched onto the existing map instead of redrawing it, which keeps the UI
responsive.

## Basemaps and Mapbox

Plotly 6 (plotly.js 3.x) draws `layout.map` with **MapLibre GL JS** and no
longer bundles Mapbox GL JS — `scattermapbox` / `layout.mapbox` are deprecated
shims. So Mapbox is usable here as a *tile source*, not as a rendering engine.
Playback is unaffected either way: the animation is Dash `Patch` updates to the
trace coordinates, independent of the basemap.

| Option | Source | Token needed |
| --- | --- | --- |
| Satellite (built-in) | MapLibre built-in style | no |
| Satellite (Mapbox token) | `mapbox.satellite` raster tiles via `MAPBOX_TOKEN` | yes |
| Dark / Streets / Light | `carto-darkmatter`, `open-street-map`, `carto-positron` | no |

The Mapbox option is a MapLibre style-spec dict in `MAPBOX_SATELLITE_STYLE`
pointing at `api.mapbox.com/v4/...`, so tile requests bill to that Mapbox
account. Only **raster** tilesets work this way — Mapbox vector styles such as
`dark-v11` reference their sprites and glyphs over the `mapbox://` protocol that
MapLibre removed, so labels and icons would fail to load.

## If the map looks empty

The track is drawn from whatever the API returns, and raw AIS carries "not
available" sentinel values that no map can plot:

| Field | Sentinel | Meaning |
| --- | --- | --- |
| `latitude` | `91` | no position fix |
| `longitude` | `181` | no position fix |
| `sog` | `102.3` | speed unknown |
| `cog` | `360` | course unknown |
| `trueHeading` | `511` | heading unknown |

`app.py` drops points whose latitude / longitude are sentinels or out of range,
nulls out the unusable `sog` / `cog` / `trueHeading`, and reports the count in
the header. MMSI `268245201`, for example, streams 1,026 points that are all
lat `91` / lon `181`, so it has nothing to draw — the status line says so
instead of showing a blank map. Pick an MMSI that actually reported positions.
