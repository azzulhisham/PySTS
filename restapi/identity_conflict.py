"""
Identity-conflict detector (re-flag / dual MMSI of one hull).

Reads the latest Class-A static row per MMSI plus the last AIS position.
Groups MMSIs that resolve to the same hull using the same corroboration
rule as backend/vesselproximitydetection.py. Detected time is the latest
AIS timestamp among the identities — never the API wall clock.

OFAC labels are attached the same way as STS / dark / illegal-anchoring:
IMO = confirmed, MMSI-only = possible, unmatched groups are kept.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any
from urllib.parse import quote

import duckdb
import pandas as pd
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

from sanctions import (
    CONFIDENCE_RANK,
    attach_sanctions,
    payload_fields,
    sort_listed_first,
)
from vessel_size import DIM_SELECT, class_b_join, dimension_fields

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

RULE_VERSION = "v1.0-identity-conflict-ais-ts-ofac"
MIN_IDENTITY_TEXT_CHARS = 3
PLACEHOLDER_IMOS = frozenset({1234567, 7654321, 9999999})
SHIP_TYPE_FILTER = "70-89 (cargo/tanker/container Class-A large vessels)"
MATCH_RULE = (
    "IMO plus name, callsign or dimensions; or name plus callsign or dimensions. "
    "Placeholder / repdigit IMOs and names shorter than 3 characters are ignored."
)

pswd = "m4r1t1m3"
DATABASE_URL = (
    f"postgresql://postgresadmin:{quote(pswd)}"
    f"@marineai2.cxwk8yige5f2.ap-southeast-5.rds.amazonaws.com:5432/pnav"
)

CANDIDATE_SQL = f"""
SELECT
    s.mmsi,
    s.ts AS identity_ts,
    s."shipName" AS shipname,
    s."shipType" AS shiptype,
    s."shipTypeDesc" AS shiptypedesc,
    s.callsign,
    s.imo,
{DIM_SELECT},
    p.ts AS last_ais_ts,
    p.latitude,
    p.longitude,
    p.sog,
    p.cog,
    p."navStatus" AS navstatus
FROM (
    SELECT *,
           row_number() OVER (PARTITION BY mmsi ORDER BY ts DESC) AS rowcount_static
    FROM public.ais_static
) s
LEFT JOIN public.ais_position p ON p.mmsi = s.mmsi
{class_b_join("s.mmsi")}
WHERE s.rowcount_static = 1
  AND s."shipType" >= 70 AND s."shipType" < 90
"""


class UnionFind:
    """Disjoint-set used to merge MMSIs that share a hull into one group."""

    def __init__(self):
        self.parent: dict[int, int] = {}

    def find(self, x: int) -> int:
        if x not in self.parent:
            self.parent[x] = x
        if self.parent[x] != x:
            self.parent[x] = self.find(self.parent[x])
        return self.parent[x]

    def union(self, x: int, y: int):
        px, py = self.find(x), self.find(y)
        if px != py:
            self.parent[px] = py


def get_pg_engine() -> Engine:
    return create_engine(
        DATABASE_URL,
        pool_size=5,
        max_overflow=10,
        pool_timeout=30,
    )


def _ensure_duckdb_spatial() -> None:
    duckdb.sql("INSTALL spatial")
    duckdb.sql("LOAD spatial")


def _clean_optional(value):
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return value


def _fmt_ts(value: Any) -> str | None:
    ts = _to_utc_ts(value)
    return None if ts is None else ts.isoformat()


def _to_utc_ts(value) -> pd.Timestamp | None:
    """Parse an AIS datetime as UTC so group min/max is well-defined."""
    cleaned = _clean_optional(value)
    if cleaned is None:
        return None
    ts = pd.Timestamp(cleaned)
    if ts.tzinfo is None:
        return ts.tz_localize("UTC")
    return ts.tz_convert("UTC")


def _ais_event_ts(row) -> pd.Timestamp | None:
    """Prefer last position time; fall back to the Type 5 static timestamp."""
    return _to_utc_ts(row.get("last_ais_ts")) or _to_utc_ts(row.get("identity_ts"))


def _normalize_identity_text(value) -> str | None:
    """Uppercase a name or callsign and strip AIS '@' padding."""
    cleaned = _clean_optional(value)
    if cleaned is None:
        return None
    text = " ".join(str(cleaned).replace("@", " ").split()).upper()
    if len(text) < MIN_IDENTITY_TEXT_CHARS:
        return None
    return text


def _normalize_imo(value) -> int | None:
    """Return a plausible 7-digit IMO; drop 0, repdigits, and placeholders."""
    cleaned = _clean_optional(value)
    if cleaned is None:
        return None
    try:
        imo = int(cleaned)
    except (TypeError, ValueError):
        return None
    if not 1_000_000 <= imo <= 9_999_999:
        return None
    if imo in PLACEHOLDER_IMOS or len(set(str(imo))) == 1:
        return None
    return imo


def _normalize_dimensions(row) -> tuple[int, int, int, int] | None:
    dims = []
    for col in ("to_bow", "to_stern", "to_port", "to_starboard"):
        value = _clean_optional(row.get(col) if hasattr(row, "get") else None)
        if value is None:
            return None
        dims.append(int(value))
    if sum(dims) == 0:
        return None
    return tuple(dims)


def vessel_identity(row) -> dict:
    """Collect the identity evidence for one MMSI. Keep in sync with the backend job."""
    getter = row.get if hasattr(row, "get") else lambda key, default=None: default
    return {
        "imo": _normalize_imo(getter("imo")),
        "name": _normalize_identity_text(getter("shipname") if getter("shipname") is not None else getter("shipName")),
        "callsign": _normalize_identity_text(getter("callsign")),
        "dims": _normalize_dimensions(row),
    }


def match_evidence(a: dict, b: dict) -> list[str]:
    """Fields that agree and are trusted enough to use."""
    evidence: list[str] = []
    if a["imo"] is not None and a["imo"] == b["imo"]:
        evidence.append("imo")
    if a["name"] is not None and a["name"] == b["name"]:
        evidence.append("name")
    if a["callsign"] is not None and a["callsign"] == b["callsign"]:
        evidence.append("callsign")
    if a["dims"] is not None and a["dims"] == b["dims"]:
        evidence.append("dims")
    return evidence


def is_same_vessel(a: dict, b: dict) -> bool:
    """
    True when two MMSIs describe the same hull.

    One shared field is never enough (placeholder IMO 1234567 is shared by
    dozens of unrelated ships). Need IMO plus one other attribute, or a name
    backed by the callsign or the dimensions.
    """
    evidence = set(match_evidence(a, b))
    if "imo" in evidence and evidence & {"name", "callsign", "dims"}:
        return True
    return "name" in evidence and bool(evidence & {"callsign", "dims"})


def load_identity_candidates(engine: Engine | None = None) -> pd.DataFrame:
    engine = engine or get_pg_engine()
    df = pd.read_sql(CANDIDATE_SQL, con=engine)
    for col in ("shipname", "shiptypedesc", "callsign"):
        if col in df.columns:
            df[col] = df[col].astype("object")
    return df.drop_duplicates(subset="mmsi", keep="first")


def _build_groups(vessels: pd.DataFrame) -> list[list[int]]:
    """Connected components of same-hull pairs (2+ MMSIs)."""
    if vessels.empty:
        return []

    identities = {int(row["mmsi"]): vessel_identity(row) for _, row in vessels.iterrows()}
    uf = UnionFind()
    by_imo: dict[int, list[int]] = defaultdict(list)
    by_name: dict[str, list[int]] = defaultdict(list)

    for mmsi, ident in identities.items():
        if ident["imo"] is not None:
            by_imo[ident["imo"]].append(mmsi)
        if ident["name"] is not None:
            by_name[ident["name"]].append(mmsi)

    compared: set[tuple[int, int]] = set()
    for bucket in list(by_imo.values()) + list(by_name.values()):
        if len(bucket) < 2:
            continue
        for i, mmsi_a in enumerate(bucket):
            for mmsi_b in bucket[i + 1:]:
                pair = (mmsi_a, mmsi_b) if mmsi_a < mmsi_b else (mmsi_b, mmsi_a)
                if pair in compared:
                    continue
                compared.add(pair)
                if is_same_vessel(identities[mmsi_a], identities[mmsi_b]):
                    uf.union(mmsi_a, mmsi_b)

    buckets: dict[int, set[int]] = defaultdict(set)
    for mmsi in uf.parent:
        buckets[uf.find(mmsi)].add(mmsi)

    return [sorted(group) for group in buckets.values() if len(group) >= 2]


def _group_match_reasons(mmsis: list[int], identities: dict[int, dict]) -> list[str]:
    reasons: set[str] = set()
    for i, a in enumerate(mmsis):
        for b in mmsis[i + 1:]:
            reasons.update(match_evidence(identities[a], identities[b]))
    order = ("imo", "name", "callsign", "dims")
    return [key for key in order if key in reasons]


def _max_internal_distance_m(members: pd.DataFrame) -> float | None:
    points = members.dropna(subset=["longitude", "latitude"])
    if len(points) < 2:
        return None

    _ensure_duckdb_spatial()
    duckdb.register("id_members", points[["mmsi", "longitude", "latitude"]])
    result = duckdb.sql(
        """
        SELECT MAX(
            ST_Distance_Sphere(
                ST_Point(a.longitude, a.latitude),
                ST_Point(b.longitude, b.latitude)
            )
        ) AS max_dist
        FROM id_members a
        INNER JOIN id_members b ON a.mmsi < b.mmsi
        """
    ).fetchone()
    if result is None or result[0] is None:
        return None
    return float(result[0])


def _best_confidence_values(values: list[str]) -> str:
    best = "none"
    for value in values:
        if CONFIDENCE_RANK.get(value, 2) < CONFIDENCE_RANK.get(best, 2):
            best = value
    return best


def identity_to_payload(row) -> dict[str, Any]:
    event_ts = _ais_event_ts(row)
    return {
        "mmsi": int(row["mmsi"]),
        "shipName": None if _clean_optional(row.get("shipname")) is None else str(row.get("shipname")),
        "callsign": None if _clean_optional(row.get("callsign")) is None else str(row.get("callsign")),
        "shipType": int(row["shiptype"]) if _clean_optional(row.get("shiptype")) is not None else None,
        "shipTypeDesc": None if _clean_optional(row.get("shiptypedesc")) is None else str(row.get("shiptypedesc")),
        "latitude": float(row["latitude"]) if _clean_optional(row.get("latitude")) is not None else None,
        "longitude": float(row["longitude"]) if _clean_optional(row.get("longitude")) is not None else None,
        "sog": float(row["sog"]) if _clean_optional(row.get("sog")) is not None else None,
        "cog": float(row["cog"]) if _clean_optional(row.get("cog")) is not None else None,
        "navStatus": int(row["navstatus"]) if _clean_optional(row.get("navstatus")) is not None else None,
        "lastAisAt": _fmt_ts(row.get("last_ais_ts")),
        "identityAt": _fmt_ts(row.get("identity_ts")),
        "detectedAt": _fmt_ts(event_ts),
        "aisTimestampSource": (
            "position" if _to_utc_ts(row.get("last_ais_ts")) is not None else "static"
        ),
        **dimension_fields(row),
        **payload_fields(row),
    }


def groups_to_payload(groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for group in groups:
        records.append({
            "clusterSignature": group["cluster_signature"],
            "detectedAt": _fmt_ts(group["detected_at"]),
            "firstAisAt": _fmt_ts(group["first_ais_at"]),
            "aisTimestampSource": "position" if group["used_position_ts"] else "static",
            "identityCount": group["identity_count"],
            "maxInternalDistanceM": (
                None if group["max_internal_distance_m"] is None
                else round(float(group["max_internal_distance_m"]), 3)
            ),
            "matchReasons": group["match_reasons"],
            "sanctionsMatch": bool(group["sanctions_match"]),
            "matchConfidence": group["match_confidence"],
            "sanctionsList": group["sanctions_list"] if group["sanctions_match"] else None,
            "identities": group["identities_payload"],
        })
    return records


def detect_identity_conflicts(
    engine: Engine | None = None,
    max_distance_m: float | None = None,
) -> dict[str, Any]:
    """
    Return groups of MMSIs that are one hull under the corroboration rule.

    detectedAt is the latest AIS timestamp in the group (position.ts, else
    static.ts). max_distance_m, when set, keeps only groups whose last
    positions are still that close (groups without two positions are dropped).
    """
    engine = engine or get_pg_engine()
    candidates = load_identity_candidates(engine)
    candidates = attach_sanctions(candidates, engine)

    identities = {int(row["mmsi"]): vessel_identity(row) for _, row in candidates.iterrows()}
    by_mmsi = candidates.set_index("mmsi", drop=False)

    groups: list[dict[str, Any]] = []
    for mmsis in _build_groups(candidates):
        members = by_mmsi.loc[mmsis].reset_index(drop=True)
        event_times = [ts for ts in (_ais_event_ts(row) for _, row in members.iterrows()) if ts is not None]
        if not event_times:
            continue

        used_position = any(_to_utc_ts(row.get("last_ais_ts")) is not None for _, row in members.iterrows())
        distance_m = _max_internal_distance_m(members)
        if max_distance_m is not None:
            if distance_m is None or distance_m >= float(max_distance_m):
                continue

        confidences = [
            "none" if _clean_optional(row.get("match_confidence")) is None else str(row.get("match_confidence"))
            for _, row in members.iterrows()
        ]
        lists = [_clean_optional(row.get("sanctions_list")) for _, row in members.iterrows()]
        sanctions_match = bool(members["sanctions_match"].any()) if "sanctions_match" in members.columns else False
        match_confidence = _best_confidence_values(confidences)
        sanctions_list = next((item for item in lists if item), None)

        extra_sort = []
        members_sorted = sort_listed_first(members, extra_sort=extra_sort)
        identities_payload = [identity_to_payload(row) for _, row in members_sorted.iterrows()]

        groups.append({
            "cluster_signature": "_".join(str(m) for m in mmsis),
            "detected_at": max(event_times),
            "first_ais_at": min(event_times),
            "used_position_ts": used_position,
            "identity_count": len(mmsis),
            "max_internal_distance_m": distance_m,
            "match_reasons": _group_match_reasons(mmsis, identities),
            "sanctions_match": sanctions_match,
            "match_confidence": match_confidence,
            "sanctions_list": sanctions_list,
            "identities_payload": identities_payload,
            "_srank": CONFIDENCE_RANK.get(match_confidence, 2),
        })

    groups.sort(key=lambda g: (g["_srank"], -g["detected_at"].timestamp()))
    for group in groups:
        group.pop("_srank", None)

    identities_payload = [ident for group in groups for ident in group["identities_payload"]]
    sanctions_match_group_count = sum(1 for group in groups if group["sanctions_match"])
    sanctions_match_identity_count = sum(1 for ident in identities_payload if ident.get("sanctionsMatch"))

    return {
        "rule_version": RULE_VERSION,
        "ship_type_filter": SHIP_TYPE_FILTER,
        "match_rule": MATCH_RULE,
        "max_distance_m": max_distance_m,
        "candidate_count": int(len(candidates)),
        "group_count": len(groups),
        "identity_count": len(identities_payload),
        "sanctions_match_group_count": sanctions_match_group_count,
        "sanctions_match_identity_count": sanctions_match_identity_count,
        "groups_payload": groups_to_payload(groups),
        "identities_payload": identities_payload,
    }
