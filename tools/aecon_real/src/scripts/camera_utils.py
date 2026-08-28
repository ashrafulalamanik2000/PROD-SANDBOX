"""Camera buffer + PDAL clip utilities (unchanged from camera_clip_utils.py)."""
import os
import json
import pdal
import geopandas as gpd
from shapely.geometry import LineString


def create_camera_buffer(camera_points_shp_path, output_folder, buffer_distance_m=40):
    """Create a buffer polygon around a line through camera points."""
    geo_df = gpd.read_file(camera_points_shp_path)
    file_name = os.path.splitext(os.path.basename(camera_points_shp_path))[0]

    if len(geo_df) < 2:
        raise ValueError(f"Need >=2 points to create line, got {len(geo_df)}")

    line = LineString(geo_df.geometry.tolist())
    buffered = line.buffer(buffer_distance_m)

    buffer_gdf = gpd.GeoDataFrame([{
        "geometry": buffered,
        "ORIG_LEN": line.length,
        "B_AREA": buffered.area,
        "SRC_FILE": file_name,
        "B_DIST": buffer_distance_m
    }], geometry="geometry", crs=geo_df.crs)

    buffer_path = os.path.join(output_folder, f"{file_name}_line_{buffer_distance_m}m_buffer.shp")
    buffer_gdf.to_file(buffer_path)
    return buffer_path


def clip_las_with_buffer(input_las_path, buffer_shp_path, output_las_path):
    """PDAL filters.crop — unchanged."""
    gdf = gpd.read_file(buffer_shp_path)
    polygon_wkt = gdf.geometry.iloc[0].wkt

    pipeline_json = {
        "pipeline": [
            input_las_path,
            {"type": "filters.crop", "polygon": polygon_wkt},
            {"type": "writers.las", "filename": output_las_path, "extra_dims": "all"}
        ]
    }
    pipeline = pdal.Pipeline(json.dumps(pipeline_json))
    pipeline.execute()
    return output_las_path
