# Deploy: live STS pair distance 30 m → 100 m

**Date (UTC):** 2026-11-06  
**Approver:** Zulhisham  
**Feature commit:** `3cf553397dec4a41df302a1bb2fc62b4363a196c`  
**Push:** not performed  
**Rollback:** not required  
**Prior prepare report archived as:** [`sts-pair-distance-100m-prepare-2026-11-06.md`](sts-pair-distance-100m-prepare-2026-11-06.md)

---

## 1. Verdict

| Item | Result |
|------|--------|
| Commit | `3cf5533` — feature + tests + docs + prepare report |
| srv-dp detector | **Deployed & restarted** — `MAX_DISTANCE_M=100.0`, `DETECTION_VERSION=2.4-pair-100m` |
| srv-01 API | **Deployed & restarted** — presentation `MAX_DISTANCE_M=100.0` + swagger |
| Strict `< 100` | Preserved in both layers |
| Shadow / sanctions / other services | Untouched |
| Observation | ≥4 completed detector cycles after restart; no Traceback/timeout/lock errors |
| Rollback | Not executed |

---

## 2. Pre-deploy checks

| Check | Result |
|-------|--------|
| `git fetch` / `main` vs `origin/main` | In sync before feature commit; no remote scope change |
| Local tests re-run | **169 passed** (`test_sts_pair_distance_100m` + shadow Rule C + `restapi/tests/`) |
| Live presentation consumers | Only `/home/srvadmin/py/mantis` (`ais_mantis_api`). Other `sts_detection.py` copies are **backup trees only**. No second live API host for this path. |
| Shadow Rule C | Separate unit `ais_sts_shadow` — left alone |

### Baseline (before restart)

| Host / signal | Value |
|---------------|-------|
| srv-dp unit | `ais_vesselproximitydetection` active, PID **1944630** |
| Live detector file | `/home/srvadmin/sts/vesselproximitydetection.py` — `MAX_DISTANCE_M=30.0`, `DETECTION_VERSION=2.3-tanker-pair-score` |
| Detector SHA (before) | `3b7a0f450fe04597f07308a3cb60dbd4c6c87051c5377b10937e0fa2c3f0a8b4` |
| Cycle cadence | ~33–34 s between “System sleep” lines |
| Last pre-change summary | `Clusters: 73 open/updated, 1 new, 0 closed (87 pair edges, 1799 vessels checked)` |
| srv-01 unit | `ais_mantis_api` active, PID **1879238** |
| Live API files | `/home/srvadmin/py/mantis/sts_detection.py` (`MAX_DISTANCE_M=35.0`), `static/swagger.json` (≤35 m text) |
| API SHA (before) | sts `651ca087…bcb4a`; swagger `1e923899…8348c` |
| Unauth smoke | `GET /mantis/sts-activities` → **401** |

---

## 3. Feature commit contents

| Path | Role |
|------|------|
| `backend/vesselproximitydetection.py` | Gate 100 m + scaled bbox + version `2.4-pair-100m` |
| `restapi/sts_detection.py` | Presentation recompute 100 m |
| `restapi/static/swagger.json` | Docs / `maxDistanceM` example 100 |
| `backend/tests/test_sts_pair_distance_100m.py` | Boundary + non-distance regressions |
| `backend/mantis-detection.md`, `backend/todo.md` | Docs |
| `reports/cursor-report.md` (then prepare archive) | Prepare report |
| `reports/mantis-147-suspected-bunkering-deploy-2026-10-27.md` | Prior report archive |

Message: *Widen live STS pair distance gate from 30 m to 100 m.*

---

## 4. Deployed files and backups

### srv-dp `10.10.20.200`

| Item | Value |
|------|-------|
| Unit | `ais_vesselproximitydetection.service` |
| Exec path | `/usr/bin/python3 /home/srvadmin/sts/vesselproximitydetection.py` |
| Deployed file | `/home/srvadmin/sts/vesselproximitydetection.py` |
| Backup | `/home/srvadmin/sts/backup-20261106-040346/` |
| Backup SHA (before) | `3b7a0f450fe04597f07308a3cb60dbd4c6c87051c5377b10937e0fa2c3f0a8b4` |
| Live SHA (after) | `51c9924216b00a1faaadf1e1362bf3a7ad517bd3b0f1fc8d4906899f5965c8e7` (= local commit) |
| Syntax check | `python3 -m py_compile` on staged file — OK before activation |
| Restart | **only** `ais_vesselproximitydetection` |
| PID before → after | **1944630 → 4187395** |
| Startup log | `Proximity detection started (interval=30s, grace=60s, threshold=100.0m).` |

### srv-01 `10.10.20.201`

| Item | Value |
|------|-------|
| Unit | `ais_mantis_api.service` |
| WorkingDirectory | `/home/srvadmin/py/mantis` |
| Deployed files | `sts_detection.py`, `static/swagger.json` |
| Backup | `/home/srvadmin/py/backup-20261106-040346/` |
| Backup SHA (before) | sts `651ca087…`; swagger `1e923899…` |
| Live SHA (after) | sts `05e8aeb62f203b00120b0035e6ed7ab6d422f5aef38be825e83791137977cb99`; swagger `b837e9ba23fc47d220715dd0baf96cf8ac7a41d79c3f3c927889eb36e1d269e3` |
| `DEPLOYED_COMMIT.txt` | `3cf553397dec4a41df302a1bb2fc62b4363a196c` |
| Syntax check | `py_compile` + `json.load(swagger)` — OK before activation |
| Restart | **only** `ais_mantis_api` |
| PID before → after | **1879238 → 3009449** |

No whole-repo copy onto live folders.

---

## 5. Unrelated services (must stay up / untouched)

| Unit | Host | Status after deploy |
|------|------|---------------------|
| `ais_sts_shadow` | srv-01 | active, PID **1824023** (unchanged) |
| `ais_sts_vesselzone` | srv-01 | active, PID **1829224** (unchanged) |
| `ais_tss_reporting` | srv-01 | active |
| `pyvam-api` | srv-01 | active |
| `ais_aton_datapreproc` | srv-01 | active, PID **772689** |
| `ais_vesselstsreview` | srv-dp | active (not restarted for this change) |

---

## 6. Post-deploy observation

### Detector cycles (after 04:04:57 UTC restart)

| Time (UTC) | Log summary |
|------------|-------------|
| 04:05:08 | `52 open/updated, 94 new, 0 closed (256 pair edges, 1803 vessels checked)` |
| 04:05:45 | `146 open/updated, 0 new, 21 closed (256 pair edges, 1802 vessels)` |
| 04:06:21 | `146 open/updated, 0 new, 0 closed (256 pair edges, 1803 vessels)` |
| 04:06:58 | `145 open/updated, 0 new, 0 closed (255 pair edges, 1803 vessels)` |

| Metric | Baseline (30 m) | After (100 m) |
|--------|-----------------|---------------|
| Pair edges / cycle | **87** | **~255–256** |
| Vessels checked | ~1799 | ~1802–1803 |
| Open clusters (steady) | ~74 | ~145–146 |
| Cycle spacing | ~33–34 s | ~36–37 s |
| Traceback / timeout / lock | none observed in window | **none** |

First cycle after restart opened many new clusters (expected when the gate widens); subsequent cycles stabilised with similar edge counts and no error spam.

### API smoke

| Check | Result |
|-------|--------|
| `GET /mantis/sts-activities` no token | **401** `{"message":"Unauthorized"}` (unchanged) |
| Swagger STS description | contains **100 m**; old **35 m** text gone |
| `maxDistanceM` example | **100.0** |
| API journal Traceback (excl. unauth noise) | none in post-restart window |

---

## 7. Proven vs unverified

| Check | Status |
|-------|--------|
| Deployed files match commit checksums | **Proven** |
| Services active; startup `threshold=100.0m` | **Proven** |
| ≥2 (actually 4) completed detector cycles | **Proven** |
| Pair-edge volume rise vs baseline | **Proven** (87 → ~256) |
| No new Traceback/timeout in observed window | **Proven** |
| Unauth API contract (401) | **Proven** |
| Swagger distance text | **Proven** |
| Authenticated STS payload / `maxDistanceM` field live | **Unverified** (no JWT minted per safety rules) |
| Specific 30–100 m pair end-to-end in UI/API | **Unverified** (would need auth + known pair; startup alone is not proof) |
| Sustained review false-positive rate / pnav load over hours | **Unverified** — monitor separately |

---

## 8. Rollback (available; not executed)

```bash
# srv-dp
BK=/home/srvadmin/sts/backup-20261106-040346
cp -a $BK/code/vesselproximitydetection.py /home/srvadmin/sts/vesselproximitydetection.py
sudo systemctl restart ais_vesselproximitydetection

# srv-01
BK=/home/srvadmin/py/backup-20261106-040346
cp -a $BK/code/sts_detection.py /home/srvadmin/py/mantis/sts_detection.py
cp -a $BK/static/swagger.json /home/srvadmin/py/mantis/static/swagger.json
sudo systemctl restart ais_mantis_api
```

Do **not** delete observations created under `2.4-pair-100m`.

---

## 9. Remaining risks

| Risk | Note |
|------|------|
| Higher pair / open-cluster counts | Observed ~3× pair edges; review queue may grow |
| Slightly longer cycles | ~36–37 s vs ~33–34 s baseline; still near 30 s loop |
| Wider false positives | Possible; assess via review reject rate over coming shifts |
| Authenticated API field check | Still outstanding without token |

---

## 10. Decision

| Item | Status |
|------|--------|
| Deploy | **Complete** on srv-dp + srv-01 |
| Rollback | Not needed |
| Push | Not done (await separate request) |
| GO for continued observation | **GO** — monitor pair edges, cycle time, review volume |
