"""Cluster + smooth the merged pas_m11_0521 veg outlines.

Reads the merged simplified outlines, rebuilds solid footprints, then:
  1. unary_union of all footprints,
  2. morphological CLOSE (buffer +GAP / -GAP, round joins) -> bridges veg
     pieces within ~2*GAP of each other into one cluster + rounds convex corners,
  3. morphological OPEN (buffer -OPEN / +OPEN, round joins) -> rounds concave
     corners and drops thin necks,
  4. Douglas-Peucker simplify -> collapses residual raster staircase.

Outputs clustered-and-smoothed outlines (.shp + .gpkg) and the cluster polygons.
All distances in metres (EPSG 26914). Tunables at top."""
import glob, os
import geopandas as gpd
from shapely.geometry import Polygon, MultiPolygon, LineString, MultiLineString
from shapely.ops import unary_union

OUTROOT = "/data/out"
SRC = os.path.join(OUTROOT, "pas_m11_0521_veg_outline_simplified.shp")
DST_LINE = os.path.join(OUTROOT, "pas_m11_0521_veg_outline_clustered_smoothed.shp")
DST_POLY = os.path.join(OUTROOT, "pas_m11_0521_veg_cluster_poly.gpkg")
EPSG = 26914

GAP = 2.0        # close radius (m): bridges veg pieces within ~2*GAP (~4 m) into one cluster
OPEN = 0.75      # open radius (m): rounds concavities, removes necks thinner than ~2*OPEN
SIMP = 0.5       # Douglas-Peucker tolerance (m)
MIN_AREA = 5.0   # drop clusters smaller than this (m^2)
QS = 8           # buffer quadrant segments (arc smoothness)

def to_polys(geom):
    """Rebuild a solid footprint from a (Multi)LineString exterior ring."""
    out = []
    parts = geom.geoms if geom.geom_type.startswith("Multi") else [geom]
    for p in parts:
        try:
            poly = Polygon(p.coords)
            if not poly.is_valid:
                poly = poly.buffer(0)
            if not poly.is_empty and poly.area > 0:
                out.append(poly)
        except Exception:
            pass
    return out

def exterior_lines(poly):
    if poly.geom_type == "Polygon":
        return LineString(poly.exterior.coords)
    return MultiLineString([g.exterior.coords for g in poly.geoms])

def main():
    src = gpd.read_file(SRC)
    print(f"read {len(src)} outline features", flush=True)
    polys = []
    for g in src.geometry.values:
        if g is not None:
            polys.extend(to_polys(g))
    print(f"rebuilt {len(polys)} solid footprints; unioning...", flush=True)
    u = unary_union(polys)

    closed = u.buffer(GAP, join_style=1, quad_segs=QS).buffer(-GAP, join_style=1, quad_segs=QS)
    opened = closed.buffer(-OPEN, join_style=1, quad_segs=QS).buffer(OPEN, join_style=1, quad_segs=QS)
    smooth = opened.simplify(SIMP)

    clusters = list(smooth.geoms) if smooth.geom_type == "MultiPolygon" else [smooth]
    clusters = [c for c in clusters if c.area >= MIN_AREA]
    print(f"{len(clusters)} clusters after MIN_AREA={MIN_AREA}", flush=True)

    rows = [{"CLUSTER_ID": i + 1, "AREA_M2": round(c.area, 2),
             "PERIM_M": round(c.length, 2), "geometry": c}
            for i, c in enumerate(clusters)]
    poly_gdf = gpd.GeoDataFrame(rows, crs=f"EPSG:{EPSG}")
    line_gdf = poly_gdf.copy()
    line_gdf["geometry"] = poly_gdf.geometry.apply(exterior_lines)

    line_gdf.to_file(DST_LINE)
    line_gdf.to_file(DST_LINE.replace(".shp", ".gpkg"), driver="GPKG")
    poly_gdf.to_file(DST_POLY, driver="GPKG")

    a = poly_gdf.AREA_M2
    print(f"\nDONE: {len(line_gdf)} clustered+smoothed outlines -> {DST_LINE}", flush=True)
    print(f"AREA_M2: min {a.min():.1f} | median {a.median():.1f} | max {a.max():.1f} | total {a.sum():.0f}", flush=True)

if __name__ == "__main__":
    main()
