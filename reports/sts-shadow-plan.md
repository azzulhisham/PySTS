# PLAN (awaiting GO): STS shadow detector — rule C

**Date:** 2026-10-27 (revised same day after plan review)  
**Status:** **PLAN ONLY — no code, DDL, or deploy until you say go.**  
**Hosts (when approved):** srv-01 only for the shadow unit; **srv-dp untouched**; live STS / API untouched.  
**Prior report archived as:** [`vesselzone-b-hold.md`](vesselzone-b-hold.md)

### Plan revisions (this update)

| # | Fix |
|---|-----|
| 1 | **Episode continuity across cycles:** match/extend open `(mmsi_a, mmsi_b)` by `ts_last` within 1-bucket gap; keep original `ts_start`. New row only if no match. Test: 10 h episode → one row. |
| 2 | **`possible_bunker`:** at least one ship is tanker (80–89) with **known length &lt; 100 m** — **not** distance-based. |
| 3 | **polygons / port_areas:** import is safe (no DB/engine/app); first `get_port_area_index()` loads GeoJSON from disk only. |
| 4 | PG timeouts: **`statement_timeout=30s`**, **`lock_timeout=5s`**. |

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

Same-hull suppression: **reuse** `is_same_vessel` logic (copy or import from proximity module **without editing** that file).  
Tags: `bunker_hub` if a ship has ≥3 partners in the window (tag only, never drop); `possible_bunker` if ≥1 tanker with **known length &lt; 100 m**.

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
| Doc section + srvhealth as needed | Restart any unit except shadow + `srvhealth-reporter` |

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

-- Fast open-episode match per pair (continuity across cycles)
CREATE INDEX IF NOT EXISTS idx_sts_shadow_open_pair
    ON public.ais_sts_shadow_episode (mmsi_a, mmsi_b, ts_last DESC)
    WHERE is_open;

CREATE INDEX IF NOT EXISTS idx_sts_shadow_mmsi_a_ts
    ON public.ais_sts_shadow_episode (mmsi_a, ts_start DESC);

CREATE INDEX IF NOT EXISTS idx_sts_shadow_mmsi_b_ts
    ON public.ais_sts_shadow_episode (mmsi_b, ts_start DESC);

COMMENT ON TABLE public.ais_sts_shadow_episode IS
    'Shadow STS pair episodes (rule C). Not consumed by MANTIS API.';
```

**Canonical pair key:** always store `mmsi_a < mmsi_b`.  
**UNIQUE `(mmsi_a, mmsi_b, ts_start)`** remains; continuity means we **reuse** the existing open row’s `ts_start` instead of inventing a new one each cycle.

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
- Bucket: `toStartOfFiveMinutes(ts)`; `argMax` lat/lon/sog (length from PG static)
- Window: rolling **last 6 h** (env `sts_shadow_lookback_hours`, default 6) — enough to form/extend ≥2 h episodes; **longer episodes survive via open-row continuity (§4.3)**, not by expanding the CH window indefinitely
- Ship shortlist: cached latest `ais_static` with `shipType` **70–89** (+ valid ship MMSI filter)
- Chunk MMSIs (e.g. 1500) — **no full CH scan**
- Loop: **15 min** (`sts_shadow_loop_seconds`, default 900)

### 4.2 Pair rule (per coinciding 5‑min bucket)

For each bucket, among ships with usable SOG &lt; 5 kn:

1. Distance ≤ **100 m** → candidate, `rule_band = '100m'`
2. Else distance ≤ **200 m** **only if** both types 80–89 **and** `max(length_a, length_b) > 150` where `length = to_bow + to_stern`; **unknown length ⇒ not longer** → `rule_band = 'tanker200m'`
3. Drop same-hull pairs via `is_same_vessel` (IMO+corroboration / name+callsign|dims)
4. **`possible_bunker`:** true when **at least one** of the two ships is shipType **80–89** with **known** length **&lt; 100 m** (unknown length ⇒ not a bunker candidate). **Not** based on pair distance.
5. After pairing in the window: any MMSI with ≥ **3** distinct partners → `bunker_hub_*` true on episodes involving that MMSI (never drop)

### 4.3 Episode grouping **and cross-cycle continuity**

**Within one CH window (in-memory):**

- Consecutive buckets where the same ordered pair qualifies, allowing **one** missing 5‑min gap
- Candidate episode needs span `ts_last - ts_start` ≥ **2 h** before it is persisted as open (or extend an existing open row that already met the bar)

**Across cycles (against Postgres) — critical for lookback=6 h:**

1. Load open rows for pairs seen this cycle: `is_open` + `(mmsi_a, mmsi_b)` (use `idx_sts_shadow_open_pair`).
2. For each in-window episode candidate for a pair:
   - If an open row exists with `ts_last` within **one 5‑min bucket** of the candidate’s earliest bucket in this window (gap ≤ 10 min wall, matching the “1 missed bucket” rule), **UPDATE that row**: keep its **`ts_start`**, advance `ts_last`, refresh aggregates/flags/centroid/`duration_h`/`updated_at`.
   - Else **INSERT** a new row with the candidate’s `ts_start` (only when no matching open episode).
3. Never recompute `ts_start` from “min bucket still inside the 6 h lookback” for an already-open episode — that would slide `ts_start` and create duplicates under `UNIQUE (mmsi_a, mmsi_b, ts_start)`.
4. Close (`is_open = false`) when the pair is absent beyond the allowed gap for a cycle.

**Example:** a 10 h continuous pair observed over several 15‑min cycles with 6 h CH lookback → **exactly one** open row; `ts_start` stays at the true episode start; `duration_h` grows past 6 h.

### 4.4 Geography (read-only) — import side-effect check

| Module | At `import` | On first use |
|--------|-------------|--------------|
| `restapi/polygons.py` | Pure in-memory polygon constants; **no** DB, engine, Flask, ClickHouse | N/A |
| `restapi/port_areas.py` | Paths + empty cache only; **no** DB/engine/app | `get_port_area_index()` reads GeoJSON from disk, builds STRtree in memory |
| `backend/polygons.py` | Loads restapi polygon list via importlib; **no** DB | N/A |

**Conclusion:** Safe to import from the MANTIS API folder. Shadow script will call `get_port_area_index()` **once at process start** (explicit), then label:

- Parent anchorage / Excl → `zone_name`, `in_excl`
- Harbour / port-area → `harbour_excl`

Labels only; do **not** filter episodes out of the table (full set for comparison).  
No need to copy pure functions unless deploy layout cannot see `restapi/` on `sys.path` (same pattern as loitering).

### 4.5 `in_mantis`

Read-only: overlapping observation for the same two MMSIs with overlapping time (bounded query). No writes to proximity tables.

### 4.6 Detection version

`detection_version = 'shadow-C-1.0'`

### 4.7 Postgres connection guards

- `application_name=sts_shadow`
- **`statement_timeout=30s`**
- **`lock_timeout=5s`**
- Small pool (1 connection)

---

## 5. Files to add (after GO)

| Path | Role |
|------|------|
| `backend/sts_shadow_detection.py` | Shadow loop: CH fetch, rule C, open-row continuity, upsert, summary log |
| `backend/tests/test_sts_shadow_rule_c.py` | Unit tests (see §8) |
| `sql/sts_shadow_episode.sql` | Create table (approved DDL only) |
| `sql/sts_shadow_episode_rollback.sql` | Drop table |
| `deploy/ais_sts_shadow.service` | Unit file for `/etc/systemd/system/` |
| `backend/mantis-detection.md` | “STS shadow test (rule C)” section |
| `reports/cursor-report.md` | This plan → later deploy results |

**Not created/edited:** `vesselproximitydetection.py`, `sts_detection.py`, `main.py`, swagger, other detectors.

---

## 6. Proposed systemd unit (`ais_sts_shadow.service`)

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

**Process guards:** as §4.7; one summary log line per cycle:  
`detected=… open=… extended=… inserted=… closed=… in_mantis=… elapsed_s=…`

**Deploy steps (after GO only):**

1. `srv_backup.sh` (adapt `--src` to mantis tree; script at `PyVAM/scripts/srv_backup.sh`)
2. Apply approved DDL once (`lock_timeout=5s`)
3. Copy script to srv-01 mantis dir
4. Install unit; `daemon-reload`; `enable --now ais_sts_shadow`
5. Record **all** other service PIDs before/after — only shadow (+ later srvhealth-reporter) may change
6. srvhealth if needed; restart **only** `srvhealth-reporter`

---

## 7. srvhealth

- `ais_sts_shadow.service` matches `ais_*` → should auto-discover.
- Confirm OK after enable; do **not** put on `ignore` while running.
- Restart **only** `srvhealth-reporter` if config changes.

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
| Tanker with **known length 80 m** + other ship | `possible_bunker=true` |
| Tanker with **unknown length** | `possible_bunker=false` (length gate) |
| Tanker length 120 m | `possible_bunker=false` |
| Cargo-only pair | `possible_bunker=false` |
| **10 h episode across several cycles** (6 h lookback) | **exactly one** row; `ts_start` unchanged; `ts_last` / `duration_h` advance |

---

## 9. Doc (`mantis-detection.md`)

Add **“STS shadow test (rule C)”**: purpose, rule summary (incl. continuity + `possible_bunker` length rule), table, unit, `detection_version`, not in API, link here, revisit after ~1 week.

---

## 10. Rollback

```bash
sudo systemctl stop ais_sts_shadow
sudo systemctl disable ais_sts_shadow
# then, with approval:
psql … -f sql/sts_shadow_episode_rollback.sql   # DROP TABLE
```

---

## 11. Open questions (still useful before GO)

1. **DDL:** approve as written (incl. `idx_sts_shadow_open_pair`)?  
2. **WorkingDirectory / Python** on srv-01 for STS venv?  
3. **Geography:** keep excl/harbour episodes in table? (recommend yes)  
4. **`in_mantis`:** same two MMSIs + time overlap only?

Lookback **6 h** is assumed unless you change it; continuity makes longer episodes safe.

---

## 12. Approval gate

| Item | Status |
|------|--------|
| Plan | **Revised — ready for review** |
| DDL | **Not applied** |
| Code / unit / deploy | **Not started** |
| srv-dp | **Not touched** |

**Stopped here.** Say **go** (with any DDL answers) to build and deploy.
