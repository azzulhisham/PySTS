"""
MANTIS dashboard overview — summary counts for all detector modules in one call.

Runs Postgres modules (dark, STS, illegal) in one thread and spoofing (ClickHouse
+ PG static/OFAC) in another so wall-clock time is ~max(PG, CH) not PG+CH.
Optional in-process cache for repeat dashboard loads.
"""

from __future__ import annotations

import copy
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any

from dark_vessels import detect_dark_vessels
from illegal_anchoring import detect_illegal_anchoring
from pg_engine import get_pg_engine
from spoofing import detect_spoofing
from sts_detection import detect_sts_in_anchorages

logger = logging.getLogger(__name__)

OVERVIEW_CACHE_TTL_S = int(os.environ.get("mantis_overview_cache_ttl_s", "120"))


def _reason_count(by_reason: dict[str, int] | None, key: str) -> int:
    if not by_reason:
        return 0
    return int(by_reason.get(key, 0))


def _conf_count(by_conf: dict[str, int] | None, key: str) -> int:
    if not by_conf:
        return 0
    return int(by_conf.get(key, 0))


def _reason_contains(by_reason: dict[str, int] | None, fragment: str) -> int:
    if not by_reason:
        return 0
    return int(sum(cnt for key, cnt in by_reason.items() if fragment in key))


def _dark_vessels_summary(result: dict[str, Any]) -> dict[str, Any]:
    by_reason = result.get("by_reason") or {}
    by_conf = result.get("by_confidence") or {}
    return {
        "ruleVersion": result["rule_version"],
        "shipTypeFilter": result["ship_type_filter"],
        "minSilenceMinutes": result["min_silence_minutes"],
        "coverageExitDays": result["coverage_exit_days"],
        "includeCoverageExit": result["include_coverage_exit"],
        "candidateCount": result["candidate_count"],
        "byReason": by_reason,
        "byConfidence": by_conf,
        "suspectedDarkCount": _reason_count(by_reason, "suspected_dark_after_slowdown"),
        "coverageExitCount": _reason_count(by_reason, "possible_coverage_exit"),
        "lowEvidenceAisGapCount": _reason_count(by_reason, "low_evidence_ais_gap"),
        "highConfidenceCount": _conf_count(by_conf, "high"),
        "mediumConfidenceCount": _conf_count(by_conf, "medium"),
        "lowConfidenceCount": _conf_count(by_conf, "low"),
        "sanctionsMatchCount": result["sanctions_match_count"],
    }


def _sts_summary(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "minSuspicionScore": result["min_suspicion_score"],
        "maxDistanceM": result["max_distance_m"],
        "openHighScoreCount": result["open_high_score_count"],
        "inAnchorageClusterCount": result["in_anchorage_cluster_count"],
        "pairCount": result["pair_count"],
        "pairedVesselCount": result["paired_vessel_count"],
        "sanctionsMatchPairCount": result["sanctions_match_pair_count"],
        "sanctionsMatchVesselCount": result["sanctions_match_vessel_count"],
    }


def _illegal_anchoring_summary(result: dict[str, Any]) -> dict[str, Any]:
    by_reason = result.get("by_reason") or {}
    return {
        "ruleVersion": result["rule_version"],
        "shipTypeFilter": result["ship_type_filter"],
        "stoppedCandidateCount": result["stopped_candidate_count"],
        "illegalCount": result["illegal_count"],
        "byReason": by_reason,
        "inRestrictedZoneCount": _reason_contains(by_reason, "in_restricted_zone"),
        "inWatchPolygonCount": _reason_contains(by_reason, "in_watch_polygon"),
        "watchPolygonCount": result["watch_polygon_count"],
        "portLimitPolygonCount": result["port_limit_polygon_count"],
        "sanctionsMatchCount": result["sanctions_match_count"],
    }


def _spoofing_summary(result: dict[str, Any]) -> dict[str, Any]:
    by_reason = result.get("by_reason") or {}
    range_meta = result.get("range_meta") or {}
    return {
        "ruleVersion": result["rule_version"],
        "shipTypeFilter": result["ship_type_filter"],
        "dedupeRule": result["dedupe_rule"],
        "phase": result["phase"],
        "detector": result["detector"],
        "dateFrom": result["date_from"],
        "dateTo": result["date_to"],
        "fromOmitted": range_meta.get("fromOmitted"),
        "toOmitted": range_meta.get("toOmitted"),
        "rangeCapped": range_meta.get("rangeCapped"),
        "maxRangeDays": range_meta.get("maxRangeDays"),
        "rawHitCount": result["raw_hit_count"],
        "filteredHitCount": result["filtered_hit_count"],
        "anomalyCount": result["anomaly_count"],
        "byReason": by_reason,
        "teleportCount": _reason_count(by_reason, "teleport"),
        "highSpeedCount": _reason_count(by_reason, "high_speed"),
        "sanctionsMatchCount": result["sanctions_match_count"],
    }


_overview_cache: dict[tuple[Any, ...], tuple[float, dict[str, Any]]] = {}
_overview_cache_lock = threading.Lock()


def _overview_cache_key(
    min_suspicion_score: float,
    include_coverage_exit: bool,
    spoofing_from: datetime | str | None,
    spoofing_to: datetime | str | None,
    include_spoofing: bool,
) -> tuple[Any, ...]:
    return (
        min_suspicion_score,
        include_coverage_exit,
        include_spoofing,
        str(spoofing_from) if spoofing_from is not None else None,
        str(spoofing_to) if spoofing_to is not None else None,
    )


def _run_postgres_modules(
    engine,
    *,
    min_suspicion_score: float,
    include_coverage_exit: bool,
) -> tuple[dict[str, float], dict[str, str], dict[str, Any]]:
    timing_ms: dict[str, float] = {}
    errors: dict[str, str] = {}
    sections: dict[str, Any] = {}

    def _run(key: str, fn):
        t0 = time.perf_counter()
        try:
            result = fn()
            timing_ms[key] = round((time.perf_counter() - t0) * 1000, 1)
            return result
        except Exception as exc:
            timing_ms[key] = round((time.perf_counter() - t0) * 1000, 1)
            errors[key] = str(exc)
            logger.exception("[mantis_overview] %s failed", key)
            return None

    dark = _run(
        "darkVessels",
        lambda: detect_dark_vessels(
            engine,
            include_coverage_exit=include_coverage_exit,
            summary_only=True,
        ),
    )
    if dark is not None:
        sections["darkVessels"] = _dark_vessels_summary(dark)

    sts = _run(
        "stsActivities",
        lambda: detect_sts_in_anchorages(
            engine,
            min_suspicion_score=min_suspicion_score,
            summary_only=True,
        ),
    )
    if sts is not None:
        sections["stsActivities"] = _sts_summary(sts)

    illegal = _run(
        "illegalAnchoring",
        lambda: detect_illegal_anchoring(engine, summary_only=True),
    )
    if illegal is not None:
        sections["illegalAnchoring"] = _illegal_anchoring_summary(illegal)

    return timing_ms, errors, sections


def _run_spoofing_module(
    engine,
    *,
    spoofing_from: datetime | str | None,
    spoofing_to: datetime | str | None,
) -> tuple[dict[str, float], dict[str, str], dict[str, Any] | None]:
    timing_ms: dict[str, float] = {}
    errors: dict[str, str] = {}
    t0 = time.perf_counter()
    try:
        spoof = detect_spoofing(
            spoofing_from,
            spoofing_to,
            engine=engine,
            summary_only=True,
        )
        timing_ms["spoofing"] = round((time.perf_counter() - t0) * 1000, 1)
        return timing_ms, errors, _spoofing_summary(spoof)
    except Exception as exc:
        timing_ms["spoofing"] = round((time.perf_counter() - t0) * 1000, 1)
        errors["spoofing"] = str(exc)
        logger.exception("[mantis_overview] spoofing failed")
        return timing_ms, errors, None


def detect_mantis_overview(
    *,
    min_suspicion_score: float = 4.5,
    include_coverage_exit: bool = True,
    spoofing_from: datetime | str | None = None,
    spoofing_to: datetime | str | None = None,
    include_spoofing: bool = True,
) -> dict[str, Any]:
    cache_key = _overview_cache_key(
        min_suspicion_score,
        include_coverage_exit,
        spoofing_from,
        spoofing_to,
        include_spoofing,
    )
    if OVERVIEW_CACHE_TTL_S > 0:
        with _overview_cache_lock:
            cached = _overview_cache.get(cache_key)
            if cached is not None:
                age = time.monotonic() - cached[0]
                if age < OVERVIEW_CACHE_TTL_S:
                    payload = copy.deepcopy(cached[1])
                    payload["cached"] = True
                    payload["cacheAgeSeconds"] = round(age, 1)
                    payload["cacheTtlSeconds"] = OVERVIEW_CACHE_TTL_S
                    return payload

    engine = get_pg_engine()
    generated_at = datetime.now(timezone.utc).isoformat()
    timing_ms: dict[str, float] = {}
    errors: dict[str, str] = {}

    out: dict[str, Any] = {
        "generatedAt": generated_at,
        "darkVessels": None,
        "stsActivities": None,
        "illegalAnchoring": None,
        "spoofing": None,
        "timingMs": timing_ms,
        "errors": errors,
        "cached": False,
        "cacheTtlSeconds": OVERVIEW_CACHE_TTL_S,
    }

    wall_t0 = time.perf_counter()

    if include_spoofing:
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="mantis-overview") as pool:
            fut_pg = pool.submit(
                _run_postgres_modules,
                engine,
                min_suspicion_score=min_suspicion_score,
                include_coverage_exit=include_coverage_exit,
            )
            fut_ch = pool.submit(
                _run_spoofing_module,
                engine,
                spoofing_from=spoofing_from,
                spoofing_to=spoofing_to,
            )
            pg_timing, pg_errors, pg_sections = fut_pg.result()
            ch_timing, ch_errors, spoof_section = fut_ch.result()
        timing_ms.update(pg_timing)
        timing_ms.update(ch_timing)
        errors.update(pg_errors)
        errors.update(ch_errors)
        out.update(pg_sections)
        out["spoofing"] = spoof_section
    else:
        pg_timing, pg_errors, pg_sections = _run_postgres_modules(
            engine,
            min_suspicion_score=min_suspicion_score,
            include_coverage_exit=include_coverage_exit,
        )
        timing_ms.update(pg_timing)
        errors.update(pg_errors)
        out.update(pg_sections)
        timing_ms["spoofing"] = 0.0

    timing_ms["total"] = round((time.perf_counter() - wall_t0) * 1000, 1)
    out["partial"] = bool(errors)

    if OVERVIEW_CACHE_TTL_S > 0 and not errors:
        with _overview_cache_lock:
            _overview_cache[cache_key] = (time.monotonic(), copy.deepcopy(out))

    return out
