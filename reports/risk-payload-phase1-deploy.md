# Deploy: identity risk Phase 1 payloads (`c7a5bd5`)

**Date (UTC):** 2026-10-22  
**Commit:** `c7a5bd5` — *Add identity risk labels to Phase 1 MANTIS payloads.*  
**Host:** srv-01 `10.10.20.201` only (`/home/srvadmin/py/mantis`, `ais_mantis_api` `:8085`)  
**Backup stamp:** `backup-20261022-082324`  
**No DDL. No pnav writes. srv-dp / identity_risk job / cron / pyvam-\* untouched.**  
Prior build report saved as [`reports/risk-payload-phase1-build.md`](risk-payload-phase1-build.md).

---

## 1. Push

| Item | Result |
|------|--------|
| `git push origin main` | **OK** — `972d1fa..c7a5bd5` |

---

## 2. Pre-copy safety diffs (srv-01 vs `972d1fa`)

Every file to be replaced was SHA-compared to `git show 972d1fa:restapi/…`. No local drift.

| Remote file | vs `972d1fa` | Action |
|-------------|--------------|--------|
| `ship_risk_attach.py` | absent (new) | create |
| `sts_detection.py` | **MATCH** | replace |
| `dark_vessels.py` | **MATCH** | replace |
| `illegal_anchoring.py` | **MATCH** | replace |
| `loitering.py` | **MATCH** | replace |
| `spoofing.py` | **MATCH** | replace |
| `main.py` | **MATCH** | replace |
| `static/swagger.json` | **MATCH** | replace |

**STOP condition not triggered.**

---

## 3. Backup (srv-01)

| Item | Path |
|------|------|
| Backup dir | `/home/srvadmin/py/mantis/backup-20261022-082324/` |
| Prior code | `…/code/{sts_detection,dark_vessels,illegal_anchoring,loitering,spoofing,main}.py` |
| Prior swagger | `…/static/swagger.json` |
| Manifest | `…/PRE_DEPLOY.txt` |

`ship_risk_attach.py` was new (not present before); listed in `PRE_DEPLOY.txt` as new.

---

## 4. Files copied (flat layout)

| Local → remote | Result |
|----------------|--------|
| `restapi/ship_risk_attach.py` → `/home/srvadmin/py/mantis/ship_risk_attach.py` | **new** |
| `restapi/sts_detection.py` → `…/sts_detection.py` | updated |
| `restapi/dark_vessels.py` → `…/dark_vessels.py` | updated |
| `restapi/illegal_anchoring.py` → `…/illegal_anchoring.py` | updated |
| `restapi/loitering.py` → `…/loitering.py` | updated |
| `restapi/spoofing.py` → `…/spoofing.py` | updated |
| `restapi/main.py` → `…/main.py` | updated |
| `restapi/static/swagger.json` → `…/static/swagger.json` | updated |

`DEPLOYED_COMMIT.txt` updated to `c7a5bd5` / `BACKUP=backup-20261022-082324`.

**Not copied / not touched:** `identity_risk.py`, `identity_risk_job.py`, `polygons.py`, srv-dp, cron, pyvam-\*.

---

## 5. Import + read-only DB check

Service Python: `/usr/bin/python3` (gunicorn shebang). Working directory `/home/srvadmin/py/mantis`, env from `mantis.env`.

| Check | Result |
|-------|--------|
| `import ship_risk_attach` (+ detectors + `main`) | **IMPORT_OK** — lookback 24, TTL 300 |
| `SELECT count(*) FROM ais_ship_risk_rating WHERE lookback_months=24` | **2610** (`RATING_COUNT_OK`) |
| Risk helpers wired on detectors | **WIRE_OK** |

No DDL / no writes.

---

## 6. Restart / health

| Step | Result |
|------|--------|
| `systemctl restart ais_mantis_api` | **OK** (08:24:17 UTC) |
| `systemctl is-active ais_mantis_api` | **active** |
| `systemctl is-active pyvam-api` | **active** (untouched) |
| `GET http://127.0.0.1:8085/` (no auth) | **401** |
| journalctl first ~1 min | gunicorn start OK; polygons loaded on both workers; **no Traceback** |
| Live traffic after restart | `GET /mantis/sts-review/notifications` → **200** (existing client JWT) |

Note: journal shows `INFO [authorize_user] error: Not enough segments` on the unauthenticated curl to `/` — expected for missing JWT, not a worker crash.

HTTP JWT Phase 1 payload smoke checks **skipped** (no minting; Zulhisham’s assistant will cover).

---

## 7. Rollback

```bash
# SSH as srvadmin@10.10.20.201
BK=/home/srvadmin/py/mantis/backup-20261022-082324
cp -a $BK/code/sts_detection.py /home/srvadmin/py/mantis/sts_detection.py
cp -a $BK/code/dark_vessels.py /home/srvadmin/py/mantis/dark_vessels.py
cp -a $BK/code/illegal_anchoring.py /home/srvadmin/py/mantis/illegal_anchoring.py
cp -a $BK/code/loitering.py /home/srvadmin/py/mantis/loitering.py
cp -a $BK/code/spoofing.py /home/srvadmin/py/mantis/spoofing.py
cp -a $BK/code/main.py /home/srvadmin/py/mantis/main.py
cp -a $BK/static/swagger.json /home/srvadmin/py/mantis/static/swagger.json
rm -f /home/srvadmin/py/mantis/ship_risk_attach.py   # was new in c7a5bd5
sudo systemctl restart ais_mantis_api
# do not touch pyvam-*
```

---

## 8. Decision

| Item | Status |
|------|--------|
| Push `c7a5bd5` | Done |
| srv-01 backup | `backup-20261022-082324` |
| Pre-diff vs `972d1fa` | Clean |
| Copy + import + rating count | Done |
| `ais_mantis_api` active / `/` → 401 | Done |
| pyvam-api still active | Done |
| No Tracebacks in first minute | Done |
| JWT HTTP checks | Skipped (by request) |
| srv-dp / job / cron | Not touched |
| Rollback | Documented |

**GO** — Phase 1 identity-risk payload fields live on srv-01.
