# Deploy: STS shadow detector (rule C) on srv-01

**Date (UTC):** 2026-10-27  
**Host:** srv-01 `10.10.20.201` only (srv-dp **not** touched)  
**Version:** `shadow-C-1.0`  
**Plan:** [`reports/sts-shadow-plan.md`](sts-shadow-plan.md) (former `cursor-report.md`)

---

## 1. Preflight

| Check | Result |
|-------|--------|
| Unit tests `backend/tests/test_sts_shadow_rule_c.py` | **14 passed** |
| DDL approved | `sql/sts_shadow_episode.sql` as written |
| Unit file | `deploy/ais_sts_shadow.service` |

---

## 2. Backup

| Item | Value |
|------|-------|
| Script | `/tmp/srv_backup.sh` (from `PyVAM/scripts/srv_backup.sh`) |
| `--src` | `/home/srvadmin/py/mantis` |
| `--dest-parent` | `/home/srvadmin/py` |
| Backup | `/home/srvadmin/py/backup-20261027-030443` |
| Result | `BACKUP_OK` (301 files, ~21M) |

---

## 3. DDL (once, `lock_timeout=5s`)

Applied via SQLAlchemy (no `psql` client on host) with `SET LOCAL lock_timeout='5s'` and `statement_timeout='30s'`.

| Object | Status |
|--------|--------|
| `public.ais_sts_shadow_episode` | created |
| `ais_sts_shadow_episode_pkey` | ok |
| `uq_sts_shadow_episode` | ok |
| `idx_sts_shadow_open` | ok |
| `idx_sts_shadow_open_pair` | ok |
| `idx_sts_shadow_mmsi_a_ts` | ok |
| `idx_sts_shadow_mmsi_b_ts` | ok |

---

## 4. Files deployed (srv-01)

| Local | Remote |
|-------|--------|
| `backend/sts_shadow_detection.py` | `/home/srvadmin/py/mantis/sts_shadow_detection.py` |
| `backend/ais_static_sql.py` | `/home/srvadmin/py/mantis/ais_static_sql.py` (new beside API tree) |
| `deploy/ais_sts_shadow.service` | `/etc/systemd/system/ais_sts_shadow.service` |

Env: `EnvironmentFile=/home/srvadmin/py/mantis/mantis.env` (existing `pnav_*` / `clickhouse_*`).

---

## 5. Service PIDs (guard)

### Before enable

| Unit | MainPID |
|------|---------|
| ais_aton_datapreproc | 772689 |
| ais_extract_lloyds | 442266 |
| ais_jkptg_ddms_uat | 1004174 |
| ais_mantis_api | 1490384 |
| ais_ptp_vesselactivities | 861260 |
| ais_sts_vesselzone | 756631 |
| ais_tss_aisposition | 1296170 |
| ais_tss_aisposition_b | 1523778 |
| ais_tss_aisstatic | 839051 |
| ais_tss_aisstatic_b | 841710 |
| ais_tss_analyzer | 840639 |
| ais_tss_reporting | 1819043 |
| ais_sts_shadow | *(absent)* |

### Immediately after `enable --now ais_sts_shadow` (03:04:52 UTC)

| Unit | MainPID | Delta |
|------|---------|-------|
| …all prior units… | *(unchanged)* | none |
| **ais_sts_shadow** | **1824023** | **new** |

`srvhealth-reporter` was already active (PID **1661570**); **not** restarted (no config change required — `ais_*` auto-discovers).

### After 2 cycles (~03:26 UTC)

| Unit | MainPID | Note |
|------|---------|------|
| ais_sts_shadow | 1824023 | unchanged |
| ais_sts_vesselzone | **1829224** | **changed at 03:20:53 UTC** — systemd Stopped/Started; **not** issued by this deploy (we only touched `ais_sts_shadow`) |
| All other listed ais_* | unchanged vs before | |
| srvhealth-reporter | 1661570 | unchanged; cycles WARN→OK once shadow unit was healthy |

---

## 6. Summary log lines (2 cycles)

```
[sts-shadow] detected=49 open=0 inserted=49 extended=0 closed=0 in_mantis=35 samples=120972 ch_s=2.33 ch_mem=None pg_ms=724 version=shadow-C-1.0
[sts-shadow] detected=48 open=49 inserted=1 extended=47 closed=2 in_mantis=35 samples=120942 ch_s=2.11 ch_mem=None pg_ms=496 version=shadow-C-1.0
```

| Cycle | UTC | detected | inserted | extended | closed | in_mantis | ch_s | pg_ms |
|-------|-----|----------|----------|----------|--------|-----------|------|-------|
| 1 | 03:05:06 | 49 | 49 | 0 | 0 | 35 | 2.33 | 724 |
| 2 | 03:20:10 | 48 | 1 | 47 | 2 | 35 | 2.11 | 496 |

Continuity working: 47 of 49 opens extended; 2 closed; 1 new insert.

---

## 7. Table metrics (after 2 cycles)

### By `rule_band`

| rule_band | rows |
|-----------|------|
| 100m | 44 |
| tanker200m | 6 |
| **total** | **50** |

### Open / closed

| is_open | count |
|---------|-------|
| open | 48 |
| closed | 2 |

### `in_mantis` split

| in_mantis | count |
|-----------|-------|
| true | 36 |
| false | 14 |

### Open duration

| min_h | avg_h | max_h |
|-------|-------|-------|
| 2.00 | 3.89 | 6.17 |

---

## 8. Load guards / pnav & ClickHouse impact

| Guard | Observed |
|-------|----------|
| `ais_static` cache ≤ 1 h | Same window query as loitering (`CURRENT_STATIC_ROW_ORDER` + shipType 70–89); second cycle reused cache (no second full static scan in logs) |
| CH `max_memory_usage=4GB`, `max_execution_time=60s` | Both cycles finished; no limit skip |
| CH wall time | **2.33 s** / **2.11 s** (`ch_mem` not returned by client summary → logged `None`) |
| PG total ms / cycle | **724** / **496** (static load + write txn) |
| One txn / cycle | yes (`engine.begin()` for close+upsert) |
| `statement_timeout=30s`, `lock_timeout=5s` | set on engine + `SET LOCAL` in write txn |
| Locks on other tables by `application_name=sts_shadow` | **0** |

### `in_mantis` index (EXPLAIN)

Index present: `ix_ais_vesselproximitymember_mmsi` on `(mmsi)`.

```
Nested Loop
  ->  Bitmap Heap Scan on ais_vesselproximitymember m
        ->  Bitmap Index Scan on ix_ais_vesselproximitymember_mmsi
              Index Cond: (mmsi = ANY (...))
  ->  Index Scan using ais_vesselproximityobservation_pkey on ais_vesselproximityobservation o
        Index Cond: (id = m.observation_id)
```

Startup log: `[sts-shadow] in_mantis index ok: ix_ais_vesselproximitymember_mmsi`.

---

## 9. srvhealth

- Unit matches `ais_*` → auto-discovered.
- Reporter **not** restarted; overall status moved WARN→OK after the new unit was running.
- Not added to `ignore`.

---

## 10. Rollback

```bash
sudo systemctl stop ais_sts_shadow
sudo systemctl disable ais_sts_shadow
# with approval:
# python3 apply rollback → sql/sts_shadow_episode_rollback.sql  (DROP TABLE)
```

Tree restore from `/home/srvadmin/py/backup-20261027-030443` if needed.

---

## 11. Decision

| Item | Status |
|------|--------|
| Tests | PASS |
| DDL | Applied once |
| Deploy srv-01 | LIVE (`ais_sts_shadow` active) |
| srv-dp | Untouched |
| 2 cycles | OK — continuity + load within limits |
| GO / NO-GO | **GO** (live shadow) |
