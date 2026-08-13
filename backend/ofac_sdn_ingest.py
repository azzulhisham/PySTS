#!/usr/bin/env python3
"""
OFAC list ingest for Postgres `pnav` (Linux / cron).

Downloads official bulk XML from the US Treasury (not the Sanctions List
Search web UI). Default is the SDN list. Pass --list cons for the
consolidated non-SDN lists (SSI, FSE, PLC, CAPTA, NS-MBS, NS-CMIC, …).

  python3 backend/ofac_sdn_ingest.py              # SDN
  python3 backend/ofac_cons_ingest.py             # non-SDN consolidated
  python3 backend/ofac_sdn_ingest.py --list both  # both, one after the other
  python3 backend/ofac_sdn_ingest.py --list cons --dry-run

  # Daily is enough. 08:15 Singapore time is after a typical US-afternoon publish.
  15 8 * * * /usr/bin/python3 /opt/PySTS/backend/ofac_sdn_ingest.py --list both >> /var/log/ofac_ingest.log 2>&1

This is a screening feed for tasking, not a legal determination.
The non-SDN file is mostly companies and people; it may have zero vessels.
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
import tempfile
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote
from urllib.request import Request, urlopen

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)

USER_AGENT = "PySTS-MANTIS-OFAC-ingest/1.0 (maritime screening; not Sanctions List Search)"

pswd = "m4r1t1m3"
encoded_password = quote(pswd)
DATABASE_URL = (
    f"postgresql://postgresadmin:{encoded_password}"
    f"@marineai2.cxwk8yige5f2.ap-southeast-5.rds.amazonaws.com:5432/pnav"
)

LISTS: dict[str, dict[str, Any]] = {
    "sdn": {
        "list_name": "SDN",
        "url": "https://www.treasury.gov/ofac/downloads/sdn.xml",
        "prefix": "ofac_sdn",
        "xml_filename": "sdn.xml",
        "min_bytes": 10_000,
    },
    "cons": {
        "list_name": "CONS",
        "url": "https://www.treasury.gov/ofac/downloads/consolidated/consolidated.xml",
        "prefix": "ofac_cons",
        "xml_filename": "consolidated.xml",
        "min_bytes": 5_000,
    },
}

# OFAC stores IMO as idType=Vessel Registration Identification, idNumber="IMO 7406784".
IMO_IN_TEXT = re.compile(r"(?:IMO\s*)?(\d{7})\b", re.IGNORECASE)

INGEST_RUN_DDL = """
CREATE TABLE IF NOT EXISTS ofac_ingest_run (
    id              BIGSERIAL PRIMARY KEY,
    source_url      TEXT NOT NULL,
    list_name       TEXT NOT NULL DEFAULT 'SDN',
    publish_date    TEXT,
    xml_record_count INTEGER,
    rows_loaded     INTEGER,
    vessel_count    INTEGER,
    ingested_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


def ddl_for(prefix: str, list_name: str) -> str:
    return (
        INGEST_RUN_DDL
        + f"""
CREATE TABLE IF NOT EXISTS {prefix}_entry (
    uid                 INTEGER PRIMARY KEY,
    sdn_type            TEXT NOT NULL,
    last_name           TEXT,
    first_name          TEXT,
    title               TEXT,
    remarks             TEXT,
    programs            TEXT[],
    vessel_call_sign    TEXT,
    vessel_type         TEXT,
    vessel_flag         TEXT,
    vessel_owner        TEXT,
    tonnage             TEXT,
    gross_registered_tonnage TEXT,
    imo                 TEXT,
    mmsi                TEXT,
    list_name           TEXT NOT NULL DEFAULT '{list_name}',
    publish_date        TEXT,
    ingested_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE {prefix}_entry ADD COLUMN IF NOT EXISTS imo TEXT;
ALTER TABLE {prefix}_entry ADD COLUMN IF NOT EXISTS mmsi TEXT;

CREATE TABLE IF NOT EXISTS {prefix}_aka (
    uid             INTEGER NOT NULL REFERENCES {prefix}_entry(uid) ON DELETE CASCADE,
    aka_uid         INTEGER,
    aka_type        TEXT,
    category        TEXT,
    last_name       TEXT,
    first_name      TEXT
);

CREATE TABLE IF NOT EXISTS {prefix}_identifier (
    uid             INTEGER NOT NULL REFERENCES {prefix}_entry(uid) ON DELETE CASCADE,
    id_uid          INTEGER,
    id_type         TEXT,
    id_number       TEXT,
    id_country      TEXT
);

CREATE INDEX IF NOT EXISTS idx_{prefix}_entry_type ON {prefix}_entry (sdn_type);
CREATE INDEX IF NOT EXISTS idx_{prefix}_entry_last_name ON {prefix}_entry (last_name);
CREATE INDEX IF NOT EXISTS idx_{prefix}_entry_imo ON {prefix}_entry (imo);
CREATE INDEX IF NOT EXISTS idx_{prefix}_entry_mmsi ON {prefix}_entry (mmsi);
CREATE INDEX IF NOT EXISTS idx_{prefix}_aka_last_name ON {prefix}_aka (last_name);
CREATE INDEX IF NOT EXISTS idx_{prefix}_id_type_number ON {prefix}_identifier (id_type, id_number);
CREATE INDEX IF NOT EXISTS idx_{prefix}_id_number ON {prefix}_identifier (id_number);

CREATE OR REPLACE VIEW {prefix}_vessel AS
SELECT
    e.uid,
    e.list_name,
    e.last_name AS vessel_name,
    e.vessel_call_sign,
    e.vessel_type,
    e.vessel_flag,
    e.vessel_owner,
    e.tonnage,
    e.gross_registered_tonnage,
    e.programs,
    e.remarks,
    e.publish_date,
    e.imo,
    e.mmsi
FROM {prefix}_entry e
WHERE e.sdn_type = 'Vessel';
"""
    )


def get_engine():
    from sqlalchemy import create_engine

    return create_engine(DATABASE_URL, pool_size=2, max_overflow=2, pool_timeout=30)


def download_xml(url: str, dest: Path, min_bytes: int) -> None:
    logging.info("Downloading %s", url)
    req = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(req, timeout=300) as resp, dest.open("wb") as out:
        while True:
            chunk = resp.read(1024 * 256)
            if not chunk:
                break
            out.write(chunk)
    size = dest.stat().st_size
    if size < min_bytes:
        raise RuntimeError(f"Downloaded file is too small ({size} bytes); URL may have changed")
    logging.info("Saved %s (%s bytes)", dest, size)


def _local(tag: str) -> str:
    return tag.split("}", 1)[-1]


def _child(el: ET.Element, tag: str) -> ET.Element | None:
    return el.find(f"{{*}}{tag}")


def _child_text(el: ET.Element, tag: str) -> str | None:
    child = _child(el, tag)
    if child is None or child.text is None:
        return None
    value = child.text.strip()
    return value or None


def parse_ofac_xml(path: Path) -> tuple[str | None, int | None, list[dict[str, Any]]]:
    """Return (publish_date, xml_record_count, entries). Uses iterparse for Linux memory."""
    publish_date: str | None = None
    xml_record_count: int | None = None
    entries: list[dict[str, Any]] = []

    for _event, elem in ET.iterparse(path, events=("end",)):
        tag = _local(elem.tag)
        if tag == "publshInformation":
            publish_date = _child_text(elem, "Publish_Date")
            count_text = _child_text(elem, "Record_Count")
            if count_text and count_text.isdigit():
                xml_record_count = int(count_text)
            elem.clear()
            continue

        if tag != "sdnEntry":
            continue

        vessel = _child(elem, "vesselInfo")
        programs = [
            p.text.strip()
            for p in elem.findall("{*}programList/{*}program")
            if p.text
        ]

        akas = []
        for aka in elem.findall("{*}akaList/{*}aka"):
            akas.append(
                {
                    "aka_uid": _child_text(aka, "uid"),
                    "aka_type": _child_text(aka, "type"),
                    "category": _child_text(aka, "category"),
                    "last_name": _child_text(aka, "lastName"),
                    "first_name": _child_text(aka, "firstName"),
                }
            )

        ids = []
        for ident in elem.findall("{*}idList/{*}id"):
            ids.append(
                {
                    "id_uid": _child_text(ident, "uid"),
                    "id_type": _child_text(ident, "idType"),
                    "id_number": _child_text(ident, "idNumber"),
                    "id_country": _child_text(ident, "idCountry"),
                }
            )

        entries.append(
            {
                "uid": int(_child_text(elem, "uid") or 0),
                "sdn_type": _child_text(elem, "sdnType") or "Unknown",
                "last_name": _child_text(elem, "lastName"),
                "first_name": _child_text(elem, "firstName"),
                "title": _child_text(elem, "title"),
                "remarks": _child_text(elem, "remarks"),
                "programs": programs,
                "vessel_call_sign": _child_text(vessel, "callSign") if vessel is not None else None,
                "vessel_type": _child_text(vessel, "vesselType") if vessel is not None else None,
                "vessel_flag": _child_text(vessel, "vesselFlag") if vessel is not None else None,
                "vessel_owner": _child_text(vessel, "vesselOwner") if vessel is not None else None,
                "tonnage": _child_text(vessel, "tonnage") if vessel is not None else None,
                "gross_registered_tonnage": (
                    _child_text(vessel, "grossRegisteredTonnage") if vessel is not None else None
                ),
                "imo": extract_imo(ids),
                "mmsi": extract_mmsi(ids),
                "akas": akas,
                "ids": ids,
            }
        )
        elem.clear()

    if not entries:
        raise RuntimeError("No sdnEntry records parsed — XML schema may have changed")

    logging.info(
        "Parsed %s entries (publish_date=%s xml_record_count=%s)",
        len(entries),
        publish_date,
        xml_record_count,
    )
    return publish_date, xml_record_count, entries


def _to_int(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def extract_imo(ids: list[dict[str, Any]]) -> str | None:
    """IMO is almost never idType=IMO; it is 'Vessel Registration Identification'."""
    for ident in ids:
        id_type = ident.get("id_type") or ""
        number = ident.get("id_number") or ""
        type_l = id_type.lower()
        if "imo" in type_l or type_l == "vessel registration identification" or number.upper().startswith("IMO"):
            match = IMO_IN_TEXT.search(number)
            if match:
                return match.group(1)
    return None


def extract_mmsi(ids: list[dict[str, Any]]) -> str | None:
    for ident in ids:
        id_type = ident.get("id_type") or ""
        if "mmsi" in id_type.lower():
            number = (ident.get("id_number") or "").strip()
            return number or None
    return None


def apply_ddl(conn, ddl: str) -> None:
    from sqlalchemy import text

    for stmt in ddl.split(";"):
        stmt = stmt.strip()
        if stmt:
            conn.execute(text(stmt))


def replace_tables(
    engine,
    entries: list[dict[str, Any]],
    cfg: dict[str, Any],
    source_url: str,
    publish_date: str | None,
    xml_record_count: int | None,
) -> dict[str, int]:
    from sqlalchemy import text

    prefix = cfg["prefix"]
    list_name = cfg["list_name"]
    vessel_count = sum(1 for e in entries if e["sdn_type"] == "Vessel")
    ingested_at = datetime.now(timezone.utc)

    entry_rows = [
        {
            "uid": e["uid"],
            "sdn_type": e["sdn_type"],
            "last_name": e["last_name"],
            "first_name": e["first_name"],
            "title": e["title"],
            "remarks": e["remarks"],
            "programs": e["programs"] or None,
            "vessel_call_sign": e["vessel_call_sign"],
            "vessel_type": e["vessel_type"],
            "vessel_flag": e["vessel_flag"],
            "vessel_owner": e["vessel_owner"],
            "tonnage": e["tonnage"],
            "gross_registered_tonnage": e["gross_registered_tonnage"],
            "imo": e["imo"],
            "mmsi": e["mmsi"],
            "list_name": list_name,
            "publish_date": publish_date,
            "ingested_at": ingested_at,
        }
        for e in entries
        if e["uid"]
    ]

    aka_rows = []
    id_rows = []
    for e in entries:
        if not e["uid"]:
            continue
        for aka in e["akas"]:
            aka_rows.append(
                {
                    "uid": e["uid"],
                    "aka_uid": _to_int(aka["aka_uid"]),
                    "aka_type": aka["aka_type"],
                    "category": aka["category"],
                    "last_name": aka["last_name"],
                    "first_name": aka["first_name"],
                }
            )
        for ident in e["ids"]:
            if not ident["id_number"]:
                continue
            id_rows.append(
                {
                    "uid": e["uid"],
                    "id_uid": _to_int(ident["id_uid"]),
                    "id_type": ident["id_type"],
                    "id_number": ident["id_number"],
                    "id_country": ident["id_country"],
                }
            )

    with engine.begin() as conn:
        apply_ddl(conn, ddl_for(prefix, list_name))
        conn.execute(text(f"TRUNCATE {prefix}_aka, {prefix}_identifier, {prefix}_entry"))

        conn.execute(
            text(
                f"""
                INSERT INTO {prefix}_entry (
                    uid, sdn_type, last_name, first_name, title, remarks, programs,
                    vessel_call_sign, vessel_type, vessel_flag, vessel_owner,
                    tonnage, gross_registered_tonnage, imo, mmsi, list_name,
                    publish_date, ingested_at
                ) VALUES (
                    :uid, :sdn_type, :last_name, :first_name, :title, :remarks, :programs,
                    :vessel_call_sign, :vessel_type, :vessel_flag, :vessel_owner,
                    :tonnage, :gross_registered_tonnage, :imo, :mmsi, :list_name,
                    :publish_date, :ingested_at
                )
                """
            ),
            entry_rows,
        )

        if aka_rows:
            conn.execute(
                text(
                    f"""
                    INSERT INTO {prefix}_aka (
                        uid, aka_uid, aka_type, category, last_name, first_name
                    ) VALUES (
                        :uid, :aka_uid, :aka_type, :category, :last_name, :first_name
                    )
                    """
                ),
                aka_rows,
            )

        if id_rows:
            conn.execute(
                text(
                    f"""
                    INSERT INTO {prefix}_identifier (
                        uid, id_uid, id_type, id_number, id_country
                    ) VALUES (
                        :uid, :id_uid, :id_type, :id_number, :id_country
                    )
                    """
                ),
                id_rows,
            )

        conn.execute(
            text(
                """
                INSERT INTO ofac_ingest_run (
                    source_url, list_name, publish_date, xml_record_count,
                    rows_loaded, vessel_count
                ) VALUES (
                    :source_url, :list_name, :publish_date, :xml_record_count,
                    :rows_loaded, :vessel_count
                )
                """
            ),
            {
                "source_url": source_url,
                "list_name": list_name,
                "publish_date": publish_date,
                "xml_record_count": xml_record_count,
                "rows_loaded": len(entry_rows),
                "vessel_count": vessel_count,
            },
        )

    return {
        "rows_loaded": len(entry_rows),
        "aka_rows": len(aka_rows),
        "id_rows": len(id_rows),
        "vessel_count": vessel_count,
    }


def ingest_one_list(
    cfg: dict[str, Any],
    *,
    xml_path: Path | None,
    keep_xml: Path | None,
    dry_run: bool,
    url_override: str | None,
    tmp_dir: Path,
) -> int:
    url = url_override or cfg["url"]
    local_xml = xml_path
    downloaded = False

    if local_xml is None:
        local_xml = tmp_dir / cfg["xml_filename"]
        download_xml(url, local_xml, cfg["min_bytes"])
        downloaded = True
        if keep_xml:
            keep_xml.parent.mkdir(parents=True, exist_ok=True)
            keep_xml.write_bytes(local_xml.read_bytes())
            logging.info("Kept XML copy at %s", keep_xml)

    publish_date, xml_record_count, entries = parse_ofac_xml(local_xml)
    vessel_count = sum(1 for e in entries if e["sdn_type"] == "Vessel")
    imo_count = sum(1 for e in entries if e["sdn_type"] == "Vessel" and e["imo"])
    mmsi_count = sum(1 for e in entries if e["sdn_type"] == "Vessel" and e["mmsi"])
    logging.info(
        "%s parsed vessels=%s with_imo=%s with_mmsi=%s",
        cfg["list_name"],
        vessel_count,
        imo_count,
        mmsi_count,
    )
    if dry_run:
        logging.info("Dry run: not writing %s to pnav", cfg["list_name"])
        return 0

    engine = get_engine()
    stats = replace_tables(engine, entries, cfg, url, publish_date, xml_record_count)
    logging.info(
        "Loaded %s into pnav: entries=%s vessels=%s akas=%s ids=%s publish_date=%s",
        cfg["list_name"],
        stats["rows_loaded"],
        stats["vessel_count"],
        stats["aka_rows"],
        stats["id_rows"],
        publish_date,
    )
    logging.info(
        "Query: SELECT * FROM %s_vessel LIMIT 20;",
        cfg["prefix"],
    )
    if downloaded:
        local_xml.unlink(missing_ok=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Ingest official OFAC SDN and/or consolidated non-SDN XML into pnav"
    )
    parser.add_argument(
        "--list",
        choices=("sdn", "cons", "both"),
        default="sdn",
        help="Which OFAC file to load (default: sdn)",
    )
    parser.add_argument("--url", default=None, help="Override the official XML URL")
    parser.add_argument("--xml", type=Path, help="Use a local XML file instead of downloading")
    parser.add_argument("--keep-xml", type=Path, help="Copy downloaded XML to this path")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Download and parse only; do not write to Postgres",
    )
    args = parser.parse_args(argv)

    keys = ("sdn", "cons") if args.list == "both" else (args.list,)
    if args.xml is not None and args.list == "both":
        logging.error("--xml cannot be used with --list both")
        return 2
    if args.url is not None and args.list == "both":
        logging.error("--url cannot be used with --list both")
        return 2

    tmp_dir = Path(tempfile.mkdtemp(prefix="ofac_ingest_"))
    try:
        for key in keys:
            ingest_one_list(
                LISTS[key],
                xml_path=args.xml,
                keep_xml=args.keep_xml,
                dry_run=args.dry_run,
                url_override=args.url,
                tmp_dir=tmp_dir,
            )
        return 0
    except Exception:
        logging.exception("OFAC ingest failed")
        return 1
    finally:
        for leftover in tmp_dir.glob("*"):
            leftover.unlink(missing_ok=True)
        tmp_dir.rmdir()


if __name__ == "__main__":
    sys.exit(main())
