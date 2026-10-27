# Port 7 Oct pnav-tuning → `ais_sts_vesselzone` (Class A)

**Date (UTC):** 2026-10-27  
**Host:** srv-01 `10.10.20.201`  
**Unit touched:** `ais_sts_vesselzone.service` **only**  
**Not touched:** `ais_sts_vesselzone_b` (still ON HOLD), `srv-dp` (not present)  
**References:** PySTS `1bd2a2b` (`backend/vesselzone_b.py`), PyTSS `261e785` (`analyzer/vesselzone.py`)  
**Purpose:** stop stressing pnav; **detection results must not change**

Prior `reports/cursor-report.md` content for the STS shadow plan was already archived as [`sts-shadow-plan.md`](sts-shadow-plan.md) (staged rename before this work).

---

## 0. Deployed vs repo (start from deployed)

| File | md5 |
|------|-----|
| Deployed `/home/srvadmin/py/sts/backend/vesselzone.py` | `e99964303ef0c211e78e6fbd989769cd` |
| Repo `backend/vesselzone.py` (pre-change) | `3cb9b036bc5cb7026799e4d78fcdf16a` |

**Diff summary (repo vs deployed, before tuning):** whitespace-only on model fields + **`time.sleep(10)` in repo vs `time.sleep(30)` on deployed**.

Work started from the **deployed** copy and kept the **30 s** sleep. Repo is now aligned with the tuned deployed file.

Backup: `/home/srvadmin/py/backup-20261027-031948` via `srv_backup.sh --src /home/srvadmin/py/sts --prev e9996430 --new pnav-tuning-a`.

---

## 1. What changed (detection-stable)

| Item | Before (deployed) | After |
|------|-------------------|-------|
| Engine | New `create_engine` every `get_pgEngine()` call (`pool_size=10`, `max_overflow=20`) | One cached engine: `pool_size=2`, `max_overflow=2`, `pool_pre_ping`, `pool_recycle=1800`, `application_name=sts_vesselzone`, `statement_timeout=60s`, `lock_timeout=5s`, `connect_timeout=10`, TCP keepalives |
| Connect failure | Bare `except` / silent fail | **One** retry per cycle (`run_cycle_with_retry`), then skip + log type/message |
| Open-zone load | All open rows, no `mmsi` predicate | `mmsi IN :mmsis` for batch only; `ORDER BY "tsDetected" DESC` kept |
| Writes | One Session held across spatial loop; commit every 300 mid-loop | Decide with **no** txn open; `_write_batches` of 300 |
| `chk_invalid_data` | Every cycle; **one connection per vessel** UPDATE | Hourly (`CLEANUP_INTERVAL_MINUTES=60`); **one** `BEGIN` + `mmsi IN :mmsis` UPDATE; same predicate/`now()` behaviour |
| `ais_position` read | `latitude` ±90 + 96h | Bounding-box from `outter_restricted_region` (same idea as TSS ~236–290). **No watermark** |
| Exceptions | `except:` / `Error reading database` / `pass` | Log `Type: message` (no credentials) |
| Sleep | 30 s (deployed) | 30 s (kept) |

Zone enter/stay/exit logic is the same DuckDB `ST_Within` loop (factored as `decide_zone_changes` for tests).

---

## 2. EXPLAIN — open-row query uses existing partial index

**No new index created.** Existing:

```text
idx_vesselinrestrictzone_open
  btree (mmsi, "tsDetected" DESC) WHERE ("tsOut" IS NULL)
```

Query:

```sql
EXPLAIN (FORMAT TEXT)
SELECT *
FROM public.ais_vesselinrestrictzone
WHERE "tsOut" IS NULL
  AND mmsi IN (...)
ORDER BY "tsDetected" DESC;
```

**Plan (srv-01 → pnav, 2026-10-27):**

```text
Sort  (cost=25.42..25.43 rows=1 width=101)
  Sort Key: "tsDetected" DESC
  ->  Index Scan using idx_vesselinrestrictzone_open on ais_vesselinrestrictzone  (cost=0.27..25.41 rows=1 width=101)
        Index Cond: (mmsi = ANY ('{...}'::integer[]))
```

Artifact: `reports/_vz_tune_artifacts/explain_open.txt`.

---

## 3. Tests

```text
venv/bin/python -m pytest backend/tests/test_vesselzone_pnav_tuning.py -q
........  8 passed
```

| Test | Proves |
|------|--------|
| `test_decide_zone_changes_matches_legacy_replay` | Same sample inputs → identical inserts / stays / exits vs pre-tuning loop |
| `test_retry_once_then_succeeds` / `_skips` | One retry max; never a loop |
| `test_cleanup_hourly_constant` | `CLEANUP_INTERVAL_MINUTES == 60` |
| `test_cleanup_uses_one_transaction` | Single `begin()` batch UPDATE |
| `test_engine_pool_settings` | Cached pool + timeouts + `sts_vesselzone` name |
| `test_write_batches_chunk_size` | Chunks of 300 |
| `test_bbox_from_outer_region` | Finite bbox from outer region |

---

## 4. Deploy PIDs

### Before restart (UTC 03:19:47)

| Unit | MainPID | State |
|------|---------|-------|
| `ais_sts_vesselzone` | **756631** | active |
| `ais_sts_vesselzone_b` | 0 | inactive |
| `ais_tss_analyzer` | 840639 | active |
| `ais_tss_reporting` | 1819043 | active |
| `ais_mantis_api` | 1490384 | active |
| `srvhealth-reporter` | 1661570 | active |
| `pyvam-api` | 1508924 | active |
| `pyvam-processor` | 1555053 | active |
| `ais_sts_shadow` | 1824023 | active |

### After restart (UTC 03:20:56) — only vesselzone restarted

| Unit | MainPID | State |
|------|---------|-------|
| `ais_sts_vesselzone` | **1829224** | active |
| `ais_sts_vesselzone_b` | 0 | inactive (unchanged) |
| All other listed units above | **unchanged** | — |

### After 1h verify (UTC 04:24:49)

| Unit | MainPID | Note |
|------|---------|------|
| `ais_sts_vesselzone` | **1829224** | Stable since restart |
| `ais_sts_vesselzone_b` | 0 | Still inactive |
| `ais_tss_analyzer` | 840639 | Unchanged |
| `ais_mantis_api` | **1837406** | Changed **without** our restart (external); not caused by this deploy |

---

## 5. One-hour verification (03:21 → 04:20 UTC)

Monitor: `/tmp/sts_vz_tune/monitor_1h.py` → `monitor_1h.jsonl` (60 samples @ 60 s).

| Metric | Before (≈20 min journal) | After (1 h monitor / journal) |
|--------|--------------------------|-------------------------------|
| `sts_vesselzone` pnav connections | (blank / no named app; many engines) | **max 1**, avg 1.0 (**≤ 4**) |
| `idle in transaction` (`sts_vesselzone`) | n/a (unnamed) | **max 0** |
| Cycle DB time (Fetching → Upserting done) | min/avg/max **1.62 / 2.46 / 4.41 s** (n=36) | min/avg/max/p50 **1.01 / 1.22 / 1.57 / 1.19 s** (n=60) |
| `Clearing invalid data` | **every cycle** (36 clears / 36 fetches) | **2** in ~1 h (startup + hourly) |
| `Fetching data` / done | 36 / 36 | **123 / 123** (~30 s sleep) |
| Errors (`Exception` / read / skip / retry fail) | — | **0** |
| Open rows (`tsOut` IS NULL) | 493 | 493 → **491** |
| New `tsDetected` last 1h | 12 | 12 → **8** (similar order) |
| `tsCurrent` last 1h | 74 | 75 → **80** (similar order) |

### pnav `application_name` after (subset)

| application_name | count (sample) |
|------------------|----------------|
| `sts_vesselzone` | 1 |
| `vesselzone` (TSS analyzer) | 1 |
| other `sts_*` | 1 each |

---

## 6. Rollback

```bash
# restore from backup tree
sudo systemctl stop ais_sts_vesselzone
cp /home/srvadmin/py/backup-20261027-031948/backend/vesselzone.py \
   /home/srvadmin/py/sts/backend/vesselzone.py
sudo systemctl start ais_sts_vesselzone
# confirm only this unit's MainPID changes
```

---

## 7. Repo files

| Path | Role |
|------|------|
| `backend/vesselzone.py` | Tuned Class A analyzer |
| `backend/tests/test_vesselzone_pnav_tuning.py` | Replay / retry / cleanup / pool tests |
| `reports/cursor-report.md` | This report |
| `reports/_vz_tune_artifacts/` | EXPLAIN, PIDs, backup log, monitor jsonl |

**Commit scope:** vesselzone port + tests + this report only (no shadow / unrelated WIP).
