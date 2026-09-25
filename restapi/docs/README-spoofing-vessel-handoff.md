# Spoofing vessel drill-down — frontend handoff

## Live sample (2026-09-21)

Generated from production detectors (not mocked names):

1. `detect_spoofing()` — fleet list for default 3-day window  
2. Scored cargo/tanker hits for **demo-friendly** cases (implied speed on-chart, Singapore-region coords, not lon sign-error spikes)  
3. Selected MMSI **312532000** · **CHUAN HAI**  
4. `detect_vessel_spoofing_analysis(312532000, same window)`

| Artifact | File |
| --- | --- |
| Full JSON payload | `sample-vessel-analysis-data.json` |
| Dashboard PNG (recommended demo) | `mantis-spoofing-vessel-analysis-demo.png` |
| Map + dashboard PNG | `mantis-spoofing-vessel-analysis-demo-with-map.png` |
| Interactive OpenStreetMap | `mantis-spoofing-vessel-analysis-demo-map.html` |
| Stylized PNG (concept only) | `mantis-spoofing-vessel-analysis-mockup.png` |

**Vessel:** CHUAN HAI · MMSI 312532000 · Belize · cargo  
**Why it’s a good demo:** Singapore Strait (~1.33°N, 104.08°E); **2** events; implied **~216–228 kn** vs reported SOG **~8 kn** on **~0.6 NM** jumps (readable on charts, not millions of kn).

## API

- Fleet: `GET /mantis/spoofing?from=&to=`
- Drill-down: `GET /mantis/spoofing/vessel-analysis?mmsi=312532000&from=&to=`

Swagger: `/mantis/spoofing/vessel-analysis`

## UI note

Implied speed at teleport can exceed chart scale (millions of kn). Show SOG on the main axis; use event callouts or a secondary indicator for implied speed on jumps.
