# Build: identity risk in Phase 4 overview + sanctions payloads

**Date (local):** 2026-10-23  
**Mode:** LOCAL BUILD + TEST only — **no deploy, no push, no DDL, no pnav writes, no JWT minting.**  
**Prior report** (Phase 3 deploy) archived as [`reports/risk-payload-phase3-deploy.md`](risk-payload-phase3-deploy.md) in commit `6ba1a3b` (reports-only).

Phases 1–3 (`c7a5bd5`, `298a5d2`, `7f52efb`) remain live. This build adds summary risk counts to **`/mantis/overview`** and per-ship risk fields to **`/mantis/sanctions`**, reusing `ship_risk_attach.py`.

---

## 1. Routes covered

| Path | Method | Handler | Module | What changed |
|------|--------|---------|--------|--------------|
| `/mantis/overview` | GET | `get_mantis_overview` | `main.py` → `mantis_overview.detect_mantis_overview` | Summary counts only (no per-ship lists) |
| `/mantis/sanctions` | GET | `get_sanctions_list` | `main.py` → `sanctions.query_ofac_vessels` | Risk on each `vessels[]` item + list totals |

---

## 2. New fields (additive only)

### `/mantis/overview`

| Location | Fields | Scope |
|----------|--------|-------|
| Top-level | `riskRatedShipCount` | **Whole** `ais_ship_risk_rating` table for lookback 24 (IMO keys + MMSI keys in the process cache). **Not** limited to ships currently in overview modules. |
| Each module block (`darkVessels`, `stsActivities`, `illegalAnchoring`, `spoofing`) | `riskRedCount`, `riskAmberCount` (after existing `sanctionsMatch*` counts) | **Same scope as that module’s sanctions counts** — ships/rows in that detector’s current summary result (dark candidates; STS **paired vessels**; illegal candidates; spoofing anomalies). |

No per-ship arrays added to overview.

### `/mantis/sanctions`

| Location | Fields |
|----------|--------|
| Each `vessels[]` item | `riskRating`, `riskScore`, `riskReason` after existing list fields (`publishDate` …) |
| Response top-level | `riskRedCount`, `riskAmberCount` over the returned vessel list |

**Defaults / failure:** missing row or cache/attach failure → `GREEN` / `0` / `"none"`; overview module counts → `0`; `riskRatedShipCount` → `0`; request must not fail.

**Lookup:** unchanged — `imo:<IMO>` first, else `mmsi:<MMSI>`.

---

## 3. Sample before / after

### Overview module (dark vessels excerpt)

**Before:** … `sanctionsMatchCount` (no risk keys).

**After — added:**

```json
{
  "riskRedCount": 10,
  "riskAmberCount": 8
}
```

**Top-level added:** `"riskRatedShipCount": 2619`

### Sanctions vessel

**Before:**

```json
{
  "imo": "8898831",
  "mmsi": null,
  "vesselName": "7-28",
  "listName": "SDN",
  "publishDate": "…"
}
```

**After — keys added only:**

```json
{
  "riskRating": "RED",
  "riskScore": 100,
  "riskReason": "sanctions match (SDN)"
}
```

---

## 4. Sanctions list RED / AMBER / GREEN breakdown (local full list)

| Metric | Value |
|--------|------:|
| Listed vessels returned | **1,949** |
| RED | **1,947** |
| AMBER | **0** |
| GREEN | **2** |

### GREEN while still on the sanctions list (lookup gap)

These rows have **no IMO and no MMSI**, so identity-risk lookup cannot key the rating table → default GREEN / `none`. Not a scoring bug; an identifier gap on the ingested OFAC vessel row.

| vesselName | listName | imo | mmsi | riskReason |
|------------|----------|-----|------|------------|
| BONU 5 | SDN | null | null | none |
| MAR AZUL | SDN | null | null | none |

---

## 5. Tests

| Suite | Result |
|-------|--------|
| `restapi/tests/test_ship_risk_phase4.py` | **7 passed** (overview summaries + rated count + golden strip; sanctions fields, GREEN fallback, golden strip, key order) |
| Full `restapi/tests` + `backend/tests` | **131 passed** |

---

## 6. Local size & timing (read-only pnav; statement_timeout 15s)

Risk cache: **2,599** IMO + **20** MMSI → `riskRatedShipCount` **2,619**; load ~**0.84 s** (cold).

| Endpoint | Scope | riskRed / riskAmber | Size before → after | Δ bytes | Timing |
|----------|-------|--------------------:|---------------------|--------:|--------|
| `GET` sanctions (via `query_ofac_vessels`) | **1,949** vessels | **1,947** / **0** | 1,021,959 → 1,176,108 | **+154,149** | first **1.41 s**; warm **1.08 s** |
| `GET` overview | summary only; rated **2,619** | per-module (see below) | 2,323 → 2,514 | **+191** | first (includeSpoofing false) **8.7 s**; overview TTL warm **~0.0 s** (`cached=true`) |

Overview module spot (this run): dark RED **10** / AMBER **8** (sanctions **10**); STS **0/0**; illegal RED **53** / AMBER **55** (sanctions **53**); spoofing RED **3** / AMBER **0** (sanctions **3**).

Golden: strip identity-risk keys → pre-existing keys/values unchanged (unit tests).

---

## 7. Files changed (this build)

| Path | Change |
|------|--------|
| `restapi/mantis_overview.py` | module `riskRedCount`/`riskAmberCount`; top `riskRatedShipCount` |
| `restapi/sanctions.py` | attach risk on OFAC list; emit per-vessel risk + list counts |
| `restapi/ship_risk_attach.py` | `rated_ship_count`; strip includes `riskRatedShipCount` |
| `restapi/main.py` | expose sanctions `riskRedCount` / `riskAmberCount` |
| `restapi/static/swagger.json` | overview + OfacVessel / SanctionsListResponse |
| `restapi/tests/test_ship_risk_phase4.py` | **new** |

---

## 8. Deploy steps (later — **not** done now)

srv-01 only (`/home/srvadmin/py/mantis`, flat layout). **Do not** touch srv-dp, job, cron, polygons, pyvam-\*.

1. Push the Phase 4 commit when approved.
2. Backup into `backup-<UTC stamp>/` with `PRE_DEPLOY.txt`:  
   `mantis_overview.py`, `sanctions.py`, `ship_risk_attach.py`, `main.py`, `static/swagger.json`.
3. Diff each server file against the last deployed git baseline (`7f52efb`); **STOP** if dirty.
4. Copy the five files (flat names; swagger under `static/`).
5. Import check with service Python from `/home/srvadmin/py/mantis` (source `mantis.env`, do not echo).
6. `sudo systemctl restart ais_mantis_api` only; confirm `ais_mantis_api` and `pyvam-*` active; `GET http://127.0.0.1:8085/` → **401**.
7. **Verification without minting a token** — call handlers directly:

```bash
set -a; source /home/srvadmin/py/mantis/mantis.env; set +a
cd /home/srvadmin/py/mantis
export PYTHONPATH=$PWD
/usr/bin/python3 - <<'PY'
from mantis_overview import detect_mantis_overview
from sanctions import query_ofac_vessels
from ship_risk_attach import strip_risk_keys
import json

o = detect_mantis_overview(include_spoofing=False)
assert "riskRatedShipCount" in o
for key in ("darkVessels", "stsActivities", "illegalAnchoring"):
    sec = o.get(key) or {}
    assert "riskRedCount" in sec and "riskAmberCount" in sec
    assert "sanctionsMatchCount" in sec or "sanctionsMatchVesselCount" in sec
print("OK overview", o["riskRatedShipCount"], {k: (o[k] or {}).get("riskRedCount") for k in ("darkVessels","stsActivities","illegalAnchoring")})

s = query_ofac_vessels()
v = s["vessels"][0]
assert set(v) >= {"riskRating", "riskScore", "riskReason", "listName"}
assert "risk_red_count" in s
stripped = strip_risk_keys({"vessels": s["vessels"], "riskRedCount": s["risk_red_count"], "riskAmberCount": s["risk_amber_count"]})
assert "riskRating" not in json.dumps(stripped)
print("OK sanctions", len(s["vessels"]), s["risk_red_count"], s["risk_amber_count"], v.get("riskRating"))
PY
```

---

## 9. Rollback

```bash
# SSH srvadmin@10.10.20.201
BK=/home/srvadmin/py/mantis/backup-<stamp>
cp -a $BK/code/mantis_overview.py /home/srvadmin/py/mantis/mantis_overview.py
cp -a $BK/code/sanctions.py /home/srvadmin/py/mantis/sanctions.py
cp -a $BK/code/ship_risk_attach.py /home/srvadmin/py/mantis/ship_risk_attach.py
cp -a $BK/code/main.py /home/srvadmin/py/mantis/main.py
cp -a $BK/static/swagger.json /home/srvadmin/py/mantis/static/swagger.json
sudo systemctl restart ais_mantis_api
# do not touch pyvam-* / srv-dp / cron / polygons
```

---

## 10. Decision

| Item | Status |
|------|--------|
| Routes listed | Done (§1) |
| Additive fields + scoped counts documented | Done (§2–4) |
| GREEN sanctioned gaps listed | Done (§4) — 2 ships, null IMO/MMSI |
| Tests + full suite | **131 passed** |
| Size / timing | Done (§6) |
| Push / deploy | **Not done** (by request) |

**Ready for review** — deploy when approved.
