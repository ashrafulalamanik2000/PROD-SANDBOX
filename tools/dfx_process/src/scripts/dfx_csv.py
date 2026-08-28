"""
dfx_csv.py — port of Write_S3Dcsv_v10_effigis_asdef.py
Parses Solve3D .lst files into camera position CSVs.
"""
import csv
import math
import os
import sys

import numpy as np


def _Rx(t):
    return np.array([[1, 0, 0], [0, math.cos(t), -math.sin(t)], [0, math.sin(t), math.cos(t)]])

def _Ry(t):
    return np.array([[math.cos(t), 0, math.sin(t)], [0, 1, 0], [-math.sin(t), 0, math.cos(t)]])

def _Rz(t):
    return np.array([[math.cos(t), -math.sin(t), 0], [math.sin(t), math.cos(t), 0], [0, 0, 1]])


def frot(psi_deg, phi_deg, theta_deg):
    """Convert Solve3D HRP angles to Roll/Pitch/Yaw via rotation matrix."""
    phi   = math.radians(phi_deg)
    theta = math.radians(theta_deg)
    psi   = math.radians(psi_deg)
    FM = np.array([[0, 0, 1], [-1, 0, 0], [0, -1, 0]]) @ (_Rz(psi) @ _Ry(theta) @ _Rx(phi)).T
    tol = sys.float_info.epsilon * 100
    if abs(FM[0, 0]) < tol and abs(FM[1, 0]) < tol:
        e1 = 0.0
        e2 = math.atan2(-FM[2, 0], FM[0, 0])
        e3 = math.atan2(-FM[1, 2], FM[1, 1])
    else:
        e1 = math.atan2(FM[1, 0], FM[0, 0])
        sp, cp = math.sin(e1), math.cos(e1)
        e2 = math.atan2(-FM[2, 0], cp * FM[0, 0] + sp * FM[1, 0])
        e3 = math.atan2(sp * FM[0, 2] - cp * FM[1, 2], cp * FM[1, 1] - sp * FM[0, 1])
    return math.degrees(e1), math.degrees(e2), math.degrees(e3)


def create_csv(lst_path, icsv=False, shrp=True):
    """Parse a Solve3D .lst file, write camera CSVs, return path to combined CSV."""
    workspace, lfile = os.path.split(lst_path)
    pname = lfile.replace("_Image Project.lst", "")
    out_dir = os.path.join(workspace, pname + "_CSVs")
    os.makedirs(out_dir, exist_ok=True)

    header = ['Filename', 'X', 'Y', 'Z', 'Roll', 'Pitch', 'Yaw'] if shrp else \
             ['Filename', 'X', 'Y', 'Z', 'Yaw', 'Roll', 'Pitch']

    big_csv = os.path.join(out_dir, pname + ".csv")

    with open(lst_path, 'r') as f:
        lines = [l.rstrip() for l in f]

    # Collect unique 360 run folders (for per-run CSVs if icsv=True)
    # Normalize backslashes → forward slashes for Linux (Docker) compat
    a360 = []
    for line in lines:
        if line.startswith("Ima"):
            ws = os.path.split(line.replace("Image=.\\", "").replace("\\", "/"))[0]
            if ws.endswith("360") and ws not in a360:
                a360.append(ws)

    with open(big_csv, 'w', newline='') as f:
        csv.writer(f).writerow(header)

    if icsv:
        for folder in a360:
            with open(os.path.join(out_dir, folder + ".csv"), 'w', newline='') as f:
                csv.writer(f).writerow(header)

    for i, line in enumerate(lines):
        if not line.startswith("Ima"):
            continue
        impath = line.replace("Image=.\\", "").replace("\\", "/")
        img_ws, jfile = os.path.split(impath)
        jfilename = os.path.splitext(jfile)[0]
        if not img_ws.endswith("360") or jfilename[-1] != '3':
            continue

        xyz  = lines[i + 1].replace("Xyz=", "").split()
        hrp  = lines[i + 2].replace("Hrp=", "").split()
        fname = jfilename[:-2] + os.path.splitext(jfile)[1]

        if shrp:
            r, p, y = frot(float(hrp[0]), float(hrp[1]), float(hrp[2]))
        else:
            r, p, y = float(hrp[1]), float(hrp[2]), float(hrp[0])

        row = [fname, xyz[0], xyz[1], xyz[2], str(r), str(p), str(y)]

        with open(big_csv, 'a', newline='') as f:
            csv.writer(f).writerow(row)

        if icsv:
            with open(os.path.join(out_dir, img_ws + ".csv"), 'a', newline='') as f:
                csv.writer(f).writerow(row)

    return big_csv
