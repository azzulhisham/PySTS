# Port-area exclusions for illegal anchoring and loitering

**Branch:** `mantis-port-exclusions`  
**Base:** `e1f29eb` (merge/sts-notifications-v1.3)  
**Scope:** illegal anchoring + loitering only. STS, spoofing, dark vessels, identity untouched.  
**Deploy:** not done (per request).

## Summary

Ships whose last position / loiter centre falls inside a harbour **port area** are no longer counted as illegal anchoring or as default “outside anchorage” loitering. Port areas follow the PyVAM 50 km² rule (port outline, or terminal union when oversized). A point inside both a parent anchorage polygon and a port area counts as **anchorage**, not port.

## Files changed

| File | Change |
|------|--------|
| `restapi/port_areas.py` | **New.** Load `data/polygons/src/{ports,terminals}.geojson`, build/cached port areas, DuckDB annotate helper, anchorage-wins. |
| `restapi/illegal_anchoring.py` | After restricted/watch/Excl, drop port-area candidates; add `port_area_excluded_count` (+ examples/count helpers). Rule → `v3.2-…-port-areas-…`. |
| `restapi/loitering.py` | API-time annotate from centre lat/lon; clear `outside_anchorage` in port; re-filter default list; add payload/summary fields. Rule → `v1.2-…-port-areas`. |
| `restapi/main.py` | Expose `portAreaExcludedCount`, `portAreaCount` on illegal + loitering responses. |
| `restapi/mantis_overview.py` | Pass through new illegal summary fields. |
| `restapi/duckdb_spatial.py` | Coerce pandas `str` dtype → object for DuckDB 1.4. |
| `backend/vesselloiteringdetection.py` | `label_geography` clears `outside_anchorage` inside port areas (no new DB columns). DuckDB frame coerce. |
| `restapi/tests/test_port_areas.py` | **New.** Westport / Klang anchorage / Excl / oversized+Bintulu / synthetic union. |
| `backend/tests/test_loiter_port_areas.py` | **New.** Backend `label_geography` Westport vs Klang anchorage. |
| `reports/cursor-report.md` | This report. |

## Port-area loader behaviour

| Metric (local geojson) | Value |
|------------------------|------:|
| Ports loaded | 780 |
| Terminals loaded | 552 |
| Port areas built | 923 |
| Ports skipped (oversized, no terminals) | 90 |

- Port ≤ 50 km² → one area = port polygon (`terminal_name=None`).
- Port > 50 km² → one area **per contained terminal** (≥95% area inside port); names keep port + terminal.
- Oversized with no terminals → skipped (e.g. **Bintulu, Sarawak** 228.6 km²).
- **Port Klang (Pelabuhan Klang)** is 69.6 km² → uses terminals (incl. Westport Container Terminal).

Startup logs (once): polygons loaded / areas built / ports skipped (+ first 20 skipped names).

## Response field additions (no renames/removals)

**GET `/mantis/illegal-anchoring`**
- `portAreaExcludedCount`
- `portAreaCount`

**GET `/mantis/loitering`**
- `portAreaExcludedCount`
- `portAreaCount`
- `byFlag.in_port_area`
- Per event: `inPortArea`, `portAreaName`, `portAreaTerminalName` (and `in_port_area` in `flags` when true)

Overview illegal summary also includes `portAreaExcludedCount` / `portAreaCount`.

## Tests

```
41 passed (restapi/tests + backend/tests)
```

Coverage:
- Westport berth excluded from illegal (via `drop_port_area_candidates`) and loitering (`outside_anchorage` cleared).
- Klang anchorage still counted (illegal) / labelled inside anchorage (loiter).
- Singapore Excl1 still excluded by existing carve-out logic.
- Oversized → terminal union (`port_test_geometry`); Bintulu skipped with no terminals.
- Synthetic injected index for containment.

## Before / after (live pnav, read-only via 10.10.20.200)

Snapshot UTC ~2026-10-21 02:42. Same stopped-vessel / open-loiter query as production modules.

### Illegal anchoring

| | Count |
|--|------:|
| Stopped candidates | 1825 |
| **Before** (restricted/watch, Excl applied) | **860** |
| **After** (port-area drop) | **860** |
| `portAreaExcludedCount` | **0** |
| Dropped examples | *(none)* |

**Note:** Of the current 860 illegal candidates, **zero** lie inside any built port/terminal polygon (even before anchorage-wins). Westport berths are outside the Port Klang watch polygon and outside the restricted limit, so they were already absent from the illegal list; the new filter is wired and tested, but is a no-op on today’s candidate set. It will bite when a watch/restricted hit lands inside a terminal footprint.

### Loitering (default list)

| | Count |
|--|------:|
| **Before** (`outside_anchorage OR near_restricted OR before_sts`) | **8** |
| **After** (port centres lose outside-anchorage) | **6** |
| `portAreaExcludedCount` | **2** |

Dropped examples (MMSI + port):

| MMSI | Port | Terminal |
|-----:|------|----------|
| 565173000 | Singapore | Brani Terminal |
| 563196200 | Singapore | Pasir Panjang Terminal2 |

## Skipped / uncertain

1. **Bintulu** and 89 other oversized ports have **no terminals** in `terminals.geojson` under the 95% containment rule → skipped (logged). No berth file used this round.
2. **Illegal live delta = 0** today — see note above. Unit tests still prove Westport drop + Klang keep + Excl keep.
3. Backend does **not** add a DB column; port membership for stored loiters is recomputed at API time from `centre_*`. New processor cycles also clear `outside_anchorage` in `label_geography`.
4. For oversized ports, areas are stored **per terminal** (equivalent containment to a unary union; preserves terminal name for examples).
5. DuckDB + pandas 2.x `str` dtype required a small coerce in `duckdb_spatial` / loiter backend registers (behaviour-preserving).

## Commit

Created on branch `mantis-port-exclusions` (see git log). **Not deployed.**
