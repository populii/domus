"""The house: walls, roof and eaves, and the parts listed in data/site/house.geojson (bay windows, porches,
steps, blocks such as decks and landings, freestanding walls, balustrades, chimneys and tanks).

Everything is laid out in the house's own frame (HouseFrame: x along the walls' dominant direction, origin at
the walls ring's first vertex) and returned in site coordinates (E, N and the model's height datum).

Wall-like faces (the walls, bay windows, porch walls) come back as Face objects whose
texture coordinates are metres on the face: u from the face's left end as seen from outside,
v the height. house_texture lays photo and drawn textures on them through an atlas. The roof
carries tile coordinates in metres (u along the eave, v up the slope). Other parts are solid
colours, named by a palette key (house_texture.PALETTE) or given as hex.

[house] roof_form picks the roof's shape. A skillion is one plane rising towards roof_azimuth_deg. A gable is two planes meeting at a
ridge on the bearing roof_azimuth_deg, through the outline's centre or roof_ridge_offset_m to the right of it. The walls' tops follow both.
Otherwise it is a uniform-pitch hip roof. For a house made of right-angled wings, that roof is
the upper envelope of one hip roof per maximal rectangle that fits in the eaves outline, so
each rectangle gets its own four-sided hip roof and the depth buffer draws the envelope.
"""

import itertools
import math
from dataclasses import dataclass, field

import numpy as np
import shapely
from shapely.geometry import Polygon, box

from .errors import DomusError
from .mesh import Mesh, signed_area, triangulate_polygon

# --------------------------------------------------------------------------- frame and meshes

def orientation(ring):
    """Dominant wall direction (radians), from the length-weighted edge angles mod 90 deg."""
    pts = np.asarray(ring, float)[:, :2]
    d = np.diff(np.vstack([pts, pts[:1]]), axis=0)
    ang = np.arctan2(d[:, 1], d[:, 0]) % (np.pi / 2)
    w = np.hypot(*d.T)
    z = np.sum(w * np.exp(4j * ang))
    return float(np.angle(z) / 4)


class HouseFrame:
    """Site (E, N) <-> house frame (x, y): origin at the walls ring's first vertex, x along the
    dominant wall direction."""

    def __init__(self, ring):
        self.theta = orientation(ring)
        self.c, self.s = math.cos(self.theta), math.sin(self.theta)
        self.origin = np.asarray(ring[0], float)[:2]

    def local(self, pts):
        p = np.asarray(pts, float)
        d = p[..., :2] - self.origin
        xy = np.stack([self.c * d[..., 0] + self.s * d[..., 1], -self.s * d[..., 0] + self.c * d[..., 1]], -1)
        return np.concatenate([xy, p[..., 2:]], -1)

    def site(self, pts):
        p = np.asarray(pts, float)
        e = self.origin[0] + self.c * p[..., 0] - self.s * p[..., 1]
        n = self.origin[1] + self.s * p[..., 0] + self.c * p[..., 1]
        return np.concatenate([np.stack([e, n], -1), p[..., 2:]], -1)


class UVMesh(Mesh):
    """Mesh with per-vertex texture coordinates (metres, or a palette key's swatch later)."""

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


def quad(mesh, a, b, c, d, uv=None):
    """Add the quad a-b-c-d (counter-clockwise seen from its front) as two triangles."""
    mesh.add([a, b, c, d], [(0, 1, 2), (0, 2, 3)], uv)


def polygon_mesh(poly, z, up=True):
    """Flat mesh of a shapely polygon (holes allowed) at height z (scalar or callable(x, y))."""
    m = UVMesh()
    for tri in shapely.constrained_delaunay_triangles(poly).geoms:
        p = np.asarray(tri.exterior.coords)[:3]
        if signed_area(p) < 0:
            p = p[::-1]
        if not up:
            p = p[::-1]
        zz = [z(*q) for q in p] if callable(z) else [z] * 3
        m.add(np.column_stack([p, zz]), [(0, 1, 2)], p)
    return m


def block(ring, bottom, top):
    """Closed prism over a ring with separate vertices per face (flat shading); bottom/top scalar
    or per-vertex. UVs: walls (u along the ring, v height), top and bottom in plan metres."""
    pts = np.asarray(ring, float)[:, :2]
    if np.allclose(pts[0], pts[-1], rtol=0, atol=1e-9):  # absolute: grid coordinates are ~1e5
        pts = pts[:-1]
    n = len(pts)
    zb = np.broadcast_to(np.asarray(bottom, float), (n,))
    zt = np.broadcast_to(np.asarray(top, float), (n,))
    if signed_area(pts) < 0:
        pts, zb, zt = pts[::-1], zb[::-1], zt[::-1]
    m = UVMesh()
    s = 0.0
    for i in range(n):
        j = (i + 1) % n
        a, b = pts[i], pts[j]
        L = float(np.hypot(*(b - a)))
        quad(m, (*a, zb[i]), (*b, zb[j]), (*b, zt[j]), (*a, zt[i]),
             [(s, zb[i]), (s + L, zb[j]), (s + L, zt[j]), (s, zt[i])])
        s += L
    for tri in triangulate_polygon(pts):
        p = pts[list(tri)]
        m.add(np.column_stack([p, zt[list(tri)]]), [(0, 1, 2)], p)
        m.add(np.column_stack([p, zb[list(tri)]]), [(2, 1, 0)], p)
    return m


def rect_ring(x0, y0, x1, y1):
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


def cylinder(cx, cy, r, z0, z1, sides=16):
    a = np.linspace(0, 2 * np.pi, sides + 1)[:-1]
    return block(np.column_stack([cx + r * np.cos(a), cy + r * np.sin(a)]), z0, z1)


# --------------------------------------------------------------------------- roof

def maximal_rectangles(poly, tol=0.02):
    """Axis-aligned rectangles inside `poly` with corners on its vertex coordinates,
    keeping only those not contained in a bigger one."""
    xs = sorted(set(np.round([p[0] for p in poly.exterior.coords], 3)))
    ys = sorted(set(np.round([p[1] for p in poly.exterior.coords], 3)))
    inside = poly.buffer(tol, join_style=2)
    rects = []
    for (x0, x1), (y0, y1) in itertools.product(itertools.combinations(xs, 2),
                                                itertools.combinations(ys, 2)):
        if x1 - x0 < 0.3 or y1 - y0 < 0.3:
            continue
        if inside.covers(box(x0, y0, x1, y1)):
            rects.append((x0, y0, x1, y1))
    maximal = [r for r in rects
               if not any(o != r and o[0] <= r[0] and o[1] <= r[1] and o[2] >= r[2] and o[3] >= r[3]
                          for o in rects)]
    return maximal


def hip_roof(rect, z_edge, pitch):
    """Four-face hip roof over rect (x0, y0, x1, y1) in local coordinates."""
    x0, y0, x1, y1 = rect
    t = math.tan(pitch)
    w, h = x1 - x0, y1 - y0
    if w >= h:
        r = h / 2
        ridge = [(x0 + r, y0 + r), (x1 - r, y0 + r)]
    else:
        r = w / 2
        ridge = [(x0 + r, y0 + r), (x0 + r, y1 - r)]
    zr = z_edge + t * r
    v = [(x0, y0, z_edge), (x1, y0, z_edge), (x1, y1, z_edge), (x0, y1, z_edge),
         (*ridge[0], zr), (*ridge[1], zr)]
    if w >= h:
        faces = [(0, 1, 5), (0, 5, 4), (1, 2, 5), (2, 3, 4), (2, 4, 5), (3, 0, 4)]
    else:
        faces = [(0, 1, 4), (1, 2, 5), (1, 5, 4), (2, 3, 5), (3, 0, 4), (3, 4, 5)]
    return v, faces


def roof_height(rects, z_edge, pitch, x, y):
    """Height of the union-of-hips roof at local (x, y); -inf outside it."""
    t = math.tan(pitch)
    best = np.full(np.shape(x), -np.inf)
    for x0, y0, x1, y1 in rects:
        d = np.minimum.reduce([np.asarray(x) - x0, x1 - np.asarray(x), np.asarray(y) - y0, y1 - np.asarray(y)])
        best = np.maximum(best, np.where(d >= 0, z_edge + t * d, -np.inf))
    return best


def hip_faces(rect):
    """The four faces of a hip roof over rect as (plan polygon, eave start, eave direction,
    inward normal); the ridge runs along the longer side."""
    x0, y0, x1, y1 = rect
    w, h = x1 - x0, y1 - y0
    r = min(w, h) / 2
    a = (x0 + r, y0 + r)                                   # ridge ends
    b = (x1 - r, y0 + r) if w >= h else (x0 + r, y1 - r)
    if w >= h:   # ridge along x: trapezoids south and north, triangles east and west
        polys = [[(x0, y0), (x1, y0), b, a], [(x1, y0), (x1, y1), b],
                 [(x1, y1), (x0, y1), a, b], [(x0, y1), (x0, y0), a]]
    else:        # ridge along y: triangles south and north, trapezoids east and west
        polys = [[(x0, y0), (x1, y0), a], [(x1, y0), (x1, y1), b, a],
                 [(x1, y1), (x0, y1), b], [(x0, y1), (x0, y0), a, b]]
    frames = [((x0, y0), (1, 0), (0, 1)), ((x1, y0), (0, 1), (-1, 0)),
              ((x1, y1), (-1, 0), (0, -1)), ((x0, y1), (0, -1), (1, 0))]
    return [(p, *f) for p, f in zip(polys, frames)]


def roof_mesh(rects, z_edge, pitch):
    """Union-of-hips roof with tile UVs: u along the eave, v up the slope (metres). u and v are
    measured from fixed lines, so coplanar faces of overlapping rectangles map identically."""
    t = math.tan(pitch)
    m = UVMesh()
    for rect in rects:
        for poly, p0, e, nrm in hip_faces(rect):
            p = np.asarray(poly, float)
            if abs(signed_area(p)) < 1e-6:
                continue
            e, nrm = np.asarray(e, float), np.asarray(nrm, float)
            d = (p - p0) @ nrm
            z = z_edge + t * d
            uv = np.column_stack([p @ e, (p0 @ nrm + d) / math.cos(pitch)])
            tris = [(0, 1, 2)] if len(p) == 3 else [(0, 1, 2), (0, 2, 3)]
            if signed_area(p) < 0:
                tris = [(a, c, b) for a, b, c in tris]
            m.add(np.column_stack([p, z]), tris, uv)
    return m


class Skillion:
    """One roof plane in the house frame. roof_plane_at_wall is its height over the lowest point of the walls' outline."""

    def __init__(self, fr, ring, plane_at_wall, pitch, azimuth_deg):
        a = math.radians(azimuth_deg)
        e, n = math.sin(a), math.cos(a)
        self.u = np.array([fr.c * e + fr.s * n, -fr.s * e + fr.c * n])
        self.pitch, self.t = pitch, math.tan(pitch)
        self.plane_at_wall = plane_at_wall
        self.low = float((np.asarray(ring, float)[:, :2] @ self.u).min())

    def height(self, x, y):
        return self.plane_at_wall + self.t * (np.asarray(x) * self.u[0] + np.asarray(y) * self.u[1] - self.low)

    def at(self, p):
        return float(self.height(p[0], p[1]))

    def crossings(self, a, b):
        """Where the line from a to b changes slope: nowhere on one plane."""
        return []

    def sides(self, poly):
        """`poly` cut into the parts that lie on one plane."""
        return [poly]

    def uv(self, p):
        across = np.array([-self.u[1], self.u[0]])
        return np.column_stack([p @ across, (p @ self.u) / math.cos(self.pitch)])

    def mesh(self, eaves):
        """The eaves outline as sloping faces, with tile UVs: u across the slope, v up it (metres)."""
        m = UVMesh()
        for part in self.sides(eaves):
            p = np.asarray(part.exterior.coords, float)[:-1]
            tris = [t if signed_area(p[list(t)]) > 0 else (t[0], t[2], t[1]) for t in triangulate_polygon(p)]
            m.add(np.column_stack([p, self.height(p[:, 0], p[:, 1])]), tris, self.uv(p))
        return m


class Gable(Skillion):
    """Two planes of one pitch meeting at a ridge. roof_plane_at_wall is their height over the outline's lowest point."""

    def __init__(self, fr, ring, plane_at_wall, pitch, azimuth_deg, offset_m=0.0):
        super().__init__(fr, ring, plane_at_wall, pitch, azimuth_deg)
        self.along = self.u                                     # the bearing given is the ridge's
        self.n = np.array([self.along[1], -self.along[0]])      # to its right
        pts = np.asarray(ring, float)[:, :2]
        self.d0 = float(np.asarray(Polygon(pts).centroid.coords[0]) @ self.n) + offset_m
        self.ridge = plane_at_wall + self.t * float(np.abs(pts @ self.n - self.d0).max())

    def dist(self, x, y):
        return np.asarray(x) * self.n[0] + np.asarray(y) * self.n[1] - self.d0

    def height(self, x, y):
        return self.ridge - self.t * np.abs(self.dist(x, y))

    def crossings(self, a, b):
        da, db = float(self.dist(a[0], a[1])), float(self.dist(b[0], b[1]))
        if da * db >= 0:
            return []
        return [np.asarray(a, float)[:2] + (np.asarray(b, float)[:2] - np.asarray(a, float)[:2]) * da / (da - db)]

    def sides(self, poly):
        x0, y0, x1, y1 = poly.bounds
        reach = 2 * math.hypot(x1 - x0, y1 - y0)
        c = np.array([(x0 + x1) / 2, (y0 + y1) / 2])
        c = c - self.n * float(self.dist(c[0], c[1]))           # on the ridge
        out = []
        for sign in (1, -1):
            half = Polygon([c - self.along * reach, c + self.along * reach, c + self.along * reach + sign * self.n * reach, c - self.along * reach + sign * self.n * reach])
            cut = poly.intersection(half)
            out += [g for g in getattr(cut, "geoms", [cut]) if isinstance(g, Polygon) and g.area > 1e-6]
        return out

    def uv(self, p):
        return np.column_stack([p @ self.along, np.abs(self.dist(p[:, 0], p[:, 1])) / math.cos(self.pitch)])


def shaped_roof(fr, ring, plane_at_wall, pitch, form, azimuth_deg, ridge_offset_m=0.0):
    """The roof object for a form that isn't a hip, or None for a hip."""
    if form == "skillion":
        return Skillion(fr, ring, plane_at_wall, pitch, azimuth_deg)
    if form == "gable":
        return Gable(fr, ring, plane_at_wall, pitch, azimuth_deg, ridge_offset_m)
    if form != "hip":
        raise ValueError(f"[house] roof_form must be hip, skillion or gable, not {form!r}")
    return None


def roof_options(cfg):
    """The keyword arguments for build() and roof_surface() from [house]."""
    return {"form": cfg.get("roof_form", "hip"), "azimuth_deg": cfg.get("roof_azimuth_deg"), "ridge_offset_m": cfg.get("roof_ridge_offset_m", 0.0)}


def roof_surface(fr, ring_local, cfg):
    """(x, y in the house frame) -> the roof's height there, -inf outside the eaves."""
    pitch = math.radians(cfg["roof_pitch_deg"])
    overhang = cfg["eaves_overhang_m"]
    eaves = Polygon(ring_local).buffer(overhang, join_style=2)
    opts = roof_options(cfg)
    roof = shaped_roof(fr, ring_local, cfg["roof_plane_at_wall"], pitch, opts["form"], opts["azimuth_deg"], opts["ridge_offset_m"])
    if roof:
        return lambda x, y: np.where(shapely.contains_xy(eaves, x, y), roof.height(x, y), -np.inf)
    rects = maximal_rectangles(eaves)
    z_edge = cfg["roof_plane_at_wall"] - math.tan(pitch) * overhang
    return lambda x, y: roof_height(rects, z_edge, pitch, x, y)


# --------------------------------------------------------------------------- faces

@dataclass
class Face:
    """A textured, wall-like face. Its mesh's uv is (u metres from the left end seen from
    outside, height). `spec` tells house_texture how to draw it when no photo covers it."""
    id: str
    mesh: UVMesh
    width: float
    z0: float
    z1: float
    spec: dict = field(default_factory=dict)


def face_quad(a, b, z0, z1, u0=0.0):
    """Vertical quad from plan point a (left, seen from outside) to b (right)."""
    m = UVMesh()
    L = float(np.hypot(b[0] - a[0], b[1] - a[1]))
    z0a, z0b = (z0, z0) if np.isscalar(z0) else z0
    z1a, z1b = (z1, z1) if np.isscalar(z1) else z1
    quad(m, (*a, z0a), (*b, z0b), (*b, z1b), (*a, z1a),
         [(u0, z0a), (u0 + L, z0b), (u0 + L, z1b), (u0, z1a)])
    return m, L


def outward_edges(ring):
    """Edges (a, b) of a ring, each ordered left to right as seen from outside."""
    pts = np.asarray(ring, float)[:, :2]
    if np.allclose(pts[0], pts[-1], rtol=0, atol=1e-9):  # absolute: grid coordinates are ~1e5
        pts = pts[:-1]
    ccw = signed_area(pts) > 0
    edges = []
    for i in range(len(pts)):
        a, b = pts[i], pts[(i + 1) % len(pts)]
        edges.append((a, b) if ccw else (b, a))
    return edges


# The kinds of part house.geojson can hold besides the walls and openings, and the viewer layer each goes in unless
# it names its own (`layer`).
PARTS = {"bay": "bay", "porch": "porch", "steps": "steps", "block": "outdoor", "wall": "outdoor",
         "balustrade": "outdoor", "chimney": "chimney", "tank": "fixtures", "retaining-walls": None}
COMPASS = {"north": (0.0, 1.0), "east": (1.0, 0.0), "south": (0.0, -1.0), "west": (-1.0, 0.0)}


def _compass(word, fr):
    """A compass direction ("north", "east", "south", "west") as a unit vector in the house frame."""
    vx, vy = COMPASS[word]
    return np.array([fr.c * vx + fr.s * vy, -fr.s * vx + fr.c * vy])


def _tread(a, b, back):
    """A tread from its nosing line a-b back to the line `back`: its ring, and its two ends as
    ((point on the nosing, point on the back line), outward direction)."""
    u = (b - a) / np.hypot(*(b - a))
    v = np.array([-u[1], u[0]])
    depth = float((back[0] - a) @ v)
    if depth < 0:
        v, depth = -v, -depth
    p0, p1 = (a, b) if u[0] * v[1] - u[1] * v[0] > 0 else (b, a)
    ring = [p0, p1, p1 + v * depth, p0 + v * depth]
    along = (p1 - p0) / np.hypot(*(p1 - p0))
    return ring, [((p0, p0 + v * depth), -along), ((p1, p1 + v * depth), along)]


def _porch(f, p, ring, fr, walls_poly, fl_upper, gmin, put, faces, openings_on, layer):
    """A porch: a landing at `floor`, a lean-to roof from `roof_at_wall` at the house down to `roof_at_edge`
    at its far side, walls on the sides named in `walls` (compass directions), posts at the open corners and
    a header over each open side. The porch is a rectangle on the house's grid with one side against the house."""
    x0, y0 = ring[:, 0].min(), ring[:, 1].min()
    x1, y1 = ring[:, 0].max(), ring[:, 1].max()
    # Turn the rectangle (by quarter turns) so that the side against the house is its x0 side.
    sides = {0: ((x0, (y0 + y1) / 2), (-1.0, 0.0)), 1: (((x0 + x1) / 2, y0), (0.0, -1.0)),
             2: ((x1, (y0 + y1) / 2), (1.0, 0.0)), 3: (((x0 + x1) / 2, y1), (0.0, 1.0))}
    house_side = min(sides, key=lambda k: walls_poly.exterior.distance(shapely.Point(sides[k][0])))
    turn = {0: 0, 1: 3, 2: 2, 3: 1}[house_side]           # quarter turns taking that side to x0
    rot = lambda q, k=turn: np.array([q[0], q[1]]) if k == 0 else rot(np.array([-q[1], q[0]]), k - 1)
    back = lambda q, k=(4 - turn) % 4: rot(q, k)
    c = np.array([rot(q) for q in ring])
    x0, y0 = c[:, 0].min(), c[:, 1].min()
    x1, y1 = c[:, 0].max(), c[:, 1].max()
    R = lambda pts: [tuple(back(np.asarray(q, float))) for q in pts]
    side_of = {}                                           # compass word -> "x1" | "y0" | "y1"
    for word in p.get("walls", []):
        d = rot(_compass(word, fr))
        side_of[word] = "x1" if d[0] > 0.7 else "y1" if d[1] > 0.7 else "y0" if d[1] < -0.7 else None
        if side_of[word] is None:
            raise DomusError(f"house.geojson: {p['id']} has a wall on its {word} side, which is the house")
    walled = set(side_of.values())
    names = {v: k for k, v in side_of.items()}
    prefix = p.get("faces", p["id"])
    floor = p["floor"]
    z_wall, z_edge_p = p["roof_at_wall"], p["roof_at_edge"]
    slope = (z_wall - z_edge_p) / (x1 - x0)
    zr = lambda x: z_wall - slope * (x - x0)
    wall_colour, roof_colour = p.get("trim_colour", "trim-white"), p.get("roof_colour", "roof")
    landing = R(rect_ring(x0, y0, x1, y1))
    put(block(landing, gmin(landing, 0.3), floor), p.get("landing_colour", "plaster"))
    th = 0.1
    under = 0.12                                           # roof build-up above the wall tops
    post = {(x1, y0): not {"x1", "y0"} <= walled, (x1, y1): not {"x1", "y1"} <= walled}
    spec = lambda side: {"kind": "porch", "fl": fl_upper, "group": f"{prefix}-{names[side]}", "layer": layer}
    if "y1" in walled:
        a, b = R([(x1, y1), (x0, y1)])
        m, length = face_quad(a, b, floor, (zr(x1) - under, zr(x0) - under))
        fid = f"{prefix}-{names['y1']}"
        faces.append(Face(fid, m, length, floor, zr(x0) - under, {**spec("y1"), "openings": openings_on(fid, np.array(a), np.array(b))}))
        nw = R(rect_ring(x0, y1 - th, x1, y1 - 0.002))
        put(block(nw, floor, [zr(rot(np.asarray(q))[0]) - under for q in nw]), wall_colour)
    if "x1" in walled:
        a, b = R([(x1, y0), (x1, y1)])
        m, length = face_quad(a, b, floor, zr(x1) - under)
        fid = f"{prefix}-{names['x1']}"
        faces.append(Face(fid, m, length, floor, zr(x1) - under, {**spec("x1"), "openings": openings_on(fid, np.array(a), np.array(b))}))
        put(block(R(rect_ring(x1 - th, y0 + (0.1 if post[(x1, y0)] else 0.0), x1 - 0.002,
                              y1 - (0.1 if post[(x1, y1)] else 0.0))), floor, zr(x1) - under), wall_colour)
    if "y0" in walled:
        a, b = R([(x0, y0), (x1, y0)])
        m, length = face_quad(a, b, floor, (zr(x0) - under, zr(x1) - under))
        fid = f"{prefix}-{names['y0']}"
        faces.append(Face(fid, m, length, floor, zr(x0) - under, {**spec("y0"), "openings": openings_on(fid, np.array(a), np.array(b))}))
        sw = R(rect_ring(x0, y0 + 0.002, x1, y0 + th))
        put(block(sw, floor, [zr(rot(np.asarray(q))[0]) - under for q in sw]), wall_colour)
    if post[(x1, y0)]:
        put(block(R(rect_ring(x1 - 0.1, y0, x1, y0 + 0.1)), floor, zr(x1) - under), wall_colour)
    if post[(x1, y1)]:
        put(block(R(rect_ring(x1 - 0.1, y1 - 0.1, x1, y1)), floor, zr(x1) - under), wall_colour)
    if "y0" not in walled:
        put(block(R(rect_ring(x0, y0, x1, y0 + 0.08)), zr(x1) - under - 0.15, zr(x1) - under), wall_colour)
    if "y1" not in walled:
        put(block(R(rect_ring(x0, y1 - 0.08, x1, y1)), zr(x1) - under - 0.15, zr(x1) - under), wall_colour)
    if "x1" not in walled:
        put(block(R(rect_ring(x1 - 0.08, y0, x1, y1)), zr(x1) - under - 0.15, zr(x1) - under), wall_colour)
    ov = 0.12
    roof_ring = [(x0, y0 - ov), (x1 + ov, y0 - ov), (x1 + ov, y1 + ov), (x0, y1 + ov)]
    put(block(R(roof_ring), [zr(q[0]) - 0.1 for q in roof_ring], [zr(q[0]) for q in roof_ring]), roof_colour)


# --------------------------------------------------------------------------- the house

@dataclass
class HouseModel:
    faces: list            # Face objects: walls, bay, porch walls
    solids: dict           # layer name -> list of (UVMesh, palette key)
    roof: UVMesh           # tile UVs in metres
    info: dict


def _ring(feature):
    return np.asarray(feature["geometry"]["coordinates"][0], float)[:-1]


def build(features, fl_upper, roof_pitch_deg, roof_plane_at_wall, overhang, ground, form="hip", azimuth_deg=None, ridge_offset_m=0.0):
    """Build the house from the features of house.geojson: the walls (id "house-walls"), openings (features with a
    `kind`) and other parts (features with a `part`, see PARTS and docs/site-format.md).

    fl_upper: the floor level the openings and parts are measured from, in the model's height datum.
    roof_plane_at_wall: height of the roof plane over the outside wall line (LiDAR fit). ground(e, n) -> heights
    (arrays) at site points.
    """
    walls_f = next(f for f in features if f["properties"].get("id") == "house-walls")
    ring_site = _ring(walls_f)
    fr = HouseFrame(ring_site)
    L = lambda f: fr.local(_ring(f))
    ring = fr.local(ring_site)
    walls_poly = Polygon(ring)
    wp = walls_f["properties"]
    soffit = wp["soffit"]
    pitch = math.radians(roof_pitch_deg)
    z_edge = roof_plane_at_wall - math.tan(pitch) * overhang
    shaped = shaped_roof(fr, ring, roof_plane_at_wall, pitch, form, azimuth_deg, ridge_offset_m)
    build_up = roof_plane_at_wall - soffit       # a shaped roof's underside: how far below it the soffit and wall tops are

    def under_roof(a, b, bottom=None):
        """A vertical face from a to b, its top following the roof less the build-up, and how far up it goes. With no bottom, the trim from there to the roof."""
        m, u, pts = UVMesh(), 0.0, [a, *shaped.crossings(a, b), b]
        for p, q in itertools.pairwise(pts):
            zp, zq = shaped.at(p), shaped.at(q)
            low, top = (bottom, (zp - build_up, zq - build_up)) if bottom is not None else ((zp - build_up, zq - build_up), (zp, zq))
            part, length = face_quad(p, q, low, top, u)
            m.extend(part)
            u += length
        return m, u, max(shaped.at(p) for p in pts) - (build_up if bottom is not None else 0.0)

    def g(x, y):
        e, n = fr.site(np.column_stack([np.atleast_1d(x), np.atleast_1d(y)])).T
        return np.asarray(ground(e, n), float)

    def gmin(pts, pad=0.3):
        p = np.asarray(pts, float)
        dense = np.vstack([a + (b - a) * t for a, b in itertools.pairwise(np.vstack([p, p[:1]]))
                           for t in np.linspace(0, 1, 6)])
        return float(np.nanmin(g(dense[:, 0], dense[:, 1]))) - pad

    faces, solids = [], {}

    def solid(layer, mesh, key):
        solids.setdefault(layer, []).append((mesh, key))

    # Openings grouped by face, in face coordinates (u along the face from its left end).
    openings = {}
    for f in (f for f in features if "kind" in f["properties"]):
        p = f["properties"]
        a, b = fr.local(np.asarray(f["geometry"]["coordinates"], float)[:, :2])
        sill = p["sill"] if "sill" in p else fl_upper + p["sill_above_fl"]
        head = p["head"] if "head" in p else fl_upper + p["head_above_fl"]
        openings.setdefault(p["face"], []).append({"id": p["id"], "kind": p["kind"], "a": a, "b": b,
                                                   "sill": sill, "head": head})

    def openings_on(face_id, a, b):
        d = (b - a) / np.hypot(*(b - a))
        out = []
        for o in openings.get(face_id, []):
            u = sorted([float((o["a"] - a) @ d), float((o["b"] - a) @ d)])
            out.append({**o, "u0": u[0], "u1": u[1]})
        return out

    # Walls: one face per ring edge, from below the ground to the eaves soffit.
    names = wp.get("faces") or [f"wall-{i}" for i in range(len(ring))]
    for (a, b), name in zip(outward_edges(ring), names):
        bottom = gmin([a, b], 0.3)
        m, length, top = under_roof(a, b, bottom) if shaped else (*face_quad(a, b, bottom, soffit), soffit)
        faces.append(Face(name, m, length, bottom, top, {
            "kind": "wall", "layer": "walls", "plinth_top": wp.get("plinth_top", {}).get(name, bottom + 0.3),
            "openings": openings_on(name, a, b), "fl": fl_upper}))

    # Roof over the walls plus eaves, its fascia and soffit.
    eaves = walls_poly.buffer(overhang, join_style=2)
    rects = [] if shaped else maximal_rectangles(eaves)
    roof = shaped.mesh(eaves) if shaped else roof_mesh(rects, z_edge, pitch)
    rim = np.asarray(eaves.exterior.coords)[:-1]
    fascia = UVMesh()
    for a, b in outward_edges(rim):
        if shaped:
            fascia.extend(under_roof(a, b)[0])
        else:
            fascia.extend(face_quad(a, b, soffit, z_edge)[0])
    solid("eaves", fascia, "trim-white")
    if shaped:
        for part in shaped.sides(eaves.difference(walls_poly)):
            under = polygon_mesh(part, soffit, up=False)
            under.vertices = np.column_stack([under.vertices[:, :2], shaped.height(under.vertices[:, 0], under.vertices[:, 1]) - build_up])
            solid("eaves", under, "soffit")
    else:
        solid("eaves", polygon_mesh(eaves.difference(walls_poly), soffit, up=False), "soffit")

    # The house's other parts, in the order house.geojson lists them. Each names its kind in `part`.
    unnamed = [f["properties"].get("id") for f in features
               if f is not walls_f and "part" not in f["properties"] and "kind" not in f["properties"]]
    if unnamed:
        print(f"house.geojson: {', '.join(map(str, unnamed))} not drawn: give each a `part` (docs/site-format.md, House parts)")
    for f in features:
        p = f["properties"]
        kind = p.get("part")
        if kind is None:
            continue
        if kind not in PARTS:
            raise DomusError(f"house.geojson: {p.get('id')} has part = {kind!r}; the parts are {', '.join(PARTS)}")
        if PARTS[kind] is None:                            # read by the checks, not drawn here
            continue
        layer = p.get("layer", PARTS[kind])
        put = lambda mesh, colour, layer=layer: solid(layer, mesh, colour)

        if kind == "bay":
            # A bay window: its faces on the wall, a skirt underneath and a flat hood.
            bay = L(f)
            z_b, z_t = fl_upper + p["bottom_above_fl"], fl_upper + p["top_above_fl"]
            hood_top = fl_upper + p["hood_top_above_fl"]
            on_wall = [i for i, q in enumerate(bay) if walls_poly.exterior.distance(shapely.Point(q)) < 0.01]
            k = next(i for i in range(len(bay)) if i in on_wall and (i + 1) % len(bay) in on_wall)
            outline = [bay[(k + 1 + j) % len(bay)] for j in range(len(bay))]  # wall point, ..., wall point
            u = 0.0
            sides = [(outline[j], outline[j + 1]) for j in range(len(outline) - 1)]
            if signed_area(np.asarray(bay)) < 0:            # make them run left to right seen from outside
                sides = [(b, a) for a, b in reversed(sides)]
            group = p.get("faces", p["id"])
            for j, (a, b) in enumerate(sides):
                m, length = face_quad(a, b, z_b, z_t, u)
                faces.append(Face(f"{group}-{j}", m, length, z_b, z_t, {"kind": "bay", "fl": fl_upper, "u0": u,
                                                                        "group": group, "layer": layer}))
                u += length
            bay_poly = Polygon(bay)
            put(polygon_mesh(bay_poly, z_b, up=False), p.get("skirt_colour", "trim-tan"))
            hood = bay_poly.buffer(p.get("hood_overhang_m", 0.08), join_style=2).difference(
                walls_poly.buffer(-0.001, join_style=2))
            hood = max(getattr(hood, "geoms", [hood]), key=lambda q: q.area)
            put(block(np.asarray(hood.exterior.coords), z_t, hood_top), p.get("trim_colour", "trim-white"))

        elif kind == "porch":
            _porch(f, p, L(f), fr, walls_poly, fl_upper, gmin, put, faces, openings_on, layer)

        elif kind == "steps":
            # A flight: each nosing line (with its tread's height) runs back to `back_line` as a solid tread.
            back = fr.local(np.asarray(p["back_line"], float))[:, :2]
            for ln in f["geometry"]["coordinates"]:
                a, b = fr.local(np.asarray(ln, float))[:, :3]
                z = float(a[2])
                ring_s, ends = _tread(a[:2], b[:2], back)
                bottom = p["base"] if "base" in p else gmin(ring_s, p.get("sink_m", 0.3))
                put(block(ring_s, bottom, z), p.get("colour", "step-grey"))
                for side in p.get("open_sides", []):
                    # an open side of the flight, finished in `side_colour`
                    (s0, s1), out = min(ends, key=lambda e: -float(e[1] @ _compass(side, fr)))
                    o = out * 0.003
                    side_m = UVMesh()
                    quad(side_m, (*(s0 + o), bottom), (*(s1 + o), bottom), (*(s1 + o), z - 0.03), (*(s0 + o), z - 0.03))
                    put(side_m, p.get("side_colour", "plaster"))

        elif kind == "block":
            # A solid with a flat top: a landing, a deck, a step, a planter, a unit on the ground.
            ring_b = L(f)
            gz = gmin(ring_b, 0.0)
            if "top" in p:
                top = p["top"]
            elif "top_below_fl_m" in p:
                top = fl_upper - p["top_below_fl_m"]
            else:
                top = gz + p["height_m"]
            base = p["base"] if "base" in p else top - p["thickness_m"] if "thickness_m" in p else gz - p.get("sink_m", 0.05)
            put(block(ring_b, base, top), p.get("colour", "concrete"))

        elif kind == "wall":
            # A freestanding wall from the line's ends, its top at their heights.
            (xa, ya, za), (xb, yb, zb) = fr.local(np.asarray(f["geometry"]["coordinates"], float))
            t = p["thickness_m"]
            d = np.array([xb - xa, yb - ya]) / math.hypot(xb - xa, yb - ya)
            off = np.array([-d[1], d[0]]) * t / 2
            a, b = np.array([xa, ya]), np.array([xb, yb])
            base = p["base"] if "base" in p else gmin([a, b], p.get("sink_m", 0.1))
            put(block([a - off, b - off, b + off, a + off], base, [za, zb, zb, za]), p.get("colour", "concrete"))

        elif kind == "balustrade":
            top = p["top"] if "top" in p else fl_upper - p["top_below_fl_m"]
            for ln in f["geometry"]["coordinates"]:
                line = fr.local(np.asarray(ln, float))[:, :2]
                _slatted(solids, line, top, p["height_m"], g, walls_poly, layer, p.get("colour", "deck-dark"))

        elif kind == "chimney":
            # A square stack from the roof, a round flue pot and a cowl.
            cr = L(f)
            z_roof = float(np.min(shaped.height(cr[:, 0], cr[:, 1]) if shaped else roof_height(rects, z_edge, pitch, cr[:, 0], cr[:, 1])))
            put(block(cr, z_roof - 0.3, p["stack_top"]), p.get("colour", "chimney"))
            cx, cy = cr[:, 0].mean(), cr[:, 1].mean()
            if "pot_top" in p:
                put(cylinder(cx, cy, 0.15, p["stack_top"], p["pot_top"]), p.get("colour", "chimney"))
                if "cowl_top" in p:
                    put(cylinder(cx, cy, 0.19, p["pot_top"], p["cowl_top"]), "metal")

        elif kind == "tank":
            tx, ty = fr.local(np.asarray(f["geometry"]["coordinates"], float)[None, :2])[0]
            gz = float(g(tx, ty)[0])
            top = p.get("top", gz + p.get("height_m", 1.5))
            put(cylinder(tx, ty, p["radius_m"], gz - 0.05, top), p.get("colour", "tank"))

    # Back to site coordinates.
    for f in faces:
        f.mesh.vertices = fr.site(f.mesh.vertices)
    for parts in solids.values():
        for m, _ in parts:
            m.vertices = fr.site(m.vertices)
    roof.vertices = fr.site(roof.vertices)
    info = {"orientation_deg": round(math.degrees(fr.theta), 3), "roof_rectangles": len(rects),
            "roof_pitch_deg": roof_pitch_deg, "roof_eave_edge": round(z_edge, 3),
            "ridge_max": round(float(roof.vertices[:, 2].max()), 3), "soffit": soffit,
            "walls_area_m2": round(walls_poly.area, 2)}
    if shaped:
        info.update(roof_form=form, roof_azimuth_deg=azimuth_deg, roof_eave_edge=round(float(roof.vertices[:, 2].min()), 3))
    return HouseModel(faces, solids, roof, info)


def _slatted(solids, line, deck_top, height, g, walls_poly, layer, colour, pitch=0.1, width=0.065, thick=0.025):
    """Slatted balustrade above the deck and slatted skirt below it along a polyline, with
    posts at the corners and every 1.8 m, and a top rail. Slats are single quads (the viewer
    draws both sides), which keeps the layer small."""
    bal, skirt = UVMesh(), UVMesh()
    for a, b in itertools.pairwise(line):
        seg = b - a
        length = float(np.hypot(*seg))
        d = seg / length
        nrm = np.array([-d[1], d[0]]) * thick
        n = max(int(length / pitch), 1)
        for i in range(n):
            c = a + d * (i + 0.5) * length / n
            p, q = c - d * width / 2, c + d * width / 2
            quad(bal, (*p, deck_top), (*q, deck_top), (*q, deck_top + height - 0.05), (*p, deck_top + height - 0.05))
            gz = float(g(c[0], c[1])[0])
            if np.isfinite(gz) and gz < deck_top - 0.25:
                quad(skirt, (*p, gz - 0.05), (*q, gz - 0.05), (*q, deck_top - 0.2), (*p, deck_top - 0.2))
        rail = [a - d * 0.03, b + d * 0.03, b + d * 0.03 + nrm * 3.6, a - d * 0.03 + nrm * 3.6]
        bal.extend(block([q - nrm * 1.3 for q in rail], deck_top + height - 0.05, deck_top + height))
        posts = max(int(math.ceil(length / 1.8)), 1)
        for i in range(posts + 1):
            c = a + seg * i / posts
            if walls_poly.distance(shapely.Point(c)) < 0.05:
                continue
            gz = float(g(c[0], c[1])[0])
            ring = rect_ring(c[0] - 0.05, c[1] - 0.05, c[0] + 0.05, c[1] + 0.05)
            bal.extend(block(ring, (gz if np.isfinite(gz) else deck_top - 1) - 0.05, deck_top + height + 0.05))
    solids.setdefault(layer, []).extend([(bal, colour), (skirt, colour)])
