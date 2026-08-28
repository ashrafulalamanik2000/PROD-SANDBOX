"""Run inference + IoU with the fine-tuned PointCONV model.

Reuses the baseline run's preprocessed tiles and manifest (so the
comparison is on identical input geometry), runs classification.py
inside the workflow's Docker image, merges tile predictions, and
computes IoU on the host using the same DTECH->PointCONV mapping
that was used for the baseline.

By default the inference stage starts a detached Docker container
and the script polls until it exits — so this works even if the
host shell has a short timeout.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

WORKFLOW_ROOT = Path(__file__).resolve().parent.parent
DOCKER_IMAGE_DEFAULT = "750433818015.dkr.ecr.us-west-2.amazonaws.com/mmworkflow:v1.8.0.1"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--workflow-root", type=Path, default=WORKFLOW_ROOT)
    p.add_argument("--experiments-root", type=Path, required=True,
                   help="Host experiments root (no portable default).")
    p.add_argument("--data-root", type=Path, required=True,
                   help="Host data root mounted at /data (no portable default).")
    p.add_argument("--run-name", default="finetune_20260429_125114")
    p.add_argument("--model-dir-name", default="PointCONV_model_6class_Mobile_v0.0.10")
    p.add_argument("--docker-image", default=DOCKER_IMAGE_DEFAULT)
    p.add_argument(
        "--reuse-tiles-dir",
        default="/data/DTECH_2025_experiments/tf1_pointconv_0p1m_tiled_20260429_1510/preprocessed_tiles",
        help="In-container path to baseline preprocessed tiles to reuse.",
    )
    p.add_argument(
        "--reuse-manifest",
        default="/data/DTECH_2025_experiments/tf1_pointconv_0p1m_tiled_20260429_1510/manifests/tf1_tile_manifest.json",
        help="In-container path to the baseline tile manifest.",
    )
    p.add_argument("--baseline-iou-mapping", default=None,
                   help="Class-mapping yml of a baseline IoU run (for comparison).")
    p.add_argument("--baseline-iou-dir", default=None,
                   help="Baseline IoU run dir for the final side-by-side comparison.")
    p.add_argument("--postprocess-workers", type=int, default=2)
    p.add_argument("--container-name", default="pointconv_finetune_eval")
    p.add_argument("--skip-inference", action="store_true")
    p.add_argument("--skip-postprocess", action="store_true")
    p.add_argument("--skip-iou", action="store_true")
    return p.parse_args()


def run_checked(args: list[str], env: dict | None = None) -> None:
    print("$ " + " ".join(args), flush=True)
    rc = subprocess.run(args, env=env).returncode
    if rc != 0:
        raise SystemExit(f"Command failed (rc={rc}): {' '.join(args)}")


def render_finetune_inputconfig(template_path: Path, out_path: Path, model_dir_name: str) -> None:
    text = template_path.read_text(encoding="utf-8")
    new_lines = []
    replaced = False
    for line in text.splitlines():
        if line.lstrip().startswith("model_directory:"):
            indent = line[: len(line) - len(line.lstrip())]
            new_lines.append(f"{indent}model_directory: {model_dir_name}")
            replaced = True
        else:
            new_lines.append(line)
    if not replaced:
        raise RuntimeError("Could not find 'model_directory:' in the template inputconfig.yml")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(new_lines), encoding="utf-8")


def docker_wait_for(container_name: str) -> int:
    """Block until the named container exits; return its exit code."""
    while True:
        rc = subprocess.run(
            ["docker", "inspect", "-f", "{{.State.Status}} {{.State.ExitCode}}", container_name],
            capture_output=True, text=True,
        )
        if rc.returncode != 0:
            # container missing - probably not started yet; wait a bit
            time.sleep(2)
            continue
        status, code = rc.stdout.strip().split(" ", 1)
        if status in ("exited", "dead"):
            return int(code)
        time.sleep(15)


def main() -> None:
    args = parse_args()
    workflow = args.workflow_root.resolve()
    experiments = args.experiments_root.resolve()
    data = args.data_root.resolve()

    run_root = experiments / args.run_name
    if not run_root.exists():
        raise SystemExit(f"Run dir not found: {run_root}")
    eval_root = run_root / "post_finetune_eval"
    eval_root.mkdir(parents=True, exist_ok=True)
    iou_out = eval_root / "iou"
    iou_out.mkdir(parents=True, exist_ok=True)

    container_eval_root = f"/exp/{args.run_name}/post_finetune_eval"
    container_tile_dir = args.reuse_tiles_dir
    container_tf1_out = f"{container_eval_root}/tf1_outputs"
    container_combined_out = f"{container_eval_root}/combined_outputs"
    container_manifest = args.reuse_manifest
    container_input_config = f"{container_eval_root}/inputconfig_finetune.yml"
    container_model_folder = f"/exp/{args.run_name}/model"

    print(f"Eval root: {eval_root}")
    print(f"Reusing tiles:    {container_tile_dir}")
    print(f"Reusing manifest: {container_manifest}")
    print(f"Model dir:        {container_model_folder}/{args.model_dir_name}")

    # Render fine-tune inputconfig.yml
    template = workflow / "tf1" / "inputconfig.yml"
    inputconfig_out = eval_root / "inputconfig_finetune.yml"
    render_finetune_inputconfig(template, inputconfig_out, args.model_dir_name)
    print(f"Custom inputconfig: {inputconfig_out}")

    common_mounts = [
        "-v", f"{workflow}:/workspace",
        "-v", f"{experiments}:/exp",
        "-v", f"{data}:/data",
    ]

    if not args.skip_inference:
        # Clean any previous container with the same name.
        subprocess.run(["docker", "rm", "-f", args.container_name], capture_output=True)

        infer_args = [
            "docker", "run", "-d",
            "--name", args.container_name,
            "--pull=never",
            "--gpus", "all",
            "--shm-size=8gb",
        ] + common_mounts + [
            "-w", "/workspace/tf1",
            args.docker_image,
            "python", "classification.py",
            "--input_inputconfig", container_input_config,
            "--input_folder", container_tile_dir,
            "--out_folder", container_tf1_out,
            "--model_folder", container_model_folder,
        ]
        # MSYS_NO_PATHCONV stops Git Bash on Windows from mangling /workspace etc.
        env = {**__import__("os").environ, "MSYS_NO_PATHCONV": "1"}
        print("$ " + " ".join(infer_args), flush=True)
        cid = subprocess.check_output(infer_args, env=env).decode().strip()
        print(f"Inference container started (id={cid})")
        rc = docker_wait_for(args.container_name)
        # Capture full logs.
        with (eval_root / "inference.log").open("w", encoding="utf-8") as h:
            subprocess.run(["docker", "logs", args.container_name], stdout=h, stderr=subprocess.STDOUT)
        if rc != 0:
            raise SystemExit(f"Inference failed (exit code {rc}); see inference.log")
        subprocess.run(["docker", "rm", args.container_name], capture_output=True)

    if not args.skip_postprocess:
        merge_args = [
            "docker", "run", "--rm",
            "--pull=never",
        ] + common_mounts + [
            "-w", "/workspace",
            args.docker_image,
            "python", "/workspace/post_processing/merge_tf1_tile_predictions.py",
            "--manifest", container_manifest,
            "--tf1-output-root", container_tf1_out,
            "--output-dir", container_combined_out,
            "--workers", str(args.postprocess_workers),
            "--overwrite",
        ]
        env = {**__import__("os").environ, "MSYS_NO_PATHCONV": "1"}
        run_checked(merge_args, env=env)

    if not args.skip_iou:
        combined_host = eval_root / "combined_outputs"
        iou_args = [
            sys.executable,
            str(workflow / "tools" / "compute_pointconv_iou.py"),
            "--combined-dir", str(combined_host),
            "--mapping", args.baseline_iou_mapping,
            "--output-dir", str(iou_out),
        ]
        run_checked(iou_args)

    # Build the comparison summary.
    baseline_summary = Path(args.baseline_iou_dir) / "iou_summary.json"
    finetune_summary = iou_out / "iou_summary.json"
    if baseline_summary.exists() and finetune_summary.exists():
        with baseline_summary.open("r", encoding="utf-8") as h:
            base = json.load(h)
        with finetune_summary.open("r", encoding="utf-8") as h:
            ft = json.load(h)

        pc_classes = base["pc_classes"]
        pc_names = base.get("pc_names") or {str(c): str(c) for c in pc_classes}

        rows = []
        for c in pc_classes:
            base_stats = base["aggregate"]["per_class"][str(c)]
            ft_stats = ft["aggregate"]["per_class"][str(c)]
            rows.append({
                "pc_class": c,
                "name": pc_names.get(str(c), str(c)),
                "baseline_iou": base_stats["iou"],
                "finetune_iou": ft_stats["iou"],
                "delta": (ft_stats["iou"] - base_stats["iou"]) if (ft_stats["iou"] == ft_stats["iou"]) and (base_stats["iou"] == base_stats["iou"]) else None,
                "baseline_recall": base_stats["recall"],
                "finetune_recall": ft_stats["recall"],
                "baseline_precision": base_stats["precision"],
                "finetune_precision": ft_stats["precision"],
                "support_gt": ft_stats["support_gt"],
            })
        comparison = {
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "baseline_summary": str(baseline_summary),
            "finetune_summary": str(finetune_summary),
            "baseline_mean_iou_supported": base["aggregate"].get("mean_iou_supported"),
            "finetune_mean_iou_supported": ft["aggregate"].get("mean_iou_supported"),
            "baseline_mean_iou_all": base["aggregate"].get("mean_iou_all"),
            "finetune_mean_iou_all": ft["aggregate"].get("mean_iou_all"),
            "per_class": rows,
        }
        with (eval_root / "comparison.json").open("w", encoding="utf-8") as h:
            json.dump(comparison, h, indent=2)

        print("")
        print("=== Baseline vs Fine-tune (LAS-level IoU on 22.7M thinned points) ===")
        print(f"{'class':<22} {'baseline':>10} {'finetune':>10} {'delta':>9} {'support':>11}")
        for r in rows:
            base_iou_s = f"{r['baseline_iou']:.4f}" if r['baseline_iou'] == r['baseline_iou'] else "n/a"
            ft_iou_s = f"{r['finetune_iou']:.4f}" if r['finetune_iou'] == r['finetune_iou'] else "n/a"
            d = r['delta']
            d_s = f"{d:+.4f}" if d is not None else "n/a"
            print(f"{r['name']:<22} {base_iou_s:>10} {ft_iou_s:>10} {d_s:>9} {r['support_gt']:>11,}")
        print(
            f"  mean (supported)  baseline={comparison['baseline_mean_iou_supported']:.4f}"
            f"  finetune={comparison['finetune_mean_iou_supported']:.4f}"
        )
        print(f"Comparison written to {eval_root / 'comparison.json'}")


if __name__ == "__main__":
    main()
