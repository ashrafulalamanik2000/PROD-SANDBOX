"""Reproject a point shapefile to a target CRS using geopandas/pyproj.

Native Python only; no Docker required. Used by stage 0 when stage 2 has
a BYO pole shapefile that needs to match the reprojected LAS CRS.

Usage:
    python reproject_shapefile.py \\
        --input-shp /path/to/poles.shp \\
        --output-shp /path/to/poles_<crs>.shp \\
        --target-crs EPSG:26911
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--input-shp", required=True, type=Path)
    p.add_argument("--output-shp", required=True, type=Path)
    p.add_argument("--target-crs", required=True,
                   help="Output CRS, e.g. EPSG:26911")
    args = p.parse_args()

    import geopandas as gpd

    if not args.input_shp.is_file():
        raise SystemExit(f"Input shapefile not found: {args.input_shp}")
    args.output_shp.parent.mkdir(parents=True, exist_ok=True)

    gdf = gpd.read_file(args.input_shp)
    print(f"Read {len(gdf):,} features from {args.input_shp.name}, "
          f"CRS={gdf.crs}")

    if gdf.crs is None:
        raise SystemExit(
            f"Input shapefile has no CRS. Set one via .to_crs() externally "
            f"or fix the .prj file.")

    out = gdf.to_crs(args.target_crs)
    # Also update TOP_X / TOP_Y columns if present (the reproj of geometry
    # doesn't touch numeric attribute columns).
    if "TOP_X" in out.columns and "TOP_Y" in out.columns:
        out = out.copy()
        out["TOP_X"] = out.geometry.x
        out["TOP_Y"] = out.geometry.y
    out.to_file(args.output_shp)
    print(f"Wrote {len(out):,} features to {args.output_shp.name} "
          f"in {args.target_crs}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
