# Research: Tokyo MOU Under-performing Ships → AMBER ship-risk factor

**Date:** 2026-10-22  
**Scope:** READ-ONLY research. No DDL applied, no pnav writes, no commits, no deploy, no cron.  
**Prior report saved as:** `reports/identity-deploy.md`  
**List label:** As of **30 September 2026**

---

## 1. How the list is published

| Item | Finding |
| --- | --- |
| Landing page | https://www.tokyo-mou.org/inspections-detentions/under-performing-ships/ |
| Format | **PDF** (WordPress file embed + download button). Not HTML table / Excel / JS API. |
| Direct URL (current) | https://www.tokyo-mou.org/wp/wp-content/uploads/Under-performing-ships-for-Septermber-2026.pdf |
| Filename note | Official upload uses typo **“Septermber”** (not “September”). |
| Provenance | PDF metadata: `Title: Under-performing ships for Septermber 2026-f.xls` — distilled from Excel; Creator Distiller 10; Created **2026-10-07**. |
| Page “as of” | **As of 30 September 2026** |
| Page modified | HTML `dateModified` **2026-10-07** (matches PDF date) |
| Update cadence | **Monthly** (historical PDFs named by month, e.g. August 2026, December 2025). Committee rule: ships detained **≥3 times** by Tokyo MOU in the **last 12 months**. |
| Size | 114 908 bytes, 2 pages (A3 landscape) |

### Fields in the PDF

| Field | Present |
| --- | --- |
| No. (list sequence) | yes |
| IMO No. | yes |
| Call sign | yes (or N/A) |
| Dets (detention count in window) | yes (3–5 on this edition) |
| Ship name (at day of detention) | yes |
| InspDate | yes (per detention row) |
| Place | yes |
| Flag | yes |
| Company | yes |
| Company IMO no. | yes |
| ISM DOC issuing RO | yes |
| RO issuing other statutory certificates | yes |
| Ship type | **no** (not on this list) |

Row model: one **ship header** row (No. + IMO + Dets + name) plus **continuation rows** for each detention (same IMO, blank No./IMO/Dets).

### Terms of use / APCIS

- Landing page purpose statement only; no separate licence block on that page.
- **APCIS** public portal (https://apcis.tmou.org/public/) and Tokyo MOU PSC Database page disclaimer:
  - Not liable for accuracy/completeness/timeliness.
  - **“The data obtained from this site should not be used for any commercial purposes, reproduced in any other sites or any publications without prior permission by the Tokyo MOU.”**
- Practical implication for MANTIS: treat as **restricted redistribution**; ingest for internal risk scoring likely needs **Secretariat permission** before production use/publication of derived lists.

### APCIS / machine-readable detention feed?

| Source | Machine-readable under-performing list? |
| --- | --- |
| Monthly PDF on tokyo-mou.org | **Yes (PDF only)** — best current ingest target |
| APCIS public UI | Interactive PSC search/display; **no** documented bulk CSV/API for the under-performing list |
| Tokyo MOU “PSC Database” page | Redirects to APCIS; same disclaimer; not a static under-performing extract |

### Similar lists elsewhere (list only — do not build)

| Regime | List | URL / notes |
| --- | --- | --- |
| **Paris MoU** | Current **bannings** / refusal of access | https://parismou.org/banning/ and Inspection Database “Current bannings”; also White/Grey/Black **flag** lists (not ship under-performing) |
| **Paris MoU** | Refusal-of-access policy | https://parismou.org/refusal-of-acces-banning/ (3 detentions in 24/36 months by flag colour, etc.) |
| **Indian Ocean MoU** | **Underperforming Ships List** (>2 detentions in 24 months) | https://www.iomou.org/ (homepage cites list dated **27 March 2026**) |
| **Indian Ocean MoU** | Ship Detention List (date-range PDF from IOCIS) | detention list generator on iomou.org |
| **Indian Ocean MoU** | Ships On-Watch / Alert | watchlist page on iomou.org |

---

## 2. Download from srv-dp

| Step | Result |
| --- | --- |
| Host | srv-dp `10.10.20.200` → `/tmp/tmou_underperforming_sep2026.pdf` |
| HTTP | **200** |
| Bytes | **114 908** |
| `curl time_total` | **1.036 s** |
| SHA256 (16) | `54d3fc326d3eacfc…` |
| Remote cleanup | **deleted** after copy to laptop `/tmp` for parse |

---

## 3. Local parse

| Metric | Value |
| --- | --- |
| Title | Under-performing Ship for September 2026 |
| Distinct ships (numbered rows) | **28** |
| Valid IMO (ISO check digit) | **28** |
| Invalid IMO | **0** |
| Detention count range | **3–5** |

---

## 4. Read-only pnav overlap (by IMO)

Indexed `= ANY(:imos)` lookups; `statement_timeout=15s`; `default_transaction_read_only=on`. No writes.

| Overlap | Count (of 28) |
| --- | ---: |
| In `ais_static` (seen on AIS) | **14** |
| In `ais_lloyds` | **17** |
| On merged sanctions views (SDN/CONS/UN/UK) | **2** (both **SDN** only) |
| Already in `ais_ship_risk_rating` (lookback 24) | **1** |

| Current rating among the 28 | n |
| --- | ---: |
| NONE (no rating row) | 27 |
| AMBER | 1 |
| RED / GREEN | 0 |

### Up to 15 examples

| IMO | List name | Dets | AIS | AIS name / MMSI | Lloyd’s | Flag (Lloyd’s) | Sanctions | Risk rating |
| ---: | --- | ---: | --- | --- | --- | --- | --- | --- |
| 9646118 | GOLD SPRING | 5 | Y | GOLD SPRING / 668116481 | Y | Sierra Leone | — | — |
| 1059876 | HARMONY 2 | 4 | Y | HARMONY 2 / 352003877 | Y | Panama | — | — |
| 8358257 | JIE TAI 8 | 4 | N | — | Y | Panama | — | — |
| 8655980 | JIN DONG 11 | 4 | Y | JIN DONG 11 / 312742000 | Y | Belize | — | — |
| 8810346 | KUSHIRO | 4 | Y | KUSHIRO / 457900980 | Y | Mongolia | — | — |
| 9000883 | SEA OPERA | 4 | Y | SEA OPERA / 613490000 | Y | Cameroon | SDN | — |
| 9200017 | SDR MARIAM PEARL | 4 | Y | SDR MARIAM PEARL / 352006192 | Y | Panama | — | — |
| 9412361 | ORION | 4 | N | — | Y | Panama (Lloyd’s name CITRINE) | — | — |
| 1079319 | YUN HAI 316 | 3 | Y | YUN HAI 316 / 352004451 | Y | Panama | — | — |
| 8357916 | HONG YUN YONG HENG | 3 | Y | HONG YUN YONG HENG / 668116200 | Y | Sao Tome & Principe | — | — |
| 8912558 | TULIP | 3 | Y | TULIP / 613467106 | Y | Cameroon | SDN | — |
| 9258325 | GOLDEN LEAF | 3 | Y | GOLDEN LEAF / 511102032 | Y | Palau | — | **AMBER 60** |
| 9310745 | AC KATHRYN | 3 | Y | AC KATHRYN / 356928000 | Y | Panama | — | — |
| 9314569 | SHENG SHI 569 | 3 | Y | KADHIM@@@@ / 620800373 | Y | Tanzania (Zanzibar) | — | — |
| 9402897 | ST AURORA | 3 | N | — | Y | Belize (Lloyd’s name QING RU) | — | — |

**Note:** SEA OPERA and TULIP are SDN but have **no** `ais_ship_risk_rating` row yet (ratings today are only materialized for ships that already have identity-risk events). A PSC factor would create/update IMO-keyed ratings for these.

---

## 5. Draft DDL (do **not** run)

```sql
-- PSC under-performing ships (Tokyo MOU first; source-extensible).
-- DRAFT ONLY — not applied.

CREATE TABLE IF NOT EXISTS public.psc_ingest_run (
    id              BIGSERIAL PRIMARY KEY,
    source          TEXT NOT NULL,          -- e.g. 'tokyo_mou_underperforming'
    list_date       DATE NOT NULL,          -- "as of" date from publication
    source_url      TEXT,
    source_sha256   TEXT,
    fetched_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    row_count       INTEGER,
    valid_imo_count INTEGER,
    status          TEXT NOT NULL DEFAULT 'ok',  -- ok | skipped | failed
    detail          JSONB,
    UNIQUE (source, list_date, source_sha256)
);

CREATE TABLE IF NOT EXISTS public.psc_underperforming (
    id              BIGSERIAL PRIMARY KEY,
    source          TEXT NOT NULL,          -- 'tokyo_mou_underperforming'
    list_date       DATE NOT NULL,
    imo             TEXT NOT NULL,          -- 7-digit, check-digit validated at ingest
    ship_name       TEXT,
    callsign        TEXT,
    flag            TEXT,
    company         TEXT,
    company_imo     TEXT,
    detention_count INTEGER,               -- "Dets" on Tokyo list
    detentions      JSONB,                 -- [{inspDate, place, flag, company, ...}, ...]
    evidence        JSONB,                 -- RO names, raw row refs, etc.
    ingest_run_id   BIGINT REFERENCES public.psc_ingest_run(id),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (source, list_date, imo)
);

CREATE INDEX IF NOT EXISTS idx_psc_underperforming_imo
    ON public.psc_underperforming (imo);
CREATE INDEX IF NOT EXISTS idx_psc_underperforming_list
    ON public.psc_underperforming (source, list_date DESC);
CREATE INDEX IF NOT EXISTS idx_psc_underperforming_imo_current
    ON public.psc_underperforming (imo, list_date DESC);

-- Optional rollback (not created as a file in this research step):
-- DROP TABLE IF EXISTS public.psc_underperforming;
-- DROP TABLE IF EXISTS public.psc_ingest_run;
```

### How `identity_risk_job` would use it (design only)

1. **Ingest job** (separate, monthly): download current PDF URL from the landing page (or configured URL), parse unique IMOs + detention_count + detention details, upsert `psc_ingest_run` + `psc_underperforming` for that `list_date`.
2. **In `identity_risk_job` / `score_ship`:**
   - Load current Tokyo rows: latest `list_date` per `source='tokyo_mou_underperforming'`.
   - For each rated ship with a valid IMO on that list, attach a factor / synthetic event, e.g.:
     - `event_type` or factor key: `psc_underperforming`
     - `confidence`: `medium` (or dedicated `psc`)
     - **Never drives RED alone**
     - Scoring: if rating would otherwise be GREEN (or only weaker signals), set **AMBER score 50** with `factors.reason = "psc_underperforming"`; if already AMBER/RED from other rules, **keep the higher severity** and still record `factors.pscUnderperforming = true` + detention_count / list_date in JSON.
   - Sanctions / high MMSI-misuse remain able to force **RED 100** as today.
3. **API:** expose under `factors` on `/mantis/ship-risk` (e.g. `pscUnderperforming`, `pscDetentionCount`, `pscListDate`, `pscSource`) without a new public endpoint in v1.

Suggested score ladder touch-point (relative to current v1.1):

| Signal | Rating / score |
| --- | --- |
| Sanctions or high true MMSI misuse (cargo/tanker) | RED 100 |
| Re-flag only | AMBER 60 |
| **PSC under-performing (Tokyo)** | **AMBER 50** (cap; no RED) |
| Medium misuse | AMBER 40 |
| Low / stale Lloyd’s | AMBER 30 |
| None | GREEN 0 |

---

## 6. Recommendation

| Question | Answer |
| --- | --- |
| Feasible to ingest? | **Yes** — small monthly PDF (28 ships), stable columns, all IMOs valid on this edition. |
| Blocker before prod? | **Permission / ToS** for APCIS-style “no commercial use / no republication without permission” — confirm with Tokyo MOU Secretariat for internal screening use. |
| Value vs identity-risk? | Half the list already on AIS in pnav; almost none currently rated — PSC would surface new AMBERs (and attach to SDN ships like SEA OPERA / TULIP once ratings are keyed by IMO). |
| Next build step (when approved) | DDL above → `psc_tokyo_ingest.py` → wire AMBER-50 factor into `score_ship` + job; tests; no cron until verified. |

---

## 7. Rollback / cleanup (research)

- srv-dp `/tmp` PDF: already deleted.  
- Laptop `/tmp/tmou_underperforming_sep2026.pdf` and parse JSON are ephemeral research artefacts (not committed).  
- No DB objects created.
