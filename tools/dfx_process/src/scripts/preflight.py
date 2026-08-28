"""Deterministic input gate for the DFX pipeline (standard library only)."""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

VALID_STAGES = ("csv", "shp", "lasindex", "pano")


@dataclass
class Check:
    level: str
    code: str
    path: str
    message: str


def parse_stages(value: str) -> tuple[str, ...]:
    stages = VALID_STAGES if value == "all" else tuple(
        dict.fromkeys(part.strip().lower() for part in value.split(",") if part.strip())
    )
    unknown = sorted(set(stages) - set(VALID_STAGES))
    if unknown:
        raise ValueError(f"unknown stage(s): {', '.join(unknown)}")
    if not stages:
        raise ValueError("at least one stage is required")
    return stages


def _missions(root: Path) -> list[Path]:
    def is_mission(path: Path) -> bool:
        return (path / "Raw Project Data" / "Image Project").is_dir()

    if is_mission(root):
        return [root]
    return sorted(
        (p for p in root.iterdir() if p.is_dir() and is_mission(p)),
        key=lambda p: p.name.lower(),
    )


def _jpeg_header(path: Path) -> bool:
    try:
        with path.open("rb") as stream:
            return stream.read(3) == b"\xff\xd8\xff"
    except OSError:
        return False


def validate(root_value: str | os.PathLike[str], stages_value: str = "all") -> dict:
    checks: list[Check] = []
    root = Path(root_value).expanduser().resolve()
    try:
        stages = parse_stages(stages_value)
    except ValueError as exc:
        checks.append(Check("error", "invalid_stages", str(root), str(exc)))
        stages = ()

    if not root.is_dir():
        checks.append(Check("error", "missing_root", str(root), "data path is not a readable directory"))
        missions: list[Path] = []
    else:
        try:
            missions = _missions(root)
        except OSError as exc:
            checks.append(Check("error", "unreadable_root", str(root), str(exc)))
            missions = []

    if root.is_dir() and not missions:
        checks.append(Check("error", "no_missions", str(root), "no DFX mission layout found"))

    for mission in missions:
        image_dir = mission / "Raw Project Data" / "Image Project"
        las_dir = mission / "Raw Project Data" / "LAS Files"
        lst = image_dir / "Image Project.lst"
        if "csv" in stages and not (lst.is_file() and lst.stat().st_size > 0):
            checks.append(Check("error", "missing_lst", str(lst), "non-empty Image Project.lst is required"))
        if "shp" in stages and "csv" not in stages:
            csv_path = mission / f"{mission.name}_CSVs" / f"{mission.name}.csv"
            if not (csv_path.is_file() and csv_path.stat().st_size > 0):
                checks.append(Check("error", "missing_csv", str(csv_path), "shp without csv stage requires an existing camera CSV"))
        if "lasindex" in stages:
            las = sorted((*las_dir.glob("*.las"), *las_dir.glob("*.laz"))) if las_dir.is_dir() else []
            if not las:
                checks.append(Check("error", "missing_las", str(las_dir), "LAS/LAZ input is required for lasindex"))
        if "pano" in stages:
            run_dirs = [p for p in image_dir.iterdir() if p.is_dir()] if image_dir.is_dir() else []
            complete = 0
            for run_dir in run_dirs:
                groups: dict[tuple[str, str], set[str]] = {}
                for path in run_dir.iterdir():
                    if path.is_file() and path.suffix.lower() in {".jpg", ".jpeg"} and len(path.stem) > 2 and _jpeg_header(path):
                        groups.setdefault((path.stem[:-2], path.suffix.lower()), set()).add(path.stem[-1])
                complete += sum(faces == set("012345") for faces in groups.values())
                incomplete = [name for (name, _), faces in groups.items() if faces != set("012345")]
                if incomplete:
                    checks.append(Check("error", "incomplete_cubemap", str(run_dir), f"{len(incomplete)} cubemap(s) do not contain all _0.._5 faces"))
            if not run_dirs or not complete:
                checks.append(Check("error", "missing_cubemaps", str(image_dir), "pano requires at least one complete _0.._5 JPG/JPEG cubemap"))

    errors = sum(c.level == "error" for c in checks)
    return {
        "ok": errors == 0,
        "root": str(root),
        "stages": list(stages),
        "missions": [str(p) for p in missions],
        "checks": [asdict(c) for c in checks],
        "summary": {"missions": len(missions), "errors": errors},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("data_path")
    parser.add_argument("--stages", default="all")
    parser.add_argument("--json", action="store_true")
    args, _ = parser.parse_known_args()
    report = validate(args.data_path, args.stages)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        state = "PASS" if report["ok"] else "FAIL"
        print(f"[DFX preflight] {state}: {report['summary']['missions']} mission(s), {report['summary']['errors']} error(s)")
        for check in report["checks"]:
            print(f"  {check['level'].upper()} {check['code']}: {check['message']} [{check['path']}]")
    return 0 if report["ok"] else 2


if __name__ == "__main__":
    sys.exit(main())
