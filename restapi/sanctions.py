"""
OFAC identity labels for existing MANTIS candidates.

Not a fourth detector. Does not change AIS suspicion_score / dark confidence.
Join is IMO first (confirmed), MMSI only when the candidate has no IMO (possible).
Name matching is not used. Unmatched vessels are kept.
"""

from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import quote

import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)

IMO_DIGITS = re.compile(r"(\d{7})")
MMSI_DIGITS = re.compile(r"(\d{8,9})")

NO_MATCH = {
    "sanctions_match": False,
    "match_confidence": "none",
    "sanctions_list": None,
}

CONFIDENCE_RANK = {"confirmed": 0, "possible": 1, "none": 2}

SWAGGER_SAMPLE_LIMIT = 20
SWAGGER_SAMPLE_MESSAGE = (
    "Swagger UI returns 20 sample rows so the page does not lag. "
    "Call this endpoint outside Swagger (curl, frontend, MCP) for the full list, "
    "or pass imo / mmsi to look up a vessel."
)

pswd = "m4r1t1m3"
DATABASE_URL = (
    f"postgresql://postgresadmin:{quote(pswd)}"
    f"@marineai2.cxwk8yige5f2.ap-southeast-5.rds.amazonaws.com:5432/pnav"
)

OFAC_VESSEL_TABLES = (
    ("ofac_sdn_entry", "SDN"),
    ("ofac_cons_entry", "CONS"),
)


def normalize_imo(value: Any) -> str | None:
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
    match = IMO_DIGITS.search(text_value)
    return match.group(1) if match else None


def normalize_mmsi(value: Any) -> str | None:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, float):
        value = int(value)
    text_value = str(value).strip()
    if not text_value or text_value.lower() in {"nan", "none", "null"}:
        return None
    match = MMSI_DIGITS.search(text_value)
    return match.group(1) if match else None


def match_vessel(
    imo: Any,
    mmsi: Any,
    by_imo: dict[str, dict[str, Any]],
    by_mmsi: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """
    IMO present → IMO lookup only (confirmed or none).
    No IMO → MMSI lookup (possible or none).
    """
    imo_key = normalize_imo(imo)
    if imo_key:
        hit = by_imo.get(imo_key)
        if hit:
            return {
                "sanctions_match": True,
                "match_confidence": "confirmed",
                "sanctions_list": hit.get("list_name"),
            }
        return dict(NO_MATCH)

    mmsi_key = normalize_mmsi(mmsi)
    if mmsi_key:
        hit = by_mmsi.get(mmsi_key)
        if hit:
            return {
                "sanctions_match": True,
                "match_confidence": "possible",
                "sanctions_list": hit.get("list_name"),
            }
    return dict(NO_MATCH)


def _view_exists(engine: Engine, view_name: str) -> bool:
    with engine.connect() as conn:
        found = conn.execute(text("SELECT to_regclass(:name)"), {"name": f"public.{view_name}"}).scalar()
    return found is not None


def _load_view(engine: Engine, view_name: str) -> pd.DataFrame:
    if not _view_exists(engine, view_name):
        logger.warning("OFAC view %s is missing — sanctions labels will be none", view_name)
        return pd.DataFrame()
    df = pd.read_sql(
        f"""
        SELECT uid, vessel_name, imo, mmsi
        FROM {view_name}
        WHERE imo IS NOT NULL OR mmsi IS NOT NULL
        """,
        con=engine,
    )
    if df.empty:
        return df
    df["list_name"] = "CONS" if "cons" in view_name else "SDN"
    return df


def load_ofac_indexes(engine: Engine) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    """SDN overwrites CONS on the same IMO/MMSI so SDN wins."""
    frames = [
        _load_view(engine, "ofac_cons_vessel"),
        _load_view(engine, "ofac_sdn_vessel"),
    ]
    by_imo: dict[str, dict[str, Any]] = {}
    by_mmsi: dict[str, dict[str, Any]] = {}
    for df in frames:
        if df.empty:
            continue
        for _, row in df.iterrows():
            rec = {
                "list_name": row.get("list_name") or "SDN",
                "uid": row.get("uid"),
                "vessel_name": row.get("vessel_name"),
            }
            imo_key = normalize_imo(row.get("imo"))
            if imo_key:
                by_imo[imo_key] = rec
            mmsi_key = normalize_mmsi(row.get("mmsi"))
            if mmsi_key:
                by_mmsi[mmsi_key] = rec
    logger.info("OFAC lookup: %s IMO keys, %s MMSI keys", len(by_imo), len(by_mmsi))
    return by_imo, by_mmsi


def attach_sanctions(
    df: pd.DataFrame,
    engine: Engine,
    *,
    imo_col: str = "imo",
    mmsi_col: str = "mmsi",
) -> pd.DataFrame:
    out = df.copy()
    if out.empty:
        out["sanctions_match"] = pd.Series(dtype="bool")
        out["match_confidence"] = pd.Series(dtype="object")
        out["sanctions_list"] = pd.Series(dtype="object")
        return out

    by_imo, by_mmsi = load_ofac_indexes(engine)
    imo_series = out[imo_col] if imo_col in out.columns else pd.Series([None] * len(out), index=out.index)
    mmsi_series = out[mmsi_col] if mmsi_col in out.columns else pd.Series([None] * len(out), index=out.index)

    matches = [
        match_vessel(imo, mmsi, by_imo, by_mmsi)
        for imo, mmsi in zip(imo_series.tolist(), mmsi_series.tolist())
    ]
    out["sanctions_match"] = [m["sanctions_match"] for m in matches]
    out["match_confidence"] = [m["match_confidence"] for m in matches]
    out["sanctions_list"] = [m["sanctions_list"] for m in matches]
    return out


def attach_sanctions_pair_sides(
    pairs: pd.DataFrame,
    engine: Engine,
) -> pd.DataFrame:
    """Label vessel A and B on an STS pair frame; pair match if either side matches."""
    out = pairs.copy()
    if out.empty:
        out["sanctions_match"] = pd.Series(dtype="bool")
        out["match_confidence"] = pd.Series(dtype="object")
        out["sanctions_list"] = pd.Series(dtype="object")
        out["sanctions_match_a"] = pd.Series(dtype="bool")
        out["match_confidence_a"] = pd.Series(dtype="object")
        out["sanctions_list_a"] = pd.Series(dtype="object")
        out["sanctions_match_b"] = pd.Series(dtype="bool")
        out["match_confidence_b"] = pd.Series(dtype="object")
        out["sanctions_list_b"] = pd.Series(dtype="object")
        return out

    by_imo, by_mmsi = load_ofac_indexes(engine)
    match_a = [
        match_vessel(imo, mmsi, by_imo, by_mmsi)
        for imo, mmsi in zip(
            out["imo_a"].tolist() if "imo_a" in out.columns else [None] * len(out),
            out["mmsi_a"].tolist(),
        )
    ]
    match_b = [
        match_vessel(imo, mmsi, by_imo, by_mmsi)
        for imo, mmsi in zip(
            out["imo_b"].tolist() if "imo_b" in out.columns else [None] * len(out),
            out["mmsi_b"].tolist(),
        )
    ]
    out["sanctions_match_a"] = [m["sanctions_match"] for m in match_a]
    out["match_confidence_a"] = [m["match_confidence"] for m in match_a]
    out["sanctions_list_a"] = [m["sanctions_list"] for m in match_a]
    out["sanctions_match_b"] = [m["sanctions_match"] for m in match_b]
    out["match_confidence_b"] = [m["match_confidence"] for m in match_b]
    out["sanctions_list_b"] = [m["sanctions_list"] for m in match_b]
    out["sanctions_match"] = [
        bool(a["sanctions_match"] or b["sanctions_match"]) for a, b in zip(match_a, match_b)
    ]
    out["match_confidence"] = [
        _best_confidence(a["match_confidence"], b["match_confidence"])
        for a, b in zip(match_a, match_b)
    ]
    out["sanctions_list"] = [
        a["sanctions_list"] or b["sanctions_list"] for a, b in zip(match_a, match_b)
    ]
    return out


def _best_confidence(a: str, b: str) -> str:
    return a if CONFIDENCE_RANK.get(a, 2) <= CONFIDENCE_RANK.get(b, 2) else b


def sort_listed_first(
    df: pd.DataFrame,
    extra_sort: list[str] | None = None,
    extra_ascending: list[bool] | None = None,
) -> pd.DataFrame:
    """Stable: confirmed, then possible, then none; extra_sort keeps ops order inside a rank."""
    if df.empty or "match_confidence" not in df.columns:
        return df
    out = df.copy()
    out["_srank"] = out["match_confidence"].map(lambda x: CONFIDENCE_RANK.get(x, 2))
    extra_sort = extra_sort or []
    cols = ["_srank"] + extra_sort
    ascending = [True] + list(extra_ascending or ([True] * len(extra_sort)))
    out = out.sort_values(cols, ascending=ascending, kind="stable").drop(columns=["_srank"])
    return out.reset_index(drop=True)


def payload_fields(row: Any, *, imo_col: str = "imo", side: str | None = None) -> dict[str, Any]:
    if side:
        imo_col = f"imo_{side}"
        match_col = f"sanctions_match_{side}"
        conf_col = f"match_confidence_{side}"
        list_col = f"sanctions_list_{side}"
    else:
        match_col = "sanctions_match"
        conf_col = "match_confidence"
        list_col = "sanctions_list"

    imo_raw = row.get(imo_col) if hasattr(row, "get") else None
    imo = normalize_imo(imo_raw)
    match = bool(row.get(match_col)) if hasattr(row, "get") else False
    conf = row.get(conf_col) if hasattr(row, "get") else "none"
    if conf is None or (isinstance(conf, float) and pd.isna(conf)):
        conf = "none"
    slist = row.get(list_col) if hasattr(row, "get") else None
    if slist is not None and isinstance(slist, float) and pd.isna(slist):
        slist = None
    return {
        "imo": imo,
        "sanctionsMatch": match,
        "matchConfidence": str(conf),
        "sanctionsList": slist if match else None,
    }


def get_pg_engine() -> Engine:
    return create_engine(
        DATABASE_URL,
        pool_size=5,
        max_overflow=10,
        pool_timeout=30,
    )


def _table_exists(engine: Engine, table_name: str) -> bool:
    with engine.connect() as conn:
        found = conn.execute(text("SELECT to_regclass(:name)"), {"name": f"public.{table_name}"}).scalar()
    return found is not None


def _json_list(value: Any) -> list[str] | None:
    if value is None:
        return None
    if isinstance(value, str):
        return [value] if value.strip() else None
    if isinstance(value, (list, tuple, set)):
        items = [str(v) for v in value if v is not None and str(v).strip()]
        return items or None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    try:
        items = [str(v) for v in list(value) if v is not None and str(v).strip()]
    except TypeError:
        return None
    return items or None


def _load_ofac_vessel_table(engine: Engine, table_name: str, fallback_list: str) -> pd.DataFrame:
    if not _table_exists(engine, table_name):
        logger.warning("OFAC table %s is missing — skipped in sanctions list", table_name)
        return pd.DataFrame()
    df = pd.read_sql(
        f"""
        SELECT
            uid,
            last_name AS vessel_name,
            vessel_call_sign,
            vessel_type,
            vessel_flag,
            vessel_owner,
            imo,
            mmsi,
            programs,
            remarks,
            publish_date,
            list_name
        FROM {table_name}
        WHERE sdn_type = 'Vessel'
        """,
        con=engine,
    )
    if df.empty:
        return df
    if "list_name" not in df.columns:
        df["list_name"] = fallback_list
    else:
        df["list_name"] = df["list_name"].fillna(fallback_list)
    return df


def load_ofac_vessel_list(engine: Engine | None = None) -> pd.DataFrame:
    engine = engine or get_pg_engine()
    frames = [
        _load_ofac_vessel_table(engine, table, list_name)
        for table, list_name in OFAC_VESSEL_TABLES
    ]
    frames = [f for f in frames if not f.empty]
    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames, ignore_index=True)
    return out.sort_values(["list_name", "vessel_name", "uid"], kind="stable").reset_index(drop=True)


def _filter_ofac_vessels(df: pd.DataFrame, imo: Any, mmsi: Any) -> pd.DataFrame:
    imo_key = normalize_imo(imo)
    mmsi_key = normalize_mmsi(mmsi)
    if not imo_key and not mmsi_key:
        return df
    if df.empty:
        return df
    imo_norm = df["imo"].map(normalize_imo) if "imo" in df.columns else pd.Series([None] * len(df))
    mmsi_norm = df["mmsi"].map(normalize_mmsi) if "mmsi" in df.columns else pd.Series([None] * len(df))
    mask = pd.Series(False, index=df.index)
    if imo_key:
        mask = mask | (imo_norm == imo_key)
    if mmsi_key:
        mask = mask | (mmsi_norm == mmsi_key)
    return df.loc[mask].reset_index(drop=True)


def ofac_vessels_to_payload(df: pd.DataFrame) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    if df.empty:
        return records
    for _, row in df.iterrows():
        uid = row.get("uid")
        records.append(
            {
                "uid": int(uid) if uid is not None and pd.notna(uid) else None,
                "listName": None if pd.isna(row.get("list_name")) else row.get("list_name"),
                "vesselName": None if pd.isna(row.get("vessel_name")) else row.get("vessel_name"),
                "imo": normalize_imo(row.get("imo")),
                "mmsi": normalize_mmsi(row.get("mmsi")),
                "callSign": None if pd.isna(row.get("vessel_call_sign")) else row.get("vessel_call_sign"),
                "vesselType": None if pd.isna(row.get("vessel_type")) else row.get("vessel_type"),
                "vesselFlag": None if pd.isna(row.get("vessel_flag")) else row.get("vessel_flag"),
                "vesselOwner": None if pd.isna(row.get("vessel_owner")) else row.get("vessel_owner"),
                "programs": _json_list(row.get("programs")),
                "remarks": None if pd.isna(row.get("remarks")) else row.get("remarks"),
                "publishDate": None if pd.isna(row.get("publish_date")) else str(row.get("publish_date")),
            }
        )
    return records


def query_ofac_vessels(
    *,
    imo: Any = None,
    mmsi: Any = None,
    sample_limit: int | None = None,
    engine: Engine | None = None,
) -> dict[str, Any]:
    """
    OFAC vessel rows for the sanctions-list endpoint.

    If sample_limit is set and the filtered result is larger, only that many
    rows are returned (Swagger UI). Full list for curl / frontend / MCP.
    """
    engine = engine or get_pg_engine()
    df = _filter_ofac_vessels(load_ofac_vessel_list(engine), imo, mmsi)
    total = int(len(df))
    sampled = False
    if sample_limit is not None and total > sample_limit:
        df = df.head(int(sample_limit)).reset_index(drop=True)
        sampled = True
    return {
        "imo": normalize_imo(imo),
        "mmsi": normalize_mmsi(mmsi),
        "total_count": total,
        "returned_count": int(len(df)),
        "sample": sampled,
        "sample_limit": int(sample_limit) if sampled else None,
        "message": SWAGGER_SAMPLE_MESSAGE if sampled else None,
        "vessels": ofac_vessels_to_payload(df),
    }
