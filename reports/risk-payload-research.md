# Research: embed identity risk ratings in MANTIS API payloads

**Date:** 2026-10-22  
**Repo:** PySTS  
**Mode:** READ-ONLY (no code changes, no DDL, no pnav writes, no deploy, no JWT minting)  
Prior deploy report saved as `reports/sanctions-merge-deploy.md`.

---

## 0. Goal and hard rule

Add ship identity risk to existing MANTIS JSON the same way OFAC/UN/UK sanctions are already attached, so the frontend does not need a per-ship `/mantis/ship-risk` call.

**Hard rule:** only **add** fields. Never rename, remove, retype, or reorder existing fields; do not change filters, sorts, or counts that today’s UI depends on.

**Source of truth for the new fields:** stored `ais_ship_risk_rating` with `lookback_months = 24` only (not live `score_ship` recompute). Lookup key: `imo:<IMO>` when IMO present, else `mmsi:<MMSI>`. **No row → `GREEN` / `0` / reason `"none"`** (matches scoring default).

Proposed per-ship fields (camelCase, appended after existing sanctions fields where those exist):

| Field | Type | Notes |
|-------|------|--------|
| `riskRating` | `"RED"` \| `"AMBER"` \| `"GREEN"` | never omit; default GREEN |
| `riskScore` | `int` | 0–100; default 0 |
| `riskReason` | `string` | from `factors.reason`; default `"none"` |

Optional response-level summaries (alongside existing `sanctionsMatchCount*`):

| Field | Meaning |
|-------|---------|
| `riskRedCount` | ships/rows with `riskRating === "RED"` |
| `riskAmberCount` | optional; ships with AMBER |

STS pairs: put the three fields on `vesselA` / `vesselB`; keep pair-level `sanctionsMatch` untouched; optionally add pair-level `riskRedCount` (0–2) **at the end** of the pair object.

---

## 1. How sanctions are attached today

Central helpers in `restapi/sanctions.py`:

| Function | Role |
|----------|------|
| `load_ofac_indexes(engine)` | Cached IMO/MMSI → list hit dicts (`mantis_ofac_cache_ttl_s`, default **300 s**) |
| `match_vessel(imo, mmsi, …)` | IMO first; MMSI only if no IMO |
| `attach_sanctions(df, engine)` | Adds `sanctions_match`, `match_confidence`, `sanctions_list`, `list_names` columns |
| `attach_sanctions_pair_sides(pairs, engine)` | Same for `*_a` / `*_b` plus pair OR |
| `payload_fields(row, side=None)` | Emits `imo`, `sanctionsMatch`, `matchConfidence`, `sanctionsList`, `listNames` |
| `sort_listed_first(df, …)` | Stable sort: confirmed → possible → none |

Pattern in detectors: build DataFrame → `attach_sanctions*` → `*_to_payload` spreads `**payload_fields(...)` → top-level `sanctionsMatchCount*`.

---

## 2. Endpoint inventory

### 2.1 Summary table

| Endpoint | Module / attach point | IMO / MMSI available | Existing sanctions fields | Where to add risk | Typical rows |
|----------|----------------------|----------------------|---------------------------|-------------------|-------------:|
| `GET /mantis/sts-activities` | `sts_detection.py`: `attach_sanctions_pair_sides` + `attach_sanctions`; `pairs_to_payload` / `vessels_to_payload` | Pair: `imo_a`/`mmsi_a`, `imo_b`/`mmsi_b`; vessel: `imo`/`mmsi` | On vessel + pair: `sanctionsMatch`, `matchConfidence`, `sanctionsList`, `listNames`; top: `sanctionsMatchPairCount`, `sanctionsMatchVesselCount` | Append risk on `vesselA`/`vesselB` and each `pairedVessels[]` item; top: `riskRedCount` (vessels) + optional `riskRedPairCount` | Tens of pairs / paired vessels (live open clusters) |
| `GET /mantis/sts-review` | `sts_review.py`: **DB column** `sanctions_match` on case (not live OFAC attach) | Case `members[]` JSON (mmsi; imo when stored) | Case: `sanctionsMatch`; top: `sanctionsMatchCount` | Append risk on each **member** (and/or case-level rollup `riskRedCount` at end); do **not** change `is_notifiable_case` | Page default **100** (max 1000) |
| `GET /mantis/sts-review/unreviewed` | Same `cases_to_payload` | Same | Case `sanctionsMatch` only (no top sanc count today) | Same member/case additive fields | Page **100** |
| `GET /mantis/sts-review/notifications` | Same + `sanctionsMatchCount` | Same | Case + top count | Same | Cap ~notify limit (tens) |
| `GET /mantis/loitering` | `loitering.py`: `attach_sanctions` → `events_to_payload` + `payload_fields` | `imo`, `mmsi` on event | Per event via `payload_fields`; top `sanctionsMatchCount` | Append risk on each `events[]`; top `riskRedCount` | Typically tens–low hundreds |
| `GET /mantis/illegal-anchoring` | `illegal_anchoring.py`: `attach_sanctions` → `vessels_to_payload` | `imo`, `mmsi` | Per vessel + `sanctionsMatchCount` | Append on `vessels[]`; top `riskRedCount` | Often hundreds (stopped candidates filtered) |
| `GET /mantis/darkvessels` | `dark_vessels.py`: `attach_sanctions` → `vessels_to_payload` | `imo`, `mmsi` | Per vessel + `sanctionsMatchCount` | Append on `vessels[]`; top `riskRedCount` | Tens–hundreds |
| `GET /mantis/identity-conflict` | `identity_conflict.py`: `attach_sanctions` on candidates; `identity_to_payload` / group rollup | Per identity `imo`, `mmsi` | Identity + group `sanctionsMatch*`; top group/identity counts | Append on each identity (and nested in `groups[].identities`); top `riskRedIdentityCount` | Small (groups of dual identities) |
| `GET /mantis/spoofing` | `spoofing.py`: `attach_sanctions` → `anomalies_to_payload` | `imo` from static join, `mmsi` | Per anomaly + `sanctionsMatchCount` | Append on `anomalies[]`; top `riskRedCount` | Depends on 3-day window; often tens–hundreds |
| `GET /mantis/spoofing/vessel-analysis` | `spoofing_vessel_analysis.py`: `attach_sanctions` on static → `payload_fields` into `vessel` header | Query `mmsi`; `imo` from static | On `vessel`: sanctions fields | Append risk on `vessel` object only (not every track point) | 1 vessel + many events/track points |
| `GET /mantis/overview` | `mantis_overview.py`: summaries from detectors (`summary_only` STS etc.) | N/A (counts only) | Module blocks expose `sanctionsMatchCount*` | Add `riskRedCount` / `riskAmberCount` next to those counts in each summary block | 1 payload |
| `GET /mantis/sanctions` | `sanctions.query_ofac_vessels` → `ofac_vessels_to_payload` | Each vessel `imo`/`mmsi` | List membership fields (`listName`, …) — not `sanctionsMatch` | Append risk on each `vessels[]` item; optional top `riskRedCount` | **1,940** full list (Swagger samples 20) |
| `GET /mantis/vessel-timeline` | `timelineplayback.py` — **no sanctions today** | Query `mmsi` only | — | Add top-level `riskRating`/`riskScore`/`riskReason` for that MMSI (load IMO from static if cheap) | Event list for one ship |
| `GET /mantis/vessel-track` | ClickHouse stream — **no sanctions** | Query `mmsi` | — | Prefer a small JSON header once; **do not** stamp every NDJSON point (size). Swagger sample: top-level fields only | Stream of points |
| `GET /mantis/ship-risk` | Already the rating API | imo/mmsi | Has full rating + events | **No change required** for this project (frontend stop-gap today) | 1 |

`POST /mantis/sts-review/<id>/verify` — not a per-ship list; skip.

---

## 3. Example JSON (additive only)

### STS pair / vessel (existing keys unchanged; new keys appended)

```json
{
  "observationId": 12345,
  "suspicionScore": 5.2,
  "distanceM": 28.4,
  "vesselA": {
    "mmsi": 352003715,
    "shipName": "SEA PEARL",
    "latitude": 1.23,
    "longitude": 103.45,
    "imo": "9245249",
    "sanctionsMatch": false,
    "matchConfidence": "none",
    "sanctionsList": null,
    "listNames": null,
    "riskRating": "RED",
    "riskScore": 100,
    "riskReason": "high_mmsi_misuse"
  },
  "vesselB": {
    "mmsi": 533133348,
    "shipName": "EXAMPLE",
    "imo": "9058713",
    "sanctionsMatch": true,
    "matchConfidence": "confirmed",
    "sanctionsList": "SDN",
    "listNames": ["SDN"],
    "riskRating": "RED",
    "riskScore": 100,
    "riskReason": "sanctions match (SDN)"
  },
  "sanctionsMatch": true,
  "matchConfidence": "confirmed",
  "riskRedCount": 2
}
```

### Top-level counts (e.g. dark / illegal / loitering / spoofing)

```json
{
  "candidateCount": 120,
  "sanctionsMatchCount": 8,
  "riskRedCount": 15,
  "riskAmberCount": 40,
  "vessels": [ "…unchanged shape + risk* on each…" ]
}
```

### STS review case (members)

```json
{
  "caseId": 99,
  "sanctionsMatch": true,
  "members": [
    {
      "mmsi": 613490000,
      "shipName": "SEA OPERA",
      "imo": "9000883",
      "riskRating": "RED",
      "riskScore": 100,
      "riskReason": "sanctions match (SDN)"
    }
  ],
  "riskRedCount": 1
}
```

---

## 4. Cheap load strategy

Mirror the OFAC index cache (`load_ofac_indexes`, TTL 300 s).

**Recommended:** one in-process cache of lookback-24 ratings:

```text
by_imo:  dict[str, {rating, score, reason}]
by_mmsi: dict[str, {rating, score, reason}]   # only for ship_key mmsi:* rows
```

- Refresh: full `SELECT ship_key, imo, mmsi, rating, score, factors->>'reason' FROM ais_ship_risk_rating WHERE lookback_months = 24` on TTL miss.
- Lookup: same rule as sanctions / job — IMO key first; else MMSI key. Miss → GREEN / 0 / `"none"`.
- Env: e.g. `mantis_risk_cache_ttl_s` default **300** (align with OFAC) or **120** (align with overview cache).
- **Do not** scan `ais_static` / positions for this feature.
- Per-request alternative: `WHERE ship_key = ANY(:keys)` for the IMOs/MMSIs in the payload — fine for small pages; worse for `/mantis/sanctions` (1,940) than one cached full load.

### Measured read cost (srv-dp → pnav, `statement_timeout` 15 s, 2026-10-22)

| Query | Result |
|-------|--------|
| Table size | heap **2.3 MB**, total **~3.1 MB**; ~**2,610** rows |
| Indexes | PK, unique `(ship_key, lookback_months)`, `imo`, `mmsi`, `rating` |
| Full lookback-24 load | **2,610** rows in **~76 ms** client time; plan Seq Scan **~1.1 ms** exec, buffers hit |
| Batch 50 IMOs | **~8 ms** |
| Batch 75 `ship_key`s | **~8 ms** |

Full table is tiny → **cache-the-whole-table** is the right default.

---

## 5. Frontend / schema risk

- Operations UI is a **separate repo** (not in this workspace). `restapi/README.md`: “Frontend — separate repo / separate developer.”
- Local OpenAPI (`restapi/static/swagger.json`) documents object schemas; **no `additionalProperties: false`** found — Swagger/OpenAPI clients that ignore unknown properties are fine.
- Typical JS/React consumers that only read known keys are fine when new keys are appended.
- Risk if the other repo uses **exact key-set equality**, Zod `.strict()`, or similar — **not visible here**. Recommend: coordinate with FE, keep additive append-only, and smoke-test dashboard pages after Phase 1.

---

## 6. Recommended rollout order

| Phase | Endpoints | Why first |
|-------|-----------|-----------|
| **1** | `sts-activities`, `darkvessels`, `illegal-anchoring`, `loitering`, `spoofing` | Same `attach_sanctions` + `payload_fields` pattern; highest map/list UI value |
| **2** | `identity-conflict`, `spoofing/vessel-analysis` | Same helpers; smaller payloads |
| **3** | `sts-review` (+ unreviewed / notifications) | Case members need a small attach loop; **must not** alter `is_notifiable_case` / queue filters |
| **4** | `overview`, `sanctions` | Overview = summary counts only; sanctions list = 1,940 rows (cache helps) |
| **5** | `vessel-timeline`, `vessel-track` | No sanctions today; timeline top-level only; track = header only, never per-point |

Keep `/mantis/ship-risk` as the drill-down for events / factors detail.

---

## 7. Draft design (do not apply)

### 7.1 New helper module (suggested) `restapi/ship_risk_attach.py`

```python
# DRAFT — not in tree
DEFAULT_RISK = {"rating": "GREEN", "score": 0, "reason": "none"}
RISK_CACHE_TTL_S = int(os.environ.get("mantis_risk_cache_ttl_s", "300"))

def load_risk_indexes(engine) -> tuple[dict, dict]:
    """Cached by_imo / by_mmsi from ais_ship_risk_rating lookback=24."""
    ...

def match_risk(imo, mmsi, by_imo, by_mmsi) -> dict:
    """IMO first; else MMSI; else DEFAULT_RISK."""
    ...

def attach_risk_ratings(df, engine, *, imo_col="imo", mmsi_col="mmsi") -> pd.DataFrame:
    """Add risk_rating, risk_score, risk_reason columns (parallel to attach_sanctions)."""
    ...

def attach_risk_pair_sides(pairs, engine) -> pd.DataFrame:
    ...

def risk_payload_fields(row, *, side=None) -> dict:
    """Return {riskRating, riskScore, riskReason} only — caller spreads after payload_fields."""
    ...
```

### 7.2 Wire-in pattern (each detector)

```python
# after attach_sanctions(...)
df = attach_risk_ratings(df, engine)
# in *_to_payload:
{ **payload_fields(row), **risk_payload_fields(row) }
# top-level:
"sanctionsMatchCount": ...,
"riskRedCount": int((df["risk_rating"] == "RED").sum()),
```

STS: call `attach_risk_pair_sides` next to `attach_sanctions_pair_sides`; spread `risk_payload_fields(p, side="a"|"b")` inside `vesselA`/`vesselB` **after** `payload_fields`.

Overview: when building each module summary, either (a) run detectors with a flag that returns risk counts, or (b) compute counts from the same cached indexes over the vessel MMSIs/IMOs already in the summary path — without changing existing numeric fields.

Sanctions list: after `ofac_vessels_to_payload`, enrich each dict with `risk_*` from cache (IMO/MMSI already on the vessel).

STS review: when building `members`, for each member call `match_risk`; append fields; optionally set case `riskRedCount` = count of RED members. **Do not** feed risk into `is_notifiable_case`.

### 7.3 Swagger

Extend schemas with the three optional properties and new count integers; keep all existing properties and required arrays unchanged.

### 7.4 Draft tests (prove existing fields unchanged)

```python
# DRAFT tests — not added yet

def test_risk_payload_fields_appended_not_replacing_sanctions():
    row = {... sanctions columns ...}
    base = payload_fields(row)
    risk = risk_payload_fields(row_with_risk)
    merged = {**base, **risk}
    assert list(merged)[:5] == list(base)  # sanctions keys still first / unchanged values
    assert merged["sanctionsMatch"] is base["sanctionsMatch"]
    assert "riskRating" in merged

def test_attach_risk_defaults_green_when_missing():
    df = attach_risk_ratings(pd.DataFrame([{"imo": None, "mmsi": 999999999}]), engine)
    assert df.iloc[0]["risk_rating"] == "GREEN"
    assert int(df.iloc[0]["risk_score"]) == 0

def test_sts_activities_payload_keys_stable(monkeypatch):
    # Snapshot key order / set of keys on vesselA before risk; after attach,
    # every old key present with same value; new keys only at end.
    ...

def test_overview_sanctions_counts_unchanged(monkeypatch):
    # With risk attach enabled, sanctionsMatchCount* equal to baseline fixture.
    ...

def test_notifiable_case_ignores_risk():
    # Case with risk RED but no sanctions/dark/illegal/outside → still not notifiable
    # unless existing flags say so.
    ...
```

Also: golden JSON fixtures for one STS pair and one dark vessel — diff must be empty if risk keys are stripped.

---

## 8. Payload size impact

| Surface | Extra bytes (order of magnitude) |
|---------|----------------------------------|
| Per ship | ~60–100 B (`riskRating` + `riskScore` + `riskReason`) |
| 100-row list | ~6–10 KB |
| Full `/mantis/sanctions` (1,940) | ~120–200 KB |
| Vessel-track NDJSON | **0** if only header enriched; large if wrongly stamped per point — avoid |

Negligible vs existing geometry / track payloads.

---

## 9. Open product choices (for FE sync)

1. Confirm default for missing row: **GREEN/0/`none`** (recommended) vs `null` “unknown”.
2. Whether pair/case objects need `riskRedCount` or only per-ship fields.
3. Whether `/mantis/sanctions` should show risk (almost all RED today) — useful for FE list colouring, or skip Phase 4 if noisy.

---

## 10. Decision / next step

Research complete. **No code applied.**  

When approved: implement `ship_risk_attach.py` + Phase 1 detectors first, with tests that freeze existing sanctions field values/key sets; then Phase 2–5. Deploy API only on srv-01 (`identity_risk` / new helper + detector modules); no DDL; job cron unchanged.
