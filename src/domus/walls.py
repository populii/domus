"""Retaining walls as model objects, shared by the existing walls and (step 3) the options.

A wall is a line in site coordinates (its exposed face), the side its retained ground is on,
a thickness, a batter (how far its face leans back), and levels: per-vertex top heights (third
coordinate) and/or an exposed height above the toe. Toe levels default to the ground model just
in front of the face; `toe_m` gives them per vertex instead.
"""

import hashlib
import itertools
import math
from functools import lru_cache
from pathlib import Path

import numpy as np

from .errors import DomusError
from .mesh import Mesh, offset_behind, prism, resample_line, wall_strip

MATERIAL_COLOURS = {
    "concrete": "#a8a49c",
    "stone": "#8f8577",
    "timber": "#9a7650",
    "block": "#b3aca1",
    "segmental": "#968e82",
    "gabion": "#8c8a80",
    "crib": "#9c8b6c",
}

# Material -> (texture image in data/walls/, metres one copy covers (u along the wall, v down
# its face from the top edge), bays). A site's own images are cut from its video frames by
# `domus prep wall-textures`, and the options' walls of these materials take them too; a material
# the site has no image of gets a painted one (domus.wall_looks). With `bays` the
# image is one bay with half a post at each side: each segment of the wall's line takes a whole
# number of copies, as near the given length as it can, so a post stands at every vertex.
MATERIAL_TEXTURES = {
    "block": ("block.jpg", (1.2, 0.2), False),         # three 0.4 m blocks, one course
    "timber": ("boards.jpg", (1.2, 0.8), True),        # a 1.0 m bay of 0.2 m boards and a 0.2 m post
    "stone": ("rubble.jpg", (1.75, 1.0), False),       # rubble
    "segmental": ("segmental.jpg", (0.9, 0.6), False),  # three 0.3 m blocks, three 0.2 m courses
    "gabion": ("gabion.jpg", (2.0, 1.0), False),       # two 1 m baskets
    "crib": ("crib.jpg", (1.2, 0.9), False),           # stretchers 0.3 m apart, headers 0.6 m apart
    "concrete": ("concrete.jpg", (2.4, 1.2), False),   # two 1.2 m formwork panels
}

# A wall can name the block it is built of: `unit_m` (a block's face: its length and its course height), and with
# it `cap_m` (the height of a capping course along the top) and `colour`. It is then drawn in those blocks at
# their true size, split-faced and in running bond, whatever picture its material has. One picture holds the
# capping course, UNIT_COURSES courses under it, and at its foot the cap's top, which the wall's top face takes.
UNIT_BLOCKS = 4         # blocks along one copy of the picture, each its own tone
UNIT_COURSES = 16       # courses under the cap: 3.2 m of 0.2 m courses before the picture comes round again
UNIT_TOP_M = 0.6        # of cap top: enough for the top of the thickest wall

# Such a wall is set out as it would be built (set_out, unit_mesh). Its courses are level and lie on one grid of
# levels: they run through where its top steps and where it stands against another wall of the same block, and
# each top carries its own capping course. Along each straight stretch the blocks are in running bond from the
# corners. Where the face turns round a corner (an outside corner) a corner block stands, which shows a whole
# block's length on one face and half a block's on the other, turn about from course to course. Whole blocks
# reach from one such corner to the next where the stretch between them is a whole number of half blocks long,
# and blocks are drawn up to UNIT_FIT long or short to make it so. Otherwise a block is cut at the corner, and
# the wall's notes say how far to move it. Where the face turns in (an inside corner) the blocks of the two faces
# butt, turn about: in one course the one face's block runs half its length on behind the other face, whose block
# stops against it, and in the next course the other way round. So there too one face shows a whole block at the
# corner and the other half of one.
UNIT_FIT = 0.01         # a block is drawn up to this much long or short, so whole blocks reach from corner to corner
UNIT_OFF_M = 0.005      # a top this far off the courses, or a corner this far off a joint, is cut and noted
UNIT_BAND = 14          # courses down a face before its picture starts again: an even number under UNIT_COURSES
UNIT_CORNER_DEG = 20.0  # a bend sharper than this is a corner; the blocks follow a gentler one
UNIT_JOIN_M = 0.06      # walls this close stand against each other, and are built as one

# Its capping course is laid in capping units as long as its blocks (_caps). Each length of top, from a corner, a
# step or an end to the next, starts and ends with a whole unit. At a corner the unit of one face runs through to
# the far side and the other face's butts against it. Where whole units do not reach, the one cut to fit is in
# the middle of the length, or two share it with a whole one's length so that neither is a sliver.
CAP_SHORT = 0.006       # a capping unit is drawn up to this much short, or UNIT_FIT long, so that whole ones reach
CAP_MIN_M = 0.15        # no cut capping unit is shorter than this


def from_feature(feature):
    """Wall dict from a GeoJSON LineString feature (data/site/walls-existing.geojson style)."""
    p = feature["properties"]
    line = np.asarray(feature["geometry"]["coordinates"], float)
    w = {
        "id": p["id"],
        "name": p.get("name", p["id"]),
        "material": p.get("material", "concrete"),
        "status": p.get("status", "existing"),
        "confidence": p.get("confidence", ""),
        "thickness": float(p.get("thickness_m", 0.2)),
        # a battered (leaning back) face: its top is batter_m further back than its foot at the toe,
        # and the line is the middle of the face
        "batter": float(p.get("batter_m", 0.0)),
        # retained_side is given looking along the line direction
        "side": 1 if p.get("retained_side", "left") == "left" else -1,
        "line": line[:, :2],
        "height": p.get("height_m"),
        # ends of the line with no post, for a wall with posts: its boards run on to them, as behind another wall
        "open_ends": open_ends(p),
        "properties": p,
    }
    chain = np.concatenate([[0], np.cumsum(np.hypot(*np.diff(line[:, :2], axis=0).T))])
    if line.shape[1] == 3:
        w["top"] = line[:, 2]
        w["top_chain"] = chain
    if "toe_m" in p:  # per-vertex toe levels, where the ground model in front of the face is no guide
        w["toe"] = np.asarray(p["toe_m"], float)
        w["toe_chain"] = chain
    return w


def open_ends(props):
    """A wall's `open_ends`: "start" and "end", the ends of its line with no post."""
    ends = props.get("open_ends") or []
    ends = [ends] if isinstance(ends, str) else list(ends)
    wrong = [e for e in ends if e not in ("start", "end")]
    if wrong:
        raise DomusError(f"{props.get('id')}: open_ends can be \"start\" and \"end\", not {', '.join(map(repr, wrong))}")
    return tuple(sorted(set(ends), key=("start", "end").index))


def line_normals(pts):
    n = len(pts)
    out = []
    for i in range(n):
        a = pts[max(i - 1, 0), :2]
        b = pts[min(i + 1, n - 1), :2]
        d = (b - a) / np.hypot(*(b - a))
        out.append([-d[1], d[0]])
    return np.array(out)


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


def wall_levels(wall, ground=None, spacing=0.25, probe=0.6):
    """Resample a wall and work out its toe (low side) and top levels along it.

    `wall` is a dict with `line` (Nx2 or Nx3 site coords), `side` (+1 if the retained, high
    side is left of the line direction, -1 if right), `thickness`, and either per-vertex
    `top` heights or a `height` above the toe. The toe comes from the ground model `probe`
    metres out on the low side (for a battered face, 0.2 m in front of its foot if that is
    further) unless `toe` is given: `ground` is that model (anything with sample(e, n)).

    With `bay_tops` (a level for each stretch of the line) the top is level along each stretch, and where
    it steps the point is there twice, once at each level, so the top drops straight down.
    """
    pts, chain = resample_line(wall["line"], spacing)
    nrm = line_normals(pts) * wall["side"]
    if wall.get("toe") is not None:
        toe = np.interp(chain, wall["toe_chain"], wall["toe"])
    else:
        if ground is None:
            raise DomusError(f"wall {wall.get('id')}: its toe comes from the ground, and none was given")
        low = pts[:, :2] - nrm * max(probe, 0.5 * wall.get("batter", 0.0) + 0.2)
        toe = ground.sample(low[:, 0], low[:, 1])
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


def texture_file(material, folder=None):
    """The site's own image of the material (a file name in its walls' pictures, `folder`: data/walls/), or None."""
    t = MATERIAL_TEXTURES.get(material)
    return t[0] if t and folder and (Path(folder) / t[0]).exists() else None


@lru_cache
def texture_image(material, out, painted=False, folder=None):
    """Put the material's wall texture in `out` (the viewer's data) and return its name there: the site's own
    image of that kind of wall, else one painted for it, or the painted one whatever the site has when `painted`
    asks for it (a new wall need not look like the old one the photo is of). None for a material drawn plain."""
    if material not in MATERIAL_TEXTURES:
        return None
    image, size_m, _ = MATERIAL_TEXTURES[material]
    if texture_file(material, folder) and not painted:
        name = f"wall-{image}"
        (out / name).write_bytes((Path(folder) / image).read_bytes())
    else:
        from . import wall_looks

        name = f"wall-painted-{image}" if texture_file(material, folder) else f"wall-{image}"
        wall_looks.paint(material, out / name, size_m)
    return name


def units(wall):
    """The block a wall names, as (length, course height, cap height, colour), or None for a wall drawn with its
    material's own picture."""
    p = wall.get("properties") or {}
    if "unit_m" not in p:
        return None
    length, height = (float(v) for v in p["unit_m"])
    return length, height, float(p.get("cap_m", 0.0)), p.get("colour")


def texture_size(wall):
    """((metres one copy of the wall's picture covers: u along it, v down its face), bays), or None for a wall
    drawn plain."""
    unit = units(wall)
    if unit:
        length, height, cap, _ = unit
        return (UNIT_BLOCKS * length, cap + UNIT_COURSES * height + UNIT_TOP_M), False
    if wall.get("material") in MATERIAL_TEXTURES:
        _, size_m, bays = MATERIAL_TEXTURES[wall["material"]]
        return size_m, bays
    return None


@lru_cache
def unit_image(unit, out):
    """Paint the picture of a wall of these blocks (domus.wall_looks.units) into `out` and return its name there."""
    from . import wall_looks

    length, height, cap, colour = unit
    name = f"wall-units-{hashlib.md5(repr(unit).encode()).hexdigest()[:8]}.jpg"
    wall_looks.paint_units(out / name, length, height, cap, colour, UNIT_BLOCKS, UNIT_COURSES, UNIT_TOP_M)
    return name


def wall_image(wall, out, painted=False, folder=None):
    """The picture one wall is drawn with, put in `out`: of its own blocks when it names them, else its
    material's (texture_image)."""
    unit = units(wall)
    return unit_image(unit, out) if unit else texture_image(wall["material"], out, painted, folder)


def texture_u(wall, chain):
    """u (in copies of the wall's picture) at `chain` along the wall: true scale, or for a
    material with bays a whole number of copies on each segment of the line. A segment running to an open end
    (`open_ends`) has no post there: its boards go on at true scale from the post before it."""
    (tu, _), bays = texture_size(wall)
    if not bays:
        return chain / tu
    seg = np.hypot(*np.diff(wall["line"][:, :2], axis=0).T)
    copies = np.maximum(1, np.round(seg / tu))
    ends = wall.get("open_ends", ())
    if not ends:
        return np.interp(chain, np.concatenate([[0], np.cumsum(seg)]), np.concatenate([[0], np.cumsum(copies)]))
    if "start" in ends:
        copies[0] = seg[0] / tu
    if "end" in ends:
        copies[-1] = seg[-1] / tu
    at = np.concatenate([[0], np.cumsum(copies)]) - (copies[0] if "start" in ends else 0.0)    # whole copies from the first post
    return np.interp(chain, np.concatenate([[0], np.cumsum(seg)]), at)


POST_WIDTH_M = 0.2      # along the wall: the post the bay image has half of at each side
POST_PROUD_M = 0.05     # how far a post stands out from the boards, and above the taller bay beside it
POST_DEPTH_M = 0.15     # and how far back into the wall it goes


def post_positions(wall):
    """Distances along a wall with bays where a post stands: every point of its line (its ends, corners and
    steps) but an open end, and between them where the bay image repeats, so the posts are the ones its picture
    shows."""
    (tu, _), _ = texture_size(wall)
    seg = np.hypot(*np.diff(wall["line"][:, :2], axis=0).T)
    start = np.concatenate([[0], np.cumsum(seg)])
    ends = wall.get("open_ends", ())
    if not ends:
        out = [start[-1]]
        for s0, length in zip(start[:-1], seg, strict=True):
            copies = int(max(1, round(length / tu)))
            out += list(s0 + length * np.arange(copies) / copies)
        return np.unique(np.round(out, 6))
    u = texture_u(wall, start)
    out = []
    for s0, length, u0, u1 in zip(start[:-1], seg, u[:-1], u[1:], strict=True):
        whole = np.arange(math.ceil(u0 - 1e-9), math.floor(u1 + 1e-9) + 1)     # where the picture has a post
        out += list(s0 + (whole - u0) / (u1 - u0) * length)
    skip = [start[0]] * ("start" in ends) + [start[-1]] * ("end" in ends)
    return np.unique(np.round([c for c in out if all(abs(c - s) > 1e-6 for s in skip)], 6))


def posts(wall, pts, chain, top, bottom):
    """The posts of a wall with bays, as blocks standing out from its face, with `uv` in copies of its image
    centred on the image's edge, where it has the post."""
    (tu, tv), _ = texture_size(wall)
    m, uv = Mesh(), []
    for c in post_positions(wall):
        near = np.abs(chain - c) < 1e-6
        k = int(np.argmin(np.abs(chain - c)))
        a, b = pts[max(k - 1, 0)], pts[min(k + 1, len(pts) - 1)]
        if near.sum() > 1:                    # a step: the point is there twice, so look past the pair
            first, last = np.flatnonzero(near)[[0, -1]]
            a, b = pts[max(first - 1, 0)], pts[min(last + 1, len(pts) - 1)]
        along = (b - a) / np.hypot(*(b - a))
        back = wall["side"] * np.array([-along[1], along[0]])
        head = float(np.max(top[near]) if near.any() else np.interp(c, chain, top))
        foot = float(np.min(bottom[near]) if near.any() else np.interp(c, chain, bottom))
        p = pts[k]
        ring = [p + s * 0.5 * POST_WIDTH_M * along + d * back
                for s, d in ((-1, -POST_PROUD_M), (1, -POST_PROUD_M), (1, POST_DEPTH_M), (-1, POST_DEPTH_M))]
        block = prism(ring, foot, head + POST_PROUD_M)
        u0 = float(texture_u(wall, np.array([c]))[0])
        du = (block.vertices[:, :2] - p) @ along / tu
        uv.append(np.column_stack([u0 + du, (block.vertices[:, 2] - head) / tv]))
        m.extend(block)
    return m, (np.vstack(uv) if uv else np.zeros((0, 2)))


def stretches(wall):
    """The straight stretches of a wall's line and the corners between them: the index of the line's point where
    each stretch starts (and of its last point), the distance along the line at each of those, and for each
    corner +1 where the face turns round it (an outside corner) or -1 where it turns in (an inside one)."""
    pts = np.asarray(wall["line"], float)[:, :2]
    seg = np.diff(pts, axis=0)
    run = np.hypot(*seg.T)
    full = np.concatenate([[0], np.cumsum(run)])
    at, corners = [0], []
    for i, j in itertools.pairwise(np.flatnonzero(run > 1e-9)):       # the two lengths of line that meet at point j
        a, b = seg[i] / run[i], seg[j] / run[j]
        turn = float(a[0] * b[1] - a[1] * b[0])
        if abs(turn) > math.sin(math.radians(UNIT_CORNER_DEG)) or float(a @ b) < 0:
            at.append(int(j))
            corners.append(1 if turn * wall["side"] > 0 else -1)
    at.append(len(pts) - 1)
    return at, [float(full[i]) for i in at], corners


def _bond(cuts, corners, length, seed=None):
    """The running bond along each straight stretch of a wall whose stretches start at `cuts` (the line's length
    last) and meet at `corners`: for each stretch the count of blocks at its start (`u0`: the even courses have a
    joint at each whole number, the odd ones at each half) and the blocks to a metre along it (`rate`). `seed`
    (stretch, u0, rate) gives one stretch its bond, as where it carries on another wall's. Without one the bond
    starts with a joint at the wall's first outside corner, or at the wall's start if it has none. Round every
    corner the next face starts half a block on: a corner block at an outside corner, blocks that butt turn about
    in an inside one. Also returns notes on the corners the blocks reach off a joint."""
    count = len(cuts) - 1
    run = np.diff(cuts)
    u0, rate, notes = [0.0] * count, [1.0 / length] * count, []
    outside = [k for k, c in enumerate(corners) if c > 0]
    if seed is not None:
        first, u0[first], rate[first] = seed
    else:
        first = outside[0] if outside else 0
        u0[first] = -run[first] / length if outside else 0.0
    way = 1.0 if rate[first] > 0 else -1.0

    def fitted(k):
        """Blocks to a metre along stretch k, which runs from one outside corner to another: a whole number of
        half blocks between them, if blocks no more than UNIT_FIT long or short do it."""
        r = max(1, round(2 * run[k] / length)) * 0.5 / run[k]
        return way * (r if abs(r * length - 1) <= UNIT_FIT else 1 / length)

    def joint(u, k):
        """The joint nearest `u` blocks, at the corner after stretch k; noted if `u` is off it."""
        on = round(2 * u) / 2
        off = abs(u - on) * length
        if off > UNIT_OFF_M:
            notes.append(f"its blocks reach its corner {cuts[k + 1]:.2f} m along it {off * 1000:.0f} mm off a joint, so a "
                         f"block is cut there: move the corner {off * 1000:.0f} mm, or {(length / 2 - off) * 1000:.0f} mm "
                         "the other way, for whole blocks")
        return on

    def kept(k, inside):
        """Stretch k runs between an inside corner and an outside one, and whole blocks do not reach from the one
        to the other: the outside corner keeps its joint, and the block cut to fit stands in the inside corner."""
        off = abs(_off(run[k], length / 2))
        notes.append(f"its blocks do not come out whole between its corners {cuts[k]:.2f} m and {cuts[k + 1]:.2f} m along "
                     f"it, so a block is cut in the inside corner {inside:.2f} m along it: move a corner {off * 1000:.0f} mm, "
                     f"or {(length / 2 - off) * 1000:.0f} mm the other way, for whole blocks")

    for k in range(first, count - 1):                                 # on from that stretch
        rate[k + 1] = way / length
        u0[k + 1] = joint(u0[k] + rate[k] * run[k], k) + 0.5
        if k + 1 < count - 1 and corners[k + 1] > 0:
            if corners[k] > 0:
                rate[k + 1] = fitted(k + 1)
            elif abs(_off(u0[k + 1] + rate[k + 1] * run[k + 1], 0.5)) * length > UNIT_OFF_M:
                u0[k + 1] = -rate[k + 1] * run[k + 1]                 # from an inside corner to an outside one: a joint at that one
                kept(k + 1, cuts[k + 1])
    for k in range(first - 1, -1, -1):                                # and back from it
        rate[k] = way / length
        end = joint(u0[k + 1], k) - 0.5
        if k > 0 and corners[k - 1] > 0:
            if corners[k] > 0:
                rate[k] = fitted(k)
            elif abs(_off(end - rate[k] * run[k], 0.5)) * length > UNIT_OFF_M:
                end = rate[k] * run[k]                                # from an outside corner to an inside one: a joint at the first
                kept(k, cuts[k + 1])
        u0[k] = end - rate[k] * run[k]
    return u0, rate, notes


def _off(drop, height):
    """How far `drop` (m) is from a whole number of courses `height` high: between minus and plus half a course."""
    return (drop + height / 2) % height - height / 2


def _span(chain, values, c0, c1):
    """`values` along a wall between the distances `c0` and `c1` along it, its ends included."""
    inside = (chain >= c0 - 1e-6) & (chain <= c1 + 1e-6)
    return np.concatenate([values[inside], np.interp([c0, c1], chain, values)])


def _lay(a, b, length):
    """Capping units `length` long laid from `a` to `b` along a wall: where their joints fall, and what the length
    counts against the choice that made it (0 where whole units reach, 1 where one is cut, a little more where two
    are). Whole units are laid in from both ends, and the cut ones are in the middle."""
    span = b - a
    if span < 0.02:
        return [float(a), float(max(a, b))], 0.0
    n = max(1, round(span / length))
    if -CAP_SHORT <= span / (n * length) - 1 <= UNIT_FIT:
        return [float(a + span * j / n) for j in range(n + 1)], 0.0
    whole, rest = int(span // length), span % length
    if whole and rest < CAP_MIN_M:
        whole, cut, count = whole - 1, [(length + rest) / 2] * 2, 1.2
    else:
        cut, count = [rest], 1.0
    first = (whole + 1) // 2
    sizes = [length] * first + cut + [length] * (whole - first)
    return [float(a + s) for s in np.concatenate([[0.0], np.cumsum(sizes)])], count


def _caps(chain, top, cuts, corners, length, depth, lap=0.0):
    """The capping units along a wall of named blocks, `length` long and `depth` deep. Its top is in lengths, from
    a corner, a step or an end to the next. At a corner one face's unit runs through: to the far face at an
    outside corner, on behind the other face in an inside one. The other face's unit butts against it, `depth`
    from an outside corner and at an inside one. A corner where the top changes level is a step. Which face runs
    through at each corner is chosen so that as few
    units as can be are cut. Returns for each length of top (where it starts, where it ends, the joints of its
    units), all as distances along the wall."""
    def drops(c):
        """Whether the top is at two levels at `c`: a corner like that is the end of the higher wall and the start
        of the lower one, as a step is, and no capping unit runs round it."""
        here = np.flatnonzero(np.abs(chain - c) < 1e-6)
        return len(here) > 1 and abs(top[here[0]] - top[here[-1]]) > 1e-9

    marks = [(float(cuts[0]), "end")] + [(float(c), "step" if drops(c) else "out" if k > 0 else "in")
                                         for c, k in zip(cuts[1:-1], corners, strict=True)]
    # A step of `lap` (one course, where two capping units make a course) along a straight stretch is capped as a
    # stair: two units stacked beside the end of the higher course, and the higher capping course lapping half a
    # unit over them. So the higher length of top runs half a unit on past the step.
    for i in range(len(chain) - 1):
        if abs(chain[i + 1] - chain[i]) < 1e-9 and abs(top[i + 1] - top[i]) > 1e-9 and all(abs(chain[i] - c) > 1e-6 for c in cuts):
            fall = float(top[i] - top[i + 1])
            marks.append((float(chain[i]), ("down" if fall > 0 else "up") if lap and abs(abs(fall) - lap) < 1e-6 else "step"))
    marks = sorted(marks) + [(float(cuts[-1]), "end")]
    turns = [i for i, (_, kind) in enumerate(marks) if kind in ("out", "in")][:12]
    best = None
    for choice in itertools.product((False, True), repeat=len(turns)):
        after = dict(zip(turns, choice, strict=True))                 # at this corner the unit of the face after it runs through
        laid, count = [], 0.0
        for i in range(len(marks) - 1):
            (c0, k0), (c1, k1) = marks[i], marks[i + 1]
            a = c0 + {"out": 0.0 if after.get(i) else depth, "in": -depth if after.get(i) else 0.0, "up": -length / 2}.get(k0, 0.0)
            b = c1 + {"out": -depth if after.get(i + 1) else 0.0, "in": 0.0 if after.get(i + 1) else depth,
                      "down": length / 2}.get(k1, 0.0)
            joints, cut = _lay(a, b, length)
            laid.append((c0, c1, joints))
            count += cut
        if best is None or count < best[0] - 1e-9:
            best = (count, laid)
    return best[1]


def set_out(walls, ground=None):
    """Set out the walls of named blocks among `walls` together, as they would be built, and give each its
    `set_out`:

    - `datum`, the level of a course's top that its courses are counted down from. Walls of one block share
      one grid of levels where their tops are a whole number of courses apart. A wall that stands against
      another takes that wall's grid whatever its own top, and the course under its cap is then cut.
    - its bond: `cuts`, where each straight stretch of its line starts, and for each stretch `u0` and `rate`
      (_bond). A stretch that lies in the same face as a stretch of a wall set out before it carries on that
      wall's bond.
    - `hidden`, the lengths of its face that lie in the face of such a wall, at least as high: not drawn twice.
    - `caps`, its capping units where it has a capping course (_caps).
    - `notes` on what does not fit: a top off the courses, a corner off a joint.

    Returns the notes by wall id. A wall with a batter, or with no levels, is left alone: its picture hangs from
    its top."""
    from shapely.geometry import Polygon

    named, found, kinds = [], [], {}
    for w in walls:
        if not units(w) or w.get("batter"):
            continue
        pts, _, chain, _, top = wall_levels(w, ground)
        if not np.all(np.isfinite(top)):
            continue                                                  # off the ground model: nothing to set it out by
        body = Polygon(np.vstack([pts, offset_behind(pts, w["thickness"], w["side"])[::-1]])).buffer(0)
        kinds.setdefault(units(w)[:3], []).append(len(named))
        named.append(w)
        found.append({"chain": chain, "top": top, "body": body, "notes": []})
    for (_, height, cap), same in kinds.items():
        tops = {i: float(found[i]["top"].max()) - cap for i in same}           # the top of each wall's highest course
        highest = max(tops.values())
        group = {i: {i} for i in same}                                         # the walls that stand against each other
        for i, j in itertools.combinations(same, 2):
            if group[i] is not group[j] and found[i]["body"].distance(found[j]["body"]) < UNIT_JOIN_M:
                group[i] |= group[j]
                for k in group[j]:
                    group[k] = group[i]
        for members in {id(g): g for g in group.values()}.values():
            lead = max(members, key=lambda i: tops[i])
            # On the highest wall's courses where that fits, so every wall's odd and even courses are the same ones.
            grid = highest if abs(_off(highest - tops[lead], height)) <= UNIT_OFF_M else tops[lead]
            for i in members:
                off = _off(grid - tops[i], height)
                if abs(off) <= UNIT_OFF_M:
                    found[i]["datum"] = tops[i] + round((grid - tops[i]) / height) * height
                    continue
                found[i]["datum"] = grid
                up, down = (off, height - off) if off > 0 else (height + off, -off)
                found[i]["notes"].append(
                    f"its top is {abs(off) * 1000:.0f} mm off the courses of {named[lead]['id']}, which it stands "
                    f"against, so the course under its cap is cut: {up * 1000:.0f} mm higher or {down * 1000:.0f} mm "
                    "lower and the courses run through")
        for i in same:
            if named[i].get("bay_tops") is not None:
                bays = np.unique(np.round(np.asarray(named[i]["bay_tops"], float), 4))
                cut = [b for b in bays if abs(_off(found[i]["datum"] - (b - cap), height)) > UNIT_OFF_M]
                if cut:
                    found[i]["notes"].append(
                        f"{len(cut)} of its {len(bays)} top levels are not a whole number of {height * 1000:.0f} mm "
                        "courses from the highest, so the course under the cap is cut there")
    laid = []       # the straight stretches set out so far, which a later wall's stretch in the same face carries on
    for i, w in enumerate(named):
        unit = units(w)
        line = np.asarray(w["line"], float)[:, :2]
        at, cuts, corners = stretches(w)
        seed, hidden, mine = None, [], []
        for k in range(len(at) - 1):
            a, b = line[at[k]], line[at[k + 1]]
            run = float(np.hypot(*(b - a)))
            if run < 1e-6:
                continue
            d = (b - a) / run
            if np.abs((line[at[k]:at[k + 1] + 1] - a) @ np.array([-d[1], d[0]])).max() > 0.01:
                continue                                              # it bends: nothing carries on from it, or it from anything
            face = -w["side"] * np.array([-d[1], d[0]])
            mine.append((k, a, d, run, face))
            for other in laid if seed is None else ():
                da, rel = other["d"], a - other["a"]
                if (other["unit"] != unit[:3] or float(other["face"] @ face) < 0.9 or abs(da[0] * d[1] - da[1] * d[0]) > 0.02
                        or abs(da[0] * rel[1] - da[1] * rel[0]) > 0.02):
                    continue                                          # another block, another way, or another line
                same_way = 1.0 if float(d @ da) > 0 else -1.0
                t0 = float(rel @ da)
                lo, hi = sorted((t0, t0 + same_way * run))
                if hi < -UNIT_JOIN_M or lo > other["run"] + UNIT_JOIN_M:
                    continue                                          # in the same line, but apart
                seed = (k, other["u0"] + other["rate"] * t0, other["rate"] * same_way)
                lo, hi = max(lo, 0.0), min(hi, other["run"])
                if hi - lo > 1e-3:                                    # the length they share is drawn once, by the higher
                    c0, c1 = sorted(cuts[k] + (t - t0) * same_way for t in (lo, hi))
                    theirs = found[other["wall"]]
                    if (_span(theirs["chain"], theirs["top"], other["cut"] + lo, other["cut"] + hi).min()
                            >= _span(found[i]["chain"], found[i]["top"], c0, c1).max() - 1e-6):
                        hidden.append((c0, c1))
                break
        u0, rate, notes = _bond(cuts, corners, unit[0], seed)
        lap = unit[1] if abs(2 * unit[2] - unit[1]) < 1e-6 else 0.0      # two capping units make a course: steps are capped as stairs
        caps = _caps(found[i]["chain"], found[i]["top"], cuts, corners, unit[0], w["thickness"], lap) if unit[2] else []
        w["set_out"] = {"datum": found[i]["datum"], "cuts": cuts, "u0": u0, "rate": rate, "hidden": hidden,
                        "caps": caps, "notes": found[i]["notes"] + notes}
        laid += [{"unit": unit[:3], "wall": i, "a": a, "d": d, "run": run, "face": face, "u0": u0[k], "rate": rate[k],
                  "cut": cuts[k]} for k, a, d, run, face in mine]
    return {w["id"]: w["set_out"]["notes"] for w in named}


def unit_mesh(wall, pts, chain, top, bottom):
    """The mesh of a wall of named blocks, set out as its `set_out` says. Every flat piece has points of its own,
    so the bond can start afresh at a corner and the courses keep their levels under a top that steps: the face
    and the back in upright pieces, each with the capping course along its top and the courses under it on the
    wall's grid; the top; the two ends; and the end of the higher bay where the top steps. The capping course and
    the top take their joints from the wall's capping units. It carries `uv` in copies of the wall's picture
    (unit_image)."""
    length, height, cap, _ = units(wall)
    (_, tv), _ = texture_size(wall)
    so = wall["set_out"]
    side, datum = wall["side"], so["datum"]
    cuts, u0, rate = np.asarray(so["cuts"]), so["u0"], so["rate"]
    caps = so.get("caps") or []
    # A point where a hidden length of face starts and ends, and each joint between two capping units.
    for c in sorted({c for span in so["hidden"] for c in span} | {c for c0, c1, joints in caps for c in joints if c0 < c < c1}):
        if chain[0] < c < chain[-1] and np.abs(chain - c).min() > 1e-6:
            i = int(np.searchsorted(chain, c))
            t = (c - chain[i - 1]) / (chain[i] - chain[i - 1])
            pts, chain, top, bottom = (np.insert(v, i, v[i - 1] + t * (v[i] - v[i - 1]), axis=0)
                                       for v in (pts, chain, top, bottom))
    behind = offset_behind(pts, wall["thickness"], side)
    # The picture's courses start under its cap: the wall's highest course is its first or its second, so that
    # odd and even courses stay the ones the grid makes them.
    shift = 2 * height * math.floor((datum - (float(top.max()) - cap)) / (2 * height) + 1e-6)
    xyz, uv, faces = [], [], []

    def quad(corners, out):
        """A flat piece: its four corners in turn, each (E, N, height, u, v), drawn to face the way `out` points."""
        p = np.asarray(corners, float)
        normal = np.cross(p[1, :3] - p[0, :3], p[2, :3] - p[0, :3]) + np.cross(p[2, :3] - p[0, :3], p[3, :3] - p[0, :3])
        if float(normal @ out) < 0:
            p = p[::-1]
        k = 4 * len(xyz)
        xyz.append(p[:, :3])
        uv.append(p[:, 3:])
        faces.extend([(k, k + 1, k + 2), (k, k + 2, k + 3)])

    def panel(pa, pb, ua, ub, bot, head, out, capped=None):
        """An upright piece of the wall between two points on the plan, `ua` and `ub` blocks along the bond, from
        `bot` up to `head` (each a level at the one point and at the other): the capping course, then the courses
        on the wall's grid, in bands of UNIT_BAND courses down the picture. `capped` is how many capping units
        along its length of top each of the two points is, where the capping course does not follow the blocks."""
        (ba, bb), (ta, tb) = bot, head
        if np.hypot(*(np.asarray(pb) - pa)) < 1e-9 or not np.all(np.isfinite([ba, bb, ta, tb])):
            return
        ca, cb = (max(ta - cap, ba), max(tb - cap, bb)) if cap else (ta, tb)
        if ta - ca > 1e-9 or tb - cb > 1e-9:
            xa, xb = (capped[0] + 0.25, capped[1] + 0.25) if capped else (ua, ub)       # the picture's cap has its joints a quarter along
            quad([(*pa, ca, xa, ca - ta), (*pb, cb, xb, cb - tb), (*pb, tb, xb, 0.0), (*pa, ta, xa, 0.0)], out)
        hi = datum - shift
        while True:
            lo = hi - UNIT_BAND * height
            a1, b1, a0, b0 = min(ca, hi), min(cb, hi), max(ba, lo), max(bb, lo)
            if a1 - a0 > 1e-9 or b1 - b0 > 1e-9:
                a0, b0 = min(a0, a1), min(b0, b1)
                quad([(*pa, a0, ua, a0 - hi - cap), (*pb, b0, ub, b0 - hi - cap),
                      (*pb, b1, ub, b1 - hi - cap), (*pa, a1, ua, a1 - hi - cap)], out)
            if lo <= min(ba, bb):
                break
            hi = lo

    def across(i):
        """The blocks an end of the wall shows across it at point i: whole half blocks, as the end of a corner block
        is, so that no sliver is drawn where the wall is a little deeper or shallower than that."""
        return 0.5 * max(1, round(2 * float(np.hypot(*(behind[i] - pts[i]))) / length))

    def end_cap(i):
        """The end of a capping unit across the wall at point i: no joint in it."""
        return (0.05, 0.05 + float(np.hypot(*(behind[i] - pts[i]))) / length) if caps else None

    def cap_at(c, mid):
        """How many capping units along its length of top the distance `c` along the wall is, in the length that
        `mid` is in: a whole number at each joint."""
        for c0, c1, joints in caps:
            if c0 - 1e-6 <= mid <= c1 + 1e-6:
                if c <= joints[0]:
                    return (c - joints[0]) / length
                if c >= joints[-1]:
                    return len(joints) - 1 + (c - joints[-1]) / length
                return float(np.interp(c, joints, np.arange(len(joints))))
        return c / length

    count = len(pts)
    for i in range(count - 1):
        d = pts[i + 1] - pts[i]
        run = float(np.hypot(*d))
        k = int(np.clip(np.searchsorted(cuts, 0.5 * (chain[i] + chain[i + 1]), side="right") - 1, 0, len(u0) - 1))
        ua, ub = (u0[k] + rate[k] * (c - cuts[k]) for c in (chain[i], chain[i + 1]))
        if run < 1e-9:                      # the top steps: the end of the higher bay shows, across the wall
            lower, higher = sorted((float(top[i]), float(top[i + 1])))
            if higher - lower > 1e-9:
                a, b = pts[max(i - 1, 0)], pts[min(i + 2, count - 1)]
                on = (b - a) / max(float(np.hypot(*(b - a))), 1e-9) * (1 if top[i] > top[i + 1] else -1)
                stair = caps and abs(2 * cap - height) < 1e-6 and abs(higher - lower - height) < 1e-6 and np.abs(cuts - chain[i]).min() > 1e-6
                if stair:
                    # Two capping units stacked beside the end of the higher course, on the lower course: the lower
                    # one is the lower capping course's first, the upper one is drawn here. The higher capping
                    # course laps half a unit over it.
                    for reach, z0, z1, u in ((length, lower, lower + cap, 0.25), (length / 2, higher - cap, higher, 0.75)):
                        f0, f1, b0, b1 = pts[i], pts[i] + on * reach, behind[i], behind[i] + on * reach
                        deep = float(np.hypot(*(behind[i] - pts[i])))
                        u1 = u + reach / length
                        face = (f0 - b0) / max(deep, 1e-9)
                        quad([(*f0, z0, u, -cap), (*f1, z0, u1, -cap), (*f1, z1, u1, 0.0), (*f0, z1, u, 0.0)], np.array([*face, 0.0]))
                        quad([(*b0, z0, u, -cap), (*b1, z0, u1, -cap), (*b1, z1, u1, 0.0), (*b0, z1, u, 0.0)], np.array([*-face, 0.0]))
                        quad([(*f0, z1, u, 0.0), (*f1, z1, u1, 0.0), (*b1, z1, u1, deep), (*b0, z1, u, deep)], np.array([0.0, 0.0, 1.0]))
                        quad([(*f1, z0, 0.3, -cap), (*b1, z0, 0.3 + deep / length, -cap), (*b1, z1, 0.3 + deep / length, 0.0),
                              (*f1, z1, 0.3, 0.0)], np.array([*on, 0.0]))
                    continue
                panel(pts[i], behind[i], ua + 0.5, ua + 0.5 + across(i), (lower, lower), (higher, higher), np.array([*on, 0.0]),
                      end_cap(i))
            continue
        d = d / run
        back = side * np.array([-d[1], d[0]])
        bot, head = (bottom[i], bottom[i + 1]), (top[i], top[i + 1])
        mid = 0.5 * (chain[i] + chain[i + 1])
        va, vb = (u + rate[k] * float((behind[j] - pts[j]) @ d) for u, j in ((ua, i), (ub, i + 1)))
        wa, wb = (min(float((behind[j] - pts[j]) @ back), UNIT_TOP_M) for j in (i, i + 1))
        face = back_face = None
        ta, tb, sa, sb = ua, ub, va, vb                               # the top's joints: the blocks', or its capping units'
        if caps:
            ca, cb = cap_at(chain[i], mid), cap_at(chain[i + 1], mid)
            each = (cb - ca) / run                                    # capping units to a metre here
            face = (ca, cb)
            back_face = tuple(c + each * float((behind[j] - pts[j]) @ d) for c, j in ((ca, i), (cb, i + 1)))
            ta, tb, sa, sb = ca + 0.25, cb + 0.25, back_face[0] + 0.25, back_face[1] + 0.25
        if not any(c0 - 1e-6 <= mid <= c1 + 1e-6 for c0, c1 in so["hidden"]):
            panel(pts[i], pts[i + 1], ua, ub, bot, head, np.array([*-back, 0.0]), face)             # the face
        panel(behind[i], behind[i + 1], va, vb, bot, head, np.array([*back, 0.0]), back_face)       # the back
        quad([(*pts[i], top[i], ta, 0.0), (*pts[i + 1], top[i + 1], tb, 0.0),
              (*behind[i + 1], top[i + 1], sb, wb), (*behind[i], top[i], sa, wa)], np.array([0.0, 0.0, 1.0]))    # the top
    for i, k, turn in ((0, 0, 0.5), (count - 1, len(u0) - 1, -0.5)):       # the ends: half a block on, as round a corner
        far = pts[np.flatnonzero(np.hypot(*(pts - pts[i]).T) > 1e-9)]
        if not len(far):
            continue
        out = pts[i] - far[0 if i == 0 else -1]
        ue = u0[k] + rate[k] * (chain[i] - cuts[k]) + turn
        panel(pts[i], behind[i], ue, ue + math.copysign(across(i), turn), (bottom[i],) * 2, (top[i],) * 2, np.array([*out, 0.0]),
              end_cap(i))
    m = Mesh(np.vstack(xyz), faces)
    m.uv = np.vstack(uv) / [UNIT_BLOCKS, tv]
    m.cap = np.zeros(len(m.vertices), bool)
    return m


def mesh(wall, ground=None):
    """The wall's mesh; it carries `uv` in copies of its material's texture (MATERIAL_TEXTURES). A wall whose
    image is a bay between posts (timber) gets the posts as blocks standing out from its boards. A wall of named
    blocks is set out block by block (set_out, unit_mesh): on its own, unless its `set_out` is already there."""
    pts, _, chain, toe, top = wall_levels(wall, ground)
    bottom = toe - 0.15  # bury the footing a little so the wall meets the ground cleanly
    info = {
        "length_m": float(chain[-1]),
        "height_min_m": float(np.nanmin(top - toe)),
        "height_max_m": float(np.nanmax(top - toe)),
        "top_min": float(np.nanmin(top)),
        "top_max": float(np.nanmax(top)),
        "toe_min": float(np.nanmin(toe)),
        "toe_max": float(np.nanmax(toe)),
        "face_area_m2": float(np.trapezoid(top - toe, chain)),
    }
    if units(wall) and not wall.get("batter"):
        if "set_out" not in wall:
            set_out([wall])
        if "set_out" in wall:
            return unit_mesh(wall, pts, chain, top, bottom), info
    m = wall_strip(pts, top, bottom, wall["thickness"], wall["side"], wall.get("batter", 0.0), toe, chain)
    if texture_size(wall):
        (tu, tv), bays = texture_size(wall)
        u = texture_u(wall, m.uv[:, 0])
        # The end caps at true scale across the wall's end; with bays, centred on the image's edge,
        # so the end shows the post the image has half of at each side.
        u[m.cap] = (m.uv[m.cap, 0] - (0.5 * wall["thickness"] if bays else 0.0)) / tu
        for end in wall.get("open_ends", ()) if bays else ():      # boards at an open end, not the post
            near = np.hypot(*(m.vertices[:, :2] - pts[0 if end == "start" else -1]).T)
            other = np.hypot(*(m.vertices[:, :2] - pts[-1 if end == "start" else 0]).T)
            u[m.cap & (near < other)] += 0.5
        m.uv = np.column_stack([u, m.uv[:, 1] / tv])
        if bays:
            cap = m.cap
            stand, stand_uv = posts(wall, pts, chain, top, bottom)
            m.uv = np.vstack([m.uv, stand_uv])
            m.add(stand.vertices, stand.faces)
            m.cap = np.concatenate([cap, np.zeros(len(stand.vertices), bool)])
    return m, info
