# Vessel Slow Speed Detection Backend

This backend process detects vessels that are slowing down, stopping, leaving a stopped location, or possibly turning off their AIS transponder while slowing down.

The main objective is to identify vessels that may try to go dark by switching off AIS before they are fully confirmed as stopped.

## Source Data

The process reads AIS position records from `public.ais_position` using `get_ais_position_data()`.

The current query loads AIS records from the last 6 days and orders them by timestamp. Each AIS row contains vessel identity, position, speed, course, navigation status, and timestamp information.

Important fields from AIS data:

- `mmsi`: vessel identifier.
- `ts`: AIS position timestamp.
- `longitude` and `latitude`: vessel position.
- `sog`: speed over ground.
- `cog`: course over ground.
- `navStatus` and `navStatusDesc`: AIS navigation status.

## Output Table

Detected activities are stored in `public.ais_vesselslowmoveactivities`.

Important fields:

- `ts`: first detected slow-speed timestamp.
- `mmsi`: vessel identifier.
- `longitude` and `latitude`: first detected position.
- `curlongitude` and `curlatitude`: latest known position.
- `sog` and `cog`: first detected speed and course.
- `cursog` and `curcog`: latest known speed and course.
- `rowcount`: count used to confirm slow movement or stop behavior.
- `rowcount2`: count used during high-speed exit handling.
- `distance`: distance between the latest AIS point and the previously stored point.
- `tscurrent`: latest AIS timestamp processed for the activity.
- `tsstop`: timestamp when the vessel is considered stopped or suspected dark.
- `tsout`: timestamp when the vessel is considered to have left the location.

## Processing Flow

The backend runs continuously in a loop. Every cycle, it performs these steps:

1. Fetch recent AIS position data.
2. Split vessels into low-speed and high-speed groups.
3. Process low-speed vessels into slow-move activities.
4. Mark stale slow-speed activities as suspected stopped or dark.
5. Process high-speed vessels to detect vessels leaving the location.
6. Sleep for 20 seconds, then repeat.

## Low-Speed Detection

A vessel is treated as low speed when:

```sql
sog <= 3.0
```

For each low-speed AIS position, the process checks whether there is already an open activity for the same `mmsi` where `tsout IS NULL`.

If no open activity exists, a new activity is inserted.

If an open activity exists, the process only updates it when the incoming AIS timestamp is newer than the stored `tscurrent`. This prevents older AIS rows from increasing `rowcount` again.

The process calculates the distance between the new AIS position and the stored current position. If the position changed, `rowcount` is incremented.

## Confirmed Stop Detection

A vessel is considered confirmed stopped when:

- the activity is still open;
- the vessel continues sending AIS positions;
- `rowcount >= 30`;
- the distance between current and previous position is less than 30 meters.

When this happens, `tsstop` is set to the current AIS timestamp.

This means the vessel was still transmitting AIS when the system confirmed that it had stopped.

## Suspected Transponder-Off Detection

The process also detects vessels that may have turned off their AIS transponder before reaching confirmed stop status.

This is handled by checking stale open records after low-speed processing.

A vessel is treated as suspected stopped or dark when:

- `tsstop IS NULL`;
- `tsout IS NULL`;
- `tscurrent` is not null;
- `tscurrent` is older than `STALE_TRANSPONDER_MINUTES`;
- `rowcount >= STALE_TRANSPONDER_MIN_ROWCOUNT`.

The current values are:

```python
STALE_TRANSPONDER_MINUTES = 30
STALE_TRANSPONDER_MIN_ROWCOUNT = 10
```

When these conditions are met, the process sets:

```sql
tsstop = tscurrent
```

This means the vessel did not provide enough AIS updates to be confirmed stopped, but it was already in a slow-speed candidate state and then disappeared from AIS.

## Estimated Dark-Stop Location

The Python file includes an `estimate_latlng()` helper that can project a possible stop location from the vessel's last known AIS position and course over ground (`cog`).

The current helper uses a fixed projected distance of `540m`. This distance assumes the vessel was travelling at about `3 knots` when the projection starts.

If the projection is scaled linearly by speed, the estimated distance becomes:

- `3 knots`: about `540m`.
- `2 knots`: about `360m`.
- `1 knot`: about `180m`.
- Below `1 knot`: less than `180m`; for example, `0.5 knot` is about `90m`.

A simple speed-aware projection could therefore calculate distance as:

```python
d = 540.0 * (sog / 3.0)
```

Another interpretation is to model the vessel as decelerating to a stop with the same deceleration rate. In that case, stopping distance scales with the square of speed:

- `3 knots`: about `540m`.
- `2 knots`: about `240m`.
- `1 knot`: about `60m`.
- Below `1 knot`: less than `60m`; for example, `0.5 knot` is about `15m`.

The non-linear deceleration projection can be calculated as:

```python
d = 540.0 * (sog / 3.0) ** 2
```

The linear version is easier to explain operationally, while the non-linear version is closer to a constant-deceleration stopping-distance model. The better choice depends on real vessel behavior, AIS update frequency, and the vessel type.

This should be interpreted as an estimated dark-stop location, not an actual confirmed stop position. The vessel may turn, drift, anchor, or slow down at a different rate after the last AIS message. The original last-known AIS latitude and longitude should therefore be preserved separately from any estimated stop latitude and longitude.

## How To Interpret Detection Results

Use `tsstop`, `tsout`, and `rowcount` together when interpreting the detection result.

- `tsstop IS NULL` and `tsout IS NULL`: the vessel is still being monitored.
- `tsstop IS NOT NULL` and `rowcount >= 30`: the vessel is confirmed stopped while still transmitting AIS.
- `tsstop IS NOT NULL` and `rowcount < 30`: the vessel is suspected to have turned off its transponder while slowing or preparing to stop.
- `tsout IS NOT NULL`: the vessel has moved out or resumed high-speed movement.

This interpretation is important because `tsstop` is used for both confirmed stop and suspected dark-stop cases.

## High-Speed Exit Detection

A vessel is treated as high speed when:

```sql
sog > 3.0
```

The process checks open slow-speed activities against vessels that now appear in the high-speed group.

If a matching high-speed AIS record is found and the distance from the stored activity position is at least 100 meters, `tsout` can be updated to indicate that the vessel has left the location.

## Operational Notes

- The process runs continuously when the file is executed directly.
- The loop interval is 20 seconds.
- DuckDB spatial functions are used to calculate distance between positions.
- PostgreSQL stores both AIS source data and detected slow-speed activity records.
- Current thresholds are hardcoded in the Python file and should be tuned based on AIS update frequency, vessel behavior, and operational area.

## Current Limitations

- `tsstop` has two meanings: confirmed stopped and suspected dark-stop. A separate field such as `tsdark`, `darkflag`, or `detectiontype` would make the result clearer.
- The AIS query reads the last 6 days of data every cycle. A timestamp or ID watermark would make processing more efficient.
- `rowcount` depends on AIS reporting frequency. A time-based rule may be more stable across vessels.
- Position jitter can affect distance and row counting, especially when a vessel is nearly stationary.


# Dark / AIS-Transponder-Off Detection — Research Findings (2026-07)

This section records findings for building a **suspected dark vessel** API on top of
`ais_vesselslowmoveactivities`, independent of anchorage polygons in
`PySTS/restapi/polygons.py`.

## Goal

Identify vessels that **likely stopped transmitting AIS after slowing down**
(possible intentional transponder-off), using the slow-speed backend output.

Important framing for future work:

- AIS silence is **evidence of disappearance from the feed**, not proof of intent.
- The operational area is **South-East Asia coverage**. Leaving the coverage footprint
  can look identical to going dark.
- Therefore API / research results should be labelled **suspected**, with confidence tiers.

## Source of truth

| Asset | Role |
|-------|------|
| `vesselslowspeeddetection.py` | Continuous detector: slow-down → stop / stale / exit |
| `public.ais_vesselslowmoveactivities` | Persisted slow-move activities with `ts`, `tscurrent`, `tsstop`, `tsout`, `rowcount`, last position & sog/cog |
| `public.ais_static` | Vessel name / type for cargo–tanker filtering |
| `public.ais_position` | Raw AIS (used by the backend loop; not required for a v1 read API) |

Anchorage polygons are **not required** for dark detection. They may be joined later
as context (where the vessel went dark), not as the primary rule.

## What the backend already encodes

Constants (current code):

```python
STALE_TRANSPONDER_MINUTES = 30
STALE_TRANSPONDER_MIN_ROWCOUNT = 1
# low-speed path: sog <= 3.0
# confirmed stop while still transmitting: rowcount >= 30 and distance < 30 m
# high-speed exit: sog > 3.0 and distance >= 100 m → tsout
```

Interpretation for dark research:

| Condition | Likely meaning |
|-----------|----------------|
| `tsstop IS NOT NULL` and `rowcount >= 30` | Confirmed stop **while still sending AIS** (not dark) |
| `tsstop IS NOT NULL` and `rowcount < 30` | **Primary dark candidate**: slowed, then silence before confirmed stop |
| Stale open row → backend sets `tsstop = tscurrent` | Suspected stop/dark after ~30 minutes without updates |
| `tsout IS NOT NULL` | Left the location / resumed high speed (close the case) |
| `tscurrent` many days old, last `cursog` high, little slow history | Often **coverage exit**, not intentional dark |

## Coverage-exit vs intentional dark (core finding)

Within SEA-only AIS:

1. **Intentional dark (higher interest)**  
   Vessel is already in a **slow-down / stop-preparation** state (`sog` low, `rowcount` climbing),
   then AIS stops near the last known position.

2. **Coverage exit / out-of-footprint (false dark)**  
   Vessel was still moving (higher `sog`) near the edge of the monitored region,
   then simply never appears again for days.

3. **Normal AIS / satellite gap**  
   Temporary silence (minutes to a few tens of minutes) without a clear slow-down story.

v1 API strategy: prefer (1), down-rank or separately label (2), require a minimum silence age for (3).

## Recommended v1 candidate SQL (dark after slow-down)

Uses `ais_vesselslowmoveactivities` + `ais_static`. Class-A large vessels only (cargo/tanker 70–89).

```sql
SELECT
    a.id AS activity_id,
    a.mmsi,
    a.ts,
    a.tscurrent,
    a.tsstop,
    a.tsout,
    a.longitude,
    a.latitude,
    a.curlongitude,
    a.curlatitude,
    a.sog,
    a.cog,
    a.cursog,
    a.curcog,
    a.rowcount,
    a.distance,
    a.navstatus,
    a.navstatusdesc,
    s."shipName" AS shipname,
    s."shipType" AS shiptype,
    s."shipTypeDesc" AS shiptypedesc,
    EXTRACT(EPOCH FROM (now() - a.tscurrent)) AS silence_seconds,
    CASE
        WHEN a.rowcount >= 5
             AND a.rowcount < 30
             AND a.tscurrent > now() - interval '3 days'
            THEN 'suspected_dark_after_slowdown'
        WHEN a.tscurrent <= now() - interval '3 days'
             OR COALESCE(a.cursog, a.sog, 0) > 3.0
            THEN 'possible_coverage_exit'
        ELSE 'low_evidence_ais_gap'
    END AS dark_reason
FROM (
    SELECT *,
           row_number() OVER (PARTITION BY mmsi ORDER BY ts DESC) AS rowcount_mmsi
    FROM public.ais_vesselslowmoveactivities
) a
INNER JOIN public.ais_static s ON s.mmsi = a.mmsi
WHERE a.rowcount_mmsi = 1
  AND a.tsout IS NULL
  AND a.tsstop IS NOT NULL
  AND a.rowcount < 30
  AND a.tscurrent IS NOT NULL
  AND a.tscurrent <= now() - interval '30 minutes'
  AND s."shipType" >= 70 AND s."shipType" < 90
ORDER BY a.tscurrent ASC;
```

Notes:

- `rowcount < 30` keeps the “went silent before confirmed stop” class from the markdown interpretation rules.
- Silence floor `30 minutes` aligns with `STALE_TRANSPONDER_MINUTES`.
- `possible_coverage_exit` is returned for research, but ops UIs may filter it out.
- Confirmed stops (`rowcount >= 30`) are excluded from dark candidates.

## MANTIS API surface (v1)

- Module: `PySTS/restapi/dark_vessels.py` (**implemented**)
- Endpoint: `GET /mantis/darkvessels` (Bearer) — query `includeCoverageExit=false` for ops-tight list
- Payload includes last known position, sog/cog, silence duration, `rowcount`, `darkReason`, and a simple confidence label.

This is a **read API** over existing backend tables. No new continuous processor is required for v1.

## Roadmap to improve reliability / accuracy

Prioritised research and engineering items for future iterations:

1. **Split `tsstop` semantics in the backend**  
   Add `tsdark` / `detection_type` (`confirmed_stop` | `suspected_dark` | `stale_mark`) so APIs and ML do not overload one field.

2. **Coverage / footprint model**  
   Maintain a polygon (or raster) of “areas we reliably receive AIS”.  
   Last fix near the outer boundary + high last sog → auto-label coverage exit.

3. **Time-based dark score instead of rowcount alone**  
   `rowcount` depends on AIS reporting rate. Prefer minutes spent sog≤3 before silence.

4. **Reappearance tracking**  
   If the same MMSI returns later far away with a plausible transit time, down-rank the earlier dark event (likely coverage gap, not dark ops).

5. **Cross-check with trajectory / movement activities**  
   Join `ais_vesselmovementactivities` and proximity clusters: dark near an STS cluster is higher interest than dark alone.

6. **Class / size filters and denylist**  
   Keep cargo/tanker focus; optionally exclude offshore supply / dredgers if they create noise.

7. **Minimum evidence thresholds for ops mode**  
   Example ops filter: `dark_reason = suspected_dark_after_slowdown` AND silence between 30 min and 72 h AND `rowcount >= 5`.

8. **Ground-truth labeling loop**  
   Export candidates to Parquet (same pattern as proximity ML export), manual label dark vs coverage-exit vs normal gap, then tune thresholds.

9. **Optional second backend task (only if needed)**  
   Only add a dedicated dark lifecycle table if you need continuous open/close history and alerts beyond on-demand SQL. For research and API v1, querying `ais_vesselslowmoveactivities` is enough.

## Working conclusion

- **Yes**, it is sensible to expose a dark-vessel endpoint from current data.
- Treat results as **suspected AIS disappearance after slow-down**.
- Explicitly model **SEA coverage exit** as a competing explanation.
- Improve accuracy by separating stop vs dark fields, adding a coverage footprint, and using time-based scores plus reappearance checks.


# Query Statement

When a specific vessel has been stopped for more than one hour, or its transponder may have been switched off for about 30 minutes:

```sql
SELECT *
FROM (
    SELECT *, row_number() OVER (PARTITION BY mmsi ORDER BY ts DESC) AS rowcount_mmsi
    FROM public.ais_vesselmovementactivities
)
WHERE tsout IS NULL
  AND (
        (tsstop IS NOT NULL AND tsstop <= now() - interval '1 HOURS')
        OR tscurrent <= now() - interval '30 MINUTES'
      )
  AND rowcount_mmsi = 1
ORDER BY curlongitude, curlatitude
```

`rowcount_mmsi = 1` keeps only the latest open activity per `mmsi`.

---

# Close Vessel Proximity Detection (`vesselproximitydetection.py`)

This section documents a related analysis goal that is **not** part of the slow-speed backend loop. It is implemented in `PySTS/backend/vesselproximitydetection.py`.

## Goal

Find **pairs of different vessels** whose **current** positions are within **30 meters** of each other, among vessels that appear stopped or stale, limited to **cargo** and **tanker** types for now.

This is separate from the **30 m confirmed-stop rule** in the slow-speed backend, which applies to **one vessel over time** (position jitter between consecutive AIS updates for the same `mmsi`).

| Use case | What 30 m means | Scope |
|----------|-----------------|--------|
| Slow-speed backend (`vesselslowspeeddetection.py`) | Same vessel barely moved between updates | Single `mmsi` |
| Close-pair script (`vesselproximitydetection.py`) | Two different vessels are near each other now | Pair of `mmsi` values |

## Source tables

| Table | Role |
|-------|------|
| `public.ais_vesselmovementactivities` | Open/stale movement activities (candidate vessels) |
| `public.ais_static` | Vessel name and type (`shipType`, `shipTypeDesc`) |

The slow-speed backend writes to `public.ais_vesselslowmoveactivities`. The pair script reads from `ais_vesselmovementactivities` (movement activities table used in operational queries).

## Input filter (candidate vessels)

The script loads vessels matching the query in [Query Statement](#query-statement) above, then:

1. **Inner join** `ais_static` on `mmsi` (vessels without static data are dropped).
2. **Vessel type filter** — same AIS codes as `st_app/app.py`:
   - Cargo: `shipType >= 70 AND shipType < 80`
   - Tanker: `shipType >= 80 AND shipType < 90`

Positions used for distance: `curlongitude`, `curlatitude` (latest known position on the activity row).

## Approach implemented

### What does not work: `LEAD` after sorting by lon/lat

An initial idea was to sort rows by `(curlongitude, curlatitude)` and compare each row only to the **next** row via `LEAD()`. That does **not** find all pairs within 30 m:

- Sort order is not geographic neighborhood order.
- Two vessels 20 m apart can be many rows apart in the sorted list.
- Most close pairs are missed; some reported pairs are not actually close in space.

### What works: self-join with spherical distance

All unordered pairs are tested in DuckDB:

- Join `vessels a` to `vessels b` with `a.mmsi < b.mmsi` (each pair once, no self-pairs).
- Filter with `ST_Distance_Sphere(ST_Point(longitude, latitude), ...) < 30`.
- Use **`ST_Point(longitude, latitude)`** — same argument order as `vesselslowspeeddetection.py` (longitude first, then latitude).

If the candidate set grows very large (thousands+), add grid bucketing before the join; for typical stopped-vessel counts, the full self-join is sufficient.

## Script location and how to run

- **File:** `PySTS/backend/vesselproximitydetection.py`

**Detection (continuous loop):**

```bash
cd PySTS
python backend/vesselproximitydetection.py
```

**ML export (Parquet, one-shot):** see [ML Parquet export guide](#ml-parquet-export-guide).

```bash
cd PySTS
python backend/vesselproximitydetection.py export
```

## Output

Logged via `logging` (not `print`):

- Each cycle logs opened / updated / closed cluster counts.
- `log_cluster_summary()` logs a table for observations touched in that cycle (duration, score, MMSI list).
- If none: no cluster rows updated; stale open clusters may still be closed.

Threshold constant in script: `MAX_DISTANCE_M = 30.0`.

## Configuration knobs

| Constant / filter | Current value | Notes |
|-------------------|---------------|--------|
| `MAX_DISTANCE_M` | `30.0` | Pair proximity threshold in meters |
| `LOOP_INTERVAL_SECONDS` | `30` | Main loop sleep between detection cycles |
| `CLOSE_GRACE_SECONDS` | `60` | Close open cluster after this many seconds not seen (~2 missed cycles) |
| `DETECTION_VERSION` | `2.0` | Stored on each observation |
| Stopped / stale SQL | See [Query Statement](#query-statement) | 1 h after `tsstop`, or 30 min since `tscurrent` |
| Vessel types | Cargo + tanker only | Change `shipType` range in `vesselproximitydetection.py` to widen |
| Latest row per MMSI | `rowcount_mmsi = 1` | Avoid duplicate activities per vessel |

## Operational caveats

- **AIS jitter:** positions within 25–35 m may be noise; consider 50 m if too many false pairs appear.
- **Time alignment:** the SQL mixes “confirmed stopped > 1 h” and “no AIS for 30 min”; two vessels can be spatially close but not both “live” at the same time — tighten `tscurrent` rules if that matters.
- **Missing static data:** `inner` join on `ais_static` excludes vessels with no static row.
- **Performance:** for very large candidate sets, move filtering into PostgreSQL with `ST_DWithin` on a geography column and a spatial index, or use grid cells in DuckDB before the distance filter.

## Change history (reference)

| Date | Change |
|------|--------|
| 2026-05 | Documented close-pair analysis; replaced `LEAD`/sort approach with DuckDB self-join; added cargo/tanker filter via `ais_static`; aligned `ST_Point` to `(longitude, latitude)` |
| 2026-05 | Implemented `backend/vesselproximitydetection.py`: SQLModel tables, pair clustering, BigDataCloud reverse geocode, DB inserts |
| 2026-05 | Open-cluster upsert lifecycle: duration tracking, suspicion score, 30 s loop, close grace |
| 2026-05 | Code review: fixed max spread for 3+ vessel clusters, MMSI/coordinate dedupe, lifecycle hardening; checkpoints documented below |
| 2026-05 | Parquet ML export via `python backend/vesselproximitydetection.py export` |

---

# PostgreSQL Storage for Proximity Clusters (AI Dataset)

When collecting observations for an AI model, **do not store results as a flat pair table** (`mmsi_a`, `mmsi_b`, …). That shape breaks when three or four vessels sit within 30 m of each other.

## Why a pair table is the wrong primary model

If vessels **A, B, C** are all within 30 m, a pair table creates **three rows**:

| mmsi_a | mmsi_b |
|--------|--------|
| A | B |
| A | C |
| B | C |

Problems for dataset collection:

- One real-world event becomes multiple training rows (label leakage / duplicate bias).
- Group size is implicit; hard to filter “clusters of 3+”.
- Cluster-level features (centroid, max spread, cargo+tanker mix) must be recomputed every time.
- Anomaly labels would need to be duplicated across rows or kept inconsistent.

**Use pairs only as an intermediate step in detection**, not as the main storage unit.

## Recommended model: observation (cluster) + members

Store **one row per proximity cluster per detection run**, and **one row per vessel in that cluster**.

```text
ais_vessel_proximity_observation   (1 row = 1 cluster event at 1 time)
        │
        └── ais_vessel_proximity_member   (N rows, N = vessel_count)
        └── ais_vessel_proximity_edge     (optional, pairwise distances inside cluster)
```

This naturally supports 2, 3, 4, or more vessels without schema changes.

### Table 1: `ais_vessel_proximity_observation`

Cluster-level facts, labels, and locality — the primary unit for ML.

| Column | Type | Purpose |
|--------|------|---------|
| `id` | `BIGSERIAL PK` | Observation id |
| `detected_at` | `TIMESTAMPTZ NOT NULL` | When the scan ran / event was captured |
| `city` | `VARCHAR` | City from reverse geocode (may be empty offshore) |
| `locality` | `VARCHAR` | Area / locality name (human-readable) |
| `locality_code` | `VARCHAR` | Stable code for joins and filtering (e.g. `SG_SINGAPORE`, `MY_PORT_KLANG`) |
| `zone_id` | `BIGINT NULL` | Optional FK to an existing zone table if available |
| `centroid_longitude` | `DOUBLE PRECISION` | Mean lon of member positions |
| `centroid_latitude` | `DOUBLE PRECISION` | Mean lat of member positions |
| `vessel_count` | `INT NOT NULL` | Number of vessels in cluster (≥ 2) |
| `max_internal_distance_m` | `DOUBLE PRECISION` | Largest pairwise distance inside cluster |
| `threshold_m` | `DOUBLE PRECISION` | Proximity rule used (default `30`) |
| `cargo_count` | `INT` | Optional aggregate feature |
| `tanker_count` | `INT` | Optional aggregate feature |
| `is_anomaly` | `BOOLEAN NULL` | **Label**: `NULL` = unreviewed, `TRUE`/`FALSE` = labeled |
| `anomaly_source` | `VARCHAR NULL` | `manual`, `rule`, `model` |
| `anomaly_notes` | `TEXT NULL` | Reviewer or rule explanation |
| `labeled_at` | `TIMESTAMPTZ NULL` | When label was set |
| `labeled_by` | `VARCHAR NULL` | Reviewer or system id |
| `detection_version` | `VARCHAR NULL` | Script/query version for reproducibility |
| `created_at` | `TIMESTAMPTZ` | Insert time |

Suggested indexes: `(detected_at)`, `(locality_code)`, `(is_anomaly)`, `(vessel_count)`.

### Table 2: `ais_vessel_proximity_member`

Per-vessel snapshot at detection time.

| Column | Type | Purpose |
|--------|------|---------|
| `id` | `BIGSERIAL PK` | |
| `observation_id` | `BIGINT FK` | Parent cluster |
| `mmsi` | `BIGINT NOT NULL` | Vessel id |
| `ship_name` | `VARCHAR` | Snapshot at detection |
| `ship_type` | `INT` | AIS type code |
| `ship_type_desc` | `VARCHAR` | |
| `longitude` | `DOUBLE PRECISION` | Position used (`curlongitude`) |
| `latitude` | `DOUBLE PRECISION` | Position used (`curlatitude`) |
| `activity_id` | `BIGINT NULL` | Optional link to `ais_vesselmovementactivities.id` |
| `tscurrent` | `TIMESTAMPTZ NULL` | Last AIS time on activity row |
| `tsstop` | `TIMESTAMPTZ NULL` | Stop / dark timestamp if present |
| `distance_to_centroid_m` | `DOUBLE PRECISION` | Per-vessel spatial feature |

Unique constraint: `(observation_id, mmsi)`.

### Table 3 (optional): `ais_vessel_proximity_edge`

Pairwise distances **inside** a cluster. Useful for ML features; not the primary entity.

| Column | Type |
|--------|------|
| `observation_id` | `BIGINT FK` |
| `mmsi_a` | `BIGINT` (`mmsi_a < mmsi_b`) |
| `mmsi_b` | `BIGINT` |
| `distance_m` | `DOUBLE PRECISION` |

Primary key: `(observation_id, mmsi_a, mmsi_b)`.

## Detection pipeline (clustering from pairs)

Keep the current **pair-finding** step, then **group into clusters**:

1. Load candidate vessels (stopped/stale, cargo/tanker filter).
2. Compute all pairs with distance `< threshold_m` (DuckDB self-join, as in `vesselproximitydetection.py`).
3. Build an undirected graph: nodes = `mmsi`, edges = close pairs.
4. Take **connected components** (union-find or graph library) → each component is one cluster.
5. For each cluster with `vessel_count >= 2`:
   - Insert `ais_vessel_proximity_observation`.
   - Insert one `ais_vessel_proximity_member` per vessel.
   - Optionally insert `ais_vessel_proximity_edge` for every pair in that cluster.

Example: A–B 12 m, B–C 18 m, A–C 25 m → **one** observation with `vessel_count = 3`, not three pair rows.

## Locality (city / port)

Pick one primary method; store both code and display name:

| Method | When to use |
|--------|-------------|
| **Zone polygon join** | Best if you already have port / restrict zones (e.g. `ais_vesselinrestrictzone` or a port boundary table). Point-in-polygon on cluster centroid. |
| **Lookup bbox table** | Simple: `locality_regions(name, code, min_lon, max_lon, min_lat, max_lat)`. |
| **Reverse geocode** | Flexible but depends on external API; cache results in `locality_code`. |

Recommendation: **zone/bbox join in PostgreSQL or DuckDB** for batch jobs; store `locality` + `locality_code` on the observation row. The current implementation uses the free [BigDataCloud reverse geocode client API](https://api.bigdatacloud.net/data/reverse-geocode-client) on cluster centroid (`city`, `locality`, `locality_code` derived from response). Results are cached per run; cache keys use centroid coordinates rounded to **4 decimal places** (~11 m).

## Anomaly label (for supervised AI)

Design labels at **observation (cluster) level**, not per pair:

| Field | Usage |
|-------|--------|
| `is_anomaly = NULL` | Collected, not yet reviewed — normal for new data |
| `is_anomaly = TRUE` | Confirmed suspicious (e.g. STS transfer, dark-ship rendezvous) |
| `is_anomaly = FALSE` | Reviewed benign (e.g. bunkering, anchorage congestion) |
| `anomaly_source` | Tracks whether label came from human review, rule engine, or model |
| `anomaly_notes` | Short reason for training audit |

Start with **append-only** inserts (`is_anomaly` NULL). Label in a separate review step or UI. Avoid overwriting historical rows so the dataset keeps a clear timeline.

## Insert strategy and open-cluster lifecycle

The detector runs every **30 seconds** and uses **one open row per cluster** (option 3), not append-only duplicates.

### Cluster identity

- `cluster_signature` = sorted MMSI list joined by `_` (e.g. `123_456_789`)
- If the MMSI set changes (e.g. pair becomes trio), the old signature stops updating and closes after the grace period; the new signature opens a new observation

### Lifecycle fields (observation)

| Field | Purpose |
|-------|---------|
| `first_detected_at` | When the cluster was first seen |
| `last_detected_at` | Updated every run while the cluster is still present |
| `duration_seconds` | `last_detected_at - first_detected_at` while open; frozen on close |
| `is_open` | `TRUE` while active; `FALSE` when closed |
| `closed_at` / `close_reason` | Set when cluster ends (`not_seen` after grace period) |
| `run_count` | Number of detection cycles that confirmed this cluster |
| `suspicion_score` | Recomputed each update; rises with duration and cluster size |

### Suspicion score (tunable)

```python
score = 2.0 * log1p(duration_hours) + 1.0 * (vessel_count - 1) + 1.5 * mixed_cargo_tanker
```

Longer proximity → higher score. Use as a **hint**; ground truth remains `is_anomaly` for ML.

### Close grace period

- `CLOSE_GRACE_SECONDS = 60` (~2 missed runs at 30 s)
- Avoids closing on brief AIS jitter or a single missed cycle
- Open clusters not seen in the current run are closed once `now - last_detected_at >= 60 s`

### Constants

| Constant | Value |
|----------|-------|
| `LOOP_INTERVAL_SECONDS` | 30 |
| `CLOSE_GRACE_SECONDS` | 60 |
| `MAX_DISTANCE_M` | 30 |
| `DETECTION_VERSION` | 2.0 |

## Insert strategy for ML datasets (export)

| Strategy | Pros | Cons |
|----------|------|------|
| **Open-cluster lifecycle (current)** | One row per cluster event; final duration on close | Must wait for `is_open = FALSE` or filter open rows explicitly |
| **Parquet export script** | Ready for pandas / scikit-learn / PyTorch | Requires `pyarrow` and labeled rows for supervised training |

For AI training, export **closed** observations (or open ones with `duration_seconds` above a threshold). Each row is one cluster event with a final duration — no need to deduplicate 30 s snapshots.

Closed events can still be filtered at export time (e.g. minimum duration, locality, labeled only).

---

# ML Parquet export guide

Export proximity cluster data from PostgreSQL to **Parquet** files for machine learning. Implemented in `PySTS/backend/vesselproximitydetection.py` (same module as the detector).

## Prerequisites

1. **Data in PostgreSQL** — run the detector first so `ais_vesselproximityobservation`, `ais_vesselproximitymember`, and `ais_vesselproximityedge` are populated:

   ```bash
   cd PySTS
   python backend/vesselproximitydetection.py
   ```

   Stop with `Ctrl+C` when enough clusters have been collected and closed.

2. **Python package** — Parquet write requires `pyarrow`:

   ```bash
   pip install pyarrow
   ```

3. **Working directory** — run commands from the `PySTS` folder (paths below assume that).

## Run export (basic command)

```bash
cd PySTS
python backend/vesselproximitydetection.py export
```

| Command | What it does |
|---------|----------------|
| `python backend/vesselproximitydetection.py` | **Detection loop** — collects/updates clusters every 30 s (not export) |
| `python backend/vesselproximitydetection.py export` | **One-shot export** — writes Parquet files and exits |

## CLI options (all flags)

Options are passed **after** `export`. Flags can be combined in any order.

| Option | Argument | Default | Effect |
|--------|----------|---------|--------|
| *(none)* | — | — | Export with defaults below |
| `--output-dir` | `<path>` | `backend/data/ml_export/` | Directory for output `.parquet` files |
| `--labeled-only` | — | off | Only rows where `is_anomaly IS NOT NULL` (supervised / reviewed set) |
| `--include-open` | — | off | Include **open** clusters (`is_open = TRUE`); default exports **closed** only |
| `--min-duration` | `<seconds>` | `0` | Only clusters with `duration_seconds >=` value (e.g. `300` = 5 minutes) |
| `<path>` (positional) | `<path>` | — | Same as `--output-dir` if a single path is given without a flag name |

### Default export behavior (no flags)

| Setting | Default value |
|---------|----------------|
| Output directory | `PySTS/backend/data/ml_export/` |
| Cluster status | **Closed only** (`is_open = FALSE`) |
| Labels | **All** rows (labeled and unlabeled) |
| Minimum duration | `0` (no duration filter) |

Defaults are defined in code as:

| Constant | Value |
|----------|-------|
| `EXPORT_DIR` | `backend/data/ml_export` (relative to `vesselproximitydetection.py`) |
| `EXPORT_CLOSED_ONLY_DEFAULT` | `True` |
| `EXPORT_LABELED_ONLY_DEFAULT` | `False` |
| `MIN_EXPORT_DURATION_SECONDS` | `0.0` |

## Output files

Each export writes **three** Parquet files into the output directory:

| File | Rows | Description |
|------|------|-------------|
| `proximity_observations.parquet` | 1 per cluster | Main ML table: `duration_seconds`, `suspicion_score`, `is_anomaly`, locality, `mmsi_list`, etc. |
| `proximity_members.parquet` | 1 per vessel | Vessel snapshots; join to observations on `observation_id` |
| `proximity_edges.parquet` | 1 per pair in cluster | Distances between MMSI pairs; join on `observation_id` |

A log line reports row counts, for example:

```text
ML Parquet export: 42 observations, 89 members, 67 edges -> .../backend/data/ml_export
```

## Example commands

```bash
cd PySTS

# 1) Default — closed clusters, all labels, default folder
python backend/vesselproximitydetection.py export

# 2) Custom output folder (flag form)
python backend/vesselproximitydetection.py export --output-dir ./datasets/proximity_v1

# 3) Custom output folder (positional path)
python backend/vesselproximitydetection.py export ./datasets/proximity_v1

# 4) Supervised learning — only human-labeled rows
python backend/vesselproximitydetection.py export --labeled-only

# 5) Ignore short events — at least 5 minutes together
python backend/vesselproximitydetection.py export --min-duration 300

# 6) Include clusters still open (duration still increasing)
python backend/vesselproximitydetection.py export --include-open

# 7) Combined — labeled, min 10 min, custom path
python backend/vesselproximitydetection.py export \
  --labeled-only \
  --min-duration 600 \
  --output-dir ./datasets/proximity_labeled_10min

# 8) Closed + min duration (typical training set before labeling)
python backend/vesselproximitydetection.py export --min-duration 300
```

## Labeling before `--labeled-only`

Export does **not** label data. Set labels in PostgreSQL, then export:

```sql
UPDATE ais_vesselproximityobservation
SET is_anomaly = TRUE,
    anomaly_source = 'manual',
    anomaly_notes = 'Possible STS',
    labeled_at = now(),
    labeled_by = 'your_name'
WHERE id = 123;
```

Then:

```bash
python backend/vesselproximitydetection.py export --labeled-only
```

Use `is_anomaly = TRUE` for suspicious, `FALSE` for benign. `suspicion_score` is a hint only, not ground truth.

## Load Parquet in Python

```python
import pandas as pd

base = "backend/data/ml_export"

obs = pd.read_parquet(f"{base}/proximity_observations.parquet")
members = pd.read_parquet(f"{base}/proximity_members.parquet")
edges = pd.read_parquet(f"{base}/proximity_edges.parquet")

# Supervised target (after labeling)
# obs["is_anomaly"]  -> True, False, or NaN

# Example: join members onto observations
# members.merge(obs[["id", "cluster_signature"]], left_on="observation_id", right_on="id")
```

## Programmatic export (without CLI)

From another script or notebook:

```python
from pathlib import Path
from backend.vesselproximitydetection import get_pgEngine, export_ml_dataset_parquet

engine = get_pgEngine()
result = export_ml_dataset_parquet(
    engine=engine,
    output_dir=Path("./datasets/my_export"),
    closed_only=True,
    labeled_only=False,
    min_duration_seconds=300.0,
)

print(result["paths"])
print(result["observations_rows"], result["members_rows"], result["edges_rows"])
```

## Troubleshooting export

| Issue | Action |
|-------|--------|
| `ImportError` / pyarrow | Run `pip install pyarrow` |
| 0 observation rows | Run detector longer; try `--include-open` or lower `--min-duration` |
| 0 rows with `--labeled-only` | Label rows in DB first (`is_anomaly` not null) |
| Empty members/edges | Normal if no clusters match filters; check observation count first |

## Example DDL (PostgreSQL)

```sql
CREATE TABLE public.ais_vessel_proximity_observation (
    id                      BIGSERIAL PRIMARY KEY,
    detected_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    locality                VARCHAR(100),
    locality_code           VARCHAR(50),
    zone_id                 BIGINT,
    centroid_longitude      DOUBLE PRECISION,
    centroid_latitude       DOUBLE PRECISION,
    vessel_count            INT NOT NULL CHECK (vessel_count >= 2),
    max_internal_distance_m DOUBLE PRECISION,
    threshold_m             DOUBLE PRECISION NOT NULL DEFAULT 30,
    cargo_count             INT,
    tanker_count            INT,
    is_anomaly              BOOLEAN,
    anomaly_source          VARCHAR(30),
    anomaly_notes           TEXT,
    labeled_at              TIMESTAMPTZ,
    labeled_by              VARCHAR(100),
    detection_version       VARCHAR(20),
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE public.ais_vessel_proximity_member (
    id                      BIGSERIAL PRIMARY KEY,
    observation_id          BIGINT NOT NULL
        REFERENCES public.ais_vessel_proximity_observation(id) ON DELETE CASCADE,
    mmsi                    BIGINT NOT NULL,
    ship_name               VARCHAR(255),
    ship_type               INT,
    ship_type_desc          VARCHAR(255),
    longitude               DOUBLE PRECISION NOT NULL,
    latitude                DOUBLE PRECISION NOT NULL,
    activity_id             BIGINT,
    tscurrent               TIMESTAMPTZ,
    tsstop                  TIMESTAMPTZ,
    distance_to_centroid_m  DOUBLE PRECISION,
    UNIQUE (observation_id, mmsi)
);

CREATE TABLE public.ais_vessel_proximity_edge (
    observation_id BIGINT NOT NULL
        REFERENCES public.ais_vessel_proximity_observation(id) ON DELETE CASCADE,
    mmsi_a         BIGINT NOT NULL,
    mmsi_b         BIGINT NOT NULL,
    distance_m     DOUBLE PRECISION NOT NULL,
    PRIMARY KEY (observation_id, mmsi_a, mmsi_b),
    CHECK (mmsi_a < mmsi_b)
);

CREATE INDEX idx_prox_obs_detected_at ON public.ais_vessel_proximity_observation (detected_at);
CREATE INDEX idx_prox_obs_locality ON public.ais_vessel_proximity_observation (locality_code);
CREATE INDEX idx_prox_obs_anomaly ON public.ais_vessel_proximity_observation (is_anomaly);
CREATE INDEX idx_prox_member_mmsi ON public.ais_vessel_proximity_member (mmsi);
CREATE INDEX idx_prox_member_obs ON public.ais_vessel_proximity_member (observation_id);
```

Follow existing PySTS conventions: SQLModel models in a backend script, `create_db_and_tables()`, lowercase table names matching other `ais_*` tables.

**Implementation:** `PySTS/backend/vesselproximitydetection.py` (models, detection, insert, and entry point).

## Example ML export query

One labeled row per cluster with member list and spread:

```sql
SELECT
    o.id,
    o.detected_at,
    o.locality,
    o.locality_code,
    o.vessel_count,
    o.max_internal_distance_m,
    o.cargo_count,
    o.tanker_count,
    o.is_anomaly,
    o.anomaly_source,
    array_agg(m.mmsi ORDER BY m.mmsi) AS mmsi_list,
    array_agg(m.ship_type_desc ORDER BY m.mmsi) AS type_list
FROM public.ais_vessel_proximity_observation o
JOIN public.ais_vessel_proximity_member m ON m.observation_id = o.id
WHERE o.is_anomaly IS NOT NULL   -- labeled only
GROUP BY o.id;
```

## Summary

| Question | Recommendation |
|----------|----------------|
| 3–4 vessels in one “pair”? | Store as **one cluster** (`vessel_count = 3` or `4`), not multiple pair rows |
| Primary table | `ais_vessel_proximity_observation` |
| Vessel details | `ais_vessel_proximity_member` |
| Pair distances | Optional `ais_vessel_proximity_edge` |
| Timestamp | `detected_at` on observation; member `tscurrent` / `tsstop` for context |
| City / locality | `locality` + `locality_code` from zone or bbox join on centroid |
| Anomaly | `is_anomaly` + `anomaly_source` + `anomaly_notes` on observation; start as `NULL` |
| Detection logic | Pairs → graph → connected components → insert cluster + members |

---

# Proximity Detection — Checkpoints and Verification

Reference for developers maintaining `vesselproximitydetection.py`. Use after deploy, code changes, or when tuning constants.

## Expected behavior (working as designed)

| Checkpoint | Expected result |
|--------------|-----------------|
| Same MMSI cluster still present each cycle | **One** open row updated; `last_detected_at`, `duration_seconds`, `suspicion_score`, and `run_count` increase; **no** duplicate observation rows every 30 s |
| New MMSI combination appears | New open observation with new `cluster_signature`; members and edges inserted |
| Cluster absent from current run | Open row stays open until `CLOSE_GRACE_SECONDS` elapsed since `last_detected_at`, then `is_open = FALSE`, `close_reason = 'not_seen'`, duration frozen |
| MMSI set changes (pair → trio) | Old signature stops updating → closes after grace; new signature opens a **new** observation |
| 3+ vessels connected via chain (A–B, B–C) | **One** cluster; `vessel_count >= 3`; not multiple pair-only rows |
| `cluster_signature` | Sorted MMSI joined by `_` (e.g. `636012345_636098765`) |
| Geocode cache | Centroid rounded to **4 decimal places** (~11 m); same area reuses API result within one run |
| `create_db_and_tables()` | Called once at startup in `main()` only (not every 30 s cycle) |
| Logging | All cycle output via `logging`; no `print()` for cluster summary |

## Bugs found and fixed (code review 2026-05)

| Issue | Symptom | Fix in code |
|-------|---------|-------------|
| **`max_internal_distance_m` understated** | For 3+ vessels linked A–B–C, spread could show only max edge &lt; 30 m while A–C is farther | `_max_internal_distance_m()` computes true max pairwise distance among all members |
| **Duplicate MMSI in candidates** | Same vessel twice in one cluster; wrong `vessel_count` | `drop_duplicates(subset="mmsi")` on `ais_static` and final candidate `df` |
| **Null coordinates** | NaN centroid / broken DuckDB distances | `dropna(subset=["curlongitude", "curlatitude"])` after filters |
| **Missing `first_detected_at`** | Possible crash on legacy row update | Set `first_detected_at = detected_at` if `None` before duration calc |
| **SQL summary IDs** | Theoretical unsafe string join | Cast `observation_ids` to `int` before building `IN (...)` clause |
| **Migrations every cycle** | Unnecessary `ALTER TABLE` / legacy update every 30 s | `create_db_and_tables()` only in `main()` |

## Operational risks (not code bugs — monitor)

| Risk | Mitigation |
|------|------------|
| **Two script instances** | Run **one** process only; partial unique index on open `cluster_signature` can raise `IntegrityError` on race |
| **`CLOSE_GRACE_SECONDS` vs loop** | At 30 s loop and 60 s grace, clusters need 2 missed cycles to close; increase grace if clusters flicker |
| **Candidate filter OR logic** | Stopped &gt; 1 h **or** stale AIS 30 min — vessels may be close but not equally “live”; tighten SQL if needed |
| **Geocode API failure** | Cluster still saved; `city` / `locality` may be empty; check logs for warnings |
| **Offshore geocode** | API may return only `locality` (e.g. sea name), empty `city` — expected |
| **`suspicion_score`** | Heuristic for ranking only; ML ground truth is `is_anomaly` after human review |
| **No DB `UNIQUE (observation_id, mmsi)`** on members | Code dedupes MMSI; consider adding DB constraint for safety |
| **AIS jitter near 30 m** | False clusters possible; tune `MAX_DISTANCE_M` or minimum duration for export |

## PostgreSQL sanity checks

Run after starting the detector or after code changes.

### No duplicate open clusters per signature

```sql
SELECT cluster_signature, COUNT(*) AS open_count
FROM ais_vesselproximityobservation
WHERE is_open = TRUE
GROUP BY cluster_signature
HAVING COUNT(*) > 1;
```

**Pass:** zero rows.

### Open clusters show growing duration

```sql
SELECT
    id,
    cluster_signature,
    is_open,
    first_detected_at,
    last_detected_at,
    duration_seconds,
    run_count,
    suspicion_score,
    vessel_count,
    max_internal_distance_m
FROM ais_vesselproximityobservation
ORDER BY last_detected_at DESC NULLS LAST
LIMIT 20;
```

**Pass:** open rows have `last_detected_at` recent; `duration_seconds` and `run_count` increase over time for persistent clusters.

### Member count matches `vessel_count`

```sql
SELECT
    o.id,
    o.cluster_signature,
    o.vessel_count,
    COUNT(m.id) AS member_rows
FROM ais_vesselproximityobservation o
JOIN ais_vesselproximitymember m ON m.observation_id = o.id
WHERE o.is_open = TRUE
GROUP BY o.id, o.cluster_signature, o.vessel_count
HAVING COUNT(m.id) <> o.vessel_count;
```

**Pass:** zero rows.

### No duplicate MMSI per observation

```sql
SELECT observation_id, mmsi, COUNT(*) AS n
FROM ais_vesselproximitymember
GROUP BY observation_id, mmsi
HAVING COUNT(*) > 1;
```

**Pass:** zero rows.

### Closed clusters have final duration

```sql
SELECT id, cluster_signature, duration_seconds, close_reason, closed_at
FROM ais_vesselproximityobservation
WHERE is_open = FALSE
ORDER BY closed_at DESC
LIMIT 20;
```

**Pass:** `duration_seconds` populated; `close_reason` often `not_seen` or `legacy_append` for old data.

## Application log checkpoints

| Log message | Meaning |
|-------------|---------|
| `Proximity detection started (interval=...)` | `main()` loop running |
| `Opened cluster <signature>` | New open observation |
| `Updated cluster <signature>` | Existing open observation refreshed |
| `Closed cluster <signature> after ... unseen` | Grace period exceeded |
| `Clusters: N open/updated, M new, K closed` | End of successful cycle |
| `No cargo/tanker candidate vessels found` | Empty candidate set; stale clusters may still close |
| `No proximity clusters within ...` | Candidates exist but no pair &lt; 30 m |
| `Reverse geocode failed` | API error; cluster still stored without locality |
| `Proximity detection error` | Uncaught exception; loop continues after sleep |

## Pre-deploy checklist

- [ ] PySTS venv has `duckdb`, `pandas`, `sqlmodel`, network to PostgreSQL and BigDataCloud
- [ ] Only **one** instance of `vesselproximitydetection.py` running
- [ ] Tables created / migrated (`create_db_and_tables` on first start)
- [ ] Partial unique index exists: `idx_prox_obs_open_signature` where `is_open = TRUE`
- [ ] Constants reviewed: `MAX_DISTANCE_M`, `CLOSE_GRACE_SECONDS`, `LOOP_INTERVAL_SECONDS`
- [ ] Sanity SQL queries above return expected empty / growing results
- [ ] Logs show open/update/close without repeating errors every cycle

## ML export checkpoint

Use **closed** observations (or open with minimum `duration_seconds`) for training samples.

**Preferred:** run the built-in Parquet export — see [ML Parquet export guide](#ml-parquet-export-guide).

Manual SQL example (same filters as export):

```sql
SELECT
    o.id,
    o.cluster_signature,
    o.first_detected_at,
    o.closed_at,
    o.duration_seconds,
    o.suspicion_score,
    o.locality,
    o.locality_code,
    o.vessel_count,
    o.max_internal_distance_m,
    o.cargo_count,
    o.tanker_count,
    o.is_anomaly,
    array_agg(m.mmsi ORDER BY m.mmsi) AS mmsi_list
FROM ais_vesselproximityobservation o
JOIN ais_vesselproximitymember m ON m.observation_id = o.id
WHERE o.is_open = FALSE
  AND o.duration_seconds >= 300   -- example: at least 5 minutes
GROUP BY o.id
ORDER BY o.duration_seconds DESC;
```

Label `is_anomaly` in a separate review step; do not rely on `suspicion_score` alone as ground truth.

