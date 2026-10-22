"""
AIS identity risk rating: query helpers + pure scoring rules.

Lloyd's List-style MMSI misuse → RED 100 (cargo/tanker or sanctioned).
Re-flag dual-MMSI → AMBER. Lookback default 24 months; older events are info-only.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

from ais_identity import clean_ship_name, is_valid_ship_mmsi
from pg_engine import get_pg_engine

logger = logging.getLogger(__name__)

RULE_VERSION = "v1.1-identity-risk-ll-mmsi"
DEFAULT_LOOKBACK_MONTHS = 24
PLACEHOLDER_IMOS = frozenset({1234567, 7654321, 9999999, 1111111, 533456789})
MISUSE_EVENT_TYPES = frozenset(
    {
        "dual_track_mmsi",
        "lloyds_mmsi_imo_conflict",
        "mmsi_multi_imo_static",
        "mmsi_reused_static_evt",
    }
)
HIGH_MISUSE_TYPES = frozenset({"dual_track_mmsi", "lloyds_mmsi_imo_conflict"})
# Lloyd's statuses that mean the registry ship is not a live peer claimant.
_DEAD_STATUS_FRAGMENTS = (
    "broken up",
    "to be broken up",
    "total loss",
    "no longer a ship",
    "hull exists",
    "laid up",
    "deleted",
    "in casualty",
    "continued existence in doubt",
)


def lloyds_status_inactive(status: Any) -> bool:
    text_value = str(status or "").strip().lower()
    if not text_value:
        return False
    return any(frag in text_value for frag in _DEAD_STATUS_FRAGMENTS)


def classify_lloyds_conflict(
    *,
    mmsi: int,
    ais_imo: str,
    lloyds_imo: str,
    agree_row_exists: bool,
    ais_imo_lloyds_mmsi: int | None,
    lloyds_imo_live_mmsi: int | None,
    lloyds_imo_status: Any = None,
    ais_imo_lloyds_mmsi_live: bool = False,
) -> str:
    """
    Classify a Lloyd's MMSI↔IMO mismatch.

    Returns:
      'stale_registry' — AIS IMO's own Lloyd's row agrees with the broadcast MMSI,
        or the registry "other" ship has moved / is inactive (sale, reflag, newbuild).
      'true_conflict' — two live ships claim the MMSI, or the broadcast IMO is tied
        by Lloyd's to a different MMSI that is still transmitting.
    """
    own_agrees = agree_row_exists or (
        ais_imo_lloyds_mmsi is not None and int(ais_imo_lloyds_mmsi) == int(mmsi)
    )
    other_moved = (
        lloyds_imo_live_mmsi is not None and int(lloyds_imo_live_mmsi) != int(mmsi)
    )
    other_dead = lloyds_status_inactive(lloyds_imo_status)
    two_live_same_mmsi = (
        lloyds_imo_live_mmsi is not None and int(lloyds_imo_live_mmsi) == int(mmsi)
    )
    ais_imo_tied_elsewhere_live = (
        ais_imo_lloyds_mmsi is not None
        and int(ais_imo_lloyds_mmsi) != int(mmsi)
        and bool(ais_imo_lloyds_mmsi_live)
    )

    # Agreeing registry always wins (new delivery alongside stale Lloyd's row).
    if own_agrees:
        return "stale_registry"
    # Live corroboration of misuse / identity swap.
    if ais_imo_tied_elsewhere_live or two_live_same_mmsi:
        return "true_conflict"
    # Other ship moved away or inactive — MMSI still pointing at old Lloyd's IMO.
    if other_moved or other_dead:
        return "stale_registry"
    # Registry disagrees but no live corroboration of theft → info only.
    return "stale_registry"


def imo_check_digit_ok(imo: str) -> bool:
    if not re.fullmatch(r"\d{7}", imo):
        return False
    digits = [int(c) for c in imo]
    total = sum(d * (7 - i) for i, d in enumerate(digits[:6]))
    return total % 10 == digits[6]


def normalize_imo_text(value: Any) -> str | None:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    text_value = str(value).strip()
    if not text_value or text_value.lower() in {"nan", "none", "null"}:
        return None
    match = re.search(r"(\d{7})", text_value)
    if not match:
        return None
    candidate = match.group(1)
    if int(candidate) in PLACEHOLDER_IMOS:
        return None
    if len(set(candidate)) == 1:
        return None
    if not imo_check_digit_ok(candidate):
        return None
    return candidate


def normalize_name(value: Any) -> str | None:
    cleaned = clean_ship_name(value)
    if not cleaned:
        return None
    text_value = cleaned.upper()
    return text_value if len(text_value) >= 2 else None


def names_agree(a: Any, b: Any) -> bool:
    na, nb = normalize_name(a), normalize_name(b)
    if not na or not nb:
        return False
    return na == nb


def ship_key(imo: str | None, mmsi: int | None) -> str | None:
    if imo:
        return f"imo:{imo}"
    if mmsi is not None and is_valid_ship_mmsi(mmsi):
        return f"mmsi:{int(mmsi)}"
    return None


def lookback_cutoff(lookback_months: int, *, now: datetime | None = None) -> datetime:
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    # Approximate months as 30.44 days for stable cutoff.
    return now - timedelta(days=float(lookback_months) * 30.44)


def is_cargo_tanker_type(ship_type: Any) -> bool:
    try:
        if ship_type is None or (isinstance(ship_type, float) and pd.isna(ship_type)):
            return False
        n = int(ship_type)
    except (TypeError, ValueError):
        return False
    return 70 <= n <= 89


def score_ship(
    events: list[dict[str, Any]],
    *,
    lookback_months: int = DEFAULT_LOOKBACK_MONTHS,
    is_cargo_tanker: bool = False,
    now: datetime | None = None,
) -> dict[str, Any]:
    """
    Pure rating rules.

    - RED 100: high-confidence misuse in window on cargo/tanker, or any sanctions match
    - RED 100: high-confidence misuse in window when sanctioned (same)
    - AMBER 60: re-flag only (in window)
    - AMBER 40: medium/low misuse or high misuse on non-cargo/tanker (unsanctioned)
    - AMBER 30: only low-confidence signals in window
    - GREEN 0: otherwise
    Older events are counted in totals / info only.
    """
    cutoff = lookback_cutoff(lookback_months, now=now)
    in_window: list[dict[str, Any]] = []
    info: list[dict[str, Any]] = []
    for ev in events:
        ts = ev.get("ts_start")
        if ts is None:
            continue
        if getattr(ts, "tzinfo", None) is None:
            try:
                ts = pd.Timestamp(ts).tz_localize("UTC").to_pydatetime()
            except Exception:
                ts = pd.Timestamp(ts, tz="UTC").to_pydatetime()
        else:
            ts = pd.Timestamp(ts).tz_convert("UTC").to_pydatetime()
        row = dict(ev)
        row["ts_start"] = ts
        if ts >= cutoff:
            in_window.append(row)
        else:
            info.append(row)

    sanc_lists: list[str] = []
    for ev in in_window + info:
        if ev.get("sanctions_match"):
            for name in ev.get("sanctions_lists") or []:
                if name and name not in sanc_lists:
                    sanc_lists.append(str(name))
    sanctions_match = bool(sanc_lists) or any(ev.get("sanctions_match") for ev in in_window)

    high_misuse = [
        ev
        for ev in in_window
        if ev.get("event_type") in HIGH_MISUSE_TYPES and str(ev.get("confidence") or "") == "high"
    ]
    medium_misuse = [
        ev
        for ev in in_window
        if ev.get("event_type") in MISUSE_EVENT_TYPES and str(ev.get("confidence") or "") == "medium"
    ]
    low_misuse = [
        ev
        for ev in in_window
        if ev.get("event_type") in MISUSE_EVENT_TYPES and str(ev.get("confidence") or "") == "low"
    ]
    # Re-flag is not MMSI misuse; confidence is 'reflag' (legacy 'high' still matches by type).
    reflag = [ev for ev in in_window if ev.get("event_type") == "reflag_dual_mmsi"]

    mmsi_misuse = bool(high_misuse or medium_misuse or low_misuse)
    rating = "GREEN"
    score = 0
    reason = "none"

    if sanctions_match:
        rating, score, reason = "RED", 100, "sanctions_match"
    if high_misuse and (is_cargo_tanker or sanctions_match):
        rating, score, reason = "RED", 100, "high_mmsi_misuse"
    elif high_misuse and not is_cargo_tanker and not sanctions_match:
        rating, score, reason = "AMBER", 40, "high_mmsi_misuse_non_cargo"
    elif medium_misuse and not sanctions_match and rating != "RED":
        rating, score, reason = "AMBER", 40, "medium_mmsi_misuse"
    elif low_misuse and not sanctions_match and rating == "GREEN":
        rating, score, reason = "AMBER", 30, "low_mmsi_misuse"
    elif reflag and rating == "GREEN":
        rating, score, reason = "AMBER", 60, "reflag_dual_mmsi"

    # Sanctions always force RED even if only reflag/misuse amber was set first.
    if sanctions_match:
        rating, score = "RED", 100
        if reason == "none":
            reason = "sanctions_match"

    latest = None
    for ev in in_window + info:
        ts = ev.get("ts_start")
        if ts is not None and (latest is None or ts > latest):
            latest = ts

    return {
        "rating": rating,
        "score": score,
        "mmsi_misuse": mmsi_misuse and rating in {"RED", "AMBER"},
        "sanctions_match": sanctions_match,
        "sanctions_lists": sanc_lists or None,
        "event_count_window": len(in_window),
        "event_count_total": len(in_window) + len(info),
        "latest_event_at": latest,
        "events_in_window": in_window,
        "info_events": info,
        "factors": {
            "reason": reason,
            "lookbackMonths": lookback_months,
            "cutoffUtc": cutoff.isoformat(),
            "isCargoTanker": is_cargo_tanker,
            "highMisuseCount": len(high_misuse),
            "mediumMisuseCount": len(medium_misuse),
            "lowMisuseCount": len(low_misuse),
            "reflagCount": len(reflag),
            "sanctionsLists": sanc_lists,
        },
    }


def _event_row_to_api(row: dict[str, Any]) -> dict[str, Any]:
    lists = row.get("sanctions_lists")
    if lists is not None and hasattr(lists, "tolist"):
        lists = list(lists)
    return {
        "id": row.get("id"),
        "eventType": row.get("event_type"),
        "confidence": row.get("confidence"),
        "mmsi": int(row["mmsi"]) if row.get("mmsi") is not None and not pd.isna(row.get("mmsi")) else None,
        "imo": row.get("imo") or None,
        "imoOther": row.get("imo_other") or None,
        "shipName": row.get("ship_name"),
        "shipNameOther": row.get("ship_name_other"),
        "tsStart": row.get("ts_start").isoformat() if hasattr(row.get("ts_start"), "isoformat") else row.get("ts_start"),
        "tsEnd": row.get("ts_end").isoformat() if hasattr(row.get("ts_end"), "isoformat") else row.get("ts_end"),
        "distanceM": row.get("distance_m"),
        "evidence": row.get("evidence"),
        "sanctionsMatch": bool(row.get("sanctions_match")),
        "sanctionsLists": list(lists) if lists else None,
        "ruleVersion": row.get("rule_version"),
    }


def query_ship_risk(
    *,
    imo: Any = None,
    mmsi: Any = None,
    lookback_months: int = DEFAULT_LOOKBACK_MONTHS,
    engine: Engine | None = None,
) -> dict[str, Any]:
    engine = engine or get_pg_engine()
    imo_key = normalize_imo_text(imo)
    mmsi_key = None
    if mmsi is not None and is_valid_ship_mmsi(mmsi):
        mmsi_key = int(mmsi)
    if not imo_key and mmsi_key is None:
        raise ValueError("imo or mmsi is required")

    lookback_months = max(1, min(int(lookback_months or DEFAULT_LOOKBACK_MONTHS), 120))
    key = ship_key(imo_key, mmsi_key)

    with engine.connect() as conn:
        rating_row = None
        if key:
            rating_row = conn.execute(
                text(
                    """
                    SELECT * FROM ais_ship_risk_rating
                    WHERE ship_key = :key AND lookback_months = :lb
                    LIMIT 1
                    """
                ),
                {"key": key, "lb": lookback_months},
            ).mappings().first()
        if rating_row is None and imo_key:
            rating_row = conn.execute(
                text(
                    """
                    SELECT * FROM ais_ship_risk_rating
                    WHERE imo = :imo AND lookback_months = :lb
                    ORDER BY computed_at DESC LIMIT 1
                    """
                ),
                {"imo": imo_key, "lb": lookback_months},
            ).mappings().first()
        if rating_row is None and mmsi_key is not None:
            rating_row = conn.execute(
                text(
                    """
                    SELECT * FROM ais_ship_risk_rating
                    WHERE mmsi = :mmsi AND lookback_months = :lb
                    ORDER BY computed_at DESC LIMIT 1
                    """
                ),
                {"mmsi": mmsi_key, "lb": lookback_months},
            ).mappings().first()

        clauses = []
        params: dict[str, Any] = {}
        if imo_key:
            clauses.append("(imo = :imo OR imo_other = :imo)")
            params["imo"] = imo_key
        if mmsi_key is not None:
            clauses.append("mmsi = :mmsi")
            params["mmsi"] = mmsi_key
        events_sql = f"""
            SELECT * FROM ais_identity_event
            WHERE {' OR '.join(clauses)}
            ORDER BY ts_start DESC
            LIMIT 500
        """
        event_rows = conn.execute(text(events_sql), params).mappings().all()

    events = [dict(r) for r in event_rows]
    # Prefer stored rating when lookback matches; else recompute from events.
    if rating_row and int(rating_row["lookback_months"]) == lookback_months:
        scored = None
        cutoff = lookback_cutoff(lookback_months)
        in_window, info = [], []
        for ev in events:
            ts = ev.get("ts_start")
            if ts is None:
                continue
            ts = pd.Timestamp(ts)
            if ts.tzinfo is None:
                ts = ts.tz_localize("UTC")
            else:
                ts = ts.tz_convert("UTC")
            ev2 = dict(ev)
            ev2["ts_start"] = ts.to_pydatetime()
            if ts.to_pydatetime() >= cutoff:
                in_window.append(ev2)
            else:
                info.append(ev2)
        factors = rating_row.get("factors") or {}
        return {
            "imo": rating_row.get("imo") or imo_key,
            "mmsi": int(rating_row["mmsi"]) if rating_row.get("mmsi") is not None else mmsi_key,
            "shipName": rating_row.get("ship_name"),
            "shipKey": rating_row.get("ship_key"),
            "rating": rating_row.get("rating"),
            "score": int(rating_row.get("score") or 0),
            "lookbackMonths": lookback_months,
            "mmsiMisuse": bool(rating_row.get("mmsi_misuse")),
            "sanctionsMatch": bool(rating_row.get("sanctions_match")),
            "sanctionsLists": list(rating_row["sanctions_lists"]) if rating_row.get("sanctions_lists") is not None else None,
            "factors": factors,
            "events": [_event_row_to_api(e) for e in in_window],
            "infoEvents": [_event_row_to_api(e) for e in info],
            "ruleVersion": rating_row.get("rule_version") or RULE_VERSION,
            "computedAt": rating_row.get("computed_at").isoformat()
            if hasattr(rating_row.get("computed_at"), "isoformat")
            else rating_row.get("computed_at"),
        }

    scored = score_ship(events, lookback_months=lookback_months, is_cargo_tanker=False)
    return {
        "imo": imo_key,
        "mmsi": mmsi_key,
        "shipName": None,
        "shipKey": key,
        "rating": scored["rating"],
        "score": scored["score"],
        "lookbackMonths": lookback_months,
        "mmsiMisuse": scored["mmsi_misuse"],
        "sanctionsMatch": scored["sanctions_match"],
        "sanctionsLists": scored["sanctions_lists"],
        "factors": scored["factors"],
        "events": [_event_row_to_api(e) for e in scored["events_in_window"]],
        "infoEvents": [_event_row_to_api(e) for e in scored["info_events"]],
        "ruleVersion": RULE_VERSION,
        "computedAt": None,
    }


def query_identity_events(
    *,
    from_ts: datetime | None = None,
    to_ts: datetime | None = None,
    event_type: str | None = None,
    sanctions_only: bool = False,
    limit: int = 100,
    engine: Engine | None = None,
) -> dict[str, Any]:
    engine = engine or get_pg_engine()
    limit = max(1, min(int(limit or 100), 500))
    clauses = ["TRUE"]
    params: dict[str, Any] = {"lim": limit}
    if from_ts is not None:
        clauses.append("ts_start >= :from_ts")
        params["from_ts"] = from_ts
    if to_ts is not None:
        clauses.append("ts_start <= :to_ts")
        params["to_ts"] = to_ts
    if event_type:
        clauses.append("event_type = :etype")
        params["etype"] = event_type
    if sanctions_only:
        clauses.append("sanctions_match = TRUE")
    sql = f"""
        SELECT * FROM ais_identity_event
        WHERE {' AND '.join(clauses)}
        ORDER BY ts_start DESC
        LIMIT :lim
    """
    with engine.connect() as conn:
        rows = conn.execute(text(sql), params).mappings().all()
        total = conn.execute(
            text(f"SELECT count(*) FROM ais_identity_event WHERE {' AND '.join(clauses)}"),
            {k: v for k, v in params.items() if k != "lim"},
        ).scalar()
    return {
        "totalCount": int(total or 0),
        "returnedCount": len(rows),
        "events": [_event_row_to_api(dict(r)) for r in rows],
        "ruleVersion": RULE_VERSION,
    }
