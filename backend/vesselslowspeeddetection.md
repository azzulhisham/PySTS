# MANTIS backend detection — maintenance

Living document for **detection factors, formulas, and thresholds**.  
Update this file whenever a constant or formula in the three pipeline scripts (or the matching `restapi` filters) changes. Accuracy work is expected to be continuous.

**Last verified against code:** 2026-08-13

| MANTIS job | Pipeline | Writes | API |
| --- | --- | --- | --- |
| STS | `backend/vesselproximitydetection.py` | `ais_vesselproximityobservation` / `member` / `edge` | `restapi/sts_detection.py` |
| Dark vessels | `backend/vesselslowspeeddetection.py` | `ais_vesselslowmoveactivities` | `restapi/dark_vessels.py` |
| Illegal anchoring | `backend/vesselstrajectorydetection.py` | `ais_vesselmovementactivities` | `restapi/illegal_anchoring.py` |

Not MANTIS: `backend/vesselzone.py`, `backend/polygons.py`, `st_app/`.  
Product polygon / Excl rules: `restapi/README.md`.  
Repo map: `PySTS/readme.md`.

AIS silence is **disappearance from the feed**, not proof of intent. Labels must stay **suspected**.

---

## How to maintain this file

When you tune accuracy:

1. Change the constant or formula in the Python file.
2. Update the matching row in [Master knob table](#master-knob-table) and the section for that job.
3. Note the date and why (false positives / misses).
4. If pipeline and API both have a related knob (e.g. pair distance 30 m vs 35 m), update **both** rows and the [Layer mismatch](#layer-mismatch-do-not-ignore) table.

---

## Master knob table

All values below are **as implemented in code today**. Do not copy stale comments.

### Shared AIS type filter (API + proximity pipeline)

| Factor | Current value | Where |
| --- | --- | --- |
| Cargo | `shipType >= 70 AND shipType < 80` | proximity load; all three MANTIS API detectors |
| Tanker | `shipType >= 80 AND shipType < 90` | same |
| Combined Class-A large | `70 <= shipType < 90` | same |
| Static join | `INNER JOIN ais_static` — vessels without static are dropped | proximity + APIs |

The **slow-speed and trajectory pipelines do not filter shipType**. They write activities for any MMSI. Type filtering happens at proximity (STS pipeline) and at the API.

### Illegal anchoring — `vesselstrajectorydetection.py`

| Factor | Current value | Role |
| --- | --- | --- |
| AIS lookback | last **2 days** (`timedelta(days=2)`) | `get_ais_position_data` |
| Loop interval | **20 s** | `time.sleep(20)` |
| Low-speed gate | `sog <= 0.5` kn | open / update movement activity |
| High-speed gate | `sog > 0.5` kn | exit path |
| Confirmed stop `rowcount` | **`>= 20`** | set `tsstop` |
| Confirmed stop distance | **`< 30` m** between consecutive updates | set `tsstop` |
| `rowcount` increment | only if still open, `tsstop IS NULL`, newer `ts`, `distance > 0`, lon/lat changed | evidence of near-stationary updates |
| High-speed exit distance | **`>= 30` m** from stored activity position | may set `tsout` |
| Exit hysteresis `rowcount2` | decrement; `tsout` blocked while `rowcount2 >= -10` | avoid flicker close |
| Distance `ST_Point` order | **`(longitude, latitude)`** | same as slow-speed and proximity |

### Dark / slow-speed — `vesselslowspeeddetection.py`

| Factor | Current value | Role |
| --- | --- | --- |
| AIS lookback | last **3 days** | `get_ais_position_data` |
| Loop interval | **20 s** | `time.sleep(20)` |
| Low-speed gate | `sog <= 3.0` kn | open / update slow-move activity |
| High-speed gate | `sog > 3.0` kn | exit path |
| Confirmed stop `rowcount` | **`>= 30`** | set `tsstop` while still transmitting |
| Confirmed stop distance | **`< 30` m** | set `tsstop` |
| Stale / suspected dark | `STALE_TRANSPONDER_MINUTES = 30` | `tscurrent` older than 30 min |
| Stale min evidence | `STALE_TRANSPONDER_MIN_ROWCOUNT = 1` | must have at least this `rowcount` |
| Stale action | `UPDATE … SET tsstop = tscurrent` | same field as confirmed stop |
| High-speed exit distance | **`>= 100` m** | may set `tsout` |
| Exit hysteresis `rowcount2` | same pattern as trajectory (`>= -10` blocks `tsout`) | flicker control |
| Distance `ST_Point` order | **`(longitude, latitude)`** | GeoJSON order |
| Dark-stop projection | `estimate_latlng()` uses **fixed `d = 540` m** along `cog` | **not applied** in the upsert loop today |

### STS — `vesselproximitydetection.py`

| Factor | Current value | Role |
| --- | --- | --- |
| Pair / cluster distance | `MAX_DISTANCE_M = 30.0` | DuckDB `ST_Distance_Sphere` |
| Loop interval | `LOOP_INTERVAL_SECONDS = 30` | main loop |
| Close grace | `CLOSE_GRACE_SECONDS = 60` (~2 missed cycles) | close open cluster |
| Detection version | `DETECTION_VERSION = "2.0"` | stored on observation |
| Candidate: stopped | `tsstop IS NOT NULL AND tsstop <= now() - interval '1 HOURS'` | from **movement** table |
| Candidate: stale | `tscurrent <= now() - interval '30 MINUTES'` | OR with stopped |
| Latest activity only | `rowcount_mmsi = 1` | latest open row per MMSI |
| Still open | `tsout IS NULL` | |
| Ship type | `70 <= shipType < 90` | after join to `ais_static` |
| Cluster identity | sorted MMSI joined by `_` | `cluster_signature` |
| Min cluster size | **2** vessels (connected component) | |
| Suspicion score | see [Suspicion score formula](#suspicion-score-formula) | recomputed every update |
| Geocode | BigDataCloud on centroid, cache key rounded to **4 decimal places** (~11 m) | locality only |

Reads `ais_vesselmovementactivities` (trajectory output), **not** `ais_vesselslowmoveactivities`.

### API layer (what the frontend actually sees)

These knobs sit in `restapi/` and can hide pipeline results even when the backend is correct.

| Factor | Current value | File |
| --- | --- | --- |
| STS min score | `MIN_SUSPICION_SCORE = 4.5` | `sts_detection.py` |
| STS pair recompute | `MAX_DISTANCE_M = 35.0` (**not** 30) | `sts_detection.py` |
| STS clusters | `is_open = TRUE` | `sts_detection.py` |
| STS geometry | centroid in **parent** polygon; drop if in **Excl** hole | `sts_detection.py` |
| Illegal: stopped/stale | stop ≥ **1 h** OR `tscurrent` ≥ **30 min** stale | `illegal_anchoring.py` |
| Illegal: ship type | `70–89` | `illegal_anchoring.py` |
| Illegal: keep | Restricted Limit **or** parent polygon | `illegal_anchoring.py` |
| Illegal: drop | any Excl hole | `illegal_anchoring.py` |
| Dark: silence floor | `MIN_SILENCE_MINUTES = 30` | `dark_vessels.py` |
| Dark: confirmed-stop exclude | `CONFIRMED_STOP_ROWCOUNT = 30` (`rowcount < 30`) | `dark_vessels.py` |
| Dark: slow-down evidence | `MIN_SLOWDOWN_ROWCOUNT = 5` | `dark_vessels.py` |
| Dark: coverage-exit age | `COVERAGE_EXIT_DAYS = 3` | `dark_vessels.py` |
| Dark: ship type | `70–89` | `dark_vessels.py` |
| Dark: polygons | **label only, never drop**; Excl name preferred if in a hole | `dark_vessels.py` |
| Dark: Restricted Limit | **not** used for `polygonName` | `dark_vessels.py` |

---

## Layer mismatch (do not ignore)

| Topic | Pipeline | API | Effect |
| --- | --- | --- | --- |
| STS pair distance | **30 m** | **35 m** recompute | **Intentional for now** — API may pair slightly farther than the cluster builder |
| STS score | all open clusters scored | only **`suspicion_score >= 4.5`** | many clusters never reach the frontend |
| Slow-speed vs trajectory SOG | dark uses **3.0 kn**; movement uses **0.5 kn** | — | “slow” and “stopped” are different populations |
| Confirmed-stop rowcount | dark **30**; movement **20** | dark API excludes `rowcount >= 30` | do not mix the two tables’ `rowcount` meaning |
| Ship type | only proximity filters 70–89 | all MANTIS detectors filter 70–89 | pipeline tables still contain other types |

---

## 1. Illegal anchoring pipeline

**File:** `backend/vesselstrajectorydetection.py`  
**Table:** `public.ais_vesselmovementactivities`  
**API:** `GET /mantis/illegal-anchoring`

### Flow

1. Load `ais_position` for the last 2 days.
2. Split `sog <= 0.5` vs `sog > 0.5`.
3. Upsert open activities (`tsout IS NULL`) per MMSI.
4. Confirmed stop: `rowcount >= 20` **and** consecutive-update distance `< 30` m → set `tsstop`.
5. High-speed path: if now moving (`sog > 0.5`) and distance from stored position `>= 30` m, may set `tsout` (after `rowcount2` hysteresis).
6. Sleep 20 s.

### Interpret fields

| Fields | Meaning |
| --- | --- |
| `tsstop IS NULL`, `tsout IS NULL` | still monitoring |
| `tsstop` set, `rowcount >= 20` | treated as stopped while transmitting |
| `tsout` set | left / resumed speed |
| `rowcount` | near-stationary update count (not minutes) |

### API keep/drop (product)

Keep Class-A 70–89 whose last position is in Restricted Limit **or** a parent polygon.  
**Drop** if inside any Excl hole. Singapore East / Western OPL / South are parents, not a blanket exclusion.

---

## 2. Dark / slow-speed pipeline

**File:** `backend/vesselslowspeeddetection.py`  
**Table:** `public.ais_vesselslowmoveactivities`  
**API:** `GET /mantis/darkvessels`

### Flow

1. Load `ais_position` for the last **3 days**.
2. Split `sog <= 3.0` vs `sog > 3.0`.
3. Upsert open slow-move activities per MMSI (`tsout IS NULL`). Only **newer** `ts` than `tscurrent` may increment `rowcount`.
4. Confirmed stop while transmitting: `rowcount >= 30` **and** distance `< 30` m → `tsstop = AIS ts`.
5. Stale mark: open row, `tsstop IS NULL`, `tscurrent` older than **30 minutes**, `rowcount >= 1` → `tsstop = tscurrent` (suspected dark).
6. High-speed exit: `sog > 3` and distance **`>= 100` m** may set `tsout`.
7. Sleep 20 s.

### Interpret `tsstop` (two meanings)

| Condition | Meaning |
| --- | --- |
| `tsstop` set, `rowcount >= 30` | confirmed stop **while still sending AIS** — **not** a dark candidate |
| `tsstop` set, `rowcount < 30` | suspected transponder-off **before** confirmed stop — **primary dark class** |
| `tsout` set | left location / resumed speed — close the case |
| `tscurrent` many days old, last sog still high | often **coverage exit**, not intentional dark |

**Accuracy debt:** one field (`tsstop`) means both confirmed stop and suspected dark. A future `tsdark` / `detection_type` would improve APIs and ML.

### Estimated dark-stop location (helper only)

`estimate_latlng(lat, lon, cog)` projects **540 m** along course. Earth radius `R = 6371000` m.

```text
d = 540.0          # metres, currently fixed (not scaled by sog)
theta = radians(cog)
delta_lat = (d * cos(theta)) / R
delta_lon = (d * sin(theta)) / (R * cos(lat0))
```

**Not used** in the upsert loop. Keep last-known AIS lat/lon as truth.

If later scaled by speed (research, not code):

```text
linear:        d = 540 * (sog / 3.0)          # 3 kn → 540 m, 1 kn → 180 m
deceleration:  d = 540 * (sog / 3.0) ** 2     # 3 kn → 540 m, 1 kn → 60 m
```

### API dark reasons and confidence

Source: `restapi/dark_vessels.py`. Candidate must have `tsout IS NULL`, `tsstop IS NOT NULL`, `rowcount < 30`, silence ≥ 30 min, shipType 70–89, latest row per MMSI.

| `darkReason` | Rule | `confidence` |
| --- | --- | --- |
| `suspected_dark_after_slowdown` | `rowcount >= 5` and `< 30` and `tscurrent` within 3 days | `high` |
| `possible_coverage_exit` | `tscurrent` older than 3 days **or** last sog `> 3.0` | `low` |
| `low_evidence_ais_gap` | otherwise | `medium` |

Query `includeCoverageExit=false` drops `possible_coverage_exit`.

Polygons: **label only**. `polygonName` from `restapi/polygons.py` named areas; Excl name if inside a hole; never drop.

### Coverage-exit vs dark (ops framing)

1. **Higher interest** — already slow (`sog` low, `rowcount` climbing), then silence near last position.
2. **Coverage exit** — still moving near the edge of SEA AIS, then gone for days.
3. **Normal gap** — short silence without a slow-down story.

---

## 3. STS / proximity pipeline

**File:** `backend/vesselproximitydetection.py`  
**Tables:** `ais_vesselproximityobservation`, `ais_vesselproximitymember`, `ais_vesselproximityedge`  
**API:** `GET /mantis/sts-activities`

### Flow

1. Load stopped/stale **cargo+tanker** from `ais_vesselmovementactivities` (1 h after `tsstop` **or** 30 min stale `tscurrent`).
2. All unordered pairs with `ST_Distance_Sphere < 30` m (`mmsi_a < mmsi_b`).
3. Union-find connected components → one cluster (2, 3, 4+ vessels = **one** observation, not pair rows).
4. Upsert by `cluster_signature`. Update duration, `run_count`, `suspicion_score`.
5. If not seen for **60 s**, close (`close_reason = 'not_seen'`).
6. Sleep 30 s.

If the MMSI set changes (pair → trio), the old signature closes after grace; a new observation opens.

### Suspicion score formula

Implemented in `compute_suspicion_score()`:

```text
duration_hours      = duration_seconds / 3600
mixed_cargo_tanker  = 1  if cargo_count > 0 and tanker_count > 0  else 0

score = 2.0 * log1p(duration_hours)
      + 1.0 * (vessel_count - 1)
      + 1.5 * mixed_cargo_tanker

return round(score, 4)
```

`log1p(x) = ln(1 + x)`. At 0 h the duration term is 0. Duration raises score with diminishing returns.

| Duration | `log1p(hours)` | `× 2.0` |
| --- | --- | --- |
| 0 h | 0.00 | 0.00 |
| 1 h | 0.69 | 1.39 |
| 3 h | 1.39 | 2.77 |
| 6 h | 1.95 | 3.89 |
| 12 h | 2.56 | 5.13 |
| 24 h | 3.22 | 6.44 |

| Term | Weight | Meaning |
| --- | --- | --- |
| Duration | `2.0 * log1p(hours)` | longer together → more suspicious, not linear |
| Extra vessels | `1.0` per vessel beyond the first | trio scores +1 vs a pair |
| Cargo + tanker mix | `+ 1.5` | typical STS interest |

**Hint only.** Ground truth for ML is `is_anomaly` after review, not this score.

Worked example: 2 vessels, 1 cargo + 1 tanker, open 3 h  
`2.0 * 1.386 + 1.0 * 1 + 1.5 * 1 ≈ 5.27` → **above** API `minSuspicionScore` 4.5.  
Same pair at ~1 h: `2.0 * 0.693 + 1 + 1.5 ≈ 3.89` → **below** API cut, not shown.

### Cargo / tanker counts in a cluster

```text
cargo  = count of members with 70 <= shipType < 80
tanker = count of members with 80 <= shipType < 90
```

### API STS extra filters

- Open clusters with `suspicion_score >= 4.5`
- Centroid inside a **parent** polygon; **drop** Excl holes
- Pairs recomputed at **≤ 35 m**
- Only paired vessels returned

---

## Accuracy roadmap (planned enhancements)

Do not treat these as current behaviour.

1. Split `tsstop` vs `tsdark` / `detection_type` on slow-move rows.
2. Coverage / footprint polygon: last fix on the outer boundary + high sog → coverage exit.
3. Time-based dark score (minutes at `sog ≤ 3`) instead of `rowcount` alone (`rowcount` depends on AIS rate).
4. Reappearance: same MMSI later far away with plausible transit → down-rank earlier dark.
5. Cross-check dark near an open STS cluster (higher interest).
6. Optional extra type denylist (OSV, dredger) if they add noise.
7. Ops-tight dark: `suspected_dark_after_slowdown` AND silence 30 min–72 h AND `rowcount >= 5`.
8. Label loop: export candidates, mark dark vs coverage-exit vs gap, retune knobs.
9. Optionally align pipeline 30 m with API 35 m later. **API stays at 35 m for now.**

---

## Known issues

| Issue | Detail |
| --- | --- |
| `tsstop` overload | Confirmed stop and suspected dark share one field |
| AIS window | Full 2- or 3-day `ais_position` scan every cycle; a watermark would be cheaper |
| `rowcount` vs time | Reporting rate differs by vessel; same rowcount ≠ same minutes |
| Position jitter | Near-stationary GPS noise can inflate `rowcount` or false 30 m pairs |
| Trajectory `ST_Point` | **Fixed 2026-08-13** — now `(longitude, latitude)`, same as slow-speed and proximity |
| `estimate_latlng` | Fixed 540 m; unused in the loop; still prints to stdout |
| Two proximity processes | Unique open `cluster_signature` can raise `IntegrityError` |
| Geocode | Cluster is still saved if BigDataCloud fails; locality may be empty offshore |

---

## Proximity ML export (condensed)

```bash
cd PySTS
python backend/vesselproximitydetection.py          # detect loop
python backend/vesselproximitydetection.py export   # Parquet, then exit
```

Defaults: closed clusters only, all labels, `backend/data/ml_export/`, min duration 0.  
Needs `pyarrow`. Files: `proximity_observations.parquet`, `proximity_members.parquet`, `proximity_edges.parquet`.

Useful flags: `--labeled-only`, `--include-open`, `--min-duration <seconds>`, `--output-dir <path>`.

Label in SQL (`is_anomaly`, `anomaly_source`, `anomaly_notes`); do not treat `suspicion_score` as ground truth.

---

## Sanity checks (proximity)

No duplicate open signatures:

```sql
SELECT cluster_signature, COUNT(*) AS open_count
FROM ais_vesselproximityobservation
WHERE is_open = TRUE
GROUP BY cluster_signature
HAVING COUNT(*) > 1;
```

Member count must equal `vessel_count` (expect zero rows):

```sql
SELECT o.id, o.vessel_count, COUNT(m.id) AS member_rows
FROM ais_vesselproximityobservation o
JOIN ais_vesselproximitymember m ON m.observation_id = o.id
WHERE o.is_open = TRUE
GROUP BY o.id, o.vessel_count
HAVING COUNT(m.id) <> o.vessel_count;
```

---

## Change log (this document)

| Date | Note |
| --- | --- |
| 2026-08-13 | `vesselstrajectorydetection.py`: `ST_Point` corrected to `(longitude, latitude)`. API STS pair distance left at **35 m** by product decision. |
| 2026-08-13 | Rewritten as MANTIS maintenance spec. Values taken from current Python (not old comments). Stale rowcount is **1**, AIS dark lookback **3 days**, movement lookback **2 days**, movement SOG **0.5 kn**, confirmed-stop rowcount movement **20** / dark **30**. API STS **4.5 / 35 m** vs pipeline **30 m** recorded as intentional for now. |
| 2026-07 | Dark API research (reasons, coverage exit, cargo/tanker 70–89). |
| 2026-05 | Proximity cluster model, suspicion score, ML export. |
