# Suspected-anchoring: harbour context + confirmed-stop elapsed (local prepare)

**Date (local):** 2026-11-06  
**Scope:** Additive API enrichments for `GET /mantis/illegal-anchoring` only. **No** commit, push, deploy, restart, or production DB changes.  
**Prior `reports/cursor-report.md` archived as:** [`sts-pair-distance-100m-deploy-2026-11-06.md`](sts-pair-distance-100m-deploy-2026-11-06.md)

---

## 1. Verdict

| Item | Result |
|------|--------|
| A. Port/terminal/berth context | **Implemented** — additive labels via existing `port_areas` STRtree cache |
| B. Actual time stopped | **Implemented where evidence exists** — from `tsstop` → `tscurrent` only |
| Candidate keep/drop | **Unchanged** (Restricted / watch / Excl / port-area exclusion) |
| Existing payload fields | **Preserved** (including legacy `duration*`, `tsStop`, `rowcount` still selected in SQL) |
| True first-stationary moment | **Not available** on current row — see pending |
| PyVAM | Read-only; polygons already identical under PySTS — **no copy needed** |
| Deploy / commit | **Not done** |

Terminology: this endpoint remains a **suspected / review** heuristic. Position and stop duration do **not** prove illegality.

---

## 2. Investigation summary

### Geographic context

| Finding | Detail |
|---------|--------|
| PySTS polygons | `data/polygons/src/{ports,terminals,berths}.geojson` already present |
| PyVAM polygons | Same three files under `PyVAM/data/polygons/src/` — **identical SHA-1** |
| Existing facility | `restapi/port_areas.py` loads/caches once; STRtree point tests; used for port-area **exclusion** and STS harbour labels |
| Runtime paths | Relative to PySTS (`restapi/` → `../data/polygons/src` or flat deploy beside module). **No** PyVAM imports, symlinks, or absolute runtime paths |

Provenance (accurate): these GeoJSON files are **operational reference geometry**. They are **not** verified legal port limits and **must not** be treated as designated anchorage boundaries. Official anchorage-boundary validation and exclusion-policy decisions remain **pending**.

### Stop timing evidence (`ais_vesselmovementactivities`)

| Field | Meaning |
|-------|---------|
| `ts` | First low-speed AIS fix that opened the open activity (episode start) |
| `tsstop` | AIS time when stop was **confirmed** (`rowcount >= 20` and distance `< 30` m) |
| `tscurrent` | Last AIS fix on the open activity |
| `rowcount` | Near-stationary update count — **not** minutes |

Reliable beginning of the **confirmed** continuous stop on the current open row: **`tsstop`**.  
True first moment the hull became stationary (before confirmation) is **not stored**. Inventing it from lookback or from activity `ts` was rejected for the new elapsed fields.

---

## 3. What was implemented

### A. Harbour / berth context (additive)

| Change | File |
|--------|------|
| `anchorage_wins=` flag on `annotate_harbour_location` | `restapi/port_areas.py` |
| Label-only annotate after keep/drop (`anchorage_wins=False`) | `restapi/illegal_anchoring.py` |
| Vessel fields: `inPortArea`, `portAreaName`, `portAreaTerminalName`, `atBerth`, `berthName` | payload |
| Response: `berthCount`, `polygonProvenance` | `main.py`, overview summary |

Selection still uses `annotate_in_port_area` / `drop_port_area_candidates` with **anchorage wins** for exclusion. Labels may still name overlapping operational port/terminal footprints on kept watch-polygon candidates. Berth is independent. No new DB spatial scans; no per-vessel file reload.

### B. Confirmed-stop elapsed (additive)

| Field | Semantics |
|-------|-----------|
| `stopStartedAt` | UTC ISO from `tsstop`, or null |
| `lastObservedAt` | UTC ISO from `tscurrent` (same instant as `lastSeenAt`) |
| `stoppedElapsedSeconds` / `stoppedElapsedHours` | `lastObservedAt − stopStartedAt` when known |
| `stopElapsedStatus` | `known` \| `unknown` |
| `stopElapsedReason` | Explanation when unknown |

Rules:

- Elapsed from **timestamps only** — never `rowcount` or assumed AIS intervals.
- End at **`tscurrent`**, not wall clock (stale AIS does not inflate duration).
- Missing `tsstop`, missing `tscurrent`, or `tscurrent < tsstop` → null elapsed + reason.
- Does **not** substitute activity `ts` or a lookback window for the new fields.
- Legacy `durationSeconds` / `durationHours` / `durationLabel` **unchanged** (still may fall back to `ts`).

`ruleVersion` → `v3.3-suspected-anchoring-harbour-context-confirmed-stop-elapsed`.

### Docs / tests

| Path | Role |
|------|------|
| `restapi/static/swagger.json` | Additive vessel + response schema; suspected terminology |
| `backend/mantis-detection.md` | Factor notes + changelog |
| `restapi/tests/test_illegal_anchoring_enrichment.py` | New local fixtures |

Untouched: STS 100 m rules, shadow detector, scoring, notifications, sanctions logic, trajectory job.

---

## 4. Test results (actual)

```text
pytest restapi/tests/test_illegal_anchoring_enrichment.py \
       restapi/tests/test_port_areas.py
→ 23 passed

pytest …enrichment + test_port_areas + ship_risk phase2–5
→ 51 passed

swagger.json → parses
```

| Case | Result |
|------|--------|
| Known elapsed (2.5 h / 3 h) | pass |
| Unknown without `tsstop` | pass |
| Stale AIS capped at `tscurrent` | pass |
| Inconsistent timestamp order | pass |
| Naive timestamps → UTC | pass |
| Legacy duration preserved alongside new fields | pass |
| Outside point → no harbour hit | pass |
| Westport → port match | pass |
| Anchorage-wins exclusion vs label-only port name | pass |
| Excl / port-drop selection unchanged | pass |
| Empty / invalid geometry safe | pass |
| Provenance flags false for legal/designated | pass |

---

## 5. Pending (not implemented)

| Item | Why |
|------|-----|
| True first-stationary timestamp | Not on the open movement row; needs new persistence or history scan — **out of scope** without approval |
| Design that would be proposed if required | Smallest option: store `ts_stop_begin` when `rowcount` first increments from a moving state (trajectory write path only); API reads that column. No new tables if column added to existing activity row. **Not built.** |
| Official anchorage-boundary validation | Explicitly pending product/legal |
| Changing Excl / Restricted / watch rules | Forbidden this pass |
| Deriving “designated anchorage” from berth footprints | Forbidden |
| Frontend display of new fields | **API only** — UI unchanged until a separate frontend task |
| Commit / deploy | Not requested |

---

## 6. Frontend work required (not done)

API-only. To surface these improvements in the UI, a separate frontend change must:

1. Render `portAreaName` / `portAreaTerminalName` / `berthName` (and flags) on suspected-anchoring cards/map.
2. Prefer `stoppedElapsedHours` + `stopElapsedStatus` for “time stopped”; show `stopElapsedReason` when unknown; keep legacy `duration*` only if still needed for old clients.
3. Show `polygonProvenance.note` (or equivalent) so operators do not treat reference polygons as legal limits.
4. Keep copy as **suspected / review**, not confirmed illegality.

No UI change is claimed from this work.

---

## 7. File-by-file summary

| Path | Change |
|------|--------|
| `restapi/port_areas.py` | `anchorage_wins` on harbour annotate |
| `restapi/illegal_anchoring.py` | Elapsed helper, harbour labels, provenance, rule v3.3 |
| `restapi/main.py` | Expose `berthCount`, `polygonProvenance` |
| `restapi/mantis_overview.py` | Same additive summary fields |
| `restapi/static/swagger.json` | Additive docs only |
| `backend/mantis-detection.md` | Semantics + changelog |
| `restapi/tests/test_illegal_anchoring_enrichment.py` | New tests |
| `data/polygons/src/*` | Already present & matching PyVAM — unchanged |

---

## 8. Safety checklist

| Constraint | Honoured |
|------------|----------|
| Local only; no commit/push/deploy/restart | Yes |
| No pnav stress / no new history scans | Yes |
| No tables/indexes | Yes |
| No PyVAM runtime dependency | Yes |
| STS 100 m / shadow / scoring / notifications untouched | Yes |
| Existing candidate inclusion/exclusion unchanged | Yes |
| Existing response fields preserved | Yes |
