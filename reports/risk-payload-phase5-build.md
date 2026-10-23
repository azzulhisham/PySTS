# Build: identity risk Phase 5 vessel-timeline + vessel-track

**Date (local):** 2026-10-23  
**Mode:** LOCAL BUILD + TEST only — **no deploy, no push, no DDL, no pnav/ClickHouse writes, no JWT minting.**  
**Prior report** (Phase 4 deploy) archived as [`reports/risk-payload-phase4-deploy.md`](risk-payload-phase4-deploy.md) in commit `92e0741` (reports-only).

Phases 1–4 remain live on srv-01. This build adds identity risk to **`GET /mantis/vessel-timeline`** (JSON top-level once) and **`GET /mantis/vessel-track`** (**HTTP headers only**; NDJSON body unchanged).

---

## 1. Routes / files

| Path | Change |
|------|--------|
| `GET /mantis/vessel-timeline` | Top-level `riskRating`, `riskScore`, `riskReason` (not on `events[]`) |
| `GET /mantis/vessel-track` | Response headers `X-Risk-Rating`, `X-Risk-Score`, `X-Risk-Reason` (+ `Access-Control-Expose-Headers`); **no** body / stream-line changes |

| File | Role |
|------|------|
| `restapi/ship_risk_attach.py` | `lookup_imo_for_mmsi`, `risk_for_mmsi`, `risk_payload_for_mmsi`, `risk_http_headers`, `encode_risk_reason_header` |
| `restapi/timelineplayback.py` | Timeline payload spreads risk once |
| `restapi/main.py` | Track resolves headers before `Response` / jsonify |
| `restapi/static/swagger.json` | Timeline fields + track response headers documented; existing NDJSON sample unchanged |
| `restapi/tests/test_ship_risk_phase5.py` | **new** |

---

## 2. Field / header list

### Timeline (JSON body, additive)

| Field | Placement |
|-------|-----------|
| `riskRating` | After `byEventType`, before `events` |
| `riskScore` | same |
| `riskReason` | same |

### Track (headers only)

| Header | Value |
|--------|--------|
| `X-Risk-Rating` | `RED` / `AMBER` / `GREEN` |
| `X-Risk-Score` | decimal string (`0`–`100`) |
| `X-Risk-Reason` | percent-encoded reason; raw text truncated to **200** chars before encoding |
| `Access-Control-Expose-Headers` | `X-Risk-Rating, X-Risk-Score, X-Risk-Reason` (so browsers can read them) |

**Headers are workable** with Flask streaming: risk is resolved **before** the generator runs; headers are set on the `Response` at construction time. Stream lines are byte-identical to pre-change for a fixed fixture (unit test). No extra NDJSON line was added.

---

## 3. IMO lookup choice and cost

| Choice | Detail |
|--------|--------|
| Strategy | **One cheap indexed read** of latest `ais_static` row for the query MMSI (`ORDER BY ts DESC NULLS LAST, id DESC LIMIT 1`), then `match_risk(imo, mmsi)` (IMO first, else MMSI). |
| On IMO lookup failure | Fall back to MMSI-only match (still via cache). |
| On any risk failure | `GREEN` / `0` / `"none"`; request continues. |
| Measured IMO lookup (cold, MMSI `312837000`) | ~**0.82 s** first call (includes PG pool); subsequent risk attach with warm cache ~**0.06–0.29 s** depending on path. |

Rating computation / table / job **unchanged** — read-only use of `ship_risk_attach` cache (`lookback_months=24`, TTL 300 s).

---

## 4. Sample before / after

### Timeline

**Before:** `mmsi`, `dateFrom`, `dateTo`, `eventCount`, `byEventType`, `events` (no risk keys).

**After — added only:**

```json
{
  "riskRating": "RED",
  "riskScore": 100,
  "riskReason": "sanctions match (SDN)"
}
```

(example: AMINA `422035100`)

### Track stream body

**Unchanged** — first line remains `{"type":"meta",...}` with **no** risk keys. Rating appears only in response headers.

---

## 5. Tests

| Suite | Result |
|-------|--------|
| `restapi/tests/test_ship_risk_phase5.py` | **8 passed** (timeline fields; GREEN fallback; golden strip; headers; reason encode/cap; stream source has no risk; fixture body byte-identical with headers layer) |
| Full `restapi/tests` + `backend/tests` | **139 passed** (was 131) |

---

## 6. Local size & timing

| Endpoint | Metric | Before → after / notes |
|----------|--------|-------------------------|
| Timeline (`422035100` AMINA, 2-day, 4 events) | JSON size | 2,175 → 2,253 bytes (**+78**) |
| Timeline AMINA | Latency | **0.53 s** wall (includes cold risk cache + IMO lookup ~**0.66 s** first IMO read) |
| Timeline AMINA | Risk | **RED 100** — `sanctions match (SDN)` |
| Timeline (`312837000`, 0 events) | Size delta | +61 bytes; risk GREEN (IMO `1033016` not in rating table) |
| Track | Headers cold | **0.72 s** (cache load) |
| Track | Time to first NDJSON line | cold **0.14 s**; warm **0.10 s** |
| Track | TTF headers+first (cold) | **0.86 s** vs body-only first line **0.14 s** (header resolve is additive before stream) |
| Track | First line contains `risk` | **false**; warm first lines equal |

---

## 7. Risks / uncertainties

| Item | Note |
|------|------|
| IMO present but not in rating table | Same Phase 1–4 rule: IMO-only lookup → GREEN (no MMSI fallback). Example: `312837000` reported IMO `1033016` → GREEN. |
| `Access-Control-Expose-Headers` | Added so browser JS can read `X-Risk-*`. Additive; does not change body. |
| Swagger JSON sample for track | Also gets the same three headers; JSON **body** still has no risk fields (per “headers only”). |
| Deploy | **Not done** (by request). |

---

## 8. Deploy steps (later — not done now)

srv-01 only. Backup / copy: `ship_risk_attach.py`, `timelineplayback.py`, `main.py`, `static/swagger.json`. Restart **only** `ais_mantis_api`. Verify without JWT: call `get_vessel_activity_timeline` and `risk_http_headers` / stream first line on the server; confirm headers present and first NDJSON line has no risk keys. **Do not** touch srv-dp.

Rollback: restore the four files from `backup-<stamp>/` and restart `ais_mantis_api`.

---

## 9. Decision

| Item | Status |
|------|--------|
| Timeline top-level risk | Done |
| Track headers only; body unchanged | Done (headers workable) |
| IMO lookup documented | Done (§3) |
| Tests + full suite | **139 passed** |
| Push / deploy | **Not done** |

**Ready for review** — deploy when approved.
