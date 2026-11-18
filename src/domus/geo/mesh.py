"""Minimal triangle-mesh helpers (site coordinates: E, N, height)."""

import numpy as np


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


def cross2(a, b):
    """z-component of the cross product of 2D vectors (array-friendly)."""
    a = np.asarray(a)
    b = np.asarray(b)
    return a[..., 0] * b[..., 1] - a[..., 1] * b[..., 0]


class Mesh:
    def __init__(self, vertices=None, faces=None):
        self.vertices = np.zeros((0, 3)) if vertices is None else np.asarray(vertices, float)
        self.faces = np.zeros((0, 3), int) if faces is None else np.asarray(faces, int)

    def add(self, vertices, faces):
        base = len(self.vertices)
        self.vertices = np.vstack([self.vertices, np.asarray(vertices, float)])
        self.faces = np.vstack([self.faces, np.asarray(faces, int) + base])
        return self

    def extend(self, other):
        return self.add(other.vertices, other.faces)

    def __len__(self):
        return len(self.faces)


class Surface:
    """A triangulated surface (`vertices` x, y, z; `faces` in threes), indexed once to be read at points again and
    again: z(e, n) gives the highest of the triangles over each point, NaN where none is. Each point tests only the
    triangles whose boxes cover its cell of a grid as fine as the triangles' median box, so it finds the triangle the
    surface has, however it was cut."""

    def __init__(self, vertices, faces):
        V, F = np.asarray(vertices, float), np.asarray(faces, int).reshape(-1, 3)
        self.sums = np.zeros((0, 9))
        if not len(F):
            return
        tri = V[F]
        a, b, c = tri[:, 0], tri[:, 1], tri[:, 2]
        den = (b[:, 1] - c[:, 1]) * (a[:, 0] - c[:, 0]) + (c[:, 0] - b[:, 0]) * (a[:, 1] - c[:, 1])
        some = np.abs(den) > 1e-15              # a triangle with no area covers no point
        tri, a, b, c, den = tri[some], a[some], b[some], c[some], den[some]
        # Each triangle's sums, so a point needs only its offset from the third corner: the first two corners' weights
        # (u, v; the third's is what's left), and the height.
        self.sums = np.column_stack([c[:, 0], c[:, 1], (b[:, 1] - c[:, 1]) / den, (c[:, 0] - b[:, 0]) / den,
                                     (c[:, 1] - a[:, 1]) / den, (a[:, 0] - c[:, 0]) / den,
                                     c[:, 2], a[:, 2] - c[:, 2], b[:, 2] - c[:, 2]])
        if not len(tri):
            return
        lo, hi = tri[:, :, :2].min(axis=1), tri[:, :, :2].max(axis=1)
        self.size = float(np.median((hi - lo).max(axis=1))) or 1.0
        self.base = lo.min(axis=0)
        i0, i1 = np.floor((lo - self.base) / self.size).astype(np.int64), np.floor((hi - self.base) / self.size).astype(np.int64)
        span = i1 - i0 + 1
        count = span[:, 0] * span[:, 1]
        face = np.repeat(np.arange(len(tri)), count)
        k = np.arange(count.sum()) - np.repeat(np.cumsum(count) - count, count)
        self.width = int(i1[:, 0].max()) + 2
        key = (i0[face, 0] + k % span[face, 0]) + self.width * (i0[face, 1] + k // span[face, 0])
        order = np.argsort(key, kind="stable")
        self.key, self.face = key[order], face[order]

    def z(self, e, n):
        e, n = np.asarray(e, float), np.asarray(n, float)
        out = np.full(e.size, np.nan)
        if not len(self.sums) or not e.size:
            return out.reshape(e.shape)
        size, base, width = self.size, self.base, self.width
        pe, pn = e.ravel(), n.ravel()
        cx, cy = np.floor((pe - base[0]) / size).astype(np.int64), np.floor((pn - base[1]) / size).astype(np.int64)
        inside_grid = (cx >= 0) & (cy >= 0) & (cx < width)
        pkey = np.where(inside_grid, cx + width * cy, -1)
        start, stop = np.searchsorted(self.key, pkey, "left"), np.searchsorted(self.key, pkey, "right")
        many = np.where(inside_grid, stop - start, 0)
        point = np.repeat(np.arange(pe.size), many)
        s = self.sums[self.face[np.repeat(start, many) + (np.arange(many.sum()) - np.repeat(np.cumsum(many) - many, many))]]
        dx, dy = pe[point] - s[:, 0], pn[point] - s[:, 1]
        u = s[:, 2] * dx + s[:, 3] * dy
        v = s[:, 4] * dx + s[:, 5] * dy
        hit = (u >= -1e-9) & (v >= -1e-9) & (1 - u - v >= -1e-9)
        np.fmax.at(out, point[hit], (s[:, 6] + u * s[:, 7] + v * s[:, 8])[hit])
        return out.reshape(e.shape)


def surface_z(vertices, faces, e, n):
    """Heights of a triangulated surface (`vertices` x, y, z; `faces` in threes) at points (e, n): the highest of the
    triangles over each point, NaN where none is (Surface, for points read once)."""
    return Surface(vertices, faces).z(e, n)


CIRCLE_NEAR_M = 0.005      # corners this near one circle are on it, to the precision they're given (delaunay_by_height)


def _off_circle(p, q, r, s):
    """How far each point s lies from the circle through p, q and r (arrays of points, (k, 2)), inf where those three
    are in a line."""
    ax, ay, bx, by, cx, cy = p[:, 0], p[:, 1], q[:, 0], q[:, 1], r[:, 0], r[:, 1]
    den = 2.0 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    with np.errstate(divide="ignore", invalid="ignore"):
        a2, b2, c2 = ax * ax + ay * ay, bx * bx + by * by, cx * cx + cy * cy
        ux = (a2 * (by - cy) + b2 * (cy - ay) + c2 * (ay - by)) / den
        uy = (a2 * (cx - bx) + b2 * (ax - cx) + c2 * (bx - ax)) / den
        gap = np.abs(np.hypot(s[:, 0] - ux, s[:, 1] - uy) - np.hypot(ax - ux, ay - uy))
    return np.where(np.abs(den) > 1e-12, gap, np.inf)


def delaunay_by_height(points, heights, near_m=CIRCLE_NEAR_M, among=None, triangles=None):
    """Triangles for points with a height each, as (k, 3) index triples into `points` (E, N), counter-clockwise: their
    Delaunay triangles, worked out about the first point so a grid millions of metres out loses nothing to rounding,
    but where two triangles' four corners lie within `near_m` of one circle, cut along the diagonal whose ends are
    closer in height (`heights`). There Delaunay could take either diagonal, to the precision the corners are given,
    and along the closer pair a bank is drawn down its slope, where the other would draw a ridge or a gully across it,
    as the ground model cuts its grid's cells (domus.model.tin.cut_cells). Where both pairs are as close, the diagonal
    from the corner given first. `among` (indices into `points`) cuts only the pairs with one of those among their
    corners again; None, every pair. Given `triangles` (index triples into `points`, such as a Delaunay of the
    caller's own), it cuts those again instead of working its own out, leaving each one it doesn't cut as it was given,
    in its place and its turn.

    The pairs are cut in passes, each over the shared edges in order of their corners, a triangle cut at most once a
    pass, until none is cut: each cut takes an edge whose ends differ less in height (or, as close, the one from the
    corner given first), so it ends. Four corners on one circle end the same whichever way Delaunay first cut them;
    five or more can end either way."""
    from scipy.spatial import Delaunay

    p = np.asarray(points, float)[:, :2]
    p = p - p[0]
    h = np.asarray(heights, float).ravel()
    given = triangles is not None
    faces = np.array(triangles if given else Delaunay(p).simplices, np.int64).reshape(-1, 3)
    recut = np.zeros(len(faces), bool)
    pick = None if among is None else np.isin(np.arange(len(p)), np.asarray(among, np.int64))
    for _ in range(10 * len(faces) + 10):
        edges = np.concatenate([faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [2, 0]]])
        other = np.concatenate([faces[:, 2], faces[:, 0], faces[:, 1]])
        tri = np.tile(np.arange(len(faces)), 3)
        key = np.sort(edges, axis=1)
        order = np.lexsort((key[:, 1], key[:, 0]))
        key, other, tri = key[order], other[order], tri[order]
        twin = np.flatnonzero((key[1:] == key[:-1]).all(axis=1))           # an edge two triangles share: k and k + 1
        a, b, c, d = key[twin, 0], key[twin, 1], other[twin], other[twin + 1]
        across = cross2(p[d] - p[c], p[a] - p[c]) * cross2(p[d] - p[c], p[b] - p[c]) < 0     # a and b either side of c-d
        near = np.minimum(_off_circle(p[a], p[b], p[c], p[d]), _off_circle(p[a], p[b], p[d], p[c])) <= near_m
        dab, dcd = np.abs(h[a] - h[b]), np.abs(h[c] - h[d])
        better = (dcd < dab - 1e-9) | ((np.abs(dcd - dab) <= 1e-9) & (np.minimum(c, d) < np.minimum(a, b)))
        go = across & near & better
        if pick is not None:
            go &= pick[a] | pick[b] | pick[c] | pick[d]
        used, cut = set(), 0
        for k in np.flatnonzero(go):
            t1, t2 = int(tri[twin[k]]), int(tri[twin[k] + 1])
            if t1 in used or t2 in used:
                continue
            faces[t1] = (c[k], d[k], a[k])
            faces[t2] = (d[k], c[k], b[k])
            recut[[t1, t2]] = True
            used.update((t1, t2))
            cut += 1
        if not cut:
            break
    turn = cross2(p[faces[:, 1]] - p[faces[:, 0]], p[faces[:, 2]] - p[faces[:, 0]]) < 0
    if given:
        turn &= recut
    faces[turn] = faces[turn][:, [0, 2, 1]]
    return faces


def signed_area(ring):
    x, y = np.asarray(ring, float)[:, :2].T
    return 0.5 * (np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))


def triangulate_polygon(ring):
    """Ear-clipping triangulation of a simple polygon (no holes). Returns index triples, CCW."""
    pts = np.asarray(ring, float)[:, :2]
    if np.allclose(pts[0], pts[-1], rtol=0, atol=1e-9):  # absolute: grid coordinates are ~1e5
        pts = pts[:-1]
    idx = list(range(len(pts)))
    if signed_area(pts) < 0:
        idx.reverse()
    tris = []

    def is_convex(a, b, c):
        return cross2(pts[b] - pts[a], pts[c] - pts[b]) > 1e-12

    def inside(p, a, b, c):
        d1 = cross2(pts[b] - pts[a], p - pts[a])
        d2 = cross2(pts[c] - pts[b], p - pts[b])
        d3 = cross2(pts[a] - pts[c], p - pts[c])
        return d1 >= -1e-12 and d2 >= -1e-12 and d3 >= -1e-12

    guard = 0
    while len(idx) > 3 and guard < 10000:
        guard += 1
        n = len(idx)
        for k in range(n):
            a, b, c = idx[k - 1], idx[k], idx[(k + 1) % n]
            if not is_convex(a, b, c):
                continue
            if any(inside(pts[j], a, b, c) for j in idx if j not in (a, b, c)):
                continue
            tris.append((a, b, c))
            idx.pop(k)
            break
        else:
            break  # degenerate input; give up on the rest
    if len(idx) == 3:
        tris.append(tuple(idx))
    return tris


def prism(ring, bottom, top):
    """Closed vertical prism over a polygon ring; bottom/top are scalars or per-vertex arrays."""
    pts = np.asarray(ring, float)[:, :2]
    if np.allclose(pts[0], pts[-1], rtol=0, atol=1e-9):  # absolute: grid coordinates are ~1e5
        pts = pts[:-1]
    if signed_area(pts) < 0:
        pts = pts[::-1]
    n = len(pts)
    zb = np.broadcast_to(np.asarray(bottom, float), (n,))
    zt = np.broadcast_to(np.asarray(top, float), (n,))
    verts = np.vstack([np.column_stack([pts, zb]), np.column_stack([pts, zt])])
    faces = []
    for i in range(n):
        j = (i + 1) % n
        faces += [(i, j, n + j), (i, n + j, n + i)]
    for a, b, c in triangulate_polygon(pts):
        faces.append((n + a, n + b, n + c))  # top, CCW seen from above
        faces.append((c, b, a))              # bottom
    return Mesh(verts, faces)


def corner_offsets(line, side=1):
    """For each point of a polyline, the way to the same point of a line offset from it by 1 m (to the left of its
    direction, times `side`): the unit normal on a straight run, and at a corner the bisector, lengthened so the
    offset line keeps its distance from both legs (1.41 at a right angle, 2 at most). A point given twice, as
    where a wall's top steps, takes the leg it has."""
    pts = np.asarray(line, float)[:, :2]
    n = len(pts)
    out = np.zeros((n, 2))
    for i in range(n):
        legs = []
        for a, b in ((pts[max(i - 1, 0)], pts[i]), (pts[i], pts[min(i + 1, n - 1)])):
            length = np.hypot(*(b - a))
            if length > 1e-9:
                legs.append((b - a) / length)
        if not legs:                                    # a line of one point, or the same point throughout
            legs = [np.array([1.0, 0.0])]
        t = legs[0] + legs[-1]
        t = legs[0] if np.hypot(*t) < 1e-9 else t / np.hypot(*t)
        out[i] = side * np.array([-t[1], t[0]]) / max(float(legs[0] @ t), 0.5)
    return out


def offset_behind(line, distance, side=1):
    """The points of a polyline moved `distance` to the left of its direction (times `side`), as the back of a wall
    is from its face: square off a straight run, and to the mitre at a corner. On the inside of a corner every
    point whose own offset would land in the other leg's wall goes to that mitre too, so the offset line never
    folds back on itself."""
    pts = np.asarray(line, float)[:, :2]
    out = pts + distance * corner_offsets(pts, side)
    chain = np.concatenate([[0], np.cumsum(np.hypot(*np.diff(pts, axis=0).T))])
    for i in range(1, len(pts) - 1):
        a, b = pts[i] - pts[i - 1], pts[i + 1] - pts[i]
        la, lb = np.hypot(*a), np.hypot(*b)
        if la < 1e-9 or lb < 1e-9:
            continue
        a, b = a / la, b / lb
        if cross2(a, b) * side <= 1e-6:                 # straight on, or turning away from the offset side
            continue
        half = np.sqrt(max(0.0, (1 + float(a @ b)) / 2))                       # the cosine of half the turn
        reach = distance * np.sqrt(max(0.0, 1 - half ** 2)) / max(half, 0.5)    # how far along each leg the mitre shadows
        out[np.abs(chain - chain[i]) < reach - 1e-9] = out[i]
    return out


def wall_strip(line, top, bottom, thickness, side, batter=0.0, toe=None, chain=None):
    """A wall of given thickness along a polyline. `side` (+1 left, -1 right of the line
    direction) is where the body sits; the line itself is one face of the wall. With a
    `batter` (m) the face leans back: it rises from its foot batter/2 in front of the line
    (at the `toe` level, below which it drops straight to the bottom) to the top batter/2
    behind the line.

    The mesh also carries `uv`, texture coordinates in metres: u the distance along the line
    (`chain`, or measured along `line`), v the distance over the cross-section from the top
    edge of the face, negative down the face (along its slope) and positive over the top and
    down the back, so a texture laid at true scale hangs from the wall's top edge.

    The two end caps have vertices of their own (the rows' first and last points again), so their
    texture runs across the wall's end instead of being smeared from one column of the face: there
    u is the distance across the end from the foot of the face and v the height below the wall's
    top at that end. `m.cap` marks those vertices."""
    pts = np.asarray(line, float)[:, :2]
    n = len(pts)
    normals = []
    for i in range(n):
        a = pts[max(i - 1, 0)]
        b = pts[min(i + 1, n - 1)]
        d = (b - a) / np.hypot(*(b - a))
        normals.append(np.array([-d[1], d[0]]))
    back = side * np.array(normals)
    foot = pts - 0.5 * batter * back
    head = pts + 0.5 * batter * back
    top = np.broadcast_to(np.asarray(top, float), (n,))
    bottom = np.broadcast_to(np.asarray(bottom, float), (n,))
    # Rows of the cross-section, from the foot of the face over the top to the foot of the back.
    rows = [np.column_stack([foot, bottom])]
    if batter and toe is not None:
        rows.append(np.column_stack([foot, np.broadcast_to(np.asarray(toe, float), (n,))]))
    behind = head + offset_behind(pts, thickness, side) - pts      # the wall keeps its thickness round a corner
    rows += [np.column_stack([head, top]), np.column_stack([behind, top]), np.column_stack([behind, bottom])]
    if chain is None:
        chain = np.concatenate([[0], np.cumsum(np.hypot(*np.diff(pts, axis=0).T))])
    slope_from = np.asarray(toe, float) if batter and toe is not None else bottom
    v_from = -np.hypot(batter, top - slope_from)                       # foot of the sloping face
    vs = [v_from - (slope_from - bottom)] if batter and toe is not None else []
    vs += [v_from, np.zeros(n), np.full(n, thickness), thickness + top - bottom]
    k = len(rows)
    faces = []
    for r in range(k - 1):                           # face, top and back
        for i in range(r * n, r * n + n - 1):
            faces += [(i, i + 1, i + n + 1), (i, i + n + 1, i + n)]
    verts = np.vstack(rows)
    s0, e0 = k * n, k * n + k                        # the caps' own vertices
    for r in range(1, k - 1):
        faces.append((s0, s0 + r, s0 + r + 1))                 # start cap
        faces.append((e0, e0 + r + 1, e0 + r))                 # end cap
    cap_xyz, cap_uv = [], []
    for i in (0, n - 1):
        p = verts[[r * n + i for r in range(k)]]
        cap_xyz.append(p)
        cap_uv.append(np.column_stack([(p[:, :2] - pts[i]) @ back[i] + 0.5 * batter, p[:, 2] - top[i]]))
    m = Mesh(np.vstack([verts] + cap_xyz), faces)
    m.uv = np.vstack([np.column_stack([np.tile(np.asarray(chain, float), k), np.concatenate(vs)])] + cap_uv)
    m.cap = np.arange(len(m.vertices)) >= s0
    return m
