"""
Cap large JSON lists when Try it out is run from Swagger UI.

Real clients (curl, frontend, MCP) always receive the full payload.
"""

from __future__ import annotations

from typing import Any

SWAGGER_SAMPLE_LIMIT = 20
SWAGGER_SAMPLE_MESSAGE = (
    "Swagger UI returns 20 sample rows so the page does not lag. "
    "Call this endpoint outside Swagger (curl, frontend, MCP) for the full result."
)


def is_swagger_referer(referer: str | None) -> bool:
    return "/swagger" in (referer or "").lower()


def apply_swagger_sample(
    payload: Any,
    list_keys: list[str],
    *,
    from_swagger: bool,
    wrap_list_as: str | None = None,
) -> Any:
    """
    If from_swagger and a listed array is longer than 20, keep the first 20
    and set sample metadata. Bare lists (polygons) stay a list for non-Swagger
    clients; Swagger gets an object wrapper so the sample message can be shown.
    """
    if isinstance(payload, list):
        if not from_swagger:
            return payload
        key = wrap_list_as or (list_keys[0] if list_keys else "items")
        payload = {key: payload}
        list_keys = [key]

    if not isinstance(payload, dict):
        return payload

    out = dict(payload)
    out["fromSwagger"] = from_swagger
    totals: dict[str, int] = {}
    sampled = False

    for key in list_keys:
        items = out.get(key)
        if not isinstance(items, list):
            continue
        totals[key] = len(items)
        if from_swagger and len(items) > SWAGGER_SAMPLE_LIMIT:
            out[key] = items[:SWAGGER_SAMPLE_LIMIT]
            sampled = True

    out["sample"] = sampled
    if sampled:
        out["sampleLimit"] = SWAGGER_SAMPLE_LIMIT
        out["message"] = SWAGGER_SAMPLE_MESSAGE
        if len(list_keys) == 1:
            key = list_keys[0]
            out["totalCount"] = totals.get(key, 0)
            out["returnedCount"] = len(out.get(key) or [])
        else:
            out["totalCounts"] = totals
            out["returnedCounts"] = {
                key: len(out.get(key) or [])
                for key in list_keys
                if isinstance(out.get(key), list)
            }
    else:
        out["sampleLimit"] = None
        out["message"] = None
        if len(list_keys) == 1 and isinstance(out.get(list_keys[0]), list):
            n = len(out[list_keys[0]])
            out.setdefault("totalCount", n)
            out.setdefault("returnedCount", n)

    return out
