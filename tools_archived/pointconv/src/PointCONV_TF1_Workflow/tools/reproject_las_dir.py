"""Reproject every LAS/LAZ file in a directory to a target CRS using PDAL.

PDAL runs inside the mmworkflow Docker image (the `pdal` conda env at
`/root/miniconda3/envs/pdal`). Same image used by Stage 1 inference --
no new dependency.

Why: PointCONV's model was trained on metric coordinates with NN radius
10.29 m. Non-metric input (e.g. EPSG:6424 = US feet) makes the model
interpret 10.29 in input units = 10.29 ft = 3.4 m, scaling all distance
features by 0.3048. Reprojecting to a metric CRS (UTM, Web Mercator,
state-plane-meters) before Stage 1 fixes this.

Usage:
    python reproject_las_dir.py \\
        --input-dir <dir_with_las_or_laz> \\
        --output-dir <reprojected_outputs> \\
        --target-crs EPSG:26911 \\
        [--source-crs EPSG:6424] \\
        [--pattern "*.laz"] \\
        [--docker-image 750433818015.dkr.ecr.us-west-2.amazonaws.com/mmworkflow:v1.8.0.1]

The PDAL pipeline used per file:
    [input] -> filters.reprojection (out_srs=<target>) -> [output]

If --source-crs is provided, it's passed as in_srs. Otherwise PDAL
reads the LAS's embedded CRS VLR.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path


def log(msg: str) -> None:
    print(f"[reproject_las] {msg}", flush=True)


def _host_docker_path(p) -> str:
    """Sibling-mount translation — standalone copy of the helper in
    chain-orchestrator/chain_orchestrator.py (this tool also runs outside the
    chain). See chain-orchestrator/docker/UNIFIED_IMAGE_DESIGN.md: when this
    script runs INSIDE the chain-full container, `docker run -v SRC:DST` is
    resolved by the HOST daemon, so SRC must be translated to a host path.

    CHAIN_HOST_PATH_MAP=<container_prefix>=<host_prefix>[;...]. Rules:
      1. unset/empty -> identity;  2. no '/' or '\\' -> named volume,
      pass through;  3. longest container prefix wins (forward-slash
      normalized);  4. map set + unmatched absolute path -> fail loudly.
    """
    raw = str(p)
    mapping = os.environ.get("CHAIN_HOST_PATH_MAP", "").strip()
    if not mapping:
        return raw
    if "/" not in raw and "\\" not in raw:
        return raw
    norm = raw.replace("\\", "/")
    best_c = best_h = None
    for entry in mapping.split(";"):
        entry = entry.strip()
        if not entry or "=" not in entry:
            continue
        c_pref, h_pref = entry.split("=", 1)
        c_pref = c_pref.strip().replace("\\", "/").rstrip("/")
        if not c_pref:
            continue
        if norm == c_pref or norm.startswith(c_pref + "/"):
            if best_c is None or len(c_pref) > len(best_c):
                best_c = c_pref
                best_h = h_pref.strip().replace("\\", "/").rstrip("/")
    if best_c is not None:
        return best_h + norm[len(best_c):]
    if norm.startswith("/") or (len(norm) >= 2 and norm[1] == ":"):
        raise RuntimeError(
            f"CHAIN_HOST_PATH_MAP is set but mount source {raw!r} matches no "
            f"container prefix in {mapping!r} — refusing to compose a "
            f"silently-wrong sibling mount (UNIFIED_IMAGE_DESIGN.md rule 4).")
    return norm


def _resolve_data_root(input_dir: Path) -> Path:
    """Pick a directory that contains both input_dir and (presumably)
    output_dir. The bash launcher uses parent-of-parent; we do the same
    so PDAL inside Docker can see /data/<rel>."""
    # Go up two levels from input dir for the bind mount root.
    root = input_dir.resolve().parent
    return root.parent if root.parent.exists() else root


def reproject_one(las_path: Path, out_path: Path, target_crs: str,
                  source_crs: str | None, docker_image: str,
                  workflow_root: Path, vertical_scale: float = 1.0) -> float:
    """Run PDAL via Docker to reproject one LAS/LAZ. Returns elapsed seconds.

    When input and output share a common ancestor, we bind-mount that
    ancestor as `/data` for a single-mount pipeline. When they don't
    (e.g. input on F:\\, output on E:\\ on Windows), we fall back to TWO
    mounts: the input parent dir as `/data/in` and the output parent
    dir as `/data/out`. The PDAL pipeline references the per-side paths.
    """
    input_abs = las_path.resolve()
    output_abs = out_path.resolve()
    # Find common ancestor.
    in_parts = input_abs.parts
    out_parts = output_abs.parts
    common_n = 0
    for a, b in zip(in_parts, out_parts):
        if a == b:
            common_n += 1
        else:
            break
    if common_n > 0:
        # Single-mount path (input and output share an ancestor).
        mount_root = Path(*in_parts[:common_n])
        in_rel = input_abs.relative_to(mount_root)
        out_rel = output_abs.relative_to(mount_root)
        in_container = str(Path("/data") / in_rel).replace("\\", "/")
        out_container = str(Path("/data") / out_rel).replace("\\", "/")
        mount_args = ["-v", f"{_host_docker_path(mount_root)}:/data"]
    else:
        # No common ancestor (different drives on Windows). Two mounts.
        in_mount = input_abs.parent
        out_mount = output_abs.parent
        in_container = str(Path("/data/in") / input_abs.name).replace("\\", "/")
        out_container = str(Path("/data/out") / output_abs.name).replace("\\", "/")
        mount_args = ["-v", f"{_host_docker_path(in_mount)}:/data/in",
                      "-v", f"{_host_docker_path(out_mount)}:/data/out"]

    # Build the PDAL pipeline JSON.
    pipeline_stages: list[object] = [in_container]
    reproj_filter: dict[str, str] = {
        "type": "filters.reprojection",
        "out_srs": target_crs,
    }
    if source_crs:
        reproj_filter["in_srs"] = source_crs
    pipeline_stages.append(reproj_filter)
    # Vertical-unit conversion. filters.reprojection between two 2D (horizontal)
    # CRS — e.g. EPSG:6424 ftUS -> EPSG:26911 m — converts X/Y but PASSES Z
    # THROUGH UNCHANGED, leaving a metric-XY/feet-Z cloud. When the source and
    # target linear units differ, the caller passes --vertical-scale (ftUS->m =
    # 0.30480060960121924; m->ftUS = 3.2808333...) and we append a pure-Z affine
    # (identity X/Y, scale Z) AFTER the reprojection so the output is unit-
    # consistent in all three axes. Default 1.0 = no-op (every other caller is
    # unaffected; metric->metric reprojections never set this).
    if vertical_scale and abs(vertical_scale - 1.0) > 1e-12:
        pipeline_stages.append({
            "type": "filters.transformation",
            "matrix": f"1 0 0 0  0 1 0 0  0 0 {vertical_scale!r} 0  0 0 0 1",
        })
    # Writer stage. Two reprojection-specific quirks:
    #
    # 1. Force LAS 1.4 + point format 7 so the extended ScanAngle field
    #    (2-byte signed short, 0.006° resolution, ±30,000 range) is
    #    used. PDAL's default writer picks LAS 1.2 PF 3 where
    #    ScanAngleRank is a single signed byte capped at ±127 —
    #    Verizon's airborne LAZ has values up to 134° and PDAL rejects
    #    the downcast with
    #      "Unable to fetch data and convert as requested:
    #       ScanAngleRank:float(133.596) -> signed char".
    #
    # 2. Explicit scale_{x,y,z} = 0.001 (1 mm) + offset_{x,y,z} = "auto".
    #    LAS stores X/Y/Z as int32; the int representation is
    #    int_X = (X - offset) / scale. If we forward the INPUT's
    #    offset (tuned for the SOURCE CRS), the OUTPUT coordinates
    #    (post-reprojection) overflow int32. Verizon's ftUS->m
    #    reprojection trips this with
    #      "Unable to convert scaled value (-6142925839) to int32".
    #    Letting PDAL auto-compute the offset from output min coords
    #    keeps the int32 representation centered. 0.001 m precision
    #    is plenty for downstream pipelines (PointCONV operates at
    #    0.025 m / 0.1 m voxels).
    #
    # We deliberately do NOT set `forward: "all"` for the same reason —
    # it would re-inject the input's scale/offset. PDAL still carries
    # over per-point dimensions (intensity, return number, etc.)
    # automatically; only the header bits we override are dropped.
    writer_stage: dict[str, object] = {
        "type": "writers.las",
        "filename": out_container,
        "minor_version": 4,
        "dataformat_id": 7,
        "scale_x": 0.001,
        "scale_y": 0.001,
        "scale_z": 0.001,
        "offset_x": "auto",
        "offset_y": "auto",
        "offset_z": "auto",
    }
    pipeline_stages.append(writer_stage)
    pipeline_json = json.dumps({"pipeline": pipeline_stages})

    # Full-image mode: the workflow tree lives INSIDE the chain-full
    # container (unmountable by a sibling) — prefer the entrypoint-seeded
    # named volume, same contract as the stage-1 launchers
    # (UNIFIED_IMAGE_DESIGN.md; the PDAL pipeline itself only reads /data
    # + stdin, so either source satisfies the mount).
    _ws_src = (os.environ.get("CHAIN_WORKFLOW_MOUNT_SRC")
               or str(workflow_root))
    cmd = [
        "docker", "run", "--rm",
        *mount_args,
        "-v", f"{_host_docker_path(_ws_src)}:/workspace",
        "-i",                                      # accept JSON on stdin
        docker_image,
        "conda", "run", "--no-capture-output",
        "-n", "pdal",
        "pdal", "pipeline", "--stdin",
    ]
    env = {**os.environ, "MSYS_NO_PATHCONV": "1"}
    t0 = time.time()
    proc = subprocess.run(cmd, input=pipeline_json, text=True,
                          capture_output=True, env=env)
    elapsed = time.time() - t0
    if proc.returncode != 0:
        sys.stderr.write(proc.stdout or "")
        sys.stderr.write(proc.stderr or "")
        raise SystemExit(
            f"PDAL reprojection failed (exit {proc.returncode}) on {las_path.name}")
    return elapsed


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--input-dir", required=True, type=Path)
    p.add_argument("--output-dir", required=True, type=Path)
    p.add_argument("--target-crs", required=True,
                   help="Output CRS, e.g. EPSG:26911")
    p.add_argument("--source-crs", default=None,
                   help="Override source CRS (default: read from LAS VLR)")
    p.add_argument("--vertical-scale", type=float, default=1.0,
                   help="Multiply Z by this factor AFTER reprojection, to "
                        "convert the vertical unit when source/target linear "
                        "units differ (filters.reprojection on 2D CRS leaves Z "
                        "untouched). ftUS->m = 0.30480060960121924 ; "
                        "m->ftUS = 3.2808333333333333. Default 1.0 = no-op.")
    p.add_argument("--pattern", default="*.la[sz]",
                   help="Glob for input files. Default matches *.las and *.laz.")
    p.add_argument("--docker-image",
                   default="750433818015.dkr.ecr.us-west-2.amazonaws.com/mmworkflow:v1.8.0.1")
    p.add_argument("--workflow-root", default=None, type=Path,
                   help="Path mounted to /workspace inside the container.")
    p.add_argument("--parallel", type=int, default=4,
                   help="Number of concurrent Docker reproject "
                        "containers (default 4). PDAL filters.reprojection "
                        "is single-threaded inside the container, so on a "
                        "modern multi-core box the chain spends most of "
                        "stage 0c with N-1 cores idle. Set --parallel 1 "
                        "to fall back to the original sequential behavior. "
                        "Each container uses ~2-4 GB RAM peak; 4 parallel "
                        "= ~10-16 GB RAM. Bump higher only on big boxes.")
    args = p.parse_args()

    if not args.input_dir.is_dir():
        raise SystemExit(f"Input dir not found: {args.input_dir}")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    workflow_root = args.workflow_root or (
        Path(__file__).resolve().parents[1])  # PointCONV_TF1_Workflow/

    files = sorted(args.input_dir.glob(args.pattern))
    # Fall back to both extensions if glob with brackets returns nothing
    # (some shells don't expand them).
    if not files:
        files = sorted(list(args.input_dir.glob("*.las")) +
                       list(args.input_dir.glob("*.laz")))
    if not files:
        raise SystemExit(f"No files matching {args.pattern!r} in {args.input_dir}")
    log(f"Found {len(files)} file(s) to reproject")
    log(f"Target CRS: {args.target_crs}")
    if args.source_crs:
        log(f"Source CRS (override): {args.source_crs}")
    else:
        # No explicit source CRS: PDAL falls back to each input's embedded CRS.
        # If the input carries NO CRS VLR, filters.reprojection has no source
        # frame and silently produces wrong (or errored) output. Fail loudly
        # here rather than ship mis-georeferenced data.
        import laspy
        try:
            _hdr_crs = laspy.open(str(files[0])).header.parse_crs()
        except Exception:
            _hdr_crs = None
        if _hdr_crs is None:
            raise SystemExit(
                f"No --source-crs given and {files[0].name} has no CRS in its "
                f"header; PDAL cannot reproject from an unknown source frame. "
                f"Pass --source-crs EPSG:<code> for this data.")
        log(f"Source CRS (from {files[0].name} header): "
            f"{_hdr_crs.to_string()[:60]}")

    # Pre-flight: PDAL strictly rejects LAS PF 6-10 without the
    # "WKT for SRS" flag (bit 4 of global_encoding). Pole-cropping
    # creates PF7 crops without setting it, which trips PDAL with:
    #   "Global encoding WKT flag not set for point format 6 - 10."
    # Set the flag in-place on each input (2-byte write at offset 6
    # in the LAS header — no point-data touched).
    #
    # 2026-05-24: previously skipped .laz on the assumption "source
    # LAZ is presumed already compliant". That broke Stage 8 of the
    # Firmatek chain, which reprojects Stage 6's *_final_classified.laz
    # outputs back to ftUS — those LAZs are written by our own chain
    # code without the WKT flag. The LASF header (offset 0-227, including
    # global_encoding at offset 6) is bit-identical between .las and .laz
    # — only the point-data block is compressed in .laz — so the in-place
    # 2-byte write is safe for either extension.
    n_patched = 0
    for src in files:
        if src.suffix.lower() not in (".las", ".laz"):
            continue
        try:
            with src.open("r+b") as f:
                sig = f.read(4)
                if sig != b"LASF":
                    continue
                f.seek(6)
                import struct as _struct
                (ge,) = _struct.unpack("<H", f.read(2))
                if not (ge & 0x0010):
                    ge |= 0x0010
                    f.seek(6)
                    f.write(_struct.pack("<H", ge))
                    n_patched += 1
        except Exception as e:
            log(f"  WARN: WKT-flag preflight failed on {src.name}: {e}")
    if n_patched:
        log(f"Pre-flight: set WKT flag on {n_patched} input file(s)")

    # Filter to files that need work (skip outputs that already exist
    # — this makes resume-on-failure cheap). Counts match the original
    # sequential-mode log format so dashboard parsers stay compatible.
    def _valid_las(p: Path) -> bool:
        # #107/#98: a crashed Docker reproject can leave a 0-byte or truncated
        # output. Treat such a file as NOT done (re-do it) rather than skipping.
        try:
            if p.stat().st_size < 227:   # smaller than a LAS header => junk
                return False
            with p.open("rb") as fh:
                return fh.read(4) == b"LASF"
        except Exception:
            return False

    work_list: list[tuple[int, Path, Path]] = []
    n_skipped = 0
    for i, src in enumerate(files, 1):
        dst = args.output_dir / src.name
        if dst.exists() and _valid_las(dst):
            log(f"  [{i}/{len(files)}] {src.name}: output exists, skipping")
            n_skipped += 1
            continue
        if dst.exists():
            log(f"  [{i}/{len(files)}] {src.name}: output exists but is "
                f"0-byte/truncated (#107) — re-doing")
        work_list.append((i, src, dst))
    log(f"Work list: {len(work_list)} file(s) to reproject "
        f"({n_skipped} already done)")

    parallel = max(1, min(args.parallel, len(work_list)))
    log(f"Parallelism: {parallel} concurrent container(s)")

    total = 0.0
    if parallel == 1:
        # Sequential fallback path — preserved for back-compat + debug.
        for i, src, dst in work_list:
            log(f"  [{i}/{len(files)}] {src.name}  "
                f"({src.stat().st_size/1e6:.1f} MB)")
            elapsed = reproject_one(src, dst, args.target_crs,
                                     args.source_crs, args.docker_image,
                                     workflow_root, args.vertical_scale)
            total += elapsed
    else:
        # Parallel path: ThreadPoolExecutor — each worker thread spawns
        # its own `docker run`. PDAL itself is single-threaded inside
        # each container; the parallelism is across containers. The
        # GIL is released during the subprocess.run() call, so Python
        # threads work fine here (no need for ProcessPoolExecutor).
        from concurrent.futures import ThreadPoolExecutor, as_completed
        import threading
        log_lock = threading.Lock()
        n_done = [0]  # mutable counter shared across threads
        n_failed = [0]

        def _worker(idx: int, src: Path, dst: Path) -> float:
            sz_mb = src.stat().st_size / 1e6
            with log_lock:
                log(f"  [{idx}/{len(files)}] {src.name}  "
                    f"({sz_mb:.1f} MB)  starting...")
            try:
                elapsed = reproject_one(src, dst, args.target_crs,
                                         args.source_crs, args.docker_image,
                                         workflow_root, args.vertical_scale)
            except SystemExit as e:
                with log_lock:
                    log(f"  [{idx}/{len(files)}] {src.name}: FAILED — {e}")
                n_failed[0] += 1
                return 0.0
            with log_lock:
                n_done[0] += 1
                log(f"  [{idx}/{len(files)}] {src.name}: done in "
                    f"{elapsed:.1f}s  "
                    f"(progress {n_done[0]}/{len(work_list)})")
            return elapsed

        with ThreadPoolExecutor(max_workers=parallel) as pool:
            futures = [pool.submit(_worker, i, src, dst)
                       for (i, src, dst) in work_list]
            for fut in as_completed(futures):
                try:
                    total += fut.result()
                except Exception as e:
                    log(f"  WARN: future raised: {e}")

        if n_failed[0] > 0:
            log(f"WARNING: {n_failed[0]} file(s) failed to reproject. "
                f"Re-run to retry (existing outputs are skipped).")

    log(f"Done. Total reprojection time: {total:.1f} s "
        f"({total/60:.1f} min) for {len(files)} files "
        f"(wall time will be less due to parallelism if --parallel > 1).")

    # Post-write: PDAL writers.las (dataformat_id: 7) emits LAS 1.4 PF7
    # WITHOUT setting bit 4 ("WKT for SRS") in global_encoding. That
    # trips laspy + any other strict LAS 1.4 reader downstream with
    #   ValueError: read length must be non-negative or -1
    # Set the flag on every output LAS post-write. Same 2-byte patch
    # as the input preflight above; runs ~5 ms per file.
    n_post = 0
    for src in files:
        dst = args.output_dir / src.name
        if not dst.exists() or dst.suffix.lower() != ".las":
            continue
        try:
            with dst.open("r+b") as f:
                sig = f.read(4)
                if sig != b"LASF":
                    continue
                f.seek(6)
                import struct as _struct
                (ge,) = _struct.unpack("<H", f.read(2))
                if not (ge & 0x0010):
                    ge |= 0x0010
                    f.seek(6)
                    f.write(_struct.pack("<H", ge))
                    n_post += 1
        except Exception as e:
            log(f"  WARN: post-write WKT-flag patch failed on {dst.name}: {e}")
    if n_post:
        log(f"Post-write: set WKT flag on {n_post} output file(s)")

    # #107/#98: every expected output must exist + be a real LAS (not a
    # 0-byte/truncated partial write from a crashed/failed container). Fail
    # loudly so the orchestrator marks the stage failed (exit non-zero) rather
    # than silently leaving a corrupt file for a downstream stage. Also closes
    # the gap where a per-file reproject failure (n_failed>0 above) was only
    # WARNed while the script still returned 0.
    bad = [(args.output_dir / src.name).name
           for src in files
           if not _valid_las(args.output_dir / src.name)]
    if bad:
        log(f"ERROR: {len(bad)} output(s) missing or 0-byte/truncated after "
            f"reproject: {', '.join(bad[:8])}"
            f"{' ...' if len(bad) > 8 else ''}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
