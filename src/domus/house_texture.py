"""Textures for the house: an atlas holding every wall-like face and a palette of solid
colours, and a repeating roof-tile texture.

Each face (domus.house.Face) is drawn at PPM pixels per metre: weatherboards above a
plastered base, with its windows and doors (data/site/house.geojson), in the colours sampled
from the site photos. Where a rectified photo or video texture exists in
data/house/ (tools/house_textures.py), it is laid over the drawing, so the drawing only
shows where no photo covers the face (behind the car, the far side of the deck, ...).
"""

import json
import math
import zlib
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from .errors import DomusError

PPM = 80                     # atlas pixels per metre of wall
ATLAS_W = 2048
PAD = 4

# Colours of a house. Hex RGB.
PALETTE = {
    "boards-grey": "#cbd2da",   # pale grey-blue boards
    "boards-blue": "#a9c4d9",   # pale blue boards
    "boards-beige": "#c9b6a8",  # beige boards
    "trim-yellow": "#e2c48e",  # window facings and bands
    "trim-tan": "#c8b29a",      # skirt boards
    "trim-white": "#eef1f1",    # joinery, fascia, corner boards, gutters
    "soffit": "#e2e6e8",
    "plaster": "#dcd9d3",  # textured plaster base
    "glass": "#3c4652",
    "roof": "#46474c",  # dark charcoal concrete tiles, lighter weathered tops
    "step-grey": "#8e959a",  # grey-painted concrete steps
    "concrete": "#aaa69e",  # bare concrete
    "gravel": "#86817b",  # gravel
    "timber-grey": "#8f8a83",  # weathered timber
    "deck": "#76503f",  # stained deck boards
    "deck-dark": "#5f3d30",  # deck slats, posts and skirt
    "chimney": "#8b8d8c",
    "metal": "#c3c7ca",
    "tank": "#c6b9a2",  # a beige rainwater tank
    "unit-grey": "#d3d5d3",  # an outdoor unit, such as a heat pump's
}

CLADDINGS = ("boards", "plaster", "brick", "panel")      # how a wall's cladding is drawn: `brick:#9c4a3a`
ROOFS = ("tiles", "metal", "membrane")                    # how the roof is drawn: [house] roof


class Looks:
    """How a site's house looks: its settings (`house`, [house] in data/site.toml, as read) and the folder of its
    rectified photos (`photos`, data/house/). The model's drawing takes one from the build, and reads no site itself."""

    def __init__(self, house=None, photos=None):
        self.house = house if house is not None else {}
        self.photos = Path(photos) if photos else None

    def colours(self):
        """The palette and the site's own colours ([house.palette]), which can add to it or change it."""
        return {**PALETTE, **self.house.get("palette", {})}

    def rgb(self, key):
        return rgb(key, self.colours())

    # Which cladding each wall shows where no photo covers it, from [house.cladding]: face = [above the floor line,
    # below it], or [[from u, above, below], ...] along the face; `default` for the rest.
    def cladding(self, face_id):
        given = self.house.get("cladding", {})
        zones = given.get(face_id, given.get("default", ["boards-beige", "boards-beige"]))
        return [tuple(z) for z in zones] if isinstance(zones[0], list) else [(0.0, *zones)]

    def trim(self, face_id):
        """The colour of the band at the floor line and of the facings round the windows below it, on the faces
        [house.trim] lists, or None."""
        t = self.house.get("trim", {})
        return t.get("colour") if face_id in t.get("faces", []) else None

    def boards(self):
        """The weatherboards' exposure, lap to lap in metres, and whether their laps are drawn over the faces' photos
        too: [house] board_m and boards_on_photos."""
        return float(self.house.get("board_m", 0.15)), bool(self.house.get("boards_on_photos", False))

    def roof_kind(self):
        """How the roof is drawn: [house] roof, concrete tiles unless given."""
        kind = self.house.get("roof", "tiles")
        if kind not in ROOFS:
            raise DomusError(f"[house] roof = {kind!r}: give one of {', '.join(ROOFS)}")
        return kind

    def roof_colour(self):
        """The roof's colour: [house] roof_colour (a palette key, one of the site's own or hex), else dark charcoal."""
        return self.house.get("roof_colour", "roof")


def material(zone):
    """A cladding as [house.cladding] gives it: (how it's drawn, its colour). A colour alone is weatherboards in it,
    and `plaster:<colour>`, `brick:<colour>` or `panel:<colour>` names another cladding."""
    kind, _, colour = zone.partition(":")
    if colour and kind in CLADDINGS:
        return kind, colour
    return "boards", zone


def lap_shade(zc, datum, board, ppm):
    """How bright each pixel row of lapped boards is, for rows at heights `zc`: the boards are counted up from
    `datum`, each darker towards its bottom edge, with a shadow line under each lap that is at least a pixel thick."""
    phase = ((zc - datum) % board) / board                 # 0 at a board's bottom edge, 1 at its top
    return np.where(phase < max(0.06, 1.2 / (board * ppm)), 0.62, 0.86 + 0.14 * phase)


def rgb(key, named=None):
    """A colour by its name among `named` (the palette unless given), or given as hex ("#rrggbb"); a cladding such
    as `brick:#9c4a3a` gives its colour's."""
    key = material(key)[1]
    if not key.startswith("#"):
        named = PALETTE if named is None else named
        if key not in named:
            raise DomusError(f"{key!r} isn't a colour Domus knows: name one of {', '.join(sorted(named))}, add it to "
                             "[house.palette] in data/site.toml, or give it as #rrggbb")
        key = named[key]
    h = key.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def cladding_shade(kind, zc, u, datum, board, ppm, rng):
    """How bright each pixel of a cladding is, rows at heights `zc` and columns at `u` along the face: lapped
    weatherboards, plaster, brick courses in mortar, or sheet panels with their joints."""
    if kind == "boards":
        return np.broadcast_to(lap_shade(zc, datum, board, ppm)[:, None], (len(zc), len(u)))
    if kind == "plaster":
        return 0.95 + rng.normal(0, 0.025, (len(zc), len(u)))
    if kind == "brick":
        joint = max(0.010, 1.2 / ppm)                           # 10 mm, and never thinner than a pixel
        course = np.floor((zc - datum) / 0.086)                 # 76 mm bricks and their joints
        bed = ((zc - datum) % 0.086) < joint
        shift = (course % 2)[:, None] * 0.115                  # stretcher bond, half a brick on
        head = ((u[None, :] + shift) % 0.230) < joint
        tone = 0.9 + 0.12 * np.sin(course[:, None] * 12.9898 + np.floor((u[None, :] + shift) / 0.230) * 78.233) ** 2
        return np.where(bed[:, None] | head, 1.25, tone)
    joint = (u % 1.2) < 0.012                                    # panels 1.2 m wide
    return np.where(joint[None, :], 0.7, 0.97) * np.ones((len(zc), 1))


def shade(c, f):
    return tuple(int(max(0, min(255, v * f))) for v in c)


# --------------------------------------------------------------------------- drawn faces

def draw_face(width, z0, z1, spec, face_id, looks, ppm=PPM, seed=0):
    """Weatherboards, plaster base, joinery: an image of the face, u to the right, height up, and where on it the
    boards show (an array, True on the boards and False on the plaster, the joinery and the trims)."""
    W, H = max(int(math.ceil(width * ppm)), 1), max(int(math.ceil((z1 - z0) * ppm)), 1)
    rng = np.random.default_rng(seed)
    zones = looks.cladding(face_id)
    band = looks.trim(face_id)
    rgb = looks.rgb
    fl = spec.get("fl", z0 + 2.5)
    plinth = spec.get("plinth_top")
    row = lambda z: (z1 - z) * ppm
    img = np.zeros((H, W, 3), np.float32)
    zc = z1 - (np.arange(H) + 0.5) / ppm                   # height of each pixel row
    datum, board = board_datum(spec, z0), looks.boards()[0]
    lap = lap_shade(zc, datum, board, ppm)
    clad = Image.new("L", (W, H), 255)
    cd = ImageDraw.Draw(clad)
    above = (zc >= fl - 0.05)[:, None, None]
    for k, (u_from, up, low) in enumerate(zones):
        c0 = int(round(u_from * ppm))
        c1 = int(round(zones[k + 1][0] * ppm)) if k + 1 < len(zones) else W
        colours = np.where(above, np.asarray(rgb(up), np.float32), np.asarray(rgb(low), np.float32))
        kinds = (material(up)[0], material(low)[0])
        if kinds == ("boards", "boards"):
            img[:, c0:c1, :] = colours * lap[:, None, None]
            continue
        u = (np.arange(c0, c1) + 0.5) / ppm
        shades = [cladding_shade(kind, zc, u, datum, board, ppm, rng) for kind in kinds]
        img[:, c0:c1, :] = colours * np.where(above[:, :, 0], shades[0], shades[1])[:, :, None]
        for kind, rows in zip(kinds, (zc >= fl - 0.05, zc < fl - 0.05), strict=True):
            if kind != "boards" and rows.any():                 # only weatherboards take laps over a photo
                r = np.flatnonzero(rows)
                cd.rectangle([c0, int(r[0]), c1, int(r[-1]) + 1], fill=0)
    if plinth is not None and plinth > z0:
        r = int(round(row(plinth)))
        noise = rng.normal(0, 6, (max(H - r, 0), W, 1))
        img[r:, :, :] = np.asarray(rgb("plaster"), np.float32) + noise
        cd.rectangle([0, r, W, H], fill=0)
    if spec.get("kind") == "wall" and fl - 0.15 > z0:      # floor band where the two storeys meet
        r0, r1 = int(round(row(fl + 0.02))), int(round(row(fl - 0.12)))
        if band:
            img[r0:r1, :, :] = rgb(band)
            cd.rectangle([0, r0 - 0.03 * ppm, W, r1 + 0.03 * ppm], fill=0)
    out = Image.fromarray(np.clip(img, 0, 255).astype(np.uint8))
    d = ImageDraw.Draw(out)
    white, glass = rgb("trim-white"), rgb("glass")
    for o in spec.get("openings", []):
        a, b = o["u0"] * ppm, o["u1"] * ppm
        top, bot = row(o["head"]), row(o["sill"])
        frame = 0.07 * ppm
        if o["sill"] < fl - 0.3 and band:                  # windows below the floor line: facings in the trim colour
            d.rectangle([a - 0.05 * ppm, top - 0.05 * ppm, b + 0.05 * ppm, bot + 0.04 * ppm], fill=rgb(band))
        d.rectangle([a, top, b, bot], fill=white)
        cd.rectangle([a - 0.15 * ppm, top - 0.15 * ppm, b + 0.15 * ppm, bot + 0.15 * ppm], fill=0)   # joinery and facings
        width_m = o["u1"] - o["u0"]
        panes = 1 if width_m < 0.75 else 2 if width_m < 1.5 else 3
        if o["kind"] == "door" and width_m > 1.2:
            panes = 2
        span = (b - a - frame) / panes
        for k in range(panes):
            x0 = a + frame + k * span
            x1 = x0 + span - frame
            y0, y1 = top + frame, bot - frame
            if o["kind"] == "door":
                y0, y1 = top + frame, bot - 2.5 * frame
            if x1 - x0 < 2 or y1 - y0 < 2:
                continue
            d.rectangle([x0, y0, x1, y1], fill=glass)
            # a soft sky reflection in the upper part of each pane
            d.rectangle([x0, y0, x1, y0 + (y1 - y0) * 0.35], fill=shade(glass, 1.35))
        d.rectangle([a - 0.03 * ppm, bot, b + 0.03 * ppm, bot + 0.04 * ppm], fill=white)  # sill
    if spec.get("kind") in ("wall", "porch"):               # corner boards
        cb = max(int(0.09 * ppm), 1)
        top_r = 0
        bot_r = int(round(row(plinth))) if plinth is not None and plinth > z0 else H
        d.rectangle([0, top_r, cb, bot_r], fill=white)
        d.rectangle([W - 1 - cb, top_r, W - 1, bot_r], fill=white)
        cd.rectangle([0, 0, 1.5 * cb, H], fill=0)
        cd.rectangle([W - 1 - 1.5 * cb, 0, W, H], fill=0)
    return out.filter(ImageFilter.SMOOTH), np.asarray(clad) > 127


def photo_layer(group, width, z0, z1, photos, ppm=PPM):
    """A rectified photo texture for a face group from the folder `photos` (data/house/), resampled onto the face's
    raster (W x H at ppm), and its mask (255 where the photo covers the face), or None."""
    index = photos / "faces.json" if photos else None
    if index is None or not index.exists():
        return None
    rec = json.loads(index.read_text(encoding="utf-8")).get(group)
    if not rec:
        return None
    src = Image.open(photos / rec["file"]).convert("RGB")
    mask = Image.open(photos / rec["mask"]).convert("L") if rec.get("mask") else \
        Image.new("L", src.size, 255)
    W, H = max(int(math.ceil(width * ppm)), 1), max(int(math.ceil((z1 - z0) * ppm)), 1)
    s = rec["ppm"]
    # affine from the face raster (col, row) to the photo raster: u = col/ppm, z = z1 - row/ppm
    a = s / ppm
    c = -rec["u0"] * s
    f = (rec["z1"] - z1) * s
    tf = (a, 0, c, 0, a, f)
    img = src.transform((W, H), Image.AFFINE, tf, resample=Image.BILINEAR)
    m = mask.transform((W, H), Image.AFFINE, tf, resample=Image.BILINEAR, fillcolor=0)
    return img, m


def board_datum(spec, z0):
    """The height the boards are counted up from: the top of the plastered base, where the lowest board sits, or
    the floor line on a face without one."""
    plinth = spec.get("plinth_top")
    return plinth if plinth is not None and plinth > z0 else spec.get("fl", z0 + 2.5)


def boards_on_photo(img, clad, z0, z1, spec, ppm, board):
    """A face's photo with the boards drawn on it. Where the boards show (`clad`), the photo's own lap lines are
    smoothed away up the wall, a board high, and the laps are drawn in their place at the site's board size, counted
    up from the plinth. The photo keeps its colours, its light and everything that is not boards."""
    a = np.asarray(img, np.float32)
    H = a.shape[0]
    k = max(int(round(board * ppm)), 1)
    w = clad.astype(np.float32)[..., None]
    pad = [(k // 2 + 1, k - k // 2), (0, 0), (0, 0)]       # a running mean over k rows, of board pixels only
    num = np.cumsum(np.pad(a * w, pad), axis=0)
    den = np.cumsum(np.pad(w, pad), axis=0)
    mean = (num[k:k + H] - num[:H]) / np.maximum(den[k:k + H] - den[:H], 1e-6)
    lap = lap_shade(z1 - (np.arange(H) + 0.5) / ppm, board_datum(spec, z0), board, ppm)
    lap = lap / lap.mean()                                 # as bright overall as the photo was
    out = np.where(clad[..., None], mean * lap[:, None, None], a)
    return Image.fromarray(np.clip(out, 0, 255).astype(np.uint8))


def face_image(group, width, z0, z1, spec, looks, ppm=PPM):
    drawn, clad = draw_face(width, z0, z1, spec, group, looks, ppm, seed=zlib.crc32(group.encode()))
    photo = photo_layer(group, width, z0, z1, looks.photos, ppm)
    if photo is None:
        return drawn, False
    img, m = photo
    board, on_photos = looks.boards()
    if on_photos and spec.get("kind") == "wall":           # a wall's joinery is known; a bay's or a porch's is in its photo
        img = boards_on_photo(img, clad & (np.asarray(m) > 200), z0, z1, spec, ppm, board)
    m = m.filter(ImageFilter.GaussianBlur(1.5))
    return Image.composite(img, drawn, m), True


# --------------------------------------------------------------------------- atlas

def build_atlas(faces, out_path, looks, ppm=PPM, extra=()):
    """Pack every face group and the palette (with any `extra` hex colours) into one image; set the faces' UVs
    into it. Returns (colour -> uv lookup, list of groups that carry a photo)."""
    groups = {}
    for f in faces:
        key = f.spec.get("group", f.id)
        g = groups.setdefault(key, {"faces": [], "width": 0.0, "z0": f.z0, "z1": f.z1, "spec": dict(f.spec)})
        g["faces"].append(f)
        g["width"] = max(g["width"], f.spec.get("u0", 0.0) + f.width)
        g["z0"], g["z1"] = min(g["z0"], f.z0), max(g["z1"], f.z1)
    images = {}
    photos = []
    for key, g in groups.items():
        img, has_photo = face_image(key, g["width"], g["z0"], g["z1"], g["spec"], looks, ppm)
        images[key] = img
        if has_photo:
            photos.append(key)
    # shelf packing, tallest first
    order = sorted(images, key=lambda k: -images[k].height)
    x = y = shelf = 0
    slots = {}
    for k in order:
        w, h = images[k].width + 2 * PAD, images[k].height + 2 * PAD
        if x + w > ATLAS_W:
            x, y, shelf = 0, y + shelf, 0
        slots[k] = (x, y)
        x += w
        shelf = max(shelf, h)
    sw = 64
    colours = list(dict.fromkeys([*PALETTE, *extra]))       # the palette, and colours the site gives as hex
    pal_y = y + shelf
    H = pal_y + sw * int(math.ceil(len(colours) * sw / ATLAS_W))
    atlas = Image.new("RGB", (ATLAS_W, H), (128, 128, 128))
    for k, (sx, sy) in slots.items():
        img = images[k]
        padded = Image.new("RGB", (img.width + 2 * PAD, img.height + 2 * PAD))
        padded.paste(img.resize((img.width + 2 * PAD, img.height + 2 * PAD)), (0, 0))   # edge colours in the pad
        padded.paste(img, (PAD, PAD))
        atlas.paste(padded, (sx, sy))
    pal_uv = {}
    for i, key in enumerate(colours):
        px, py = (i * sw) % ATLAS_W, pal_y + (i * sw) // ATLAS_W * sw
        atlas.paste(Image.new("RGB", (sw, sw), looks.rgb(key)), (px, py))
        pal_uv[key] = ((px + sw / 2) / ATLAS_W, 1 - (py + sw / 2) / H)
    for key, g in groups.items():
        sx, sy = slots[key]
        for f in g["faces"]:
            u, z = f.mesh.uv[:, 0], f.mesh.uv[:, 1]
            col = sx + PAD + u * ppm
            row = sy + PAD + (g["z1"] - z) * ppm
            f.mesh.uv = np.column_stack([col / ATLAS_W, 1 - row / H])
    atlas.save(out_path, quality=86)
    return pal_uv, sorted(photos)


def roof_texture(out_path, looks, size=512, metres=2.0):
    """The roof's repeating texture, as [house] roof names it: returns the metres it covers."""
    kind = looks.roof_kind()
    if kind == "tiles":
        return roof_tiles(out_path, looks, size, metres)
    rng = np.random.default_rng(7)
    ppm = size / metres
    base = np.asarray(looks.rgb(looks.roof_colour()), np.float32)
    _, xx = np.mgrid[0:size, 0:size] / ppm
    if kind == "metal":                                     # long-run iron: a rib every 0.2 m down the slope
        across = (xx % 0.2) / 0.2
        light = 0.82 + 0.25 * np.clip(1 - np.abs(across - 0.1) / 0.05, 0, 1) - 0.12 * (np.abs(across - 0.14) < 0.02)
    else:                                                    # a membrane: sheets 1 m wide, their seams raised
        light = np.where((xx % 1.0) < 0.02, 1.15, 0.95)
    img = base * light[..., None] + rng.normal(0, 3, (size, size, 1))
    im = Image.fromarray(np.clip(img, 0, 255).astype(np.uint8)[::-1])
    im.filter(ImageFilter.SMOOTH).save(out_path, quality=86)
    return metres


def roof_tiles(out_path, looks, size=512, metres=2.0):
    """Repeating concrete-tile texture covering `metres` x `metres` of roof: six courses up the
    slope and six tiles across (about 0.33 m each, so it tiles seamlessly), laid in straight
    columns as on the roof, with weathering."""
    rng = np.random.default_rng(7)
    ppm = size / metres
    base = np.asarray(looks.rgb(looks.roof_colour()), np.float32)
    gauge = wid = metres / 6
    yy, xx = np.mgrid[0:size, 0:size] / ppm
    course = np.floor(yy / gauge)
    t = (yy % gauge) / gauge                     # 0 at a course's lower (overlapping) edge
    across = (xx % wid) / wid
    profile = 0.78 + 0.3 * np.sin(np.pi * across) ** 0.6       # rounded tile profile across
    lap = np.where(t < 0.08, 0.55, 0.85 + 0.2 * t)             # shadow under the course above
    tone = 1 + rng.normal(0, 0.05, (6, 6))
    ci = course.astype(int) % 6
    ti = np.floor(xx / wid).astype(int) % 6
    img = base * (profile * lap * tone[ci, ti])[..., None]
    img += rng.normal(0, 5, img.shape)
    im = Image.fromarray(np.clip(img, 0, 255).astype(np.uint8)[::-1])   # v grows up the slope
    im.filter(ImageFilter.SMOOTH).save(out_path, quality=86)
    return metres
