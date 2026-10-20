# MANTIS detection — maintenance spec

Living document for **detection factors, formulas, and thresholds** across STS, dark vessels, illegal anchoring, and **position anomalies (spoofing)**.  
Formerly `vesselslowspeeddetection.md`. The pipeline script `vesselslowspeeddetection.py` is unchanged.  
Update this file whenever a constant or formula in the three pipeline scripts (or the matching `restapi` filters) changes. Accuracy work is expected to be continuous.

Work still open vs done: [`todo.md`](todo.md). Keep both files in step — if you finish an ingest, join key, or label, mark `todo.md` **and** record the current behaviour here.

**Last verified against code:** 2026-09-24

| MANTIS job | Pipeline | Writes | API |
| --- | --- | --- | --- |
| STS | `backend/vesselproximitydetection.py` | `ais_vesselproximityobservation` / `member` / `edge` | `restapi/sts_detection.py` |
| Dark vessels | `backend/vesselslowspeeddetection.py` | `ais_vesselslowmoveactivities` | `restapi/dark_vessels.py` |
| Illegal anchoring | `backend/vesselstrajectorydetection.py` | `ais_vesselmovementactivities` | `restapi/illegal_anchoring.py` |
| Position anomalies (Phase 1) | *(none yet — live ClickHouse scan)* | — | `restapi/spoofing.py` |
| Loitering | `backend/vesselloiteringdetection.py` | `ais_vesselloiteractivity` | `restapi/loitering.py` |
| Identity conflict | *(none — live Postgres scan)* | — | `restapi/identity_conflict.py` |

Identity ingest (not a detector): `backend/ofac_sdn_ingest.py` (SDN) and `backend/ofac_cons_ingest.py` (non-SDN). See [Identity enrichment](#identity-enrichment-not-a-fourth-detector).

Not MANTIS: `backend/vesselzone.py`, `backend/polygons.py`, `st_app/`.  
Product polygon / Excl rules: `restapi/README.md`.  
Repo map: `PySTS/readme.md`.

AIS silence is **disappearance from the feed**, not proof of intent. Labels must stay **suspected**.

---

## How to maintain this file

When you tune accuracy **or** add identity data:

1. Change the constant, formula, or ingest in the Python file.
2. Update the matching row in [Master knob table](#master-knob-table), the job section, or [Identity enrichment](#identity-enrichment-not-a-fourth-detector).
3. Note the date and why (false positives / misses / new list).
4. If pipeline and API both have a related knob (e.g. pair distance 30 m vs 35 m), update **both** rows and the [Layer mismatch](#layer-mismatch-do-not-ignore) table.
5. Tick or add the matching item in [`todo.md`](todo.md) so the work list and this spec do not drift.

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
| Identity conflict | same-hull groups of 2+ MMSIs; `detectedAt` = latest AIS `ts` | `identity_conflict.py` |
| Identity match | IMO + (name\|callsign\|dims) **or** name + (callsign\|dims) | same file |
| Spoofing Phase 1 | teleport implied speed; dedupe 1/MMSI/UTC day; `from`/`to` max 3 d | `spoofing.py` |
| Sanctions / bunker labels | OFAC **on API**; bunker **skipped** | `sanctionsMatch` on STS / dark / anchoring / identity-conflict / spoofing; `onBunkerRegister` later |

### Identity ingest (`ofac_sdn_ingest.py` / `ofac_cons_ingest.py` — not a detector loop)

| Factor | Current value | Role |
| --- | --- | --- |
| SDN source | official XML `https://www.treasury.gov/ofac/downloads/sdn.xml` | not the search website |
| Non-SDN source | official XML `https://www.treasury.gov/ofac/downloads/consolidated/consolidated.xml` | SSI, FSE, PLC, CAPTA, NS-MBS, NS-CMIC, … |
| Connection | same RDS `pnav` URL as the three pipeline jobs | one-shot write |
| Refresh | **once a day** (or cron `15 8 * * *` with `--list both`) | OFAC can change any US business day |
| Replace policy | `TRUNCATE` then reload **that list’s** tables only | SDN run does not wipe CONS, and vice versa |
| SDN tables | `ofac_sdn_entry`, `ofac_sdn_aka`, `ofac_sdn_identifier` | view `ofac_sdn_vessel` |
| Non-SDN tables | `ofac_cons_entry`, `ofac_cons_aka`, `ofac_cons_identifier` | view `ofac_cons_vessel` |
| History | `ofac_ingest_run` (`list_name` = `SDN` or `CONS`) | shared |
| Join key | **IMO first** (7 digits). MMSI second if present. Name / callsign last. | `*_entry.imo` / `.mmsi` |
| IMO in XML | `idType = Vessel Registration Identification`, value like `IMO 7406784` | parser stores `7406784` |
| Non-SDN vessels | **none in the 07/27/2026 file** (481 entities/individuals) | empty `ofac_cons_vessel` is valid |
| API attach | **done 2026-08-13** — labels on STS / dark / illegal-anchoring JSON; AIS scores unchanged | `restapi/sanctions.py` |

---

## Layer mismatch (do not ignore)

| Topic | Pipeline | API | Effect |
| --- | --- | --- | --- |
| STS pair distance | **30 m** | **35 m** recompute | **Intentional for now** — API may pair slightly farther than the cluster builder |
| STS score | all open clusters scored | only **`suspicion_score >= 4.5`** | many clusters never reach the frontend |
| Slow-speed vs trajectory SOG | dark uses **3.0 kn**; movement uses **0.5 kn** | — | “slow” and “stopped” are different populations |
| Confirmed-stop rowcount | dark **30**; movement **20** | dark API excludes `rowcount >= 30` | do not mix the two tables’ `rowcount` meaning |
| Ship type | only proximity filters 70–89 | all MANTIS detectors filter 70–89 | pipeline tables still contain other types |
| Sanctions | OFAC SDN + CONS tables in `pnav` | API adds `sanctionsMatch` / `matchConfidence`; **does not** change `suspicion_score` or dark `confidence` | listed ships sorted first; unmatched still returned |

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

### Performance (2026-09-09)

Same detection rules; cycle cost reduced only:

- **TXN1:** in-memory open-activity map (one load per cycle) instead of a SELECT per low-speed fix; dict updated on insert for same-cycle MMSI.
- **TXN2:** only open rows whose MMSI has `sog > 0.5` in the current AIS batch; no per-row `duckdb.register` (first high-speed fix per MMSI via groupby).
- **Logging:** per-row INFO → DEBUG; cycle summary at INFO (`seen/updated/inserted`, `open_rows/skipped_no_high_speed_fix/updates`).

**Not changed:** trajectory still applies nav/`tscurrent` updates even when `ts <= tscurrent` (only `rowcount` increment is gated on newer ts). No stale bulk UPDATE (trajectory never had one).

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

### Performance (2026-08-18)

Same detection rules; cycle cost reduced only:

- **TXN1:** in-memory open-activity map (one load per cycle) instead of a SELECT per low-speed fix; skip fixes with `ts <= tscurrent`; dict updated on insert for same-cycle MMSI.
- **TXN2 (stale):** bulk `UPDATE` marking suspected dark — unchanged.
- **TXN3:** only open rows whose MMSI has `sog > 3` in the current AIS batch; no per-row `duckdb.register` (first high-speed fix per MMSI via groupby).
- **Logging:** per-row INFO → DEBUG; cycle summary at INFO (`seen/skipped_not_newer/updated/inserted`, `open_rows/skipped_no_high_speed_fix/updates`).

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

1. Load stopped/stale **cargo+tanker** from `ais_vesselmovementactivities` (1 h after `tsstop` **or** 30 min stale `tscurrent`), joined to the **latest** `ais_static` row per MMSI.
2. All unordered pairs with `ST_Distance_Sphere < 30` m (`mmsi_a < mmsi_b`).
3. Drop pairs that are the **same hull** — see [Same-hull suppression](#same-hull-suppression).
4. Union-find connected components → one cluster (2, 3, 4+ vessels = **one** observation, not pair rows).
5. Upsert by `cluster_signature`. Update duration, `run_count`, `suspicion_score`.
6. If not seen for **60 s**, close (`close_reason = 'not_seen'`). An open cluster whose whole MMSI set is one hull closes immediately (`close_reason = 'same_vessel'`).
7. Sleep 30 s.

If the MMSI set changes (pair → trio), the old signature closes after grace; a new observation opens.

### Same-hull suppression

A re-flagged vessel keeps broadcasting its retired MMSI, so two identities of one ship sit metres apart and score as an STS. `is_same_vessel()` compares the latest `ais_static` row of each MMSI:

| Evidence | Normalisation |
| --- | --- |
| `imo` | must be 7 digits; `0`, repdigits and `PLACEHOLDER_IMOS` (`1234567`, `7654321`, `9999999`) rejected |
| `shipName`, `callsign` | `'@'` padding stripped, upper-cased, whitespace collapsed; unusable below `MIN_IDENTITY_TEXT_CHARS = 3` |
| dimensions | `(to_bow, to_stern, to_port, to_starboard)`; unusable when any part is null or all are zero |

Match rule: **IMO plus one** other agreeing attribute, **or** name plus callsign or dimensions. One shared field is never enough — IMO `1234567` alone is shared by 39 unrelated MMSIs in `ais_static`, so suppressing on a bare IMO match would hide real encounters. Same IMO with a different name, callsign *and* dimensions is left alone as ambiguous.

Only the pair **edge** is dropped. In a trio where two members are one hull but both sit near a genuine third vessel, the cluster survives with the retired identity still counted, so `vessel_count` stays inflated by one.

### Cleanup of unresolvable detections

`backend/cleanup_stale_detections.py` is a manual, dry-run-by-default script (`--apply` to write, `--stale-days`, default **7**):

| Step | Action |
| --- | --- |
| Open activities in both activity tables with `tscurrent` older than the cutoff | `tsout = tscurrent` |
| Open observations whose newest member `tscurrent` is older than the cutoff | close, `close_reason = 'stale_source'` |
| Open observations whose members are one hull | close, `close_reason = 'same_vessel'` (takes precedence) |

Needed because neither detector can close an activity on its own: `tsout` is only set when a **fresh** high-speed fix arrives, and each detector reads only the last 2–3 days of `ais_position`, so a vessel that stops transmitting drops out of its own input and stays open forever. Note this also removes those MMSIs from the dark-vessel feed, which requires `tsout IS NULL`.

### Suspicion score formula

Implemented in `compute_suspicion_score()`:

```text
duration_hours = duration_seconds / 3600
composition    = 1.0  if tanker_count >= 2          (a cargo ship may also be present)
                 0.5  elif cargo_count >= 2
                 0.0  otherwise                     (cargo beside one tanker, dredger beside one ship)

score = 2.0 * log1p(duration_hours)
      + 1.0 * (vessel_count - 1)
      + 1.5 * composition

return round(score, 4)
```

Cargo is ship type 70–79, tanker 80–89. Licensed dredgers count as neither. Before `2.3-tanker-pair-score` the composition term was `1` for any cargo + tanker mix and `0` otherwise; closed observations keep the score they were closed with.

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
| Composition | `+ 1.5` two or more tankers, `+ 0.75` two or more cargo ships, `0` otherwise | tanker-to-tanker is the illicit oil transfer pattern in Malaysian waters; cargo beside a tanker is usually bunkering |

**Hint only.** Ground truth for ML is `is_anomaly` after review, not this score.

Time for a two-vessel pair to reach the API `minSuspicionScore` 4.5: tanker + tanker ≈ 1.7 h, cargo + cargo ≈ 3.0 h, cargo + tanker ≈ 4.8 h.

Worked example: 2 tankers, open 3 h  
`2.0 * 1.386 + 1.0 * 1 + 1.5 * 1 ≈ 5.27` → **above** 4.5.  
1 cargo + 1 tanker, open 3 h: `2.0 * 1.386 + 1 + 0 ≈ 3.77` → **below** 4.5, not shown.

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

## 3b. Loitering — `vesselloiteringdetection.py`

**File:** `backend/vesselloiteringdetection.py`  
**Table:** `public.ais_vesselloiteractivity`  
**API:** `GET /mantis/loitering` (Postgres only; does not scan ClickHouse)

Track source is ClickHouse `pnav.ais_position`, cargo/tanker MMSIs from latest `ais_static` (ship type 70–89), one sample per MMSI per 5 minutes over the last **2 hours**. Loop default **15 minutes** (`loiter_loop_seconds`).

A vessel is loitering when, over that window:

| Factor | Value |
| --- | --- |
| Span | first sample to last ≥ **2 hours** |
| Samples | ≥ **8** |
| Radius from centre | **> 100 m** and ≤ **6 NM** (11,112 m) |
| Net progress | < **0.5 NM** and < **20%** of path length |

The 6 NM cap is the storage ceiling so both anchor swing and a wider wander are kept. The API narrows them. Tighter than 100 m stays a stop (trajectory job). One open row per MMSI (`tsout IS NULL`). The row closes when the ship leaves the pattern, or when the centre moves more than **2 NM**.

| Flag | Rule |
| --- | --- |
| `outside_anchorage` | Centre is not inside a parent anchorage (`restapi/polygons.py`; Excl holes ignored) |
| `near_restricted` | Distance from centre to the restricted-limit ring ≤ **5 NM** (0 if inside) |
| `before_sts` | An STS observation for this MMSI has `first_detected_at` between loiter start and **6 hours** after loiter end, and the STS centroid is within **5 NM** of the loiter centre |

With no `pattern`, the API returns open rows where at least one flag is true. `pattern=loitering` keeps a long track in a wide circle: path ≥ **2 NM**, net ≤ **0.5 NM**, net/path ≤ **0.10**, radius ≥ **max(length, 300 m)** and ≤ **6 NM**. `pattern=anchorSwing` keeps a short track in a hull-sized circle: radius **100 m** to **max(length, 300 m)**, path ≤ **1 NM**, net/path ≤ **0.20**. A pattern does not apply the flag filter unless `outsideAnchorage`, `nearRestricted`, or `beforeSts` is also set. `minPathM`, `maxPathM`, `maxNetM`, `maxNetOverPath`, `minRadiusM`, and `maxRadiusM` override the preset. An explicit radius replaces the hull-relative cut.

---

## 4. Position anomalies (spoofing) — API Phase 1

**File:** `restapi/spoofing.py`  
**API:** `GET /mantis/spoofing`  
**Product alias (docs only):** `GET /mantis/position-anomaly` — **not served**; keep `/mantis/spoofing` stable for frontend / MCP across phases.

There is **no backend pipeline job yet**. Each request scans ClickHouse `pnav.ais_position` live (same host as `restapi/timelineplayback.py`).

### Scope vs other endpoints

| Concern | Endpoint | Notes |
| --- | --- | --- |
| Impossible movement between fixes | **`/mantis/spoofing`** Phase 1 `teleport` | position layer |
| Same hull, two MMSIs (re-flag) | **`/mantis/identity-conflict`** | identity layer — do not merge into spoofing |
| AIS off after slow-down | **`/mantis/darkvessels`** | silence, not position jump |

Tug / patrol / fast-manoeuvring types (e.g. MMSIs that showed thousands of raw teleport pairs) are **not blocklisted**. They drop out naturally when the API restricts to **Class-A cargo/tanker** (`shipType` 70–89 from latest `ais_static`).

### Phase 1 — implemented (2026-08-18)

**Rule version:** `v1.1-teleport-snapback-filter`

1. Load latest `ais_static` per MMSI with `70 <= shipType < 90`.
2. ClickHouse: consecutive valid fixes on the same MMSI (`VALID_POSITION_SQL` — same as `/mantis/vessel-track`).
3. Flag a **fix pair** when all of:
   - `deltaSeconds` between **10** and **900** (avoid divide-by-tiny-Δt noise)
   - `distanceM >= 1000`
   - **(`distanceM >= 20_000` AND implied speed > 50 kn)** OR **implied speed > 80 kn**
   - Implied speed (kn) = `geoDistance(prev, curr) / deltaSeconds × 1.94384`
4. Drop a **one-point spike**. The outlier is ignored when the fix before it and the fix after it are still within **2 km** of each other and that gap is under **10%** of the jump. A false track that stays at the new position is kept. Same rule on `GET /mantis/spoofing/vessel-analysis`.
5. Inner join flagged pairs to cargo/tanker static only.
6. **Dedupe:** one row per `(mmsi, UTC calendar day of anomaly fix)` — keep the hit with **highest** `impliedSpeedKn`.
7. Attach OFAC labels (`sanctions.py`); listed vessels sorted first.
8. `detectedAt` / `lastSeenAt` = AIS time of the **anomaly fix** (second point) — not API wall clock.

Optional query params `from` / `to` (ISO 8601 UTC); same **3-day max** window as `GET /mantis/vessel-track` (`resolve_track_range`).

Response fields include `reason` (`teleport` | `high_speed`), `phase` (1), `detector` (`teleport`), `rawHitCount`, `filteredHitCount`, `anomalyCount`.

### Live probe (2026-09-03, last 3 days)

| Stage | Count |
| --- | ---: |
| Raw teleport pairs (all vessel types) | ~4,200 |
| After cargo/tanker filter | ~360 |
| After dedupe (1 / MMSI / UTC day) | **~63** |

Typical real hits: longitude sign errors (e.g. Singapore ↔ Atlantic on the same latitude band). Re-run: `python3 restapi/spoofing.py` from `PySTS/restapi` (needs network + DB deps).

### Performance note

Full 3-day window scan over ~33M valid fixes takes **~20–40 s** on current ClickHouse. Acceptable for occasional API use; **not** ideal for high-frequency polling without caching or a pre-computed table.

### Future phases (not implemented — see [`todo.md`](todo.md))

Keep the **same URL** (`/mantis/spoofing`). Extend response (`phase`, `detector`, `reason`) only.

| Phase | Goal | Likely work |
| --- | --- | --- |
| **2** | Pre-compute + persist | Background job (e.g. nightly) writing `ais_positionanomaly` or similar in Postgres; API reads table instead of full CH scan every call |
| **2** | MCP | `get_spoofing` in `mcp/client.py` + `mcp/server.py` |
| **3** | More detectors | Same endpoint: circle/spoof patterns, SOG vs implied speed mismatch, “anchored” nav status with large jumps |
| **3** | Cross-signals | Boost rank when teleport coincides with open STS cluster or OFAC-listed identity |
| **4** | Ops tuning | Threshold review from labelled exports; optional polygon filter; pagination |
| **—** | Out of scope for spoofing | Duplicate-MMSI / re-flag → stays on **`/mantis/identity-conflict`** |

**Not planned:** renaming the route to `/mantis/position-anomaly` (frontend already binds `/mantis/spoofing`).

---

## Identity enrichment (not a fourth detector)

AIS behaviour stays first. Sanctions lists are **labels on existing STS / dark / anchoring candidates**. They do not create a new MANTIS job and they are **not** a legal finding. Bunker / barge register is **skipped for now** (no source yet).

Open vs done for this work: [`todo.md`](todo.md) section **Now — obtain and join identity lists**.

### In pnav today (2026-08-13)

| Object | Purpose |
| --- | --- |
| `backend/ofac_sdn_ingest.py` | SDN XML → `ofac_sdn_*`. Same `pnav` connection as the three detectors. Default `--list sdn`. |
| `backend/ofac_cons_ingest.py` | Consolidated non-SDN XML → `ofac_cons_*`. Wrapper for `--list cons`. |
| `ofac_sdn_entry` / `ofac_cons_entry` | One record. Vessel fields plus normalized `imo` / `mmsi`. |
| `ofac_sdn_aka` / `ofac_cons_aka` | Aliases |
| `ofac_sdn_identifier` / `ofac_cons_identifier` | Raw OFAC IDs |
| `ofac_sdn_vessel` / `ofac_cons_vessel` | View: `sdn_type = 'Vessel'` |
| `ofac_ingest_run` | Each run’s list name, publish date, and row counts |
| `restapi/sanctions.py` | Join OFAC onto existing STS / dark / anchoring JSON. Lookup: `GET /mantis/sanctions`. |

SDN parse of the 08/07/2026 file: **19,199** entries, **1,524** vessels, **1,519** with IMO, **792** with MMSI.

Non-SDN parse of the 07/27/2026 file: **481** entries (118 individual, 363 entity), **0 vessels**. Empty `ofac_cons_vessel` is expected until OFAC lists a ship on a non-SDN program.

Run once:

```bash
python3 backend/ofac_sdn_ingest.py
python3 backend/ofac_cons_ingest.py
# or both:
python3 backend/ofac_sdn_ingest.py --list both
```

Daily cron (Singapore 08:15, after a typical US-afternoon OFAC publish):

```cron
15 8 * * * /usr/bin/python3 /opt/PySTS/backend/ofac_sdn_ingest.py --list both >> /var/log/ofac_ingest.log 2>&1
```

Check after a run:

```sql
SELECT list_name, COUNT(*) FROM ofac_sdn_entry GROUP BY list_name;
SELECT sdn_type, COUNT(*) FROM ofac_cons_entry GROUP BY sdn_type;
SELECT COUNT(*) FROM ofac_sdn_vessel;
SELECT COUNT(*) FROM ofac_cons_vessel;  -- may be 0
SELECT * FROM ofac_ingest_run ORDER BY ingested_at DESC LIMIT 5;
```

### Not in pnav / API yet

Matches `todo.md` still unchecked:

- Bunker / barge register — **skipped 2026-08-13**; no file yet
- UN / UK OFSI / EU files (after OFAC is stable)
- `onBunkerRegister` on API payloads (after a register exists)
- Brief the frontend developer on `sanctionsMatch` before UI work

### Join rules (API today — `restapi/sanctions.py`)

1. Match **IMO** on `ais_static` vs `ofac_sdn_vessel` (then `ofac_cons_vessel`) → `matchConfidence = confirmed`.
2. If the candidate has **no IMO**: MMSI vs OFAC MMSI → `possible`.
3. If the candidate **has an IMO that does not match**, do **not** fall back to MMSI.
4. No name / callsign match.
5. AIS keep/drop, `suspicion_score`, and dark `confidence` are unchanged. Listed rows are sorted first.
6. Unmatched vessels stay (`sanctionsMatch: false`, `matchConfidence: none`).
7. A listed vessel that is not dark / STS / stopped still does not appear just because it is listed.

AIS hull size on the same three list payloads: `toBow` / `toStern` / `toPort` / `toStarboard` from `ais_static` (Class A), else `ais_staticb` (Class B). `lengthM` = bow+stern, `beamM` = port+starboard. Missing offsets stay `null`. This does not change keep/drop.

---

## Accuracy roadmap (planned enhancements)

Do not treat these as current behaviour except item 1, which is in the API now.

1. **Done 2026-08-13.** OFAC labels on STS / dark / anchoring payloads. Bunker labels later.
2. Human review stays. Optional extra UN/UK/EU lists later.
3. Split `tsstop` vs `tsdark` / `detection_type` on slow-move rows.
4. Coverage / footprint polygon: last fix on the outer boundary + high sog → coverage exit.
5. Time-based dark score (minutes at `sog ≤ 3`) instead of `rowcount` alone (`rowcount` depends on AIS rate).
6. Reappearance: same MMSI later far away with plausible transit → down-rank earlier dark.
7. Cross-check dark near an open STS cluster (higher interest).
8. Optional extra type denylist (OSV, dredger) if they add noise. Do not wait on a bunker list.
9. Ops-tight dark: `suspected_dark_after_slowdown` AND silence 30 min–72 h AND `rowcount >= 5`.
10. Label loop: export candidates, mark dark vs coverage-exit vs gap, retune knobs.
11. Optionally align pipeline 30 m with API 35 m later. **API stays at 35 m for now.**

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

## Locked design — unexplained STS notification (not built)

Agreed 2026-10-09. Do not implement a different shape without updating this section.

The notification is an **unexplained pair that still needs a person**. The machine does not set `is_anomaly`. Training uses only rows a person has classed.

1. **Proximity candidates.** Types **70–89** can start a pair. Also admit an MMSI or IMO that is on the dredge-licence cache (later, the bunker list), and only when that vessel is within **30 m** of a cargo or tanker. Do not add a vessel just because of its type when the type is fishing (**30**), towing (**31**, **32**), tug (**52**), or passenger (**60–69**). Leave pleasure, pilot, and high-speed craft out as well. The licence cache is read from `jkptg_prod.shipprojects` on `marineai` every **12 hours**, then matched in process. No cross-database join.
2. **Spoofing store.** A separate job writes `ais_positionanomaly`. The API reads that table. The STS case job does not scan ClickHouse.
3. **Review job.** Its own `systemctl` service, about every **15 minutes**, one `pnav` connection, observations touched since a watermark. Every row in `ais_vesselproximityobservation` can become a case. `polygons.py` does not decide which rows are kept. `in_anchorage` / `outside_anchorage` are labels only. Writes `ais_stsreviewcase` only. Does not recompute pairs.
4. **Notification API.** Reads `ais_stsreviewcase` where `suspicion_score >= 4.5`, `dredge_licence_match` is false, `bunker_list_match` is false, and at least one of `sanctions_match`, `outside_anchorage`, `dark_overlap`, `illegal_anchor_overlap` is true. Loiter and anchor swing are stored and do not trigger. Status on the wire is `unreviewed`.
5. **Verify API.** A person sets `review_class` to `illicit_transfer`, `bunkering`, `dredge`, `other_legal`, or `uncertain`. Export for training keeps labeled rows only.
6. **STS screen buffer.** `GET /mantis/sts-activities` keeps the anchorage cut. Each **parent** polygon is expanded by **1 km** before the centroid test. Excl holes stay at the drawn ring and are not expanded. Buffer in metres (geodesic), not an alphashape and not a degree offset. Expanded rings are built once at process start. A centroid inside two expanded parents keeps one anchorage name, as today. Overview uses the same filter, so its STS counts follow this band. `ais_stsreviewcase` does not use the band: `in_anchorage` / `outside_anchorage` stay on the original parent polygon.

When the bunker list arrives, load it like `shipprojects`. A match sets `bunker_list_match` and the pair drops off the notification. The row stays as a legal example.

### `ais_positionanomaly`

One kept hit per MMSI per UTC day (highest `implied_kn`), after the snap-back filter. Ship name, dimensions, and OFAC are joined at read time from `ais_static` and the sanctions views.

| Column | Type | Notes |
| --- | --- | --- |
| `id` | bigserial PK | |
| `mmsi` | bigint | not null |
| `event_day` | date | UTC day of `ts`. Unique with `mmsi` |
| `ts` | timestamptz | anomaly fix (second point) |
| `prev_ts` | timestamptz | previous fix |
| `latitude` | double precision | |
| `longitude` | double precision | |
| `prev_latitude` | double precision | |
| `prev_longitude` | double precision | |
| `dist_m` | double precision | |
| `dt_s` | integer | whole seconds, 10–900 |
| `implied_kn` | double precision | |
| `reason` | varchar(20) | `teleport` or `high_speed` |
| `rule_version` | varchar(80) | writer version, currently `v1.1-teleport-snapback-filter` |
| `created_at` | timestamptz | |
| `updated_at` | timestamptz | replaced when a worse hit arrives the same day |

Unique `(mmsi, event_day)`.

### `ais_stsreviewcase`

One row per `ais_vesselproximityobservation`. The review job may refresh the snapshot while `status = unreviewed`. It must not overwrite `review_class`, `labeled_at`, `labeled_by`, or `review_notes` after a person has saved them.

| Column | Type | Notes |
| --- | --- | --- |
| `id` | bigserial PK | |
| `observation_id` | bigint | unique, the proximity observation |
| `cluster_signature` | varchar(255) | |
| `status` | varchar(20) | `unreviewed` (default) or `reviewed` |
| `review_class` | varchar(30) | null until a person sets it |
| `labeled_at` | timestamptz | |
| `labeled_by` | varchar(120) | |
| `review_notes` | text | |
| `first_detected_at` | timestamptz | copied from the observation |
| `last_detected_at` | timestamptz | |
| `duration_seconds` | double precision | |
| `suspicion_score` | double precision | sort hint only |
| `is_open` | boolean | |
| `centroid_latitude` | double precision | |
| `centroid_longitude` | double precision | |
| `vessel_count` | integer | |
| `cargo_count` | integer | |
| `tanker_count` | integer | |
| `max_internal_distance_m` | double precision | |
| `in_anchorage` | boolean | parent polygon, Excl holes ignored |
| `anchorage_name` | text | null when outside |
| `outside_anchorage` | boolean | |
| `sanctions_match` | boolean | true if any member matches OFAC |
| `dredge_licence_match` | boolean | in-force `shipprojects` row covers the pair time |
| `bunker_list_match` | boolean | false until the bunker list exists |
| `dark_overlap` | boolean | a member is on an open dark candidate during the pair |
| `illegal_anchor_overlap` | boolean | a member is an illegal-anchoring candidate during the pair |
| `loiter_overlap` | boolean | loiter circle overlaps the pair. Context only |
| `anchor_swing_overlap` | boolean | swing circle overlaps the pair. Context only |
| `spoof_overlap` | boolean | a member has an `ais_positionanomaly` row on a day the pair was open |
| `members` | jsonb | one object per hull, see below |
| `refreshed_at` | timestamptz | last snapshot |
| `created_at` | timestamptz | |
| `notified_at` | timestamptz | set once, when the case first passes the `GET /mantis/sts-review` queue rule; never changed after. Partial index `idx_stsreviewcase_notified_at`. Read by `GET /mantis/sts-review/notifications` |

Each object in `members`: `mmsi`, `imo`, `shipName`, `shipType`, `shipTypeDesc`, `sanctionsMatch`, `matchConfidence`, `sanctionsList`, `onDredgeLicence`, `dredgeProjectId`, `onBunkerList`.

### Work list

Items 1–7 are in the repo and the processors are running. Item 8 is not built. The MANTIS API process is not on `10.10.20.200`, so the route changes are in the repo only until that API is deployed.

| Order | Work | Where | Load rule |
| --- | --- | --- | --- |
| 1 | Dredge-licence cache every **12 hours** from `marineai` / `jkptg_prod.public.shipprojects` (`mmsi`, `imo`, `shipName`, `id` as project id, licence start/end, `status`). Match MMSI, else IMO. In force only when the pair time sits inside the licence dates. | New helper used by the proximity job and the review job. Own short connection. Never joined from `pnav`. | One small read twice a day. |
| 2 | Admit those licence MMSIs into proximity **only** within **30 m** of a type **70–89** vessel. Same-hull suppression unchanged. Types **30**, **31**, **32**, **52**, **60–69** never enter on type alone. Pleasure, pilot, and high-speed craft stay out. | `backend/vesselproximitydetection.py` only. | No extra ClickHouse. No second scan of movement history. Extra rows are the licensed MMSIs that are already stopped. |
| 2b | Widen `GET /mantis/sts-activities` by a **1 km** metre buffer on each parent polygon. Excl holes unchanged. Precompute rings at startup. No alphashape. | `restapi/sts_detection.py` | In-memory geometry only. Same Postgres read as today. |
| 3 | Create `ais_positionanomaly` as specified above. Job recomputes the current spoofing rule on a slow loop and upserts `(mmsi, event_day)` when `implied_kn` is higher. | New `backend` script, own `systemctl` unit. | ClickHouse only inside this process. Pool size 1 on `pnav` for the upsert. |
| 4 | Point `GET /mantis/spoofing` and vessel-analysis at `ais_positionanomaly` for the fleet list. Same response fields as today. | `restapi/spoofing.py`, `restapi/spoofing_vessel_analysis.py` | Postgres read. No ClickHouse on those routes once the table is the source. |
| 5 | Create `ais_stsreviewcase` as specified above. Review job every **15 minutes**, watermark on `observation_id` / `last_detected_at`, one `pnav` connection, statement timeout. Refresh snapshots for `status = unreviewed` only. | New `backend` script, own `systemctl` unit. | Reads proximity, loiter, dark, illegal-anchoring, sanctions, and `ais_positionanomaly` by MMSI and time. Writes this table only. Does not set `is_anomaly` on the observation. |
| 6 | `GET /mantis/sts-review` returns cases that pass the notification rule in step 4 of the locked design, newest `last_detected_at` first, then `suspicion_score` descending. | `restapi/main.py`, Swagger | Read-only. |
| 7 | `POST /mantis/sts-review/<id>/verify` sets `review_class`, `labeled_by`, `review_notes`, `labeled_at`, and `status = reviewed`. | Same | One update of that row. |
| 8 | Training export reads `ais_stsreviewcase` where `review_class` is not null. | Later, with the first labeled batch | Not part of the first build unless asked. |

Bunker list, when it exists, is a second cache in the same shape as step 1. It sets `bunker_list_match` and `members[].onBunkerList`. No new table and no new route.

Out of scope until a later ask: summing detector flags into a score, auto-setting `is_anomaly`, concession-polygon tests, opening proximity to all ship types, and streaming the overview.

---

## Change log (this document)

| Date | Note |
| --- | --- |
| 2026-10-12 | Notifications for frontend polling. New column `ais_stsreviewcase.notified_at`, set once by the STS review pipeline when a case first passes the queue rule (score ≥ 4.5, no dredge or bunker match, and sanctions, outside anchorage, dark, or illegal anchoring). Backfilled 15,811 cases already in the queue with `COALESCE(refreshed_at, created_at)` so they are not sent as new. New route `GET /mantis/sts-review/notifications?since=&area=`: cases with `notified_at` after `since`, oldest first, at most 200 per call, window ends at server time minus 60 s; `nextSince` is the next cursor and a timestamp group is never split across calls. `area=anchorage` (default) keeps centroids inside a parent polygon buffered 1 km, outside Excl holes (same as `/mantis/sts-activities`); `area=all` keeps every case. Review pipeline restarted on 10.10.20.200 (backups `*.bak-20261012-071110`); API on 10.10.20.201 (backups `*.bak-20261012-071256`). Cursor paging verified on live data. |
| 2026-10-12 | Swagger: response schemas added for `GET /mantis/sts-review`, `GET /mantis/sts-review/unreviewed`, `POST /mantis/sts-review/{caseId}/verify`, and `GET /mantis/loitering`. Every 4xx/5xx response now references `ErrorMessage`. Missing `400` added on loitering and overview. Sample fields (`fromSwagger`, `sample`, `totalCount`, …) documented on every list response. Checked against live payloads on 10.10.20.201; no API code changed. |
| 2026-10-12 | `GET /mantis/sts-review` and `GET /mantis/sts-review/unreviewed` are paginated: `page` (from 1) and `pageSize` (default 100, maximum 1000). Each call runs one `COUNT` and one `LIMIT`/`OFFSET` read on a single connection. `caseCount` / `totalCount` and `sanctionsMatchCount` cover all matching cases; `page`, `pageSize`, `totalPages`, `returnedCount` describe the page. `id DESC` added as the last sort key so pages do not overlap. Deployed to 10.10.20.201 (backups `*.bak-20261012-062907`). |
| 2026-10-12 | `GET /mantis/sts-review` now sorts by `last_detected_at` descending, then `suspicion_score` descending (was sanctions first, then score). `GET /mantis/sts-review/unreviewed` already used this order. Deployed to 10.10.20.201 with a graceful gunicorn reload (backups `*.bak-20261012-062036`). |
| 2026-10-09 | Proximity `2.3-tanker-pair-score`. Composition term is now `1.0` when a cluster has two or more tankers (cargo may also be present), `0.5` for two or more cargo ships, `0` otherwise; it was `1` for any cargo + tanker mix. Open observations take the new score on their next update. Closed observations are not rescored. Deployed to 10.10.20.200 at 12:33 UTC (backup `vesselproximitydetection.py.bak-20261009-123319`); service active, no errors after restart. |
| 2026-10-09 | Built and started. `ais_positionanomaly` and `ais_stsreviewcase` created on `pnav`. Proximity `2.2-dredge-licence` restarted on `10.10.20.200`. New units `ais_vesselspoofingstore` and `ais_vesselstsreview` are active. First cycle wrote 20 spoofing rows and 100 unreviewed cases. Pleasure, pilot, high-speed craft, tug, fishing, and passenger stay out. API routes are in the repo; that host's only container is `py-bpa-restapi`. |
| 2026-10-09 | Locked and frozen, not started. Proximity stays 70–89 plus dredge-licence MMSIs (12-hour cache from `shipprojects`); fishing 30, towing 31–32, tug 52, and passenger 60–69 stay out. `ais_stsreviewcase` follows every `ais_vesselproximityobservation`. `GET /mantis/sts-activities` keeps the anchorage cut with each parent polygon buffered by **1 km**; Excl holes are not buffered. Spoofing facts go to `ais_positionanomaly`. Notification reads `ais_stsreviewcase` and does not set `is_anomaly`. |
| 2026-09-29 | Spoofing drops a one-point position spike. A teleport pair is ignored when the fixes on either side of the outlier are within **2 km** and that gap is under **10%** of the jump. A jump that stays at the new position is kept. `rule_version` **v1.1-teleport-snapback-filter**. Same filter on `/mantis/spoofing/vessel-analysis`. Cargo/tanker filter, 10–900 s gap, and daily dedupe unchanged. |
| 2026-09-24 | Loitering storage ceiling widened. `MAX_RADIUS_M` is **6 NM** (11,112 m); `detection_version` **1.1-loiter-circle-6nm**. Net still &lt; **0.5 NM** and net/path still &lt; **0.20**. ClickHouse read unchanged (2 h, 5-minute samples, cargo/tanker 70–89). API `GET /mantis/loitering`: no `pattern` still returns open rows with a flag. `pattern=loitering` keeps path ≥ **2 NM**, net ≤ **0.5 NM**, net/path ≤ **0.10**, radius ≥ **max(length, 300 m)** and ≤ **6 NM**. `pattern=anchorSwing` keeps radius **100 m** to **max(length, 300 m)**, path ≤ **1 NM**, net/path ≤ **0.20**. `minPathM`, `maxPathM`, `maxNetM`, `maxNetOverPath`, `minRadiusM`, `maxRadiusM` override the preset. Response adds `netOverPath` and `filters`. |
| 2026-09-23 | New loitering job `backend/vesselloiteringdetection.py` → `ais_vesselloiteractivity`, API `GET /mantis/loitering` (Postgres read only). Circle test: span ≥ 2 h, ≥ 8 samples, radius &gt; 100 m and ≤ 1 NM, net &lt; 0.5 NM and &lt; 20% of path. Flags `outside_anchorage`, `near_restricted` (5 NM), `before_sts` (6 h, 5 NM). One open row per MMSI. Loop 15 minutes. |
| 2026-09-09 | `vesselstrajectorydetection.py` performance (same detection rules): TXN1 in-memory open-activity map; TXN2 only open rows whose MMSI has `sog > 0.5` in the current AIS batch (no per-row `duckdb.register`). Trajectory update semantics unchanged (no skip on `ts <= tscurrent`). Cycle summary logs added. |
| 2026-08-18 | `vesselslowspeeddetection.py` performance (same detection rules): TXN1 uses in-memory open-activity map + skips fixes with `ts <= tscurrent`; TXN2 only processes open rows whose MMSI has `sog > 3` in the current AIS batch (no per-row `duckdb.register`). Cycle logs summarize seen/skipped/updated counts. |
| 2026-08-18 | Phase 1 `GET /mantis/spoofing` (Swagger alias `/mantis/position-anomaly`): ClickHouse teleport scan on consecutive AIS fixes; cargo/tanker 70–89; dedupe one row per MMSI per UTC day; OFAC labels. |
| 2026-08-18 | New API `GET /mantis/identity-conflict`. Live scan of latest `ais_static` + `ais_position` for cargo/tanker MMSIs that are one hull (same corroboration rule as STS same-hull suppression). `detectedAt` is the latest AIS timestamp in the group, not wall clock. OFAC labels on each identity and rolled up on the group. Optional `maxDistanceM`. |
| 2026-08-18 | STS same-hull suppression (`DETECTION_VERSION` **2.1**). Pairs that are one re-flagged vessel are dropped before clustering, and open clusters that collapse to one hull close as `same_vessel`. `load_candidate_vessels` now joins the **latest** `ais_static` row and pulls `imo` / `callsign` / dimensions. Caught 9 of 93 live pairs, incl. `352006140_525108038` (PIS MENTAWAI, IMO 1050973 on both MMSIs) which had been open 23.6 days on fixes from 28 June. Added `cleanup_stale_detections.py` for activities and observations that can never close themselves; distance, grace, and score formula unchanged. |
| 2026-08-18 | Investigation note: `detected_at` / `first_detected_at` / `last_detected_at` on `ais_vesselproximityobservation` are **job wall-clock** (`datetime.now(timezone.utc)`), not AIS time, and `duration_seconds` is wall-clock too. No AIS timestamp is stored on the observation — only `ais_vesselproximitymember.tscurrent`. A stale cluster therefore inflates its own duration and suspicion score. Not changed. |
| 2026-08-17 | `/mantis/vessel-track` date params: only `mmsi` required. `to` defaults to now (UTC), `from` to 3 days before the effective `to`, so no dates = last 3 days. `from` without `to` ends at now unless `from` + 3 days is earlier (`rangeCapped`). 3-day cap unchanged; `from` > `to` still 400. Meta adds `fromOmitted` / `requestedDateFrom`. |
| 2026-08-17 | `/mantis/vessel-track` now returns only plottable fixes: `VALID_POSITION_SQL` filters AIS "not available" positions (lat 91 / lon 181, plus NaN / ±Inf) in ClickHouse for Class A and Class B. ~1.3% of `ais_position`. `(0,0)` kept; `sog` 102.3 / `cog` 360 / `trueHeading` 511 still passed through. Detectors unchanged. |
| 2026-08-14 | API vessel size: `toBow` / `toStern` / `toPort` / `toStarboard` plus `lengthM` / `beamM` on STS, dark, and illegal-anchoring. Class A `ais_static` first, Class B `ais_staticb` fallback. Detectors unchanged. |
| 2026-08-13 | API OFAC labels: `restapi/sanctions.py` on STS / dark / illegal-anchoring. IMO = confirmed, MMSI-only = possible, no name match, unmatched kept, listed sorted first. `suspicion_score` / dark `confidence` unchanged. |
| 2026-08-13 | Bunker / barge register **skipped** until a source exists. |
| 2026-08-13 | OFAC consolidated non-SDN ingest added (`ofac_cons_ingest.py` / `--list cons`). Tables `ofac_cons_*`. Current file has **0 vessels** (entities/individuals only). SDN tables untouched. |
| 2026-08-13 | OFAC SDN ingest added (`ofac_sdn_ingest.py`). Tables/view in `pnav`; IMO normalized from `Vessel Registration Identification`. API labels **not** attached yet. Cross-linked with `todo.md`. |
| 2026-08-13 | `vesselstrajectorydetection.py`: `ST_Point` corrected to `(longitude, latitude)`. API STS pair distance left at **35 m** by product decision. |
| 2026-08-13 | Rewritten as MANTIS maintenance spec. Values taken from current Python (not old comments). Stale rowcount is **1**, AIS dark lookback **3 days**, movement lookback **2 days**, movement SOG **0.5 kn**, confirmed-stop rowcount movement **20** / dark **30**. API STS **4.5 / 35 m** vs pipeline **30 m** recorded as intentional for now. |
| 2026-07 | Dark API research (reasons, coverage exit, cargo/tanker 70–89). |
| 2026-05 | Proximity cluster model, suspicion score, ML export. |
