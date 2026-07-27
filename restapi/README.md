# MANTIS API

Flask REST API that serves anchorage area polygons from `polygons.py`, secured with JWT Bearer authentication. OpenAPI 3.1 documentation is available via Swagger UI.

## Features

- `GET /mantis/polygons` — return all anchorage polygons + restricted limit
- `GET /mantis/sts-activities` — STS proximity pairs inside anchorage polygons
- `GET /mantis/illegal-anchoring` — heuristic illegal-anchoring candidates
- `GET /mantis/darkvessels` — suspected dark / AIS-transponder-off vessels
- `POST /authentication/token` — issue a JWT access token
- `GET /` — health check (Bearer required)
- Swagger UI at `/swagger`
- Production WSGI server via **gunicorn**

## Project layout

```
restapi/
├── main.py                 # Flask application (MANTIS API)
├── polygons.py             # Anchorage polygon definitions
├── sts_detection.py        # STS proximity detection inside anchorages
├── illegal_anchoring.py    # Heuristic illegal-anchoring detection (v2)
├── dark_vessels.py         # Suspected dark / AIS-off detection (v1)
├── gunicorn_config.py      # Gunicorn WSGI settings
├── requirements.txt
├── Dockerfile
├── README.md
├── test.ipynb              # Polygon map + STS validation notebook
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

## API endpoints

| Method | Path | Auth | Description |
| --- | --- | --- | --- |
| `POST` | `/authentication/token` | No | Issue JWT access token |
| `GET` | `/mantis/polygons` | Bearer | All anchorage polygons + restricted limit |
| `GET` | `/mantis/sts-activities` | Bearer | STS pairs inside anchorage polygons |
| `GET` | `/mantis/illegal-anchoring` | Bearer | Heuristic illegal-anchoring candidates |
| `GET` | `/mantis/darkvessels` | Bearer | Suspected dark / AIS-off vessels |
| `GET` | `/` | Bearer | Health check |

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

Coordinates are GeoJSON-ordered `[longitude, latitude]`. The list includes all anchorage areas plus the **Restricted Limit** polygon.

### STS activities

`GET /mantis/sts-activities` returns **active** proximity clusters (`is_open = true`) with **suspicion score ≥ 4.5** whose centroid is inside an anchorage polygon. Pairs are recomputed at **≤ 35 m**. Only **paired vessels** are included.

Each pair payload includes:

- `vesselA` / `vesselB`: `mmsi`, `shipName`, `latitude`, `longitude`, `sog`, `cog`
- `distanceM`
- `durationSeconds` / `durationHours` / `durationLabel` (how long the cluster has been open)
- `pairedAt` (`last_detected_at` — when the pairing was last determined)
- `firstDetectedAt`

Optional query param: `minSuspicionScore` (float, default `4.5`).

Example:

```bash
curl "http://localhost:8080/mantis/sts-activities?minSuspicionScore=4.5" \
  -H "Authorization: Bearer <jwt-token>"
```

### Illegal anchoring (v2 heuristic)

`GET /mantis/illegal-anchoring` flags stopped/stale **Class-A large vessels** only (AIS `shipType` **70–89**: cargo / container / tanker) that are:

1. Inside the **restricted-limit** polygon, and/or  
2. Inside a **watch polygon** from `polygons.py` (Malaysia / Indonesia OPL & anchorages)

**Excluded:** vessels inside Singapore **port-limit** polygons (`Singapore East Anchorage`, `Singapore Western OPL`, `Singapore South Anchorage` + Excl*).

```bash
curl http://localhost:8080/mantis/illegal-anchoring \
  -H "Authorization: Bearer <jwt-token>"
```

### Dark vessels (v1 heuristic)

`GET /mantis/darkvessels` returns **Class-A large vessels** (shipType **70–89**) from `ais_vesselslowmoveactivities` that slowed then went silent before a confirmed stop (`rowcount < 30`, silence ≥ 30 minutes). Independent of anchorage polygons.

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
