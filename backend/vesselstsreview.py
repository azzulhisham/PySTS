"""Build ais_stsreviewcase rows from proximity observations.

One pnav connection, at most 100 observations per cycle. Open observations are
taken first. Reviewed rows are never overwritten. ClickHouse is not used.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

BACKEND_DIR = Path(__file__).resolve().parent


def _restapi_dir() -> Path:
    for candidate in (BACKEND_DIR.parent / "restapi", BACKEND_DIR / "restapi"):
        if (candidate / "duckdb_spatial.py").exists():
            return candidate
    return BACKEND_DIR.parent / "restapi"


RESTAPI_DIR = _restapi_dir()
sys.path.insert(0, str(BACKEND_DIR))
sys.path.insert(0, str(RESTAPI_DIR))

from ais_static_sql import CURRENT_STATIC_ROW_ORDER  # noqa: E402
from dredge_licence import matching_project  # noqa: E402
from duckdb_spatial import spatial_df  # noqa: E402
from illegal_anchoring import classify_illegal_anchoring  # noqa: E402
from mantis_tables import ensure_tables  # noqa: E402
from polygons import anchorage_areas, is_excl_name  # noqa: E402
from sanctions import load_ofac_indexes, match_vessel  # noqa: E402
from sts_review import is_notifiable_case  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

LOOP_SECONDS = int(os.environ.get("sts_review_loop_seconds", "900"))
CATCHUP_PAUSE_SECONDS = int(os.environ.get("sts_review_catchup_pause_seconds", "5"))
BATCH_LIMIT = int(os.environ.get("sts_review_batch", "100"))
NM_M = 1852.0

def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if value is None or str(value).strip() == "":
        raise RuntimeError(
            f"Environment variable {name} is required for STS review "
            f"(no hard-coded credentials)."
        )
    return str(value)


_password = _require_env("pnav_db_password")
DATABASE_URL = (
    f"postgresql://postgresadmin:{quote(_password)}"
    f"@marineai2.cxwk8yige5f2.ap-southeast-5.rds.amazonaws.com:5432/pnav"
)

_engine: Engine | None = None


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        _engine = create_engine(
            DATABASE_URL,
            pool_size=1,
            max_overflow=0,
            pool_timeout=30,
            pool_pre_ping=True,
            connect_args={
                "application_name": "sts_review",
                "options": "-c statement_timeout=30000 -c lock_timeout=5000",
            },
        )
    return _engine


def _utc(value) -> datetime | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    parsed = pd.Timestamp(value)
    if pd.isna(parsed):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.tz_localize("UTC")
    else:
        parsed = parsed.tz_convert("UTC")
    return parsed.to_pydatetime()


def _polygon_frames() -> tuple[pd.DataFrame, pd.DataFrame]:
    parents = []
    holes = []
    for area in anchorage_areas:
        ring = [[float(lon), float(lat)] for lon, lat in area["polygon"]]
        if ring[0] != ring[-1]:
            ring.append(ring[0])
        row = {
            "anchorage_name": area["name"],
            "geojson": json.dumps({"type": "Polygon", "coordinates": [ring]}),
        }
        if is_excl_name(area["name"]):
            holes.append(row)
        else:
            parents.append(row)
    return pd.DataFrame(parents), pd.DataFrame(holes)


def label_anchorages(observations: pd.DataFrame) -> pd.DataFrame:
    """Original parent polygons only. The 1 km STS-screen buffer is not applied."""
    out = observations.copy()
    out["in_anchorage"] = False
    out["anchorage_name"] = None
    out["outside_anchorage"] = True
    if out.empty:
        return out
    located = out.dropna(subset=["centroid_longitude", "centroid_latitude"])
    if located.empty:
        return out
    parents, holes = _polygon_frames()
    if parents.empty:
        return out
    named = spatial_df(
        """
        SELECT o.id AS observation_id, MIN(p.anchorage_name) AS anchorage_name
        FROM observations o
        CROSS JOIN parents p
        WHERE ST_Within(
            ST_Point(o.centroid_longitude, o.centroid_latitude),
            ST_GeomFromGeoJSON(p.geojson)
        )
        GROUP BY o.id
        """,
        {"observations": located, "parents": parents},
    )
    if named.empty:
        return out
    excl_ids: set[int] = set()
    if not holes.empty:
        excluded = spatial_df(
            """
            SELECT DISTINCT o.id AS observation_id
            FROM observations o
            CROSS JOIN holes h
            WHERE ST_Within(
                ST_Point(o.centroid_longitude, o.centroid_latitude),
                ST_GeomFromGeoJSON(h.geojson)
            )
            """,
            {"observations": located, "holes": holes},
        )
        if not excluded.empty:
            excl_ids = set(excluded["observation_id"].astype(int))
    names = {
        int(row["observation_id"]): row["anchorage_name"]
        for _, row in named.iterrows()
        if int(row["observation_id"]) not in excl_ids
    }
    out["in_anchorage"] = out["id"].astype(int).map(lambda item: item in names)
    out["anchorage_name"] = out["id"].astype(int).map(names.get)
    out["outside_anchorage"] = ~out["in_anchorage"]
    return out


def _length_m(row) -> float:
    bow = row.get("to_bow")
    stern = row.get("to_stern")
    if bow is None or stern is None or pd.isna(bow) or pd.isna(stern):
        return 0.0
    return float(bow) + float(stern)


def _is_loiter(row, length: float) -> bool:
    path = float(row["path_m"] or 0)
    net = float(row["net_m"] or 0)
    radius = float(row["radius_m"] or 0)
    if path <= 0:
        return False
    hull = max(length, 300.0)
    return (
        path >= 2 * NM_M
        and net <= 0.5 * NM_M
        and (net / path) <= 0.10
        and radius >= hull
        and radius <= 6 * NM_M
    )


def _is_swing(row, length: float) -> bool:
    path = float(row["path_m"] or 0)
    net = float(row["net_m"] or 0)
    radius = float(row["radius_m"] or 0)
    if path <= 0:
        return False
    hull = max(length, 300.0)
    return radius >= 100.0 and radius <= hull and path <= NM_M and (net / path) <= 0.20


def _overlaps(row_start, row_end, win_start, win_end) -> bool:
    start = _utc(row_start)
    end = _utc(row_end) or datetime.now(timezone.utc)
    if start is None or win_start is None or win_end is None:
        return False
    return start <= win_end and end >= win_start


PICK_SQL = text(
    """
    SELECT
        o.id,
        o.cluster_signature,
        o.first_detected_at,
        o.last_detected_at,
        o.duration_seconds,
        o.suspicion_score,
        o.is_open,
        o.centroid_latitude,
        o.centroid_longitude,
        o.vessel_count,
        o.cargo_count,
        o.tanker_count,
        o.max_internal_distance_m
    FROM public.ais_vesselproximityobservation o
    LEFT JOIN public.ais_stsreviewcase c ON c.observation_id = o.id
    WHERE c.id IS NULL
       OR (
            c.status = 'unreviewed'
            AND o.last_detected_at IS DISTINCT FROM c.last_detected_at
       )
    ORDER BY o.is_open DESC, o.last_detected_at DESC NULLS LAST
    LIMIT :batch_limit
    """
)

UPSERT = text(
    """
    INSERT INTO public.ais_stsreviewcase (
        observation_id, cluster_signature, status,
        first_detected_at, last_detected_at, duration_seconds, suspicion_score, is_open,
        centroid_latitude, centroid_longitude, vessel_count, cargo_count, tanker_count,
        max_internal_distance_m, in_anchorage, anchorage_name, outside_anchorage,
        sanctions_match, dredge_licence_match, bunker_list_match,
        dark_overlap, illegal_anchor_overlap, loiter_overlap, anchor_swing_overlap,
        spoof_overlap, members, refreshed_at, created_at, notified_at
    ) VALUES (
        :observation_id, :cluster_signature, 'unreviewed',
        :first_detected_at, :last_detected_at, :duration_seconds, :suspicion_score, :is_open,
        :centroid_latitude, :centroid_longitude, :vessel_count, :cargo_count, :tanker_count,
        :max_internal_distance_m, :in_anchorage, :anchorage_name, :outside_anchorage,
        :sanctions_match, :dredge_licence_match, false,
        :dark_overlap, :illegal_anchor_overlap, :loiter_overlap, :anchor_swing_overlap,
        :spoof_overlap, CAST(:members AS jsonb), now(), now(),
        CASE WHEN CAST(:notify AS boolean) THEN now() END
    )
    ON CONFLICT (observation_id) DO UPDATE SET
        cluster_signature = EXCLUDED.cluster_signature,
        first_detected_at = EXCLUDED.first_detected_at,
        last_detected_at = EXCLUDED.last_detected_at,
        duration_seconds = EXCLUDED.duration_seconds,
        suspicion_score = EXCLUDED.suspicion_score,
        is_open = EXCLUDED.is_open,
        centroid_latitude = EXCLUDED.centroid_latitude,
        centroid_longitude = EXCLUDED.centroid_longitude,
        vessel_count = EXCLUDED.vessel_count,
        cargo_count = EXCLUDED.cargo_count,
        tanker_count = EXCLUDED.tanker_count,
        max_internal_distance_m = EXCLUDED.max_internal_distance_m,
        in_anchorage = EXCLUDED.in_anchorage,
        anchorage_name = EXCLUDED.anchorage_name,
        outside_anchorage = EXCLUDED.outside_anchorage,
        sanctions_match = EXCLUDED.sanctions_match,
        dredge_licence_match = EXCLUDED.dredge_licence_match,
        dark_overlap = EXCLUDED.dark_overlap,
        illegal_anchor_overlap = EXCLUDED.illegal_anchor_overlap,
        loiter_overlap = EXCLUDED.loiter_overlap,
        anchor_swing_overlap = EXCLUDED.anchor_swing_overlap,
        spoof_overlap = EXCLUDED.spoof_overlap,
        members = EXCLUDED.members,
        refreshed_at = now(),
        notified_at = COALESCE(ais_stsreviewcase.notified_at, EXCLUDED.notified_at)
    WHERE ais_stsreviewcase.status = 'unreviewed'
    """
)


def _notifiable(row: dict) -> bool:
    """Same test as the GET /mantis/sts-review queue. bunker_list_match is always false here."""
    return is_notifiable_case(row)


def _ids(frame: pd.DataFrame) -> list[int]:
    return [int(value) for value in frame["mmsi"].dropna().unique().tolist()]


def run_once() -> int:
    engine = get_engine()
    observations = pd.read_sql(PICK_SQL, con=engine, params={"batch_limit": BATCH_LIMIT})
    if observations.empty:
        logging.info("STS review: nothing to refresh")
        return 0

    observations = label_anchorages(observations)
    obs_ids = [int(value) for value in observations["id"].tolist()]
    members = pd.read_sql(
        text(
            """
            SELECT observation_id, mmsi, ship_name, ship_type, ship_type_desc
            FROM public.ais_vesselproximitymember
            WHERE observation_id = ANY(:ids)
            """
        ),
        con=engine,
        params={"ids": obs_ids},
    )
    mmsis = _ids(members) if not members.empty else []
    static = pd.DataFrame()
    if mmsis:
        static = pd.read_sql(
            text(
                f"""
                SELECT DISTINCT ON (mmsi)
                    mmsi, imo, to_bow, to_stern
                FROM public.ais_static
                WHERE mmsi = ANY(:mmsis)
                ORDER BY mmsi, {CURRENT_STATIC_ROW_ORDER}
                """
            ),
            con=engine,
            params={"mmsis": mmsis},
        )
    static_by_mmsi = {}
    if not static.empty:
        static_by_mmsi = {int(row["mmsi"]): row for _, row in static.iterrows()}

    by_imo, by_mmsi = load_ofac_indexes(engine)

    dark_mmsis: set[int] = set()
    if mmsis:
        dark = pd.read_sql(
            text(
                """
                SELECT DISTINCT mmsi
                FROM public.ais_vesselslowmoveactivities
                WHERE tsout IS NULL
                  AND mmsi = ANY(:mmsis)
                  AND tsstop IS NOT NULL
                  AND rowcount < 30
                  AND tscurrent IS NOT NULL
                  AND tscurrent <= now() - interval '30 minutes'
                """
            ),
            con=engine,
            params={"mmsis": mmsis},
        )
        if not dark.empty:
            dark_mmsis = set(dark["mmsi"].astype(int))

    illegal_mmsis: set[int] = set()
    if mmsis:
        stopped = pd.read_sql(
            text(
                """
                SELECT DISTINCT ON (mmsi)
                    mmsi, curlongitude, curlatitude
                FROM public.ais_vesselmovementactivities
                WHERE tsout IS NULL
                  AND mmsi = ANY(:mmsis)
                  AND curlongitude IS NOT NULL
                  AND curlatitude IS NOT NULL
                ORDER BY mmsi, ts DESC
                """
            ),
            con=engine,
            params={"mmsis": mmsis},
        )
        if not stopped.empty:
            classified = classify_illegal_anchoring(stopped)
            if not classified.empty and "mmsi" in classified.columns:
                illegal_mmsis = set(classified["mmsi"].astype(int))

    loiter = pd.DataFrame()
    spoof = pd.DataFrame()
    if mmsis:
        batch_start = observations["first_detected_at"].min()
        batch_end = observations["last_detected_at"].max()
        loiter = pd.read_sql(
            text(
                """
                SELECT mmsi, radius_m, path_m, net_m, started_at, ended_at
                FROM public.ais_vesselloiteractivity
                WHERE mmsi = ANY(:mmsis)
                  AND started_at <= :batch_end
                  AND COALESCE(ended_at, now()) >= :batch_start
                """
            ),
            con=engine,
            params={"mmsis": mmsis, "batch_start": batch_start, "batch_end": batch_end},
        )
        spoof = pd.read_sql(
            text(
                """
                SELECT mmsi, event_day
                FROM public.ais_positionanomaly
                WHERE mmsi = ANY(:mmsis)
                  AND event_day BETWEEN CAST(:batch_start AS date) AND CAST(:batch_end AS date)
                  AND reason NOT IN ('bad_position_data', 'duplicate_mmsi')
                """
            ),
            con=engine,
            params={"mmsis": mmsis, "batch_start": batch_start, "batch_end": batch_end},
        )

    rows = []
    for _, obs in observations.iterrows():
        obs_id = int(obs["id"])
        win_start = _utc(obs.get("first_detected_at")) or _utc(obs.get("last_detected_at"))
        win_end = _utc(obs.get("last_detected_at")) or win_start
        group = members[members["observation_id"] == obs_id] if not members.empty else members
        payloads = []
        sanctions = False
        dredge = False
        dark = False
        illegal = False
        loiter_hit = False
        swing_hit = False
        spoof_hit = False
        for _, member in group.iterrows():
            mmsi = int(member["mmsi"])
            info = static_by_mmsi.get(mmsi)
            imo = None if info is None or pd.isna(info.get("imo")) else info.get("imo")
            length = 0.0 if info is None else _length_m(info)
            sanction = match_vessel(imo, mmsi, by_imo, by_mmsi)
            project = None
            if win_start is not None and win_end is not None:
                project = matching_project(mmsi, imo, win_start, win_end)
            if sanction["sanctions_match"]:
                sanctions = True
            if project is not None:
                dredge = True
            ship_type = member.get("ship_type")
            if (
                mmsi in dark_mmsis
                and ship_type is not None
                and not pd.isna(ship_type)
                and 70 <= int(ship_type) < 90
            ):
                dark = True
            if mmsi in illegal_mmsis:
                illegal = True
            if not loiter.empty:
                mine = loiter[loiter["mmsi"].astype(int) == mmsi]
                for _, event in mine.iterrows():
                    if not _overlaps(event["started_at"], event["ended_at"], win_start, win_end):
                        continue
                    if _is_loiter(event, length):
                        loiter_hit = True
                    if _is_swing(event, length):
                        swing_hit = True
            if not spoof.empty and win_start is not None and win_end is not None:
                days = spoof[spoof["mmsi"].astype(int) == mmsi]
                for _, event in days.iterrows():
                    day = pd.Timestamp(event["event_day"]).date()
                    if win_start.date() <= day <= win_end.date():
                        spoof_hit = True
            payloads.append({
                "mmsi": mmsi,
                "imo": None if imo is None else str(imo),
                "shipName": None if pd.isna(member.get("ship_name")) else member.get("ship_name"),
                "shipType": None if pd.isna(member.get("ship_type")) else int(member.get("ship_type")),
                "shipTypeDesc": (
                    None if pd.isna(member.get("ship_type_desc")) else member.get("ship_type_desc")
                ),
                "sanctionsMatch": sanction["sanctions_match"],
                "matchConfidence": sanction["match_confidence"],
                "sanctionsList": sanction["sanctions_list"],
                "onDredgeLicence": project is not None,
                "dredgeProjectId": None if project is None else project["project_id"],
                "onBunkerList": False,
            })
        rows.append({
            "observation_id": obs_id,
            "cluster_signature": obs.get("cluster_signature"),
            "first_detected_at": _utc(obs.get("first_detected_at")),
            "last_detected_at": _utc(obs.get("last_detected_at")),
            "duration_seconds": None if pd.isna(obs.get("duration_seconds")) else float(obs.get("duration_seconds")),
            "suspicion_score": None if pd.isna(obs.get("suspicion_score")) else float(obs.get("suspicion_score")),
            "is_open": bool(obs.get("is_open")),
            "centroid_latitude": None if pd.isna(obs.get("centroid_latitude")) else float(obs.get("centroid_latitude")),
            "centroid_longitude": None if pd.isna(obs.get("centroid_longitude")) else float(obs.get("centroid_longitude")),
            "vessel_count": None if pd.isna(obs.get("vessel_count")) else int(obs.get("vessel_count")),
            "cargo_count": None if pd.isna(obs.get("cargo_count")) else int(obs.get("cargo_count")),
            "tanker_count": None if pd.isna(obs.get("tanker_count")) else int(obs.get("tanker_count")),
            "max_internal_distance_m": (
                None if pd.isna(obs.get("max_internal_distance_m")) else float(obs.get("max_internal_distance_m"))
            ),
            "in_anchorage": bool(obs.get("in_anchorage")),
            "anchorage_name": obs.get("anchorage_name"),
            "outside_anchorage": bool(obs.get("outside_anchorage")),
            "sanctions_match": sanctions,
            "dredge_licence_match": dredge,
            "dark_overlap": dark,
            "illegal_anchor_overlap": illegal,
            "loiter_overlap": loiter_hit,
            "anchor_swing_overlap": swing_hit,
            "spoof_overlap": spoof_hit,
            "members": json.dumps(payloads),
        })

    for row in rows:
        row["notify"] = _notifiable(row)
    with engine.begin() as conn:
        conn.execute(UPSERT, rows)
    logging.info("STS review: refreshed %s case(s)", len(rows))
    return len(rows)


def main() -> None:
    ensure_tables(get_engine())
    logging.info(
        "STS review started (interval=%ss, catchup=%ss, batch=%s)",
        LOOP_SECONDS, CATCHUP_PAUSE_SECONDS, BATCH_LIMIT,
    )
    while True:
        pause = LOOP_SECONDS
        try:
            written = run_once()
            if written >= BATCH_LIMIT:
                pause = CATCHUP_PAUSE_SECONDS
        except KeyboardInterrupt:
            logging.info("Stopping STS review.")
            return
        except Exception:
            logging.exception("STS review cycle failed")
        time.sleep(pause)


if __name__ == "__main__":
    main()
