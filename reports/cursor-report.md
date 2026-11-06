# Deploy: suspected-anchoring harbour context + confirmed-stop elapsed

**Date (UTC):** 2026-11-06  
**Approver:** Zulhisham  
**Branch:** `main`  
**Feature commit:** `32ba658492764f85b748a3c7f8c4a63d639d623f`  
**Push:** `4e0f6df..32ba658` → `origin/main` (no GitHub Actions / no auto-deploy)  
**Prior prepare report archived as:** [`suspected-anchoring-enrichment-prepare-2026-11-06.md`](suspected-anchoring-enrichment-prepare-2026-11-06.md)

---

## 1. Verdict

| Item | Result |
|------|--------|
| Scope | Additive harbour labels + elapsed **since stop confirmation** only |
| Host | **srv-01** `10.10.20.201` only |
| Service restarted | **only** `ais_mantis_api` |
| srv-dp / detectors | **Not touched** |
| Rollback | **Not required** |
| Frontend / legal boundary validation | **Still pending** |

Duration wording: fields are **elapsed since stop confirmation** (`tsstop`→`tscurrent`), capped at last AIS observation — **not** exact total time stationary.

---

## 2. Pre-deploy

| Check | Result |
|-------|--------|
| `git fetch` | `origin/main` unchanged relative to local tip before push (local was ahead by STS commits + this feature) |
| Conflicts / unexpected scope | None — enrichment diffs limited to API enrichment files |
| PySTS polygons | `data/polygons/src/{ports,terminals,berths}.geojson` present; resolve via `_default_poly_dir()` under PySTS — **no PyVAM** |
| Server polygons | Already at `/home/srvadmin/py/mantis/data/polygons/src/` — SHA-256 **match** local; not recopied |
| Auto-deploy on push | **None** (no `.github/workflows`) |

### Tests (actual)

```text
pytest restapi/tests/test_illegal_anchoring_enrichment.py \
       restapi/tests/test_port_areas.py \
       restapi/tests/test_ship_risk_phase{2,3,4,5}.py
→ 54 passed
```

Includes new regressions:

| Case | Result |
|------|--------|
| Reopened stop does not inherit prior elapsed | pass |
| Payload rows isolate short vs long stop | pass |
| After resumption, null `tsstop` → unknown (not prior hours) | pass |
| Prior harbour / selection / provenance cases | pass |

---

## 3. Commit / push evidence

| Step | Evidence |
|------|----------|
| Commit | `32ba658` — *Add suspected-anchoring harbour context and confirmed-stop elapsed.* |
| Files in commit | `illegal_anchoring.py`, `port_areas.py`, `main.py`, `mantis_overview.py`, `swagger.json`, `test_illegal_anchoring_enrichment.py`, `mantis-detection.md`, prepare report + prior STS deploy archive |
| Push | `git push origin main` → `4e0f6df..32ba658` |
| Also pushed (already deployed earlier) | STS 100 m commits `3cf5533`, `ca6c8df` (remote sync only) |

---

## 4. Deployed manifest (srv-01)

| Artifact | Live path | Action |
|----------|-----------|--------|
| `illegal_anchoring.py` | `/home/srvadmin/py/mantis/illegal_anchoring.py` | replaced |
| `port_areas.py` | `/home/srvadmin/py/mantis/port_areas.py` | replaced |
| `main.py` | `/home/srvadmin/py/mantis/main.py` | replaced |
| `mantis_overview.py` | `/home/srvadmin/py/mantis/mantis_overview.py` | replaced |
| `static/swagger.json` | `/home/srvadmin/py/mantis/static/swagger.json` | replaced |
| `ports.geojson` | `…/mantis/data/polygons/src/ports.geojson` | verified match — not recopied |
| `terminals.geojson` | `…/mantis/data/polygons/src/terminals.geojson` | verified match — not recopied |
| `berths.geojson` | `…/mantis/data/polygons/src/berths.geojson` | verified match — not recopied |
| `DEPLOYED_COMMIT.txt` | `/home/srvadmin/py/mantis/DEPLOYED_COMMIT.txt` | `32ba658…` |

WorkingDirectory: `/home/srvadmin/py/mantis` → polygon loader finds `data/polygons/src` beside the API tree.

### Checksums (after)

| File | SHA-256 |
|------|---------|
| illegal_anchoring.py | `1991a1857c0118949aa39ec780d08088de6efb61d81b34cfef7e5a3129ff8c9f` |
| port_areas.py | `263e61e80c28ffea663c81bd9324666a0359f252b37159d948b6e0a1fa7a64a5` |
| main.py | `c184f77f9d41944214da200481f1ed15008a1895c0e296f22a0201d4d27b2169` |
| mantis_overview.py | `4408b367173d360a6b25b7d2e6eccb09f5c21306f4fd3ff52d69098d7921366c` |
| swagger.json | `0fead71636fa02be045a2e61c4e5b6286a4318fcce7872e4e48ffedd0347f501` |
| berths.geojson | `c3c70780d2c70ef9e94ae413bd1aa1121f575cc80789895707e4c6ff2206bf6d` |
| ports.geojson | `c87490210b472b6b64ecad2ea18c94d4ad290bba6aab30cc37d777ede48ffd48` |
| terminals.geojson | `ddbbfda0a23b23a4e320c018c28f94e00616208dcdf8f1a623c510b31527e3ed` |

### Backup

| Item | Path |
|------|------|
| Backup root | `/home/srvadmin/py/backup-20261106-123811/` |
| Code | `…/code/{illegal_anchoring,port_areas,main,mantis_overview}.py` |
| Swagger | `…/static/swagger.json` |
| Polygon SHA record | `…/polygons/SHA256_LIVE.txt` |
| Pre-deploy stamp | `PRE_DEPLOY_UTC=2026-11-06T12:38:13Z` |

Syntax: `py_compile` + swagger JSON parse + import check before activation — **OK**.

---

## 5. Restart

| Unit | Before PID | After PID |
|------|------------|-----------|
| **ais_mantis_api** | 3009449 | **3045481** |
| ais_sts_shadow | 1824023 | 1824023 (unchanged) |
| ais_tss_reporting | 2809648 | 2809648 (unchanged) |
| pyvam-api | 2177790 | 2177790 (unchanged) |
| ais_tss_analyzer | 840639 | 840639 (unchanged) |

srv-dp: not contacted for this change.

---

## 6. Verification

| Check | Result |
|-------|--------|
| Unit active | **active** |
| Startup | gunicorn listen `:8085`; workers booted |
| Polygon load | `polygons loaded ports=780 terminals=552 berths=1737; port areas built=923; ports skipped=90` (both workers) |
| Traceback / CRITICAL in restart window | **none** observed |
| `GET /mantis/illegal-anchoring` no token | **401** `{"message":"Unauthorized"}` |
| `GET /mantis/sts-activities` no token | **401** |
| Swagger additive fields | `stopStartedAt`, `lastObservedAt`, `stoppedElapsedHours`, `stopElapsedStatus`, `inPortArea`, `berthName`, `polygonProvenance`, `berthCount` present |
| Swagger duration wording | documents **not** exact total time stationary |
| STS 100 m description still present | **yes** |
| Authenticated payload field values | **Unverified** (no JWT minted per safety rules) |
| End-to-end map/UI display | **Unverified** — frontend pending |

---

## 7. Rollback (available; not executed)

```bash
BK=/home/srvadmin/py/backup-20261106-123811
cp -a $BK/code/illegal_anchoring.py /home/srvadmin/py/mantis/illegal_anchoring.py
cp -a $BK/code/port_areas.py /home/srvadmin/py/mantis/port_areas.py
cp -a $BK/code/main.py /home/srvadmin/py/mantis/main.py
cp -a $BK/code/mantis_overview.py /home/srvadmin/py/mantis/mantis_overview.py
cp -a $BK/static/swagger.json /home/srvadmin/py/mantis/static/swagger.json
sudo systemctl restart ais_mantis_api
```

---

## 8. Remaining limitations (not completed)

| Item | Status |
|------|--------|
| Frontend display of new fields | **Pending** |
| Official anchorage-boundary validation | **Pending** |
| Exclusion-policy product decisions | **Pending** |
| Exact total time stationary | **Not available** without new persistence |
| Authenticated live payload inspection | **Not done** this session |

---

## 9. Decision

| Item | Status |
|------|--------|
| Deploy | **Complete** on srv-01 `ais_mantis_api` |
| Push | **Complete** to `origin/main` |
| Rollback | Not needed |
| GO for continued API use | **GO** — treat duration as elapsed-since-confirmation; UI work separate |
