# Deploy: identity risk Phase 4 overview + sanctions (`228c19b`)

**Date (UTC):** 2026-10-23  
**Commits pushed:** `c8bc22e` → `6ba1a3b` → `228c19b`  
**Host:** srv-01 `10.10.20.201` only (`/home/srvadmin/py/mantis`, `ais_mantis_api` `:8085`)  
**Backup stamp:** `backup-20261023-073511`  
**No DDL. No pnav writes. srv-dp untouched (including its `sanctions.py`). job / cron / polygons / pyvam-\* / ais_tss_aisposition / pycpm-processor untouched.**  
Prior build report saved as [`reports/risk-payload-phase4-build.md`](risk-payload-phase4-build.md).

---

## 1. Push

| Item | Result |
|------|--------|
| `git push origin main` | **OK** — `5032970..228c19b` (includes `c8bc22e`, `6ba1a3b`, `228c19b`) |

---

## 2. Pre-copy safety diffs (srv-01 vs `7f52efb`)

Every file to be replaced was SHA-256 compared to `git show 7f52efb:restapi/…`.

| Remote file | vs `7f52efb` | Action |
|-------------|--------------|--------|
| `mantis_overview.py` | **MATCH** | replace |
| `sanctions.py` | **MATCH** | replace |
| `ship_risk_attach.py` | **MATCH** | replace |
| `main.py` | **MATCH** | replace |
| `static/swagger.json` | **MATCH** | replace |

**STOP condition not triggered.**

### `sanctions.py`: srv-01 vs srv-dp (read-only)

| Location | Path | SHA-256 | vs git |
|----------|------|---------|--------|
| srv-01 **before** deploy | `/home/srvadmin/py/mantis/sanctions.py` | `20b607fd…` | = `7f52efb` |
| srv-01 **after** deploy | same | `98108310…` | = `228c19b` |
| srv-dp (untouched) | `/home/srvadmin/sts/restapi/sanctions.py` | `20b607fd…` | = `7f52efb` (pre–Phase 4) |

**srv-01 and srv-dp `sanctions.py` now differ** (by design): API host has Phase 4 attach; job host keeps the prior copy for `identity_risk_job`. srv-dp was only hashed; nothing was written or restarted there.

---

## 3. Backup (srv-01)

| Item | Path |
|------|------|
| Backup dir | `/home/srvadmin/py/mantis/backup-20261023-073511/` |
| Prior code | `…/code/{mantis_overview,sanctions,ship_risk_attach,main}.py` |
| Prior swagger | `…/static/swagger.json` |
| Manifest | `…/PRE_DEPLOY.txt` (`PREV_COMMIT=7f52efb`, `NEW_COMMIT=228c19b`) |

---

## 4. Files copied (flat layout)

| Local → remote | Result |
|----------------|--------|
| `restapi/mantis_overview.py` | updated |
| `restapi/sanctions.py` | updated |
| `restapi/ship_risk_attach.py` | updated |
| `restapi/main.py` | updated |
| `restapi/static/swagger.json` | updated |

Import check (service Python + `mantis.env`, no secret echo): **IMPORT_OK** — lookback 24, TTL 300; `rated_ship_count` present.

`DEPLOYED_COMMIT.txt` → `228c19b` / `BACKUP=backup-20261023-073511`.

---

## 5. Restart / health

| Check | Result |
|-------|--------|
| `systemctl restart ais_mantis_api` | **OK** (07:35:21 UTC) |
| `ais_mantis_api` | **active** (PID 1409830 → **1444484**) |
| `pyvam-api` | **active**, PID **1390232** unchanged |
| `pyvam-processor` | **active**, PID **1390231** unchanged |
| `pyvam-dash` | **active**, PID **1390233** unchanged |
| `GET http://127.0.0.1:8085/` | **401** |
| journalctl ~2–3 min | gunicorn start OK; polygons loaded; Traceback count **0** |
| Live traffic | `GET /mantis/sts-review/notifications` → **200** (existing client JWT; not minted by us) |

---

## 6. Handler verification (no JWT — direct Python on srv-01)

### Overview

| Check | Result |
|-------|--------|
| `riskRatedShipCount` | **2619** |
| Module `riskRedCount` / `riskAmberCount` | present on dark / STS / illegal |
| Existing sanctions counts | still present (dark sanctions **9**; STS vessel **0**; illegal **51**) |
| Module spot | dark RED **9** / AMBER **7**; STS RED **0** / AMBER **1**; illegal RED **53** / AMBER **55** |

### Sanctions

| Check | Result |
|-------|--------|
| Vessel count | **1949** |
| RED / AMBER | **1947** / **0** |
| GREEN listed | **BONU 5**, **MAR AZUL** (null IMO/MMSI) |
| Golden strip | **OK** — pre-existing keys/values unchanged |

### Phases 1–3 still labelled

| Check | Result |
|-------|--------|
| illegal-anchoring | RED **53** / AMBER **55** (sanctions **53**) |
| identity-conflict | RED **126** / AMBER **1,156** |
| AMINA vessel-analysis | **RED 100** — `sanctions match (SDN)` |
| sts-review list | risk fields present (page sample AMBER **1**) |

---

## 7. Rollback

```bash
# SSH as srvadmin@10.10.20.201
BK=/home/srvadmin/py/mantis/backup-20261023-073511
cp -a $BK/code/mantis_overview.py /home/srvadmin/py/mantis/mantis_overview.py
cp -a $BK/code/sanctions.py /home/srvadmin/py/mantis/sanctions.py
cp -a $BK/code/ship_risk_attach.py /home/srvadmin/py/mantis/ship_risk_attach.py
cp -a $BK/code/main.py /home/srvadmin/py/mantis/main.py
cp -a $BK/static/swagger.json /home/srvadmin/py/mantis/static/swagger.json
sudo systemctl restart ais_mantis_api
# do not touch pyvam-* / srv-dp / cron / polygons
```

---

## 8. Decision

| Item | Status |
|------|--------|
| Push `c8bc22e` + `6ba1a3b` + `228c19b` | Done |
| Backup | `backup-20261023-073511` |
| Diff vs `7f52efb` | Clean |
| srv-dp `sanctions.py` left at `7f52efb` hash | Confirmed (differs from srv-01 after deploy) |
| Copy + import | Done |
| Restart `ais_mantis_api` only | Done |
| pyvam PIDs unchanged | Done |
| No Tracebacks | Done |
| Handler verify (no JWT) | Done — overview + sanctions + Phases 1–3 |
| Rollback | Documented |

**GO** — Phase 4 identity-risk fields live on `/mantis/overview` and `/mantis/sanctions` on srv-01.
