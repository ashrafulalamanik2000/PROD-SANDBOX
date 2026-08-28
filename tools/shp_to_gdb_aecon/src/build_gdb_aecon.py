#!/usr/bin/env python
"""
Build populated SDAI-template File Geodatabases from AECON (Burlington)
deliverable shapefiles. Adapted from the shp-to-gdb skill's build_gdb.py for
the AECON naming convention (FSA_* tiles).

Differences vs the PAS builder:
  - Source shapefile names are resolved case-insensitively with trailing
    whitespace stripped (AECON files like "POLE_TOPS .shp").
  - Output layers are written in the SOURCE CRS (default EPSG:26917, NAD83
    UTM 17N — Burlington/Ontario) instead of the template's 26914 (Texas).
  - BOULDER / CONCRETE_PAD sources are already polygons/lines: copied
    directly (CONCRETE_PAD polygons become boundary lines), no point-buffering.
  - OTHER_POLES / STREET_FURNITURE keep their per-feature FEATURE_CO codes.
  - TREES points populate DIAMETER (dbh_1p3m) and HEIGHT (canopy_top).
  - TREE_BUSH_HEDGE (2D lines) get per-vertex Z interpolated (IDW, k=3) from
    TREES.shp ground points; TRESS_BUSHES_HEDGES (3D) load as-is.
  - STREET_SIGNS and SIGN_POST both feed TRAFFIC_SIGN (same feature type).

Run with the gdal_env interpreter, once per tile folder:
  & "$env:USERPROFILE\\.conda\\envs\\gdal_env\\python.exe" build_gdb_aecon.py \
      --shp "<local work dir>\\FSA_0006B"
"""
import os, argparse
from osgeo import ogr, osr
ogr.UseExceptions()

SKIP_FIELDS = {"OBJECTID", "SHAPE", "SHAPE_LENGTH", "SHAPE_AREA", "SHAPE_LEN"}
drv = ogr.GetDriverByName("OpenFileGDB")
log = []                 # (gdb, class, geom, n, sources-used)
missing = []             # (target, source) referenced but not found


def S(src, fc=None, fields=None, zfield=None, ztrees=False, clipb=False,
      alt=False):
    return {"src": src, "fc": fc, "fields": fields, "zfield": zfield,
            "ztrees": ztrees, "clipb": clipb, "alt": alt}


MIN_CLIP_PART_AREA = 0.25  # m2 — slivers below this are dropped after clipping


def rings_to_poly(geom):
    """closed 2D/3D linestring(s) -> one valid 2D polygon (union of rings)."""
    out = None
    parts = [geom] if geom.GetGeometryName() == "LINESTRING" else \
            [geom.GetGeometryRef(i) for i in range(geom.GetGeometryCount())]
    for ln in parts:
        pts = [(ln.GetX(i), ln.GetY(i)) for i in range(ln.GetPointCount())]
        if len(pts) < 3:
            continue
        if pts[0] != pts[-1]:
            pts.append(pts[0])
        ring = ogr.Geometry(ogr.wkbLinearRing)
        for x, y in pts:
            ring.AddPoint_2D(x, y)
        p = ogr.Geometry(ogr.wkbPolygon)
        p.AddGeometry(ring)
        if not p.IsValid():
            p = p.Buffer(0)
        if p.IsEmpty():
            continue
        out = p if out is None else out.Union(p)
    return out


def building_union(srcmap):
    """union of BUILDINGS footprints (2D), or None if no BUILDINGS source."""
    path = srcmap.get("BUILDINGS")
    if not path:
        return None
    ds = ogr.Open(path)
    u = None
    for f in ds.GetLayer():
        g = f.GetGeometryRef()
        if g is None:
            continue
        p = rings_to_poly(g)
        if p is not None:
            u = p if u is None else u.Union(p)
    ds = None
    return u


def clip_out_buildings(geom, bunion):
    """Subtract building footprints from a closed-outline feature.
    Returns (new 2D MultiLineString | None-if-eliminated, was_clipped)."""
    poly = rings_to_poly(geom)
    if poly is None or bunion is None or not poly.Intersects(bunion):
        return geom, False
    diff = poly.Difference(bunion)
    parts = [diff] if diff.GetGeometryName() == "POLYGON" else \
            [diff.GetGeometryRef(i) for i in range(diff.GetGeometryCount())]
    mls = ogr.Geometry(ogr.wkbMultiLineString)
    kept = 0
    for p in parts:
        if p.GetGeometryName() != "POLYGON" or p.GetArea() < MIN_CLIP_PART_AREA:
            continue
        for r in range(p.GetGeometryCount()):
            ring = p.GetGeometryRef(r)
            ls = ogr.Geometry(ogr.wkbLineString)
            for i in range(ring.GetPointCount()):
                ls.AddPoint_2D(ring.GetX(i), ring.GetY(i))
            mls.AddGeometry(ls)
        kept += 1
    return (mls if kept else None), True


def nearest_vertex_zfunc(geom):
    """Z provider for re-shaped outlines: nearest original vertex's Z."""
    verts = []
    def collect(g):
        if g.GetGeometryName() in ("LINESTRING", "LINEARRING"):
            verts.extend((g.GetX(i), g.GetY(i), g.GetZ(i))
                         for i in range(g.GetPointCount()))
        else:
            for i in range(g.GetGeometryCount()):
                collect(g.GetGeometryRef(i))
    collect(geom)
    def zf(x, y, _z):
        return min(verts, key=lambda v: (x - v[0]) ** 2 + (y - v[1]) ** 2)[2]
    return zf


def tree_z_interpolator(shp_dir):
    """IDW (k=3) elevation lookup built from TREES.shp ground points.
    Trees with Z<=0 (bad fits) are excluded. Returns None if unavailable."""
    path = source_map(shp_dir).get("TREES")
    if not path:
        return None
    ds = ogr.Open(path)
    pts = [(g.GetX(), g.GetY(), g.GetZ())
           for f in ds.GetLayer()
           if (g := f.GetGeometryRef()) is not None and g.GetZ() > 0]
    ds = None
    if not pts:
        return None
    xs = [p[0] for p in pts]; ys = [p[1] for p in pts]; zs = [p[2] for p in pts]

    def interp(x, y, _z):
        d2 = sorted(((x - px) ** 2 + (y - py) ** 2, pz)
                    for px, py, pz in zip(xs, ys, zs))[:3]
        if d2[0][0] < 1e-6:
            return d2[0][1]
        wsum = zsum = 0.0
        for dd, pz in d2:
            w = 1.0 / dd
            wsum += w; zsum += w * pz
        return zsum / wsum
    interp.n_points = len(pts)
    return interp


def fco(f):
    try:
        return f.GetField("FEATURE_CO")
    except Exception:
        return None


def other_pole_fc(f):
    # codes per the SDAI feature-code spec (Loop doc, July 2026)
    v = (fco(f) or "").strip().upper()
    return {"OTHER_POLES": "OTHER POLE",
            "STREET_LIGHTS": "STREETLIGHT BASE NON ATTACHMENT",
            "TRAFFIC_LIGHTS": "TRAFFICLIGHT BASE NON ATTACHMENT"}.get(v, "OTHER POLE")


def furniture_fc(f):
    # translate AECON source codes to the SDAI feature-code spec
    v = (fco(f) or "").strip().upper()
    return {"ONU": "ONU/POWER SUPPLY",
            "OPI_CABINET": "OPI",
            "TRAFFIC_CONTROL_BOX": "TRAFFIC CONTROL BOX"}.get(v, v or None)


# ---- mappings (target feature class -> source shapefile specs) ------------
AERIAL = {
    "POLE_TOPS":   [S("POLE_TOPS", fc="POLE TOP")],
    "POLE_BASES":  [S("POLE_BASES", fc="POLE BASE")],
    "OTHER_POLES": [S("OTHER_POLES", fc=other_pole_fc)],
}
TOPO = {
    "BACK_OF_CURBS":       [S("BACK_OF_CURBS")],
    "BOLLARD":             [S("BOLLARDS")],
    "BOULDER":             [S("BOULDER", alt=True), S("BOULDERS", alt=True)],
    "BUILDINGS":           [S("BUILDINGS")],
    "CATCHBASINS":         [S("CATCHBASINS")],
    "CONCRETE_PAD":        [S("CONCRETE_PAD")],
    "DRIVEWAYS":           [S("DRIVEWAYS")],
    "EDGE_OF_PAVEMENT":    [S("EDGE_OF_PAVEMENTS")],
    "FENCES":              [S("FENCES")],
    "FIRE_HYDRANTS":       [S("FIRE_HYDRANTS")],
    "FRONT_OF_CURBS":      [S("FRONT_OF_CURBS")],
    "GRAVEL_ROADEDGES":    [S("GRAVEL_ROAD")],
    "MANHOLES":            [S("MANHOLES")],
    "METER":               [S("HYDROMETERS", fc="HYDRO METER")],
    "PADMOUNTED_TX":       [S("PADMOUNTED_TX")],
    "POSTBOX":             [S("MAILBOXES")],
    "SIDEWALK":            [S("SIDEWALKS")],
    "STREET_FURNITURE":    [S("STREET_FURNITURE", fc=furniture_fc),
                            S("GAS_LINE_MARKER_POST",
                              fc="GAS LINE MARKER/POST")],
    # per Ash: SIGN_POST and STREET_SIGNS are the same feature type
    "TRAFFIC_SIGN":        [S("STREET_SIGNS"), S("SIGN_POST")],
    "TREES":               [S("TREES", fields={
                                "DIAMETER": lambda f: f.GetField("dbh_1p3m"),
                                "HEIGHT":   lambda f: f.GetField("canopy_top")})],
    # NB: FSA_0006B TREE_BUSH_HEDGE is 2D and its Z_Min/Z_Max are one
    # tile-wide constant, so 2D outlines get per-vertex Z interpolated from
    # nearby TREES ground points (ztrees); Z_Min stays as a last-resort
    # fallback if TREES.shp is absent. HEIGHT is left null either way.
    # sources don't distinguish cluster/hedge/bush -> default TREE CLUSTER.
    # clipb: building footprints are subtracted from the outlines (per Ash,
    # 2026-07-09) — rings re-close along walls, slivers <0.25 m2 dropped,
    # outlines fully inside a building removed, Z re-assigned per vertex.
    "TREES_BUSHES_HEDGES": [S("TREE_BUSH_HEDGE", fc="TREE CLUSTER",
                              ztrees=True, zfield="Z_Min", clipb=True,
                              alt=True),
                            S("TRESS_BUSHES_HEDGES", fc="TREE CLUSTER",
                              zfield="BASE_Z", clipb=True, alt=True)],
    "WALKWAYS":            [S("WALKWAYS")],
}


def source_map(shp_dir):
    """normalized (stripped, upper) basename -> path, for *.shp in shp_dir"""
    m = {}
    for fn in os.listdir(shp_dir):
        if fn.lower().endswith(".shp"):
            m[os.path.splitext(fn)[0].strip().upper()] = os.path.join(shp_dir, fn)
    return m


def copy_fields(dst_lyr, tpl_defn):
    for i in range(tpl_defn.GetFieldCount()):
        fd = tpl_defn.GetFieldDefn(i)
        if fd.GetName().upper() in SKIP_FIELDS:
            continue
        dst_lyr.CreateField(fd)


def fc_field(defn):
    for nm in ("FEATURE_CODE", "FEATURE_CO"):
        if defn.GetFieldIndex(nm) >= 0:
            return nm
    return None


def iter_lines(g):
    nm = g.GetGeometryName()
    if nm in ("LINESTRING", "LINEARRING"):
        yield g
    elif nm == "POLYGON":
        for r in range(g.GetGeometryCount()):
            yield g.GetGeometryRef(r)
    else:
        for i in range(g.GetGeometryCount()):
            yield from iter_lines(g.GetGeometryRef(i))


def iter_polys(g):
    if g.GetGeometryName() == "POLYGON":
        yield g
    else:
        for i in range(g.GetGeometryCount()):
            yield from iter_polys(g.GetGeometryRef(i))


def zm_from(geom, target_type, zfunc=None):
    """Rebuild geom as the target ZM type with explicit M=0. zfunc(x, y, z)
    supplies Z per vertex for 2D sources; None keeps the geometry Z."""
    tflat = ogr.GT_Flatten(target_type)
    if zfunc is None:
        zfunc = lambda x, y, z: z

    if tflat == ogr.wkbPoint:
        p = ogr.Geometry(ogr.wkbPoint)
        p.AddPointZM(geom.GetX(), geom.GetY(),
                     zfunc(geom.GetX(), geom.GetY(), geom.GetZ()), 0)
        return p
    if tflat == ogr.wkbMultiLineString:
        mls = ogr.Geometry(ogr.wkbMultiLineString)
        for ln in iter_lines(geom):
            nl = ogr.Geometry(ogr.wkbLineString)
            for i in range(ln.GetPointCount()):
                x, y = ln.GetX(i), ln.GetY(i)
                nl.AddPointZM(x, y, zfunc(x, y, ln.GetZ(i)), 0)
            mls.AddGeometry(nl)
        return mls
    if tflat == ogr.wkbMultiPolygon:
        mp = ogr.Geometry(ogr.wkbMultiPolygon)
        for pl in iter_polys(geom):
            npl = ogr.Geometry(ogr.wkbPolygon)
            for r in range(pl.GetGeometryCount()):
                ring = pl.GetGeometryRef(r)
                nr = ogr.Geometry(ogr.wkbLinearRing)
                for i in range(ring.GetPointCount()):
                    x, y = ring.GetX(i), ring.GetY(i)
                    nr.AddPointZM(x, y, zfunc(x, y, ring.GetZ(i)), 0)
                npl.AddGeometry(nr)
            mp.AddGeometry(npl)
        return mp
    raise ValueError("unsupported target type " + str(target_type))


def maybe_transform(geom, src_srs, dst_srs):
    if src_srs and dst_srs and not src_srs.IsSame(dst_srs):
        g = geom.Clone()
        g.Transform(osr.CoordinateTransformation(src_srs, dst_srs))
        return g
    return geom


def load_target(dlyr, gtype, dst_srs, srcmap, specs, target, shp_dir):
    defn = dlyr.GetLayerDefn()
    fcfld = fc_field(defn)
    n = 0
    used = []
    tried_missing = []
    for spec in specs:
        path = srcmap.get(spec["src"].upper())
        if not path:
            tried_missing.append(spec)
            continue
        ds = ogr.Open(path)
        slyr = ds.GetLayer()
        ssrs = slyr.GetSpatialRef()
        sdefn = slyr.GetLayerDefn()
        zfield = spec["zfield"] if spec["zfield"] and \
            sdefn.GetFieldIndex(spec["zfield"]) >= 0 else None
        interp = tree_z_interpolator(shp_dir) if spec["ztrees"] else None
        if interp:
            print(f"  [{target}] {spec['src']}: 2D features get Z via IDW of "
                  f"{interp.n_points} TREES ground points")
        bunion = building_union(srcmap) if spec["clipb"] else None
        nclip = ngone = 0
        for sf in slyr:
            g = sf.GetGeometryRef()
            if g is None:
                continue
            src3d = g.Is3D()
            zfunc = None
            if not src3d:
                if interp:
                    zfunc = lambda x, y, z: interp(x, y, z)
                elif zfield:
                    zdef = sf.GetField(zfield)
                    zfunc = lambda x, y, z, zd=zdef: zd
            if bunion is not None:
                clipped, was = clip_out_buildings(g, bunion)
                if was:
                    nclip += 1
                    if clipped is None:
                        ngone += 1
                        continue
                    if src3d:
                        zfunc = nearest_vertex_zfunc(g)
                    g = clipped
            g = maybe_transform(g, ssrs, dst_srs)
            f = ogr.Feature(defn)
            f.SetGeometry(zm_from(g, gtype, zfunc))
            fcv = spec["fc"](sf) if callable(spec["fc"]) else spec["fc"]
            if fcv is not None and fcfld:
                f.SetField(fcfld, fcv)
            for dst, getter in (spec["fields"] or {}).items():
                if defn.GetFieldIndex(dst) < 0:
                    continue
                try:
                    v = getter(sf)
                except Exception:
                    v = None
                if v is not None:
                    f.SetField(dst, v)
            dlyr.CreateFeature(f)
            n += 1
        if nclip:
            print(f"  [{target}] {spec['src']}: {nclip} outlines clipped to "
                  f"building footprints ({ngone} entirely inside, removed)")
        used.append(os.path.basename(path))
        ds = None
    for spec in tried_missing:
        # alt sources (name variants) only count as missing if nothing loaded
        if not (spec["alt"] and n > 0):
            missing.append((target, spec["src"]))
    return n, used


def build(tpl_path, out_path, shp_dir, dst_srs):
    is_aerial = "AERIAL" in os.path.basename(tpl_path).upper()
    mapping = AERIAL if is_aerial else TOPO
    srcmap = source_map(shp_dir)
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
        dlyr = out_ds.CreateLayer(name, dst_srs, gtype)
        copy_fields(dlyr, tlyr.GetLayerDefn())
        n, used = (0, [])
        if name in mapping:
            n, used = load_target(dlyr, gtype, dst_srs, srcmap,
                                  mapping[name], name, shp_dir)
        log.append((gdb, name, ogr.GeometryTypeToName(gtype), n, used))
    out_ds = None
    tpl_ds = None


def main():
    ap = argparse.ArgumentParser(
        description="Build SDAI-template GDBs from AECON shapefiles.")
    here = os.path.dirname(os.path.abspath(__file__))
    ap.add_argument("--templates", default=os.path.join(here, "templates"))
    ap.add_argument("--shp", required=True, help="tile folder with shapefiles")
    ap.add_argument("--out", default=None, help="output dir (default = --shp)")
    ap.add_argument("--epsg", type=int, default=26917,
                    help="output CRS (default 26917, NAD83 / UTM 17N)")
    a = ap.parse_args()
    out_dir = a.out or a.shp

    dst_srs = osr.SpatialReference()
    dst_srs.ImportFromEPSG(a.epsg)
    dst_srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)

    gdbs = [os.path.join(a.templates, d) for d in os.listdir(a.templates)
            if d.lower().endswith(".gdb")]
    if not gdbs:
        raise SystemExit("No *.gdb templates found in " + a.templates)

    for tpl in sorted(gdbs):
        build(tpl, os.path.join(out_dir, os.path.basename(tpl)), a.shp, dst_srs)

    print(f"{'GDB':30s} {'CLASS':24s} {'GEOM':26s} {'N':>6s}  SOURCES")
    for g, c, gt, n, used in log:
        print(f"{g[:28]:30s} {c:24s} {gt:26s} {n:6d}  {', '.join(used)}")
    print("TOTAL features written:", sum(x[3] for x in log))
    if missing:
        print("\nWARNING: mapped source shapefiles not found (classes left empty/partial):")
        for tgt, src in missing:
            print(f"   - {src}.shp  (target {tgt})")


if __name__ == "__main__":
    main()
