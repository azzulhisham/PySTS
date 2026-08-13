# Seadragon Maritime Data Platform — Management Deck

Management-friendly overview of the shared AIS data backbone (JKPTG, TSS Reporting, MANTIS / PySTS).

**Blacksmith branding removed.** Attached screenshot images are **not** used — all diagrams are custom-drawn.

## Deliverable

| File | Description |
|------|-------------|
| `Seadragon_Maritime_Data_Platform_Overview.pdf` | Main management PDF |
| `build_deck.py` | Rebuild script (ReportLab) — enforces gaps between cards |
| `assets/` | Icons + custom diagrams only |

## Rebuild

```bash
cd PySTS/restapi/datapipeline
# optional: regenerate diagrams/icons
../venv/bin/python -c "print('run asset script if needed')"
../venv/bin/python build_deck.py
```

## Design notes

- Consistent **5 mm gap** between horizontal and vertical cards
- Soft shadows + teal accent on cards
- Pipeline diagrams are generated (not screenshots)
