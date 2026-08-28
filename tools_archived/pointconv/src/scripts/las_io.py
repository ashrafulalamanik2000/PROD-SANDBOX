#!/usr/bin/env python3
"""SDAI LAZ I/O — the single place the project's LAZ-output standard lives.

THE STANDARD (full rationale in ``docs/LAZ_CONVENTIONS.md``):

  * **LAS 1.4, Point Data Record Format 7** — XYZ + intensity + return info +
    classification (full byte) + RGB + GPS time. (PF7 is the widest common
    format our pipelines already target; classification needs the full byte so
    synthetic markers like the survey-location class 64 are legal.)
  * **scale = 0.001 m** (1 mm) on X / Y / Z.
  * **A CRS is ALWAYS attached** — written as a WKT VLR, and on LAS 1.4 the
    "WKT" global-encoding bit is set (the spec requires it for PF >= 6). A file
    with no projection is non-conforming; downstream viewers/QGIS can't place it.
  * **One clean ``ExtraBytes`` VLR** (no duplicates) and no redundant vendor CRS
    VLRs riding along (e.g. the ``CRS_*`` / ``EPOCH`` records PLS SpatialExplorer
    emits) — these are dropped by rebuilding the header from the point format.
  * **A single, run-wide offset** so tiles / crops from different source files
    stay on one integer grid (a per-source local offset is what shifted LA 12's
    P_300 crop).

This module has NO internal project dependencies (only ``laspy`` + ``numpy``,
and ``pyproj`` for CRS parsing) so any project in the repo can use it by
importing it or vendoring a copy of this single file.

CLI (audit a directory's conformance):
    python las_io.py --verify <dir-or-file> [--recursive]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import laspy
import numpy as np

# --- the standard -----------------------------------------------------------
STD_VERSION = "1.4"
STD_POINT_FORMAT = 7
STD_SCALE = 0.001
# Source-vendor VLRs that are redundant once we attach a clean CRS. PLS
# SpatialExplorer emits a WKT VLR *plus* CRS_EPSG / CRS_WKT2_* / CRS_PROJ* /
# EPOCH / TIME_FORMAT records; carrying them all bloats the header and confuses
# strict readers. We keep exactly one CRS (the WKT VLR add_crs writes).
_REDUNDANT_VENDOR_USER_IDS = {"EPOCH", "TIME_FORMAT", "CRS_DEFINITIONS"}
_REDUNDANT_VENDOR_PREFIXES = ("CRS_",)


# --- CRS helpers ------------------------------------------------------------
def to_crs(crs_like):
    """Normalize an EPSG int/str, a WKT/PROJ string, or a pyproj.CRS to a
    pyproj.CRS. Returns None when given None/empty or on any parse failure."""
    if crs_like is None:
        return None
    try:
        import pyproj
        if isinstance(crs_like, pyproj.CRS):
            return crs_like
        s = str(crs_like).strip()
        if not s:
            return None
        if s.isdigit():
            return pyproj.CRS.from_epsg(int(s))
        return pyproj.CRS.from_user_input(s)   # EPSG:xxxx, WKT, or PROJ
    except Exception:  # noqa: BLE001
        return None


def resolve_crs(*, crs=None, carry_header=None):
    """CRS policy for an output file: an explicit ``crs`` wins; otherwise carry
    the source header's CRS (which is often a richer *compound* horizontal +
    vertical CRS than a bare EPSG code). Returns pyproj.CRS or None."""
    c = to_crs(crs)
    if c is not None:
        return c
    if carry_header is not None:
        try:
            return carry_header.parse_crs()
        except Exception:  # noqa: BLE001
            return None
    return None


# --- header hygiene (for writers that build their own header) ---------------
def is_redundant_source_vlr(vlr) -> bool:
    """True if a SOURCE header's VLR should be DROPPED when copying it into a
    conforming output header:

    * any ``ExtraBytes`` VLR — the writer re-creates the *correct* one from its
      own point format (copying the source's leaves a stale/duplicate record);
    * redundant vendor CRS records (``CRS_*`` / ``EPOCH`` / ``TIME_FORMAT``,
      e.g. PLS SpatialExplorer) — once a clean WKT CRS is attached they only
      bloat the header and confuse strict readers.

    Use in a writer's VLR-copy loop: ``if is_redundant_source_vlr(v): continue``."""
    if type(vlr).__name__ == "ExtraBytesVlr":
        return True
    uid = getattr(vlr, "user_id", "") or ""
    return uid in _REDUNDANT_VENDOR_USER_IDS or uid.startswith(_REDUNDANT_VENDOR_PREFIXES)


def set_wkt_bit_if_crs(header) -> bool:
    """Set the LAS-1.4 WKT global-encoding bit when the header carries a **WKT
    CRS VLR**.

    laspy sets this bit via ``add_crs`` but NOT when a WKT CRS VLR is merely
    *copied* into a header (the common case for writers that inherit a source
    header). The LAS 1.4 spec requires the bit for PF >= 6, and strict readers
    reject the CRS without it. The bit asserts a WKT VLR specifically, so a
    GeoTIFF-keyed CRS must NOT set it (converting GeoTIFF -> WKT is a separate
    normalization step, handled by ``make_standard_header``/``add_crs``). No-op
    on LAS < 1.4. Returns True if the bit was set."""
    try:
        if f"{header.version.major}.{header.version.minor}" != STD_VERSION:
            return False
        if any(type(v).__name__ == "WktCoordinateSystemVlr"
               for v in header.vlrs):
            header.global_encoding.wkt = True
            return True
    except Exception:  # noqa: BLE001
        pass
    return False


# --- header construction ----------------------------------------------------
def make_standard_header(*, offsets, crs=None, carry_header=None,
                         point_format=STD_POINT_FORMAT, version=STD_VERSION,
                         scale=STD_SCALE):
    """Build a conforming ``laspy.LasHeader``.

    * Rebuilds from the point format, so the result has exactly one clean
      ``ExtraBytes`` VLR (collapsing any source duplicates) and none of the
      source's redundant vendor CRS VLRs.
    * If ``carry_header`` is given and already at ``point_format``, its
      point-format object is reused so extra dims (e.g. ``point_id``) survive.
    * Attaches the resolved CRS; on LAS 1.4 ``add_crs`` sets the WKT bit.
    """
    pf = point_format
    if (carry_header is not None
            and getattr(carry_header.point_format, "id", None) == point_format):
        pf = carry_header.point_format
    hdr = laspy.LasHeader(version=version, point_format=pf)
    hdr.scales = [scale, scale, scale]
    hdr.offsets = list(offsets)
    c = resolve_crs(crs=crs, carry_header=carry_header)
    if c is not None:
        try:
            hdr.add_crs(c)
        except Exception:  # noqa: BLE001
            pass
    return hdr


# --- streaming writer / merger ---------------------------------------------
def stream_merge_las(src_paths, out_path, *, crs=None, offsets=None,
                     strict=False) -> bool:
    """Merge LAS/LAZ files into ONE conforming output WITHOUT loading them all
    into RAM (peak ~ the single largest input, not the sum).

    * Output conforms to the standard (PF7 / LAS 1.4 / scale 0.001, a clean
      single ExtraBytes VLR, CRS attached + WKT bit set).
    * Inputs whose scale/offset/format match the output grid are appended raw
      (fast); any input on a *different* grid is re-encoded through scaled
      coordinates so its points land correctly (they are NOT shifted). Inputs
      that can't be read or re-encoded are skipped with a loud warning —
      unless ``strict``, which raises RuntimeError after the merge so callers
      feeding downstream ML stages hard-fail instead of silently passing
      empty/partial clouds along.

    Returns True if any points were written.
    """
    src_paths = [p for p in src_paths if p and Path(p).exists()]
    if not src_paths:
        return False
    first = laspy.read(str(src_paths[0]))
    if offsets is not None:
        out_off = list(offsets)
    else:
        # Offsets must come from the DATA, not the first source's header:
        # sources carrying offset=[0,0,0] with large UTM coords overflow
        # int32 once re-encoded onto the 1 mm standard grid (northing
        # 4.8e6 / 0.001 > 2**31 -> laspy "Values given do not fit", empty
        # merged corridors on EPSG:2958 / PA3157, 2026-06-10). floor(min)
        # keeps the scaled ints near zero at any scale.
        mins = []
        for p in src_paths:
            try:
                with laspy.open(str(p)) as rdr:
                    if int(rdr.header.point_count) > 0:
                        mins.append([float(v) for v in rdr.header.mins])
            except Exception:  # noqa: BLE001  (unreadable: skipped below)
                pass
        if mins:
            out_off = [float(np.floor(v)) for v in np.min(mins, axis=0)]
        else:
            out_off = list(first.header.offsets)
    out_hdr = make_standard_header(
        offsets=out_off, crs=crs, carry_header=first.header,
        point_format=first.header.point_format.id)
    del first
    n_pts = n_reenc = n_skip = 0
    with laspy.open(str(out_path), mode="w", header=out_hdr) as writer:
        for p in src_paths:
            try:
                d = laspy.read(str(p))
            except Exception as e:  # noqa: BLE001
                print(f"  [las_io] WARN skip unreadable {Path(p).name}: {e}")
                n_skip += 1
                continue
            same = (d.header.point_format.id == out_hdr.point_format.id
                    and list(d.header.scales) == list(out_hdr.scales)
                    and list(d.header.offsets) == list(out_hdr.offsets))
            if same:
                writer.write_points(d.points)
            else:
                try:
                    rec = laspy.ScaleAwarePointRecord.zeros(
                        int(d.header.point_count),
                        point_format=out_hdr.point_format,
                        scales=out_hdr.scales, offsets=out_hdr.offsets)
                    for dn in d.point_format.dimension_names:
                        if dn in ("X", "Y", "Z"):
                            continue
                        try:
                            rec[dn] = d.points[dn]
                        except Exception:  # noqa: BLE001
                            pass
                    rec.x = np.asarray(d.x)
                    rec.y = np.asarray(d.y)
                    rec.z = np.asarray(d.z)
                    writer.write_points(rec)
                    n_reenc += 1
                    del rec
                except Exception as e:  # noqa: BLE001
                    print(f"  [las_io] WARN skip {Path(p).name}: "
                          f"re-encode failed ({e})")
                    n_skip += 1
                    del d
                    continue
            n_pts += int(d.header.point_count)
            del d
    msg = (f"  [las_io] {n_pts:,} pts from {len(src_paths) - n_skip} files "
           f"-> {Path(out_path).name}")
    if n_reenc:
        msg += f"  ({n_reenc} re-encoded to merge grid)"
    if n_skip:
        msg += f"  ({n_skip} SKIPPED)"
    print(msg)
    if strict and (n_skip or n_pts == 0):
        raise RuntimeError(
            f"merge {Path(out_path).name}: {n_skip} source(s) skipped, "
            f"{n_pts:,} points written -- refusing to continue (strict); "
            "an empty/partial merged cloud must not reach downstream stages")
    return n_pts > 0


def conform_las(in_path, out_path=None, *, crs=None) -> bool:
    """Rewrite a single existing LAS/LAZ to conform to the standard (PF7 / LAS
    1.4 / scale 0.001, CRS attached + WKT bit set, one clean ExtraBytes VLR,
    vendor CRS VLRs dropped). If ``out_path`` is None, writes
    ``<in>.conformed.laz`` beside the input (non-destructive). Internally a
    one-file ``stream_merge_las``, so a file on a divergent grid is re-encoded.
    Returns True on success."""
    in_path = Path(in_path)
    if out_path is None:
        out_path = in_path.with_suffix(".conformed.laz")
    return stream_merge_las([in_path], out_path, crs=crs)


# --- conformance audit ------------------------------------------------------
def verify_las(path) -> list:
    """Return a list of conformance issues for an existing LAS/LAZ (empty list
    == conforms). Header-only (fast — does not read points). RGB-emptiness is a
    data-quality concern, not a header property, so it is NOT checked here."""
    issues = []
    try:
        with laspy.open(str(path)) as fh:
            h = fh.header
            v = f"{h.version.major}.{h.version.minor}"
            if v != STD_VERSION:
                issues.append(f"LAS version {v} (want {STD_VERSION})")
            if h.point_format.id != STD_POINT_FORMAT:
                issues.append(f"PDRF {h.point_format.id} (want {STD_POINT_FORMAT})")
            if any(abs(float(s) - STD_SCALE) > 1e-12 for s in h.scales):
                issues.append(
                    f"scale {tuple(round(float(s),5) for s in h.scales)} "
                    f"(want {STD_SCALE})")
            crs = None
            try:
                crs = h.parse_crs()
            except Exception:  # noqa: BLE001
                crs = None
            if crs is None:
                issues.append("no CRS attached")
            elif v == STD_VERSION and not h.global_encoding.wkt:
                issues.append("WKT global-encoding bit not set (LAS 1.4 + PF>=6)")
            ebv = sum(1 for x in h.vlrs
                      if type(x).__name__ == "ExtraBytesVlr")
            if ebv > 1:
                issues.append(f"{ebv} ExtraBytes VLRs (duplicate)")
    except Exception as e:  # noqa: BLE001
        issues.append(f"unreadable: {e}")
    return issues


def audit_dir(root, recursive=True) -> dict:
    """Verify every .las/.laz under ``root``. Returns
    {path: [issues]} for non-conforming files, plus a cross-file offset check
    (files on >1 distinct offset are flagged — the run-wide-offset rule)."""
    root = Path(root)
    pat = "**/*.la[sz]" if recursive else "*.la[sz]"
    files = sorted(root.glob(pat)) if root.is_dir() else [root]
    report = {}
    offsets = {}
    for f in files:
        iss = verify_las(f)
        if iss:
            report[str(f)] = iss
        try:
            with laspy.open(str(f)) as fh:
                offsets.setdefault(tuple(round(float(o), 3)
                                         for o in fh.header.offsets), []).append(f.name)
        except Exception:  # noqa: BLE001
            pass
    return {"files": len(files), "nonconforming": report, "offsets": offsets}


def _main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Audit LAZ conformance to the SDAI standard.")
    ap.add_argument("--verify", required=True, help="file or directory to audit")
    ap.add_argument("--recursive", action="store_true", help="recurse into subdirs")
    args = ap.parse_args(argv)
    res = audit_dir(args.verify, recursive=args.recursive)
    print(f"audited {res['files']} file(s); "
          f"{len(res['nonconforming'])} non-conforming")
    for path, iss in res["nonconforming"].items():
        print(f"  FAIL {path}")
        for i in iss:
            print(f"      - {i}")
    if len(res["offsets"]) > 1:
        print(f"  WARN {len(res['offsets'])} distinct offsets (run-wide-offset rule):")
        for off, names in res["offsets"].items():
            print(f"      {off}: {len(names)} file(s)")
    return 1 if res["nonconforming"] else 0


if __name__ == "__main__":
    sys.exit(_main())
