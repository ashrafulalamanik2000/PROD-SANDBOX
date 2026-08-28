"""Host-side DFX preflight, ECR login, image pull, and Docker launcher."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from preflight import validate

REGISTRY = "750433818015.dkr.ecr.us-west-2.amazonaws.com"
IMAGE = f"{REGISTRY}/mmworkflow:latest"


def _option(args: list[str], name: str, default: str) -> str:
    for index, value in enumerate(args):
        if value == name and index + 1 < len(args):
            return args[index + 1]
        if value.startswith(name + "="):
            return value.split("=", 1)[1]
    return default


def _runtime(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise RuntimeError(f"{name} executable not found")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("data_path")
    parser.add_argument("--preflight-only", action="store_true")
    parser.add_argument("--json", action="store_true")
    known, pipeline_args = parser.parse_known_args()

    root = Path(known.data_path).expanduser().resolve()
    report = validate(root, _option(pipeline_args, "--stages", "all"))
    if known.json:
        print(json.dumps(report, indent=2))
    else:
        state = "PASS" if report["ok"] else "FAIL"
        print(f"[DFX preflight] {state}: {report['summary']['missions']} mission(s), {report['summary']['errors']} error(s)")
        for check in report["checks"]:
            print(f"  {check['level'].upper()} {check['code']}: {check['message']} [{check['path']}]")
    if not report["ok"]:
        return 2
    if known.preflight_only:
        return 0

    try:
        aws, docker = _runtime("aws"), _runtime("docker")
        probe = subprocess.run([docker, "info"], stdout=subprocess.DEVNULL,
                               stderr=subprocess.PIPE, text=True)
        if probe.returncode:
            raise RuntimeError(probe.stderr.strip() or "Docker daemon is unavailable")
        password = subprocess.run(
            [aws, "ecr", "get-login-password", "--region", "us-west-2"],
            check=True, stdout=subprocess.PIPE,
        ).stdout
        subprocess.run(
            [docker, "login", "--username", "AWS", "--password-stdin", REGISTRY],
            input=password, check=True,
        )
        subprocess.run([docker, "pull", IMAGE], check=True)
    except (RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"ERROR: runtime gate failed: {exc}", file=sys.stderr)
        return 3

    scripts = Path(__file__).resolve().parent
    command = [docker, "run", "--rm", "-v", f"{root}:/data", "-v", f"{scripts}:/app"]
    aws_dir = Path.home() / ".aws"
    if aws_dir.is_dir():
        command += ["-v", f"{aws_dir}:/root/.aws"]
    command += [IMAGE, "/root/miniconda3/envs/pdal/bin/python", "/app/dfx.py", "/data", *pipeline_args]
    env = dict(os.environ, MSYS_NO_PATHCONV="1")
    return subprocess.run(command, check=False, env=env).returncode


if __name__ == "__main__":
    sys.exit(main())
