"""Restore RGB (and intensity) onto classified clouds from their source tiles.

WHY
---
Stage 1's Wave-B writer (`pointconv_infer.py` ->
`classification_from_patches.py`) emits point format 7, so the `red`/`green`/
`blue` dimensions EXIST — but it never carries the source colour through, so they
are all **zero**. `intensity` is dropped the same way. A viewer shows a
black cloud and nothing errors, which makes this easy to ship by accident.

Verified on Otter Creek (2026-08-19): source `clipped.las` is 99.2 % non-zero RGB
(16-bit, max 65024); every `*_combined_0p1m.laz` and every downstream
`*_final_classified.laz` is 0.0 % non-zero. The loss is at Stage 1 — the CPU
chain (Stage 6 / 6v / noise) preserves whatever it is given.

> The older `classify.sh` + v0.0.10 path does NOT have this problem: its
> `_t_raw.las` keeps RGB. Only the dim-6 `point-conv-distribution` path drops it.

EXACT, NOT INTERPOLATED
-----------------------
The classified points are the original points (0.1 m voxel representatives), not
resampled positions: a KD-tree query from classified -> source returns
**distance 0.0 for 100 %** of points. So this is an exact per-point join, and the
worker asserts it — `--max-dist 0.0` by default, so any tile that does not match
exactly is reported and skipped instead of silently getting approximate colour.
Raise `--max-dist` (and accept nearest-neighbour colour) only deliberately.

Pairing: `<stem>_final_classified.laz` <- `<stem>.las` in `--source-dir`
(`--strip` controls the suffix removed to recover the source stem).

EXIT CODES: 0 all tiles restored, 3 some skipped, 1 nothing done.
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

DEFAULT_FIELDS = ("red", "green", "blue", "intensity")
log = logging.getLogger("restore-rgb")


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


def _xyz(las) -> np.ndarray:
    return np.column_stack([np.asarray(las.x), np.asarray(las.y),
                            np.asarray(las.z)])


def process_pair(cls_path: Path, src_path: Path, dst: Path, *,
                 fields: tuple[str, ...], max_dist: float,
                 chunk: int) -> dict:
    t0 = time.time()
    log.info(f"{cls_path.name}")
    cls = laspy.read(cls_path, laz_backend=_laz_backend_for(cls_path))
    n = len(cls.points)
    if n == 0:
        return {"tile": cls_path.name, "status": "empty"}
    src = laspy.read(src_path, laz_backend=_laz_backend_for(src_path))

    src_dims = {d.name for d in src.point_format.dimensions}
    cls_dims = {d.name for d in cls.point_format.dimensions}
    use = [f for f in fields if f in src_dims and f in cls_dims]
    missing = [f for f in fields if f not in use]
    if not use:
        return {"tile": cls_path.name, "status": "no_common_fields",
                "missing": missing}

    log.info(f"  {n:,} classified pts <- {len(src.points):,} source pts; "
             f"fields {use}" + (f"  (skipped {missing})" if missing else ""))
    tree = cKDTree(_xyz(src))
    cx = _xyz(cls)
    dmax = 0.0
    idx = np.empty(n, dtype=np.int64)
    for s in range(0, n, chunk):
        e = min(s + chunk, n)
        d, i = tree.query(cx[s:e], k=1, workers=-1)
        idx[s:e] = i
        dmax = max(dmax, float(d.max()))
    log.info(f"  max source distance {dmax:.6g} m "
             f"({'EXACT' if dmax == 0.0 else 'approximate'})")
    if dmax > max_dist:
        log.warning(f"  SKIPPED: max distance {dmax:.6g} exceeds --max-dist "
                    f"{max_dist:.6g}. Wrong source tile, or the classified "
                    f"points are not original points. Not writing.")
        return {"tile": cls_path.name, "status": "distance_exceeded",
                "max_dist_m": dmax}

    before = {}
    for f in use:
        v = np.asarray(src[f])[idx]
        before[f] = float((np.asarray(cls[f]) != 0).mean())
        cls[f] = v
    after = {f: float((np.asarray(cls[f]) != 0).mean()) for f in use}

    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.name + ".partial")
    cls.write(tmp, laz_backend=_laz_backend_for(dst))
    os.replace(tmp, dst)
    dt = time.time() - t0
    log.info("  " + "  ".join(
        f"{f}: {100*before[f]:.1f}% -> {100*after[f]:.1f}% non-zero"
        for f in use) + f"  ({dt:.1f}s)")
    return {"tile": cls_path.name, "source": src_path.name, "status": "ok",
            "n_points": n, "fields": use, "max_dist_m": dmax,
            "nonzero_before": before, "nonzero_after": after,
            "seconds": round(dt, 1)}


def main() -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s",
                        handlers=[logging.StreamHandler(sys.stdout)],
                        force=True)
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--classified-dir", required=True, type=Path,
                   help="Dir of classified clouds to repair.")
    p.add_argument("--source-dir", required=True, type=Path,
                   help="Dir of the ORIGINAL tiles that still carry colour "
                        "(the tiles Stage 1 consumed).")
    p.add_argument("--out-dir", type=Path, default=None,
                   help="Default: in place (atomic replace).")
    p.add_argument("--pattern", default="*_final_classified.la[sz]")
    p.add_argument("--strip", default="_final_classified",
                   help="Removed from the classified stem to get the source "
                        "stem. Use '_tf1_pointconv_combined_0p1m' to repair "
                        "Stage-1 output directly.")
    p.add_argument("--source-ext", default=".las")
    p.add_argument("--fields", default=",".join(DEFAULT_FIELDS),
                   help=f"Comma-separated dims to copy. Default "
                        f"{','.join(DEFAULT_FIELDS)}.")
    p.add_argument("--max-dist", type=float, default=0.0,
                   help="Max allowed classified->source distance (m). Default "
                        "0.0 = require an EXACT point-for-point match. A tile "
                        "over this is skipped, not approximated.")
    p.add_argument("--chunk", type=int, default=5_000_000)
    p.add_argument("--summary-json", type=Path, default=None)
    a = p.parse_args()

    fields = tuple(f.strip() for f in a.fields.split(",") if f.strip())
    tiles = sorted(a.classified_dir.glob(a.pattern))
    if not tiles:
        raise SystemExit(f"no files matching {a.pattern} in {a.classified_dir}")
    in_place = a.out_dir is None
    out_dir = a.out_dir or a.classified_dir
    log.info(f"restoring {fields} on {len(tiles)} tile(s); "
             f"{'IN PLACE' if in_place else f'-> {out_dir}'}")

    results, n_ok, n_skip = [], 0, 0
    t0 = time.time()
    for i, c in enumerate(tiles, 1):
        stem = c.stem
        if a.strip and stem.endswith(a.strip):
            stem = stem[: -len(a.strip)]
        src = a.source_dir / f"{stem}{a.source_ext}"
        if not src.exists():
            alt = list(a.source_dir.glob(f"{stem}.la[sz]"))
            if alt:
                src = alt[0]
            else:
                log.warning(f"[{i}/{len(tiles)}] {c.name}: no source tile "
                            f"{stem}{a.source_ext} -- skipped")
                results.append({"tile": c.name, "status": "no_source",
                                "expected": str(src)})
                n_skip += 1
                continue
        log.info(f"[{i}/{len(tiles)}]")
        try:
            r = process_pair(c, src, (c if in_place else out_dir / c.name),
                             fields=fields, max_dist=a.max_dist, chunk=a.chunk)
        except Exception as exc:                    # noqa: BLE001
            log.error(f"  FAILED: {exc}")
            results.append({"tile": c.name, "status": "error",
                            "error": str(exc)})
            n_skip += 1
            continue
        results.append(r)
        if r["status"] == "ok":
            n_ok += 1
        else:
            n_skip += 1

    summary = {"worker": "restore_rgb.py", "fields": list(fields),
               "max_dist_m": a.max_dist, "in_place": in_place,
               "n_tiles": len(tiles), "n_ok": n_ok, "n_skipped": n_skip,
               "elapsed_sec": round(time.time() - t0, 1), "tiles": results}
    sj = a.summary_json or (out_dir / "rgb_restore_summary.json")
    try:
        sj.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        log.info(f"summary -> {sj}")
    except OSError as exc:
        log.warning(f"could not write summary: {exc}")

    log.info(f"DONE in {(time.time()-t0)/60:.1f} min: {n_ok} restored, "
             f"{n_skip} skipped")
    if n_ok == 0:
        return 1
    return 3 if n_skip else 0


if __name__ == "__main__":
    sys.exit(main())
