#!/usr/bin/env python3
"""
UN Security Council consolidated sanctions ingest for Postgres `pnav`.

Downloads the free English XML feed and (when not --dry-run) replaces
un_entry / un_aka / un_identifier, then rebuilds view un_vessel.

  python3 backend/un_sanctions_ingest.py --dry-run
  python3 backend/un_sanctions_ingest.py --xml backend/data/un_consolidated.xml --dry-run
  python3 backend/un_sanctions_ingest.py --keep-xml backend/data/un_consolidated.xml

Vessels are not a separate UN type. A listed vessel (e.g. AVAX) carries a ship
IMO as ``IMO: 9058713``. Owner/manager comments often cite company numbers as
``IMO number`` / ``(IMO) Number`` / ``Company Number IMO`` — those go to
un_identifier only (``IMO company number``), never un_entry.imo / un_vessel.
Ship IMOs must also pass the standard check digit.
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

USER_AGENT = "PySTS-MANTIS-UN-ingest/1.0 (maritime screening; UN SC consolidated list)"

pswd = "m4r1t1m3"
encoded_password = quote(pswd)
DATABASE_URL = (
    f"postgresql://postgresadmin:{encoded_password}"
    f"@marineai2.cxwk8yige5f2.ap-southeast-5.rds.amazonaws.com:5432/pnav"
)

SOURCE_URL = "https://scsanctions.un.org/resources/xml/en/consolidated.xml"
LIST_NAME = "UN"
PREFIX = "un"
XML_FILENAME = "un_consolidated.xml"
MIN_BYTES = 50_000

# Ship IMO on a listed vessel: "IMO: 9058713" (colon required; not "IMO number:").
IMO_SHIP = re.compile(r"(?<![A-Za-z])IMO\s*:\s*(\d{7})\b", re.IGNORECASE)

# Company / operator IMO figures in owner-manager comments — not vessel keys.
IMO_COMPANY = re.compile(
    r"(?:"
    r"IMO\s+number\s*[:.]?\s*"
    r"|IMO\s+company(?:\s+number)?\s*[:.]?\s*"
    r"|Company\s+Number\s+IMO\s*"
    r"|International Maritime Organization\s*\(IMO\)\s*Number\s*[:.]?\s*"
    r"|\(IMO\)\s*Number\s*[:.]?\s*"
    r")(\d{7})\b",
    re.IGNORECASE,
)
MMSI_IN_CONTEXT = re.compile(r"\bMMSI\s*[:.#]?\s*(\d{9})\b", re.IGNORECASE)

INGEST_RUN_DDL = """
CREATE TABLE IF NOT EXISTS un_ingest_run (
    id               BIGSERIAL PRIMARY KEY,
    source_url       TEXT NOT NULL,
    list_name        TEXT NOT NULL DEFAULT 'UN',
    publish_date     TEXT,
    xml_record_count INTEGER,
    rows_loaded      INTEGER,
    vessel_count     INTEGER,
    ingested_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""

UN_DDL = (
    INGEST_RUN_DDL
    + """
CREATE TABLE IF NOT EXISTS un_entry (
    uid              INTEGER PRIMARY KEY,
    entry_type       TEXT NOT NULL,
    reference_number TEXT,
    name             TEXT,
    comments         TEXT,
    regime           TEXT,
    listed_on        TEXT,
    imo              TEXT,
    mmsi             TEXT,
    list_name        TEXT NOT NULL DEFAULT 'UN',
    publish_date     TEXT,
    ingested_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS un_aka (
    uid         INTEGER NOT NULL REFERENCES un_entry(uid) ON DELETE CASCADE,
    aka_type    TEXT,
    quality     TEXT,
    alias_name  TEXT
);

CREATE TABLE IF NOT EXISTS un_identifier (
    uid         INTEGER NOT NULL REFERENCES un_entry(uid) ON DELETE CASCADE,
    id_type     TEXT,
    id_number   TEXT,
    id_country  TEXT
);

CREATE INDEX IF NOT EXISTS idx_un_entry_type ON un_entry (entry_type);
CREATE INDEX IF NOT EXISTS idx_un_entry_name ON un_entry (name);
CREATE INDEX IF NOT EXISTS idx_un_entry_imo ON un_entry (imo);
CREATE INDEX IF NOT EXISTS idx_un_entry_mmsi ON un_entry (mmsi);
CREATE INDEX IF NOT EXISTS idx_un_entry_ref ON un_entry (reference_number);
CREATE INDEX IF NOT EXISTS idx_un_aka_alias ON un_aka (alias_name);
CREATE INDEX IF NOT EXISTS idx_un_id_type_number ON un_identifier (id_type, id_number);
CREATE INDEX IF NOT EXISTS idx_un_id_number ON un_identifier (id_number);

CREATE OR REPLACE VIEW un_vessel AS
SELECT
    e.uid,
    e.list_name,
    e.name AS vessel_name,
    e.imo,
    e.mmsi,
    e.regime,
    e.listed_on,
    e.publish_date,
    e.reference_number,
    e.comments
FROM un_entry e
WHERE e.imo IS NOT NULL OR e.mmsi IS NOT NULL;
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


def imo_check_digit_ok(imo: str) -> bool:
    """Standard 7-digit IMO check: sum(d[i]*(7-i) for i in 0..5) % 10 == d[6]."""
    if not re.fullmatch(r"\d{7}", imo):
        return False
    digits = [int(c) for c in imo]
    total = sum(d * (7 - i) for i, d in enumerate(digits[:6]))
    return total % 10 == digits[6]


def extract_ship_imos(text: str) -> list[str]:
    """Check-digit-valid ship IMOs from ``IMO: NNNNNNN`` phrasing only."""
    if not text:
        return []
    found: list[str] = []
    for match in IMO_SHIP.finditer(text):
        candidate = match.group(1)
        if imo_check_digit_ok(candidate) and candidate not in found:
            found.append(candidate)
    return found


def extract_company_imos(text: str) -> list[str]:
    """IMO company / operator numbers (valid or invalid check digit)."""
    if not text:
        return []
    found: list[str] = []
    for match in IMO_COMPANY.finditer(text):
        candidate = match.group(1)
        if candidate not in found:
            found.append(candidate)
    return found


# Back-compat alias used by older tests / callers.
def extract_imos_from_text(text: str) -> list[str]:
    return extract_ship_imos(text)


def extract_mmsi_from_text(text: str) -> str | None:
    if not text:
        return None
    match = MMSI_IN_CONTEXT.search(text)
    return match.group(1) if match else None


def _individual_name(elem: ET.Element) -> str | None:
    parts = []
    for tag in ("FIRST_NAME", "SECOND_NAME", "THIRD_NAME", "FOURTH_NAME"):
        value = _child_text(elem, tag)
        if value:
            parts.append(value)
    return " ".join(parts) if parts else None


def _entity_name(elem: ET.Element) -> str | None:
    return _child_text(elem, "FIRST_NAME")


def _aliases(elem: ET.Element, alias_tag: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for aka in elem.findall(f"{{*}}{alias_tag}"):
        alias_name = _child_text(aka, "ALIAS_NAME")
        if not alias_name:
            continue
        rows.append(
            {
                "aka_type": "a.k.a.",
                "quality": _child_text(aka, "QUALITY"),
                "alias_name": alias_name,
            }
        )
    return rows


def _blob_for_ids(name: str | None, comments: str | None, akas: list[dict[str, Any]]) -> str:
    parts = [name or "", comments or ""]
    parts.extend(a["alias_name"] for a in akas if a.get("alias_name"))
    return "\n".join(parts)


def parse_un_xml(path: Path) -> tuple[str | None, int, list[dict[str, Any]]]:
    """Return (date_generated, xml_record_count, entries)."""
    publish_date: str | None = None
    entries: list[dict[str, Any]] = []

    # Root attribute via start event; entries via end.
    for event, elem in ET.iterparse(path, events=("start", "end")):
        tag = _local(elem.tag)
        if event == "start" and tag == "CONSOLIDATED_LIST":
            publish_date = elem.attrib.get("dateGenerated")
            continue
        if event != "end" or tag not in ("INDIVIDUAL", "ENTITY"):
            continue

        entry_type = "Individual" if tag == "INDIVIDUAL" else "Entity"
        name = _individual_name(elem) if tag == "INDIVIDUAL" else _entity_name(elem)
        comments = _child_text(elem, "COMMENTS1")
        alias_tag = "INDIVIDUAL_ALIAS" if tag == "INDIVIDUAL" else "ENTITY_ALIAS"
        akas = _aliases(elem, alias_tag)
        blob = _blob_for_ids(name, comments, akas)
        ship_imos = extract_ship_imos(blob)
        company_imos = extract_company_imos(blob)
        mmsi = extract_mmsi_from_text(blob)

        ids: list[dict[str, Any]] = []
        for raw in ship_imos:
            ids.append({"id_type": "IMO", "id_number": raw, "id_country": None})
        for raw in company_imos:
            ids.append(
                {
                    "id_type": "IMO company number",
                    "id_number": raw,
                    "id_country": None,
                }
            )
        if mmsi:
            ids.append({"id_type": "MMSI", "id_number": mmsi, "id_country": None})

        uid_text = _child_text(elem, "DATAID")
        if not uid_text or not uid_text.isdigit():
            elem.clear()
            continue

        entries.append(
            {
                "uid": int(uid_text),
                "entry_type": entry_type,
                "reference_number": _child_text(elem, "REFERENCE_NUMBER"),
                "name": name,
                "comments": comments,
                "regime": _child_text(elem, "UN_LIST_TYPE"),
                "listed_on": _child_text(elem, "LISTED_ON"),
                "imo": ship_imos[0] if ship_imos else None,
                "all_imos": ship_imos,
                "mmsi": mmsi,
                "akas": akas,
                "ids": ids,
            }
        )
        elem.clear()

    if not entries:
        raise RuntimeError("No INDIVIDUAL/ENTITY records parsed — XML schema may have changed")

    logging.info(
        "Parsed %s entries (dateGenerated=%s)",
        len(entries),
        publish_date,
    )
    return publish_date, len(entries), entries


def apply_ddl(conn, ddl: str) -> None:
    from sqlalchemy import text

    for stmt in ddl.split(";"):
        stmt = stmt.strip()
        if stmt:
            conn.execute(text(stmt))


def replace_tables(
    engine,
    entries: list[dict[str, Any]],
    source_url: str,
    publish_date: str | None,
    xml_record_count: int,
) -> dict[str, int]:
    from sqlalchemy import text

    vessel_count = sum(1 for e in entries if e["imo"] or e["mmsi"])
    ingested_at = datetime.now(timezone.utc)

    entry_rows = [
        {
            "uid": e["uid"],
            "entry_type": e["entry_type"],
            "reference_number": e["reference_number"],
            "name": e["name"],
            "comments": e["comments"],
            "regime": e["regime"],
            "listed_on": e["listed_on"],
            "imo": e["imo"],
            "mmsi": e["mmsi"],
            "list_name": LIST_NAME,
            "publish_date": publish_date,
            "ingested_at": ingested_at,
        }
        for e in entries
    ]

    aka_rows = []
    id_rows = []
    for e in entries:
        for aka in e["akas"]:
            aka_rows.append(
                {
                    "uid": e["uid"],
                    "aka_type": aka["aka_type"],
                    "quality": aka["quality"],
                    "alias_name": aka["alias_name"],
                }
            )
        for ident in e["ids"]:
            if not ident["id_number"]:
                continue
            id_rows.append(
                {
                    "uid": e["uid"],
                    "id_type": ident["id_type"],
                    "id_number": ident["id_number"],
                    "id_country": ident["id_country"],
                }
            )

    with engine.begin() as conn:
        apply_ddl(conn, UN_DDL)
        conn.execute(text("TRUNCATE un_aka, un_identifier, un_entry"))
        conn.execute(
            text(
                """
                INSERT INTO un_entry (
                    uid, entry_type, reference_number, name, comments, regime,
                    listed_on, imo, mmsi, list_name, publish_date, ingested_at
                ) VALUES (
                    :uid, :entry_type, :reference_number, :name, :comments, :regime,
                    :listed_on, :imo, :mmsi, :list_name, :publish_date, :ingested_at
                )
                """
            ),
            entry_rows,
        )
        if aka_rows:
            conn.execute(
                text(
                    """
                    INSERT INTO un_aka (uid, aka_type, quality, alias_name)
                    VALUES (:uid, :aka_type, :quality, :alias_name)
                    """
                ),
                aka_rows,
            )
        if id_rows:
            conn.execute(
                text(
                    """
                    INSERT INTO un_identifier (uid, id_type, id_number, id_country)
                    VALUES (:uid, :id_type, :id_number, :id_country)
                    """
                ),
                id_rows,
            )
        conn.execute(
            text(
                """
                INSERT INTO un_ingest_run (
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
                "list_name": LIST_NAME,
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


def _print_dry_run_summary(publish_date: str | None, entries: list[dict[str, Any]]) -> None:
    with_imo = [e for e in entries if e["imo"]]
    print(f"XML generation date: {publish_date}")
    print(f"Total entries: {len(entries)}")
    print(f"Entries with an IMO (check digit OK): {len(with_imo)}")
    print("Sample vessels (up to 10):")
    for e in with_imo[:10]:
        print(
            f"  name={e['name']!r} imo={e['imo']} ref={e['reference_number']}"
        )
    if not with_imo:
        print("  (none)")


def ingest(
    *,
    xml_path: Path | None,
    keep_xml: Path | None,
    dry_run: bool,
    url_override: str | None,
) -> int:
    url = url_override or SOURCE_URL
    tmp_dir = Path(tempfile.mkdtemp(prefix="un_ingest_"))
    local_xml = xml_path
    downloaded = False
    try:
        if local_xml is None:
            local_xml = tmp_dir / XML_FILENAME
            download_xml(url, local_xml, MIN_BYTES)
            downloaded = True
            if keep_xml:
                keep_xml.parent.mkdir(parents=True, exist_ok=True)
                keep_xml.write_bytes(local_xml.read_bytes())
                logging.info("Kept XML copy at %s", keep_xml)

        publish_date, xml_record_count, entries = parse_un_xml(local_xml)
        vessel_count = sum(1 for e in entries if e["imo"] or e["mmsi"])
        imo_count = sum(1 for e in entries if e["imo"])
        mmsi_count = sum(1 for e in entries if e["mmsi"])
        logging.info(
            "UN parsed vessels(with imo/mmsi)=%s with_imo=%s with_mmsi=%s",
            vessel_count,
            imo_count,
            mmsi_count,
        )
        _print_dry_run_summary(publish_date, entries)

        if dry_run:
            logging.info("Dry run: not writing UN list to pnav")
            return 0

        engine = get_engine()
        stats = replace_tables(engine, entries, url, publish_date, xml_record_count)
        logging.info(
            "Loaded UN into pnav: entries=%s vessels=%s akas=%s ids=%s publish_date=%s",
            stats["rows_loaded"],
            stats["vessel_count"],
            stats["aka_rows"],
            stats["id_rows"],
            publish_date,
        )
        logging.info("Query: SELECT * FROM un_vessel LIMIT 20;")
        return 0
    finally:
        if downloaded and local_xml is not None:
            local_xml.unlink(missing_ok=True)
        for leftover in tmp_dir.glob("*"):
            leftover.unlink(missing_ok=True)
        tmp_dir.rmdir()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Ingest UN Security Council consolidated sanctions XML into pnav"
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

    try:
        return ingest(
            xml_path=args.xml,
            keep_xml=args.keep_xml,
            dry_run=args.dry_run,
            url_override=args.url,
        )
    except Exception:
        logging.exception("UN sanctions ingest failed")
        return 1


if __name__ == "__main__":
    sys.exit(main())
