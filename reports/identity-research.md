# AIS identity manipulation — ship risk rating research (read-only)

**Date:** 2026-10-22  
**Scope:** Research only for an MMSI-misuse risk rating (Lloyd’s List–style RED / score 100 over a 24‑month lookback).  
**Constraints observed:** No DDL, no pnav writes, no commits, no deploy. Queries used `statement_timeout=15s`, `default_transaction_read_only=on`, indexed joins / small tables / `LIMIT`s. **No full scans of AIS position history tables for detection.** Passwords not printed.

**Reference rule:** if a vessel is using another vessel’s MMSI now, or did so in the past 24 months → **RED / score 100**; events outside a user-set window are info-only.

---

## 1. Inventory — what MANTIS already has

| Path | Detects | Storage | Key columns | Kept vs recomputed |
| --- | --- | --- | --- | --- |
| **`restapi/spoofing.py`** (`is_duplicate_mmsi_track`, `relabel_duplicate_mmsi`, `detect_spoofing`) | Impossible jumps (`teleport` / `high_speed`); snap-back / short-gap → `bad_position_data`; track oscillating between two clusters ≥20 km apart → **`duplicate_mmsi`** | Reads **`ais_positionanomaly`** (written by store). Enrichment from `ais_static` | Table: `mmsi`, `event_day`, `ts`, `prev_ts`, lat/lon, `dist_m`, `implied_kn`, `reason`. No IMO/name in table | **Persisted** (daily upsert per MMSI). Detect window for new hits is **max 3 days** of CH track |
| **`backend/vesselspoofingstore.py`** | Same spoofing pipeline for cargo/tanker MMSIs | Upserts **`ais_positionanomaly`** `UNIQUE (mmsi, event_day)` | Same as above + `rule_version`, `created_at`/`updated_at` | **Persisted**; loop ~900s; cargo list cache 12h |
| **`restapi/ais_identity.py`** | Nothing (helpers only) | None | `is_valid_ship_mmsi` (MID 201–775, not `xxx000000`); `clean_ship_name` | N/A |
| **`restapi/identity_conflict.py`** + MCP **`get_identity_conflicts`** → `GET /mantis/identity-conflict` | **Re-flag / dual-MMSI:** ≥2 MMSIs that look like **one hull** (IMO+name/callsign/dims, or name+callsign/dims) | **No event table** | Live: `mmsi`, `imo`, `shipName`, `callsign`, dims, `last_ais_ts`; `detectedAt` = latest AIS ts in group | **Recomputed every request** from latest `ais_static` + `ais_position` |
| **`backend/vesselproximitydetection.py`** identity map | Same same-hull rule to **suppress false STS** pairs; close_reason `same_vessel` | Identity map **in-memory**; STS clusters in proximity tables | `mmsi`/`imo`/`shipName`/`callsign`/dims from `ais_static`; pairs in `ais_vesselproximityobservation` | Map recomputed each cycle; **`same_vessel` closures persisted** on observations |
| **`ais_lloyds`** (on pnav; **not used by MANTIS code today**) | Registry of IMO ↔ MMSI / name (Lloyd’s-style ship particulars) | Snapshot table | `imoshipno`, `maritimemobileserviceidentitymmsinumber`, `shipname`, `updated`, … | Static reference; indexes on IMO and MMSI |
| **`ais_static_evt`** | Type‑5 field change log keyed by **IMO** | Persisted events | `ts`, `imo`, `detchg`, `prev`, `cur` — includes `detchg='mmsi'` | **Persisted** history of identity field changes |

### Important distinction vs Lloyd’s List rule

| Pattern | Meaning | Closest MANTIS signal |
| --- | --- | --- |
| **Using another vessel’s MMSI** (LL RED) | This hull broadcasts an MMSI that belongs to a *different* ship | Dual-track `duplicate_mmsi`; AIS IMO ≠ Lloyd’s IMO for that MMSI; one MMSI seen with multiple IMOs |
| **One hull, two MMSIs** (re-flag) | Same ship identity on two MMSIs | **`/mantis/identity-conflict`** and STS `same_vessel` — **inverse** of LL MMSI theft |

---

## 2. Stored history — can we see 24 months?

| Table / source | Rows (approx.) | Earliest → latest | Distinct keys | 24‑month lookback today? |
| --- | ---: | --- | --- | --- |
| `ais_positionanomaly` | **205** | 2026‑10‑06 → 2026‑10‑22 (~**16 days**) | 105 MMSIs | **No** — days only |
| └ `reason=duplicate_mmsi` | **2** | 2026‑10‑17 → 2026‑10‑19 | **1** MMSI | **No** |
| `ais_static_evt` | **971 020** | 2026‑03‑05 → 2026‑10‑22 (~**7.5 months**) | 22 738 IMOs | **No** — ~7.5 mo |
| └ `detchg='mmsi'` | **20 281** raw (~**1 621** unique IMO/prev/cur/day) | 2026‑03‑05 → 2026‑10‑21 | 1 054 IMOs | **No** |
| `ais_static` (current) | **31 166** | ts 2025‑11‑10 → 2026‑10‑22 | 30 893 MMSI / 28 159 IMO | Snapshot + ~11 mo of row timestamps, not a full history |
| `ais_lloyds` | **39 739** | `updated` 2025‑07‑07 → 2026‑10‑22 | 39 739 IMO / 39 056 MMSI | Registry snapshot, not event history |
| `ais_vesselproximityobservation` | **326 623** | 2026‑05‑29 → 2026‑10‑22 (~**5 months**) | — | **No** for 24 mo; **14** `close_reason='same_vessel'` |
| Identity-conflict API | — | Live only | **653** groups / 1 321 identities (now) | **No history** |

**Verdict:** MANTIS **cannot** evaluate a true **24‑month** MMSI-misuse lookback from current stores. Best historical identity signal is `ais_static_evt` (~7.5 months). Spoofing dual-track history is only ~two weeks.

---

## 3. What counts as “using another vessel’s MMSI” — counts & examples

### Class A — Simultaneous dual track on one MMSI (`duplicate_mmsi`)

| Metric | Value |
| --- | ---: |
| Events | 2 |
| Distinct MMSIs | 1 |
| Period | 2026‑10‑17 … 2026‑10‑19 |

| mmsi | imo / name (current static) | dates | distance |
| --- | --- | --- | ---: |
| 533133348 | 8619132 / SILVER HARMONY | 2026‑10‑17, 2026‑10‑19 | ~**11 112 km** between near-simultaneous fixes (MY lon ~101° vs lon ~1°) |

Thresholds in code: clusters ≥20 km apart, spread &lt;2 km, ≥3 switches (`spoofing.py`).

### Class B — AIS Type‑5 IMO ≠ Lloyd’s IMO for the same MMSI

| Metric | Value |
| --- | ---: |
| Current mismatches (valid ship MMSI + plausible IMOs) | **111** |
| Same display name (likely registry lag / dual IMO coding) | **10–11** |
| Different / null names (stronger misuse signal) | **~100–101** |

**10 examples (different names preferred):**

| mmsi | AIS IMO / name | Lloyd’s IMO / name | AIS static ts |
| --- | --- | --- | --- |
| 441510000 | 9742077 SEA TRADER | 9372377 FC GRACE | 2026‑10‑22 |
| 613618200 | 9529293 LUNA | 9206669 MARANTA | 2026‑10‑22 |
| 620800194 | 9360403 STEDYON | 9053995 WASEL | 2026‑10‑22 |
| 613617404 | 9259343 LORIAN | 9072513 GLORY 9 | 2026‑10‑22 |
| 650000133 | 9242120 TIANMA | 8552229 ARES | 2026‑10‑22 |
| 629009824 | 9189110 TELA | 8999881 NOYA 01 | 2026‑10‑22 |
| 613612000 | 9258167 AKAR WEST | 9224805 OXIS | 2026‑10‑22 |
| 613833000 | 9408554 XING YUAN | 9168946 ZEVS | 2026‑10‑22 |
| 352005545 | 9223345 REMIZE | 9223344 REMIZ | 2026‑10‑22 |
| 677062600 | 9230892 PEPPERONI | 9277735 LUNAR TIDE | (in sanc set) |

### Class C — One MMSI with ≥2 distinct IMOs in current `ais_static`

| Metric | Value |
| --- | ---: |
| MMSIs | **9** |

| mmsi | IMOs | names | earliest → latest |
| --- | --- | --- | --- |
| 371633000 | 9117727, 9664897 | SUPER SHUTTLE RORO 7 \| YM MODERATION | 2026‑08‑22 → 2026‑09‑29 |
| 525001052 | 1597500, 8650564 | KMP MENUMBING RAYA (near-duplicate names) | 2026‑08‑28 → 2026‑09‑18 |
| 525003657 | 8694948, 9954802 | TB LL LOTTUS 02 \| TB LL MARIO | 2026‑10‑20 → 2026‑10‑21 |
| 525015961 | 9371654, 9392858 | (blank) \| GAS ARIA | 2026‑09‑01 → 2026‑10‑08 |
| 525110123 | 9146200, 9792802 | KP. ORCA 02 \| JAYA 99 | 2026‑09‑02 → 2026‑09‑15 |
| 525210020 | 1196193, 1199548 | GOLDEN QUEEN BARUNA \| SUD 2200 18 | 2026‑09‑01 → 2026‑09‑29 |
| 538007631 | 1048968, 9795919 | FUJEZIJ MARU \| GAZ IMPERIAL | 2026‑07‑25 → 2026‑09‑30 |
| 566357000 | 9106120, 9681235 | KOTA SEGAR \| MV BATAMFAST 18 | 2026‑07‑25 → 2026‑10‑22 |
| 677082300 | 1105041, 9001875 | JADE ISLAND (same name, two IMOs) | 2026‑09‑07 → 2026‑09‑09 |

### Class D — Historical MMSI reused by ≥2 IMOs (`ais_static_evt` mmsi changes)

| Metric | Value |
| --- | ---: |
| MMSIs | **5** (within ~Mar–Jun 2026) |

| mmsi | IMOs | earliest → latest | Notes |
| --- | --- | --- | --- |
| 468241000 | 9259173, 9284582 | 2026‑04‑14 → 2026‑04‑22 | 9284582 = SDN/UK **PANDA** |
| 533111111 | 1111111, 533456789 | 2026‑03‑19 → 2026‑06‑12 | **Placeholder / junk** IDs |
| 561006003 | 9031519, 9418078 | 2026‑03‑29 → 2026‑06‑14 | Both SDN (SEA HERMES / ELIZA II) |
| 613471902 | 9208069, 9291250 | 2026‑03‑17 → 2026‑06‑09 | SDN/UK STELLAR BEVERLY + UK CORTEX |
| 613472010 | 9197870, 9265756 | 2026‑04‑16 → 2026‑05‑15 | UK OASIS on 9265756 |

### Class E — Related but **not** LL “MMSI theft” (document for design)

| Source | Count now / stored | Meaning |
| --- | --- | --- |
| Identity-conflict live groups | **653** groups (61 sanctions-labelled) | Same hull, **two MMSIs** (re-flag) |
| Proximity `same_vessel` closures | **14** (2026‑07 → 2026‑09) | Same rule applied to STS |

---

## 4. Sanctions link (merged 1940-ship universe)

Sanction views used: `ofac_sdn_vessel` (1559), `ofac_cons_vessel` (0), `un_vessel` (1), `uk_vessel` (682). Overlap is by **IMO or MMSI** on those views.

| Class | Events / MMSIs with sanctions hit | Listed |
| --- | ---: | --- |
| A `duplicate_mmsi` | **0** / 1 | SILVER HARMONY 8619132 — not on lists |
| B Lloyd’s mismatch | **13** MMSIs | See list below |
| C multi-IMO `ais_static` | **0** | — |
| D evt shared MMSI | **4** of 5 MMSIs (excl. junk 533111111) | PANDA, SEA HERMES, ELIZA II, STELLAR BEVERLY, CORTEX, OASIS |
| E identity-conflict (re-flag) | **61** groups | Live API (examples: NADIA, MT VIRGO, WHITE CRANE, DANYA, …) |

### Lloyd’s-mismatch × sanctions (13)

| mmsi | AIS IMO / name | Lloyd’s IMO / name | Lists |
| --- | --- | --- | --- |
| 538010377 | 9247986 AVA 10 | 9820960 CONCARAN | SDN AVA 10 |
| 613407803 | 9255684 NANJING | 9134426 XIANG SHUN | SDN GOLDEN EAGLE; UK FIORA |
| 613612000 | 9258167 AKAR WEST | 9224805 OXIS | UK AKAR WEST; SDN+UK OXIS |
| 613833000 | 9408554 XING YUAN | 9168946 ZEVS | UK HE BO; SDN+UK ZEVS |
| 677062600 | 9230892 PEPPERONI | 9277735 LUNAR TIDE | SDN ROSALIND; UK LUNAR TIDE |
| 352005545 | 9223345 REMIZE | 9223344 REMIZ | SDN REMIZ |
| 613471902 | 9251781 SYN ACRAB | 9291250 CORTEX | UK CORTEX |
| 613472010 | 9197870 N LUNA | 9265756 SUNNY | UK OASIS |
| 650000125 | 9268112 ALBA | 8551990 FELICITY MACKENZIE | SDN+UK OLIA |
| 650000126 | 9308778 AMIL | 8552255 POSEIDON | SDN SATINA |
| 650000133 | 9242120 TIANMA | 8552229 ARES | SDN NYANTARA |
| 629009824 | 9189110 TELA | 8999881 NOYA 01 | SDN TELA |
| 352245000 | 9262766 ZEUS GLORIA | 8303989 GHENA | SDN FIONA II |

### Identity-conflict × sanctions (live, re-flag — not MMSI theft)

61 groups. Sample (same IMO on two MMSIs, SDN):

| IMO | name | MMSIs |
| --- | --- | --- |
| 9122461 | NADIA | 341765000, 750775000 |
| 9236250 | MT VIRGO | 611000208, 629009436 |
| 9323429 | WHITE CRANE | 352004693, 668116508 |
| 9294331 | DANYA | 650000120, 650000304 |

---

## 5. False-positive risks

| Risk | Effect on counts | Rough inflation |
| --- | --- | --- |
| **Invalid / default MMSIs** (0, 111111111, 123456789, `xxx000000`, MID outside 201–775) | Already filtered in most Class B/C queries via `ais_identity` rules. Class D still contains **533111111** + placeholder IMOs | Without filter, Class D would be noisier; **~20%** of the 5 shared-MMSI set is junk (1/5) |
| **Placeholder IMOs** (1234567, 1111111, repdigits) | Identity-conflict already drops these; Class C/D should too | Class C includes dubious IMOs like **1597500**, **1196193** — treat as weak |
| **Same name, different IMO** (Lloyd’s vs AIS) | ~10–11 of 111 mismatches | ~**10%** of Class B may be coding/lag, not theft |
| **Shared MMSIs on small craft / domestic** | Indonesian MID `525…` dominates Class C multi-IMO | Several Class C rows look like ferry/tug churn — **high FP** if scored RED without ship-type / size gate |
| **MID / flag mismatches** | Only 1 invalid ship MMSI in Lloyd’s-mismatch set after filters | Low after `VALID_SHIP_MMSI` |
| **AIS base stations / SAR** (`00…` / `99…` MIDs) | 0 in filtered Lloyd’s-mismatch set | Low if MID filter kept |
| **Re-flag ≠ MMSI theft** | 653 identity-conflict groups would **massively** inflate RED if mislabeled as LL misuse | Do **not** map re-flag to MMSI-theft RED without a separate amber rule |
| **`duplicate_mmsi` RF ghosts** | Code allows ≤10% outliers; SILVER HARMONY case is extreme distance | Rare in store today (n=2); keep as high confidence when it fires |
| **Duplicate `ais_static_evt` rows** | 20 281 raw mmsi changes → ~1 621 unique/day | Deduplicate before scoring |

---

## 6. Draft design (**do not build**)

### 6.1 Concepts

1. **`identity_event`** — durable facts (one row per detected episode).  
2. **`ship_risk_rating`** — current scorecard per ship key (prefer **IMO**, else MMSI), with lookback.  
3. **Lookback setting** — default **24 months**; API query param overrides; events older than window remain queryable as **infoOnly**.  
4. **Rating** — RED / AMBER / GREEN + numeric score.  
   - MMSI misuse (Classes A/B-strong/D) inside window → **RED, score 100**.  
   - Sanctioned IMO/MMSI (merged lists) → **RED** (score ≥100 or parallel flag `sanctionsMatch`).  
   - Re-flag / dual-MMSI (identity-conflict) → proposed **AMBER** (not LL RED) unless product says otherwise.  
   - Else GREEN.

### 6.2 Event types (suggested)

| `event_type` | Source to backfill | Confidence |
| --- | --- | --- |
| `dual_track_mmsi` | `ais_positionanomaly` where `reason='duplicate_mmsi'` | High |
| `lloyds_mmsi_imo_conflict` | Join latest `ais_static` ↔ `ais_lloyds` on MMSI, IMO≠, names differ | Medium–high |
| `mmsi_multi_imo_static` | Current `ais_static` multi-IMO | Medium |
| `mmsi_reused_static_evt` | `ais_static_evt` `detchg='mmsi'` collapsed to MMSI→many IMOs | Medium |
| `reflag_dual_mmsi` | Live identity-conflict (optional persist on schedule) | High for re-flag; **not** LL theft |

### 6.3 API (draft)

| Endpoint | Purpose |
| --- | --- |
| `GET /mantis/ship-risk?imo=&mmsi=&lookbackMonths=24` | Single-ship rating + events (in-window vs infoOnly) |
| `GET /mantis/identity-events?from=&to=&eventType=&sanctionsOnly=` | Event browse |
| Keep `/mantis/identity-conflict` | Live re-flag groups (unchanged) |
| Keep `/mantis/spoofing?includeDataQuality=true` | Dual-track / DQ |

### 6.4 Backfill today

| Source | Backfill depth | Notes |
| --- | --- | --- |
| `ais_positionanomaly` | ~16 days | Tiny; easy |
| `ais_static_evt` mmsi changes | ~7.5 months | Best historical MMSI reuse signal |
| Lloyd’s mismatch | Point-in-time now | Need nightly snapshot table to get history |
| Identity-conflict | None unless scheduled job starts writing | |

**Gap to 24 months:** retain `ais_static_evt` longer; snapshot Lloyd’s mismatches daily; extend spoofing store retention; optional CH-based dual-track job with longer windows (heavy — out of scope for this research).

### 6.5 Draft DDL (full — **do not run**)

```sql
-- Draft only. Not applied.

CREATE TABLE IF NOT EXISTS public.ais_identity_event (
    id              BIGSERIAL PRIMARY KEY,
    event_type      TEXT NOT NULL,
    -- 'dual_track_mmsi' | 'lloyds_mmsi_imo_conflict' | 'mmsi_multi_imo_static'
    -- | 'mmsi_reused_static_evt' | 'reflag_dual_mmsi'
    confidence      TEXT NOT NULL DEFAULT 'medium',  -- high | medium | low
    mmsi            BIGINT,
    imo             TEXT,             -- primary ship key when known (AIS or Lloyd's)
    imo_other       TEXT,             -- conflicting IMO (other vessel / registry)
    ship_name       TEXT,
    ship_name_other TEXT,
    ts_start        TIMESTAMPTZ NOT NULL,
    ts_end          TIMESTAMPTZ,
    distance_m      DOUBLE PRECISION, -- dual-track separation when applicable
    evidence        JSONB,            -- raw prev/cur, cluster centres, list names, etc.
    source_table    TEXT,             -- ais_positionanomaly | ais_static_evt | ais_lloyds | live
    source_ref      TEXT,             -- e.g. anomaly id / event fingerprint
    sanctions_match BOOLEAN NOT NULL DEFAULT FALSE,
    sanctions_lists TEXT[],           -- e.g. {SDN,UK}
    rule_version    TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (event_type, mmsi, imo, imo_other, ts_start)
);

CREATE INDEX IF NOT EXISTS idx_identity_event_mmsi_ts
    ON public.ais_identity_event (mmsi, ts_start DESC);
CREATE INDEX IF NOT EXISTS idx_identity_event_imo_ts
    ON public.ais_identity_event (imo, ts_start DESC);
CREATE INDEX IF NOT EXISTS idx_identity_event_type_ts
    ON public.ais_identity_event (event_type, ts_start DESC);
CREATE INDEX IF NOT EXISTS idx_identity_event_sanctions
    ON public.ais_identity_event (sanctions_match)
    WHERE sanctions_match;

CREATE TABLE IF NOT EXISTS public.ais_ship_risk_rating (
    id                 BIGSERIAL PRIMARY KEY,
    ship_key           TEXT NOT NULL,          -- 'imo:#######' or 'mmsi:#########'
    imo                TEXT,
    mmsi               BIGINT,
    ship_name          TEXT,
    rating             TEXT NOT NULL,          -- RED | AMBER | GREEN
    score              INTEGER NOT NULL CHECK (score BETWEEN 0 AND 100),
    lookback_months    INTEGER NOT NULL DEFAULT 24,
    mmsi_misuse        BOOLEAN NOT NULL DEFAULT FALSE,  -- LL rule inside lookback
    sanctions_match    BOOLEAN NOT NULL DEFAULT FALSE,
    sanctions_lists    TEXT[],
    event_count_window INTEGER NOT NULL DEFAULT 0,
    event_count_total  INTEGER NOT NULL DEFAULT 0,
    latest_event_at    TIMESTAMPTZ,
    factors            JSONB,                  -- breakdown for UI
    rule_version       TEXT NOT NULL,
    computed_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (ship_key, lookback_months)
);

CREATE INDEX IF NOT EXISTS idx_ship_risk_rating_rating
    ON public.ais_ship_risk_rating (rating, score DESC);
CREATE INDEX IF NOT EXISTS idx_ship_risk_imo
    ON public.ais_ship_risk_rating (imo);
CREATE INDEX IF NOT EXISTS idx_ship_risk_mmsi
    ON public.ais_ship_risk_rating (mmsi);

-- Optional: nightly Lloyd's mismatch snapshot so Class B becomes historical
CREATE TABLE IF NOT EXISTS public.ais_lloyds_mmsi_conflict_snap (
    snap_date     DATE NOT NULL,
    mmsi          BIGINT NOT NULL,
    ais_imo       TEXT NOT NULL,
    lloyds_imo    TEXT NOT NULL,
    ais_name      TEXT,
    lloyds_name   TEXT,
    name_agree    BOOLEAN,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (snap_date, mmsi, ais_imo, lloyds_imo)
);
```

### 6.6 Scoring sketch (draft)

| Condition (inside lookback) | Rating | Score |
| --- | --- | ---: |
| Any high-confidence MMSI misuse event (A, B name-disagree, D non-junk) | RED | 100 |
| Sanctioned IMO/MMSI on merged lists | RED | max(score, 100) |
| Only re-flag dual-MMSI (E) | AMBER | 60 (tunable) |
| Only weak signals (same-name Lloyd’s lag, placeholder IMOs) | AMBER / info | 30–50 |
| None | GREEN | 0 |

Events with `ts_start` older than lookback: return in `infoEvents[]`, do not drive RED.

---

## 7. Recommendations (research)

1. **Do not claim 24‑month coverage** until `ais_static_evt` retention + Lloyd’s conflict snapshots (and/or longer dual-track store) cover that window.  
2. Treat **identity-conflict / `same_vessel` as re-flag (AMBER)**, not Lloyd’s MMSI-theft RED.  
3. Best near-term RED sources: **Lloyd’s mismatch with disagreeing names** (n≈100 now) + **evt MMSI reuse** (n=5 historically, several sanctioned) + rare **`duplicate_mmsi`**.  
4. Gate on `ais_identity.is_valid_ship_mmsi`, drop placeholder IMOs, prefer cargo/tanker for RED automation.  
5. Wire sanctions via existing merged vessel views / `listNames` dedupe logic already in `sanctions.py`.

---

## 8. Cleanup

No DB or host changes. This report only under `reports/cursor-report.md` (not for commit unless requested).
