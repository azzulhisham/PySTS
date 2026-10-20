"""Human-verification queue for proximity observations.

GET reads unreviewed cases that are still unexplained. POST records a label.
Neither path sets ais_vesselproximityobservation.is_anomaly.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from ais_identity import clean_ship_name
from pg_engine import get_pg_engine

MIN_SUSPICION_SCORE = 4.5
REVIEW_CLASSES = frozenset({
    "illicit_transfer",
    "bunkering",
    "dredge",
    "other_legal",
    "uncertain",
})

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


def cases_to_payload(frame: pd.DataFrame) -> list[dict[str, Any]]:
    records = []
    if frame.empty:
        return records
    for _, row in frame.iterrows():
        members = row.get("members")
        if isinstance(members, str):
            members = json.loads(members)
        elif members is None or (isinstance(members, float) and pd.isna(members)):
            members = []
        cleaned_members = []
        for member in members or []:
            if not isinstance(member, dict):
                cleaned_members.append(member)
                continue
            item = dict(member)
            if "shipName" in item:
                item["shipName"] = clean_ship_name(item.get("shipName"))
            cleaned_members.append(item)
        members = cleaned_members
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
            "firstDetectedAt": _iso(row.get("first_detected_at")),
            "lastDetectedAt": _iso(row.get("last_detected_at")),
            "durationSeconds": None if _clean(row.get("duration_seconds")) is None else float(row["duration_seconds"]),
            "centroidLatitude": None if _clean(row.get("centroid_latitude")) is None else float(row["centroid_latitude"]),
            "centroidLongitude": None if _clean(row.get("centroid_longitude")) is None else float(row["centroid_longitude"]),
            "vesselCount": None if _clean(row.get("vessel_count")) is None else int(row["vessel_count"]),
            "inAnchorage": None if _clean(row.get("in_anchorage")) is None else bool(row["in_anchorage"]),
            "anchorageName": _clean(row.get("anchorage_name")),
            "outsideAnchorage": None if _clean(row.get("outside_anchorage")) is None else bool(row["outside_anchorage"]),
            "sanctionsMatch": bool(_clean(row.get("sanctions_match"))),
            "dredgeLicenceMatch": bool(_clean(row.get("dredge_licence_match"))),
            "bunkerListMatch": bool(_clean(row.get("bunker_list_match"))),
            "darkOverlap": bool(_clean(row.get("dark_overlap"))),
            "illegalAnchorOverlap": bool(_clean(row.get("illegal_anchor_overlap"))),
            "loiterOverlap": bool(_clean(row.get("loiter_overlap"))),
            "anchorSwingOverlap": bool(_clean(row.get("anchor_swing_overlap"))),
            "spoofOverlap": bool(_clean(row.get("spoof_overlap"))),
            "members": members or [],
        })
    return records


LIST_SQL = text(
    """
    SELECT *
    FROM public.ais_stsreviewcase
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
    ORDER BY sanctions_match DESC NULLS LAST, suspicion_score DESC NULLS LAST
    """
)


UNREVIEWED_SQL = text(
    """
    SELECT *
    FROM public.ais_stsreviewcase
    WHERE status = 'unreviewed'
      AND (
            CAST(:date_from AS timestamptz) IS NULL
            OR COALESCE(last_detected_at, first_detected_at) >= CAST(:date_from AS timestamptz)
      )
      AND (
            CAST(:date_to AS timestamptz) IS NULL
            OR COALESCE(first_detected_at, last_detected_at) <= CAST(:date_to AS timestamptz)
      )
    ORDER BY last_detected_at DESC NULLS LAST, suspicion_score DESC NULLS LAST
    """
)


def list_unreviewed(
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    engine: Engine | None = None,
) -> dict[str, Any]:
    """Every unreviewed case. Optional window overlaps the pair's open interval."""
    if date_from is not None and date_to is not None and date_from > date_to:
        raise ValueError("from must be earlier than to")
    engine = engine or get_pg_engine()
    frame = pd.read_sql(
        UNREVIEWED_SQL,
        con=engine,
        params={"date_from": date_from, "date_to": date_to},
    )
    cases = cases_to_payload(frame)
    return {
        "date_from": None if date_from is None else date_from.astimezone(timezone.utc).isoformat(),
        "date_to": None if date_to is None else date_to.astimezone(timezone.utc).isoformat(),
        "case_count": len(cases),
        "cases": cases,
    }


def list_unexplained(min_suspicion_score: float = MIN_SUSPICION_SCORE, engine: Engine | None = None) -> dict[str, Any]:
    engine = engine or get_pg_engine()
    frame = pd.read_sql(LIST_SQL, con=engine, params={"min_score": float(min_suspicion_score)})
    cases = cases_to_payload(frame)
    return {
        "min_suspicion_score": float(min_suspicion_score),
        "case_count": len(cases),
        "sanctions_match_count": sum(1 for case in cases if case["sanctionsMatch"]),
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
