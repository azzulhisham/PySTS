# Build: identity risk in Phase 1 payloads

**Date (local):** 2026-10-22  
**Mode:** LOCAL BUILD only — code + tests + read-only pnav check. **No DDL, no pnav writes, no deploy, no JWT minting.**  
**Prior research** saved unchanged as [`reports/risk-payload-research.md`](risk-payload-research.md).

---

## 1. Summary

Phase 1 endpoints now append identity-risk labels from the stored `ais_ship_risk_rating` table (`lookback_months = 24`):

| Field | Where | Default (no row / load fail) |
|-------|--------|------------------------------|
| `riskRating` | per ship / vessel / event | `"GREEN"` |
| `riskScore` | same | `0` |
| `riskReason` | same | `"none"` |
| `riskRedCount` / `riskAmberCount` | response top-level (after `sanctionsMatchCount*`) | `0` / `0` |
| `riskRedCount` (0–2) | STS **pair** object (end) | `0` |

**Hard rule kept:** only ADD fields. Existing keys, order, values, counts, filters, and sorts unchanged. Same-payload golden strip of all `risk*` keys restores today’s shape.

---

## 2. Files changed

| Path | Change |
|------|--------|
| `restapi/ship_risk_attach.py` | **new** — cache, match (IMO then MMSI), attach helpers, `strip_risk_keys` |
| `restapi/sts_detection.py` | `attach_risk_pair_sides` + vessel attach; pair `riskRedCount`; top counts |
| `restapi/dark_vessels.py` | attach + per-vessel fields + counts |
| `restapi/illegal_anchoring.py` | same |
| `restapi/loitering.py` | same |
| `restapi/spoofing.py` | same (+ empty-result defaults) |
| `restapi/main.py` | expose `riskRedCount` / `riskAmberCount` on Phase 1 routes only |
| `restapi/static/swagger.json` | optional properties + count fields; required lists untouched |
| `restapi/tests/test_ship_risk_attach.py` | **new** — defaults, IMO>MMSI, TTL, failure→GREEN, golden strips |

**Not touched:** `/mantis/overview`, `/mantis/sanctions`, other endpoints, DDL, job, cron.

Env: `mantis_risk_cache_ttl_s` (default **300**), same pattern as OFAC index cache.

---

## 3. Behaviour

- **Source:** `SELECT ship_key, imo, mmsi, rating, score, factors->>'reason' FROM ais_ship_risk_rating WHERE lookback_months = 24` into in-process `by_imo` / `by_mmsi`.
- **Lookup:** IMO key first (no MMSI fallback if IMO present); else MMSI; else GREEN/0/`none`.
- **Failure:** load or attach exception → warning log + defaults; endpoint must not 500 because of risk.
- **Placement:** risk fields after existing sanctions fields (`sanctionsMatch` / `matchConfidence` / `sanctionsList` / `listNames`). Top counts after `sanctionsMatchCount*`.

Live key order check (dark vessel PARISAN):

`… matchConfidence, sanctionsList, listNames, riskRating, riskScore, riskReason`

---

## 4. Tests

```text
restapi/tests/test_ship_risk_attach.py  — 11 passed
full restapi/tests + backend/tests      — 105 passed
```

Coverage:

- Missing row → GREEN / 0 / `"none"`
- IMO preferred; IMO present but missing → GREEN (no MMSI fallback)
- Cache TTL refresh
- Cache load failure → empty indexes + defaults; attach failure never raises
- Golden: strip `risk*` from Phase 1 payload builders → identical keys/order/values to no-risk shape
- Field append order after sanctions; top-level count order

---

## 5. Read-only pnav check

**Client:** laptop → RDS `pnav`, `statement_timeout` **15 s** (via `mantis_pg_statement_timeout_ms=15000`).  
**No writes. No secrets printed.**

### 5.1 Rating table / cache

| Metric | Value |
|--------|------:|
| Rows (`lookback_months=24`) | **2,610** |
| RED / AMBER in table | **1,945** / **665** |
| Cache keys IMO / MMSI | **2,590** / **20** |
| Full cache load (client) | **~368 ms** |
| TTL | **300 s** |

### 5.2 Phase 1 builders (warm caches)

Sizes: **before** = same payload with all `risk*` keys stripped; **after** = full Phase 1 payload.  
Timing: warm (polygons + OFAC + risk cache already loaded). Cold STS first call ~5.8 s (shared port-area + sanctions load, not risk-specific).

| Endpoint | Rows | riskRedCount | riskAmberCount | Size before | Size after | Δ bytes | Warm time |
|----------|-----:|-------------:|---------------:|------------:|-----------:|--------:|----------:|
| `/mantis/sts-activities` | 8 pairs / 16 vessels | 0 | 0 | 22,950 | 25,094 | +2,144 | 0.35 s |
| `/mantis/darkvessels` | 207 | 12 | 8 | 182,277 | 195,282 | +13,005 | 1.35 s |
| `/mantis/illegal-anchoring` | 891 | 53 | 59 | 792,727 | 848,938 | +56,211 | 1.39 s |
| `/mantis/loitering` | 0 | 0 | 0 | 57 | 97 | +40 | 0.08 s |
| `/mantis/spoofing` | 21 | 4 | 0 | 15,624 | 17,013 | +1,389 | 0.19 s |

Same-payload golden (strip `risk*`): **OK** on all five.

### 5.3 Sample rows

SEA PEARL was **not** in the live STS set at check time (0 RED/AMBER on current pairs). Sanctioned / RED samples:

| Endpoint | Sample |
|----------|--------|
| darkvessels | **PARISAN** MMSI `422037300` IMO `9465851` — sanctions SDN, `riskRating=RED`, `riskScore=100`, reason `sanctions match (SDN)`, `darkReason=low_evidence_ais_gap` |
| illegal-anchoring | **VICTORY ARI** MMSI `305049000` IMO `9290919` — SDN, RED/100/`sanctions match (SDN)`, reason `in_restricted_zone+in_watch_polygon` |
| spoofing | **AMINA** MMSI `422035100` IMO `9305192` — SDN, RED/100/`sanctions match (SDN)`, reason `teleport` |
| sts-activities | Example pair obs `216442`: PUTRA SATRIA ↔ ORIENTAL SPIRIT — both GREEN/0/`none`, pair `riskRedCount=0` |
| loitering | Empty list at check time (counts 0) |

---

## 6. Swagger

Optional properties added on Phase 1 vessel/event schemas and response count fields. Existing `required` arrays and property definitions left intact.

---

## 7. Commit

Local commit only (no push / no deploy). Message focuses on additive Phase 1 risk labels.

---

## 8. Out of scope / next

- Phase 2–5 endpoints (`identity-conflict`, `sts-review`, `overview`, `sanctions`, timeline/track) — **not** implemented.
- Deploy to srv-01 / srv-dp — **not** done.
- Live recompute via `score_ship` — **not** used; stored ratings only.

**When approved:** deploy API modules on srv-01 (`ship_risk_attach.py` + updated detectors/`main`/`swagger`); no DDL; job cron unchanged.
