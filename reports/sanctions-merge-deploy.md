# Deploy: sanctions IMO-merge fix (`972d1fa`)

**Date (UTC):** 2026-10-22  
**Commit:** `972d1fa` — *Fix sanctions ratings: never merge listed IMOs by shared MMSI.*  
**Hosts:** srv-dp `10.10.20.200` (`/home/srvadmin/sts`), srv-01 `10.10.20.201` (`/home/srvadmin/py/mantis`)  
**Backup stamp:** `backup-20261022-074915`  
**No DDL. Cron unchanged (40 8 UTC). `polygons.py` and pyvam-\* untouched.**  
Prior fix report saved as `reports/sanctions-merge-fix.md`.

---

## 1. Push

| Item | Result |
|------|--------|
| `git push origin main` | **OK** — `dd82a32..972d1fa` |

---

## 2. Backups

| Host | Path |
|------|------|
| srv-dp | `/home/srvadmin/sts/backup-20261022-074915/` |
| srv-01 | `/home/srvadmin/py/mantis/backup-20261022-074915/` |

Contents: prior `identity_risk*.py`, `changed-before.tgz`, `PRE_DEPLOY.txt`.  
srv-dp also has matching `crontab-before.txt` / `crontab-after.txt` (identical).

---

## 3. srv-dp — files

| Local → remote | Action |
|----------------|--------|
| `backend/identity_risk_job.py` → `/home/srvadmin/sts/identity_risk_job.py` | **updated** |
| `restapi/identity_risk.py` → `/home/srvadmin/sts/restapi/identity_risk.py` | **updated** |
| `polygons.py` | **not touched** |

Import / sanity:

```text
IMPORT_OK v1.1-identity-risk-ll-mmsi sanctions match (SDN, UK)
SCORE_OWN_IMO_OK AMBER False low_mmsi_misuse
```

Cron (unchanged):

```text
40 8 * * * /usr/bin/python3 /home/srvadmin/sts/identity_risk_job.py >> /var/log/identity_risk.log 2>&1
```

---

## 4. srv-01 — files / restart

| Step | Result |
|------|--------|
| Backup | `backup-20261022-074915/code/identity_risk.py` |
| Copy | `/home/srvadmin/py/mantis/identity_risk.py` |
| Import | `IMPORT_OK v1.1-identity-risk-ll-mmsi` |
| `systemctl restart ais_mantis_api` | **OK** |
| `systemctl is-active ais_mantis_api` | **active** |
| pyvam-api | **active** (untouched) |
| `GET http://127.0.0.1:8085/` | **401** (API up) |

HTTP JWT smoke checks **skipped** (no existing token; no minting).

---

## 5. Manual job run (srv-dp)

Appended to `/var/log/identity_risk.log` (`===== MANUAL RUN 972d1fa 2026-10-22T07:50:05Z =====`).

| Field | Value |
|-------|------:|
| Exit | **0** |
| Elapsed | 16.0 s |
| Events upserted | 756 |
| Ratings upserted | **2,610** |
| `sanctions_ratings_added` | **1,866** |
| `sanctions_ratings_upgraded` | 72 |
| Sanctioned ships considered | 1,938 |

---

## 6. pnav verification (read-only)

### Counts (`lookback_months = 24`)

| Metric | Expected | Observed |
|--------|--------:|---------:|
| Total | 2,610 | **2,610** |
| RED | 1,945 | **1,945** |
| AMBER | 665 | **665** |
| `sanctions_match` | 1,938 | **1,938** |
| Dual `imo:` + `mmsi:` for same MMSI | 0 | **0** |

### Focus IMOs

| IMO | Name | rating | score | lists | reason |
|-----|------|--------|------:|-------|--------|
| 9154000 | VIOLET 1 | RED | 100 | SDN | `sanctions match (SDN)` |
| 9163269 | PETERPAUL | RED | 100 | SDN | `sanctions match (SDN)` |
| 9224805 | OXIS | RED | 100 | SDN, UK | `sanctions match (SDN, UK)` |
| 9258167 | AKAR WEST | RED | 100 | UK | `low_mmsi_misuse; sanctions match (UK)` |
| 9168946 | ZEVS | RED | 100 | SDN, UK | `sanctions match (SDN, UK)` |
| 9408554 | XING YUAN | RED | 100 | UK | `low_mmsi_misuse; sanctions match (UK)` |
| 9230892 | PEPPERONI | AMBER | 30 | — | `low_mmsi_misuse` |
| 9251781 | SYN ACRAB | AMBER | 30 | — | `low_mmsi_misuse` |
| 9197870 | N LUNA | AMBER | 40 | — | `medium_mmsi_misuse` |
| 9259173 | SCIONG | AMBER | 40 | — | `medium_mmsi_misuse` |

### Unchanged controls

| Ship | IMO | rating | score | reason |
|------|-----|--------|------:|--------|
| SEA OPERA | 9000883 | RED | 100 | `sanctions match (SDN)` |
| TULIP | 8912558 | RED | 100 | `sanctions match (SDN)` |
| SEA PEARL | 9245249 | RED | 100 | `high_mmsi_misuse` (`sanctions_match=false`) |

---

## 7. Rollback

### srv-dp

```bash
# SSH as srvadmin@10.10.20.200
BK=/home/srvadmin/sts/backup-20261022-074915
cp -a $BK/code/identity_risk_job.py /home/srvadmin/sts/identity_risk_job.py
cp -a $BK/restapi/identity_risk.py /home/srvadmin/sts/restapi/identity_risk.py
# cron already unchanged
/usr/bin/python3 /home/srvadmin/sts/identity_risk_job.py >> /var/log/identity_risk.log 2>&1
```

### srv-01

```bash
# SSH as srvadmin@10.10.20.201
BK=/home/srvadmin/py/mantis/backup-20261022-074915
cp -a $BK/code/identity_risk.py /home/srvadmin/py/mantis/identity_risk.py
sudo systemctl restart ais_mantis_api
# do not touch pyvam-*
```

---

## 8. Decision

| Item | Status |
|------|--------|
| Push `972d1fa` | Done |
| Backups | `backup-20261022-074915` |
| Job + log | Done — counts match dry-run |
| Focus + control ships | As expected |
| HTTP JWT checks | Skipped |
| Rollback | Documented |

**GO**
