"""Small site features from the curated files data/site/features-*.geojson.

Fences and gates, sheds, boxes (bins, a log stack, letterboxes), pads and paths, posts and
poles, a clothes line, drains drawn as lines and point marks, built on the ground model and
textured with small images in data/features/ (cut from site photos and video frames by
tools/feature_textures.py). A feature's properties:
- `id`, unique across the files; `name`; `source` and `basis` (which frames, photos, aerial or
  LiDAR cells each value rests on) and `confidence`, as in the other curated layers; `note`.
- `kind`, with its geometry:
  - fence (LineString along the fence's face): `style` paling (palings on the line, rails and
    posts on the `rails` side, "left" or "right" of the line's direction, and a capping;
    `posts` false leaves the posts out),
    picket, post-and-rail (posts on the line, `rails_at` heights of the rail tops above the
    ground) or timber-edge (a low retaining edge: boards on the line, round posts on the
    `posts` side, "left", "right" or "none"); `post_spacing_m`;
  - gate (LineString from the hinge to the latch end): `style` picket (pickets on two rails),
    brace (the same with a diagonal brace) or paling (palings on rails with a brace);
  - shed (Polygon: front-left, front-right, back-right, back-left; the front has the doors):
    `front_height_m` and `back_height_m` to the top of the walls, `door_from_m` and
    `door_width_m` along the front, `overhang_m` of the mono-pitch roof;
  - building or box (Polygon): a prism; `taper_m` insets its top (a compost bin);
  - pad (Polygon): a surface draped on the ground (or flat at `top_m`); `thickness_m` raises
    it as a slab with sides (a shed's pad); `uv_bearing_deg` turns its texture;
  - post (Point): `radius_m`, `shape` round or square; `style` pole tapers it to
    `top_radius_m`; lamp also puts a lamp head on top, or on an arm `arm_m` long toward
    `arm_bearing_deg`;
  - clothesline (LineString between the two brackets on a fence): `out_m`, `lines_n`;
  - line (LineString drawn on the ground, `colour`) and mark (Point, a label): drawn by the
    build.
- heights: `height_m` above the ground (the highest ground under the feature for solids),
  or `top_m` for a level top; `base_m` fixes the base instead of the ground. Levels are
  NZVD2016.
- `material` (and `frame_material` for a fence's posts and rails): a key of MATERIALS.
"""

from pathlib import Path

import numpy as np
from scipy.spatial import Delaunay
from shapely.geometry import Point, Polygon

from ..geo import geojson, mesh
from ..geo.mesh import resample_line

FILES = "features-*.geojson"

# Material -> (colour, texture image in data/features/ or None, metres one copy of the image
# covers (u along the part, v up it)). Colours are the textures' medians (the layer swatch);
# tools/feature_textures.py says which photo or frame each image is cut from.
MATERIALS = {
    "paling-grey": ("#7b7d87", "paling-grey.jpg", (0.75, 1.8)),     # weathered palings, about 5 across
    "paling-brown": ("#50483a", "paling-brown.jpg", (1.0, 1.65)),    # newer stained palings
    "timber-grey": ("#858684", "timber-grey.jpg", (1.2, 0.15)),      # rails, cappings, square posts, edges
    "timber-upright": ("#858684", "timber-upright.jpg", (0.15, 1.2)),  # the same, grain upright: posts
    "timber-post": ("#776e55", "timber-post.jpg", (0.1, 0.3)),       # round posts of the low edges
    "picket-white": ("#e9e7e0", None, (1.0, 1.0)),
    "concrete": ("#948a7d", "concrete.jpg", (1.0, 1.0)),
    "paving": ("#807f85", "paving.jpg", (2.4, 1.6)),                 # driveway and parking area
    "shed-cladding": ("#ad917b", "shed-cladding.jpg", (0.96, 1.75)),  # nine boards to a copy, as in the frame
    "shed-door": ("#3b414c", "shed-door.jpg", (1.2, 1.72)),          # one copy over the doors
    "shed-band": ("#323c45", "shed-band.jpg", (3.1, 0.28)),          # one copy across the front
    "shed-roof": ("#36393d", None, (1.0, 1.0)),
    "plastic-black": ("#39383f", "compost.jpg", (0.7, 0.8)),
    "firewood": ("#72655e", "firewood.jpg", (1.2, 1.2)),
    "drain-grate": ("#383a44", "drain-grate.jpg", (1.0, 0.13)),
    "metal-grey": ("#8f9498", None, (1.0, 1.0)),
    "box-grey": ("#b9c0c6", None, (1.0, 1.0)),  # letterboxes
    "box-red": ("#a3503f", None, (1.0, 1.0)),
    "metal-dark": ("#3a3d40", None, (1.0, 1.0)),
    "pole-grey": ("#8a8070", None, (1.0, 1.0)),                      # weathered pole
    "plastic-white": ("#e3e5e3", None, (1.0, 1.0)),
    "hedge": ("#3f6b35", None, (1.0, 1.0)),
    "timber": ("#8b6f4e", None, (1.0, 1.0)),
    "grey": ("#bdb6a8", None, (1.0, 1.0)),
}
GROUPS = {"fence": "Fences and gates", "gate": "Fences and gates", "shed": "Sheds, bins and stacks",
          "building": "Other buildings", "box": "Sheds, bins and stacks",
          "clothesline": "Sheds, bins and stacks", "pad": "Paths, pads and paving", "post": "Posts and poles"}


class UVMesh(mesh.Mesh):
    """Mesh with per-vertex texture coordinates, in copies of the material's image."""

    def __init__(self, vertices=None, faces=None, uv=None):
        super().__init__(vertices, faces)
        self.uv = np.zeros((len(self.vertices), 2)) if uv is None else np.asarray(uv, float)

    def add(self, vertices, faces, uv=None):
        vertices = np.asarray(vertices, float)
        uv = np.zeros((len(vertices), 2)) if uv is None else np.asarray(uv, float).reshape(-1, 2)
        self.uv = np.vstack([self.uv, uv])
        return super().add(vertices, faces)

    def extend(self, other):
        return self.add(other.vertices, other.faces, getattr(other, "uv", None))


class Parts(dict):
    """Material -> UVMesh, filled by the builders; UVs given in metres are scaled to the
    material's image size here."""

    def mesh(self, mat):
        return self.setdefault(mat, UVMesh())

    def quad(self, mat, a, b, c, d, uv_m):
        tu, tv = MATERIALS[mat][2]
        uv = np.asarray(uv_m, float) / (tu, tv)
        self.mesh(mat).add([a, b, c, d], [(0, 1, 2), (0, 2, 3)], uv)

    def tri(self, mat, a, b, c, uv_m):
        tu, tv = MATERIALS[mat][2]
        self.mesh(mat).add([a, b, c], [(0, 1, 2)], np.asarray(uv_m, float) / (tu, tv))


def read_all(root):
    """The site's features: every data/site/features-*.geojson in the site folder `root`, each feature with its file."""
    feats = []
    for path in sorted((root / "data" / "site").glob(FILES)):
        for f in geojson.read(path)["features"]:
            f["properties"].setdefault("file", path.name)
            feats.append(f)
    return feats


# --------------------------------------------------------------------------- geometry helpers

def same(a, b):
    """Two plan points are the same (a ring's closing point). Absolute tolerance only: with
    grid coordinates near 400 000, numpy's default relative tolerance spans metres."""
    return bool(np.all(np.abs(np.asarray(a, float) - np.asarray(b, float)) < 1e-6))


def triangles(pts):
    """mesh.triangulate_polygon on coordinates relative to the ring's centre, which keeps its
    convexity and inside tests well conditioned for grid coordinates near 400 000."""
    pts = np.asarray(pts, float)[:, :2]
    return mesh.triangulate_polygon(pts - pts.mean(axis=0))


def ccw(ring):
    pts = np.asarray(ring, float)[:, :2]
    if same(pts[0], pts[-1]):
        pts = pts[:-1]
    return pts[::-1] if mesh.signed_area(pts) < 0 else pts


def block(parts, mat, ring, bottom, top, top_cap=True, u0=0.0):
    """Closed prism over a ring with its own vertices per face (crisp edges): sides with u along
    the ring and v up from the bottom, top and bottom in plan metres. bottom/top: scalars or
    per-vertex (in the ring's own order)."""
    pts = np.asarray(ring, float)[:, :2]
    if same(pts[0], pts[-1]):
        pts = pts[:-1]
    n = len(pts)
    zb = np.broadcast_to(np.asarray(bottom, float), (n,)).copy()
    zt = np.broadcast_to(np.asarray(top, float), (n,)).copy()
    if mesh.signed_area(pts) < 0:
        pts, zb, zt = pts[::-1], zb[::-1], zt[::-1]
    lo = float(np.min(zb))
    s = u0
    for i in range(n):
        j = (i + 1) % n
        a, b = pts[i], pts[j]
        length = float(np.hypot(*(b - a)))
        parts.quad(mat, (*a, zb[i]), (*b, zb[j]), (*b, zt[j]), (*a, zt[i]),
                   [(s, zb[i] - lo), (s + length, zb[j] - lo), (s + length, zt[j] - lo), (s, zt[i] - lo)])
        s += length
    for t in triangles(pts):
        t = list(t)
        p = pts[t]
        if top_cap:
            parts.tri(mat, *np.column_stack([p, zt[t]]), p)
        parts.tri(mat, *np.column_stack([p, zb[t]])[::-1], p[::-1])


def rect(c, u, along, across):
    """Ring of a rectangle centred at c, `along` long in direction u and `across` wide."""
    nrm = np.array([-u[1], u[0]])
    a, b = 0.5 * along * u, 0.5 * across * nrm
    return np.array([c - a - b, c + a - b, c + a + b, c - a + b])


def strip(parts, mat, pts, z0, z1, off0, off1, u0=0.0, ends=True, v_from=None):
    """A vertical slab along a polyline between offsets off0 < off1 across it (left of the line's
    direction positive), from z0 to z1 (per point). Faces: both sides, top, bottom and the two
    ends; UVs in metres (u along the line, v up from `v_from`, the per-point base by default)."""
    pts = np.asarray(pts, float)[:, :2]
    z0 = np.broadcast_to(np.asarray(z0, float), (len(pts),))
    z1 = np.broadcast_to(np.asarray(z1, float), (len(pts),))
    vb = z0 if v_from is None else np.broadcast_to(np.asarray(v_from, float), (len(pts),))
    d = np.gradient(pts, axis=0) if len(pts) > 2 else np.repeat(np.diff(pts, axis=0), 2, axis=0)
    d = d / np.hypot(d[:, 0], d[:, 1])[:, None]
    nrm = np.column_stack([-d[:, 1], d[:, 0]])
    p0, p1 = pts + off0 * nrm, pts + off1 * nrm
    s = u0 + np.concatenate([[0], np.cumsum(np.hypot(*np.diff(pts, axis=0).T))])
    t = off1 - off0
    for i in range(len(pts) - 1):
        j = i + 1
        # outer face (off1 side) and inner face (off0 side)
        parts.quad(mat, (*p1[i], z0[i]), (*p1[j], z0[j]), (*p1[j], z1[j]), (*p1[i], z1[i]),
                   [(s[i], z0[i] - vb[i]), (s[j], z0[j] - vb[j]), (s[j], z1[j] - vb[j]), (s[i], z1[i] - vb[i])])
        parts.quad(mat, (*p0[j], z0[j]), (*p0[i], z0[i]), (*p0[i], z1[i]), (*p0[j], z1[j]),
                   [(s[j], z0[j] - vb[j]), (s[i], z0[i] - vb[i]), (s[i], z1[i] - vb[i]), (s[j], z1[j] - vb[j])])
        parts.quad(mat, (*p0[i], z1[i]), (*p1[i], z1[i]), (*p1[j], z1[j]), (*p0[j], z1[j]),
                   [(s[i], 0), (s[i], t), (s[j], t), (s[j], 0)])
        parts.quad(mat, (*p0[j], z0[j]), (*p1[j], z0[j]), (*p1[i], z0[i]), (*p0[i], z0[i]),
                   [(s[j], 0), (s[j], t), (s[i], t), (s[i], 0)])
    if ends:
        for i, sign in ((0, -1), (len(pts) - 1, 1)):
            a, b = (p0[i], p1[i]) if sign > 0 else (p1[i], p0[i])
            parts.quad(mat, (*a, z0[i]), (*b, z0[i]), (*b, z1[i]), (*a, z1[i]),
                       [(0, 0), (t, 0), (t, z1[i] - z0[i]), (0, z1[i] - z0[i])])


def upright(mat):
    """The material for a post: weathered timber with its grain standing upright."""
    return "timber-upright" if mat == "timber-grey" else mat


def side_sign(p, key, default="left"):
    return 1.0 if p.get(key, default) == "left" else -1.0


def line_points(f, spacing=0.5):
    pts, s = resample_line(np.asarray(f["geometry"]["coordinates"], float)[:, :2], spacing)
    return pts, s


def heights(p, g):
    """Base and top per point: base_m or the ground (5 cm into it), top_m or ground + height_m."""
    base = np.full(len(g), p["base_m"]) if "base_m" in p else g - 0.05
    top = np.full(len(g), p["top_m"]) if "top_m" in p else g + p.get("height_m", 1.2)
    return base, top


def stations(length, spacing):
    """Positions along a line for posts: both ends and evenly in between, at most `spacing` apart."""
    n = max(int(np.ceil(length / spacing - 1e-6)), 1)
    return np.linspace(0, length, n + 1)


def at(pts, s, value):
    """Interpolate a per-point value (or the plan position) at distance s along the line."""
    cum = np.concatenate([[0], np.cumsum(np.hypot(*np.diff(pts, axis=0).T))])
    if np.ndim(value) == 2:
        return np.array([np.interp(s, cum, value[:, k]) for k in range(value.shape[1])]).T
    return np.interp(s, cum, value)


def direction(pts, s):
    cum = np.concatenate([[0], np.cumsum(np.hypot(*np.diff(pts, axis=0).T))])
    i = min(max(int(np.searchsorted(cum, s, side="right")) - 1, 0), len(pts) - 2)
    d = pts[i + 1] - pts[i]
    return d / np.hypot(*d)


# --------------------------------------------------------------------------- builders

def fence(f, ground):
    p = f["properties"]
    style = p.get("style", "paling")
    pts, s = line_points(f)
    g = ground(pts[:, 0], pts[:, 1])
    base, top = heights(p, g)
    length = float(s[-1])
    parts = Parts()
    side = side_sign(p, "rails")
    frame = p.get("frame_material", "timber-grey")
    if style == "paling":
        # palings on the line, rails on their `rails` side, posts behind the rails, a capping
        cap = 0.025
        strip(parts, p.get("material", "paling-grey"), pts, base + 0.07, top - cap, -0.011, 0.011, v_from=base)
        h = top - base
        rails = p.get("rails_at") or ([0.25, None, -0.22] if float(np.median(h)) > 1.4 else [0.2, -0.2])
        for r in rails:
            mid = (base + top) / 2 if r is None else (top + r if r < 0 else base + 0.05 + r)
            lo, hi = sorted((side * 0.011, side * 0.061))
            strip(parts, frame, pts, mid - 0.05, mid + 0.05, lo, hi)
        if p.get("posts", True):
            for st in stations(length, p.get("post_spacing_m", 2.4)):
                d = direction(pts, st)
                c = at(pts, st, pts) + side * 0.111 * np.array([-d[1], d[0]])
                block(parts, upright(frame), rect(c, d, 0.1, 0.1), at(pts, st, base) - 0.05, at(pts, st, top) - cap)
        lo, hi = sorted((-0.03 * side, 0.17 * side))
        strip(parts, frame, pts, top - cap, top, lo, hi)
    elif style == "picket":
        pickets(parts, p.get("material", "picket-white"), pts, base, top, side)
        for st in (0.0, length):
            c = at(pts, st, pts) + side * 0.06 * np.array([-direction(pts, st)[1], direction(pts, st)[0]])
            block(parts, p.get("material", "picket-white"), rect(c, direction(pts, st), 0.09, 0.09),
                  at(pts, st, base) - 0.05, at(pts, st, top) + 0.05)
    elif style == "post-and-rail":
        mat = p.get("material", "timber-grey")
        for st in stations(length, p.get("post_spacing_m", 2.0)):
            c = at(pts, st, pts)
            block(parts, upright(mat), rect(c, direction(pts, st), 0.1, 0.1), at(pts, st, base) - 0.05,
                  at(pts, st, top))
        for r in p.get("rails_at", [None]):
            hi_z = top if r is None else base + 0.05 + r
            lo, hi = sorted((side * 0.05, side * 0.1))
            strip(parts, mat, pts, hi_z - 0.15, hi_z, lo, hi)
    elif style == "timber-edge":
        strip(parts, p.get("material", "timber-grey"), pts, base, top, -0.025, 0.025, v_from=base)
        post_side = side_sign(p, "posts")
        r = p.get("post_radius_m", 0.075)
        for st in ([] if p.get("posts") == "none" else stations(length, p.get("post_spacing_m", 1.2))):
            c = at(pts, st, pts) + post_side * (0.025 + r) * np.array([-direction(pts, st)[1], direction(pts, st)[0]])
            a = np.linspace(0, 2 * np.pi, 11)[:-1]
            block(parts, p.get("frame_material", "timber-grey"), c + r * np.column_stack([np.cos(a), np.sin(a)]),
                  at(pts, st, base) - 0.05, at(pts, st, top) + p.get("post_above_m", 0.05))
    else:  # hedge or anything else: a plain slab
        thick = p.get("thickness_m", 0.7 if style == "hedge" else 0.05)
        strip(parts, p.get("material", "hedge" if style == "hedge" else "timber"), pts, base, top, 0, thick)
    return parts


def pickets(parts, mat, pts, base, top, side, width=0.07, gap=0.045, rails=True, brace=False):
    """Pointed pickets on the line (between base and top) on two rails on `side`."""
    length = float(np.sum(np.hypot(*np.diff(pts, axis=0).T)))
    n = max(int((length - 0.02) // (width + gap)), 1)
    start = (length - (n * width + (n - 1) * gap)) / 2
    for k in range(n):
        s0 = start + k * (width + gap)
        sm = s0 + width / 2
        u = direction(pts, sm)
        nrm = np.array([-u[1], u[0]])
        c = at(pts, sm, pts)
        b, t = float(at(pts, sm, base)) + 0.05, float(at(pts, sm, top))
        ring = [c - u * width / 2 - nrm * 0.01, c + u * width / 2 - nrm * 0.01,
                c + u * width / 2 + nrm * 0.01, c - u * width / 2 + nrm * 0.01]
        block(parts, mat, ring, b, t - 0.05, top_cap=False)
        # the point: a ridge across the picket's thickness, two sloping faces and two gables
        p0, p1, p2, p3 = (np.array([*q, t - 0.05]) for q in ring)
        a0, a1 = np.array([*(c - nrm * 0.01), t]), np.array([*(c + nrm * 0.01), t])
        parts.tri(mat, p0, p1, a0, [(0, 0), (width, 0), (width / 2, 0.05)])
        parts.tri(mat, p2, p3, a1, [(0, 0), (width, 0), (width / 2, 0.05)])
        parts.quad(mat, p3, p0, a0, a1, [(0, 0), (0.02, 0), (0.02, 0.06), (0, 0.06)])
        parts.quad(mat, p1, p2, a1, a0, [(0, 0), (0.02, 0), (0.02, 0.06), (0, 0.06)])
    if rails:
        lo_off, hi_off = sorted((side * 0.01, side * 0.045))
        for zr in (base + 0.25, top - 0.3):
            strip(parts, mat, pts, zr - 0.04, zr + 0.04, lo_off, hi_off)
        if brace:
            a = at(pts, 0.05, pts)
            b2 = at(pts, length - 0.05, pts)
            z_a, z_b = float(at(pts, 0.05, base)) + 0.25, float(at(pts, length - 0.05, top)) - 0.3
            seg = np.array([a, b2])
            strip(parts, mat, seg, np.array([z_a - 0.04, z_b - 0.04]), np.array([z_a + 0.04, z_b + 0.04]),
                  lo_off, hi_off)


def gate(f, ground):
    """A gate leaf from the hinge (first point) to the latch end, a few centimetres off the
    ground: pickets on two rails (style picket), the same with a diagonal brace (brace), or
    palings on two rails with a brace (paling); rails on the `rails` side."""
    p = f["properties"]
    pts = np.asarray(f["geometry"]["coordinates"], float)[:, :2]
    g = ground(pts[:, 0], pts[:, 1])
    base, top = heights(p, g)
    base = np.maximum(base, g)
    parts = Parts()
    side = side_sign(p, "rails")
    if p.get("style") == "paling":
        mat, frame = p.get("material", "paling-grey"), p.get("frame_material", "timber-grey")
        strip(parts, mat, pts, base + 0.05, top, -0.011, 0.011, v_from=base)
        lo, hi = sorted((side * 0.011, side * 0.056))
        zr = (base + 0.2, top - 0.2)
        for z in zr:
            strip(parts, frame, pts, z - 0.045, z + 0.045, lo, hi)
        brace = np.array([pts[0], pts[-1]])
        strip(parts, frame, brace, np.array([zr[0][0] + 0.05, zr[1][-1] - 0.14]),
              np.array([zr[0][0] + 0.14, zr[1][-1] - 0.05]), lo, hi)
        return parts
    pickets(parts, p.get("material", "picket-white"), pts, base, top, side,
            brace=p.get("style") == "brace")
    return parts


def shed(f, ground):
    """Mono-pitch garden shed: clad walls on a black plinth, double doors and a dark band of
    clerestory windows across the high front, a dark roof with an overhang."""
    p = f["properties"]
    ring = np.asarray(f["geometry"]["coordinates"][0], float)[:, :2]
    if same(ring[0], ring[-1]):
        ring = ring[:-1]
    fl, fr, br, bl = ring[:4]
    g = ground(ring[:, 0], ring[:, 1])
    z0 = p.get("base_m", float(np.nanmax(g)))
    hf, hb = p["front_height_m"], p["back_height_m"]
    parts = Parts()
    clad, band = p.get("material", "shed-cladding"), "shed-band"
    u_front = (fr - fl) / np.hypot(*(fr - fl))
    out = np.array([u_front[1], -u_front[0]])           # outward normal of the front (right of FL->FR)
    if np.dot(out, (fl + fr) / 2 - (bl + br) / 2) < 0:
        out = -out
    depth = float(np.hypot(*(bl - fl)))
    width = float(np.hypot(*(fr - fl)))
    band_h = p.get("band_m", 0.28)

    def wall_top(q):
        """Wall top at a plan point: falls from the front to the back."""
        t = np.clip(np.dot(fl - q, out) / depth, 0, 1)
        return z0 + hf + (hb - hf) * t

    corners = [fl, fr, br, bl]
    s = 0.0
    for i in range(4):
        a, b = corners[i], corners[(i + 1) % 4]
        length = float(np.hypot(*(b - a)))
        ta, tb = wall_top(a), wall_top(b)
        if i == 0:      # front: cladding below the band, the band above
            ta = tb = z0 + hf - band_h
            parts.quad(band, (*a, ta), (*b, tb), (*b, z0 + hf), (*a, z0 + hf), [(0, 0), (length, 0), (length, band_h), (0, band_h)])
        parts.quad(clad, (*a, z0 + 0.06), (*b, z0 + 0.06), (*b, tb), (*a, ta),
                   [(s, 0), (s + length, 0), (s + length, tb - z0), (s, ta - z0)])
        nrm = np.array([(b - a)[1], -(b - a)[0]]) / length
        if np.dot(nrm, (a + b) / 2 - ring.mean(axis=0)) < 0:
            nrm = -nrm
        # black plinth strip, 6 cm high, 1 cm proud
        parts.quad("metal-dark", (*(a + 0.01 * nrm), z0), (*(b + 0.01 * nrm), z0), (*(b + 0.01 * nrm), z0 + 0.06),
                   (*(a + 0.01 * nrm), z0 + 0.06), [(0, 0), (length, 0), (length, 0.06), (0, 0.06)])
        s += length
    # doors, 1.5 cm proud of the front
    d0, dw = p.get("door_from_m", width / 2 - 0.6), p.get("door_width_m", 1.2)
    a, b = fl + u_front * d0 + out * 0.015, fl + u_front * (d0 + dw) + out * 0.015
    dh = hf - band_h - 0.02
    parts.quad("shed-door", (*a, z0 + 0.06), (*b, z0 + 0.06), (*b, z0 + dh), (*a, z0 + dh),
               [(0, 0), (dw, 0), (dw, dh - 0.06), (0, dh - 0.06)])
    # roof: a slab over the walls with an overhang, falling to the back
    ov = p.get("overhang_m", 0.08)
    c = ring.mean(axis=0)
    roof_ring = []
    for q in corners:
        v = q - c
        roof_ring.append(q + ov * np.sign(np.dot(v, u_front)) * u_front + ov * np.sign(np.dot(v, out)) * out)
    roof_ring = np.array(roof_ring)
    zr = np.array([wall_top(q) for q in corners]) + 0.02
    zr = zr + (zr[0] - zr[3]) / depth * ov * np.array([1, 1, -1, -1])   # carry the pitch over the overhang
    block(parts, "shed-roof", roof_ring, zr, zr + 0.07)
    return parts


def solid(f, ground):
    """Prism over a polygon (building, box) from its base to its top; `taper_m` insets the top. A
    ring whose points carry a height (and no top_m) gets its top per vertex: a box with a sloping top."""
    p = f["properties"]
    coords = np.asarray(f["geometry"]["coordinates"][0], float)
    ring = coords[:, :2]
    g = ground(ring[:, 0], ring[:, 1])
    base = p.get("base_m", float(np.nanmin(g)) - 0.05)
    top = p["top_m"] if "top_m" in p else (p.get("base_m", float(np.nanmin(g))) + p.get("height_m", 2.0))
    if coords.shape[1] == 3 and "top_m" not in p:
        top = coords[:-1, 2] if same(ring[0], ring[-1]) else coords[:, 2]
    mat = p.get("material", "grey" if p["kind"] == "building" else "timber")
    parts = Parts()
    taper = p.get("taper_m", 0.0)
    if taper:
        pts = ccw(ring)
        c = pts.mean(axis=0)
        inset = np.array([q + taper * (c - q) / np.hypot(*(c - q)) for q in pts])
        n = len(pts)
        s = 0.0
        for i in range(n):
            j = (i + 1) % n
            length = float(np.hypot(*(pts[j] - pts[i])))
            parts.quad(mat, (*pts[i], base), (*pts[j], base), (*inset[j], top), (*inset[i], top),
                       [(s, 0), (s + length, 0), (s + length, top - base), (s, top - base)])
            s += length
        for t in triangles(inset):
            q = inset[list(t)]
            parts.tri(mat, *np.column_stack([q, np.full(3, top)]), q)
    else:
        block(parts, mat, ring, base, top)
    return parts


def pad(f, ground, step=0.25):
    """A surface over a polygon, draped on the ground 3 cm up (or flat at top_m); with
    thickness_m, a slab with sides down to the ground."""
    p = f["properties"]
    ring = np.asarray(f["geometry"]["coordinates"][0], float)[:, :2]
    poly = Polygon(ring)
    e0, n0, e1, n1 = poly.bounds
    E, N = np.meshgrid(np.arange(e0, e1, step), np.arange(n0, n1, step))
    inner = np.array([(e, n) for e, n in zip(E.ravel(), N.ravel())
                      if poly.contains(Point(e, n)) and poly.exterior.distance(Point(e, n)) > step / 4]).reshape(-1, 2)
    edge, _ = resample_line(ring if same(ring[0], ring[-1]) else np.vstack([ring, ring[:1]]), step)
    pts = np.vstack([edge[:-1], inner]) if len(inner) else edge[:-1]
    tri = Delaunay(pts)
    keep = [s for s in tri.simplices if poly.buffer(1e-6).contains(Point(pts[s].mean(axis=0)))]
    thick = p.get("thickness_m", 0.0)
    z = np.full(len(pts), p["top_m"]) if "top_m" in p else ground(pts[:, 0], pts[:, 1]) + (thick or 0.03)
    faces = np.array(keep, int)
    a, b, c = (pts[faces[:, k]] for k in range(3))
    flip = mesh.cross2(b - a, c - a) < 0
    faces[flip] = faces[flip][:, [0, 2, 1]]
    mat = p.get("material", "concrete")
    tu, tv = MATERIALS[mat][2]
    ang = np.radians(p.get("uv_bearing_deg", 90.0))      # u runs along this bearing
    ux, uy = np.sin(ang), np.cos(ang)
    uv = np.column_stack([pts[:, 0] * ux + pts[:, 1] * uy, -pts[:, 0] * uy + pts[:, 1] * ux])
    uv = (uv - uv.min(axis=0)) / (tu, tv)
    parts = Parts()
    parts[mat] = UVMesh(np.column_stack([pts, z]), faces, uv)
    if thick:
        rim = edge[:-1]                      # the first points of pts, so z[:len(rim)] is their top
        block(parts, mat, rim, ground(rim[:, 0], rim[:, 1]) - 0.05, z[:len(rim)], top_cap=False)
    return parts


def post(f, ground):
    p = f["properties"]
    e, n = f["geometry"]["coordinates"][:2]
    r = float(p.get("radius_m", 0.06))
    g = p.get("base_m", float(ground(np.array([e]), np.array([n]))[0]))
    h = p.get("height_m", 1.0) if "top_m" not in p else p["top_m"] - g
    mat = p.get("material", "timber-grey" if p.get("shape") == "square" else "pole-grey")
    parts = Parts()
    if p.get("shape") == "square":
        block(parts, upright(mat), rect(np.array([e, n]), np.array([1.0, 0.0]), 2 * r, 2 * r), g - 0.05, g + h)
        return parts
    a = np.linspace(0, 2 * np.pi, 13)[:-1]
    if p.get("style") in ("pole", "lamp"):
        # tapered pole; a lamp also gets a head on top or on an arm toward `arm_bearing_deg`. A pole
        # stands in the ground: where the model's ground is below its `base_m` (a surveyed level that
        # the LiDAR does not match), the shaft carries on down to it instead of floating.
        r_top = p.get("top_radius_m", 0.6 * r)
        lamp_h = h - 0.15 if p["style"] == "lamp" else h
        foot = min(g, float(ground(np.array([e]), np.array([n]))[0])) - 0.05
        ring_b = np.column_stack([e + r * np.cos(a), n + r * np.sin(a)])
        ring_t = np.column_stack([e + r_top * np.cos(a), n + r_top * np.sin(a)])
        for i in range(12):
            j = (i + 1) % 12
            parts.quad(mat, (*ring_b[i], foot), (*ring_b[j], foot), (*ring_t[j], g + lamp_h), (*ring_t[i], g + lamp_h),
                       [(i / 12, 0), ((i + 1) / 12, 0), ((i + 1) / 12, g + lamp_h - foot), (i / 12, g + lamp_h - foot)])
        c0 = np.array([e, n])
        arm = p.get("arm_m", 0.0)
        if p["style"] == "pole":          # a flat cap on the top
            for k in range(1, 11):
                parts.tri(mat, (*ring_t[0], g + h), (*ring_t[k], g + h), (*ring_t[k + 1], g + h),
                          [ring_t[0] - c0, ring_t[k] - c0, ring_t[k + 1] - c0])
        elif arm:
            bearing = np.radians(p.get("arm_bearing_deg", 0.0))
            u = np.array([np.sin(bearing), np.cos(bearing)])
            block(parts, mat, rect(c0 + u * arm / 2, u, arm, 0.06), g + lamp_h - 0.05, g + lamp_h + 0.02)
            block(parts, "metal-dark", rect(c0 + u * (arm - 0.2), u, 0.5, 0.2), g + lamp_h - 0.12,
                  g + lamp_h + 0.04)
        else:      # a lamp head on the pole top
            ring_h = np.column_stack([e + 1.6 * r_top * np.cos(a), n + 1.6 * r_top * np.sin(a)])
            block(parts, "metal-dark", ring_h, g + lamp_h, g + h)
        return parts
    block(parts, mat, np.column_stack([e + r * np.cos(a), n + r * np.sin(a)]), g - 0.05, g + h)
    return parts


def clothesline(f, ground):
    """Fold-down clothes line on a fence: an arm out from each bracket and lines between them."""
    p = f["properties"]
    pts = np.asarray(f["geometry"]["coordinates"], float)[:2, :2]
    g = ground(pts[:, 0], pts[:, 1])
    z = p["top_m"] if "top_m" in p else float(np.max(g)) + p.get("height_m", 1.8)
    u = (pts[1] - pts[0]) / np.hypot(*(pts[1] - pts[0]))
    out = side_sign(p, "side") * np.array([-u[1], u[0]])
    reach = p.get("out_m", 0.6)
    mat = p.get("material", "metal-grey")
    parts = Parts()
    for q in pts:
        block(parts, mat, rect(q + out * reach / 2, out, reach, 0.03), z - 0.03, z)
    n = p.get("lines_n", 5)
    for k in range(n):
        o = reach * (k + 0.5) / n
        seg = np.array([pts[0] + out * o, pts[1] + out * o])
        strip(parts, "plastic-white", seg, z - 0.008, z, -0.004, 0.004)
    return parts


BUILDERS = {"fence": fence, "gate": gate, "shed": shed, "building": solid, "box": solid, "pad": pad,
            "post": post, "clothesline": clothesline}


def build(feats, ground):
    """{group name: [(UVMesh, material, feature properties)]} for every drawable feature
    (a feature made of several materials gives one entry per material)."""
    out = {}
    for f in feats:
        kind = f["properties"].get("kind")
        if kind in BUILDERS:
            for mat, m in BUILDERS[kind](f, ground).items():
                if len(m):
                    out.setdefault(GROUPS[kind], []).append((m, mat, f["properties"]))
    return out


def texture_file(mat, folder):
    """The image a material's layer uses (published as viewer/data/feature-<name>), if the site's feature pictures
    (`folder`, data/features/) have it, or None."""
    name = MATERIALS[mat][1]
    return name if name and folder and (Path(folder) / name).exists() else None
