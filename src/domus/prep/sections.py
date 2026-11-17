"""Cross-sections through the point clouds along a reference line, for reading by eye.

    domus prep sections walk-dense walk-site [...] [--step 1]

Stations run along the line in "sections" of data/prep.json: `line_start` (points leading in) then the main boundary's
corners numbered `line_corners`, with chainage s from the corner numbered `origin_corner`. Each section takes a 0.3 m
slice of every cloud and plots offset from the line against height, with the LiDAR ground, the walls now in the model
and the line. Writes data/video/checks/sections/<first cloud>-s<s>.png and a contact sheet.
"""

import argparse
import itertools

import numpy as np
from PIL import Image, ImageDraw

from ..geo import crs, geojson, raster
from . import config


def video_dir():
    """The site's video work, data/video/."""
    return crs.ROOT / "data" / "video"


def edge_line():
    """The reference line and the point its chainage counts from."""
    cfg = config("sections")
    ring = geojson.read(crs.ROOT / "data" / "site" / "boundary.geojson")["features"][0]["geometry"]["coordinates"][0]
    corners = [ring[i][:2] for i in cfg["line_corners"]]
    return np.array([*cfg.get("line_start", []), *corners], float), np.array(ring[cfg["origin_corner"]][:2], float)


def frame_of(pts, line, origin):
    """(s, o) of points against the polyline: s along it from `origin` (towards the house),
    o across it, positive to the right of the line."""
    seg_start = [0.0]
    for a, b in itertools.pairwise(line):
        seg_start.append(seg_start[-1] + np.hypot(*(b - a)))
    k0 = int(np.argmin(np.hypot(*(line - origin).T)))
    s0 = seg_start[k0]
    best = np.full(len(pts), np.inf)
    s = np.zeros(len(pts))
    o = np.zeros(len(pts))
    for k, (a, b) in enumerate(itertools.pairwise(line)):
        d = b - a
        length = np.hypot(*d)
        u = d / length
        n = np.array([u[1], -u[0]])          # right of the direction of travel
        rel = pts[:, :2] - a
        t = rel @ u
        tc = np.clip(t, 0, length)
        dist = np.hypot(*(rel - tc[:, None] * u).T)
        better = dist < best
        best[better] = dist[better]
        s[better] = seg_start[k] + t[better] - s0
        o[better] = (rel @ n)[better]
    return s, o


def main():
    ap = argparse.ArgumentParser(prog="domus prep sections")
    ap.add_argument("clouds", nargs="+")
    ap.add_argument("--step", type=float, default=1.0)
    ap.add_argument("--from", dest="s_from", type=float, default=-6.0)
    ap.add_argument("--to", type=float, default=16.0)
    args = ap.parse_args()
    line, start = edge_line()
    clouds = []
    for name in args.clouds:
        d = np.load(video_dir() / f"{name}.npz")
        xyz = d["xyz"]
        keep = d["nview"] >= 3 if "nview" in d else np.ones(len(xyz), bool)
        s, o = frame_of(xyz[keep], line, start)
        clouds.append((name, xyz[keep], d["rgb"][keep], s, o))
    walls = geojson.read(crs.ROOT / "data" / "site" / "walls-existing.geojson")["features"]
    dem = raster.dem()
    out = video_dir() / "checks" / "sections"
    out.mkdir(parents=True, exist_ok=True)
    W, H, sc = 760, 520, 120.0
    tiles = []
    for sk in np.arange(args.s_from, args.to + 1e-6, args.step):
        img = Image.new("RGB", (W, H), (250, 250, 247))
        dr = ImageDraw.Draw(img)
        # Section line in plan: the point at s = sk on the edge (found by sampling the polyline
        # densely), and its across direction.
        dense = np.vstack([np.linspace(a, b, 200) for a, b in itertools.pairwise(line)])
        ds, _ = frame_of(np.column_stack([dense, np.zeros(len(dense))]), line, start)
        i = int(np.argmin(np.abs(ds - sk)))
        c = dense[i]
        j = min(i + 1, len(dense) - 1)
        u = dense[j] - dense[max(i - 1, 0)]
        u /= np.linalg.norm(u)
        n = np.array([u[1], -u[0]])
        offs = np.linspace(-3, 3, 121)
        gz = dem.sample(c[0] + offs * n[0], c[1] + offs * n[1])
        zc = float(np.nanmedian(gz))

        def px(oo, zz, zc=zc):
            return W / 2 + oo * sc, H / 2 - (zz - zc) * sc

        for gx in range(-3, 4):
            dr.line([px(gx, zc - 2), px(gx, zc + 2)], fill=(228, 228, 222))
        for gzz in np.arange(np.floor(zc - 2), zc + 2.01, 0.5):
            dr.line([px(-3, gzz), px(3, gzz)], fill=(228, 228, 222))
            dr.text((2, px(0, gzz)[1] - 6), f"{gzz:.1f}", fill=(140, 140, 140))
        dr.line([px(oo, zz) for oo, zz in zip(offs, gz) if np.isfinite(zz)], fill=(200, 120, 40), width=2)
        n_pts = 0
        for name, xyz, rgb, s, o in clouds:
            m = np.abs(s - sk) < 0.15
            n_pts += int(m.sum())
            for oo, zz, cc in zip(o[m], xyz[m, 2], rgb[m]):
                x, y = px(oo, zz)
                dr.rectangle([x - 1, y - 1, x + 1, y + 1], fill=tuple(int(v) for v in cc))
        dr.line([px(0, zc - 2), px(0, zc + 2)], fill=(255, 190, 0), width=1)
        for f in walls:
            pts = np.array(f["geometry"]["coordinates"])
            ws, wo = frame_of(np.column_stack([pts, np.zeros(len(pts))]), line, start)
            for a in range(len(pts) - 1):
                lo, hi = sorted((ws[a], ws[a + 1]))
                if lo <= sk <= hi and hi > lo:
                    t = (sk - ws[a]) / (ws[a + 1] - ws[a])
                    oo = wo[a] + t * (wo[a + 1] - wo[a])
                    g = float(dem.sample(np.array([c[0] + oo * n[0]]), np.array([c[1] + oo * n[1]]))[0])
                    if pts.shape[1] == 3:   # third coordinate = top of the wall
                        top = pts[a, 2] + t * (pts[a + 1, 2] - pts[a, 2])
                    else:
                        top = g + f["properties"]["height_m"]
                    dr.rectangle([px(oo - 0.1, top), px(oo + 0.1, min(g, top - 0.05))], outline=(0, 120, 255), width=2)
                    dr.text(px(oo + 0.12, top), f["properties"]["id"], fill=(0, 120, 255))
        dr.text((8, 6), f"s = {sk:+.1f} m along the line   {n_pts} points   "
                        f"right of the line to the right   1 m = {sc:.0f} px", fill=(20, 20, 20))
        dr.text((8, H - 16), "orange: LiDAR ground; yellow: the reference line; blue: model walls",
                fill=(90, 90, 90))
        path = out / f"{args.clouds[0]}-s{sk:+05.1f}.png"
        img.save(path)
        tiles.append(img)
    cols = 3
    sheet = Image.new("RGB", (cols * W // 2, ((len(tiles) + cols - 1) // cols) * H // 2), "white")
    for k, t in enumerate(tiles):
        sheet.paste(t.resize((W // 2, H // 2)), ((k % cols) * W // 2, (k // cols) * H // 2))
    sheet.save(out / f"{args.clouds[0]}-sheet.jpg", quality=85)
    print(f"{len(tiles)} sections -> {out.relative_to(crs.ROOT)}")


