"""Geometry-derived per-point input features for PointConv (ablation C).

Shared by BOTH the training prep (finetune/prepare_finetune_data.py) and the
inference path (tf1/PointCONV/SamplePoints_Parr_Deterministic.py) so the model
sees identical feature semantics at train and inference time.

Features (all rotation-invariant about Z, all in [0, 1], float32):
  hag        height above local ground / 20 m (clipped). Ground = per-cell
             2nd-percentile Z on a 1 m XY grid, empty cells filled from the
             nearest valid cell. Separates scrub (0.2-1 m) from bare ground
             regardless of terrain slope.
  linearity  (l1 - l2) / l1 of the local PCA eigenvalues (k-NN neighborhood).
             ~1 for wires/pole shafts, ~0 for scattered scrub, ~0.5 planar.
  verticality |z-component of the principal eigenvector|. ~1 for poles/trunks,
             ~0 for wires (horizontal linear) and planar ground.

Everything is pure numpy/scipy (available in the mmworkflow pdal env) and
vectorized: neighbor search via one batched cKDTree.query, covariance via
einsum, eigendecomposition via numpy's batched eigh on (N,3,3) stacks.
"""
from __future__ import annotations

import numpy as np
from scipy.spatial import cKDTree


HAG_CLIP_M = 20.0          # normalization ceiling (towers/trees clip to 1.0)
HAG_CELL_M = 1.0           # ground-grid cell size
HAG_PERCENTILE = 2.0       # per-cell ground percentile (robust to low noise)
PCA_K = 16                 # neighbors for the local PCA


def compute_ground_grid(xyz: np.ndarray, cell: float = HAG_CELL_M,
                        percentile: float = HAG_PERCENTILE):
    """Per-cell low-percentile Z on an XY grid; empty cells filled by nearest
    valid cell (iterative 3x3 min-dilation). Returns (grid, x0, y0, nx, ny)."""
    x, y, z = xyz[:, 0], xyz[:, 1], xyz[:, 2]
    x0, y0 = float(x.min()), float(y.min())
    ix = np.minimum(((x - x0) / cell).astype(np.int64), int((x.max() - x0) / cell))
    iy = np.minimum(((y - y0) / cell).astype(np.int64), int((y.max() - y0) / cell))
    nx, ny = int(ix.max()) + 1, int(iy.max()) + 1
    cell_id = ix * ny + iy

    # Sort once; take the per-cell percentile by rank within each group.
    order = np.argsort(cell_id, kind="stable")
    cid_s, z_s = cell_id[order], z[order]
    starts = np.flatnonzero(np.r_[True, cid_s[1:] != cid_s[:-1]])
    counts = np.diff(np.r_[starts, cid_s.size])
    ranks = np.minimum((counts * (percentile / 100.0)).astype(np.int64), counts - 1)
    # z within each group must be sorted to take a rank: sort by (cell, z).
    order2 = np.lexsort((z, cell_id))
    z_s2 = z[order2]
    ground_per_cell = z_s2[starts + ranks]
    cells = cid_s[starts]

    grid = np.full((nx * ny,), np.nan, dtype=np.float64)
    grid[cells] = ground_per_cell
    grid = grid.reshape(nx, ny)

    # Fill empty cells from neighbors (min over 3x3, iterate until full).
    if np.isnan(grid).any():
        filled = grid.copy()
        for _ in range(max(nx, ny)):
            nanmask = np.isnan(filled)
            if not nanmask.any():
                break
            padded = np.pad(filled, 1, constant_values=np.nan)
            stacks = [padded[i:i + nx, j:j + ny] for i in range(3) for j in range(3)]
            neighbor_min = np.nanmin(np.stack(stacks, axis=0), axis=0)
            filled[nanmask] = neighbor_min[nanmask]
        filled[np.isnan(filled)] = np.nanmin(grid)  # fully empty pathological case
        grid = filled
    return grid, x0, y0, nx, ny


def compute_hag(xyz: np.ndarray, cell: float = HAG_CELL_M,
                percentile: float = HAG_PERCENTILE,
                clip_m: float = HAG_CLIP_M) -> np.ndarray:
    """Normalized height above local ground, (N,) float32 in [0, 1]."""
    grid, x0, y0, nx, ny = compute_ground_grid(xyz, cell, percentile)
    ix = np.clip(((xyz[:, 0] - x0) / cell).astype(np.int64), 0, nx - 1)
    iy = np.clip(((xyz[:, 1] - y0) / cell).astype(np.int64), 0, ny - 1)
    hag = xyz[:, 2] - grid[ix, iy]
    return (np.clip(hag, 0.0, clip_m) / clip_m).astype(np.float32)


def compute_pca_features(xyz: np.ndarray, k: int = PCA_K,
                         chunk: int = 1_000_000) -> tuple[np.ndarray, np.ndarray]:
    """(linearity, verticality), each (N,) float32 in [0, 1].

    Batched: one cKDTree.query for k-NN, then per-chunk einsum covariance +
    numpy batched eigh on (chunk, 3, 3)."""
    n = xyz.shape[0]
    kk = min(k, n)
    tree = cKDTree(xyz)
    linearity = np.empty(n, dtype=np.float32)
    verticality = np.empty(n, dtype=np.float32)
    for s in range(0, n, chunk):
        e = min(s + chunk, n)
        _, idx = tree.query(xyz[s:e], k=kk, workers=-1)
        nb = xyz[idx]                                   # (m, k, 3)
        nb = nb - nb.mean(axis=1, keepdims=True)
        cov = np.einsum("mki,mkj->mij", nb, nb) / float(kk)   # (m, 3, 3)
        vals, vecs = np.linalg.eigh(cov)                # ascending eigenvalues
        l1 = vals[:, 2]
        l2 = vals[:, 1]
        with np.errstate(divide="ignore", invalid="ignore"):
            lin = np.where(l1 > 1e-12, (l1 - l2) / np.maximum(l1, 1e-12), 0.0)
        e1 = vecs[:, :, 2]                              # principal eigenvector
        linearity[s:e] = np.clip(lin, 0.0, 1.0).astype(np.float32)
        verticality[s:e] = np.abs(e1[:, 2]).astype(np.float32)
    return linearity, verticality


def compute_features(xyz: np.ndarray) -> np.ndarray:
    """All feature channels for a cloud: (N, 3) float32 [hag, linearity, verticality]."""
    hag = compute_hag(xyz)
    lin, vert = compute_pca_features(xyz)
    return np.column_stack([hag, lin, vert]).astype(np.float32)


FEATURE_NAMES = ["hag", "linearity", "verticality"]


if __name__ == "__main__":
    # Self-test on synthetic geometry: sloped ground + a pole + a wire + scrub.
    rng = np.random.default_rng(42)
    gx = rng.uniform(0, 40, 20000)
    gy = rng.uniform(0, 40, 20000)
    ground = np.column_stack([gx, gy, 0.2 * gx + rng.normal(0, 0.02, gx.size)])
    pole = np.column_stack([np.full(300, 20.0) + rng.normal(0, 0.03, 300),
                            np.full(300, 20.0) + rng.normal(0, 0.03, 300),
                            0.2 * 20 + np.linspace(0, 12, 300)])
    wire = np.column_stack([np.linspace(0, 40, 400),
                            np.full(400, 10.0) + rng.normal(0, 0.01, 400),
                            0.2 * 20 + 8 + rng.normal(0, 0.01, 400)])
    scrub_c = rng.uniform(5, 35, (40, 2))
    scrub = np.concatenate([
        np.column_stack([c[0] + rng.normal(0, 0.3, 60), c[1] + rng.normal(0, 0.3, 60),
                         0.2 * c[0] + rng.uniform(0.05, 0.8, 60)]) for c in scrub_c])
    xyz = np.concatenate([ground, pole, wire, scrub])
    feats = compute_features(xyz)
    n_g, n_p, n_w = ground.shape[0], pole.shape[0], wire.shape[0]
    sl = {
        "ground": slice(0, n_g),
        "pole": slice(n_g, n_g + n_p),
        "wire": slice(n_g + n_p, n_g + n_p + n_w),
        "scrub": slice(n_g + n_p + n_w, None),
    }
    for name, s in sl.items():
        f = feats[s]
        print(f"{name:7s} hag={f[:,0].mean():.3f}  lin={f[:,1].mean():.3f}  vert={f[:,2].mean():.3f}")
    assert feats[sl["ground"]][:, 0].mean() < 0.02, "ground HAG should be ~0 despite slope"
    assert feats[sl["pole"]][:, 0].mean() > 0.2 and feats[sl["pole"]][:, 2].mean() > 0.8
    assert feats[sl["wire"]][:, 1].mean() > 0.9 and feats[sl["wire"]][:, 2].mean() < 0.2
    assert feats[sl["scrub"]][:, 0].mean() > 0.01 and feats[sl["scrub"]][:, 1].mean() < 0.75
    print("GEOMETRY FEATURES SELF-TEST PASSED (sloped ground ~0 HAG; pole vertical; "
          "wire linear+horizontal; scrub elevated+non-linear)")
