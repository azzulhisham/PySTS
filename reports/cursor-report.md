# PLAN (awaiting GO): STS shadow detector — rule C

**Date:** 2026-10-27  
**Status:** **PLAN ONLY — no code, DDL, or deploy until you approve.**  
**Hosts (when approved):** srv-01 only for the shadow unit; **srv-dp untouched**; live STS / API untouched.  
**Prior report archived as:** [`vesselzone-b-hold.md`](vesselzone-b-hold.md)

---

## 1. Goal

Run a **shadow-only** STS detector with **rule C** beside live MANTIS for ~1 week:

| Live MANTIS STS today | Shadow rule C |
|----------------------|---------------|
| Pairs &lt; **30 m** | Pairs ≤ **100 m**, or ≤ **200 m** if **both** tankers (80–89) **and** at least one length &gt; 150 m |
| Only ships already **stopped** in `ais_vesselmovementactivities` | No stop pre-filter; both **SOG &lt; 5 kn** |
| Clusters / suspicion score / review cases | Pair **episodes** ≥ **2 h** (allow **1** missed 5‑min bucket) |
| Writes proximity observation tables | Writes **only** `public.ais_sts_shadow_episode` |
| Exposed in API | **No** API / payload / MANTIS UI |

Same-hull suppression: **reuse** `is_same_vessel` logic (copy/import from proximity module without changing that file’s behaviour).  
Tags: `bunker_hub` if a ship has ≥3 partners in the window (tag only); `possible_bunker` when one tanker is &lt; 100 m.

Test background (read-only, `~/Downloads/ststest`): ~**22 extra pairs/day**, est. **80–85%** real.

---

## 2. What will / will not change

| Do | Do not |
|----|--------|
| New script `backend/sts_shadow_detection.py` | Edit `vesselproximitydetection.py` |
| New SQL create + undo under `sql/` | Edit `sts_detection.py` / any live detector or API |
| New unit `ais_sts_shadow.service` on **srv-01** | Touch **srv-dp** |
| Own table upserts + small cached `ais_static` read | Write to proximity / movement / review tables |
| Unit tests for rule C | Show anything in MANTIS |
| Doc section + srvhealth ignore/discovery as needed | Restart any unit except shadow + `srvhealth-reporter` |

---

## 3. Proposed DDL (needs your approval before run)

### Create — `sql/sts_shadow_episode.sql`

```sql
-- STS shadow episodes (rule C). APPROVAL REQUIRED before apply.
-- Shadow only: not used by MANTIS API.

CREATE TABLE IF NOT EXISTS public.ais_sts_shadow_episode (
    id                  BIGSERIAL PRIMARY KEY,
    mmsi_a              BIGINT NOT NULL,
    mmsi_b              BIGINT NOT NULL,
    ship_name_a         TEXT,
    ship_name_b         TEXT,
    ship_type_a         INTEGER,
    ship_type_b         INTEGER,
    length_m_a          DOUBLE PRECISION,
    length_m_b          DOUBLE PRECISION,
    ts_start            TIMESTAMPTZ NOT NULL,
    ts_last             TIMESTAMPTZ NOT NULL,
    duration_h          DOUBLE PRECISION NOT NULL,
    min_distance_m      DOUBLE PRECISION,
    median_distance_m   DOUBLE PRECISION,
    max_distance_m      DOUBLE PRECISION,
    median_sog_kn       DOUBLE PRECISION,
    rule_band           TEXT NOT NULL,          -- '100m' | 'tanker200m'
    possible_bunker     BOOLEAN NOT NULL DEFAULT FALSE,
    bunker_hub_a        BOOLEAN NOT NULL DEFAULT FALSE,
    bunker_hub_b        BOOLEAN NOT NULL DEFAULT FALSE,
    centroid_latitude   DOUBLE PRECISION,
    centroid_longitude  DOUBLE PRECISION,
    zone_name           TEXT,                   -- parent anchorage if any
    in_excl             BOOLEAN NOT NULL DEFAULT FALSE,
    harbour_excl        BOOLEAN NOT NULL DEFAULT FALSE,
    in_mantis           BOOLEAN NOT NULL DEFAULT FALSE,
    is_open             BOOLEAN NOT NULL DEFAULT TRUE,
    detection_version   TEXT NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_sts_shadow_episode UNIQUE (mmsi_a, mmsi_b, ts_start),
    CONSTRAINT chk_sts_shadow_mmsi_order CHECK (mmsi_a < mmsi_b),
    CONSTRAINT chk_sts_shadow_rule_band CHECK (rule_band IN ('100m', 'tanker200m'))
);

CREATE INDEX IF NOT EXISTS idx_sts_shadow_open
    ON public.ais_sts_shadow_episode (ts_last DESC)
    WHERE is_open;

CREATE INDEX IF NOT EXISTS idx_sts_shadow_mmsi_a_ts
    ON public.ais_sts_shadow_episode (mmsi_a, ts_start DESC);

CREATE INDEX IF NOT EXISTS idx_sts_shadow_mmsi_b_ts
    ON public.ais_sts_shadow_episode (mmsi_b, ts_start DESC);

COMMENT ON TABLE public.ais_sts_shadow_episode IS
    'Shadow STS pair episodes (rule C). Not consumed by MANTIS API.';
```

**Canonical pair key:** always store `mmsi_a < mmsi_b`.

### Undo — `sql/sts_shadow_episode_rollback.sql`

```sql
-- Undo STS shadow table. Only after unit stopped/disabled.
DROP TABLE IF EXISTS public.ais_sts_shadow_episode;
```

---

## 4. Detection design (rule C)

### 4.1 ClickHouse fetch (bounded)

Same pattern as loitering (`vesselloiteringdetection.py`):

- Source: `pnav.ais_position`
- Bucket: `toStartOfFiveMinutes(ts)`; `argMax` lat/lon/sog (and dims not in CH — from PG static)
- Window: rolling **last N hours** (propose **N = 6** so a 2 h episode can open/extend; configurable env `sts_shadow_lookback_hours`)
- Ship shortlist: MMSIs from cached latest `ais_static` with `shipType` **70–89** (valid ship MMSI filter reused)
- Chunk MMSIs (e.g. 1500) — **no full CH scan**
- Loop: **15 min** (`sts_shadow_loop_seconds`, default 900)

### 4.2 Pair rule (per coinciding 5‑min bucket)

For each bucket, among ships with usable SOG &lt; 5 kn:

1. Distance ≤ **100 m** → candidate, `rule_band = '100m'`
2. Else distance ≤ **200 m** **only if** both types 80–89 **and** `max(length_a, length_b) > 150` where `length = to_bow + to_stern`; **unknown length ⇒ not longer** → `rule_band = 'tanker200m'`
3. Drop same-hull pairs via `is_same_vessel` (IMO+corroboration / name+callsign|dims) — logic copied or imported carefully so live proximity file is **not** edited
4. Tag `possible_bunker` if the episode ever has a pair distance &lt; 100 m with at least one tanker
5. After pairing in the window: any MMSI with ≥ **3** distinct partners → `bunker_hub_*` true on episodes involving that MMSI (never drop)

### 4.3 Episode grouping

- Consecutive buckets where the same ordered pair qualifies, allowing **one** missing 5‑min gap
- Keep episode if span `ts_last - ts_start` ≥ **2 h**
- Aggregate min / median / max distance; median SOG; centroid of last (or mean) positions
- Upsert on `(mmsi_a, mmsi_b, ts_start)`; refresh `ts_last`, distances, flags, `updated_at`
- Close (`is_open = false`) when pair absent beyond allowed gap for a cycle

### 4.4 Geography (read-only)

Reuse API helpers without writing API code:

- Parent anchorage / Excl from `polygons` (`anchorage_areas`, `is_excl_name`) → `zone_name`, `in_excl`
- Harbour / port-area exclude from `port_areas` → `harbour_excl`
- Labels only; do not filter episodes out of the table (shadow wants full set for comparison)

### 4.5 `in_mantis`

Read-only check: overlapping open/closed row in `ais_vesselproximityobservation` (+ members) for the same two MMSIs with overlapping time windows (bounded by `ts_start`/`ts_last` ± small grace). Indexed lookups only; no updates to proximity tables.

### 4.6 Detection version

Propose: `detection_version = 'shadow-C-1.0'`

---

## 5. Files to add (after GO)

| Path | Role |
|------|------|
| `backend/sts_shadow_detection.py` | Shadow loop: CH fetch, rule C, upsert, summary log |
| `backend/tests/test_sts_shadow_rule_c.py` | Unit tests (see §8) |
| `sql/sts_shadow_episode.sql` | Create table (approved DDL only) |
| `sql/sts_shadow_episode_rollback.sql` | Drop table |
| `deploy/ais_sts_shadow.service` (or `systemd/…`) | Unit file checked into repo for copy to `/etc/systemd/system/` |
| `backend/mantis-detection.md` | Short “STS shadow test (rule C)” section |
| `reports/cursor-report.md` | This plan → later deploy results |

**Not created/edited:** `vesselproximitydetection.py`, `sts_detection.py`, `main.py`, swagger, other detectors.

---

## 6. Proposed systemd unit (`ais_sts_shadow.service`)

Run on **srv-01**, same layout pattern as other STS/MANTIS jobs (WorkingDirectory under `/home/srvadmin/…`, Python from the **existing STS/MANTIS venv** used by related units — confirm path at deploy: e.g. `/home/srvadmin/py/mantis/venv` or system `/usr/bin/python3` + deps already present for loitering-style jobs).

```ini
[Unit]
Description=MANTIS STS shadow detector (rule C) — test only
After=network-online.target

[Service]
Type=simple
User=srvadmin
WorkingDirectory=/home/srvadmin/py/mantis
# Exact ExecStart/venv path confirmed at deploy against live STS units
ExecStart=/usr/bin/python3 /home/srvadmin/py/mantis/sts_shadow_detection.py
EnvironmentFile=/home/srvadmin/py/mantis/mantis.env
SyslogIdentifier=ais_sts_shadow
Restart=on-failure
RestartSec=30

[Install]
WantedBy=multi-user.target
```

**Process guards (in script):**

- `application_name=sts_shadow`
- `statement_timeout` (e.g. 120s) + `lock_timeout` (e.g. 5s) on PG
- One summary log line per cycle: `detected=… open=… upserted=… in_mantis=… elapsed_s=…`

**Deploy steps (after GO only):**

1. `srv_backup.sh` (adapt `--src` to mantis tree on srv-01; script lives at `PyVAM/scripts/srv_backup.sh`)
2. Apply approved DDL once (`lock_timeout=5s`)
3. Copy script (+ any tiny shared helper if needed) to srv-01 mantis dir
4. Install unit; `daemon-reload`; `enable --now ais_sts_shadow`
5. Record **all** other service PIDs before/after — only shadow (+ later srvhealth-reporter) may change
6. Update `/etc/srvhealth.json` if needed; restart **only** `srvhealth-reporter`

---

## 7. srvhealth

- Unit name `ais_sts_shadow.service` matches pattern `ais_*` → should auto-discover.
- After enable: confirm it appears OK (active + log activity within quiet window).
- If pattern somehow misses it, add explicit coverage; restart **only** `srvhealth-reporter`.
- Do **not** add to `ignore` while it is meant to run.

---

## 8. Unit tests (`test_sts_shadow_rule_c.py`)

| Case | Expect |
|------|--------|
| Two cargo 80 m apart, SOG&lt;5 | pair, `rule_band=100m` |
| Two tankers 150 m, both length &gt;150 | pair, `tanker200m` |
| Two tankers 150 m, lengths unknown | **no** pair |
| Two tankers 150 m, only one length &gt;150 | pair (`tanker200m`) |
| Pair 50 m but SOG 6 kn | **no** pair |
| Episode buckets with one 5‑min hole, span ≥2 h | one episode |
| Same hull (shared IMO + name) | suppressed |
| Ship with 3 partners | `bunker_hub` tagged, pairs kept |
| `possible_bunker` when tanker &lt;100 m in episode | true |

---

## 9. Doc (`mantis-detection.md`)

Add section **“STS shadow test (rule C)”**: purpose, rule summary, table name, unit name, `detection_version`, “not in API”, link to this report, revisit after ~1 week (compare `in_mantis` false positives/negatives).

---

## 10. Rollback

```bash
sudo systemctl stop ais_sts_shadow
sudo systemctl disable ais_sts_shadow
# then, with approval:
psql … -f sql/sts_shadow_episode_rollback.sql   # DROP TABLE
```

Restore pre-deploy file backup from `srv_backup.sh` stamp if needed. Leave other services alone.

---

## 11. Open questions for you (before GO)

1. **Lookback hours:** propose **6** — OK, or prefer 3 / 8 / 12?  
2. **DDL:** approve as written, or change column names / add fields?  
3. **WorkingDirectory / Python:** confirm srv-01 path for “existing STS venv” (mantis flat dir vs dedicated venv).  
4. **Geography:** keep all shadow episodes even if `in_excl` / `harbour_excl` (recommended for comparison), or drop those from upserts?  
5. **`in_mantis` match:** same two MMSIs + overlapping time only, or also require distance/score thresholds from live obs?

---

## 12. Approval gate

| Item | Status |
|------|--------|
| Plan | **Ready for review** |
| DDL | **Not applied** — awaiting approval |
| Code / unit / deploy | **Not started** |
| srv-dp | **Not touched** |

**Stopped here.** Reply with GO (and any DDL/lookback answers) to proceed with build + deploy.
