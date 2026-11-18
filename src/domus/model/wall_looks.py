"""A picture of each wall material, painted for a site with no photo of that kind of wall, and of the steps and the
paving a wall option lays.

Each picture is one repeat of the material, at the size domus.walls.MATERIAL_TEXTURES gives it, and
tiles both ways. They are drawn, not photographed: enough to tell a gabion from a block wall in the
model, and to compare how a scheme looks in each.
"""

import numpy as np
import scipy.ndimage as nd
from scipy.spatial import cKDTree

PPM = 256       # pixels to the metre


def _noise(shape, rng, scales):
    """Smoothed random fields summed (scales in px, weights), about -1..1, tiling both ways."""
    out = np.zeros(shape)
    for s, w in scales:
        out += w * nd.gaussian_filter(rng.normal(size=shape), s, mode="wrap") * (np.mean(s) if np.ndim(s) else s)
    return out / (np.abs(out).max() + 1e-9)


def _px(size_m):
    return int(round(size_m[1] * PPM)), int(round(size_m[0] * PPM))      # rows, columns


def _rgb(colour, shade):
    return np.clip(np.asarray(colour, float)[None, None, :] * shade[..., None], 0, 255)


def _stones(shape, rng, n, palette, gap_px, gap_colour):
    """Stones as the cells round `n` scattered points, tiling both ways: each a colour from the palette, darker
    towards its edge, with `gap_px` of joint between neighbours."""
    h, w = shape
    pts = rng.uniform(0, 1, (n, 2)) * [h, w]
    tiled = np.concatenate([pts + [dy * h, dx * w] for dy in (-1, 0, 1) for dx in (-1, 0, 1)])
    yy, xx = np.mgrid[0:h, 0:w]
    d, i = cKDTree(tiled).query(np.column_stack([yy.ravel(), xx.ravel()]), k=2)
    cell = (i[:, 0] % n).reshape(h, w)
    edge = (d[:, 1] - d[:, 0]).reshape(h, w)                 # 0 on the line between two stones
    cols = np.asarray(palette, float)[rng.integers(len(palette), size=n)] * rng.uniform(0.85, 1.12, (n, 1))
    shade = 0.84 + 0.16 * np.clip(edge / (4.0 * gap_px), 0, 1) + 0.07 * _noise(shape, rng, ((6, 1.0), (1.5, 0.7)))
    img = np.clip(cols[cell] * shade[..., None], 0, 255)
    img[edge < gap_px] = np.asarray(gap_colour, float)
    return img


def timber(size_m, rng):
    """A bay of horizontal boards behind round-edged posts, half a post at each side."""
    h, w = _px(size_m)
    board = int(round(0.2 * PPM))
    grain = _noise((h, w), rng, (((1.2, 40), 1.0), ((0.8, 8), 0.5)))
    shade = 1 + 0.10 * grain
    for r0 in range(0, h, board):                           # each board its own tone, a dark joint under it
        shade[r0:r0 + board] *= rng.uniform(0.9, 1.08)
        shade[r0:r0 + 2] *= 0.45
        shade[r0 + 2:r0 + 5] *= 1.08
    img = _rgb((152, 120, 86), shade)
    half = int(round(0.1 * PPM))                            # the post: 0.2 m wide, split between the two sides
    pgrain = 1 + 0.10 * _noise((h, w), rng, (((40, 1.0), 1.0), ((8, 0.8), 0.5)))
    x = np.arange(w)
    off = np.minimum(x, w - 1 - x) / half                   # 0 at the bay's sides (the post's middle), 1 at its edge
    post = off < 1
    round_shade = 0.78 + 0.30 * np.sqrt(np.clip(1 - off ** 2, 0, 1))
    img[:, post] = _rgb((134, 104, 74), pgrain * round_shade[None, :])[:, post]
    shadow = (off >= 1) & (off < 1.25)                      # the post's shadow on the boards
    img[:, shadow] *= 0.7
    return img


def block(size_m, rng):
    """One course of mortared concrete blocks, 0.4 m long."""
    h, w = _px(size_m)
    shade = 1 + 0.07 * _noise((h, w), rng, ((5, 1.0), (1.0, 0.8)))
    img = _rgb((170, 165, 156), shade)
    joint = np.asarray((205, 201, 192), float)
    img[:3] = joint
    for x0 in np.arange(0, w, 0.4 * PPM).round().astype(int):
        img[:, x0:x0 + 3] = joint
    return img


def stone(size_m, rng):
    """Random rubble, laid dry."""
    palette = [(126, 120, 108), (144, 136, 120), (110, 106, 98), (152, 142, 122), (132, 122, 104), (118, 116, 112)]
    return _stones(_px(size_m), rng, 46, palette, 2.5, (58, 54, 48))


def segmental(size_m, rng):
    """Split-face concrete blocks 0.3 m long in 0.2 m courses, each course half a block along and set back."""
    h, w = _px(size_m)
    course, length = int(round(0.2 * PPM)), int(round(0.3 * PPM))
    shade = 1 + 0.16 * _noise((h, w), rng, ((3, 1.0), (0.9, 1.0)))
    img = _rgb((150, 142, 130), shade)
    for k, r0 in enumerate(range(0, h, course)):
        img[r0:r0 + 4] *= 0.5                               # the shadow under the course above
        img[r0 + 4:r0 + 8] *= 1.1
        for x0 in range((length // 2) * (k % 2), w, length):
            img[r0:r0 + course, x0:x0 + 2] *= 0.55
    return img


def gabion(size_m, rng):
    """Stone-filled wire baskets, 1 m long and high, in 75 mm mesh."""
    h, w = _px(size_m)
    palette = [(134, 132, 126), (150, 146, 136), (112, 112, 110), (160, 154, 140), (124, 120, 112)]
    img = _stones((h, w), rng, 260, palette, 2.0, (40, 40, 40))
    wire = np.asarray((196, 200, 204), float)
    mesh = int(round(0.075 * PPM))
    for x0 in range(0, w, mesh):
        img[:, x0:x0 + 1] = 0.5 * img[:, x0:x0 + 1] + 0.5 * wire
    for r0 in range(0, h, mesh):
        img[r0:r0 + 1] = 0.5 * img[r0:r0 + 1] + 0.5 * wire
    for x0 in range(0, w, PPM):                             # the baskets' frames
        img[:, x0:x0 + 3] = wire
    for r0 in range(0, h, PPM):
        img[r0:r0 + 3] = wire
    return img


def crib(size_m, rng):
    """A timber crib: stretchers 0.3 m apart, the header ends showing between them over the stone fill."""
    h, w = _px(size_m)
    module, rail = int(round(0.3 * PPM)), int(round(0.12 * PPM))
    fill = _stones((h, w), rng, 220, [(96, 94, 90), (112, 108, 100), (84, 84, 84)], 2.0, (30, 30, 30)) * 0.8
    grain = 1 + 0.10 * _noise((h, w), rng, (((1.2, 40), 1.0), ((0.8, 8), 0.5)))
    timber_rgb = _rgb((148, 126, 96), grain)
    img = fill
    for k, r0 in enumerate(range(0, h, module)):
        img[r0:r0 + rail] = timber_rgb[r0:r0 + rail]        # the stretcher
        img[r0 + rail:r0 + rail + 4] *= 0.4                 # and its shadow on the fill
        for x0 in range(int(round((0.15 + 0.3 * (k % 2)) * PPM)), w, int(round(0.6 * PPM))):
            img[r0 + rail:r0 + module, x0:x0 + rail] = 0.88 * timber_rgb[r0 + rail:r0 + module, x0:x0 + rail]
    return img


def concrete(size_m, rng):
    """Cast concrete: 1.2 m formwork panels with their tie holes."""
    h, w = _px(size_m)
    shade = 1 + 0.05 * _noise((h, w), rng, ((40, 1.0), (8, 0.6), (1.0, 0.5)))
    img = _rgb((176, 173, 166), shade)
    panel = int(round(1.2 * PPM))
    for x0 in range(0, w, panel):
        img[:, x0:x0 + 2] *= 0.8
    img[:2] *= 0.8
    yy, xx = np.mgrid[0:h, 0:w]
    for cy in (0.3 * PPM, 0.9 * PPM):
        for cx in np.arange(0.3 * PPM, w, 0.6 * PPM):
            img[np.hypot(yy - cy, xx - cx) < 4] *= 0.6
    return img


def _hex(colour, default):
    """`colour` ("#rrggbb") as three numbers, or `default`."""
    if not colour:
        return np.asarray(default, float)
    c = colour.lstrip("#")
    return np.asarray([int(c[i:i + 2], 16) for i in (0, 2, 4)], float)


def units(length_m, height_m, cap_m, colour, blocks, courses, top_m, rng):
    """A wall of split-faced blocks `length_m` by `height_m`, laid dry in running bond. From the top of the picture:
    a capping course `cap_m` high if it has one, `courses` courses of blocks, and `top_m` of the cap's smooth top
    (domus.walls lays the picture so the wall's top face takes that). `blocks` blocks across, each its own tone."""
    w = int(round(blocks * length_m * PPM))
    h = int(round((cap_m + courses * height_m + top_m) * PPM))
    top = int(round(top_m * PPM))
    length = length_m * PPM                                        # a block, in pixels (not whole ones)
    base = _hex(colour, (150, 142, 130))
    # A split face: broken stone, lit from above, with the aggregate showing as specks.
    relief = _noise((h, w), rng, ((7, 1.0), (3, 0.8), (1.2, 0.6)))
    lit = np.gradient(relief, axis=0)
    lit = lit / (np.abs(lit).max() + 1e-9)
    shade = 1 + 0.10 * relief - 0.34 * lit
    speck = rng.random((h, w))
    shade[speck < 0.03] *= 0.74
    shade[speck > 0.985] *= 1.16
    x = np.arange(w)
    edges = [int(round((cap_m + k * height_m) * PPM)) for k in range(courses + 1)]
    rows = [(0, edges[0], 0.25, 1.05)] if cap_m else []            # the cap's joints fall a quarter along the blocks'
    rows += [(edges[k], edges[k + 1], 0.5 * (k % 2), 1.0) for k in range(courses)]
    edge = max(2, int(round(0.014 * PPM)))                         # the arris of a split face, worn round
    for r0, r1, offset, lift in rows:
        along = (x - offset * length) % length
        tone = rng.uniform(0.93, 1.07, blocks + 1)[((x - offset * length) // length).astype(int) % (blocks + 1)] * lift
        dx = np.minimum(along, length - along)[None, :]
        dy = np.minimum(np.arange(r1 - r0), r1 - r0 - 1 - np.arange(r1 - r0))[:, None]
        round_off = 0.80 + 0.20 * np.clip(np.minimum(dx, dy) / edge, 0, 1)
        shade[r0:r1] *= tone[None, :] * round_off
        shade[r0:r0 + 2] *= 0.50                                   # the bed joint, in the shadow of the course above
        shade[r0:r1, along < 2] *= 0.55                            # and the joint between two blocks
    img = _rgb(base, shade)
    if top:                                                        # the cap's top: cast smooth, with the same joints
        t0 = h - top
        smooth = 1.08 + 0.04 * _noise((top, w), rng, ((12, 1.0), (1.0, 0.6)))
        smooth[:, ((x - 0.25 * length) % length) < 2] *= 0.6
        img[t0:] = _rgb(base, smooth)
    return img


def sleepers(size_m, rng):
    """One weathered timber sleeper seen along its length: the grain runs along it, with a few checks, and its two
    edges are worn dark, so sleepers laid side by side or stacked show the joint between them."""
    h, w = _px(size_m)
    grain = _noise((h, w), rng, (((1.0, 60), 1.0), ((0.7, 14), 0.6), ((0.5, 3), 0.25)))
    grey = 0.5 + 0.5 * _noise((h, w), rng, (((6, 90), 1.0),))      # where it has weathered silver
    shade = 1 + 0.16 * grain
    for _ in range(7):                                             # checks along the grain
        r, x0, n = rng.integers(3, h - 3), rng.integers(0, w), rng.integers(w // 8, w // 3)
        cols = (x0 + np.arange(n)) % w
        shade[np.clip(r + np.round(1.2 * np.sin(np.arange(n) / 37.0)).astype(int), 0, h - 1), cols] *= 0.55
    img = _rgb((112, 90, 68), shade) * (1 - 0.35 * grey[..., None]) + _rgb((128, 124, 116), shade) * (0.35 * grey[..., None])
    img[:2] *= 1.12
    img[2:4] *= 1.04
    img[-3:] *= 0.45
    return img


PAVER_M = (0.2, 0.1)    # a paver's length and width
PAVER_RGB = (185, 178, 165)    # its colour on average: the colour the viewer sketches a path or paving in


def pavers(size_m, rng):
    """Concrete pavers seen from above, laid in stretcher bond: rows of pavers 0.2 m long and 0.1 m wide, each row
    half a paver along from the one before, with sanded joints. Each paver has its own tone, a little darker at its
    edges where they're bevelled."""
    h, w = _px(size_m)
    rows = np.arange(h) * size_m[1] / h / PAVER_M[1]                     # in paver widths down the picture
    row = np.floor(rows).astype(int)
    along = np.arange(w)[None, :] * size_m[0] / w / PAVER_M[0] + 0.5 * (row[:, None] % 2)     # in paver lengths
    paver = np.floor(along).astype(int)
    count = int(round(size_m[0] / PAVER_M[0])) + 1
    tone = rng.uniform(0.9, 1.08, (int(round(size_m[1] / PAVER_M[1])) + 1, count))
    tone[:, -1] = tone[:, 0]                       # the half paver at the right edge is the left edge's other half
    shade = tone[row[:, None] % tone.shape[0], paver % count] * (1 + 0.05 * _noise((h, w), rng, ((3, 1.0), (0.8, 0.8))))
    # how far each pixel is from its paver's edge, in metres: darker within a centimetre of it (the bevel), sand in the joint
    edge = np.minimum(np.minimum(along % 1, 1 - along % 1) * PAVER_M[0],
                      np.minimum(rows % 1, 1 - rows % 1)[:, None] * PAVER_M[1])
    shade = shade * (0.82 + 0.18 * np.clip(edge / 0.01, 0, 1))
    img = _rgb(PAVER_RGB, shade / shade.mean())
    img[edge < 0.002] = np.asarray((132, 122, 104), float)
    return img


PAINTERS = {"timber": timber, "block": block, "stone": stone, "segmental": segmental, "gabion": gabion,
            "crib": crib, "concrete": concrete, "sleepers": sleepers, "pavers": pavers}


def paint(material, path, size_m, seed=23):
    """Write the material's picture, `size_m` (along the wall, down its face) across, to `path`."""
    from PIL import Image

    img = PAINTERS[material](size_m, np.random.default_rng(seed))
    Image.fromarray(np.clip(img, 0, 255).astype(np.uint8)).save(path, quality=88)
    return path


def paint_units(path, length_m, height_m, cap_m, colour, blocks, courses, top_m, seed=23):
    """Write the picture of a wall of named blocks (units) to `path`."""
    from PIL import Image

    img = units(length_m, height_m, cap_m, colour, blocks, courses, top_m, np.random.default_rng(seed))
    Image.fromarray(np.clip(img, 0, 255).astype(np.uint8)).save(path, quality=90)
    return path
