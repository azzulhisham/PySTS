# Identity risk rating — deploy report

**Date (UTC):** 2026-10-22  
**Commits:** `04460a6` + `dc6e221` + path fix `377001e`  
**Hosts:** srv-dp `10.10.20.200` (`/home/srvadmin/sts`), srv-01 `10.10.20.201` (`/home/srvadmin/py/mantis`)  
**Backup stamp:** `backup-20261022-051912`  
**No DDL. No EU. pyvam-* not touched.**

---

## 1. srv-dp — backup

| Item | Path |
| --- | --- |
| Backup folder | `/home/srvadmin/sts/backup-20261022-051912/` |
| Tree archive | `sts-tree.tgz` (184034 bytes) |
| Code snapshot | `code/*.py`, `restapi/*.py` |
| Crontab before | `crontab-before.txt` (+ copy `crontab-before-20261022-051912.txt`) |
| Crontab after | `crontab-after.txt` (+ copy `crontab-after-20261022-051912.txt`) |
| Manual run log | `manual_identity_risk.log` |
| Marker | `PRE_DEPLOY.txt`, updated `DEPLOYED_COMMIT.txt` |

---

## 2. srv-dp — files copied

| Local → remote | Action |
| --- | --- |
| `backend/identity_risk_job.py` → `/home/srvadmin/sts/identity_risk_job.py` | **copied** (includes `377001e` path fix for `sts/restapi`) |
| `restapi/identity_risk.py` → `/home/srvadmin/sts/restapi/identity_risk.py` | **copied** (new) |
| `restapi/sanctions.py` → `/home/srvadmin/sts/restapi/sanctions.py` | **copied** (server copy was pre-UK merge; job needs `LIST_PRIORITY` + merged `load_ofac_vessel_list`) |
| `ais_identity.py`, `identity_conflict.py` | **unchanged** (already matched git SHA) |
| `backend/polygons.py` | **NOT copied** |

### polygons.py decision

srv-dp `/home/srvadmin/sts/polygons.py` is the **restapi anchorage catalogue** and is imported by:

- `vesselloiteringdetection.py` (`from polygons import anchorage_areas, is_excl_name, restricted_limit`)
- `vesselstsreview.py` (`from polygons import anchorage_areas, is_excl_name`)

Replacing it with laptop `backend/polygons.py` would risk breaking those processors. Identity-risk job does **not** import `polygons`. Left server file alone.

### Import check (`/usr/bin/python3`)

```
IMPORT_OK v1.1-identity-risk-ll-mmsi LIST_PRIORITY ['SDN', 'CONS', 'UN', 'UK']
job_restapi /home/srvadmin/sts/restapi
```

---

## 3. srv-dp — manual job run

Log (`manual_identity_risk.log`):

```
Loading sanctions indexes…
Snapshotting Lloyd's MMSI mismatches for 2026-10-22…
Lloyd's mismatches kept after filters: 92
Collecting identity events…
Sanctions lookup: 1927 IMO keys, 824 MMSI keys
identity_risk_job done: {
  "snap_date": "2026-10-22",
  "lloyds_snap_rows": 92,
  "lloyds_by_conflict_class": {"stale_registry": 83, "true_conflict": 9},
  "events_upserted": 756,
  "events_by_type": {
    "dual_track_mmsi": 2,
    "lloyds_mmsi_imo_conflict": 92,
    "mmsi_multi_imo_static": 5,
    "mmsi_reused_static_evt": 4,
    "reflag_dual_mmsi": 653
  },
  "events_by_confidence": {"high": 11, "low": 83, "medium": 9, "reflag": 653},
  "ratings_upserted": 744,
  "legacy_duplicate_keys_avoided": 851,
  "lookback_months": 24,
  "elapsed_sec": 15.124,
  "rule_version": "v1.1-identity-risk-ll-mmsi"
}
```

| Expectation | Observed |
| --- | --- |
| ~756 events | **756** |
| ~744 ratings | **744** |
| ~83 RED (76 sanctioned) | unchanged from prior laptop backfill (job upserted same slice) |
| ~9 s | **15.1 s** (acceptable; host/network) |

---

## 4. srv-dp — crontab

Log file: `/var/log/identity_risk.log` owned `srvadmin:srvadmin`, mode `664`.

### Before

```
20 8 * * * /usr/bin/python3 /home/srvadmin/sts/un_sanctions_ingest.py >> /var/log/un_ingest.log 2>&1
10 8 * * * /usr/bin/python3 /home/srvadmin/sts/ofac_sdn_ingest.py  >> /var/log/ofac_sdn_ingest.log 2>&1
15 8 * * * /usr/bin/python3 /home/srvadmin/sts/ofac_cons_ingest.py >> /var/log/ofac_cons_ingest.log 2>&1
30 8 * * * /usr/bin/python3 /home/srvadmin/sts/uk_sanctions_ingest.py >> /var/log/uk_ingest.log 2>&1
```

### After (+ one line only)

```
20 8 * * * /usr/bin/python3 /home/srvadmin/sts/un_sanctions_ingest.py >> /var/log/un_ingest.log 2>&1
10 8 * * * /usr/bin/python3 /home/srvadmin/sts/ofac_sdn_ingest.py  >> /var/log/ofac_sdn_ingest.log 2>&1
15 8 * * * /usr/bin/python3 /home/srvadmin/sts/ofac_cons_ingest.py >> /var/log/ofac_cons_ingest.log 2>&1
30 8 * * * /usr/bin/python3 /home/srvadmin/sts/uk_sanctions_ingest.py >> /var/log/uk_ingest.log 2>&1
40 8 * * * /usr/bin/python3 /home/srvadmin/sts/identity_risk_job.py >> /var/log/identity_risk.log 2>&1
```

---

## 5. srv-01 — backup

| Item | Path |
| --- | --- |
| Backup folder | `/home/srvadmin/py/mantis/backup-20261022-051912/` |
| Tree archive | `mantis-tree.tgz` (104795 bytes) |
| Code snapshot | `code/main.py`, `sanctions.py`, … |
| Swagger snapshot | `static/swagger.json` |
| Marker | `PRE_DEPLOY.txt` |

### `main.py` diff vs git

Server `main.py` had **no unique local changes**. Diff vs git was only the missing identity-risk import + `/mantis/ship-risk` + `/mantis/identity-events` routes. Safe to replace.

---

## 6. srv-01 — files copied / restart

| File | Action |
| --- | --- |
| `restapi/identity_risk.py` → `…/mantis/identity_risk.py` | copied |
| `restapi/main.py` → `…/mantis/main.py` | copied |
| `restapi/static/swagger.json` → `…/mantis/static/swagger.json` | copied |

Restart: **`systemctl restart ais_mantis_api` only**.

| Service | After |
| --- | --- |
| ais_mantis_api | active |
| pyvam-api | active (untouched) |
| pyvam-processor | active (untouched) |
| pyvam-dash | active (untouched) |

Import check before restart: `IMPORT_OK v1.1-identity-risk-ll-mmsi`, routes registered.

---

## 7. API verification (token not printed)

Auth: short-lived JWT minted on-host (`id` + `role` + `expiredDate`), called via `127.0.0.1:8085`.

| Check | Result |
| --- | --- |
| `GET /mantis/ship-risk?imo=9224805` (OXIS) | **200** — rating **RED**, score **100**, `factors.reason=sanctions_match`, `sanctionsMatch=true` |
| `GET /mantis/ship-risk?mmsi=352003715` (SEA PEARL) | **200** — rating **RED**, score **100**, name SEA PEARL, `factors.reason=high_mmsi_misuse`, factors present |
| `GET /mantis/identity-events?sanctionsOnly=true&limit=20` | **200** — returnedCount **20**, totalCount **77** |
| `GET /mantis/sanctions` | **200** — **1940** vessels |
| `GET /mantis/sts-activities` | **200** |
| `GET /static/swagger.json` | **200** — paths include `/mantis/ship-risk` and `/mantis/identity-events` |

---

## 8. Journal watch (10 minutes)

| Item | Result |
| --- | --- |
| Window | ~05:22:53Z → ~05:32:53Z UTC |
| Tracebacks | **0** |
| Application exceptions | **0** |
| Notes | Earlier `[authorize_user] error: Not enough segments` INFO lines were from failed verify attempts before the correct JWT shape; successful 200s for ship-risk / identity-events / sanctions / sts-activities followed. No rollback. |
| Decision | **GO** |

---

## 9. Rollback steps

### srv-dp

```bash
# SSH as srvadmin@10.10.20.200
BK=/home/srvadmin/sts/backup-20261022-051912
# remove cron line
crontab -l | grep -v identity_risk_job.py | crontab -
# restore files
cp -a $BK/code/. /home/srvadmin/sts/   # only if needed for other py files
cp -a $BK/restapi/sanctions.py /home/srvadmin/sts/restapi/sanctions.py
rm -f /home/srvadmin/sts/identity_risk_job.py
rm -f /home/srvadmin/sts/restapi/identity_risk.py
# optional: restore full tree
# tar -C /home/srvadmin/sts -xzf $BK/sts-tree.tgz
```

### srv-01

```bash
# SSH as srvadmin@10.10.20.201
BK=/home/srvadmin/py/mantis/backup-20261022-051912
cp -a $BK/code/main.py /home/srvadmin/py/mantis/main.py
cp -a $BK/static/swagger.json /home/srvadmin/py/mantis/static/swagger.json
rm -f /home/srvadmin/py/mantis/identity_risk.py
sudo systemctl restart ais_mantis_api
# do not touch pyvam-*
```

---

## 10. Git notes

| Commit | Role |
| --- | --- |
| `04460a6` | Initial identity risk feature |
| `dc6e221` | Stale vs true-conflict tune |
| `377001e` | srv-dp `sts/restapi` import path (pushed with this deploy) |
