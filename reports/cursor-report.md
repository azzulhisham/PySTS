# OFAC ingest deploy to srv-dp (.200)

**Date:** 2026-10-21  
**Hosts:** srv-dp `10.10.20.200` (ingest/cron), srv-01 `10.10.20.201` (API verify only — **no restart**)  
**Commits:** deploy base `6cad253`; fix `5b870f2` / `64d8aff` (skip vessel `CREATE OR REPLACE VIEW`)  
**Backup:** `backup-20261021-125328`  
**Decision:** **GO**

---

## 1. Timezone

| Field | Value |
|-------|-------|
| Host | srv-dp |
| Local time | 2026-10-21 … UTC |
| Time zone | **Etc/UTC (UTC, +0000)** |
| NTP | active / synchronized |

Cron times below are **server UTC** (same clock as the existing UN job).

---

## 2. Transaction / DDL safety check (before run)

| Check | Result |
|-------|--------|
| Row replace | `TRUNCATE` + `INSERT` inside `with engine.begin()` — **one transaction**; API readers never see empty tables (MVCC until commit) |
| Table DDL | `CREATE TABLE IF NOT EXISTS`, `CREATE INDEX IF NOT EXISTS`, `ALTER TABLE … ADD COLUMN IF NOT EXISTS` only |
| View DDL (original main) | Script also had `CREATE OR REPLACE VIEW ofac_*_vessel` |
| Live `ofac_sdn_vessel` | Columns start `uid, vessel_name, …` (**no** `list_name`) |
| Live `ofac_cons_vessel` | Includes `list_name` before `vessel_name` |
| First real SDN attempt | **Failed** on `CREATE OR REPLACE VIEW` (*cannot change name of view column "vessel_name" to "list_name"*); transaction **rolled back** — counts unchanged |
| Fix | Removed view REPLACE from `ofac_sdn_ingest.py` (`64d8aff`); views left as-is; only IF NOT EXISTS table/index/column DDL + transactional replace |

**No other DDL.** Restapi untouched; no service restarts.

---

## 3. Before / after row counts

| Object | Before | After | Δ |
|--------|-------:|------:|---|
| `ofac_sdn_entry` | 19199 | **19457** | +1.3% |
| `ofac_sdn_aka` | 24576 | **24617** | +0.2% |
| `ofac_sdn_identifier` | 53473 | **54909** | +2.7% |
| `ofac_sdn_vessel` | 1524 | **1559** | +2.3% |
| SDN vessel with IMO | 1519 | **1546** | +1.8% |
| SDN vessel with MMSI | 792 | **826** | +4.3% |
| `ofac_cons_entry` | 481 | **486** | +1.0% |
| `ofac_cons_aka` | 1186 | **1191** | +0.4% |
| `ofac_cons_identifier` | 2632 | **2668** | +1.4% |
| `ofac_cons_vessel` | 0 | **0** | — |
| `un_vessel` | 1 | **1** | unchanged |

Last-run before: SDN ingest `2026-08-13` publish `08/07/2026`; CONS `2026-08-13` publish `07/27/2026`.  
After: SDN publish **`10/15/2026`**, CONS **`10/19/2026`** (`ofac_ingest_run` ids 3–4).

**10% drop gate:** not triggered (all key counts rose slightly).

Dry-run (local XML after slow Treasury download): SDN vessels=1559 / IMO=1546 / MMSI=826; CONS vessels=0.

---

## 4. Backup

| Item | Value |
|------|-------|
| Path | `/home/srvadmin/sts/backup-20261021-125328` |
| `PRE_DEPLOY.txt` | `BACKUP_NAME=backup-20261021-125328`, PREV=`ec6ebd2`, NEW=`6cad253` (+ later `FIX_COMMIT` / `FIX2_COMMIT` notes) |
| Contents | prior `*.py`, `restapi/`, `data/`, `code-tree.tgz`, prior `DEPLOYED_COMMIT.txt` |

---

## 5. Files deployed (.200)

| Path | Notes |
|------|-------|
| `/home/srvadmin/sts/ofac_sdn_ingest.py` | from main `@64d8aff` |
| `/home/srvadmin/sts/ofac_cons_ingest.py` | wrapper; imports sibling `ofac_sdn_ingest` |
| `/home/srvadmin/sts/data/sdn.xml` | official SDN XML (wget; ~29 MiB) |
| `/home/srvadmin/sts/data/consolidated.xml` | official CONS XML |
| `/home/srvadmin/sts/DEPLOYED_COMMIT.txt` | `64d8aff…` |

Treasury download from `.200` is **very slow** (~20–40 KB/s). One-shot load used `--xml data/…` after `wget -c`. Daily cron still downloads live (may take a long time).

---

## 6. Cron (srvadmin, UTC)

```
10 8 * * * /usr/bin/python3 /home/srvadmin/sts/ofac_sdn_ingest.py  >> /var/log/ofac_sdn_ingest.log 2>&1
15 8 * * * /usr/bin/python3 /home/srvadmin/sts/ofac_cons_ingest.py >> /var/log/ofac_cons_ingest.log 2>&1
20 8 * * * /usr/bin/python3 /home/srvadmin/sts/un_sanctions_ingest.py >> /var/log/un_ingest.log 2>&1
```

Log files created, owned by `srvadmin`. UN line kept.

---

## 7. API check (.201, no restart)

| Check | Result |
|-------|--------|
| `GET /mantis/sanctions` | **200** |
| Counts | **SDN 1559**, **UN 1**, total **1560** |
| AVAX | present, IMO `9058713`, `listName` `UN` |
| Fresh index process | IMO keys **1547** (= 1546 SDN IMO + 1 UN), MMSI keys **824**; `match(9058713)` → UN |
| Cache | List endpoint already reflected new tables (TTL ≤ 5 min); **no** `ais_mantis_api` restart |

---

## 8. Stray copy (not touched)

`/home/srvadmin/py/pyvam/backend/copied/PySTS` on `.201` still contains a copied tree including `backend/ofac_sdn_ingest.py` (**STRAY_OFAC_PRESENT**). Left alone per instructions.

---

## 9. Rollback (code/cron only; leave refreshed OFAC rows)

```bash
# .200 — restore prior scripts from this backup; remove OFAC cron; keep UN
APP=/home/srvadmin/sts
BAK=$APP/backup-20261021-125328
cp -a "$BAK/code/"*.py "$APP/" 2>/dev/null || true
# remove only OFAC lines
crontab -l | grep -v ofac_sdn_ingest.py | grep -v ofac_cons_ingest.py | crontab -
# optional: rm -f $APP/ofac_sdn_ingest.py $APP/ofac_cons_ingest.py
```

To revert **data** to the Aug snapshot you would need a DB restore (not done; not requested). Tables were refreshed in place successfully.

---

## 10. Notes

- Initial safety review: truncate is transactional; view REPLACE was the hazard and was removed after a rolled-back failure.
- No restapi deploy, no service restart.
- IMO/MMSI key totals moved from ~1519/790 to ~1546/824 (plus 1 UN IMO) — consistent with a fresher SDN file, within the 10% gate.
