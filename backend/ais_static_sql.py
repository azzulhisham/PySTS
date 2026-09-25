"""Shared SQL for choosing the one current ais_static row per MMSI.

Twin of CURRENT_STATIC_ROW_ORDER in restapi/vessel_size.py — backend does not
import from restapi (see the abandoned RESTAPI_DIR path insert in
vesselloiteringdetection.py), so the rule is duplicated the same way polygons.py
is. Keep both copies in step.

The ingest job keys on IMO when the transponder sends a usable one and on MMSI
otherwise, so a vessel can hold more than one ais_static row. Newest-first alone
can land on an empty Message 5 shell, which reports a blank name and shipType 0 —
enough to drop the vessel from a shipType filter entirely. Score the populated
row ahead of it.
"""

from __future__ import annotations

STATIC_ROW_PREFERENCE = """
    CASE WHEN NULLIF(NULLIF(TRIM(REPLACE("shipName", '@', '')), ''), '0')
              IS NOT NULL THEN 4 ELSE 0 END
  + CASE WHEN NULLIF(NULLIF(TRIM(REPLACE(callsign, '@', '')), ''), '0')
              IS NOT NULL THEN 2 ELSE 0 END
  + CASE WHEN COALESCE(to_bow, 0) + COALESCE(to_stern, 0)
              + COALESCE(to_port, 0) + COALESCE(to_starboard, 0) > 0
         THEN 1 ELSE 0 END
"""

CURRENT_STATIC_ROW_ORDER = f"({STATIC_ROW_PREFERENCE}) DESC, ts DESC NULLS LAST, id DESC"
