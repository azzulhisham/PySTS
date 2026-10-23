# Deploy: loitering motion metrics (`d24b12d`)

**Date (UTC):** 2026-10-23  
**Commits:** `d24b12d` (code) + archive `73032a5`  
**Hosts:**  
- **srv-01** `10.10.20.201` — API only (`ais_mantis_api`)  
- **srv-dp** `10.10.20.200` — detector only (`ais_vesselloiterdetection`)  
**Backup stamp:** `backup-20261023-092800`  
**DDL:** `sql/loiter_motion_metrics.sql` applied once (six nullable columns). No backfill.  

Prior STOP report: [`loiter-motion-deploy-stop.md`](loiter-motion-deploy-stop.md).  
Build report: [`loiter-motion-build.md`](loiter-motion-build.md).

---

## 1. Pre-copy safety

### srv-dp detector vs git

| Item | Value |
|------|-------|
| Expected match commit | `20a1cf1` (last touch of `backend/vesselloiteringdetection.py` before `d24b12d`) |
| Live SHA-256 before copy | `5bce1dfe…c757c7` |
| `git show 20a1cf1:backend/vesselloiteringdetection.py` | **MATCH** |
| New file SHA-256 | `ae6ee643…bbdf0b` |
| Import roots live vs new | **Identical** (no new top-level modules) |

### srv-01 API vs `e1c481c`

| File | Live SHA vs `e1c481c` | Action |
|------|----------------------|--------|
| `loitering.py` | **MATCH** `1a8e44e6…` | replace |
| `main.py` | **MATCH** `6d5b1291…` | replace |
| `static/swagger.json` | **MATCH** `41d9fb75…` | replace |

**STOP condition not triggered.**

---

## 2. Backup

| Host | Path | Contents |
|------|------|----------|
| srv-01 | `/home/srvadmin/py/mantis/backup-20261023-092800/` | `code/{loitering,main}.py`, `static/swagger.json`, `PRE_DEPLOY.txt` (`PREV=e1c481c`, `NEW=d24b12d`) |
| srv-dp | `/home/srvadmin/sts/backup-20261023-092800/` | `code/vesselloiteringdetection.py` (SHA = `20a1cf1`), `PRE_DEPLOY.txt` |

Note: srv-01 API files were SCP’d in a first attempt before the backup dir was created; backup blobs are the verified `e1c481c` contents (SHA matched live immediately before overwrite). Rollback still restores those exact hashes.

---

## 3. Files copied / services restarted

### srv-01 (first)

| Local → remote | Result |
|----------------|--------|
| `restapi/loitering.py` → `/home/srvadmin/py/mantis/loitering.py` | updated |
| `restapi/main.py` → `/home/srvadmin/py/mantis/main.py` | updated |
| `restapi/static/swagger.json` → `/home/srvadmin/py/mantis/static/swagger.json` | updated |

| Check | Result |
|-------|--------|
| Import | **IMPORT_OK**; `motion_columns_present=False` (pre-DDL) |
| Code-before-DDL | `durationH=3.0`; `avgSogKn`/`headingChangeCount`/`loiterRatio` **null** |
| Pre event count | **4** |
| `systemctl restart ais_mantis_api` | **OK** |
| `ais_mantis_api` | active; PID **1462290 → 1490384** |
| `pyvam-api` / `processor` / `dash` | PIDs **1390232 / 1390231 / 1390233** unchanged |
| `GET /` | **401** |
| Tracebacks (~2 min) | **0** |
| `DEPLOYED_COMMIT.txt` | `d24b12d` / `BACKUP=backup-20261023-092800` |

### srv-dp (second; detector only)

| Local → remote | Result |
|----------------|--------|
| `backend/vesselloiteringdetection.py` → `/home/srvadmin/sts/vesselloiteringdetection.py` | updated (SHA `ae6ee643…`) |

| Check | Result |
|-------|--------|
| Import (service Python + `mantis.env`, secrets redacted) | **IMPORT_OK** `1.2-loiter-motion-metrics` |
| `systemctl restart ais_vesselloiterdetection` | **OK** (09:44:14 UTC) |
| Service | active; PID **2941934 → 3134302** |
| Other processors (PIDs unchanged) | proximity **1944630**, spoofing **2879959**, stsreview **2942393**, slowspeed **846102** |
| Tracebacks since restart | **0** |
| Untouched | `sanctions.py`, `identity_risk_job`, cron, env files, other units |

---

## 4. DDL

| Item | Result |
|------|--------|
| File | `sql/loiter_motion_metrics.sql` |
| `SET lock_timeout = '5s'` | set |
| Attempts | **1** (OK) |
| Other DDL | none |

### `information_schema` after

| column_name | data_type | is_nullable |
|-------------|-----------|-------------|
| `avg_sog_kn` | double precision | YES |
| `max_sog_kn` | double precision | YES |
| `sog_std_kn` | double precision | YES |
| `sog_change_count` | integer | YES |
| `heading_change_count` | integer | YES |
| `loiter_ratio` | double precision | YES |

---

## 5. Detector cycle timing

| Cycle | Start (UTC) | Detected log | Wall (start→detected) | Samples | Result |
|-------|-------------|--------------|----------------------|---------|--------|
| **Before** (old PID) | 09:41:21 | 09:41:23 | **~2.5 s** | 43,109 | detected=5 insert=2 update=3 |
| **After** (new PID, post-DDL) | 09:44:36 | 09:44:40 | **~4.5 s** | 42,844 | detected=4 insert=0 update=4 **`motion_persist=True`** |

Loop spacing remains ~15 min (unchanged). Extra ~2 s is consistent with SOG/heading aggregation on the same CH window (no second scan).

---

## 6. Handler verification (srv-01, no JWT)

Direct `detect_loitering()` after cycle + DDL:

| Check | Result |
|-------|--------|
| `motion_columns_present` | **True** |
| `event_count` | **4** (same as pre-deploy; normal live set) |
| Motion filled | **4 of 4** events have `avgSogKn`, `sogChangeCount`, `headingChangeCount`, `loiterRatio` |
| Legacy keys present | **25** core keys asserted on each event |
| `GET /` | **401** |
| pyvam PIDs | unchanged |

### Sample events (informational)

| loiterId | mmsi | durationH | avgSogKn | sogChangeCount | headingChangeCount | loiterRatio | pathM |
|----------|------|-----------|----------|----------------|--------------------|-------------|-------|
| 8986 | 311002457 | 2.0 | 0.25 | 0 | 0 | 11.74 | 716 |
| 8971 | 533887000 | 3.25 | 2.44 | 13 | 6 | 9.05 | 4995 |
| 8979 | 677048000 | 2.5 | 0.44 | 6 | 8 | 41.76 | 1433 |
| 8987 | 677073400 | 2.0 | 0.44 | 7 | 5 | 365.39 | 1113 |

---

## 7. Rollback

### srv-01

```bash
# SSH as srvadmin@10.10.20.201
BK=/home/srvadmin/py/mantis/backup-20261023-092800
cp -a $BK/code/loitering.py /home/srvadmin/py/mantis/loitering.py
cp -a $BK/code/main.py /home/srvadmin/py/mantis/main.py
cp -a $BK/static/swagger.json /home/srvadmin/py/mantis/static/swagger.json
sudo systemctl restart ais_mantis_api
# do not touch pyvam-*
# leave nullable motion columns in place — do not DROP
```

### srv-dp

```bash
# SSH as srvadmin@10.10.20.200
BK=/home/srvadmin/sts/backup-20261023-092800
cp -a $BK/code/vesselloiteringdetection.py /home/srvadmin/sts/vesselloiteringdetection.py
sudo systemctl restart ais_vesselloiterdetection
# do not touch other units / cron / env / sanctions / identity_risk_job
# leave nullable motion columns in place — do not DROP
```

---

## 8. Decision

| Item | Status |
|------|--------|
| SHA compare (detector `20a1cf1`, API `e1c481c`) | Clean |
| srv-01 API copy + `ais_mantis_api` restart | Done |
| srv-dp detector-only copy + `ais_vesselloiterdetection` restart | Done |
| Other srv-dp / pyvam PIDs unchanged | Done |
| DDL (6 nullable columns) | Done (attempt 1) |
| Cycle with `motion_persist=True` | Done (~4.5 s vs ~2.5 s before) |
| Handler: motion filled, legacy keys OK, count=4 | Done |
| No Tracebacks | Done |
| Rollback documented (columns kept) | Done |

**GO** — loitering SOG/heading motion metrics live: API on srv-01, detector on srv-dp, DDL on pnav.
