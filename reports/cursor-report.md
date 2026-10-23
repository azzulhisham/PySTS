# Build: identity risk in Phase 3 STS review-case payloads

**Date (local):** 2026-10-23  
**Mode:** LOCAL BUILD + TEST only — **no deploy, no push, no DDL, no pnav writes, no JWT minting.**  
**Prior report** (Phase 2 deploy) archived as [`reports/risk-payload-phase2-deploy.md`](risk-payload-phase2-deploy.md) in commit `f8e56ee` (reports-only).

Phase 1 (`c7a5bd5`) and Phase 2 (`298a5d2`) remain live. This build extends the same `ship_risk_attach.py` 5-minute cache to **STS review-case GET payloads** only (read-time attach). Stored cases are not updated.

---

## 1. Routes covered (confirmed)

| Path | Method | Handler | Module | Ship objects |
|------|--------|---------|--------|--------------|
| `/mantis/sts-review` | GET | `get_sts_review` | `restapi/main.py` → `sts_review.list_unexplained` → `cases_to_payload` | `cases[].members[]` (usually 2 vessels; can be more) |
| `/mantis/sts-review/unreviewed` | GET | `get_sts_review_unreviewed` | `restapi/main.py` → `sts_review.list_unreviewed` → `cases_to_payload` | Same `members[]` |
| `/mantis/sts-review/notifications` | GET | `get_sts_review_notifications` | `restapi/main.py` → `sts_review.list_notifications` → `cases_to_payload` | Same `members[]` |
| `/mantis/sts-review/{caseId}/verify` | POST | `post_sts_review_verify` | `restapi/main.py` → `sts_review.verify_case` | **Write route — unchanged** (no risk params, no risk columns in `VERIFY_SQL`, response has no risk fields) |

Member shape (pre-existing, unchanged): `mmsi`, `imo`, `shipName`, `shipType` / `shipTypeDesc`, `sanctionsMatch`, `matchConfidence`, `sanctionsList`, dredge/bunker flags, etc. Risk fields are appended after sanctions-related member fields.

**Not in scope:** `/mantis/sts-activities` (Phase 1 already has risk). No PyVAM. No srv-dp.

---

## 2. New fields (additive only)

| Location | Fields |
|----------|--------|
| Each `members[]` entry | `riskRating`, `riskScore`, `riskReason` (current rating from `ais_ship_risk_rating` lookback 24) |
| Each case | `riskRedCount` (how many members on that case are currently RED) — placed before `members` |
| List / notifications top-level | `riskRedCount`, `riskAmberCount` over **members on the returned page / batch** (same pattern as Phase 1 STS pairs) |

**Defaults / failure:** missing row or cache/attach failure → `GREEN` / `0` / `"none"`; request must not fail because of risk.

**Lookup:** unchanged — `imo:<IMO>` first, else `mmsi:<MMSI>`; IMO present but absent from table → GREEN (no MMSI fallback). One `load_risk_indexes` call per page via process cache (TTL 300 s).

**Stored vs current (important for reviewers):**

| Field | Meaning |
|-------|---------|
| Member `sanctionsMatch` / `matchConfidence` / `sanctionsList` | **Captured when the case was written** by the STS review pipeline (snapshot in JSON `members`). |
| Case-level `sanctionsMatch` | **Stored** boolean on `ais_stsreviewcase` at write time. |
| Member `riskRating` / `riskScore` / `riskReason` | **Current** identity risk, attached at **read time only**. Not written into the case row or member JSON. |
| Case `riskRedCount` | Count of members with **current** `riskRating == RED` on this response. |

A reviewer tells them apart by name: anything named `sanctions*` on the case/member is historical; anything named `risk*` is live. They can diverge (e.g. sanctions cleared later, or a new RED reason appears after case creation).

---

## 3. Sample before / after

### Member (caseId 35, MMSI `562111000` — live AMBER)

**Before (strip identity-risk keys):**

```json
{
  "mmsi": 562111000,
  "imo": "961234.0",
  "shipName": "TEST",
  "sanctionsMatch": false,
  "matchConfidence": "none",
  "sanctionsList": null
}
```

**After — keys added only:**

```json
{
  "riskRating": "AMBER",
  "riskScore": 60,
  "riskReason": "reflag_dual_mmsi"
}
```

### Case-level addition

```json
{
  "riskRedCount": 0
}
```

(placed before `members`; existing `status`, `suspicionScore`, `reviewClass`, `sanctionsMatch`, etc. unchanged)

### List-level addition (queue page example)

```json
{
  "sanctionsMatchCount": <unchanged>,
  "riskRedCount": 0,
  "riskAmberCount": 32
}
```

---

## 4. Tests

| Suite | Result |
|-------|--------|
| `restapi/tests/test_ship_risk_phase3.py` | **6 passed** (member fields after sanctions, GREEN on cache failure, golden strip only-adds-keys, list counts, verify signature / SQL unchanged, count key order) |
| Full `restapi/tests` + `backend/tests` | **124 passed** |

---

## 5. Local size & timing (read-only pnav; statement_timeout 15s)

Risk cache: **2,599** IMO + **20** MMSI keys; load ~**1.06 s** (cold).

| Endpoint | Scope | riskRed / riskAmber | Size before → after | Δ bytes | Timing |
|----------|-------|--------------------:|---------------------|--------:|--------|
| `GET` sts-review (via `list_unexplained`) | page 1, pageSize 100; **276** members; total cases 17,587 | **0** / **32** | 169,125 → 188,277 | **+19,152** | first **1.42 s**; warm **0.30 s** |
| `GET` sts-review/unreviewed | page 1, pageSize 100; **222** members; total 329,433 | **0** / **13** | 152,771 → 168,382 | **+15,611** | first **0.52 s**; warm **0.32 s** |
| `GET` sts-review/notifications | since=now−24h, area=all, includeInPort; **112** cases / **256** members | **0** / **11** | 176,386 → 194,273 | **+17,887** | first **1.62 s**; warm **0.20 s** |

Golden: strip identity-risk keys → same pre-existing key set/order/values (unit tests + live strip for size “before”).

Default 5-minute notifications window was empty at measure time (Δ 0); 24h window used for meaningful size/timing.

---

## 6. Files changed (this build)

| Path | Change |
|------|--------|
| `restapi/sts_review.py` | `_enrich_members_with_risk`, `risk_counts_from_cases`; `cases_to_payload` loads cache once and attaches; list helpers return risk counts |
| `restapi/main.py` | expose `riskRedCount` / `riskAmberCount` on the three GET responses |
| `restapi/static/swagger.json` | `StsReviewMember` risk fields; case `riskRedCount`; list/notifications counts |
| `restapi/tests/test_ship_risk_phase3.py` | **new** |
| `reports/cursor-report.md` | this report |

**Unchanged:** `verify_case` / `VERIFY_SQL` / POST handler; `ship_risk_attach.py` (reuse only); no DDL; no stored-case writes.

---

## 7. Deploy steps (later — **not** done now)

srv-01 only (`/home/srvadmin/py/mantis`, flat layout). **Do not** touch srv-dp, identity_risk job, cron, polygons, pyvam-\*.

1. Push the Phase 3 commit when approved.
2. Backup into `backup-<UTC stamp>/` with `PRE_DEPLOY.txt`:  
   `sts_review.py`, `main.py`, `static/swagger.json`  
   (`ship_risk_attach.py` already live from Phase 1 — include in backup only if you re-copy it; not required for this change set).
3. Diff each existing server file against the last deployed git baseline; **STOP** if dirty.
4. Copy the three files above (flat names; swagger under `static/`).
5. Import check with service Python (`/usr/bin/python3` / gunicorn shebang) from `/home/srvadmin/py/mantis`:  
   `import sts_review`, `import main` (or gunicorn worker import path).
6. `sudo systemctl restart ais_mantis_api` **only**; confirm `ais_mantis_api` and `pyvam-api` active; `GET http://127.0.0.1:8085/` → **401**.
7. **Verification without minting a token** — call handlers directly on the server with service env (same pattern as Phase 2):

```bash
# On srv-01 as srvadmin; env from mantis.env — do not echo secrets
set -a; source /home/srvadmin/py/mantis/mantis.env; set +a
cd /home/srvadmin/py/mantis
/usr/bin/python3 - <<'PY'
from sts_review import list_unexplained, list_unreviewed, list_notifications
from datetime import datetime, timedelta, timezone
q = list_unexplained(page=1, page_size=5)
assert "riskRedCount" in q  # snake in helper; HTTP JSON uses riskRedCount via main
# Prefer asserting on cases payload:
c = q["cases"][0]
m = c["members"][0]
assert set(m) >= {"riskRating", "riskScore", "riskReason"}
assert "riskRedCount" in c
print("OK queue", c["caseId"], m.get("riskRating"), q["risk_red_count"], q["risk_amber_count"])
u = list_unreviewed(page=1, page_size=5)
print("OK unreviewed", u["risk_red_count"], u["risk_amber_count"])
n = list_notifications(since=datetime.now(timezone.utc)-timedelta(hours=24), area="all", include_in_port=True)
print("OK notify", n["notification_count"], n["risk_red_count"], n["risk_amber_count"])
# Confirm POST path unchanged: inspect signature only — do not write
import inspect
from sts_review import verify_case
assert list(inspect.signature(verify_case).parameters) == [
    "case_id", "review_class", "labeled_by", "review_notes"
]
print("OK verify signature unchanged")
PY
```

HTTP JWT smoke optional (existing client token only); never mint a JWT or read a signing secret.

---

## 8. Rollback

```bash
# SSH srvadmin@10.10.20.201
BK=/home/srvadmin/py/mantis/backup-<stamp>
cp -a $BK/code/sts_review.py /home/srvadmin/py/mantis/sts_review.py
cp -a $BK/code/main.py /home/srvadmin/py/mantis/main.py
cp -a $BK/static/swagger.json /home/srvadmin/py/mantis/static/swagger.json
sudo systemctl restart ais_mantis_api
# do not touch pyvam-* / srv-dp / cron / polygons / ship_risk_attach.py unless you replaced it
```

---

## 9. Decision

| Item | Status |
|------|--------|
| Routes listed (incl. write route unchanged) | Done (§1) |
| Additive risk fields at read time | Done |
| Stored-case vs current rating documented | Done (§2) |
| Tests + full suite | **124 passed** |
| Size / timing | Done (§5) |
| Push / deploy | **Not done** (by request) |

**Ready for review** — deploy when approved.
