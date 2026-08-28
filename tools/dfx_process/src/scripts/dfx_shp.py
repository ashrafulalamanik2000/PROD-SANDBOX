"""
dfx_shp.py — port of Solve3D_HP_v5_effigis_asdef.py
Creates georeferenced camera point shapefiles with viewer hyperlinks.
Uses geopandas + pyproj only (no osgeo dependency).
"""
import os

import geopandas as gpd
import pandas as pd
from pyproj import Transformer
from shapely.geometry import Point


def create_camera_points(csv_path, pn, epsg, addhp=True, platform='viewer'):
    """Write a camera points shapefile from a CSV. Returns output path."""
    workspace, filename = os.path.split(csv_path)
    suffix = "_CameraPoints2.shp" if addhp else "_CameraPoints.shp"
    outshp = os.path.join(workspace, filename.replace(".csv", suffix))

    df = pd.read_csv(csv_path)
    try:
        x_col, y_col, z_col = df['X'], df['Y'], df['Z']
    except KeyError:
        x_col, y_col, z_col = df['Easting'], df['Northing'], df['Elevation']

    geom = [Point(x, y, z) for x, y, z in zip(x_col, y_col, z_col)]
    gdf = gpd.GeoDataFrame(df, geometry=geom)
    gdf = gdf.set_crs(epsg=epsg)

    if addhp:
        transformer = Transformer.from_crs(f"EPSG:{epsg}", "EPSG:4326", always_xy=True)
        lons, lats = transformer.transform(x_col.values, y_col.values)

        gdf['Lat']  = lats
        gdf['Long'] = lons

        if platform == 'viewer':
            gdf['Hyperlink'] = [
                (f"https://viewer.spatialdata.ai/#hash={pn}&minimap=1&fov=90"
                 f"&x={x}&y={y}&z={z}&ae=0&az=17&lon={lon}&lat={lat}")
                for x, y, z, lon, lat in zip(x_col, y_col, z_col, lons, lats)
            ]
        else:
            gdf['Hyperlink'] = [
                f"https://cloud.spatialdata.ai/viewer/{pn}?lng=en#&x={x}&y={y}&z={z}"
                for x, y, z in zip(x_col, y_col, z_col)
            ]

        gdf['GMap'] = [
            f"https://www.google.com/maps/search/?api=1&query={lon}%2C{lat}"
            for lon, lat in zip(lons, lats)
        ]
        gdf['GStreet'] = [
            f"https://www.google.com/maps/@?api=1&map_action=pano"
            f"&viewpoint={lon}%2C{lat}&heading=17&pitch=0&fov=90"
            for lon, lat in zip(lons, lats)
        ]

    gdf.to_file(outshp)
    return outshp
