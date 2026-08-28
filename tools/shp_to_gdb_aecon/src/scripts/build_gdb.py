#!/usr/bin/env python
"""
Build populated SDAI-template File Geodatabases from PAS/sub shapefiles.

Discovers the *.gdb templates in --templates, recreates each as an empty copy
in --out (exact schema / geometry type / CRS), then loads the matching PAS
shapefiles from --shp per the mapping below. Geometry is written as ZM with
M=0 (Esri FGDB rejects NaN measures). Sources are reprojected to the template
CRS if they differ.

Run with the gdal_env interpreter:
  & "$env:USERPROFILE\\.conda\\envs\\gdal_env\\python.exe" build_gdb.py \
      --templates "<dir with *.gdb templates>" \
      --shp       "<dir with PAS shapefiles>" \
      --out       "<output dir; default = --shp>"

Edit the mapping dicts below if a sub delivers different shapefile names.
"""
import os, argparse
from osgeo import ogr, osr
ogr.UseExceptions()

# ---- mappings (target feature class -> source shapefile) ----------------
# AERIAL: target -> (source shp | None, FEATURE_CODE value | None)
AERIAL = {
    "POLE_TOPS":              ("UTILITYPOLES_TOP",  "POLE TOP"),
    "POLE_BASES":             ("UTILITYPOLES_BASE", "POLE BASE"),
    "OTHER_POLES":            ("OTHERPOLES",        "OTHER POLE"),
    "CONDUCTOR_LINES":        (None, None),
    "CONDUCTOR_POINTS":       (None, None),
    "GUY_WIRE_LINES":         (None, None),
    "POLE_ATTACHMENT_POINTS": (None, None),
}
# TOPO plain copies: target -> list of (source shp, FEATURE_CODE value | None).
# Several sources may feed one class (each tagged with its own code).
TOPO_SIMPLE = {
    "BACK_OF_CURBS":       [("Back_of_Curb", None)],
    "BUILDINGS":           [("Final Building", None)],
    "DITCH_BOTTOMS":       [("Ditch_BOTTOM", None)],
    "DITCH_TOP_OF_SLOPE":  [("Ditch_TOP", None)],
    "DRIVEWAYS":           [("DRIVEWAY", None)],
    "DRIVEWAY_MATERIAL":   [("DRIVEWAY_MATERIALS_GRAVELS", "GRAVEL"),
                            ("DRIVEWAY_MATERIALS_BRICKS", "BRICK"),
                            ("DRIVEWAY_MATERIALS_CONCRETE", "CONCRETE")],
    "EDGE_OF_PAVEMENT":    [("EDGE_OF_PAVEMENT", None)],
    "GRAVEL_ROADEDGES":    [("EDGE_OF_GRAVELS", None)],
    "FENCES":              [("Fence", None)],
    "FIRE_HYDRANTS":       [("FIREHYDRANTS", None)],
    "FRONT_OF_CURBS":      [("Front_of_Curb", None)],
    "TRAFFIC_SIGN":        [("TRAFFICSIGN", None)],
    "TREES_BUSHES_HEDGES": [("TREE_BUSH_HEDGE", "TREES")],
    "METER":               [("HYDROMETER", "HYDRO METER")],
    "STREET_FURNITURE":    [("BPAD", "BPAD"), ("GLB", "GLB"), ("ONU", "ONU"),
                            ("GAS_LINE_MARKER", "GAS LINE MARKER"),
                            ("STREETLIGHTS", "STREET LIGHT"),
                            ("STREET_FEATURE_UNKNOWN", "UNKNOWN"),
                            ("TRAFFIC_CONTROL", "TRAFFIC CONTROL")],
}
# Point sources whose target class is polygon/line: buffer -> dissolve clusters.
# target -> (source shp, buffer radius m, kind: "polygon" | "line")
BUFFER_MAP = {
    "BOULDER":      ("BOULDER", 0.5, "polygon"),
    "CONCRETE_PAD": ("CONCRETE_PAD", 1.5, "line"),
}

SKIP_FIELDS = {"OBJECTID", "SHAPE", "SHAPE_LENGTH", "SHAPE_AREA", "SHAPE_LEN"}
drv = ogr.GetDriverByName("OpenFileGDB")
missing = set()          # source shapefiles referenced but not found
log = []                 # (gdb, class, geom, n)


def open_src(shp_dir, name):
    path = os.path.join(shp_dir, name + ".shp")
    if not os.path.exists(path):
        missing.add(name)
        return None, None
    ds = ogr.Open(path)
    return ds, ds.GetLayer()


def copy_fields(dst_lyr, tpl_defn):
    for i in range(tpl_defn.GetFieldCount()):
        fd = tpl_defn.GetFieldDefn(i)
        if fd.GetName().upper() in SKIP_FIELDS:
            continue
        dst_lyr.CreateField(fd)


def fc_field(defn):
    # AERIAL templates use FEATURE_CODE; TOPO uses the truncated FEATURE_CO.
    for nm in ("FEATURE_CODE", "FEATURE_CO"):
        if defn.GetFieldIndex(nm) >= 0:
            return nm
    return None


def zm_from(geom, target_type):
    """Rebuild geom as the target ZM type with explicit M=0."""
    tflat = ogr.GT_Flatten(target_type)
    if tflat == ogr.wkbPoint:
        p = ogr.Geometry(ogr.wkbPoint)
        p.AddPointZM(geom.GetX(), geom.GetY(), geom.GetZ(), 0)
        return p
    if tflat == ogr.wkbMultiLineString:
        mls = ogr.Geometry(ogr.wkbMultiLineString)
        lines = [geom] if geom.GetGeometryName() == "LINESTRING" else \
                [geom.GetGeometryRef(i) for i in range(geom.GetGeometryCount())]
        for ln in lines:
            nl = ogr.Geometry(ogr.wkbLineString)
            for i in range(ln.GetPointCount()):
                nl.AddPointZM(ln.GetX(i), ln.GetY(i), ln.GetZ(i), 0)
            mls.AddGeometry(nl)
        return mls
    if tflat == ogr.wkbMultiPolygon:
        mp = ogr.Geometry(ogr.wkbMultiPolygon)
        polys = [geom] if geom.GetGeometryName() == "POLYGON" else \
                [geom.GetGeometryRef(i) for i in range(geom.GetGeometryCount())]
        for pl in polys:
            npl = ogr.Geometry(ogr.wkbPolygon)
            for r in range(pl.GetGeometryCount()):
                ring = pl.GetGeometryRef(r)
                nr = ogr.Geometry(ogr.wkbLinearRing)
                for i in range(ring.GetPointCount()):
                    nr.AddPointZM(ring.GetX(i), ring.GetY(i), ring.GetZ(i), 0)
                npl.AddGeometry(nr)
            mp.AddGeometry(npl)
        return mp
    raise ValueError("unsupported target type " + str(target_type))


def add_feature(dst_lyr, geom, fc_value):
    defn = dst_lyr.GetLayerDefn()
    f = ogr.Feature(defn)
    if geom is not None:
        f.SetGeometry(geom)
    fld = fc_field(defn)
    if fc_value is not None and fld:
        f.SetField(fld, fc_value)
    dst_lyr.CreateFeature(f)


def maybe_transform(geom, src_srs, dst_srs):
    if src_srs and dst_srs and not src_srs.IsSame(dst_srs):
        g = geom.Clone()
        g.Transform(osr.CoordinateTransformation(src_srs, dst_srs))
        return g
    return geom


def polygon_to_zm_multi(poly2d, z):
    mp = ogr.Geometry(ogr.wkbMultiPolygon)
    parts = [poly2d] if poly2d.GetGeometryName() == "POLYGON" else \
            [poly2d.GetGeometryRef(k) for k in range(poly2d.GetGeometryCount())]
    for part in parts:
        np_ = ogr.Geometry(ogr.wkbPolygon)
        for r in range(part.GetGeometryCount()):
            ring = part.GetGeometryRef(r)
            nr = ogr.Geometry(ogr.wkbLinearRing)
            for i in range(ring.GetPointCount()):
                nr.AddPointZM(ring.GetX(i), ring.GetY(i), z, 0)
            np_.AddGeometry(nr)
        mp.AddGeometry(np_)
    return mp


def load_simple(dlyr, gtype, dst_srs, shp_dir, sources):
    n = 0
    for src, fc in sources:
        sds, slyr = open_src(shp_dir, src)
        if slyr is None:
            continue
        ssrs = slyr.GetSpatialRef()
        for sf in slyr:
            g = maybe_transform(sf.GetGeometryRef(), ssrs, dst_srs)
            add_feature(dlyr, zm_from(g, gtype), fc)
            n += 1
        sds = None
    return n


def load_buffered(dlyr, shp_dir, src, R, kind):
    sds, slyr = open_src(shp_dir, src)
    if slyr is None:
        return 0
    pts = [(f.GetGeometryRef().GetX(), f.GetGeometryRef().GetY(),
            f.GetGeometryRef().GetZ()) for f in slyr]
    sds = None
    if not pts:
        return 0
    multi = ogr.Geometry(ogr.wkbMultiPolygon)
    for x, y, z in pts:
        p = ogr.Geometry(ogr.wkbPoint); p.AddPoint(x, y)
        multi.AddGeometry(p.Buffer(R, 30))
    dissolved = multi.UnionCascaded()
    clusters = [dissolved] if dissolved.GetGeometryName() == "POLYGON" else \
               [dissolved.GetGeometryRef(k) for k in range(dissolved.GetGeometryCount())]
    n = 0
    for cl in clusters:
        zin = [z for x, y, z in pts
               if cl.Contains(ogr.CreateGeometryFromWkt(f"POINT ({x} {y})"))]
        zc = sum(zin) / len(zin) if zin else 0.0
        if kind == "polygon":
            add_feature(dlyr, polygon_to_zm_multi(cl, zc), None)
        else:  # line: polygon boundary as (multi)linestring
            ring = cl.GetGeometryRef(0)
            mls = ogr.Geometry(ogr.wkbMultiLineString)
            ls = ogr.Geometry(ogr.wkbLineString)
            for i in range(ring.GetPointCount()):
                ls.AddPointZM(ring.GetX(i), ring.GetY(i), zc, 0)
            mls.AddGeometry(ls)
            add_feature(dlyr, mls, None)
        n += 1
    return n


def build(tpl_path, out_path, shp_dir):
    is_aerial = "AERIAL" in os.path.basename(tpl_path).upper()
    tpl_ds = ogr.Open(tpl_path)
    if os.path.exists(out_path):
        try:
            drv.DeleteDataSource(out_path)
        except RuntimeError:
            raise SystemExit(
                f"ERROR: cannot overwrite {out_path} — it is locked. "
                "Close it in ArcGIS/ArcCatalog/ArcGIS Pro and re-run.")
    out_ds = drv.CreateDataSource(out_path)
    gdb = os.path.basename(out_path)

    for li in range(tpl_ds.GetLayerCount()):
        tlyr = tpl_ds.GetLayerByIndex(li)
        name = tlyr.GetName()
        gtype = tlyr.GetGeomType()
        dst_srs = tlyr.GetSpatialRef()
        dlyr = out_ds.CreateLayer(name, dst_srs, gtype)
        copy_fields(dlyr, tlyr.GetLayerDefn())

        n = 0
        if is_aerial:
            src, fc = AERIAL.get(name, (None, None))
            if src:
                n = load_simple(dlyr, gtype, dst_srs, shp_dir, [(src, fc)])
        else:
            if name in TOPO_SIMPLE:
                n = load_simple(dlyr, gtype, dst_srs, shp_dir, TOPO_SIMPLE[name])
            elif name in BUFFER_MAP:
                src, R, kind = BUFFER_MAP[name]
                n = load_buffered(dlyr, shp_dir, src, R, kind)
        log.append((gdb, name, ogr.GeometryTypeToName(gtype), n))
    out_ds = None
    tpl_ds = None


def main():
    ap = argparse.ArgumentParser(description="Build SDAI-template GDBs from PAS shapefiles.")
    here = os.path.dirname(os.path.abspath(__file__))
    cands = [os.path.join(here, "templates"), os.path.join(here, "..", "templates")]
    bundled = next((c for c in cands if os.path.isdir(c)), cands[0])
    ap.add_argument("--templates", default=bundled,
                    help="dir containing the *.gdb templates (default: bundled templates/)")
    ap.add_argument("--shp", required=True, help="dir containing the PAS shapefiles")
    ap.add_argument("--out", default=None, help="output dir (default = --shp)")
    a = ap.parse_args()
    out_dir = a.out or a.shp

    gdbs = [os.path.join(a.templates, d) for d in os.listdir(a.templates)
            if d.lower().endswith(".gdb")]
    if not gdbs:
        raise SystemExit("No *.gdb templates found in " + a.templates)

    for tpl in sorted(gdbs):
        build(tpl, os.path.join(out_dir, os.path.basename(tpl)), a.shp)

    print(f"{'GDB':30s} {'CLASS':24s} {'GEOM':26s} {'N':>6s}")
    for g, c, gt, n in log:
        print(f"{g[:28]:30s} {c:24s} {gt:26s} {n:6d}{'  <-- filled' if n else ''}")
    print("TOTAL features written:", sum(x[3] for x in log))
    if missing:
        print("\nWARNING: mapped source shapefiles not found (their classes left empty):")
        for m in sorted(missing):
            print("   -", m + ".shp")


if __name__ == "__main__":
    main()
