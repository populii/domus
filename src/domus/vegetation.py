"""Vegetation: trees, shrubs, hedges, planted beds, the lawns and stumps.

Mapping (tools/segment_canopy.py). The LiDAR surface model less the ground model (with the
curated fixes) is the canopy height. Cells count as vegetation where the canopy stands above
0.5 m and there are living leaves: NDVI from LINZ's near-infrared photo where it has one, else
the aerial photo green there (or dark and not grey: foliage in shade). That leaves out roofs,
cars and paving; colour alone takes a green roof for a tree; the house outline is masked as well, and so is canopy
gone since the survey (removed_areas). The canopy is cut into crowns by a watershed from its
peaks, and neighbouring pieces whose shared edge is nearly as high as the lower top, or whose
tops are close together, are merged (segment). Each crown becomes a candidate with its outline,
top and colour (data/sources/canopy.geojson); data/site/trees.geojson holds the curated
features, named and checked against the photos and videos, and only they are drawn.

Curated features (data/site/trees.geojson). `kind`:
- tree, shrub, hedge (Polygon: the crown's outline). `top: lidar` takes the top from the
  surface model, capped at `height_m` + 0.5; otherwise the top is `height_m` above the ground.
  Trees: `crown_base_m`, `trunks` (E, N points; else the crown's highest point),
  `trunk_diameter_m`, `stems` (more than one: a low fork). `density` (0-1) thins the foliage into
  clumps with gaps. `look`: broadleaf, small-leaf, glossy or cherry (the leaf sprite); `colour`
  (from the photos; without it, from the aerial), `bark`, `flowers`.
- bed (Polygon): planting drawn as clumps; `look` strap (agapanthus), flax, ivy, fern, border,
  rosette (aeonium), weeds, long-grass; `height_m`, `colour`, `flowers`.
- lawn (Polygon): drawn as a texture on the ground's triangles; `colour`. patch (Polygon) on a
  lawn: `look` sand (painted into the lawn's texture) or weeds (a clump on the ground).
- stump (Point): `radius_m`, `height_m` (to the cut face's centre), `base_m` (else the ground,
  and never below it), `face` (STUMP_FACES), `lean_deg` and `lean_toward_deg` (degrees from
  north) for a leaning stem cut square, `shape: mound` for a grubbed-out root mound,
  `felled_since_lidar` with `crown_centre` and `crown_radius_m` (its crown is cut out).
- removed (Polygon): canopy gone since the survey (cut out, not drawn).
Every feature carries `name`, `source`, `basis` where it needs one, and a short `confidence`.

Drawing (domus.build). Every tree, shrub and hedge fills the space between its top and an
underside: the crown base for a tree, near the ground for a shrub or hedge. Foliage is drawn as
leaf cards (small textured squares), dense in a shell under the top and round the sides, their
colour the plant's own varied with the aerial's light and dark, darker deeper in, and lit with a
normal pointing out of the crown. Trunks and branches are tapered tubes, stumps have their cut
face on top, and the lawns and the soil under the beds are drapes on the ground's triangles.
"""

import heapq
import math
import zlib

import numpy as np
import scipy.ndimage as nd
import shapely
from shapely.geometry import MultiPolygon, Polygon

from . import geojson, raster, terrain
from .crs import ROOT, settings

TREES = ROOT / "data" / "site" / "trees.geojson"


def LIDAR_NAME():
    return settings().get("labels", {}).get("lidar", "LiDAR")
STEP = 0.25                 # m, mapping grid
NDVI_MIN = 0.2              # living leaves, against roofs and paving (LINZ's 2025 urban photos: about 0.4 against 0.04)

# --------------------------------------------------------------------------- mapping


def grid(extent, step=STEP):
    """Cell centres of a site-aligned grid over `extent`, rows running north to south."""
    e0, n0, e1, n1 = extent
    w = int(round((e1 - e0) / step))
    h = int(round((n1 - n0) / step))
    return np.meshgrid(e0 + (np.arange(w) + 0.5) * step, n1 - (np.arange(h) + 0.5) * step)


def canopy_height(E, N, ground=None, surface=None):
    """LiDAR surface less the ground at the grid points (NaN outside the clip set to 0)."""
    g = (ground or terrain.ground()).sample(E, N)
    s = (surface or raster.dsm()).sample(E, N)
    return np.nan_to_num(s - g, nan=0.0)


def aerial_rgb(E, N, step=STEP, aerial=None):
    """Mean aerial colour over each cell (3 x 3 samples), float RGB 0-255."""
    a = aerial or raster.aerial()
    acc = np.zeros(E.shape + (3,))
    for dx in (-step / 3, 0.0, step / 3):
        for dy in (-step / 3, 0.0, step / 3):
            acc += np.stack([a.sample(E + dx, N + dy, band=b) for b in range(3)], -1)
    return np.nan_to_num(acc / 9, nan=128.0)


def aerial_ndvi(E, N, step=STEP):
    """Mean NDVI, (NIR - red) / (NIR + red), over each cell from the near-infrared photo; NaN where there is none.
    Leaves about 0.3-0.5; roofs, paint, paving and bare ground below 0.1, green or not."""
    if not raster.NIR.exists():
        return np.full(E.shape, np.nan)
    a = raster.nir()
    red, nir = np.zeros(E.shape), np.zeros(E.shape)
    for dx in (-step / 3, 0.0, step / 3):
        for dy in (-step / 3, 0.0, step / 3):
            red += a.sample(E + dx, N + dy, band=0)
            nir += a.sample(E + dx, N + dy, band=1)
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(red + nir > 0, (nir - red) / (nir + red), np.nan)


def greenness(rgb):
    """Excess green, (2G - R - B) / (R + G + B): foliage about 0.08-0.2, roofs and paving below 0.03."""
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    return (2 * g - r - b) / (r + g + b + 1.0)


def vegetation_mask(chm, rgb, exclude=None, min_height=0.5, ndvi=None):
    """Cells of standing vegetation: canopy above `min_height` and living leaves, by NDVI where the near-infrared
    photo covers the cell, else green (or dark and not grey) in the aerial. Colour alone takes green roofs for trees."""
    exg = greenness(rgb)
    bright = rgb.mean(-1)
    leafy = (exg > 0.045) | ((bright < 70) & (exg > 0.0))
    if ndvi is not None:
        leafy = np.where(np.isfinite(ndvi), ndvi > NDVI_MIN, leafy)
    veg = (chm > min_height) & leafy
    if exclude is not None:
        veg &= ~exclude
    veg = nd.binary_opening(veg, iterations=1)
    veg = nd.binary_closing(veg, iterations=1) & (chm > min_height - 0.1)
    if exclude is not None:
        veg &= ~exclude
    return veg


def crown_radius(height):
    """Rough crown radius for a top `height` m above ground, used to tell bumps from crowns."""
    return np.clip(0.5 + 0.15 * np.asarray(height, float), 0.75, 2.5)


def segment(chm, veg, step=STEP, min_height=1.0, min_area=1.0):
    """Label crowns: a watershed of the smoothed canopy from its regional maxima, inside `veg`.

    Neighbouring pieces are then merged, the shallowest first, when the highest point of their
    shared edge is less than max(0.25 m, 10 % of the lower top) below the lower top, or when
    their tops are closer than crown_radius() of the lower one: bumps of one crown, not two
    plants. Pieces smaller than `min_area` m2 join the neighbour they share most edge with, or
    are dropped. Returns int labels (0 = no crown), numbered 1.. by decreasing top height."""
    sm = nd.gaussian_filter(chm, 0.375 / step)
    pk = (sm >= nd.maximum_filter(sm, size=3) - 1e-9) & veg & (sm > min_height)
    markers, _ = nd.label(pk)
    lab = flood(sm, markers, veg)
    ids = np.arange(lab.max() + 1)
    top = np.asarray(nd.maximum(sm, lab, ids), float)
    pos = np.asarray(nd.maximum_position(sm, lab, ids), float) * step
    pairs = _shared_edges(lab, sm)
    parent = ids.copy()

    def root(k):
        while parent[k] != k:
            parent[k] = parent[parent[k]]
            k = parent[k]
        return k

    while True:
        best = None
        for (a, b), (saddle, _) in pairs.items():
            lo = min(top[a], top[b])
            depth = lo - saddle
            near = math.dist(pos[a], pos[b]) < crown_radius(lo)
            if (depth < max(0.25, 0.1 * lo) or near) and (best is None or depth < best[0]):
                best = (depth, a, b)
        if best is None:
            break
        _, a, b = best
        keep, drop = (a, b) if top[a] >= top[b] else (b, a)
        parent[drop] = keep
        merged = {}
        for (x, y), (s, n) in pairs.items():
            x, y = root(x), root(y)
            if x == y:
                continue
            key = (min(x, y), max(x, y))
            s0, n0 = merged.get(key, (-1.0, 0))
            merged[key] = (max(s0, s), n0 + n)
        pairs = merged
    lab = np.array([root(k) for k in ids])[lab]
    area = np.bincount(lab.ravel(), minlength=lab.max() + 1) * step * step
    for k in np.nonzero((area > 0) & (area < min_area))[0]:
        if k == 0:
            continue
        nbrs = [(n, b if a == k else a) for (a, b), (_, n) in pairs.items() if k in (a, b)]
        lab[lab == k] = max(nbrs)[1] if nbrs else 0
    # Renumber by decreasing top height.
    ids = np.unique(lab[lab > 0])
    tops = nd.maximum(sm, lab, ids)
    order = ids[np.argsort(-np.asarray(tops))]
    remap = np.zeros(lab.max() + 1, np.int64)
    remap[order] = np.arange(1, len(order) + 1)
    return remap[lab]


def flood(height, markers, mask):
    """Watershed from labelled markers, flooding downhill: cells are claimed, highest first, by
    the labelled neighbour that reaches them, so each cell joins the crown it can be reached
    from without descending further. (scipy.ndimage.watershed_ift gives wrong regions for
    markers at different heights, so it is not used.)"""
    lab = np.where(mask, markers, 0).astype(np.int64)
    rows, cols = lab.shape
    flat_h = height.ravel()
    flat_l = lab.ravel()
    free = mask.ravel() & (flat_l == 0)
    heap = [(-flat_h[i], i) for i in np.flatnonzero(flat_l > 0)]
    heapq.heapify(heap)
    while heap:
        _, i = heapq.heappop(heap)
        r, c = divmod(i, cols)
        for q, ok in ((i - cols, r > 0), (i + cols, r < rows - 1), (i - 1, c > 0), (i + 1, c < cols - 1)):
            if ok and free[q]:
                free[q] = False
                flat_l[q] = flat_l[i]
                heapq.heappush(heap, (-flat_h[q], q))
    return flat_l.reshape(rows, cols)


def _shared_edges(lab, sm):
    """{(a, b): (saddle height, edge length in cells)} for 4-neighbouring labels a < b, both > 0."""
    out = {}
    for da, db, sa, sb in ((lab[:, :-1], lab[:, 1:], sm[:, :-1], sm[:, 1:]),
                           (lab[:-1, :], lab[1:, :], sm[:-1, :], sm[1:, :])):
        m = (da != db) & (da > 0) & (db > 0)
        a, b = np.minimum(da[m], db[m]), np.maximum(da[m], db[m])
        h = np.minimum(sa[m], sb[m])
        for x, y, z in zip(a.tolist(), b.tolist(), h.tolist()):
            s, n = out.get((x, y), (-1.0, 0))
            out[(x, y)] = (max(s, z), n + 1)
    return out


def outline(mask, E, N, step=STEP, simplify=0.15):
    """Polygon (shapely, holes dropped) of the cells in `mask`, simplified by `simplify` m."""
    ii, jj = np.nonzero(mask)
    if not len(ii):
        return None
    h = step / 2
    cells = [shapely.box(E[i, j] - h, N[i, j] - h, E[i, j] + h, N[i, j] + h) for i, j in zip(ii, jj)]
    poly = shapely.union_all(cells).buffer(0.01).buffer(-0.01)
    if isinstance(poly, MultiPolygon):
        poly = max(poly.geoms, key=lambda p: p.area)
    poly = Polygon(poly.exterior).simplify(simplify, preserve_topology=True)
    return poly if poly.is_valid and poly.area > 0 else None


def crowns(lab, chm, rgb, E, N, ground_h, step=STEP):
    """Candidate features from crown labels: outline, apex, heights, area and colour."""
    sm = nd.gaussian_filter(chm, 0.375 / step)
    out = []
    for k in range(1, lab.max() + 1):
        m = lab == k
        if not m.any():
            continue
        poly = outline(m, E, N, step)
        if poly is None:
            continue
        i, j = np.unravel_index(np.argmax(np.where(m, sm, -np.inf)), m.shape)
        h = chm[m]
        top = rgb[m & (chm > np.percentile(h, 40))]
        # Elongation: ratio of the outline's long to short side of its rotated bounding box.
        rect = np.asarray(poly.minimum_rotated_rectangle.exterior.coords)[:4]
        sides = sorted([float(np.hypot(*(rect[1] - rect[0]))), float(np.hypot(*(rect[2] - rect[1])))])
        out.append({
            "segment": int(k),
            "outline": poly,
            "apex": (float(E[i, j]), float(N[i, j])),
            "ground_at_apex": float(ground_h[i, j]),
            "height_max": float(np.percentile(h, 98)),
            "height_mean": float(h.mean()),
            "area": float(m.sum() * step * step),
            "elongation": sides[1] / max(sides[0], 0.25),
            "colour": "#" + "".join(f"{int(round(v)):02x}" for v in np.median(top, axis=0)),
        })
    return out


def guess_kind(c):
    """tree / shrub / hedge from a crown's size and shape (a first guess for curation)."""
    if c["height_max"] >= 4.0 and c["area"] >= 6.0 and c["elongation"] < 3.0:
        return "tree"
    if c["elongation"] >= 3.0 and c["height_max"] < 5.0:
        return "hedge"
    return "shrub"


def house_mask(E, N, buffer=1.0):
    """Cells under the house (its walls, bay windows and porches from data/site/house.geojson), widened by
    `buffer`: the eaves (0.35 m) and the half-diagonal of a 1 m LiDAR cell that takes the roof's
    height where it overlaps the eaves."""
    feats = geojson.read_optional(ROOT / "data" / "site" / "house.geojson")
    polys = [Polygon(f["geometry"]["coordinates"][0]) for f in feats
             if f["geometry"]["type"] == "Polygon" and (f["properties"]["id"] == "house-walls"
                                                         or f["properties"].get("part") in ("bay", "porch"))]
    return shapely.contains_xy(shapely.union_all(polys).buffer(buffer), E, N)


def read(path=TREES):
    return geojson.read_optional(path)


def removed_areas(feats):
    """Polygons of canopy that has gone since the LiDAR: a felled tree's crown (a stump with
    `felled_since_lidar`: a circle of `crown_radius_m`, default 3.5 m, about `crown_centre` or
    the stump) and polygon features marked `removed` (kind `removed`, such as the ground cleared
    for a new shed)."""
    out = []
    for f in feats:
        p = f["properties"]
        if p.get("felled_since_lidar"):
            e, n = p.get("crown_centre", f["geometry"]["coordinates"][:2])
            out.append(shapely.Point(e, n).buffer(float(p.get("crown_radius_m", 3.5))))
        elif p.get("removed") and f["geometry"]["type"] == "Polygon":
            out.append(Polygon(f["geometry"]["coordinates"][0]))
    return out


def removed_mask(feats, E, N):
    """True where (E, N) lies in a crown gone since the LiDAR (removed_areas)."""
    out = np.zeros(np.shape(E), bool)
    for poly in removed_areas(feats):
        out |= shapely.contains_xy(poly, E, N)
    return out


def map_canopy(extent, removed=(), ground=None):
    """Crown candidates over `extent` from the LiDAR and the aerial.

    Canopy inside the `removed` polygons (gone since the survey) is cut out before the crowns
    are found, and reported as crowns of its own with `removed` set. Returns the candidates
    (dicts from crowns()) and the grids {E, N, chm, rgb, veg, labels}."""
    E, N = grid(extent)
    ground = ground or terrain.ground()
    gh = ground.sample(E, N)
    chm = canopy_height(E, N, ground)
    rgb = aerial_rgb(E, N)
    veg = vegetation_mask(chm, rgb, exclude=house_mask(E, N), ndvi=aerial_ndvi(E, N))
    gone = [shapely.contains_xy(poly, E, N) for poly in removed]
    lab = segment(chm, veg & ~np.any(gone, axis=0) if gone else veg)
    cands = crowns(lab, chm, rgb, E, N, gh)
    for m in gone:
        m = m & veg & (lab == 0)
        for c in crowns(m.astype(int), chm, rgb, E, N, gh) if m.any() else []:
            c["segment"] = int(lab.max() + 1)
            c["removed"] = True
            lab[m] = c["segment"]
            cands.append(c)
    return cands, {"E": E, "N": N, "chm": chm, "rgb": rgb, "veg": veg, "labels": lab}


def as_json(c):
    """Candidate crown as a GeoJSON feature (data/sources/canopy.geojson)."""
    ring = [[round(x, 3), round(y, 3)] for x, y in c["outline"].exterior.coords]
    extra = {"removed": True} if c.get("removed") else {}
    return geojson.feature("Polygon", [ring], segment=c["segment"], kind=guess_kind(c),
                           height_m=round(c["height_max"], 2), height_mean_m=round(c["height_mean"], 2),
                           area_m2=round(c["area"], 1), apex=[round(c["apex"][0], 2), round(c["apex"][1], 2)],
                           colour=c["colour"], elongation=round(c["elongation"], 2), **extra)


# --------------------------------------------------------------------------- drawing: textures
#
# All textures are drawn here, from fixed seeds, so the build needs no source photos. Colours
# come from the features (sampled from the photos and frames, see data/site/trees.geojson);
# the foliage sprites are grey and take each card's colour in the viewer.

SPRITES = ("broadleaf", "small-leaf", "glossy", "cherry", "strap", "flax", "ivy", "fern", "rosette", "blossom", "weeds",
           "stalks")
ATLAS_GRID = (4, 3)          # columns and rows of sprites in foliage.png
CELL = 256                   # sprite size, px
SS = 4                       # supersampling while drawing
# Sprites drawn standing on their bottom edge (clumps); the others are centred on the card.
BOTTOM = {"strap", "flax", "fern", "weeds", "stalks"}


def _ovate(cx, cy, length, width, angle, base=0.38):
    """Outline of a leaf from its base (cx, cy) along `angle` (radians, image coordinates)."""
    s = np.linspace(0.0, 1.0, 16)
    half = 0.5 * width * np.sin(np.pi * s) ** 0.7 * np.where(s < base, 1.0, 1.0 - 0.25 * (s - base) / (1 - base))
    pts = np.concatenate([np.column_stack([s * length, half]), np.column_stack([s[::-1] * length, -half[::-1]])])
    c, si = math.cos(angle), math.sin(angle)
    return [(cx + x * c - y * si, cy + x * si + y * c) for x, y in pts]


def _strap(x0, y0, length, width, angle, bend, n=18):
    """Outline and axis of a strap leaf from (x0, y0), heading `angle`, curving by `bend` (radians)."""
    s = np.linspace(0.0, 1.0, n)
    a = angle + bend * s ** 1.5
    dx, dy = np.cos(a) * length / (n - 1), np.sin(a) * length / (n - 1)
    x = x0 + np.concatenate([[0.0], np.cumsum(dx[:-1])])
    y = y0 + np.concatenate([[0.0], np.cumsum(dy[:-1])])
    w = 0.5 * width * (1 - s ** 2.2)
    nx, ny = -np.sin(a), np.cos(a)
    left = list(zip(x + nx * w, y + ny * w))
    right = list(zip(x - nx * w, y - ny * w))
    return left + right[::-1], list(zip(x, y))


def _draw_sprite(name, rng):
    """One sprite as (luminance, alpha) images of CELL x CELL px."""
    from PIL import Image, ImageDraw

    S = CELL * SS
    lum = Image.new("L", (S, S), 200)
    alpha = Image.new("L", (S, S), 0)
    dl, da = ImageDraw.Draw(lum), ImageDraw.Draw(alpha)
    pad = 10 * SS

    def leaf(poly, value, rib=None, rib_value=None, width=3):
        dl.polygon(poly, fill=int(value))
        da.polygon(poly, fill=255)
        if rib is not None:
            dl.line(rib, fill=int(rib_value if rib_value is not None else min(255, value + 25)), width=width * SS)

    def inside(poly):
        return all(pad < x < S - pad and pad < y < S - pad for x, y in poly)

    if name in ("broadleaf", "glossy", "cherry"):
        # Leaves along twigs that radiate from near the centre.
        count, l_min, l_max, wfrac, lo, hi = {"broadleaf": (170, 20, 36, 0.42, 150, 250),
                                              "glossy": (80, 28, 44, 0.5, 120, 245),
                                              "cherry": (80, 15, 28, 0.42, 160, 250)}[name]
        cx = cy = S / 2
        twigs = []
        n_twigs = int(rng.integers(7, 10))
        a0 = rng.uniform(0, 2 * np.pi)
        for k in range(n_twigs):
            a = a0 + 2 * np.pi * (k + rng.uniform(-0.3, 0.3)) / n_twigs
            ln = rng.uniform(0.22, 0.36) * S
            x0, y0 = cx + rng.normal(0, 0.04) * S, cy + rng.normal(0, 0.04) * S
            twigs.append((x0, y0, cx + math.cos(a) * ln, cy + math.sin(a) * ln))
            if name == "cherry":
                dl.line([twigs[-1][:2], twigs[-1][2:]], fill=110, width=2 * SS)
                da.line([twigs[-1][:2], twigs[-1][2:]], fill=255, width=2 * SS)
        for k in range(count):
            x0, y0, x1, y1 = twigs[k % len(twigs)]
            t = rng.uniform(0.1, 1.05)
            bx, by = x0 + (x1 - x0) * t, y0 + (y1 - y0) * t
            a = math.atan2(y1 - y0, x1 - x0) + rng.choice([-1, 1]) * rng.uniform(0.4, 1.3)
            ln = rng.uniform(l_min, l_max) * SS * (0.75 if name == "cherry" else 1.0)
            poly = _ovate(bx, by, ln, ln * wfrac, a)
            if not inside(poly):
                continue
            v = rng.uniform(lo, hi)
            rib = [(bx, by), (bx + math.cos(a) * ln * 0.85, by + math.sin(a) * ln * 0.85)]
            leaf(poly, v, rib, min(255, v + (35 if name == "glossy" else 18)), 3 if name == "glossy" else 2)
            if name == "glossy" and rng.random() < 0.6:   # specular streak
                q = a + math.pi / 2
                streak = [(bx + math.cos(a) * ln * f + math.cos(q) * ln * 0.08,
                           by + math.sin(a) * ln * f + math.sin(q) * ln * 0.08) for f in (0.25, 0.65)]
                dl.line(streak, fill=255, width=4 * SS)
    elif name in ("small-leaf", "ivy"):
        # Small leaves, or ivy's three-lobed ones, packed in a round blob.
        count, l_min, l_max, lo, hi = {"small-leaf": (700, 9, 16, 140, 250), "ivy": (240, 20, 32, 110, 235)}[name]
        lobes_r = rng.uniform(0.55, 1.0, 9)
        for _ in range(count):
            th = rng.uniform(0, 2 * np.pi)
            edge = np.interp(th, np.linspace(0, 2 * np.pi, 10), np.append(lobes_r, lobes_r[0]))
            r = (0.47 if name == "small-leaf" else 0.45) * S * edge * rng.random() ** 0.75
            bx, by = S / 2 + r * math.cos(th), S / 2 + r * math.sin(th)
            ln = rng.uniform(l_min, l_max) * SS
            a = rng.uniform(0, 2 * np.pi)
            v = rng.uniform(lo, hi)
            poly = _ovate(bx, by, ln, ln * (0.45 if name == "small-leaf" else 0.55), a)
            if not inside(poly):
                continue
            if name == "small-leaf":
                leaf(poly, v)
            else:      # glossy ground cover: a midrib and a highlight
                rib = [(bx, by), (bx + math.cos(a) * ln * 0.85, by + math.sin(a) * ln * 0.85)]
                leaf(poly, v, rib, min(255, v + 35), 2)
                if rng.random() < 0.5:
                    q = a + math.pi / 2
                    dl.line([(bx + math.cos(a) * ln * f + math.cos(q) * ln * 0.1,
                              by + math.sin(a) * ln * f + math.sin(q) * ln * 0.1) for f in (0.3, 0.6)],
                            fill=255, width=2 * SS)
    elif name in ("strap", "flax", "weeds"):
        # Leaves rising from the bottom centre.
        n, l_rng, w_rng, spread, bend, lo, hi = {
            "strap": (20, (0.45, 0.9), (13, 18), 0.95, 0.9, 150, 250),
            "flax": (13, (0.6, 0.95), (14, 19), 0.5, 0.45, 120, 225),
            "weeds": (46, (0.3, 0.85), (3, 5), 0.9, 0.7, 150, 250)}[name]
        for _ in range(n):
            a = -np.pi / 2 + rng.uniform(-spread, spread) * rng.uniform(0.3, 1.0)
            ln = rng.uniform(*l_rng) * (S - 2 * pad)
            w = rng.uniform(*w_rng) * SS
            b = rng.normal(0, bend / 2) if name == "weeds" else np.sign(a + np.pi / 2) * bend * rng.uniform(0.3, 1.0)
            poly, axis = _strap(S / 2 + rng.normal(0, 0.02 * S), S - pad, ln, w, a, b)
            poly = [(min(max(x, pad), S - pad), min(max(y, pad), S - pad)) for x, y in poly]
            v = rng.uniform(lo, hi)
            leaf(poly, v, None if name == "weeds" else axis, min(255, v + 30), 4 if name == "flax" else 3)
    elif name == "fern":
        for _ in range(8):
            a = -np.pi / 2 + rng.uniform(-0.75, 0.75)
            ln = rng.uniform(0.55, 0.85) * (S - 2 * pad)
            _, axis = _strap(S / 2, S - pad, ln, 1, a, np.sign(a + np.pi / 2) * rng.uniform(0.2, 0.6), n=24)
            dl.line(axis, fill=150, width=3 * SS)
            da.line(axis, fill=255, width=3 * SS)
            for i in range(2, len(axis) - 1):
                (x0, y0), (x1, y1) = axis[i - 1], axis[i]
                d = math.atan2(y1 - y0, x1 - x0)
                pl = (1 - i / len(axis)) * 0.11 * S + 0.015 * S
                for side in (-1, 1):
                    poly = _ovate(x1, y1, pl, pl * 0.32, d + side * 1.2)
                    if inside(poly):
                        leaf(poly, rng.uniform(170, 250))
    elif name == "rosette":
        for k in range(60):          # leaves on a golden-angle spiral, darker towards the centre
            ring = (k / 60) ** 0.5
            ln = (0.12 + 0.28 * ring) * S
            leaf(_ovate(S / 2, S / 2, ln, ln * 0.45, k * 2.399963, base=0.7), 120 + 110 * ring + rng.uniform(-15, 15))
    elif name == "stalks":          # dry flower stalks with seed heads, rising from the bottom
        for _ in range(9):
            a = -np.pi / 2 + rng.normal(0, 0.3)
            ln = rng.uniform(0.5, 0.95) * (S - 2 * pad)
            _, axis = _strap(S / 2 + rng.normal(0, 0.05 * S), S - pad, ln, 1, a, rng.normal(0, 0.3), n=16)
            v = int(rng.uniform(150, 230))
            dl.line(axis, fill=v, width=2 * SS)
            da.line(axis, fill=255, width=2 * SS)
            for x, y in axis[-4:]:
                r = rng.uniform(2, 4) * SS
                dl.ellipse([x - r, y - r * 1.5, x + r, y + r * 1.5], fill=min(255, v + 10))
                da.ellipse([x - r, y - r * 1.5, x + r, y + r * 1.5], fill=255)
    elif name == "blossom":
        for _ in range(9):
            cx, cy = rng.uniform(0.22, 0.78) * S, rng.uniform(0.22, 0.78) * S
            r = rng.uniform(0.06, 0.1) * S
            for p in range(5):
                a = p * 2 * np.pi / 5 + rng.uniform(0, 0.4)
                px_, py_ = cx + math.cos(a) * r * 0.75, cy + math.sin(a) * r * 0.75
                box = [px_ - r * 0.6, py_ - r * 0.6, px_ + r * 0.6, py_ + r * 0.6]
                dl.ellipse(box, fill=int(rng.uniform(215, 255)))
                da.ellipse(box, fill=255)
            dl.ellipse([cx - r * 0.3, cy - r * 0.3, cx + r * 0.3, cy + r * 0.3], fill=170)
    return lum.resize((CELL, CELL), Image.LANCZOS), alpha.resize((CELL, CELL), Image.LANCZOS)


def foliage_atlas(path, seed=7):
    """foliage.png: the SPRITES on an ATLAS_GRID of CELL px cells, grey with alpha. Returns each
    sprite's mean grey over its leaves (0-1), which the card colours are divided by, so a plant
    shows its sampled colour on average."""
    from PIL import Image

    cols, rows = ATLAS_GRID
    lum = Image.new("L", (cols * CELL, rows * CELL), 200)
    alpha = Image.new("L", (cols * CELL, rows * CELL), 0)
    means = {}
    for k, name in enumerate(SPRITES):
        lv, av = _draw_sprite(name, np.random.default_rng(seed + k))
        lum.paste(lv, ((k % cols) * CELL, (k // cols) * CELL))
        alpha.paste(av, ((k % cols) * CELL, (k // cols) * CELL))
        a = np.asarray(av) > 127
        means[name] = float(np.asarray(lv, float)[a].mean() / 255) if a.any() else 1.0
    Image.merge("LA", (lum, alpha)).save(path, optimize=True)
    return means


def _noise(shape, rng, scales=((32, 1.0), (8, 0.5), (2, 0.25)), wrap=True):
    """Sum of smoothed random fields (scales in px, weights), normalised to about -1..1."""
    out = np.zeros(shape)
    for s, w in scales:
        out += w * nd.gaussian_filter(rng.normal(size=shape), s, mode="wrap" if wrap else "reflect") * s
    return out / (np.abs(out).max() + 1e-9)


def bark_texture(path, size=256, seed=3):
    """bark.jpg: light grey bark that tiles (mean about 0.9), with vertical fissures and horizontal
    lenticels; the viewer tints it with each tree's bark colour."""
    from PIL import Image

    rng = np.random.default_rng(seed)
    streak = nd.gaussian_filter(rng.normal(size=(size, size)), (24, 1.5), mode="wrap")
    streak /= np.abs(streak).max()
    lent = np.zeros((size, size))
    for _ in range(140):
        cx, cy, ln = rng.integers(0, size), rng.integers(0, size), rng.integers(4, 14)
        lent[cy, (cx + np.arange(ln)) % size] = 1
    lent = nd.gaussian_filter(lent, 0.7, mode="wrap")
    v = 232 + 26 * streak + 18 * _noise((size, size), rng, ((16, 1.0), (3, 0.6))) - 80 * np.clip(lent, 0, 1)
    Image.fromarray(np.clip(np.stack([v, v * 0.985, v * 0.96], -1), 0, 255).astype(np.uint8)).save(path, quality=82)
    return path


# Cut faces in stumps.jpg (cells 1..; cell 0 is bark): (face colour, ring contrast, rim colour).
# fresh: newly cut and drilled; weathered: grey with age; dark: old and small;
# mound: a grubbed-out root mound, soil showing between weeds (no rings).
STUMP_FACES = {"fresh": ((236, 210, 196), 0.10, (158, 139, 119)),
               "weathered": ((160, 116, 82), 0.07, (120, 104, 84)),
               "dark": ((112, 90, 64), 0.18, (90, 76, 58)),
               "mound": ((104, 92, 68), 0.0, (104, 92, 68))}


def stump_texture(path, cell=128, seed=5):
    """stumps.jpg: a strip of square cells, bark first, then the cut faces of STUMP_FACES."""
    from PIL import Image, ImageDraw

    rng = np.random.default_rng(seed)
    out = np.zeros((cell, cell * (len(STUMP_FACES) + 1), 3), np.uint8)
    yy, xx = np.mgrid[0:cell, 0:cell]
    r = np.hypot(xx - cell / 2 + 0.5, yy - cell / 2 + 0.5) / (cell / 2)
    th = np.arctan2(yy - cell / 2, xx - cell / 2)
    streak = nd.gaussian_filter(rng.normal(size=(cell, cell)), (10, 1.2), mode="wrap")
    v = 150 + 40 * streak / np.abs(streak).max()
    out[:, :cell] = np.clip(np.stack([v, v * 0.9, v * 0.78], -1), 0, 255).astype(np.uint8)
    for k, (name, (base, ring_amp, rim)) in enumerate(STUMP_FACES.items()):
        rr = r + 0.03 * np.sin(3 * th + rng.uniform(0, 6)) + 0.02 * np.sin(7 * th + rng.uniform(0, 6))
        if name == "mound":
            fib = nd.gaussian_filter(rng.normal(size=(cell, cell)), 0.8)
            shade = 1 + 0.25 * fib / np.abs(fib).max()
        else:
            rings = np.sin(rr * (14 if name == "fresh" else 11) * np.pi + 0.8 * _noise((cell, cell), rng, ((4, 1.0),))) * ring_amp
            cracks = np.zeros((cell, cell))
            if name != "fresh":
                for a in rng.uniform(-np.pi, np.pi, 5):
                    cracks += np.exp(-((th - a + np.pi) % (2 * np.pi) - np.pi) ** 2 / 0.0008) * (rr < 0.85) * (rr > 0.1)
            shade = 1 + rings - 0.45 * np.clip(cracks, 0, 1) + 0.06 * _noise((cell, cell), rng, ((6, 1.0), (1.5, 0.5)))
        col = np.stack([base[i] * shade for i in range(3)], -1)
        if name == "mound":    # weeds over the soil
            weeds = nd.gaussian_filter(rng.random((cell, cell)), 1.2) > 0.5
            col[weeds] = col[weeds] * 0.25 + np.array([96, 112, 52]) * 0.75
        else:
            col[rr > 0.9] = rim
        img = Image.fromarray(np.clip(col, 0, 255).astype(np.uint8))
        if name == "weathered":  # pale lichen dots and blotches
            d = ImageDraw.Draw(img)
            for _ in range(7):
                rr_, th_ = 0.75 * math.sqrt(rng.random()), rng.uniform(0, 2 * np.pi)
                hx, hy = cell / 2 * (1 + rr_ * math.cos(th_)), cell / 2 * (1 + rr_ * math.sin(th_))
                sz = rng.uniform(1.5, 5)
                d.ellipse([hx - sz, hy - sz * 0.8, hx + sz, hy + sz * 0.8], fill=(214, 210, 196))
        if name == "fresh":      # drilled holes in a rough grid
            d = ImageDraw.Draw(img)
            for gy in np.linspace(-0.55, 0.55, 4):
                for gx in np.linspace(-0.55, 0.55, 4):
                    if gx * gx + gy * gy <= 0.5:
                        hx = cell / 2 + (gx + rng.normal(0, 0.04)) * cell / 2
                        hy = cell / 2 + (gy + rng.normal(0, 0.04)) * cell / 2
                        d.ellipse([hx - 3.5, hy - 3, hx + 3.5, hy + 3], fill=(92, 64, 48))
        out[:, cell * (k + 1):cell * (k + 2)] = np.asarray(img)
    Image.fromarray(out).save(path, quality=85)
    return path


def grass_detail_texture(path, size=256, seed=13):
    """grass-detail.jpg: a grey tile of short grass blades (about 0.5 m across), averaging mid-grey,
    that the viewer multiplies into the lawn close up."""
    from PIL import Image, ImageDraw

    rng = np.random.default_rng(seed)
    S = size * 2
    img = Image.new("L", (S, S), 128)
    d = ImageDraw.Draw(img)
    for _ in range(5200):
        x, y = rng.uniform(0, S), rng.uniform(0, S)
        a = rng.normal(-np.pi / 2, 0.6)
        ln = rng.uniform(6, 22)
        v = int(np.clip(rng.normal(128, 38), 40, 230))
        for ox in (-S, 0, S):           # draw wrapped copies so the tile repeats seamlessly
            for oy in (-S, 0, S):
                x0, y0 = x + ox, y + oy
                if -30 < x0 < S + 30 and -30 < y0 < S + 30:
                    d.line([(x0, y0), (x0 + math.cos(a) * ln, y0 + math.sin(a) * ln)], fill=v, width=2)
    arr = np.asarray(img.resize((size, size), Image.LANCZOS), float)
    arr = 128 + (arr - arr.mean()) * 1.1
    Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8)).convert("RGB").save(path, quality=85)
    return path


def litter_texture(path, size=256, tile_m=1.0, seed=17):
    """litter.jpg: a tile of bare soil with leaf litter and twigs (`tile_m` across), in the colours
    of a bed's floor (dark soil, brown and
    grey-brown fallen leaves)."""
    from PIL import Image, ImageDraw

    rng = np.random.default_rng(seed)
    S = size * 2
    base = 1 + 0.18 * _noise((S, S), rng, ((16, 1.0), (4, 0.6), (1.2, 0.4)))
    soil = np.array([74, 62, 48], float)[None, None, :] * base[..., None]
    img = Image.fromarray(np.clip(soil, 0, 255).astype(np.uint8))
    d = ImageDraw.Draw(img)
    leaf_cols = [(122, 92, 60), (138, 112, 76), (96, 80, 58), (150, 128, 94), (88, 72, 50), (108, 96, 70)]
    for _ in range(900):
        x, y = rng.uniform(0, S), rng.uniform(0, S)
        a = rng.uniform(0, 2 * np.pi)
        ln = rng.uniform(10, 26)
        col = leaf_cols[rng.integers(len(leaf_cols))]
        f = rng.uniform(0.8, 1.15)
        col = tuple(int(np.clip(c * f, 0, 255)) for c in col)
        for ox in (-S, 0, S):
            for oy in (-S, 0, S):
                if -40 < x + ox < S + 40 and -40 < y + oy < S + 40:
                    d.polygon(_ovate(x + ox, y + oy, ln, ln * 0.45, a), fill=col)
    for _ in range(60):     # twigs
        x, y = rng.uniform(0, S), rng.uniform(0, S)
        a = rng.uniform(0, 2 * np.pi)
        ln = rng.uniform(20, 70)
        d.line([(x, y), (x + math.cos(a) * ln, y + math.sin(a) * ln)], fill=(70, 58, 44), width=2)
    img.resize((size, size), Image.LANCZOS).save(path, quality=82)
    return path


def bed_texture(path, size=512, seed=29):
    """bed.jpg: a tile of a planted bed 2 m across, seen from above: bark mulch with clumps of low plants, some
    of them in flower. For the new beds of a wall option, which have no plants of their own in the model."""
    from PIL import Image, ImageDraw

    rng = np.random.default_rng(seed)
    S = size * 2
    metre = S / 2.0
    base = 1 + 0.2 * _noise((S, S), rng, ((14, 1.0), (4, 0.7), (1.5, 0.5)))
    img = Image.fromarray(np.clip(np.array([66, 50, 38], float)[None, None, :] * base[..., None], 0, 255).astype(np.uint8))
    d = ImageDraw.Draw(img)

    def copies(x, y, reach):
        """Where to draw something centred on (x, y) so the tile repeats without a seam."""
        return [(x + ox, y + oy) for ox in (-S, 0, S) for oy in (-S, 0, S)
                if -reach < x + ox < S + reach and -reach < y + oy < S + reach]

    for _ in range(1400):       # chips of bark
        x, y, a, ln = rng.uniform(0, S), rng.uniform(0, S), rng.uniform(0, 2 * np.pi), rng.uniform(6, 16)
        col = tuple(int(c * rng.uniform(0.75, 1.25)) for c in (92, 68, 48))
        for px, py in copies(x, y, 20):
            d.polygon(_ovate(px, py, ln, ln * 0.4, a), fill=col)
    greens = [(62, 104, 52), (78, 122, 60), (52, 92, 58), (96, 132, 70), (70, 110, 84)]
    blooms = [(238, 236, 226), (236, 200, 72), (170, 120, 190), (226, 122, 132)]
    for k in range(13):         # plants: a rosette of leaves 0.2 to 0.5 m across, every third one in flower
        x, y, r = rng.uniform(0, S), rng.uniform(0, S), rng.uniform(0.1, 0.25) * metre
        green = greens[rng.integers(len(greens))]
        n = int(rng.integers(14, 26))
        leaves = [(2 * np.pi * j / n + rng.normal(0, 0.15), r * rng.uniform(0.7, 1.1), rng.uniform(0.78, 1.18)) for j in range(n)]
        flowers = [(rng.uniform(0, 2 * np.pi), rng.uniform(0, 0.8) * r) for _ in range(9)] if k % 3 == 0 else []
        bloom = blooms[rng.integers(len(blooms))]
        for px, py in copies(x, y, int(2 * r)):
            for a, ln, shade in leaves:
                d.polygon(_ovate(px + math.cos(a) * ln * 0.5, py + math.sin(a) * ln * 0.5, ln, ln * 0.32, a),
                          fill=tuple(int(np.clip(c * shade, 0, 255)) for c in green))
            for a, rr in flowers:
                cx, cy = px + math.cos(a) * rr, py + math.sin(a) * rr
                d.ellipse([cx - 5, cy - 5, cx + 5, cy + 5], fill=bloom)
    img.resize((size, size), Image.LANCZOS).save(path, quality=84)
    return path


def lawn_box(lawn_poly):
    """The box (E0, N0, E1, N1) a lawn's picture covers: the lawn's own, and 0.2 m round it."""
    e0, n0, e1, n1 = lawn_poly.bounds
    return e0 - 0.2, n0 - 0.2, e1 + 0.2, n1 + 0.2


def lawn_drape(v, faces, box, texture, frame):
    """A lawn for the viewer: ground triangles (`v`, `faces`) showing its picture, which covers `box`."""
    e0, n0, e1, n1 = box
    uv = np.column_stack([(v[:, 0] - e0) / (e1 - e0), (v[:, 1] - n0) / (n1 - n0)])
    return {"texture": texture, "positions": _r(frame.xyz(v)), "indices": [int(i) for i in faces.ravel()],
            "uv": _r(uv, 4), "detail": "grass-detail.jpg", "detail_m": 0.5}


def lawn_plain_box(lawn_poly, reach, px=0.025):
    """The box of a lawn's plain picture, grown to take in `reach` (E0, N0, E1, N1) as well: the lawn's own
    picture's box and whole pixels round it. Returns the box and the pixels added (west, east, south, north)."""
    e0, n0, e1, n1 = lawn_box(lawn_poly)
    grow = (max(0, math.ceil((e0 - reach[0]) / px)), max(0, math.ceil((reach[2] - e1) / px)),
            max(0, math.ceil((n0 - reach[1]) / px)), max(0, math.ceil((reach[3] - n1) / px)))
    return (e0 - grow[0] * px, n0 - grow[2] * px, e1 + grow[1] * px, n1 + grow[3] * px), grow


def lawn_plain_texture(path, lawn_poly, colour, reach, px=0.025, seed=11):
    """A lawn's grass without its bare patches, for the new lawns drawn beside it: the lawn's own picture less
    its patches, so old and new grass match where they meet, mirrored out from its edges as far as `reach`.
    Returns the box it covers (lawn_plain_box)."""
    from PIL import Image

    lawn_texture(path, lawn_poly, colour, [], px=px, seed=seed)
    box, (west, east, south, north) = lawn_plain_box(lawn_poly, reach, px)
    if west or east or south or north:
        img = np.pad(np.asarray(Image.open(path).convert("RGB")), ((north, south), (west, east), (0, 0)), mode="symmetric")
        Image.fromarray(img).save(path, quality=80, optimize=True)
    return box


def lawns(feats):
    """The lawns among `feats`: [{id, name, colour, poly}]."""
    return [{"id": f["properties"]["id"], "name": f["properties"].get("name", f["properties"]["id"]),
             "colour": f["properties"].get("colour", "#7f9446"), "poly": shapely.geometry.shape(f["geometry"])}
            for f in feats if f["properties"].get("kind") == "lawn"]


def lawn_texture(path, lawn_poly, colour, patches, px=0.025, seed=11):
    """lawn.jpg: the lawn's bounding box at `px` m per pixel, north up: grass in `colour` with
    mottling, and the bare patches [(polygon, look, colour)] painted in with ragged edges.
    Returns the box (E0, N0, E1, N1) the image covers."""
    from PIL import Image

    rng = np.random.default_rng(seed)
    e0, n0, e1, n1 = lawn_box(lawn_poly)
    w, h = int(math.ceil((e1 - e0) / px)), int(math.ceil((n1 - n0) / px))
    E, N = np.meshgrid(e0 + (np.arange(w) + 0.5) * px, n1 - (np.arange(h) + 0.5) * px)
    mott = _noise((h, w), rng, ((40, 1.0), (12, 0.6), (3, 0.35)), wrap=False)
    blades = rng.normal(size=(h, w))
    blades = nd.gaussian_filter(blades, (1.2, 0.4)) + nd.gaussian_filter(blades, (0.4, 1.2))
    blades /= np.abs(blades).max()
    tone = 1 + 0.10 * mott + 0.12 * blades
    yellow = np.clip(nd.gaussian_filter(rng.normal(size=(h, w)), 30) * 25, -1, 1)
    img = np.array(_rgb(colour), float)[None, None, :] * tone[..., None]
    img[..., 0] *= 1 + 0.06 * yellow
    img[..., 2] *= 1 - 0.08 * yellow
    for poly, look, pcol in patches:
        inside = shapely.contains_xy(poly, E, N).astype(float)
        m = np.clip((nd.gaussian_filter(inside, 3) + 0.35 * _noise((h, w), rng, ((4, 1.0), (1.5, 0.6)), wrap=False)
                     - 0.45) * 4, 0, 1)
        if look == "sand":
            grain = 1 + 0.08 * _noise((h, w), rng, ((1.0, 1.0), (4, 0.5)), wrap=False)
            tufts = (nd.gaussian_filter(rng.random((h, w)), 1.0) > 0.53) * 0.55
            bare = np.array(_rgb(pcol), float)[None, None, :] * grain[..., None]
            bare = bare * (1 - tufts[..., None]) + img * tufts[..., None]
        else:   # weedy mound: soil showing between the weeds
            bare = np.array([110, 98, 72], float)[None, None, :] * (1 + 0.15 * blades[..., None])
        img = img * (1 - m[..., None]) + bare * m[..., None]
    Image.fromarray(np.clip(img, 0, 255).astype(np.uint8)).save(path, quality=80, optimize=True)
    return (e0, n0, e1, n1)


def _rgb(hexstr):
    h = hexstr.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


# --------------------------------------------------------------------------- drawing: plants
#
# A plant is drawn as leaf cards: square quads showing one sprite, each with a position, size,
# heading (yaw), tilt from vertical (pitch, 90 = lying flat), spin in its own plane (roll), a
# colour and a normal for lighting (pointing out of the crown, so a crown shades as one mass).

# Crowns (trees, shrubs, hedges): sprite, card size range (m), and how many card areas of
# foliage cover each square metre of the crown's outer surface.
CROWN_LOOKS = {"broadleaf": ("broadleaf", (0.75, 1.15), 2.2), "small-leaf": ("small-leaf", (0.5, 0.8), 2.4),
               "glossy": ("glossy", (0.45, 0.7), 2.6), "cherry": ("cherry", (0.7, 1.0), 1.6)}
# Beds: parts drawn as clumps on a jittered grid (spacing m, cards per clump, size as a
# fraction of the bed's height) or as low ground cover (cards per m2, size range m).
BED_LOOKS = {
    "strap": [("clump", "strap", 0.2, 4, 1.0)],
    "flax": [("clump", "flax", 0.45, 4, 1.0)],
    "ivy": [("cover", "ivy", 34.0, (0.4, 0.6))],
    "fern": [("clump", "fern", 0.5, 3, 1.0), ("cover", "ivy", 5.0, (0.3, 0.45)), ("clump", "strap", 1.1, 3, 0.9)],
    "border": [("clump", "fern", 0.6, 3, 0.55), ("cover", "ivy", 7.0, (0.3, 0.45)), ("clump", "flax", 1.3, 3, 0.75),
               ("bush", "small-leaf", 4.0, (0.45, 0.7))],
    "rosette": [("rosette", "rosette", 0.4, 1, 0.8), ("clump", "weeds", 0.22, 3, 0.9), ("flowers", "blossom", 5.0, (0.18, 0.28))],
    "weeds": [("clump", "weeds", 0.18, 2, 0.8), ("clump", "stalks", 0.7, 1, 1.5)],
    "long-grass": [("clump", "weeds", 0.16, 3, 1.0)],
}
CONTEXT_DENSITY = 0.55       # plants outside the lot that no photo shows: fewer, bigger cards
CONTEXT_SIZE = 1.25


def sample_in(poly, n, rng):
    """`n` points spread uniformly inside a polygon."""
    e0, n0, e1, n1 = poly.bounds
    out, have = [], 0
    while have < n:
        m = max(64, int((n - have) * (e1 - e0) * (n1 - n0) / max(poly.area, 1e-6) * 1.3))
        p = np.column_stack([rng.uniform(e0, e1, m), rng.uniform(n0, n1, m)])
        p = p[shapely.contains_xy(poly, p[:, 0], p[:, 1])]
        out.append(p)
        have += len(p)
    return np.vstack(out)[:n]


def jittered_grid(poly, spacing, rng):
    """Points on a square grid of `spacing` m, each moved by up to a third of it, inside `poly`."""
    e0, n0, e1, n1 = poly.bounds
    E, N = np.meshgrid(np.arange(e0 + spacing / 2, e1, spacing), np.arange(n0 + spacing / 2, n1, spacing))
    p = np.column_stack([E.ravel(), N.ravel()]) + rng.uniform(-0.33, 0.33, (E.size, 2)) * spacing
    p = p[shapely.contains_xy(poly, p[:, 0], p[:, 1])]
    if not len(p):
        q = poly.representative_point()
        p = np.array([[q.x, q.y]])
    return p


def edge_vectors(poly, p):
    """Distance from each point to the polygon's outline, and the unit vector towards it."""
    ring = poly.exterior
    pts = shapely.points(p)
    d = shapely.distance(pts, ring)
    q = shapely.get_coordinates(shapely.line_interpolate_point(ring, shapely.line_locate_point(ring, pts)))
    v = q - p
    n = np.hypot(v[:, 0], v[:, 1])
    v = np.where(n[:, None] > 1e-9, v / np.maximum(n, 1e-9)[:, None], 0.0)
    return d, v


class Cards:
    """Leaf cards collected per plant and sprite (site coordinates; angles in degrees)."""

    def __init__(self):
        self.runs = {}

    def add(self, plant, sprite, pos, size, yaw, pitch, roll, rgb, normal):
        n = len(pos)
        if not n:
            return
        cols = [np.asarray(pos, float).reshape(n, 3), np.broadcast_to(size, (n,)), np.broadcast_to(yaw, (n,)),
                np.broadcast_to(pitch, (n,)), np.broadcast_to(roll, (n,)),
                np.broadcast_to(np.asarray(rgb, float), (n, 3)), np.asarray(normal, float).reshape(n, 3)]
        self.runs.setdefault((plant, sprite), []).append(np.column_stack(cols))

    def count(self):
        return sum(len(a) for parts in self.runs.values() for a in parts)

    def pack(self, frame, gains=None):
        """{positions, attributes, runs} for the viewer: positions as int16 cm in viewer
        coordinates; per card 10 bytes: size (cm), yaw, pitch, roll, r, g, b, normal x, y, z
        (signed, viewer coordinates); runs [plant, sprite, bottom-anchored, start, count].
        `gains` {sprite: mean grey of its leaves}: card colours are divided by it (in linear
        light), so that a plant shows its sampled colour on average."""
        import base64

        runs, chunks, start = [], [], 0
        for (plant, sprite), parts in sorted(self.runs.items()):
            a = np.vstack(parts)
            if gains:
                lin = (np.clip(a[:, 7:10], 0, 255) / 255) ** 2.2 / gains.get(sprite, 1.0)
                a[:, 7:10] = 255 * np.clip(lin, 0, 1) ** (1 / 2.2)
            runs.append([plant, SPRITES.index(sprite), int(sprite in BOTTOM), start, len(a)])
            chunks.append(a)
            start += len(a)
        a = np.vstack(chunks) if chunks else np.zeros((0, 13))
        pos = np.round(frame.xyz(a[:, :3]) * 100).astype("<i2")
        nrm = frame.xyz(a[:, 10:13] + np.array([frame.e0, frame.n0, frame.h0]))
        nrm /= np.maximum(np.linalg.norm(nrm, axis=1), 1e-9)[:, None]
        attr = np.column_stack([
            np.clip(np.round(a[:, 3] * 100), 1, 255),
            np.round(a[:, 4] % 360 / 360 * 256) % 256,
            np.clip(np.round(a[:, 5] / 90 * 255), 0, 255),
            np.round(a[:, 6] % 360 / 360 * 256) % 256,
            np.clip(np.round(a[:, 7:10]), 0, 255)]).astype(np.uint8)
        attr = np.column_stack([attr, np.round(nrm * 127).astype(np.int8).view(np.uint8)])
        return {"positions": base64.b64encode(pos.tobytes()).decode("ascii"),
                "attributes": base64.b64encode(attr.tobytes()).decode("ascii"), "runs": runs}


class Plant:
    """A curated feature ready to draw: its outline, ground, top and bottom of its foliage."""

    def __init__(self, feature, ground, lot):
        p = feature["properties"]
        self.props = p
        self.id, self.kind, self.look = p["id"], p["kind"], p.get("look", "broadleaf")
        self.name = p.get("name", self.id)
        geom = shapely.geometry.shape(feature["geometry"])
        self.poly = geom if geom.geom_type == "Polygon" else None
        self.ground = ground
        self.context = not p.get("colour") and (self.poly is None or not self.poly.intersects(lot))
        self.colour = np.array(_rgb(p["colour"]), float) if p.get("colour") else None
        self.height = float(p.get("height_m", 2.0))

    def top(self, e, n, g):
        """Top of the foliage (absolute): the LiDAR surface for `top: lidar` (no lower than
        `top_min_m` above the ground, 0.4 m unless given, and no higher than 0.5 m over `height_m`),
        else ground + height."""
        if self.props.get("top") == "lidar":
            s = raster.dsm().sample(e, n)
            s = np.where(np.isfinite(s), s, g + self.height)
            return np.clip(s, g + float(self.props.get("top_min_m", 0.4)), g + self.height + 0.5)
        return g + self.height

    def bottom(self, g, top, d):
        """Underside of the foliage: a tree's crown base (rising towards the crown's edge), near
        the ground for anything else. `d` is the distance to the outline."""
        if self.kind == "tree":
            cb = float(self.props.get("crown_base_m", 1.5))
            lift = 0.3 * np.clip(top - g - cb, 0, None) * np.clip(1 - d / 1.5, 0, 1)
            return np.minimum(g + cb + lift, top - 0.3)
        return g + (0.05 if self.kind == "hedge" else 0.12)

    def info(self):
        p = self.props
        bits = [p["species"]] if p.get("species") else []
        if self.kind in ("tree", "shrub", "hedge", "bed"):
            word = "top about" if p.get("top") == "lidar" else "about"
            bits.append(f"{word} {self.height:.1f} m above the ground" + (f" ({LIDAR_NAME()})" if p.get("top") == "lidar" else ""))
        if p.get("note"):
            bits.append(p["note"])
        out = {"note": "; ".join(bits)} if bits else {}
        for k in ("source", "confidence"):
            if p.get(k):
                out[k] = p[k]
        return out


def _aerial_rgb(e, n):
    a = raster.aerial()
    rgb = np.stack([a.sample(e, n, band=b) for b in range(3)], -1)
    return np.nan_to_num(rgb, nan=110.0)


def _context_colour(rgb):
    """A crown's foliage colour from the aerial where no photo shows it: the median of its green
    samples (others are paving or roofs seen through gaps), with the saturation lifted towards
    what the photos show (the aerial is greyer: haze, shadow inside the crowns)."""
    green = rgb[greenness(rgb) > 0.04]
    med = np.median(green if len(green) >= 5 else rgb, axis=0)
    m = med.mean()
    return np.clip(m + (med - m) * 1.5, 0, 255)


def clumps(p, rng, density, scale=1.1):
    """True for the points of `p` (N x 3, m) that fall in leafy clumps: a smooth random field of
    about `scale` m is thresholded so that a `density` fraction of space is kept, leaving gaps."""
    if density >= 0.999:
        return np.ones(len(p), bool)
    f = np.zeros(len(p))
    for _ in range(8):
        k = rng.normal(size=3)
        k *= 2 * np.pi / scale / np.linalg.norm(k)
        f += np.sin(p @ k + rng.uniform(0, 2 * np.pi))
    return f >= np.quantile(f, 1 - density)


def crown_cards(plant, k, cards, rng):
    """Leaf cards filling a crown: a dense shell under its top and round its sides, thinner inside,
    in clumps with gaps between them where the feature's `density` (0-1, default 1) is less than 1."""
    sprite, (s0, s1), cover = CROWN_LOOKS.get(plant.look, CROWN_LOOKS["broadleaf"])
    if plant.context:
        s0, s1, cover = s0 * CONTEXT_SIZE, s1 * CONTEXT_SIZE, cover * CONTEXT_DENSITY
    density = float(plant.props.get("density", 1.0))
    poly = plant.poly
    probe = sample_in(poly, 400, rng)
    g = plant.ground(probe[:, 0], probe[:, 1])
    t = plant.top(probe[:, 0], probe[:, 1], g)
    d, _ = edge_vectors(poly, probe)
    thick = float(np.mean(np.clip(t - plant.bottom(g, t, d), 0.2, None)))
    surface = poly.area + poly.exterior.length * thick * 0.6
    size_m = 0.5 * (s0 + s1)
    n = int(np.clip(cover * surface / (0.33 * size_m ** 2), 12, 6000))
    p = sample_in(poly, int(n / max(density, 0.2)) if density < 1 else n, rng)
    n = len(p)
    g = plant.ground(p[:, 0], p[:, 1])
    top = plant.top(p[:, 0], p[:, 1], g)
    d, out = edge_vectors(poly, p)
    bot = plant.bottom(g, top, d)
    T = np.clip(top - bot, 0.15, None)
    size = rng.uniform(s0, s1, n)
    side = d < 0.4 * size
    depth = np.where(side, rng.uniform(0, 1, n) * T, np.minimum(T, rng.exponential(0.16 * T + 0.12)))
    z = np.clip(top - depth - 0.22 * size, bot, None)
    keep = clumps(np.column_stack([p, z]), rng, density)
    if plant.kind == "tree":
        # Where the LiDAR surface stays below the crown base it saw what stands under the crown's
        # edge (a driveway, a woodpile, a fence, the deck) or a gap, not leaves: no cards there. Left
        # in, they hung 0.3-1 m above such places.
        keep &= top - g > float(plant.props.get("crown_base_m", 1.5)) - 0.2
    if not keep.any():
        return
    p, g, top, d, out, bot, T, size, depth, z = (a[keep] for a in (p, g, top, d, out, bot, T, size, depth, z))
    n = len(p)
    # Colour: the plant's own (from the photos) or the aerial's, varied with the aerial's light
    # and dark within the crown, darker deeper inside.
    air = _aerial_rgb(p[:, 0], p[:, 1])
    lum = air.mean(-1)
    base = plant.colour if plant.colour is not None else _context_colour(air)
    rgb = base[None, :] * np.clip((lum / max(np.median(lum), 1.0)) ** 0.5, 0.8, 1.2)[:, None]
    ao = 0.58 + 0.42 * (1 - np.clip(depth / np.minimum(T, 1.6), 0, 1))
    rgb = rgb * (ao * rng.normal(1.0, 0.07, n))[:, None] * rng.normal(1.0, 0.03, (n, 3))
    # Normal: out through the nearest side near the outline, up or down by height in the crown.
    r_in = max(float(d.max()), 0.3)
    wh = np.clip(1.0 - d / r_in, 0.0, 1.0)
    nz = (z - 0.5 * (top + bot)) / np.maximum(0.5 * T, 0.1) + 0.7
    nrm = np.column_stack([out[:, 0] * wh * 1.4, out[:, 1] * wh * 1.4, nz]) + rng.normal(0, 0.25, (n, 3))
    lo, hi = {"cherry": (25, 85)}.get(plant.look, (10, 80))
    cards.add(k, sprite, np.column_stack([p, z]), size, rng.uniform(0, 360, n), rng.uniform(lo, hi, n),
              rng.uniform(0, 360, n), rgb, nrm)
    if plant.props.get("flowers"):          # flowers on the outer shell
        m = max(int(0.12 * n), 4)
        pick = rng.choice(np.flatnonzero(depth < 0.35 * T + 0.1) if np.any(depth < 0.35 * T + 0.1) else np.arange(n), m)
        fl = np.array(_rgb(plant.props["flowers"]), float)[None, :] * rng.normal(1.0, 0.08, (m, 1))
        cards.add(k, "blossom", np.column_stack([p[pick], z[pick] + 0.05]), rng.uniform(0.22, 0.34, m),
                  rng.uniform(0, 360, m), rng.uniform(10, 80, m), rng.uniform(0, 360, m), fl, nrm[pick])


def bed_cards(plant, k, cards, rng):
    """Beds and clumps: clumps of upright cards on a jittered grid, low ground cover, flowers."""
    poly, h = plant.poly, plant.height
    base = plant.colour if plant.colour is not None else np.array([90, 110, 55], float)
    for part in BED_LOOKS.get(plant.look, BED_LOOKS["weeds"]):
        how, sprite = part[0], part[1]
        if how in ("clump", "rosette"):
            spacing, per, frac = part[2:]
            c = jittered_grid(poly, spacing, rng)
            m = len(c)
            g = plant.ground(c[:, 0], c[:, 1])
            colour = base if sprite != "weeds" or plant.look in ("weeds", "long-grass") else np.array([110, 128, 62], float)
            if sprite == "stalks":
                colour = np.array([132, 116, 90], float)  # dry stalks
            if plant.look in ("fern", "border") and sprite == "strap":
                colour = np.array([78, 111, 41], float)
            if plant.look == "border" and sprite == "flax":
                colour = np.array([66, 84, 40], float)  # flax in the border
            for j in range(per):
                yaw = rng.uniform(0, 360, m) + j * 180.0 / per
                size = h * frac * rng.uniform(0.8, 1.15, m)
                if how == "rosette":     # aeonium heads, flat-ish, on short stems
                    pos = np.column_stack([c, g + rng.uniform(0.2, 0.45, m) * h / 0.5])
                    pitch, size = rng.uniform(55, 85, m), rng.uniform(0.22, 0.34, m)
                    nrm = np.column_stack([np.zeros(m), np.zeros(m), np.ones(m)])
                else:                    # bottom-anchored upright clump
                    pos = np.column_stack([c, g - 0.03])
                    pitch = rng.uniform(0, 14, m)
                    a = np.radians(yaw)
                    nrm = np.column_stack([np.cos(a) * 0.5, np.sin(a) * 0.5, np.ones(m)])
                rgb = colour[None, :] * rng.normal(1.0, 0.08, (m, 1)) * rng.normal(1.0, 0.03, (m, 3))
                cards.add(k, sprite, pos, size, yaw, pitch, 0.0, rgb, nrm)
        elif how in ("cover", "flowers", "bush"):
            per_m2, (s0, s1) = part[2:]
            m = max(int(per_m2 * poly.area), 3)
            p = sample_in(poly, m, rng)
            g = plant.ground(p[:, 0], p[:, 1])
            if how == "cover":
                hh = min(h, 0.35) if plant.look != "ivy" else h
                z = g + rng.uniform(0.03, max(hh, 0.06), m)
                rgb = (base if plant.look == "ivy" else np.array([51, 67, 42], float))[None, :]
                pitch = rng.uniform(25, 88, m)
            elif how == "bush":       # young shrubs in a border, up to the bed's height
                z = g + rng.uniform(0.3, max(h, 0.6), m)
                rgb = np.array([82, 98, 50], float)[None, :]
                pitch = rng.uniform(10, 80, m)
            else:
                z = g + rng.uniform(0.35, 0.6, m) * h / 0.5
                rgb = np.array(_rgb(plant.props.get("flowers", "#c0587f")), float)[None, :]
                pitch = rng.uniform(20, 80, m)
            rgb = rgb * rng.normal(1.0, 0.08, (m, 1)) * rng.normal(1.0, 0.03, (m, 3))
            cards.add(k, sprite, np.column_stack([p, z]), rng.uniform(s0, s1, m), rng.uniform(0, 360, m), pitch,
                      rng.uniform(0, 360, m), rgb, np.tile([0.0, 0.0, 1.0], (m, 1)) + rng.normal(0, 0.3, (m, 3)))


def _curve(p0, p1, p2, n):
    """n + 1 points on the quadratic Bezier curve p0 -> p2 with control point p1."""
    t = np.linspace(0.0, 1.0, n + 1)[:, None]
    return (1 - t) ** 2 * p0 + 2 * (1 - t) * t * p1 + t ** 2 * p2


def _branch(out, pts, r0, r1):
    """Add the segments of a polyline as tapered tubes from radius r0 to r1."""
    radii = np.linspace(r0, r1, len(pts))
    for k in range(len(pts) - 1):
        out.append((pts[k], pts[k + 1], float(radii[k]), float(radii[k + 1])))


def tree_limbs(plant, rng):
    """Trunk, leaders and branches of a tree: [(p0, p1, r0, r1)] tapered segments, site coordinates.

    The trunk rises from each of the feature's `trunks` (or the crown's highest point) to a fork,
    under the crown base, or low down for a multi-stemmed tree (`stems`). Leaders curve up and out
    from the fork to points spread round the upper crown; each carries two side branches that reach
    out to the crown's edge. A sketch of the structure under the LiDAR crown, not a survey of it."""
    p = plant.props
    poly = plant.poly
    pts = sample_in(poly, 400, rng)
    g = plant.ground(pts[:, 0], pts[:, 1])
    top = plant.top(pts[:, 0], pts[:, 1], g)
    trunks = p.get("trunks") or [list(pts[np.argmax(top - g)])]
    stems = int(p.get("stems", 1))
    cb = float(p.get("crown_base_m", 1.5))
    out = []
    for x0, y0 in trunks:
        g0 = float(plant.ground(np.array([x0]), np.array([y0]))[0])
        rel = pts - [x0, y0]
        dist = np.hypot(rel[:, 0], rel[:, 1])
        ang = np.arctan2(rel[:, 1], rel[:, 0])
        near = dist < max(2.0, np.percentile(dist, 30))
        H = float(np.percentile((top - g0)[near], 90))
        dia = float(p.get("trunk_diameter_m", np.clip(0.035 * H, 0.08, 0.5)))
        r0 = dia / 2
        reach = float(np.percentile(dist, 85))
        base = np.array([x0, y0, g0 - 0.15])
        fork = base + [0.0, 0.0, 0.15 + (rng.uniform(0.9, 1.4) if stems > 1 else max(0.8 * cb, 1.0))]
        _branch(out, [base, fork], r0, 0.85 * r0)
        n_lead = stems if stems > 1 else 3 + int(H > 6)
        a_start = rng.uniform(0, 2 * np.pi)
        r_lead = (0.65 if stems > 1 else 0.5) * r0
        for j in range(n_lead):
            a = a_start + 2 * np.pi * (j + rng.uniform(-0.2, 0.2)) / n_lead
            off = np.abs((ang - a + np.pi) % (2 * np.pi) - np.pi)
            cand = np.flatnonzero((off < np.pi / n_lead + 0.2) & (dist > 0.2 * reach) & (dist < 0.8 * reach))
            if not len(cand):
                cand = np.argsort(off)[:5]
            k = cand[rng.integers(len(cand))]
            z_end = g0 + cb + 0.72 * max(top[k] - g0 - cb, 0.5)
            end = np.array([pts[k, 0], pts[k, 1], z_end])
            ctrl = fork + [0.25 * (end[0] - fork[0]), 0.25 * (end[1] - fork[1]), 0.6 * (end[2] - fork[2])]
            lead = _curve(fork, ctrl, end, 3)
            _branch(out, lead, r_lead, 0.25 * r_lead)
            for f, side in ((0.45, -1), (0.7, 1)):     # side branches towards the crown's edge
                q = _curve(fork, ctrl, end, 20)[int(f * 20)]
                b = a + side * rng.uniform(0.3, 0.9)
                off = np.abs((ang - b + np.pi) % (2 * np.pi) - np.pi)
                cand = np.flatnonzero((off < 0.5) & (dist > 0.6 * reach))
                if not len(cand):
                    continue
                m = cand[rng.integers(len(cand))]
                tip = np.array([pts[m, 0], pts[m, 1], g0 + cb + 0.55 * max(top[m] - g0 - cb, 0.5)])
                mid = q + 0.5 * (tip - q) + [0.0, 0.0, 0.25]
                rb = r_lead * (1 - 0.75 * f) * 0.55
                _branch(out, _curve(q, mid, tip, 2), rb, 0.25 * rb)
    return out


def stump_items(feats, ground):
    """Stumps from data/site/trees.geojson: centre (E, N, base height), radius, height of the cut
    face's centre above the base, lean of the trunk from vertical (deg, cut square to it) and the
    direction it leans (deg from north), face type (STUMP_FACES), and shape: a cut stump, or a
    `mound` (a grubbed-out root mound, drawn as a low dome). A stump stands on its `base_m` where
    given (a stump on a wall top), but never below the ground model, so it stays in view where
    the two disagree."""
    out = []
    for f in feats:
        p = f["properties"]
        if p.get("kind") != "stump":
            continue
        e, n = f["geometry"]["coordinates"][:2]
        g = float(ground(np.array([e]), np.array([n]))[0])
        base = max(float(p.get("base_m", g)), g)
        out.append({"id": p["id"], "name": p.get("name", p["id"]), "at": (e, n, base), "r": float(p["radius_m"]),
                    "h": float(p["height_m"]), "lean": float(p.get("lean_deg", 0.0)),
                    "toward": float(p.get("lean_toward_deg", 0.0)), "face": p.get("face", "weathered"),
                    "mound": p.get("shape") == "mound", "bark": p.get("bark", "#7d6a55"), "props": p})
    return out


def on_mounds(ground, stumps):
    """The ground with the root mounds among `stumps` added (a dome of their radius and height),
    so plants growing on a mound stand on it."""
    mounds = [s for s in stumps if s["mound"]]
    if not mounds:
        return ground

    def g(e, n):
        e, n = np.asarray(e, float), np.asarray(n, float)
        z = np.array(ground(e, n), float)
        for s in mounds:
            x, y, _ = s["at"]
            z += s["h"] * np.clip(1 - ((e - x) ** 2 + (n - y) ** 2) / s["r"] ** 2, 0, None)
        return z
    return g


def drape(tin, poly):
    """The ground TIN's triangles inside `poly`, cut at its edge (heights interpolated in the
    original triangle, so the drape lies exactly on the ground): vertices (N, 3), faces (M, 3)."""
    V, F = tin.vertices, tin.faces
    e0, n0, e1, n1 = poly.bounds
    xy = V[F][:, :, :2]
    hit = (xy[:, :, 0].max(1) >= e0) & (xy[:, :, 0].min(1) <= e1) & (xy[:, :, 1].max(1) >= n0) & (xy[:, :, 1].min(1) <= n1)
    verts, faces = [], []
    for tri in V[F[hit]]:
        t = Polygon(tri[:, :2])
        if not t.is_valid or t.area < 1e-9 or not t.intersects(poly):
            continue
        piece = t if poly.contains(t) else t.intersection(poly)
        if piece.geom_type == "GeometryCollection":
            piece = MultiPolygon([q for q in piece.geoms if q.geom_type == "Polygon"])
        if piece.is_empty or piece.area < 1e-6:
            continue
        a, b, c = tri
        m = np.array([[b[0] - a[0], c[0] - a[0]], [b[1] - a[1], c[1] - a[1]]])
        inv = np.linalg.inv(m)
        tris = [piece] if piece is t else list(shapely.constrained_delaunay_triangles(piece).geoms)
        for q in tris:
            pts = np.asarray(q.exterior.coords)[:3]
            if q is t:
                pts = tri[:, :2]
            uv = (inv @ (pts - a[:2]).T).T
            z = a[2] + uv[:, 0] * (b[2] - a[2]) + uv[:, 1] * (c[2] - a[2])
            base_i = len(verts)
            verts.extend(np.column_stack([pts, z]))
            ccw = (pts[1, 0] - pts[0, 0]) * (pts[2, 1] - pts[0, 1]) - (pts[1, 1] - pts[0, 1]) * (pts[2, 0] - pts[0, 0]) > 0
            faces.append((base_i, base_i + 1, base_i + 2) if ccw else (base_i, base_i + 2, base_i + 1))
    verts, faces = np.asarray(verts, float).reshape(-1, 3), np.asarray(faces, int).reshape(-1, 3)
    # Weld vertices shared by neighbouring triangles (to the millimetre), for smooth shading.
    _, first, inverse = np.unique(np.round(verts * 1000).astype(np.int64), axis=0, return_index=True,
                                  return_inverse=True)
    return verts[first], inverse.ravel()[faces]


def _r(a, nd=3):
    return [round(float(v), nd) for v in np.ravel(a)]


def layers(feats, frame, ground, tin, out_dir, seed=1):
    """Viewer layers for the vegetation in `feats` (data/site/trees.geojson) and the textures
    they use, written to `out_dir`. Returns (layers, summary)."""
    lot = Polygon(geojson.read(ROOT / "data" / "site" / "boundary.geojson")["features"][0]["geometry"]["coordinates"][0])
    gains = foliage_atlas(out_dir / "foliage.png")
    bark_texture(out_dir / "bark.jpg")
    stump_texture(out_dir / "stumps.jpg")
    groups = {"trees": ("veg-trees", f"Trees: crowns from the {LIDAR_NAME()}" + (", looks from the photos and videos" if settings().get("video", {}).get("clouds") else ""), "#4f6e3b"),
              "shrubs": ("veg-shrubs", "Shrubs and hedges", "#5d7a3a"),
              "beds": ("veg-beds", "Planted beds, ground cover and weeds", "#6d8f3c")}
    out = {key: {"plants": [], "cards": Cards(), "limbs": []} for key in groups}
    counts = {}
    st = stump_items(feats, ground)
    ground = on_mounds(ground, st)
    for f in feats:
        p = f["properties"]
        kind = p.get("kind")
        counts[kind] = counts.get(kind, 0) + 1
        if kind in ("stump", "lawn") or (kind == "patch" and p.get("look") != "weeds") or p.get("removed"):
            continue
        plant = Plant(f, ground, lot)
        if plant.poly is None:
            continue
        key = "trees" if kind == "tree" else "shrubs" if kind in ("shrub", "hedge") else "beds"
        g = out[key]
        k = len(g["plants"])
        rng = np.random.default_rng([seed, zlib_crc(plant.id)])
        g["plants"].append({"name": plant.name, "info": plant.info()})
        if kind in ("tree", "shrub", "hedge"):
            crown_cards(plant, k, g["cards"], rng)
        else:
            bed_cards(plant, k, g["cards"], rng)
        if kind == "tree":
            bark = np.array(_rgb(p.get("bark", "#6e6656")), float)
            for p0, p1, r0, r1 in tree_limbs(plant, rng):
                g["limbs"].append([k, *_r(frame.xyz([p0])[0], 2), *_r(frame.xyz([p1])[0], 2), round(r0, 3),
                                   round(r1, 3), *[int(v) for v in bark]])
    result, summary = [], {"features": counts}
    for key, (lid, name, colour) in groups.items():
        g = out[key]
        if not g["plants"]:
            continue
        layer = {"type": "vegetation", "id": lid, "name": name, "group": "Vegetation", "colour": colour,
                 "plants": g["plants"], "atlas": "foliage.png", "atlas_grid": list(ATLAS_GRID),
                 "cards": g["cards"].pack(frame, gains)}
        if g["limbs"]:
            layer["limbs"] = {"texture": "bark.jpg", "items": g["limbs"]}
        result.append(layer)
        summary[key] = {"plants": len(g["plants"]), "cards": g["cards"].count(), "limbs": len(g["limbs"])}
    # Stumps: a cylinder of bark with its cut face on top (leaning where the trunk leans), or a mound.
    if st:
        items = []
        for s in st:
            e, n, base = s["at"]
            a = math.radians(s["toward"])
            # Face direction in viewer coordinates (x east, z south).
            items.append({"plant": len(items), "at": _r(frame.xyz([[e, n, base]])[0], 3), "r": round(s["r"], 3),
                          "h": round(s["h"], 3), "lean": round(s["lean"], 1),
                          "dir": [round(math.sin(a), 4), round(-math.cos(a), 4)],
                          "face": list(STUMP_FACES).index(s["face"]) + 1, "mound": s["mound"]})
        plants = [{"name": s["name"], "info": {k: s["props"][k] for k in ("note", "source", "confidence")
                                                 if s["props"].get(k)}} for s in st]
        result.append({"type": "vegetation", "id": "stumps", "name": "Tree stumps (cut faces from the frames)",
                       "group": "Vegetation", "colour": "#8a6a4a", "plants": plants,
                       "stumps": {"texture": "stumps.jpg", "cells": len(STUMP_FACES) + 1, "items": items}})
        summary["stumps"] = len(items)
    # Lawn: the TIN's triangles inside the lawn, textured with the grass and its bare patches.
    for f in feats:
        p = f["properties"]
        if p.get("kind") != "lawn":
            continue
        poly = shapely.geometry.shape(f["geometry"])
        patches = [(shapely.geometry.shape(q["geometry"]), q["properties"].get("look", "sand"),
                    q["properties"].get("colour", "#cfa77e")) for q in feats
                   if q["properties"].get("kind") == "patch" and shapely.geometry.shape(q["geometry"]).intersects(poly)]
        fname = f"{p['id']}.jpg"
        e0, n0, e1, n1 = lawn_texture(out_dir / fname, poly, p.get("colour", "#7f9446"), patches,
                                      seed=zlib_crc(p["id"]) % 10000)
        v, faces = drape(tin, poly)
        result.append({"type": "vegetation", "id": f"veg-{p['id']}", "name": p.get("name", p["id"]),
                       "group": "Vegetation", "colour": p.get("colour", "#7f9446"),
                       "plants": [{"name": p.get("name", p["id"]), "info": {k: p[k] for k in ("note", "source", "basis")
                                                                             if p.get(k)}}],
                       "drape": lawn_drape(v, faces, (e0, n0, e1, n1), fname, frame)})
        summary[p["id"]] = {"area_m2": round(poly.area, 1), "triangles": len(faces)}
    if any(f["properties"].get("kind") == "lawn" for f in feats):
        grass_detail_texture(out_dir / "grass-detail.jpg")
    # Beds and the hedge's foot: soil and leaf litter on the ground under the plants.
    beds = [(shapely.geometry.shape(f["geometry"]), f["properties"]) for f in feats
            if f["properties"].get("kind") in ("bed", "hedge") and f["geometry"]["type"] == "Polygon"
            and f["properties"].get("look") not in ("ivy", "rosette", "long-grass")]   # those cover the ground
    if beds:
        litter_texture(out_dir / "litter.jpg")
        P, I, U, names = [], [], [], []
        for poly, p in beds:
            v, faces = drape(tin, poly)
            I.extend((faces + sum(len(q) for q in P)).ravel().tolist())
            P.append(v)
            U.append(v[:, :2] / 1.0)
            names.append(p.get("name", p["id"]))
        v = np.vstack(P)
        uv = np.vstack(U)
        uv -= np.floor(uv.min(axis=0))
        result.append({"type": "vegetation", "id": "veg-bed-floor", "name": "Soil and leaf litter under the beds",
                       "group": "Vegetation", "colour": "#5a4a38",
                       "plants": [{"name": "Soil and leaf litter under the beds and hedge",
                                   "info": {"note": "; ".join(names),
                                            "source": settings().get("labels", {}).get("bed_floor_source", "Outlines as the beds in data/site/trees.geojson")}}],
                       "drape": {"texture": "litter.jpg", "repeat": True, "positions": _r(frame.xyz(v)),
                                 "indices": I, "uv": _r(uv, 3)}})
        summary["bed_floor_triangles"] = len(I) // 3
    return result, summary


def zlib_crc(text):
    """A stable seed from a feature id."""
    return zlib.crc32(text.encode())


def map_site():
    """`domus canopy`: candidate crowns from the LiDAR, written for curating into data/site/trees.geojson."""
    extent = settings()["model"]["extent"]
    cands, grids = map_canopy(extent, removed_areas(read()))
    fc = geojson.collection([as_json(c) for c in cands], "canopy",
                            "Candidate crowns from the LiDAR canopy (surface less ground) and the aerial; "
                            "data/site/trees.geojson holds the curated features")
    out = ROOT / settings().get("rasters", {}).get("canopy", "data/sources/canopy.geojson")
    geojson.write(out, fc)
    kinds = {}
    for c in cands:
        k = "removed" if c.get("removed") else guess_kind(c)
        kinds[k] = kinds.get(k, 0) + 1
    print(f"{len(cands)} crowns over {grids['veg'].sum() * STEP ** 2:.0f} m2 of canopy: {kinds}; wrote {out}")
