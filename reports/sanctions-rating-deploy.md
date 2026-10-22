# Deploy: sanctioned ships → RED identity risk ratings

**Date (UTC):** 2026-10-22  
**Commit:** `dd82a32` — *Ensure every sanctioned ship gets a RED identity risk rating.*  
**Hosts:** srv-dp `10.10.20.200` (`/home/srvadmin/sts`), srv-01 `10.10.20.201` (`/home/srvadmin/py/mantis`)  
**Backup stamp:** `backup-20261022-072948`  
**No DDL. Cron unchanged (40 8 UTC). `polygons.py` not touched.**  
Prior report saved as `reports/sanctions-rating-fix.md`.

---

## 1. Push

| Item | Result |
|------|--------|
| `git push origin main` | **OK** — `377001e..dd82a32` |

---

## 2. Backups

| Host | Path |
|------|------|
| srv-dp | `/home/srvadmin/sts/backup-20261022-072948/` |
| srv-01 | `/home/srvadmin/py/mantis/backup-20261022-072948/` |

Each backup contains: prior `identity_risk*.py` copies, `changed-before.tgz`, `PRE_DEPLOY.txt`.  
srv-dp also has `crontab-before.txt` / `crontab-after.txt` (identical).

---

## 3. srv-dp — files

| Local → remote | Action |
|----------------|--------|
| `backend/identity_risk_job.py` → `/home/srvadmin/sts/identity_risk_job.py` | **updated** |
| `restapi/identity_risk.py` → `/home/srvadmin/sts/restapi/identity_risk.py` | **updated** |
| `polygons.py` | **not touched** (mtime still 2026-09-23) |

Import check:

```text
IMPORT_OK v1.1-identity-risk-ll-mmsi sanctions match (SDN, UK)
LIST_PRIORITY ['SDN', 'CONS', 'UN', 'UK']
job_restapi_ok True
```

Cron (unchanged):

```text
40 8 * * * /usr/bin/python3 /home/srvadmin/sts/identity_risk_job.py >> /var/log/identity_risk.log 2>&1
```

---

## 4. srv-01 — files / restart

`main.py` imports `identity_risk` and serves `/mantis/ship-risk` → **updated** `identity_risk.py` only.

| Step | Result |
|------|--------|
| Backup | `backup-20261022-072948/code/identity_risk.py` |
| Copy | `/home/srvadmin/py/mantis/identity_risk.py` |
| Import | `IMPORT_OK v1.1-identity-risk-ll-mmsi` |
| `systemctl restart ais_mantis_api` | **OK** |
| `systemctl is-active ais_mantis_api` | **active** |
| `pyvam-*` | **untouched** (pyvam-api still active) |
| Health probe `http://127.0.0.1:8085/` | **401** `{"message":"Unauthorized"}` (API up; auth required as designed) |

---

## 5. Manual job run (srv-dp)

Appended to `/var/log/identity_risk.log` (marker `===== MANUAL RUN dd82a32 2026-10-22T07:30:09Z =====`).

| Field | Value |
|-------|------:|
| Exit | **0** |
| Elapsed | 16.8 s |
| Events upserted | 756 |
| Ratings upserted | **2,607** |
| `sanctions_ratings_added` | **1,863** |
| `sanctions_ratings_upgraded` | 0 |
| Sanctioned ships considered | 1,938 |

---

## 6. pnav verification (read-only)

### Counts (`lookback_months = 24`)

| Metric | Expected | Observed |
|--------|--------:|---------:|
| Total ratings | ~2,607 | **2,607** |
| RED | ~1,946 | **1,946** |
| AMBER | ~661 | **661** |
| `sanctions_match = true` | ~1,939 | **1,939** |
| Ships under both `imo:` and `mmsi:` for same MMSI | 0 | **0** |

### SEA OPERA / TULIP

| Ship | ship_key | rating | score | lists | reason | events |
|------|----------|--------|------:|-------|--------|-------:|
| SEA OPERA | `imo:9000883` | RED | 100 | SDN | `sanctions match (SDN)` | 0 |
| TULIP | `imo:8912558` | RED | 100 | SDN | `sanctions match (SDN)` | 0 |

### OXIS / SEA PEARL (earlier RED cases)

| Ship | Check | Result |
|------|-------|--------|
| SEA PEARL (`imo:9245249`, MMSI 352003715) | Stored row | **RED 100**, reason **`high_mmsi_misuse`** kept, `sanctions_match=false` |
| OXIS (IMO `9224805`) | Stored `ship_key` | No separate row (see §7 — consolidated onto AKAR WEST `imo:9258167` via shared MMSI) |
| OXIS via `query_ship_risk(imo=9224805)` | Live recompute from events (`imo` / `imo_other`) | **RED 100**, `sanctionsMatch=true`, reason `sanctions match (SDN, UK)` |

---

## 7. Count gap: 1,866 vs 1,863 newly added

Naive arithmetic: **1,938** keyed sanctioned − **72** already in event-based ratings = **1,866**.  
Job/dry-run **newly added** = **1,863**. Difference = **3**.

Those three sanctioned vessels share an MMSI with an **already-present** rating key, so the merge consolidates onto the existing key instead of inserting a new `imo:` row:

| Sanctioned ship | Preferred key | MMSI | Landed on (existing key) | Why |
|-----------------|---------------|-----:|--------------------------|-----|
| **OXIS** | `imo:9224805` | 613612000 | `imo:9258167` (AKAR WEST) | Lloyd's conflict event already keys the AIS IMO; OXIS is `imo_other` |
| **ZEVS** | `imo:9168946` | 613833000 | `imo:9408554` (XING YUAN) | Same pattern (Lloyd's conflict / shared MMSI) |
| **VIOLET 1** | `imo:9154000` | 352900000 | `imo:9163269` (PETERPAUL) | Same MMSI as another sanctioned ship already inserted in the merge pass |

Check: 1,938 − 72 − 3 = **1,863**.

---

## 8. API checks (JWT)

**Skipped.** No Zulhisham JWT was available locally (`/tmp/.mantis_api_tok` absent; `mcp/.env` has access-key fields only, not a Bearer JWT). Per instructions, did **not** mint a token or use the server signing secret.

Equivalent read-only checks were done via `query_ship_risk` against pnav (above). HTTP `/mantis/sanctions` count was not re-hit over the wire; the job loaded the same merged list and logged **1,938** keyed / list total remains **1,940** (2 without IMO/MMSI).

---

## 9. Rollback

### srv-dp

```bash
# SSH as srvadmin@10.10.20.200
BK=/home/srvadmin/sts/backup-20261022-072948
cp -a $BK/code/identity_risk_job.py /home/srvadmin/sts/identity_risk_job.py
cp -a $BK/restapi/identity_risk.py /home/srvadmin/sts/restapi/identity_risk.py
# cron already unchanged — nothing to restore there
# Rebuild ratings with previous code (drops sanction-only rows on next recompute):
/usr/bin/python3 /home/srvadmin/sts/identity_risk_job.py >> /var/log/identity_risk.log 2>&1
```

### srv-01

```bash
# SSH as srvadmin@10.10.20.201
BK=/home/srvadmin/py/mantis/backup-20261022-072948
cp -a $BK/code/identity_risk.py /home/srvadmin/py/mantis/identity_risk.py
sudo systemctl restart ais_mantis_api
# do not touch pyvam-*
```

---

## 10. Decision

| Item | Status |
|------|--------|
| Push `dd82a32` | Done |
| Backups | Done |
| srv-dp job + log | Done — 2,607 / 1,946 RED / 661 AMBER |
| srv-01 API service | Active; health responds |
| SEA OPERA / TULIP | RED 100 in table |
| SEA PEARL reason preserved | Yes (`high_mmsi_misuse`) |
| HTTP JWT API smoke | **Skipped** (no existing JWT) |
| Rollback path | Documented |

**GO** for the data/job deploy. Re-check `/mantis/ship-risk` over HTTP when Zulhisham’s JWT is available.
