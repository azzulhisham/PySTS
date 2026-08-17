# MANTIS (`PySTS`)

MANTIS lives in **this repository only**: `PySTS`.

This team’s scope is **backend data processing** and the **API**. The operations frontend is another developer’s repository — it is not here.

This is **not** TSS Reporting. Do not copy MANTIS polygon rules, detectors, or API changes into `PyTSS`, `PyTSS-Reporting`, or any other sibling project.

```
AIS positions (ClickHouse) + vessel static (Postgres)
                    │
                    ▼
    PySTS/backend  (MANTIS pipeline only)
      vesselproximitydetection.py
      vesselslowspeeddetection.py
      vesselstrajectorydetection.py
                    │
                    ▼
              Postgres `pnav`
                    │
                    ▼
              PySTS/restapi
              MANTIS API
                    │
                    ▼
         Frontend (separate repo,
         separate developer)
```

## What is MANTIS in this repo

| Path | Role |
| --- | --- |
| `backend/vesselproximitydetection.py` | Pipeline: ship-to-ship proximity clusters |
| `backend/vesselslowspeeddetection.py` | Pipeline: slow-move / AIS-silence activities |
| `backend/vesselstrajectorydetection.py` | Pipeline: movement / stop activities |
| `restapi/` | API: read those tables, apply product rules, JSON for the frontend |
| `mcp/` | Optional. Calls `restapi` only — does not re-implement detectors |

`restapi` does **not** re-run the AIS pipeline. If STS / dark / illegal-anchoring data is empty or stale, run or fix the three pipeline scripts above.

API contract (polygons, Excl holes, keep/drop/label): **[restapi/README.md](restapi/README.md)**.

## Critical pipeline → API

| Pipeline script | Writes | API consumer |
| --- | --- | --- |
| `vesselproximitydetection.py` | `ais_vesselproximityobservation`, `ais_vesselproximitymember` | `GET /mantis/sts-activities` |
| `vesselslowspeeddetection.py` | `ais_vesselslowmoveactivities` | `GET /mantis/darkvessels` |
| `vesselstrajectorydetection.py` | `ais_vesselmovementactivities` | `GET /mantis/illegal-anchoring` (also enriches STS members with sog/cog) |

**Detection factors and formulas (living maintenance spec):** [`backend/mantis-detection.md`](backend/mantis-detection.md) — thresholds, ship types, durations, and scoring for all three MANTIS pipeline jobs plus the API filters. Update that file whenever a knob changes.

## Not MANTIS (same `PySTS` folder, different purpose)

These files sit next to MANTIS code. Do **not** treat them as part of the MANTIS product.

| Path | What it is |
| --- | --- |
| `st_app/` | Streamlit app to **visualise and analyse test data**. Used for analysis while building MANTIS. Not the MANTIS product. |
| `backend/polygons.py` | Polygon rings for a **different** purpose. Not in MANTIS at this time. |
| `backend/vesselzone.py` | Zone occupancy job. Imports `backend/polygons.py`. **Not in MANTIS at this time.** |
| `backend/vesselzone_b.py` | Related zone job. Not in MANTIS at this time. |
| `backend/vesseltimeline_port.py` | Port-name helper. Not a MANTIS pipeline job. |
| `app.py`, `socket_server.py`, `templates/` | Legacy web / playback. Not the MANTIS API. |

`backend/polygons.py` must not be merged with `restapi/polygons.py`. The MANTIS API catalogue is **only** `restapi/polygons.py`.

## API (`restapi/`)

Flask + gunicorn. JWT Bearer. Swagger at `/swagger`.

Product endpoints (fed by the three pipeline jobs):

- `GET /mantis/polygons`
- `GET /mantis/sts-activities`
- `GET /mantis/illegal-anchoring`
- `GET /mantis/darkvessels`

Also in this API (playback helpers; zone events are **not** produced by the MANTIS-critical pipeline):

- `GET /mantis/vessel-timeline`
- `GET /mantis/vessel-track` — AIS track replay from ClickHouse (NDJSON stream, max 3 days)

```bash
cd restapi
source venv/bin/activate
gunicorn -c gunicorn_config.py main:app
# Swagger: http://localhost:8080/swagger
```

## MCP (`mcp/`)

Optional. See `mcp/README.md`. Talks to `restapi` over HTTP.

## Analysis notebook / Streamlit (not the product)

```bash
source venv/bin/activate
streamlit run ./st_app/app.py --server.port 8080
```

## Out of scope (other repositories)

| Path | Why |
| --- | --- |
| Frontend operations app | Separate developer, separate repo |
| `PyTSS-Reporting/` | TSS Reporting — different product |
| `PyTSS/` | Different product / analyser tree |

## Maintainer rule

1. How AIS becomes proximity / slow-move / movement rows → the three `backend/` scripts listed above
2. What the frontend receives (polygons, Excl, keep/drop/label) → `restapi/`
3. Never mix MANTIS into TSS Reporting
4. Do not pull `vesselzone.py` or `backend/polygons.py` into MANTIS unless product scope changes
