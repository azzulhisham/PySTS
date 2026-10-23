# Build: identity risk in Phase 2 payloads

**Date (local):** 2026-10-23  
**Mode:** LOCAL BUILD + TEST only — **no deploy, no push, no DDL, no pnav writes, no JWT minting.**  
**Prior report** (identity cron lock-fix) archived as [`reports/identity-cron-fix.md`](identity-cron-fix.md) in commit `0e9be04` (reports-only).

Phase 1 (`c7a5bd5`) remains the live baseline for sts-activities / darkvessels / illegal-anchoring / loitering / spoofing. This build extends the same `ship_risk_attach.py` cache to **identity-conflict** and **spoofing/vessel-analysis**.

---

## 1. Routes covered (confirmed)

| Path | Handler | Module | Ship objects in payload |
|------|---------|--------|-------------------------|
| `GET /mantis/identity-conflict` | `get_identity_conflict` | `restapi/main.py` → `identity_conflict.detect_identity_conflicts` | Each **identity** (nested under `groups[].identities[]` and flat `identities[]`): MMSI, static/position fields, dimensions, sanctions, then risk. **Groups** are multi-MMSI clusters (often 2+). |
| `GET /mantis/spoofing/vessel-analysis` | `get_spoofing_vessel_analysis` | `restapi/main.py` → `spoofing_vessel_analysis.detect_vessel_spoofing_analysis` | Single **`vessel`** header (MMSI, flag, static, sanctions, then risk). Events keep spoofing `riskLevel` (critical/high/medium) — **not** identity risk. |

No other Phase 2 routes.

---

## 2. New fields (additive only)

### `/mantis/identity-conflict`

| Location | Fields |
|----------|--------|
| Each identity | `riskRating`, `riskScore`, `riskReason` (after sanctions fields) |
| Each group | `riskRedCount` (how many identities in the group are RED) — same idea as STS pair `riskRedCount`; **no** `riskRatingMax` (would be a new pattern; group size is not always 2) |
| Response top-level | `riskRedCount`, `riskAmberCount` after `sanctionsMatchIdentityCount` |

### `/mantis/spoofing/vessel-analysis`

| Location | Fields |
|----------|--------|
| `vessel` | `riskRating`, `riskScore`, `riskReason` after sanctions |
| Lists / summary | No top-level risk counts (payload is one ship, not a ship list) |

**Defaults / failure:** missing row or cache/attach failure → `GREEN` / `0` / `"none"`; request must not fail because of risk (unchanged helper behaviour).

**Lookup:** unchanged — `imo:<IMO>` first, else `mmsi:<MMSI>`; IMO present but absent from table → GREEN (no MMSI fallback).

---

## 3. Sample before / after

### Identity (one ship in a conflict group)

**Before (strip identity-risk keys):**

```json
{
  "mmsi": 613471907,
  "shipName": "CHENG HE",
  "sanctionsMatch": true,
  "matchConfidence": "confirmed",
  "sanctionsList": "SDN",
  "listNames": ["UK"]
}
```

*(sanctions list values vary; key set/order of pre-existing fields unchanged.)*

**After — keys added only:**

```json
{
  "riskRating": "RED",
  "riskScore": 100,
  "riskReason": "reflag_dual_mmsi; sanctions match (UK)"
}
```

**Group sample** (`613471907_667002010`): both identities RED → `riskRedCount: 2`.

### Vessel-analysis header (AMINA / MMSI `422035100`)

**Before:** … `sanctionsMatch`, `matchConfidence`, `sanctionsList`, `listNames` (no identity-risk keys).

**After — added:** `riskRating: "RED"`, `riskScore: 100`, `riskReason: "sanctions match (SDN)"`.

Spoofing event `riskLevel` remains and is **not** stripped by golden helpers (strip list is explicit identity-risk keys only).

---

## 4. Tests

| Suite | Result |
|-------|--------|
| `restapi/tests/test_ship_risk_phase2.py` | **7 passed** (fields, GREEN fallback, golden strip, group `riskRedCount`, vessel header, attach failure, count key order) |
| Full `restapi/tests` + `backend/tests` | **118 passed** |

---

## 5. Local size & timing (read-only pnav; statement_timeout 15s)

Risk cache: **2,599** IMO + **20** MMSI keys; load ~**1.0 s** (cold).

| Endpoint | Rows / scope | riskRed / riskAmber | Size before → after | Δ bytes | Timing |
|----------|--------------|--------------------:|---------------------|--------:|--------|
| identity-conflict | 654 groups / **1,323** identities | **126** / **1,158** | 1,905,536 → 2,118,279 | **+212,743** | first **4.66 s**; warm **3.48 s** |
| spoofing/vessel-analysis | 1 vessel (AMINA), 4 events; track off; CH mocked for local | n/a (single ship) | 2,781 → 2,859 | **+78** | **0.21 s** (PG + risk; CH stubbed) |

Golden: strip identity-risk keys → same pre-existing key set/order/values (unit tests); live identity-conflict strip used for size “before”.

---

## 6. Known gap: IMO present but not in rating table (proposal only)

**Rule today (unchanged):** if the ship reports an IMO, lookup is IMO-only. No row → GREEN even when `mmsi:<MMSI>` is rated.

### Measured on live identity-conflict identities (cached table only)

| Metric | Value |
|--------|------:|
| Identities in payload | 1,323 |
| With IMO **not** in `by_imo` but MMSI **in** `by_mmsi` | **5** (~0.4%) |
| Shown rating | GREEN (all 5) |
| MMSI table rating | AMBER 60 (all 5 samples) |

| MMSI | Reported IMO | Shown | Would be (MMSI key) |
|------|--------------|-------|---------------------|
| 338600000 | 1494647 | GREEN | AMBER 60 |
| 341707978 | 1342177 | GREEN | AMBER 60 |
| 563721368 | 8547685 | GREEN | AMBER 60 |
| 574140286 | 9043344 | GREEN | AMBER 60 |
| 613918804 | 9137627 | GREEN | AMBER 60 |

Vessel-analysis sample (AMINA) and first 50 spoofing fleet MMSIs: **0** such gaps.

### Proposed safe fallback (do **not** implement in this build)

Only when `normalize_imo(imo)` is set **and** missing from `by_imo`, **and** `normalize_mmsi(mmsi)` hits `by_mmsi`: use the MMSI rating **or** expose a separate `riskRatingMmsi` / `matchConfidence`-style flag so UI can show “MMSI-only identity risk” without silently overriding a wrong IMO. Prefer explicit secondary field over changing primary `riskRating` semantics. Re-measure after any change.

---

## 7. Files changed (this build)

| Path | Change |
|------|--------|
| `restapi/identity_conflict.py` | attach + per-identity fields + group `riskRedCount` + top counts |
| `restapi/spoofing_vessel_analysis.py` | attach on static; vessel header risk fields |
| `restapi/main.py` | expose identity-conflict `riskRedCount` / `riskAmberCount` |
| `restapi/ship_risk_attach.py` | `strip_risk_keys` only removes identity-risk JSON keys (preserves `riskLevel`) |
| `restapi/static/swagger.json` | optional properties / counts |
| `restapi/tests/test_ship_risk_phase2.py` | **new** |

---

## 8. Deploy steps (later — **not** done now)

srv-01 only (`/home/srvadmin/py/mantis`, flat layout). **Do not** touch srv-dp, job, cron, polygons, pyvam-\*.

1. Push the Phase 2 commit when approved.
2. Backup into `backup-<UTC stamp>/` with `PRE_DEPLOY.txt`:  
   `identity_conflict.py`, `spoofing_vessel_analysis.py`, `main.py`, `ship_risk_attach.py`, `static/swagger.json`.
3. Diff each existing server file against the last deployed git baseline; **STOP** if dirty.
4. Copy the five files above (flat names; swagger under `static/`).
5. Import check with service Python (`/usr/bin/python3` / gunicorn shebang) from `/home/srvadmin/py/mantis`.
6. `sudo systemctl restart ais_mantis_api` only; confirm `ais_mantis_api` and `pyvam-api` active; `GET http://127.0.0.1:8085/` → **401**.
7. HTTP checks **without minting a token** (use an existing client JWT if available, or skip authenticated payload smoke).

---

## 9. Rollback

```bash
# SSH srvadmin@10.10.20.201
BK=/home/srvadmin/py/mantis/backup-<stamp>
cp -a $BK/code/identity_conflict.py /home/srvadmin/py/mantis/identity_conflict.py
cp -a $BK/code/spoofing_vessel_analysis.py /home/srvadmin/py/mantis/spoofing_vessel_analysis.py
cp -a $BK/code/main.py /home/srvadmin/py/mantis/main.py
cp -a $BK/code/ship_risk_attach.py /home/srvadmin/py/mantis/ship_risk_attach.py
cp -a $BK/static/swagger.json /home/srvadmin/py/mantis/static/swagger.json
sudo systemctl restart ais_mantis_api
# do not touch pyvam-*
```

---

## 10. Decision

| Item | Status |
|------|--------|
| Routes listed | Done (§1) |
| Additive risk fields | Done |
| Tests + full suite | **118 passed** |
| Size / timing | Done (§5) |
| IMO-gap measure + proposal | Done (§6) — **no lookup change** |
| Push / deploy | **Not done** (by request) |

**Ready for review** — deploy when approved.
