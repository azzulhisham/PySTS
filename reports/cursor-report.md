# Identity risk: rate every sanctioned ship

**Date:** 2026-10-22  
**Repo:** PySTS  
**Scope:** Ensure every vessel on the merged sanctions set gets an `ais_ship_risk_rating` row (RED 100), even with no `ais_identity_event`.  
**Constraints honoured:** no DDL, no pnav writes, no deploy, no cron, no tokens minted. Dry-run was read-only (`statement_timeout` 15s). Prior Tokyo MOU research saved as `reports/tokyo-mou-research.md`.

---

## 1. Problem

`identity_risk_job.py` only wrote ratings for ships that appeared in `ais_identity_event`.  
`/mantis/sanctions` returns **1,940** vessels (e.g. SEA OPERA, TULIP), but most had **no** rating row, so `/mantis/ship-risk` returned nothing for them.

---

## 2. Change

| File | Change |
|------|--------|
| `backend/identity_risk_job.py` | After event-based scoring, merge the same deduped sanctions list as `/mantis/sanctions` (`load_ofac_vessel_list` → SDN + CONS + UN + UK). Missing ships get RED 100; existing rows stay ≥ RED 100 and gain list names in `factors.reason`. IMO key preferred over MMSI (consolidate MMSI-only → IMO). |
| `restapi/identity_risk.py` | `format_sanctions_reason` / `merge_sanctions_lists`; `score_ship` reason text → `sanctions match (LISTS)` (never downgrade). |
| `restapi/tests/test_identity_risk.py` | Tests for no-events RED, no duplicate/downgrade, IMO/MMSI consolidate. |

**Key rules (unchanged):** one row per ship — `imo:#######` when IMO present, else `mmsi:#########`. A ship must not appear under both.

**Reason format:**

- New / sanctions-only: `sanctions match (SDN, UK)`
- Already rated with another reason: `high_mmsi_misuse; sanctions match (SDN, UK)`

**No schema changes.** Sanctions read uses the small OFAC/UN/UK tables/views only (same path as `/mantis/sanctions`).

---

## 3. Tests

```text
./venv/bin/pytest restapi/tests/test_identity_risk.py -q   → 15 passed
./venv/bin/pytest restapi/tests/ -q                        → 79 passed
```

Covered:

1. Sanctioned ship, no events → RED 100, reason with list names  
2. Already-rated RED → not duplicated, not downgraded; lists added to reason  
3. AMBER + sanctions → upgraded to RED 100  
4. MMSI-only event rating + sanctions IMO → consolidated to single `imo:` row  

---

## 4. Read-only pnav dry-run (no writes)

Recomputed event ratings in memory, then applied `apply_sanctions_to_ratings`. Compared to current `ais_ship_risk_rating` (lookback 24).

### Current table (before job)

| Metric | Value |
|--------|------:|
| Total ratings | 744 |
| AMBER | 661 |
| RED | 83 |
| of which `sanctions_match` | 76 |

### Merged sanctions list

| Metric | Value |
|--------|------:|
| `/mantis/sanctions` vessels | 1,940 |
| With usable IMO or MMSI key | 1,938 |
| Skipped (no IMO/MMSI) | 2 |
| Already present in event-based ratings (by ship_key) | 72 |

### Expected after next job run

| Metric | Value |
|--------|------:|
| **Total ratings** | **2,607** |
| **RED** | **1,946** |
| **AMBER** | **661** |
| GREEN | 0 |
| `sanctions_match = true` | 1,939 |
| **Sanctioned ships newly added** | **1,863** |
| Sanctioned ships upgraded (AMBER→RED) | 0 |
| IMO+MMSI duplicate pairs | 0 |

Derivation check: 744 event ships + 1,863 new = 2,607.  
RED: 83 event RED + 1,863 new = 1,946 (AMBER unchanged at 661).

### Examples

| Ship | IMO | Lists | In ratings today? | After dry-run |
|------|-----|-------|-------------------|---------------|
| SEA OPERA | 9000883 | SDN | **No** | `imo:9000883` RED 100, reason `sanctions match (SDN)`, events 0 |
| TULIP | 8912558 | SDN | **No** | `imo:8912558` RED 100, reason `sanctions match (SDN)`, events 0 |

---

## 5. Commit

Local commit only (not pushed, not deployed):

```text
Ensure every sanctioned ship gets a RED identity risk rating.
```

Includes job merge, reason helpers, tests, and this report. Prior research archived at `reports/tokyo-mou-research.md`.

---

## 6. Not done (by request)

- No DDL / schema changes  
- No writes to pnav  
- No deploy to srv-dp / srv-01  
- No cron changes  

**Next step when approved:** run `identity_risk_job.py` once on srv-dp (or promote the commit and let the existing cron pick it up), then spot-check `/mantis/ship-risk?imo=9000883` and `8912558`.
