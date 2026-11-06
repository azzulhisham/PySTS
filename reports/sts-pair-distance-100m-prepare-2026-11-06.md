# Live STS pair distance: 30 m → 100 m (local prepare)

**Date (local work):** 2026-11-06  
**Scope:** Implement and test locally only. **No** push, deploy, restart, live DB queries, production load tests, schema/index changes, or production data changes.  
**Prior `reports/cursor-report.md` archived as:** [`mantis-147-suspected-bunkering-deploy-2026-10-27.md`](mantis-147-suspected-bunkering-deploy-2026-10-27.md)

---

## 1. Verdict

| Item | Result |
|------|--------|
| Live candidate gate | `MAX_DISTANCE_M = 100.0`, strict `<` preserved |
| API presentation recompute | Same `100.0` / strict `<` — 30–100 m pairs no longer hidden |
| Shadow Rule C | Untouched |
| Movement stop/exit radii | Untouched |
| API fields | No remove/rename/change of existing payload fields |
| Local tests | **169 passed** (distance suite + restapi + shadow Rule C) |
| Deploy | **Not executed** — plan below for later approval |

Call-path check: API `find_pairs_within_observations` recomputes unordered pairs among observation members with the same DuckDB sphere/`ST_Point(lon, lat)` convention as the pipeline gate. It is not a separate product rule; aligning it to 100 m does **not** require clustering/scoring redesign. Work proceeded.

---

## 2. Verified deployment context (pre-change)

| Host / unit | Role |
|-------------|------|
| **srv-dp** `ais_vesselproximitydetection.service` | Live detector: `/home/srvadmin/sts/vesselproximitydetection.py` |
| **srv-01** MANTIS API (`ais_mantis_api` / container as deployed) | Reads open observations; `restapi/sts_detection.py` presentation filter |

| Layer | Was | Now (local tree) |
|-------|-----|------------------|
| Pipeline `MAX_DISTANCE_M` | `30.0` | `100.0` |
| Pipeline comparison | strict `<` | strict `<` (unchanged) |
| Pipeline bbox prefilter | fixed `0.001` deg (~110 m) | `(max_distance_m / 111320) * 2` |
| API `MAX_DISTANCE_M` | `35.0` | `100.0` |
| `DETECTION_VERSION` | `2.3-tanker-pair-score` | `2.4-pair-100m` |
| Shadow `sts_shadow_detection.py` | separate Rule C | unchanged |

---

## 3. File-by-file summary

| Path | Change |
|------|--------|
| `backend/vesselproximitydetection.py` | `MAX_DISTANCE_M` 30→100; `DETECTION_VERSION` → `2.4-pair-100m`; bbox scales with `max_distance_m` so 100 m pairs are not dropped by the old ~110 m box |
| `restapi/sts_detection.py` | `MAX_DISTANCE_M` 35→100; docs/comments align with live gate |
| `restapi/static/swagger.json` | STS description `< 100 m`; `maxDistanceM` example `100.0` |
| `backend/mantis-detection.md` | Factor table + layer-mismatch + changelog for 100 m alignment |
| `backend/todo.md` | Marked 100 m align done |
| `backend/tests/test_sts_pair_distance_100m.py` | **New** local fixtures: boundaries at 29/30/35/99/100/100.1/150; old 30 m still excludes 50 m; identity + candidate SQL still require stop/stale |
| `backend/sts_shadow_detection.py` | **Not modified** |
| Unrelated projects / sanctions / pending work | **Not modified** |

---

## 4. Behaviour preserved (non-distance)

| Condition | Status |
|-----------|--------|
| Stopped ≥ 1 h **or** stale ≥ 30 min (`load_candidate_vessels`) | Unchanged |
| `tsout IS NULL`, latest activity, ship type 70–89 | Unchanged |
| Same-hull `is_same_vessel` before clustering | Unchanged |
| Cluster connected components, close grace, suspicion score | Unchanged |
| Review / notification thresholds (e.g. score ≥ 4.5) | Unchanged |
| Movement high-speed exit / stop radii | Unchanged (separate code paths) |
| Shadow Rule C bands (`100m` / `tanker200m`) | Unchanged |

---

## 5. Boundary convention (strict `<`)

| Intended sphere distance (m) | Pipeline gate @ 100 | API recompute @ 100 |
|------------------------------|---------------------|---------------------|
| 29 | pass | pass |
| 30 | pass | pass |
| 35 | pass | pass |
| 99 / 99.9 | pass | pass |
| **100.0** | **fail** | **fail** |
| 100.1 / 101 / 150 | fail | fail |

With explicit `max_distance_m=30.0`, a 50 m pair still fails (convention preserved).

Fixtures place pairs so DuckDB’s `ST_Distance_Sphere` under the live `ST_Point(longitude, latitude)` call order yields the intended metre value (same convention as production). Absolute geodesy of that Point order is a **pre-existing** DuckDB/spatial detail; this change does **not** reorder `ST_Point` arguments.

---

## 6. Test results (actual)

Environment: `/Users/zultan/sources/python/PySTS/venv/bin/python` (DuckDB available).

```text
pytest backend/tests/test_sts_pair_distance_100m.py \
       backend/tests/test_sts_shadow_rule_c.py \
       restapi/tests/
→ 169 passed, 2 warnings (JWT test key length in suspected-bunkering only)
```

| Suite | Outcome |
|-------|---------|
| `test_sts_pair_distance_100m.py` | All pass (incl. 30/35/100 boundaries) |
| `test_sts_shadow_rule_c.py` | All pass (shadow unchanged) |
| `restapi/tests/` (full) | All pass |

No live DB, no production scans.

---

## 7. Risk note (not measured in production)

Widening 30→100 m (and aligning API 35→100) **will** admit more candidate pairs into clustering, open observations, review cases, and API pair lists whenever vessels sit in the new band.

| Area | Qualitative risk | Not claimed |
|------|------------------|-------------|
| Stored pairs / open clusters | Higher count possible | No measured Δ |
| Cycle CPU (bbox + sphere join) | Slightly larger join window (`bbox` ~0.0018° vs 0.001°) | No timing sample |
| Postgres writes (observations/members/review) | More upserts if more clusters persist | No prod query |
| Reviewed false positives | More borderline pairs for human review | No rate estimate |
| API latency | More pairs after recompute for same polygons | No load test |

Candidate SQL (stop/stale/type) and score ≥ 4.5 still bound volume; this is not a full-fleet N² scan of every AIS fix.

---

## 8. Deployment / rollback plan (**do not execute**)

### Affected services

| Service | Host | Artifact |
|---------|------|----------|
| `ais_vesselproximitydetection.service` | **srv-dp** | `/home/srvadmin/sts/vesselproximitydetection.py` |
| MANTIS REST API (`ais_mantis_api` / deployed container) | **srv-01** | `restapi/sts_detection.py` (+ swagger static if shipped) |

Shadow Rule C unit (if present) must **not** be restarted for this change.

### Deploy steps (later approval)

1. Backup live files with timestamped `.bak-*` names on each host.
2. Copy updated `vesselproximitydetection.py` to srv-dp path; restart **only** `ais_vesselproximitydetection`.
3. Deploy API package/image with `sts_detection.py` + swagger; restart **only** the MANTIS API unit/container.
4. Confirm logs: `DETECTION_VERSION` / threshold logging shows 100 m; no crash loop.
5. Spot-check API `maxDistanceM` in STS responses = 100; sample a known pair in the 30–100 m band if one is available from normal traffic (no forced full scan).

### Rollback

1. Restore `.bak-*` of `vesselproximitydetection.py` on srv-dp; restart proximity service.
2. Restore previous API artifact; restart API only.
3. Open observations created under `2.4-pair-100m` remain in DB; they age out via existing close grace / lifecycle — no schema rollback. Optional: leave them; do not bulk-delete without a separate approved data plan.

### Post-deploy assessment (observe only after approval)

| Signal | How to assess |
|--------|----------------|
| Candidate / pair counts | Compare cycle logs (`pairs`, vessel counts) before vs after over several hours |
| Processing time | Same log timings / systemd journal cycle duration — flag if loop approaches `LOOP_INTERVAL_SECONDS` |
| DB load | Existing monitoring on `pnav` (connections, write rate on proximity tables) — no new heavy queries |
| False positives | Review queue volume and verifier reject rate for new `detection_version` |

---

## 9. Explicit non-actions this session

- No `git push`, no remote deploy, no service restart  
- No production DB reads/writes, no load tests  
- No DDL / index / historical backfill  
- No shadow detector, sanctions, or unrelated project edits  
- No local git commit unless separately requested  

---

## 10. Approval gate

Ready for a **separate** deploy approval covering srv-dp proximity + srv-01 API only. Until then, production remains on 30 m / 35 m.
