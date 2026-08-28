"""Label statistical outliers as class 7 (ASPRS "Low point / noise").

This is the labelling counterpart to the chain's Stage 0e
(`outlier_removal_las_dir.py`), which REMOVES isolated points from the corridor
crops *before* inference so they cannot corrupt the classification. Running 0e
is still the right thing for classification quality — but it deletes the points,
so no class-7 ever reaches the deliverable.

This worker instead runs the same statistical k-NN isolation test on an
already-classified cloud and rewrites the outliers' `classification` to 7,
keeping the points. Use it when the deliverable is supposed to carry a noise
class rather than be silently thinned.

Method (mirrors Stage 0e's Statistical Outlier Removal): for each point take the
mean distance to its `--k` nearest neighbours; the cutoff is
`mean + std_ratio * std` over all those mean distances, floored at
`--min-threshold` metres so a very clean, very dense cloud does not start
flagging good points. Points above the cutoff become class 7.

Stage 0e's defaults are k=16, std_ratio=6.0, floor 1.0 m, and those are kept
here. 0e measured ~0.09 % removed on real mobile data; a similar order is the
sanity check.

IMPORTANT — this OVERWRITES the semantic label of the points it flags. The
pre-existing label is preserved in `original_class` when that dimension does not
already exist; when Stage 6 already created it, it is left alone (it holds the
PointCONV base label, which is better provenance). Ground is excluded from
flagging by default (`--protect-classes 2,40`): a sparse road edge is not noise.

EXIT CODES: 0 success, 3 benign (nothing flagged anywhere), 1 error.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path

import laspy
import numpy as np
from scipy.spatial import cKDTree

CLASS_NOISE = 7
log = logging.getLogger("mark-noise")


def _laz_backend_for(path: Path):
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


def process_one(src: Path, dst: Path, *, k: int, std_ratio: float,
                min_threshold: float, protect: tuple[int, ...],
                max_points: int, query_chunk: int) -> dict:
    t0 = time.time()
    log.info(f"{src.name}: reading...")
    las = laspy.read(src, laz_backend=_laz_backend_for(src))
    n = len(las.points)
    if n == 0:
        return {"source": src.name, "status": "empty"}
    if n > max_points:
        log.warning(f"{src.name}: {n:,} points exceeds --max-points "
                    f"{max_points:,} -- the k-NN tree would not fit; skipped. "
                    f"Raise --max-points if you have the RAM.")
        return {"source": src.name, "status": "too_large", "n_points": n}

    cls = np.asarray(las.classification, dtype=np.uint8)
    xyz = np.column_stack([np.asarray(las.x), np.asarray(las.y),
                           np.asarray(las.z)])
    log.info(f"  {n:,} points; building k-NN tree (k={k})...")
    tree = cKDTree(xyz)

    # Query in chunks. The full (N, k+1) distance matrix is the memory wall
    # here, not the tree: at N=126M, k=16 it is 24 GiB of float64 that we only
    # ever reduce to one mean per point. Chunking the QUERY keeps peak at
    # chunk*(k+1)*12 B while the result is bit-identical.
    mean_d = np.empty(n, dtype=np.float64)
    for start in range(0, n, query_chunk):
        end = min(start + query_chunk, n)
        # k+1 because the query returns the point itself at distance 0.
        d, _ = tree.query(xyz[start:end], k=k + 1, workers=-1)
        mean_d[start:end] = d[:, 1:].mean(axis=1)
        if n > query_chunk:
            log.info(f"    k-NN {end:,}/{n:,} ({100.0*end/n:.0f}%)")
    cutoff = float(mean_d.mean() + std_ratio * mean_d.std())
    cutoff = max(cutoff, min_threshold)
    log.info(f"  mean-NN distance: mean {mean_d.mean():.4f} "
             f"std {mean_d.std():.4f} -> cutoff {cutoff:.4f} m "
             f"(floor {min_threshold})")

    flag = mean_d > cutoff
    if protect:
        protected = np.isin(cls, np.asarray(protect, dtype=np.uint8))
        n_saved = int((flag & protected).sum())
        flag &= ~protected
        if n_saved:
            log.info(f"  protected {n_saved:,} flagged points in classes "
                     f"{list(protect)}")
    n_flag = int(flag.sum())
    log.info(f"  flagged {n_flag:,} / {n:,} ({100.0*n_flag/n:.3f}%) -> class 7")
    if n_flag == 0:
        return {"source": src.name, "status": "none_flagged", "n_points": n,
                "cutoff_m": cutoff}

    if "original_class" not in las.point_format.extra_dimension_names:
        las.add_extra_dim(laspy.ExtraBytesParams("original_class", np.uint8))
        las.original_class = cls.copy()
        log.info("  created original_class extra dim")
    else:
        log.info("  original_class already present -- left as-is")

    was = {str(int(v)): int(c) for v, c in
           zip(*np.unique(cls[flag], return_counts=True))}
    out = cls.copy()
    out[flag] = CLASS_NOISE
    las.classification = out

    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.name + ".partial")
    las.write(tmp, laz_backend=_laz_backend_for(dst))
    os.replace(tmp, dst)
    dt = time.time() - t0
    log.info(f"  wrote {dst} ({dt:.1f}s)")
    return {"source": src.name, "status": "ok", "n_points": n,
            "n_noise": n_flag, "cutoff_m": cutoff,
            "reclassified_from": was, "seconds": round(dt, 1)}


def main() -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s",
                        handlers=[logging.StreamHandler(sys.stdout)],
                        force=True)
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--input", required=True, type=Path,
                   help="A LAS/LAZ file or a directory of them.")
    p.add_argument("--out-dir", type=Path, default=None,
                   help="Default: in place (atomic replace).")
    p.add_argument("--suffix", default="")
    p.add_argument("--pattern", default="*.la[sz]")
    p.add_argument("--k", type=int, default=16,
                   help="Neighbours per point (Stage 0e default 16).")
    p.add_argument("--std-ratio", type=float, default=6.0,
                   help="Cutoff = mean + std_ratio*std (Stage 0e default 6.0).")
    p.add_argument("--min-threshold", type=float, default=1.0,
                   help="Floor on the cutoff, metres (Stage 0e default 1.0) -- "
                        "stops a dense clean cloud flagging good points.")
    p.add_argument("--protect-classes", default="2,40",
                   help="Never flag these classes. Default '2,40' (ground and "
                        "road: a sparse pavement edge is not noise).")
    p.add_argument("--max-points", type=int, default=400_000_000,
                   help="Refuse a cloud larger than this. The k-NN TREE is still "
                        "in-memory (~30 B/point) even though the query is "
                        "chunked, so this bounds RAM. Default 400,000,000 "
                        "(~12 GiB tree+coords at pf7); lower it on a small host.")
    p.add_argument("--summary-json", type=Path, default=None,
                   help="Default: <out_dir>/noise_marking_summary.json. Point "
                        "it elsewhere when re-running single files so an "
                        "existing whole-run summary is not overwritten.")
    p.add_argument("--query-chunk", type=int, default=10_000_000,
                   help="Points per k-NN query batch. Bounds the (chunk, k+1) "
                        "distance matrix, which is the real memory wall. "
                        "Default 10,000,000 (~2 GiB at k=16).")
    a = p.parse_args()

    protect = tuple(int(v) for v in a.protect_classes.split(",") if v.strip())
    src = a.input
    if src.is_dir():
        sources = sorted(src.glob(a.pattern))
        if not sources:
            raise SystemExit(f"no files matching {a.pattern} in {src}")
        default_out = src
    elif src.is_file():
        sources, default_out = [src], src.parent
    else:
        raise SystemExit(f"input not found: {src}")

    in_place = a.out_dir is None and not a.suffix
    out_dir = a.out_dir or default_out
    log.info(f"noise marking: {len(sources)} source(s); "
             f"{'IN PLACE' if in_place else f'-> {out_dir}'}")

    results, n_ok, n_err = [], 0, 0
    for s in sources:
        dst = s if in_place else out_dir / f"{s.stem}{a.suffix}{s.suffix}"
        try:
            r = process_one(s, dst, k=a.k, std_ratio=a.std_ratio,
                            min_threshold=a.min_threshold, protect=protect,
                            max_points=a.max_points,
                            query_chunk=a.query_chunk)
        except Exception as exc:                  # noqa: BLE001
            log.error(f"{s.name}: FAILED -- {exc}")
            results.append({"source": s.name, "status": "error",
                            "error": str(exc)})
            n_err += 1
            continue
        results.append(r)
        if r["status"] == "ok":
            n_ok += 1

    total = sum(r.get("n_noise", 0) for r in results)
    sj = a.summary_json or (out_dir / "noise_marking_summary.json")
    try:
        sj.write_text(json.dumps(
            {"params": {"k": a.k, "std_ratio": a.std_ratio,
                        "min_threshold": a.min_threshold,
                        "protect_classes": list(protect)},
             "n_sources": len(sources), "n_ok": n_ok,
             "total_noise_points": total, "sources": results}, indent=2),
            encoding="utf-8")
        log.info(f"summary -> {sj}")
    except OSError as exc:
        log.warning(f"could not write summary: {exc}")

    log.info(f"DONE: {n_ok} marked, {n_err} failed; "
             f"{total:,} points -> class 7")
    if n_err:
        return 1
    return 0 if n_ok else 3


if __name__ == "__main__":
    sys.exit(main())
