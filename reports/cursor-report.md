# Round 2 fixes — review flag-only + STRtree harbour lookup

**Date:** 2026-10-21  
**Branch:** `mantis-sts-berth-mmsi`  
**Base:** prior commit `3de440d`  
**Deploy:** **not deployed**

## 1. Summary

| Change | Result |
|--------|--------|
| STS review / unreviewed | **Flag only** — never drop rows; page size / order / `returnedCount` match main |
| New per-row fields | `inPortArea`, `portAreaName`, `portAreaTerminalName`, `atBerth`, `berthName`, `invalidMmsi` |
| New page counts | `inPortAreaCount`, `atBerthCount`, `invalidMmsiCount` |
| `includeInPort` on review lists | **Removed** |
| STS activities / notifications / `notified_at` | Still **drop** harbour + invalid MMSI (activities: `includeInPort` restores harbour) |
| Illegal / loitering / identity | Invalid MMSI drop unchanged (not DB-offset paged) |
| Spatial lookup | Shapely **STRtree** at startup; no per-call GeoJSON / DuckDB CROSS JOIN |
| Warm `/sts-activities` | **0.84 s** (target &lt; 1 s; main was 0.70 s) |

## 2. Files changed

| Path | Change |
|------|--------|
| `restapi/port_areas.py` | STRtree for ports / parent anchorages / berths; `annotate_harbour_location` uses `tree.query(..., predicate="within")`; DuckDB path kept as `annotate_harbour_location_duckdb` for parity tests |
| `restapi/sts_review.py` | `_annotate_cases` (flag-only); filter path only for notifications; `invalidMmsi` on payload; `includeInPort` removed from paged lists |
| `restapi/main.py` | Review endpoints: new count fields; drop `includeInPort` |
| `restapi/tests/test_sts_berth_mmsi.py` | Flag-only page, STRtree↔DuckDB parity, notification drops |
| `restapi/tests/test_sts_notifications.py` | Notifiable skips invalid MMSI |

Unchanged drop behaviour: `sts_detection.py`, notifications filter, `vesselstsreview.py` notify, illegal / loitering / identity MMSI filters.

## 3. Tests

```
52 passed
```

| Case | Result |
|------|--------|
| Review annotate keeps all rows / order | Pass — Westport `atBerth` + `berthName`; invalid row `invalidMmsi` |
| Notifications drop harbour + invalid MMSI | Pass (`includeInPort` keeps harbour, still drops invalid) |
| STRtree ≡ DuckDB on Westport, Klang anchorage, Pengerang, Singapore Excl, open sea | Pass |
| Existing port-area / plausibility / notifications tests | Pass |

## 4. Live read-only: main (deployed) vs branch (`/tmp` overlay)

Deployed API on `.201` = main @ round-1 port areas (no STS berth flags).  
Branch = this tree with `mantis.env`, **no service restart**.

### Row-for-row review pages

| Page | Match | returnedCount |
|------|-------|---------------|
| `/mantis/sts-review?page=1&pageSize=100` | **true** (same `caseId` order) | 100 = 100 |
| `/mantis/sts-review/unreviewed?page=1&pageSize=100` | **true** | 100 = 100 |

Head ids (both): `16, 59, 35, 9, 49`.  
Branch page flags (counts only): review `inPortAreaCount=43`, `atBerthCount=29`, `invalidMmsiCount=1`; unreviewed `40` / `28` / `1`.

### Timings (seconds)

| Endpoint | Main (deployed) | Branch | Notes |
|----------|----------------:|-------:|-------|
| illegal-anchoring | 2.08 | **2.09** | Same illegalCount 862; +6 invalid MMSI excl. |
| loitering | 0.30 | **0.11** | eventCount 2 both |
| sts-activities | 0.70 | **0.84** warm | See cold-start note below |
| sts-review | 0.16 | **0.20** | Flag-only; 100 rows |
| sts-review/unreviewed | 0.13 | **0.13** | Flag-only; 100 rows |
| sts-review/notifications | 0.05 | **0.05** | Empty window both |
| identity-conflict | 7.23 | **7.13** | groups 658→652 (−37 invalid MMSI) |
| darkvessels | 1.36 | — | Not re-run on branch (unchanged) |
| spoofing | 0.06 | — | Unchanged |
| overview | 3.25 | — | Unchanged |

**STS cold-start note:** A fresh Python process’s first `filter_observations_in_anchorages` (DuckDB spatial, unchanged from main) took ~13 s once; harbour STRtree annotate itself is **~2 ms** for 11 clusters. After warmup, full `detect_sts_in_anchorages` = **0.836 s** (pairCount 9, port excl. 2, berth excl. 1). Gunicorn workers keep DuckDB/index warm like main.

**Startup (once per worker):** port/berth STRtree build ~0.6 s locally / ~9 s cold on `.201` when also parsing 1,737 berths (logged: `berths=1737`).

### Counts (where drops still apply)

| Endpoint | Paging model | Drop behaviour |
|----------|--------------|----------------|
| sts-activities | Full result set | Drop harbour by default; `includeInPort=true` restores; always drop invalid MMSI |
| sts-review/notifications | Notify batch (≤200) | Drop harbour + invalid MMSI |
| illegal-anchoring | Full candidate load (not DB OFFSET page) | Drop invalid MMSI (+ existing port-area drop) |
| loitering | Full open-event load | Drop invalid MMSI (+ port clears `outside_anchorage`) |
| identity-conflict | Full candidate load | Drop invalid MMSI |

## 5. Skipped / uncertain

1. **Not deployed** — production still serves prior build until this branch is released.  
2. Fair STS timing requires a warm worker (same as main); cold DuckDB spatial init is unrelated to STRtree.  
3. Notifications window empty in both snapshots — drop path covered by unit tests.  
4. Probe helpers (`reports/_live_compare.py`) not committed.

## 6. Commit

See tip of `mantis-sts-berth-mmsi` after this change set.
