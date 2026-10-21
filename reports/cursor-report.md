# Deploy report — STS berth / MMSI (round 2)

**Date:** 2026-10-21  
**Branch:** `main` @ **`7c26744`** (merged from `mantis-sts-berth-mmsi` @ `693c600` + eager-load follow-up)  
**Decision:** **GO**

## 1. Merge

| Step | Result |
|------|--------|
| Fast-forward `main` ← `mantis-sts-berth-mmsi` | `c7ccf03` → `693c600` |
| `.gitignore` | Added `restapi/data/` (`ffec55c`) |
| Follow-up | Eager-load port areas in `vesselstsreview` startup (`7c26744`) so `.200` logs `berths=1737` |

## 2. Backups

| Host | Backup directory | PRE_DEPLOY.txt |
|------|------------------|----------------|
| `.201` srv-01 | `/home/srvadmin/py/mantis/backup-20261021-041859` | `BACKUP_NAME=backup-20261021-041859`, PREV=`20a1cf1`, NEW initially `ffec55c` |
| `.200` srv-dp | `/home/srvadmin/sts/backup-20261021-041859` | same stamp; PREV=`20a1cf1` |

Each backup has `code` / `restapi` copies, `data/`, `code-tree.tgz`, prior `DEPLOYED_COMMIT.txt`.

## 3. Files deployed

### `.201` `/home/srvadmin/py/mantis/` (flat)

| Path | Notes |
|------|-------|
| `*.py` from `restapi/` | Incl. `port_areas.py` (STRtree), `sts_review.py`, `sts_detection.py`, … |
| `tests/` | Updated unit tests |
| `data/polygons/src/{ports,terminals,berths}.geojson` | Re-copied (checksum / missing `SHA256SUMS`) |
| `DEPLOYED_COMMIT.txt` | `7c2674451c209625fa92c1fe25852c4275b40422` |

### `.200` `/home/srvadmin/sts/`

| Path | Notes |
|------|-------|
| `vesselstsreview.py` | Harbour notify + eager port-area load |
| `restapi/*.py` | Shared modules (loitering uses these) |
| `data/polygons/src/*.geojson` | Re-copied |
| `DEPLOYED_COMMIT.txt` | `7c26744…` |

`vesselloiteringdetection.py` unchanged on disk (already called `get_port_area_index()`); picks up new `restapi/port_areas.py`.

## 4. Restarts

| Host | Service | Action |
|------|---------|--------|
| `.201` | `ais_mantis_api` | Restarted |
| `.200` | `ais_vesselstsreview` | Restarted (twice: code then eager-load) |
| `.200` | `ais_vesselloiterdetection` | Restarted |
| `.200` | `ais_vesselspoofingstore` | **Untouched** (still active) |
| `.201` | `pycpm-processor` | **Untouched** |

## 5. Startup log lines (required)

### `.201` ais_mantis_api — PASS (both workers)

```
2026-10-21 04:31:06,515 - INFO - polygons loaded ports=780 terminals=552 berths=1737; port areas built=923; ports skipped=90
2026-10-21 04:31:06,583 - INFO - polygons loaded ports=780 terminals=552 berths=1737; port areas built=923; ports skipped=90
```

Gunicorn started without worker timeouts.

### `.200` ais_vesselloiterdetection — PASS

```
2026-10-21 04:32:21,733 - INFO - polygons loaded ports=780 terminals=552 berths=1737; port areas built=923; ports skipped=90
```

### `.200` ais_vesselstsreview — PASS

```
2026-10-21 04:33:09,619 - INFO - polygons loaded ports=780 terminals=552 berths=1737; port areas built=923; ports skipped=90
2026-10-21 04:33:09,626 - INFO - STS review started (interval=900s, catchup=5s, batch=100)
2026-10-21 04:33:13,337 - INFO - STS review: refreshed 91 case(s)
```

No Tracebacks on the three services (probe window).

## 6. Endpoint checks (before → after)

Before snapshot taken against deployed `20a1cf1` immediately pre-deploy.

| Endpoint | Before | After | sec before → after |
|----------|--------|-------|--------------------|
| `/mantis/sts-review` page 1 | returned **100**, head `16,59,35,9,49` | returned **100**, same head; **+flags** (`inPortAreaCount=42`, `atBerthCount=28`, `invalidMmsiCount=1`) | 0.19 → **0.17** |
| `/mantis/sts-review/unreviewed` | returned **100**, same head | returned **100**, same head; flags `34/22/1` | 0.16 → **0.15** |
| `/mantis/sts-activities` | pairCount **11** | pairCount **11**, portExcl **2**, berthExcl **1**, warm | 0.63 → **0.47** |
| `/mantis/sts-activities?includeInPort=true` | — | pairCount **13** (harbour restored) | — → **0.44** |
| `/mantis/sts-review/notifications` | 200, count 0 | **200**, count 0 | 0.04 → 0.23 |
| `/mantis/illegal-anchoring` | 866 | 870 (+`invalidMmsiExcludedCount=6`) | 1.77 → 1.64 |
| `/mantis/loitering` | 2 | 2 | 0.27 → 0.23 |
| `/mantis/identity-conflict` | groups 659 | **652** (`invalidMmsiExcludedCount=37`) | 7.23 → 7.16 |
| `/mantis/darkvessels` | 218 | 218 | 1.27 → 1.31 |
| `/mantis/spoofing` | 16 / 47 | 16 / 47 | 0.14 → 0.67 |
| `/mantis/overview` | 200 | **200** (warm cache 0.001 s; first hit after restart slower) | 3.24 → warm OK |

### Row-for-row review pages

| Page | Exact id list | Longest common prefix | Notes |
|------|---------------|----------------------:|-------|
| sts-review | No (tail churn) | **26** / 100 | Head identical `16,59,35,9,49,42,62,14…`; processor refreshed **91** cases during deploy → `last_detected_at` reorders page tail |
| unreviewed | No (tail churn) | **27** / 100 | Same head; new case ids at end from live ingest |

**Flags on page:** case `35` has `invalidMmsi=true`; harbour rows present (`harbourOnPage` 45 / 36). Pagination size and SQL order unchanged.

## 7. Rollback command

Restore from **this** backup (`backup-20261021-041859`):

```bash
# .201 restapi
ssh srvadmin@10.10.20.201
APP=/home/srvadmin/py/mantis
BAK=$APP/backup-20261021-041859
sudo systemctl stop ais_mantis_api
cp -a $BAK/code/*.py $APP/
# optional: restore data from $BAK/data
sudo systemctl start ais_mantis_api

# .200 processors
ssh srvadmin@10.10.20.200
APP=/home/srvadmin/sts
BAK=$APP/backup-20261021-041859
echo '<sudo-pass>' | sudo -S systemctl stop ais_vesselstsreview ais_vesselloiterdetection
cp -a $BAK/code/vesselstsreview.py $APP/
cp -a $BAK/restapi/*.py $APP/restapi/
echo '<sudo-pass>' | sudo -S systemctl start ais_vesselstsreview ais_vesselloiterdetection
```

No DB undo (no DDL / no new columns).

## 8. Uncertain / notes

1. Review page **exact** id equality failed only at the tail because `ais_vesselstsreview` wrote during the deploy window; head order and `returnedCount=100` match. Not treated as a code FAIL.  
2. Overview first post-restart call was slow (~17 s cold parallel modules); subsequent call **0.001 s** cached.  
3. Polygons were re-copied because servers lacked `SHA256SUMS` markers; content matches prior deploy.  
4. Local `main` is ahead of `origin/main` (not pushed unless requested).
