"""Extract tree TRUNKS and 2D CANOPY footprints from classified clouds (no training).

PointCONV (c1_lv 6-class) has no dedicated trunk class; tree stems are folded
into High Vegetation (class 5). This extractor recovers individual trees and
fits a truncated-cone trunk model, reporting the trunk diameter / radius /
circumference at 1 m above ground.

Recipe (classical, deterministic)
---------------------------------
1. GROUND model: stream class-2 (ground) points -> a min-Z DEM raster; fill
   gaps by nearest. Gives ground_z(x,y) -> height-above-ground (HAG) everywhere.
2. CANOPY segmentation: HAG the class-5 (high-veg) points; rasterize a Canopy
   Height Model (CHM, max-HAG per cell); detect local-maxima apexes (one per
   tree, min spacing + min height); assign each canopy point to its nearest
   apex within a max crown radius (Voronoi crown split -> individual trees).
3. TRUNK fit (ground-up, per tree): locate the stem base (XY-median of the
   lowest crown points); collect near-vertical stem points; fit an algebraic
   circle per height band; the band centers define the trunk axis (lean) and
   the band radii define a truncated cone r(h)=r0+k*h. Report at h=1.0 m:
   radius, diameter, circumference (+ bonus DBH at 1.3 m).
4. CANOPY footprint: 2D concave (or convex) hull of each crown's points ->
   area + crown diameter.

Outputs (LineString/Point/Polygon Z, .shp + .gpkg twin):
  <out_dir>/Tree_Stems.{shp,gpkg}   POINT Z at the trunk base, trunk attrs
  <out_dir>/Tree_Canopy.{shp,gpkg}  POLYGON crown footprint, canopy attrs

Usage:
    python extract_tree_trunk_canopy.py \\
        --input <classified.las | dir> [--pattern "*.las"] \\
        --out-dir <dir> [--epsg 26917] \\
        [--canopy-class 5] [--ground-class 2] \\
        [--measure-height 1.0] [--min-canopy-height 3.0]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np


# ----------------------------------------------------------------- ingest

def stream_points(las_paths, classes, chunk_pts=5_000_000):
    """Yield (xyz, cls) chunks restricted to the requested class codes."""
    import laspy
    cset = np.asarray(sorted(classes), dtype=np.int32)
    for path in las_paths:
        with laspy.open(str(path)) as rd:
            for ch in rd.chunk_iterator(chunk_pts):
                c = np.asarray(ch.classification)
                m = np.isin(c, cset)
                if not m.any():
                    continue
                xyz = np.column_stack(
                    (np.asarray(ch.x)[m], np.asarray(ch.y)[m],
                     np.asarray(ch.z)[m]))
                yield xyz, c[m]


def collect(las_paths, canopy_class, ground_class, dem_cell):
    """One streaming pass: hold canopy points; reduce ground to a min-Z grid."""
    canopy = []
    gmin = {}            # (ix,iy) -> min ground z   (DEM at dem_cell)
    n_canopy = n_ground = 0
    for xyz, c in stream_points(las_paths, (canopy_class, ground_class)):
        cm = c == canopy_class
        if cm.any():
            canopy.append(xyz[cm])
            n_canopy += int(cm.sum())
        gm = c == ground_class
        if gm.any():
            g = xyz[gm]
            n_ground += len(g)
            ix = np.floor(g[:, 0] / dem_cell).astype(np.int64)
            iy = np.floor(g[:, 1] / dem_cell).astype(np.int64)
            # per-chunk reduce, then merge into the running dict
            order = np.lexsort((iy, ix))
            ix, iy, z = ix[order], iy[order], g[order, 2]
            bnd = np.nonzero((np.diff(ix) != 0) | (np.diff(iy) != 0))[0] + 1
            for s, e in zip(np.concatenate(([0], bnd)),
                            np.concatenate((bnd, [len(ix)]))):
                k = (int(ix[s]), int(iy[s]))
                zmin = float(z[s:e].min())
                pv = gmin.get(k)
                if pv is None or zmin < pv:
                    gmin[k] = zmin
    canopy = np.vstack(canopy) if canopy else np.empty((0, 3))
    return canopy, gmin, n_canopy, n_ground


# ------------------------------------------------------------------- DEM

def build_dem(gmin, dem_cell):
    """Dense min-Z DEM over the ground extent, gaps filled by nearest cell.

    Returns (dem, ix0, iy0): dem[row,col] is ground Z for cell
    (ix0+col, iy0+row). Empty grids return (None, 0, 0).
    """
    from scipy.ndimage import distance_transform_edt
    if not gmin:
        return None, 0, 0
    keys = np.array(list(gmin.keys()), dtype=np.int64)
    vals = np.array(list(gmin.values()), dtype=np.float64)
    ix0, iy0 = keys[:, 0].min(), keys[:, 1].min()
    nx = int(keys[:, 0].max() - ix0 + 1)
    ny = int(keys[:, 1].max() - iy0 + 1)
    dem = np.full((ny, nx), np.nan, dtype=np.float64)
    dem[keys[:, 1] - iy0, keys[:, 0] - ix0] = vals
    nan = np.isnan(dem)
    if nan.any():
        idx = distance_transform_edt(nan, return_distances=False,
                                     return_indices=True)
        dem = dem[tuple(idx)]
    return dem, int(ix0), int(iy0)


def ground_at(dem, ix0, iy0, dem_cell, xy):
    """Vectorized ground Z lookup for Nx2 XY (clamped to the DEM extent)."""
    ny, nx = dem.shape
    col = np.clip(np.floor(xy[:, 0] / dem_cell).astype(np.int64) - ix0, 0, nx - 1)
    row = np.clip(np.floor(xy[:, 1] / dem_cell).astype(np.int64) - iy0, 0, ny - 1)
    return dem[row, col]


# --------------------------------------------------------- crown segmentation

def find_apexes(canopy, hag, chm_cell, min_spacing, min_apex_h):
    """Local-maxima apexes of the Canopy Height Model. Returns Nx2 apex XY."""
    from scipy.ndimage import maximum_filter
    x0 = np.floor(canopy[:, 0].min() / chm_cell).astype(np.int64)
    y0 = np.floor(canopy[:, 1].min() / chm_cell).astype(np.int64)
    col = np.floor(canopy[:, 0] / chm_cell).astype(np.int64) - x0
    row = np.floor(canopy[:, 1] / chm_cell).astype(np.int64) - y0
    nx = int(col.max() + 1); ny = int(row.max() + 1)
    chm = np.zeros((ny, nx), dtype=np.float64)
    np.maximum.at(chm, (row, col), hag)
    win = max(3, int(round(min_spacing / chm_cell)))
    if win % 2 == 0:
        win += 1
    mx = maximum_filter(chm, size=win, mode="constant", cval=0.0)
    peaks = (chm == mx) & (chm >= min_apex_h)
    pr, pc = np.nonzero(peaks)
    apex_xy = np.column_stack(((pc + x0 + 0.5) * chm_cell,
                               (pr + y0 + 0.5) * chm_cell))
    apex_h = chm[pr, pc]
    return apex_xy, apex_h


def assign_crowns(canopy_xy, apex_xy, max_crown_r):
    """Assign each canopy point to its nearest apex within max_crown_r.

    Returns an int label per point (-1 = unassigned / sparse veg).
    """
    from scipy.spatial import cKDTree
    if len(apex_xy) == 0:
        return np.full(len(canopy_xy), -1, dtype=np.int64)
    kd = cKDTree(apex_xy)
    dist, lab = kd.query(canopy_xy, k=1)
    lab = lab.astype(np.int64)
    lab[dist > max_crown_r] = -1
    return lab


# --------------------------------------------------------------- circle fit

def _kasa(x, y):
    """Algebraic (Kasa) circle fit — fast, but inflates radius on short arcs."""
    A = np.column_stack((x, y, np.ones(len(x))))
    b = x * x + y * y
    sol, *_ = np.linalg.lstsq(A, b, rcond=None)
    cx, cy = sol[0] / 2.0, sol[1] / 2.0
    r = float(np.sqrt(max(sol[2] + cx * cx + cy * cy, 0.0)))
    return cx, cy, r


def fit_circle(xy):
    """Taubin circle fit. Returns (cx, cy, r, rms_resid, arc_deg).

    Taubin (1991) is far more stable than Kasa on the PARTIAL arcs that mobile
    lidar sees (a trunk is scanned from the road side, ~90-220 deg of wrap);
    Kasa systematically over-estimates the radius there. Falls back to Kasa if
    the Taubin denominator degenerates (near-collinear points).
    """
    x, y = xy[:, 0], xy[:, 1]
    n = len(x)
    mx, my = x.mean(), y.mean()
    u, v = x - mx, y - my
    z = u * u + v * v
    Mxx = (u * u).mean(); Myy = (v * v).mean(); Mxy = (u * v).mean()
    Mxz = (u * z).mean(); Myz = (v * z).mean(); Mzz = (z * z).mean()
    Mz = Mxx + Myy
    Cov = Mxx * Myy - Mxy * Mxy
    A3 = 4.0 * Mz
    A2 = -3.0 * Mz * Mz - Mzz
    A1 = Mzz * Mz + 4.0 * Cov * Mz - Mxz * Mxz - Myz * Myz - Mz ** 3
    A0 = (Mxz * Mxz * Myy + Myz * Myz * Mxx - Mzz * Cov
          - 2.0 * Mxz * Myz * Mxy + Mz * Mz * Cov)
    A22, A33 = A2 + A2, A3 + A3 + A3
    xn, yn = 0.0, A0
    for _ in range(50):                      # Newton from the smallest root
        Dy = A1 + xn * (A22 + A33 * xn)
        if Dy == 0.0:
            break
        xnew = xn - yn / Dy
        if xnew == xn or not np.isfinite(xnew):
            break
        ynew = A0 + xnew * (A1 + xnew * (A2 + xnew * A3))
        if abs(ynew) >= abs(yn):
            break
        xn, yn = xnew, ynew
    det = xn * xn - xn * Mz + Cov
    if abs(det) < 1e-12:
        cx, cy, r = _kasa(x, y)
    else:
        xc = (Mxz * (Myy - xn) - Myz * Mxy) / det / 2.0
        yc = (Myz * (Mxx - xn) - Mxz * Mxy) / det / 2.0
        cx, cy = xc + mx, yc + my
        r = float(np.sqrt(max(xc * xc + yc * yc + Mz, 0.0)))
    rad = np.hypot(x - cx, y - cy)
    rms = float(np.sqrt(np.mean((rad - r) ** 2))) if n else np.inf
    ang = np.sort(np.arctan2(y - cy, x - cx))
    if len(ang) >= 2:
        gaps = np.diff(np.concatenate((ang, [ang[0] + 2 * np.pi])))
        arc = float(np.degrees(2 * np.pi - gaps.max()))
    else:
        arc = 0.0
    return cx, cy, r, rms, arc


def fit_trunk(stem_xyz, ground_z, *, measure_h, slice_h, zmin, zmax,
              min_slice_pts, max_core_r, core_spread_max, min_slices,
              r_min, r_max):
    """Fit a truncated-cone trunk by isolating the woody stem core.

    The cylinder of near-stem points still contains low foliage/branches at
    the same height as the trunk. To recover the *woody* stem we slice the
    column into thin height bins and, per slice, iteratively trim to a tight
    XY core (drop points beyond max_core_r of the running centroid). A slice
    is "stem-like" only if its core circle fit is plausible (radius in range)
    and tight (RMS <= core_spread_max). A trunk requires >= min_slices such
    slices forming a vertical column, with one near measure_h. The slice
    centers define the axis (lean); the slice radii define a truncated cone
    r(h)=r0+k*h, evaluated at measure_h.
    """
    hag = stem_xyz[:, 2] - ground_z
    edges = np.arange(zmin, zmax + 1e-9, slice_h)
    slices = []  # (h_center, cx, cy, r, rms, n_core, arc)
    for i in range(len(edges) - 1):
        m = (hag >= edges[i]) & (hag < edges[i + 1])
        if int(m.sum()) < min_slice_pts:
            continue
        P = stem_xyz[m, :2]
        cxy = np.median(P, axis=0)
        core = P
        for _ in range(3):  # shrink to the woody core
            d = np.hypot(P[:, 0] - cxy[0], P[:, 1] - cxy[1])
            core = P[d <= max_core_r]
            if len(core) < min_slice_pts:
                break
            cxy = np.median(core, axis=0)
        if len(core) < min_slice_pts:
            continue
        cx, cy, r, rms, arc = fit_circle(core)
        if not (r_min <= r <= r_max) or rms > core_spread_max:
            continue
        slices.append((0.5 * (edges[i] + edges[i + 1]), cx, cy, r, rms,
                       int(len(core)), arc))
    if len(slices) < min_slices:
        return None
    S = np.asarray([s[:5] for s in slices], dtype=np.float64)
    hs, cxs, cys, rs, rmss = S[:, 0], S[:, 1], S[:, 2], S[:, 3], S[:, 4]
    near = np.abs(hs - measure_h)
    if near.min() > 0.6:           # no woody evidence near the report height
        return None
    # axis lean from slice centers vs height
    ax = np.polyfit(hs, cxs, 1)
    ay = np.polyfit(hs, cys, 1)
    lean_deg = float(np.degrees(np.arctan(np.hypot(ax[0], ay[0]))))
    base_x = float(np.polyval(ax, 0.0))
    base_y = float(np.polyval(ay, 0.0))
    # truncated-cone radius taper, evaluated at measure_h (robust to one
    # noisy slice); fall back to the nearest slice if the fit extrapolates out
    kr = np.polyfit(hs, rs, 1)
    taper = float(kr[0])
    r_cone = float(np.polyval(kr, measure_h))
    j = int(np.argmin(near))
    r_direct = float(rs[j])
    r_meas = r_cone if (r_min <= r_cone <= r_max) else r_direct
    # NR1: breast-height (1.3 m) cone radius, clamped to the same plausibility
    # band as r_meas. The DBH attribute used to add the RAW taper slope
    # (2*(r + taper*0.3)) with no clamp, so a steep spurious taper from a few
    # noisy slices silently wrote an implausible DBH. Fall back to r_meas when
    # the extrapolation leaves the band.
    r_1p3 = float(np.polyval(kr, 1.3))
    if not (r_min <= r_1p3 <= r_max):
        r_1p3 = r_meas
    cx_m = float(np.polyval(ax, measure_h))
    cy_m = float(np.polyval(ay, measure_h))
    return dict(
        base_x=base_x, base_y=base_y,
        cx_meas=cx_m, cy_meas=cy_m,
        lean_deg=round(lean_deg, 2), taper=round(taper, 4),
        n_bands=len(slices), r_meas=r_meas, rad_1p3m=round(r_1p3, 3),
        resid=round(float(np.median(rmss)), 4),
        arc_deg=round(float(slices[j][6]), 1), n_stem=int(len(stem_xyz)),
        n_meas_band=int(slices[j][5]),
    )


# ----------------------------------------------------------------- main

def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input", required=True, type=Path,
                    help="classified LAS/LAZ file or directory")
    ap.add_argument("--pattern", default="*.las",
                    help="glob when --input is a directory")
    ap.add_argument("--out-dir", required=True, type=Path,
                    help="output dir for Tree_Stems/Tree_Canopy .shp+.gpkg")
    ap.add_argument("--epsg", type=int, default=None,
                    help="output CRS; default: first input file's header CRS")
    ap.add_argument("--canopy-class", type=int, default=5)
    ap.add_argument("--ground-class", type=int, default=2)
    # ground / CHM
    ap.add_argument("--dem-cell", type=float, default=0.5)
    ap.add_argument("--chm-cell", type=float, default=0.5)
    ap.add_argument("--min-canopy-height", type=float, default=3.0,
                    help="min apex HAG to count as a tree (m)")
    ap.add_argument("--min-tree-spacing", type=float, default=3.0,
                    help="min horizontal spacing between tree apexes (m)")
    ap.add_argument("--max-crown-radius", type=float, default=8.0,
                    help="max canopy-point distance from its apex (m)")
    ap.add_argument("--min-crown-pts", type=int, default=120,
                    help="min canopy points to keep a tree")
    # trunk
    ap.add_argument("--measure-height", type=float, default=1.0,
                    help="height above ground for diameter/radius/circ (m)")
    ap.add_argument("--trunk-search-radius", type=float, default=0.8,
                    help="XY radius around the stem base for stem points (m)")
    ap.add_argument("--trunk-zmin", type=float, default=0.3)
    ap.add_argument("--trunk-zmax", type=float, default=4.0)
    ap.add_argument("--slice-height", type=float, default=0.25,
                    help="height-bin thickness for stem slices (m)")
    ap.add_argument("--min-slice-pts", type=int, default=4,
                    help="min core points to fit a stem slice")
    ap.add_argument("--max-core-radius", type=float, default=0.5,
                    help="trim each slice to this XY radius of its centroid "
                         "(woody-core isolation, excludes low foliage)")
    ap.add_argument("--core-spread-max", type=float, default=0.06,
                    help="max circle-fit RMS for a slice to count as woody (m)")
    ap.add_argument("--min-stem-slices", type=int, default=3,
                    help="min vertically-continuous woody slices for a trunk")
    ap.add_argument("--r-min", type=float, default=0.03,
                    help="min plausible trunk radius (m)")
    ap.add_argument("--r-max", type=float, default=0.5,
                    help="max plausible trunk radius (m)")
    # canopy footprint
    ap.add_argument("--hull", choices=("concave", "convex"), default="concave")
    ap.add_argument("--concave-ratio", type=float, default=0.3)
    # deconflict with poles
    ap.add_argument("--pole-shp", type=Path, default=None,
                    help="optional pole point shapefile; drop stems within "
                         "--pole-radius of a pole")
    ap.add_argument("--pole-radius", type=float, default=1.5)
    args = ap.parse_args()

    if args.input.is_dir():
        las_paths = sorted(args.input.glob(args.pattern))
        if not las_paths:
            raise SystemExit(f"no files match {args.pattern} in {args.input}")
    elif args.input.is_file():
        las_paths = [args.input]
    else:
        raise SystemExit(f"input not found: {args.input}")

    print(f"Streaming class-{args.canopy_class}/{args.ground_class} from "
          f"{len(las_paths)} file(s)...")
    canopy, gmin, n_canopy, n_ground = collect(
        las_paths, args.canopy_class, args.ground_class, args.dem_cell)
    print(f"  canopy={n_canopy:,} pts  ground={n_ground:,} pts "
          f"-> {len(gmin):,} DEM cells")
    if len(canopy) < args.min_crown_pts:
        raise SystemExit("no tree canopy — wrong --canopy-class or no veg")
    dem, ix0, iy0 = build_dem(gmin, args.dem_cell)
    if dem is None:
        raise SystemExit("no ground points — cannot reference HAG; pass a "
                         "cloud with class-2 ground")

    hag = canopy[:, 2] - ground_at(dem, ix0, iy0, args.dem_cell, canopy[:, :2])
    apex_xy, apex_h = find_apexes(canopy, hag, args.chm_cell,
                                  args.min_tree_spacing, args.min_canopy_height)
    print(f"  {len(apex_xy)} candidate tree apex(es) "
          f"(>= {args.min_canopy_height} m)")
    if len(apex_xy) == 0:
        raise SystemExit("no canopy apexes above --min-canopy-height")
    lab = assign_crowns(canopy[:, :2], apex_xy, args.max_crown_radius)

    poles = None
    if args.pole_shp and args.pole_shp.exists():
        import geopandas as gpd
        pg = gpd.read_file(args.pole_shp)
        poles = np.column_stack((pg.geometry.x.values, pg.geometry.y.values))
        print(f"  deconflict: {len(poles)} pole(s) loaded")

    from shapely.geometry import Point, Polygon, mapping  # noqa: F401
    from shapely import convex_hull, concave_hull
    from shapely.geometry import MultiPoint

    stem_rows, canopy_rows = [], []
    n_trees = n_with_trunk = n_pole_skip = 0

    for k in range(len(apex_xy)):
        sel = lab == k
        if int(sel.sum()) < args.min_crown_pts:
            continue
        crown = canopy[sel]
        chag = hag[sel]
        n_trees += 1
        gz = float(ground_at(dem, ix0, iy0, args.dem_cell,
                             apex_xy[k:k + 1])[0])
        top_hag = float(chag.max())
        # stem base = XY-median of the lowest crown points
        low = crown[chag <= max(np.percentile(chag, 30), args.trunk_zmax)]
        if len(low) == 0:
            low = crown
        sbx, sby = float(np.median(low[:, 0])), float(np.median(low[:, 1]))
        # pole deconfliction
        if poles is not None and len(poles):
            if np.min(np.hypot(poles[:, 0] - sbx, poles[:, 1] - sby)) \
                    <= args.pole_radius:
                n_pole_skip += 1
                continue
        # crown footprint hull
        mp = MultiPoint(crown[:, :2])
        try:
            hull = (concave_hull(mp, ratio=args.concave_ratio)
                    if args.hull == "concave" else convex_hull(mp))
        except Exception:
            hull = convex_hull(mp)
        if hull.geom_type != "Polygon" or hull.area <= 0:
            hull = convex_hull(mp)
        if hull.geom_type != "Polygon":
            continue
        verts = np.asarray(hull.exterior.coords)
        crown_diam = 0.0
        if len(verts) >= 2:
            d2 = ((verts[:, None, 0] - verts[None, :, 0]) ** 2 +
                  (verts[:, None, 1] - verts[None, :, 1]) ** 2)
            crown_diam = float(np.sqrt(d2.max()))
        tid = f"T_{n_trees:04d}"
        canopy_rows.append(dict(
            tree_id=tid, crown_area=round(float(hull.area), 2),
            crown_diam=round(crown_diam, 2), top_hag=round(top_hag, 2),
            n_pts=int(sel.sum()), geometry=hull))
        # trunk fit
        d_xy = np.hypot(crown[:, 0] - sbx, crown[:, 1] - sby)
        stem = crown[(d_xy <= args.trunk_search_radius) &
                     (chag >= args.trunk_zmin) & (chag <= args.trunk_zmax)]
        trunk = None
        if len(stem) >= args.min_slice_pts:
            trunk = fit_trunk(
                stem, gz, measure_h=args.measure_height,
                slice_h=args.slice_height, zmin=args.trunk_zmin,
                zmax=args.trunk_zmax, min_slice_pts=args.min_slice_pts,
                max_core_r=args.max_core_radius,
                core_spread_max=args.core_spread_max,
                min_slices=args.min_stem_slices,
                r_min=args.r_min, r_max=args.r_max)
        if trunk is None:
            continue
        n_with_trunk += 1
        r = trunk["r_meas"]
        quality = ("good" if (trunk["n_bands"] >= 4 and trunk["arc_deg"] >= 180
                              and trunk["resid"] <= args.core_spread_max)
                   else "partial" if (trunk["n_bands"] >= 3
                                      and trunk["arc_deg"] >= 90)
                   else "low")
        stem_rows.append(dict(
            tree_id=tid, ground_z=round(gz, 2),
            diam_1m=round(2 * r, 3), rad_1m=round(r, 3),
            circ_1m=round(2 * np.pi * r, 3),
            dbh_1p3m=round(2 * trunk["rad_1p3m"], 3),
            trunk_ht=round(top_hag, 2), canopy_top=round(top_hag, 2),
            lean_deg=trunk["lean_deg"], taper=trunk["taper"],
            n_bands=trunk["n_bands"], n_stem=trunk["n_stem"],
            arc_deg=trunk["arc_deg"], resid=trunk["resid"],
            quality=quality,
            geometry=Point(trunk["base_x"], trunk["base_y"], gz)))

    print(f"  {n_trees} tree(s) -> {n_with_trunk} with a fitted trunk"
          + (f"; {n_pole_skip} skipped near poles" if poles is not None else ""))
    if not canopy_rows:
        raise SystemExit("no trees survived crown filtering")

    # CRS
    crs = f"EPSG:{args.epsg}" if args.epsg else None
    if crs is None:
        import laspy
        with laspy.open(str(las_paths[0])) as rd:
            try:
                crs = rd.header.parse_crs()
            except Exception:
                crs = None
        if crs is None:
            raise SystemExit("no CRS in las header — pass --epsg")

    import geopandas as gpd
    args.out_dir.mkdir(parents=True, exist_ok=True)
    can_gdf = gpd.GeoDataFrame(canopy_rows, crs=crs)
    can_shp = args.out_dir / "Tree_Canopy.shp"
    can_gdf.to_file(can_shp)
    can_gdf.to_file(can_shp.with_suffix(".gpkg"), driver="GPKG")
    print(f"Wrote {can_shp} (+ .gpkg): {len(can_gdf)} canopy polygons")

    if stem_rows:
        stem_gdf = gpd.GeoDataFrame(stem_rows, crs=crs)
        stem_shp = args.out_dir / "Tree_Stems.shp"
        stem_gdf.to_file(stem_shp)
        stem_gdf.to_file(stem_shp.with_suffix(".gpkg"), driver="GPKG")
        med = float(np.median([r["diam_1m"] for r in stem_rows]))
        print(f"Wrote {stem_shp} (+ .gpkg): {len(stem_gdf)} trunks, "
              f"median diameter@{args.measure_height}m = {med:.2f} m, "
              f"CRS {stem_gdf.crs}")
    else:
        print("WARN no trunks fitted — wrote canopy only")
    return 0


if __name__ == "__main__":
    sys.exit(main())
