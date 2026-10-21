"""
Port / terminal / berth areas for MANTIS harbour exclusions.

Loaded once from data/polygons/src/{ports,terminals,berths}.geojson and cached
in memory. Point tests use a Shapely STRtree built at startup (no per-call
GeoJSON parse, no DuckDB polygon CROSS JOIN).
"""

from __future__ import annotations

import json
import logging
import math
import threading
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from shapely import STRtree, points as shapely_points
from shapely.geometry import mapping, shape
from shapely.ops import unary_union

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


# Fallback path when geojson is not yet on disk at import time.
DEFAULT_POLY_DIR = Path(__file__).resolve().parents[1] / "data" / "polygons" / "src"

_LOCK = threading.Lock()
_CACHE: "PortAreaIndex | None" = None


@dataclass(frozen=True)
class PortArea:
    port_name: str
    terminal_name: str | None
    geojson: str
    area_km2: float


@dataclass(frozen=True)
class BerthArea:
    berth_name: str
    geojson: str


@dataclass
class PortAreaIndex:
    areas: list[PortArea]
    berths: list[BerthArea] = field(default_factory=list)
    ports_loaded: int = 0
    terminals_loaded: int = 0
    berths_loaded: int = 0
    ports_skipped: list[str] = field(default_factory=list)
    poly_dir: Path = DEFAULT_POLY_DIR
    # Shapely geometries + STRtrees (built once; never re-parsed per request).
    port_geoms: list = field(default_factory=list, repr=False)
    port_names: list[str] = field(default_factory=list, repr=False)
    port_terminals: list = field(default_factory=list, repr=False)
    port_tree: Any = field(default=None, repr=False)
    anch_geoms: list = field(default_factory=list, repr=False)
    anch_tree: Any = field(default=None, repr=False)
    berth_geoms: list = field(default_factory=list, repr=False)
    berth_names: list[str] = field(default_factory=list, repr=False)
    berth_tree: Any = field(default=None, repr=False)
    trees_ready: bool = False


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


def _parent_anchorage_geoms() -> list:
    geoms = []
    for area in anchorage_areas:
        if is_excl_name(area["name"]):
            continue
        ring = [[float(lon), float(lat)] for lon, lat in area["polygon"]]
        if ring[0] != ring[-1]:
            ring.append(ring[0])
        try:
            geom = shape({"type": "Polygon", "coordinates": [ring]})
        except Exception:
            continue
        if geom.is_empty:
            continue
        geoms.append(geom)
    return geoms


def _strtree(geoms: list):
    """STRtree for a geom list; empty list → empty tree (shapely 2.0+)."""
    return STRtree(geoms)


def attach_harbour_trees(index: PortAreaIndex) -> PortAreaIndex:
    """
    Build shapely geometries + STRtrees once on the index.

    Safe to call repeatedly; skips work when trees_ready is set.
    """
    if index.trees_ready:
        return index

    port_geoms = []
    port_names: list[str] = []
    port_terminals: list = []
    for area in index.areas:
        try:
            geom = shape(json.loads(area.geojson))
        except Exception:
            continue
        if geom is None or geom.is_empty:
            continue
        port_geoms.append(geom)
        port_names.append(area.port_name)
        port_terminals.append(area.terminal_name)

    berth_geoms = []
    berth_names: list[str] = []
    for berth in index.berths:
        try:
            geom = shape(json.loads(berth.geojson))
        except Exception:
            continue
        if geom is None or geom.is_empty:
            continue
        berth_geoms.append(geom)
        berth_names.append(berth.berth_name)

    anch_geoms = _parent_anchorage_geoms()

    index.port_geoms = port_geoms
    index.port_names = port_names
    index.port_terminals = port_terminals
    index.port_tree = _strtree(port_geoms)
    index.anch_geoms = anch_geoms
    index.anch_tree = _strtree(anch_geoms)
    index.berth_geoms = berth_geoms
    index.berth_names = berth_names
    index.berth_tree = _strtree(berth_geoms)
    index.trees_ready = True
    return index


def build_port_areas(poly_dir: Path | None = None) -> PortAreaIndex:
    directory = Path(poly_dir) if poly_dir is not None else _default_poly_dir()
    ports = _read_features(directory / "ports.geojson", "port")
    terminals = _read_features(directory / "terminals.geojson", "terminal")
    berth_feats = _read_features(directory / "berths.geojson", "berth")
    areas: list[PortArea] = []
    port_geoms = []
    port_names: list[str] = []
    port_terminals: list = []
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
            for child in children:
                geom = child["geom"]
                areas.append(
                    PortArea(
                        port_name=port_name,
                        terminal_name=child["name"] or None,
                        geojson=_geom_to_geojson(geom),
                        area_km2=area_km2(geom),
                    )
                )
                port_geoms.append(geom)
                port_names.append(port_name)
                port_terminals.append(child["name"] or None)
        else:
            areas.append(
                PortArea(
                    port_name=port_name,
                    terminal_name=None,
                    geojson=_geom_to_geojson(used),
                    area_km2=area_km2(used),
                )
            )
            port_geoms.append(used)
            port_names.append(port_name)
            port_terminals.append(None)

    berths = []
    berth_geoms = []
    berth_names: list[str] = []
    for b in berth_feats:
        name = b["name"] or f"berth:{b['poly_id']}"
        berths.append(BerthArea(berth_name=name, geojson=_geom_to_geojson(b["geom"])))
        berth_geoms.append(b["geom"])
        berth_names.append(name)

    anch_geoms = _parent_anchorage_geoms()

    LOG.info(
        "polygons loaded ports=%d terminals=%d berths=%d; port areas built=%d; ports skipped=%d",
        len(ports),
        len(terminals),
        len(berths),
        len(areas),
        len(skipped),
    )
    if skipped:
        LOG.info("skipped names (first 20): %s", ", ".join(skipped[:20]))

    return PortAreaIndex(
        areas=areas,
        berths=berths,
        ports_loaded=len(ports),
        terminals_loaded=len(terminals),
        berths_loaded=len(berths),
        ports_skipped=skipped,
        poly_dir=directory,
        port_geoms=port_geoms,
        port_names=port_names,
        port_terminals=port_terminals,
        port_tree=_strtree(port_geoms),
        anch_geoms=anch_geoms,
        anch_tree=_strtree(anch_geoms),
        berth_geoms=berth_geoms,
        berth_names=berth_names,
        berth_tree=_strtree(berth_geoms),
        trees_ready=True,
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
        if index is not None and not index.trees_ready:
            attach_harbour_trees(index)
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


def berths_frame(index: PortAreaIndex | None = None) -> pd.DataFrame:
    index = index or get_port_area_index()
    if not index.berths:
        return pd.DataFrame(columns=["berth_name", "geojson"])
    return pd.DataFrame(
        [{"berth_name": b.berth_name, "geojson": b.geojson} for b in index.berths]
    )


def _parent_anchorage_frame() -> pd.DataFrame:
    """GeoJSON frame for DuckDB parity tests only."""
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


def _bulk_within_hits(tree, geoms_meta_len: int, pts) -> np.ndarray:
    """
    STRtree.query bulk within. Returns (2, n) int array [point_i, tree_i].

    Shapely 2.0+; empty tree or no hits → shape (2, 0).
    """
    if geoms_meta_len == 0 or tree is None or len(pts) == 0:
        return np.zeros((2, 0), dtype=np.intp)
    hits = tree.query(pts, predicate="within")
    if hits is None:
        return np.zeros((2, 0), dtype=np.intp)
    arr = np.asarray(hits)
    if arr.size == 0:
        return np.zeros((2, 0), dtype=np.intp)
    if arr.ndim == 1:
        # Single-point query path — not used for bulk, but be safe.
        return np.vstack([np.zeros(len(arr), dtype=np.intp), arr.astype(np.intp)])
    return arr.astype(np.intp, copy=False)


def _min_name_by_point(hits: np.ndarray, names: list[str]) -> dict[int, str]:
    """DuckDB-compatible MIN(name) per input point index."""
    best: dict[int, str] = {}
    if hits.size == 0:
        return best
    for inp, tree_i in zip(hits[0].tolist(), hits[1].tolist()):
        name = names[int(tree_i)]
        prev = best.get(int(inp))
        if prev is None or name < prev:
            best[int(inp)] = name
    return best


def _min_port_by_point(
    hits: np.ndarray, names: list[str], terminals: list
) -> dict[int, tuple[str, Any]]:
    """MIN(port_name) and MIN(terminal_name) independently per point (DuckDB)."""
    by_inp: dict[int, list[tuple[str, Any]]] = defaultdict(list)
    if hits.size == 0:
        return {}
    for inp, tree_i in zip(hits[0].tolist(), hits[1].tolist()):
        by_inp[int(inp)].append((names[int(tree_i)], terminals[int(tree_i)]))
    out: dict[int, tuple[str, Any]] = {}
    for inp, pairs in by_inp.items():
        port_name = min(p[0] for p in pairs)
        terms = [p[1] for p in pairs if p[1] is not None and p[1] == p[1]]
        terminal = min(terms) if terms else None
        out[inp] = (port_name, terminal)
    return out


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
    return annotate_harbour_location(
        frame, lon_col=lon_col, lat_col=lat_col, index=index, include_berths=False
    )


def annotate_harbour_location(
    frame: pd.DataFrame,
    *,
    lon_col: str,
    lat_col: str,
    index: PortAreaIndex | None = None,
    include_berths: bool = True,
) -> pd.DataFrame:
    """
    Vectorised port (+ optional berth) annotation via STRtree.

    Anchorage wins over port area. Berth is independent (at_berth if inside a
    berth polygon regardless of anchorage).
    """
    out = frame.copy()
    out["in_port_area"] = False
    out["port_area_name"] = None
    out["port_area_terminal_name"] = None
    out["at_berth"] = False
    out["berth_name"] = None
    if out.empty:
        return out

    index = index or get_port_area_index()
    if not index.trees_ready:
        attach_harbour_trees(index)

    if not index.port_geoms and not (include_berths and index.berth_geoms):
        return out

    work = out.reset_index(drop=True)
    lons = pd.to_numeric(work[lon_col], errors="coerce").to_numpy(dtype=float)
    lats = pd.to_numeric(work[lat_col], errors="coerce").to_numpy(dtype=float)
    valid = np.isfinite(lons) & np.isfinite(lats)
    if not valid.any():
        return out

    valid_idx = np.nonzero(valid)[0]
    pts = shapely_points(np.column_stack([lons[valid], lats[valid]]))

    in_port = np.zeros(len(work), dtype=bool)
    at_berth = np.zeros(len(work), dtype=bool)
    port_names_out: list[Any] = [None] * len(work)
    port_terms_out: list[Any] = [None] * len(work)
    berth_names_out: list[Any] = [None] * len(work)

    anch_hits = _bulk_within_hits(index.anch_tree, len(index.anch_geoms), pts)
    in_anch = set(int(i) for i in anch_hits[0].tolist()) if anch_hits.size else set()

    port_hits = _bulk_within_hits(index.port_tree, len(index.port_geoms), pts)
    port_best = _min_port_by_point(port_hits, index.port_names, index.port_terminals)
    for local_i, (pname, tname) in port_best.items():
        if local_i in in_anch:
            continue
        row_i = int(valid_idx[local_i])
        in_port[row_i] = True
        port_names_out[row_i] = pname
        port_terms_out[row_i] = tname

    if include_berths and index.berth_geoms:
        berth_hits = _bulk_within_hits(index.berth_tree, len(index.berth_geoms), pts)
        berth_best = _min_name_by_point(berth_hits, index.berth_names)
        for local_i, bname in berth_best.items():
            row_i = int(valid_idx[local_i])
            at_berth[row_i] = True
            berth_names_out[row_i] = bname

    work["in_port_area"] = in_port
    work["at_berth"] = at_berth
    work["port_area_name"] = port_names_out
    work["port_area_terminal_name"] = port_terms_out
    work["berth_name"] = berth_names_out
    return work


def annotate_harbour_location_duckdb(
    frame: pd.DataFrame,
    *,
    lon_col: str,
    lat_col: str,
    index: PortAreaIndex | None = None,
    include_berths: bool = True,
) -> pd.DataFrame:
    """Legacy DuckDB path — kept for STRtree parity tests only."""
    from duckdb_spatial import spatial_df

    out = frame.copy()
    out["in_port_area"] = False
    out["port_area_name"] = None
    out["port_area_terminal_name"] = None
    out["at_berth"] = False
    out["berth_name"] = None
    if out.empty:
        return out

    areas = port_areas_frame(index)
    berths = berths_frame(index) if include_berths else pd.DataFrame(columns=["berth_name", "geojson"])
    if areas.empty and berths.empty:
        return out

    work = out.reset_index(drop=True)
    work["_pa_row"] = range(len(work))
    pts = work[["_pa_row", lon_col, lat_col]].rename(
        columns={lon_col: "longitude", lat_col: "latitude"}
    )
    anchorages = _parent_anchorage_frame()
    empty_port = pd.DataFrame(columns=["port_name", "terminal_name", "geojson"])
    empty_berth = pd.DataFrame(columns=["berth_name", "geojson"])
    empty_anch = pd.DataFrame(columns=["anchorage_name", "geojson"])

    hit = spatial_df(
        """
        WITH base AS (
            SELECT _pa_row, longitude, latitude
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
            SELECT b._pa_row
            FROM base b
            CROSS JOIN anchorages a
            WHERE ST_Within(
                ST_Point(b.longitude, b.latitude),
                ST_GeomFromGeoJSON(a.geojson)
            )
            GROUP BY b._pa_row
        ),
        berth_hit AS (
            SELECT
                b._pa_row,
                MIN(br.berth_name) AS berth_name
            FROM base b
            CROSS JOIN berths br
            WHERE ST_Within(
                ST_Point(b.longitude, b.latitude),
                ST_GeomFromGeoJSON(br.geojson)
            )
            GROUP BY b._pa_row
        )
        SELECT
            b._pa_row,
            (ph._pa_row IS NOT NULL AND ah._pa_row IS NULL) AS in_port_area,
            CASE WHEN ah._pa_row IS NULL THEN ph.port_area_name END AS port_area_name,
            CASE WHEN ah._pa_row IS NULL THEN ph.port_area_terminal_name END AS port_area_terminal_name,
            (bh._pa_row IS NOT NULL) AS at_berth,
            bh.berth_name AS berth_name
        FROM base b
        LEFT JOIN port_hit ph ON ph._pa_row = b._pa_row
        LEFT JOIN anch_hit ah ON ah._pa_row = b._pa_row
        LEFT JOIN berth_hit bh ON bh._pa_row = b._pa_row
        """,
        {
            "points": pts,
            "port_areas": areas if not areas.empty else empty_port,
            "anchorages": anchorages if not anchorages.empty else empty_anch,
            "berths": berths if not berths.empty else empty_berth,
        },
    )
    if hit.empty:
        return out
    drop_cols = [
        "in_port_area",
        "port_area_name",
        "port_area_terminal_name",
        "at_berth",
        "berth_name",
    ]
    merged = work.drop(columns=drop_cols, errors="ignore").merge(hit, on="_pa_row", how="left")
    merged["in_port_area"] = merged["in_port_area"].fillna(False).astype(bool)
    merged["at_berth"] = merged["at_berth"].fillna(False).astype(bool)
    return merged.drop(columns=["_pa_row"])


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


def harbour_exclude_mask(frame: pd.DataFrame) -> pd.Series:
    """True when the row should leave the default list (in port area or at berth)."""
    if frame.empty:
        return pd.Series(dtype=bool)
    in_port = frame["in_port_area"].fillna(False).astype(bool) if "in_port_area" in frame.columns else False
    at_berth = frame["at_berth"].fillna(False).astype(bool) if "at_berth" in frame.columns else False
    return in_port | at_berth
