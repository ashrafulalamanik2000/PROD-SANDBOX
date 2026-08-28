"""Pre-sample 16K-point PointCONV inference patches during Stage 0c.

Architectural optimization (see task #137 and docs/stages/0c_crops_to_metric.md):
Stage 0c's PDAL reproject is single-threaded per Docker container with B1
parallelism at the container level. On a 24-core box that leaves
~(host_cores − parallel) cores idle. PointCONV inference (Stage 1) spends
most of its wall time on CPU-bound 16K-point patch sampling (KD-tree +
median-subtract). Pre-sampling the patches on those idle host cores during
Stage 0c lets Stage 1 skip the heaviest non-GPU work.

This script is the WAVE A deliverable — produces the patch cache on
disk. WAVE B (run_tf1_wave_b.sh -> classification_from_patches.py) now
consumes the cache for inference; its config_hash guard rejects a stale cache.
presample_dir() builds the cache AFTER the reproject; presample_dir_streaming()
(OPT 1, task #146) overlaps it WITH the reproject -- chain_orchestrator's
run_stage0c_reproject_crops uses the streaming path when presample_pipelined is
on (the default), hiding the pre-sample under the reproject's idle cores.

CLI:
    python presample_pointconv_patches.py --crops-dir <crops_metric_dir>
        [--output-dir <patches_dir>]
        [--inputconfig <inputconfig.yml>]
        [--workers <N>]

Default behavior:
    crops-dir = <run>/02_pole_crop/output/crops_metric
    output-dir = <run>/02_pole_crop/output/patches_pointconv
    inputconfig = PointCONV_TF1_Workflow/tf1/inputconfig.yml
    workers = max(1, host_cores // 2)

Output layout:
    <output-dir>/
        <pole>_patches.npz            (per pole/source LAS file)
            xyz_orig         : (N_total, 3) float64 — original XYZ (post-median-subtract)
            xyz_class        : (N_total,) uint8 — original LAS classification
            patch_indices    : (N_patches, 16384) int32 — index into xyz_orig per patch
            patch_count      : (N_total,) uint16 — how many patches include each point
            mask_predict     : (N_total,) bool — which originals are predictable
            config_hash      : str — SHA256 of (random_seed, radius_nn, n_points, voxel_size)
        _manifest.json
            generated_at, n_files_processed, n_patches_total, config_hash, source_paths

Determinism contract:
    random_seed = 42 (hardcoded — matches in-Docker sampler)
    Radius_NN, NUM_POINT, num_candidates, max_points_per_region etc. read
    from inputconfig.yml so the cache stays in sync with whatever PointCONV
    is configured to use.

Safety:
    Never fails the calling stage. SystemExit on missing inputs is caught
    + demoted to WARN by the orchestrator hook in run_stage0c_reproject_crops.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path


# Find PointCONV_TF1_Workflow on the local filesystem to import the
# sampler. The chain orchestrator's tf1/PointCONV/ is the canonical
# location. We do this dynamically rather than via a pinned package
# install because the workflow code is iteratively updated and we
# always want to use whatever the host has checked out.
# Walk ancestors instead of indexing parents[N]: the old parents[3]
# resolved to <...>/Claude (repo) / /opt/Claude (image) instead of the
# projects/ dir one level down, so EVERY presample worker died with
# "No module named 'tf1'" and Firmatek Wave B got an empty cache
# (LaVerne run 20260610_140201 — only the Firmatek chain exercises this
# path, which is why Aecon runs never hit it).
_POINTCONV_TF1 = None
for _anc in Path(__file__).resolve().parents:
    # Match both the chain-orchestrator layout (PointCONV/PointCONV_TF1_Workflow)
    # and the bundled-skill layout (PointCONV_TF1_Workflow beside scripts/).
    for _cand in (_anc / "PointCONV" / "PointCONV_TF1_Workflow",
                  _anc / "PointCONV_TF1_Workflow"):
        if (_cand / "tf1" / "PointCONV").is_dir():
            _POINTCONV_TF1 = _cand
            break
    if _POINTCONV_TF1 is not None:
        break
if _POINTCONV_TF1 is None:
    # Fallback: the workflow bundled alongside this skill (scripts/ -> skill root).
    _POINTCONV_TF1 = Path(__file__).resolve().parent.parent / "PointCONV_TF1_Workflow"
sys.path.insert(0, str(_POINTCONV_TF1))


def _load_pointconv_conf(inputconfig_path: Path,
                          model_dir: Path | None = None) -> dict:
    """Read sampling-related fields from inputconfig.yml + model exp_def.p.

    Mirrors what the in-Docker `classification.py` would pass to
    `SamplePoints_Parr_Deterministic`. The fields live in two places:
      - inputconfig.yml: PointConv.{num_candidates, max/min_points_per_region,
        num_threads_PointCONV_sample, min_samples_per_point},
        preprocessing.{voxel_size, min_num_pts_voxel}
      - model/<model_dir>/exp_def.p: Radius_NN

    This MUST stay in sync with what tf1/classification.py and
    PointCONV_Segment.py read at inference time — if they diverge, the
    cache won't match what the in-Docker sampler would have produced
    and the IoU benchmark fails."""
    import yaml as _yaml
    with inputconfig_path.open("r", encoding="utf-8") as f:
        cfg = _yaml.safe_load(f) or {}
    pc = cfg.get("PointConv", {}) or {}
    pp = cfg.get("preprocessing", {}) or {}

    # Radius_NN + dim live in the model's exp_def.p — not in the inputconfig.
    # If a model_dir is passed, read them; otherwise fall back to the legacy
    # defaults (deprecated path). dim>3 models (c1/c1_lv: XYZ + hag +
    # linearity + verticality) need use_geometry_features at sample time so
    # the cached patches carry all input channels — a dim-3 cache fed to a
    # dim-6 model dies in TF1 with a placeholder shape error (LaVerne
    # 20260611_002210; the Wave B path predates the dim-6 models).
    radius_nn = 11.0
    dim = 3
    if model_dir is not None:
        exp_def_path = Path(model_dir) / "exp_def.p"
        if exp_def_path.is_file():
            try:
                from pickle import load as _pload
                with open(exp_def_path, "rb") as f:
                    _exp = _pload(f)
                if "data_definition" in _exp \
                        and "Radius_NN" in _exp["data_definition"]:
                    radius_nn = float(_exp["data_definition"]["Radius_NN"])
                elif "Radius_NN" in _exp:
                    radius_nn = float(_exp["Radius_NN"])
                dim = int(_exp.get("dim", 3))
            except Exception as e:
                print(f"  [presample] WARN: could not read Radius_NN/dim from "
                      f"{exp_def_path}: {e}. Falling back to "
                      f"{radius_nn}/dim={dim}.")

    return {
        # Sampling parameters from PointConv section (NOT a `sampling` key).
        "num_candidates": pc.get("num_candidates", 30),
        "max_points_per_region": pc.get("max_points_per_region", 311296),
        "min_points_in_region": pc.get("min_points_in_region", 24576),
        "num_threads_PointCONV_sample": pc.get(
            "num_threads_PointCONV_sample", 4),
        "min_samples_per_point": pc.get("min_samples_per_point", 1),
        # Training-data branch is irrelevant for inference pre-sampling.
        "training_data_config": None,
        "class_mapping_model": None,
        # Other fields the sampler reads:
        "radius_nn": radius_nn,
        "n_points": pp.get("min_num_pts_voxel", 16384),
        "voxel_size": pp.get("voxel_size", 0.1),
        # Model input channels (3 = XYZ; 6 adds hag/linearity/verticality).
        # In the conf dict => part of _config_hash, so a cache sampled for
        # one dim is never silently consumed by a model of another.
        "dim": dim,
    }


def _config_hash(point_conv_conf: dict, random_seed: int) -> str:
    """SHA256 over the fields that determine patch identity.

    Stage 1 reads this off the .npz and refuses to use the cache if
    the live inputconfig.yml's hash doesn't match (prevents silent
    stale-cache bugs when an operator tunes a knob between Stage 0c
    and Stage 1)."""
    h = hashlib.sha256()
    for k in sorted(point_conv_conf.keys()):
        h.update(f"{k}={point_conv_conf[k]}".encode("utf-8"))
    h.update(f"seed={random_seed}".encode("utf-8"))
    return h.hexdigest()[:16]


def _voxel_thin_las(las_path: Path, voxel_size: float,
                     thinned_path: Path) -> int:
    """Voxel-thin a LAS file to <voxel_size>m and write to thinned_path.

    Mirrors the per-tile voxel-key dedup in
    build_tf1_inference_tiles_streaming.py:_process_one_pass (lines 316-321):
    for each (kx, ky, kz) integer voxel key, keep the FIRST point seen.

    This step is REQUIRED for byte-equivalence to v0 inference because the
    PointCONV model was trained on 0.1m voxelized data; sampling 16K-point
    patches from raw (non-voxelized) data gives a different local density
    that throws off the predictions.

    Returns the thinned point count."""
    import numpy as _np
    import laspy
    # Memory-lean variant of the original (which held the full point records
    # PLUS xyz PLUS a 3-column key matrix through a structured-array unique —
    # ~19 GB peak on a 131M-point cloud). Identical output: same canonical
    # (x,y,z) lexsort, same first-point-per-voxel dedup. The LAS is read twice
    # instead, so the point records are never resident alongside the sort temps.
    src = laspy.read(str(las_path))
    n = len(src.points)
    if n == 0:
        # Empty source — write a copy and return 0.
        src.write(str(thinned_path))
        return 0
    x = _np.asarray(src.x, dtype=_np.float64)
    y = _np.asarray(src.y, dtype=_np.float64)
    z = _np.asarray(src.z, dtype=_np.float64)
    del src                                   # drop the point records (biggest block)
    kx = _np.floor(x / voxel_size).astype(_np.int64)
    ky = _np.floor(y / voxel_size).astype(_np.int64)
    kz = _np.floor(z / voxel_size).astype(_np.int64)
    # Pack the 3 voxel keys into one int64 (injective, so identical dedup
    # groups); fall back to the row-wise unique only if a range can't fit.
    kx -= kx.min(); ky -= ky.min(); kz -= kz.min()
    if int(kx.max()) < (1 << 21) and int(ky.max()) < (1 << 21) and int(kz.max()) < (1 << 21):
        keys = (kx << 42) | (ky << 21) | kz
        del kx, ky, kz
        row_unique = False
    else:
        keys = _np.column_stack((kx, ky, kz))
        del kx, ky, kz
        row_unique = True
    # Global dedup, one point per voxel. Canonicalize point order first (by
    # x,y,z) so the kept representative is a deterministic function of geometry,
    # NOT of the input cloud's point order / upstream merge order. Without this,
    # np.unique keeps first-in-input-order, so re-exporting the same cloud with
    # reordered points (or a different merge order) could shift which raw point
    # survives. Mirrors the lexsort canonicalization in pipeline.py/find_poles.py.
    order = _np.lexsort((z, y, x))
    del x, y, z
    if row_unique:
        _, first_in_sorted = _np.unique(keys[order], axis=0, return_index=True)
    else:
        _, first_in_sorted = _np.unique(keys[order], return_index=True)
    del keys
    first_idx = _np.sort(order[first_in_sorted])
    del order, first_in_sorted
    # Re-read and subset all the point arrays via laspy.
    src = laspy.read(str(las_path))
    out = laspy.LasData(header=src.header, points=src.points[first_idx])
    del src
    out.write(str(thinned_path))
    return int(first_idx.size)


def _presample_one(args_tuple) -> dict:
    """Pre-sample one LAS file. Top-level function for multiprocessing.

    args_tuple = (las_path, out_dir, point_conv_conf, random_seed
                  [, sample_num_threads]).

    sample_num_threads (task #148) decouples the RUNTIME sampler thread count
    from the HASHED one: the config_hash written to the .npz is always computed
    from the inputconfig's num_threads_PointCONV_sample, so Wave B's consumer
    (which recomputes the hash from the same inputconfig) keeps accepting the
    cache; but the actual SamplePoints call runs at sample_num_threads. Running
    it at 1 makes the cache deterministic + worker-count-invariant (the loky
    children are unseeded, so num_threads>1 reshuffles patch_indices run to run)
    and lets the outer ProcessPool fill the box. None = legacy (sampler uses the
    inputconfig value)."""
    las_path, out_dir, point_conv_conf, random_seed = args_tuple[:4]
    sample_num_threads = args_tuple[4] if len(args_tuple) > 4 else None
    las_path = Path(las_path)
    out_path = Path(out_dir) / f"{las_path.stem}_patches.npz"
    # Capture the hash from the ORIGINAL conf (inputconfig num_threads) BEFORE
    # any runtime override, so the cache stays consumer-compatible.
    cfg_hash = _config_hash(point_conv_conf, random_seed)
    if sample_num_threads is not None:
        point_conv_conf = {**point_conv_conf,
                           "num_threads_PointCONV_sample": int(sample_num_threads)}
    result = {"las": str(las_path), "npz": str(out_path), "ok": False,
              "error": None, "n_patches": 0, "n_points": 0,
              "n_points_raw": 0, "n_points_thinned": 0}
    try:
        # Import inside the worker so each process gets its own copy of
        # the (potentially large) sampler module.
        from tf1.PointCONV.SamplePoints_Parr_Deterministic import (
            SamplePoints_Parr_Deterministic,
        )
        import numpy as _np
        import laspy as _laspy
        # Step 1: voxel-thin to 0.1m to match v0's training-data density.
        # Write to a temp LAS in out_dir (same volume — no cross-FS rename).
        voxel_size = float(point_conv_conf.get("voxel_size", 0.1))
        with _laspy.open(str(las_path)) as _r:
            result["n_points_raw"] = int(_r.header.point_count)
        thinned_path = Path(out_dir) / f"{las_path.stem}_thin_tmp.las"
        n_thinned = _voxel_thin_las(las_path, voxel_size, thinned_path)
        result["n_points_thinned"] = n_thinned
        # Step 2: SamplePoints on the thinned LAS (so xyz_orig in the cache
        # is at 0.1m voxel resolution — matches v0).
        #
        # CRITICAL: nn_points_all MUST equal NUM_POINT (16384) for v0 parity.
        # v0's PointCONV_Segment.py:53,228 passes nn_points_all=NUM_POINT.
        # If you pass a smaller value (e.g. 1024) the sampler returns
        # 1024-point patches and downstream code has to pad to NUM_POINT by
        # replicating indices — which destroys spatial diversity inside each
        # patch and the model can't classify thin structures (wires) properly.
        sample = SamplePoints_Parr_Deterministic(
            str(thinned_path),
            point_conv_conf,
            nn_points_all=point_conv_conf["n_points"],
            min_samples_per_point=point_conv_conf.get(
                "min_samples_per_point", 1),
            classifications_keep=None,
            above_ground_minimum=None,
            minimum_pts_in_xyz=point_conv_conf["n_points"],
            random_seed=random_seed,
            Radius_NN=point_conv_conf["radius_nn"],
            # dim>3 models: sampler appends hag/linearity/verticality to the
            # absolute cloud, so sample["xyz"] (cached below as xyz_orig)
            # carries (N, dim) rows. Mirrors PointCONV_Segment's dim gate.
            use_geometry_features=int(point_conv_conf.get("dim", 3)) > 3,
        )
        # Step 3: delete the temp thinned LAS — it's redundant with the
        # cached xyz_orig + xyz_class.
        try:
            thinned_path.unlink()
        except Exception:
            pass
        if sample is None:
            result["error"] = "sampler returned None (likely too few points)"
            return result
        # Cache only the inference-relevant fields. Skip color_orig + the
        # learning_data branches — those aren't needed for inference and
        # bloat the cache.
        n_total = sample["xyz"].shape[0]
        # patch_indices: pad each per-patch array to n_points and stack.
        patch_inds = sample["point_cloud_sample_ind"]
        n_points = point_conv_conf["n_points"]
        n_patches = len(patch_inds)
        patches_padded = _np.zeros((n_patches, n_points), dtype=_np.int32)
        for i, idx in enumerate(patch_inds):
            if len(idx) >= n_points:
                patches_padded[i] = idx[:n_points]
            else:
                # Repeat indices to fill (matches the sampler's own
                # repeat-padding behavior for under-populated patches).
                rep = _np.tile(idx, n_points // len(idx) + 1)[:n_points]
                patches_padded[i] = rep
        _np.savez_compressed(
            out_path,
            xyz_orig=sample["xyz"].astype(_np.float64),
            xyz_class=sample["xyz_class"].astype(_np.uint8),
            patch_indices=patches_padded,
            patch_count=sample["pnt_sample_number"].astype(_np.uint16),
            mask_predict=sample["mask_predict_from_original_file"].astype(bool),
            config_hash=_np.array([cfg_hash], dtype="U16"),
        )
        result["ok"] = True
        result["n_patches"] = int(n_patches)
        result["n_points"] = int(n_total)
    except Exception as e:
        import traceback
        result["error"] = f"{type(e).__name__}: {e}\n{traceback.format_exc()}"
    return result


def presample_dir(crops_dir: Path, out_dir: Path,
                   inputconfig_path: Path,
                   workers: int,
                   random_seed: int = 42,
                   model_dir: Path | None = None,
                   sample_num_threads: int | None = None) -> dict:
    """Pre-sample every LAS file in crops_dir into out_dir.

    sample_num_threads overrides the runtime sampler thread count without
    touching the config_hash (task #148; see _presample_one). None = legacy."""
    crops_dir = Path(crops_dir)
    out_dir = Path(out_dir)
    if not crops_dir.is_dir():
        raise SystemExit(f"crops_dir not found: {crops_dir}")
    out_dir.mkdir(parents=True, exist_ok=True)

    # Auto-detect pattern (mirror chain_orchestrator._detect_crop_pattern).
    # Match BOTH .las and .laz: compress_intermediates writes crops as .laz, and
    # the streaming path already globs "*.la[sz]" -- keep this sequential/CLI path
    # in sync so it doesn't silently find 0 files on a compressed run.
    if list(crops_dir.glob("Pole_*.la[sz]")):
        pattern = "Pole_*.la[sz]"
    elif list(crops_dir.glob("P_*.la[sz]")):
        pattern = "P_*.la[sz]"
    else:
        pattern = "*.la[sz]"
    files = sorted(p for p in crops_dir.glob(pattern)
                   if not p.name.endswith("_thin_tmp.las"))
    if not files:
        raise SystemExit(
            f"no LAS/LAZ files matching {pattern!r} in {crops_dir}")
    print(f"  [presample] {len(files)} files in {crops_dir.name}/ "
          f"(pattern={pattern!r})")

    point_conv_conf = _load_pointconv_conf(inputconfig_path, model_dir)
    print(f"  [presample] inputconfig: {inputconfig_path.name}")
    if model_dir is not None:
        print(f"  [presample] model_dir: {model_dir}")
    print(f"  [presample] Radius_NN={point_conv_conf['radius_nn']}, "
          f"n_points={point_conv_conf['n_points']}, "
          f"num_candidates={point_conv_conf['num_candidates']}, "
          f"max_pts_per_region={point_conv_conf['max_points_per_region']}, "
          f"min_pts_in_region={point_conv_conf['min_points_in_region']}")
    _hash_nt = point_conv_conf.get("num_threads_PointCONV_sample")
    print(f"  [presample] workers={workers}, random_seed={random_seed}, "
          f"sampler_threads={sample_num_threads if sample_num_threads is not None else _hash_nt}"
          f"{f' (hash keeps {_hash_nt})' if sample_num_threads is not None and sample_num_threads != _hash_nt else ''}")
    print(f"  [presample] output -> {out_dir}")

    args = [(str(p), str(out_dir), point_conv_conf, random_seed,
             sample_num_threads)
            for p in files]
    results = []
    start = dt.datetime.now()
    with ProcessPoolExecutor(max_workers=workers) as exe:
        futures = {exe.submit(_presample_one, a): a for a in args}
        for fut in as_completed(futures):
            r = fut.result()
            results.append(r)
            if r["ok"]:
                print(f"  [presample] OK {Path(r['las']).name} -> "
                      f"{r['n_patches']} patches, {r['n_points']:,} pts")
            else:
                print(f"  [presample] FAIL {Path(r['las']).name}: "
                      f"{r['error'].splitlines()[0] if r['error'] else 'unknown'}")
    elapsed = (dt.datetime.now() - start).total_seconds()

    n_ok = sum(1 for r in results if r["ok"])
    n_patches_total = sum(r["n_patches"] for r in results if r["ok"])
    total_disk = sum(Path(r["npz"]).stat().st_size
                     for r in results if r["ok"]
                     and Path(r["npz"]).exists())
    summary = {
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "elapsed_sec": elapsed,
        "n_files_processed": n_ok,
        "n_files_failed": len(files) - n_ok,
        "n_patches_total": int(n_patches_total),
        "disk_bytes_total": int(total_disk),
        "config_hash": _config_hash(point_conv_conf, random_seed),
        "inputconfig_path": str(inputconfig_path),
        "pattern": pattern,
        "random_seed": random_seed,
        "sources": [r["las"] for r in results],
        "failures": [
            {"las": r["las"], "error": r["error"]}
            for r in results if not r["ok"]
        ],
    }
    manifest_path = out_dir / "_manifest.json"
    manifest_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"\n  [presample] DONE: {n_ok}/{len(files)} files, "
          f"{n_patches_total:,} total patches, "
          f"{total_disk / (1024**2):.1f} MiB on disk, "
          f"{elapsed:.1f}s wall time")
    print(f"  [presample] manifest -> {manifest_path}")
    if files and n_ok == 0:
        # Every crop failed => systemic (import/config), not data-shaped.
        # Surface the first error instead of leaving an empty cache that
        # Wave B trips over a stage later.
        _first = next((r["error"] for r in results if not r["ok"]), "?")
        raise RuntimeError(
            f"presample: ALL {len(files)} crops failed -- first error:\n"
            f"{_first}")
    return summary


# --- OPT 1 (pipeline Stage 0c: reproject || pre-sample) ----------------------

def _is_valid_lasf(p: Path) -> bool:
    """True if p looks like a complete LAS/LAZ (LASF signature + bigger than a
    bare header). A crashed/partial reproject can leave a 0-byte / truncated
    file -- treat that as not-yet-ready."""
    try:
        if p.stat().st_size < 227:          # smaller than a LAS header => junk
            return False
        with p.open("rb") as fh:
            return fh.read(4) == b"LASF"
    except OSError:
        return False


def _ensure_wkt_flag(p: Path) -> None:
    """Set bit 4 ('WKT for SRS') of global_encoding in place (idempotent).

    reproject_las_dir.py writes PF7 LAS WITHOUT this bit and only patches it
    in a loop at the END of its run. The streaming consumer reads each crop
    much earlier, and laspy trips on an unflagged PF7 LAS 1.4 with
    'read length must be non-negative or -1' -- so the consumer sets the bit
    itself first. A 2-byte write at offset 6; the LASF header is bit-identical
    for .las/.laz. Safe (no-op) if the reproject already set it."""
    import struct
    try:
        with p.open("r+b") as f:
            if f.read(4) != b"LASF":
                return
            f.seek(6)
            (ge,) = struct.unpack("<H", f.read(2))
            if not (ge & 0x0010):
                f.seek(6)
                f.write(struct.pack("<H", ge | 0x0010))
    except OSError:
        pass


def _scan_ready(crops_dir: Path, pattern: str, submitted: set,
                prev_size: dict, *, require_stable: bool) -> list:
    """Return crops ready to pre-sample: a valid LASF not yet submitted.

    When require_stable (the reproject is still running) a crop is 'ready'
    only once its size is unchanged since the previous scan -- the reproject
    writes each crop in a single PDAL pass, so a stable size means the write
    finished and we won't hand a half-written file to the sampler. When not
    require_stable (the reproject has finished) every unsubmitted crop is
    ready immediately."""
    ready = []
    for p in sorted(crops_dir.glob(pattern)):
        if p in submitted or p.name.endswith("_thin_tmp.las"):
            continue
        if not _is_valid_lasf(p):
            continue
        if not require_stable:
            ready.append(p)
            continue
        sz = p.stat().st_size
        if prev_size.get(p) == sz:
            ready.append(p)
        else:
            prev_size[p] = sz               # still growing -- re-check next scan
    return ready


def _compute_presample_workers(cpu_count: int, reproject_parallel: int,
                               inner_threads: int, *, pipelined: bool) -> int:
    """Pick a pre-sample worker count that keeps total CPU demand near the core
    count instead of oversubscribing it.

    The pre-sample is NESTED-parallel: each ProcessPool worker runs
    SamplePoints_Parr_Deterministic, whose divide_and_conquer_sample_groups
    fans out to `inner_threads` joblib(loky) PROCESSES during the sampling
    phase (num_threads_PointCONV_sample, default 4). So the pre-sample's peak
    CPU demand is `workers * inner_threads`, NOT `workers` -- the multiplier the
    old `cpu_count // 2` default silently ignored.

    When `pipelined` (OPT 1, task #146) the PDAL reproject runs CONCURRENTLY:
    B1 spawns `reproject_parallel` single-threaded Docker PDAL containers. We
    reserve cores for them -- capped at half the box, since they're I/O-bound
    and won't pin a full core each -- and hand the rest to the pre-sample:

        workers = max(1, (cpu_count - reserve) // inner_threads)

    Worked example (the #147 Verizon 20K box): cpu_count=24, parallel=8,
    inner=4 -> reserve=8, budget=16, workers=4 -> peak 8 + 4*4 = 24. The old
    default gave workers=8 -> 8 + 8*4 = 40 threads on 24 cores (~1.6x
    oversubscription + the RAM of 32 sampler processes each holding a pole).

    Sequential path (pipelined=False): the reproject has already finished, so
    the pre-sample owns the whole box -> reserve=0 -> workers = cpu_count //
    inner_threads.
    """
    c = max(1, int(cpu_count))
    inner = max(1, int(inner_threads))
    reserve = min(max(0, int(reproject_parallel)), c // 2) if pipelined else 0
    budget = max(1, c - reserve)
    return max(1, budget // inner)


def presample_dir_streaming(crops_dir: Path, out_dir: Path,
                            inputconfig_path: Path, workers: int,
                            random_seed: int = 42,
                            model_dir: Path | None = None, *,
                            producer_done,
                            sample_num_threads: int | None = None,
                            pattern: str = "*.la[sz]",
                            poll_interval: float = 2.0) -> dict:
    """Streaming variant of presample_dir (OPT 1).

    Consumes each crop AS the reproject emits it (producer/consumer) instead
    of waiting for the whole reproject to finish, so the CPU-bound 16K-point
    sampling overlaps the reproject's idle-core window. Reuses _presample_one
    verbatim, so each per-pole .npz is byte-identical to presample_dir's --
    only the submission TIMING differs (sampling is per-pole independent +
    seeded). `producer_done()` must return True once the reproject finishes;
    after that a final sweep submits any stragglers (no stability wait)."""
    crops_dir = Path(crops_dir)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    conf = _load_pointconv_conf(inputconfig_path, model_dir)
    _hash_nt = conf.get("num_threads_PointCONV_sample")
    print(f"  [presample/stream] inputconfig={inputconfig_path.name}, "
          f"Radius_NN={conf['radius_nn']}, n_points={conf['n_points']}, "
          f"workers={workers}, seed={random_seed}, "
          f"sampler_threads={sample_num_threads if sample_num_threads is not None else _hash_nt}"
          f"{f' (hash keeps {_hash_nt})' if sample_num_threads is not None and sample_num_threads != _hash_nt else ''}, "
          f"poll={poll_interval}s")
    print(f"  [presample/stream] output -> {out_dir}")

    submitted: set = set()
    prev_size: dict = {}
    futures: dict = {}
    results: list = []
    start = dt.datetime.now()

    with ProcessPoolExecutor(max_workers=workers) as exe:
        def _submit(p: Path) -> None:
            _ensure_wkt_flag(p)
            futures[exe.submit(_presample_one,
                               (str(p), str(out_dir), conf, random_seed,
                                sample_num_threads))] = p
            submitted.add(p)
            print(f"  [presample/stream] queued {p.name} "
                  f"(seen {len(submitted)})")

        def _collect_done() -> None:
            for fut in [f for f in futures if f.done()]:
                r = fut.result()
                results.append(r)
                del futures[fut]
                if r["ok"]:
                    print(f"  [presample/stream] OK {Path(r['las']).name} -> "
                          f"{r['n_patches']} patches ({len(results)} done)")
                else:
                    print(f"  [presample/stream] FAIL {Path(r['las']).name}: "
                          f"{(r['error'] or 'unknown').splitlines()[0]}")

        # Phase 1: stream while the reproject is still producing crops.
        while not producer_done():
            for p in _scan_ready(crops_dir, pattern, submitted, prev_size,
                                 require_stable=True):
                _submit(p)
            _collect_done()
            time.sleep(poll_interval)
        # Phase 2: reproject finished -- submit every remaining crop (no wait).
        for p in _scan_ready(crops_dir, pattern, submitted, prev_size,
                             require_stable=False):
            _submit(p)
        # Phase 3: drain the pool.
        for fut in as_completed(list(futures)):
            r = fut.result()
            results.append(r)
            tag = "OK" if r["ok"] else "FAIL"
            print(f"  [presample/stream] {tag} {Path(r['las']).name} (drain)")

    elapsed = (dt.datetime.now() - start).total_seconds()
    n_ok = sum(1 for r in results if r["ok"])
    n_patches_total = sum(r["n_patches"] for r in results if r["ok"])
    total_disk = sum(Path(r["npz"]).stat().st_size for r in results
                     if r["ok"] and Path(r["npz"]).exists())
    n_crops = sum(1 for p in crops_dir.glob(pattern) if _is_valid_lasf(p))
    summary = {
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "elapsed_sec": elapsed,
        "n_files_processed": n_ok,
        "n_files_failed": len(submitted) - n_ok,
        "n_patches_total": int(n_patches_total),
        "disk_bytes_total": int(total_disk),
        "config_hash": _config_hash(conf, random_seed),
        "inputconfig_path": str(inputconfig_path),
        "pattern": pattern,
        "random_seed": random_seed,
        "mode": "streaming",
        "sources": [r["las"] for r in results],
        "failures": [{"las": r["las"], "error": r["error"]}
                     for r in results if not r["ok"]],
    }
    (out_dir / "_manifest.json").write_text(json.dumps(summary, indent=2),
                                            encoding="utf-8")
    print(f"\n  [presample/stream] DONE: {n_ok}/{len(submitted)} crops, "
          f"{n_patches_total:,} patches, {total_disk / (1024**2):.1f} MiB, "
          f"{elapsed:.1f}s wall")
    if n_ok != n_crops:
        print(f"  [presample/stream] WARN: {n_crops} crops present but "
              f"{n_ok} pre-sampled OK -- Stage 1 Wave B would miss poles")
    if submitted and n_ok == 0:
        # Every crop failed => systemic (import/config), not data-shaped.
        _first = next((r["error"] for r in results if not r["ok"]), "?")
        raise RuntimeError(
            f"presample: ALL {len(submitted)} crops failed -- first error:\n"
            f"{_first}")
    return summary


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--crops-dir", type=Path, required=True,
                    help="Directory of per-pole metric LAS files "
                         "(typically <run>/02_pole_crop/output/crops_metric)")
    p.add_argument("--output-dir", type=Path, default=None,
                    help="Where to write *_patches.npz files. "
                         "Default: <crops_dir>/../patches_pointconv")
    p.add_argument("--inputconfig", type=Path, default=None,
                    help="PointCONV tf1/inputconfig.yml. "
                         "Default: <pointconv>/tf1/inputconfig.yml")
    p.add_argument("--model-dir", type=Path, default=None,
                    help="PointCONV model dir holding exp_def.p. "
                         "Used to read Radius_NN for SamplePoints. "
                         "Stage 1 in chain.yml has the canonical "
                         "model name (e.g. "
                         "PointCONV_model_6class_Mobile_v0.0.15).")
    p.add_argument("--workers", type=int, default=None,
                    help="Parallel worker count. Default: host_cores//2")
    p.add_argument("--random-seed", type=int, default=42,
                    help="Sampler RNG seed (must match Stage 1's). "
                         "Default 42.")
    p.add_argument("--sample-threads", type=int, default=None,
                    help="Override the runtime sampler num_threads WITHOUT "
                         "changing the config_hash (task #148). 1 = "
                         "deterministic, worker-count-invariant cache (the "
                         "loky children are unseeded, so >1 is nondeterministic) "
                         "and lets --workers fill the box. Default: None "
                         "(legacy — use the inputconfig value).")
    args = p.parse_args()

    if args.output_dir is None:
        args.output_dir = args.crops_dir.parent / "patches_pointconv"
    if args.inputconfig is None:
        args.inputconfig = _POINTCONV_TF1 / "tf1" / "inputconfig.yml"
    if args.workers is None:
        args.workers = max(1, os.cpu_count() // 2)

    presample_dir(args.crops_dir, args.output_dir,
                   args.inputconfig, args.workers, args.random_seed,
                   model_dir=args.model_dir,
                   sample_num_threads=args.sample_threads)
    return 0


if __name__ == "__main__":
    sys.exit(main())
