# Fix: duplicate STS pairs from multi-row `ais_static` join

**Date (UTC):** 2026-10-27  
**Host:** srv-01 `10.10.20.201` — `/home/srvadmin/py/mantis`  
**Service restarted:** **only** `ais_mantis_api`  
**Prior report archived as:** [`reports/sts-shadow-deploy.md`](sts-shadow-deploy.md)

---

## 1. Cause

`MEMBERS_SQL` in `restapi/sts_detection.py` used:

```sql
LEFT JOIN public.ais_static s ON s.mmsi = m.mmsi
```

Some MMSIs have **2+** rows in `ais_static` (e.g. `565209000`). That fan-out duplicated member rows, so `find_pairs_within_observations` emitted duplicate pairs for the same `(observation_id, mmsi_a, mmsi_b)`.

### Example — observation **340095** (ALPHA / PARMELIA I)

| Join | member rows | notes |
|------|-------------|-------|
| Old plain JOIN | **3** | `564462000`×1 + `565209000`×2 |
| New LATERAL | **2** | one static row per MMSI |

`565209000` static rows:

| id | shipName | ts |
|----|----------|-----|
| 50121 | PARMELIA I | 2026-10-27 03:11:54 |
| 37968 | PARMELIA 1 | 2026-09-04 15:37:48 |

---

## 2. Fix (STS only)

### 2.1 One-row-per-MMSI join

Replaced plain JOIN with **LATERAL** + `ORDER BY ts DESC NULLS LAST, id DESC LIMIT 1` (uses `ix_ais_static_mmsi_ts`). Class-B path unchanged (`class_b_join`). Payload columns unchanged (`imo` + `DIM_SELECT`).

### 2.2 Safety nets

| Layer | Dedupe key |
|-------|------------|
| `load_members` | `(observation_id, mmsi)` |
| `find_pairs_within_observations` (before pairing) | `(observation_id, mmsi)` |
| `find_pairs_within_observations` (after pairing) | `(observation_id, mmsi_a, mmsi_b)` |

### 2.3 Not done

- No new index, no DDL, no data deletes  
- Non-STS modules listed below — **not** changed  

---

## 3. EXPLAIN (LATERAL on obs 340095)

Index used: **`ix_ais_static_mmsi_ts`** `(mmsi, ts DESC)`.

```
Nested Loop Left Join  (actual rows=2)
  ->  Index Scan using ix_ais_vesselproximitymember_observation_id
        Index Cond: (observation_id = 340095)
  ->  Limit
        ->  Sort  (Sort Key: ais_static.ts DESC NULLS LAST, ais_static.id DESC)
              ->  Index Scan using ix_ais_static_mmsi_ts on ais_static
                    Index Cond: (mmsi = m.mmsi)
Execution Time: 0.061 ms
```

---

## 4. Verification

| Check | Before (inflated) | After |
|-------|-------------------|-------|
| Members for 340095 | 3 | **2** |
| Pairs for 340095 | **2** | **1** (ALPHA–PARMELIA I, 27.37 m) |
| Active high-score members | 90 rows (88 unique) | **88** |
| Active pairs (35 m, no anchorage cut) | **66** | **63** |
| Unique `(obs, mmsi_a, mmsi_b)` | 63 | **63** |

**Pair-count delta = 3** — exactly the duplicate pair rows removed; unique pair set unchanged.

Unit tests: `restapi/tests/test_sts_members_dedupe.py` — **3 passed**.

---

## 5. Other API modules — `ais_static` joins (grep)

| Module | Pattern | Action |
|--------|---------|--------|
| **`sts_detection.py`** | was plain `LEFT JOIN ais_static s ON s.mmsi` | **Fixed** (LATERAL) |
| `loitering.py` | `class_a_join` (DISTINCT ON one-row) | OK — leave |
| `dark_vessels.py` | `class_a_join` | OK — leave |
| `illegal_anchoring.py` | `class_a_join` | OK — leave |
| `timelineplayback.py` | `LEFT JOIN public.ais_static s ON s.mmsi = p.mmsi` | **Listed only** (not STS) |
| `spoofing.py` | window / `ORDER BY … CURRENT_STATIC_ROW_ORDER` | OK — leave |
| `spoofing_vessel_analysis.py` | window + `rowcount_static = 1` | OK — leave |
| `ship_risk_attach.py` | `WHERE mmsi = :mmsi … LIMIT 1` | OK — leave |
| `vessel_size.py` | defines `class_a_join` / `class_b_join` | helper |

---

## 6. Deploy (srv-01)

| Step | Result |
|------|--------|
| `srv_backup.sh` | `/home/srvadmin/py/backup-20261027-035119` — `BACKUP_OK` |
| Copied | `sts_detection.py` → `/home/srvadmin/py/mantis/sts_detection.py` |
| Restart | **only** `ais_mantis_api` |
| DDL / srv-dp | none |

### PIDs before → after

| Unit | Before | After |
|------|--------|-------|
| ais_mantis_api | 1490384 | **1837406** (restarted) |
| ais_aton_datapreproc | 772689 | 772689 |
| ais_extract_lloyds | 442266 | 442266 |
| ais_jkptg_ddms_uat | 1004174 | 1004174 |
| ais_ptp_vesselactivities | 861260 | 861260 |
| ais_sts_shadow | 1824023 | 1824023 |
| ais_sts_vesselzone | 1829224 | 1829224 |
| ais_tss_aisposition | 1296170 | 1296170 |
| ais_tss_aisposition_b | 1523778 | 1523778 |
| ais_tss_aisstatic | 839051 | 839051 |
| ais_tss_aisstatic_b | 841710 | 841710 |
| ais_tss_analyzer | 840639 | 840639 |
| ais_tss_reporting | 1819043 | 1819043 |
| srvhealth-reporter | 1661570 | 1661570 |

Only `ais_mantis_api` changed.

---

## 7. Rollback

```bash
BK=/home/srvadmin/py/backup-20261027-035119
# restore sts_detection.py from backup tree, then:
sudo systemctl restart ais_mantis_api
```

---

## 8. Decision

| Item | Status |
|------|--------|
| Root cause | Confirmed (`ais_static` ×2 on 565209000) |
| Fix live | Yes |
| Obs 340095 | **1 pair** |
| Total pairs | Differs only by removed duplicates (66→63) |
| GO | **GO** |
