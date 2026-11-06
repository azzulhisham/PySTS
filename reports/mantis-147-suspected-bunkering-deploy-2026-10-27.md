# MANTIS-147: `GET /mantis/suspected-bunkering`

**Date (UTC):** 2026-10-27  
**Host:** srv-01 `10.10.20.201` — `/home/srvadmin/py/mantis`  
**Commit:** `d0e88c5935c06ee03e31307258152d15d13ae8d7`  
**Service restarted:** **only** `ais_mantis_api`  
**Prior report archived as:** [`reports/sts-duplicate-pairs-fix.md`](sts-duplicate-pairs-fix.md)

---

## 1. Scope

| Constraint | Honoured |
|------------|----------|
| ADD-ONLY (new route + module + swagger) | Yes |
| No existing route/payload changes | Yes |
| No DDL / no pnav writes | Yes |
| No new backend service / srv-dp | Yes |
| Do not stress pnav | Batched MMSI enrichment; `statement_timeout=30s`; `lock_timeout=5s`; 5‑min response cache |

---

## 2. Files

| Path | Role |
|------|------|
| `restapi/suspected_bunkering.py` | Scoring + read-only queries + cache |
| `restapi/main.py` | New route only |
| `restapi/static/swagger.json` | Path + schemas |
| `restapi/tests/test_suspected_bunkering.py` | Scoring + auth (401/200) |

---

## 3. API contract

`GET /mantis/suspected-bunkering`  
Auth: Bearer (401 without). Pagination: `page` / `pageSize` via `_page_args` / `_page_payload`.

| Query | Default | Notes |
|-------|---------|-------|
| `hours` | 24 | max 168; open always included |
| `band` | — | HIGH / MEDIUM / LOW |
| `minScore` | — | float |
| `mmsi` | — | either side |

Response (camelCase): `ruleVersion`, `hours`, `episodeCount`, `byBand`, `episodes[]`, page fields, `queryMs`, `pnavQueryMs`, `skippedSignals`, `cacheHit`.

Each episode: `vesselA` / `vesselB` (mmsi, shipName, shipType, lengthM, flag, sanctions/risk), centroid, `zoneName`, `tsStart`/`tsLast`, `durationH`, distances, `medianSogKn`, `ruleBand`, `isOpen`, `score`, `band`, `reasons[]`, `licensed` (`"unknown"` placeholder for JKPTG).

No separate frontend contract found — matched other MANTIS list routes + Swagger.

---

## 4. Scoring (tunable constants)

| Signal | Points | Reason key |
|--------|--------|------------|
| possible_bunker | 2.0 | `possible_bunker` |
| bunker_hub | 2.0 | `bunker_hub` |
| outside known anchorage/port | 1.5 | `outside_known_area` |
| night (MYT 19:00–07:00) | 1.0 | `at_night` |
| duration &lt; 4 h | 1.0 | `short_duration` |
| AIS anomaly ±6 h | 1.5 | `ais_anomaly` |
| loitering overlap ±6 h | 1.0 | `loitering` |
| identity event / non-GREEN risk | 1.5 | `identity_risk` |
| sanctions hit | 2.0 | `sanctions` |

Cap 10. Bands: HIGH ≥ 7, MEDIUM ≥ 4, else LOW.

---

## 5. Indexes & EXPLAIN

All required indexes **present**; `skippedSignals=[]`.

### episodes (`ais_sts_shadow_episode`)

Table still tiny (~40 candidate rows). Planner chose **Seq Scan** + filter (cost 6); `idx_sts_shadow_open` available for open-only paths as the table grows.

```
Sort
  ->  Seq Scan on ais_sts_shadow_episode e
        Filter: ((possible_bunker OR bunker_hub_*) AND (is_open OR ts_last >= now()-24h))
```

### anomaly — `ais_positionanomaly_mmsi_event_day_key`

```
Bitmap Index Scan on ais_positionanomaly_mmsi_event_day_key
  Index Cond: (mmsi = ANY (...))
```

(`idx_positionanomaly_ts` also present; MMSI unique index used for this shape.)

### loiter — `ix_ais_vesselloiteractivity_mmsi`

```
Bitmap Index Scan on ix_ais_vesselloiteractivity_mmsi
  Index Cond: (mmsi = ANY (...))
```

### identity — `idx_identity_event_mmsi_ts`

```
Index Only Scan using idx_identity_event_mmsi_ts
  Index Cond: (mmsi = ANY (...) AND ts_start range)
```

### static — `ix_ais_static_mmsi_ts` (DISTINCT ON filtered by `mmsi = ANY`)

```
Bitmap Index Scan on ix_ais_static_mmsi_ts
  Index Cond: (mmsi = ANY (...))
```

Same one-row-per-MMSI rule as `class_a_join`, scoped to batch MMSIs (no full-table DISTINCT ON).

Sanctions / ship-risk use existing in-process helpers (`attach_sanctions_pair_sides`, `attach_risk_pair_sides`).

---

## 6. Live verification (srv-01)

| Check | Result |
|-------|--------|
| Unit scoring tests | 5 passed (auth skipped locally without flask_cors) |
| HTTP without token | **401** |
| HTTP with token | **200** |
| Episodes (hours=24) | **36** (HIGH 1 / MEDIUM 23 / LOW 12) |
| `licensed` | `unknown` |
| Cold detect `queryMs` | **1069–1107 ms** |
| pnav breakdown | episodes 32 ms, static 78, anomaly 32, loiter 28, identity 33, sanctions/risk **679** |
| Cached HTTP (same worker) | **1.8 ms**, `cacheHit=true` |

---

## 7. Deploy

| Step | Result |
|------|--------|
| `srv_backup.sh` | `/home/srvadmin/py/backup-20261027-042652` — `BACKUP_OK` |
| Copied | `suspected_bunkering.py`, `main.py`, `static/swagger.json` |
| `DEPLOYED_COMMIT.txt` | `d0e88c5935c06ee03e31307258152d15d13ae8d7` |
| Restart | **only** `ais_mantis_api` |
| srv-dp | Untouched |

### PIDs before → after

| Unit | Before | After |
|------|--------|-------|
| **ais_mantis_api** | 1837406 | **1879238** |
| ais_aton_datapreproc | 772689 | 772689 |
| ais_extract_lloyds | 442266 | 442266 |
| ais_jkptg_ddms_uat | 1004174 | 1004174 |
| ais_ptp_vesselactivities | 861260 | 861260 |
| ais_sts_shadow | 1824023 | 1824023 |
| ais_sts_vesselzone | 1829224 | 1829224 |
| ais_tss_* (all) | unchanged | unchanged |
| srvhealth-reporter | 1661570 | 1661570 |

---

## 8. Rollback

```bash
BK=/home/srvadmin/py/backup-20261027-042652
cp -a $BK/code/main.py /home/srvadmin/py/mantis/main.py
cp -a $BK/static/swagger.json /home/srvadmin/py/mantis/static/swagger.json
rm -f /home/srvadmin/py/mantis/suspected_bunkering.py
sudo systemctl restart ais_mantis_api
```

---

## 9. Decision

| Item | Status |
|------|--------|
| Route live | Yes |
| Auth 401/200 | Yes |
| pnav load | Acceptable (~1 s cold; cache 5 min) |
| GO | **GO** |
