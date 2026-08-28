"""Euler/rotation math — unchanged from auto_create_csv_all.py."""
import sys
import math as m
import numpy as np


def Rx(theta):
    return np.matrix([[1, 0, 0],
                      [0, m.cos(theta), -m.sin(theta)],
                      [0, m.sin(theta), m.cos(theta)]])


def Ry(theta):
    return np.matrix([[m.cos(theta), 0, m.sin(theta)],
                      [0, 1, 0],
                      [-m.sin(theta), 0, m.cos(theta)]])


def Rz(theta):
    return np.matrix([[m.cos(theta), -m.sin(theta), 0],
                      [m.sin(theta), m.cos(theta), 0],
                      [0, 0, 1]])


def Frot(psii, phii, thetai):
    """Convert HRP (heading, roll, pitch in degrees) to Euler angles [psi, theta, phi] in degrees."""
    phi = m.radians(phii)
    theta = m.radians(thetai)
    psi = m.radians(psii)

    R = Rz(psi) * Ry(theta) * Rx(phi)
    RT = R.transpose()
    XYZ = np.matrix([[0, 0, 1], [-1, 0, 0], [0, -1, 0]])
    FM = XYZ * RT

    tol = sys.float_info.epsilon * 100
    if abs(FM.item(0, 0)) < tol and abs(FM.item(1, 0)) < tol:
        eul1 = 0
        eul2 = m.atan2(-FM.item(2, 0), FM.item(0, 0))
        eul3 = m.atan2(-FM.item(1, 2), FM.item(1, 1))
    else:
        eul1 = m.atan2(FM.item(1, 0), FM.item(0, 0))
        sp = m.sin(eul1)
        cp = m.cos(eul1)
        eul2 = m.atan2(-FM.item(2, 0), cp * FM.item(0, 0) + sp * FM.item(1, 0))
        eul3 = m.atan2(sp * FM.item(0, 2) - cp * FM.item(1, 2), cp * FM.item(1, 1) - sp * FM.item(0, 1))

    return [m.degrees(eul3), m.degrees(eul2), m.degrees(eul1)]
