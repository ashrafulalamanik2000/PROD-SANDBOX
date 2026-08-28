"""QA a high-veg TREE CLUSTER deliverable folder.

Reports, per *.shp, the metrics that define this deliverable's quality:
  - feature count and 2D/3D
  - vertices per metre of boundary
  - % sharp corners (>60 deg direction change)  <- the TRUE smoothness metric.
        blocky raster staircase ~= 45-51% ; well-smoothed <= ~0.5%.
        DO NOT judge smoothness by vertex count -- count DROPS when smoothing works.
  - overlapping cluster pairs (>0.5 m^2)         <- should be ~0 after the dissolve step.

Usage:  python qa_check.py <folder-of-shp> [reference.shp ...]
"""
import sys, glob, os
import numpy as np, geopandas as gpd
from shapely.geometry import Polygon
from shapely.strtree import STRtree

def report(path):
    g = gpd.read_file(path)
    nv = sharp = tot = 0; ln = 0.0; is3d = False
    polys = []
    for geom in g.geometry:
        if geom is None: continue
        for p in (geom.geoms if geom.geom_type.startswith("Multi") else [geom]):
            c = np.asarray(p.coords); nv += len(c); ln += p.length
            if c.shape[1] > 2: is3d = True
            if len(c) >= 3:
                v1 = c[1:-1,:2]-c[:-2,:2]; v2 = c[2:,:2]-c[1:-1,:2]
                n1 = np.linalg.norm(v1,axis=1); n2 = np.linalg.norm(v2,axis=1)
                ok = (n1>1e-9)&(n2>1e-9)
                cos = np.clip((v1*v2).sum(1)[ok]/(n1[ok]*n2[ok]),-1,1)
                t = np.degrees(np.arccos(cos)); sharp += int((t>60).sum()); tot += int(ok.sum())
            try:
                poly = Polygon(c[:,:2])
                if not poly.is_valid: poly = poly.buffer(0)
                if not poly.is_empty and poly.area > 0: polys.append(poly)
            except Exception: pass
    tree = STRtree(polys); ov = 0
    for a in polys:
        for hit in tree.query(a):
            # shapely 2.x STRtree.query returns integer indices; 1.x returns geometries
            b = polys[hit] if isinstance(hit, (int, np.integer)) else hit
            if b is a: continue
            if a.intersection(b).area > 0.5: ov += 1
    ov //= 2
    name = os.path.basename(path)
    print(f"{name:>44}: {len(g):>5} feats | {'3D' if is3d else '2D'} | "
          f"{nv/max(ln,1):.2f} v/m | {100*sharp/max(tot,1):5.1f}% sharp | {ov:>4} overlap pairs")

def main():
    if len(sys.argv) < 2:
        print(__doc__); sys.exit(1)
    targets = []
    for arg in sys.argv[1:]:
        targets += sorted(glob.glob(os.path.join(arg, "*.shp"))) if os.path.isdir(arg) else [arg]
    for t in targets:
        report(t)

if __name__ == "__main__":
    main()
