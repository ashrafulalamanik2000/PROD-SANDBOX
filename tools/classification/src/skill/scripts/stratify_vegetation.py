"""Stratify a single vegetation class into LOW / MEDIUM / HIGH vegetation by
height above ground (HAG).

WHY THIS EXISTS
---------------
The Topology-Aerial chain's PointCONV model
(`PointCONV_model_6class_Mobile_v0.0.18_retune_c2`) is a SIX-class model: it
emits {0, 2, 5, 6, 14, 15, 18} and folds ALL vegetation into class 5
("High Vegetation"). Stage 6 (`final_classified_pointcloud.py`) then overlays
road (40) / pole-body (19) / building-wall (47) onto that base. Nothing in the
chain ever emits class 3 or class 4, so a final-classified cloud carries a
single undifferentiated veg class.

Downstream SDAI tooling DOES expect the split — `veg_outline.py` rasterizes
classes `3,4,5` and the SDAI/PTC catalog reserves:

    3 = Low Vegetation      4 = Medium Vegetation      5 = High Vegetation

This worker closes that gap. It is a pure post-process on an already-classified
cloud: it computes HAG from the cloud's OWN ground points and re-labels the veg
points into 3 / 4 / 5. It is the "6v" step of the classification workflow and
runs after Stage 6 so the pole/road/wall overrides are already baked in and are
never touched.

METHOD
------
1. Build a ground DEM from the cloud's ground classes (default `2,40` — class 40
   road points ARE ground, Stage 6 just refined their label) as a minimum-Z grid
   at `--dem-cell` metres.
2. Fill the DEM: a point whose own cell holds no ground gets the min-Z of the
   nearest populated cell (vectorized cKDTree query over populated cell centres
   — this is the fast, correct version of the legacy `Add_Hag/add_hag.py`
   per-cell Python loop, and unlike that module it does NOT destroy Z).
3. `hag = z - dem_z`, written to a `hag` float32 extra dimension for QC.
4. Re-label veg points:  `hag < low_max` -> 3,  `< med_max` -> 4,  else -> 5.

Only points already in `--veg-classes` are touched. Ground, road, pole, wire,
tower, building and wall labels pass through byte-for-byte.

The pre-stratification label is preserved in the `original_class` extra
dimension. If Stage 6 already created that dimension it is LEFT ALONE (it holds
the true PointCONV base label, which is the more useful provenance).

UNITS
-----
Thresholds are given in METRES. The Topology-Aerial chain has a feet<->metre
round-trip for ftUS deliveries, so a final cloud can legitimately be in survey
feet. With `--units auto` (default) the worker reads the header CRS and converts
the thresholds into the data's own units; a cloud with no CRS falls back to
metres with a WARN. `--units ft` / `--units m` force it.

EXIT CODES (stage-package contract)
-----------------------------------
    0 = success
    3 = benign empty (no veg points, or not enough ground to build a DEM)
    1 = error
"""
from __future__ import annotations

import argparse
import copy
import json
import logging
import os
import sys
import time
from pathlib import Path

import laspy
import numpy as np
from scipy.spatial import cKDTree


# SDAI/PTC catalog (SDAI_Classification_CLASSCODES_v4) — identical to ASPRS here.
CLASS_LOW_VEG = 3
CLASS_MED_VEG = 4
CLASS_HIGH_VEG = 5

DEFAULT_GROUND_CLASSES = (2, 40)   # 2 ground; 40 = SDAI "Road / Pavement",
                                   # Stage 6's refinement OF ground.
DEFAULT_VEG_CLASSES = (5,)         # PointCONV folds all veg into 5. Pass
                                   # "3,4,5" to re-stratify an already-split
                                   # cloud at new thresholds.

# Guard: nx*ny cells. 40M cells * 4B = 160 MB, the ceiling before we coarsen.
MAX_DEM_CELLS = 40_000_000
MIN_GROUND_POINTS = 100

# Above this point count the in-memory path (laspy.read of the whole cloud) is
# replaced by the two-pass streaming path. 40M points at pf3 is ~1.4 GB packed,
# and laspy.read + the float64 x/y/z views roughly triples that -- still fine.
# A 473M-point Otter-Creek-scale clip is ~16 GB packed and simply cannot be
# read whole, which is why the streaming path exists.
STREAM_THRESHOLD_POINTS = 40_000_000

log = logging.getLogger("veg-strat")


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
        force=True,
    )


def _laz_backend_for(path: Path):
    """Mirror of the Stage 6 worker's backend pick, so a .laz round-trips."""
    if not str(path).lower().endswith(".laz"):
        return None
    for cand in (
        getattr(laspy.LazBackend, "LazrsParallel", None),
        getattr(laspy.LazBackend, "Lazrs", None),
        getattr(laspy.LazBackend, "Laszip", None),
    ):
        if cand is not None and cand.is_available():
            return cand
    return None


def _metres_per_unit(src, units: str, from_header: bool = False) -> tuple[float, str]:
    """Return (metres per data unit, how it was decided).

    `src` is a LasData (default) or a LasHeader (`from_header=True`, the
    streaming path, which never materializes a LasData).

    `--units auto` reads the header CRS. Anything whose linear unit is a foot
    variant uses that CRS's own conversion factor (US survey foot 0.3048006096
    vs international foot 0.3048 — the 2 ppm difference is irrelevant at these
    thresholds, but we take the real number rather than guessing).
    """
    if units == "m":
        return 1.0, "forced --units m"
    if units == "ft":
        return 0.3048006096, "forced --units ft (US survey foot)"

    header = src if from_header else src.header
    try:
        crs = header.parse_crs()
    except Exception as exc:                      # noqa: BLE001 - header may be junk
        log.warning(f"  could not parse header CRS ({exc}); assuming METRES")
        return 1.0, "no parsable CRS -> assumed metres"
    if crs is None:
        log.warning("  header carries no CRS; assuming METRES. Pass --units ft "
                    "if this cloud is in survey feet.")
        return 1.0, "no CRS -> assumed metres"

    try:
        axis = crs.axis_info[0]
        factor = float(axis.unit_conversion_factor)
        name = axis.unit_name
    except Exception:                             # noqa: BLE001
        log.warning("  CRS has no usable axis unit; assuming METRES")
        return 1.0, "CRS without axis unit -> assumed metres"
    if factor <= 0:
        return 1.0, "CRS unit factor <= 0 -> assumed metres"
    return factor, f"from CRS ({name}, {factor} m/unit)"


def _build_ground_dem(gx: np.ndarray, gy: np.ndarray, gz: np.ndarray,
                      cell: float):
    """Minimum-Z grid over the ground points.

    Returns (dem, nx, ny, minx, miny, cell) where `dem` is a flat float32 array
    of nx*ny cells holding +inf where no ground point landed.
    """
    minx, maxx = float(gx.min()), float(gx.max())
    miny, maxy = float(gy.min()), float(gy.max())

    while True:
        nx = max(1, int(np.ceil((maxx - minx) / cell)) + 1)
        ny = max(1, int(np.ceil((maxy - miny) / cell)) + 1)
        if nx * ny <= MAX_DEM_CELLS:
            break
        cell *= 2.0
        log.warning(f"  DEM grid would be {nx}x{ny} cells; coarsening "
                    f"--dem-cell to {cell:g}")

    ix = np.floor((gx - minx) / cell).astype(np.int64)
    iy = np.floor((gy - miny) / cell).astype(np.int64)
    np.clip(ix, 0, nx - 1, out=ix)
    np.clip(iy, 0, ny - 1, out=iy)

    dem = np.full(nx * ny, np.inf, dtype=np.float32)
    np.minimum.at(dem, iy * nx + ix, gz.astype(np.float32))
    filled = int(np.isfinite(dem).sum())
    log.info(f"  ground DEM {nx}x{ny} @ {cell:g} u, "
             f"{filled:,}/{nx*ny:,} cells populated "
             f"({100.0 * filled / (nx * ny):.1f}%)")
    return dem, nx, ny, minx, miny, cell


def _hag_for(x: np.ndarray, y: np.ndarray, z: np.ndarray,
             dem, nx, ny, minx, miny, cell,
             chunk_size: int) -> np.ndarray:
    """Height above the filled ground DEM, for every point.

    Two-step by design: a direct cell lookup covers the points that sit over
    populated ground (the vast majority), and only the leftovers pay for a
    nearest-populated-cell KD query. On a corridor cloud that is typically
    <5% of points, which is what keeps this fast enough to run on the full
    final cloud rather than a subsample.
    """
    finite = np.isfinite(dem)
    if not finite.any():
        raise RuntimeError("ground DEM is entirely empty")

    pop_flat = np.flatnonzero(finite)
    pop_iy, pop_ix = np.divmod(pop_flat, nx)
    pop_xy = np.column_stack([
        minx + (pop_ix + 0.5) * cell,
        miny + (pop_iy + 0.5) * cell,
    ])
    tree = cKDTree(pop_xy)
    pop_z = dem[pop_flat]

    n = x.size
    hag = np.empty(n, dtype=np.float32)
    n_direct = 0
    for start in range(0, n, chunk_size):
        end = min(start + chunk_size, n)
        cx = np.floor((x[start:end] - minx) / cell).astype(np.int64)
        cy = np.floor((y[start:end] - miny) / cell).astype(np.int64)
        np.clip(cx, 0, nx - 1, out=cx)
        np.clip(cy, 0, ny - 1, out=cy)
        gz = dem[cy * nx + cx]

        miss = ~np.isfinite(gz)
        n_direct += int(gz.size - miss.sum())
        if miss.any():
            idx = np.flatnonzero(miss)
            _, nn = tree.query(
                np.column_stack([x[start:end][idx], y[start:end][idx]]),
                k=1, workers=-1)
            gz[idx] = pop_z[nn]
        hag[start:end] = z[start:end] - gz

    log.info(f"  HAG: {n_direct:,}/{n:,} points over a populated ground cell "
             f"({100.0 * n_direct / max(n, 1):.1f}%); "
             f"{n - n_direct:,} via nearest-cell fallback")
    return hag


def _ensure_extra_dim(las, name: str, dtype) -> bool:
    """Add an extra dimension if absent. Returns True if it was created."""
    if name in las.point_format.extra_dimension_names:
        return False
    las.add_extra_dim(laspy.ExtraBytesParams(name=name, type=dtype))
    return True


def _class_histogram(cls: np.ndarray) -> dict[str, int]:
    vals, counts = np.unique(cls, return_counts=True)
    return {str(int(v)): int(c) for v, c in zip(vals, counts)}


def process_one(src: Path, dst: Path, *,
                low_max_m: float, med_max_m: float,
                dem_cell_m: float,
                ground_classes: tuple[int, ...],
                veg_classes: tuple[int, ...],
                units: str,
                write_hag: bool,
                chunk_size: int) -> dict:
    t0 = time.time()
    log.info(f"{src.name}: reading...")
    las = laspy.read(src, laz_backend=_laz_backend_for(src))
    n_total = len(las.points)
    if n_total == 0:
        log.warning(f"{src.name}: 0 points -- skipped")
        return {"source": src.name, "status": "empty", "n_points": 0}

    mpu, how = _metres_per_unit(las, units)
    low_max = low_max_m / mpu
    med_max = med_max_m / mpu
    dem_cell = dem_cell_m / mpu
    log.info(f"  units: {how}; thresholds {low_max_m}/{med_max_m} m "
             f"-> {low_max:.4g}/{med_max:.4g} data units, "
             f"dem-cell {dem_cell:.4g}")

    x = np.asarray(las.x)
    y = np.asarray(las.y)
    z = np.asarray(las.z)
    cls = np.asarray(las.classification, dtype=np.uint8)
    before = _class_histogram(cls)

    veg_mask = np.isin(cls, np.asarray(veg_classes, dtype=np.uint8))
    n_veg = int(veg_mask.sum())
    ground_mask = np.isin(cls, np.asarray(ground_classes, dtype=np.uint8))
    n_ground = int(ground_mask.sum())
    log.info(f"  {n_total:,} points; veg(src {list(veg_classes)})={n_veg:,}; "
             f"ground(src {list(ground_classes)})={n_ground:,}")

    if n_veg == 0:
        log.warning(f"{src.name}: no points in veg classes {list(veg_classes)} "
                    f"-- nothing to stratify (benign)")
        return {"source": src.name, "status": "no_veg", "n_points": n_total,
                "class_histogram_before": before}
    if n_ground < MIN_GROUND_POINTS:
        log.warning(f"{src.name}: only {n_ground} ground points in classes "
                    f"{list(ground_classes)} (need >= {MIN_GROUND_POINTS}) -- "
                    f"cannot build a DEM, leaving the cloud untouched (benign)")
        return {"source": src.name, "status": "no_ground", "n_points": n_total,
                "n_ground": n_ground, "class_histogram_before": before}

    dem, nx, ny, minx, miny, dem_cell = _build_ground_dem(
        x[ground_mask], y[ground_mask], z[ground_mask], dem_cell)
    hag = _hag_for(x, y, z, dem, nx, ny, minx, miny, dem_cell, chunk_size)

    # Re-label ONLY the veg points. np.where on the veg subset keeps every other
    # class (ground 2, road 40, wire 14, tower 15, pole 18/19, building 6/47)
    # byte-identical -- this stage refines vegetation and nothing else.
    vh = hag[veg_mask]
    new_veg = np.where(vh < low_max, CLASS_LOW_VEG,
                       np.where(vh < med_max, CLASS_MED_VEG, CLASS_HIGH_VEG)
                       ).astype(np.uint8)

    # Provenance: only create original_class if Stage 6 did not. When it exists
    # it already holds the PointCONV base label, which is strictly better
    # provenance than "whatever the class was one step ago".
    created_orig = _ensure_extra_dim(las, "original_class", np.uint8)
    if created_orig:
        las.original_class = cls.copy()
        log.info("  created original_class extra dim (pre-stratification label)")
    else:
        log.info("  original_class already present (Stage 6) -- left as-is")

    if write_hag:
        _ensure_extra_dim(las, "hag", np.float32)
        las.hag = hag

    out_cls = cls.copy()
    out_cls[veg_mask] = new_veg
    las.classification = out_cls
    after = _class_histogram(out_cls)

    n_low = int((new_veg == CLASS_LOW_VEG).sum())
    n_med = int((new_veg == CLASS_MED_VEG).sum())
    n_high = int((new_veg == CLASS_HIGH_VEG).sum())
    log.info(f"  veg split: low(3)={n_low:,} ({100*n_low/n_veg:.1f}%)  "
             f"med(4)={n_med:,} ({100*n_med/n_veg:.1f}%)  "
             f"high(5)={n_high:,} ({100*n_high/n_veg:.1f}%)")

    # Atomic write: a crash mid-write must not leave a truncated deliverable
    # where the previous stage's good output was (in-place is the default).
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.name + ".partial")
    las.write(tmp, laz_backend=_laz_backend_for(dst))
    os.replace(tmp, dst)
    dt = time.time() - t0
    log.info(f"  wrote {dst} ({dt:.1f}s)")

    return {
        "source": src.name,
        "output": str(dst),
        "status": "ok",
        "n_points": n_total,
        "n_ground": n_ground,
        "n_veg_in": n_veg,
        "veg_low_3": n_low,
        "veg_med_4": n_med,
        "veg_high_5": n_high,
        "hag_stats_veg": {
            "min": float(vh.min()), "median": float(np.median(vh)),
            "p95": float(np.percentile(vh, 95)), "max": float(vh.max()),
        },
        "class_histogram_before": before,
        "class_histogram_after": after,
        "seconds": round(dt, 1),
    }


def _grid_from_header(header, cell: float):
    """Grid geometry from the header bbox (streaming pass 1 can't pre-scan the
    ground extent, and the header bbox is a superset of it anyway)."""
    minx, miny = float(header.mins[0]), float(header.mins[1])
    maxx, maxy = float(header.maxs[0]), float(header.maxs[1])
    while True:
        nx = max(1, int(np.ceil((maxx - minx) / cell)) + 1)
        ny = max(1, int(np.ceil((maxy - miny) / cell)) + 1)
        if nx * ny <= MAX_DEM_CELLS:
            return nx, ny, minx, miny, cell
        cell *= 2.0
        log.warning(f"  DEM grid would be {nx}x{ny} cells; coarsening "
                    f"--dem-cell to {cell:g}")


def _cell_index(x, y, nx, ny, minx, miny, cell):
    ix = np.floor((x - minx) / cell).astype(np.int64)
    iy = np.floor((y - miny) / cell).astype(np.int64)
    np.clip(ix, 0, nx - 1, out=ix)
    np.clip(iy, 0, ny - 1, out=iy)
    return iy * nx + ix


def process_one_streaming(src: Path, dst: Path, *,
                          low_max_m: float, med_max_m: float,
                          dem_cell_m: float,
                          ground_classes: tuple[int, ...],
                          veg_classes: tuple[int, ...],
                          units: str,
                          write_hag: bool,
                          chunk_size: int) -> dict:
    """Two-pass streaming variant for clouds too large to hold in RAM.

    Pass 1 reads the cloud and accumulates the ground min-Z grid.
    Pass 2 re-reads it, computes HAG per chunk, relabels the veg points, and
    writes each chunk straight out.

    Output keeps the SOURCE point format. That is always safe here: this stage
    only ever writes classes 3/4/5, which fit the 5-bit legacy `classification`
    field, and any input already carrying class 40 (Stage 6 road) must already
    be point format >= 6. So no laspy.convert, and no 2x memory to do it.
    """
    t0 = time.time()
    in_place = src.resolve() == dst.resolve()
    veg_arr = np.asarray(veg_classes, dtype=np.uint8)
    ground_arr = np.asarray(ground_classes, dtype=np.uint8)

    with laspy.open(src, laz_backend=_laz_backend_for(src)) as reader:
        header = reader.header
        n_total = header.point_count
        log.info(f"{src.name}: {n_total:,} points -- STREAMING "
                 f"(over the {STREAM_THRESHOLD_POINTS:,}-point in-memory limit)")
        if n_total == 0:
            return {"source": src.name, "status": "empty", "n_points": 0}

        mpu, how = _metres_per_unit(header, units, from_header=True)
        low_max = low_max_m / mpu
        med_max = med_max_m / mpu
        nx, ny, minx, miny, cell = _grid_from_header(header, dem_cell_m / mpu)
        log.info(f"  units: {how}; thresholds {low_max_m}/{med_max_m} m "
                 f"-> {low_max:.4g}/{med_max:.4g} data units, "
                 f"dem-cell {cell:.4g}")
        log.info(f"  DEM grid {nx}x{ny} = {nx*ny:,} cells "
                 f"({nx*ny*4/2**20:.0f} MiB)")

        # ---- Pass 1: ground min-Z grid --------------------------------------
        dem = np.full(nx * ny, np.inf, dtype=np.float32)
        n_ground = 0
        n_veg = 0
        hist_before = np.zeros(256, dtype=np.int64)
        seen = 0
        log.info("  pass 1/2: building ground DEM...")
        for pts in reader.chunk_iterator(chunk_size):
            cls = np.asarray(pts.classification, dtype=np.uint8)
            hist_before += np.bincount(cls, minlength=256)
            n_veg += int(np.isin(cls, veg_arr).sum())
            gm = np.isin(cls, ground_arr)
            ng = int(gm.sum())
            if ng:
                n_ground += ng
                gx = np.asarray(pts.x)[gm]
                gy = np.asarray(pts.y)[gm]
                gz = np.asarray(pts.z)[gm].astype(np.float32)
                np.minimum.at(dem, _cell_index(gx, gy, nx, ny, minx, miny, cell), gz)
            seen += len(cls)
            log.info(f"    pass1 {seen:,}/{n_total:,} "
                     f"({100.0*seen/n_total:.1f}%) ground={n_ground:,} "
                     f"veg={n_veg:,}")

    before = {str(int(i)): int(v) for i, v in enumerate(hist_before) if v}
    if n_veg == 0:
        log.warning(f"{src.name}: no points in veg classes {list(veg_classes)} "
                    f"-- nothing to stratify (benign). Class histogram: "
                    f"{before}")
        return {"source": src.name, "status": "no_veg", "n_points": n_total,
                "class_histogram_before": before}
    if n_ground < MIN_GROUND_POINTS:
        log.warning(f"{src.name}: only {n_ground} ground points in classes "
                    f"{list(ground_classes)} (need >= {MIN_GROUND_POINTS}) -- "
                    f"cannot build a DEM, leaving the cloud untouched (benign)")
        return {"source": src.name, "status": "no_ground", "n_points": n_total,
                "n_ground": n_ground, "class_histogram_before": before}

    filled = int(np.isfinite(dem).sum())
    log.info(f"  ground DEM: {filled:,}/{nx*ny:,} cells populated "
             f"({100.0*filled/(nx*ny):.1f}%) from {n_ground:,} ground points")

    pop_flat = np.flatnonzero(np.isfinite(dem))
    pop_iy, pop_ix = np.divmod(pop_flat, nx)
    tree = cKDTree(np.column_stack([minx + (pop_ix + 0.5) * cell,
                                    miny + (pop_iy + 0.5) * cell]))
    pop_z = dem[pop_flat]

    # ---- Pass 2: relabel + write ---------------------------------------------
    # In-place on a multi-GB cloud can't be a true in-place rewrite, so stream
    # to a sibling .partial and os.replace at the end (same atomicity guarantee
    # as the in-memory path, and it needs the same free space as the source).
    tmp = dst.with_name(dst.name + ".partial")
    dst.parent.mkdir(parents=True, exist_ok=True)
    n_low = n_med = n_high = 0
    hist_after = np.zeros(256, dtype=np.int64)
    hag_min, hag_max, hag_sum = np.inf, -np.inf, 0.0
    seen = 0
    log.info(f"  pass 2/2: relabelling -> {tmp.name}"
             f"{' (will replace the source)' if in_place else ''}")
    with laspy.open(src, laz_backend=_laz_backend_for(src)) as reader:
        out_header = copy.deepcopy(reader.header)
        new_dims = []
        if "original_class" not in out_header.point_format.extra_dimension_names:
            new_dims.append(laspy.ExtraBytesParams("original_class", np.uint8))
            created_orig = True
        else:
            created_orig = False
        if write_hag and "hag" not in out_header.point_format.extra_dimension_names:
            new_dims.append(laspy.ExtraBytesParams("hag", np.float32))
        if new_dims:
            out_header.add_extra_dims(new_dims)
        log.info(f"  original_class: {'created' if created_orig else 'already present (Stage 6) -- left as-is'}")

        with laspy.open(tmp, mode="w", header=out_header,
                        laz_backend=_laz_backend_for(tmp)) as writer:
            for pts in reader.chunk_iterator(chunk_size):
                n = len(pts)
                x = np.asarray(pts.x)
                y = np.asarray(pts.y)
                z = np.asarray(pts.z)
                cls = np.asarray(pts.classification, dtype=np.uint8)

                gz = dem[_cell_index(x, y, nx, ny, minx, miny, cell)]
                miss = ~np.isfinite(gz)
                if miss.any():
                    idx = np.flatnonzero(miss)
                    _, nn = tree.query(np.column_stack([x[idx], y[idx]]),
                                       k=1, workers=-1)
                    gz[idx] = pop_z[nn]
                hag = (z - gz).astype(np.float32)

                vm = np.isin(cls, veg_arr)
                out_cls = cls.copy()
                if vm.any():
                    vh = hag[vm]
                    nv = np.where(vh < low_max, CLASS_LOW_VEG,
                                  np.where(vh < med_max, CLASS_MED_VEG,
                                           CLASS_HIGH_VEG)).astype(np.uint8)
                    out_cls[vm] = nv
                    n_low += int((nv == CLASS_LOW_VEG).sum())
                    n_med += int((nv == CLASS_MED_VEG).sum())
                    n_high += int((nv == CLASS_HIGH_VEG).sum())
                    hag_min = min(hag_min, float(vh.min()))
                    hag_max = max(hag_max, float(vh.max()))
                    hag_sum += float(vh.sum())
                hist_after += np.bincount(out_cls, minlength=256)

                # Copy the raw record field-for-field: exact, and it sidesteps
                # decoding/re-encoding the packed legacy bit fields.
                out = laspy.ScaleAwarePointRecord.zeros(n, header=out_header)
                for name in pts.array.dtype.names:
                    out.array[name] = pts.array[name]
                if created_orig:
                    out["original_class"] = cls
                if write_hag:
                    out["hag"] = hag
                out.classification = out_cls
                writer.write_points(out)

                seen += n
                log.info(f"    pass2 {seen:,}/{n_total:,} "
                         f"({100.0*seen/n_total:.1f}%) "
                         f"low={n_low:,} med={n_med:,} high={n_high:,}")

    # Verify before replacing anything: a short write here would silently
    # truncate the deliverable (the exact failure the Stage 6 worker warns about).
    with laspy.open(tmp) as chk:
        n_out = chk.header.point_count
    if n_out != n_total:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"streamed output has {n_out:,} points but the source "
                           f"has {n_total:,} -- refusing to publish a truncated "
                           f"cloud")
    os.replace(tmp, dst)

    dt = time.time() - t0
    log.info(f"  veg split: low(3)={n_low:,} ({100*n_low/n_veg:.1f}%)  "
             f"med(4)={n_med:,} ({100*n_med/n_veg:.1f}%)  "
             f"high(5)={n_high:,} ({100*n_high/n_veg:.1f}%)")
    log.info(f"  wrote {dst} ({dt/60:.1f} min, {n_out:,} points verified)")
    return {
        "source": src.name, "output": str(dst), "status": "ok",
        "mode": "streaming",
        "n_points": n_total, "n_ground": n_ground, "n_veg_in": n_veg,
        "veg_low_3": n_low, "veg_med_4": n_med, "veg_high_5": n_high,
        "hag_stats_veg": {"min": hag_min, "mean": hag_sum / max(n_veg, 1),
                          "max": hag_max},
        "class_histogram_before": before,
        "class_histogram_after": {str(int(i)): int(v)
                                  for i, v in enumerate(hist_after) if v},
        "seconds": round(dt, 1),
    }


def _resolve_sources(args) -> tuple[list[Path], Path | None]:
    """Return (sources, default_out_dir)."""
    if args.run_dir:
        d = args.run_dir / "06_final_classification"
        if not d.is_dir():
            raise SystemExit(
                f"no 06_final_classification/ in {args.run_dir} -- run Stage 6 "
                f"(final_classified_pointcloud.py) first")
        srcs = sorted(d.glob(args.pattern))
        if not srcs:
            raise SystemExit(f"no files matching {args.pattern} in {d}")
        return srcs, d
    src = args.input
    if src.is_dir():
        if args.input_pattern:
            srcs = sorted(src.glob(args.input_pattern))
            if not srcs:
                raise SystemExit(f"no files matching {args.input_pattern!r} "
                                 f"under {src}")
        else:
            srcs = sorted(list(src.glob("*.las")) + list(src.glob("*.laz")))
            if not srcs:
                # Stage 1 nests each tile in its own subdir
                # (class_out/<tile>/<tile>_t_raw.las), so fall back to one
                # level down before giving up.
                srcs = sorted(src.glob("*/*_t_raw.la[sz]"))
                if srcs:
                    log.info(f"  no LAS at the top level; using "
                             f"{len(srcs)} */*_t_raw.la[sz] under {src}")
            if not srcs:
                raise SystemExit(
                    f"no LAS/LAZ files in {src} (and no */*_t_raw.las "
                    f"either). Pass --input-pattern to select explicitly.")
        return srcs, src
    if not src.exists():
        raise SystemExit(f"input not found: {src}")
    return [src], src.parent


def main() -> int:
    _setup_logging()
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--run-dir", type=Path,
                     help="Chain run dir; stratifies "
                          "06_final_classification/*_final_classified.laz "
                          "in place.")
    src.add_argument("--input", type=Path,
                     help="A single LAS/LAZ, or a directory of them.")
    p.add_argument("--out-dir", type=Path, default=None,
                   help="Write here instead of in place. Default: in place "
                        "(atomic replace) so Stage 7/8 keep globbing the same "
                        "*_final_classified.laz names.")
    p.add_argument("--suffix", default="",
                   help="Append to the output stem, e.g. '_veg'. Implies "
                        "not-in-place. NOTE: a suffixed file no longer matches "
                        "Stage 7/8's *_final_classified.laz glob.")
    p.add_argument("--pattern", default="*_final_classified.la[sz]",
                   help="Glob inside 06_final_classification/ (--run-dir mode).")
    p.add_argument("--input-pattern", default=None,
                   help="Glob applied under a directory --input, e.g. "
                        "'*/*_t_raw.las' for a Stage-1 class_out tree or "
                        "'**/*.laz' to recurse. Without it, top-level "
                        "*.las/*.laz is tried first, then */*_t_raw.la[sz].")
    p.add_argument("--low-max", type=float, default=0.5, metavar="M",
                   help="HAG (METRES) below which veg is LOW (class 3). "
                        "Default 0.5.")
    p.add_argument("--med-max", type=float, default=2.0, metavar="M",
                   help="HAG (METRES) below which veg is MEDIUM (class 4); "
                        "at or above is HIGH (class 5). Default 2.0.")
    p.add_argument("--dem-cell", type=float, default=1.0, metavar="M",
                   help="Ground min-Z DEM cell size in METRES. Default 1.0. "
                        "Coarser is smoother but flattens real relief; finer "
                        "leaves more empty cells for the NN fallback.")
    p.add_argument("--ground-classes", default="2,40",
                   help="Comma-separated classes forming the ground surface. "
                        "Default '2,40' (2 ground + 40 SDAI road, which IS "
                        "ground refined by Stage 6).")
    p.add_argument("--veg-classes", default="5",
                   help="Comma-separated classes to split. Default '5' "
                        "(PointCONV folds all veg into 5). Use '3,4,5' to "
                        "re-stratify at new thresholds.")
    p.add_argument("--units", choices=("auto", "m", "ft"), default="auto",
                   help="Data XY/Z units. 'auto' (default) reads the header "
                        "CRS and converts the metre thresholds into data "
                        "units; falls back to metres with a WARN if there is "
                        "no CRS.")
    p.add_argument("--no-hag", dest="write_hag", action="store_false",
                   help="Do not write the 'hag' float32 extra dimension "
                        "(it costs 4 B/point).")
    p.add_argument("--chunk-size", type=int, default=5_000_000,
                   help="Points per HAG lookup chunk (in-memory path) or per "
                        "read/write chunk (streaming path). Default 5,000,000.")
    p.add_argument("--streaming", choices=("auto", "on", "off"), default="auto",
                   help="'auto' (default) streams any cloud over "
                        "--stream-threshold points and reads smaller ones "
                        "whole. 'on' forces the two-pass streaming path "
                        "(bounded RAM, reads the file twice); 'off' forces the "
                        "in-memory path and will MemoryError on a huge cloud.")
    p.add_argument("--stream-threshold", type=int,
                   default=STREAM_THRESHOLD_POINTS,
                   help=f"Point count above which --streaming auto switches to "
                        f"the streaming path. Default "
                        f"{STREAM_THRESHOLD_POINTS:,}.")
    p.add_argument("--summary-json", type=Path, default=None,
                   help="Default: <out_dir>/veg_stratification_summary.json")
    args = p.parse_args()

    if args.med_max <= args.low_max:
        raise SystemExit(f"--med-max ({args.med_max}) must exceed --low-max "
                         f"({args.low_max})")
    ground_classes = tuple(int(v) for v in args.ground_classes.split(",") if v.strip())
    veg_classes = tuple(int(v) for v in args.veg_classes.split(",") if v.strip())
    if not veg_classes:
        raise SystemExit("--veg-classes is empty")
    overlap = set(ground_classes) & set(veg_classes)
    if overlap:
        raise SystemExit(f"--ground-classes and --veg-classes overlap on "
                         f"{sorted(overlap)}; the DEM would be built from the "
                         f"points being stratified")

    sources, default_out = _resolve_sources(args)
    in_place = args.out_dir is None and not args.suffix
    out_dir = args.out_dir or default_out
    log.info(f"veg stratification: {len(sources)} source(s); "
             f"{'IN PLACE' if in_place else f'-> {out_dir}'}")
    log.info(f"  low(3) < {args.low_max} m <= med(4) < {args.med_max} m "
             f"<= high(5)")

    results, n_ok = [], 0
    for s in sources:
        dst = s if in_place else out_dir / f"{s.stem}{args.suffix}{s.suffix}"
        try:
            if args.streaming == "on":
                stream = True
            elif args.streaming == "off":
                stream = False
            else:
                with laspy.open(s, laz_backend=_laz_backend_for(s)) as _r:
                    stream = _r.header.point_count > args.stream_threshold
            fn = process_one_streaming if stream else process_one
            r = fn(
                s, dst,
                low_max_m=args.low_max, med_max_m=args.med_max,
                dem_cell_m=args.dem_cell,
                ground_classes=ground_classes, veg_classes=veg_classes,
                units=args.units, write_hag=args.write_hag,
                chunk_size=args.chunk_size)
        except Exception as exc:                  # noqa: BLE001 - per-source isolation
            log.error(f"{s.name}: FAILED -- {exc}")
            results.append({"source": s.name, "status": "error",
                            "error": str(exc)})
            continue
        results.append(r)
        if r["status"] == "ok":
            n_ok += 1

    totals = {
        "veg_low_3": sum(r.get("veg_low_3", 0) for r in results),
        "veg_med_4": sum(r.get("veg_med_4", 0) for r in results),
        "veg_high_5": sum(r.get("veg_high_5", 0) for r in results),
    }
    summary = {
        "worker": "stratify_vegetation.py",
        "params": {
            "low_max_m": args.low_max, "med_max_m": args.med_max,
            "dem_cell_m": args.dem_cell,
            "ground_classes": list(ground_classes),
            "veg_classes": list(veg_classes),
            "units": args.units, "in_place": in_place,
            "write_hag": args.write_hag,
        },
        "n_sources": len(sources), "n_ok": n_ok,
        "totals": totals, "sources": results,
    }
    sj = args.summary_json or (out_dir / "veg_stratification_summary.json")
    try:
        sj.parent.mkdir(parents=True, exist_ok=True)
        sj.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        log.info(f"summary -> {sj}")
    except OSError as exc:
        log.warning(f"could not write summary {sj}: {exc}")

    n_err = sum(1 for r in results if r["status"] == "error")
    log.info(f"DONE: {n_ok} stratified, {n_err} failed, "
             f"{len(results) - n_ok - n_err} benign-skipped. "
             f"low(3)={totals['veg_low_3']:,} med(4)={totals['veg_med_4']:,} "
             f"high(5)={totals['veg_high_5']:,}")
    if n_err:
        return 1
    if n_ok == 0:
        return 3          # benign empty: nothing had veg + ground to work on
    return 0


if __name__ == "__main__":
    sys.exit(main())
