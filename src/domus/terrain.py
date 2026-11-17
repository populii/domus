"""Ground surface: the LiDAR ground model, with retaining walls cut in as breaklines.

The LiDAR DEM is a 1 m grid, so a wall shows up in it as a 1-2 m wide slope. Walls are
therefore inserted as breaklines: grid points close to a wall are dropped and replaced by
rows of points along the wall's low-side face (at its toe level) and high-side face (at its
top level), then everything is triangulated.

Where the LiDAR ground is known to be wrong (steep banks under trees, where it only
interpolates), data/site/ground-fixes.geojson replaces it: its polygons drop the LiDAR
ground inside them and its 3D lines and points stand in.
"""

import itertools
from functools import lru_cache

import numpy as np
import shapely
from scipy.interpolate import LinearNDInterpolator
from scipy.spatial import Delaunay
from shapely.ops import unary_union

from . import geojson, raster
from .crs import ROOT
from .mesh import Mesh, cross2, offset_behind

FIXES = ROOT / "data" / "site" / "ground-fixes.geojson"


def grid(extent, step):
    e0, n0, e1, n1 = extent
    es = np.arange(e0, e1 + step / 2, step)
    ns = np.arange(n0, n1 + step / 2, step)
    return np.meshgrid(es, ns)


def resample_line(line, spacing):
    """Points about every `spacing` metres along a polyline and at each of its own vertices (its
    corners and steps), and their distances along it."""
    pts = np.asarray(line, float)
    seg = np.hypot(*np.diff(pts[:, :2], axis=0).T)
    s = np.concatenate([[0], np.cumsum(seg)])
    t = np.linspace(0, s[-1], max(int(np.ceil(s[-1] / spacing)) + 1, 2))
    t = np.union1d(t[np.abs(t[:, None] - s[None, :]).min(axis=1) > 0.1 * spacing], s)
    out = np.column_stack([np.interp(t, s, pts[:, k]) for k in range(pts.shape[1])])
    return out, t


def line_normals(pts):
    n = len(pts)
    out = []
    for i in range(n):
        a = pts[max(i - 1, 0), :2]
        b = pts[min(i + 1, n - 1), :2]
        d = (b - a) / np.hypot(*(b - a))
        out.append([-d[1], d[0]])
    return np.array(out)


def distance_to_polyline(e, n, line):
    p = np.column_stack([np.ravel(e), np.ravel(n)])
    best = np.full(len(p), np.inf)
    pts = np.asarray(line, float)[:, :2]
    for a, b in itertools.pairwise(pts):
        ab = b - a
        t = np.clip(((p - a) @ ab) / (ab @ ab), 0, 1)
        d = np.hypot(*(p - (a + t[:, None] * ab)).T)
        best = np.minimum(best, d)
    return best.reshape(np.shape(e))


class Ground:
    """The ground without walls: the LiDAR DEM, with the curated fixes applied.

    `areas` are polygons (Nx2) inside which the DEM is not used; `points` (Mx3) are the
    fix lines (resampled) and points. Inside the areas the ground is the triangulation of
    those points and the DEM grid points around the areas.
    """

    def __init__(self, dem, areas=(), points=(), step=0.5):
        self.dem = dem
        self.points = np.asarray(points, float).reshape(-1, 3)
        self.region = unary_union([shapely.Polygon(a) for a in areas]) if len(areas) else None
        self.interp = None
        if self.region is not None:
            E, N = grid(self.region.buffer(3.0).bounds, step)
            H = dem.sample(E, N)
            ok = np.isfinite(H) & ~self.inside(E, N)
            pts = np.vstack([np.column_stack([E[ok], N[ok], H[ok]]), self.points])
            self.interp = LinearNDInterpolator(pts[:, :2], pts[:, 2])

    def inside(self, e, n):
        if self.region is None:
            return np.zeros(np.shape(e), bool)
        return shapely.contains_xy(self.region, e, n)

    def sample(self, e, n):
        e = np.asarray(e, float)
        n = np.asarray(n, float)
        h = np.array(self.dem.sample(e, n), float)
        fix = self.inside(e, n)
        if fix.any():
            h[fix] = self.interp(e[fix], n[fix])
        return h


def read_fixes(path=FIXES):
    """Fix areas (polygon rings) and fix points (lines resampled every 0.25 m, points and multipoints)
    from a GeoJSON file."""
    areas, points = [], [np.empty((0, 3))]
    if not path.exists():
        return areas, points[0]
    for f in geojson.read(path)["features"]:
        g = f["geometry"]
        if g["type"] == "Polygon":
            areas.append(np.asarray(g["coordinates"][0], float)[:, :2])
        elif g["type"] == "LineString":
            points.append(resample_line(g["coordinates"], 0.25)[0])
        elif g["type"] == "Point":
            points.append(np.asarray([g["coordinates"]], float))
        elif g["type"] == "MultiPoint":
            points.append(np.asarray(g["coordinates"], float).reshape(-1, 3))
    return areas, np.vstack(points)


@lru_cache
def ground():
    """Ground model without walls: the LiDAR DEM with data/site/ground-fixes.geojson applied."""
    return Ground(raster.dem(), *read_fixes())


def bay_levels(line, bays, chain):
    """For a wall whose top is level along each stretch of its line (`bays`, one level per stretch), the level of
    the bay that ends and of the bay that starts at each distance `chain` along it. The two differ only at a
    point of the line where the wall steps."""
    full = np.concatenate([[0], np.cumsum(np.hypot(*np.diff(np.asarray(line, float)[:, :2], axis=0).T))])
    bays = np.asarray(bays, float)
    at = np.argmin(np.abs(np.asarray(chain, float)[:, None] - full[None, :]), axis=1)     # the nearest point of the line
    on = np.abs(np.asarray(chain, float) - full[at]) < 1e-6
    seg = np.clip(np.searchsorted(full, chain, side="right") - 1, 0, len(bays) - 1)
    before = np.where(on, bays[np.clip(at - 1, 0, len(bays) - 1)], bays[seg])
    after = np.where(on, bays[np.clip(at, 0, len(bays) - 1)], bays[seg])
    return before, after


def wall_levels(wall, spacing=0.25, probe=0.6):
    """Resample a wall and work out its toe (low side) and top levels along it.

    `wall` is a dict with `line` (Nx2 or Nx3 site coords), `side` (+1 if the retained, high
    side is left of the line direction, -1 if right), `thickness`, and either per-vertex
    `top` heights or a `height` above the toe. The toe comes from the ground model `probe`
    metres out on the low side (for a battered face, 0.2 m in front of its foot if that is
    further) unless `toe` is given.

    With `bay_tops` (a level for each stretch of the line) the top is level along each stretch, and where
    it steps the point is there twice, once at each level, so the top drops straight down.
    """
    pts, chain = resample_line(wall["line"], spacing)
    nrm = line_normals(pts) * wall["side"]
    if wall.get("toe") is not None:
        toe = np.interp(chain, wall["toe_chain"], wall["toe"])
    else:
        low = pts[:, :2] - nrm * max(probe, 0.5 * wall.get("batter", 0.0) + 0.2)
        toe = ground().sample(low[:, 0], low[:, 1])
    if wall.get("bay_tops") is not None:
        before, after = bay_levels(wall["line"], wall["bay_tops"], chain)
        twice = np.repeat(np.arange(len(chain)), np.where(np.isclose(before, after), 1, 2))
        second = np.concatenate([[False], twice[1:] == twice[:-1]])
        pts, nrm, chain, toe = pts[twice], nrm[twice], chain[twice], toe[twice]
        top = np.where(second, after[twice], before[twice])
    elif wall.get("top") is not None:
        top = np.interp(chain, wall["top_chain"], wall["top"])
    else:
        top = toe + wall["height"]
    return pts[:, :2], nrm, chain, toe, top


def outline_points(poly, spacing=0.2):
    """Points about every `spacing` along a polygon's rings and at their corners, with the unit normal pointing out
    of the polygon at each (at a corner, the mean of its two edges')."""
    pts, nrm = [], []
    for geom in getattr(poly, "geoms", [poly]):
        if geom.geom_type != "Polygon" or geom.is_empty:
            continue
        geom = shapely.geometry.polygon.orient(geom, 1.0)       # exterior counter-clockwise: the outside is on the right
        for ring in [geom.exterior, *geom.interiors]:
            xy = np.asarray(ring.coords)[:, :2]
            for a, b in itertools.pairwise(xy):
                length = np.hypot(*(b - a))
                if length < 1e-9:
                    continue
                t = (b - a) / length
                for d in np.linspace(0, length, max(int(np.ceil(length / spacing)), 1), endpoint=False):
                    pts.append(a + t * d)
                    nrm.append([t[1], -t[0]])
    return np.asarray(pts, float).reshape(-1, 2), np.asarray(nrm, float).reshape(-1, 2)


def build(extent, step, walls=(), surface=None, edges=()):
    """Triangulated ground surface over `extent` (E0, N0, E1, N1). `surface(E, N)` replaces the
    ground model (a wall option's designed ground); the fix points stay as breaklines only where it
    leaves the ground as it is.

    `edges` are (polygon, hidden) pairs whose outline is a clean edge in the surface, where it would otherwise
    be smeared over a grid cell: a flight of steps, with the ground beside it on one side of the line and the
    ground under it on the other; or with `hidden` a building, where the ground runs level to the wall and
    meets whatever is under the building just inside it, out of sight."""
    E, N = grid(extent, step)
    g = ground()
    # Inside the fix areas the grid takes the fixed ground, so the surface has no long triangles
    # between sparse fix lines; the fix points themselves go in as breaklines.
    H = g.sample(E, N) if surface is None else surface(E, N)
    keep = np.isfinite(H)
    clear = np.zeros_like(keep)
    fix = g.points
    if surface is not None and len(fix):
        fix = fix[np.abs(surface(fix[:, 0], fix[:, 1]) - fix[:, 2]) < 0.01]
    breaks = []
    stood = []      # where the walls stand: each one's face line and the ground it covers
    for w in walls:
        pts, nrm, chain, toe, top = wall_levels(w)
        # Behind an option's wall the ground is the ground it holds, which a wall standing above it (an upstand,
        # or a level top over falling ground) leaves below its top. An existing wall's top is its ground.
        held = top if w.get("retained") is None else np.minimum(top, np.interp(chain, w["toe_chain"], w["retained"]))
        once = np.concatenate([[True], np.hypot(*np.diff(pts, axis=0).T) > 1e-9])    # a stepped top has its points twice
        pts, nrm, chain, toe, held = pts[once], nrm[once], chain[once], toe[once], held[once]
        half = 0.5 * w.get("batter", 0.0)
        clear |= distance_to_polyline(E, N, pts) < max(0.75, w["thickness"] + 0.5 + half)
        # The ground keeps its level right up to the foot of the wall's face and to its back: the step between the
        # two is inside the wall, where it does not show.
        face = pts - nrm * half
        back = pts + nrm * half + offset_behind(pts, w["thickness"], w["side"]) - pts   # as the wall's own back
        stood.append((pts, shapely.Polygon(np.vstack([face, back[::-1]])).buffer(0.05)))
        breaks.append(np.column_stack([face, toe]))
        breaks.append(np.column_stack([back, held]))
        if half:
            # Under a battered face the ground stays at the toe, clear of the face; not at the wall's
            # ends, where a low row would show as a notch.
            inner = (chain > 0.3) & (chain < chain[-1] - 0.3)
            breaks.append(np.column_stack([pts[inner] + nrm[inner] * half, toe[inner]]))
    sample = g.sample if surface is None else surface
    e0, n0, e1, n1 = extent
    for poly, hidden in edges:
        at, out = outline_points(poly)
        inside = (at[:, 0] > e0 + step) & (at[:, 0] < e1 - step) & (at[:, 1] > n0 + step) & (at[:, 1] < n1 - step)
        for line, body in stood:        # a wall along the outline already gives the ground its edge there
            inside &= (distance_to_polyline(at[:, 0], at[:, 1], line) > 0.08) & ~shapely.contains_xy(body, at[:, 0], at[:, 1])
        at, out = at[inside], out[inside]
        if not len(at):
            continue
        for geom in getattr(poly, "geoms", [poly]):
            for ring in [geom.exterior, *geom.interiors]:
                clear |= distance_to_polyline(E, N, np.asarray(ring.coords)) < 0.18
                if len(fix):        # a fix point on the outline would hold the old ground against the new edge
                    fix = fix[distance_to_polyline(fix[:, 0], fix[:, 1], np.asarray(ring.coords)) > 0.2]
        # two rows of points close together: the ground outside the outline, and the ground inside it
        rows = ((-0.02, 0.10), (-0.10, -0.10)) if hidden else ((0.02, 0.07), (-0.02, -0.07))
        for where, taken_at in rows:
            p, q = at + out * where, at + out * taken_at
            h = sample(q[:, 0], q[:, 1])
            ok = np.isfinite(h)
            breaks.append(np.column_stack([p[ok], h[ok]]))
    if len(fix):
        breaks.append(fix)
    pts = np.column_stack([E[keep & ~clear], N[keep & ~clear], H[keep & ~clear]])
    if breaks:
        pts = np.vstack([pts] + breaks)
    # Triangulated about the points' own middle: in whole grid coordinates (hundreds of kilometres) the arithmetic
    # cannot tell apart points a few centimetres from each other, as the two rows along an edge are.
    tri = Delaunay(pts[:, :2] - pts[:, :2].mean(axis=0))
    faces = tri.simplices
    # Drop sliver triangles along the hull.
    a, b, c = (pts[faces[:, k], :2] for k in range(3))
    area = 0.5 * np.abs(cross2(b - a, c - a))
    longest = np.max([np.hypot(*(b - a).T), np.hypot(*(c - b).T), np.hypot(*(a - c).T)], axis=0)
    good = (area > 1e-6) & (longest < 4 * step)
    faces = faces[good]
    # Orient counter-clockwise seen from above.
    a, b, c = (pts[faces[:, k], :2] for k in range(3))
    flip = cross2(b - a, c - a) < 0
    faces[flip] = faces[flip][:, [0, 2, 1]]
    return Mesh(pts, faces)


def contours(E, N, H, levels):
    """Marching squares over a grid: returns {level: [(x0, y0, x1, y1), ...]} segments."""
    out = {}
    for lv in levels:
        segs = []
        z = H - lv
        z00, z10, z11, z01 = z[:-1, :-1], z[:-1, 1:], z[1:, 1:], z[1:, :-1]
        e00, e10 = E[:-1, :-1], E[:-1, 1:]
        n00, n01 = N[:-1, :-1], N[1:, :-1]
        valid = np.isfinite(z00) & np.isfinite(z10) & np.isfinite(z11) & np.isfinite(z01)
        code = ((z00 > 0) * 1 + (z10 > 0) * 2 + (z11 > 0) * 4 + (z01 > 0) * 8)
        for (i, j) in zip(*np.nonzero(valid & (code > 0) & (code < 15))):
            corners = [(e00[i, j], n00[i, j], z00[i, j]), (e10[i, j], n00[i, j], z10[i, j]),
                       (e10[i, j], n01[i, j], z11[i, j]), (e00[i, j], n01[i, j], z01[i, j])]
            cross = []
            for k in range(4):
                (xa, ya, za), (xb, yb, zb) = corners[k], corners[(k + 1) % 4]
                if (za > 0) != (zb > 0):
                    t = za / (za - zb)
                    cross.append((xa + t * (xb - xa), ya + t * (yb - ya)))
            if len(cross) == 2:
                segs.append((*cross[0], *cross[1]))
            elif len(cross) == 4:
                segs.append((*cross[0], *cross[1]))
                segs.append((*cross[2], *cross[3]))
        out[lv] = segs
    return out
