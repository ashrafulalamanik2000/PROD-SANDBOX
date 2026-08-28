"""Consolidated, run-dir-driven PointCONV inference entrypoint for the
stage1_pointconv package (GPU / mmworkflow).

A faithful host-orchestrated standalone of the chain's Wave-B inference: it
presamples 16K-point dim-6 patches on the host (CPU), then runs the TF1
classification inside the mmworkflow GPU image as a SIBLING container — the same
two proven tools the chain uses (`presample_pointconv_patches.py` +
`run_tf1_wave_b.sh`), wired together so the stage runs from a single run dir.

This is a HOST-ORCHESTRATED GPU stage: the worker itself runs on the host and
spawns the GPU container. The package driver runs it via the sibling-GPU path
(it still enforces the GPU compute gate + ensures the mmworkflow image first).

Sequence:
    presample (CPU, host)  ->  <run>/01_pointconv/patches/*_patches.npz
      ->  classification_from_patches.py in mmworkflow (GPU, sibling)
      ->  <run>/01_pointconv/combined_outputs/*_combined_0p1m.laz

RUN-DIR CONTRACT (everything relative to --run-dir):
  input:   01_pointconv/source/*.la[sz]      (the cloud to classify)
  output:  01_pointconv/combined_outputs/*_tf1_pointconv_combined_0p1m.laz

The model + inputconfig default to the in-repo dim-6 c1_lv set; presample and
classification MUST use the same inputconfig (a config_hash check enforces it).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_PRESAMPLE = _HERE / "presample_pointconv_patches.py"
# PointCONV TF1 workflow (tf1/classification_from_patches.py, tf1/*.yml, models/).
# Bundled with this skill by default; override with --pcv-dir or POINTCONV_TF1_DIR.
_BUNDLED_PCV = _HERE.parent / "PointCONV_TF1_Workflow"
_DEFAULT_PCV = os.environ.get("POINTCONV_TF1_DIR") or (
    str(_BUNDLED_PCV) if _BUNDLED_PCV.is_dir() else "")

# Bundled model + its paired dim-6 config (both ship in PointCONV_TF1_Workflow/).
_DEFAULT_MODEL = "PointCONV_model_6class_Mobile_v0.0.18_retune_c2"
_DEFAULT_INPUTCONFIG = "tf1/inputconfig_finetune_lowmem.yml"   # dim-6
_DEFAULT_IMAGE = "750433818015.dkr.ecr.us-west-2.amazonaws.com/mmworkflow:v1.8.0.1"


def _run(cmd, *, dry_run, env=None):
    argv = [str(c) for c in cmd]
    print(f"[stage1_infer] $ {' '.join(argv)}", flush=True)
    if dry_run:
        return
    subprocess.run(argv, check=True, env=env)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--run-dir", required=True, type=Path)
    p.add_argument("--pcv-dir", default=_DEFAULT_PCV,
                   help="path to PointCONV_TF1_Workflow (or set env POINTCONV_TF1_DIR).")
    p.add_argument("--input-subdir", default="01_pointconv/source",
                   help="run-dir-relative dir holding the cloud(s) to classify.")
    p.add_argument("--model-name", default=_DEFAULT_MODEL)
    p.add_argument("--model-dir", default=None,
                   help="dir CONTAINING the model-name subdir; default = repo models/.")
    p.add_argument("--inputconfig", default=_DEFAULT_INPUTCONFIG,
                   help="inputconfig path relative to PointCONV_TF1_Workflow/.")
    p.add_argument("--image", default=_DEFAULT_IMAGE,
                   help="mmworkflow image ref (the package passes the gated/ensured ref).")
    p.add_argument("--batch-size", type=int, default=24)
    p.add_argument("--random-seed", type=int, default=42)
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--epsg", type=int, default=None,
                   help="fallback CRS to stamp on the classified output when the "
                        "source cloud has none (the source CRS is preferred).")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--rebuild-patches", action="store_true",
                   help="remove patches only when their manifest mismatches current inputs/config.")
    args = p.parse_args()

    if not args.pcv_dir or not Path(args.pcv_dir).is_dir():
        print("FAIL: --pcv-dir (PointCONV_TF1_Workflow) not set or missing; "
              "pass --pcv-dir or set POINTCONV_TF1_DIR (see SETUP.md).", file=sys.stderr)
        return 1
    pcv = Path(args.pcv_dir).resolve()

    run_dir = args.run_dir.resolve()
    src_dir = run_dir / args.input_subdir
    patches_dir = run_dir / "01_pointconv" / "patches"
    out_root = run_dir / "01_pointconv"
    model_dir = Path(args.model_dir) if args.model_dir else (pcv / "models")
    model_path = model_dir / args.model_name

    print(f"[stage1_infer] run_dir = {run_dir}", flush=True)
    print(f"[stage1_infer] source  = {src_dir}", flush=True)
    print(f"[stage1_infer] model   = {model_path}", flush=True)
    print(f"[stage1_infer] image   = {args.image}", flush=True)
    have_src = src_dir.is_dir() and any(src_dir.glob("*.la[sz]"))
    have_patches_pre = patches_dir.is_dir() and any(patches_dir.glob("*_patches.npz"))
    if not args.dry_run and not have_src and not have_patches_pre:
        print(f"FAIL: no LAS/LAZ in {src_dir} and no patches in {patches_dir}",
              file=sys.stderr)
        return 1

    # 1. Presample dim-6 patches on the host (CPU). Same --inputconfig as inference
    #    (a config_hash check enforces it). SKIPPED when patches are already
    #    present — the host presampler's patch set is not bit-stable across runs,
    #    so a golden fixture ships the patches directly and pins determinism on the
    #    (deterministic) GPU classification. This also mirrors the chain, where
    #    stage0c presamples and stage1 only classifies.
    manifest_path = patches_dir / ".manifest.json"
    manifest = _patch_manifest(src_dir, pcv / args.inputconfig, args.random_seed)
    have_patches = patches_dir.is_dir() and any(patches_dir.glob("*_patches.npz"))
    if have_patches and manifest["inputs"]:
        prior = _read_manifest(manifest_path)
        if prior != manifest:
            if not args.rebuild_patches:
                print("FAIL: existing patches do not match current inputs/config; "
                      "rerun with --rebuild-patches or use a clean run directory",
                      file=sys.stderr)
                return 2
            print(f"[stage1_infer] removing stale patches: {patches_dir}", flush=True)
            if not args.dry_run:
                shutil.rmtree(patches_dir)
            have_patches = False
    elif have_patches:
        print("[stage1_infer] no source files available; accepting externally supplied patches",
              flush=True)
    if have_patches:
        print(f"[stage1_infer] patches present in {patches_dir} — skipping presample",
              flush=True)
    else:
        # Put PointCONV_TF1_Workflow on PYTHONPATH so the presampler can
        # `import tf1` regardless of where this skill lives on disk.
        pre_env = dict(os.environ)
        pre_env["PYTHONPATH"] = os.pathsep.join(
            [str(pcv)] + ([pre_env["PYTHONPATH"]] if pre_env.get("PYTHONPATH") else []))
        _run([sys.executable, _PRESAMPLE,
              "--crops-dir", src_dir,
              "--output-dir", patches_dir,
              "--inputconfig", pcv / args.inputconfig,
              "--model-dir", model_path,
              "--random-seed", args.random_seed,
              "--workers", args.workers], dry_run=args.dry_run, env=pre_env)
        if not args.dry_run:
            created = list(patches_dir.glob("*_patches.npz"))
            if not created:
                print("FAIL: presample completed without producing patches", file=sys.stderr)
                return 1
            _write_manifest(manifest_path, manifest)

    # 2. Classify in mmworkflow (GPU, sibling container) — a direct port of
    #    run_tf1_wave_b.sh's docker run (no bash dependency, so it works wherever
    #    the package runs). The whole exp root is mounted at /exp; the workflow at
    #    /workspace; the models dir at /model. Container paths are /exp + the
    #    host path relative to exp_root.
    exp_root = run_dir.parent

    def c_exp(path: Path) -> str:
        return "/exp/" + path.resolve().relative_to(exp_root).as_posix()

    docker = shutil.which("docker") or "docker"
    cmd = [
        docker, "run", "--rm", "--pull=never", "--gpus", "all", "--shm-size=8gb",
        "-e", "CUDA_CACHE_PATH=/exp/.cuda_cache",
        "-e", "CUDA_CACHE_MAXSIZE=2147483648",
        "-v", f"{pcv.as_posix()}:/workspace",
        "-v", f"{exp_root.as_posix()}:/exp",
        "-v", f"{model_dir.as_posix()}:/model",
        "-w", "/workspace/tf1",
        args.image,
        "python", "classification_from_patches.py",
        "--patches-dir", c_exp(patches_dir),
        "--output-dir", c_exp(out_root) + "/combined_outputs",
        "--model-dir", f"/model/{args.model_name}",
        "--inputconfig", f"/workspace/{args.inputconfig}",
        "--run-dir", c_exp(run_dir),
        "--batch-size", str(args.batch_size),
        "--random-seed", str(args.random_seed),
    ]
    _run(cmd, dry_run=args.dry_run)

    combined = out_root / "combined_outputs"
    hits = sorted(combined.glob("*_combined_0p1m.la[sz]")) if combined.is_dir() else []
    if not args.dry_run and not hits:
        print(f"FAIL: inference completed without outputs in {combined}", file=sys.stderr)
        return 1
    if not args.dry_run:
        patch_count = len(list(patches_dir.glob("*_patches.npz")))
        if len(hits) < patch_count:
            print(f"FAIL: expected at least {patch_count} classified output(s), found {len(hits)}",
                  file=sys.stderr)
            return 1

    # CRS passthrough: classification_from_patches.py writes the classified cloud
    # from patches (numpy, CRS-less), so the output carries NO CRS. The chain masks
    # this by passing EPSG explicitly to every downstream stage, but a standalone
    # consumer that reads the header (e.g. the stage4w walls worker) then errors
    # "no CRS in las header". Stamp the source cloud's CRS (or --epsg fallback) onto
    # any output that lacks one.
    if not args.dry_run and hits:
        _stamp_crs(hits, src_dir, args.epsg)

    print(f"[stage1_infer] DONE. combined_outputs: {combined} "
          f"({len(hits)} file(s))", flush=True)
    return 0


def _patch_manifest(src_dir: Path, config: Path, random_seed: int) -> dict:
    files = []
    for path in sorted(src_dir.glob("*.la[sz]")):
        stat = path.stat()
        files.append({"name": path.name, "size": stat.st_size, "mtime_ns": stat.st_mtime_ns})
    config_hash = hashlib.sha256(config.read_bytes()).hexdigest()
    return {"version": 1, "inputs": files, "inputconfig_sha256": config_hash,
            "random_seed": random_seed}


def _read_manifest(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _write_manifest(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temp.replace(path)


def _usable_crs(crs) -> bool:
    """A CRS is usable only if it resolves to an EPSG authority. laspy/PDAL write
    a garbage ENGCRS ("Unknown engineering datum") when no real CRS is present —
    that is non-None but maps to no authority, so downstream tools still can't use
    it. Treat such a CRS (and None) as "no CRS"."""
    try:
        return crs is not None and crs.to_authority() is not None
    except Exception:
        return False


def _source_crs(src_dir: Path):
    """First USABLE CRS among the source clouds, or None."""
    import laspy
    for f in sorted(src_dir.glob("*.la[sz]")):
        try:
            crs = laspy.read(str(f)).header.parse_crs()
            if _usable_crs(crs):
                return crs
        except Exception:
            continue
    return None


def _stamp_crs(hits, src_dir: Path, epsg) -> None:
    """Stamp a usable CRS onto each classified output that lacks one. Prefer the
    source cloud's CRS; fall back to --epsg. A no-op only for outputs that already
    carry a USABLE (authority-resolving) CRS — a garbage ENGCRS is re-stamped."""
    import laspy
    from pyproj import CRS
    src = _source_crs(src_dir)
    if src is None and epsg is not None:
        src = CRS.from_epsg(int(epsg))
    if src is None:
        print("[stage1_infer] WARN: no usable source CRS and no --epsg; classified "
              "output left as-is (downstream header-readers will need --epsg)",
              flush=True)
        return
    for h in hits:
        try:
            las = laspy.read(str(h))
            if _usable_crs(las.header.parse_crs()):
                continue
            las.header.add_crs(src)        # replaces any garbage ENGCRS
            las.write(str(h))
            print(f"[stage1_infer] stamped CRS {src.to_authority()} -> {h.name}",
                  flush=True)
        except Exception as e:
            print(f"[stage1_infer] WARN: could not stamp CRS on {h.name}: {e}",
                  flush=True)


if __name__ == "__main__":
    sys.exit(main())
