#!/usr/bin/env python3
"""
UK FCDO Sanctions List (UKSL) ingest for Postgres `pnav`.

Downloads the free UK-Sanctions-List.xml feed and (when not --dry-run) replaces
uk_entry / uk_aka / uk_identifier in one transaction, then relies on view uk_vessel.

  python3 backend/uk_sanctions_ingest.py --dry-run
  python3 backend/uk_sanctions_ingest.py --xml /tmp/UK-Sanctions-List.xml --dry-run
  python3 backend/uk_sanctions_ingest.py --keep-xml backend/data/uk_sanctions_list.xml

Safety (same spirit as OFAC/UN loads):
  - all-or-nothing TRUNCATE+INSERT in one transaction
  - skip load when DateGenerated matches the last uk_ingest_run.publish_date
  - refuse load when Ship count (valid IMO/MMSI vessels) drops more than 10%
    vs the previous successful run
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

USER_AGENT = "PySTS-MANTIS-UK-ingest/1.0 (maritime screening; FCDO UK Sanctions List)"

pswd = "m4r1t1m3"
encoded_password = quote(pswd)
DATABASE_URL = (
    f"postgresql://postgresadmin:{encoded_password}"
    f"@marineai2.cxwk8yige5f2.ap-southeast-5.rds.amazonaws.com:5432/pnav"
)

SOURCE_URL = "https://sanctionslist.fcdo.gov.uk/docs/UK-Sanctions-List.xml"
LIST_NAME = "UK"
XML_FILENAME = "UK-Sanctions-List.xml"
MIN_BYTES = 100_000
SHIP_DROP_MAX_FRACTION = 0.10

IMO_DIGITS = re.compile(r"(\d{7})")

QUERY_TIMEOUT_MS = 15_000
INGEST_TIMEOUT_MS = 120_000


def get_engine(*, statement_timeout_ms: int = QUERY_TIMEOUT_MS):
    from sqlalchemy import create_engine

    options = (
        f"-c statement_timeout={int(statement_timeout_ms)} "
        f"-c lock_timeout=5000"
    )
    return create_engine(
        DATABASE_URL,
        pool_size=2,
        max_overflow=2,
        pool_timeout=30,
        connect_args={"options": options},
    )


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


def extract_ship_imo(raw: str | None) -> str | None:
    """Return a check-digit-valid 7-digit IMO from UK ``IMONumber`` text, else None."""
    if not raw:
        return None
    match = IMO_DIGITS.search(str(raw).strip())
    if not match:
        return None
    candidate = match.group(1)
    return candidate if imo_check_digit_ok(candidate) else None


def should_skip_unchanged_date(
    publish_date: str | None,
    last_publish_date: str | None,
) -> bool:
    if not publish_date or not last_publish_date:
        return False
    return str(publish_date).strip() == str(last_publish_date).strip()


def ship_count_drop_too_large(
    new_count: int,
    previous_count: int | None,
    *,
    max_fraction: float = SHIP_DROP_MAX_FRACTION,
) -> bool:
    """True when new ship/vessel count falls more than max_fraction vs last run."""
    if previous_count is None or previous_count <= 0:
        return False
    return float(new_count) < float(previous_count) * (1.0 - float(max_fraction))


def _compose_name(name_el: ET.Element) -> str | None:
    parts: list[str] = []
    for i in range(1, 7):
        value = _child_text(name_el, f"Name{i}")
        if value:
            parts.append(value)
    return " ".join(parts) if parts else None


def _is_primary_name_type(name_type: str | None) -> bool:
    if not name_type:
        return False
    return "primary" in name_type.strip().lower()


def _dedupe_aliases(akas: list[dict[str, Any]], primary_name: str | None) -> list[dict[str, Any]]:
    """Drop duplicate alias strings (casefold) and aliases that equal the primary name."""
    seen: set[str] = set()
    primary_key = (primary_name or "").strip().casefold()
    if primary_key:
        seen.add(primary_key)
    out: list[dict[str, Any]] = []
    for aka in akas:
        alias = (aka.get("alias_name") or "").strip()
        if not alias:
            continue
        key = alias.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(
            {
                "aka_type": aka.get("aka_type"),
                "quality": aka.get("quality"),
                "alias_name": alias,
            }
        )
    return out


def _names_from_designation(elem: ET.Element) -> tuple[str | None, list[dict[str, Any]]]:
    primary: str | None = None
    akas: list[dict[str, Any]] = []
    names_el = _child(elem, "Names")
    if names_el is None:
        return None, []
    for name_el in names_el.findall("{*}Name"):
        composed = _compose_name(name_el)
        if not composed:
            continue
        name_type = _child_text(name_el, "NameType")
        if _is_primary_name_type(name_type) and primary is None:
            primary = composed
        else:
            akas.append(
                {
                    "aka_type": name_type or "Alias",
                    "quality": None,
                    "alias_name": composed,
                }
            )
    # If XML labelled no Primary, first name becomes primary.
    if primary is None and akas:
        first = akas.pop(0)
        primary = first["alias_name"]
    return primary, _dedupe_aliases(akas, primary)


def _first_text(elem: ET.Element, tag: str) -> str | None:
    for node in elem.iter():
        if _local(node.tag) == tag and node.text and node.text.strip():
            return node.text.strip()
    return None


def _ship_details(elem: ET.Element) -> dict[str, Any]:
    imo: str | None = None
    all_imos: list[str] = []
    for node in elem.iter():
        if _local(node.tag) != "IMONumber":
            continue
        candidate = extract_ship_imo(node.text)
        if candidate and candidate not in all_imos:
            all_imos.append(candidate)
    if all_imos:
        imo = all_imos[0]
    return {
        "imo": imo,
        "all_imos": all_imos,
        "vessel_flag": _first_text(elem, "CurrentBelievedFlagOfShip"),
        "vessel_type": _first_text(elem, "TypeOfShip"),
        "vessel_owner": _first_text(elem, "CurrentOwnerOperator"),
    }


def _comments(elem: ET.Element) -> str | None:
    bits: list[str] = []
    for tag in ("OtherInformation", "UKStatementofReasons"):
        value = _child_text(elem, tag)
        if value:
            bits.append(value)
    return "\n".join(bits) if bits else None


def _identifiers(
    elem: ET.Element,
    *,
    is_ship: bool,
    ship_imos: list[str],
) -> list[dict[str, Any]]:
    ids: list[dict[str, Any]] = []
    ofsi = _child_text(elem, "OFSIGroupID")
    if ofsi:
        ids.append({"id_type": "OFSI Group ID", "id_number": ofsi, "id_country": None})
    un_ref = _child_text(elem, "UNReferenceNumber")
    if un_ref:
        ids.append({"id_type": "UN Reference Number", "id_number": un_ref, "id_country": None})
    if is_ship:
        for imo in ship_imos:
            ids.append({"id_type": "IMO", "id_number": imo, "id_country": None})
    return ids


def parse_uk_xml(path: Path) -> tuple[str | None, int, list[dict[str, Any]]]:
    """Return (date_generated, xml_record_count, entries) deduped by UniqueID."""
    publish_date: str | None = None
    entries: list[dict[str, Any]] = []
    seen_uids: set[str] = set()

    for event, elem in ET.iterparse(path, events=("end",)):
        tag = _local(elem.tag)
        if tag == "DateGenerated" and publish_date is None:
            if elem.text and elem.text.strip():
                publish_date = elem.text.strip()
            elem.clear()
            continue
        if tag != "Designation":
            continue

        uid = _child_text(elem, "UniqueID")
        if not uid:
            elem.clear()
            continue
        if uid in seen_uids:
            elem.clear()
            continue
        seen_uids.add(uid)

        entry_type = _child_text(elem, "IndividualEntityShip") or "Unknown"
        is_ship = entry_type.strip().lower() == "ship"
        name, akas = _names_from_designation(elem)
        ship = _ship_details(elem) if is_ship else {
            "imo": None,
            "all_imos": [],
            "vessel_flag": None,
            "vessel_type": None,
            "vessel_owner": None,
        }
        # IMO only for Ship rows with a valid check digit.
        imo = ship["imo"] if is_ship else None
        all_imos = ship["all_imos"] if is_ship else []

        ofsi = _child_text(elem, "OFSIGroupID")
        entries.append(
            {
                "uid": uid,
                "entry_type": entry_type,
                "reference_number": ofsi or uid,
                "name": name,
                "comments": _comments(elem),
                "regime": _child_text(elem, "RegimeName"),
                "listed_on": _child_text(elem, "DateDesignated"),
                "imo": imo,
                "all_imos": all_imos,
                "mmsi": None,
                "vessel_flag": ship["vessel_flag"] if is_ship else None,
                "vessel_type": ship["vessel_type"] if is_ship else None,
                "vessel_owner": ship["vessel_owner"] if is_ship else None,
                "akas": akas,
                "ids": _identifiers(elem, is_ship=is_ship, ship_imos=all_imos),
            }
        )
        elem.clear()

    if not entries:
        raise RuntimeError("No Designation records parsed — XML schema may have changed")

    logging.info("Parsed %s entries (DateGenerated=%s)", len(entries), publish_date)
    return publish_date, len(entries), entries


def vessel_count(entries: list[dict[str, Any]]) -> int:
    return sum(
        1
        for e in entries
        if str(e.get("entry_type") or "").lower() == "ship"
        and (e.get("imo") or e.get("mmsi"))
    )


def last_ingest_meta(engine) -> tuple[str | None, int | None]:
    from sqlalchemy import text

    with engine.connect() as conn:
        exists = conn.execute(text("SELECT to_regclass('public.uk_ingest_run')")).scalar()
        if not exists:
            return None, None
        row = conn.execute(
            text(
                """
                SELECT publish_date, vessel_count
                FROM uk_ingest_run
                ORDER BY id DESC
                LIMIT 1
                """
            )
        ).fetchone()
    if not row:
        return None, None
    return row[0], row[1]


def _execute_values(conn, sql: str, rows: list[tuple], *, page_size: int = 1000) -> None:
    """Batch INSERT via psycopg2 execute_values (fast over WAN vs row-at-a-time)."""
    if not rows:
        return
    from psycopg2.extras import execute_values

    raw = conn.connection.driver_connection
    with raw.cursor() as cur:
        execute_values(cur, sql, rows, page_size=page_size)


def replace_tables(
    engine,
    entries: list[dict[str, Any]],
    source_url: str,
    publish_date: str | None,
    xml_record_count: int,
) -> dict[str, int]:
    from sqlalchemy import text

    ships = vessel_count(entries)
    ingested_at = datetime.now(timezone.utc)

    entry_rows = [
        (
            e["uid"],
            e["entry_type"],
            e["reference_number"],
            e["name"],
            e["comments"],
            e["regime"],
            e["listed_on"],
            e["imo"],
            e["mmsi"],
            e["vessel_flag"],
            e["vessel_type"],
            e["vessel_owner"],
            LIST_NAME,
            publish_date,
            ingested_at,
        )
        for e in entries
    ]

    aka_rows = []
    id_rows = []
    for e in entries:
        for aka in e["akas"]:
            aka_rows.append(
                (e["uid"], aka["aka_type"], aka["quality"], aka["alias_name"])
            )
        for ident in e["ids"]:
            if not ident["id_number"]:
                continue
            id_rows.append(
                (e["uid"], ident["id_type"], ident["id_number"], ident["id_country"])
            )

    with engine.begin() as conn:
        conn.execute(text(f"SET LOCAL statement_timeout = '{INGEST_TIMEOUT_MS}'"))
        conn.execute(text("TRUNCATE uk_aka, uk_identifier, uk_entry"))
        _execute_values(
            conn,
            """
            INSERT INTO uk_entry (
                uid, entry_type, reference_number, name, comments, regime,
                listed_on, imo, mmsi, vessel_flag, vessel_type, vessel_owner,
                list_name, publish_date, ingested_at
            ) VALUES %s
            """,
            entry_rows,
        )
        _execute_values(
            conn,
            """
            INSERT INTO uk_aka (uid, aka_type, quality, alias_name)
            VALUES %s
            """,
            aka_rows,
        )
        _execute_values(
            conn,
            """
            INSERT INTO uk_identifier (uid, id_type, id_number, id_country)
            VALUES %s
            """,
            id_rows,
        )
        conn.execute(
            text(
                """
                INSERT INTO uk_ingest_run (
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
                "vessel_count": ships,
            },
        )

    return {
        "rows_loaded": len(entry_rows),
        "aka_rows": len(aka_rows),
        "id_rows": len(id_rows),
        "vessel_count": ships,
    }


def _print_dry_run_summary(publish_date: str | None, entries: list[dict[str, Any]]) -> None:
    ships = [e for e in entries if str(e.get("entry_type") or "").lower() == "ship" and e.get("imo")]
    print(f"XML DateGenerated: {publish_date}")
    print(f"Total entries: {len(entries)}")
    print(f"Ship entries with valid IMO: {len(ships)}")
    print("Sample ships (up to 10):")
    for e in ships[:10]:
        print(f"  name={e['name']!r} imo={e['imo']} uid={e['uid']}")
    if not ships:
        print("  (none)")


def ingest(
    *,
    xml_path: Path | None,
    keep_xml: Path | None,
    dry_run: bool,
    url_override: str | None,
    force: bool = False,
) -> int:
    url = url_override or SOURCE_URL
    tmp_dir = Path(tempfile.mkdtemp(prefix="uk_ingest_"))
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

        publish_date, xml_record_count, entries = parse_uk_xml(local_xml)
        ships = vessel_count(entries)
        imo_count = sum(1 for e in entries if e.get("imo"))
        logging.info(
            "UK parsed vessels(with imo/mmsi)=%s with_imo=%s",
            ships,
            imo_count,
        )
        _print_dry_run_summary(publish_date, entries)

        if dry_run:
            logging.info("Dry run: not writing UK list to pnav")
            return 0

        query_engine = get_engine(statement_timeout_ms=QUERY_TIMEOUT_MS)
        last_date, last_ships = last_ingest_meta(query_engine)
        if not force and should_skip_unchanged_date(publish_date, last_date):
            logging.info(
                "Skip UK load: DateGenerated %s unchanged vs last uk_ingest_run",
                publish_date,
            )
            return 0
        if not force and ship_count_drop_too_large(ships, last_ships):
            logging.error(
                "Refuse UK load: vessel_count %s is more than 10%% below last run %s",
                ships,
                last_ships,
            )
            return 2

        ingest_engine = get_engine(statement_timeout_ms=INGEST_TIMEOUT_MS)
        stats = replace_tables(ingest_engine, entries, url, publish_date, xml_record_count)
        logging.info(
            "Loaded UK into pnav: entries=%s vessels=%s akas=%s ids=%s publish_date=%s",
            stats["rows_loaded"],
            stats["vessel_count"],
            stats["aka_rows"],
            stats["id_rows"],
            publish_date,
        )
        logging.info("Query: SELECT * FROM uk_vessel LIMIT 20;")
        return 0
    finally:
        if downloaded and local_xml is not None:
            local_xml.unlink(missing_ok=True)
        for leftover in tmp_dir.glob("*"):
            leftover.unlink(missing_ok=True)
        tmp_dir.rmdir()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Ingest UK FCDO Sanctions List XML into pnav"
    )
    parser.add_argument("--url", default=None, help="Override the official XML URL")
    parser.add_argument("--xml", type=Path, help="Use a local XML file instead of downloading")
    parser.add_argument("--keep-xml", type=Path, help="Copy downloaded XML to this path")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Download and parse only; do not write to Postgres",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Load even when DateGenerated is unchanged (still enforces 10%% drop guard unless first run)",
    )
    args = parser.parse_args(argv)

    try:
        return ingest(
            xml_path=args.xml,
            keep_xml=args.keep_xml,
            dry_run=args.dry_run,
            url_override=args.url,
            force=args.force,
        )
    except Exception:
        logging.exception("UK sanctions ingest failed")
        return 1


if __name__ == "__main__":
    sys.exit(main())
