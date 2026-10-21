"""
Port / terminal areas used to suppress illegal-anchoring and loitering hits
inside harbour footprints (same 50 km² rule as PyVAM).

Loaded once from data/polygons/src/{ports,terminals}.geojson and cached in
memory. Spatial point tests for API/backend use DuckDB (vectorised); the
startup build uses Shapely for area and containment.
"""

from __future__ import annotations

import json
import logging
import math
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd
from shapely.geometry import mapping, shape
from shapely.ops import unary_union

from duckdb_spatial import spatial_df
from polygons import anchorage_areas, is_excl_name

LOG = logging.getLogger(__name__)

MAX_PORT_KM2 = 50.0
CONTAIN_FRAC = 0.95

def _default_poly_dir() -> Path:
    """Repo: <root>/restapi/port_areas.py → <root>/data/...; flat deploy: beside this file."""
    here = Path(__file__).resolve().parent
    for root in (here.parent, here):
        candidate = root / "data" / "polygons" / "src"
        if (candidate / "ports.geojson").is_file():
            return candidate
    return here.parent / "data" / "polygons" / "src"


# Resolved at build time via _default_poly_dir() so deploy order (code then data) is safe.
DEFAULT_POLY_DIR = Path(__file__).resolve().parents[1] / "data" / "polygons" / "src"

_LOCK = threading.Lock()
_CACHE: "PortAreaIndex | None" = None


@dataclass(frozen=True)
class PortArea:
    port_name: str
    terminal_name: str | None
    geojson: str
    area_km2: float


@dataclass
class PortAreaIndex:
    areas: list[PortArea]
    ports_loaded: int
    terminals_loaded: int
    ports_skipped: list[str]
    poly_dir: Path


def area_km2(geom) -> float:
    """Approximate geodesic area from a lon/lat geometry (PyVAM formula)."""
    if geom is None or geom.is_empty:
        return 0.0
    lat = float(geom.centroid.y)
    kx = 111.32 * max(math.cos(math.radians(lat)), 0.2)
    ky = 110.574
    return float(geom.area) * kx * ky


def port_test_geometry(port_geom, terminal_geoms: list, *, max_km2: float = MAX_PORT_KM2):
    """Port outline when ≤ max_km2; otherwise the union of contained terminals."""
    if port_geom is None or port_geom.is_empty:
        geoms = [g for g in terminal_geoms if g is not None and not g.is_empty]
        return unary_union(geoms) if geoms else None
    if area_km2(port_geom) > max_km2:
        geoms = [g for g in terminal_geoms if g is not None and not g.is_empty]
        return unary_union(geoms) if geoms else None
    return port_geom


def _read_features(path: Path, kind: str) -> list[dict[str, Any]]:
    if not path.is_file():
        LOG.warning("port_areas: missing %s (%s)", kind, path)
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    out: list[dict[str, Any]] = []
    for feat in data.get("features") or []:
        props = feat.get("properties") or {}
        geom_raw = feat.get("geometry")
        if not geom_raw:
            continue
        try:
            geom = shape(geom_raw)
        except Exception:
            continue
        if geom.is_empty:
            continue
        name = props.get("name") or props.get("Name") or ""
        poly_id = str(props.get("id") or props.get("Id") or feat.get("id") or "")
        out.append({"kind": kind, "poly_id": poly_id, "name": name, "geom": geom})
    return out


def _terminals_inside(port_geom, terminals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Terminals whose area is at least CONTAIN_FRAC inside the port outline."""
    if port_geom is None or port_geom.is_empty:
        return []
    hits: list[dict[str, Any]] = []
    for term in terminals:
        child = term["geom"]
        if child is None or child.is_empty or child.area <= 0:
            continue
        try:
            overlap = child.intersection(port_geom).area / child.area
        except Exception:
            continue
        if overlap >= CONTAIN_FRAC:
            hits.append(term)
    return hits


def _geom_to_geojson(geom) -> str:
    return json.dumps(mapping(geom))


def build_port_areas(poly_dir: Path | None = None) -> PortAreaIndex:
    directory = Path(poly_dir) if poly_dir is not None else _default_poly_dir()
    ports = _read_features(directory / "ports.geojson", "port")
    terminals = _read_features(directory / "terminals.geojson", "terminal")
    areas: list[PortArea] = []
    skipped: list[str] = []

    for port in ports:
        port_name = port["name"] or f"port:{port['poly_id']}"
        children = _terminals_inside(port["geom"], terminals)
        child_geoms = [c["geom"] for c in children]
        used = port_test_geometry(port["geom"], child_geoms)
        if used is None or used.is_empty:
            skipped.append(port_name)
            continue

        port_km2 = area_km2(port["geom"])
        if port_km2 > MAX_PORT_KM2:
            # One area per terminal so the payload can carry a terminal name.
            for child in children:
                areas.append(
                    PortArea(
                        port_name=port_name,
                        terminal_name=child["name"] or None,
                        geojson=_geom_to_geojson(child["geom"]),
                        area_km2=area_km2(child["geom"]),
                    )
                )
        else:
            areas.append(
                PortArea(
                    port_name=port_name,
                    terminal_name=None,
                    geojson=_geom_to_geojson(used),
                    area_km2=area_km2(used),
                )
            )

    LOG.info(
        "polygons loaded ports=%d terminals=%d; port areas built=%d; ports skipped=%d",
        len(ports),
        len(terminals),
        len(areas),
        len(skipped),
    )
    if skipped:
        LOG.info("skipped names (first 20): %s", ", ".join(skipped[:20]))
    return PortAreaIndex(
        areas=areas,
        ports_loaded=len(ports),
        terminals_loaded=len(terminals),
        ports_skipped=skipped,
        poly_dir=directory,
    )


def get_port_area_index(poly_dir: Path | None = None) -> PortAreaIndex:
    global _CACHE
    if _CACHE is not None and poly_dir is None:
        return _CACHE
    with _LOCK:
        if _CACHE is not None and poly_dir is None:
            return _CACHE
        index = build_port_areas(poly_dir)
        if poly_dir is None:
            _CACHE = index
        return index


def reset_port_areas(index: PortAreaIndex | None = None) -> None:
    """Tests inject a synthetic index. None clears the cache."""
    global _CACHE
    with _LOCK:
        _CACHE = index


def port_areas_frame(index: PortAreaIndex | None = None) -> pd.DataFrame:
    index = index or get_port_area_index()
    if not index.areas:
        return pd.DataFrame(columns=["port_name", "terminal_name", "geojson"])
    return pd.DataFrame(
        [
            {
                "port_name": a.port_name,
                "terminal_name": a.terminal_name,
                "geojson": a.geojson,
            }
            for a in index.areas
        ]
    )


def _parent_anchorage_frame() -> pd.DataFrame:
    rows = []
    for area in anchorage_areas:
        if is_excl_name(area["name"]):
            continue
        ring = [[float(lon), float(lat)] for lon, lat in area["polygon"]]
        if ring[0] != ring[-1]:
            ring.append(ring[0])
        rows.append({
            "anchorage_name": area["name"],
            "geojson": json.dumps({"type": "Polygon", "coordinates": [ring]}),
        })
    return pd.DataFrame(rows) if rows else pd.DataFrame(
        columns=["anchorage_name", "geojson"]
    )


def annotate_in_port_area(
    frame: pd.DataFrame,
    *,
    lon_col: str,
    lat_col: str,
    id_col: str | None = None,
    index: PortAreaIndex | None = None,
) -> pd.DataFrame:
    """
    Add in_port_area / port_area_name / port_area_terminal_name.

    A point inside both a parent anchorage and a port area counts as anchorage
    (in_port_area=False), matching PyVAM.
    """
    out = frame.copy()
    out["in_port_area"] = False
    out["port_area_name"] = None
    out["port_area_terminal_name"] = None
    if out.empty:
        return out

    areas = port_areas_frame(index)
    if areas.empty:
        return out

    work = out.reset_index(drop=True)
    work["_pa_row"] = range(len(work))
    points = work[["_pa_row", lon_col, lat_col]].rename(
        columns={lon_col: "longitude", lat_col: "latitude"}
    )
    anchorages = _parent_anchorage_frame()

    hit = spatial_df(
        """
        WITH base AS (
            SELECT
                _pa_row,
                longitude,
                latitude
            FROM points
            WHERE longitude IS NOT NULL AND latitude IS NOT NULL
        ),
        port_hit AS (
            SELECT
                b._pa_row,
                MIN(p.port_name) AS port_area_name,
                MIN(p.terminal_name) AS port_area_terminal_name
            FROM base b
            CROSS JOIN port_areas p
            WHERE ST_Within(
                ST_Point(b.longitude, b.latitude),
                ST_GeomFromGeoJSON(p.geojson)
            )
            GROUP BY b._pa_row
        ),
        anch_hit AS (
            SELECT
                b._pa_row
            FROM base b
            CROSS JOIN anchorages a
            WHERE ST_Within(
                ST_Point(b.longitude, b.latitude),
                ST_GeomFromGeoJSON(a.geojson)
            )
            GROUP BY b._pa_row
        )
        SELECT
            b._pa_row,
            (ph._pa_row IS NOT NULL AND ah._pa_row IS NULL) AS in_port_area,
            CASE
                WHEN ah._pa_row IS NULL THEN ph.port_area_name
                ELSE NULL
            END AS port_area_name,
            CASE
                WHEN ah._pa_row IS NULL THEN ph.port_area_terminal_name
                ELSE NULL
            END AS port_area_terminal_name
        FROM base b
        LEFT JOIN port_hit ph ON ph._pa_row = b._pa_row
        LEFT JOIN anch_hit ah ON ah._pa_row = b._pa_row
        """,
        {
            "points": points,
            "port_areas": areas,
            "anchorages": anchorages if not anchorages.empty else pd.DataFrame(
                columns=["anchorage_name", "geojson"]
            ),
        },
    )

    if hit.empty:
        return out

    merged = work.drop(
        columns=["in_port_area", "port_area_name", "port_area_terminal_name"],
        errors="ignore",
    ).merge(hit, on="_pa_row", how="left")
    merged["in_port_area"] = merged["in_port_area"].fillna(False).astype(bool)
    merged = merged.drop(columns=["_pa_row"])
    return merged


def apply_port_area_to_outside_anchorage(frame: pd.DataFrame) -> pd.DataFrame:
    """
    Clear outside_anchorage when the centre is inside a port area (anchorage
    already wins inside annotate_in_port_area).
    """
    if frame.empty or "outside_anchorage" not in frame.columns:
        return frame
    out = frame.copy()
    mask = out.get("in_port_area")
    if mask is None:
        return out
    out.loc[mask.fillna(False).astype(bool), "outside_anchorage"] = False
    return out
