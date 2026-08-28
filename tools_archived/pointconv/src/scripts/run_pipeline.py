"""One-path PointCONV gate and launcher."""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

from preflight import DEFAULT_CONFIG, DEFAULT_IMAGE, DEFAULT_MODEL, validate


def positive_int(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return number


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument("data_path", help="LAS/LAZ file or directory containing LAS/LAZ files")
    parser.add_argument("--run-dir")
    parser.add_argument("--pcv-dir")
    parser.add_argument("--model-name", default=DEFAULT_MODEL)
    parser.add_argument("--model-dir")
    parser.add_argument("--inputconfig", default=DEFAULT_CONFIG)
    parser.add_argument("--image", default=DEFAULT_IMAGE)
    parser.add_argument("--batch-size", type=positive_int, default=24)
    parser.add_argument("--random-seed", type=int, default=42)
    parser.add_argument("--workers", type=positive_int, default=4)
    parser.add_argument("--epsg", type=positive_int)
    parser.add_argument("--preflight-only", action="store_true")
    parser.add_argument("--preflight-json", action="store_true")
    parser.add_argument("--skip-runtime-check", action="store_true")
    parser.add_argument("--rebuild-patches", action="store_true",
                        help="discard patches only when their manifest does not match current inputs")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    report = validate(
        args.data_path, run_dir=args.run_dir, pcv_dir=args.pcv_dir,
        model_name=args.model_name, model_dir=args.model_dir, inputconfig=args.inputconfig, image=args.image,
        check_runtime=not (args.skip_runtime_check or args.dry_run),
    )
    if args.preflight_json:
        print(json.dumps(report, indent=2))
    else:
        state = "PASS" if report["ok"] else "FAIL"
        print(f"[PointCONV gate] {state}: {report['summary']['files']} file(s), {report['summary']['errors']} error(s)")
        for check in report["checks"]:
            print(f"  {check['level'].upper()} {check['code']}: {check['message']} [{check['path']}]")
    if not report["ok"]:
        return 2
    if args.preflight_only:
        return 0

    run_dir = Path(report["run_dir"])
    if not args.dry_run:
        source = run_dir / "01_pointconv" / "source"
        source.mkdir(parents=True, exist_ok=True)
        for value in report["input_files"]:
            src = Path(value)
            dst = source / src.name
            if src.resolve() != dst.resolve():
                shutil.copy2(src, dst)
        print(f"[PointCONV gate] staged {len(report['input_files'])} file(s) -> {source}")

    command = [sys.executable, str(Path(__file__).with_name("pointconv_classify.py")),
               "--run-dir", str(run_dir), "--pcv-dir", report["pcv_dir"],
               "--model-name", args.model_name, "--inputconfig", args.inputconfig,
               "--image", args.image, "--batch-size", str(args.batch_size),
               "--random-seed", str(args.random_seed), "--workers", str(args.workers)]
    if args.model_dir:
        command += ["--model-dir", args.model_dir]
    if args.epsg is not None:
        command += ["--epsg", str(args.epsg)]
    if args.dry_run:
        command.append("--dry-run")
    if args.rebuild_patches:
        command.append("--rebuild-patches")
    return subprocess.run(command, check=False).returncode


if __name__ == "__main__":
    sys.exit(main())
