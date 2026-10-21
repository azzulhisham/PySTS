# Round 2 — STS berth / port exclusion + MMSI filter

**Date:** 2026-10-21  
**Branch:** `mantis-sts-berth-mmsi` (from `main` @ `c7ccf03`)  
**Deploy:** **not deployed** (per request)

## 1. Summary

| Item | Result |
|------|--------|
| Berths loaded at startup | **1,737** (logged with ports/terminals) |
| STS harbour flags | `inPortArea` / `atBerth` (+ names) at API + review notify path |
| Default STS / review / notifications | Drop `inPortArea` or `atBerth`; `includeInPort=true` restores |
| `notified_at` stamping | Skips in-port / at-berth and invalid member MMSI |
| MMSI filter | Same `is_valid_ship_mmsi` on illegal, STS, loitering, identity |
| Additive response fields | `portAreaExcludedCount`, `atBerthExcludedCount`, `invalidMmsiExcludedCount` |
| DDL / stored rows | **None** — no new columns; rows unchanged |
| `/sts-review/unreviewed` | **0.70 s** after (page 100) |

## 2. Files changed

| Path | Change |
|------|--------|
| `restapi/port_areas.py` | Load `berths.geojson`; `annotate_harbour_location`; `harbour_exclude_mask`; berth log line |
| `restapi/ais_identity.py` | `filter_valid_ship_mmsi_frame` helper (rule unchanged) |
| `restapi/sts_detection.py` | Harbour annotate + exclude; MMSI filter; additive counts; `includeInPort` |
| `restapi/sts_review.py` | Harbour annotate/filter; `is_notifiable` skips port/berth; invalid MMSI; counts; `includeInPort` |
| `restapi/illegal_anchoring.py` | MMSI filter + `invalidMmsiExcludedCount` |
| `restapi/loitering.py` | MMSI filter + `invalidMmsiExcludedCount` |
| `restapi/identity_conflict.py` | MMSI filter + `invalidMmsiExcludedCount` |
| `restapi/main.py` | Wire `includeInPort` + new counts on STS/illegal/loiter/identity |
| `restapi/mantis_overview.py` | Surface illegal `invalidMmsiExcludedCount` |
| `backend/vesselstsreview.py` | Harbour flags after `label_anchorages`; notify=false if port/berth/invalid MMSI (not persisted) |
| `restapi/tests/test_sts_berth_mmsi.py` | **New** — Westport berth, Klang anchorage, MMSI drops |
| `restapi/tests/test_port_areas.py` | Assert 1737 berths |
| `restapi/tests/test_sts_notifications.py` | Notifiable skips port/berth |

## 3. Tests

```
51 passed (restapi/tests + backend/tests/test_loiter_port_areas.py)
```

| Case | Result |
|------|--------|
| STS centroid at Westport **Berth No.14** | Excluded by default; with `includeInPort=true` → `atBerth=true`, name `Berth No.14` |
| STS centroid in Klang anchorage | Kept (`inPortArea=false`, `atBerth=false`) |
| Invalid MMSI `0`, `111111111`, bad MID | Dropped via shared filter; valid kept |
| Existing port-area / plausibility / notifications tests | Pass |

## 4. Before / after (live, read-only)

**Before** = deployed API on `.201` @ `20a1cf1` / `c7ccf03` (round 1, no STS berth / no MMSI on these endpoints).  
**After** = round-2 code run once from `/tmp/mantis-round2` with `mantis.env` (no service restart, no deploy).

Live AIS churn means absolute counts can move between the two snapshots; exclusion splits below are from the after run.

### Counts

| Endpoint | Before | After (default) | Port excl. | Berth excl. | Invalid MMSI excl. | sec before → after |
|----------|--------|-----------------|-----------:|------------:|-------------------:|--------------------|
| `/mantis/illegal-anchoring` | illegalCount **864** | **868** | 0 | — | **6** | 1.77 → 2.43 |
| `/mantis/loitering` | eventCount **1** | **3** | 0 | — | **0** | 0.26 → 0.30 |
| `/mantis/sts-activities` | pairCount **12** | **10** | **3** | **2** | **0** | 0.65 → ~9* |
| `/mantis/sts-review` (page 100) | returned **100** / total 17122 | returned **54** / total 17122 | **43** | **29** | **1** | 0.21 → 0.78 |
| `/mantis/sts-review/unreviewed` | returned **100** / total 324410 | returned **60** / total 324410 | **38** | **24** | **1** | 0.13 → 0.70 |
| `/mantis/sts-review/notifications` | notificationCount **0** | **0** | 0 | 0 | 0 | 0.04 → 0.15 |
| `/mantis/identity-conflict` | groups **658** / identities **1340** | **652** / **1318** | — | — | **37** | 7.02 → 6.93 |

\*After STS timed two detect passes (`includeInPort` false+true) in the probe (~18 s total); single default call is roughly half.

`includeInPort=true` (STS activities): pairCount **13** (harbour exclusions cleared).  
STS review / unreviewed with `includeInPort=true`: returnedCount **99** on the same page window.

### Startup log (after probe)

```
polygons loaded ports=780 terminals=552 berths=1737; port areas built=923; ports skipped=90
```

### Excluded examples (up to 10)

**STS harbour / berth (activities clusters excluded by default)**

| observationId | MMSIs | Port | Berth | Flags |
|--------------:|-------|------|-------|-------|
| 322380 | 357387000, 574000590 | Kota Kinabalu, Sabah | Berth No.7 | inPort + atBerth |
| 324196 | 533016000, 533735000 | Pengerang / Pengerang Terminal | Berth West | inPort + atBerth |
| 324249 | 219030706, 414155000 | Pengerang / Pengerang Terminal | — | inPort |

**STS review page — port/berth (shown with `includeInPort=true`)**

| observationId | MMSIs | Port | Berth |
|--------------:|-------|------|-------|
| 308568 | 352006547, 563079400 | Port Klang (Pelabuhan Klang) | Berth No.2 |
| 317021 | 351462000, 538005619 | Port Klang (Pelabuhan Klang) | Berth No.3 |
| 310119 | 341973000, 354912000, 566716000, 636024498 | Phuket Port | Phuket Berth No.1 |
| 308535 | 371846000, 566690000 | Johor, Pasir Gudang | MMHE Berth 3 |
| 189986 | 574013747, 574014605 | Duyen Hai | — |
| 196925 | 533132754, 677029100 | Sibu, Sarawak | — |
| 301411 | 525400275, 525500147 | Tanjung Ru | — |
| 315828 | 525003328, 525210123 | Tanjung Uncang | — |
| 312294 | 303582000, 369344000 | Singapore | — |
| 315453 | 312747000, 533133921 | Tanjung Uncang | — |

**Invalid MMSI**

| Endpoint | Count | Example MMSIs |
|----------|------:|---------------|
| Illegal anchoring | 6 | 1193046, 9290775, 50862783, 56312345, 567000000, 832718046 |
| STS review (sample window) | 1+ | obs 248848 → 56312345, 567000000 (with 562111000) |
| Identity conflict | 37 | 13, 907912, 1193046, 5631102, 9290775, 12062552, 50862783, 53726285, 56312345, 61954928 |
| Loitering | 0 | — |
| STS activities (this snapshot) | 0 | — |

Before STS review/unreviewed page samples still included **56312345** and **567000000**; after they are dropped from the default page.

## 5. Skipped / uncertain

1. **No deploy** — production still serves round 1 until this branch is merged/deployed.
2. **Pagination vs filters** — harbour/MMSI filters run on the fetched page, so `returnedCount` can be &lt; `pageSize` while `caseCount` stays the DB total. No new columns (as required).
3. **Illegal `portAreaExcludedCount`** still **0** on live stopped candidates (same as round 1).
4. **Notifications** window was empty in both snapshots (`notificationCount=0`); harbour/MMSI gates are wired but not exercised by live notify traffic here.
5. **Live churn** — illegal/loitering absolute counts moved between before and after snapshots; use exclusion columns for the round-2 effect.
6. Helper probes (`reports/_live_before.py`, `reports/_live_after.py`) used for this report were **not** committed.

## 6. Commit

Branch `mantis-sts-berth-mmsi` — see git log for the commit hash after push of this change set.
