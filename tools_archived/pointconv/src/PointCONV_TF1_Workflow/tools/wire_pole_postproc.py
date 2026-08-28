"""wire_pole_postproc — Tier-1 geometric post-processing for PointCONV c2 clouds.

Recovers Wire and Pole points the model leaked to Vegetation/Man-made, using
ONLY geometry (no retrain, no model change, no color). Two passes, composed:

  PASS A (wire):  RANSAC-fit conductor lines from predicted-wire seeds, model
                  each span's catenary sag, and reclaim non-wire points within
                  --wire-tol of a conductor curve  ->  Wire (14).
  PASS B (pole):  DBSCAN predicted-pole seeds into poles, keep only those that
                  pass a real-pole gate (thin vertical shaft, ground-connected
                  -- rejects trees), and reclaim non-pole points inside a
                  --pole-radius vertical cylinder around the axis that are
                  locally vertical (PCA >= --pole-vmin)  ->  Pole (18).

Validated on the WARNER benchmark (2026-06-18): Wire IoU ~0.821->0.847,
Pole IoU ~0.767->0.794, all other classes flat, on a held-out check.

The decision uses NO ground truth. classification is rewritten; two uint8 extra
dims record the action (wire_postproc_action / pole_postproc_action: 1=reclaimed).
Runs per-file (--input/--output, the chain mode) or batch (--input-dir/--output-dir).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
from scipy.spatial import cKDTree

# ---- classes ----
LAS_GROUND, LAS_VEG, LAS_MANMADE, LAS_WIRE, LAS_POLE = 2, 5, 6, 14, 18
RECLAIM_FROM = (LAS_VEG, LAS_MANMADE)

# ---- locked operating points (WARNER-tuned + holdout-validated) ----
WIRE_TOL = 0.25            # Pass A: perp dist to conductor curve (m)
POLE_RADIUS = 0.30         # Pass B: cylinder radius around pole axis (m)
POLE_VMIN = 0.50           # Pass B: candidate vertical-linearity guard

# ---- Pass A internals ----
RANSAC_TOL = 0.35
RANSAC_ITERS = 200
RANSAC_MIN_INLIERS = 25
MAX_CONDUCTORS = 600
WIRE_PREFILTER = 0.45      # added to wire-tol for the per-conductor candidate prefilter
                           # (=> 0.70 m at the locked 0.25 tol; matches the validated rollout —
                           #  must be generous enough to catch within-tol pts in seed gaps)
T_MARGIN = 1.5

# ---- Pass B internals ----
DBSCAN_EPS = 1.0
DBSCAN_MIN = 8
MIN_VERT = 2.5
MAX_SHAFT = 0.5
MIN_RATIO = 1.0
GROUND_RADIUS = 3.0
GROUND_GAP = 3.0
Z_MARGIN = 0.5
SHAFT_FRAC = 0.30
POLE_AXIS_PREFILTER = 0.85
LIN_RADIUS = 0.50


# ============================ Pass A geometry ============================
def ransac_extract_conductors(xyz, tol, iters, min_inliers, max_conductors, rng):
    n = xyz.shape[0]
    remaining = np.arange(n)
    conductors = []
    while remaining.size >= min_inliers and len(conductors) < max_conductors:
        pts = xyz[remaining]; m = pts.shape[0]
        best_inliers, best_count = None, 0
        for _ in range(iters):
            i, j = rng.integers(0, m, size=2)
            if i == j:
                continue
            a = pts[i]; d = pts[j] - a
            nrm = np.linalg.norm(d)
            if nrm < 1e-6:
                continue
            d = d / nrm
            ap = pts - a
            proj = ap @ d
            dist = np.linalg.norm(ap - np.outer(proj, d), axis=1)
            inl = dist < tol
            c = int(inl.sum())
            if c > best_count:
                best_count, best_inliers = c, inl
        if best_inliers is None or best_count < min_inliers:
            break
        conductors.append(remaining[best_inliers])
        remaining = remaining[~best_inliers]
    return conductors


def fit_conductor_model(pts):
    centroid = pts.mean(axis=0)
    evals, evecs = np.linalg.eigh(np.cov((pts - centroid).T))
    order = np.argsort(evals)[::-1]
    u, v, w = evecs[:, order[0]], evecs[:, order[1]], evecs[:, order[2]]
    rel = pts - centroid
    t = rel @ u
    deg = 2 if pts.shape[0] >= 5 else 1
    return {"centroid": centroid, "u": u, "v": v, "w": w,
            "pv": np.polyfit(t, rel @ v, deg), "pw": np.polyfit(t, rel @ w, deg),
            "t_min": float(t.min()), "t_max": float(t.max())}


def pass_a_reclaim(xyz, pred, rng, wire_tol=WIRE_TOL):
    seed_idx = np.flatnonzero(pred == LAS_WIRE)
    cand_idx = np.flatnonzero(np.isin(pred, RECLAIM_FROM))
    if seed_idx.size < RANSAC_MIN_INLIERS or cand_idx.size == 0:
        return np.empty(0, np.int64), 0
    seed_xyz = xyz[seed_idx]
    conductors = ransac_extract_conductors(seed_xyz, RANSAC_TOL, RANSAC_ITERS,
                                           RANSAC_MIN_INLIERS, MAX_CONDUCTORS, rng)
    if not conductors:
        return np.empty(0, np.int64), 0
    cand_xyz = xyz[cand_idx]
    cand_tree = cKDTree(cand_xyz)
    perp_best = np.full(cand_xyz.shape[0], np.inf)
    pref = wire_tol + WIRE_PREFILTER
    for ci in conductors:
        model = fit_conductor_model(seed_xyz[ci])
        near = cand_tree.query_ball_point(seed_xyz[ci], r=pref)
        local = set()
        for lst in near:
            local.update(lst)
        if not local:
            continue
        local = np.fromiter(local, np.int64)
        rel = cand_xyz[local] - model["centroid"]
        t = rel @ model["u"]
        in_t = (t >= model["t_min"] - T_MARGIN) & (t <= model["t_max"] + T_MARGIN)
        if not in_t.any():
            continue
        local = local[in_t]; t = t[in_t]; rel = cand_xyz[local] - model["centroid"]
        perp = np.hypot(rel @ model["v"] - np.polyval(model["pv"], t),
                        rel @ model["w"] - np.polyval(model["pw"], t))
        np.minimum.at(perp_best, local, perp)
    return cand_idx[perp_best < wire_tol], len(conductors)


# ============================ Pass B geometry ============================
def dbscan_xyz(xyz, eps, min_samples):
    n = xyz.shape[0]
    labels = np.full(n, -1, np.int64)
    if n == 0:
        return labels
    tree = cKDTree(xyz)
    visited = np.zeros(n, bool)
    cid = 0
    for i in range(n):
        if visited[i]:
            continue
        visited[i] = True
        seeds = tree.query_ball_point(xyz[i], r=eps)
        if len(seeds) < min_samples:
            continue
        labels[i] = cid
        seed_set = list(seeds); head = 0
        while head < len(seed_set):
            j = seed_set[head]; head += 1
            if not visited[j]:
                visited[j] = True
                js = tree.query_ball_point(xyz[j], r=eps)
                if len(js) >= min_samples:
                    seed_set.extend([s for s in js if not visited[s]])
            if labels[j] == -1:
                labels[j] = cid
        cid += 1
    return labels


def build_pole_models(seed_xyz, labels, ground_tree, ground_z):
    models = []
    n_clusters = int(labels.max() + 1) if labels.max() >= 0 else 0
    for cid in range(n_clusters):
        m = seed_xyz[labels == cid]
        if m.shape[0] < DBSCAN_MIN:
            continue
        z_base, z_top = float(m[:, 2].min()), float(m[:, 2].max())
        vert = z_top - z_base
        shaft = m[m[:, 2] <= z_base + SHAFT_FRAC * vert]
        if shaft.shape[0] >= 5:
            cx, cy = float(shaft[:, 0].mean()), float(shaft[:, 1].mean())
            shaft_r = float(np.hypot(shaft[:, 0] - cx, shaft[:, 1] - cy).max())
        else:
            cx, cy = float(m[:, 0].mean()), float(m[:, 1].mean())
            shaft_r = float(np.hypot(m[:, 0] - cx, m[:, 1] - cy).max())
        horiz = float(np.hypot(m[:, 0] - cx, m[:, 1] - cy).max())
        ratio = vert / max(horiz, 1e-3)
        gz, connected = z_base, True
        if ground_tree is not None:
            gi = ground_tree.query_ball_point((cx, cy), r=GROUND_RADIUS)
            if gi:
                gz = float(np.median(ground_z[gi]))
                connected = (z_base <= gz + GROUND_GAP)
        if vert >= MIN_VERT and shaft_r <= MAX_SHAFT and ratio >= MIN_RATIO and connected:
            models.append({"cx": cx, "cy": cy, "z_lo": gz - Z_MARGIN, "z_hi": z_top + Z_MARGIN})
    return models


def candidate_axis_dist(models, cand_xyz):
    best = np.full(cand_xyz.shape[0], np.inf)
    tree2d = cKDTree(cand_xyz[:, :2])
    for mo in models:
        idx = tree2d.query_ball_point((mo["cx"], mo["cy"]), r=POLE_AXIS_PREFILTER)
        if not idx:
            continue
        idx = np.asarray(idx, np.int64)
        zc = cand_xyz[idx, 2]
        in_z = (zc >= mo["z_lo"]) & (zc <= mo["z_hi"])
        if not in_z.any():
            continue
        idx = idx[in_z]
        d = np.hypot(cand_xyz[idx, 0] - mo["cx"], cand_xyz[idx, 1] - mo["cy"])
        np.minimum.at(best, idx, d)
    return best


def vert_linearity(query_xyz, tree, src_xyz, radius):
    out = np.zeros(query_xyz.shape[0])
    nbrs = tree.query_ball_point(query_xyz, r=radius)
    for k, nb in enumerate(nbrs):
        if len(nb) < 4:
            continue
        P = src_xyz[nb]
        ev, evec = np.linalg.eigh(np.cov((P - P.mean(0)).T))
        out[k] = abs(evec[:, int(np.argmax(ev))][2])
    return out


def pass_b_reclaim(xyz, pred, radius=POLE_RADIUS, vmin=POLE_VMIN):
    seed_idx = np.flatnonzero(pred == LAS_POLE)
    cand_idx = np.flatnonzero(np.isin(pred, RECLAIM_FROM))
    if seed_idx.size < DBSCAN_MIN or cand_idx.size == 0:
        return np.empty(0, np.int64), 0
    seed_xyz = xyz[seed_idx]
    g = np.flatnonzero(pred == LAS_GROUND)
    gtree = cKDTree(xyz[g][:, :2]) if g.size else None
    gz = xyz[g][:, 2] if g.size else None
    labels = dbscan_xyz(seed_xyz, DBSCAN_EPS, DBSCAN_MIN)
    models = build_pole_models(seed_xyz, labels, gtree, gz)
    if not models:
        return np.empty(0, np.int64), 0
    cand_xyz = xyz[cand_idx]
    axd = candidate_axis_dist(models, cand_xyz)
    sel = axd < radius
    if vmin > 0:
        pool = np.flatnonzero(axd < POLE_AXIS_PREFILTER)
        if pool.size:
            vlin = np.zeros(cand_idx.size)
            vlin[pool] = vert_linearity(xyz[cand_idx[pool]], cKDTree(xyz), xyz, LIN_RADIUS)
            sel &= (vlin >= vmin)
    return cand_idx[sel], len(models)


# ============================ driver ============================
def process_one(in_path: Path, out_path: Path, params: dict) -> dict:
    import laspy
    las = laspy.read(in_path)
    xyz = np.column_stack([np.asarray(las.x), np.asarray(las.y), np.asarray(las.z)]).astype(np.float64)
    pred = np.asarray(las.classification, np.int32)
    rng = np.random.default_rng(params["seed"])

    new_pred = pred.copy()
    wire_act = np.zeros(pred.size, np.uint8)
    pole_act = np.zeros(pred.size, np.uint8)

    # PASS A (wire) — fit from the original predictions
    a_idx, n_cond = pass_a_reclaim(xyz, pred, rng, params["wire_tol"])
    new_pred[a_idx] = LAS_WIRE
    wire_act[a_idx] = 1

    # PASS B (pole) — operate on the A-updated labels (A's wire pts are not pole candidates)
    b_idx, n_poles = pass_b_reclaim(xyz, new_pred, params["pole_radius"], params["pole_vmin"])
    new_pred[b_idx] = LAS_POLE
    pole_act[b_idx] = 1

    out = laspy.LasData(header=las.header, points=las.points.copy())
    out.classification = new_pred
    for name, desc in (("wire_postproc_action", "Pass A wire reclaim"),
                       ("pole_postproc_action", "Pass B pole reclaim")):
        if name not in out.point_format.dimension_names:
            out.add_extra_dim(laspy.ExtraBytesParams(name=name, type=np.uint8, description=desc))
    out.wire_postproc_action = wire_act
    out.pole_postproc_action = pole_act
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out.write(out_path)

    return {"input": str(in_path), "output": str(out_path), "n_points": int(pred.size),
            "n_conductors": int(n_cond), "n_poles": int(n_poles),
            "wire_reclaimed": int(a_idx.size), "pole_reclaimed": int(b_idx.size),
            "wire_before": int((pred == LAS_WIRE).sum()), "wire_after": int((new_pred == LAS_WIRE).sum()),
            "pole_before": int((pred == LAS_POLE).sum()), "pole_after": int((new_pred == LAS_POLE).sum())}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--input", type=Path, help="One combined LAS/LAZ to correct.")
    g.add_argument("--input-dir", type=Path, help="Directory of combined LAS/LAZ (batch mode).")
    p.add_argument("--output", type=Path, help="Output path (single-file mode).")
    p.add_argument("--output-dir", type=Path, help="Output directory (batch mode).")
    p.add_argument("--pattern", default="*_tf1_pointconv_combined_0p1m.la[sz]",
                   help="Glob for batch mode.")
    p.add_argument("--wire-tol", type=float, default=WIRE_TOL)
    p.add_argument("--pole-radius", type=float, default=POLE_RADIUS)
    p.add_argument("--pole-vmin", type=float, default=POLE_VMIN)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--summary-json", type=Path, default=None, help="Where to write the run summary.")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    params = {"wire_tol": args.wire_tol, "pole_radius": args.pole_radius,
              "pole_vmin": args.pole_vmin, "seed": args.seed}
    results = []
    if args.input is not None:
        if args.output is None:
            sys.exit("--output is required with --input")
        print(f"[wire_pole_postproc] {args.input.name}")
        r = process_one(args.input, args.output, params)
        results.append(r)
        print(f"  conductors {r['n_conductors']}  poles {r['n_poles']}  "
              f"wire +{r['wire_reclaimed']}  pole +{r['pole_reclaimed']}")
    else:
        if args.output_dir is None:
            sys.exit("--output-dir is required with --input-dir")
        files = sorted(args.input_dir.glob(args.pattern))
        if not files:
            sys.exit(f"no files match {args.pattern} in {args.input_dir}")
        print(f"[wire_pole_postproc] {len(files)} file(s)")
        for f in files:
            r = process_one(f, args.output_dir / f.name, params)
            results.append(r)
            print(f"  {f.name.split(' ')[0]}: conductors {r['n_conductors']} poles {r['n_poles']} "
                  f"| wire +{r['wire_reclaimed']} pole +{r['pole_reclaimed']}")
    if args.summary_json:
        args.summary_json.parent.mkdir(parents=True, exist_ok=True)
        args.summary_json.write_text(json.dumps({"params": params, "files": results}, indent=2))
        print(f"wrote {args.summary_json}")


if __name__ == "__main__":
    main()
