# Deploy STOP: loitering motion metrics (`d24b12d`)

**Date (UTC):** 2026-10-23  
**Intended commit:** `d24b12d` (+ archive `ae58686` / `c4b784f`)  
**Decision:** **STOP** — no file copy, no service restart, no DDL.

Prior build report: [`reports/loiter-motion-build.md`](loiter-motion-build.md).

---

## 1. Push

| Item | Result |
|------|--------|
| `git push origin main` | **OK** — `e1c481c..c4b784f` (includes `ba1c13f`, `c1fb615`, `d24b12d`, report archive) |

---

## 2. Detector host discovery (read-only)

Per deploy rules: locate where `backend/vesselloiteringdetection.py` runs; if not srv-01 → **STOP before copying**.

### srv-01 `10.10.20.201` (read-only)

| Check | Result |
|-------|--------|
| `hostname` | `srv-01` |
| `ps` for `vesselloitering` / loiter | **none** |
| crontab (user/root) for loiter | **none** |
| `vesselloiteringdetection.py` under `/home/srvadmin/py/mantis` or `/home/srvadmin/sts` | **not present** |
| Related units | `ais_mantis_api` only (MANTIS API); no loiter unit |

### srv-dp `10.10.20.200` (read-only — discovery only; nothing written)

| Check | Result |
|-------|--------|
| `hostname` | `srv-dp` |
| File | `/home/srvadmin/sts/vesselloiteringdetection.py` (present; mtime Oct 21) |
| Process | `python3 /home/srvadmin/sts/vesselloiteringdetection.py` (PID **2941934**, since Oct 21) |
| systemd | **`ais_vesselloiterdetection.service`** — **loaded active running** |
| Unit path | `/etc/systemd/system/ais_vesselloiterdetection.service` (+ `env.conf` drop-in) |
| crontab loiter lines | none (runs as systemd service) |

**Conclusion:** the loitering detector runs on **srv-dp**, not srv-01.

---

## 3. STOP gate

| Rule | Outcome |
|------|---------|
| Detector must be on srv-01, else STOP before copying | **Triggered** — detector is on **srv-dp** |
| srv-dp must not be touched for this deploy | Honoured — discovery only |
| Copy / restart / DDL | **Not started** |

---

## 4. What was not done

| Step | Status |
|------|--------|
| SHA-256 compare / backup / copy on srv-01 | Skipped |
| Restart `ais_mantis_api` | Skipped |
| Restart loitering detector | Skipped (would be `ais_vesselloiterdetection` on srv-dp) |
| `sql/loiter_motion_metrics.sql` DDL | Skipped |
| Post-cycle metric verify | Skipped |

API-only files (`loitering.py`, `main.py`, `swagger.json`) were also **not** copied: without the detector update, motion columns would never be filled, and the approved plan required detector restart + cycle verify together with DDL.

---

## 5. Intended services (for a future approved deploy)

| Role | Host | Unit / path |
|------|------|-------------|
| MANTIS API | srv-01 | `ais_mantis_api` → `/home/srvadmin/py/mantis` |
| Loitering detector | **srv-dp** | `ais_vesselloiterdetection` → `/home/srvadmin/sts/vesselloiteringdetection.py` |

A complete rollout of `d24b12d` needs an **explicit approval to deploy the detector on srv-dp** (and then DDL + API on srv-01), or a decision to move the loiter service to srv-01 first.

---

## 6. Rollback

N/A — no remote code or schema changes were applied in this attempt.

---

## 7. Decision

| Item | Status |
|------|--------|
| Push | Done |
| Detector location | **srv-dp** (`ais_vesselloiterdetection`) |
| Copy / restart / DDL | **STOPPED** |
| GO / NO-GO | **NO-GO** until srv-dp detector deploy is approved (or host policy changes) |

**NO-GO** — loitering motion metrics are on `main` locally/remotely in git, but **not live**. Waiting for next instruction on srv-dp.
