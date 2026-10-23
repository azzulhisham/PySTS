# Deploy: identity risk Phase 5 vessel-timeline + vessel-track (`e1c481c`)

**Date (UTC):** 2026-10-23  
**Commits pushed:** `bfb3239` → `92e0741` → `e1c481c`  
**Host:** srv-01 `10.10.20.201` only (`/home/srvadmin/py/mantis`, `ais_mantis_api` `:8085`)  
**Backup stamp:** `backup-20261023-075610`  
**No DDL. No pnav writes. srv-dp / job / cron / polygons / pyvam-\* / ais_tss_aisposition / pycpm-processor untouched.**  
Prior build report saved as [`reports/risk-payload-phase5-build.md`](risk-payload-phase5-build.md).

---

## 1. Push

| Item | Result |
|------|--------|
| `git push origin main` | **OK** — `228c19b..e1c481c` (includes `bfb3239`, `92e0741`, `e1c481c`) |

---

## 2. Pre-copy safety diffs (srv-01 vs `228c19b`)

| Remote file | vs `228c19b` | Action |
|-------------|--------------|--------|
| `ship_risk_attach.py` | **MATCH** | replace |
| `timelineplayback.py` | **MATCH** | replace |
| `main.py` | **MATCH** | replace |
| `static/swagger.json` | **MATCH** | replace |

**STOP condition not triggered.**

---

## 3. Backup (srv-01)

| Item | Path |
|------|------|
| Backup dir | `/home/srvadmin/py/mantis/backup-20261023-075610/` |
| Prior code | `…/code/{ship_risk_attach,timelineplayback,main}.py` |
| Prior swagger | `…/static/swagger.json` |
| Manifest | `…/PRE_DEPLOY.txt` (`PREV_COMMIT=228c19b`, `NEW_COMMIT=e1c481c`) |

---

## 4. Files copied

| Local → remote | Result |
|----------------|--------|
| `restapi/ship_risk_attach.py` | updated |
| `restapi/timelineplayback.py` | updated |
| `restapi/main.py` | updated |
| `restapi/static/swagger.json` | updated |

Import check: **IMPORT_OK** — lookback 24, TTL 300; `risk_http_headers` / `risk_payload_for_mmsi` present.

`DEPLOYED_COMMIT.txt` → `e1c481c` / `BACKUP=backup-20261023-075610`.

---

## 5. Restart / health

| Check | Result |
|-------|--------|
| `systemctl restart ais_mantis_api` | **OK** (07:56:20 UTC) |
| `ais_mantis_api` | **active** (PID 1444484 → **1462290**) |
| `pyvam-api` | **active**, PID **1390232** unchanged |
| `pyvam-processor` | **active**, PID **1390231** unchanged |
| `pyvam-dash` | **active**, PID **1390233** unchanged |
| `GET http://127.0.0.1:8085/` | **401** |
| journalctl ~2–3 min | gunicorn start OK; polygons loaded; Traceback count **0** |
| Live traffic | `GET /mantis/sts-review/notifications` → **200** (existing client JWT; not minted by us) |

---

## 6. Handler verification (no JWT)

### Phase 5

| Check | Result |
|-------|--------|
| Timeline AMINA `422035100` | **RED 100** — `sanctions match (SDN)`; 4 events; golden strip **OK** |
| Track headers | `X-Risk-Rating=RED`, `X-Risk-Score=100`, reason decoded `sanctions match (SDN)` |
| Track first NDJSON line | `type=meta`; **no** risk keys |

### Phases 1–4 still labelled

| Check | Result |
|-------|--------|
| overview `riskRatedShipCount` | **2619** |
| sanctions list | **1949** vessels; RED **1947** |
| illegal-anchoring | RED **53** / AMBER **55** |
| identity-conflict | RED **126** / AMBER **1,156** |

---

## 7. Rollback

```bash
# SSH as srvadmin@10.10.20.201
BK=/home/srvadmin/py/mantis/backup-20261023-075610
cp -a $BK/code/ship_risk_attach.py /home/srvadmin/py/mantis/ship_risk_attach.py
cp -a $BK/code/timelineplayback.py /home/srvadmin/py/mantis/timelineplayback.py
cp -a $BK/code/main.py /home/srvadmin/py/mantis/main.py
cp -a $BK/static/swagger.json /home/srvadmin/py/mantis/static/swagger.json
sudo systemctl restart ais_mantis_api
# do not touch pyvam-* / srv-dp / cron / polygons
```

---

## 8. Decision

| Item | Status |
|------|--------|
| Push `bfb3239` + `92e0741` + `e1c481c` | Done |
| Backup | `backup-20261023-075610` |
| Diff vs `228c19b` | Clean |
| Copy + import | Done |
| Restart `ais_mantis_api` only | Done |
| pyvam PIDs unchanged | Done |
| No Tracebacks | Done |
| Handler verify (no JWT) | Done — timeline RED; track headers; Phases 1–4 intact |
| Rollback | Documented |

**GO** — Phase 5 identity-risk fields live on vessel-timeline and vessel-track (headers) on srv-01.
