"""Bring in a phone or tablet LiDAR scan as a point cloud on the site's grid.

    domus scan walk.ply --tag lawn --control lawn-control.csv --up y
    domus scan site.las --tag survey --epsg 2193

iPhones and iPads with LiDAR scan through an app (Polycam, Scaniverse, 3d Scanner App and so on), which
exports a file. This reads the exports that hold plain points: PLY, LAS, OBJ, and XYZ/TXT/PTS/CSV text. A scan
is in true metres in its own frame, so unlike a video it needs no scale, only a rotation and a shift:

- `--control` a CSV with columns x,y,z (in the scan) and E,N,H (on the site's grid), for points you can pick
  in both: a house corner, a survey mark. With `--up`, the scan's vertical axis (y for most ARKit exports, z for
  most LAS) is taken as true, as the phone's sensors keep it, and two points are enough. Without it the
  fit takes three or more points not in a line, and tilts as they say.
- `--epsg` for a file already on a real grid.

Writes data/video/<tag>-scan.npz, which `domus video export` picks up for the next build.
"""

import argparse
import csv
import re
from pathlib import Path

import numpy as np
from pyproj import Transformer

from . import crs
from .crs import ROOT
from .errors import DomusError

UP_TO_Z = {"z": np.eye(3), "y": np.array([[1.0, 0, 0], [0, 0, -1.0], [0, 1.0, 0]])}
PLY_TYPES = {"char": "i1", "int8": "i1", "uchar": "u1", "uint8": "u1", "short": "i2", "int16": "i2", "ushort": "u2",
             "uint16": "u2", "int": "i4", "int32": "i4", "uint": "u4", "uint32": "u4", "float": "f4", "float32": "f4",
             "double": "f8", "float64": "f8"}
WARN_RESIDUAL_M = 0.15


def _colours(n, r=None, g=None, b=None):
    if r is None:
        return np.full((n, 3), 170, np.uint8)
    rgb = np.column_stack([r, g, b]).astype(float)
    if rgb.size and rgb.max() > 255:      # 16-bit, as LAS keeps them
        rgb = rgb / 256
    elif rgb.size and rgb.max() <= 1.0:   # 0 to 1, as some text exports
        rgb = rgb * 255
    return rgb.clip(0, 255).astype(np.uint8)


def read_ply(path):
    raw = Path(path).read_bytes()
    end = raw.find(b"end_header")
    if not raw.startswith(b"ply") or end < 0:
        raise DomusError(f"{path}: not a PLY file")
    body_at = raw.index(b"\n", end) + 1
    fmt, count, props, element = None, 0, [], None
    before = 0
    for line in raw[:end].decode("ascii", "replace").splitlines():
        w = line.split()
        if not w:
            continue
        if w[0] == "format":
            fmt = w[1]
        elif w[0] == "element":
            element = w[1]
            if element == "vertex":
                count = int(w[2])
            elif not count:
                before += 1
        elif w[0] == "property" and element == "vertex":
            if w[1] == "list":
                raise DomusError(f"{path}: list properties on vertices aren't supported")
            props.append((w[2], PLY_TYPES[w[1]]))
    if before:
        raise DomusError(f"{path}: other elements come before the vertices; export a plain point cloud")
    names = [p[0] for p in props]
    if fmt == "ascii":
        rows = raw[body_at:].decode("ascii").split("\n")[:count]
        a = np.loadtxt(rows, ndmin=2)
        col = {n: a[:, k] for k, n in enumerate(names)}
    else:
        order = "<" if fmt == "binary_little_endian" else ">"
        dt = np.dtype([(n, order + t) for n, t in props])
        a = np.frombuffer(raw, dt, count, body_at)
        col = {n: a[n] for n in names}
    n = len(col["x"])
    xyz = np.column_stack([col["x"], col["y"], col["z"]]).astype(float)
    if "red" in col:
        return xyz, _colours(n, col["red"], col["green"], col["blue"])
    if "r" in col:
        return xyz, _colours(n, col["r"], col["g"], col["b"])
    return xyz, _colours(n)


# LAS point format -> (record length without extra bytes, offset of red, or None for no colour)
LAS_RGB = {0: None, 1: None, 2: 20, 3: 28, 5: 28, 6: None, 7: 30, 8: 30}


def read_las(path):
    raw = Path(path).read_bytes()
    if raw[:4] != b"LASF":
        raise DomusError(f"{path}: not a LAS file")
    at = int(np.frombuffer(raw, "<u4", 1, 96)[0])
    fmt_raw = raw[104]
    if fmt_raw >= 128:
        raise DomusError(f"{path}: a compressed LAZ file; export LAS or PLY instead")
    fmt = fmt_raw & 0x3f
    if fmt not in LAS_RGB:
        raise DomusError(f"{path}: LAS point format {fmt} isn't supported")
    length = int(np.frombuffer(raw, "<u2", 1, 105)[0])
    n = int(np.frombuffer(raw, "<u4", 1, 107)[0])
    if n == 0 and raw[25] >= 4:     # LAS 1.4 keeps large counts in its own field
        n = int(np.frombuffer(raw, "<u8", 1, 247)[0])
    scale = np.frombuffer(raw, "<f8", 3, 131)
    offset = np.frombuffer(raw, "<f8", 3, 155)
    rec = np.frombuffer(raw, f"V{length}", n, at)
    bytes_ = rec.view("u1").reshape(n, length)
    ints = bytes_[:, :12].copy().view("<i4").reshape(n, 3)
    xyz = ints * scale + offset
    o = LAS_RGB[fmt]
    if o is None:
        return xyz, _colours(n)
    rgb = bytes_[:, o:o + 6].copy().view("<u2").reshape(n, 3)
    return xyz, _colours(n, rgb[:, 0], rgb[:, 1], rgb[:, 2])


def read_obj(path):
    pts = []
    for line in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("v "):
            pts.append([float(v) for v in line.split()[1:]])
    if not pts:
        raise DomusError(f"{path}: no vertices")
    width = min(len(p) for p in pts)
    a = np.array([p[:width] for p in pts])
    return a[:, :3], _colours(len(a), *(a[:, k] for k in (3, 4, 5))) if width >= 6 else _colours(len(a))


def read_text(path):
    rows = []
    for line in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        cells = re.split(r"[,\s]+", line.strip())
        try:
            rows.append([float(c) for c in cells])
        except ValueError:
            continue
    rows = [r for r in rows if len(r) >= 3]
    if not rows:
        raise DomusError(f"{path}: no x y z rows")
    width = min(len(r) for r in rows)
    a = np.array([r[:width] for r in rows])
    # x y z r g b, or x y z intensity r g b as PTS has it
    if width >= 7:
        return a[:, :3], _colours(len(a), a[:, 4], a[:, 5], a[:, 6])
    if width == 6:
        return a[:, :3], _colours(len(a), a[:, 3], a[:, 4], a[:, 5])
    return a[:, :3], _colours(len(a))


READERS = {".ply": read_ply, ".las": read_las, ".obj": read_obj, ".xyz": read_text, ".txt": read_text,
           ".pts": read_text, ".csv": read_text}


def read_cloud(path):
    suffix = Path(path).suffix.lower()
    if suffix not in READERS:
        raise DomusError(f"{path}: can't read {suffix or 'this'} files. Export the scan from its app as PLY or LAS "
                         "(a point cloud, or a mesh in PLY or OBJ).")
    return READERS[suffix](path)


def read_control(path):
    with open(path, newline="", encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))
    try:
        return (np.array([[float(r[k]) for k in ("x", "y", "z")] for r in rows]),
                np.array([[float(r[k]) for k in ("E", "N", "H")] for r in rows]))
    except KeyError as e:
        raise DomusError(f"{path}: needs columns x,y,z,E,N,H; missing {e}") from e


def fit_rigid_3d(src, dst):
    """Rotation R and shift t (no scale, no mirroring) with dst = src @ R.T + t, by Kabsch."""
    ms, md = src.mean(0), dst.mean(0)
    if np.linalg.matrix_rank(src - ms, tol=0.05) < 2:
        raise DomusError("The control points lie in a line, so they can't fix the scan's rotation: spread them out, "
                         "or give --up.")
    u, _, vt = np.linalg.svd((src - ms).T @ (dst - md))
    d = np.sign(np.linalg.det(vt.T @ u.T))
    R = vt.T @ np.diag([1, 1, d]) @ u.T
    return R, md - R @ ms


def fit_about_vertical(src, dst):
    """The same with the vertical taken as true: a turn in plan, a shift and a height shift."""
    ms, md = src.mean(0), dst.mean(0)
    a, b = (src - ms)[:, :2], (dst - md)[:, :2]
    th = np.arctan2(np.sum(a[:, 0] * b[:, 1] - a[:, 1] * b[:, 0]), np.sum(a * b))
    c, s = np.cos(th), np.sin(th)
    R = np.array([[c, -s, 0], [s, c, 0], [0, 0, 1.0]])
    return R, md - R @ ms


def place(xyz, control, up=None):
    """The points on the site's grid and the control's residuals (m)."""
    src, dst = control
    if up:
        pre = UP_TO_Z[up]
        src2 = src @ pre.T
        if len(src2) < 2 or np.hypot(*np.ptp(src2[:, :2], axis=0)) < 0.5:
            raise DomusError("With --up, give at least two control points at least 0.5 m apart.")
        R, t = fit_about_vertical(src2, dst)
        return (xyz @ pre.T) @ R.T + t, np.linalg.norm(src2 @ R.T + t - dst, axis=1)
    if len(src) < 3:
        raise DomusError("Without --up, give at least three control points.")
    R, t = fit_rigid_3d(src, dst)
    return xyz @ R.T + t, np.linalg.norm(src @ R.T + t - dst, axis=1)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="domus scan", description="Bring in a phone LiDAR scan as a point cloud.")
    ap.add_argument("file")
    ap.add_argument("--tag", required=True, help="a short name for this scan")
    how = ap.add_mutually_exclusive_group(required=True)
    how.add_argument("--control", help="CSV with x,y,z,E,N,H for points in both the scan and on the grid")
    how.add_argument("--epsg", type=int, help="the file is already on this grid")
    ap.add_argument("--up", choices=["y", "z"], help="the scan's vertical axis, taken as true")
    args = ap.parse_args(argv)
    if not re.fullmatch(r"[A-Za-z0-9_-]+", args.tag):
        ap.error("--tag takes letters, digits, - and _")
    xyz, rgb = read_cloud(args.file)
    ok = np.isfinite(xyz).all(axis=1)
    xyz, rgb = xyz[ok], rgb[ok]
    print(f"{args.file}: {len(xyz):,} points")
    if args.control:
        site, res = place(xyz, read_control(args.control), args.up)
        print(f"control: {len(res)} points, residuals median {np.median(res):.3f} m, largest {res.max():.3f} m")
        if res.max() > WARN_RESIDUAL_M:
            print(f"warning: a control point is over {WARN_RESIDUAL_M} m off. Check it was picked at the same place in both.")
    else:
        e, n = Transformer.from_crs(args.epsg, crs.site_epsg(), always_xy=True).transform(xyz[:, 0], xyz[:, 1])
        site = np.column_stack([e, n, xyz[:, 2]])
    x0, y0, x1, y1 = crs.settings()["model"]["extent"]
    inside = (site[:, 0] > x0) & (site[:, 0] < x1) & (site[:, 1] > y0) & (site[:, 1] < y1)
    print(f"{inside.sum():,} points fall inside the model's extent")
    if not inside.any():
        print("warning: none of the scan lands on the site. Check the control points' coordinates.")
    out = ROOT / "data" / "video" / f"{args.tag}-scan.npz"
    out.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(out, xyz=site, rgb=rgb, nview=np.full(len(site), 3, np.uint8))
    print(f"wrote {out}. Next: domus video export, then domus build")


if __name__ == "__main__":
    main()
