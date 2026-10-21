# UN Security Council sanctions — Phase 2 deploy report

**Date:** 2026-10-21  
**Commit:** `ec6ebd2` (`mantis-un-sanctions` → `main`)  
**Backup stamp:** `backup-20261021-071811`  
**Decision:** **GO** (no rollback)

---

## 1. Fix: ship IMO vs company number

| Rule | Behaviour |
|------|-----------|
| Ship IMO | Only `IMO: NNNNNNN` (colon), check digit OK → `un_entry.imo` / `un_vessel` |
| Company IMO | `IMO number` / `IMO company` / `(IMO) Number` / `Company Number IMO` → `un_identifier` as `IMO company number` only |

| Entry | Ref | Result |
|-------|-----|--------|
| AVAX | LYe.006 | **Included** — IMO `9058713` |
| OCEAN MARITIME MANAGEMENT (OMM) | KPe.020 | **Excluded** — `1790183` stored as company number |

Dry-run after fix:

```
Total entries: 1010
Entries with an IMO (check digit OK): 1
Sample vessels: AVAX imo=9058713 ref=LYe.006
```

Tests: `test_omm_excluded_avax_included` + full `restapi/tests` / related backend tests — **pass** before merge.

---

## 2. Git

| Step | Result |
|------|--------|
| Branch | `mantis-un-sanctions` |
| Commit | `ec6ebd2` — Add UN Security Council sanctions ingest and API merge |
| Merge | Fast-forward `main` ← branch |
| Push | `origin/mantis-un-sanctions`, `origin/main` |

---

## 3. DDL applied (approved only)

Applied once via ingest `CREATE … IF NOT EXISTS` / `CREATE OR REPLACE VIEW` on pnav:

- `un_ingest_run`, `un_entry`, `un_aka`, `un_identifier` + indexes  
- view `un_vessel`  

**No other DDL.**

---

## 4. Ingest row counts (pnav)

| Object | Count / value |
|--------|----------------|
| `un_entry` | **1010** |
| `un_vessel` | **1** — AVAX / `9058713` / LYe.006 |
| `un_ingest_run` | **1** (`rows_loaded=1010`, `vessel_count=1`, publish `2026-10-20T23:00:05.952Z`) |
| `un_aka` | 2766 |
| `un_identifier` | 17 (`IMO`×1, `IMO company number`×16) |

Host: srv-dp `.200` — `/home/srvadmin/sts/un_sanctions_ingest.py`  
One-shot: `python3 un_sanctions_ingest.py --xml data/un_consolidated.xml`

---

## 5. Cron (srv-dp `.200`)

OFAC cron was **not** present on the host (empty user/root crontab; no `ofac_sdn_ingest.py` on disk). UN cron added **5 minutes after** the documented OFAC time (`15 8`):

```
20 8 * * * /usr/bin/python3 /home/srvadmin/sts/un_sanctions_ingest.py >> /var/log/un_ingest.log 2>&1
```

Log: `/var/log/un_ingest.log` (owned by `srvadmin`).

---

## 6. Backups + restarts

| Host | Backup | `PRE_DEPLOY.txt` |
|------|--------|------------------|
| `.200` srv-dp `/home/srvadmin/sts` | `backup-20261021-071811` | `BACKUP_NAME=backup-20261021-071811`, PREV=`7c26744`, NEW=`ec6ebd2` |
| `.201` srv-01 `/home/srvadmin/py/mantis` | `backup-20261021-071811` | same stamp / commits |

| Restart | Service |
|---------|---------|
| `.201` | `ais_mantis_api` only → **active** |
| `.200` | none (ingest script + cron only) |

---

## 7. Verification

### Index / labels (live on `.201`)

| Check | Result |
|-------|--------|
| Log | `Sanctions lookup: 1520 IMO keys, 790 MMSI keys` |
| OFAC IMO keys | **1519** (unchanged set) + **1** UN → total **1520** |
| MMSI keys | **790** (unchanged) |
| UN IMO keys | **1** (`9058713`) |
| `match_vessel(9058713)` | `sanctions_list=UN`, `list_names=['UN']`, confirmed |

### Sanctions vessel list

| | Before | After |
|--|-------:|------:|
| Total vessels | 1524 | **1525** |
| SDN | 1524 | **1524** |
| UN | 0 | **1** (AVAX `9058713`) |

### AVAX in AIS (ClickHouse `56.69.44.39:8123`, last 7 days)

Bounded read-only:

- Postgres `ais_static` for IMO `9058713`: **0** rows  
- ClickHouse `pnav.ais_static` where `imo` matches `9058713` and `ts` ≥ 7d: **0** rows  

**AVAX not seen in AIS in the last 7 days.** Label path verified via live index unit check above.

### Endpoint health (all HTTP 200)

| Endpoint | Before count | After count | Before s | After s (cold) | Warm s |
|----------|-------------:|------------:|---------:|---------------:|-------:|
| `/mantis/sanctions` | 1524 | 1525 (+1 UN) | 0.32 | 0.76 | — |
| `/mantis/sts-activities` | pairCount 7 | 7 | 0.72 | 16.16* | **0.44–0.51** |
| `/mantis/sts-review` | 17146 | 17146 | 0.17 | 0.17 | — |
| `/mantis/illegal-anchoring` | 867 | 868† | 1.73 | 1.62 | — |
| `/mantis/darkvessels` | 202 | 200† | 1.34 | 1.81 | — |
| `/mantis/identity-conflict` | groups 653 | 653 | 7.29 | 7.19 | — |
| `/mantis/overview` | OK | OK | 3.06 | 16.71* | **~0–3** |

\* First call after API restart (cold cache).  
† Live AIS churn (±2); not a sanctions regression.  
Existing fields (`sanctionsMatch`, `matchConfidence`, `sanctionsList`) unchanged in shape; additive `listNames` when matched.

---

## 8. Rollback command (code only; leave UN tables)

If needed, restore from this backup (do **not** drop `un_*`):

```bash
# .201
APP=/home/srvadmin/py/mantis
BAK=$APP/backup-20261021-071811
cp -a "$BAK/code/"*.py "$APP/"
sudo systemctl restart ais_mantis_api

# .200
APP=/home/srvadmin/sts
BAK=$APP/backup-20261021-071811
# remove new ingest if rolling back script only:
# rm -f $APP/un_sanctions_ingest.py
# crontab -l | grep -v un_sanctions_ingest.py | crontab -
cp -a "$BAK/code/"*.py "$APP/" 2>/dev/null || true
```

UN tables/view in pnav stay in place per deploy policy.

---

## 9. Files deployed

| Host | Paths |
|------|--------|
| `.200` | `un_sanctions_ingest.py`, `data/un_consolidated.xml`, `DEPLOYED_COMMIT.txt` |
| `.201` | `sanctions.py`, `tests/test_sanctions_un.py`, `DEPLOYED_COMMIT.txt` |

---

## 10. Notes

- Documented OFAC cron (`15 8` → `/opt/PySTS/...`) was not installed on srv-dp; UN cron uses `/home/srvadmin/sts/un_sanctions_ingest.py` at `20 8`.
- Helper snapshot script used for verify: `reports/_un_api_snapshot.py` (local/ops only).
