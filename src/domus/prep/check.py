"""Adversarial checks of a site's model against its own data, the LiDAR and the placed videos.

    domus prep check                      # all checks
    domus prep check sanity roof          # some of them
    domus prep check plumb --tags walk-colmap
    domus prep check --plots              # also write JPEGs

Checks:
- sanity: object bases against the ground the viewer draws (the TIN with the walls cut in);
  stumps whose base_m disagrees with that ground; spikes, cliffs and holes in the ground in and
  near the lot; walls whose top is below the ground just behind them; ids used twice; geometry
  outside the model extent.
- roof: the hip roof (data/site.toml over data/site/house.geojson) against the LiDAR surface
  model at the 1 m cells wholly inside the eaves, and the plan shift of the house that fits it best.
- plumb: the tilt of each placed reconstruction (data/video/<tag>-georef.json), from the vertical
  vanishing point of straight vertical edges (house corners, door frames, posts) in its own
  frames, carried into the reconstruction by its camera rotations. No control is involved, so a
  placement whose verticals lean is tilted: ground far from its control comes out too high on one
  side and too low on the other (about 1.7 cm per metre per degree).
- clouds: each placed cloud's lowest flat surface per 0.25 m cell against the drawn ground on open
  ground (not under modelled objects or vegetation), and its house-wall points against each face.

Prints the findings, writes docs/checks/adversarial/check_model.json (and with --plots, JPEGs in
the same folder) and exits 1 if a check fails its tolerance.
"""

import argparse
import itertools
import json
import math
import sys
import time
from collections import Counter

import cv2
import numpy as np
import shapely
from PIL import Image, ImageDraw
from scipy.spatial import Delaunay, cKDTree
from shapely.geometry import LineString, Polygon

from .. import crs, features, geojson, house, raster, terrain, vegetation, walls
from ..crs import ROOT
from ..sfm_georef import load
from ..video.georef_sfm import consensus_up, frame_path, frame_ups, tilt_of

VID = ROOT / "data" / "video"
OUT = ROOT / "docs" / "checks" / "adversarial"

TOL_BASE = 0.10          # m, a base off the drawn ground
TOL_ROOF_SHIFT = 0.30    # m, best-fit plan shift of the roof against the DSM
TOL_PLANES_DEVIATION = 0.2
TOL_TILT = 1.5           # degrees, a placement's tilt (when its frames agree within 1 degree)
TOL_GROUND = 0.15        # m, open-ground cells of a placed cloud off the drawn ground


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# --------------------------------------------------------------------------- the model's geometry

class Model:
    """The geometry the build draws, as plain meshes in site coordinates."""

    def __init__(self):
        cfg = crs.settings()
        self.cfg = cfg
        self.extent = cfg["model"]["extent"]
        g = terrain.ground()
        self.ground = lambda e, n: g.sample(np.atleast_1d(e), np.atleast_1d(n))
        wall_feats = geojson.read_optional(ROOT / "data" / "site" / "walls-existing.geojson")
        self.walls = [walls.from_feature(f) for f in wall_feats]
        self.tin = terrain.build(self.extent, cfg["model"]["terrain_step_m"], self.walls)
        self._mid = self.tin.vertices[:, :2].mean(axis=0)          # triangulated about its middle, as terrain.build does
        self._tri = Delaunay(self.tin.vertices[:, :2] - self._mid)
        kept = set(map(tuple, np.sort(self.tin.faces, axis=1)))
        self._ok = np.array([tuple(s) in kept for s in np.sort(self._tri.simplices, axis=1)])
        hfc = geojson.read(ROOT / "data" / "site" / "house.geojson")["features"]
        self.hf = {f["properties"]["id"]: f for f in hfc}
        self.parts = lambda *kinds: [f for f in hfc if f["properties"].get("part") in kinds]
        h = cfg["house"]   # the checks are of a site with a house
        self.hm = house.build(hfc, h["fl_upper"], h["roof_pitch_deg"], h["roof_plane_at_wall"],
                              h["eaves_overhang_m"], self.ground, **house.roof_options(h))
        self.ring = np.asarray(self.hf["house-walls"]["geometry"]["coordinates"][0], float)[:-1, :2]
        self.house_poly = Polygon(self.ring)
        self.site_features = features.read_all()
        self.feature_meshes = {}
        for items in features.build(self.site_features, self.ground).values():
            for m, _, p in items:
                self.feature_meshes.setdefault(p["id"], []).append(m)
        self.veg = vegetation.read()
        lot = geojson.read(ROOT / "data" / "site" / "boundary.geojson")["features"][0]
        self.lot = Polygon(lot["geometry"]["coordinates"][0])

    def tin_z(self, e, n):
        """Height of the drawn ground (NaN where it has no triangle)."""
        p = np.column_stack([np.ravel(e), np.ravel(n)]).astype(float) - self._mid
        s = self._tri.find_simplex(p)
        out = np.full(len(p), np.nan)
        ok = s >= 0
        ok[ok] = self._ok[s[ok]]
        t = self._tri.transform[s[ok]]
        b = np.einsum("ijk,ik->ij", t[:, :2, :], p[ok] - t[:, 2, :])
        bary = np.column_stack([b, 1 - b.sum(1)])
        out[ok] = (bary * self.tin.vertices[self._tri.simplices[s[ok]], 2]).sum(1)
        return out.reshape(np.shape(e))

    def raised_footprint(self):
        """Plan area under modelled objects and vegetation, where a camera need not see ground."""
        polys = [self.house_poly.buffer(0.4)]
        for p in self.hm.solids.values():
            for m, _ in p:
                polys.append(shapely.MultiPoint(m.vertices[:, :2]).convex_hull.buffer(0.1))
        for ms in self.feature_meshes.values():
            for m in ms:
                if np.nanmax(m.vertices[:, 2] - self.ground(m.vertices[:, 0], m.vertices[:, 1])) > 0.15:
                    polys.append(shapely.MultiPoint(m.vertices[:, :2]).convex_hull.buffer(0.1))
        for w in self.walls:
            polys.append(LineString(w["line"]).buffer(w["thickness"] + 0.3))
        for f in self.veg:
            p = f["properties"]
            low_tree = p.get("kind") == "tree" and p.get("crown_base_m", 1.5) < 1.0
            low = p.get("kind") in ("shrub", "hedge", "bed") or low_tree
            if low and f["geometry"]["type"] == "Polygon":
                polys.append(Polygon(f["geometry"]["coordinates"][0]))
        return shapely.union_all(polys)


# --------------------------------------------------------------------------- sanity

def check_sanity(m, findings):
    fails = 0
    e0, n0, e1, n1 = m.extent
    box = shapely.box(e0, n0, e1, n1)
    for f in m.site_features + m.veg:
        if not box.contains(shapely.geometry.shape(f["geometry"])):
            findings.append(("sanity", "extent", f["properties"]["id"], "runs outside the model extent"))
    # feature bases: the lowest vertex over all of a feature's parts, against the drawn ground there
    for f in m.site_features:
        p = f["properties"]
        ms = m.feature_meshes.get(p["id"])
        if not ms:
            continue
        v = np.vstack([q.vertices for q in ms])
        low = v[v[:, 2] <= v[:, 2].min() + 0.02]
        gap = float(np.nanmax(low[:, 2] - m.tin_z(low[:, 0], low[:, 1])))
        if gap > TOL_BASE:
            on_purpose = "base_m" in p or p.get("kind") == "clothesline"   # on a wall, a post, a tank, a fence
            msg = f"lowest part {gap:.2f} m above the drawn ground" + (" (set so on purpose)" if on_purpose else "")
            findings.append(("sanity", "info" if on_purpose else "floating", p["id"], msg))
            fails += not on_purpose
    # stumps: base_m against the drawn ground (the build lifts a stump to the ground when it is lower)
    for s in vegetation.stump_items(m.veg, m.ground):
        e, n, base = s["at"]
        z = float(m.tin_z(np.array([e]), np.array([n]))[0])
        given = s["props"].get("base_m")
        if given is not None and abs(given - z) > TOL_BASE:
            msg = f"base_m {given:.2f}, drawn ground {z:.2f} ({given - z:+.2f} m); drawn at {base:.2f}"
            findings.append(("sanity", "stump", s["id"], msg))
            fails += 1
    # ground: steep triangles and spikes in and near the lot, away from walls and the house
    v, f_ = m.tin.vertices, m.tin.faces
    a, b, c = v[f_[:, 0]], v[f_[:, 1]], v[f_[:, 2]]
    nrm = np.cross(b - a, c - a)
    slope = np.degrees(np.arccos(np.abs(nrm[:, 2]) / np.linalg.norm(nrm, axis=1)))
    cen = (a + b + c) / 3
    away = np.ones(len(f_), bool)
    for w in m.walls:
        away &= shapely.distance(LineString(w["line"]), shapely.points(cen[:, :2])) > w["thickness"] + 0.6
    away &= ~shapely.contains_xy(m.house_poly.buffer(0.6), cen[:, 0], cen[:, 1])
    for f in m.parts("block"):         # a landing below the ground (`pit`), such as a stairwell, is a pit on purpose
        if f["properties"].get("pit") and f["geometry"]["type"] == "Polygon":
            well = Polygon(f["geometry"]["coordinates"][0]).buffer(0.4)
            away &= ~shapely.contains_xy(well, cen[:, 0], cen[:, 1])
    near_lot = shapely.contains_xy(m.lot.buffer(2.0), cen[:, 0], cen[:, 1])
    steep = (slope > 55) & away & near_lot
    if steep.any():
        # group them by 1 m cells into clusters
        pts = cen[steep]
        keys = np.floor(pts[:, :2]).astype(int)
        uniq, inv = np.unique(keys, axis=0, return_inverse=True)
        groups = sorted(((np.sum(inv.ravel() == k), uniq[k]) for k in range(len(uniq))), key=lambda t: -t[0])
        where = ", ".join(f"{n} at ({u[0] + 0.5:.1f}, {u[1] + 0.5:.1f})" for n, u in groups[:6])
        msg = (f"{steep.sum()} triangles steeper than 55 degrees away from walls and the house, "
               f"by 1 m cell: {where}")
        findings.append(("sanity", "ground", "TIN", msg))
        fails += 1
    x0, y0, x1, y1 = m.lot.bounds
    lot_grid = np.meshgrid(np.arange(x0, x1, 0.25), np.arange(y0, y1, 0.25))
    inside = shapely.contains_xy(m.lot, *lot_grid) & ~shapely.contains_xy(m.house_poly, *lot_grid)
    holes = np.isnan(m.tin_z(lot_grid[0][inside], lot_grid[1][inside]))
    if holes.any():
        findings.append(("sanity", "ground", "TIN", f"{holes.sum()} points of a 0.25 m grid in the lot have no ground"))
        fails += 1
    # walls: ground just behind the top
    for w in m.walls:
        pts, n2, chain, _toe, top = terrain.wall_levels(w)
        behind = pts + n2 * (w["thickness"] + 0.35)
        over = m.tin_z(behind[:, 0], behind[:, 1]) - top
        if np.nanmax(over) > 0.05:
            k = int(np.nanargmax(over))
            msg = (f"drawn ground 0.35 m behind it is {over[k]:.2f} m above its top at {chain[k]:.1f} m along it "
                   f"(top {top[k]:.2f})")
            findings.append(("sanity", "wall", w["id"], msg))
            fails += 1
    # ids
    ids = Counter(f["properties"]["id"] for f in m.site_features + m.veg)
    for name in ("services", "house"):
        ids.update(f["properties"]["id"] for f in geojson.read_optional(ROOT / "data" / "site" / f"{name}.geojson"))
    for k, n in ids.items():
        if n > 1:
            findings.append(("sanity", "duplicate", k, f"id used {n} times"))
            fails += 1
    return fails


# --------------------------------------------------------------------------- roof

def dsm_cells(window):
    d = raster.dsm()
    tr = d.transform
    rows, cols = d.data[0].shape
    cc, rr = np.meshgrid(np.arange(cols) + 0.5, np.arange(rows) + 0.5)
    e, n = crs.nztm_to_site((tr.c + cc * tr.a).ravel(), (tr.f + rr * tr.e).ravel())
    e, n, z = np.asarray(e), np.asarray(n), d.data[0].ravel()
    e0, n0, e1, n1 = window
    k = (e > e0) & (e < e1) & (n > n0) & (n < n1) & np.isfinite(z)
    return e[k], n[k], z[k]


def roof_failure(form, best, deviation):
    """What's wrong with the roof's fit, or None. Moving a plane only raises or lowers it, which the fit already takes out, so a skillion says nothing about where
    the house is, and a gable only across its ridge: there the test is how far the LiDAR stands off the roof."""
    if form != "hip":
        return f"the LiDAR roof stands {deviation:.2f} m off the model's on average" if deviation > TOL_PLANES_DEVIATION else None
    shift = math.hypot(*best)
    return f"the LiDAR roof fits best with the house moved {shift:.2f} m" if shift > TOL_ROOF_SHIFT else None


def check_roof(m, findings, plots):
    h = m.cfg["house"]
    fr = house.HouseFrame(m.ring)
    walls_l = Polygon(fr.local(m.ring))
    eaves_l = walls_l.buffer(h["eaves_overhang_m"], join_style=2)
    surface = house.roof_surface(fr, fr.local(m.ring), h)
    eaves = Polygon(fr.site(np.asarray(eaves_l.exterior.coords)))
    x0, y0, x1, y1 = eaves.bounds
    e, n, z = dsm_cells((x0 - 2, y0 - 2, x1 + 2, y1 + 2))
    excl = shapely.union_all([Polygon(f["geometry"]["coordinates"][0]).buffer(b)
                              for kind, b in (("chimney", 1.0), ("porch", 0.8), ("bay", 0.8)) for f in m.parts(kind)])
    sel = shapely.contains_xy(eaves.buffer(-0.72), e, n) & ~shapely.contains_xy(excl, e, n)
    e, n, z = e[sel], n[sel], z[sel]

    def roof(dx, dy):
        loc = fr.local(np.column_stack([e - dx, n - dy]))
        return surface(loc[:, 0], loc[:, 1])

    def cost(dx, dy):
        r = z - roof(dx, dy)
        r = r[np.isfinite(r)]
        return float(np.mean(np.abs(r - np.median(r)))) if len(r) > 0.8 * len(z) else np.inf
    shifts = np.arange(-0.8, 0.801, 0.05)
    grid = np.array([[cost(dx, dy) for dx in shifts] for dy in shifts])
    i, j = np.unravel_index(np.argmin(grid), grid.shape)
    res = z - roof(0, 0)
    med = float(np.median(res))
    best = (float(shifts[j]), float(shifts[i]))
    at_zero = grid[len(shifts) // 2, len(shifts) // 2]
    msg = (f"{len(z)} DSM cells inside the eaves: residual median {med:+.3f} m, mean absolute deviation "
           f"{at_zero:.3f} m; best plan shift of the house E {best[0]:+.2f} N {best[1]:+.2f} m "
           f"(deviation {grid[i, j]:.3f} m)")
    findings.append(("roof", "info", "house", msg))
    failure = roof_failure(h.get("roof_form", "hip"), best, at_zero)
    fails = int(failure is not None)
    if fails:
        findings.append(("roof", "fail", "house", failure))
    if plots:
        img = PlanImage((x0 - 1, y0 - 1, x1 + 1, y1 + 1), 0.04)
        img.cells(e, n, diverging(res - med, 0.3), 1.0)
        img.ring(np.asarray(eaves.exterior.coords), (60, 60, 60))
        img.ring(np.vstack([m.ring, m.ring[:1]]), (200, 30, 30))
        img.title(f"LiDAR DSM - model roof at the DSM's 1 m cells (blue -0.3 .. red +0.3 m, less the median); "
                  f"best shift E {best[0]:+.2f} N {best[1]:+.2f}")
        img.save(OUT / "check-roof-vs-dsm.jpg")
    return fails


# --------------------------------------------------------------------------- plumb

def placement_tilt(tag):
    """(tilt deg, bearing deg the true up leans toward in the placed frame, frames used, frame spread),
    from every registered frame, with the vertical vanishing-point code domus.video.georef_sfm holds
    placements by."""
    rec = load(VID / f"{tag}-sfm.npz")
    params = np.array(json.loads((VID / f"{tag}-georef.json").read_text(encoding="utf-8"))["params"])
    frames = [(n, None) for n, rv in zip(rec["names"], rec["rvecs"]) if np.all(np.isfinite(rv))]
    ups = frame_ups(rec, frames, lambda n: cv2.imread(str(frame_path(tag, n)), cv2.IMREAD_GRAYSCALE))
    if len(ups) < 3:
        return None
    m, keep, off = consensus_up([u for _, u in ups])
    tilt, bearing = tilt_of(params, m)
    return tilt, bearing, int(keep.sum()), float(np.median(off[keep]))


def check_plumb(tags, findings):
    fails = 0
    for tag in tags:
        r = placement_tilt(tag)
        if r is None:
            findings.append(("plumb", "info", tag, "too few frames with vertical edges"))
            continue
        tilt, bearing, n, spread = r
        # frames that agree make a firm verdict; when they scatter (frames of foliage or a drain
        # grate whose slots pass for verticals) a large tilt is still worth a look by hand
        level = "info" if tilt <= TOL_TILT else "fail" if spread < 1.0 else "warn"
        fails += level == "fail"
        msg = (f"tilted {tilt:.1f} degrees: true vertical leans toward {bearing:.0f} degrees in the placed cloud, "
               f"so level ground falls that way ({n} frames, spread {spread:.2f} degrees)")
        findings.append(("plumb", level, tag, msg))
    return fails


# --------------------------------------------------------------------------- clouds

def check_clouds(m, tags, findings, plots):
    fails = 0
    raised = m.raised_footprint()
    planes = [(a, b) for a, b in house.outward_edges(m.ring)]
    fronts = shapely.union_all([Polygon(f["geometry"]["coordinates"][0]).buffer(0.35)
                                for f in m.parts("porch", "bay", "steps") if f["geometry"]["type"] == "Polygon"])
    names = m.hf["house-walls"]["properties"].get("faces") or [f"wall-{i}" for i in range(len(planes))]
    for tag in tags:
        for kind in ("dense", "site"):
            path = VID / f"{tag}-{kind}.npz"
            if not path.exists():
                continue
            d = np.load(path)
            sup = d["pairs"] if "pairs" in d else d["nview"]
            xyz = d["xyz"][sup >= (2 if kind == "dense" else 3)]
            box = m.cfg.get("video", {}).get("keep", {}).get(f"{tag}-{kind}")
            if box:
                xyz = xyz[(xyz[:, 0] > box[0]) & (xyz[:, 1] > box[1]) & (xyz[:, 0] < box[2]) & (xyz[:, 1] < box[3])]
            cams = np.load(VID / f"{tag}-site.npz")["cameras"]
            xyz = xyz[cKDTree(cams).query(xyz)[0] < 6.0]
            if len(xyz) < 200:
                continue
            tree = cKDTree(xyz)
            _, idx = tree.query(xyz, k=min(12, len(xyz)))
            nb = xyz[idx] - xyz[idx].mean(1, keepdims=True)
            nz = np.abs(np.linalg.eigh(np.einsum("nki,nkj->nij", nb, nb))[1][:, 2, 0])
            flat = xyz[nz > 0.8]
            key = np.floor(flat[:, :2] / 0.25).astype(np.int64)
            order = np.lexsort((flat[:, 2], key[:, 1], key[:, 0]))
            key, flat = key[order], flat[order]
            brk = np.r_[0, np.flatnonzero(np.any(key[1:] != key[:-1], axis=1)) + 1, len(key)]
            cells = []
            for a, b in itertools.pairwise(brk):
                if b - a >= 6:
                    cells.append(((key[a, 0] + 0.5) * 0.25, (key[a, 1] + 0.5) * 0.25,
                                  float(np.median(flat[a:a + max((b - a) // 5, 1) + 1, 2]))))
            if not cells:
                continue
            c = np.array(cells)
            r = c[:, 2] - m.tin_z(c[:, 0], c[:, 1])
            open_ = np.isfinite(r) & ~shapely.contains_xy(raised, c[:, 0], c[:, 1])
            if open_.sum() >= 10:
                share = float(np.mean(np.abs(r[open_]) > TOL_GROUND))
                level = "fail" if share > 0.5 or abs(np.median(r[open_])) > 0.1 else "info"
                fails += level == "fail"
                msg = (f"open ground: {open_.sum()} cells, median {np.median(r[open_]):+.3f} m, "
                       f"{share:.0%} beyond {TOL_GROUND} m")
                findings.append(("clouds", level, f"{tag}-{kind}", msg))
            # house walls
            soffit = m.hf["house-walls"]["properties"]["soffit"]
            g = m.tin_z(xyz[:, 0], xyz[:, 1])
            out_front = ~shapely.contains_xy(fronts, xyz[:, 0], xyz[:, 1])    # not on the porch or bay
            rows = []
            for name, (a, b) in zip(names, planes):
                L = float(np.hypot(*(b - a)))
                u = (b - a) / L
                out_n = np.array([u[1], -u[0]])
                rel = xyz[:, :2] - a
                t, o = rel @ u, rel @ out_n
                k = (t > 0.2) & (t < L - 0.2) & (np.abs(o) < 0.5) & (xyz[:, 2] > g + 0.3) & (xyz[:, 2] < soffit - 0.1)
                k &= out_front
                if k.sum() >= 50:
                    rows.append(f"{name} {np.median(o[k]):+.2f} ({k.sum()})")
            if rows:
                msg = "house wall offsets, + outside: " + ", ".join(rows)
                findings.append(("clouds", "info", f"{tag}-{kind}", msg))
            if plots and kind == "dense" and open_.sum() >= 10:
                e0, n0 = c[:, 0].min() - 1, c[:, 1].min() - 1
                img = PlanImage((e0, n0, c[:, 0].max() + 1, c[:, 1].max() + 1), 0.03)
                img.cells(c[open_, 0], c[open_, 1], diverging(r[open_], 0.3), 0.25)
                img.ring(np.vstack([m.ring, m.ring[:1]]), (200, 30, 30))
                img.title(f"{tag}: cloud's lowest flat surface - drawn ground, open cells (blue -0.3 .. red +0.3 m)")
                img.save(OUT / f"check-cloud-{tag}.jpg")
    return fails


# --------------------------------------------------------------------------- plots

def diverging(v, vmax):
    t = np.clip(np.asarray(v, float) / vmax, -1, 1)[:, None]
    lo, mid, hi = np.array([33, 102, 172.0]), np.array([240, 240, 240.0]), np.array([178, 24, 43.0])
    return np.where(t < 0, mid + (lo - mid) * -t, mid + (hi - mid) * t).astype(np.uint8)


class PlanImage:
    """A north-up plan over the greyed aerial."""

    def __init__(self, window, px):
        self.e0, self.n0, self.e1, self.n1 = window
        self.px = px
        w, h = int((self.e1 - self.e0) / px), int((self.n1 - self.n0) / px)
        e, n = np.meshgrid(self.e0 + (np.arange(w) + 0.5) * px, self.n1 - (np.arange(h) + 0.5) * px)
        a = raster.aerial()
        rgb = np.nan_to_num(np.stack([a.sample(e, n, band=b) for b in range(3)], -1), nan=200.0)
        rgb = 0.5 * rgb + 0.5 * (rgb.mean(-1, keepdims=True) * 0.6 + 100)
        self.img = Image.new("RGB", (w, h + 22), (255, 255, 255))
        self.img.paste(Image.fromarray(rgb.clip(0, 255).astype(np.uint8)), (0, 22))
        self.dr = ImageDraw.Draw(self.img)

    def p(self, e, n):
        return (e - self.e0) / self.px, (self.n1 - n) / self.px + 22

    def cells(self, e, n, colours, size):
        for x, y, c in zip(e, n, colours):
            a, b = self.p(x - size / 2, y + size / 2), self.p(x + size / 2, y - size / 2)
            self.dr.rectangle([a, b], fill=tuple(int(v) for v in c))

    def ring(self, pts, colour):
        self.dr.line([self.p(*q[:2]) for q in pts], fill=colour, width=2)

    def title(self, text):
        self.dr.text((4, 4), text, fill=(0, 0, 0))

    def save(self, path):
        img = self.img
        if img.width > 1400:
            img = img.resize((1400, int(img.height * 1400 / img.width)), Image.LANCZOS)
        img.save(path, quality=82)


# --------------------------------------------------------------------------- main

def placed_tags():
    return sorted(p.name[: -len("-georef.json")] for p in VID.glob("*-georef.json")
                  if (VID / p.name.replace("-georef.json", "-sfm.npz")).exists()
                  and (VID / p.name.replace("-georef.json", "-site.npz")).exists())


CHECKS = ("sanity", "roof", "plumb", "clouds")


def run(checks=CHECKS, tags=None, plots=False):
    """Runs the checks and writes check_model.json. Returns its contents: `failures` (check: how many failed) and
    `findings` (each a dict of check, level, object and detail). `tags` are the placed reconstructions for plumb and
    clouds, all of them unless given; `plots` also writes JPEGs."""
    OUT.mkdir(parents=True, exist_ok=True)
    tags = tags or placed_tags()
    m = Model()
    findings, fails = [], {}
    if "sanity" in checks:
        fails["sanity"] = check_sanity(m, findings)
    if "roof" in checks:
        fails["roof"] = check_roof(m, findings, plots)
    if "plumb" in checks:
        fails["plumb"] = check_plumb(tags, findings)
    if "clouds" in checks:
        fails["clouds"] = check_clouds(m, tags, findings, plots)
    report = {"run": time.strftime("%Y-%m-%d %H:%M"), "failures": fails,
              "findings": [dict(zip(("check", "level", "object", "detail"), f)) for f in findings]}
    (OUT / "check_model.json").write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")
    return report


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("checks", nargs="*", default=list(CHECKS))
    ap.add_argument("--tags", nargs="*", help="placed reconstructions for plumb and clouds (default: all)")
    ap.add_argument("--plots", action="store_true")
    args = ap.parse_args()
    report = run(args.checks, args.tags, args.plots)
    for f in report["findings"]:
        print(f"{f['check']:7s} {f['level']:9s} {f['object']:40s} {f['detail']}")
    log(f"failures by check: {report['failures']}; wrote {OUT.relative_to(ROOT) / 'check_model.json'}")
    sys.exit(1 if any(report["failures"].values()) else 0)


