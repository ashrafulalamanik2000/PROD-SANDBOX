"""Deterministic input gate for the AECON pipeline (standard library only)."""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

VALID_STAGES = ("yaml", "organize", "metadata", "pano", "colorize")
CAMERA_DIRS = ("Forward", "Left", "Rear", "Right", "Top", "Bottom")


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


def _looks_like_project(path: Path) -> bool:
    return bool(list(path.glob("*.lst")) or list(path.glob("*.iprj")) or (path / "Images").is_dir())


def _projects(root: Path) -> list[Path]:
    if _looks_like_project(root):
        return [root]
    return sorted((p for p in root.iterdir() if p.is_dir() and _looks_like_project(p)), key=lambda p: p.name.lower())


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
        projects: list[Path] = []
    else:
        try:
            projects = _projects(root)
        except OSError as exc:
            checks.append(Check("error", "unreadable_root", str(root), str(exc)))
            projects = []
    if root.is_dir() and not projects:
        checks.append(Check("error", "no_projects", str(root), "no AECON project layout found"))

    for project in projects:
        lst_files = [p for p in project.glob("*.lst") if p.stat().st_size > 0]
        iprj_files = [p for p in project.glob("*.iprj") if p.stat().st_size > 0]
        images = project / "Images"
        lidar = project / "Lidar"
        if "yaml" in stages and not lst_files:
            checks.append(Check("error", "missing_lst", str(project), "at least one non-empty .lst file is required"))
        if "organize" in stages and "yaml" not in stages and not (project / "InputConfig.yml").is_file():
            checks.append(Check("error", "missing_input_config", str(project / "InputConfig.yml"), "organize without yaml stage requires InputConfig.yml"))
        if "organize" in stages and not iprj_files:
            checks.append(Check("error", "missing_iprj", str(project), "a non-empty .iprj file is required"))
        if "organize" in stages:
            camera_stems = []
            for name in CAMERA_DIRS:
                folder = images / name
                stems = {p.stem for p in folder.iterdir() if p.is_file() and p.suffix.lower() in {".jpg", ".jpeg"} and _jpeg_header(p)} if folder.is_dir() else set()
                camera_stems.append(stems)
                if not stems:
                    checks.append(Check("error", "missing_camera_images", str(folder), "camera folder must contain JPG images"))
            if all(camera_stems):
                common = set.intersection(*camera_stems)
                union = set.union(*camera_stems)
                if common != union:
                    checks.append(Check("error", "unmatched_camera_frames", str(images), f"{len(union - common)} frame basename(s) are not present in all six camera folders"))
        organized = project / "Organized_Projects"
        if "metadata" in stages and "organize" not in stages:
            organized_yaml = organized / "Raw Project Data" / "InputConfig.yml"
            if not organized_yaml.is_file():
                checks.append(Check("error", "missing_organized_config", str(organized_yaml), "metadata without organize stage requires organized InputConfig.yml"))
        if "pano" in stages and "metadata" not in stages:
            metadata = list(organized.glob("**/Run_*_metadata.csv")) if organized.is_dir() else []
            if not metadata:
                checks.append(Check("error", "missing_metadata", str(organized), "pano without metadata stage requires Run_*_metadata.csv"))
        if "colorize" in stages:
            las = sorted(lidar.glob("*.las")) if lidar.is_dir() else []
            if not las:
                checks.append(Check("error", "missing_lidar", str(lidar), "colorize currently requires .las input"))
            if "pano" not in stages:
                pano = organized / "Pano_output"
                if not list(pano.glob("Run_*_metadata.csv")):
                    checks.append(Check("error", "missing_pano_metadata", str(pano), "colorize without pano stage requires Run_*_metadata.csv"))
                if not list(pano.glob("*.jpg")):
                    checks.append(Check("error", "missing_panoramas", str(pano), "colorize without pano stage requires panorama JPGs"))

    errors = sum(c.level == "error" for c in checks)
    return {
        "ok": errors == 0,
        "root": str(root),
        "stages": list(stages),
        "projects": [str(p) for p in projects],
        "checks": [asdict(c) for c in checks],
        "summary": {"projects": len(projects), "errors": errors},
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
        print(f"[AECON preflight] {state}: {report['summary']['projects']} project(s), {report['summary']['errors']} error(s)")
        for check in report["checks"]:
            print(f"  {check['level'].upper()} {check['code']}: {check['message']} [{check['path']}]")
    return 0 if report["ok"] else 2


if __name__ == "__main__":
    sys.exit(main())
