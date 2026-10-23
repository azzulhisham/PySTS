# Deploy: identity risk Phase 2 payloads (`298a5d2`)

**Date (UTC):** 2026-10-23  
**Commits pushed:** `0e9be04` (reports archive) → `298a5d2` — *Add identity risk labels to identity-conflict and vessel-analysis.*  
**Host:** srv-01 `10.10.20.201` only (`/home/srvadmin/py/mantis`, `ais_mantis_api` `:8085`)  
**Backup stamp:** `backup-20261023-062417`  
**No DDL. No pnav writes. srv-dp / job / cron / polygons / pyvam-\* / ais_tss_aisposition / pycpm-processor untouched.**  
Prior build report saved as [`reports/risk-payload-phase2-build.md`](risk-payload-phase2-build.md).

---

## 1. Push

| Item | Result |
|------|--------|
| `git push origin main` | **OK** — `ed6c239..298a5d2` (includes `0e9be04` + `298a5d2`) |

---

## 2. Pre-copy safety diffs (srv-01 vs `c7a5bd5`)

| Remote file | vs `c7a5bd5` | Action |
|-------------|--------------|--------|
| `identity_conflict.py` | **MATCH** | replace |
| `spoofing_vessel_analysis.py` | **MATCH** | replace |
| `main.py` | **MATCH** | replace |
| `ship_risk_attach.py` | **MATCH** | replace |
| `static/swagger.json` | **MATCH** | replace |

**STOP condition not triggered.**

---

## 3. Backup

| Item | Path |
|------|------|
| Backup dir | `/home/srvadmin/py/mantis/backup-20261023-062417/` |
| Code | `…/code/{identity_conflict,spoofing_vessel_analysis,main,ship_risk_attach}.py` |
| Swagger | `…/static/swagger.json` |
| Manifest | `…/PRE_DEPLOY.txt` |

---

## 4. Files copied (flat layout)

| Local → remote | Result |
|----------------|--------|
| `restapi/identity_conflict.py` | updated |
| `restapi/spoofing_vessel_analysis.py` | updated |
| `restapi/main.py` | updated |
| `restapi/ship_risk_attach.py` | updated |
| `restapi/static/swagger.json` | updated |

Import check (service Python + `mantis.env`): **IMPORT_OK** lookback 24, TTL 300; `strip_risk_keys` preserves spoofing `riskLevel`.

---

## 5. Restart / health

| Check | Result |
|-------|--------|
| `systemctl restart ais_mantis_api` | **OK** (06:25:06 UTC) |
| `ais_mantis_api` | **active** (PID 1353518 → **1391794**) |
| `pyvam-api` | **active**, PID **1390232** unchanged |
| `pyvam-processor` | **active**, PID **1390231** unchanged |
| `pyvam-dash` | **active**, PID **1390233** unchanged |
| `GET http://127.0.0.1:8085/` | **401** |
| journalctl ~2 min | gunicorn start OK; polygons loaded; **no Traceback** |

Unauthenticated curl logged expected INFO `authorize_user … Not enough segments` (not a crash).

---

## 6. Handler verification (no JWT — direct Python on srv-01)

Read-only detector calls with service Python / `mantis.env`. No token minting.

### Identity-conflict

| Check | Result |
|-------|--------|
| New fields on identities | **present** (`riskRating` / `riskScore` / `riskReason`) |
| Group `riskRedCount` | **present** |
| Totals | **RED 126 / AMBER 1,158** (identities 1,323 / groups 654) |
| CHENG HE (`613471907_667002010`) | both sides **RED 100** — `reflag_dual_mmsi; sanctions match (UK)`; group `riskRedCount=2` |
| Golden strip | strip identity-risk keys → **no** `risk*` leftovers |

### Vessel-analysis — AMINA (`422035100`)

| Field | Value |
|-------|-------|
| `riskRating` | **RED** |
| `riskScore` | **100** |
| `riskReason` | `sanctions match (SDN)` |
| Event `riskLevel` | unchanged (`critical` / `data quality`); **no** identity `riskRating` on events |

### Phase 1 still labelled

| Endpoint | risk fields | Counts (spot) |
|----------|-------------|----------------|
| sts-activities | pair `riskRedCount` + vessel `riskRating` | RED 0 / AMBER 0 (4 pairs) |
| darkvessels | vessel `riskRating` | RED 9 / AMBER 7 |
| illegal-anchoring | vessel `riskRating` | RED 53 / AMBER 58 |
| loitering | event `riskRating` (or empty-safe) | RED 0 / AMBER 0 (4 events) |
| spoofing | anomaly `riskRating` | RED 3 / AMBER 0 |

---

## 7. Rollback

```bash
# SSH as srvadmin@10.10.20.201
BK=/home/srvadmin/py/mantis/backup-20261023-062417
cp -a $BK/code/identity_conflict.py /home/srvadmin/py/mantis/identity_conflict.py
cp -a $BK/code/spoofing_vessel_analysis.py /home/srvadmin/py/mantis/spoofing_vessel_analysis.py
cp -a $BK/code/main.py /home/srvadmin/py/mantis/main.py
cp -a $BK/code/ship_risk_attach.py /home/srvadmin/py/mantis/ship_risk_attach.py
cp -a $BK/static/swagger.json /home/srvadmin/py/mantis/static/swagger.json
sudo systemctl restart ais_mantis_api
# do not touch pyvam-* / srv-dp / cron / polygons
```

---

## 8. Decision

| Item | Status |
|------|--------|
| Push `0e9be04` + `298a5d2` | Done |
| Backup | `backup-20261023-062417` |
| Diff vs `c7a5bd5` | Clean |
| Copy + import | Done |
| Restart `ais_mantis_api` only | Done |
| pyvam PIDs unchanged | Done |
| No Tracebacks | Done |
| Handler verify (no JWT) | Done — CHENG HE RED; AMINA RED; Phase 1 intact |
| Rollback | Documented |

**GO** — Phase 2 identity-risk fields live on srv-01.
