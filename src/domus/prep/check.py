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
- through: in the built model, ground that comes through a wall option's walls and flights of steps (and the
  existing walls it keeps), or stands over a tread's edge more than a skin's thickness: the step looks sunk.
- fixes: ground-fix points a wall option keeps as breaklines inside one of its walls, near its top or above it,
  where they show through the wall.
- blocks: block walls set out in whole units: each course of each straight stretch starts and ends with a whole
  block or half of one, the top course of a bay ends at a step down with a whole block, and the caps are whole.
  The sizes are each wall's own block's (`unit_m`).

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
from scipy.spatial import cKDTree
from shapely.geometry import LineString, Polygon

from .. import canopy, crs, features, geojson, house, mesh, raster, terrain, vegetation, walls
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
        hfc = geojson.read(ROOT / "data" / "site" / "house.geojson")["features"]
        self.hf = {f["properties"]["id"]: f for f in hfc}
        self.parts = lambda *kinds: [f for f in hfc if f["properties"].get("part") in kinds]
        h = cfg["house"]   # the checks are of a site with a house
        self.hm = house.build(hfc, h["fl_upper"], h["roof_pitch_deg"], h["roof_plane_at_wall"],
                              h["eaves_overhang_m"], self.ground, **house.roof_options(h))
        self.ring = np.asarray(self.hf["house-walls"]["geometry"]["coordinates"][0], float)[:-1, :2]
        self.house_poly = Polygon(self.ring)
        self.site_features = features.read_all(ROOT)
        self.feature_meshes = {}
        for items in features.build(self.site_features, self.ground).values():
            for m, _, p in items:
                self.feature_meshes.setdefault(p["id"], []).append(m)
        self.veg = canopy.read()
        lot = geojson.read(ROOT / "data" / "site" / "boundary.geojson")["features"][0]
        self.lot = Polygon(lot["geometry"]["coordinates"][0])

    def tin_z(self, e, n):
        """Height of the drawn ground (NaN where it has no triangle), read in the triangles the build drew."""
        return mesh.surface_z(self.tin.vertices, self.tin.faces, e, n)

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
    e, n, z = (a.ravel() for a in raster.dsm().cells())
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



# --------------------------------------------------------------------------- the wall options

TOL_THROUGH = 0.004      # m: ground over a wall's or a step's top
# A tread's edges: (how far out, how much the ground may stand above the tread there). Ground may rise away from a
# tread, as a lawn beyond a flight's head does, and a lawn's or bed's skin is drawn 12 mm proud of its ground.
BESIDE_TREAD = ((0.03, 0.015), (0.08, 0.03))
TOL_FIX_UNDER_TOP = 0.02  # m: a fix point this near a wall's top, or above it, shows through it
TOL_BLOCK = 0.011         # blocks: a piece this near a whole or half block is one


def _triangles(layer):
    """A mesh layer's triangles in viewer metres, (n, 3 corners, x y z); a lawn left as it is keeps them in `drape`."""
    src = layer.get("drape", layer)
    p = np.asarray(src["positions"], float).reshape(-1, 3)
    return p[np.asarray(src["indices"], int).reshape(-1, 3)]


def _height(tri, x, z):
    """The highest of the triangles `tri` (viewer metres) over each point (x, z): -inf where none is."""
    if not len(tri) or not len(x):
        return np.full(len(x), -np.inf)
    v = tri.reshape(-1, 3)[:, [0, 2, 1]]                # plan x, z and the height up
    h = mesh.surface_z(v, np.arange(len(v)).reshape(-1, 3), x, z)
    return np.where(np.isnan(h), -np.inf, h)


def _level_faces(tri):
    """The triangles that look straight up or down."""
    n = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    size = np.linalg.norm(n, axis=1)
    return tri[(size > 1e-9) & (np.abs(n[:, 1]) > 0.87 * size)]


def _top_points(tri, step=0.04):
    """Points over the level faces of `tri`, a little in from their edges."""
    out = []
    for a, b, c in _level_faces(tri):
        k = max(2, int(np.ceil(np.sqrt(np.linalg.norm(np.cross(b - a, c - a))) / step)))
        for i in range(k + 1):
            for j in range(k + 1 - i):
                u, v = (i + 0.33) / (k + 1), (j + 0.33) / (k + 1)
                if u + v < 0.98:
                    out.append(a + u * (b - a) + v * (c - a))
    return np.asarray(out, float).reshape(-1, 3)


def _beside_points(tri, step=0.05):
    """Points just outside each edge of each level face of `tri`, at the face's height, with how far out they are."""
    out = []
    for face in _level_faces(tri):
        mid = face.mean(axis=0)
        for i in range(3):
            a, b = face[i], face[(i + 1) % 3]
            e = b - a
            length = float(np.hypot(e[0], e[2]))
            if length < 1e-6:
                continue
            nrm = np.array([e[2], 0.0, -e[0]]) / length
            if np.dot(nrm, a - mid) < 0:
                nrm = -nrm
            for s in np.arange(step / 2, length, step):
                for d, _ in BESIDE_TREAD:
                    out.append([*(a + e * (s / length) + nrm * d), d])
    return np.asarray(out, float).reshape(-1, 4)


def _where(model, point):
    """Where a point in viewer metres is, for a finding: in the options' frame, and its height."""
    e0, n0, h0 = model["origin"]
    e, n = point[0] + e0, n0 - point[2]
    frame = (model.get("summary", {}).get("options_site") or {}).get("frame")
    if frame:
        from ..design import SiteFrame

        s, o = SiteFrame(frame["origin"], frame["toward"], frame.get("side", "right")).so(np.array([e, n]))
        return f"s {s:.2f}, o {o:.2f}, height {point[1] + h0:.2f}"
    return f"E {e:.2f}, N {n:.2f}, height {point[1] + h0:.2f}"


def check_through(findings, tol=TOL_THROUGH):
    """Ground that comes through an option's walls and flights, or stands over a tread's edge, in the built model."""
    path = crs.OUTPUT / "model.json"
    if not path.exists():
        findings.append(("through", "info", "model", "no model built yet: run domus build first"))
        return 0
    model = json.loads(path.read_text(encoding="utf-8"))
    layers = model["layers"]
    by_id = {x.get("id"): x for x in layers}
    fails = 0
    for o in model.get("summary", {}).get("options", []):
        oid = o["id"]
        mine = [x for x in layers if x.get("option") == oid and x.get("type") in ("mesh", "vegetation")
                and ("positions" in x or "drape" in x)]
        ground = [(x["id"].replace(f"option-{oid}-", ""), _triangles(x)) for x in mine if x.get("role") in ("ground", "skin")]
        solids = [x for x in mine if x.get("role") not in ("ground", "skin") and "positions" in x]
        solids += [by_id[f"wall-{w}"] for w in o.get("keep_existing", []) if f"wall-{w}" in by_id]
        solid = [(x["id"].replace(f"option-{oid}-", ""), _triangles(x)) for x in solids]
        tops = [_level_faces(t) for _, t in solid]
        every_top = np.vstack(tops) if tops else np.zeros((0, 3, 3))
        for name, t in solid:
            p = _top_points(t)
            if not len(p):
                continue
            p = p[p[:, 1] >= _height(_level_faces(t), p[:, 0], p[:, 2]) - 1e-6]      # its top, not an underside
            for gname, g in ground:
                over = _height(g, p[:, 0], p[:, 2]) - p[:, 1]
                bad = over > tol
                if bad.any():
                    k = int(np.argmax(over))
                    fails += 1
                    findings.append(("through", "fail", f"{oid}/{name}", (f"{gname} comes through at {int(bad.sum())} of "
                                     f"{len(p)} points, by up to {over[k]:.3f} m ({_where(model, p[k])})")))
            if "steps" not in name:
                continue
            lv = _level_faces(t)
            mids = lv.mean(axis=1)
            treads = lv[mids[:, 1] >= _height(lv, mids[:, 0], mids[:, 2]) - 1e-6]
            q = _beside_points(treads)
            if len(q):
                q = q[~np.isfinite(_height(every_top, q[:, 0], q[:, 2]))]          # open ground beside the tread
            for gname, g in ground:
                if not len(q):
                    break
                allowed = np.interp(q[:, 3], [d for d, _ in BESIDE_TREAD], [a for _, a in BESIDE_TREAD])
                over = _height(g, q[:, 0], q[:, 2]) - q[:, 1] - allowed
                bad = over > 0
                if bad.any():
                    k = int(np.argmax(over))
                    fails += 1
                    findings.append(("through", "fail", f"{oid}/{name}", (f"{gname} stands over a tread's edge at "
                                     f"{int(bad.sum())} of {len(q)} points, by up to {over[k]:.3f} m more than allowed "
                                     f"({_where(model, q[k, :3])})")))
    return fails


def check_fixes(findings):
    """Ground-fix points an option keeps (where it leaves the ground as it is) that stand inside one of its walls,
    near its top or above it: the build keeps them as breaklines, so they show through the wall."""
    from .. import design, options

    g = terrain.ground()
    if not len(g.points):
        return 0
    fr, fails = design.frame(), 0
    for opt in options.load_all():
        res = design.evaluate(opt)
        pts = g.points
        kept = pts[np.abs(res["design"].drawn(pts[:, 0], pts[:, 1]) - pts[:, 2]) < 0.01]
        for w in res["walls"]:
            wd = w["def"]
            line, _, _, _, top = terrain.wall_levels(wd)
            body = Polygon(np.vstack([line, mesh.offset_behind(line, wd["thickness"], wd["side"])[::-1]])).buffer(0.03)
            for p in kept[shapely.contains_xy(body, kept[:, 0], kept[:, 1])]:
                k = int(np.argmin(np.hypot(line[:, 0] - p[0], line[:, 1] - p[1])))
                over = float(p[2] - top[k])
                if over > -TOL_FIX_UNDER_TOP:
                    s, o = fr.so(p[None, :2])[0]
                    fails += 1
                    findings.append(("fixes", "fail", f"{opt['id']}/{w['id']}", (f"a ground-fix point at s {s:.2f}, o {o:.2f} "
                                     f"is {p[2]:.2f}, the wall's top there {top[k]:.2f} ({over:+.2f} m): move or drop it")))
    return fails


def _piece(u, parity, before):
    """The length, in blocks, of the piece of a course that ends (`before`) or starts at `u` along a stretch."""
    v = round((u - 0.5 * parity) % 1.0, 6) % 1.0
    if v < TOL_BLOCK or v > 1 - TOL_BLOCK:
        return 1.0
    return v if before else 1.0 - v


def _cut(x):
    return not (abs(x - 1.0) < TOL_BLOCK or abs(x - 0.5) < TOL_BLOCK)


def _cut_piece(findings, where, metres, at):
    findings.append(("blocks", "fail", where, f"a course has a cut piece {metres * 1000:.0f} mm long {at}"))
    return 1


def check_blocks(findings):
    """Block walls set out in whole units: every course of each stretch starts and ends with a whole block or half
    of one (the end of a corner unit), the top course of a bay ends at a step down with a whole block, and the caps
    are whole units. Each wall's block (`unit_m`) gives the sizes."""
    from .. import design, options

    fr, fails = design.frame(), 0
    for opt in options.load_all():
        res = design.evaluate(opt)
        for w in res["walls"]:
            d = w["def"]
            unit = walls.units(d)
            if not unit or "set_out" not in d:
                continue
            length, height, cap, _ = unit
            so = d["set_out"]
            pts, _, chain, _, top = terrain.wall_levels(d)
            cuts, u0, rate = so["cuts"], so["u0"], so["rate"]
            where = f"{opt['id']}/{w['id']}"
            for k in range(len(u0)):
                c0, c1 = cuts[k], cuts[k + 1]
                for h0, h1 in so.get("hidden", []):        # a length inside another wall's face, which that wall draws
                    if h0 <= c0 + 1e-6 < h1:
                        c0 = h1
                    if h0 < c1 - 1e-6 <= h1:
                        c1 = h0
                ua, ub = (u0[k] + rate[k] * (c - cuts[k]) for c in (c0, c1))
                s, o = fr.so(pts[int(np.argmin(np.abs(chain - c0)))][None, :2])[0]
                for p in (0, 1):
                    if _cut(_piece(ua, p, False)):
                        fails += _cut_piece(findings, where, _piece(ua, p, False) * length, f"at the start of stretch {k} (s {s:.2f}, o {o:.2f})")
                    if _cut(_piece(ub, p, True)):
                        fails += _cut_piece(findings, where, _piece(ub, p, True) * length, f"at the end of stretch {k}")
                blocks = abs(ub - ua)
                if abs(blocks * 2 - round(blocks * 2)) > 2 * TOL_BLOCK:
                    fails += 1
                    findings.append(("blocks", "fail", where, (f"stretch {k} is {blocks * length * 1000:.0f} mm, not a whole "
                                     "number of half blocks")))
            for i in range(len(chain) - 1):                # a step in the top: the chain has its point twice
                if abs(chain[i + 1] - chain[i]) < 1e-9 and abs(top[i + 1] - top[i]) > 1e-6:
                    hi, lo = max(top[i], top[i + 1]), min(top[i], top[i + 1])
                    down_along = top[i] > top[i + 1]
                    side = chain[i] - 1e-6 if down_along else chain[i] + 1e-6
                    k = int(np.clip(np.searchsorted(cuts, side, side="right") - 1, 0, len(u0) - 1))
                    u = u0[k] + rate[k] * (chain[i] - cuts[k])
                    level = hi - cap
                    while level > lo - cap + 1e-6:
                        parity = int(round((so["datum"] - level) / height)) % 2
                        x = _piece(u, parity, before=bool(down_along) == (rate[k] > 0))
                        if abs(x - 1.0) >= TOL_BLOCK:
                            fails += _cut_piece(findings, where, x * length,
                                                f"at the step from {hi:.2f} down to {lo:.2f}, in the course with its top at {level:.2f}")
                        level -= height
            for c0, c1, joints in so.get("caps") or []:
                sizes = np.diff(joints)
                odd = [s for s in sizes if abs(s - length) > 0.01 * length]
                if odd:
                    fails += 1
                    findings.append(("blocks", "fail", where, (f"the cap from {c0:.2f} to {c1:.2f} m along it has "
                                     f"{len(odd)} cut units: ") + ", ".join(f"{s * 1000:.0f} mm" for s in odd)))
    return fails


# --------------------------------------------------------------------------- main

def placed_tags():
    return sorted(p.name[: -len("-georef.json")] for p in VID.glob("*-georef.json")
                  if (VID / p.name.replace("-georef.json", "-sfm.npz")).exists()
                  and (VID / p.name.replace("-georef.json", "-site.npz")).exists())


CHECKS = ("sanity", "roof", "plumb", "clouds", "through", "fixes", "blocks")


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
    for name, check in (("through", check_through), ("fixes", check_fixes), ("blocks", check_blocks)):
        if name in checks:
            fails[name] = check(findings)
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


