#!/usr/bin/env python3
"""
MANTIS MCP server

What is MCP?
  Model Context Protocol — a standard way for AI apps (like Cursor)
  to call your tools. Cursor starts this script, then the AI can
  invoke the five tools below instead of writing curl by hand.

How it runs:
  Cursor launches:  python server.py
  Communication is over stdin/stdout (stdio). You usually do not
  run this yourself except for a quick smoke test.

Tools exposed:
  1. get_token
  2. get_polygons
  3. get_sts_activities
  4. get_illegal_anchoring
  5. get_dark_vessels
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from client import client

# Server name shown in Cursor's MCP list
mcp = FastMCP("mantis")


@mcp.tool()
def get_token(force_refresh: bool = False) -> dict[str, Any]:
    """
    Obtain (or refresh) a MANTIS API JWT access token.

    Call this first if you want to confirm login works.
    Other tools auto-login if needed.
    """
    return client.get_token(force_refresh=force_refresh)


@mcp.tool()
def get_polygons() -> dict[str, Any]:
    """
    Return all anchorage polygons plus the Restricted Limit polygon
    from GET /mantis/polygons.
    """
    return client.get_polygons()


@mcp.tool()
def get_sts_activities(
    min_suspicion_score: float | None = None,
    max_distance_m: float | None = None,
) -> dict[str, Any]:
    """
    Return active high-suspicion STS (ship-to-ship) pairs inside
    anchorage polygons from GET /mantis/sts-activities.

    Optional filters:
      min_suspicion_score — e.g. 4.5
      max_distance_m — e.g. 35
    """
    return client.get_sts_activities(
        min_suspicion_score=min_suspicion_score,
        max_distance_m=max_distance_m,
    )


@mcp.tool()
def get_illegal_anchoring() -> dict[str, Any]:
    """
    Return heuristic illegal-anchoring candidates
    from GET /mantis/illegal-anchoring
    (restricted / parent polygons; Excl carve-outs excluded).
    """
    return client.get_illegal_anchoring()


@mcp.tool()
def get_dark_vessels(include_coverage_exit: bool = True) -> dict[str, Any]:
    """
    Return suspected dark / AIS-transponder-off vessels
    from GET /mantis/darkvessels.

    Candidates are labeled with polygonName when inside a polygon;
    they are not dropped. Set include_coverage_exit=False for a
    tighter ops list (excludes possible_coverage_exit).
    """
    return client.get_dark_vessels(include_coverage_exit=include_coverage_exit)


if __name__ == "__main__":
    # mcp.run(transport="streamable-http", host="0.0.0.0", port=8000)

    
    # stdio transport — required for Cursor / Claude Desktop style MCP hosts
    mcp.run(transport="stdio")
