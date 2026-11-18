"""The ground as a triangulated surface (a TIN), with retaining walls cut in as breaklines.

A LiDAR DEM is a grid, 1 m on New Zealand's, so a wall shows up in it as a 1-2 m wide slope. Walls are therefore
inserted as breaklines: grid points close to a wall are dropped and replaced by rows of points along the wall's
low-side face (at its toe level) and high-side face (at its top level), then everything is triangulated.

Where the ground model is known to be wrong (steep banks under trees, where LiDAR only interpolates), a site's
ground fixes replace it: their polygons drop the ground model inside them and their 3D lines and points stand in.

The surface drawn that way is the best ground there is, and DrawnGround reads it as a ground: the wall options are
worked out on it.

A pure part of the model: it reads no site, and takes the ground model, its fixes and the model's origin from the
caller (`domus.terrain` gives a site's).
"""

import itertools

import numpy as np
import shapely
from scipy.interpolate import LinearNDInterpolator
from scipy.spatial import Delaunay
from shapely.ops import unary_union

from ..geo import geojson
from ..geo.mesh import Mesh, Surface, cross2, offset_behind, resample_line
from .walls import wall_levels


def grid(extent, step):
    e0, n0, e1, n1 = extent
    es = np.arange(e0, e1 + step / 2, step)
    ns = np.arange(n0, n1 + step / 2, step)
    return np.meshgrid(es, ns)


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


class DrawnGround:
    """The ground as the build draws it, read like a Ground (sample(e, n)): the heights of `surface`, the triangulated
    ground build() makes of `ground` with the existing walls cut in, read in its own triangles, and `ground`'s where
    it has none, outside the area drawn. So the ground the wall options are designed on and measured from is the ground
    the model shows: the LiDAR only where nothing better is known, the fixes where there are some, and the walls' faces
    and tops."""

    def __init__(self, surface, ground):
        self.surface, self.ground = surface, ground
        self.points = ground.points         # the fixes, as build() keeps them as breaklines
        self._triangles = Surface(surface.vertices, surface.faces)

    def sample(self, e, n):
        e = np.asarray(e, float)
        n = np.asarray(n, float)
        h = self._triangles.z(e, n)
        off = ~np.isfinite(h)
        if off.any():
            h[off] = self.ground.sample(e[off], n[off])
        return h


def read_fixes(path):
    """Fix areas (polygon rings) and fix points (lines resampled every 0.25 m, points and multipoints)
    from a GeoJSON file, or none if there's no file at `path`."""
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


def cut_cells(faces, cell, heights):
    """Triangles with each whole grid cell cut along the diagonal whose ends are closer in height. A cell's four corners
    lie on a circle, so Delaunay may cut it either way; along the closer pair a bank is drawn down its slope, where
    the other diagonal would draw a ridge or a gully across it. `cell` gives each point's (row, column) on the grid,
    or (-1, -1) for a point off it (a breakline's)."""
    faces = np.asarray(faces).copy()
    edges = np.concatenate([faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [2, 0]]])
    other = np.concatenate([faces[:, 2], faces[:, 0], faces[:, 1]])
    tri = np.tile(np.arange(len(faces)), 3)
    key = np.sort(edges, axis=1)
    order = np.lexsort((key[:, 1], key[:, 0]))
    key, other, tri = key[order], other[order], tri[order]
    twin = np.flatnonzero((key[1:] == key[:-1]).all(axis=1))           # an edge two triangles share: k and k + 1
    a, b, c, d = key[twin, 0], key[twin, 1], other[twin], other[twin + 1]
    on = (cell[a, 0] >= 0) & (cell[b, 0] >= 0) & (cell[c, 0] >= 0) & (cell[d, 0] >= 0)
    ra, rb, rc, rd = cell[a], cell[b], cell[c], cell[d]
    # a and b opposite corners of one cell, c and d its other two
    diagonal = on & (np.abs(ra - rb) == 1).all(axis=1)
    corners = ((rc == np.column_stack([ra[:, 0], rb[:, 1]])).all(axis=1) & (rd == np.column_stack([rb[:, 0], ra[:, 1]])).all(axis=1)) | \
              ((rd == np.column_stack([ra[:, 0], rb[:, 1]])).all(axis=1) & (rc == np.column_stack([rb[:, 0], ra[:, 1]])).all(axis=1))
    flip = diagonal & corners & (np.abs(heights[c] - heights[d]) < np.abs(heights[a] - heights[b]) - 1e-9)
    faces[tri[twin][flip]] = np.column_stack([c[flip], d[flip], a[flip]])
    faces[tri[twin + 1][flip]] = np.column_stack([d[flip], c[flip], b[flip]])
    return faces


def build(extent, step, ground, origin, walls=(), surface=None, edges=()):
    """Triangulated ground surface over `extent` (E0, N0, E1, N1), from `ground`, the ground model without walls (a
    Ground: anything with sample(e, n) and its fix `points`), triangulated about the model's `origin` (E, N).
    `surface(E, N)` replaces the ground model (a wall option's designed ground); the fix points stay as breaklines only
    where it leaves the ground as it is.

    `edges` are (polygon, hidden) pairs whose outline is a clean edge in the surface, where it would otherwise
    be smeared over a grid cell: a flight of steps, with the ground beside it on one side of the line and the
    ground under it on the other; or with `hidden` a building, where the ground runs level to the wall and
    meets whatever is under the building just inside it, out of sight."""
    E, N = grid(extent, step)
    g = ground
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
        pts, nrm, chain, toe, top = wall_levels(w, ground)
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
    cell = np.column_stack(np.nonzero(keep & ~clear))          # each grid point's row and column, in the same order
    if breaks:
        pts = np.vstack([pts] + breaks)
        cell = np.vstack([cell, np.full((len(pts) - len(cell), 2), -1)])
    # Triangulated about the site's origin, to the micrometre: in whole grid coordinates (hundreds of kilometres) the
    # arithmetic cannot tell apart points a few centimetres from each other, as the two rows along an edge are, and
    # from the origin the same site on any grid gives the same triangles.
    tri = Delaunay(np.round(pts[:, :2] - np.asarray(origin[:2], float), 6))
    faces = cut_cells(tri.simplices, cell, pts[:, 2])
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
