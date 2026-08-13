#!/usr/bin/env python3
"""
OFAC consolidated non-SDN ingest for Postgres `pnav`.

Same connection, parser, and replace pattern as ofac_sdn_ingest.py.
Loads SSI, FSE, PLC, CAPTA, NS-MBS, NS-CMIC and other non-SDN lists
from the official consolidated XML.

  python3 backend/ofac_cons_ingest.py
  python3 backend/ofac_cons_ingest.py --dry-run

This list is mostly companies and people. Zero vessels is valid today.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ofac_sdn_ingest import main

if __name__ == "__main__":
    sys.exit(main(["--list", "cons", *sys.argv[1:]]))
