# Build: loitering SOG + heading motion metrics (local only)

**Date (UTC):** 2026-10-23  
**Scope:** BUILD ONLY — local commit, no push, no deploy, no DDL execution, no srv-01 / srv-dp / cron touch.  
**Prior housekeeping:** Phase 5 deploy report already archived as [`risk-payload-phase5-deploy.md`](risk-payload-phase5-deploy.md) (`c1fb615`).

---

## 1. Summary

Extended the existing loitering ClickHouse 5-minute fetch with SOG / COG / trueHeading, computed per-event motion evidence, and exposed it add-only on `GET /mantis/loitering`. Detection geometry, presets, default flag filter, and which events are detected are unchanged. New Postgres columns are defined in a migration file only (not applied); code skips writing them / returns null until they exist.

---

## 2. Files changed

| Path | Change |
|------|--------|
| `backend/vesselloiteringdetection.py` | Fetch sog/cog/trueHeading; `compute_motion_metrics`, `attach_loiter_ratio`, missing-column-safe upsert; `DETECTION_VERSION` → `1.2-loiter-motion-metrics` |
| `restapi/loitering.py` | Select motion cols or NULL aliases; payload fields; opt-in filters; `durationH` |
| `restapi/main.py` | Wire `minHeadingChanges`, `maxAvgSogKn`, `minDurationH` |
| `restapi/static/swagger.json` | Document new event fields + query params (existing samples unchanged) |
| `sql/loiter_motion_metrics.sql` | **New** — nullable `ALTER TABLE … ADD COLUMN` (not run) |
| `backend/tests/test_loiter_motion_metrics.py` | **New** — synthetic tracks, wrap, 511, missing-col |
| `restapi/tests/test_loiter_motion_api.py` | **New** — payload / golden strip / filters |
| `reports/cursor-report.md` | This report |

---

## 3. New columns / API fields

### Postgres (`ais_vesselloiteractivity`) — nullable, migration only

| Column | Type | Meaning |
|--------|------|---------|
| `avg_sog_kn` | `DOUBLE PRECISION` | Mean usable SOG (kn) |
| `max_sog_kn` | `DOUBLE PRECISION` | Max usable SOG (kn) |
| `sog_std_kn` | `DOUBLE PRECISION` | Sample stddev of usable SOG |
| `sog_change_count` | `INTEGER` | Consecutive 5-min SOG steps with \|Δ\| ≥ threshold |
| `heading_change_count` | `INTEGER` | Consecutive heading steps with wrap-aware \|Δ\| ≥ threshold |
| `loiter_ratio` | `DOUBLE PRECISION` | `path_m / net_m`; null when `net_m` ≤ 1 m |

### API event fields (camelCase)

| Field | Source |
|-------|--------|
| `durationH` | Hours from `startedAt` → `endedAt` (always computable) |
| `avgSogKn` | `avg_sog_kn` (null if column missing / unset) |
| `maxSogKn` | `max_sog_kn` |
| `sogStdKn` | `sog_std_kn` |
| `sogChangeCount` | `sog_change_count` |
| `headingChangeCount` | `heading_change_count` |
| `loiterRatio` | `loiter_ratio` |

### Optional query params (default **off**)

| Param | Effect |
|-------|--------|
| `minHeadingChanges` | Keep `headingChangeCount >= N` |
| `maxAvgSogKn` | Keep `avgSogKn <= N` |
| `minDurationH` | Keep `durationH >= N` |

Default list (no motion params) is identical aside from the new fields (null until DDL + next detector cycle).

---

## 4. Proposed thresholds (and why)

| Constant | Value | Rationale |
|----------|-------|-----------|
| SOG unavailable | `sog >= 102` kn | AIS “not available” is 102.3 |
| Heading unavailable | `511` (and invalid / >360) | AIS true heading N/A; fall back to COG |
| `SOG_CHANGE_THRESHOLD_KN` | **0.5 kn** | Filters noise on consecutive 5-min buckets; anchor creep stays ~0 changes |
| `HEADING_CHANGE_THRESHOLD_DEG` | **45°** | Mid of 30–45° band; counts deliberate turns, not yaw at anchor |
| Heading wrap | smallest angle mod 360 | `359→1` = **2°** (does not count at 45°) |
| `NET_EPS_M` | **1 m** | `loiter_ratio` null when net ~0 (avoids huge/inf ratios) |
| Lookback / min span | **2 h** (unchanged) | Do **not** adopt Lloyd’s/Seasearcher 16 h minimum |

These thresholds are for **evidence only** in this phase — they do not gate detection.

---

## 5. Migration SQL (NOT RUN)

File: `sql/loiter_motion_metrics.sql`

```sql
ALTER TABLE public.ais_vesselloiteractivity
    ADD COLUMN IF NOT EXISTS avg_sog_kn DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS max_sog_kn DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS sog_std_kn DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS sog_change_count INTEGER,
    ADD COLUMN IF NOT EXISTS heading_change_count INTEGER,
    ADD COLUMN IF NOT EXISTS loiter_ratio DOUBLE PRECISION;
```

Deploy order: ship code first (skips persist / returns null) → apply DDL when approved → next detector cycle fills columns.

---

## 6. ClickHouse query cost (before / after)

Same window (2 h), same filters, same MMSI list (all **6** currently open loiter MMSIs — only 6 open rows at sample time). Extended SELECT adds `argMax(sog|cog|trueHeading, ts)` only — **no second scan**.

| Query | Wall (s) | CH `elapsed_ns` | `read_rows` | `read_bytes` | `result_rows` |
|-------|----------|-----------------|-------------|--------------|---------------|
| **Before** (lat/lon only) | 0.049 | 26.3M | 882,316 | 24.7 MB | 149 |
| **After** (+ sog/cog/trueHeading) | 0.095 | 44.3M | 882,316 | 42.3 MB | 149 |
| **Delta** | ~+0.05 s | ~1.7× | same | ~1.7× | same |

Full detector cycles use the same extended query shape over cargo/tanker MMSI chunks (`MMSI_CHUNK=1500`); relative byte/CPU uplift should be similar (extra columns in the same filtered range read). No full-table scan added.

---

## 7. Sample of current open loiter events (metrics computed live)

`motion_cols_present=False` on pnav (DDL not applied). Metrics below computed in-process from the extended CH fetch for sanity-checking thresholds (not yet persisted).

| id | mmsi | durationH | avgSogKn | maxSogKn | sogStdKn | sogChangeCount | headingChangeCount | loiterRatio | path_m | net_m |
|----|------|-----------|----------|----------|----------|----------------|--------------------|-------------|--------|-------|
| 8974 | 563086610 | 2.25 | 0.172 | 0.3 | 0.054 | 0 | 0 | 23.11 | 388 | 17 |
| 8976 | 477255700 | 2.25 | 0.184 | 0.6 | 0.157 | 0 | 0 | 9.78 | 477 | 49 |
| 8977 | 311002457 | 2.00 | 0.792 | 3.1 | 0.960 | 3 | 2 | 5.52 | 2225 | 403 |
| 8968 | 372596000 | 2.50 | 3.044 | 12.0 | 3.530 | 9 | 3 | 6.10 | 4887 | 801 |
| 8971 | 533887000 | 2.50 | 2.236 | 7.2 | 2.425 | 13 | 4 | 11.13 | 4588 | 412 |
| 8975 | 352001456 | 2.25 | 1.408 | 3.7 | 0.937 | 7 | 1 | 7.33 | 4571 | 624 |

**Threshold sanity:** Low-SOG “swing-like” rows show `headingChangeCount=0` and `sogChangeCount=0`. Higher-path rows show more SOG/heading steps but still modest heading counts over 2 h (max 4 at 45°) — optional `minHeadingChanges` for “circling” evidence may want a lower bar (e.g. ≥3) or a longer observational window later; detection itself stays 2 h.

---

## 8. Tests

| Suite | Result |
|-------|--------|
| New motion unit/API tests | **15 passed** |
| Full `backend/tests` + `restapi/tests` | **154 passed** (was 139) |

Covered: circling vs anchor-swing synthetics; heading 511/null; wrap `359→1=2°`; missing-column upsert omit; API null motion fields; golden legacy keys; opt-in filters default off.

---

## 9. Risks and open questions

| Item | Note |
|------|------|
| DDL not applied | Until `sql/loiter_motion_metrics.sql` runs, API motion fields stay null (`durationH` still works). |
| 45° heading bar | Live 2 h open set maxed at 4 turns — may under-fire for mild circling; revisit after more samples. |
| SOG 12 kn on a “loiter” | Geometry still matches storage rules; motion fields make that visible (informational). |
| `create_db_and_tables` | Still does not add motion columns (SQLModel unchanged) — intentional; migration file is the only DDL path. |
| Opt-in filters + missing cols | If filters are set before DDL, metric nulls fail the filter (documented behaviour). |
| Full-fleet CH cost | Measured on 6 MMSIs; production chunks are larger but same query shape — monitor first post-deploy cycle. |

---

## 10. Git

Local commit only — **do not push** (per request).
