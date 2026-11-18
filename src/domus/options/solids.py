"""A wall option as closed solids, with Manifold (manifold3d): the ground as it is and as designed, the new walls and
steps, and the soil the option cuts and fills, built from the option's own shapes on the ground the build draws.

domus.options.scheme works an option out point by point; the solids follow the same rules as shapes:

- The ground as it is: the triangles of the ground the build draws (a site's tin.DrawnGround), closed into a solid
  with sides and a flat bottom below everything.
- The ground the option starts from (Design.base): the ground as it is, but where a wall it takes away stood
  (Design.gone), at that wall's toe, the lowest where two meet. Its batters raise and cut that, and where nothing
  else is, it's the designed ground.
- A terrace: its outline extruded and cut by its plane, keeping what's below (with its own level at each corner, the
  triangles Domus interpolates those levels on). The first terrace a point is in gives its level there.
- A batter from a terrace's free edge (Design.free, Design.batters) raises the ground to the terrace's level at the
  edge's nearest point less the distance over the batter, and cuts it to that level plus the distance, as far as
  BATTER_REACH_M. Where the level is the same all along the edge, that's the convex hull of a cone at each end of each
  straight piece of the edge (its apex at the level, widening 1 in `batter` out to the reach, carried on down as a
  cylinder). Where the level changes along the edge, each piece gives the batter only in its own part of the plan,
  where it's the nearest piece (from the lower envelope of the distances to the pieces), and there it's a wedge along
  the piece, or a cone round whichever end is nearest. Upturned, the same shapes hold what the batters cut away, and
  of all the terraces' batters the highest raise and the lowest cut are taken.
- A flight of steps: under its outline the ground it stands on (Stair.z), a ramp, or on a foot that slopes across it
  the higher of the ramp and the ground at its foot carried in level under it. The last flight a point is under gives
  its ground.
- The soil (scheme.soil_moved): before, the ground as it is, but at its toe inside an existing wall; after, in a new
  wall's trench (scheme.wall_edges: its body, unless it says how it's built) at the trench's bottom, and inside an
  existing wall the option keeps at its toe (the lowest where walls meet); under a flight of steps, its ramp; in a
  terrace, its level less any paving; and where a batter moves the ground, the batter. Everywhere else the soil stays
  as it was. The soil a new wall's option gives over its drainage aggregate (`seal_m`, scheme.drainage) is brought
  back in over what its trench took out: fill, from that far under the soil's level after down to no lower than the
  trench's bottom, behind its body where no wall stands. A wall's plan is scheme.wall_body's, and its toe at a point of the plan is the toe at
  the nearest point of its line: in the part of the plan nearest each straight piece of the line (from the lower
  envelope of the distances to the pieces, as for a batter) it runs along the piece and square across it.

How the solids are put together. Manifold works out a boolean exactly however two solids meet, but where they share a
surface it takes many times as long, so no solid is cut by another that shares a face with it: each part of the plan
(the walls, the steps, each terrace, the rest of the regrade area) is worked out with solids that reach EDGE_M past it,
whose bottoms are a metre apart, and the cut and fill are lists of pieces that touch but don't overlap, whose volumes
add up.

Where one point is raised by a batter and cut by another's, Domus takes the larger change. The raise less the cut is
continuous, so where batters disagree it's read on a fine grid (CONFLICT_STEP_M) and the line where it changes sign is
drawn straight across each half of a cell: the line is that close to Domus's, and the ground either side of it is the
batters' own. A cone is a polygon of SEGMENTS sides, so a batter's reach round a corner, and the line between two
pieces' parts of the plan, are a whisker short of a circle's.

Only what an option can change need be worked out: within a box (option_window gives the one round an option's
terraces, walls and steps, as far as a batter reaches), else the whole regrade area. A batter's free edge is
Design.free's, the outline less shapely's buffers round the walls and steps, unless the design gives its own as
straight pieces (`free_pieces`): free_pieces() works the edge out exactly, against true circles, so the viewer's sketch
preview (app/viewer/sketch-core.js, a JavaScript copy of the part of this a sketch uses, step for step) can work out
the same edge. domus.options.preview holds the two to each other.

A pure part of the model: it reads no site, and takes the option worked out (a scheme.Design), its walls levelled and
the site's existing walls from its caller.
"""

import itertools
import math

import manifold3d as mf
import numpy as np
import shapely
from shapely.geometry import Polygon, box

from ..geo.mesh import offset_behind
from ..model import walls as walls_mod
from .scheme import BATTER_REACH_M, HELD_M, body_plan, drainage, wall_body

SEGMENTS = 128          # the sides of a batter's cone
EDGE_M = 1e-6           # how far a solid reaches past the part of the plan it's worked out in, so no two share a face
MARGIN_M = 1.0          # how far past the regrade area the ground is taken
CONFLICT_STEP_M = 0.02  # where batters disagree, how finely the larger change is found (scheme takes it point by point)
TIE_LIFT_M = 1e-8       # each piece's distance cone is this much higher than the one before it (_nearest_cells)


# --- solids from shapes ----------------------------------------------------------------------

def solid(vertices, triangles):
    """A closed mesh as a Manifold: an error if it isn't one."""
    m = mf.Manifold(mf.Mesh64(vert_properties=np.ascontiguousarray(vertices, dtype=np.float64),
                              tri_verts=np.ascontiguousarray(triangles, dtype=np.uint64)))
    if m.status() != mf.Error.NoError:
        raise ValueError(f"not a closed solid: {m.status()}")
    return m


def section(geom, origin):
    """A shapely plan in site coordinates as a Manifold CrossSection about `origin` (E, N)."""
    rings = []
    for part in shapely.get_parts(geom):
        if part.geom_type != "Polygon" or part.is_empty:
            continue
        part = shapely.geometry.polygon.orient(part, 1.0)     # outer rings counter-clockwise, holes clockwise
        rings.append(np.asarray(part.exterior.coords)[:-1, :2] - origin)
        rings += [np.asarray(r.coords)[:-1, :2] - origin for r in part.interiors]
    return mf.CrossSection([np.ascontiguousarray(r, dtype=np.float64) for r in rings], mf.FillRule.Positive)


def outline(m):
    """The plan of a solid: its projection (Manifold.project), its overlaps filled."""
    return mf.CrossSection(m.project().to_polygons(), mf.FillRule.Positive)


def prism(cs, low, high):
    """A plan (CrossSection) extruded from `low` to `high`."""
    return mf.Manifold.extrude(cs, high - low).translate((0.0, 0.0, low))


def split_by_plane(m, point, gradient):
    """`m` cut by the plane through `point` (x, y, z) that rises `gradient` (dz/dx, dz/dy): (below it, above it)."""
    n = np.array([gradient[0], gradient[1], -1.0])
    length = float(np.linalg.norm(n))
    c = point[2] - gradient[0] * point[0] - gradient[1] * point[1]    # on the plane, gx x + gy y - z = -c
    n = n / length
    return m.trim_by_plane(tuple(n), -c / length), m.trim_by_plane(tuple(-n), c / length)


def union(parts):
    """The parts as one solid."""
    parts = [p for p in parts if not p.is_empty()]
    return mf.Manifold.batch_boolean(parts, mf.OpType.Add) if parts else mf.Manifold()


def close_surface(vertices, faces, bottom):
    """A height field of triangles (each facing up, none over another) closed into a solid: its triangles on top, its
    edge carried straight down as sides, and a flat bottom at `bottom`. Where two parts of the surface touch only at
    a corner, each part takes its own copy of the corner, so every edge is a simple loop."""
    v = np.asarray(vertices, float)
    f = np.asarray(faces, np.int64).reshape(-1, 3)
    a, b, c = (v[f[:, k], :2] for k in range(3))
    f = f[(b[:, 0] - a[:, 0]) * (c[:, 1] - a[:, 1]) - (b[:, 1] - a[:, 1]) * (c[:, 0] - a[:, 0]) > 0]   # with an area
    f, source = _unpinch(f, len(v))
    used = np.unique(f)
    index = np.full(len(source), -1)
    index[used] = np.arange(len(used))
    f, v = index[f], v[source[used]]
    n = len(v)
    loops = _edge_loops(f)
    tris = [f]
    for loop in loops:
        p = np.asarray(loop)
        q = np.roll(p, -1)
        tris += [np.column_stack([p, p + n, q + n]), np.column_stack([p, q + n, q])]
    rings = [np.ascontiguousarray(v[np.asarray(loop), :2], dtype=np.float64) for loop in loops]
    order = np.concatenate([np.asarray(loop) for loop in loops])
    tris.append(order[np.asarray(mf.triangulate(rings), np.int64)][:, ::-1] + n)
    return solid(np.vstack([v, np.column_stack([v[:, :2], np.full(n, bottom)])]), np.vstack(tris))


def close_between(vertices, faces, low):
    """A height field of triangles (each facing up, none over another) closed into a solid down to the heights `low`
    under its corners: its triangles on top, the same triangles at those heights under them, and its edge carried
    straight down between the two. Each corner's `low` is under it."""
    v = np.asarray(vertices, float)
    f = np.asarray(faces, np.int64).reshape(-1, 3)
    low = np.asarray(low, float)
    a, b, c = (v[f[:, k], :2] for k in range(3))
    f = f[(b[:, 0] - a[:, 0]) * (c[:, 1] - a[:, 1]) - (b[:, 1] - a[:, 1]) * (c[:, 0] - a[:, 0]) > 0]   # with an area
    f, source = _unpinch(f, len(v))
    used = np.unique(f)
    index = np.full(len(source), -1)
    index[used] = np.arange(len(used))
    f, v, low = index[f], v[source[used]], low[source[used]]
    n = len(v)
    tris = [f, f[:, ::-1] + n]
    for loop in _edge_loops(f):
        p = np.asarray(loop)
        q = np.roll(p, -1)
        tris += [np.column_stack([p, p + n, q + n]), np.column_stack([p, q + n, q])]
    return solid(np.vstack([v, np.column_stack([v[:, :2], low])]), np.vstack(tris))


def _border(f):
    """Each edge only one triangle has, in the direction it runs it: (from, to)."""
    edges = np.concatenate([f[:, [0, 1]], f[:, [1, 2]], f[:, [2, 0]]])
    big = int(f.max()) + 1
    have = edges[:, 0] * big + edges[:, 1]
    return edges[~np.isin(edges[:, 1] * big + edges[:, 0], have)]


def _unpinch(f, count):
    """The faces with a copy of each corner where the surface only touches itself (a corner with two edges out along
    the border): one for each fan of triangles round it that shares no edge with the others. Gives the faces and the
    vertex each index stands for."""
    f = f.copy()
    source = list(range(count))
    border = _border(f)
    starts, many = np.unique(border[:, 0], return_counts=True)
    for vtx in starts[many > 1]:
        around = np.flatnonzero((f == vtx).any(axis=1))
        group = {int(t): int(t) for t in around}
        by_neighbour = {}
        for t in around:
            for w in f[t]:
                if w != vtx:
                    by_neighbour.setdefault(int(w), []).append(int(t))
        for ts in by_neighbour.values():           # triangles sharing an edge through the corner are one fan
            for t in ts[1:]:
                group[_root(group, t)] = _root(group, ts[0])
        fans = {}
        for t in around:
            fans.setdefault(_root(group, int(t)), []).append(int(t))
        for fan in list(fans.values())[1:]:
            source.append(int(vtx))
            for t in fan:
                f[t][f[t] == vtx] = len(source) - 1
    return f, np.asarray(source)


def _root(group, t):
    """The fan a triangle is in (a union-find's root, the path halved on the way)."""
    while group[t] != t:
        group[t] = group[group[t]]
        t = group[t]
    return t


def _edge_loops(f):
    """The loops of the border, each in the direction its triangles run it."""
    nxt = {int(a): int(b) for a, b in _border(f)}
    loops, seen = [], set()
    for start in nxt:
        if start in seen:
            continue
        loop, p = [], start
        while p not in seen:
            seen.add(p)
            loop.append(p)
            p = nxt[p]
        loops.append(loop)
    return loops


def strip(face, back, top, bottom):
    """A closed solid over the strip between two lines with a point of one for each point of the other (a wall from
    its face to its back), from `bottom` to `top` at each pair (scalars, or one for each pair)."""
    face, back = np.asarray(face, float)[:, :2], np.asarray(back, float)[:, :2]
    k = len(face)
    top = np.broadcast_to(np.asarray(top, float), (k,))
    bottom = np.broadcast_to(np.asarray(bottom, float), (k,))
    ring = np.vstack([face, back[::-1]])
    if np.dot(ring[:, 0], np.roll(ring[:, 1], -1)) - np.dot(ring[:, 1], np.roll(ring[:, 0], -1)) < 0:
        face, back, top, bottom = face[::-1], back[::-1], top[::-1], bottom[::-1]     # counter-clockwise from above
    verts = np.vstack([np.column_stack([face, top]), np.column_stack([back, top]),
                       np.column_stack([face, bottom]), np.column_stack([back, bottom])])
    fi, bi, lo = np.arange(k), np.arange(k) + k, 2 * k
    a, b, c, d = fi[:-1], fi[1:], bi[1:], bi[:-1]
    tris = [np.column_stack([a, b, c]), np.column_stack([a, c, d]),                     # top
            np.column_stack([c, b, a]) + lo, np.column_stack([d, c, a]) + lo]            # bottom
    p = np.r_[fi, bi[::-1]]
    q = np.roll(p, -1)
    tris += [np.column_stack([p, p + lo, q + lo]), np.column_stack([p, q + lo, q])]     # sides
    return solid(verts, np.vstack(tris))


def cone_hull(a, za, b, zb, batter, far, segments, up):
    """The convex hull of two cones, their apexes at (a, za) and (b, zb), each widening 1 in `batter` out to
    BATTER_REACH_M and running on to the level `far` as a cylinder: downwards for a batter's fill, up for its cut."""
    s = 1.0 if up else -1.0
    ang = 2 * np.pi * np.arange(segments) / segments
    ring = np.column_stack([np.cos(ang), np.sin(ang)]) * BATTER_REACH_M
    pts = []
    for p, z in ((a, za), (b, zb)):
        pts.append([[p[0], p[1], z]])
        for level in (z + s * BATTER_REACH_M / batter, far):
            pts.append(np.column_stack([p[0] + ring[:, 0], p[1] + ring[:, 1], np.full(segments, level)]))
    return mf.Manifold.hull_points(np.ascontiguousarray(np.vstack(pts), dtype=np.float64))


def _pieces(geom):
    """The straight pieces of a line or lines: (from, to)."""
    for part in shapely.get_parts(geom):
        if part.geom_type in ("LineString", "LinearRing") and not part.is_empty:
            c = np.asarray(part.coords)[:, :2]
            for p, q in itertools.pairwise(c):
                if np.hypot(*(q - p)) > 1e-9:
                    yield p, q
        elif part.geom_type in ("MultiLineString", "GeometryCollection"):
            yield from _pieces(part)


def _overlay(shapes):
    """The parts of the plan the shapes (CrossSections) divide it into, each with the shapes it's in."""
    cells = []
    for i, cs in enumerate(shapes):
        if cs.is_empty():
            continue
        out, rest = [], cs
        for cell, members in cells:
            both, left = cell ^ cs, cell - cs
            if not both.is_empty():
                out.append((both, [*members, i]))
            if not left.is_empty():
                out.append((left, members))
            rest = rest - cell
        if not rest.is_empty():
            out.append((rest, [i]))
        cells = out
    return cells


# --- free edges, worked out exactly ------------------------------------------------------------------------------

def _between(v0, v1, lo, hi):
    """Where v0 + t v1 lies between `lo` and `hi`: (t0, t1), unbounded where it never leaves, or None."""
    if v1 == 0.0:
        return (-math.inf, math.inf) if lo < v0 < hi else None
    t0, t1 = (lo - v0) / v1, (hi - v0) / v1
    return (t0, t1) if t0 < t1 else (t1, t0)


def _capsule_span(a, d, p, q, r):
    """Where the line a + t d comes within `r` of the segment p-q: (t0, t1), or None. Within r of a segment is a
    convex shape (a strip square across it, and a disc round each end), so that's one stretch of the line."""
    spans = []
    A = d[0] * d[0] + d[1] * d[1]
    for c in (p, q):
        fx, fy = a[0] - c[0], a[1] - c[1]
        B, C = 2.0 * (d[0] * fx + d[1] * fy), fx * fx + fy * fy - r * r
        disc = B * B - 4.0 * A * C
        if A > 0.0 and disc > 0.0:
            root = math.sqrt(disc)
            spans.append(((-B - root) / (2.0 * A), (-B + root) / (2.0 * A)))
    length = math.hypot(q[0] - p[0], q[1] - p[1])
    if length > 0.0:
        ux, uy = (q[0] - p[0]) / length, (q[1] - p[1]) / length
        ox, oy = a[0] - p[0], a[1] - p[1]
        across = _between(ux * oy - uy * ox, ux * d[1] - uy * d[0], -r, r)
        along = _between(ux * ox + uy * oy, ux * d[0] + uy * d[1], 0.0, length)
        if across and along and max(across[0], along[0]) < min(across[1], along[1]):
            spans.append((max(across[0], along[0]), min(across[1], along[1])))
    if not spans:
        return None
    return min(t0 for t0, _ in spans), max(t1 for _, t1 in spans)


def _convex_span(a, d, ring, r):
    """Where the line a + t d comes within `r` of a convex polygon (`ring`, its corners) or inside it: (t0, t1), or
    None. That's convex too, so it's from the first of the line's stretches near a side, or inside, to the last."""
    spans = [s for s in (_capsule_span(a, d, ring[k], ring[(k + 1) % len(ring)], r) for k in range(len(ring))) if s]
    area = sum(ring[k - 1][0] * ring[k][1] - ring[k][0] * ring[k - 1][1] for k in range(len(ring)))
    sign = 1.0 if area > 0 else -1.0
    inside = (-math.inf, math.inf)
    for k in range(len(ring)):
        p, q = ring[k], ring[(k + 1) % len(ring)]
        ex, ey = q[0] - p[0], q[1] - p[1]
        half = _between(sign * (ex * (a[1] - p[1]) - ey * (a[0] - p[0])), sign * (ex * d[1] - ey * d[0]), 0.0, math.inf)
        if half is None:
            inside = None
            break
        inside = (max(inside[0], half[0]), min(inside[1], half[1]))
    if inside and inside[0] < inside[1]:
        spans.append(inside)
    if not spans:
        return None
    return min(t0 for t0, _ in spans), max(t1 for _, t1 in spans)


def free_pieces(design):
    """Each terrace's free edge as straight pieces (from, to), as Design.free has it but worked out exactly: its outline
    less the parts within a wall's thickness and HELD_M of the wall's line, or within HELD_M of a flight of steps (or
    in it). Design takes shapely's buffers there, polygons a whisker inside the circles round a wall's ends."""
    holds = []                     # (corners, how near holds it, a flight's outline)
    for w in design.walls:
        line = [(float(x), float(y)) for x, y in np.asarray(w["line"], float)[:, :2]]
        holds.append((line, float(w["thickness"]) + HELD_M, False))
    for st in design.stairs:
        holds.append(([(float(x), float(y)) for x, y in np.asarray(st.poly.exterior.coords)[:-1, :2]], HELD_M, True))
    out = []
    for t in design.terraces:
        ring = [(float(x), float(y)) for x, y in np.asarray(t.poly.exterior.coords)[:, :2]]
        pieces = []
        for a, b in itertools.pairwise(ring):
            d = (b[0] - a[0], b[1] - a[1])
            length = math.hypot(*d)
            if length <= 1e-12:
                continue
            held = []
            for corners, r, flight in holds:
                if flight:
                    spans = [_convex_span(a, d, corners, r)]
                else:
                    spans = [_capsule_span(a, d, p, q, r) for p, q in itertools.pairwise(corners)]
                held += [(max(t0, 0.0), min(t1, 1.0)) for t0, t1 in (s for s in spans if s) if t0 < 1.0 and t1 > 0.0]
            held.sort()
            u = 0.0
            for h0, h1 in held:
                if (h0 - u) * length > 1e-6:
                    pieces.append(((a[0] + u * d[0], a[1] + u * d[1]), (a[0] + h0 * d[0], a[1] + h0 * d[1])))
                u = max(u, h1)
            if (1.0 - u) * length > 1e-6:
                pieces.append(((a[0] + u * d[0], a[1] + u * d[1]), b))
        out.append([(np.asarray(p0, float), np.asarray(p1, float)) for p0, p1 in pieces])
    return out


def batter_levels(design, pieces, e, n):
    """What the batters do at points (e, n), as Design.batters does from the free edges' straight pieces (`pieces`, one
    list for each terrace): the level they raise the ground to and the level they cut it to, -inf and inf where none
    reaches. The first piece that's nearest gives the edge's level."""
    e, n = np.ravel(np.asarray(e, float)), np.ravel(np.asarray(n, float))
    raise_to, lower_to = np.full(len(e), -np.inf), np.full(len(e), np.inf)
    for t, own in zip(design.terraces, pieces, strict=True):
        if not own:
            continue
        best, fx, fy = np.full(len(e), np.inf), np.zeros(len(e)), np.zeros(len(e))
        for a, b in own:
            ab = b - a
            u = np.clip(((e - a[0]) * ab[0] + (n - a[1]) * ab[1]) / float(ab @ ab), 0.0, 1.0)
            x, y = a[0] + u * ab[0], a[1] + u * ab[1]
            dist = np.hypot(e - x, n - y)
            nearer = dist < best
            best[nearer], fx[nearer], fy[nearer] = dist[nearer], x[nearer], y[nearer]
        near = best < BATTER_REACH_M
        if near.any():
            ze = np.full(int(near.sum()), _level_of(t)) if _is_level(t) else t.z(fx[near], fy[near])
            raise_to[near] = np.maximum(raise_to[near], ze - best[near] / t.batter)
            lower_to[near] = np.minimum(lower_to[near], ze + best[near] / t.batter)
    return raise_to, lower_to


def option_window(design, walls):
    """The box (E0, N0, E1, N1) round all an option can change: its terraces, its steps and its walls from face to
    back, as far as a batter reaches and a metre more. None for an option with none of them."""
    shapes = [t.poly for t in design.terraces] + [st.poly for st in design.stairs] + [wall_body(w)[0] for w in walls]
    if not shapes:
        return None
    b = np.array([g.bounds for g in shapes])
    reach = BATTER_REACH_M + 1.0
    return (float(b[:, 0].min()) - reach, float(b[:, 1].min()) - reach, float(b[:, 2].max()) + reach, float(b[:, 3].max()) + reach)


def _is_level(t):
    """Whether terrace `t` is at one level all over: a plane that doesn't fall, or the same level at every corner."""
    if t.plane is not None:
        return not np.any(t.plane[2])
    values = t.levels
    return bool(np.all(values == values[0]))


def _level_of(t):
    """A level terrace's level (_is_level)."""
    return float(t.plane[0]) if t.plane is not None else float(t.levels[0])


# --- an option as solids ---------------------------------------------------------------------

class Solids:
    """An option as solids (option_solids()), in metres about `origin` (E, N), heights as they are.

    `existing` is the ground as it is over the regrade area, and `designed` the pieces of the designed ground there,
    side by side; `walls` the new walls, each from its face to its back and its foot to its top, and `steps` the treads
    of every flight; `cut` and `fill` the pieces of soil the option takes away and brings in."""

    def __init__(self, origin, existing, designed, walls, steps, cut, fill, named=()):
        self.origin = origin
        self.existing, self.designed, self.walls, self.steps = existing, designed, walls, steps
        self.cut, self.fill = cut, fill
        self.named = list(named)            # (what, which part of the plan, piece): every cut, fill and designed piece

    def volumes(self):
        """Cut and fill in m3: the pieces' volumes added up."""
        return {"cut_m3": float(sum(p.volume() for p in self.cut)), "fill_m3": float(sum(p.volume() for p in self.fill))}

    def height(self, e, n):
        """The designed ground's height at points (e, n) in site coordinates: the top of the piece there (the highest,
        on a seam between two), NaN off the regrade area."""
        x = np.ravel(np.asarray(e, float)) - self.origin[0]
        y = np.ravel(np.asarray(n, float)) - self.origin[1]
        out = np.full(len(x), np.nan)
        for piece in self.designed:
            lo = piece.bounding_box()
            for i in np.flatnonzero((x >= lo[0]) & (x <= lo[3]) & (y >= lo[1]) & (y <= lo[4])):
                hits = piece.ray_cast((x[i], y[i], lo[5] + 1.0), (x[i], y[i], lo[2] - 1.0))
                if hits:
                    out[i] = np.fmax(out[i], hits[0].position[2])
        return out.reshape(np.shape(e))


def option_ground(s, vertices, faces, patch, garden, stairs=()):
    """The ground with an option, as one solid over `patch` (E0, N0, E1, N1) about the solids' origin: its designed
    pieces (Solids `s`) over the regrade area (`garden`), and outside it the ground as it is, given as the triangles
    of the ground the build draws (`vertices` on the site's grid, `faces`), which reach past the patch. Under its
    flights of steps (`stairs`, the design's) it's the ramp just under the treads, as the rule draws it
    (Stair.under), where the soil under them is the ground the flight stands on (Stair.z): on a foot that slopes
    across the flight, that's carried in level, and it would come through the lowest treads."""
    o = s.origin
    v = np.column_stack([np.asarray(vertices, float)[:, :2] - o, np.asarray(vertices, float)[:, 2]])
    zb = float(np.floor(np.nanmin(v[:, 2]))) - 3.0
    outside = section(box(*patch).difference(garden), o)
    ground = close_surface(v, faces, zb) ^ prism(outside, zb - 1.0, float(np.nanmax(v[:, 2])) + 10.0)
    flights = list(reversed(list(stairs)))          # as option_solids numbers them: the last flight a point is under
    pieces = []
    for what, part, piece in s.named:
        if what != "designed":
            continue
        if part.startswith("steps ") and flights:
            st = flights[int(part.split()[1])]
            lo = piece.bounding_box()
            a = st.a - o
            piece = split_by_plane(prism(piece.project(), lo[2], lo[5] + 1.0), (a[0], a[1], st.foot - 0.03),
                                   st.along * st.rise / st.length)[0]
        pieces.append(piece)
    return union([*pieces, ground])


def _faces(m, origin, keep):
    """A solid's triangles that `keep` (given each one's corners and its normal's height) chooses, as (vertices on the
    site's grid, faces), the vertices only those they use."""
    mesh = m.to_mesh64()
    v = np.asarray(mesh.vert_properties, float)[:, :3]
    f = np.asarray(mesh.tri_verts, np.int64).reshape(-1, 3)
    corners = v[f]
    n = np.cross(corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0])
    nz = n[:, 2] / np.maximum(np.linalg.norm(n, axis=1), 1e-300)
    f = f[keep(corners, nz)]
    used, f = np.unique(f, return_inverse=True)
    return np.column_stack([v[used, :2] + origin, v[used, 2]]), f.reshape(-1, 3)


UPRIGHT = 1e-6      # a face whose normal rises less than this is upright: the ground's own slopes are far from it


def surface(ground, origin, patch, cut=None, walls=()):
    """The ground with an option (option_ground) as the viewer draws it: its top, and the upright faces where two of its
    parts meet at different heights, as at the regrade area's edge where a batter runs into it, but not its bottom or
    the patch's sides. (vertices on the site's grid, faces). Less `cut` (a solid about the origin: what stands on the
    ground and covers it, as the walls and the flights do, walls_cut), with no upright face on a wall's drawn outline
    under its head (`walls`, as walls_cut takes them): that lies in the wall's faces and would show through them. Over
    its head it's the ground's own, where that stands over the wall beside it."""
    x0, y0, x1, y1 = (patch[0] - origin[0], patch[1] - origin[1], patch[2] - origin[0], patch[3] - origin[1])
    if cut is not None and not cut.is_empty():
        ground = ground - cut

    def keep(c, nz):
        on_side = np.zeros(len(c), bool)
        for axis, at in ((0, x0), (0, x1), (1, y0), (1, y1)):
            on_side |= np.all(np.abs(c[:, :, axis] - at) < 1e-6, axis=1)
        upright = np.abs(nz) <= UPRIGHT                 # as near upright as the arithmetic leaves a face cut upright
        k = np.flatnonzero(upright & ~on_side)
        mid = c[k].mean(axis=1)
        e, n = mid[:, 0] + origin[0], mid[:, 1] + origin[1]
        for plan, head, _ in walls:
            near = shapely.distance(plan.boundary, shapely.points(e, n)) < 1e-5
            if near.any():
                on_side[k[near][mid[near, 2] < head(e[near], n[near])]] = True
        return (nz > UPRIGHT) | (upright & ~on_side)
    return _faces(ground, origin, keep)


def _not_upright(vertices, faces):
    """A mesh's triangles that aren't upright, as their corners (on the site's grid), and their normals."""
    v = np.asarray(vertices, float)
    t = v[np.asarray(faces, np.int64).reshape(-1, 3)]
    n = np.cross(t[:, 1] - t[:, 0], t[:, 2] - t[:, 0])
    flat = np.abs(n[:, 2]) > 1e-6 * np.maximum(np.linalg.norm(n, axis=1), 1e-300)
    return t[flat], n[flat]


def footprint(vertices, faces):
    """The plan a mesh covers from above (shapely, on the site's grid): its triangles that aren't upright, as one."""
    t, _ = _not_upright(vertices, faces)
    plan = shapely.unary_union(shapely.polygons(t[:, :, :2]))
    return shapely.make_valid(plan).buffer(1e-6) if not plan.is_empty else plan


def over(plan, origin, levels):
    """Solids over `plan` (shapely, on the site's grid), about `origin`: one between each two of `levels` in turn, the
    lowest first, each a height at points (e, n) or one height. They're worked out on the same triangles, constrained
    to the plan's outline with a corner every 5 cm along it, where the heights are read, so each meets the next."""
    pieces = [[] for _ in levels[1:]]
    for part in shapely.get_parts(plan):
        if part.geom_type != "Polygon" or part.area < 1e-9:
            continue
        dense = shapely.segmentize(part, 0.05)
        tris = np.array([np.asarray(t.exterior.coords)[:3, :2] for t in shapely.get_parts(shapely.constrained_delaunay_triangles(dense))])
        if not len(tris):
            continue
        a, b, c = tris[:, 0], tris[:, 1], tris[:, 2]
        cw = (b[:, 0] - a[:, 0]) * (c[:, 1] - a[:, 1]) - (b[:, 1] - a[:, 1]) * (c[:, 0] - a[:, 0]) < 0
        tris[cw] = tris[cw][:, [0, 2, 1]]
        xy, faces = np.unique(tris.reshape(-1, 2), axis=0, return_inverse=True)
        z = [np.broadcast_to(np.asarray(h(xy[:, 0], xy[:, 1]) if callable(h) else h, float), len(xy)) for h in levels]
        for k, (lo, hi) in enumerate(itertools.pairwise(z)):
            pieces[k].append(close_between(np.column_stack([xy - origin, hi]), faces.reshape(-1, 3), lo))
    return [union(p) for p in pieces]


CUT_SLAB_M = 0.001      # the least a wall's cut takes of the ground under its top (walls_cut)


def _highest(t, n):
    """The highest of triangles (corners on the site's grid, none upright, and their normals) within a hair of points
    (e, n), on their planes: a height at each point, NaN where none is."""
    tree = shapely.STRtree(shapely.polygons(t[:, :, :2]))

    def at(e, n_):
        e, n_ = np.asarray(e, float), np.asarray(n_, float)
        i, k = tree.query(shapely.points(e, n_), predicate="dwithin", distance=1e-5)
        z = t[k, 0, 2] - (n[k, 0] * (e[i] - t[k, 0, 0]) + n[k, 1] * (n_[i] - t[k, 0, 1])) / n[k, 2]
        out = np.full(len(e), np.nan)
        np.fmax.at(out, i, z)
        return out
    return at


def standing(vertices, faces, line, chain, toe, top, soil_over=(), soil_over_m=0.0):
    """A wall as the ground with an option meets it, from its drawn mesh and its levels as walls.wall_levels gives
    them: (its drawn plan, as footprint gives it; its head, the top of its mesh as it's drawn, its capping course and
    posts included; the parts of its plan, each with its floor), the head and each floor a height at points (e, n) on
    the site's grid. surface takes the plan and the head, walls_cut the head and the parts. A part's floor is the
    lower of the wall's toe and its top at the nearest point of its line, but where the soil covers the wall as it's
    built
    (`soil_over`, the faces it covers, and `soil_over_m`, how far over them it comes: the body's top behind a
    shallower capping course, walls.unit_mesh), it's that far over them: the ground stays there."""
    line = shapely.LineString(np.asarray(line, float)[:, :2])
    chain, top = np.asarray(chain, float), np.asarray(top, float)
    low = np.minimum(np.asarray(toe, float), top)
    f = np.asarray(faces, np.int64).reshape(-1, 3)
    covered = np.zeros(len(f), bool)
    covered[np.asarray(list(soil_over), np.int64)] = True
    t, n = _not_upright(vertices, f)
    keep = np.abs(n[:, 2]) > 1e-12
    highest = _highest(t[keep], n[keep])

    def along(levels, e, n_):
        return np.interp(shapely.line_locate_point(line, shapely.points(e, n_)), chain, levels)

    def head(e, n_):
        z = highest(e, n_)
        return np.where(np.isfinite(z), z, along(top, e, n_))

    def floor(e, n_):
        return along(low, e, n_)
    plan = footprint(vertices, f)
    if not covered.any():
        return plan, head, [(plan, floor)]
    tc, nc = _not_upright(vertices, f[covered])
    soil = _highest(tc, nc)

    def under_soil(e, n_):
        z = soil(e, n_)
        return np.where(np.isfinite(z), z, along(top, e, n_) - soil_over_m) + soil_over_m
    buried = footprint(vertices, f[covered])
    return plan, head, [(plan.difference(buried), floor), (buried, under_soil)]


def walls_cut(walls, stairs, origin, ceiling):
    """What the walls and flights that stand on an option's ground take of it, as one solid about `origin`. Over each
    part of each wall's drawn plan (`walls`, as standing gives them), everything above the part's floor, so nothing of
    the ground stands over a wall, its capping course and its posts included, while under a capping course standing
    out from its face the ground stays at the toe, and where the soil covers the wall as it's built it stays. It's
    taken in two parts, up to the wall's head, the top of it as it's drawn, and above it, so the faces it cuts in the
    ground stop there: under it they lie in the wall's faces (surface leaves them out), and over it they're the
    ground's own, where that stands over the wall beside it. Over each flight (`stairs`, the design's), everything
    above the ramp just under its treads, inside the regrade area or out of it."""
    parts = []
    for _, head, pieces in walls:
        for plan, floor in pieces:
            def split(e, n, head=head, floor=floor):
                return np.maximum(head(e, n), floor(e, n) + CUT_SLAB_M)
            parts += over(plan, origin, [floor, split, ceiling])
    for st in stairs:
        a = st.a - origin
        column = prism(section(st.poly.buffer(EDGE_M, join_style="mitre"), origin), st.foot - 5.0, ceiling)
        parts.append(split_by_plane(column, (a[0], a[1], st.foot - 0.03), st.along * st.rise / st.length)[1])
    return union(parts) if parts else mf.Manifold()


def top(solid, origin, plan):
    """The top of a solid within `plan` (a shapely plan on the site's grid), as (vertices on the site's grid, faces):
    a lawn or a bed laid on the ground with an option (option_ground), just where its outline says."""
    lo = solid.bounding_box()
    piece = solid ^ prism(section(plan, origin), lo[2] - 1.0, lo[5] + 1.0)
    return _faces(piece, origin, lambda c, nz: nz > 1e-9)


def ground_triangles(existing, window, step=0.25):
    """The ground as it is (`existing`, anything with sample(e, n)) as triangles over `window` (E0, N0, E1, N1). A
    site's drawn ground (tin.DrawnGround) gives its own triangles, which are that ground exactly; any other ground is
    read on a `step` grid."""
    surface = getattr(existing, "surface", None)
    if surface is not None:
        v, f = surface.vertices, np.asarray(surface.faces, np.int64)
        lo, hi = v[:, :2].min(axis=0), v[:, :2].max(axis=0)
        if lo[0] > window[0] or lo[1] > window[1] or hi[0] < window[2] or hi[1] < window[3]:
            raise ValueError("the ground drawn doesn't reach past the regrade area: widen the model's extent")
        mid = v[f, :2].mean(axis=1)
        near = (mid[:, 0] > window[0] - 2) & (mid[:, 0] < window[2] + 2) & (mid[:, 1] > window[1] - 2) & (mid[:, 1] < window[3] + 2)
        return v, f[near]
    e = np.arange(window[0], window[2] + step / 2, step)
    n = np.arange(window[1], window[3] + step / 2, step)
    E, N = np.meshgrid(e, n)
    idx = np.arange(E.size).reshape(E.shape)
    a, b, c, d = idx[:-1, :-1].ravel(), idx[:-1, 1:].ravel(), idx[1:, 1:].ravel(), idx[1:, :-1].ravel()
    faces = np.concatenate([np.column_stack([a, b, c]), np.column_stack([a, c, d])])
    return np.column_stack([E.ravel(), N.ravel(), existing.sample(E, N).ravel()]), faces


def _toe_columns(body, origin, low, high, segments=SEGMENTS):
    """Everything over a wall's plan below its toe, from `low`, and everything above it, to `high`: (below, above),
    reaching 2 EDGE_M past the plan. `body` is the wall as scheme.wall_body gives it, (plan, line, chain, toe), and its
    toe at a point of the plan is the toe at the nearest point of the line, as scheme reads it: in the part of the plan
    nearest each straight piece of the line, the toe along the piece carried square across it, and level past its
    ends. So a plan that folds where a leaning wall's foot turns a tight corner (which wall_body's buffer puts right)
    takes its toe from the line, never from the fold."""
    plan, line, chain, toe = body
    pts = np.asarray(line.coords, float)[:, :2] - origin
    at = np.concatenate([[0.0], np.cumsum(np.hypot(*np.diff(pts, axis=0).T))])       # each corner's distance along
    keep = [k for k in range(len(pts) - 1) if at[k + 1] - at[k] > 1e-9]
    x0, y0, x1, y1 = shapely.union(plan, line).bounds
    far = float(np.hypot(x1 - x0, y1 - y0)) + 1.0          # from anywhere on the line to past anywhere in the plan
    cs = section(plan, origin).offset(2 * EDGE_M, mf.JoinType.Miter)
    ends = np.array([[pts[k], pts[k + 1]] for k in keep])
    cells = _nearest_cells(ends, segments, far) if len(keep) > 1 else [None] * len(keep)
    chain, toe = np.asarray(chain, float), np.asarray(toe, float)
    below, above = [], []
    for k, cell in zip(keep, cells, strict=True):
        region = cs if cell is None else cs ^ cell.offset(EDGE_M, mf.JoinType.Miter)
        if region.is_empty():
            continue
        length = at[k + 1] - at[k]
        along = (pts[k + 1] - pts[k]) / length
        inner = chain[(chain > at[k] + 1e-9) & (chain < at[k + 1] - 1e-9)] - at[k]
        t = np.unique(np.r_[-far, 0.0, inner, length, length + far])      # where the toe along the piece bends
        z = np.interp(at[k] + np.clip(t, 0.0, length), chain, toe)
        col = prism(region, low - 1.0, high + 1.0)
        below.append(_square_column(pts[k], along, t, z, low, far, False) ^ col)
        above.append(_square_column(pts[k], along, t, z, high, far, True) ^ col)
    return union(below), union(above)


def _square_column(start, along, t, z, level, far, up):
    """The solid between heights `z` along a straight line (at distances `t` from `start` along the unit vector
    `along`, rising) and the flat `level` below them (or above them, `up`), carried square across the line `far` to
    either side: their profile extruded across the line and set in place, so every corner is in double precision (the
    viewer's copy builds it the same way, where a mesh would round its corners to single precision)."""
    line = [(float(ti), float(zi)) for ti, zi in zip(t, z, strict=True)]
    ring = [*line, (line[-1][0], level), (line[0][0], level)] if up else [(line[0][0], level), (line[-1][0], level), *line[::-1]]
    m = mf.Manifold.extrude(mf.CrossSection([np.asarray(ring, float)], mf.FillRule.Positive), 2.0 * far)
    nx, ny = -float(along[1]), float(along[0])
    return m.transform(np.array([[float(along[0]), 0.0, -nx, float(start[0]) + far * nx],
                                 [float(along[1]), 0.0, -ny, float(start[1]) + far * ny],
                                 [0.0, 1.0, 0.0, 0.0]]))


def option_solids(design, walls, existing_walls=(), segments=SEGMENTS, old=None, within=None, conflict_step=CONFLICT_STEP_M):
    """An option as solids (Solids): `design` worked out (a scheme.Design, on a site's drawn ground for the solids to
    be exact), `walls` its walls levelled (Design.wall_levels) and `existing_walls` the site's (domus.walls dicts).
    `old` gives the existing walls instead, each as (its id, scheme.wall_body's (plan, line, chain, toe)), and
    `within` a box (E0, N0, E1, N1) to work out only what's in it (option_window), and `conflict_step` the grid where
    batters disagree (_cut_wins)."""
    origin = np.floor(np.asarray(design.garden.bounds[:2], float))
    g0, g1, g2, g3 = design.garden.bounds
    if within is not None:
        g0, g1, g2, g3 = max(g0, within[0]), max(g1, within[1]), min(g2, within[2]), min(g3, within[3])
    window = (g0 - MARGIN_M, g1 - MARGIN_M, g2 + MARGIN_M, g3 + MARGIN_M)
    V, F = ground_triangles(design.existing, window)
    V = np.column_stack([np.asarray(V, float)[:, :2] - origin, np.asarray(V, float)[:, 2]])
    # Below and above everything: the ground, the terraces, and the batters' cones, which reach BATTER_REACH_M / batter
    # below and above their terrace's levels.
    pieces = getattr(design, "free_pieces", None)       # each terrace's free edge as straight pieces
    if pieces is None:
        pieces = [list(_pieces(free)) for free in design.free]
    corners = [t.z(*np.asarray(t.poly.exterior.coords)[:, :2].T) for t in design.terraces]
    deep = [BATTER_REACH_M / t.batter if own else 0.0 for t, own in zip(design.terraces, pieces, strict=True)]
    zb = float(np.floor(min([np.nanmin(V[F, 2]), *(np.min(z) - d for z, d in zip(corners, deep, strict=True))]))) - 3.0
    top = float(np.ceil(max([np.nanmax(V[F, 2]), *(np.max(z) + d for z, d in zip(corners, deep, strict=True))]))) + 5.0
    win = section(box(*window), origin)
    ground = close_surface(V, F, zb) ^ prism(win, zb - 1.0, top + 1.0)
    area = section(design.garden, origin)
    if within is not None:
        area = area ^ section(box(*within), origin)

    def tall(cs):
        """A plan, through everything."""
        return prism(cs, zb - 2.0, top + 2.0)

    def wide(geom):
        return section(geom, origin).offset(EDGE_M, mf.JoinType.Miter)

    def on_toes(bodies):
        """The ground, but at its toe inside each wall of `bodies` (scheme.wall_body's), the lowest where two meet."""
        if not bodies:
            return ground
        plans = [section(b[0], origin) for b in bodies]
        under = [_toe_columns(b, origin, zb, top + 1.0, segments)[0] for b in bodies]
        toes = []
        for cell, members in _overlay(plans):
            low = under[members[0]]
            for k in members[1:]:
                low = low ^ under[k]
            toes.append(low ^ tall(cell.offset(EDGE_M, mf.JoinType.Miter)))
        return union([ground - tall(mf.CrossSection.batch_boolean(plans, mf.OpType.Add)), *toes])

    # The soil before: the ground, but at its toe inside an existing wall. Only the walls that come near the regrade
    # area matter.
    near = box(*window)
    if old is None:
        old = [(w["id"], wall_body(w, design.existing)) for w in existing_walls]
    old = [(i, b) for i, b in old if b[0].intersects(near)]
    before = on_toes([b for _, b in old])
    # The ground the option starts from (Design.base): the ground, but at its toe where a wall it takes away stood.
    base = on_toes([b for b in design.gone if b[0].intersects(near)])

    cut, fill, named = [], [], []

    def soil(region, after, above, part):
        """The soil moved in `region` (a plan, the `part` of the plan it is), where `after` holds everything below the
        soil after and `above` everything above it."""
        if region.is_empty():
            return
        here = tall(region)
        fill.append((after ^ here) - before)
        cut.append((before ^ here) ^ above)
        named.extend([("fill", part, fill[-1]), ("cut", part, cut[-1])])

    # The walls, new and kept: the soil is at the wall's toe, the lowest where two meet.
    kept = [b for i, b in old if i in design.keep_existing]
    gone = [section(b[0], origin) for i, b in old if i not in design.keep_existing]
    gone = mf.CrossSection.batch_boolean(gone, mf.OpType.Add) if gone else mf.CrossSection()
    bodies = []                                                  # (plan, below the toe, above it, a new wall's)
    for body, new in [(wall_body(w), True) for w in walls] + [(b, False) for b in kept]:
        plan = section(body[0], origin) ^ area
        if not plan.is_empty():
            bodies.append((plan, *_toe_columns(body, origin, zb + 1.0, top + 1.0, segments), new))
    walled = mf.CrossSection.batch_boolean([b[0] for b in bodies], mf.OpType.Add) if bodies else mf.CrossSection()
    for cell, members in _overlay([b[0] for b in bodies]):
        if not any(bodies[k][3] for k in members):   # only kept walls: the soil stays at their toes, but where one the
            cell = cell ^ gone                       # option takes away was lower there, it comes up to theirs
            if cell.is_empty():
                continue
        after, above = bodies[members[0]][1], bodies[members[0]][2]
        for k in members[1:]:
            after, above = after ^ bodies[k][1], above + bodies[k][2]
        soil(cell, after, above, f"walls {members}")

    # Steps: the last flight a point is under gives its ground.
    stepped = mf.CrossSection()
    ramps = []
    for st in reversed(design.stairs):
        below, over = _stair_columns(st, origin, zb, top)
        foot = (section(st.poly, origin) ^ area) - stepped
        ramps.append((foot, below))
        soil(foot - walled, below, over, f"steps {len(ramps) - 1}")
        stepped = stepped + foot

    # Terraces: the first a point is in gives its level, and the soil is any paving's depth under it.
    taken = mf.CrossSection()
    levels, soils = [], []                                       # (its part of the plan, below its level / its soil's)
    for k, t in enumerate(design.terraces):
        own = ((section(t.poly, origin) ^ area) - taken) - stepped
        taken = taken + (section(t.poly, origin) ^ area)
        level, over = _terrace_columns(t, wide(t.poly), origin, zb, top, 0.0)
        levels.append((own, level))
        if t.paving:
            level, over = _terrace_columns(t, wide(t.poly), origin, zb, top, t.paving)
        soils.append((own, level))
        soil(own - walled, level, over, f"terrace {k}")

    # The rest of the regrade area: where the batters raise the ground the soil is theirs, and where they only cut it,
    # it comes down to their cut.
    rest = (area - taken) - stepped
    # The batters run on below the bottom of `box_all` and above its top, so what's above or below them in it is
    # only what's above the raise's surface or below the cut's.
    fills, cuts = [], []
    for t, own in zip(design.terraces, pieces, strict=True):
        if own:
            raise_t, cut_t = _batter(t, own, origin, zb + 0.25, top + 2.5, segments)
            fills.append(raise_t)
            cuts.append(cut_t)
    designed = []
    soils += [(foot, below) for foot, below in ramps]
    if fills:
        raise_to, cut_to = union(fills), union(cuts)
        box_all = prism(win, zb + 0.5, top + 1.5)
        under_cut = box_all - cut_to
        up = outline(raise_to - base) ^ rest
        lowered = outline(base ^ cut_to) ^ rest
        both = up ^ lowered
        if not both.is_empty():     # raised by one batter and cut by another: the larger change wins, as in Design
            cut_wins = _cut_wins(both, design, origin, conflict_step)
            up = up - cut_wins
        down = lowered - up
        soil(up - walled, raise_to, box_all - raise_to, "batters raise")
        soil(down - walled, under_cut, cut_to, "batters cut")
        designed += [("batters raise", raise_to ^ tall(up)), ("batters cut", under_cut ^ tall(down)),
                     ("ground", base ^ tall((rest - up) - down))]
        soils += [(up, raise_to), (down, under_cut), ((rest - up) - down, before)]
    else:
        designed.append(("ground", base ^ tall(rest)))
        soils.append((rest, before))
    designed += [(f"terrace {k}", level ^ tall(own)) for k, (own, level) in enumerate(levels) if not own.is_empty()]
    designed += [(f"steps {k}", below ^ tall(foot)) for k, (foot, below) in enumerate(ramps) if not foot.is_empty()]
    named += [("designed", part, piece) for part, piece in designed]

    # The soil over a new wall's drainage aggregate: from its `seal` under the soil's level after, down to no lower
    # than its trench's bottom, behind its body where no wall stands. Where two walls' meet, the deeper seal's.
    sealed = sorted(((i, d) for i, w in enumerate(walls) if (d := drainage(w))), key=lambda wd: -wd[1][4])
    if sealed:
        blocked = mf.CrossSection.batch_boolean([section(body_plan(w), origin) for w in walls]
                                                + [b[0] for b in bodies if not b[3]], mf.OpType.Add)
        done = mf.CrossSection()
        for i, (plan, line, chain, level, depth) in sealed:
            region = ((section(plan, origin) ^ area) - blocked) - done
            done = done + region
            if region.is_empty():
                continue
            # above the trench's bottom, at each point the bottom at the nearest point of the wall's line, as
            # scheme.soil_moved reads it
            above = _toe_columns((plan, line, chain, level), origin, zb + 1.0, top + 1.0, segments)[1]
            for own, below in soils:
                if (own ^ region).is_empty():
                    continue
                piece = ((below ^ tall(own ^ region)) ^ above) - below.translate((0.0, 0.0, -depth))
                fill.append(piece)
                named.append(("fill", f"seal {i}", piece))

    return Solids(origin, ground ^ tall(area), [d for _, d in designed if not d.is_empty()],
                  [_wall_solid(w, design.existing, origin) for w in walls],
                  [tread for st in design.stairs for tread in _treads(st, origin)],
                  [c for c in cut if not c.is_empty()], [f for f in fill if not f.is_empty()],
                  [(what, part, piece) for what, part, piece in named if not piece.is_empty()])


def _batter(t, free, origin, low, high, segments):
    """Terrace `t`'s batters from its free edge (`free`, its straight pieces), as two solids: all they raise the ground
    to (on down to `low`) and all they cut away (on up to `high`), out to BATTER_REACH_M.

    Design raises a point to the terrace's level at the nearest point of the free edge, less the distance over the
    batter, and cuts it to that level plus the distance. Where the level is the same all along the edge, the nearest
    point is also the one that gives the most, so the hulls of cones along the edge are the batters exactly. Where it
    isn't, each straight piece of the edge gives the batter only where it's the nearest piece (its part of the plan,
    from the lower envelope of the distances to the pieces), and there it's a wedge along the piece, or a cone round
    whichever end is nearest."""
    pieces = _straight(t, free)
    ends = np.array([[p, q] for p, q in pieces]) - origin
    if _is_level(t):                                            # level: the nearest point gives the most
        z = [(_level_of(t), _level_of(t))] * len(pieces)
        hulls = [(cone_hull(a, za, b, zb, t.batter, low, segments, False), cone_hull(a, za, b, zb, t.batter, high, segments, True))
                 for (a, b), (za, zb) in zip(ends, z, strict=True)]
        return union([h[0] for h in hulls]), union([h[1] for h in hulls])
    z = [tuple(float(v) for v in t.z(np.array([p[0], q[0]]), np.array([p[1], q[1]]))) for p, q in pieces]
    bottom, roof = min(low, high) - 1.0, max(low, high) + 1.0
    raised, cut = [], []
    for (a, b), (za, zb), cell in zip(ends, z, _nearest_cells(ends, segments), strict=True):
        if cell.is_empty():
            continue
        along = (b - a) / np.hypot(*(b - a))
        across = np.array([-along[1], along[0]]) * BATTER_REACH_M
        far = along * 2 * BATTER_REACH_M
        slab = _region([a - across, b - across, b + across, a + across])
        beyond = ((a, za, _region([a - across, a + across, a + across - far, a - across - far])),
                  (b, zb, _region([b - across, b - across + far, b + across + far, b + across])))
        for up, out, end in ((False, raised, low), (True, cut, high)):
            drop = (1.0 if up else -1.0) * BATTER_REACH_M / t.batter
            wedge = mf.Manifold.hull_points(np.ascontiguousarray(
                [[*p, zp + k * drop] if k < 2 else [*p, end]
                 for q, zp in ((a, za), (b, zb)) for p, k in ((q, 0), (q - across, 1), (q + across, 1), (q - across, 2), (q + across, 2))],
                dtype=np.float64))
            out.append(wedge ^ prism(slab ^ cell, bottom, roof))
            for p, zp, side in beyond:
                region = side ^ cell
                if not region.is_empty():
                    out.append(cone_hull(p, zp, p, zp, t.batter, end, segments, up) ^ prism(region, bottom, roof))
    return union(raised), union(cut)


def _region(corners):
    """A convex plan from its corners, either way round."""
    return mf.CrossSection([np.ascontiguousarray(corners, dtype=np.float64)], mf.FillRule.NonZero)


def _straight(t, free):
    """The straight pieces of a terrace's free edge (`free`, straight pieces), cut again where its level bends: a
    terrace with its own level at each corner is linear on each triangle between them."""
    pieces = list(free)
    if t.plane is not None or _is_level(t):
        return pieces
    edges = shapely.MultiLineString([t.corners[[i, j]] for s in t.triangles for i, j in ((s[0], s[1]), (s[1], s[2]), (s[2], s[0]))])
    out = []
    for p, q in pieces:
        line = shapely.LineString([p, q])
        cuts = sorted({float(line.project(x)) for x in shapely.get_parts(line.intersection(edges)) if x.geom_type == "Point"})
        at = [0.0, *[c for c in cuts if 1e-9 < c < line.length - 1e-9], line.length]
        d = (q - p) / line.length
        out += [(p + d * u, p + d * v) for u, v in itertools.pairwise(at) if v - u > 1e-9]
    return out


def _nearest_cells(ends, segments, reach=BATTER_REACH_M):
    """For each straight piece (a, b) of an edge or a line, the part of the plan within `reach` where it's the nearest
    piece: where the distance to it (an upturned cone of slope one along it) is the lowest of them all.

    Two pieces that meet at a corner have the same cone round it, and where those coincide either could have that
    part of the plan: Manifold's union keeps one face of the two, and which can differ from one build of Manifold to
    another (Windows' and Linux's, say). So each piece's cone stands TIE_LIFT_M higher than the one before it, and
    the part goes to the first of them, wherever this runs; the cells move by no more than that."""
    ang = 2 * np.pi * np.arange(segments) / segments
    ring = np.column_stack([np.cos(ang), np.sin(ang)]) * reach
    distance = []
    for k, (a, b) in enumerate(ends):
        lift = k * TIE_LIFT_M
        pts = [[*a, lift], [*b, lift]] + [[*(p + r), reach + lift] for p in (a, b) for r in ring]
        distance.append(mf.Manifold.hull_points(np.ascontiguousarray(pts, dtype=np.float64)).as_original())
    ids = {m.original_id(): k for k, m in enumerate(distance)}
    lowest = union(distance).to_mesh64()
    v = np.asarray(lowest.vert_properties)[:, :3]
    f = np.asarray(lowest.tri_verts, np.int64)
    run = np.asarray(lowest.run_index, np.int64) // 3
    owner = np.full(len(f), -1)
    for r, oid in enumerate(np.asarray(lowest.run_original_id)):
        owner[run[r]:run[r + 1]] = ids.get(int(oid), -1)
    a, b, c = v[f[:, 0]], v[f[:, 1]], v[f[:, 2]]
    under = np.cross(b - a, c - a)[:, 2] < -1e-12                 # facing down: the lowest distance there
    cells = []
    for k in range(len(ends)):
        tris = [np.ascontiguousarray(v[t, :2][::-1], dtype=np.float64) for t in f[under & (owner == k)]]
        cells.append(mf.CrossSection(tris, mf.FillRule.NonZero) if tris else mf.CrossSection())
    return cells


def _cut_wins(region, design, origin, step=CONFLICT_STEP_M):
    """Where, in `region` (a plan about `origin`), one batter cuts the ground by more than another raises it, so
    Design takes the cut. The raise less the cut is continuous, so it's read on a `step` grid of nodes (Design.batters,
    the ground the option starts from), taken as linear on each half of a cell, and the part where it's below nothing
    is cut out of each half exactly. Whole cells in a row where it's below nothing go in as one rectangle."""
    x0, y0, x1, y1 = region.bounds()
    xs = np.arange(x0 - step, x1 + 2 * step, step)
    ys = np.arange(y0 - step, y1 + 2 * step, step)
    X, Y = np.meshgrid(xs + origin[0], ys + origin[1])
    raise_to, lower_to = design.batters(X, Y)
    h0 = np.ravel(design.base(X, Y))
    gap = (np.maximum(raise_to - h0, 0) - np.maximum(h0 - lower_to, 0)).reshape(X.shape)   # up less down: Design's test
    X, Y = X - origin[0], Y - origin[1]
    below = gap < 0
    whole = below[:-1, :-1] & below[:-1, 1:] & below[1:, :-1] & below[1:, 1:]            # cells below nothing throughout
    pieces = []
    for j in range(whole.shape[0]):
        row = np.r_[False, whole[j], False].astype(np.int8)
        starts, stops = np.flatnonzero(np.diff(row) == 1), np.flatnonzero(np.diff(row) == -1)
        for i0, i1 in zip(starts, stops, strict=True):
            pieces.append(np.array([[xs[i0], ys[j]], [xs[i1], ys[j]], [xs[i1], ys[j + 1]], [xs[i0], ys[j + 1]]], float))
    some = (below[:-1, :-1] | below[:-1, 1:] | below[1:, :-1] | below[1:, 1:]) & ~whole
    for j, i in zip(*np.nonzero(some), strict=True):
        for tri in (((j, i), (j, i + 1), (j + 1, i + 1)), ((j, i), (j + 1, i + 1), (j + 1, i))):
            g = np.array([gap[k] for k in tri])
            if (g < 0).any():
                pieces.append(_below_nothing(np.array([[X[k], Y[k]] for k in tri]), g))
    if not pieces:
        return mf.CrossSection()
    return mf.CrossSection([np.ascontiguousarray(q, dtype=np.float64) for q in pieces], mf.FillRule.NonZero) ^ region


def _below_nothing(pts, g):
    """The part of a triangle (corners `pts`) where a value linear over it, `g` at the corners, is below nothing."""
    out = []
    for k in range(3):
        a, b = k, (k + 1) % 3
        if g[a] < 0:
            out.append(pts[a])
        if (g[a] < 0) != (g[b] < 0):
            t = g[a] / (g[a] - g[b])
            out.append(pts[a] + t * (pts[b] - pts[a]))
    return np.ascontiguousarray(out, dtype=np.float64)


def _terrace_columns(t, plan_cs, origin, zb, top, paving):
    """Everything over `plan_cs` below terrace `t`'s level less `paving` (from a metre above `zb`), and everything
    above it."""
    col = prism(plan_cs, zb + 1.0, top + 1.0)
    if t.plane is not None:
        level, at, grad = t.plane
        return split_by_plane(col, (at[0] - origin[0], at[1] - origin[1], level - paving), -np.asarray(grad, float))
    if _is_level(t):                                    # its own level at each corner, the same at every one
        return split_by_plane(col, (0.0, 0.0, _level_of(t) - paving), (0.0, 0.0))
    # Its own level at each corner, linear between them on its triangles (Terrace.triangles)
    surface = close_surface(np.column_stack([t.corners - origin, t.levels - paving]), t.triangles, zb + 0.5)
    return surface ^ col, col - surface


def _stair_columns(st, origin, zb, top):
    """Everything over a flight's outline below the ground it stands on (Stair.z), and everything above it."""
    col = prism(section(st.poly.buffer(EDGE_M, join_style="mitre"), origin), zb + 1.0, top + 1.0)
    a = st.a - origin
    ramp = (a[0], a[1], st.foot - 0.03)
    rise = st.along * st.rise / st.length
    if st.cross is None:
        return split_by_plane(col, ramp, rise)
    # On a foot that slopes across it: the higher of the ramp and the ground at its foot, carried in level under it.
    left, right = st.cross
    side = np.array([-st.along[1], st.along[0]])                   # to the left, facing up the flight
    level = (a[0], a[1], (left + right) / 2 - 0.03)
    below = split_by_plane(col, ramp, rise)[0] + split_by_plane(col, level, side * (left - right) / st.width)[0]
    return below, col - below


def _wall_solid(w, ground, origin):
    """A new wall as a solid: from its face to its back (its body's depth) and from its foot (its toe, less any
    embedment, walls.foot) to its top, stepped where its top steps."""
    pts, _, chain, toe, top = walls_mod.wall_levels(w, ground)
    face = pts - origin
    return strip(face, offset_behind(pts, walls_mod.body_depth(w), w["side"]) - origin, np.maximum(top, toe + 1e-3),
                 walls_mod.foot(w, chain, toe, clear=0.0))


def _treads(st, origin):
    """A flight's treads as solids, as Stair.mesh draws them: each from below the foot to its own top."""
    going, hw = st.length / st.n, st.width / 2
    side = np.array([-st.along[1], st.along[0]])
    out = []
    for i, level in enumerate(st.tops()):
        p0, p1 = st.a + st.along * going * i, st.a + st.along * going * (i + 1)
        tread = Polygon([p0 - side * hw, p1 - side * hw, p1 + side * hw, p0 + side * hw])
        out.append(prism(section(tread, origin), st.foot + st.rise / st.n * i - 0.3, level))
    return out
