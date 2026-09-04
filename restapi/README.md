# MANTIS API

Flask REST API that **serves the frontend** from AIS activities already produced by the MANTIS data pipeline.

This team owns **backend processing** and **this API**. The operations frontend is another developer’s repository.

This file is the **technical contract** for the API: how polygons are registered, how Excl holes work, how each detector must apply those rules, and how OFAC identity labels are attached. Marketing copy lives in `marketing/` and must not replace this document.

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
| API | `PySTS/restapi/` (here) | Read those tables, apply polygon / Excl rules, attach OFAC labels and AIS size, return JSON |
| MCP | `PySTS/mcp/` | Optional tools that **call this API** |
| Whole-repo map | [`../readme.md`](../readme.md) | What is MANTIS vs what is not |

**Do not** copy this API, these polygons, or these detector rules into `PyTSS-Reporting` or `PyTSS`.

**Do not** re-implement the AIS pipeline here. If STS / dark / illegal-anchoring look stale, fix the three pipeline scripts.

**Not MANTIS** (do not treat as this product): `st_app/` (analysis Streamlit), `backend/polygons.py`, `backend/vesselzone.py` (imports `backend/polygons.py`). Those serve other purposes. The MANTIS API catalogue is only `restapi/polygons.py`.

## Features

- `GET /mantis/polygons` — all named polygons + restricted limit
- `GET /mantis/sts-activities` — STS proximity pairs inside parent polygons (Excl holes excluded; OFAC labels on each vessel)
- `GET /mantis/illegal-anchoring` — heuristic illegal-anchoring candidates (v3; OFAC labels)
- `GET /mantis/identity-conflict` — re-flag / dual-MMSI identity groups (AIS timestamp; OFAC labels)
- `GET /mantis/spoofing` — position anomalies Phase 1: teleport (cargo/tanker; dedupe per MMSI/day). Swagger alias: `/mantis/position-anomaly`
- `GET /mantis/sanctions` — OFAC vessel list (search by `imo` or `mmsi`; full list otherwise)
- `GET /mantis/vessel-timeline` — derived activity/events for one vessel
- `GET /mantis/vessel-track` — AIS position track for map replay (NDJSON stream; max 3 days)
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
├── identity_conflict.py    # Re-flag / dual-MMSI identity groups (AIS timestamp)
├── sanctions.py            # OFAC identity labels (IMO / MMSI join; not a detector)
├── vessel_size.py          # AIS Class A/B dimensions (to_bow / to_stern / to_port / to_starboard)
├── swagger_sample.py       # 20-row cap when Try it out is run from /swagger
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

### 20-row sample on this page (all list endpoints)

Try it out from `/swagger` is detected via the `Referer` header. If a list in the JSON is longer than **20** rows, the API returns **20 samples** only so the page does not lag.

| Field | Meaning |
| --- | --- |
| `sample` | `true` when a list was capped |
| `sampleLimit` | `20` |
| `fromSwagger` | `true` |
| `totalCount` / `totalCounts` | Size before the cap |
| `returnedCount` / `returnedCounts` | Rows actually in the payload |
| `message` | Explains that curl / frontend / MCP get the full result |

Applies to: polygons (Swagger gets a wrapped object), STS `pairs` / `pairedVessels`, illegal-anchoring `vessels`, dark `vessels`, identity-conflict `groups` / `identities`, spoofing `anomalies`, sanctions `vessels`, timeline `events`, track `track`.

**Not sampled:** `POST /authentication/token` and `GET /` (health).

curl, the operations frontend, and MCP always receive the **full** payload. `/mantis/polygons` stays a **bare JSON array** for those clients; only Swagger gets `{ "polygons": [ ...20 ], "sample": true, ... }`.

`GET /mantis/vessel-track` is different for real clients: they receive an **NDJSON stream** of 20-minute chunks (not one JSON object). Swagger Try it out still gets a 20-point JSON sample.

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
| Illegal anchoring | `illegal_anchoring.py` (`rule_version` `v3.1-…-ofac-label`) | Stopped Class-A 70–89; keep if in Restricted Limit **or** a parent; **drop if in any Excl** | `watchPolygonName`; `inPortLimit` / `portLimitName` / `portLimitPolygonCount` are **compat keys for Excl holes** |
| Dark vessels | `dark_vessels.py` (`rule_version` `v1.2-slowmove-dark-polygon-ofac-label`) | **Never drop** because of a polygon | `polygonName` (Excl preferred if in a hole); `inExclPolygon` |
| Identity conflict | `identity_conflict.py` (`rule_version` `v1.0-identity-conflict-ais-ts-ofac`) | Same-hull groups of 2+ MMSIs; optional `maxDistanceM` | none (not a location detector) |
| Position anomalies (spoofing) | `spoofing.py` (`rule_version` `v1.0-teleport-cargo-tanker-daily-dedupe-ofac`) | Phase 1 teleport; cargo/tanker 70–89; dedupe 1/MMSI/UTC day; live ClickHouse | none. Future phases: [`backend/todo.md`](../backend/todo.md#position-anomalies--get-mantisspoofing-phase-1-done) |

OFAC identity (STS / dark / illegal-anchoring / identity-conflict): `imo`, `sanctionsMatch`, `matchConfidence` (`confirmed` \| `possible` \| `none`), `sanctionsList`. See [OFAC labels](#ofac-labels-identity-not-a-detector).

MCP (`PySTS/mcp`) is a pass-through to this API. It does not re-implement polygon or OFAC rules.

## OFAC labels (identity, not a detector)

This is the **product contract** for sanctions fields on the API. Detection knobs and ingest tables also live in [`../backend/mantis-detection.md`](../backend/mantis-detection.md). Do not turn OFAC into a fourth MANTIS job, and do not treat a match as a legal finding.

Code: `sanctions.py` (`match_vessel`, `attach_sanctions`, `attach_sanctions_pair_sides`).

OFAC data is already in Postgres `pnav` (loaded by `backend/ofac_sdn_ingest.py` and `backend/ofac_cons_ingest.py`). This API **only joins** those tables onto candidates that already passed STS / dark / illegal-anchoring / identity-conflict rules.

| What | Detail |
| --- | --- |
| Not a new table | No OFAC “events” table. No new pipeline job. |
| Not a score | Does **not** change `suspicionScore`, dark `confidence`, or the 4.5 STS cut. |
| Not a drop rule | Unmatched vessels **stay**. A listed ship that is not already a candidate does **not** appear. |
| Not bunker | `onBunkerRegister` is skipped until a bunker register exists. |

### Data used

| Source | View | Typical content |
| --- | --- | --- |
| OFAC SDN | `ofac_sdn_vessel` | Ships (`sdn_type = Vessel`), IMO on most rows |
| OFAC consolidated non-SDN | `ofac_cons_vessel` | Joined if the view exists. Current file is companies/people — **zero vessels is valid** |
| Candidate identity | `ais_static."imo"` and MMSI on the activity row | IMO preferred |

If `ofac_sdn_vessel` is missing, every candidate gets `sanctionsMatch: false` and the API still returns the list.

### Match rules

Apply **in this order**. Name, callsign, and flag are **not** used.

| Candidate has | OFAC has | Result | `matchConfidence` |
| --- | --- | --- | --- |
| IMO (7 digits, from `ais_static` or `IMO 9187629`) | Same IMO on SDN or CONS | match | `confirmed` |
| IMO | That IMO **not** on OFAC | **no match** — do **not** try MMSI | `none` |
| No IMO, has MMSI | Same MMSI on OFAC | match (weaker; MMSI can be missing or spoofed) | `possible` |
| No IMO, MMSI not on OFAC | — | no match | `none` |
| Neither IMO nor MMSI | — | no match | `none` |

If the same IMO exists on both lists, **SDN wins**. `sanctionsList` is then `SDN`. CONS has no ships today, so almost every hit is `SDN`.

### JSON fields (every vessel on the three lists)

| Field | Type | Meaning |
| --- | --- | --- |
| `imo` | string or `null` | Normalized 7-digit IMO from `ais_static`, if present |
| `toBow` | number or `null` | Metres from AIS GPS antenna to bow |
| `toStern` | number or `null` | Metres from AIS GPS antenna to stern |
| `toPort` | number or `null` | Metres from AIS GPS antenna to port |
| `toStarboard` | number or `null` | Metres from AIS GPS antenna to starboard |
| `lengthM` | number or `null` | Overall length: `toBow + toStern`. `null` if either offset is missing |
| `beamM` | number or `null` | Beam (width): `toPort + toStarboard`. `null` if either offset is missing |
| `lastSeenAt` | string (ISO 8601 UTC) or `null` | **Last AIS position time** (`tscurrent`). Use this — not `pairedAt` / `detectedAt` — as the anchor for `GET /mantis/vessel-track` |
| `sanctionsMatch` | boolean | `true` if this ship matched OFAC |
| `matchConfidence` | `confirmed` \| `possible` \| `none` | How sure the **identity** match is — not how sure the AIS behaviour is |
| `sanctionsList` | `SDN` \| `CONS` \| `null` | Which OFAC file matched. `null` when `sanctionsMatch` is false |

Size is from AIS static, not OFAC. Prefer `ais_static` (Class A); if those four offsets are missing, use `ais_staticb` (Class B). Dark and illegal-anchoring still require a Class A static row (`shipType` 70–89); Class B does **not** add extra candidates. Missing offsets stay `null` — vessels are not dropped.

STS pairs also have pair-level `sanctionsMatch` / `matchConfidence` (true / best of vessel A and B). Each of `vesselA` and `vesselB` still has its own fields.

Example (dark vessel that matched by IMO):

```json
{
  "mmsi": 256845000,
  "shipName": "APAMA",
  "imo": "9187631",
  "darkReason": "suspected_dark_after_slowdown",
  "confidence": "high",
  "sanctionsMatch": true,
  "matchConfidence": "confirmed",
  "sanctionsList": "SDN"
}
```

Unmatched example:

```json
{
  "imo": "9234567",
  "sanctionsMatch": false,
  "matchConfidence": "none",
  "sanctionsList": null
}
```

### Sort (priority, not score)

On STS, dark, illegal-anchoring, and identity-conflict payloads, rows are ordered:

1. `confirmed`
2. `possible`
3. `none`

Inside the same band, existing ops order is kept (STS still prefers higher `suspicionScore` then shorter distance; dark still prefers older `tscurrent`).

That is so a listed ship that is also dark or in an STS pair is seen first. It is **not** a higher AIS confidence.

### Counts in the response envelope

| Endpoint | Extra count fields |
| --- | --- |
| `/mantis/darkvessels` | `sanctionsMatchCount` |
| `/mantis/illegal-anchoring` | `sanctionsMatchCount` |
| `/mantis/sts-activities` | `sanctionsMatchPairCount`, `sanctionsMatchVesselCount` |
| `/mantis/identity-conflict` | `sanctionsMatchGroupCount`, `sanctionsMatchIdentityCount` |

### Frontend

New fields on the list endpoints (`sanctionsMatch` and AIS size). Brief the frontend developer before painting them on the map. Do not treat `sanctionsMatch: true` as an auto-verdict in the UI.

### Lookup endpoint (`GET /mantis/sanctions`)

Browse or search the ingested OFAC **vessel** list (not people/companies). This is a directory, not a detector.

| Query | Behaviour |
| --- | --- |
| `imo=9187631` (or `IMO 9187631`) | Rows whose IMO matches |
| `mmsi=256845000` | Rows whose MMSI matches |
| both `imo` and `mmsi` | Rows matching **either** |
| neither | **Entire** vessel list (~1,500 SDN ships today) |

**Swagger UI:** same 20-row sample cap as every other list endpoint (see [20-row sample](#20-row-sample-on-this-page-all-list-endpoints)). curl / frontend / MCP get the full vessel list.

```bash
# Full list (not from Swagger)
curl "http://localhost:8080/mantis/sanctions" \
  -H "Authorization: Bearer <jwt-token>"

# Lookup by IMO
curl "http://localhost:8080/mantis/sanctions?imo=9187631" \
  -H "Authorization: Bearer <jwt-token>"
```

## API endpoints

| Method | Path | Auth | Description |
| --- | --- | --- | --- |
| `POST` | `/authentication/token` | No | Issue JWT access token |
| `GET` | `/mantis/polygons` | Bearer | All named polygons + Restricted Limit |
| `GET` | `/mantis/sts-activities` | Bearer | STS pairs inside parent polygons (Excl excluded); OFAC labels |
| `GET` | `/mantis/illegal-anchoring` | Bearer | Illegal-anchoring candidates (v3); OFAC labels |
| `GET` | `/mantis/darkvessels` | Bearer | Dark / AIS-off candidates (polygon + OFAC label only) |
| `GET` | `/mantis/identity-conflict` | Bearer | Re-flag / dual-MMSI identity groups (AIS time; OFAC labels) |
| `GET` | `/mantis/spoofing` | Bearer | Position anomalies Phase 1 (teleport; cargo/tanker 70–89; dedupe per MMSI/day; optional `from`/`to`, max 3 days) |
| `GET` | `/mantis/sanctions` | Bearer | OFAC vessel list (`imo` / `mmsi` search) |
| `GET` | `/mantis/vessel-timeline` | Bearer | Derived events for one MMSI (`mmsi`, `from`, `to`) |
| `GET` | `/mantis/vessel-track` | Bearer | AIS track replay (`mmsi` required; `from` / `to` optional, default last 3 days, max 3 days; NDJSON stream) |
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

- `vesselA` / `vesselB`: `mmsi`, `shipName`, `latitude`, `longitude`, `sog`, `cog`, `lastSeenAt`, `imo`, `toBow`, `toStern`, `toPort`, `toStarboard`, `lengthM`, `beamM`, `sanctionsMatch`, `matchConfidence`, `sanctionsList`
- pair-level `sanctionsMatch` / `matchConfidence` (best of the two vessels)
- `distanceM`
- `durationSeconds` / `durationHours` / `durationLabel` (how long the cluster has been open)
- `pairedAt` — pipeline **wall clock** (`last_detected_at` on the observation). Do **not** use for vessel-track.
- `firstDetectedAt` — same (wall clock)
- `lastSeenAt` on each vessel — **AIS time** of the fix used for that member (`tscurrent`, else `ais_position.ts`)
- `anchorageName` — the **parent** polygon (never an Excl name)

Optional query param: `minSuspicionScore` (float, default `4.5`). OFAC does **not** let a pair through if the score is below this cut. See [OFAC labels](#ofac-labels-identity-not-a-detector).

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

Each vessel also has OFAC fields (`imo`, `sanctionsMatch`, `matchConfidence`, `sanctionsList`), AIS size (`toBow`, …), and **`lastSeenAt`** (same as `tsCurrent` — last AIS fix on the movement activity). Keep/drop above is unchanged. See [OFAC labels](#ofac-labels-identity-not-a-detector).

### Dark vessels (v1.2 heuristic)

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

Research notes and improvement roadmap: `../backend/mantis-detection.md`. OFAC fields are the same as on STS / illegal-anchoring; dark `confidence` is unchanged. Size fields are the same AIS static join. Each vessel has **`lastSeenAt`** (same as `tsCurrent`). See [OFAC labels](#ofac-labels-identity-not-a-detector).

```bash
# All candidates (including possible coverage exit)
curl "http://localhost:8080/mantis/darkvessels" \
  -H "Authorization: Bearer <jwt-token>"

# Ops-tight list (exclude possible_coverage_exit)
curl "http://localhost:8080/mantis/darkvessels?includeCoverageExit=false" \
  -H "Authorization: Bearer <jwt-token>"
```

### Identity conflict

`GET /mantis/identity-conflict` groups Class-A cargo/tanker MMSIs (`shipType` **70–89**) that resolve to **one hull** — a re-flagged vessel still broadcasting a retired MMSI, or two identities with the same name plus callsign/dimensions.

Match rule (same as the backend STS same-hull suppression):

- **IMO plus** name, callsign, or dimensions, **or**
- **name plus** callsign or dimensions

Placeholder IMOs (`1234567`, `7654321`, `9999999`, repdigits) and names shorter than 3 characters are ignored, so a shared junk IMO does not collapse unrelated ships.

`detectedAt` is the **latest AIS timestamp** in the group (`ais_position.ts`, falling back to `ais_static.ts`). It is not the API wall clock. Each identity also has `lastAisAt` (position) and `identityAt` (static Type 5).

OFAC labels are attached per identity and rolled up on the group (`sanctionsMatch` is true if **any** identity matches). Unmatched groups stay in the list. Groups are sorted listed-first, then newest AIS time.

Optional query param: `maxDistanceM` (float). When set, only groups whose last-known positions are closer than that many metres are returned (groups without two positions are dropped).

```bash
curl "http://localhost:8080/mantis/identity-conflict" \
  -H "Authorization: Bearer <jwt-token>"

# Only identities still sitting near each other (e.g. false STS pairs)
curl "http://localhost:8080/mantis/identity-conflict?maxDistanceM=50" \
  -H "Authorization: Bearer <jwt-token>"
```

### Vessel timeline and track

These endpoints are independent of the parent/Excl keep/drop rules above.

Zone-visit rows (`ais_vesselinzone`, `ais_vesselinrestrictzone`) are **not** written by the three MANTIS-critical pipeline jobs. `backend/vesselzone.py` is out of MANTIS scope at this time.

| Endpoint | Query | Source |
| --- | --- | --- |
| `GET /mantis/vessel-timeline` | `mmsi`, `from`, `to` (required) | PostgreSQL: zone visits, restricted zones, stop/slow-move, static identity changes |
| `GET /mantis/vessel-track` | `mmsi` (required); `from` / `to` optional (default last 3 days); `includeClassB` optional (default false) | ClickHouse AIS positions, **NDJSON stream** of 20-minute chunks |

**Linking from STS / dark / illegal-anchoring:** use each vessel's **`lastSeenAt`** (AIS time), not `pairedAt` or `detectedAt` (pipeline wall clock). Typical pattern: pass `to=<lastSeenAt>` and omit `from` to get the default 3-day window ending at that fix, or set both bounds around the event you care about (max 3 days).

```bash
# STS pair member — replay track ending at the AIS fix shown on the map
curl -N "http://localhost:8080/mantis/vessel-track?mmsi=352006140&to=2026-06-28T04%3A15%3A10Z" \
  -H "Authorization: Bearer <jwt-token>" \
  -H "accept: application/x-ndjson"
```

#### Track range (3-day cap)

Only `mmsi` is required. Both dates are optional and the window is never wider
than 3 days.

| `from` | `to` | Effective range |
| --- | --- | --- |
| omitted | omitted | last 3 days, ending now (UTC) |
| given | omitted | `from` → now, or `from` + 3 days when that is earlier (`rangeCapped: true`) |
| omitted | given | `to` - 3 days → `to` |
| given | given | as requested; `to` clamped to `from` + 3 days when the span is longer (`rangeCapped: true`) |

- Blank values (`from=`) count as omitted.
- `from` after `to` is still `400`.
- A `from` in the future is paired with `from` + 3 days — no data, but not an error.
- Meta reports `fromOmitted`, `toOmitted`, `rangeCapped`, `requestedDateFrom`, `requestedDateTo`.

```bash
# last 3 days, no dates at all
curl -N "http://localhost:8080/mantis/vessel-track?mmsi=533000123" \
  -H "Authorization: Bearer <jwt-token>"
```

#### Track position validity

Raw AIS encodes "position not available" as **latitude 91 / longitude 181**, and
those rows are stored in ClickHouse verbatim (~1.3% of `ais_position`). They
cannot be plotted, so both track queries filter them out in SQL:

```sql
AND latitude BETWEEN -90 AND 90
AND longitude BETWEEN -180 AND 180
```

The range test also drops `NaN` and `±Inf`, which ClickHouse treats as outside
any `BETWEEN`. It applies to Class A (`ais_position`) and Class B
(`ais_type18`), so every point in the stream has a usable fix. A vessel that
never reported a position now streams `meta` + `done` with `pointCount: 0`
instead of thousands of unplottable points. See `VALID_POSITION_SQL` in
`timelineplayback.py`.

`(0, 0)` is **not** filtered — it is a real location and appears in only a
handful of rows. `sog` 102.3, `cog` 360 and `trueHeading` 511 are the AIS
"unavailable" markers for those fields and are still passed through as-is.

#### Track stream (curl / frontend / Dash)

Content-Type: `application/x-ndjson`. One JSON object per line:

1. `{"type":"meta", ...}` — effective `dateFrom` / `dateTo`, cap flags
2. `{"type":"chunk", "chunkIndex":0, "points":[...]}` — one line per 20-minute ClickHouse window that has positions (empty windows are skipped)
3. `{"type":"done", "pointCount":N, "chunkCount":M}` — end
4. `{"type":"error", "message":"..."}` — only if ClickHouse fails after the stream has started

ClickHouse is queried **one 20-minute window at a time**. The first chunk is flushed as soon as that window returns, so the map can start drawing without waiting for the rest of the range. The API process does not keep the full track in memory.

```bash
# to omitted → from + 3 days
curl -N "http://localhost:8080/mantis/vessel-track?mmsi=533000123&from=2026-06-10T00:00:00Z" \
  -H "Authorization: Bearer <jwt-token>"

# requested 10 days is clamped to 3
curl -N "http://localhost:8080/mantis/vessel-track?mmsi=533000123&from=2026-06-10T00:00:00Z&to=2026-06-20T00:00:00Z" \
  -H "Authorization: Bearer <jwt-token>"
```

Python Dash (and any Python client) can read the stream:

```python
import json
import requests

def iter_track_chunks(base_url, token, mmsi, date_from, date_to=None):
    params = {"mmsi": mmsi, "from": date_from}
    if date_to:
        params["to"] = date_to
    with requests.get(
        f"{base_url}/mantis/vessel-track",
        headers={"Authorization": f"Bearer {token}"},
        params=params,
        stream=True,
        timeout=300,
    ) as response:
        response.raise_for_status()
        for line in response.iter_lines(decode_unicode=True):
            if line:
                yield json.loads(line)

# Dash: extend a figure as each chunk arrives (callback or background thread + Interval).
points = []
for msg in iter_track_chunks("http://localhost:8080", token, 533000123, "2026-06-10T00:00:00Z"):
    if msg.get("type") == "chunk":
        points.extend(msg["points"])
```

**This Swagger page:** still JSON, first 20 points only, so Try it out does not hang on a stream.

## Related packages (boundaries)

- Whole MANTIS layout: [`../readme.md`](../readme.md)
- Detection knobs + OFAC ingest spec: [`../backend/mantis-detection.md`](../backend/mantis-detection.md)
- OFAC load into `pnav`: `backend/ofac_sdn_ingest.py`, `backend/ofac_cons_ingest.py`
- MANTIS pipeline only: `vesselproximitydetection.py`, `vesselslowspeeddetection.py`, `vesselstrajectorydetection.py`
- MCP wrapper: `PySTS/mcp/` (HTTP client to this API only)
- Frontend: separate developer, not in this repo
- **Not MANTIS:** `st_app/`, `backend/polygons.py`, `backend/vesselzone.py`
- **Not this project:** `PyTSS-Reporting`, `PyTSS`
