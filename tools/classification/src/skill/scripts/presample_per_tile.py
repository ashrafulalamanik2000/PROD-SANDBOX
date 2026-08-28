"""Presample dim-6 PointCONV patches ONE TILE PER PROCESS (crash workaround).

WHY
---
`point-conv-distribution/scripts/presample_pointconv_patches.py` accumulates state
across the files it processes in a single run, and on dense data that state
eventually kills the process: no Python traceback, no manifest, an orphan
`<stem>_thin_tmp.las`, exit 1, usually preceded by
`geometry_features.py: RuntimeWarning: All-NaN slice encountered`.

Proven on the Otter Creek delivery (2026-08-19): tile `s_0_0.las` **fails as the
4th file of a batch but succeeds alone** — 114 patches, 14.2 s, exit 0. The crash
is therefore cumulative-process-state, NOT a property of any tile:

  * not size          -- a 645 k-point tile fails, an 18.9 M one succeeded once
  * not thread count  -- `--sample-threads 1` fails identically
  * not worker count  -- `--workers 1` fails identically
  * not duplicate XYZ -- a 21.8 % duplicate tile passed, an 18.7 % one failed
  * not memory        -- 110 GiB free throughout
  * nondeterministic  -- the same tile can pass in one run and die in the next

So: give every tile a fresh process. Each subprocess presamples exactly one tile
and exits, which discards whatever accumulates.

This is a WORKAROUND, not a fix. The underlying fault is in the vendored sampler.

RESUMABLE
---------
A tile whose `<stem>_patches.npz` already exists is skipped, so re-running picks up
where a previous pass stopped. That also means a genuinely bad tile fails in
isolation and is skipped by name instead of taking the whole run down.

AFTER THIS
----------
`pointconv_infer.py` skips its own presample when `*_patches.npz` are present, so
run this first and then call it to do only the GPU classification:

    python presample_per_tile.py --source-dir <RUN>/01_pointconv/source \\
                                 --patches-dir <RUN>/01_pointconv/patches
    python <dist>/scripts/pointconv_infer.py --run-dir <RUN> --epsg 26917 \\
        --batch-size 12

EXIT CODES: 0 = every tile has patches, 3 = some tiles failed (others usable),
1 = nothing produced.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

DIST_DEFAULT = Path(
    "C:/Users/sdaiprod/source/agentic-workflows/Greg_Sandbox/"
    "standalone-skill-distribution/point-conv-distribution")
MIN_POINTS = 24_576   # min_pts_in_region -- below this the sampler returns None
EXPECTED_KEYS = ("xyz_orig", "xyz_class", "patch_indices", "patch_count",
                 "mask_predict", "config_hash")


def npz_is_valid(path: Path) -> bool:
    """A written .npz is NOT proof of success.

    The flaky presample can emit a file that exists and looks plausible but
    contains an object array, which the GPU step then rejects with
    `ValueError: Cannot load file containing pickled data when allow_pickle=False`
    -- 143 tiles in, after the whole run. Validate the same way the consumer
    loads it, and check the keys are present, so a corrupt tile is caught and
    retried here instead of failing the GPU pass.
    """
    if not path.exists() or path.stat().st_size == 0:
        return False
    try:
        import numpy as np
        with np.load(path, allow_pickle=False) as d:
            keys = set(d.keys())
            if not set(EXPECTED_KEYS) <= keys:
                return False
            # touch each array so a lazily-decoded corrupt member surfaces now
            for k in EXPECTED_KEYS:
                _ = d[k].shape
        return True
    except Exception:                              # noqa: BLE001
        return False


def log(m: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--source-dir", required=True, type=Path,
                   help="Dir of tiles to presample (<RUN>/01_pointconv/source).")
    p.add_argument("--patches-dir", required=True, type=Path,
                   help="Dir to collect <stem>_patches.npz into.")
    p.add_argument("--dist", type=Path, default=DIST_DEFAULT,
                   help="point-conv-distribution root.")
    p.add_argument("--model-name",
                   default="PointCONV_model_6class_Mobile_v0.0.18_retune_c2")
    p.add_argument("--inputconfig", default="tf1/inputconfig_finetune_lowmem.yml",
                   help="Relative to PointCONV_TF1_Workflow/. MUST match the one "
                        "the GPU step uses -- a config_hash check enforces it.")
    p.add_argument("--random-seed", type=int, default=42)
    p.add_argument("--sample-threads", type=int, default=None)
    p.add_argument("--retries", type=int, default=1,
                   help="Extra attempts per tile. The crash is nondeterministic, "
                        "so a retry in a fresh process often succeeds. Default 1.")
    p.add_argument("--python", default=sys.executable)
    p.add_argument("--pattern", default="*.la[sz]")
    args = p.parse_args()

    presample = args.dist / "scripts" / "presample_pointconv_patches.py"
    inputconfig = args.dist / "PointCONV_TF1_Workflow" / args.inputconfig
    model_dir = (args.dist / "PointCONV_TF1_Workflow" / "models" /
                 args.model_name)
    for pth, what in ((presample, "presample worker"),
                      (inputconfig, "inputconfig"),
                      (model_dir, "model dir")):
        if not pth.exists():
            raise SystemExit(f"{what} not found: {pth}")

    tiles = sorted(t for t in args.source_dir.glob(args.pattern)
                   if not t.name.endswith("_thin_tmp.las"))
    if not tiles:
        raise SystemExit(f"no tiles matching {args.pattern} in {args.source_dir}")
    args.patches_dir.mkdir(parents=True, exist_ok=True)

    stage = args.patches_dir / "_one_tile_stage"
    results, n_ok, n_skip, n_fail, n_small = [], 0, 0, 0, 0
    t0 = time.time()
    log(f"presampling {len(tiles)} tile(s), one process each "
        f"(retries={args.retries})")

    for i, tile in enumerate(tiles, 1):
        npz = args.patches_dir / f"{tile.stem}_patches.npz"
        if npz_is_valid(npz):
            n_skip += 1
            results.append({"tile": tile.name, "status": "already_done"})
            continue
        if npz.exists():
            log(f"[{i}/{len(tiles)}] {tile.name}: existing npz is CORRUPT "
                f"(won't load with allow_pickle=False) -- regenerating")
            npz.unlink(missing_ok=True)

        # Undersized tiles fail deterministically; don't waste a process.
        try:
            import laspy
            n_pts = laspy.open(tile).header.point_count
        except Exception:                          # noqa: BLE001
            n_pts = None
        if n_pts is not None and n_pts < MIN_POINTS:
            n_small += 1
            log(f"[{i}/{len(tiles)}] {tile.name}: {n_pts:,} pts < "
                f"{MIN_POINTS:,} -- skipped (sampler needs min_pts_in_region)")
            results.append({"tile": tile.name, "status": "too_small",
                            "points": n_pts})
            continue

        ok = False
        for attempt in range(1, args.retries + 2):
            if stage.exists():
                shutil.rmtree(stage, ignore_errors=True)
            stage.mkdir(parents=True, exist_ok=True)
            link = stage / tile.name
            try:
                os.link(tile, link)
            except OSError:
                shutil.copy2(tile, link)

            cmd = [str(args.python), str(presample),
                   "--crops-dir", str(stage),
                   "--output-dir", str(args.patches_dir),
                   "--inputconfig", str(inputconfig),
                   "--model-dir", str(model_dir),
                   "--random-seed", str(args.random_seed),
                   "--workers", "1"]
            if args.sample_threads is not None:
                cmd += ["--sample-threads", str(args.sample_threads)]
            rc = subprocess.call(cmd, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL)
            # The worker leaves a thinned temp beside the patches; drop it so the
            # patches dir stays clean for the GPU step's glob.
            for junk in args.patches_dir.glob(f"{tile.stem}_thin_tmp.las"):
                junk.unlink(missing_ok=True)
            if npz_is_valid(npz):
                ok = True
                size_mb = npz.stat().st_size / 2**20
                log(f"[{i}/{len(tiles)}] {tile.name}: OK "
                    f"({n_pts:,} pts -> {size_mb:.1f} MiB)"
                    + (f" [attempt {attempt}]" if attempt > 1 else ""))
                results.append({"tile": tile.name, "status": "ok",
                                "points": n_pts, "attempt": attempt})
                break
            why = "corrupt npz" if npz.exists() else f"rc={rc}"
            npz.unlink(missing_ok=True)
            log(f"[{i}/{len(tiles)}] {tile.name}: attempt {attempt} FAILED "
                f"({why})")
        if not ok:
            n_fail += 1
            results.append({"tile": tile.name, "status": "failed",
                            "points": n_pts})
        else:
            n_ok += 1

    shutil.rmtree(stage, ignore_errors=True)
    # The per-tile runs each overwrote _manifest.json with a 1-file manifest;
    # replace it with one describing the whole set.
    have = sorted(p.name for p in args.patches_dir.glob("*_patches.npz")
                  if npz_is_valid(p))
    summary = {
        "driver": "presample_per_tile.py",
        "note": "one process per tile -- workaround for the cumulative-state "
                "crash in presample_pointconv_patches.py",
        "model_name": args.model_name,
        "inputconfig": str(inputconfig),
        "random_seed": args.random_seed,
        "n_tiles": len(tiles), "n_ok": n_ok, "n_already_done": n_skip,
        "n_too_small": n_small, "n_failed": n_fail,
        "n_patch_files_present": len(have),
        "elapsed_sec": round(time.time() - t0, 1),
        "tiles": results,
    }
    (args.patches_dir / "_per_tile_manifest.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8")

    log("")
    log(f"DONE in {(time.time()-t0)/60:.1f} min: {n_ok} presampled, "
        f"{n_skip} already done, {n_small} too small, {n_fail} failed. "
        f"{len(have)} patch file(s) present.")
    if n_fail:
        log(f"  failed tiles: "
            f"{[r['tile'] for r in results if r['status']=='failed']}")
        log("  re-run to retry only those (already-done tiles are skipped).")
    if not have:
        return 1
    return 3 if n_fail else 0


if __name__ == "__main__":
    sys.exit(main())
