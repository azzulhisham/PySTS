# Deploy: identity risk Phase 3 STS review-case payloads (`7f52efb`)

**Date (UTC):** 2026-10-23  
**Commits pushed:** `f8e56ee` → `7f52efb` → `5032970`  
**Host:** srv-01 `10.10.20.201` only (`/home/srvadmin/py/mantis`, `ais_mantis_api` `:8085`)  
**Backup stamp:** `backup-20261023-065850`  
**No DDL. No pnav writes. POST verify not called. srv-dp / job / cron / polygons / pyvam-\* / ais_tss_aisposition / pycpm-processor untouched.**  
Prior build report saved as [`reports/risk-payload-phase3-build.md`](risk-payload-phase3-build.md).

---

## 1. Push

| Item | Result |
|------|--------|
| `git push origin main` | **OK** — `298a5d2..5032970` (includes `f8e56ee`, `7f52efb`, `5032970`) |

---

## 2. Pre-copy safety diffs (srv-01 vs `298a5d2`)

Every file to be replaced was SHA-256 compared to `git show 298a5d2:restapi/…`.

| Remote file | vs `298a5d2` | Action |
|-------------|--------------|--------|
| `sts_review.py` | **MATCH** | replace |
| `main.py` | **MATCH** | replace |
| `static/swagger.json` | **MATCH** | replace |

**STOP condition not triggered.**

---

## 3. Backup (srv-01)

| Item | Path |
|------|------|
| Backup dir | `/home/srvadmin/py/mantis/backup-20261023-065850/` |
| Prior code | `…/code/{sts_review,main}.py` |
| Prior swagger | `…/static/swagger.json` |
| Manifest | `…/PRE_DEPLOY.txt` (`PREV_COMMIT=298a5d2`, `NEW_COMMIT=7f52efb`) |

`ship_risk_attach.py` was **not** replaced (already live from Phase 1/2).

---

## 4. Files copied (flat layout)

| Local → remote | Result |
|----------------|--------|
| `restapi/sts_review.py` → `/home/srvadmin/py/mantis/sts_review.py` | updated (SHA `0c5756be…`) |
| `restapi/main.py` → `…/main.py` | updated (SHA `2a759a9a…`) |
| `restapi/static/swagger.json` → `…/static/swagger.json` | updated (SHA `4f5a2f03…`) |

Import check (service Python + `mantis.env`, no secret echo): **IMPORT_OK** — `cases_to_payload` / `risk_counts_from_cases` present; `DEFAULT_LOOKBACK_MONTHS=24`, `RISK_CACHE_TTL_S=300`.

`DEPLOYED_COMMIT.txt` → `7f52efb` / `BACKUP=backup-20261023-065850`.

---

## 5. Restart / health

| Check | Result |
|-------|--------|
| `systemctl restart ais_mantis_api` | **OK** (07:00:32 UTC) |
| `ais_mantis_api` | **active** (PID 1391794 → **1409830**) |
| `pyvam-api` | **active**, PID **1390232** unchanged |
| `pyvam-processor` | **active**, PID **1390231** unchanged |
| `pyvam-dash` | **active**, PID **1390233** unchanged |
| `GET http://127.0.0.1:8085/` (no auth) | **401** |
| journalctl ~2 min after restart | gunicorn start OK; polygons loaded on both workers; Traceback count **0** |
| Live traffic | `GET /mantis/sts-review/notifications` → **200** (existing client JWT; not minted by us) |

Unauthenticated curl logged expected INFO `authorize_user … Not enough segments` (not a crash).

---

## 6. Handler verification (no JWT — direct Python on srv-01)

Read-only helper calls with service Python / `mantis.env`. **No token minting. `verify_case` not called** (signature / `VERIFY_SQL` inspected only).

### STS review (Phase 3)

| Check | Result |
|-------|--------|
| `list_unexplained` (pageSize 20) | members have `riskRating`/`riskScore`/`riskReason`; cases have `riskRedCount`; totals **RED 0 / AMBER 4**; golden strip **OK** |
| `list_unreviewed` (pageSize 20) | same fields; **RED 0 / AMBER 4**; golden strip **OK** |
| `list_notifications` (24 h, area=all) | **110** cases; **RED 0 / AMBER 11**; golden strip **OK** |
| `verify_case` signature | `["case_id", "review_class", "labeled_by", "review_notes"]` unchanged |
| `VERIFY_SQL` | no risk columns |

Sample member (queue caseId 16): `riskRating=GREEN`, `riskScore=0`, `riskReason=none`; stored `sanctionsMatch=false` unchanged.

### Phase 2 still labelled

| Check | Result |
|-------|--------|
| identity-conflict totals | **RED 126 / AMBER 1,156** (spot; prior deploy noted 126 / 1,158 — small drift OK) |
| AMINA (`422035100`) vessel-analysis | **RED 100** — `sanctions match (SDN)` |

### Phase 1 still labelled

| Check | Result |
|-------|--------|
| `detect_sts_in_anchorages` | **4** pairs; top-level `risk_red_count` / `risk_amber_count` present (**0 / 0** this sample); ship-risk cache load OK (lookback 24) |

---

## 7. Rollback

```bash
# SSH as srvadmin@10.10.20.201
BK=/home/srvadmin/py/mantis/backup-20261023-065850
cp -a $BK/code/sts_review.py /home/srvadmin/py/mantis/sts_review.py
cp -a $BK/code/main.py /home/srvadmin/py/mantis/main.py
cp -a $BK/static/swagger.json /home/srvadmin/py/mantis/static/swagger.json
sudo systemctl restart ais_mantis_api
# do not touch pyvam-* / srv-dp / cron / polygons / ship_risk_attach.py
```

---

## 8. Decision

| Item | Status |
|------|--------|
| Push `f8e56ee` + `7f52efb` + `5032970` | Done |
| Backup | `backup-20261023-065850` |
| Diff vs `298a5d2` | Clean |
| Copy + import | Done |
| Restart `ais_mantis_api` only | Done |
| pyvam PIDs unchanged | Done |
| No Tracebacks (~2 min) | Done |
| Handler verify (no JWT / no POST verify) | Done — review risk fields + golden strip; Phase 1/2 intact |
| Rollback | Documented |

**GO** — Phase 3 identity-risk fields live on STS review GET payloads on srv-01.
