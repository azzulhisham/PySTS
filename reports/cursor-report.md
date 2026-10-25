# Hold: `ais_sts_vesselzone_b` on srv-01 (25 Oct 2026)

**Date (UTC):** 2026-10-25  
**Host:** srv-01 `10.10.20.201` only  
**Approved:** stop + disable **only** `ais_sts_vesselzone_b.service`  
**Prior report archived as:** [`loiter-motion-deploy.md`](loiter-motion-deploy.md)

---

## 1. Before

| Unit | MainPID | ActiveState | UnitFileState |
|------|---------|-------------|---------------|
| `ais_sts_vesselzone_b` | **841939** | active | enabled |
| `ais_sts_vesselzone` (Class A) | **756631** | active | disabled† |

† Pre-existing: Class A was already `UnitFileState=disabled` while running; left unchanged.

### Other running `ais_*` / `pyvam-*` / mantis PIDs (before)

| Unit | MainPID |
|------|---------|
| `ais_aton_datapreproc` | 772689 |
| `ais_extract_lloyds` | 442266 |
| `ais_jkptg_ddms_uat` | 1004174 |
| `ais_mantis_api` | 1490384 |
| `ais_ptp_vesselactivities` | 861260 |
| `ais_sts_vesselzone` | 756631 |
| `ais_sts_vesselzone_b` | 841939 |
| `ais_tss_aisposition` | 1296170 |
| `ais_tss_aisposition_b` | 1523778 |
| `ais_tss_aisstatic` | 839051 |
| `ais_tss_aisstatic_b` | 841710 |
| `ais_tss_analyzer` | 840639 |
| `ais_tss_reporting` | 764170 |
| `pyvam-api` | 1508924 |
| `pyvam-dash` | 1508934 |
| `pyvam-processor` | 1555053 |
| `pyvam-soms-dash` | 1518275 |

---

## 2. Actions

```bash
sudo systemctl stop ais_sts_vesselzone_b.service
sudo systemctl disable ais_sts_vesselzone_b.service
```

- Unit file **not** edited/deleted; code **not** touched.
- No other services stopped or restarted in this step.

---

## 3. After

| Unit | MainPID | ActiveState | UnitFileState |
|------|---------|-------------|---------------|
| `ais_sts_vesselzone_b` | **0** | **inactive** | **disabled** |
| `ais_sts_vesselzone` | **756631** (unchanged) | active | disabled† |

| Check | Result |
|-------|--------|
| PID diff vs before | Only `_b` removed from running set; **no other PID changed** |
| Class A journal | Still logging `[UPDATE]` cycles (e.g. 13:32:42 UTC) |
| `GET /mantis/vessel-timeline` | **HTTP 200** (sample MMSI `533000089`, 3 events) |
| Unit/code files | Untouched |

---

## 4. Open Class B rows (pnav read-only)

Query (uses partial index `idx_vesselinrestrictzone_open` on open rows; join to `ais_positionb` via `mmsi` — no writes, rows not closed):

```sql
SELECT count(*) AS open_class_b
FROM public.ais_vesselinrestrictzone rz
WHERE rz."tsOut" IS NULL
  AND EXISTS (
    SELECT 1 FROM public.ais_positionb b WHERE b.mmsi = rz.mmsi
  );
```

| Metric | Value |
|--------|-------|
| Open Class B (`tsOut` IS NULL ∩ `ais_positionb`) | **46** |
| Open total (all classes) | 509 |

`EXPLAIN`: Index Only Scan on `idx_vesselinrestrictzone_open` (~562 open rows) + hash join to `ais_positionb` (~21k rows). No write.

---

## 5. Documentation (PySTS)

| File | Change |
|------|--------|
| `backend/mantis-detection.md` | New section **ON HOLD: ais_sts_vesselzone_b** + changelog 2026-10-25 |
| Commit | doc-only (this hold) |

---

## 6. srvhealth

Inactive `_b` would otherwise be **FAIL** under unit rules. Updated ignore + note:

| Item | Result |
|------|--------|
| Repo `srvhealth/srvhealth.json` | `ignore` += `ais_sts_vesselzone_b.service`; `not_covered` note for ON HOLD |
| Live `/etc/srvhealth.json` | replaced (backup `/etc/srvhealth.json.bak-20261025-vesselzone-b-hold`) |
| Restart | **only** `srvhealth-reporter` (PID 1568847 → **1661570**) |
| Other PIDs after reporter restart | vesselzone **756631**, mantis_api **1490384**, pyvam-api **1508924** unchanged |

---

## 7. Rollback (when revisiting)

```bash
# After class-scoped cleanup + RDS deadlock confirmation:
sudo systemctl enable --now ais_sts_vesselzone_b.service
# Then remove ais_sts_vesselzone_b.service from srvhealth ignore (and the not_covered note),
# copy srvhealth.json to /etc, restart srvhealth-reporter only.
```

Do **not** mass-close the 46 open Class B rows unless product asks.

---

## 8. Decision

| Item | Status |
|------|--------|
| Stop + disable `_b` only | Done |
| Other PIDs unchanged | Done |
| Class A still cycling | Done |
| Timeline HTTP 200 | Done |
| Open Class B count reported (46) | Done (not closed) |
| `mantis-detection.md` ON HOLD | Done |
| srvhealth ignore + reporter restart | Done |

**GO** — Class B restricted-zone detector on hold; Class A and MANTIS API unaffected.
