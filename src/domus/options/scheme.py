"""A wall option as designed ground: terraces, the walls that hold them, the batters between them
and the ground left alone, and what it all comes to (cut and fill, flat lawn, wall heights, cover
over the drains).

Options are drawn in the site frame of options/_site.toml: s along a reference boundary
from its first corner, o in from it, into the lot. A terrace is a polygon with
a level: a plane (`level` at `at`, falling `fall` towards `fall_to`) or, with three numbers per
vertex, its own level at each corner. A wall is a line; by default its top is the designed ground
behind it plus `upstand_m` and its toe the designed ground in front of it, so moving a terrace's
level moves its walls with it.

The designed ground is the existing ground (the ground it's given: a site's best, as its build draws it) with each
terrace's level inside its polygon. Terrace edges not held by a wall slope back to the existing ground at the terrace's
batter, inside the regrade area only: whatever a batter cannot reach there is reported as a step
at the edge of that area.

Cut and fill are soil, measured from the existing ground (soil_moved). The soil a new wall holds back counts; the wall
itself, from its face to its back and from its toe to its top, doesn't, nor does what it's built on and with: a wall
that says how it's built stands in a trench from its face, which its base, bedding, courses and drainage aggregate
fill from the trench's bottom; the soil it gives over the aggregate (`seal_m`) is soil, brought back in. Nor does the
paving of a paved terrace (`kind` paving), whose level is the paving's top: the soil is `paving_m` under it. An
existing wall isn't soil either: under one, the soil is at its toe.

A flight of steps is a line from its foot to its head and a width. It takes its levels from the
designed ground just beyond each end, so it follows the terraces it joins, and the ground under it
is a ramp between them.

A pure part of the model: it reads no site. domus.design reads a site's options/_site.toml, ground, regrade area,
house, walls, services and easements, and passes them in (a Context).
"""

import math

import numpy as np
import shapely
from scipy.interpolate import LinearNDInterpolator, NearestNDInterpolator
from shapely.geometry import LineString, Polygon
from shapely.ops import unary_union

from ..errors import DomusError
from ..geo.mesh import Mesh, offset_behind, prism, resample_line
from ..model import walls as walls_mod
from ..model.house import COMPASS
from ..model.walls import bay_levels, line_normals
from . import materials

LIDAR_TOLERANCE_M = 0.15     # height uncertainty of the existing ground, for the range on cut and fill
BATTER_REACH_M = 6.0         # how far a batter reaches from a terrace's free edge
HELD_M = 0.15                # a terrace's edge is held, and gets no batter, this far past a wall's back or round steps
WALL_STEP_M = 0.25           # a new wall's levels are worked out this often along it
TOE_PROBE_M = 0.35           # its toe is the designed ground this far in front of its face
BACK_PROBE_M = 0.1           # and the ground it holds this far behind its back
PAVING_M = 0.2               # a paved terrace's paving, from its top to the soil: pavers on sand over a basecourse


class SiteFrame:
    """(s, o) <-> (E, N): s along a reference boundary from `origin` towards `toward`, o in from it. The lot lies
    on the boundary's right looking along it, or its left with side = "left"."""

    def __init__(self, origin, toward, side="right"):
        self.origin = np.asarray(origin, float)
        d = np.asarray(toward, float) - self.origin
        self.along = d / np.hypot(*d)
        self.inward = np.array([-self.along[1], self.along[0]]) * (1.0 if side == "left" else -1.0)

    def en(self, so):
        so = np.asarray(so, float)
        xy = self.origin + so[..., :1] * self.along + so[..., 1:2] * self.inward
        return np.concatenate([xy, so[..., 2:]], axis=-1)

    def so(self, en):
        p = np.asarray(en, float)[..., :2] - self.origin
        return np.stack([p @ self.along, p @ self.inward], axis=-1)


def _points(cfg, key_en, key_so, frame):
    """A point list given in site coordinates (`key_en`) or in the frame (`key_so`)."""
    if key_so in cfg:
        return frame.en(np.asarray(cfg[key_so], float))
    return np.asarray(cfg[key_en], float)


def _fall(cfg):
    """Fall as a gradient (rise over run) from "1:50", 0.02 or "2%"."""
    f = cfg.get("fall", 0)
    if isinstance(f, str):
        f = f.strip()
        if f.endswith("%"):
            return float(f[:-1]) / 100
        if ":" in f:
            a, b = f.split(":")
            return float(a) / float(b)
    return float(f)


def compass(direction):
    """The way a terrace falls, from its `fall_to`: a compass word on the site's grid ("north", "east", "south",
    "west") or a bearing in degrees clockwise from its north, as a unit vector (east, north)."""
    if isinstance(direction, str):
        if direction not in COMPASS:
            raise DomusError(f"fall_to = {direction!r}: give north, east, south, west, or a bearing in degrees")
        return np.array(COMPASS[direction])
    b = math.radians(float(direction))
    return np.array([math.sin(b), math.cos(b)])


class Terrace:
    """A terrace: a polygon with a level (a plane, or its own level at each corner), in the options' `frame`."""

    def __init__(self, cfg, defaults, frame):
        self.id = cfg["id"]
        self.name = cfg.get("name", self.id)
        self.kind = cfg.get("kind", "lawn")       # lawn | planting | paving | ground
        self.batter = float(cfg.get("batter", defaults["batter"]))
        # A paved terrace's level is the paving's top: the soil is this far under it.
        self.paving = float(cfg.get("paving_m", defaults.get("paving_m", PAVING_M))) if self.kind == "paving" else 0.0
        pts = _points(cfg, "polygon", "polygon_so", frame)
        self.poly = Polygon(pts[:, :2]).buffer(0)
        if pts.shape[1] == 3:
            # its own level at each corner, linear between them on its corners' Delaunay triangles, worked out about
            # its first corner: Qhull finds them from each corner's squared distance from the origin, which on the
            # site's grid is too large to keep their shape, so near four corners on one circle it would cut them as
            # the rounding fell, and could leave a corner out
            self._origin = pts[0, :2].copy()
            self._lin = LinearNDInterpolator(pts[:, :2] - self._origin, pts[:, 2])
            self._near = NearestNDInterpolator(pts[:, :2], pts[:, 2])
            self.plane = None
        else:
            at = frame.en(np.asarray(cfg["at_so"], float)) if "at_so" in cfg else \
                np.asarray(cfg.get("at", self.poly.representative_point().coords[0]), float)
            g = _fall(cfg)
            down = compass(cfg.get("fall_to", "north"))   # the direction the terrace falls towards
            self.plane = (float(cfg["level"]), at[:2], g * down)
        self.properties = cfg

    def z(self, e, n):
        e = np.asarray(e, float)
        n = np.asarray(n, float)
        if self.plane is not None:
            level, at, grad = self.plane
            return level - ((e - at[0]) * grad[0] + (n - at[1]) * grad[1])
        z = self._lin(e - self._origin[0], n - self._origin[1])
        bad = ~np.isfinite(z)
        if bad.any():
            z[bad] = self._carried(e[bad], n[bad])
        return z

    def _carried(self, e, n):
        """The level at points (e, n) that the interpolator reads as outside its triangles. Rounding puts a point on
        an edge of them (a batter's foot, say) on either side of it, the more often the larger the grid's
        coordinates, so a point within a micrometre of a triangle takes that triangle's level, carried on to it.
        Only a point further out takes the nearest corner's."""
        tri = self._lin.tri
        corners = tri.points[tri.simplices]                                    # (triangles, 3 corners, 2), about _origin
        ends = np.roll(corners, -1, axis=1)
        p = np.column_stack([e, n]) - self._origin
        rel = p[:, None, None, :] - corners[None]                              # (points, triangles, 3, 2)
        side = ends - corners
        u = np.clip((rel * side[None]).sum(-1) / (side * side).sum(-1)[None], 0.0, 1.0)
        off = rel - u[..., None] * side[None]
        gap = np.sqrt((off * off).sum(-1)).min(axis=2)                         # each point's distance from each triangle
        best = gap.argmin(axis=1)
        out = self._near(e, n)
        near = gap[np.arange(len(p)), best] < 1e-6
        if near.any():
            t = tri.transform[best[near]]                                      # each to its triangle's corners' weights
            w = np.einsum("ijk,ik->ij", t[:, :2], p[near] - t[:, 2])
            w = np.column_stack([w, 1.0 - w.sum(axis=1)])
            levels = np.asarray(self._lin.values, float).ravel()[tri.simplices[best[near]]]
            out[near] = (levels * w).sum(axis=1)
        return out

    def slope(self):
        if self.plane is not None:
            return float(np.hypot(*self.plane[2]))
        b = np.asarray(self.poly.exterior.coords)[:-1]
        zz = self.z(b[:, 0], b[:, 1])
        a = np.column_stack([b, np.ones(len(b))])
        coef = np.linalg.lstsq(a, zz, rcond=None)[0]
        return float(np.hypot(coef[0], coef[1]))


def option_wall(cfg, defaults, frame):
    """Wall dict (domus.walls style) for an option's wall; levels come later (Design.wall_levels)."""
    line = _points(cfg, "line", "line_so", frame)
    side = cfg.get("retained_side")
    bays = cfg.get("tops")
    if bays is not None and len(bays) != len(line) - 1:
        raise ValueError(f"wall {cfg['id']}: `tops` gives a level for each stretch of its line, so it needs "
                         f"{len(line) - 1} of them, not {len(bays)}")
    thickness = float(cfg.get("thickness_m", defaults["thickness_m"]))
    depth = float(cfg.get("depth_m", defaults.get("depth_m") or thickness))
    return {
        "id": cfg["id"],
        "name": cfg.get("name", cfg["id"]),
        "material": cfg.get("material", "block"),
        "status": "proposed",
        "confidence": "",
        "thickness": thickness,
        # How it's built, in the ground and behind its face (docs/options.md): its body `depth` deep from the face,
        # where `thickness` is what its set-out and its corners take; its capping course `cap_depth` deep, standing
        # `cap_overhang` out from the face; its first course `embed` below the ground at its toe: at least the first
        # number, the second for each metre the wall stands above it, and the third in whole courses, where each is
        # more; on `bedding` and a `base` course, in a trench `trench` wide from its face, with `seal` of soil over the
        # drainage aggregate behind the wall.
        "depth": depth,
        "cap_depth": float(cfg.get("cap_depth_m", defaults.get("cap_depth_m") or depth)),
        "cap_overhang": float(cfg.get("cap_overhang_m", defaults.get("cap_overhang_m", 0.0))),
        "embed": (float(cfg.get("embed_m", defaults.get("embed_m", 0.0))),
                  float(cfg.get("embed_per_m", defaults.get("embed_per_m", 0.0))),
                  int(cfg.get("embed_courses", defaults.get("embed_courses", 0)))),
        "seal": float(cfg.get("seal_m", defaults.get("seal_m", 0.0))),
        "bedding": float(cfg.get("bedding_m", defaults.get("bedding_m", 0.0))),
        "base": float(cfg.get("base_m", defaults.get("base_m", 0.0))),
        "trench": float(cfg.get("trench_m", defaults.get("trench_m") or depth)),
        # the shortest piece a named unit is cut to: a shorter one and a whole unit become two equal cuts (walls.set_out)
        "cut_min": float(cfg.get("cut_min_m", defaults.get("cut_min_m", 0.15))),
        # What it takes besides, for its materials (domus.options.materials): its units' pins and their cores, its
        # drainage pipe and filter fabric, and a timber wall's posts. None unless it says.
        **{k: cfg.get(k, defaults.get(k)) for k in MATERIAL_KEYS},
        "side": None if side is None else (1 if side == "left" else -1),
        "line": line[:, :2],
        "fixed_top": line[:, 2] if line.shape[1] == 3 else (float(cfg["top"]) if "top" in cfg else None),
        # a level top for each stretch of the line: the wall steps at the points between them
        "bay_tops": None if bays is None else np.asarray(bays, float),
        "height": cfg.get("height_m"),
        "upstand": float(cfg.get("upstand_m", defaults["upstand_m"])),
        "properties": cfg,
    }


# A wall's settings for its materials alone (domus.options.materials), which its option or [defaults] can give.
MATERIAL_KEYS = ("pins_per_unit", "pins_per_box", "core_void_m3", "pipe_m", "outlet_spacing_m", "geotextile",
                 "post_spacing_m")

STAIR_RISER_M = 0.17         # the riser a flight aims for
# How a flight is drawn, by its material: its colour, and for some a picture with the metres one copy covers.
# A flight of `sleepers` is two timber sleepers to a tread, and the picture is one sleeper seen along its length.
STEP_LOOKS = {"concrete": ("#b9b6ae", None), "sleepers": ("#6f5a45", ("steps-sleepers.jpg", (2.4, 0.22)))}
STAIR_STEEP = (0.19, 0.25)   # a riser over the first or a going under the second is flagged as steep
STAIR_PROUD_M = 0.02         # a tread is laid where it stands this far above the ground at the flight's foot


class Stair:
    """A straight flight of steps: its centre line from one end to the other, and its width. Each end takes the
    designed ground's level just beyond it, unless `foot` and `head` give the first and last point's levels.

    Where the ground slopes across the foot of the flight (a driveway that climbs past it), `foot` gives two
    levels: the ground at the left and at the right end of the foot line, as you face up the flight. The treads
    stay level and the foot line straight, and every tread runs the full width. The lowest tread is level with the
    ground at the high end of the foot line, so its riser is nothing there and the whole of the ground's rise at
    the low end. The risers above it are equal."""

    def __init__(self, cfg, frame):
        self.id = cfg["id"]
        self.name = cfg.get("name", self.id)
        self.material = cfg.get("material", "concrete")
        self.width = float(cfg.get("width_m", 1.0))
        line = _points(cfg, "line", "line_so", frame)[:, :2]
        self.a, self.b = line[0], line[-1]
        self.length = float(np.hypot(*(self.b - self.a)))
        self.along = (self.b - self.a) / self.length
        across = np.array([-self.along[1], self.along[0]]) * self.width / 2
        self.poly = Polygon([self.a - across, self.b - across, self.b + across, self.a + across])
        foot = cfg.get("foot")
        self.cross = None           # the ground at the left and right ends of the foot line, where it slopes
        if isinstance(foot, (list, tuple)):
            if len(foot) != 2:
                raise ValueError(f"steps {self.id}: `foot` is one level, or the two at the left and right ends of the foot line")
            if float(foot[0]) != float(foot[1]):
                self.cross = (float(foot[0]), float(foot[1]))
            foot = min(float(v) for v in foot)
        self.given = [foot, cfg.get("head")]
        self.risers = cfg.get("risers")
        self.properties = cfg

    def settle(self, sample):
        """Take the levels not given from the ground 0.3 m beyond each end (`sample`), put the lower end first, and
        count the risers."""
        beyond = np.array([self.a - 0.3 * self.along, self.b + 0.3 * self.along])
        found = sample(beyond[:, 0], beyond[:, 1])
        self.foot, self.head = (float(found[i]) if g is None else float(g) for i, g in enumerate(self.given))
        if self.head < self.foot:
            if self.cross:
                raise ValueError(f"steps {self.id}: `foot` gives two levels, so its line starts at the foot and its head is the higher end")
            self.a, self.b, self.along = self.b, self.a, -self.along
            self.foot, self.head = self.head, self.foot
        self.rise = self.head - self.foot
        self.n = int(self.risers or max(1, round(self.rise / STAIR_RISER_M)))
        self.first = max(self.cross) if self.cross and self.n > 1 else None    # the lowest tread, on a foot that slopes
        self.riser = (self.head - self.first) / (self.n - 1) if self.first is not None else self.rise / self.n

    def tops(self):
        """The level of each tread, from the lowest to the head."""
        if self.first is None:
            return [self.foot + self.riser * (i + 1) for i in range(self.n)]
        return [self.first + self.riser * i for i in range(self.n)]

    def base(self, across):
        """The ground at the foot line, `across` metres to the left of the centre line."""
        if self.cross is None:
            return np.full(np.shape(across), self.foot)
        left, right = self.cross
        return right + np.clip(np.asarray(across, float) / self.width + 0.5, 0.0, 1.0) * (left - right)

    def under(self, e, n):
        """The ground drawn under the flight: a ramp from foot to head, just under the steps."""
        chain = (np.asarray(e, float) - self.a[0]) * self.along[0] + (np.asarray(n, float) - self.a[1]) * self.along[1]
        return self.foot + np.clip(chain / self.length, 0.0, 1.0) * self.rise - 0.03

    def z(self, e, n, beside=None):
        """The ground the flight stands on: the ramp from foot to head, just under the steps. Where the ground
        slopes across the foot, that ground is carried in level under the flight until the ramp rises over it.
        With `beside` (points, one for each of `e`, `n`) the ground is read across the flight where those points
        are: at a wall's own face, for a wall that runs up beside the flight."""
        ramp = self.under(e, n)
        if self.cross is None:
            return ramp
        at = np.column_stack([np.ravel(e), np.ravel(n)]) if beside is None else np.asarray(beside, float)[:, :2]
        across = (at[:, 1] - self.a[1]) * self.along[0] - (at[:, 0] - self.a[0]) * self.along[1]
        return np.maximum(ramp, (self.base(across) - 0.03).reshape(np.shape(ramp)))

    def mesh(self):
        """Its treads as blocks: `n` risers up from the foot, the top tread level with the head. A flight of
        sleepers is built sleeper by sleeper, and its mesh carries `uv` in copies of the sleeper's picture."""
        if self.material == "sleepers":
            return self._sleepers()
        m = Mesh()
        going, hw = self.length / self.n, self.width / 2
        side = np.array([-self.along[1], self.along[0]])
        for i, top in enumerate(self.tops()):
            p0, p1 = self.a + self.along * going * i, self.a + self.along * going * (i + 1)
            m.extend(prism([p0 - side * hw, p1 - side * hw, p1 + side * hw, p0 + side * hw],
                           self.foot + self.rise / self.n * i - 0.3, top))
        return m

    def _sleepers(self):
        """Each tread as two sleepers side by side across the flight, a riser high: the grain runs across the
        flight on the treads and risers, and the flight's sides show the sleepers' ends, stacked."""
        m, uv = Mesh(), []
        going, riser, tops = self.length / self.n, self.riser, self.tops()
        side = np.array([-self.along[1], self.along[0]])
        hw, low = self.width / 2, self.foot - 0.3
        tu = STEP_LOOKS["sleepers"][1][1][0]

        def at(along, across, z):
            return [*(self.a + self.along * along + side * across), z]

        def quad(corners, uvs):                 # counter-clockwise seen from outside
            m.add(corners, [(0, 1, 2), (0, 2, 3)])
            uv.extend(uvs)

        for s0, s1 in ((-hw, hw),):
            w0, w = (s0 + hw) / tu, (s1 + hw) / tu
            for i in range(self.n):
                a0, top = going * i, tops[i]
                under = 1 - (top - low) / riser     # v at the foot of the block: 1 at a sleeper's top edge, 0 a riser lower
                for k in range(2):                  # the tread's two sleepers, each with its own stretch of grain
                    b0, b1 = a0 + going * k / 2, a0 + going * (k + 1) / 2
                    u0 = 0.29 * (2 * i + k)
                    quad([at(b0, s0, top), at(b1, s0, top), at(b1, s1, top), at(b0, s1, top)],
                         [(u0 + w0, 1), (u0 + w0, 0), (u0 + w, 0), (u0 + w, 1)])
                    # and its ends at the two sides of the flight: a short piece of grain, so each has its own tone
                    quad([at(b0, s0, low), at(b1, s0, low), at(b1, s0, top), at(b0, s0, top)],
                         [(u0, under), (u0 + 0.03, under), (u0 + 0.03, 1), (u0, 1)])
                    quad([at(b1, s1, low), at(b0, s1, low), at(b0, s1, top), at(b1, s1, top)],
                         [(u0 + 0.5, under), (u0 + 0.53, under), (u0 + 0.53, 1), (u0 + 0.5, 1)])
                u0 = 0.29 * 2 * i + 0.13            # the riser: the front sleeper's side
                quad([at(a0, s1, low), at(a0, s0, low), at(a0, s0, top), at(a0, s1, top)],
                     [(u0 + w, under), (u0 + w0, under), (u0 + w0, 1), (u0 + w, 1)])
            under = 1 - (self.head - low) / riser
            quad([at(self.length, s0, low), at(self.length, s1, low), at(self.length, s1, self.head),
                  at(self.length, s0, self.head)], [(w0, under), (w, under), (w, 1), (w0, 1)])
        m.uv = np.asarray(uv, float)
        return m


class Design:
    """The designed ground of one option, and its walls and steps with their levels: on the ground as it is
    (`existing`, anything with sample(e, n)), regraded only inside `garden` (the regrade area), with the options'
    `defaults` and drawn in their `frame`.

    Each of `existing_walls` (domus.walls dicts) the option takes away, not in its `keep_existing`, goes, face and
    all. Its footprint (wall_body's plan, from its face's foot with any batter to its back) joins `garden` as far as
    `limits` lets it (a site's lot within its [regrade] limits, less its house; the whole footprint where it's None),
    and there the option starts from the soil under the wall, at its toe, not from its old face (base): its terraces
    and batters reach across it as anywhere else, and where none of them reaches, the ground is left at the toe."""

    def __init__(self, opt, defaults, existing, garden, frame, existing_walls=(), limits=None):
        self.defaults = defaults
        self.existing = existing
        self.frame = frame
        self.terraces = [Terrace(t, self.defaults, frame) for t in opt.get("terraces", [])]
        self.walls = [option_wall(w, self.defaults, frame) for w in opt.get("walls", [])]
        self.keep_existing = list(opt.get("keep_existing", []))
        self.gone = []      # each wall the option takes away, as wall_body gives it, its plan within `limits`
        for w in existing_walls:
            if w["id"] in self.keep_existing:
                continue
            plan, line, chain, toe = wall_body(w, existing)
            if limits is not None:
                plan = _polygons(plan.intersection(limits))
            if plan.area > 0:
                self.gone.append((plan, line, chain, toe))
        self.garden = unary_union([garden, *(g[0] for g in self.gone)]) if self.gone else garden
        self.stairs = [Stair(s, frame) for s in opt.get("stairs", [])]
        self._garden_prepared = False
        # Terrace edges along a wall or a flight of steps are held by it; the rest are free and get a batter.
        self.free = [t.poly.exterior for t in self.terraces]
        holds = [LineString(w["line"]).buffer(w["thickness"] + HELD_M) for w in self.walls]
        holds += [st.poly.buffer(HELD_M) for st in self.stairs]
        if holds:
            held = unary_union(holds)
            self.free = [edge.difference(held) for edge in self.free]
        for st in self.stairs:      # each flight's levels come from the terraces it joins
            st.settle(self._terraced)

    # --- the designed ground ---------------------------------------------------------------
    def sample(self, e, n, drawn=False):
        z = self._terraced(e, n)
        if self.stairs:
            shape = z.shape
            ef, nf, z = np.ravel(np.asarray(e, float)), np.ravel(np.asarray(n, float)), z.ravel()
            garden = shapely.contains_xy(self.garden, ef, nf) & np.isfinite(z)
            for st in self.stairs:
                m = garden & shapely.contains_xy(st.poly, ef, nf)
                z[m] = st.under(ef[m], nf[m]) if drawn else st.z(ef[m], nf[m])
            z = z.reshape(shape)
        return z

    def drawn(self, e, n):
        """The designed ground as it is drawn: under a flight of steps, the ramp just under its treads. On a foot
        that slopes across the flight that is lower than the ground the flight stands on (`sample`), which would
        come through the treads carried out over it."""
        return self.sample(e, n, drawn=True)

    def base(self, e, n):
        """The ground the option starts from at points (e, n): the ground as it is, but where a wall it takes away
        stood (Design.gone), the soil under that wall, at its toe, the lowest where two meet."""
        e = np.asarray(e, float)
        n = np.asarray(n, float)
        h0 = np.asarray(self.existing.sample(e, n), float)
        return _on_toes(self.gone, e, n, h0) if self.gone else h0

    def _terraced(self, e, n):
        """The ground with the terraces and their batters, before the steps, on the ground it starts from (base)."""
        e = np.asarray(e, float)
        n = np.asarray(n, float)
        shape = np.shape(e)
        e = e.ravel()
        n = n.ravel()
        h0 = self.base(e, n)
        z = h0.copy()
        if not self.terraces:
            return z.reshape(shape)
        if not self._garden_prepared:
            shapely.prepare(self.garden)
            for t in self.terraces:
                shapely.prepare(t.poly)
            self._garden_prepared = True
        garden = shapely.contains_xy(self.garden, e, n) & np.isfinite(h0)
        inside_any = np.zeros(len(e), bool)
        for t in self.terraces:
            m = garden & shapely.contains_xy(t.poly, e, n) & ~inside_any
            z[m] = t.z(e[m], n[m])
            inside_any |= m
        # Batters from the free edges, out to where they meet the existing ground.
        cand = garden & ~inside_any
        if cand.any():
            raise_to, lower_to = self.batters(e[cand], n[cand])
            up = np.maximum(raise_to - h0[cand], 0)
            down = np.maximum(h0[cand] - lower_to, 0)
            z[cand] = np.where(up >= down, h0[cand] + up, h0[cand] - down)
        return z.reshape(shape)

    def batters(self, e, n):
        """What the terraces' batters do at points (e, n) outside the terraces: the level they raise the ground to and
        the level they cut it to, -inf and inf where none reaches. Each terrace's free edge raises a point to the
        terrace's level at the edge's nearest point less the distance over the batter, and cuts it to that level plus
        the distance, out to BATTER_REACH_M; the highest raise and the lowest cut are taken."""
        e = np.ravel(np.asarray(e, float))
        n = np.ravel(np.asarray(n, float))
        raise_to = np.full(len(e), -np.inf)
        lower_to = np.full(len(e), np.inf)
        pts = shapely.points(e, n)
        for t, free in zip(self.terraces, self.free, strict=True):
            if free.is_empty:
                continue
            d = shapely.distance(pts, free)
            near = d < BATTER_REACH_M
            if not near.any():
                continue
            foot = shapely.get_coordinates(shapely.shortest_line(pts[near], free))[1::2]
            ze = t.z(foot[:, 0], foot[:, 1])
            raise_to[near] = np.maximum(raise_to[near], ze - d[near] / t.batter)
            lower_to[near] = np.minimum(lower_to[near], ze + d[near] / t.batter)
        return raise_to, lower_to

    def regraded(self, e, n, z=None, h0=None):
        """Where the option regrades the ground, at points (e, n): in its terraces and under its flights of steps,
        inside the regrade area, where a batter reaches, and where a wall it takes away stood (base). Everywhere else it
        leaves the ground as it is. `z` and `h0` are the designed ground and the ground as it is at those points, when
        they're known already."""
        e = np.asarray(e, float)
        n = np.asarray(n, float)
        z = self.sample(e, n) if z is None else z
        h0 = self.existing.sample(e, n) if h0 is None else h0
        moved = np.asarray(z != h0)             # a batter, or anything else that moves the ground
        garden = shapely.contains_xy(self.garden, e, n)
        for shape in [t.poly for t in self.terraces] + [st.poly for st in self.stairs]:
            moved |= garden & shapely.contains_xy(shape, e, n)
        return moved

    def paving(self, e, n):
        """How deep the paving is at points (e, n): a paved terrace's (`kind` paving) where its level is the designed
        ground, and none elsewhere, nor under a flight of steps."""
        e = np.asarray(e, float)
        n = np.asarray(n, float)
        shape = np.shape(e)
        e, n = e.ravel(), n.ravel()
        out = np.zeros(len(e))
        if any(t.paving for t in self.terraces):
            garden = shapely.contains_xy(self.garden, e, n)
            taken = np.zeros(len(e), bool)
            for t in self.terraces:         # the first terrace a point is in gives its level, as in _terraced
                m = garden & shapely.contains_xy(t.poly, e, n) & ~taken
                out[m] = t.paving
                taken |= m
            for st in self.stairs:
                out[shapely.contains_xy(st.poly, e, n)] = 0.0
        return out.reshape(shape)

    # --- walls -------------------------------------------------------------------------------
    def wall_levels(self, spacing=WALL_STEP_M):
        """Each wall with `side`, `top`/`toe` (and their chains) from the designed ground: the top
        from the ground just behind it plus its upstand, the toe from the ground 0.35 m in front."""
        out = []
        for w in self.walls:
            w = dict(w)
            pts, chain = resample_line(w["line"], spacing)
            nrm = line_normals(pts)
            if w["side"] is None:
                left = self.sample(*(pts[:, :2] + nrm * 0.8).T)
                right = self.sample(*(pts[:, :2] - nrm * 0.8).T)
                w["side"] = 1 if np.nanmean(left - right) > 0 else -1
            nrm = nrm * w["side"]
            back = pts[:, :2] + nrm * (w["depth"] + BACK_PROBE_M)
            front = pts[:, :2] - nrm * TOE_PROBE_M
            retained = self.sample(back[:, 0], back[:, 1])
            toe = self.sample(front[:, 0], front[:, 1])
            for st in self.stairs:      # beside a flight on a sloping foot: the ground under it at the wall's own face
                beside = shapely.contains_xy(st.poly, front[:, 0], front[:, 1]) if st.cross else np.zeros(len(front), bool)
                if beside.any():
                    toe[beside] = st.z(front[beside, 0], front[beside, 1], beside=pts[beside, :2])
            if w["bay_tops"] is not None:
                top = np.max(bay_levels(w["line"], w["bay_tops"], chain), axis=0)   # the higher bay at a step
            elif w["fixed_top"] is not None:
                ft = np.asarray(w["fixed_top"], float)
                if ft.ndim:
                    full = np.concatenate([[0], np.cumsum(np.hypot(*np.diff(w["line"], axis=0).T))])
                    top = np.interp(chain, full, ft)
                else:
                    top = np.full(len(chain), float(ft))
            elif w["height"] is not None:
                top = toe + float(w["height"])
            else:
                top = retained + w["upstand"]
            top = np.maximum(top, toe + 0.05)
            # its first course below the ground at its toe: at least `least`, `per_m` for each metre of its height
            # there, and `courses` whole courses of a wall of named blocks
            least, per_m, courses = w["embed"]
            course = (walls_mod.units(w) or (0.0, 0.0))[1]
            sunk = np.maximum(np.maximum(least, per_m * np.maximum(top - toe, 0.0)), courses * course)
            w.update(top=top, top_chain=chain, toe=toe, toe_chain=chain, retained=retained,
                     samples=pts[:, :2], normals=nrm, embedment=sunk)
            out.append(w)
        return out


# --- comparing options ------------------------------------------------------------------------

def _polygons(geom):
    """The polygons in `geom`, without the lines or points where an intersection only touches."""
    parts = [p for p in shapely.get_parts(geom) if p.geom_type in ("Polygon", "MultiPolygon") and p.area > 0]
    return unary_union(parts) if parts else Polygon()


def grid_in(poly, step):
    """The points of a `step` grid inside `poly` (E, N), none in an empty one (a site with no house)."""
    if poly.is_empty:
        return np.empty(0), np.empty(0)
    e0, n0, e1, n1 = poly.bounds
    E, N = np.meshgrid(np.arange(e0 + step / 2, e1, step), np.arange(n0 + step / 2, n1, step))
    m = shapely.contains_xy(poly, E, N)
    return E[m], N[m]


def wall_edges(w, ground=None):
    """Where a wall stands in the ground, and the level the site's soil stops at under it: (face, back, chain, level),
    the level at each distance `chain` along its line. A new wall's (levelled by Design.wall_levels) is its trench: its
    face is its line and its back the trench's `trench` width behind it on its retained `side`, which what's built
    there fills from the trench's bottom: its base and bedding, the wall's own courses from their embedment below its
    toe, and any drainage aggregate behind them. None of it is soil, so the soil stops at the trench's bottom, its toe
    less its embedment, bedding and base (trench_bottom). A wall that doesn't say how it's built stands on its toe, in
    a trench as deep as its body. An existing wall's (a domus.walls dict) is its body, where the build draws it into
    `ground` (domus.tin.build): its face at the foot, half any batter in front of its line, a point for each of its
    samples, and the soil stops at its toe, `ground`'s there."""
    line = np.asarray(w["line"], float)[:, :2]
    if ground is None:
        return line, offset_behind(line, w["trench"], w["side"]), w["toe_chain"], trench_bottom(w)
    pts, nrm, chain, _, _ = walls_mod.wall_levels(w, ground)
    half = 0.5 * w.get("batter", 0.0)
    face = pts - nrm * half
    back = pts + nrm * half + offset_behind(pts, w["thickness"], w["side"]) - pts
    return face, back, chain, ground.sample(face[:, 0], face[:, 1])


def trench_bottom(w):
    """A new wall's trench's bottom at each of its toe's samples (levelled by Design.wall_levels): where its first
    course stands (walls.foot), less its bedding and base course."""
    return walls_mod.foot(w, w["toe_chain"], w["toe"], clear=0.0) - w["bedding"] - w["base"]


def wall_body(w, ground=None):
    """A wall's plan in the ground, from its face to its back, and the soil's level under it along it: (plan, line,
    chain, level), from wall_edges. The plan reaches a micrometre past its outline, so a point on the outline is the
    wall's on any grid, however its sums round."""
    face, back, chain, level = wall_edges(w, ground)
    return Polygon(np.vstack([face, back[::-1]])).buffer(1e-6), LineString(np.asarray(w["line"], float)[:, :2]), chain, level


def body_plan(w):
    """Where a new wall's body stands on the plan (levelled by Design.wall_levels): from its face back its `depth`."""
    line = np.asarray(w["line"], float)[:, :2]
    return Polygon(np.vstack([line, offset_behind(line, w["depth"], w["side"])[::-1]])).buffer(1e-6)


def drainage(w):
    """The drainage aggregate behind a new wall (levelled by Design.wall_levels), where its trench reaches past its body
    and its option gives soil over the aggregate (`seal`): (plan, line, chain, level, seal), the plan from its body's
    back to its trench's, the trench's bottom along it, and that soil's depth. None where there's no such soil."""
    if w["trench"] <= w["depth"] + 1e-9 or w["seal"] <= 0:
        return None
    line = np.asarray(w["line"], float)[:, :2]
    back, deep = offset_behind(line, w["depth"], w["side"]), offset_behind(line, w["trench"], w["side"])
    return Polygon(np.vstack([back, deep[::-1]])).buffer(1e-6), LineString(line), w["toe_chain"], trench_bottom(w), w["seal"]


def _on_toes(bodies, e, n, z):
    """`z` at points (e, n), but a wall's toe inside the wall (`bodies`, wall_body's): the lowest where two meet."""
    under = np.full(np.shape(e), np.inf)
    for plan, line, chain, toe in bodies:
        inside = shapely.contains_xy(plan, e, n)
        if inside.any():
            at = shapely.line_locate_point(line, shapely.points(e[inside], n[inside]))
            under[inside] = np.minimum(under[inside], np.interp(at, chain, toe))
    return np.where(np.isfinite(under), under, z)


def soil_moved(design, walls, existing_walls, e, n):
    """How much soil an option (`design`) digs out and brings in at points (e, n): (cut, fill), each a depth of soil.
    Cut and fill are soil.

    Before, the soil is the ground as it is (`design.existing`), but inside an existing wall (`existing_walls`, from
    its face to its back) it's at the wall's toe: the wall itself isn't soil. After, it's the designed ground where the
    option regrades, less the paving where a terrace is paved, and as it was everywhere else; in a new wall's trench
    (`walls`, levelled by Design.wall_levels: its body, unless it says how it's built) it's at the trench's bottom, and
    inside an existing wall the option keeps, at that wall's toe. So the soil a new wall holds back counts, and what's
    built of it, the wall from its foot to its top and its base, bedding and drainage aggregate, doesn't (wall_body).
    Where a wall's option gives soil over its drainage aggregate (`seal_m`), that's soil brought back in over what was
    dug out: fill, under the finished ground behind the wall's body, where no wall stands."""
    e = np.asarray(e, float)
    n = np.asarray(n, float)
    h0, z = design.existing.sample(e, n), design.sample(e, n)
    old = [wall_body(w, design.existing) for w in existing_walls]
    before = _on_toes(old, e, n, h0)
    surface = np.where(design.regraded(e, n, z, h0), z - design.paving(e, n), before)
    kept = [b for w, b in zip(existing_walls, old, strict=True) if w["id"] in design.keep_existing]
    dz = _on_toes([wall_body(w) for w in walls] + kept, e, n, surface) - before
    cut, fill = np.maximum(-dz, 0.0), np.maximum(dz, 0.0)
    sealed = [d for d in (drainage(w) for w in walls) if d]
    if sealed:
        seal = np.zeros(np.shape(e))
        for plan, line, chain, level, depth in sealed:
            inside = shapely.contains_xy(plan, e, n)
            if inside.any():
                at = shapely.line_locate_point(line, shapely.points(e[inside], n[inside]))
                seal[inside] = np.maximum(seal[inside], np.clip(surface[inside] - np.interp(at, chain, level), 0.0, depth))
        for plan in [body_plan(w) for w in walls] + [b[0] for b in kept]:
            seal[shapely.contains_xy(plan, e, n)] = 0.0
        fill = fill + np.where(np.isfinite(seal), seal, 0.0)
    return cut, fill


def soil_change(design, walls, existing_walls, e, n):
    """How far an option (`design`) moves the soil at points (e, n), up for fill and down for cut: the soil it brings
    in less the soil it digs out there (soil_moved)."""
    cut, fill = soil_moved(design, walls, existing_walls, e, n)
    return fill - cut


def flat_area(sample, poly, step=0.25, slope=0.05, exclude=None):
    """Area of `poly` whose ground (from `sample`) is no steeper than `slope`, measured over 1 m."""
    e, n = grid_in(poly, step)
    if exclude is not None and not exclude.is_empty:
        keep = ~shapely.contains_xy(exclude, e, n)
        e, n = e[keep], n[keep]
    h = 0.5
    ge = (sample(e + h, n) - sample(e - h, n)) / (2 * h)
    gn = (sample(e, n + h) - sample(e, n - h)) / (2 * h)
    ok = np.hypot(ge, gn) <= slope
    return float(ok.sum() * step * step), e[ok], n[ok]


def retained(w):
    """Retained height at each of the wall's samples: the ground it holds above its toe."""
    if w["fixed_top"] is None and w["height"] is None and w.get("bay_tops") is None:
        return w["retained"] - w["toe"]
    return w["top"] - w["toe"]


def wall_checks(w, points=()):
    """The most a wall retains, and what its footing comes close to. `points` are (name, E, N) of things a footing must
    not hit (posts, poles, lids): within the wall's body, `depth` deep (its `thickness` unless it says), and 0.3 m."""
    flags = []
    hmax = float(np.nanmax(np.maximum(retained(w), 0.0)))
    line = LineString(w["line"])
    for name, pe, pn in points:
        dd = line.distance(shapely.Point(pe, pn))
        if dd < w["depth"] + 0.3:
            flags.append(f"its footing comes within {dd:.1f} m of the {name}")
    return hmax, flags


def drain_checks(design, services, depths_by_id, rules):
    """Cover over the drains after the cuts, and walls crossing drains. A drain's levels are worked out as for the
    pipes the viewer draws (domus.drains), or its assumed depth taken; a drain whose levels are known to be wrong
    (`follow_up`) says so in every check of it.
    """
    from ..model import drains

    out = []
    points = drains.level_points(services)
    for f in services:
        sid = f["properties"]["id"]
        if f["geometry"]["type"] != "LineString":
            continue
        found = drains.levels(f["geometry"]["coordinates"], points.get(sid, []), design.existing.sample,
                              depths_by_id.get(sid), spacing=0.25)
        if found is None:
            continue
        pts, chain, invert, known = found
        line = LineString(np.asarray(f["geometry"]["coordinates"])[:, :2])
        h0 = design.existing.sample(pts[:, 0], pts[:, 1])
        assumed = not known
        follow_up = f["properties"].get("follow_up")
        depth = h0 - invert
        h1 = design.sample(pts[:, 0], pts[:, 1])
        dz = h1 - h0
        cover = depth - 0.11 + dz   # top of a 110 mm pipe
        name = f["properties"]["name"]
        changed = np.abs(dz) > 0.05
        if changed.any():
            k = int(np.argmin(np.where(changed, cover, np.inf)))
            msg = {"drain": name, "assumed_depth": assumed,
                   "cut_max_m": round(float(max(0, -dz.min())), 2), "fill_max_m": round(float(max(0, dz.max())), 2),
                   "least_cover_m": round(float(cover[k]), 2)}
            if cover[k] < rules["drain_cover_m"]:
                msg["flag"] = (f"cover drops to {cover[k]:.2f} m (under {rules['drain_cover_m']} m)"
                               + (" at its assumed depth" if assumed else ""))
            if follow_up:
                msg["follow_up"] = follow_up
                msg["flag"] = "; ".join([*([msg["flag"]] if "flag" in msg else []), f"its levels are to be followed up: {follow_up}"])
            out.append(msg)
        for w in design.walls:
            x = LineString(w["line"]).intersection(line)
            if x.is_empty:
                continue
            for p in getattr(x, "geoms", [x]):
                c = line.project(p)
                d = float(np.interp(c, chain, depth))
                z = float(np.interp(c, chain, invert))
                flag = (f"{w['id']} crosses it (invert {z:.2f}, {d:.2f} m deep{', assumed' if assumed else ''}): "
                        "bridge the footing over the pipe or sleeve it")
                out.append({"drain": name, "wall": w["id"], "assumed_depth": assumed,
                            **({"follow_up": follow_up} if follow_up else {}),
                            "flag": flag + (f"; its levels are to be followed up: {follow_up}" if follow_up else "")})
    return out


def easement_checks(w, easements=()):
    """A wall running into an easement: what it may hold there is in the easement's instrument."""
    line = LineString(w["line"])
    return [f"it runs into {name}{f' ({intent.lower()})' if intent else ''}: check the easement allows a wall there"
            for name, intent, g in easements if line.intersects(g.buffer(w["thickness"] / 2 if g.area else 0.3))]


class Context:
    """What a site gives its wall options to be worked out on: domus.design reads a site's (design.context()).

    `frame` is the options' SiteFrame, `settings` options/_site.toml as read (its defaults and rules), `ground` the
    ground as it is, which the options are designed on and measured from (anything with sample(e, n): a site's is the
    ground as its build draws it), `garden` the regrade area and `regrade` its settings. The rest are for cut and fill
    and the checks: the existing walls (domus.walls dicts), the house's outline, the building itself and any walls retaining
    the ground against it (shapely geometry, empty or None where there are none), the point features a footing must
    miss ((name, E, N)), the easements ((name, intent, shape)), the services (GeoJSON features) with their settings
    ([services] in data/site.toml), and `limits`, as far as the footprint of an existing wall an option takes away
    joins its regrade area (Design)."""

    def __init__(self, frame, settings, ground, garden, regrade, existing_walls=(), house_outline=None,
                 house_building=None, house_retaining_walls=None, obstacles=(), easements=(), services=(),
                 services_settings=None, limits=None):
        self.frame, self.settings, self.ground, self.garden, self.regrade = frame, settings, ground, garden, regrade
        self.limits = limits
        self.existing_walls = list(existing_walls)
        self.house_outline = Polygon() if house_outline is None else house_outline
        self.house_building = Polygon() if house_building is None else house_building
        self.house_retaining_walls = house_retaining_walls
        self.obstacles, self.easements, self.services = tuple(obstacles), tuple(easements), list(services)
        self.services_settings = {} if services_settings is None else services_settings


def evaluate(opt, site, existing=None, step=0.1):
    """Everything the viewer and the comparison table need about one option, worked out on what `site` (a Context)
    gives it, from the ground as it is unless `existing` gives another."""
    d = Design(opt, site.settings["defaults"], existing or site.ground, site.garden, site.frame, site.existing_walls,
               site.limits)
    rules, fg = site.settings.get("rules", {}), site.regrade
    fr = site.frame
    walls = d.wall_levels()
    walls_mod.set_out(walls, site.ground)        # the walls of named blocks, set out together: their courses and their bond
    garden = d.garden
    # Round each new wall, its body `depth` deep (its `thickness` unless it says) and 0.3 m, and each flight of steps
    built = [LineString(w["line"]).buffer(w["depth"] + 0.3) for w in walls] + [st.poly.buffer(0.1) for st in d.stairs]
    wall_zone = unary_union(built) if built else None

    # Cut and fill over the regrade area: soil, from the ground as it is (soil_moved).
    e, n = grid_in(garden, step)
    cut, fill = soil_moved(d, walls, site.existing_walls, e, n)
    ok = np.isfinite(cut) & np.isfinite(fill)
    cut, fill = cut[ok], fill[ok]
    cell = step * step
    disturbed = (cut > 0.02) | (fill > 0.02)
    vol = {"cut_m3": float(cut[cut > 0].sum() * cell), "fill_m3": float(fill[fill > 0].sum() * cell),
           "area_m2": float(disturbed.sum() * cell),
           "cut_max_m": float(cut.max()) if len(cut) else 0.0,
           "fill_max_m": float(fill.max()) if len(fill) else 0.0}
    vol["net_m3"] = vol["fill_m3"] - vol["cut_m3"]
    vol["tolerance_m3"] = vol["area_m2"] * LIDAR_TOLERANCE_M

    # Flat lawn: ground no steeper than 1:20 over 1 m, away from the walls, before and after.
    flat_before, _, _ = flat_area(d.existing.sample, garden, slope=fg.get("flat_slope", 0.05))
    flat_after, fe, fn = flat_area(d.sample, garden, slope=fg.get("flat_slope", 0.05), exclude=wall_zone)
    areas = {}
    terr = []
    for t in d.terraces:
        a = t.poly.intersection(garden).area
        areas[t.kind] = areas.get(t.kind, 0.0) + a
        terr.append({"id": t.id, "name": t.name, "kind": t.kind, "area_m2": round(a, 1),
                     "slope": round(t.slope(), 3)})

    # Steps left where a batter runs into the edge of the regrade area (away from the walls).
    edge = []
    probes = []         # just inside every edge of the garden: its outline, and a house standing clear inside it
    for part in getattr(garden, "geoms", [garden]):
        for ring in (part.exterior, *part.interiors):
            pts, _ = resample_line(np.asarray(ring.coords), 0.25)
            nrm = line_normals(pts) * 0.1
            probes += [pts[:, :2] + nrm, pts[:, :2] - nrm]
    probe = np.vstack(probes)
    inside = shapely.contains_xy(garden, probe[:, 0], probe[:, 1])
    if wall_zone is not None:
        inside &= ~shapely.contains_xy(wall_zone, probe[:, 0], probe[:, 1])
    kept_walls = [w for w in site.existing_walls if w["id"] in d.keep_existing]
    if kept_walls:      # an existing wall that stays still holds the edge it stands on
        kept_zone = unary_union([LineString(w["line"]).buffer(w["thickness"] + 0.3) for w in kept_walls])
        inside &= ~shapely.contains_xy(kept_zone, probe[:, 0], probe[:, 1])
    if inside.any():
        q = probe[inside]
        step = d.sample(q[:, 0], q[:, 1]) - d.existing.sample(q[:, 0], q[:, 1])
        # Where the garden's edge is the house, the house holds what is filled against it, and a cut there is
        # reported further down: only the rest of the edge needs a wall or a batter.
        house = site.house_outline
        at_house = np.zeros(len(q), bool) if house.is_empty else \
            shapely.distance(shapely.points(q[:, 0], q[:, 1]), house) < fg.get("house_margin_m", 0.6) + 0.25
        unheld = ("the regrading meets the edge of the regrade area with a {h:.2f} m step at s {s:.1f}, o {o:.1f}: "
                  "needs a wall or a longer batter there")
        on_house = ("raises the ground {h:.2f} m against the house (s {s:.1f}, o {o:.1f}): "
                    "keep it below the cladding and clear of any vents")
        for where, height, text in ((~at_house, np.abs(step), unheld), (at_house, step, on_house)):
            if where.any() and np.nanmax(height[where]) > 0.1:
                k = int(np.flatnonzero(where)[np.nanargmax(height[where])])
                so = fr.so(q[k])
                edge.append({"step_m": round(float(height[k]), 2), "at_so": [round(float(so[0]), 1), round(float(so[1]), 1)],
                             "flag": text.format(h=height[k], s=so[0], o=so[1])})

    # Walls: heights, area, checks.
    winfo = []
    for w in walls:
        m, info = walls_mod.mesh(w, site.ground)
        hmax, flags = wall_checks(w, site.obstacles)
        flags += easement_checks(w, site.easements)
        flags += w.get("set_out", {}).get("notes", [])
        so = fr.so(w["line"])
        winfo.append({"id": w["id"], "name": w["name"], "material": w["material"],
                      "length_m": round(info["length_m"], 2),
                      "height_min_m": round(info["height_min_m"], 2), "height_max_m": round(info["height_max_m"], 2),
                      "retained_max_m": round(hmax, 2), "face_area_m2": round(info["face_area_m2"], 2),
                      "top_min": round(info["top_min"], 2), "top_max": round(info["top_max"], 2),
                      "toe_min": round(info["toe_min"], 2), "toe_max": round(info["toe_max"], 2),
                      "line_so": [[round(float(a), 2), round(float(b), 2)] for a, b in so],
                      "flags": flags, "mesh": m, "def": w})

    # Steps: how many risers, and how steep.
    stairs = []
    for st in d.stairs:
        riser, going = st.riser, st.length / st.n
        flags = []
        if riser > STAIR_STEEP[0] or going < STAIR_STEEP[1]:
            flags.append(f"risers of {riser:.2f} m on goings of {going:.2f} m are steep: lengthen the flight or add a riser")
        so = fr.so(np.array([st.a, st.b]))
        stairs.append({"id": st.id, "name": st.name, "material": st.material, "risers": st.n, "riser_m": round(riser, 3),
                       "going_m": round(going, 3), "rise_m": round(st.rise, 2), "width_m": round(st.width, 2),
                       "length_m": round(st.length, 2), "foot": round(st.foot, 2), "head": round(st.head, 2),
                       "line_so": [[round(float(a), 2), round(float(b), 2)] for a, b in so], "flags": flags,
                       "mesh": st.mesh()})
        if st.cross:        # on ground that slopes across its foot: how far the ground rises along the lowest tread
            stairs[-1]["foot_rise_m"] = round(abs(st.cross[0] - st.cross[1]), 2)

    # Ground lowered next to the house exposes its foundations.
    near_house = site.house_building.buffer(1.0).difference(site.house_building)
    he, hn = grid_in(near_house.intersection(garden.buffer(0.7)), 0.1)
    if len(he):
        hdz = d.sample(he, hn) - d.existing.sample(he, hn)
        if np.nanmin(hdz) < -0.15:
            k = int(np.nanargmin(hdz))
            so = fr.so(np.array([he[k], hn[k]]))
            edge.append({"step_m": round(float(-hdz[k]), 2), "at_so": [round(float(so[0]), 1), round(float(so[1]), 1)],
                         "flag": f"lowers the ground {-hdz[k]:.2f} m within 1 m of the house (s {so[0]:.1f}, o {so[1]:.1f}): "
                                 "check the foundation depth, or keep the ground there and hold the lawn with a low wall"})

    # Walls retaining the ground against the house, if the site has any (no footings drawn), carry the ground beside them.
    lower = site.house_retaining_walls
    if lower is not None:
        band = lower.buffer(1.5).difference(site.house_building).intersection(garden.buffer(0.7))
        le, ln = grid_in(band, 0.1)
        if len(le):
            ldz = d.sample(le, ln) - d.existing.sample(le, ln)
            for k, what in ((int(np.nanargmax(ldz)), "raises"), (int(np.nanargmin(ldz)), "lowers")):
                if abs(ldz[k]) > 0.1:
                    so = fr.so(np.array([le[k], ln[k]]))
                    edge.append({"step_m": round(float(abs(ldz[k])), 2),
                                 "at_so": [round(float(so[0]), 1), round(float(so[1]), 1)],
                                 "flag": f"{what} the ground {abs(ldz[k]):.2f} m within 1.5 m of the lower floor's "
                                         f"retaining walls (s {so[0]:.1f}, o {so[1]:.1f}): an engineer should check them"})
        for w in winfo:
            dd = LineString(w["def"]["line"]).distance(lower)
            if dd < 1.5:
                w["flags"].append(f"its footing comes within {dd:.1f} m of the lower floor's retaining walls")

    svc = site.services
    svc_cfg = site.services_settings
    drains = drain_checks(d, svc, svc_cfg.get("assumed_depth_m", {}), rules)
    # Inspection point lids, if the ground moves under them.
    for f in svc:
        if f["properties"]["id"] in svc_cfg.get("lids", []):
            pe, pn = f["geometry"]["coordinates"][:2]
            dzi = float(d.sample([pe], [pn])[0] - d.existing.sample(np.array([pe]), np.array([pn]))[0])
            if abs(dzi) > 0.05:
                drains.append({"drain": f["properties"]["name"],
                               "flag": f"the ground moves {dzi:+.2f} m at this inspection point: reset its riser or lid"})

    kept = set(d.keep_existing)
    removed_m = sum(float(np.hypot(*np.diff(w["line"], axis=0).T).sum()) for w in site.existing_walls if w["id"] not in kept)
    volumes = {k: round(v, 1 if k.endswith(("m3", "m2")) else 2) for k, v in vol.items()}
    return {
        "design": d,
        "walls": winfo,
        "stairs": stairs,
        "terraces": terr,
        "volumes": volumes,
        "materials": materials.listed(winfo, d, volumes, site.settings),
        "flat": {"before_m2": round(flat_before, 1), "after_m2": round(flat_after, 1),
                 "gain_m2": round(flat_after - flat_before, 1), "points": (fe, fn)},
        "areas": {k: round(v, 1) for k, v in areas.items()},
        "edge": edge,
        "drains": drains,
        "removed_existing_m": round(removed_m, 1),
        "keep_existing": sorted(kept),
    }
