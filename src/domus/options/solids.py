"""A wall option as closed solids, with Manifold (manifold3d): the ground as it is and as designed, the new walls and
steps, and the soil the option cuts and fills, built from the option's own shapes on the ground the build draws.

domus.options.scheme works an option out point by point; the solids follow the same rules as shapes:

- The ground as it is: the triangles of the ground the build draws (a site's tin.DrawnGround), closed into a solid
  with sides and a flat bottom below everything.
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
- The soil (scheme.soil_change): before, the ground as it is, but at its toe inside an existing wall; after, inside a
  new wall or an existing wall the option keeps, at that wall's toe (the lowest where walls meet); under a flight of
  steps, its ramp; in a terrace, its level less any paving; and where a batter moves the ground, the batter. Everywhere
  else the soil stays as it was.

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

A pure part of the model: it reads no site, and takes the option worked out (a scheme.Design), its walls levelled and
the site's existing walls from its caller.
"""

import itertools

import manifold3d as mf
import numpy as np
import shapely
from shapely.geometry import Polygon, box

from ..geo.mesh import offset_behind, resample_line
from ..model import walls as walls_mod
from .scheme import BATTER_REACH_M

SEGMENTS = 128          # the sides of a batter's cone
EDGE_M = 1e-6           # how far a solid reaches past the part of the plan it's worked out in, so no two share a face
WALL_SPACING_M = 0.25   # a new wall's toe is known this often along it (Design.wall_levels)
MARGIN_M = 1.0          # how far past the regrade area the ground is taken
CONFLICT_STEP_M = 0.02  # where batters disagree, how finely the larger change is found (scheme takes it point by point)


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


# --- an option as solids ---------------------------------------------------------------------

class Solids:
    """An option as solids (option_solids()), in metres about `origin` (E, N), heights as they are.

    `existing` is the ground as it is over the regrade area, and `designed` the pieces of the designed ground there,
    side by side; `walls` the new walls, each from its face to its back and its toe to its top, and `steps` the treads
    of every flight; `cut` and `fill` the pieces of soil the option takes away and brings in."""

    def __init__(self, origin, existing, designed, walls, steps, cut, fill):
        self.origin = origin
        self.existing, self.designed, self.walls, self.steps = existing, designed, walls, steps
        self.cut, self.fill = cut, fill

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


def wall_soil_line(w, ground=None):
    """A wall's face, its back and its toe at points along it, as scheme.wall_body works out its plan and the soil
    under it: a new wall's (levelled by Design.wall_levels, `ground` None), or an existing wall's on `ground`."""
    if ground is None:
        pts, chain = resample_line(np.asarray(w["line"], float)[:, :2], WALL_SPACING_M)
        face = pts[:, :2]
        return face, offset_behind(face, w["thickness"], w["side"]), np.interp(chain, w["toe_chain"], w["toe"])
    pts, nrm, _, _, _ = walls_mod.wall_levels(w, ground)
    half = 0.5 * w.get("batter", 0.0)
    face = pts - nrm * half
    back = pts + nrm * half + offset_behind(pts, w["thickness"], w["side"]) - pts
    return face, back, ground.sample(face[:, 0], face[:, 1])


def _widened(face, back, by):
    """A wall's face and back moved `by` apart, and on past its ends: a solid that reaches past its plan."""
    face, back = face.copy(), back.copy()
    across = back - face
    length = np.maximum(np.hypot(*across.T), 1e-12)[:, None]
    face, back = face - across / length * by, back + across / length * by
    for line in (face, back):
        for k, j in ((0, 1), (-1, -2)):
            t = line[k] - line[j]
            line[k] = line[k] + t / max(float(np.hypot(*t)), 1e-12) * by
    return face, back


def option_solids(design, walls, existing_walls, segments=SEGMENTS):
    """An option as solids (Solids): `design` worked out (a scheme.Design, on a site's drawn ground for the solids to
    be exact), `walls` its walls levelled (Design.wall_levels) and `existing_walls` the site's (domus.walls dicts)."""
    garden = design.garden
    origin = np.floor(np.asarray(garden.bounds[:2], float))
    g0, g1, g2, g3 = garden.bounds
    window = (g0 - MARGIN_M, g1 - MARGIN_M, g2 + MARGIN_M, g3 + MARGIN_M)
    V, F = ground_triangles(design.existing, window)
    V = np.column_stack([np.asarray(V, float)[:, :2] - origin, np.asarray(V, float)[:, 2]])
    corners = [t.z(*np.asarray(t.poly.exterior.coords)[:, :2].T) for t in design.terraces]
    zb = float(np.floor(min([np.nanmin(V[F, 2]), *(np.min(z) for z in corners)]))) - 3.0
    top = float(np.ceil(max([np.nanmax(V[F, 2]), *(np.max(z) for z in corners)]))) + 5.0
    win = section(box(*window), origin)
    ground = close_surface(V, F, zb) ^ prism(win, zb - 1.0, top + 1.0)
    area = section(garden, origin)

    def tall(cs):
        """A plan, through everything."""
        return prism(cs, zb - 2.0, top + 2.0)

    def wide(geom):
        return section(geom, origin).offset(EDGE_M, mf.JoinType.Miter)

    # The soil before: the ground, but at its toe inside an existing wall.
    old = [wall_soil_line(w, design.existing) for w in existing_walls]
    before = ground
    if old:
        holes = union([tall(section(Polygon(np.vstack([f, b[::-1]])).buffer(EDGE_M), origin)) for f, b, _ in old])
        toes = [strip(*_widened(f - origin, b - origin, 2 * EDGE_M), t, zb) for f, b, t in old]
        before = union([ground - holes, *toes])

    cut, fill = [], []

    def soil(region, after, above):
        """The soil moved in `region` (a plan), where `after` holds everything below the soil after and `above`
        everything above it."""
        if region.is_empty():
            return
        here = tall(region)
        fill.append((after ^ here) - before)
        cut.append((before ^ here) ^ above)

    # The walls, new and kept: the soil is at the wall's toe, the lowest where two meet.
    kept = [(w, line) for w, line in zip(existing_walls, old, strict=True) if w["id"] in design.keep_existing]
    bodies = []                                                  # (plan, below the toe, above it, a new wall's)
    for (face, back, toe), new in [(wall_soil_line(w), True) for w in walls] + [(line, False) for _, line in kept]:
        wf, wb = _widened(face - origin, back - origin, 2 * EDGE_M)
        plan = section(Polygon(np.vstack([face, back[::-1]])).buffer(EDGE_M), origin) ^ area
        bodies.append((plan, strip(wf, wb, toe, zb + 1.0), strip(wf, wb, top + 1.0, toe), new))
    walled = mf.CrossSection.batch_boolean([b[0] for b in bodies], mf.OpType.Add) if bodies else mf.CrossSection()
    for cell, members in _overlay([b[0] for b in bodies]):
        if not any(bodies[k][3] for k in members):               # only kept walls: the soil stays at their toes
            continue
        after, above = bodies[members[0]][1], bodies[members[0]][2]
        for k in members[1:]:
            after, above = after ^ bodies[k][1], above + bodies[k][2]
        soil(cell, after, above)

    # Steps: the last flight a point is under gives its ground.
    stepped = mf.CrossSection()
    ramps = []
    for st in reversed(design.stairs):
        below, over = _stair_columns(st, origin, zb, top)
        foot = (section(st.poly, origin) ^ area) - stepped
        ramps.append((foot, below))
        soil(foot - walled, below, over)
        stepped = stepped + foot

    # Terraces: the first a point is in gives its level, and the soil is any paving's depth under it.
    taken = mf.CrossSection()
    levels = []
    for t in design.terraces:
        own = ((section(t.poly, origin) ^ area) - taken) - stepped
        taken = taken + (section(t.poly, origin) ^ area)
        level, over = _terrace_columns(t, wide(t.poly), origin, zb, top, 0.0)
        levels.append((own, level))
        if t.paving:
            level, over = _terrace_columns(t, wide(t.poly), origin, zb, top, t.paving)
        soil(own - walled, level, over)

    # The rest of the regrade area: where the batters raise the ground the soil is theirs, and where they only cut it,
    # it comes down to their cut.
    rest = (area - taken) - stepped
    # The batters run on below the bottom of `box_all` and above its top, so what's above or below them in it is
    # only what's above the raise's surface or below the cut's.
    fills, cuts = [], []
    for t, free in zip(design.terraces, design.free, strict=True):
        if not free.is_empty:
            raise_t, cut_t = _batter(t, free, origin, zb + 0.25, top + 2.5, segments)
            fills.append(raise_t)
            cuts.append(cut_t)
    designed = []
    if fills:
        raise_to, cut_to = union(fills), union(cuts)
        box_all = prism(win, zb + 0.5, top + 1.5)
        under_cut = box_all - cut_to
        up = outline(raise_to - ground) ^ rest
        lowered = outline(ground ^ cut_to) ^ rest
        both = up ^ lowered
        if not both.is_empty():     # raised by one batter and cut by another: the larger change wins, as in Design
            cut_wins = _cut_wins(both, design, origin)
            up = up - cut_wins
        down = lowered - up
        soil(up - walled, raise_to, box_all - raise_to)
        soil(down - walled, under_cut, cut_to)
        designed += [raise_to ^ tall(up), under_cut ^ tall(down), ground ^ tall((rest - up) - down)]
    else:
        designed.append(ground ^ tall(rest))
    designed += [level ^ tall(own) for own, level in levels if not own.is_empty()]
    designed += [below ^ tall(foot) for foot, below in ramps if not foot.is_empty()]

    return Solids(origin, ground ^ tall(area), [d for d in designed if not d.is_empty()],
                  [_wall_solid(w, design.existing, origin) for w in walls],
                  [tread for st in design.stairs for tread in _treads(st, origin)],
                  [c for c in cut if not c.is_empty()], [f for f in fill if not f.is_empty()])


def _batter(t, free, origin, low, high, segments):
    """Terrace `t`'s batters from its free edge `free`, as two solids: all they raise the ground to (on down to `low`)
    and all they cut away (on up to `high`), out to BATTER_REACH_M.

    Design raises a point to the terrace's level at the nearest point of the free edge, less the distance over the
    batter, and cuts it to that level plus the distance. Where the level is the same all along the edge, the nearest
    point is also the one that gives the most, so the hulls of cones along the edge are the batters exactly. Where it
    isn't, each straight piece of the edge gives the batter only where it's the nearest piece (its part of the plan,
    from the lower envelope of the distances to the pieces), and there it's a wedge along the piece, or a cone round
    whichever end is nearest."""
    pieces = _straight(t, free)
    ends = np.array([[p, q] for p, q in pieces]) - origin
    z = [tuple(float(v) for v in t.z(np.array([p[0], q[0]]), np.array([p[1], q[1]]))) for p, q in pieces]
    if t.plane is not None and not np.any(t.plane[2]):         # level: the nearest point gives the most
        hulls = [(cone_hull(a, za, b, zb, t.batter, low, segments, False), cone_hull(a, za, b, zb, t.batter, high, segments, True))
                 for (a, b), (za, zb) in zip(ends, z, strict=True)]
        return union([h[0] for h in hulls]), union([h[1] for h in hulls])
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
    """The straight pieces of a terrace's free edge, cut where its level bends: a terrace with its own level at each
    corner is linear on each triangle between them."""
    pieces = list(_pieces(free))
    if t.plane is not None:
        return pieces
    tri = t._lin.tri
    edges = shapely.MultiLineString([tri.points[[i, j]] for s in tri.simplices for i, j in ((s[0], s[1]), (s[1], s[2]), (s[2], s[0]))])
    out = []
    for p, q in pieces:
        line = shapely.LineString([p, q])
        cuts = sorted({float(line.project(x)) for x in shapely.get_parts(line.intersection(edges)) if x.geom_type == "Point"})
        at = [0.0, *[c for c in cuts if 1e-9 < c < line.length - 1e-9], line.length]
        d = (q - p) / line.length
        out += [(p + d * u, p + d * v) for u, v in itertools.pairwise(at) if v - u > 1e-9]
    return out


def _nearest_cells(ends, segments):
    """For each straight piece (a, b) of an edge, the part of the plan within BATTER_REACH_M where it's the nearest
    piece: where the distance to it (an upturned cone of slope one along it) is the lowest of them all."""
    ang = 2 * np.pi * np.arange(segments) / segments
    ring = np.column_stack([np.cos(ang), np.sin(ang)]) * BATTER_REACH_M
    distance = []
    for a, b in ends:
        pts = [[*a, 0.0], [*b, 0.0]] + [[*(p + r), BATTER_REACH_M] for p in (a, b) for r in ring]
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
    the ground as it is), taken as linear on each half of a cell, and the part where it's below nothing is cut out of
    each half exactly. Whole cells in a row where it's below nothing go in as one rectangle."""
    x0, y0, x1, y1 = region.bounds()
    xs = np.arange(x0 - step, x1 + 2 * step, step)
    ys = np.arange(y0 - step, y1 + 2 * step, step)
    X, Y = np.meshgrid(xs + origin[0], ys + origin[1])
    raise_to, lower_to = design.batters(X, Y)
    h0 = np.ravel(design.existing.sample(X, Y))
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
    # Its own level at each corner, linear between them on the triangles Domus interpolates on (where four corners
    # lie on a circle there's a choice of diagonal: take the interpolator's).
    corners = np.asarray(t._lin.tri.points, float) - origin
    z = np.asarray(t._lin.values, float).ravel() - paving
    surface = close_surface(np.column_stack([corners, z]), t._lin.tri.simplices, zb + 0.5)
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
    """A new wall as a solid: from its face to its back and from its toe to its top, stepped where its top steps."""
    pts, _, _, toe, top = walls_mod.wall_levels(w, ground)
    face = pts - origin
    return strip(face, offset_behind(pts, w["thickness"], w["side"]) - origin, np.maximum(top, toe + 1e-3), toe)


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
