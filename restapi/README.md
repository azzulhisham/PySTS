# MANTIS API

Flask REST API that **serves the frontend** from AIS activities already produced by the MANTIS data pipeline.

This team owns **backend processing** and **this API**. The operations frontend is another developer’s repository.

This file is the **technical contract** for the API: how polygons are registered, how Excl holes work, and how each detector must apply those rules. Marketing copy lives in `marketing/` and must not replace this document.

## Where this API sits (read this first)

MANTIS in `PySTS` is **three pipeline jobs + this API**. It is not TSS Reporting, and it is not the Streamlit analysis app.

```
ClickHouse AIS  +  Postgres static
        │
        ▼
PySTS/backend/          MANTIS pipeline only:
                          vesselproximitydetection.py
                          vesselslowspeeddetection.py
                          vesselstrajectorydetection.py
        │
        ▼
Postgres `pnav`         (ClickHouse for track replay)
        │
        ▼
PySTS/restapi/          this API — product rules + JSON
        │
        ▼
Frontend                separate repo / separate developer
```

| Layer | Path | Responsibility |
| --- | --- | --- |
| Pipeline | `backend/vesselproximitydetection.py` | STS clusters → `ais_vesselproximityobservation` / `member` |
| Pipeline | `backend/vesselslowspeeddetection.py` | Slow-move / silence → `ais_vesselslowmoveactivities` |
| Pipeline | `backend/vesselstrajectorydetection.py` | Stops / movement → `ais_vesselmovementactivities` |
| API | `PySTS/restapi/` (here) | Read those tables, apply polygon / Excl rules, return JSON |
| MCP | `PySTS/mcp/` | Optional tools that **call this API** |
| Whole-repo map | [`../readme.md`](../readme.md) | What is MANTIS vs what is not |

**Do not** copy this API, these polygons, or these detector rules into `PyTSS-Reporting` or `PyTSS`.

**Do not** re-implement the AIS pipeline here. If STS / dark / illegal-anchoring look stale, fix the three pipeline scripts.

**Not MANTIS** (do not treat as this product): `st_app/` (analysis Streamlit), `backend/polygons.py`, `backend/vesselzone.py` (imports `backend/polygons.py`). Those serve other purposes. The MANTIS API catalogue is only `restapi/polygons.py`.

## Features

- `GET /mantis/polygons` — all named polygons + restricted limit
- `GET /mantis/sts-activities` — STS proximity pairs inside parent polygons (Excl holes excluded)
- `GET /mantis/illegal-anchoring` — heuristic illegal-anchoring candidates (v3)
- `GET /mantis/darkvessels` — suspected dark / AIS-transponder-off vessels (label by polygon; never drop)
- `GET /mantis/vessel-timeline` — derived activity/events for one vessel
- `GET /mantis/vessel-track` — AIS position track for map replay
- `POST /authentication/token` — issue a JWT access token
- `GET /` — health check (Bearer required)
- Swagger UI at `/swagger`
- Production WSGI server via **gunicorn**

## Project layout

```
restapi/
├── main.py                 # Flask application (MANTIS API)
├── polygons.py             # Named polygons, Excl holes, Restricted Limit
├── sts_detection.py        # STS proximity inside parent polygons
├── illegal_anchoring.py    # Illegal-anchoring detection (v3, Excl holes excluded)
├── dark_vessels.py         # Dark / AIS-off detection (polygon label only)
├── gunicorn_config.py      # Gunicorn WSGI settings
├── requirements.txt
├── Dockerfile
├── README.md
├── test.ipynb              # Polygon map + STS validation notebook
├── marketing/              # Marketing brochure PDF + build assets
│   └── MANTIS_Marketing_Brochure.pdf
├── data/
│   └── anchorage.xlsx      # Source DMS coordinates
└── static/
    └── swagger.json        # OpenAPI 3.1 specification
```

## Setup

```bash
cd PySTS/restapi

python3 -m venv venv
source venv/bin/activate

pip install -r requirements.txt
```

## Run with gunicorn (recommended)

```bash
cd PySTS/restapi
source venv/bin/activate

gunicorn -c gunicorn_config.py main:app
```

The API listens on `http://0.0.0.0:8080` by default.

### Useful overrides

| Variable | Default | Description |
| --- | --- | --- |
| `py_flask_port` | `8080` | Port used in the default bind address |
| `gunicorn_bind` | `0.0.0.0:<py_flask_port>` | Full bind address |
| `gunicorn_workers` | `2` | Number of worker processes |
| `gunicorn_timeout` | `120` | Worker timeout (seconds) |
| `gunicorn_loglevel` | `info` | Log level |

Example:

```bash
gunicorn_workers=4 py_flask_port=8080 gunicorn -c gunicorn_config.py main:app
```

## Run with Flask (local debugging only)

```bash
python main.py
```

Do **not** use the Flask development server in production.

## Docker

```bash
cd PySTS/restapi

docker build --platform linux/amd64 -t pysts-restapi:v1.0.0 .

docker run --rm -p 8080:8080 pysts-restapi:v1.0.0
```

Optional environment overrides:

```bash
docker run --rm -p 8080:8080 \
  -e sts_user_id=user@sts.my \
  -e sts_access_key=<your-access-key> \
  -e sts_jwt_secret=<your-jwt-secret> \
  -e gunicorn_workers=4 \
  pysts-restapi:v1.0.0
```

## Authentication

Protected endpoints require a Bearer JWT.

### 1. Obtain a token

```bash
curl -X POST http://localhost:8080/authentication/token \
  -H "Content-Type: application/json" \
  -d '{
    "userId": "user@sts.my",
    "accessKey": "vZOODBrmB3cc0nvMiLwXtssAnchorageuj15dNSohbDgldkW_NI"
  }'
```

Response:

```json
{
  "accessToken": "<jwt-token>",
  "expiredDate": "2026-12-31 23:59:59"
}
```

### 2. Call a protected endpoint

```bash
curl http://localhost:8080/mantis/polygons \
  -H "Authorization: Bearer <jwt-token>"
```

### Default credentials

| Setting | Default value | Env override |
| --- | --- | --- |
| User ID | `user@sts.my` | `sts_user_id` |
| Access key | `vZOODBrmB3cc0nvMiLwXtssAnchorageuj15dNSohbDgldkW_NI` | `sts_access_key` |
| JWT secret | `admin-sts@pinc.my` | `sts_jwt_secret` |

Change these for any non-local deployment.

## Swagger UI

Open:

```
http://localhost:8080/swagger
```

1. Call **POST /authentication/token**
2. Copy `accessToken`
3. Click **Authorize**, paste the token, then call protected endpoints

OpenAPI spec file: `static/swagger.json` (OpenAPI 3.1.0)

## Detection rules (maintainer source of truth)

These rules are the product contract. Do not “simplify” them without an explicit product decision. A named polygon that is **not** listed in `anchorage_areas` is **dead code** — `/mantis/polygons`, STS, illegal-anchoring and dark-vessel labels will ignore it.

Code: `polygons.py` (`is_excl_name`, `anchorage_areas`, `all_polygons`, `restricted_limit`).

### Catalogue

| List | What it is | Used by |
| --- | --- | --- |
| `anchorage_areas` | Every named area (parents **and** Excl holes) | STS, illegal-anchoring, dark labels, `/mantis/polygons` |
| `restricted_limit` | Large study-box polygon `"Restricted Limit"` | Illegal-anchoring keep-rule; `/mantis/polygons` |
| `all_polygons` | `anchorage_areas + [restricted_limit]` | `GET /mantis/polygons`, health `polygonCount` |

Counts at last catalogue update (2026-08): **103** named areas (96 parents + **7** Excl) and **1** Restricted Limit → **104** in `all_polygons`.

Each area is `{"name": str, "polygon": [[lon, lat], ...]}`. Coordinates are GeoJSON-ordered `[longitude, latitude]`. Rings should be closed (first point repeated as last).

### Parent vs Excl (holes)

A polygon whose **display name** contains `excl` (case-insensitive) is a **carve-out / hole inside a parent**. Helper: `is_excl_name(name)` → `"excl" in (name or "").lower()`.

**Worked example**

- Parent: `singapore_south_anchorage_singapore` → `"Singapore South Anchorage, Singapore"`
- Hole: `singapore_south_anchorage_excl1_singapore` → `"Singapore South Anchorage (Excl1), Singapore"` (and Excl2–5)

| Last position | STS | Illegal anchoring | Dark vessels |
| --- | --- | --- | --- |
| Inside parent, **not** inside any Excl | keep | keep | keep; `polygonName` = parent |
| Inside an Excl hole (also inside parent) | **drop** | **drop** | **keep**; `polygonName` = Excl name; `inExclPolygon` = true |
| Outside all named areas | drop | keep only if inside Restricted Limit | keep; `polygonName` = null |

Singapore East Anchorage, Singapore Western OPL and Singapore South Anchorage are **parents / watch zones**. They are **not** a blanket “Singapore port-limit” exclusion. Only the Excl holes are exclusions for STS and illegal-anchoring.

**Current Excl holes** (must stay named with `Excl` in the display name):

- Singapore South Anchorage (Excl1–5), Singapore
- Bintulu Sarawak Anchorage (Excl1), Malaysia
- Muara Port Anchorage (Excl1), Brunei Darussalam

### Restricted Limit

- **Illegal anchoring:** KEEP if last position is inside Restricted Limit **or** a parent polygon; then DROP if inside any Excl.
- **STS:** Restricted Limit is **not** a detection zone. Centroid must be inside a parent polygon.
- **Dark vessels:** Restricted Limit is **not** used for `polygonName`. It is a large box and would label almost every candidate `Restricted Limit`. Labels use `anchorage_areas` only.

### Duplicate display names

`palembang_anchorage1_indonesia` / `palembang_anchorage2_indonesia` and `bakauheni_anchorage1_indonesia` / `bakauheni_anchorage2_indonesia` may share the same `"name"` string. **Leave the names duplicated.** Distinguish them by Python variable name, not by renaming for uniqueness.

### Adding or changing a polygon

1. Define the dict in `polygons.py`.
2. Append it to `anchorage_areas` (source order). If you skip this list, the polygon will not be served or used.
3. If it is a hole inside a parent, the **display name must contain `Excl`** (e.g. `(Excl1)`). Otherwise STS/illegal-anchoring will treat it as a watch zone.
4. If it is a parent, also add every hole that belongs inside it.
5. Do not put `restricted_limit` into `anchorage_areas`.
6. Update `static/swagger.json` descriptions only if behaviour changes.
7. `test.ipynb` calls `port_limit_polygons()` — that helper now returns **Excl holes**, not “Singapore port limit”.

### How each detector applies the catalogue

| Detector | File | Keep / drop | Polygon fields |
| --- | --- | --- | --- |
| STS | `sts_detection.py` | Centroid in a **parent**; drop if centroid is also in any Excl | `anchorageName` from the parent match |
| Illegal anchoring | `illegal_anchoring.py` (`rule_version` `v3-in-restricted-or-watch-exclude-excl-holes`) | Stopped Class-A 70–89; keep if in Restricted Limit **or** a parent; **drop if in any Excl** | `watchPolygonName`; `inPortLimit` / `portLimitName` / `portLimitPolygonCount` are **compat keys for Excl holes** |
| Dark vessels | `dark_vessels.py` (`rule_version` `v1.1-slowmove-dark-polygon-label`) | **Never drop** because of a polygon | `polygonName` (Excl preferred if in a hole); `inExclPolygon` |

MCP (`PySTS/mcp`) is a pass-through to this API. It does not re-implement polygon rules.

## API endpoints

| Method | Path | Auth | Description |
| --- | --- | --- | --- |
| `POST` | `/authentication/token` | No | Issue JWT access token |
| `GET` | `/mantis/polygons` | Bearer | All named polygons + Restricted Limit |
| `GET` | `/mantis/sts-activities` | Bearer | STS pairs inside parent polygons (Excl excluded) |
| `GET` | `/mantis/illegal-anchoring` | Bearer | Illegal-anchoring candidates (v3) |
| `GET` | `/mantis/darkvessels` | Bearer | Dark / AIS-off candidates (polygon label only) |
| `GET` | `/mantis/vessel-timeline` | Bearer | Derived events for one MMSI (`mmsi`, `from`, `to`) |
| `GET` | `/mantis/vessel-track` | Bearer | AIS track replay (`mmsi`, `from`, `to`; optional `includeClassB`) |
| `GET` | `/` | Bearer | Health check (`polygonCount` = `len(all_polygons)`) |

### Polygon response shape

```json
[
  {
    "name": "East Malaysia OPL, Malaysia",
    "polygon": [
      [104.8106002778, 2.2179960278],
      [104.4250488333, 2.2255358056]
    ]
  },
  {
    "name": "Restricted Limit",
    "polygon": [
      [104.565, 2.283333333333333],
      [104.95033277777777, 2.2838644444444443]
    ]
  }
]
```

Coordinates are GeoJSON-ordered `[longitude, latitude]`. The list is `all_polygons` (every named area **including Excl holes**, plus Restricted Limit). Excl polygons are part of the catalogue so clients can draw holes; STS and illegal-anchoring still **exclude** vessels found inside them. See [Detection rules](#detection-rules-maintainer-source-of-truth).

### STS activities

`GET /mantis/sts-activities` returns **active** proximity clusters (`is_open = true`) with **suspicion score ≥ 4.5** whose **centroid** is inside a **parent / watch** polygon. If the centroid is inside an **Excl** hole, the cluster is dropped even if it is also inside the parent.

Pairs are recomputed at **≤ 35 m**. Only **paired vessels** are included.

Each pair payload includes:

- `vesselA` / `vesselB`: `mmsi`, `shipName`, `latitude`, `longitude`, `sog`, `cog`
- `distanceM`
- `durationSeconds` / `durationHours` / `durationLabel` (how long the cluster has been open)
- `pairedAt` (`last_detected_at` — when the pairing was last determined)
- `firstDetectedAt`
- `anchorageName` — the **parent** polygon (never an Excl name)

Optional query param: `minSuspicionScore` (float, default `4.5`).

Example:

```bash
curl "http://localhost:8080/mantis/sts-activities?minSuspicionScore=4.5" \
  -H "Authorization: Bearer <jwt-token>"
```

### Illegal anchoring (v3 heuristic)

`GET /mantis/illegal-anchoring` flags stopped/stale **Class-A large vessels** only (AIS `shipType` **70–89**: cargo / container / tanker).

**Keep** if last position is:

1. Inside the **Restricted Limit** polygon, and/or
2. Inside a **parent / watch** polygon (including Singapore East, Western OPL, South, and all other named parents)

**Drop** if last position is inside **any Excl hole**, even when that point is also inside the parent and/or Restricted Limit.

Example: a vessel in `Singapore South Anchorage, Singapore` is a candidate **unless** it is also inside `Singapore South Anchorage (Excl1), Singapore` (or Excl2–5).

JSON keys kept for compatibility — they now mean **Excl hole**, not “Singapore port limit”:

| Key | Meaning |
| --- | --- |
| `inPortLimit` | Last position was inside an Excl hole (returned vessels are already filtered, so this is typically `false`) |
| `portLimitName` | Excl display name when applicable |
| `portLimitPolygonCount` | Number of Excl polygons in the catalogue |
| `watchPolygonName` | Parent polygon name |
| `watchPolygonCount` | Number of parent / watch polygons |

```bash
curl http://localhost:8080/mantis/illegal-anchoring \
  -H "Authorization: Bearer <jwt-token>"
```

### Dark vessels (v1.1 heuristic)

`GET /mantis/darkvessels` returns **Class-A large vessels** (shipType **70–89**) from `ais_vesselslowmoveactivities` that slowed then went silent before a confirmed stop (`rowcount < 30`, silence ≥ 30 minutes).

**Polygon policy: label only, never drop.** Every candidate stays in the payload. When the last position (`curlongitude`/`curlatitude`, falling back to `longitude`/`latitude`) is inside a named area in `anchorage_areas`:

- `polygonName` is set to that area’s display name
- If the point is inside both a parent and an Excl hole, **the Excl name is used** (more specific) and `inExclPolygon` is `true`
- Restricted Limit is **not** used for labelling

| `darkReason` | Meaning |
| --- | --- |
| `suspected_dark_after_slowdown` | Higher interest — slow-down evidence then AIS gap |
| `possible_coverage_exit` | Likely left SEA AIS footprint (competing explanation) |
| `low_evidence_ais_gap` | Silence without strong slow-down evidence |

Research notes and improvement roadmap: `../backend/vesselslowspeeddetection.md` (section *Dark / AIS-Transponder-Off Detection*).

```bash
# All candidates (including possible coverage exit)
curl "http://localhost:8080/mantis/darkvessels" \
  -H "Authorization: Bearer <jwt-token>"

# Ops-tight list (exclude possible_coverage_exit)
curl "http://localhost:8080/mantis/darkvessels?includeCoverageExit=false" \
  -H "Authorization: Bearer <jwt-token>"
```

### Vessel timeline and track

These endpoints are independent of the parent/Excl keep/drop rules above.

Zone-visit rows (`ais_vesselinzone`, `ais_vesselinrestrictzone`) are **not** written by the three MANTIS-critical pipeline jobs. `backend/vesselzone.py` is out of MANTIS scope at this time.

| Endpoint | Query | Source |
| --- | --- | --- |
| `GET /mantis/vessel-timeline` | `mmsi`, `from`, `to` (required) | PostgreSQL: zone visits, restricted zones, stop/slow-move, static identity changes |
| `GET /mantis/vessel-track` | `mmsi`, `from`, `to` (required); `includeClassB` (optional, default false) | ClickHouse AIS positions for map replay |

## Related packages (boundaries)

- Whole MANTIS layout: [`../readme.md`](../readme.md)
- MANTIS pipeline only: `vesselproximitydetection.py`, `vesselslowspeeddetection.py`, `vesselstrajectorydetection.py`
- MCP wrapper: `PySTS/mcp/` (HTTP client to this API only)
- Frontend: separate developer, not in this repo
- **Not MANTIS:** `st_app/`, `backend/polygons.py`, `backend/vesselzone.py`
- **Not this project:** `PyTSS-Reporting`, `PyTSS`
