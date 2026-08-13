# MANTIS — next direction

Living list for **this team** (pipeline + API in `PySTS` only).  
Frontend is another developer. Do not mix with TSS Reporting (`PyTSS` / `PyTSS-Reporting`).

**Product stance:** MANTIS produces a **tasking list** (suspected anomalies). It is not a legal finding. Keep AIS behaviour first; add identity lists as **enrichment**, not as a replacement for STS / dark / anchoring.

Specs (keep in step with this list):

- Detection knobs, formulas, **and identity ingest state**: [`mantis-detection.md`](mantis-detection.md)
- API polygon / Excl rules: [`../restapi/README.md`](../restapi/README.md)
- What is / is not MANTIS: [`../readme.md`](../readme.md)

When an item is done: mark `[x]`, note the date, and update [`mantis-detection.md`](mantis-detection.md) if a knob, formula, table, or join key changed.

---

## Now — obtain and join identity lists

Sanctions files are in `pnav`. OFAC labels are on the three API lists. Bunker register is **skipped until a source exists**.

- [x] Obtain OFAC SDN as official bulk XML (not the search website). **2026-08-13.** Ingest: `backend/ofac_sdn_ingest.py` → tables `ofac_sdn_entry`, `ofac_sdn_aka`, `ofac_sdn_identifier`, view `ofac_sdn_vessel`. IMO is normalized from identifier type `Vessel Registration Identification` (`IMO 7406784` → `7406784`). Spec: [`mantis-detection.md` Identity enrichment](mantis-detection.md#identity-enrichment-not-a-fourth-detector).
- [x] Add OFAC consolidated **non-SDN** list (same ingest pattern). **2026-08-13.** `backend/ofac_cons_ingest.py` (or `ofac_sdn_ingest.py --list cons`) → `ofac_cons_entry` / `_aka` / `_identifier` / view `ofac_cons_vessel`. Official file: `https://www.treasury.gov/ofac/downloads/consolidated/consolidated.xml`. Current file is companies and people (SSI, CMIC, PLC, …); **zero vessels is valid**. SDN tables are not truncated.
- [ ] **Skipped for now (2026-08-13).** Obtain a **bunker / barge register**. No file yet. Resume when a source exists (IMO preferred, MMSI if present, name, flag, effective dates, refresh cycle). Do not wait on this for OFAC labels.
- [ ] Obtain other sanctions files if needed (UN / UK OFSI / EU) after OFAC is stable.
- [x] Design join as **labels on existing candidates**, not a fourth detector. **2026-08-13.** `restapi/sanctions.py`: `sanctionsMatch` / `matchConfidence` (`confirmed` \| `possible` \| `none`). `onBunkerRegister` later, when a register exists.
- [x] Attach **OFAC** labels on STS pairs, dark candidates, and illegal-anchoring candidates in `restapi/` (AIS heuristic unchanged). **2026-08-13.** IMO = `confirmed`; MMSI only if candidate has no IMO = `possible`; no name match; unmatched kept. Spec: [`mantis-detection.md` Identity enrichment](mantis-detection.md#identity-enrichment-not-a-fourth-detector).
- [x] Raise **priority for combinations**, do not auto-verdict. **2026-08-13.** Listed ships/pairs are sorted first on the three API lists. `suspicionScore` / dark `confidence` unchanged. Combinations:
  - listed vessel + dark after slow-down
  - listed vessel in an STS pair
  - stop in a watch polygon + listed owner/ship
  - *(later)* bunker barge + cargo/tanker STS outside a designated anchorage
- [x] Do **not** drop unmatched vessels. **2026-08-13.** Implemented: `sanctionsMatch: false`, `matchConfidence: none`, vessel still returned. Sanctions lists lag.

---

## Next — make the three detectors more accurate

Behaviour first. Do these without waiting for perfect lists.

### Dark vessels (`vesselslowspeeddetection.py` + `restapi/dark_vessels.py`)

- [ ] Split `tsstop` vs suspected dark (`tsdark` / `detection_type`: `confirmed_stop` | `suspected_dark` | `stale_mark`).
- [ ] Time-based evidence (minutes at `sog ≤ 3 kn`) instead of `rowcount` alone.
- [ ] Coverage / footprint: last fix on the outer boundary + high sog → coverage exit.
- [ ] Reappearance: same MMSI later far away with plausible transit → down-rank earlier dark.
- [ ] Cross-check: dark near an open STS cluster → higher interest.
- [ ] Ops-tight filter option: `suspected_dark_after_slowdown` AND silence 30 min–72 h AND stronger slow-down evidence.

### STS (`vesselproximitydetection.py` + `restapi/sts_detection.py`)

- [ ] Keep pipeline cluster distance **30 m**; API pair recompute stays **35 m** until you decide to align.
- [ ] **Skipped for now.** Use bunker register so cargo–tanker mix is not the only STS interest signal. Resume when a register exists.
- [ ] Suspicion score remains a **hint**; ground truth stays human `is_anomaly`.

### Illegal anchoring (`vesselstrajectorydetection.py` + `restapi/illegal_anchoring.py`)

- [ ] Time-at-stop (hours) for briefing, not only `rowcount >= 20`.
- [ ] Keep parent / Excl hole rules; do not turn Singapore East / Western OPL / South into a blanket exclusion.
- [ ] Confirm polygons against official port / anchorage limits before calling anything “illegal” in an authority brief.

---

## Then — watch-floor quality

- [ ] Label loop: export candidates, mark dark vs coverage-exit vs gap vs benign STS vs true tasking; retune knobs in `mantis-detection.md`.
- [ ] Optional type denylist (OSV, dredger) if they add noise (bunker list is skipped for now; do not wait on it).
- [ ] AIS lookback watermark (stop full 2–/3-day `ais_position` scan every cycle) when the loops become expensive.
- [ ] One process only for proximity (unique open `cluster_signature`).
- [ ] Brief the frontend developer on new JSON fields (`sanctionsMatch`, later `onBunkerRegister`) before shipping.

---

## Out of scope until product says otherwise

- `backend/vesselzone.py` and `backend/polygons.py` (not MANTIS today)
- `st_app/` (analysis only)
- Copying rules into `PyTSS-Reporting`

---

## Order of work (short)

1. OFAC SDN + non-SDN files are in `pnav`. Bunker register **skipped** until a source exists.
2. **Done:** OFAC labels on STS / dark / anchoring API payloads (`sanctionsMatch` only). Listed items sorted first; AIS scores unchanged.
3. Optional: UN / UK OFSI / EU files after OFAC is stable.
4. Split dark vs confirmed stop; add time-based dark evidence.
5. Keep human review on every list you send upwards. Brief frontend on `sanctionsMatch` before UI work.
