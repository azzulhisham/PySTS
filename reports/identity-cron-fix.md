# Fix: identity_risk_job lock-timeout retry + cron 09:30 UTC

**Date (UTC):** 2026-10-22  
**Commit:** `1c5340a` — *Retry identity_risk_job on lock timeout; document cron move to 09:30.*  
**Host:** srv-dp `10.10.20.200` (`/home/srvadmin/sts`) only.  
**Backup stamp:** `backup-20261022-091146`  
**No DDL. No ingest data-logic changes. No polygons.py / pyvam-\*. Job not run after deploy (next cron 09:30).**  
Prior deploy report saved as [`reports/risk-payload-phase1-deploy.md`](risk-payload-phase1-deploy.md).

---

## 1. Incident (2026-10-22)

| Time (UTC) | Event |
|------------|--------|
| 08:10:01 | `ofac_sdn_ingest` starts download of `sdn.xml` |
| 08:31:14 | Download saved (~29.2 MB) — **21m 13s** |
| 08:31:18 | Parse done; TRUNCATE+INSERT into OFAC SDN tables begins |
| 08:40:07 | `identity_risk_job` cron fails: `LockNotAvailable` on `FROM ofac_sdn_entry` (via `load_sanctions_indexes`) |
| 08:40:50 | SDN ingest finishes load into pnav |
| 08:55 | Manual identity_risk re-run **OK** — **2,619** ratings |

Cron overlap: SDN still holding locks past 08:40 while identity_risk started.

---

## 2. Code change

| Item | Detail |
|------|--------|
| File | `backend/identity_risk_job.py` |
| Behaviour | On `LockNotAvailable` / lock-timeout `OperationalError`, log, wait **120s**, retry whole `run_once` for up to **30 minutes**, then fail with clear `RuntimeError` + exit **1** |
| `lock_timeout` GUC | **unchanged** at **5000 ms** (not raised) |
| Tests | `backend/tests/test_identity_risk_job_retry.py` — succeed on later attempt; give up after limit; non-lock errors not retried |

Suite: **111 passed**.

---

## 3. Cron change (srv-dp)

| | Before | After |
|--|--------|-------|
| identity_risk | `40 8 * * *` … `identity_risk_job.py` | `30 9 * * *` … `identity_risk_job.py` |

Sanctions crons unchanged: SDN 08:10, CONS 08:15, UN 08:20, UK 08:30.

Crontab backup path documented in §6 after deploy.

---

## 4. Why was SDN download 21 minutes?

Source: `/var/log/ofac_sdn_ingest.log` on srv-dp (short history: 2026-10-21 … 2026-10-22 only).

| Run | Download start → Saved/Parse | Elapsed | Size |
|-----|------------------------------|--------:|-----:|
| 2026-10-21 12:53:29 → parse 13:32:33 | (no `Saved` line in that run) | **~39 min** | (same era SDN feed) |
| **2026-10-22 08:10:01 → 08:31:14** | Saved | **21m 13s** | **29,245,041** bytes |
| CONS 2026-10-22 08:15:01 → 08:15:06 | Saved | **~5 s** | 1,078,267 bytes |

**Verdict:** Not a one-off for “today only” in the available log — **both** recorded SDN pulls were very slow (21–39 min). CONS from the same host/treasury CDN path was normal (~5 s). That points to **treasury.gov slow/throttled transfer of the large SDN XML (~29 MB)** (or path congestion specific to that URL), not a local parse bug. Log history is too short to quantify “normally a few seconds,” but CONS and the user’s baseline imply SDN is usually fast; the last two pulls were outliers / a short bad streak. **No code change** made for this.

After today’s slow download, DB refresh alone ran ~08:31–08:40 (~9.5 min), which is what collided with the 08:40 identity cron.

---

## 5. Push / deploy / checks

| Step | Result |
|------|--------|
| `git push origin main` | **OK** — `c7a5bd5..1c5340a` |
| Pre-diff vs `972d1fa` | **MATCH** (safe to replace) |
| Backup | `/home/srvadmin/sts/backup-20261022-091146/` (`code/identity_risk_job.py`, `crontab-before.txt`, `crontab-after.txt`, `PRE_DEPLOY.txt`) |
| Copy | `/home/srvadmin/sts/identity_risk_job.py` (flat) |
| Import check | **IMPORT_OK** `v1.1-identity-risk-ll-mmsi` wait=120 max=1800; `lock_timeout=5000` still present |
| Crontab | see §3 before/after |
| Job run | **skipped** — next automatic run **09:30 UTC** |

### Crontab before / after

**Before** (`crontab-before.txt`):

```text
20 8 * * * … un_sanctions_ingest.py …
10 8 * * * … ofac_sdn_ingest.py …
15 8 * * * … ofac_cons_ingest.py …
30 8 * * * … uk_sanctions_ingest.py …
40 8 * * * … identity_risk_job.py …
```

**After** (`crontab-after.txt`):

```text
20 8 * * * … un_sanctions_ingest.py …
10 8 * * * … ofac_sdn_ingest.py …
15 8 * * * … ofac_cons_ingest.py …
30 8 * * * … uk_sanctions_ingest.py …
30 9 * * * … identity_risk_job.py …
```

---

## 6. Rollback

```bash
# SSH srvadmin@10.10.20.200
BK=/home/srvadmin/sts/backup-20261022-091146
cp -a $BK/code/identity_risk_job.py /home/srvadmin/sts/identity_risk_job.py
crontab $BK/crontab-before.txt         # restores 08:40
# do not touch pyvam-* / polygons.py
```

---

## 7. Decision

| Item | Status |
|------|--------|
| Push `1c5340a` | Done |
| Lock retry | Deployed + import OK |
| Cron 09:30 UTC | Deployed (backup has before/after) |
| SDN download analysis | Report only — both recent SDN pulls slow (21–39 min); CONS ~5 s |
| Job re-run after deploy | Not run |

**GO**
