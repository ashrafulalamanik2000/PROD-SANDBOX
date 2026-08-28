"""Snapshot host resource state (GPU + disk + RAM + active Docker) into a
small JSON. Called by the dashboard watch loop on every render iteration.

Output: <run_dir>/viz/resources.json — overwritten each call (no history;
the dashboard reads the latest snapshot only).

Why: Stage 1 GPU stalls and disk-full failures are real risks for long
multi-hour runs. Surfacing them in the dashboard (and in the diagnostic
bundle) means a tester doesn't have to alt-tab to nvidia-smi / df -h to
notice them.

Usage:
    python poll_resources.py <run_dir>
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

# Hide cmd/PowerShell pop-up windows when this script polls nvidia-smi
# and docker ps every 10 s. Windows-only; no-op elsewhere.
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


def _safe_run(cmd: list[str], timeout: int = 5) -> str | None:
    try:
        r = subprocess.run(cmd, check=False, capture_output=True,
                            text=True, timeout=timeout,
                            creationflags=_NO_WINDOW)
        return r.stdout if r.returncode == 0 else None
    except Exception:
        return None


def poll_gpu() -> list[dict]:
    """One entry per GPU. Empty list if nvidia-smi isn't available."""
    out = _safe_run([
        "nvidia-smi",
        "--query-gpu=index,name,utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw",
        "--format=csv,noheader,nounits",
    ])
    if not out:
        return []
    gpus = []
    for line in out.strip().splitlines():
        try:
            parts = [p.strip() for p in line.split(",")]
            gpus.append({
                "index":            int(parts[0]),
                "name":             parts[1],
                "util_pct":         int(parts[2]),
                "mem_used_mib":     int(parts[3]),
                "mem_total_mib":    int(parts[4]),
                "mem_pct":          round(100 * int(parts[3]) / max(int(parts[4]), 1), 1),
                "temperature_c":    int(parts[5]),
                "power_w":          float(parts[6]) if parts[6] not in ("[N/A]", "") else None,
            })
        except Exception:
            continue
    return gpus


def poll_disk(run_dir: Path) -> dict:
    """Free space on the volume backing the run_dir."""
    total, used, free = shutil.disk_usage(run_dir)
    return {
        "path":          str(run_dir),
        "total_gb":      round(total / (1024 ** 3), 1),
        "used_gb":       round(used / (1024 ** 3), 1),
        "free_gb":       round(free / (1024 ** 3), 1),
        "free_pct":      round(100 * free / max(total, 1), 1),
    }


def poll_ram() -> dict:
    """Total + available RAM (cross-platform best effort)."""
    try:
        import psutil
        vm = psutil.virtual_memory()
        return {
            "total_gb":     round(vm.total / (1024 ** 3), 1),
            "available_gb": round(vm.available / (1024 ** 3), 1),
            "used_pct":     vm.percent,
        }
    except ImportError:
        pass
    # Fallback: Windows wmic / Linux /proc/meminfo
    if os.name == "nt":
        out = _safe_run(["wmic", "OS", "get", "FreePhysicalMemory,TotalVisibleMemorySize",
                         "/value"])
        if out:
            kv = {}
            for line in out.splitlines():
                if "=" in line:
                    k, v = line.split("=", 1)
                    kv[k.strip()] = v.strip()
            try:
                free_kb = int(kv.get("FreePhysicalMemory", "0"))
                total_kb = int(kv.get("TotalVisibleMemorySize", "0"))
                return {
                    "total_gb":     round(total_kb / (1024 ** 2), 1),
                    "available_gb": round(free_kb / (1024 ** 2), 1),
                    "used_pct":     round(100 * (1 - free_kb / max(total_kb, 1)), 1),
                }
            except Exception:
                pass
    return {"error": "ram polling unsupported on this platform without psutil"}


def poll_docker() -> dict:
    """Active container summary."""
    out = _safe_run([
        "docker", "ps", "--format",
        "{{.Names}}|{{.Image}}|{{.Status}}|{{.RunningFor}}",
    ])
    if out is None:
        return {"available": False}
    containers = []
    for line in out.strip().splitlines():
        if not line:
            continue
        parts = line.split("|")
        if len(parts) >= 4:
            containers.append({
                "name":   parts[0],
                "image":  parts[1],
                "status": parts[2],
                "uptime": parts[3],
            })
    return {"available": True, "active_containers": containers}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("run_dir", type=Path)
    p.add_argument("--out", type=Path, default=None,
                   help="Output JSON (default <run_dir>/viz/resources.json)")
    args = p.parse_args()

    if not args.run_dir.is_dir():
        raise SystemExit(f"Run dir not found: {args.run_dir}")
    out_path = args.out or args.run_dir / "viz" / "resources.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    snapshot = {
        "polled_at": dt.datetime.now().isoformat(timespec="seconds"),
        "gpus":      poll_gpu(),
        "disk":      poll_disk(args.run_dir),
        "ram":       poll_ram(),
        "docker":    poll_docker(),
    }
    out_path.write_text(json.dumps(snapshot, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
