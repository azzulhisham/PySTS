# Port-area exclusions — deploy report

**Date:** 2026-10-21  
**Branch:** `main` (merged from `mantis-port-exclusions`)  
**Deployed commit:** `20a1cf10e1520ff0854a05177050e4a19997f3db`  
**Decision:** **GO**

## 1. Repo tidy-up

| Change | Detail |
|--------|--------|
| `.gitignore` | Ignore `data/*` but track `data/polygons/src/*.geojson` |
| GeoJSON committed | `ports.geojson`, `terminals.geojson`, `berths.geojson` |
| `restapi/requirements.txt` | `shapely==2.2.0` (local venv pin) |
| `backend/requirements.txt` | **Created** with `shapely==2.2.0` |
| Merge | Fast-forward `main` ← `mantis-port-exclusions` |

**Follow-up commits on `main` (also on feature branch):**

| Commit | Why |
|--------|-----|
| `04d1084` | Track geojson + pin shapely |
| `12b0b73` | Resolve poly dir for flat `.201` layout |
| `d4e8d10` | Eager-load port areas at API/processor start |
| `20a1cf1` | Find `restapi/` beside loitering script on `.200` |

## 2. Backups (pre-deploy)

| Host | Backup directory | PRE_DEPLOY.txt |
|------|------------------|----------------|
| `.200` srv-dp | `/home/srvadmin/sts/backup-20261021-032132` | `BACKUP_NAME=backup-20261021-032132` (names **this** backup; not 111611) |
| `.201` srv-01 | `/home/srvadmin/py/mantis/backup-20261021-032132` | same |

Each backup includes copied `*.py` / `restapi` / optional prior `data`, plus `code-tree.tgz`. Prior backups (`backup-20261020-*`) left in place.

### Shapely check (no upgrades)

| Host | Interpreter | Result |
|------|-------------|--------|
| `.200` | `/usr/bin/python3` (loitering ExecStart) | Already present **2.0.4** — left unchanged |
| `.201` | `/usr/bin/python3` (gunicorn shebang) | Already present **2.1.1** — left unchanged |

Pinned `2.2.0` is for requirements/docs; servers already imported shapely so nothing was installed/upgraded.

## 3. Files deployed

### `.200` `/home/srvadmin/sts/`

| Path | Notes |
|------|-------|
| `vesselloiteringdetection.py` | Final `20a1cf1` path fix |
| `restapi/*.py` | Includes `port_areas.py`, loitering helpers, etc. |
| `data/polygons/src/{ports,terminals,berths}.geojson` | Relative path used by loader |
| `DEPLOYED_COMMIT.txt` | `20a1cf10e1520ff0854a05177050e4a19997f3db` |

### `.201` `/home/srvadmin/py/mantis/`

| Path | Notes |
|------|-------|
| `*.py` (flat API layout) | Includes `main.py`, `port_areas.py`, `illegal_anchoring.py`, `loitering.py`, … |
| `static/`, `tests/` | From package |
| `data/polygons/src/{ports,terminals,berths}.geojson` | Beside modules (`_default_poly_dir`) |
| `DEPLOYED_COMMIT.txt` | `20a1cf10e1520ff0854a05177050e4a19997f3db` |
| `mantis.env` | Untouched |

## 4. Service restarts

| Host | Service | Action |
|------|---------|--------|
| `.200` | `ais_vesselloiterdetection` | Restarted (twice: first import fail → path fix → OK) |
| `.200` | `ais_vesselstsreview`, `ais_vesselspoofingstore` | **Not restarted** (remained active) |
| `.201` | `ais_mantis_api` | Restarted |
| `.201` | `pycpm-processor` | **Untouched** (still failed) |

## 5. Startup log lines (required)

### `.200` loitering — PASS

```
2026-10-21 03:25:02,548 - INFO - polygons loaded ports=780 terminals=552; port areas built=923; ports skipped=90
```

(First restart at 03:23 logged `port_areas import failed` — treated as FAIL; fixed with `20a1cf1` and re-restarted.)

### `.201` ais_mantis_api — PASS (both workers)

```
2026-10-21 03:27:13,923 - INFO - polygons loaded ports=780 terminals=552; port areas built=923; ports skipped=90
2026-10-21 03:27:14,043 - INFO - polygons loaded ports=780 terminals=552; port areas built=923; ports skipped=90
```

## 6. Endpoint checks (before → after)

Window for spoofing: `2026-10-18T03:21:35Z` → `2026-10-21T03:21:35Z` (from before snapshot).

| Endpoint | Before | After | sec before → after |
|----------|--------|-------|--------------------|
| `/mantis/illegal-anchoring` | 200, illegalCount=**865**, rule v3.1, **no** portArea* | 200, illegalCount=**864**, portAreaExcludedCount=**0**, portAreaCount=**923**, rule v3.2 | 1.98 → 2.83 |
| `/mantis/loitering` | 200, eventCount=**5**, rule v1.1 | 200, eventCount=**0**, portAreaCount=**923**, rule v1.2; MMSIs **565173000** / **563196200** absent | 0.14 → 0.07 |
| `/mantis/spoofing` | anomalyCount=18, dataQualityCount=51 | same 18 / 51 | 0.28 → 0.77 |
| `/mantis/darkvessels` | candidateCount=213 | 212 (live churn) | 1.31 → 1.35 |
| `/mantis/sts-review` | pageSize=100, total≈17119 | same | 0.24 → 0.21 |
| `/mantis/sts-review/unreviewed` | pageSize=100, total≈324373 | same, **0.22 s** | 0.18 → 0.22 |
| `/mantis/sts-review/notifications` | 200, 130 | 200, 130 | 0.33 → 13.7* |
| `/mantis/overview` | 200, partial=false | 200, partial=false | 2.99 → 3.20 |

\*Notifications slower on one call (cold worker / DB); still 200 with same counts.

### Loitering Singapore MMSIs

| MMSI | Status after deploy |
|-----:|---------------------|
| 565173000 | Still **open**, `outside_anchorage=false` (port clear) → not in default list |
| 563196200 | **Closed** (`tsout` set) → not in default list |

### DB / DDL

- No new columns, no DDL, no full-table scans added.
- Spatial checks remain in-memory / DuckDB.

## 7. Rollback command

If needed, restore from **this** backup (not 111611):

```bash
# .201 restapi first
ssh srvadmin@10.10.20.201
APP=/home/srvadmin/py/mantis
BAK=$APP/backup-20261021-032132
sudo systemctl stop ais_mantis_api
cp -a $BAK/code/*.py $APP/
# optional: tar -C $APP -xzf $BAK/code-tree.tgz
sudo systemctl start ais_mantis_api

# .200 loitering
ssh srvadmin@10.10.20.200
APP=/home/srvadmin/sts
BAK=$APP/backup-20261021-032132
sudo systemctl stop ais_vesselloiterdetection
cp -a $BAK/code/vesselloiteringdetection.py $APP/
cp -a $BAK/restapi/*.py $APP/restapi/
sudo systemctl start ais_vesselloiterdetection
```

No DB undo.

## 8. Uncertain / notes

1. Illegal `portAreaExcludedCount` remains **0** on live data (candidates still outside terminal footprints); filter active and logged.
2. Default loitering list emptied after processor cycle cleared port centres’ `outside_anchorage` and other open rows churned; Singapore targets verified absent as required.
3. Server shapely left at 2.0.4 / 2.1.1 (already importable); requirements pin 2.2.0 for new installs only.
