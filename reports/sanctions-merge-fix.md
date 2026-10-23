# Fix: sanctions ratings must not merge across IMOs

**Date:** 2026-10-22  
**Repo:** PySTS  
**Follow-up to:** `dd82a32` (deployed)  
**Scope:** code + read-only dry-run only. No DDL, no deploy, no pnav writes.  
Prior deploy report saved as `reports/sanctions-rating-deploy.md`.

---

## 1. Bug

When merging the sanctions list into `ais_ship_risk_rating`, a sanctioned IMO that shared an MMSI with an existing rating was folded onto that other IMO’s key. Event scoring also treated `sanctions_match` as belonging to the AIS/primary ship even when the hit came from `imo_other` / a peer on the same MMSI.

| Symptom | Cause |
|---------|--------|
| VIOLET 1 (`9154000`) missing → API GREEN 0 | Merged onto PETERPAUL `imo:9163269` |
| AKAR WEST `9258167` RED “sanctions match (SDN, UK)” | Inherited OXIS (`9224805`) lists via shared MMSI / event flag |
| XING YUAN `9408554` RED “sanctions match (SDN, UK)” | Inherited ZEVS (`9168946`) the same way |

---

## 2. Fix

| Area | Change |
|------|--------|
| `sanctions_for` | IMO present → IMO lookup only; else MMSI only (same rule as `match_vessel`) |
| Event collectors | Attribute lists to **primary** IMO only (Lloyd’s: AIS IMO, not `lloyds_imo`) |
| `_find_rating_key_for_sanctions` | IMO-keyed ships always keep `imo:<their IMO>`; never merge onto another IMO via MMSI. Only rekey `mmsi:` → `imo:` for the **same** vessel |
| `score_ship` | With `own_imo` set, ignore event `sanctions_match` (use `extra_sanctions_lists` / job merge instead) |
| `query_ship_risk` | Exact `ship_key` / own IMO; MMSI-only lookup uses `mmsi:` key only; live recompute calls `match_vessel` for this ship |

---

## 3. Tests

```text
./venv/bin/pytest restapi/tests/test_identity_risk.py -q  → 17 passed
./venv/bin/pytest restapi/tests/ -q                       → 81 passed
```

New cases:

- Two sanctioned IMOs sharing an MMSI → two RED rows (VIOLET 1 / PETERPAUL style)
- Contaminated `imo_other` event flag does not RED the non-listed AIS IMO; listed IMO still RED via `extra_sanctions_lists` (OXIS / AKAR WEST style)

---

## 4. Read-only dry-run (pnav)

Simulated: re-attribute event sanctions (primary IMO only) → `score_ship(..., own_imo=…)` → `apply_sanctions_to_ratings`. **No writes.**

### Expected totals (lookback 24)

| Metric | Current (live) | Expected after next job |
|--------|---------------:|------------------------:|
| Total ratings | 2,607 | **2,610** |
| RED | 1,946 | **1,945** |
| AMBER | 661 | **665** |
| `sanctions_match` | 1,939 | **1,938** |
| Newly added sanctioned rows | — | **1,866** |
| Dual `imo:`+`mmsi:` pairs | 0 | **0** |

(+3 rows = OXIS, ZEVS, VIOLET 1 now get their own keys. RED −1 / AMBER +4 from four ships that only had inherited flags — see below.)

### Six focus IMOs — before → after

| IMO | Name | Before (live table) | After (dry-run) |
|-----|------|---------------------|-----------------|
| **9154000** | VIOLET 1 | *no row* | **RED 100**, SDN, `sanctions match (SDN)`, events 0 |
| **9163269** | PETERPAUL | RED 100, SDN | **RED 100**, SDN (unchanged own listing) |
| **9224805** | OXIS | *no row* | **RED 100**, SDN+UK, `sanctions match (SDN, UK)`, events 0 |
| **9258167** | AKAR WEST | RED 100, SDN+UK (inherited SDN) | **RED 100**, **UK only** — `low_mmsi_misuse; sanctions match (UK)` |
| **9168946** | ZEVS | *no row* | **RED 100**, SDN+UK, events 0 |
| **9408554** | XING YUAN / HE BO | RED 100, SDN+UK (inherited SDN) | **RED 100**, **UK only** — `low_mmsi_misuse; sanctions match (UK)` |

**Note on AKAR WEST / XING YUAN:** they do **not** return to AMBER. Both IMOs are themselves on the **UK** list (`uk_vessel`). The fix removes the wrongly inherited **SDN** (from OXIS / ZEVS). Stale-registry AMBER would apply only if they were not listed at all.

### Other ships whose `sanctions_match` changes

These had RED / `sanctions_match` only via peer inheritance; they are **not** on the merged list under their own IMO → expected **AMBER**, `sanctions_match=false`:

| ship_key | Name | Before | After |
|----------|------|--------|-------|
| `imo:9230892` | PEPPERONI | RED, SDN+UK | AMBER 30, `low_mmsi_misuse` |
| `imo:9251781` | SYN ACRAB | RED, UK | AMBER 30, `low_mmsi_misuse` |
| `imo:9197870` | N LUNA | RED, UK | AMBER 40, `medium_mmsi_misuse` |
| `imo:9259173` | SCIONG | RED, SDN+UK | AMBER 40, `medium_mmsi_misuse` |

New own-key sanctioned rows among the focus set: **VIOLET 1**, **OXIS**, **ZEVS** (plus the broader +1,866 sanction-only inserts vs event-only baseline; vs live table the net new keys are these three that were previously collapsed).

---

## 5. Commit

Local only (not pushed / not deployed):

```text
Fix sanctions ratings: never merge listed IMOs by shared MMSI.
```

---

## 6. Not done

- No deploy to srv-dp / srv-01  
- No pnav writes / no job run  
- No DDL  

**Next when approved:** deploy `identity_risk_job.py` + `restapi/identity_risk.py`, run the job once, confirm the six IMOs and the four lost-inheritance ships.
