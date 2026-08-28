"""Split vegetation (class 5) into Low(3)/Med(4)/High(5) by height-above-ground.
Chunked + memory-safe (handles 600M+ pt tiles). Two passes:
  1) stream class-2 ground -> min-Z DEM grid (gap-filled by nearest cell)
  2) stream all points -> re-stamp class-5 by HAG band; write reclassified LAZ.
Bands: HAG < LOW  -> 3 ; LOW <= HAG < MED -> 4 ; HAG >= MED -> 5.
"""
import sys, argparse
import numpy as np, laspy
from scipy.ndimage import distance_transform_edt

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--split-dir", default=None,
                    help="also write per-tier veg LAZ: low.laz/med.laz/high.laz here")
    ap.add_argument("--ground-class", type=int, default=2)
    ap.add_argument("--veg-class", type=int, default=5)
    ap.add_argument("--low", type=float, default=1.0)   # HAG < low -> class 3
    ap.add_argument("--med", type=float, default=3.0)   # low<=HAG<med -> 4 ; >=med -> 5
    ap.add_argument("--cell", type=float, default=1.0)
    ap.add_argument("--chunk", type=int, default=5_000_000)
    a = ap.parse_args()

    # ---- pass 1: ground min-Z DEM ----
    minz = None; ix0 = iy0 = nx = ny = None
    xmin = ymin = xmax = ymax = None
    with laspy.open(a.input) as r:
        for ch in r.chunk_iterator(a.chunk):
            m = np.asarray(ch.classification) == a.ground_class
            if not m.any():
                continue
            x = np.asarray(ch.x)[m]; y = np.asarray(ch.y)[m]; z = np.asarray(ch.z)[m]
            lo_x, lo_y, hi_x, hi_y = x.min(), y.min(), x.max(), y.max()
            xmin = lo_x if xmin is None else min(xmin, lo_x); ymin = lo_y if ymin is None else min(ymin, lo_y)
            xmax = hi_x if xmax is None else max(xmax, hi_x); ymax = hi_y if ymax is None else max(ymax, hi_y)
    if xmin is None:
        sys.exit("no ground points; cannot reclassify")
    ix0 = int(np.floor(xmin / a.cell)); iy0 = int(np.floor(ymin / a.cell))
    nx = int(np.floor(xmax / a.cell)) - ix0 + 1; ny = int(np.floor(ymax / a.cell)) - iy0 + 1
    minz = np.full(nx * ny, np.inf, dtype=np.float64)
    with laspy.open(a.input) as r:
        for ch in r.chunk_iterator(a.chunk):
            m = np.asarray(ch.classification) == a.ground_class
            if not m.any():
                continue
            x = np.asarray(ch.x)[m]; y = np.asarray(ch.y)[m]; z = np.asarray(ch.z)[m]
            flat = (np.floor(y / a.cell).astype(np.int64) - iy0) * nx + (np.floor(x / a.cell).astype(np.int64) - ix0)
            np.minimum.at(minz, flat, z)
    dem = minz.reshape(ny, nx)
    nanm = ~np.isfinite(dem)
    if nanm.any():
        idx = distance_transform_edt(nanm, return_distances=False, return_indices=True)
        dem = dem[tuple(idx)]

    def ground_at(x, y):
        col = np.clip(np.floor(x / a.cell).astype(np.int64) - ix0, 0, nx - 1)
        row = np.clip(np.floor(y / a.cell).astype(np.int64) - iy0, 0, ny - 1)
        return dem[row, col]

    # ---- pass 2: re-stamp class-5 by HAG, write full + optional per-tier ----
    import os, contextlib
    counts = {3: 0, 4: 0, 5: 0}
    with laspy.open(a.input) as r:
        hdr = r.header
        with contextlib.ExitStack() as stack:
            w = stack.enter_context(laspy.open(a.output, mode="w", header=hdr))
            tier_w = {}
            if a.split_dir:
                os.makedirs(a.split_dir, exist_ok=True)
                for k, nm in ((3, "low"), (4, "med"), (5, "high")):
                    tier_w[k] = stack.enter_context(
                        laspy.open(os.path.join(a.split_dir, nm + ".laz"), mode="w", header=hdr))
            for ch in r.chunk_iterator(a.chunk):
                c = np.asarray(ch.classification)
                vm = c == a.veg_class
                cc = c
                if vm.any():
                    x = np.asarray(ch.x)[vm]; y = np.asarray(ch.y)[vm]; z = np.asarray(ch.z)[vm]
                    hag = z - ground_at(x, y)
                    newc = np.full(vm.sum(), 5, dtype=c.dtype)
                    newc[hag < a.low] = 3
                    newc[(hag >= a.low) & (hag < a.med)] = 4
                    cc = c.copy(); cc[vm] = newc
                    ch.classification = cc
                    for k in (3, 4, 5):
                        counts[k] += int((newc == k).sum())
                w.write_points(ch)
                for k, tw in tier_w.items():
                    sel = cc == k
                    if sel.any():
                        tw.write_points(ch[sel])
    tot = sum(counts.values())
    print(f"reclassified veg: Low(3)={counts[3]:,} Med(4)={counts[4]:,} High(5)={counts[5]:,} of {tot:,}", flush=True)

if __name__ == "__main__":
    main()
