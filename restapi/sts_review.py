"""Human-verification queue for proximity observations.

GET reads unreviewed cases that are still unexplained. POST records a label.
Neither path sets ais_vesselproximityobservation.is_anomaly.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote

import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from ais_identity import clean_ship_name, is_valid_ship_mmsi
from pg_engine import get_pg_engine
from ship_risk_attach import load_risk_indexes, match_risk

MIN_SUSPICION_SCORE = 4.5
REVIEW_CLASSES = frozenset({
    "illicit_transfer",
    "bunkering",
    "dredge",
    "other_legal",
    "uncertain",
})


def case_has_invalid_mmsi(row) -> bool:
    """True when any member MMSI fails is_valid_ship_mmsi."""
    members = row.get("members") if hasattr(row, "get") else None
    if members is None and hasattr(row, "__getitem__"):
        try:
            members = row["members"]
        except Exception:
            members = None
    if isinstance(members, str):
        try:
            members = json.loads(members)
        except Exception:
            return False
    if not members:
        return False
    for member in members:
        if not isinstance(member, dict):
            continue
        if not is_valid_ship_mmsi(member.get("mmsi")):
            return True
    return False


def is_notifiable_case(row: dict) -> bool:
    """Queue rule for notifications / notified_at. Skips harbour and invalid MMSI."""
    if row.get("in_port_area") or row.get("at_berth"):
        return False
    if case_has_invalid_mmsi(row):
        return False
    score = row.get("suspicion_score")
    return (
        score is not None
        and score >= MIN_SUSPICION_SCORE
        and not row.get("dredge_licence_match")
        and bool(
            row.get("sanctions_match")
            or row.get("outside_anchorage")
            or row.get("dark_overlap")
            or row.get("illegal_anchor_overlap")
        )
    )


def notified_at_on_upsert(*, existing_notified_at, should_notify, now):
    """
    Mirror ON CONFLICT: notified_at = COALESCE(existing, EXCLUDED.notified_at).

    Stamp once when newly notifiable; never overwrite an existing stamp.
    """
    if existing_notified_at is not None:
        return existing_notified_at
    return now if should_notify else None

_write_engine: Engine | None = None
_write_lock = threading.Lock()


def get_write_engine() -> Engine:
    """One writable connection for the verify action. The read pool stays read-only."""
    global _write_engine
    if _write_engine is not None:
        return _write_engine
    with _write_lock:
        if _write_engine is None:
            password = os.environ.get("pnav_db_password")
            if not password or not str(password).strip():
                raise RuntimeError(
                    "Environment variable pnav_db_password is required for "
                    "STS review writes (no hard-coded credentials)."
                )
            url = (
                f"postgresql://postgresadmin:{quote(password)}"
                f"@marineai2.cxwk8yige5f2.ap-southeast-5.rds.amazonaws.com:5432/pnav"
            )
            _write_engine = create_engine(
                url,
                pool_size=1,
                max_overflow=0,
                pool_timeout=15,
                pool_pre_ping=True,
                connect_args={
                    "application_name": "mantis_sts_review",
                    "options": "-c statement_timeout=15000 -c lock_timeout=5000",
                },
            )
    return _write_engine


def _clean(value: Any) -> Any:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    try:
        if pd.isna(value):
            return None
    except TypeError:
        pass
    return value


def _iso(value: Any) -> str | None:
    value = _clean(value)
    if value is None:
        return None
    parsed = pd.Timestamp(value)
    if parsed.tzinfo is None:
        parsed = parsed.tz_localize("UTC")
    else:
        parsed = parsed.tz_convert("UTC")
    return parsed.isoformat()


def _iso_z(value: Any) -> str | None:
    """UTC with a Z suffix, so the value can go back in a query string without encoding '+'."""
    iso = _iso(value)
    return None if iso is None else iso.replace("+00:00", "Z")


def _enrich_members_with_risk(
    members: list[Any],
    by_imo: dict[str, dict[str, Any]],
    by_mmsi: dict[str, dict[str, Any]],
) -> tuple[list[Any], int]:
    """
    Append current identity-risk fields onto stored member snapshots (read-time only).

    Stored sanctionsMatch / matchConfidence / sanctionsList on the member stay as
    captured when the case was written. riskRating/Score/Reason are live from
    ais_ship_risk_rating via the process cache.
    """
    enriched: list[Any] = []
    risk_red = 0
    for member in members or []:
        if not isinstance(member, dict):
            enriched.append(member)
            continue
        item = dict(member)
        if "shipName" in item:
            item["shipName"] = clean_ship_name(item.get("shipName"))
        hit = match_risk(item.get("imo"), item.get("mmsi"), by_imo, by_mmsi)
        item["riskRating"] = hit["rating"]
        item["riskScore"] = int(hit["score"])
        item["riskReason"] = hit["reason"]
        if hit["rating"] == "RED":
            risk_red += 1
        enriched.append(item)
    return enriched, risk_red


def risk_counts_from_cases(cases: list[dict[str, Any]]) -> dict[str, int]:
    """List-level RED/AMBER counts over member ships in the returned cases page."""
    red = amber = 0
    for case in cases:
        for member in case.get("members") or []:
            if not isinstance(member, dict):
                continue
            rating = member.get("riskRating")
            if rating == "RED":
                red += 1
            elif rating == "AMBER":
                amber += 1
    return {"risk_red_count": red, "risk_amber_count": amber}


def cases_to_payload(
    frame: pd.DataFrame,
    engine: Engine | None = None,
) -> list[dict[str, Any]]:
    records = []
    if frame.empty:
        return records
    # One cache load for the page; never fails the request (empty indexes → GREEN).
    try:
        by_imo, by_mmsi = load_risk_indexes(engine)
    except Exception:
        by_imo, by_mmsi = {}, {}
    for _, row in frame.iterrows():
        members = row.get("members")
        if isinstance(members, str):
            members = json.loads(members)
        elif members is None or (isinstance(members, float) and pd.isna(members)):
            members = []
        members, risk_red_count = _enrich_members_with_risk(members or [], by_imo, by_mmsi)
        port_name = _clean(row.get("port_area_name"))
        terminal_name = _clean(row.get("port_area_terminal_name"))
        berth_name = _clean(row.get("berth_name"))
        records.append({
            "caseId": int(row["id"]),
            "observationId": int(row["observation_id"]),
            "status": row.get("status"),
            "reviewClass": _clean(row.get("review_class")),
            "labeledAt": _iso(row.get("labeled_at")),
            "labeledBy": _clean(row.get("labeled_by")),
            "reviewNotes": _clean(row.get("review_notes")),
            "suspicionScore": None if _clean(row.get("suspicion_score")) is None else float(row["suspicion_score"]),
            "isOpen": None if _clean(row.get("is_open")) is None else bool(row["is_open"]),
            "notifiedAt": _iso(row.get("notified_at")),
            "firstDetectedAt": _iso(row.get("first_detected_at")),
            "lastDetectedAt": _iso(row.get("last_detected_at")),
            "durationSeconds": None if _clean(row.get("duration_seconds")) is None else float(row["duration_seconds"]),
            "centroidLatitude": None if _clean(row.get("centroid_latitude")) is None else float(row["centroid_latitude"]),
            "centroidLongitude": None if _clean(row.get("centroid_longitude")) is None else float(row["centroid_longitude"]),
            "vesselCount": None if _clean(row.get("vessel_count")) is None else int(row["vessel_count"]),
            "inAnchorage": None if _clean(row.get("in_anchorage")) is None else bool(row["in_anchorage"]),
            "anchorageName": _clean(row.get("anchorage_name")),
            "outsideAnchorage": None if _clean(row.get("outside_anchorage")) is None else bool(row["outside_anchorage"]),
            "inPortArea": bool(row.get("in_port_area")) if "in_port_area" in row.index else False,
            "portAreaName": port_name,
            "portAreaTerminalName": terminal_name,
            "atBerth": bool(row.get("at_berth")) if "at_berth" in row.index else False,
            "berthName": berth_name,
            "invalidMmsi": bool(row.get("invalid_mmsi")) if "invalid_mmsi" in row.index else case_has_invalid_mmsi(row),
            "sanctionsMatch": bool(_clean(row.get("sanctions_match"))),
            "dredgeLicenceMatch": bool(_clean(row.get("dredge_licence_match"))),
            "bunkerListMatch": bool(_clean(row.get("bunker_list_match"))),
            "darkOverlap": bool(_clean(row.get("dark_overlap"))),
            "illegalAnchorOverlap": bool(_clean(row.get("illegal_anchor_overlap"))),
            "loiterOverlap": bool(_clean(row.get("loiter_overlap"))),
            "anchorSwingOverlap": bool(_clean(row.get("anchor_swing_overlap"))),
            "spoofOverlap": bool(_clean(row.get("spoof_overlap"))),
            "riskRedCount": int(risk_red_count),
            "members": members,
        })
    return records


def _annotate_cases(frame: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, int]]:
    """
    Flag harbour / invalid-MMSI on every row. Never drops rows (paged lists).

    Counts are for the rows on this page only.
    """
    from port_areas import annotate_harbour_location

    stats = {
        "in_port_area_count": 0,
        "at_berth_count": 0,
        "invalid_mmsi_count": 0,
    }
    if frame.empty:
        return frame, stats

    annotated = annotate_harbour_location(
        frame,
        lon_col="centroid_longitude",
        lat_col="centroid_latitude",
    )
    annotated["invalid_mmsi"] = annotated.apply(case_has_invalid_mmsi, axis=1)
    stats["in_port_area_count"] = int(annotated["in_port_area"].fillna(False).sum())
    stats["at_berth_count"] = int(annotated["at_berth"].fillna(False).sum())
    stats["invalid_mmsi_count"] = int(annotated["invalid_mmsi"].fillna(False).sum())
    return annotated.reset_index(drop=True), stats


def _annotate_and_filter_cases(
    frame: pd.DataFrame,
    *,
    include_in_port: bool = False,
) -> tuple[pd.DataFrame, dict[str, int], list[dict[str, Any]]]:
    """
    Annotate then drop in-port / at-berth / invalid MMSI (notifications path).

    include_in_port=True keeps harbour rows but still drops invalid MMSI.
    """
    from port_areas import harbour_exclude_mask

    stats = {
        "port_area_excluded_count": 0,
        "at_berth_excluded_count": 0,
        "invalid_mmsi_excluded_count": 0,
    }
    examples: list[dict[str, Any]] = []
    if frame.empty:
        return frame, stats, examples

    annotated, _ = _annotate_cases(frame)
    invalid_mask = annotated["invalid_mmsi"].fillna(False).astype(bool)
    stats["invalid_mmsi_excluded_count"] = int(invalid_mask.sum())
    kept = annotated.loc[~invalid_mask].copy()

    if not include_in_port and not kept.empty:
        exclude = harbour_exclude_mask(kept)
        dropped = kept.loc[exclude]
        stats["port_area_excluded_count"] = int(dropped["in_port_area"].fillna(False).sum())
        stats["at_berth_excluded_count"] = int(dropped["at_berth"].fillna(False).sum())
        for _, row in dropped.head(10).iterrows():
            examples.append({
                "observationId": int(row["observation_id"]),
                "portName": row.get("port_area_name"),
                "terminalName": row.get("port_area_terminal_name"),
                "berthName": row.get("berth_name"),
                "inPortArea": bool(row.get("in_port_area")),
                "atBerth": bool(row.get("at_berth")),
            })
        kept = kept.loc[~exclude].reset_index(drop=True)
    else:
        kept = kept.reset_index(drop=True)
    return kept, stats, examples


DEFAULT_PAGE_SIZE = 100
MAX_PAGE_SIZE = 1000

# id breaks ties so pages do not overlap when a review cycle stamps many cases
# with the same last_detected_at.
PAGE_ORDER = """
    ORDER BY last_detected_at DESC NULLS LAST, suspicion_score DESC NULLS LAST, id DESC
    LIMIT :limit OFFSET :offset
"""

QUEUE_WHERE = """
    WHERE status = 'unreviewed'
      AND suspicion_score >= :min_score
      AND dredge_licence_match IS NOT TRUE
      AND bunker_list_match IS NOT TRUE
      AND (
            sanctions_match IS TRUE
         OR outside_anchorage IS TRUE
         OR dark_overlap IS TRUE
         OR illegal_anchor_overlap IS TRUE
      )
"""

_QUEUE_PARAMS = {"min_score": MIN_SUSPICION_SCORE}

LIST_SQL = text("SELECT * FROM public.ais_stsreviewcase" + QUEUE_WHERE + PAGE_ORDER)

LIST_COUNT_SQL = text(
    """
    SELECT COUNT(*) AS case_count,
           COUNT(*) FILTER (WHERE sanctions_match IS TRUE) AS sanctions_match_count
    FROM public.ais_stsreviewcase
    """
    + QUEUE_WHERE
)

UNREVIEWED_WHERE = """
    WHERE status = 'unreviewed'
      AND (
            CAST(:date_from AS timestamptz) IS NULL
            OR COALESCE(last_detected_at, first_detected_at) >= CAST(:date_from AS timestamptz)
      )
      AND (
            CAST(:date_to AS timestamptz) IS NULL
            OR COALESCE(first_detected_at, last_detected_at) <= CAST(:date_to AS timestamptz)
      )
"""

UNREVIEWED_SQL = text("SELECT * FROM public.ais_stsreviewcase" + UNREVIEWED_WHERE + PAGE_ORDER)

UNREVIEWED_COUNT_SQL = text(
    "SELECT COUNT(*) AS case_count FROM public.ais_stsreviewcase" + UNREVIEWED_WHERE
)


NOTIFY_LIMIT = 200
NOTIFY_DEFAULT_LOOKBACK = timedelta(minutes=5)
NOTIFY_AREAS = ("anchorage", "all")

# The review pipeline stamps a whole batch with its transaction start time, so a
# row can become visible a few seconds after its notified_at. Reading only up to
# now() minus this lag keeps a cursor from skipping such rows.
NOTIFY_COMMIT_LAG = "60 seconds"

NOTIFY_UNTIL_SQL = text(f"SELECT now() - interval '{NOTIFY_COMMIT_LAG}'")

NOTIFY_SQL = text(
    "SELECT * FROM public.ais_stsreviewcase"
    + QUEUE_WHERE
    + """
      AND notified_at > :since
      AND notified_at <= :until
    ORDER BY notified_at, id
    LIMIT :limit
    """
)

NOTIFY_AT_SQL = text(
    "SELECT * FROM public.ais_stsreviewcase"
    + QUEUE_WHERE
    + """
      AND notified_at = :at
    ORDER BY id
    """
)


def _in_anchorage_screen(frame: pd.DataFrame) -> pd.DataFrame:
    """Keep cases whose centroid is inside a parent anchorage buffered 1 km, outside Excl holes."""
    from sts_detection import filter_observations_in_anchorages

    located = frame.dropna(subset=["centroid_latitude", "centroid_longitude"])
    if located.empty:
        return frame.iloc[0:0]
    kept = filter_observations_in_anchorages(
        located[["observation_id", "centroid_longitude", "centroid_latitude"]].reset_index(drop=True)
    )
    return frame[frame["observation_id"].isin(set(kept["observation_id"].tolist()))]


def list_notifications(
    since: datetime | None = None,
    area: str = "anchorage",
    engine: Engine | None = None,
    *,
    include_in_port: bool = False,
) -> dict[str, Any]:
    """
    Cases that entered the review queue after `since`, oldest first. Pass the
    returned next_since as `since` on the following call.
    """
    if area not in NOTIFY_AREAS:
        raise ValueError("area must be anchorage or all")
    if since is not None and since.tzinfo is None:
        since = since.replace(tzinfo=timezone.utc)
    engine = engine or get_pg_engine()
    with engine.connect() as conn:
        until = conn.execute(NOTIFY_UNTIL_SQL).scalar_one()
        if since is None:
            since = until - NOTIFY_DEFAULT_LOOKBACK
        if since >= until:
            frame = pd.DataFrame()
            next_since, has_more = since, False
        else:
            frame = pd.read_sql(
                NOTIFY_SQL,
                con=conn,
                params={**_QUEUE_PARAMS, "since": since, "until": until, "limit": NOTIFY_LIMIT + 1},
            )
            has_more = len(frame) > NOTIFY_LIMIT
            if not has_more:
                next_since = until
            else:
                # Never split one notified_at between two calls: the cursor is "after this time".
                boundary = frame.iloc[NOTIFY_LIMIT]["notified_at"]
                frame = frame.iloc[:NOTIFY_LIMIT]
                frame = frame[frame["notified_at"] < boundary]
                if frame.empty:
                    frame = pd.read_sql(NOTIFY_AT_SQL, con=conn, params={**_QUEUE_PARAMS, "at": boundary})
                    next_since = boundary
                else:
                    next_since = frame["notified_at"].max()

    scanned = len(frame)
    if area == "anchorage" and not frame.empty:
        frame = _in_anchorage_screen(frame)
    frame, harbour_stats, _ = _annotate_and_filter_cases(frame, include_in_port=include_in_port)
    cases = cases_to_payload(frame, engine=engine)
    risk = risk_counts_from_cases(cases)
    return {
        "area": area,
        "since": _iso_z(since),
        "until": _iso_z(until),
        "next_since": _iso_z(next_since),
        "has_more": has_more,
        "scanned_count": scanned,
        "notification_count": len(cases),
        "sanctions_match_count": sum(1 for case in cases if case["sanctionsMatch"]),
        "risk_red_count": risk["risk_red_count"],
        "risk_amber_count": risk["risk_amber_count"],
        "include_in_port": include_in_port,
        **harbour_stats,
        "cases": cases,
    }


def _check_page(page: int, page_size: int) -> None:
    if page < 1:
        raise ValueError("page must be 1 or greater")
    if page_size < 1 or page_size > MAX_PAGE_SIZE:
        raise ValueError(f"pageSize must be between 1 and {MAX_PAGE_SIZE}")


def _page_fields(page: int, page_size: int, total: int, returned: int) -> dict[str, int]:
    return {
        "page": page,
        "page_size": page_size,
        "total_pages": (total + page_size - 1) // page_size,
        "returned_count": returned,
    }


def list_unreviewed(
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    page: int = 1,
    page_size: int = DEFAULT_PAGE_SIZE,
    engine: Engine | None = None,
) -> dict[str, Any]:
    """One page of unreviewed cases. Optional window overlaps the pair's open interval."""
    if date_from is not None and date_to is not None and date_from > date_to:
        raise ValueError("from must be earlier than to")
    _check_page(page, page_size)
    engine = engine or get_pg_engine()
    window = {"date_from": date_from, "date_to": date_to}
    with engine.connect() as conn:
        total = int(conn.execute(UNREVIEWED_COUNT_SQL, window).scalar_one())
        frame = pd.read_sql(
            UNREVIEWED_SQL,
            con=conn,
            params={**window, "limit": page_size, "offset": (page - 1) * page_size},
        )
    # Flag only — same rows / order / page size as the SQL page (main behaviour).
    frame, harbour_stats = _annotate_cases(frame)
    cases = cases_to_payload(frame, engine=engine)
    risk = risk_counts_from_cases(cases)
    return {
        "date_from": None if date_from is None else date_from.astimezone(timezone.utc).isoformat(),
        "date_to": None if date_to is None else date_to.astimezone(timezone.utc).isoformat(),
        "case_count": total,
        **harbour_stats,
        "risk_red_count": risk["risk_red_count"],
        "risk_amber_count": risk["risk_amber_count"],
        **_page_fields(page, page_size, total, len(cases)),
        "cases": cases,
    }


def list_unexplained(
    min_suspicion_score: float = MIN_SUSPICION_SCORE,
    page: int = 1,
    page_size: int = DEFAULT_PAGE_SIZE,
    engine: Engine | None = None,
) -> dict[str, Any]:
    """One page of the notification queue. Counts cover every matching case."""
    _check_page(page, page_size)
    engine = engine or get_pg_engine()
    score = {"min_score": float(min_suspicion_score)}
    with engine.connect() as conn:
        counts = conn.execute(LIST_COUNT_SQL, score).mappings().one()
        frame = pd.read_sql(
            LIST_SQL,
            con=conn,
            params={**score, "limit": page_size, "offset": (page - 1) * page_size},
        )
    # Flag only — same rows / order / page size as the SQL page (main behaviour).
    frame, harbour_stats = _annotate_cases(frame)
    cases = cases_to_payload(frame, engine=engine)
    risk = risk_counts_from_cases(cases)
    total = int(counts["case_count"])
    return {
        "min_suspicion_score": float(min_suspicion_score),
        "case_count": total,
        "sanctions_match_count": int(counts["sanctions_match_count"]),
        "risk_red_count": risk["risk_red_count"],
        "risk_amber_count": risk["risk_amber_count"],
        **harbour_stats,
        **_page_fields(page, page_size, total, len(cases)),
        "cases": cases,
    }


VERIFY_SQL = text(
    """
    UPDATE public.ais_stsreviewcase
    SET status = 'reviewed',
        review_class = :review_class,
        labeled_by = :labeled_by,
        review_notes = :review_notes,
        labeled_at = :labeled_at
    WHERE id = :case_id
      AND status = 'unreviewed'
    RETURNING id, observation_id, status, review_class, labeled_at, labeled_by
    """
)


def verify_case(
    case_id: int,
    review_class: str,
    labeled_by: str | None,
    review_notes: str | None,
) -> dict[str, Any] | None:
    if review_class not in REVIEW_CLASSES:
        raise ValueError(
            "reviewClass must be illicit_transfer, bunkering, dredge, other_legal, or uncertain"
        )
    labeled_at = datetime.now(timezone.utc)
    with get_write_engine().begin() as conn:
        row = conn.execute(
            VERIFY_SQL,
            {
                "case_id": int(case_id),
                "review_class": review_class,
                "labeled_by": labeled_by,
                "review_notes": review_notes,
                "labeled_at": labeled_at,
            },
        ).mappings().first()
    if row is None:
        return None
    return {
        "caseId": int(row["id"]),
        "observationId": int(row["observation_id"]),
        "status": row["status"],
        "reviewClass": row["review_class"],
        "labeledAt": _iso(row["labeled_at"]),
        "labeledBy": row["labeled_by"],
    }
