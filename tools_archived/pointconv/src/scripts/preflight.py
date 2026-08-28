"""Deterministic input and runtime gate for PointCONV classification."""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

DEFAULT_MODEL = "PointCONV_model_6class_Mobile_v0.0.18_retune_c2"
DEFAULT_CONFIG = "tf1/inputconfig_finetune_lowmem.yml"
DEFAULT_IMAGE = "750433818015.dkr.ecr.us-west-2.amazonaws.com/mmworkflow:v1.8.0.1"


@dataclass
class Check:
    level: str
    code: str
    path: str
    message: str


def default_run_dir(data_path: Path) -> Path:
    return data_path.parent / f"{data_path.stem}_pointconv_run" if data_path.is_file() else data_path / "_pointconv_run"


def input_files(data_path: Path) -> list[Path]:
    if data_path.is_file() and data_path.suffix.lower() in {".las", ".laz"}:
        return [data_path]
    if data_path.is_dir():
        return sorted(p for p in data_path.iterdir() if p.is_file() and p.suffix.lower() in {".las", ".laz"})
    return []


def _point_format(path: Path) -> int:
    with path.open("rb") as stream:
        header = stream.read(105)
    if len(header) < 105 or header[:4] != b"LASF":
        raise ValueError("invalid or truncated LAS/LAZ header")
    return header[104] & 0x3F


def validate(data_value: str | os.PathLike[str], *, run_dir=None, pcv_dir=None,
             model_name=DEFAULT_MODEL, model_dir=None, inputconfig=DEFAULT_CONFIG,
             image=DEFAULT_IMAGE, check_runtime=True) -> dict:
    checks: list[Check] = []
    data_path = Path(data_value).expanduser().resolve()
    files = input_files(data_path)
    run_path = Path(run_dir).expanduser().resolve() if run_dir else default_run_dir(data_path)
    bundled = Path(__file__).resolve().parent.parent / "PointCONV_TF1_Workflow"
    pcv = Path(pcv_dir or os.environ.get("POINTCONV_TF1_DIR") or bundled).expanduser().resolve()

    if not files:
        checks.append(Check("error", "missing_input", str(data_path), "path must be a LAS/LAZ file or a directory containing LAS/LAZ files"))
    for path in files:
        try:
            if path.stat().st_size == 0:
                checks.append(Check("error", "empty_input", str(path), "input file is empty"))
        except OSError as exc:
            checks.append(Check("error", "unreadable_input", str(path), str(exc)))

    source_dir = run_path / "01_pointconv" / "source"
    if source_dir.is_dir():
        expected_names = {path.name.lower() for path in files}
        staged = [p for p in source_dir.iterdir() if p.is_file() and p.suffix.lower() in {".las", ".laz"}]
        stale = [p for p in staged if p.name.lower() not in expected_names]
        for path in stale:
            checks.append(Check("error", "stale_staged_input", str(path), "staged input is not present in the current data path; remove it or use a clean run directory"))

    models = Path(model_dir).expanduser().resolve() if model_dir else pcv / "models"
    required = (
        pcv / "tf1" / "classification_from_patches.py",
        pcv / inputconfig,
        models / model_name,
        Path(__file__).resolve().parent / "presample_pointconv_patches.py",
    )
    for path in required:
        if not path.exists():
            checks.append(Check("error", "missing_component", str(path), "required PointCONV component is missing"))
    model_path = models / model_name
    if model_path.is_dir():
        model_files = [p for p in model_path.rglob("*") if p.is_file()]
        if not model_files:
            checks.append(Check("error", "empty_model", str(model_path), "model directory contains no files"))
        for path in model_files:
            try:
                with path.open("rb") as stream:
                    prefix = stream.read(64)
                if prefix.startswith(b"version https://git-lfs.github.com/spec/v1"):
                    checks.append(Check("error", "lfs_pointer", str(path), "model file is an unresolved Git LFS pointer"))
            except OSError as exc:
                checks.append(Check("error", "unreadable_model", str(path), str(exc)))

    for path in files:
        try:
            point_format = _point_format(path)
            if point_format not in {2, 3, 5, 7, 8, 10}:
                checks.append(Check("error", "missing_rgb", str(path), f"point format {point_format} has no standard RGB dimensions"))
        except (OSError, ValueError) as exc:
            checks.append(Check("error", "invalid_las", str(path), str(exc)))

    if check_runtime:
        for module in ("laspy", "numpy", "scipy", "sklearn", "yaml", "tqdm"):
            if importlib.util.find_spec(module) is None:
                checks.append(Check("error", "missing_python_dependency", module, f"Python module '{module}' is required"))
        docker = shutil.which("docker")
        if not docker:
            checks.append(Check("error", "missing_docker", "PATH", "docker executable was not found"))
        else:
            probe = subprocess.run([docker, "info"], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
            if probe.returncode:
                checks.append(Check("error", "docker_unavailable", docker, probe.stderr.strip() or "Docker daemon is unavailable"))
            else:
                image_probe = subprocess.run([docker, "image", "inspect", image], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                if image_probe.returncode:
                    checks.append(Check("error", "missing_image", image, "image is not local; pipeline uses --pull=never"))
                else:
                    try:
                        gpu_probe = subprocess.run(
                            [docker, "run", "--rm", "--pull=never", "--gpus", "all",
                             "--entrypoint", "nvidia-smi", image, "-L"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
                            timeout=60,
                        )
                        if gpu_probe.returncode:
                            checks.append(Check("error", "gpu_unavailable", image, gpu_probe.stderr.strip() or "GPU is not visible in the container"))
                    except subprocess.TimeoutExpired:
                        checks.append(Check("error", "gpu_probe_timeout", image, "GPU probe exceeded 60 seconds"))

    errors = sum(c.level == "error" for c in checks)
    return {
        "ok": errors == 0,
        "data_path": str(data_path),
        "run_dir": str(run_path),
        "pcv_dir": str(pcv),
        "input_files": [str(p) for p in files],
        "checks": [asdict(c) for c in checks],
        "summary": {"files": len(files), "errors": errors},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("data_path")
    parser.add_argument("--run-dir")
    parser.add_argument("--pcv-dir")
    parser.add_argument("--model-name", default=DEFAULT_MODEL)
    parser.add_argument("--model-dir")
    parser.add_argument("--inputconfig", default=DEFAULT_CONFIG)
    parser.add_argument("--image", default=DEFAULT_IMAGE)
    parser.add_argument("--skip-runtime", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    report = validate(args.data_path, run_dir=args.run_dir, pcv_dir=args.pcv_dir,
                      model_name=args.model_name, model_dir=args.model_dir, inputconfig=args.inputconfig,
                      image=args.image, check_runtime=not args.skip_runtime)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        state = "PASS" if report["ok"] else "FAIL"
        print(f"[PointCONV preflight] {state}: {report['summary']['files']} file(s), {report['summary']['errors']} error(s)")
        print(f"  run_dir: {report['run_dir']}")
        for check in report["checks"]:
            print(f"  {check['level'].upper()} {check['code']}: {check['message']} [{check['path']}]")
    return 0 if report["ok"] else 2


if __name__ == "__main__":
    sys.exit(main())
