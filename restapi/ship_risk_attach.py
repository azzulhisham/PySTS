"""
Identity risk labels for existing MANTIS candidates (Phase 1).

Additive only: never changes sanctions fields, detector scores, filters, or sorts.
Source: stored ais_ship_risk_rating (lookback_months=24). Lookup IMO first, else MMSI.
Missing row → GREEN / 0 / "none". Cache load failure → same defaults; never raises to callers.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any
from urllib.parse import quote

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

from pg_engine import get_pg_engine
from sanctions import normalize_imo, normalize_mmsi

logger = logging.getLogger(__name__)

DEFAULT_LOOKBACK_MONTHS = 24
DEFAULT_RISK: dict[str, Any] = {
    "rating": "GREEN",
    "score": 0,
    "reason": "none",
}
RISK_CACHE_TTL_S = int(os.environ.get("mantis_risk_cache_ttl_s", "300"))

_risk_index_cache: tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]] | None = None
_risk_index_cache_at: float = 0.0
_risk_index_cache_lock = threading.Lock()


def clear_risk_index_cache() -> None:
    """Test helper: drop the in-process risk index cache."""
    global _risk_index_cache, _risk_index_cache_at
    with _risk_index_cache_lock:
        _risk_index_cache = None
        _risk_index_cache_at = 0.0


def _default_hit() -> dict[str, Any]:
    return dict(DEFAULT_RISK)


def _build_risk_indexes(engine: Engine) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    df = pd.read_sql(
        text(
            """
            SELECT ship_key, imo, mmsi, rating, score, factors->>'reason' AS reason
            FROM ais_ship_risk_rating
            WHERE lookback_months = :lb
            """
        ),
        con=engine,
        params={"lb": DEFAULT_LOOKBACK_MONTHS},
    )
    by_imo: dict[str, dict[str, Any]] = {}
    by_mmsi: dict[str, dict[str, Any]] = {}
    if df is None or df.empty:
        return by_imo, by_mmsi
    for _, row in df.iterrows():
        hit = {
            "rating": str(row.get("rating") or "GREEN"),
            "score": int(row.get("score") or 0),
            "reason": str(row.get("reason") or "none"),
        }
        ship_key = str(row.get("ship_key") or "")
        imo_key = normalize_imo(row.get("imo"))
        mmsi_key = normalize_mmsi(row.get("mmsi"))
        if ship_key.startswith("imo:") and imo_key:
            by_imo[imo_key] = hit
        elif ship_key.startswith("mmsi:") and mmsi_key:
            by_mmsi[mmsi_key] = hit
        elif imo_key:
            by_imo[imo_key] = hit
        elif mmsi_key:
            by_mmsi[mmsi_key] = hit
    logger.info(
        "Ship risk lookup: %s IMO keys, %s MMSI keys (lookback=%s)",
        len(by_imo),
        len(by_mmsi),
        DEFAULT_LOOKBACK_MONTHS,
    )
    return by_imo, by_mmsi


def load_risk_indexes(
    engine: Engine | None = None,
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    """In-process cache so Phase 1 endpoints do not re-read ratings on every attach."""
    global _risk_index_cache, _risk_index_cache_at
    now = time.monotonic()
    with _risk_index_cache_lock:
        if _risk_index_cache is not None and (now - _risk_index_cache_at) < RISK_CACHE_TTL_S:
            return _risk_index_cache

    engine = engine or get_pg_engine()
    try:
        by_imo, by_mmsi = _build_risk_indexes(engine)
    except Exception:
        logger.warning("Ship risk index load failed — using empty indexes (GREEN defaults)", exc_info=True)
        by_imo, by_mmsi = {}, {}

    with _risk_index_cache_lock:
        _risk_index_cache = (by_imo, by_mmsi)
        _risk_index_cache_at = time.monotonic()
    return by_imo, by_mmsi


def match_risk(
    imo: Any,
    mmsi: Any,
    by_imo: dict[str, dict[str, Any]],
    by_mmsi: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """IMO present → IMO lookup only. No IMO → MMSI lookup. Else GREEN defaults."""
    imo_key = normalize_imo(imo)
    if imo_key:
        hit = by_imo.get(imo_key)
        return dict(hit) if hit else _default_hit()
    mmsi_key = normalize_mmsi(mmsi)
    if mmsi_key:
        hit = by_mmsi.get(mmsi_key)
        return dict(hit) if hit else _default_hit()
    return _default_hit()


def attach_risk_ratings(
    df: pd.DataFrame,
    engine: Engine | None = None,
    *,
    imo_col: str = "imo",
    mmsi_col: str = "mmsi",
) -> pd.DataFrame:
    out = df.copy()
    if out.empty:
        out["risk_rating"] = pd.Series(dtype="object")
        out["risk_score"] = pd.Series(dtype="object")
        out["risk_reason"] = pd.Series(dtype="object")
        return out

    try:
        by_imo, by_mmsi = load_risk_indexes(engine)
        imo_series = (
            out[imo_col] if imo_col in out.columns else pd.Series([None] * len(out), index=out.index)
        )
        mmsi_series = (
            out[mmsi_col] if mmsi_col in out.columns else pd.Series([None] * len(out), index=out.index)
        )
        hits = [
            match_risk(imo, mmsi, by_imo, by_mmsi)
            for imo, mmsi in zip(imo_series.tolist(), mmsi_series.tolist())
        ]
    except Exception:
        logger.warning("attach_risk_ratings failed — defaulting all rows to GREEN", exc_info=True)
        hits = [_default_hit() for _ in range(len(out))]

    out["risk_rating"] = [h["rating"] for h in hits]
    out["risk_score"] = [h["score"] for h in hits]
    out["risk_reason"] = [h["reason"] for h in hits]
    return out


def attach_risk_pair_sides(
    pairs: pd.DataFrame,
    engine: Engine | None = None,
) -> pd.DataFrame:
    """Label vessel A and B; pair risk_red_count = number of RED sides (0–2)."""
    out = pairs.copy()
    if out.empty:
        out["risk_rating_a"] = pd.Series(dtype="object")
        out["risk_score_a"] = pd.Series(dtype="object")
        out["risk_reason_a"] = pd.Series(dtype="object")
        out["risk_rating_b"] = pd.Series(dtype="object")
        out["risk_score_b"] = pd.Series(dtype="object")
        out["risk_reason_b"] = pd.Series(dtype="object")
        out["risk_red_count"] = pd.Series(dtype="object")
        return out

    try:
        by_imo, by_mmsi = load_risk_indexes(engine)
        match_a = [
            match_risk(imo, mmsi, by_imo, by_mmsi)
            for imo, mmsi in zip(
                out["imo_a"].tolist() if "imo_a" in out.columns else [None] * len(out),
                out["mmsi_a"].tolist() if "mmsi_a" in out.columns else [None] * len(out),
            )
        ]
        match_b = [
            match_risk(imo, mmsi, by_imo, by_mmsi)
            for imo, mmsi in zip(
                out["imo_b"].tolist() if "imo_b" in out.columns else [None] * len(out),
                out["mmsi_b"].tolist() if "mmsi_b" in out.columns else [None] * len(out),
            )
        ]
    except Exception:
        logger.warning("attach_risk_pair_sides failed — defaulting all rows to GREEN", exc_info=True)
        match_a = [_default_hit() for _ in range(len(out))]
        match_b = [_default_hit() for _ in range(len(out))]

    out["risk_rating_a"] = [m["rating"] for m in match_a]
    out["risk_score_a"] = [m["score"] for m in match_a]
    out["risk_reason_a"] = [m["reason"] for m in match_a]
    out["risk_rating_b"] = [m["rating"] for m in match_b]
    out["risk_score_b"] = [m["score"] for m in match_b]
    out["risk_reason_b"] = [m["reason"] for m in match_b]
    out["risk_red_count"] = [
        int(a["rating"] == "RED") + int(b["rating"] == "RED") for a, b in zip(match_a, match_b)
    ]
    return out


def risk_payload_fields(row: Any, *, side: str | None = None) -> dict[str, Any]:
    """Emit riskRating / riskScore / riskReason only (append after sanctions fields)."""
    if side:
        rating = row.get(f"risk_rating_{side}") if hasattr(row, "get") else None
        score = row.get(f"risk_score_{side}") if hasattr(row, "get") else None
        reason = row.get(f"risk_reason_{side}") if hasattr(row, "get") else None
    else:
        rating = row.get("risk_rating") if hasattr(row, "get") else None
        score = row.get("risk_score") if hasattr(row, "get") else None
        reason = row.get("risk_reason") if hasattr(row, "get") else None

    if rating is None or (isinstance(rating, float) and pd.isna(rating)):
        rating = DEFAULT_RISK["rating"]
    if score is None or (isinstance(score, float) and pd.isna(score)):
        score = DEFAULT_RISK["score"]
    if reason is None or (isinstance(reason, float) and pd.isna(reason)):
        reason = DEFAULT_RISK["reason"]
    try:
        score_i = int(score)
    except (TypeError, ValueError):
        score_i = 0
    return {
        "riskRating": str(rating),
        "riskScore": score_i,
        "riskReason": str(reason),
    }


def risk_counts(df: pd.DataFrame, *, rating_col: str = "risk_rating") -> dict[str, int]:
    """Top-level RED / AMBER counts; safe on empty / missing column."""
    if df is None or getattr(df, "empty", True) or rating_col not in getattr(df, "columns", []):
        return {"risk_red_count": 0, "risk_amber_count": 0}
    series = df[rating_col].fillna("GREEN").astype(str)
    return {
        "risk_red_count": int((series == "RED").sum()),
        "risk_amber_count": int((series == "AMBER").sum()),
    }


def rated_ship_count(engine: Engine | None = None) -> int:
    """
    Rows in the cached ais_ship_risk_rating indexes (lookback 24).

    Counts IMO keys + MMSI keys (one entry per ship_key). Load failure → 0.
    """
    try:
        by_imo, by_mmsi = load_risk_indexes(engine)
        return int(len(by_imo) + len(by_mmsi))
    except Exception:
        logger.warning("rated_ship_count failed — returning 0", exc_info=True)
        return 0


# One indexed ais_static read per MMSI (latest row). Failure → None (MMSI-only risk).
_IMO_FOR_MMSI_SQL = text(
    """
    SELECT imo
    FROM public.ais_static
    WHERE mmsi = :mmsi
    ORDER BY ts DESC NULLS LAST, id DESC
    LIMIT 1
    """
)

RISK_REASON_HEADER_MAX_LEN = 200


def lookup_imo_for_mmsi(mmsi: Any, engine: Engine | None = None) -> str | None:
    """Cheap single-row IMO lookup for one MMSI. Never raises."""
    try:
        mmsi_i = int(mmsi)
    except (TypeError, ValueError):
        return None
    try:
        engine = engine or get_pg_engine()
        df = pd.read_sql(_IMO_FOR_MMSI_SQL, con=engine, params={"mmsi": mmsi_i})
        if df is None or df.empty:
            return None
        return normalize_imo(df.iloc[0].get("imo"))
    except Exception:
        logger.warning("lookup_imo_for_mmsi failed for mmsi=%s", mmsi, exc_info=True)
        return None


def risk_for_mmsi(
    mmsi: Any,
    engine: Engine | None = None,
    *,
    resolve_imo: bool = True,
) -> dict[str, Any]:
    """
    Identity risk for one ship (timeline / track). Uses cache; optional IMO lookup.

    Never raises — GREEN / 0 / "none" on any failure.
    """
    try:
        by_imo, by_mmsi = load_risk_indexes(engine)
        imo = lookup_imo_for_mmsi(mmsi, engine) if resolve_imo else None
        hit = match_risk(imo, mmsi, by_imo, by_mmsi)
        return {
            "rating": str(hit.get("rating") or DEFAULT_RISK["rating"]),
            "score": int(hit.get("score") or 0),
            "reason": str(hit.get("reason") or DEFAULT_RISK["reason"]),
            "imo": imo,
        }
    except Exception:
        logger.warning("risk_for_mmsi failed — GREEN defaults", exc_info=True)
        return {
            "rating": DEFAULT_RISK["rating"],
            "score": int(DEFAULT_RISK["score"]),
            "reason": DEFAULT_RISK["reason"],
            "imo": None,
        }


def risk_payload_for_mmsi(
    mmsi: Any,
    engine: Engine | None = None,
    *,
    resolve_imo: bool = True,
) -> dict[str, Any]:
    """CamelCase riskRating / riskScore / riskReason for JSON payloads."""
    hit = risk_for_mmsi(mmsi, engine, resolve_imo=resolve_imo)
    return {
        "riskRating": hit["rating"],
        "riskScore": hit["score"],
        "riskReason": hit["reason"],
    }


def encode_risk_reason_header(reason: Any) -> str:
    """Percent-encode reason for HTTP headers; truncate before encoding."""
    text = str(reason if reason is not None else DEFAULT_RISK["reason"])
    if len(text) > RISK_REASON_HEADER_MAX_LEN:
        text = text[: RISK_REASON_HEADER_MAX_LEN - 3] + "..."
    return quote(text, safe="")


def risk_http_headers(
    mmsi: Any,
    engine: Engine | None = None,
    *,
    resolve_imo: bool = True,
) -> dict[str, str]:
    """
    X-Risk-* headers for vessel-track (body stream unchanged).

    Includes Access-Control-Expose-Headers so browsers can read the values.
    """
    hit = risk_for_mmsi(mmsi, engine, resolve_imo=resolve_imo)
    return {
        "X-Risk-Rating": str(hit["rating"]),
        "X-Risk-Score": str(int(hit["score"])),
        "X-Risk-Reason": encode_risk_reason_header(hit["reason"]),
        "Access-Control-Expose-Headers": "X-Risk-Rating, X-Risk-Score, X-Risk-Reason",
    }


# Identity-risk JSON keys only — do not strip spoofing `riskLevel` on vessel-analysis events.
_IDENTITY_RISK_JSON_KEYS = frozenset(
    {
        "riskRating",
        "riskScore",
        "riskReason",
        "riskRedCount",
        "riskAmberCount",
        "riskRatingMax",
        "riskRatedShipCount",
    }
)


def strip_risk_keys(obj: Any) -> Any:
    """Deep-copy structure without identity-risk JSON keys (for golden tests)."""
    if isinstance(obj, dict):
        return {
            k: strip_risk_keys(v)
            for k, v in obj.items()
            if not (isinstance(k, str) and k in _IDENTITY_RISK_JSON_KEYS)
        }
    if isinstance(obj, list):
        return [strip_risk_keys(v) for v in obj]
    return obj
