"""High-veg TREE CLUSTER outlines from classified LAS/LAZ.

Per site, for HIGH vegetation (canopy height-above-ground >= --med, default 3 m):
  1. reclassify veg (class 5) into Low(3)/Med(4)/High(5) by HAG   [reclassify_veg_hag.py]
  2. build 2D TREE CLUSTER outline lines from HIGH veg, with a HEIGHT (max canopy) attr,
     DISSOLVING touching/overlapping footprints into single clusters and SMOOTHING them
     (morphological close/open + light Douglas-Peucker) so they are not blocky staircases
  3. drape Z onto the lines -> PolyLineZ (3D)
  4. copy ONLY the 3D (_Z) high-veg lines into the deliverable folder

All CPU. Idempotent: skips reclass tiles / shp already present (delete them to rebuild).

Portable: paths + sites come from a JSON config (or CLI), the reclassify script is
resolved next to THIS file, and the buffer call works on both shapely 1.x (resolution=)
and shapely 2.x (quad_segs=). Runs under gdal_env, the mmworkflow container, or myenv.

Usage:
  python run_highveg_clusters.py --config sites.json [SITE ...] [--workroot DIR] [--final DIR]
  # SITE ... optional filter; default = every site in the config.

sites.json (see sites.example.json):
  {
    "src_root": "\\\\SDAI-FS1\\...\\All Segmented",   # optional base for "subdirs"
    "sites": {
      "GIL_M2": {"epsg": 26915, "subdirs": ["gil_m2_0523"]},
      "PNT_M3": {"epsg": 26914, "subdirs": ["pnt_m3_a", "pnt_m3_b"]},
      "OTHER":  {"epsg": 26914, "laz": ["D:/abs/path/*.laz"]}   # explicit globs instead
    }
  }
"""
import glob, os, sys, time, json, shutil, argparse, subprocess
from collections import defaultdict
import numpy as np, laspy, geopandas as gpd
from scipy.ndimage import distance_transform_edt, binary_closing, label as ndi_label, maximum as ndi_max
from shapely.geometry import LineString, MultiLineString, box
from shapely.ops import unary_union
import shapely
# NOTE: some envs (e.g. myenv) have ABI-broken rasterio/gdal (numpy 2.x dtype mismatch),
# so we vectorize the labelled raster with a pure-shapely run-length union instead of
# rasterio.features.shapes.

PY = sys.executable
RECLASS_PY = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reclassify_veg_hag.py")

# ---- tunables (overridable via CLI); defaults reproduce the DFX pas_m14 deliverable ----
LOW = "1.0"; MED = "3.0"          # HAG bands (m): <LOW->3, LOW..MED->4, >=MED->5 (HIGH)
DEM_CELL = 1.0; CHM_CELL = 0.5    # ground DEM / canopy-height-model cell size (m)
CLUSTER_GAP = 2.0; MIN_AREA = 5.0 # raster close radius (m) ; drop clusters < this area (m^2)
# vector smoothing (dissolve THEN close/open per merged cluster, matches cluster_smooth ref).
# SM_SIMP must stay SMALL (~0.1): a large value collapses the rounded arcs back to facets.
SM_GAP = 2.0; SM_OPEN = 0.75; SM_SIMP = 0.1; SM_QS = 16
HIGH_CLASS = 5; GROUND_CLASS = 2; CHUNK = 5_000_000

_SHP2 = int(shapely.__version__.split(".")[0]) >= 2
def _buffer(geom, dist, qs):
    """Round-join buffer; arc resolution = qs segments/quadrant.
    shapely 2.x names it quad_segs=, shapely 1.x names it resolution=."""
    if _SHP2:
        return geom.buffer(dist, join_style="round", quad_segs=qs)
    return geom.buffer(dist, join_style=1, resolution=qs)

# ---------------------------------------------------------------- build clusters
def _ground_dem(laz):
    xmin=ymin=xmax=ymax=None
    with laspy.open(laz) as r:
        for ch in r.chunk_iterator(CHUNK):
            m=np.asarray(ch.classification)==GROUND_CLASS
            if not m.any(): continue
            x=np.asarray(ch.x)[m]; y=np.asarray(ch.y)[m]
            xmin=x.min() if xmin is None else min(xmin,x.min()); ymin=y.min() if ymin is None else min(ymin,y.min())
            xmax=x.max() if xmax is None else max(xmax,x.max()); ymax=y.max() if ymax is None else max(ymax,y.max())
    if xmin is None: return None
    ix0=int(np.floor(xmin/DEM_CELL)); iy0=int(np.floor(ymin/DEM_CELL))
    dnx=int(np.floor(xmax/DEM_CELL))-ix0+1; dny=int(np.floor(ymax/DEM_CELL))-iy0+1
    minz=np.full(dnx*dny,np.inf)
    with laspy.open(laz) as r:
        for ch in r.chunk_iterator(CHUNK):
            m=np.asarray(ch.classification)==GROUND_CLASS
            if not m.any(): continue
            x=np.asarray(ch.x)[m]; y=np.asarray(ch.y)[m]; z=np.asarray(ch.z)[m]
            flat=(np.floor(y/DEM_CELL).astype(np.int64)-iy0)*dnx+(np.floor(x/DEM_CELL).astype(np.int64)-ix0)
            np.minimum.at(minz,flat,z)
    dem=minz.reshape(dny,dnx); nan=~np.isfinite(dem)
    if nan.any(): dem=dem[tuple(distance_transform_edt(nan,return_distances=False,return_indices=True))]
    return dem,ix0,iy0,dnx,dny

def _chm_for_tile(laz):
    g=_ground_dem(laz)
    if g is None: return None,None
    dem,ix0,iy0,dnx,dny=g
    def ground_at(x,y):
        col=np.clip(np.floor(x/DEM_CELL).astype(np.int64)-ix0,0,dnx-1)
        row=np.clip(np.floor(y/DEM_CELL).astype(np.int64)-iy0,0,dny-1)
        return dem[row,col]
    pts=[]
    with laspy.open(laz) as r:
        for ch in r.chunk_iterator(CHUNK):
            m=np.asarray(ch.classification)==HIGH_CLASS
            if not m.any(): continue
            x=np.asarray(ch.x)[m]; y=np.asarray(ch.y)[m]; z=np.asarray(ch.z)[m]
            pts.append(np.c_[x,y,z-ground_at(x,y)])
    if not pts: return None,None
    P=np.vstack(pts)
    x0=np.floor(P[:,0].min()/CHM_CELL)*CHM_CELL; y1=np.ceil(P[:,1].max()/CHM_CELL)*CHM_CELL
    cnx=int(np.ceil((P[:,0].max()-x0)/CHM_CELL))+1; cny=int(np.ceil((y1-P[:,1].min())/CHM_CELL))+1
    chm=np.zeros((cny,cnx),dtype=np.float32)
    col=np.floor((P[:,0]-x0)/CHM_CELL).astype(np.int64); row=np.floor((y1-P[:,1])/CHM_CELL).astype(np.int64)
    np.maximum.at(chm,(row,col),P[:,2].astype(np.float32))
    return chm, x0, y1

def _polygonize_labels(labels, x0, y1, cell):
    """rasterio-free: one shapely (Multi)Polygon per label, cell-square edges.
    Builds boxes from horizontal same-label runs per row, unions per label.
    Pixel (r,c) covers x in [x0+c*cell, x0+(c+1)*cell], y in [y1-(r+1)*cell, y1-r*cell]."""
    ny, nx = labels.shape
    boxes = defaultdict(list)
    for r in range(ny):
        row = labels[r]
        idx = np.nonzero(row)[0]
        if idx.size == 0:
            continue
        # split idx into runs that are both contiguous AND same label value
        brk = np.where((np.diff(idx) != 1) | (row[idx[1:]] != row[idx[:-1]]))[0] + 1
        yt = y1 - r * cell; yb = y1 - (r + 1) * cell
        for run in np.split(idx, brk):
            lab = int(row[run[0]]); c0 = int(run[0]); c1 = int(run[-1]) + 1
            boxes[lab].append(box(x0 + c0 * cell, yb, x0 + c1 * cell, yt))
    return {lab: unary_union(bxs) for lab, bxs in boxes.items()}

def build_clusters(reclass_dir, dst, epsg):
    r=int(round(CLUSTER_GAP/CHM_CELL))
    yy,xx=np.ogrid[-r:r+1,-r:r+1]; disk=(xx*xx+yy*yy)<=r*r
    conn=np.ones((3,3),dtype=bool)
    raw_polys=[]; raw_h=[]      # cell-square label footprints + canopy hmax, ALL tiles
    for laz in sorted(glob.glob(os.path.join(reclass_dir,"*.laz"))):
        nm=os.path.splitext(os.path.basename(laz))[0]
        chm,x0,y1=_chm_for_tile(laz)
        if chm is None: print(f"    {nm}: no high veg",flush=True); continue
        mask=chm>0
        closed=binary_closing(mask,structure=disk)
        labels,n=ndi_label(closed,structure=conn)
        if n==0: print(f"    {nm}: 0 clusters",flush=True); continue
        hmax_by_lab=ndi_max(chm,labels,index=np.arange(1,n+1))  # per-label max canopy ht
        polys=_polygonize_labels(labels,x0,y1,CHM_CELL)
        tf=0
        for lab,poly in polys.items():
            if poly is None or poly.is_empty or poly.area<=0: continue
            hmax=float(hmax_by_lab[lab-1])
            if not np.isfinite(hmax) or hmax<=0: continue
            raw_polys.append(poly); raw_h.append(hmax); tf+=1  # keep small ones too: they may merge
        print(f"    {nm}: {tf} footprints",flush=True)
    if not raw_polys:
        print("    WARNING: no clusters produced",flush=True); return None
    # DISSOLVE: union all overlapping/touching footprints into single clusters (also heals
    # tile-edge seams). Each merged cluster keeps the MAX canopy height of its members, THEN
    # is smoothed -- so neighbours become one cluster instead of overlapping smoothed lines.
    merged=unary_union(raw_polys)
    mparts=[m for m in (merged.geoms if merged.geom_type=="MultiPolygon" else [merged])
            if not m.is_empty and m.area>=MIN_AREA]
    src=gpd.GeoDataFrame({"H":raw_h},geometry=raw_polys,crs=f"EPSG:{epsg}")
    mg=gpd.GeoDataFrame({"cid":range(len(mparts))},geometry=mparts,crs=f"EPSG:{epsg}")
    hbyc=gpd.sjoin(src,mg,predicate="intersects",how="inner").groupby("cid")["H"].max().to_dict()
    feats=[]
    for cid,mp in enumerate(mparts):
        hmax=hbyc.get(cid)
        if hmax is None: continue
        poly=_buffer(_buffer(_buffer(_buffer(mp, SM_GAP, SM_QS), -SM_GAP, SM_QS),
                             -SM_OPEN, SM_QS), SM_OPEN, SM_QS).simplify(SM_SIMP)
        if poly.is_empty: continue
        rings=[poly.exterior] if poly.geom_type=="Polygon" else [p.exterior for p in poly.geoms]
        ls=LineString(rings[0].coords) if len(rings)==1 else MultiLineString([rg.coords for rg in rings])
        feats.append({"FEATURE_CO":"TREE CLUSTER","HEIGHT":round(float(hmax),2),
                      "SHAPE_Leng":round(float(ls.length),3),"geometry":ls})
    if not feats:
        print("    WARNING: no clusters after dissolve",flush=True); return None
    gdf=gpd.GeoDataFrame(feats,crs=f"EPSG:{epsg}")
    gdf.to_file(dst); gdf.to_file(dst.replace(".shp",".gpkg"),driver="GPKG")
    print(f"    {len(gdf)} TREE CLUSTERs (dissolved from {len(raw_polys)} footprints) -> {dst}",flush=True)
    print(f"    HEIGHT m: min {gdf.HEIGHT.min():.2f} | mean {gdf.HEIGHT.mean():.2f} | max {gdf.HEIGHT.max():.2f}",flush=True)
    return dst

# ---------------------------------------------------------------- drape Z
def drape(reclass_dir, in_shp, out_shp):
    laz=sorted(glob.glob(os.path.join(reclass_dir,"*.laz")))
    xmin=ymin=xmax=ymax=None
    for f in laz:
        with laspy.open(f) as r:
            for ch in r.chunk_iterator(CHUNK):
                m=np.asarray(ch.classification)==GROUND_CLASS
                if not m.any(): continue
                x=np.asarray(ch.x)[m]; y=np.asarray(ch.y)[m]
                xmin=x.min() if xmin is None else min(xmin,x.min()); ymin=y.min() if ymin is None else min(ymin,y.min())
                xmax=x.max() if xmax is None else max(xmax,x.max()); ymax=y.max() if ymax is None else max(ymax,y.max())
    ix0=int(np.floor(xmin/DEM_CELL)); iy0=int(np.floor(ymin/DEM_CELL))
    nx=int(np.floor(xmax/DEM_CELL))-ix0+1; ny=int(np.floor(ymax/DEM_CELL))-iy0+1
    minz=np.full(nx*ny,np.inf)
    for f in laz:
        with laspy.open(f) as r:
            for ch in r.chunk_iterator(CHUNK):
                m=np.asarray(ch.classification)==GROUND_CLASS
                if not m.any(): continue
                x=np.asarray(ch.x)[m]; y=np.asarray(ch.y)[m]; z=np.asarray(ch.z)[m]
                flat=(np.floor(y/DEM_CELL).astype(np.int64)-iy0)*nx+(np.floor(x/DEM_CELL).astype(np.int64)-ix0)
                np.minimum.at(minz,flat,z)
    dem=minz.reshape(ny,nx); nan=~np.isfinite(dem)
    if nan.any(): dem=dem[tuple(distance_transform_edt(nan,return_distances=False,return_indices=True))]
    def ground_at(xy):
        col=np.clip(np.floor(xy[:,0]/DEM_CELL).astype(np.int64)-ix0,0,nx-1)
        row=np.clip(np.floor(xy[:,1]/DEM_CELL).astype(np.int64)-iy0,0,ny-1)
        return dem[row,col]
    g=gpd.read_file(in_shp)
    def d(geom):
        parts=geom.geoms if geom.geom_type=="MultiLineString" else [geom]
        out=[]
        for ls in parts:
            xy=np.asarray(ls.coords)[:,:2]
            out.append(LineString(np.column_stack([xy,ground_at(xy)])))
        return out[0] if len(out)==1 else MultiLineString(out)
    g["geometry"]=g.geometry.apply(d)
    g.to_file(out_shp); g.to_file(out_shp.replace(".shp",".gpkg"),driver="GPKG")
    print(f"    draped {len(g)} clusters -> {out_shp}",flush=True)

# ---------------------------------------------------------------- copy finals
def copy_final(shp, dst_dir):
    os.makedirs(dst_dir,exist_ok=True)
    stem=os.path.splitext(shp)[0]
    for ext in (".shp",".shx",".dbf",".prj",".cpg",".gpkg"):
        s=stem+ext
        if os.path.exists(s): shutil.copy2(s, os.path.join(dst_dir, os.path.basename(s)))

# ---------------------------------------------------------------- config
def load_sites(cfg_path):
    with open(cfg_path) as f: cfg=json.load(f)
    root=cfg.get("src_root","")
    sites={}
    for name,spec in cfg["sites"].items():
        laz=[]
        for sub in spec.get("subdirs",[]):
            laz+=sorted(glob.glob(os.path.join(root,sub,"*.laz")))
        for pat in spec.get("laz",[]):
            laz+=sorted(glob.glob(pat))
        sites[name]=(int(spec["epsg"]), laz)
    return sites

# ---------------------------------------------------------------- main
def main():
    global LOW, MED, DEM_CELL, CHM_CELL, CLUSTER_GAP, MIN_AREA
    global SM_GAP, SM_OPEN, SM_SIMP, SM_QS, HIGH_CLASS, GROUND_CLASS
    ap=argparse.ArgumentParser(description="High-veg TREE CLUSTER outline builder")
    ap.add_argument("sites", nargs="*", help="site name filter (default: all in config)")
    ap.add_argument("--config", required=True, help="sites JSON (see sites.example.json)")
    ap.add_argument("--workroot", default=".", help="LOCAL dir for reclass + intermediates")
    ap.add_argument("--final", default=None, help="deliverable dir (default <workroot>/HIGHVEG_FINAL)")
    ap.add_argument("--low", default=LOW); ap.add_argument("--med", default=MED)
    ap.add_argument("--dem-cell", type=float, default=DEM_CELL)
    ap.add_argument("--chm-cell", type=float, default=CHM_CELL)
    ap.add_argument("--cluster-gap", type=float, default=CLUSTER_GAP)
    ap.add_argument("--min-area", type=float, default=MIN_AREA)
    ap.add_argument("--sm-gap", type=float, default=SM_GAP)
    ap.add_argument("--sm-open", type=float, default=SM_OPEN)
    ap.add_argument("--sm-simp", type=float, default=SM_SIMP)
    ap.add_argument("--sm-qs", type=int, default=SM_QS)
    ap.add_argument("--high-class", type=int, default=HIGH_CLASS)
    ap.add_argument("--ground-class", type=int, default=GROUND_CLASS)
    ap.add_argument("--keep-2d", action="store_true", help="also copy the 2D lines to --final")
    a=ap.parse_args()
    LOW, MED = str(a.low), str(a.med)
    DEM_CELL, CHM_CELL, CLUSTER_GAP, MIN_AREA = a.dem_cell, a.chm_cell, a.cluster_gap, a.min_area
    SM_GAP, SM_OPEN, SM_SIMP, SM_QS = a.sm_gap, a.sm_open, a.sm_simp, a.sm_qs
    HIGH_CLASS, GROUND_CLASS = a.high_class, a.ground_class

    SITES = load_sites(a.config)
    WORKROOT = a.workroot
    FINAL = a.final or os.path.join(WORKROOT, "HIGHVEG_FINAL")
    os.makedirs(FINAL, exist_ok=True)
    only = a.sites if a.sites else list(SITES)
    for site in only:
        if site not in SITES:
            print(f"!! {site} not in config, skipping",flush=True); continue
        epsg, srcs = SITES[site]
        t0=time.time()
        print(f"\n######## {site}  (EPSG {epsg}, {len(srcs)} tiles) ########",flush=True)
        wd=os.path.join(WORKROOT, site.lower()+"_hv"); reclass=os.path.join(wd,"reclass")
        os.makedirs(reclass,exist_ok=True)
        # 1. reclass per tile
        for i,src in enumerate(srcs,1):
            out=os.path.join(reclass,f"{i:02d}.laz")
            if os.path.exists(out):
                print(f"  [{i}/{len(srcs)}] {os.path.basename(src)} -> {i:02d}.laz (skip, exists)",flush=True); continue
            tt=time.time()
            r=subprocess.run([PY,RECLASS_PY,"--input",src,"--output",out,"--low",LOW,"--med",MED],
                             capture_output=True,text=True)
            if r.returncode!=0 or not os.path.exists(out):
                print(f"  [{i}/{len(srcs)}] RECLASS FAIL {os.path.basename(src)}: {r.stderr.strip()[-300:]}",flush=True)
                break
            msg=r.stdout.strip().splitlines()[-1] if r.stdout.strip() else "ok"
            print(f"  [{i}/{len(srcs)}] {os.path.basename(src)} -> {i:02d}.laz | {msg} ({int(time.time()-tt)}s)",flush=True)
        # 2. build clusters (delete the shp to force a rebuild)
        dst=os.path.join(wd,f"TREESBUSHESHEDGES_{site}.shp")
        if os.path.exists(dst):
            print(f"  build: {os.path.basename(dst)} exists, skip",flush=True)
        else:
            print("  building TREE CLUSTER lines...",flush=True)
            build_clusters(reclass,dst,epsg)
        # 3. drape Z
        dstz=os.path.join(wd,f"TREESBUSHESHEDGES_{site}_Z.shp")
        if os.path.exists(dst) and not os.path.exists(dstz):
            print("  draping Z...",flush=True)
            drape(reclass,dst,dstz)
        elif os.path.exists(dstz):
            print(f"  drape: {os.path.basename(dstz)} exists, skip",flush=True)
        # 4. copy finals -- only the 3D (_Z) high-veg lines unless --keep-2d
        if a.keep_2d and os.path.exists(dst): copy_final(dst,FINAL)
        if os.path.exists(dstz): copy_final(dstz,FINAL)
        print(f"  {site} DONE ({int(time.time()-t0)}s)",flush=True)
    print(f"\nALL DONE -> {FINAL}",flush=True)

if __name__=="__main__":
    main()
