"""What an export holds: the layers of a built model that the viewer shows, as meshes and lines in viewer metres.

The build writes the model (model.json and its pictures, docs/model-format.md). This reads it back, picks the layers
the viewer shows for the site as it is or with one wall option shown (`shown`), and draws each as the viewer does
(`parts`): leaf cards, limbs, stumps and drains as the triangles the viewer makes of them, and the ground cut where an
option's designed ground takes its place. Lawns and beds lie on the ground they're drawn on, which the viewer draws
them in front of; other software can't do that, so they're raised LIFT_M. Labels are left out, as neither glTF nor
IFC holds text, and so are the layers the viewer hides until they're switched on.

Positions are in viewer metres: x east of the site's origin, y up from its height and z south of it.
"""

import base64
import math
from dataclasses import dataclass, field

import numpy as np

from ..errors import DomusError

MODEL_FORMAT = 1            # the model.json format this reads (domus.build.MODEL_FORMAT)
LIFT_M = 0.02               # how far lawns and beds are raised off the ground they're drawn on
HIDE_WITH_OPTION = ("veg-beds", "veg-bed-floor", "stumps")      # the viewer hides these while an option is shown


@dataclass
class Look:
    """How a piece is drawn: its colour (sRGB, as "#rrggbb"), its picture (a file in the model's folder) and whether
    the picture repeats, how rough it is, whether both its sides show, and whether it's see-through: "MASK" cuts it
    where its picture is clear (leaf cards), "BLEND" fades it to `opacity`. A `linear` picture holds shading to
    multiply colours by, not colours (the leaf cards' grey)."""

    colour: str = "#ffffff"
    texture: str | None = None
    repeat: bool = False
    roughness: float = 0.85
    double_sided: bool = False
    alpha: str = "OPAQUE"
    opacity: float = 1.0
    linear: bool = False


@dataclass
class Piece:
    """Some of a part's geometry with one look: `positions` (n by 3), and `faces` (m by 3, triangles) or, for lines,
    none (the positions in pairs). `normals` per vertex, or None for flat shading; `uv` per vertex as the viewer lays
    pictures, v up from the picture's foot; `colours` per vertex, linear RGB, which multiply the look's."""

    positions: np.ndarray
    faces: np.ndarray | None
    look: Look
    normals: np.ndarray | None = None
    uv: np.ndarray | None = None
    colours: np.ndarray | None = None


@dataclass
class Part:
    """One thing in the model: a layer the viewer shows, or one plant of a vegetation layer. `colour` is its swatch in
    the viewer's layers panel; `info` what the viewer shows when it's picked; `children` its plants."""

    layer: dict = field(repr=False)
    name: str
    group: str
    colour: str
    info: dict
    pieces: list = field(default_factory=list)
    children: list = field(default_factory=list)
    plant: str | None = None

    @property
    def id(self):
        return self.layer["id"]

    def mesh(self):
        """Its triangles, its children's among them, as one mesh: (positions, faces)."""
        verts, faces = [], []
        for p in self.pieces + [p for c in self.children for p in c.pieces]:
            if p.faces is not None and len(p.faces):
                faces.append(p.faces + sum(len(v) for v in verts))
                verts.append(p.positions)
        if not verts:
            return np.zeros((0, 3)), np.zeros((0, 3), np.int64)
        return np.vstack(verts), np.vstack(faces)


def options(model):
    """The wall options the model was built with, by id: its summary's."""
    return {o["id"]: o for o in model.get("summary", {}).get("options", [])}


def option_of(model, option):
    """The summary of the option `option` (its id), or None for None; a DomusError if the model has no such option."""
    if option is None:
        return None
    found = options(model)
    if option not in found:
        have = ", ".join(found) or "none"
        raise DomusError(f"The model has no option {option!r} (it has {have}). An option's id is its file's name in "
                         "options/; build the site again after adding one.")
    return found[option]


def shown(model, option=None):
    """The layers the viewer shows, in the model's order: those it doesn't hide until they're switched on, or with the
    option `option` (an option's id) shown, as its options panel does it. Then the option's own layers come in, and
    out go the existing walls it doesn't keep, the beds, their plants and the stumps, the site features it takes out,
    the lawns it draws itself on its own ground, and the outlines of ground that isn't from the ground model."""
    picked = option_of(model, option)
    keep = set(picked["keep_existing"]) if picked else set()
    existing = {f"wall-{w['id']}": w["id"] for w in model.get("summary", {}).get("walls", [])}
    out = []
    for layer in model["layers"]:
        lid = layer["id"]
        if layer.get("option"):
            on = layer["option"] == option
        else:
            on = layer.get("visible", True) is not False and lid != "contours-minor"
            if picked and ((lid in existing and existing[lid] not in keep) or lid in HIDE_WITH_OPTION
                           or option in layer.get("hide_with", ()) or option in layer.get("redrawn_by", ())
                           or lid.startswith("fix-")):
                on = False
        if on:
            out.append(layer)
    return out


def option_box(model, option):
    """Where the option's designed ground takes the place of the ground, in plan: (x0, z0, x1, z1) in viewer metres,
    from its ground's `mask` (fractions of the model's extent), or None."""
    ground = next((x for x in model["layers"] if x["id"] == f"option-{option}-ground"), None) if option else None
    if ground is None or "mask" not in ground:
        return None
    e0, n0, e1, n1 = model["extent"]
    E0, N0 = model["origin"][:2]
    u0, v0, u1, v1 = ground["mask"]
    x = lambda u: e0 + u * (e1 - e0) - E0
    z = lambda v: -(n0 + v * (n1 - n0) - N0)
    return x(u0), z(v1), x(u1), z(v0)


def parts(model, option=None):
    """What an export writes, in the model's order: each layer `shown` gives, drawn as the viewer draws it."""
    box = option_box(model, option)
    drape = next(iter(model.get("textures", {}).values()), {}).get("file")    # the ground's first drape, as the viewer
    out = []
    for layer in shown(model, option):
        kind = layer["type"]
        if kind == "mesh":
            part = mesh_part(layer, drape, box)
        elif kind == "lines":
            part = lines_part(layer, box if layer["id"] in ("contours-major", "contours-minor") else None)
        elif kind == "vegetation":
            part = vegetation_part(layer)
        elif kind == "pipes":
            part = pipe_part(layer)
        else:
            continue
        if part.pieces or part.children:
            out.append(part)
    return out


def _part(layer, **kw):
    return Part(layer=layer, name=layer.get("name", layer["id"]), group=layer.get("group") or "Other",
                colour=layer.get("colour") or "#888888", info=dict(layer.get("info") or {}), **kw)


def vertex_normals(v, faces):
    """Each vertex's normal as the viewer works it out (three.js's computeVertexNormals): the sum of its triangles'
    normals, each as long as twice its area, made unit length. A vertex no triangle uses points up."""
    n = np.zeros_like(v, dtype=float)
    if len(faces):
        a, b, c = v[faces[:, 0]], v[faces[:, 1]], v[faces[:, 2]]
        fn = np.cross(b - a, c - a)
        for k in range(3):
            np.add.at(n, faces[:, k], fn)
    length = np.linalg.norm(n, axis=1)
    n[length < 1e-12] = (0.0, 1.0, 0.0)
    return n / np.maximum(np.linalg.norm(n, axis=1), 1e-12)[:, None]


def keep_vertices(keep, v, faces, *attrs):
    """The mesh with only the vertices `keep` marks, and the triangles all of whose vertices it keeps:
    (v, faces, *attrs)."""
    if keep.all():
        return (v, faces, *attrs)
    faces = faces[keep[faces].all(axis=1)] if len(faces) else faces
    new = np.full(len(v), -1, np.int64)
    new[keep] = np.arange(int(keep.sum()))
    return (v[keep], new[faces], *[a[keep] if a is not None else None for a in attrs])


def finite(v, faces, *attrs):
    """The mesh without the vertices that have no position, and the triangles that use them: (v, faces, *attrs)."""
    return keep_vertices(np.isfinite(v).all(axis=1), v, faces, *attrs)


def compact(v, faces, *attrs):
    """The mesh without the vertices no triangle uses: (v, faces, *attrs)."""
    used = np.zeros(len(v), bool)
    used[faces.ravel()] = True
    return keep_vertices(used, v, faces, *attrs)


def _clip(poly, axis, value, side):
    """The polygon `poly` (rows: x, y, z and then whatever is worked out along with them) cut by a plane: what lies on
    the side of `value` along `axis` that `side` gives (-1: below, 1: above)."""
    out = []
    for i, a in enumerate(poly):
        b = poly[(i + 1) % len(poly)]
        da, db = side * (a[axis] - value), side * (b[axis] - value)
        if da >= 0:
            out.append(a)
        if (da >= 0) != (db >= 0):
            out.append(a + (da / (da - db)) * (b - a))
    return out


def cut_out(v, faces, box, *attrs):
    """The mesh (`v`, `faces`) less what lies inside `box`, (x0, z0, x1, z1) in plan, as the viewer hides the ground
    under an option's designed ground: triangles inside it go, and those across its edge are cut along it. `attrs` are
    arrays with a row for each vertex (normals, pictures' coordinates), worked out along the cuts as the heights are.
    Returns (v, faces, *attrs), with the vertices no triangle uses any more left out."""
    x0, z0, x1, z1 = box
    tri = v[faces]
    xs, zs = tri[..., 0], tri[..., 2]
    clear = (xs.max(1) <= x0) | (xs.min(1) >= x1) | (zs.max(1) <= z0) | (zs.min(1) >= z1)
    inside = ((xs > x0) & (xs < x1) & (zs > z0) & (zs < z1)).all(1)
    across = ~clear & ~inside
    data = np.column_stack([v, *[a for a in attrs if a is not None]])
    # The ground outside the box, in four pieces that only touch: west of it, east of it, and south and north of it
    # between the two. Each is where a few planes meet, so a triangle cut to each is a convex polygon.
    regions = [((0, x0, -1),), ((0, x1, 1),), ((0, x0, 1), (0, x1, -1), (2, z0, -1)), ((0, x0, 1), (0, x1, -1), (2, z1, 1))]
    rows, new_faces = [], []
    for f in faces[across]:
        for region in regions:
            poly = [data[i] for i in f]
            for axis, value, side in region:
                poly = _clip(poly, axis, value, side)
                if len(poly) < 3:
                    break
            if len(poly) < 3:
                continue
            p = np.asarray(poly)
            area = np.linalg.norm(np.cross(p[1:-1, :3] - p[0, :3], p[2:, :3] - p[0, :3]), axis=1)
            k = len(data) + len(rows)
            for i in range(1, len(p) - 1):
                if area[i - 1] > 1e-9:
                    new_faces.append((k, k + i, k + i + 1))
            rows.extend(p)
    if rows:
        data = np.vstack([data, np.asarray(rows)])
    faces = np.vstack([faces[clear], np.asarray(new_faces, np.int64).reshape(-1, 3)])
    out, at = [data[:, :3]], 3
    for a in attrs:
        if a is None:
            out.append(None)
            continue
        out.append(data[:, at:at + a.shape[1]])
        at += a.shape[1]
    return compact(out[0], faces, *out[1:])


def mesh_part(layer, drape, box=None):
    """A mesh layer as the viewer draws it: the ground and an option's designed ground under the first drape (and the
    ground, with an option shown, less what lies inside `box`); an option's lawns and beds on its ground with their
    tiles, raised LIFT_M; the rest in their pictures, or their colours, both sides showing, with the roof flat."""
    v = np.asarray(layer.get("positions", []), float).reshape(-1, 3)
    f = np.asarray(layer.get("indices", []), np.int64).reshape(-1, 3)
    uv = np.asarray(layer["uv"], float).reshape(-1, 2) if layer.get("uv") is not None else None
    if uv is not None and len(uv) != len(v):
        uv = None
    v, f, uv = finite(v, f, uv)
    part = _part(layer)
    if not len(f):
        return part
    role, colour = layer.get("role"), layer.get("colour") or "#888888"
    flat = layer["id"].startswith("house-roof")
    n = None if flat else vertex_normals(v, f)
    if layer["id"] == "ground" or role == "ground":
        texture = drape if uv is not None else None
        look = Look(colour="#ffffff" if texture else colour, texture=texture, roughness=1.0)
        if layer["id"] == "ground" and box is not None:
            v, f, n, uv = cut_out(v, f, box, n, uv)
            n = n / np.maximum(np.linalg.norm(n, axis=1), 1e-12)[:, None]
    elif role == "skin":
        texture = layer.get("texture") if uv is not None else None
        look = Look(colour=(layer.get("tint") or "#ffffff") if texture else colour, texture=texture, repeat=True,
                    roughness=1.0)
        v = v + (0.0, LIFT_M, 0.0)
    else:
        texture = layer.get("texture") if uv is not None else None
        opacity = layer.get("opacity")
        look = Look(colour="#ffffff" if texture else colour, texture=texture, repeat=bool(layer.get("repeat")),
                    roughness=0.85, double_sided=True, alpha="OPAQUE" if opacity is None else "BLEND",
                    opacity=1.0 if opacity is None else float(opacity))
    part.pieces.append(Piece(v, f, look, n, uv if texture else None))
    return part


def lines_part(layer, box=None):
    """A lines layer: its segments, less those with both ends inside `box` (the existing contours where an option's
    own take over, as the viewer leaves them out)."""
    seg = np.asarray(layer.get("segments", []), float).reshape(-1, 2, 3)
    seg = seg[np.isfinite(seg).all(axis=(1, 2))]
    if box is not None and len(seg):
        x0, z0, x1, z1 = box
        inside = (seg[..., 0] > x0) & (seg[..., 0] < x1) & (seg[..., 2] > z0) & (seg[..., 2] < z1)
        seg = seg[~inside.all(axis=1)]
    part = _part(layer)
    if len(seg):
        part.pieces.append(Piece(seg.reshape(-1, 3), None, Look(colour=layer.get("colour") or "#333333")))
    return part


def srgb_to_linear(c):
    """sRGB values (0 to 1) as linear light, as three.js reads a colour given in sRGB."""
    c = np.asarray(c, float)
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def hex_rgb(colour):
    """A colour as "#rrggbb" (or "#rgb"), as three numbers from 0 to 1; grey for anything else."""
    text = str(colour or "").lstrip("#")
    if len(text) == 3:
        text = "".join(c * 2 for c in text)
    try:
        return [int(text[i:i + 2], 16) / 255 for i in (0, 2, 4)] if len(text) == 6 else [0.53, 0.53, 0.53]
    except ValueError:
        return [0.53, 0.53, 0.53]


def tubes(a, b, r0, r1, sides, wraps=None):
    """Tapered tubes from `a` to `b` (rows of points), `r0` to `r1` round, with `sides` faces, built as the viewer
    builds a tree's limbs: (positions, normals, uv, faces), uv going round `wraps` times and along in metres."""
    a, b = np.asarray(a, float).reshape(-1, 3), np.asarray(b, float).reshape(-1, 3)
    n = len(a)
    r0, r1 = np.broadcast_to(r0, (n,)).astype(float), np.broadcast_to(r1, (n,)).astype(float)
    axis = b - a
    length = np.linalg.norm(axis, axis=1)
    ax = axis / np.maximum(length, 1e-12)[:, None]
    up = np.where((np.abs(ax[:, 1]) > 0.9)[:, None], (1.0, 0.0, 0.0), (0.0, 1.0, 0.0))
    u = np.cross(ax, up)
    u /= np.maximum(np.linalg.norm(u, axis=1), 1e-12)[:, None]
    w = np.cross(ax, u)
    w /= np.maximum(np.linalg.norm(w, axis=1), 1e-12)[:, None]
    t = np.arange(sides + 1) / sides * 2 * math.pi
    d = np.cos(t)[None, :, None] * u[:, None, :] + np.sin(t)[None, :, None] * w[:, None, :]      # (n, sides + 1, 3)
    pos = np.stack([a[:, None, :] + d * r0[:, None, None], b[:, None, :] + d * r1[:, None, None]], axis=2)
    nrm = np.stack([d, d], axis=2)
    wraps = np.ones(n) if wraps is None else np.asarray(wraps, float)
    uu = (np.arange(sides + 1) / sides)[None, :] * wraps[:, None]
    uv = np.stack([np.stack([uu, np.zeros_like(uu)], -1), np.stack([uu, np.broadcast_to(length[:, None], uu.shape)], -1)],
                  axis=2)
    base = (np.arange(n) * 2 * (sides + 1))[:, None]
    i0 = base + 2 * np.arange(sides)[None, :]
    faces = np.stack([i0, i0 + 2, i0 + 1, i0 + 1, i0 + 2, i0 + 3], axis=-1).reshape(-1, 3)
    return pos.reshape(-1, 3), nrm.reshape(-1, 3), uv.reshape(-1, 2), faces


def pipe_part(layer):
    """A drain at its depth: a tube along its path, as the viewer draws it under the see-through ground."""
    path = np.asarray(layer.get("path", []), float).reshape(-1, 3)
    part = _part(layer)
    if len(path) < 2:
        return part
    v, n, _, f = tubes(path[:-1], path[1:], layer.get("radius", 0.05), layer.get("radius", 0.05), 10)
    part.pieces.append(Piece(v, f, Look(colour=layer.get("colour") or "#2f2f2f", roughness=0.55), n))
    return part


def _euler_yxz(x, y, z):
    """Rotation matrices (n by 3 by 3) from three.js Euler angles in the order YXZ: Ry(y) Rx(x) Rz(z)."""
    a, b, c, d, e, f = np.cos(x), np.sin(x), np.cos(y), np.sin(y), np.cos(z), np.sin(z)
    return np.stack([np.stack([c * e + d * f * b, d * e * b - c * f, a * d], -1),
                     np.stack([a * f, a * e, -b], -1),
                     np.stack([c * f * b - d * e, d * f + c * e * b, a * c], -1)], axis=1)


# A leaf card is three.js's 1 m square (PlaneGeometry), its corners in the order it makes them: top left, top right,
# bottom left, bottom right; and its two triangles.
CARD = np.array([[-0.5, 0.5, 0.0], [0.5, 0.5, 0.0], [-0.5, -0.5, 0.0], [0.5, -0.5, 0.0]])
CARD_UV = np.array([[0.0, 1.0], [1.0, 1.0], [0.0, 0.0], [1.0, 0.0]])
CARD_FACES = np.array([[0, 2, 1], [2, 3, 1]])


def cards_pieces(layer):
    """A vegetation layer's leaf cards, by plant: {plant index: Piece}. Each card is a square of the atlas's sprite,
    placed, turned, sized and tinted as the viewer places its instances. Its normal leans out of the crown, as the
    viewer lights a crown as one mass, and its front faces the way that normal does."""
    c = layer["cards"]
    pos = np.frombuffer(base64.b64decode(c["positions"]), "<i2").reshape(-1, 3).astype(float) / 100
    at = np.frombuffer(base64.b64decode(c["attributes"]), np.uint8).reshape(-1, 10)
    cols, rows = layer.get("atlas_grid", (4, 3))
    look = Look(texture=layer.get("atlas"), roughness=0.8, double_sided=True, alpha="MASK", linear=True)
    by_plant = {}
    for plant, sprite, bottom, start, count in c["runs"]:
        k = slice(start, start + count)
        p, a = pos[k], at[k].astype(float)
        if not len(p):
            continue
        rot = _euler_yxz(-a[:, 2] / 255 * math.pi / 2, a[:, 1] / 128 * math.pi, a[:, 3] / 128 * math.pi)
        corners = CARD + ((0.0, 0.5, 0.0) if bottom else (0.0, 0.0, 0.0))
        v = p[:, None, :] + (a[:, 0] / 100)[:, None, None] * np.einsum("nij,kj->nki", rot, corners)
        normal = at[k][:, 7:10].view(np.int8).astype(float) / 127
        normal /= np.maximum(np.linalg.norm(normal, axis=1), 1e-9)[:, None]
        front = rot[:, :, 2]
        back = np.einsum("ni,ni->n", front, normal) < 0
        front[back] *= -1
        nrm = 0.25 * front + 0.75 * normal
        nrm /= np.maximum(np.linalg.norm(nrm, axis=1), 1e-9)[:, None]
        col, row = sprite % cols, sprite // cols
        uv = np.column_stack([(col + CARD_UV[:, 0]) / cols, 1 - (row + 1 - CARD_UV[:, 1]) / rows])
        n = len(p)
        faces = np.where(back[:, None, None], CARD_FACES[None, :, ::-1], CARD_FACES[None, :, :])
        faces = (faces + 4 * np.arange(n)[:, None, None]).reshape(-1, 3)
        piece = Piece(v.reshape(-1, 3), faces, look, np.repeat(nrm, 4, axis=0), np.tile(uv, (n, 1)),
                      np.repeat(srgb_to_linear(a[:, 4:7] / 255), 4, axis=0))
        by_plant.setdefault(plant, []).append(piece)
    return by_plant


def limbs_pieces(layer):
    """A vegetation layer's trunks and limbs, by plant: tapered tubes of bark, tinted by each one's colour."""
    items = np.asarray(layer["limbs"]["items"], float).reshape(-1, 12)
    look = Look(texture=layer["limbs"].get("texture"), repeat=True, roughness=0.95)
    by_plant = {}
    for plant in dict.fromkeys(int(x) for x in items[:, 0]):
        it = items[items[:, 0] == plant]
        wraps = np.maximum(1, np.round(2 * math.pi * it[:, 7] / 0.3))
        v, n, uv, f = tubes(it[:, 1:4], it[:, 4:7], it[:, 7], it[:, 8], 8, wraps)
        colours = np.repeat(srgb_to_linear(it[:, 9:12] / 255), 2 * 9, axis=0)
        by_plant.setdefault(plant, []).append(Piece(v, f, look, n, uv, colours))
    return by_plant


def stump_piece(it, cells, look):
    """One stump as the viewer draws it: a bark cylinder cut square at its face, leaning as its trunk did and
    reaching 0.1 m below its base, the face showing its cell of the stumps' picture; or a root mound, a low dome."""
    sides = 20
    x, y, z = it["at"]
    r, h = it["r"], it["h"]
    P, N, U, faces = [], [], [], []
    if it.get("mound"):
        rings = 5
        for k in range(rings + 1):
            fr = k / rings
            for j in range(sides + 1):
                t = j / sides * 2 * math.pi
                c, s = math.cos(t), math.sin(t)
                P.append((x + r * fr * c, y - 0.05 + (h + 0.05) * (1 - fr * fr), z + r * fr * s))
                nv = np.array([2 * h * fr * c / r, 1.0, 2 * h * fr * s / r])
                N.append(nv / np.linalg.norm(nv))
                U.append(((it["face"] + 0.5 + 0.47 * fr * c) / cells, 0.5 - 0.47 * fr * s))
                if k > 0 and j < sides:
                    a, b = (k - 1) * (sides + 1) + j, k * (sides + 1) + j
                    faces += [(a, b + 1, b), (a, a + 1, b + 1)]
    else:
        lean = math.radians(it["lean"])
        dx, dz = it["dir"]
        ax = np.array([math.sin(lean) * dx, math.cos(lean), math.sin(lean) * dz])
        u = np.array([-dz, 0.0, dx])
        w = np.cross(ax, u)
        w /= np.linalg.norm(w)
        face = np.array([x, y + h, z])
        foot = face - ax * (h + 0.1) / math.cos(lean)
        for j in range(sides + 1):
            t = j / sides * 2 * math.pi
            d = u * math.cos(t) + w * math.sin(t)
            for o, v in ((foot, 0.0), (face, 1.0)):
                P.append(o + r * d)
                N.append(d)
                U.append((j / sides / cells, v))
        faces += [f for j in range(sides) for f in ((2 * j, 2 * j + 2, 2 * j + 1), (2 * j + 1, 2 * j + 2, 2 * j + 3))]
        c0 = len(P)
        P.append(face)
        N.append(ax)
        U.append(((it["face"] + 0.5) / cells, 0.5))
        for j in range(sides + 1):
            t = j / sides * 2 * math.pi
            c, s = math.cos(t), math.sin(t)
            P.append(face + r * (u * c + w * s))
            N.append(ax)
            U.append(((it["face"] + 0.5 + 0.47 * c) / cells, 0.5 - 0.47 * s))
            if j < sides:
                faces.append((c0, c0 + j + 1, c0 + j + 2))
    return Piece(np.asarray(P, float), np.asarray(faces, np.int64), look, np.asarray(N, float), np.asarray(U, float))


def drape_piece(d):
    """A lawn or the soil under beds: the ground's own triangles showing its picture, raised LIFT_M off them."""
    v = np.asarray(d.get("positions", []), float).reshape(-1, 3)
    f = np.asarray(d.get("indices", []), np.int64).reshape(-1, 3)
    uv = np.asarray(d.get("uv", []), float).reshape(-1, 2)
    if len(uv) != len(v):
        uv = None
    v, f, uv = finite(v, f, uv)
    if not len(f):
        return None
    texture = d.get("texture") if uv is not None else None
    look = Look(texture=texture, repeat=bool(d.get("repeat")), roughness=1.0)
    return Piece(v + (0.0, LIFT_M, 0.0), f, look, vertex_normals(v, f), uv)


def vegetation_part(layer):
    """A vegetation layer, a part for each of its plants: their leaf cards, trunks and limbs, stumps and mounds, or
    the lawn or soil drawn on the ground. A layer of one plant is drawn as that plant."""
    plants = layer.get("plants") or [{}]
    by_plant = {}
    if layer.get("cards"):
        for k, pieces in cards_pieces(layer).items():
            by_plant.setdefault(k, []).extend(pieces)
    if layer.get("limbs"):
        for k, pieces in limbs_pieces(layer).items():
            by_plant.setdefault(k, []).extend(pieces)
    if layer.get("stumps"):
        st = layer["stumps"]
        look = Look(texture=st.get("texture"), roughness=0.9)
        for it in st["items"]:
            by_plant.setdefault(it["plant"], []).append(stump_piece(it, st["cells"], look))
    if layer.get("drape"):
        piece = drape_piece(layer["drape"])
        if piece is not None:
            by_plant.setdefault(0, []).append(piece)
    part = _part(layer)
    if len(plants) == 1 and set(by_plant) <= {0}:
        part.pieces = by_plant.get(0, [])
        part.plant = plants[0].get("name")
        part.info = {**part.info, **(plants[0].get("info") or {})}
        return part
    for k in sorted(by_plant):
        plant = plants[k] if 0 <= k < len(plants) else {}
        name = plant.get("name") or f"{part.name} {k + 1}"
        part.children.append(Part(layer=layer, name=name, group=part.group, colour=part.colour,
                                  info=dict(plant.get("info") or {}), pieces=by_plant[k], plant=name))
    return part
