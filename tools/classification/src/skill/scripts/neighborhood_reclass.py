#!/usr/bin/env python
"""Neighborhood-vote reclassification: flip mislabelled points to a target
class when their local non-source neighborhood is dominated by it.

Built for the "vegetation blobs on rooftops" case: for every point in
--from-classes (default veg 3,4,5), find its k nearest neighbors AMONG POINTS
NOT IN --from-classes (the "context" cloud), capped at --max-dist.  If the
fraction of those context neighbors belonging to --vote-classes (default
building 6 + wall 47) is >= --min-ratio, the point is relabelled to
--to-class (default 6).

Why context-only voting: a naive all-points majority vote lets a large
misclassified blob vote for itself and never converges from the inside.
Voting only among NON-source points asks "what surface is this blob sitting
on/next to?" - a roof blob sees class 6 underneath regardless of blob size,
while a genuine tree's nearest non-veg points are ground (2), so it is left
alone.  Deep-canopy points whose nearest context is farther than --max-dist
are left unchanged (too ambiguous to flip).

Safety rails:
  * only --from-classes points can change; everything else passes through
  * requires >= --min-neighbors context neighbors inside --max-dist
  * in-place writes are atomic (.partial + os.replace)
  * a JSON summary with before/after histograms + flip diagnostics is
    written next to the output

Typical run (fix veg on buildings):
  python neighborhood_reclass.py --input <final_classified.las> --in-place
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

import laspy
import numpy as np
from scipy.spatial import cKDTree


def parse_classes(text):
    return sorted({int(t) for t in text.split(",") if t.strip()})


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input", required=True, type=Path)
    out = ap.add_mutually_exclusive_group(required=True)
    out.add_argument("--in-place", action="store_true")
    out.add_argument("--output", type=Path)
    ap.add_argument("--from-classes", default="3,4,5",
                    help="classes eligible to be flipped (default veg 3,4,5)")
    ap.add_argument("--vote-classes", default="6,47",
                    help="context classes that vote FOR the flip (default 6,47)")
    ap.add_argument("--to-class", type=int, default=6)
    ap.add_argument("--split-by-dominant-voter", action="store_true",
                    help="instead of a single --to-class, assign each flipped "
                         "point the vote-class with the most votes in its "
                         "context (e.g. roof veg -> 6, wall-adjacent veg -> 47)")
    ap.add_argument("--k", type=int, default=8,
                    help="context neighbors consulted per point")
    ap.add_argument("--max-dist", type=float, default=1.5,
                    help="metres - context farther than this does not vote")
    ap.add_argument("--min-ratio", type=float, default=0.6,
                    help="fraction of valid context votes that must be "
                         "vote-classes")
    ap.add_argument("--min-neighbors", type=int, default=4,
                    help="minimum valid context neighbors to allow a flip")
    ap.add_argument("--exclude-context", default="7",
                    help="classes excluded from the context cloud entirely "
                         "(default noise 7)")
    ap.add_argument("--chunk", type=int, default=5_000_000)
    args = ap.parse_args()

    t0 = time.time()
    from_cls = parse_classes(args.from_classes)
    vote_cls = parse_classes(args.vote_classes)
    excl_cls = parse_classes(args.exclude_context) if args.exclude_context else []

    print(f"reading {args.input} ...", flush=True)
    las = laspy.read(str(args.input))
    cls = np.asarray(las.classification)
    n = len(cls)
    before = {int(c): int(v) for c, v in zip(*np.unique(cls, return_counts=True))}
    print(f"  {n:,} points", flush=True)

    src_mask = np.isin(cls, from_cls)
    ctx_mask = ~src_mask & ~np.isin(cls, excl_cls)
    n_src, n_ctx = int(src_mask.sum()), int(ctx_mask.sum())
    print(f"  source points (from-classes {from_cls}): {n_src:,}")
    print(f"  context points: {n_ctx:,}", flush=True)
    if n_src == 0 or n_ctx == 0:
        print("nothing to do")
        return 0

    xyz = np.column_stack([np.asarray(las.x, dtype=np.float32),
                           np.asarray(las.y, dtype=np.float32),
                           np.asarray(las.z, dtype=np.float32)])
    ctx_xyz = xyz[ctx_mask]
    ctx_cls = cls[ctx_mask]
    ctx_is_vote = np.isin(ctx_cls, vote_cls)
    # per-vote-class membership, for --split-by-dominant-voter
    ctx_vote_of = {c: (ctx_cls == c) for c in vote_cls}

    print(f"  building context KD-tree ({n_ctx:,} pts)...", flush=True)
    tree = cKDTree(ctx_xyz)

    src_idx = np.flatnonzero(src_mask)
    flip = np.zeros(n_src, dtype=bool)
    ratio_all = np.zeros(n_src, dtype=np.float32)
    assigned = np.full(n_src, args.to_class, dtype=np.uint8)
    k = args.k
    done = 0
    for s in range(0, n_src, args.chunk):
        e = min(s + args.chunk, n_src)
        d, i = tree.query(xyz[src_idx[s:e]], k=k, workers=-1,
                          distance_upper_bound=args.max_dist)
        if k == 1:
            d = d[:, None]; i = i[:, None]
        valid = np.isfinite(d)
        n_valid = valid.sum(axis=1)
        i_safe = np.where(valid, i, 0)
        votes = np.where(valid, ctx_is_vote[i_safe], False).sum(axis=1)
        with np.errstate(invalid="ignore", divide="ignore"):
            ratio = np.where(n_valid > 0, votes / n_valid, 0.0)
        ratio_all[s:e] = ratio
        flip[s:e] = (n_valid >= args.min_neighbors) & (ratio >= args.min_ratio)
        if args.split_by_dominant_voter:
            per = np.stack([np.where(valid, ctx_vote_of[c][i_safe], False)
                            .sum(axis=1) for c in vote_cls])   # (n_vote, chunk)
            assigned[s:e] = np.asarray(vote_cls, dtype=np.uint8)[per.argmax(axis=0)]
        done = e
        print(f"    voted {done:,}/{n_src:,} ({100*done//n_src}%)", flush=True)

    n_flip = int(flip.sum())
    tgt_desc = ("dominant voter class" if args.split_by_dominant_voter
                else f"class {args.to_class}")
    print(f"  flipping {n_flip:,} / {n_src:,} source points "
          f"({100.0*n_flip/max(n_src,1):.2f}%) -> {tgt_desc}", flush=True)
    flip_idx = src_idx[flip]
    per_from = {int(c): int((cls[flip_idx] == c).sum()) for c in from_cls}
    new_labels = assigned[flip] if args.split_by_dominant_voter \
        else np.full(n_flip, args.to_class, dtype=np.uint8)
    per_to = {int(c): int(v) for c, v in
              zip(*np.unique(new_labels, return_counts=True))}
    cls[flip_idx] = new_labels
    las.classification = cls

    diag = {}
    if "hag" in las.point_format.dimension_names and n_flip:
        hag = np.asarray(las["hag"])[flip_idx]
        diag["flipped_hag_m"] = {
            "min": float(np.min(hag)), "median": float(np.median(hag)),
            "p95": float(np.percentile(hag, 95)), "max": float(np.max(hag))}

    after = {int(c): int(v) for c, v in zip(*np.unique(cls, return_counts=True))}

    target = args.input if args.in_place else args.output
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".partial")
    print(f"  writing {target} ...", flush=True)
    las.write(str(tmp))
    os.replace(tmp, target)

    summary = {
        "input": str(args.input), "output": str(target),
        "params": {"from_classes": from_cls, "vote_classes": vote_cls,
                   "to_class": args.to_class, "k": k,
                   "max_dist": args.max_dist, "min_ratio": args.min_ratio,
                   "min_neighbors": args.min_neighbors},
        "n_points": n, "n_source": n_src, "n_flipped": n_flip,
        "flipped_per_from_class": per_from,
        "flipped_per_to_class": per_to,
        "diagnostics": diag,
        "class_histogram_before": before, "class_histogram_after": after,
        "elapsed_s": round(time.time() - t0, 1),
    }
    sj = target.parent / "neighborhood_reclass_summary.json"
    sj.write_text(json.dumps(summary, indent=2))
    print(f"  summary -> {sj}")
    print(f"DONE in {summary['elapsed_s']}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
