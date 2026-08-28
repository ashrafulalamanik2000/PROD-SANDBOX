"""LAS colorization — logic UNCHANGED from BatchLASColorFromScratch_v8.py."""
import os
import numpy as np
import pandas as pd
import geopandas as gpd
import laspy
import cv2
from scipy.spatial import cKDTree
from concurrent.futures import ThreadPoolExecutor


def rotation_matrix(yaw, pitch, roll):
    yaw = np.deg2rad(yaw)
    pitch = np.deg2rad(pitch)
    roll = np.deg2rad(roll)
    Rz = np.array([[np.cos(yaw), -np.sin(yaw), 0], [np.sin(yaw), np.cos(yaw), 0], [0, 0, 1]])
    Ry = np.array([[np.cos(pitch), 0, np.sin(pitch)], [0, 1, 0], [-np.sin(pitch), 0, np.cos(pitch)]])
    Rx = np.array([[1, 0, 0], [0, np.cos(roll), -np.sin(roll)], [0, np.sin(roll), np.cos(roll)]])
    return Rz @ Ry @ Rx


def colorize_one_las(las_path, eop_csv, panos_shp, lasindex_shp, images_folder,
                     output_las_path, search_radius=40, threads=8):
    """Colorize a single LAS file using nearby panoramic images. Logic unchanged from v8."""
    las = laspy.read(las_path)
    points = np.vstack((las.x, las.y, las.z)).T
    tree = cKDTree(points)

    eops_df = pd.read_csv(eop_csv)
    panos_gdf = gpd.read_file(panos_shp)
    lasindex_gdf = gpd.read_file(lasindex_shp)

    las_base = os.path.splitext(os.path.basename(las_path))[0]
    matching_boundary = lasindex_gdf[lasindex_gdf["filename"].str.contains(las_base, case=False, na=False)]
    if matching_boundary.empty:
        raise ValueError(f"No matching LAS boundary for {las_base}")

    panos_gdf = panos_gdf.to_crs(matching_boundary.crs)
    panos_gdf['geometry'] = panos_gdf.geometry.buffer(10)
    selected_panos = gpd.sjoin(panos_gdf, matching_boundary, predicate='intersects')

    R_sum = np.zeros_like(las.x, dtype=np.float32)
    G_sum = np.zeros_like(las.x, dtype=np.float32)
    B_sum = np.zeros_like(las.x, dtype=np.float32)
    weight_sum = np.zeros_like(las.x, dtype=np.float32)

    def process_pano(idx_row):
        idx, _ = idx_row
        pano_name = os.path.basename(selected_panos.iloc[idx]['Filename'])
        image_path = os.path.join(images_folder, pano_name)
        if not os.path.exists(image_path):
            return

        eop_row = eops_df.loc[eops_df['Filename'] == pano_name]
        if eop_row.empty:
            return

        pos = np.array([eop_row['X'].values[0], eop_row['Y'].values[0], eop_row['Z'].values[0]])
        yaw, pitch, roll = eop_row['Yaw'].values[0], eop_row['Pitch'].values[0], eop_row['Roll'].values[0]
        R_cam = rotation_matrix(yaw, pitch, roll)

        img = cv2.imread(image_path)
        if img is None:
            return
        img_h, img_w = img.shape[:2]

        nearby_idx = tree.query_ball_point(pos, r=search_radius)
        if not nearby_idx:
            return

        nearby_pts = points[nearby_idx] - pos
        cam_pts = (R_cam @ nearby_pts.T).T

        x_proj = -cam_pts[:, 0]
        y_proj = cam_pts[:, 2]
        z_proj = -cam_pts[:, 1]

        theta = np.arctan2(x_proj, z_proj) + np.radians(90)
        phi = np.arctan2(y_proj, np.sqrt(x_proj**2 + z_proj**2))

        u = (theta + np.pi) / (2 * np.pi) * img_w
        u = np.mod(u, img_w).astype(int)
        v = (np.pi / 2 - phi) / np.pi * img_h
        v = np.clip(v, 0, img_h - 1).astype(int)

        distances = np.linalg.norm(nearby_pts, axis=1)
        distances = np.clip(distances, 0.1, None)
        weights = 1.0 / distances
        colors = img[v, u]

        np.add.at(R_sum, nearby_idx, colors[:, 2] * weights)
        np.add.at(G_sum, nearby_idx, colors[:, 1] * weights)
        np.add.at(B_sum, nearby_idx, colors[:, 0] * weights)
        np.add.at(weight_sum, nearby_idx, weights)

    with ThreadPoolExecutor(max_workers=threads) as executor:
        list(executor.map(process_pano, enumerate(selected_panos.index)))

    weight_sum = np.maximum(weight_sum, 1e-5)
    R = (R_sum / weight_sum).astype(np.uint8)
    G = (G_sum / weight_sum).astype(np.uint8)
    B = (B_sum / weight_sum).astype(np.uint8)

    if las.header.point_format.id < 2:
        las = laspy.convert(las, point_format_id=2)

    las.red = (R.astype(np.uint16)) * 256
    las.green = (G.astype(np.uint16)) * 256
    las.blue = (B.astype(np.uint16)) * 256
    las.write(output_las_path)
