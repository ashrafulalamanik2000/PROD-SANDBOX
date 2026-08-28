"""Upload a fine-tuned PointCONV TF1 model to the sdai-model S3 bucket.

The TF1 wrapper at tf1/PointCONV/PointCONV.py looks up models under
  s3://sdai-model/lidar_ml/<model_directory>
when the local model directory is missing. This helper pushes a freshly
trained model to that prefix so the Docker_Run_Classification_Finetune.bat
launcher (and the tiled launcher) can fetch it on a clean machine.

By default it uploads:
  models/PointCONV_model_6class_Mobile_v0.0.10
to:
  s3://sdai-model/lidar_ml/PointCONV_model_6class_Mobile_v0.0.10

Pass --dry-run to print the planned uploads without sending anything.

Requires:
  - boto3 (pip install boto3)
  - Valid AWS credentials at ~/.aws/credentials (or AWS_PROFILE env var)
  - Permission to write to the sdai-model bucket
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--local-dir",
        type=Path,
        default=(Path(__file__).resolve().parent.parent
                 / "models" / "PointCONV_model_6class_Mobile_v0.0.10"),
        help="Local fine-tuned model directory to upload (defaults to the bundled model).",
    )
    p.add_argument(
        "--bucket",
        default="sdai-model",
        help="S3 bucket name.",
    )
    p.add_argument(
        "--prefix",
        default=None,
        help="S3 key prefix. Defaults to lidar_ml/<basename(local-dir)>.",
    )
    p.add_argument(
        "--profile",
        default=None,
        help="AWS profile name (uses default credential chain if omitted).",
    )
    p.add_argument(
        "--region",
        default=None,
        help="AWS region; falls back to the profile/default region.",
    )
    p.add_argument("--dry-run", action="store_true",
                   help="Print planned uploads, do not transfer.")
    p.add_argument("--overwrite", action="store_true",
                   help="Re-upload even if the destination key already exists.")
    return p.parse_args()


def iter_files(root: Path):
    """Yield (file_path, relative_path_str) under root."""
    for path in sorted(root.rglob("*")):
        if path.is_file():
            yield path, path.relative_to(root).as_posix()


def main() -> int:
    args = parse_args()
    local_dir = args.local_dir.resolve()
    if not local_dir.is_dir():
        print(f"ERROR: local directory not found: {local_dir}", file=sys.stderr)
        return 2

    prefix = args.prefix or f"lidar_ml/{local_dir.name}"
    prefix = prefix.rstrip("/")

    print(f"Source: {local_dir}")
    print(f"Target: s3://{args.bucket}/{prefix}/")
    print()

    if args.dry_run:
        for fp, rel in iter_files(local_dir):
            size = fp.stat().st_size
            print(f"  [dry-run] {rel}  ({size:,} bytes)")
        print(f"\n{sum(1 for _ in iter_files(local_dir))} files would be uploaded.")
        return 0

    try:
        import boto3
        from botocore.exceptions import ClientError
    except ImportError:
        print("ERROR: boto3 is not installed. Install with: pip install boto3", file=sys.stderr)
        return 2

    session_kwargs = {}
    if args.profile:
        session_kwargs["profile_name"] = args.profile
    if args.region:
        session_kwargs["region_name"] = args.region
    session = boto3.session.Session(**session_kwargs)
    s3 = session.client("s3")

    uploaded = 0
    skipped = 0
    failed = 0
    for fp, rel in iter_files(local_dir):
        key = f"{prefix}/{rel}"
        if not args.overwrite:
            try:
                s3.head_object(Bucket=args.bucket, Key=key)
                print(f"  [skip] {key} (already exists)")
                skipped += 1
                continue
            except ClientError as exc:
                code = exc.response.get("Error", {}).get("Code")
                if code not in ("404", "NoSuchKey", "NotFound"):
                    print(f"  [warn] head_object failed for {key}: {exc}")
                # else: not found — proceed to upload
        try:
            print(f"  -> s3://{args.bucket}/{key}  ({fp.stat().st_size:,} bytes)")
            s3.upload_file(str(fp), args.bucket, key)
            uploaded += 1
        except Exception as exc:
            print(f"  [fail] {key}: {exc}", file=sys.stderr)
            failed += 1

    print()
    print(f"Uploaded: {uploaded}  Skipped: {skipped}  Failed: {failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
