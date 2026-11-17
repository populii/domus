"""A wall option as designed ground: terraces, the walls that hold them, the batters between them
and the ground left alone, and what it all comes to (cut and fill, flat lawn, wall heights, cover
over the drains).

Options are drawn in the site frame of options/_site.toml: s along a reference boundary
from its first corner, o in from it, into the lot. A terrace is a polygon with
a level: a plane (`level` at `at`, falling `fall` towards `fall_to`) or, with three numbers per
vertex, its own level at each corner. A wall is a line; by default its top is the designed ground
behind it plus `upstand_m` and its toe the designed ground in front of it, so moving a terrace's
level moves its walls with it.

The designed ground is the existing ground (terrain.ground()) with each terrace's level inside its
polygon. Terrace edges not held by a wall slope back to the existing ground at the terrace's
batter, inside the regrade area only: whatever a batter cannot reach there is reported as a step
at the edge of that area.

A flight of steps is a line from its foot to its head and a width. It takes its levels from the
designed ground just beyond each end, so it follows the terraces it joins, and the ground under it
is a ramp between them.
"""

import tomllib
from functools import lru_cache

import numpy as np
import shapely
from scipy.interpolate import LinearNDInterpolator, NearestNDInterpolator
from shapely.geometry import LineString, Polygon
from shapely.ops import unary_union

from . import crs, geojson, terrain
from .crs import ROOT
from .mesh import Mesh, prism

OPTIONS = ROOT / "options"
SITE = OPTIONS / "_site.toml"
LIDAR_TOLERANCE_M = 0.15     # height uncertainty of the existing ground, for the range on cut and fill


@lru_cache
def settings():
    return tomllib.loads(SITE.read_text(encoding="utf-8"))


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


@lru_cache
def frame():
    f = settings()["frame"]
    return SiteFrame(f["origin"], f["toward"], f.get("side", "right"))


@lru_cache
def title():
    feats = geojson.read(ROOT / "data" / "site" / "boundary.geojson")["features"]
    main = crs.settings().get("boundary", {}).get("main", feats[0]["properties"]["id"])
    return Polygon(next(f for f in feats if f["properties"]["id"] == main)["geometry"]["coordinates"][0])


@lru_cache
def house_outline():
    """What the house takes of the ground: its walls and every part drawn as an area (porches, blocks such as
    decks and landings) in data/site/house.geojson. A bay window hangs from the wall above the ground
    (domus.house draws it from `bottom_above_fl` up), so the ground runs on under it and it is left out."""
    feats = geojson.read_optional(ROOT / "data" / "site" / "house.geojson")
    polys = [Polygon(np.asarray(f["geometry"]["coordinates"][0])[:, :2]) for f in feats
             if f["geometry"]["type"] == "Polygon" and f["properties"].get("part") != "bay"]
    return unary_union([p.buffer(0) for p in polys])


@lru_cache
def house_retaining_walls():
    """Walls that retain the ground against the house, such as round a lower floor dug into a slope
    (part = "retaining-walls" in data/site/house.geojson), or None."""
    from shapely.geometry import MultiLineString

    lines = []
    for f in geojson.read_optional(ROOT / "data" / "site" / "house.geojson"):
        if f["properties"].get("part") == "retaining-walls":
            g = f["geometry"]
            lines += [[c[:2] for c in part] for part in (g["coordinates"] if g["type"] == "MultiLineString" else [g["coordinates"]])]
    return MultiLineString(lines) if lines else None


def house_building():
    """The house itself (its walls and bay windows), for the surcharge check."""
    feats = geojson.read_optional(ROOT / "data" / "site" / "house.geojson")
    return unary_union([Polygon(np.asarray(f["geometry"]["coordinates"][0])[:, :2]) for f in feats
                        if f["properties"].get("id") == "house-walls" or f["properties"].get("part") == "bay"])


def regrade_settings():
    """options/_site.toml [regrade] (or [front_garden], its old name): where options may regrade the ground."""
    cfg = settings()
    if "regrade" in cfg:
        return cfg["regrade"]
    if "front_garden" in cfg:
        return cfg["front_garden"]
    raise SystemExit(f"{SITE} has no [regrade] section, which says where options may regrade the ground. See docs/options.md.")


@lru_cache
def regrade_area():
    """Where options regrade and where flat lawn is counted: the lot, or the part of it in [regrade] polygon_so
    (a polygon in the frame) or with s under s_max, less the house and a margin round it."""
    cfg = regrade_settings()
    fr = frame()
    area = title()
    if "polygon_so" in cfg:
        area = area.intersection(Polygon(fr.en(np.asarray(cfg["polygon_so"], float))))
    if "s_max" in cfg:
        s_min = -1000.0
        area = area.intersection(Polygon(fr.en(np.array([[s_min, s_min], [cfg["s_max"], s_min], [cfg["s_max"], -s_min], [s_min, -s_min]]))))
    return area.difference(house_outline().buffer(cfg.get("house_margin_m", 0.6)))


def _points(cfg, key_en, key_so):
    """A point list given in site coordinates (`key_en`) or in the frame (`key_so`)."""
    if key_so in cfg:
        return frame().en(np.asarray(cfg[key_so], float))
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


# Directions in the frame, as a lawn's fall names them: "north" back along the reference boundary,
# "south" along it, "east" towards it, "west" in from it. A site picks its boundary so these read
# close to the compass.
FALL_DIRS = {"north": (-1, 0), "south": (1, 0), "east": (0, -1), "west": (0, 1)}


class Terrace:
    def __init__(self, cfg, defaults):
        self.id = cfg["id"]
        self.name = cfg.get("name", self.id)
        self.kind = cfg.get("kind", "lawn")       # lawn | planting | paving
        self.batter = float(cfg.get("batter", defaults["batter"]))
        pts = _points(cfg, "polygon", "polygon_so")
        self.poly = Polygon(pts[:, :2]).buffer(0)
        if pts.shape[1] == 3:
            # its own level at each corner, linear between them
            self._lin = LinearNDInterpolator(pts[:, :2], pts[:, 2])
            self._near = NearestNDInterpolator(pts[:, :2], pts[:, 2])
            self.plane = None
        else:
            at = frame().en(np.asarray(cfg["at_so"], float)) if "at_so" in cfg else \
                np.asarray(cfg.get("at", self.poly.representative_point().coords[0]), float)
            g = _fall(cfg)
            ds, do = FALL_DIRS[cfg.get("fall_to", "north")]
            fr = frame()
            down = ds * fr.along + do * fr.inward   # the direction the terrace falls towards
            self.plane = (float(cfg["level"]), at[:2], g * down)
        self.properties = cfg

    def z(self, e, n):
        e = np.asarray(e, float)
        n = np.asarray(n, float)
        if self.plane is not None:
            level, at, grad = self.plane
            return level - ((e - at[0]) * grad[0] + (n - at[1]) * grad[1])
        z = self._lin(e, n)
        bad = ~np.isfinite(z)
        if bad.any():
            z[bad] = self._near(e[bad], n[bad])
        return z

    def slope(self):
        if self.plane is not None:
            return float(np.hypot(*self.plane[2]))
        b = np.asarray(self.poly.exterior.coords)[:-1]
        zz = self.z(b[:, 0], b[:, 1])
        a = np.column_stack([b, np.ones(len(b))])
        coef = np.linalg.lstsq(a, zz, rcond=None)[0]
        return float(np.hypot(coef[0], coef[1]))


def option_wall(cfg, defaults):
    """Wall dict (domus.walls style) for an option's wall; levels come later (Design.wall_levels)."""
    line = _points(cfg, "line", "line_so")
    side = cfg.get("retained_side")
    bays = cfg.get("tops")
    if bays is not None and len(bays) != len(line) - 1:
        raise ValueError(f"wall {cfg['id']}: `tops` gives a level for each stretch of its line, so it needs "
                         f"{len(line) - 1} of them, not {len(bays)}")
    return {
        "id": cfg["id"],
        "name": cfg.get("name", cfg["id"]),
        "material": cfg.get("material", "block"),
        "status": "proposed",
        "confidence": "",
        "thickness": float(cfg.get("thickness_m", defaults["thickness_m"])),
        "side": None if side is None else (1 if side == "left" else -1),
        "line": line[:, :2],
        "fixed_top": line[:, 2] if line.shape[1] == 3 else (float(cfg["top"]) if "top" in cfg else None),
        # a level top for each stretch of the line: the wall steps at the points between them
        "bay_tops": None if bays is None else np.asarray(bays, float),
        "height": cfg.get("height_m"),
        "upstand": float(cfg.get("upstand_m", defaults["upstand_m"])),
        "properties": cfg,
    }


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

    def __init__(self, cfg):
        self.id = cfg["id"]
        self.name = cfg.get("name", self.id)
        self.material = cfg.get("material", "concrete")
        self.width = float(cfg.get("width_m", 1.0))
        line = _points(cfg, "line", "line_so")[:, :2]
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
    """The designed ground of one option, and its walls and steps with their levels."""

    def __init__(self, opt, existing=None):
        cfg = settings()
        self.defaults = cfg["defaults"]
        self.existing = existing or terrain.ground()
        self.garden = regrade_area()
        self.terraces = [Terrace(t, self.defaults) for t in opt.get("terraces", [])]
        self.walls = [option_wall(w, self.defaults) for w in opt.get("walls", [])]
        self.keep_existing = list(opt.get("keep_existing", []))
        self.stairs = [Stair(s) for s in opt.get("stairs", [])]
        self._garden_prepared = False
        # Terrace edges along a wall or a flight of steps are held by it; the rest are free and get a batter.
        self.free = [t.poly.exterior for t in self.terraces]
        holds = [LineString(w["line"]).buffer(w["thickness"] + 0.15) for w in self.walls]
        holds += [st.poly.buffer(0.15) for st in self.stairs]
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

    def _terraced(self, e, n):
        """The ground with the terraces and their batters, before the steps."""
        e = np.asarray(e, float)
        n = np.asarray(n, float)
        shape = np.shape(e)
        e = e.ravel()
        n = n.ravel()
        h0 = np.asarray(self.existing.sample(e, n), float)
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
            raise_to = np.full(len(e), -np.inf)
            lower_to = np.full(len(e), np.inf)
            ce, cn = e[cand], n[cand]
            pts = shapely.points(ce, cn)
            for t, free in zip(self.terraces, self.free, strict=True):
                if free.is_empty:
                    continue
                d = shapely.distance(pts, free)
                near = d < 6.0
                if not near.any():
                    continue
                foot = shapely.get_coordinates(shapely.shortest_line(pts[near], free))[1::2]
                ze = t.z(foot[:, 0], foot[:, 1])
                idx = np.flatnonzero(cand)[near]
                raise_to[idx] = np.maximum(raise_to[idx], ze - d[near] / t.batter)
                lower_to[idx] = np.minimum(lower_to[idx], ze + d[near] / t.batter)
            up = np.maximum(raise_to[cand] - h0[cand], 0)
            down = np.maximum(h0[cand] - lower_to[cand], 0)
            z[cand] = np.where(up >= down, h0[cand] + up, h0[cand] - down)
        return z.reshape(shape)

    # --- walls -------------------------------------------------------------------------------
    def wall_levels(self, spacing=0.25):
        """Each wall with `side`, `top`/`toe` (and their chains) from the designed ground: the top
        from the ground just behind it plus its upstand, the toe from the ground 0.35 m in front."""
        out = []
        for w in self.walls:
            w = dict(w)
            pts, chain = terrain.resample_line(w["line"], spacing)
            nrm = terrain.line_normals(pts)
            if w["side"] is None:
                left = self.sample(*(pts[:, :2] + nrm * 0.8).T)
                right = self.sample(*(pts[:, :2] - nrm * 0.8).T)
                w["side"] = 1 if np.nanmean(left - right) > 0 else -1
            nrm = nrm * w["side"]
            back = pts[:, :2] + nrm * (w["thickness"] + 0.1)
            front = pts[:, :2] - nrm * 0.35
            retained = self.sample(back[:, 0], back[:, 1])
            toe = self.sample(front[:, 0], front[:, 1])
            for st in self.stairs:      # beside a flight on a sloping foot: the ground under it at the wall's own face
                beside = shapely.contains_xy(st.poly, front[:, 0], front[:, 1]) if st.cross else np.zeros(len(front), bool)
                if beside.any():
                    toe[beside] = st.z(front[beside, 0], front[beside, 1], beside=pts[beside, :2])
            if w["bay_tops"] is not None:
                top = np.max(terrain.bay_levels(w["line"], w["bay_tops"], chain), axis=0)   # the higher bay at a step
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
            w.update(top=top, top_chain=chain, toe=toe, toe_chain=chain, retained=retained,
                     samples=pts[:, :2], normals=nrm)
            out.append(w)
        return out


# --- comparing options ------------------------------------------------------------------------

def grid_in(poly, step):
    e0, n0, e1, n1 = poly.bounds
    E, N = np.meshgrid(np.arange(e0 + step / 2, e1, step), np.arange(n0 + step / 2, n1, step))
    m = shapely.contains_xy(poly, E, N)
    return E[m], N[m]


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
    """The most a wall retains, and what its footing comes close to. `points` are (name, E, N) of things a footing must not hit (posts, poles, lids)."""
    flags = []
    hmax = float(np.nanmax(np.maximum(retained(w), 0.0)))
    line = LineString(w["line"])
    for name, pe, pn in points:
        dd = line.distance(shapely.Point(pe, pn))
        if dd < w["thickness"] + 0.3:
            flags.append(f"its footing comes within {dd:.1f} m of the {name}")
    return hmax, flags


def drain_checks(design, services, depths_by_id, rules):
    """Cover over the drains after the cuts, and walls crossing drains.

    A drain's invert comes from `invert_m` points naming the drain in `line` and, on the drain
    named by [services] printed_line in data/site.toml, from points that also give the depth they
    were read from; where both cover a point the higher invert (less cover) is used. Drains with
    neither take an assumed depth.
    """
    from shapely.geometry import Point

    out = []
    printed_line = crs.settings().get("services", {}).get("printed_line")
    printed = [(f["geometry"]["coordinates"][:2], float(f["properties"]["invert_m"]))
               for f in services if "depth_m" in f["properties"] and "invert_m" in f["properties"]]
    inverts = {}
    for f in services:
        pr = f["properties"]
        if "invert_m" in pr and pr.get("line") and "depth_m" not in pr:
            inverts.setdefault(pr["line"], []).append((f["geometry"]["coordinates"][:2], float(pr["invert_m"])))
    for f in services:
        sid = f["properties"]["id"]
        if f["geometry"]["type"] != "LineString" or (sid != printed_line and sid not in depths_by_id
                                                     and sid not in inverts):
            continue
        line = LineString(np.asarray(f["geometry"]["coordinates"])[:, :2])
        pts, chain = terrain.resample_line(np.asarray(line.coords), 0.25)
        h0 = design.existing.sample(pts[:, 0], pts[:, 1])
        # Each source spans its own points only; past both ends the nearest known level carries on.
        profiles, carried = [], []
        if sid == printed_line and printed:
            known = sorted((line.project(Point(xy)), z) for xy, z in printed)
            z = np.interp(chain, [c for c, _ in known], [z for _, z in known])
            profiles.append(np.where((chain >= known[0][0]) & (chain <= known[-1][0]), z, np.nan))
            carried.append(z)
        if sid in inverts:
            known = sorted((line.project(Point(xy)), z) for xy, z in inverts[sid])
            z = np.interp(chain, [c for c, _ in known], [z for _, z in known])
            profiles.append(np.where((chain >= known[0][0]) & (chain <= known[-1][0]), z, np.nan))
            carried.append(z)
        assumed = not profiles
        if profiles:
            stack = np.array(profiles)
            covered = ~np.isnan(stack).all(axis=0)
            invert = np.where(covered, np.nanmax(np.where(covered, stack, 0.0), axis=0), carried[-1])
        else:
            invert = h0 - depths_by_id[sid]
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
            out.append(msg)
        for w in design.walls:
            x = LineString(w["line"]).intersection(line)
            if x.is_empty:
                continue
            for p in getattr(x, "geoms", [x]):
                c = line.project(p)
                d = float(np.interp(c, chain, depth))
                z = float(np.interp(c, chain, invert))
                out.append({"drain": name, "wall": w["id"], "assumed_depth": assumed,
                            "flag": f"{w['id']} crosses it (invert {z:.2f}, {d:.2f} m deep{', assumed' if assumed else ''}): "
                                    "bridge the footing over the pipe or sleeve it"})
    return out


def driveway_area():
    """A driveway or other load beside the lot, for the surcharge check: [regrade] driveway_so in options/_site.toml
    (a polygon in the frame), or None."""
    so = regrade_settings().get("driveway_so")
    return Polygon(frame().en(np.array(so, float))) if so else None


def existing_walls():
    from . import walls as walls_mod

    feats = geojson.read_optional(ROOT / "data" / "site" / "walls-existing.geojson")
    return [walls_mod.from_feature(f) for f in feats]


@lru_cache
def obstacles():
    """Point features a wall's footing should miss: poles, posts, survey marks, lids, downpipes."""
    from . import features

    out = []
    for f in features.read_all():
        g = f["geometry"]
        p = f["properties"]
        if g["type"] == "Point":
            out.append((p.get("name", p["id"]).split(" (")[0].lower(), *g["coordinates"][:2]))
    return tuple(out)


@lru_cache
def easement_shapes():
    """(name, intent, shape) of the easements in data/site/title.geojson (domus title)."""
    from . import title

    return tuple((f["properties"]["name"], f["properties"].get("intent"), shapely.geometry.shape(f["geometry"]))
                 for f in title.easements())


def easement_checks(w, easements=()):
    """A wall running into an easement: what it may hold there is in the easement's instrument."""
    line = LineString(w["line"])
    return [f"it runs into {name}{f' ({intent.lower()})' if intent else ''}: check the easement allows a wall there"
            for name, intent, g in easements if line.intersects(g.buffer(w["thickness"] / 2 if g.area else 0.3))]


def evaluate(opt, existing=None, step=0.1):
    """Everything the viewer and the comparison table need about one option."""
    from . import walls as walls_mod

    d = Design(opt, existing)
    cfg = settings()
    rules, fg = cfg.get("rules", {}), regrade_settings()
    fr = frame()
    walls = d.wall_levels()
    walls_mod.set_out(walls)        # the walls of named blocks, set out together: their courses and their bond
    garden = d.garden
    built = [LineString(w["line"]).buffer(w["thickness"] + 0.3) for w in walls] + [st.poly.buffer(0.1) for st in d.stairs]
    wall_zone = unary_union(built) if built else None

    # Cut and fill over the regrade area.
    e, n = grid_in(garden, step)
    h0 = d.existing.sample(e, n)
    dz = d.sample(e, n) - h0
    ok = np.isfinite(dz)
    e, n, dz = e[ok], n[ok], dz[ok]
    cell = step * step
    disturbed = np.abs(dz) > 0.02
    vol = {"cut_m3": float(-dz[dz < 0].sum() * cell), "fill_m3": float(dz[dz > 0].sum() * cell),
           "area_m2": float(disturbed.sum() * cell),
           "cut_max_m": float(max(0.0, -dz.min())) if len(dz) else 0.0,
           "fill_max_m": float(max(0.0, dz.max())) if len(dz) else 0.0}
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
            pts, _ = terrain.resample_line(np.asarray(ring.coords), 0.25)
            nrm = terrain.line_normals(pts) * 0.1
            probes += [pts[:, :2] + nrm, pts[:, :2] - nrm]
    probe = np.vstack(probes)
    inside = shapely.contains_xy(garden, probe[:, 0], probe[:, 1])
    if wall_zone is not None:
        inside &= ~shapely.contains_xy(wall_zone, probe[:, 0], probe[:, 1])
    kept_walls = [w for w in existing_walls() if w["id"] in d.keep_existing]
    if kept_walls:      # an existing wall that stays still holds the edge it stands on
        kept_zone = unary_union([LineString(w["line"]).buffer(w["thickness"] + 0.3) for w in kept_walls])
        inside &= ~shapely.contains_xy(kept_zone, probe[:, 0], probe[:, 1])
    if inside.any():
        q = probe[inside]
        step = d.sample(q[:, 0], q[:, 1]) - d.existing.sample(q[:, 0], q[:, 1])
        # Where the garden's edge is the house, the house holds what is filled against it, and a cut there is
        # reported further down: only the rest of the edge needs a wall or a batter.
        house = house_outline()
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
        m, info = walls_mod.mesh(w)
        hmax, flags = wall_checks(w, obstacles())
        flags += easement_checks(w, easement_shapes())
        flags += w.get("set_out", {}).get("notes", [])
        so = fr.so(w["line"])
        winfo.append({"id": w["id"], "name": w["name"], "material": w["material"],
                      "length_m": round(info["length_m"], 2),
                      "height_min_m": round(info["height_min_m"], 2), "height_max_m": round(info["height_max_m"], 2),
                      "retained_max_m": round(hmax, 2), "face_area_m2": round(info["face_area_m2"], 2),
                      "top_min": round(info["top_min"], 2), "top_max": round(info["top_max"], 2),
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
    near_house = house_building().buffer(1.0).difference(house_building())
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
    lower = house_retaining_walls()
    if lower is not None:
        band = lower.buffer(1.5).difference(house_building()).intersection(garden.buffer(0.7))
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

    svc = geojson.read_optional(ROOT / "data" / "site" / "services.geojson")
    svc_cfg = crs.settings().get("services", {})
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
    removed_m = sum(float(np.hypot(*np.diff(w["line"], axis=0).T).sum()) for w in existing_walls() if w["id"] not in kept)
    return {
        "design": d,
        "walls": winfo,
        "stairs": stairs,
        "terraces": terr,
        "volumes": {k: round(v, 1 if k.endswith(("m3", "m2")) else 2) for k, v in vol.items()},
        "flat": {"before_m2": round(flat_before, 1), "after_m2": round(flat_after, 1),
                 "gain_m2": round(flat_after - flat_before, 1), "points": (fe, fn)},
        "areas": {k: round(v, 1) for k, v in areas.items()},
        "edge": edge,
        "drains": drains,
        "removed_existing_m": round(removed_m, 1),
        "keep_existing": sorted(kept),
    }
