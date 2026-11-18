"""Build the viewer's model from the site data.

    domus build

Writes model.json (geometry in viewer coordinates: x east, y up, z south of the origin in
data/site.toml, metres) and the draped textures to crs.OUTPUT. Re-run after editing anything in
data/. Every layer but the ground and the boundary is optional: a site shows what it has.
"""

import base64
import csv
import datetime
import itertools
import json
import os
import subprocess
from types import SimpleNamespace

import numpy as np
import shapely
from PIL import Image
from shapely.geometry import LineString
from shapely.ops import unary_union

from .. import __version__, options
from ..errors import DomusError
from ..geo import crs, geojson, mesh, raster
from ..geo.georef import invert_affine
from ..importers.nz import title
from ..model import canopy, features, house, house_texture, terrain, vegetation, walls
from ..options import design
from . import cache as buildcache

POINT_STEP = 0.0025     # m, the video point cloud's storage step
MODEL_FORMAT = 1        # how model.json says things, for the viewer; build.json records it with the Domus that built it

# House layers: key -> (name, panel colour). A part goes in its kind's layer (house.PARTS) or the one it names
# (`layer`); [house.layers.<key>] name and colour in data/site.toml name a layer or change these.
ROOF_NAMES = {"hip": "hipped", "skillion": "skillion", "gable": "gable"}
HOUSE_LAYERS = {
    "walls": ("walls (photo textures where photos and frames cover them)", "#cbd2da"),
    "eaves": ("eaves, fascia and soffit", "#eef1f1"),
    "bay": ("bay windows", None),
    "porch": ("porches", None),
    "steps": ("steps", None),
    "outdoor": ("decks, landings and other parts", None),
    "chimney": ("chimney", None),
    "fixtures": ("tanks and fixtures", None),
}


class Frame:
    """Site (E, N, height) <-> viewer (x east, y up, z south) coordinates."""

    def __init__(self, origin):
        self.e0, self.n0, self.h0 = origin

    def xyz(self, pts):
        p = np.asarray(pts, float)
        return np.column_stack([p[:, 0] - self.e0, p[:, 2] - self.h0, -(p[:, 1] - self.n0)])


def r(a, nd=3):
    return [round(float(v), nd) for v in np.ravel(a)]


def datum_note(vertical):
    """" (NZVD2016)" after a name of something with heights on that datum; nothing for a datum the site doesn't name."""
    return f" ({vertical})" if vertical else ""


def mesh_json(m, frame, colour, name, **extra):
    v = frame.xyz(m.vertices)
    return {"type": "mesh", "name": name, "colour": colour, "positions": r(v),
            "indices": [int(i) for i in np.ravel(m.faces)], **extra}


def wall_texture(m, wall, painted=False):
    """uv and texture fields for a wall layer (domus.walls), its image put in the viewer's data: the
    picture of the wall's own blocks if it names them, else the site's own picture of that kind of wall,
    or one painted for it (always, with `painted`); none for a material drawn plain."""
    tex = walls.wall_image(wall, crs.OUTPUT, painted, crs.ROOT / "data" / "walls")
    return {"uv": r(m.uv, 4), "texture": tex, "repeat": True} if tex else {}


PIPE_RADIUS = 0.05      # m: 100 mm drains


def pipe_layers(svc_feats, frame, ground, colours):
    """The drains of data/site/services.geojson as pipes below the ground ("pipes" layers: a viewer polyline of the
    pipe's centre and its radius), at the levels domus.drains works out for each, or [services] assumed_depth_m below
    the ground. [services.notes] says where each drain's levels came from, and a drain's own `follow_up` what's known
    to be wrong with them. Drains with neither levels nor an assumed depth are left out."""
    from shapely.geometry import LineString

    from ..model import drains

    by_id = {f["properties"]["id"]: f for f in svc_feats}
    cfg = crs.settings().get("services", {})
    assumed, notes = cfg.get("assumed_depth_m", {}), cfg.get("notes", {})
    points = drains.level_points(svc_feats)
    out = []
    for sid in dict.fromkeys([*points, *assumed]):
        f = by_id.get(sid)
        if f is None or f["geometry"]["type"] != "LineString":
            continue
        p = f["properties"]
        pts, chain, invert, known = drains.levels(f["geometry"]["coordinates"], points.get(sid, []), ground, assumed.get(sid))
        if known:
            line = LineString(f["geometry"]["coordinates"])
            lo, hi = min(v for _, v in known), max(v for _, v in known)
            span = f"{len(known)} of them, between {known[0][0]:.0f} and {known[-1][0]:.0f} m along its {line.length:.0f} m"
            n = {**cfg.get("default_note", {}), **notes.get(sid, {})}
            info = {"note": n.get("text", "Invert {lo}-{hi} from the levels recorded ({span}).").format(
                        lo=f"{lo:.2f}", hi=f"{hi:.2f}", span=span),
                    "confidence": n.get("confidence", "levels as recorded")}
            depth = ground(pts[:, 0], pts[:, 1]) - invert
        else:
            depth = np.full(len(chain), assumed[sid])
            info = {"note": f"Depth not recorded: drawn {assumed[sid]:.1f} m deep to invert.",
                    "confidence": "depth assumed"}
        if p.get("follow_up"):
            info["follow_up"] = p["follow_up"]
        z = invert + PIPE_RADIUS
        out.append({"type": "pipes", "id": f"pipe-{sid}", "name": f"{p['name']}, underground",
                    "group": "Services underground", "colour": colours.get(sid, "#2f2f2f"), "radius": PIPE_RADIUS,
                    "path": r(frame.xyz(np.column_stack([pts, z]))),
                    "info": {**info, "source": p.get("source", ""), "depth_m": r([depth.min(), depth.max()], 2)}})
    return out


def lines_json(polylines, frame, colour, name, heights, **extra):
    """polylines: list of Nx2 site coords; heights: callable(e, n) -> z, or 'z' to use column 3."""
    segs = []
    for pl in polylines:
        p = np.asarray(pl, float)
        z = p[:, 2] if heights == "z" else heights(p[:, 0], p[:, 1])
        v = frame.xyz(np.column_stack([p[:, :2], z]))
        for a, b in itertools.pairwise(v):
            if np.all(np.isfinite(a)) and np.all(np.isfinite(b)):
                segs.extend([*a, *b])
    return {"type": "lines", "name": name, "colour": colour, "segments": r(segs), **extra}


def drape_texture(extent, px, sampler, path, mode="RGB"):
    """Render a north-up image of the site extent at `px` m/pixel using sampler(E, N)->array."""
    e0, n0, e1, n1 = extent
    w = int(round((e1 - e0) / px))
    h = int(round((n1 - n0) / px))
    E, N = np.meshgrid(e0 + (np.arange(w) + 0.5) * px, n1 - (np.arange(h) + 0.5) * px)
    Image.fromarray(sampler(E, N), mode).save(path, quality=88)
    return {"width": w, "height": h, "file": path.name}


def aerial_sampler(E, N):
    a = raster.aerial()
    rgb = np.stack([a.sample(E, N, band=b) for b in range(3)], axis=-1)
    return np.nan_to_num(rgb, nan=200).clip(0, 255).astype(np.uint8)


def paved_under_canopy():
    """data/site/paved-under-canopy.geojson: where the aerial photo sees paving through tree crowns and deep shade,
    as a shapely polygon (the file's polygons together), or None."""
    from shapely.geometry import shape

    areas = [shape(f["geometry"]) for f in geojson.read_optional(crs.ROOT / "data" / "site" / "paved-under-canopy.geojson")]
    if not areas:
        return None
    return areas[0] if len(areas) == 1 else unary_union(areas)


def paving_grain():
    """The site's paving texture (data/features/paving.jpg, features.MATERIALS) in grey, and the metres one copy
    covers, or None if the site has none."""
    folder = crs.ROOT / "data" / "features"
    name = features.texture_file("paving", folder)
    if name is None:
        return None
    return np.asarray(Image.open(folder / name).convert("L"), np.float32), features.MATERIALS["paving"][2]


def _blur(a, sigma_px):
    import cv2

    return cv2.GaussianBlur(np.ascontiguousarray(a, np.float32), (0, 0), sigma_px)


def _photo_classes(rgb, grey_spread=0.18):
    """Brightness, greenness and greyness of aerial pixels: greenness is green less the mean of red and blue, over
    the brightness, and a pixel is grey if its colours spread by less than `grey_spread` of its brightest."""
    L = rgb.mean(axis=2)
    green = (rgb[..., 1] - 0.5 * (rgb[..., 0] + rgb[..., 2])) / np.maximum(L, 1)
    grey = (rgb.max(axis=2) - rgb.min(axis=2)) / np.maximum(rgb.max(axis=2), 1) < grey_spread
    return L, green, grey


def _ends(paving, key, default):
    """A [where it starts, where it's full] setting of [aerial.paving]: two different numbers."""
    value = paving.get(key, default)
    if not isinstance(value, (list, tuple)) or len(value) != 2 or value[0] == value[1]:
        raise DomusError(f"[aerial.paving] {key} = {value!r} in data/site.toml: give two different numbers, where it "
                         "starts and where it's full")
    return value[0], value[1]


def _paving_in_sun(photo, area, paving, px=0.08):
    """The paving's sunlit brightness and colour in the photo (`photo(E, N)`, RGB): the sun_percentile of the
    brightness of the grey pixels in `area` that are less green than sun_green, and the median colour of those at
    least sun_share of that bright ([aerial.paving], `paving`)."""
    e0, n0, e1, n1 = area.bounds
    E, N = np.meshgrid(np.arange(e0, e1, px), np.arange(n0, n1, px))
    rgb = photo(E, N).astype(np.float32)
    L, green, grey = _photo_classes(rgb, paving.get("grey", 0.18))
    sunlit = shapely.contains_xy(area, E, N) & grey & (green < paving.get("sun_green", 0.04))
    if not sunlit.any():
        raise DomusError("The aerial photo sees no paving in sun in data/site/paved-under-canopy.geojson, to take the "
                         "paving's colour from: take in some of the same paving in sun, or see [aerial.paving] in "
                         "docs/site-format.md")
    lsun = np.percentile(L[sunlit], paving.get("sun_percentile", 75))
    return lsun, np.median(rgb[sunlit & (L > paving.get("sun_share", 0.85) * lsun)], axis=0)


def paint_paving(photo, E, N, area, paving=None, grain=None):
    """The aerial photo with paving shown where the photo sees tree crowns or deep shade on it: the photo was taken
    from above in sun, while under the trees, where the model is seen from, the paving shows. `photo(E, N)` gives the
    photo's RGB at a grid of points on the site's grid (E, N), `area` is where it sees paving through the trees
    (paved_under_canopy()), `paving` holds [aerial.paving]'s settings (their defaults where it has none) and `grain`
    is the paving texture in grey with the metres one copy covers (paving_grain()), or None for none.

    Inside `area`, pixels that are foliage (greener than foliage_green, or not grey and darker than foliage_dark of
    the paving's sunlit brightness: crowns lean in the photo, so they are found by colour, not from the trees'
    outlines) take the paving's own sunlit colour from the photo (_paving_in_sun), with the texture's grain; paving
    in shade (darker than shade_dark of the sunlit brightness, once smoothed over shade_blur_m) is lifted to that
    colour keeping shade_detail of the photo's detail (the rest is the dapple of the crowns' light). Each of those
    settings is two numbers: where it starts, and where it's full. It all fades out over feather_m at the area's
    edges. Elsewhere the photo is unchanged."""
    paving = {} if paving is None else paving
    rgb = photo(E, N).astype(np.float32)
    px = abs(E[0, 1] - E[0, 0])
    inside = shapely.contains_xy(area, E, N).astype(np.float32)
    L, green, grey = _photo_classes(rgb, paving.get("grey", 0.18))
    lsun, base = _paving_in_sun(photo, area, paving)
    # Foliage: green, or darker than the paving in shade and not grey.
    g0, g1 = _ends(paving, "foliage_green", [0.02, 0.1])
    d0, d1 = _ends(paving, "foliage_dark", [0.6, 0.4])
    foliage = np.clip((green - g0) / (g1 - g0), 0, 1)
    foliage = np.maximum(foliage, (~grey) * np.clip((d0 * lsun - L) / ((d0 - d1) * lsun), 0, 1))
    foliage = _blur(foliage, paving.get("foliage_blur_m", 0.15) / px).clip(0, 1)
    # Shade on the paving: lift the slow brightness to the sunlit level, keep the detail.
    Lb = _blur(L, paving.get("shade_blur_m", 0.4) / px)
    s0, s1 = _ends(paving, "shade_dark", [0.95, 0.7])
    shade = np.clip((s0 * lsun - Lb) / ((s0 - s1) * lsun), 0, 1)
    detail = paving.get("shade_detail", 0.5)
    lifted = base * ((1 - detail) + detail * L / np.maximum(Lb, 1))[..., None]
    if grain is None:
        surface = base * np.ones(E.shape + (1,), np.float32)
    else:
        tex, (tu, tv) = grain
        ty = ((N / tv) % 1 * tex.shape[0]).astype(int).clip(0, tex.shape[0] - 1)
        tx = ((E / tu) % 1 * tex.shape[1]).astype(int).clip(0, tex.shape[1] - 1)
        surface = base * (_blur(tex, 0.5 * tex.shape[1] * px / tu)[ty, tx] / tex.mean())[..., None]
    out = rgb * (1 - shade[..., None]) + lifted * shade[..., None]
    out = out * (1 - foliage[..., None]) + surface * foliage[..., None]
    w = _blur(inside, paving.get("feather_m", 0.25) / px)[..., None]
    return (rgb * (1 - w) + out * w).clip(0, 255).astype(np.uint8)


def ground_photo_sampler(E, N):
    """The aerial photo with the paving under the site's trees painted in (paint_paving): the areas in
    data/site/paved-under-canopy.geojson, [aerial.paving] in data/site.toml and the site's paving texture."""
    return paint_paving(aerial_sampler, E, N, paved_under_canopy(), crs.settings().get("aerial", {}).get("paving", {}),
                        paving_grain())


PAPER = np.array([238, 236, 228], np.float32)
INK = np.array([128, 24, 24], np.float32)


def relief(E, N):
    """Hillshade of the ground model (0..1), light from the north-west."""
    h = terrain.ground().sample(E, N)
    step = abs(E[0, 1] - E[0, 0])
    gy, gx = np.gradient(np.nan_to_num(h, nan=np.nanmean(h)), step)
    gy = -gy  # rows run south
    az, alt = np.radians(315), np.radians(45)
    slope = np.arctan(np.hypot(gx, gy))
    aspect = np.arctan2(gy, -gx)
    shade = np.sin(alt) * np.cos(slope) + np.cos(alt) * np.sin(slope) * np.cos(az - aspect)
    return np.clip(0.55 + 0.45 * shade, 0, 1)


def relief_sampler(E, N):
    s = relief(E, N)[..., None]
    return (PAPER * (0.78 + 0.22 * s)).clip(0, 255).astype(np.uint8)


def scan_sampler(sheet, shift=(0.0, 0.0)):
    """A georeferenced scanned sheet (data/georef/<sheet>.json): its linework in red ink over a
    relief-shaded paper tone. `shift` moves the sheet (east, north) in metres from where its georeference puts it."""
    rec = json.loads((crs.ROOT / "data" / "georef" / f"{sheet}.json").read_text(encoding="utf-8"))
    ai = invert_affine(np.array(rec["A"]))
    scan = np.asarray(Image.open(crs.ROOT / rec["image"]).convert("L"), np.float32)
    from scipy.ndimage import map_coordinates

    def f(E, N):
        e, n = E - shift[0], N - shift[1]
        col = ai[0, 0] * e + ai[0, 1] * n + ai[0, 2]
        row = ai[1, 0] * e + ai[1, 1] * n + ai[1, 2]
        v = map_coordinates(scan, [row, col], order=1, cval=255)
        ink = np.clip((215 - v) / 135, 0, 1)[..., None]
        base = PAPER * (0.82 + 0.18 * relief(E, N)[..., None])
        return (base * (1 - ink) + INK * ink).clip(0, 255).astype(np.uint8)
    return f


def load_points(path):
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def git_commit():
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=crs.ROOT, capture_output=True,
                              text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


LAWN_JOIN_M = 1.0       # a new lawn this close to a lawn that is there is drawn as that lawn's grass


def skin_area(outline, walled, beside):
    """Where a bed, or a lawn on its own, is drawn within its `outline`: a little inside it, but right up to a
    wall (`walled`, the walls' own plan) and to another bed or lawn of the scheme that lies against it (`beside`),
    so that no strip of bare ground shows between a bed and its wall or between two beds."""
    return unary_union([outline.buffer(-0.03), outline.intersection(walled.buffer(0.04)), outline.intersection(beside.buffer(0.04))])


def sloping_foot(st):
    """What a flight on ground that slopes across its foot does about it, in words."""
    return (f"The ground rises {st['foot_rise_m']:.2f} m across its foot. Its lowest tread is level with the ground at the "
            f"high end and stands {st['foot_rise_m']:.2f} m above it at the low end, and the risers above it are equal.")


# Cut and fill colours for an option's drape: fill blue, cut orange, full strength at 1 m.
CUT_RGB = np.array([196, 84, 22], float)
FILL_RGB = np.array([38, 104, 176], float)


def option_layers(opt, res, frame, extent, cfg, existing_walls, lawns=(), existing_tin=None):
    """Viewer layers, panel summary and drape textures for one wall option (domus.design). `lawns` are the
    site's lawns (vegetation.lawns) and `existing_tin` the ground as it is: a lawn the option leaves alone stays
    drawn, on the option's ground, and a new lawn within a metre of it is drawn as the same grass. Both take the
    lawn's `<id>-plain.jpg`, its grass without its bare patches, which covers its `plain_box` (the caller sets
    that and paints it). The summary's `lawns_kept` lists the lawns drawn on the option's ground, and
    `lawn_pictures` the lawns whose grass a new lawn is drawn with."""
    d = res["design"]
    oid = opt["id"]
    group = f"Option: {opt['short']}"
    tag = {"group": group, "option": oid, "visible": False}
    e0, n0, e1, n1 = extent
    # The patch of designed ground: the regrade area and the walls, with a margin; the viewer hides
    # the existing ground inside `mask` (a little inside the patch) while the option is shown.
    walls_bb = [LineString(w["line"]).bounds for w in d.walls]
    bb = np.array([d.garden.bounds, *walls_bb])
    patch = [float(bb[:, 0].min() - 1.5), float(bb[:, 1].min() - 1.5), float(bb[:, 2].max() + 1.5),
             float(bb[:, 3].max() + 1.5)]
    patch = [round(v * 2) / 2 for v in patch]   # on the ground grid
    mask = [patch[0] + 0.6, patch[1] + 0.6, patch[2] - 0.6, patch[3] - 0.6]
    kept = [w for w in existing_walls if w["id"] in res["keep_existing"]]
    wall_defs = [w["def"] for w in res["walls"]]
    # The ground has a clean edge round the flights of steps, and runs level to the house's walls.
    crisp = [(unary_union([st.poly for st in d.stairs]), False)] if d.stairs else []
    if not design.house_outline().is_empty:
        crisp.append((design.house_outline(), True))
    tin = terrain.build(patch, cfg["model"]["terrain_step_m"], wall_defs + kept, surface=d.drawn, edges=crisp)
    uv = np.column_stack([(tin.vertices[:, 0] - e0) / (e1 - e0), (tin.vertices[:, 1] - n0) / (n1 - n0)])
    out = [{**mesh_json(tin, frame, "#9fb58a", f"{opt['short']}: designed ground"), "id": f"option-{oid}-ground",
            "role": "ground", "uv": r(uv, 5), **tag,
            "mask": r([(mask[0] - e0) / (e1 - e0), (mask[1] - n0) / (n1 - n0),
                       (mask[2] - e0) / (e1 - e0), (mask[3] - n0) / (n1 - n0)], 5)}]
    # Lawns and beds on the designed ground.
    from shapely.geometry import Polygon as SPoly

    from ..model.vegetation import bed_texture, drape, grass_detail_texture

    if any(t.kind not in ("lawn", "ground") for t in d.terraces) and not (crs.OUTPUT / "bed.jpg").exists():
        bed_texture(crs.OUTPUT / "bed.jpg")
    if any(t.kind == "lawn" for t in d.terraces) and not (crs.OUTPUT / "grass-detail.jpg").exists():
        grass_detail_texture(crs.OUTPUT / "grass-detail.jpg")
    # A lawn or a bed stops at the walls and steps that stand in it, and runs up to the house's wall. It meets a
    # wall at the wall's own face and back, where the ground under it does (terrain.build), and keeps a little
    # clear of a flight of steps.
    walled = []
    for w in wall_defs:
        line = terrain.wall_levels(w)[0]
        walled.append(SPoly(np.vstack([line, mesh.offset_behind(line, w["thickness"], w["side"])[::-1]])).buffer(0))
    walled = unary_union(walled)
    stepped = unary_union([st.poly for st in d.stairs]).buffer(0.01)
    stand = unary_union([walled, stepped]) if wall_defs or d.stairs else None
    reach = d.garden.buffer(design.regrade_settings().get("house_margin_m", 0.6) + 0.03).intersection(design.title())
    pictures = []       # the lawns whose grass a new lawn is drawn with
    for t in d.terraces:
        if t.kind == "ground":      # a regrade only: drawn as the ground round it
            continue
        poly = t.poly.intersection(reach)
        lawn = t.kind == "lawn"
        # A new lawn beside a lawn that is there is that lawn's grass, so the two read as one. It keeps off the
        # walls and steps, as every lawn and bed does, and runs to its own outline elsewhere: the lawn that is
        # kept starts there, and a gap would show as a line between them.
        host = next((x for x in lawns if "plain_box" in x and t.poly.distance(x["poly"]) < LAWN_JOIN_M), None) if lawn else None
        if host:
            poly = t.poly.intersection(d.garden.buffer(design.regrade_settings().get("house_margin_m", 0.6))).intersection(design.title())
        if stand is not None:
            poly = poly.difference(unary_union([walled, stepped.buffer(0.02)]) if host else stand)
        if poly.is_empty:
            continue
        polys = list(getattr(poly, "geoms", [poly]))
        beside = unary_union([o.poly for o in d.terraces if o is not t and o.kind != "ground"])
        m = mesh.Mesh()
        for q in polys:
            if q.geom_type != "Polygon" or q.area < 1e-3:
                continue
            v, f = drape(tin, q if host else skin_area(q, walled, beside))
            m.add(v + [0, 0, 0 if host else 0.012], f)
        if not len(m):
            continue
        if host:
            out.append({"type": "vegetation", "id": f"option-{oid}-terrace-{t.id}", "role": "skin",
                        "name": f"{opt['short']}: {t.name} ({t.poly.area:.0f} m2)", "colour": host["colour"],
                        "plants": [{"name": t.name, "info": {"note": f"New lawn, drawn as the grass of {host['name']}."}}],
                        "drape": vegetation.lawn_drape(m.vertices, m.faces, host["plain_box"],
                                                       f"{host['id']}-plain.jpg", frame), **tag})
            if host["id"] not in pictures:
                pictures.append(host["id"])
            continue
        tile = 0.5 if lawn else 2.0      # the grass tile's and the planted bed tile's size on the ground
        # Tiled from the viewer's origin: from the grid's own, millions of tiles away, the viewer's 32-bit numbers
        # couldn't place the texture within a tile.
        out.append({**mesh_json(m, frame, "#6f9a48" if lawn else "#6b5a45",
                                f"{opt['short']}: {t.name} ({t.poly.area:.0f} m2)"),
                    "id": f"option-{oid}-terrace-{t.id}", "role": "skin",
                    "uv": r((m.vertices[:, :2] - (frame.e0, frame.n0)) / tile, 4),
                    "texture": "grass-detail.jpg" if lawn else "bed.jpg", "repeat": True,
                    "tint": "#7aa552" if lawn else None, **tag})
    # The lawns that are there stay drawn where the option leaves them: on its ground inside the mask, less its
    # own lawns and beds and the walls and steps that stand in them, and on the ground as it is outside it. They
    # are drawn as their grass without the bare patches they have today (the picture a new lawn beside them takes),
    # as the beds, their plants and the stumps are not drawn while an option is shown: the soil under a clump of
    # weeds would show as a bare spot with the weeds gone.
    inside = shapely.geometry.box(*mask)
    own = [t.poly for t in d.terraces if t.kind != "ground"]
    taken = unary_union(own + ([stand] if stand is not None else [])) if own or stand is not None else None
    kept = []
    for lawn in lawns:
        if not lawn["poly"].intersects(inside):
            continue
        here = lawn["poly"].intersection(inside)
        if taken is not None:
            here = here.difference(taken)
            here = here.buffer(-0.15).buffer(0.15).intersection(here)       # less the slivers a wall cuts off
        V, F = [], []
        for part, ground_tin in ((here, tin), (lawn["poly"].difference(inside), existing_tin)):
            for q in getattr(part, "geoms", [part]):
                if ground_tin is None or q.geom_type != "Polygon" or q.area < 1e-3:
                    continue
                v, f = drape(ground_tin, q)
                F.append(f + sum(len(x) for x in V))
                V.append(v)
        if not V:
            continue
        out.append({"type": "vegetation", "id": f"option-{oid}-lawn-{lawn['id']}", "role": "skin",
                    "name": f"{opt['short']}: {lawn['name']}, where it is left as it is", "colour": lawn["colour"],
                    "plants": [{"name": lawn["name"], "info": {"note": "Left as it is by this option."}}],
                    "drape": vegetation.lawn_drape(np.vstack(V), np.vstack(F), *(
                        (lawn["plain_box"], f"{lawn['id']}-plain.jpg") if "plain_box" in lawn else
                        (vegetation.lawn_box(lawn["poly"]), f"{lawn['id']}.jpg")), frame), **tag})
        kept.append(lawn["id"])
    # Walls.
    for w in res["walls"]:
        info = {k: w[k] for k in ("id", "name", "material", "length_m", "height_min_m", "height_max_m",
                                  "retained_max_m", "face_area_m2", "top_min", "top_max", "toe_min", "toe_max")}
        info["checks"] = "; ".join(w["flags"]) or "none"
        out.append({**mesh_json(w["mesh"], frame, walls.MATERIAL_COLOURS.get(w["material"], "#999"),
                                f"{w['id']} {w['name']} ({w['material']})"),
                    "id": f"option-{oid}-{w['id']}", **tag,
                    **wall_texture(w["mesh"], w["def"], w["def"]["properties"].get("picture") == "painted"), "info": info})
    # Steps.
    stair_keys = ("id", "name", "material", "risers", "riser_m", "going_m", "rise_m", "width_m", "length_m", "foot", "head")
    for st in res["stairs"]:
        colour, picture = design.STEP_LOOKS.get(st["material"], (walls.MATERIAL_COLOURS.get(st["material"], "#b9b6ae"), None))
        look = {}
        if picture and hasattr(st["mesh"], "uv"):       # a flight drawn with a picture: paint it once, lay it by the mesh's uv
            from ..model import wall_looks

            if not (crs.OUTPUT / picture[0]).exists():
                wall_looks.paint(st["material"], crs.OUTPUT / picture[0], picture[1])
            look = {"uv": r(st["mesh"].uv, 4), "texture": picture[0], "repeat": True}
        out.append({**mesh_json(st["mesh"], frame, colour,
                                f"{st['id']} {st['name']} ({st['risers']} risers of {st['riser_m']:.2f} m)"),
                    "id": f"option-{oid}-steps-{st['id']}", **tag, **look,
                    "info": {**{k: st[k] for k in stair_keys}, "checks": "; ".join(st["flags"]) or "none",
                             **({"note": sloping_foot(st)} if "foot_rise_m" in st else {})}})
    # Contours of the designed ground over the patch; the viewer drops the existing ones inside the mask.
    E, N = terrain.grid([mask[0] - 0.25, mask[1] - 0.25, mask[2] + 0.25, mask[3] + 0.25], 0.25)
    H = d.sample(E, N)
    # No contour where a wall, a flight of steps or the house stands: the ground steps there, under them.
    under = [g for g in (stand, design.house_outline()) if g is not None and not g.is_empty]
    if under:
        H = np.where(shapely.contains_xy(unary_union(under).buffer(0.13), E, N), np.nan, H)
    levels = np.arange(np.floor(np.nanmin(H)), np.ceil(np.nanmax(H)) + 0.01, 0.25)
    cs = terrain.contours(E, N, H, levels)
    segs = [[(x0, y0, lv + 0.03), (x1, y1, lv + 0.03)] for lv in levels for x0, y0, x1, y1 in cs[lv]]
    out.append({**lines_json(segs, frame, "#3d3a33", f"{opt['short']}: contours 0.25 m{datum_note(cfg.get('crs', {}).get('vertical', ''))}", "z"),
                "id": f"option-{oid}-contours", "role": "contours", **tag,
                "labels": [{"text": f"{lv:.0f}", "at": r(frame.xyz([[*cs[lv][len(cs[lv]) // 2][:2], lv + 0.1]])[0], 2)}
                           for lv in levels if abs(lv - round(lv)) < 1e-6 and cs.get(lv)]})
    # Cut and fill drape: the plain relief, coloured by the change in ground level.
    def cutfill(Eg, Ng):
        base = relief_sampler(Eg, Ng).astype(float)
        if base.ndim == 2:
            base = np.repeat(base[..., None], 3, axis=-1)
        dz = np.zeros(Eg.shape)
        inside = (Eg > patch[0]) & (Eg < patch[2]) & (Ng > patch[1]) & (Ng < patch[3])
        dz[inside] = d.sample(Eg[inside], Ng[inside]) - d.existing.sample(Eg[inside], Ng[inside])
        dz = np.nan_to_num(dz)
        a = np.clip(np.abs(dz) / 1.0, 0, 1)[..., None] * (np.abs(dz) > 0.02)[..., None]
        a = np.where(a > 0, 0.25 + 0.75 * a, 0)
        col = np.where((dz > 0)[..., None], FILL_RGB, CUT_RGB)
        rgb = base * (1 - a) + col * a
        # a fine line every 0.25 m of change
        band = (np.abs(dz) > 0.05) & (np.abs((dz / 0.25) - np.round(dz / 0.25)) < 0.06)
        rgb[band] *= 0.55
        return rgb.clip(0, 255).astype(np.uint8)

    tex = {f"cutfill-{oid}": {"label": f"Cut and fill, {opt['short']}", "option": oid,
                              **drape_texture(extent, 0.08, cutfill, crs.OUTPUT / f"cutfill-{oid}.jpg")}}
    walls_out = [{k: w[k] for k in ("id", "name", "material", "length_m", "height_min_m", "height_max_m",
                                    "retained_max_m", "face_area_m2", "top_min", "top_max", "line_so", "flags")} for w in res["walls"]]
    summ = {"id": oid, "name": opt["name"], "short": opt["short"], "description": opt["description"].replace("\n", " "),
            "file": opt["path"], "walls": walls_out, "terraces": res["terraces"], "volumes": res["volumes"],
            "flat": {k: v for k, v in res["flat"].items() if k != "points"}, "areas": res["areas"],
            "edge": res["edge"],
            "drains": [x for x in res["drains"] if "flag" in x],
            "keep_existing": res["keep_existing"],
            "removed_existing_m": res["removed_existing_m"],
            "stairs": [{k: st[k] for k in (*stair_keys, "line_so", "flags", "foot_rise_m") if k in st}
                       for st in res["stairs"]],
            "steps": len(res["stairs"]) or int(opt.get("steps", 0)), "lawns_kept": kept, "lawn_pictures": pictures}
    return out, summ, tex


def add_ground(b):
    """Ground."""
    cfg, extent, frame, ground, labels, layers, wall_defs = b.cfg, b.extent, b.frame, b.ground, b.labels, b.layers, b.wall_defs
    tin = terrain.build(extent, cfg["model"]["terrain_step_m"], wall_defs)
    e0, n0, e1, n1 = extent
    uv = np.column_stack([(tin.vertices[:, 0] - e0) / (e1 - e0), (tin.vertices[:, 1] - n0) / (n1 - n0)])
    # The ground's drapes: the aerial photo (with paving under the trees where the site records it, and as
    # taken), the site's georeferenced sheets ([[drapes]] in data/site.toml), the plain relief. They read only the
    # site's files, so the last build's are taken when those haven't changed.
    k = buildcache.key(b.cache.site, "ground") if b.cache.enabled else None
    textures = b.cache.get("ground", k) if k else None
    if textures is None:
        before = buildcache.snapshot(crs.OUTPUT)
        textures = ground_textures(cfg, extent)
        if k:
            b.cache.put("ground", k, textures, buildcache.written(crs.OUTPUT, before))
    layers.append({**mesh_json(tin, frame, "#9fb58a",
                               labels.get("ground", "Ground (LiDAR, with walls cut in)")),
                   "id": "ground", "uv": r(uv, 5), "group": "Ground"})
    for f in geojson.read_optional(terrain.fixes_path()):
        if f["geometry"]["type"] == "Polygon":
            layers.append({**lines_json([np.asarray(f["geometry"]["coordinates"][0])], frame, "#ff6a3d",
                                        f"Ground not from LiDAR: {f['properties']['name']}",
                                        lambda e, n: ground(e, n) + 0.08),
                           "id": f"fix-{f['properties']['id']}", "group": "Ground",
                           "info": {k: v for k, v in f["properties"].items() if k in ("note", "basis")}})
    b.textures = textures
    b.tin = tin


def ground_textures(cfg, extent):
    """The ground's drape pictures, written to the output folder: {key: {label, width, height, file}}."""
    textures = {}
    if raster.AERIAL.exists():
        aerial = cfg.get("aerial", {})
        key = aerial.get("key", "aerial")
        if paved_under_canopy() is not None:
            textures[key] = {"label": aerial.get("label", "Aerial photo"),
                             **drape_texture(extent, 0.08, ground_photo_sampler, crs.OUTPUT / f"{key}.jpg")}
            textures[f"{key}-raw"] = {"label": aerial.get("label_raw", "Aerial photo as taken"),
                                      **drape_texture(extent, 0.08, aerial_sampler, crs.OUTPUT / f"{key}-raw.jpg")}
        else:
            textures[key] = {"label": aerial.get("label", "Aerial photo"),
                             **drape_texture(extent, 0.08, aerial_sampler, crs.OUTPUT / f"{key}.jpg")}
    for sheet in cfg.get("drapes", []):
        textures[sheet["key"]] = {"label": sheet["label"],
                                  **drape_texture(extent, sheet.get("px", 0.05),
                                                  scan_sampler(sheet["georef"], sheet.get("shift_m", (0.0, 0.0))),
                                                  crs.OUTPUT / f"{sheet['key']}.jpg")}
    textures["relief"] = {"label": "Plain relief", **drape_texture(extent, 0.1, relief_sampler, crs.OUTPUT / "relief.jpg")}
    return textures


def add_contours(b):
    """Contours from the ground grid."""
    extent, frame, dem, layers, vertical = b.extent, b.frame, b.dem, b.layers, b.vertical
    E, N = terrain.grid(extent, 0.25)
    H = dem.sample(E, N)
    lo, hi = np.floor(np.nanmin(H)), np.ceil(np.nanmax(H))
    levels = np.arange(lo, hi + 0.01, 0.25)
    cs = terrain.contours(E, N, H, levels)
    major = [lv for lv in levels if abs(lv - round(lv)) < 1e-6]
    for name, sel, colour in (("Contours 1 m", major, "#3d3a33"),
                              ("Contours 0.25 m", [lv for lv in levels if lv not in major], "#6b675e")):
        segs = []
        for lv in sel:
            for x0, y0, x1, y1 in cs[lv]:
                segs.append([(x0, y0, lv + 0.03), (x1, y1, lv + 0.03)])
        layers.append({**lines_json(segs, frame, colour, f"{name}{datum_note(vertical)}", "z"),
                       "id": "contours-major" if sel is major else "contours-minor", "group": "Ground",
                       "labels": [{"text": f"{lv:.0f}", "at": r(frame.xyz([[*cs[lv][len(cs[lv]) // 2][:2], lv + 0.1]])[0], 2)}
                                  for lv in major if cs.get(lv)] if sel is major else []})


def add_boundaries(b):
    """Boundaries."""
    cfg, frame, ground, layers = b.cfg, b.frame, b.ground, b.layers
    bnd = geojson.read(crs.ROOT / "data" / "site" / "boundary.geojson")["features"]
    main_boundary = cfg.get("boundary", {}).get("main", bnd[0]["properties"]["id"])
    for f in bnd:
        ring = f["geometry"]["coordinates"][0]
        colour = "#ffd400" if f["properties"]["id"] == main_boundary else "#43b7ff"
        layers.append({**lines_json([np.asarray(ring)], frame, colour, f["properties"]["name"],
                                    lambda e, n: ground(e, n) + 0.08),
                       "id": f["properties"]["id"], "group": "Boundaries",
                       "visible": f["properties"]["id"] == main_boundary,
                       "info": {k: v for k, v in f["properties"].items() if k in ("area_m2", "source", "note")}})
    for f in title.read():
        p, g = f["properties"], f["geometry"]
        parts = g["coordinates"] if g["type"] in ("MultiPolygon", "MultiLineString") else [g["coordinates"]]
        lines = [np.asarray(ring)[:, :2] for part in parts for ring in (part if "Polygon" in g["type"] else [part])]
        easement = p.get("kind") == "easement"
        layers.append({**lines_json(lines, frame, "#ff7ad9" if easement else "#c8a2ff", p["name"], lambda e, n: ground(e, n) + 0.06),
                       "id": p["id"], "group": "Boundaries", "visible": False,
                       "info": {k: v for k, v in p.items() if k in ("intent", "estate", "type", "titles", "area_m2", "source", "note")}})


def add_house(b):
    """House: walls, roof and the parts in data/site/house.geojson, textured through an atlas (house_texture) and a repeating roof-tile image."""
    cfg, frame, ground, layers, summary = b.cfg, b.frame, b.ground, b.layers, b.summary
    hfc = geojson.read_optional(crs.ROOT / "data" / "site" / "house.geojson")
    hcfg = cfg.get("house", {})
    if hfc:
        hm = house.build(hfc, hcfg["fl_upper"], hcfg["roof_pitch_deg"], hcfg["roof_plane_at_wall"],
                         hcfg["eaves_overhang_m"], ground, **house.roof_options(hcfg))
        extra = [k for parts in hm.solids.values() for _, k in parts if k.startswith("#")]
        looks = house_texture.Looks(hcfg, crs.ROOT / "data" / "house")
        pal_uv, house_photos = house_texture.build_atlas(hm.faces, crs.OUTPUT / "house-atlas.jpg", looks, extra=extra)
        tile_m = house_texture.roof_texture(crs.OUTPUT / "house-roof.jpg", looks)
        hparts, first = {"walls": house.UVMesh(), "eaves": house.UVMesh()}, {}
        for f in hm.faces:
            hparts.setdefault(f.spec.get("layer", "walls"), house.UVMesh()).extend(f.mesh)
        for k, parts in hm.solids.items():
            for m, key in parts:
                m.uv = np.tile(pal_uv[key], (len(m.vertices), 1))
                hparts.setdefault(k, house.UVMesh()).extend(m)
                first.setdefault(k, key)
        named = hcfg.get("layers", {})
        sources = {}
        for f in hfc:
            fp = f["properties"]
            if fp.get("id") == "house-walls":
                keys = ("walls", "eaves")
            elif "part" in fp:
                keys = (fp.get("layer", house.PARTS.get(fp["part"])),)
            else:
                continue
            for k in keys:
                if fp.get("source"):
                    sources.setdefault(k, []).append(fp["source"])
        for k, m in hparts.items():
            if not len(m):
                continue
            name, colour = HOUSE_LAYERS.get(k, (k, None))
            name = named.get(k, {}).get("name", name)
            colour = named.get(k, {}).get("colour") or colour or "#" + "".join(f"{v:02x}" for v in looks.rgb(first.get(k, "concrete")))
            src = "; ".join(dict.fromkeys(sources.get(k, [])))
            if k == "walls":
                src += hcfg.get("walls_source_note", "")
            label = "walls (drawn, no photos yet)" if k == "walls" and not house_photos else name
            layers.append({**mesh_json(m, frame, colour, f"House: {label}"), "id": f"house-{k}", "group": "House",
                           "uv": r(m.uv, 5), "texture": "house-atlas.jpg", "info": {"source": src}})
            if k == "walls":
                roof_hex = "#" + "".join(f"{v:02x}" for v in looks.rgb(looks.roof_colour()))
                layers.append({**mesh_json(hm.roof, frame, roof_hex,
                                           f"House: roof ({ROOF_NAMES[hcfg.get('roof_form', 'hip')]}, {hcfg['roof_pitch_deg']:g} degrees"
                                           + hcfg.get("roof_name_note", "") + ")"),
                               "id": "house-roof", "group": "House", "uv": r(hm.roof.uv / tile_m, 4),
                               "texture": "house-roof.jpg", "repeat": True,
                               "info": {"source": hcfg.get("roof_source", "the house's outline and [house] in data/site.toml")}})
        summary["house"] = {**hm.info, "fl_upper": hcfg["fl_upper"],
                            "fl_lower": round(hcfg.get("fl_lower", hcfg["fl_upper"]), 3),
                            "photo_textures": house_photos}


def add_ifc(b):
    """A designer's model of the house ([ifc] in data/site.toml: its parts as a mesh file and a list of layers), each part its own layer, hidden until switched on."""
    cfg, frame, layers = b.cfg, b.frame, b.layers
    ifc = cfg.get("ifc")
    if ifc and (crs.ROOT / ifc["meta"]).exists():
        meta = json.loads((crs.ROOT / ifc["meta"]).read_text(encoding="utf-8"))
        ifc_meshes = np.load(crs.ROOT / ifc["meshes"])
        fit = meta["fit"]
        for lay in meta["layers"]:
            m = mesh.Mesh(ifc_meshes[f"{lay['key']}_v"], ifc_meshes[f"{lay['key']}_f"])
            layers.append({**mesh_json(m, frame, lay["colour"], f"{ifc['name']}: {lay['name']}"),
                           "id": f"{ifc['id_prefix']}-{lay['key']}", "group": ifc["group"], "visible": False,
                           "info": {"source": ifc["source"],
                                    "note": ifc["note"].format(misfit=fit["outline_misfit_median_m"])}})


def add_walls(b):
    """Walls, textured from the video frames (domus.walls.MATERIAL_TEXTURES) on the same geometry."""
    frame, layers, summary, wall_defs = b.frame, b.layers, b.summary, b.wall_defs
    wall_info = []
    walls.set_out(wall_defs, b.dem)
    for w in wall_defs:
        m, info = walls.mesh(w, b.dem)
        info = {"id": w["id"], "name": w["name"], "material": w["material"], "confidence": w["confidence"],
                **{k: round(v, 2) for k, v in info.items()},
                "note": w["properties"].get("note", "")}
        wall_info.append(info)
        layers.append({**mesh_json(m, frame, walls.MATERIAL_COLOURS.get(w["material"], "#999"), w["name"]),
                       "id": f"wall-{w['id']}", "group": "Existing walls", **wall_texture(m, w),
                       "info": info})
    summary["walls"] = wall_info


def add_options(b):
    """Wall options (options/*.toml, domus.design): each one's designed ground over the regrade area, its walls, lawns and beds, contours and a cut-and-fill drape, hidden until picked in the viewer's options panel."""
    cfg, extent, frame, layers, summary, textures, wall_defs = b.cfg, b.extent, b.frame, b.layers, b.summary, b.textures, b.wall_defs
    summary["options"] = []
    b.removed_features = {}     # site feature id -> the options that take it out
    b.lawns_redrawn = {}        # lawn id -> the options that draw it themselves, on their own ground
    lawns = vegetation.lawns(canopy.read())
    found = [(opt, design.evaluate(opt)) for opt in options.load_all()]
    # Each lawn that is there has a plain picture, its grass without its bare patches: the options draw the lawn
    # with it where they keep it, and the new lawns beside it, which it reaches.
    for lawn in lawns if found else ():
        new = [t.poly for _, res in found for t in res["design"].terraces
               if t.kind == "lawn" and t.poly.distance(lawn["poly"]) < LAWN_JOIN_M]
        lawn["plain_reach"] = unary_union([*new, lawn["poly"]]).buffer(0.2).bounds
        lawn["plain_box"] = vegetation.lawn_plain_box(lawn["poly"], lawn["plain_reach"])[0]
    # An option's layers come from the last build when nothing they're drawn from has changed: the site, the option's
    # own file, and the lawns' pictures, which every option's new lawns reach into.
    reach = [[lawn["id"], lawn.get("plain_box"), lawn.get("plain_reach")] for lawn in lawns]
    for opt, res in found:
        k = buildcache.key(b.cache.site, "option", opt, (crs.ROOT / opt["path"]).read_bytes().hex(), reach) if b.cache.enabled else None
        cached = b.cache.get(f"option-{opt['id']}", k) if k else None
        if cached is None:
            before = buildcache.snapshot(crs.OUTPUT)
            opt_layers, opt_summary, opt_textures = option_layers(opt, res, frame, extent, cfg, wall_defs, lawns, b.tin)
            if k:
                b.cache.put(f"option-{opt['id']}", k, [opt_layers, opt_summary, opt_textures], buildcache.written(crs.OUTPUT, before))
        else:
            opt_layers, opt_summary, opt_textures = cached
        layers.extend(opt_layers)
        textures.update(opt_textures)
        summary["options"].append(opt_summary)
        for lawn_id in opt_summary["lawns_kept"]:
            b.lawns_redrawn.setdefault(lawn_id, []).append(opt["id"])
        for fid in opt.get("remove_features", []):
            b.removed_features.setdefault(fid, []).append(opt["id"])
    shared_textures(layers)
    for lawn in lawns:
        if any(lawn["id"] in o["lawn_pictures"] + o["lawns_kept"] for o in summary["options"]):
            vegetation.lawn_plain_texture(crs.OUTPUT / f"{lawn['id']}-plain.jpg", lawn["poly"], lawn["colour"], lawn["plain_reach"],
                                          seed=vegetation.zlib_crc(lawn["id"]) % 10000)
    if summary["options"]:
        summary["options_site"] = {"flat_slope": design.regrade_settings().get("flat_slope", 0.05),
                                   "garden_m2": round(design.regrade_area().area, 1),
                                   "frame": design.settings()["frame"]}


def shared_textures(layers):
    """The planted beds' and the grass's tiles, which options' layers share: an option taken from the last build
    doesn't paint them, so they're painted here if a layer uses one and it isn't there."""
    from ..model.vegetation import bed_texture, grass_detail_texture

    used = {layer.get("texture") for layer in layers} | {(layer.get("drape") or {}).get("detail") for layer in layers}
    for name, paint in (("bed.jpg", bed_texture), ("grass-detail.jpg", grass_detail_texture)):
        if name in used and not (crs.OUTPUT / name).exists():
            paint(crs.OUTPUT / name)


def add_services(b):
    """Services: each drain's line on the ground, and below it the pipe at its depth, for the viewer's see-through ground."""
    cfg, frame, ground, layers = b.cfg, b.frame, b.ground, b.layers
    svc_feats = geojson.read_optional(crs.ROOT / "data" / "site" / "services.geojson")
    colours = cfg.get("services", {}).get("colours", {})
    for f in svc_feats:
        p = f["properties"]
        if f["geometry"]["type"] == "LineString":
            layers.append({**lines_json([np.asarray(f["geometry"]["coordinates"])], frame,
                                        colours.get(p["id"], "#2f2f2f"), p["name"], lambda e, n: ground(e, n) + 0.06),
                           "id": p["id"], "group": "Services", "info": {k: v for k, v in p.items() if k != "id"}})
    if svc_feats:
        layers.extend(pipe_layers(svc_feats, frame, ground, colours))
    b.svc_feats = svc_feats


def add_marks(b):
    """Points: survey marks, sewer depth labels, tree."""
    cfg, frame, ground, labels, layers, vertical, svc_feats = b.cfg, b.frame, b.ground, b.labels, b.layers, b.vertical, b.svc_feats
    marks = []
    survey = cfg.get("survey", {})
    control = crs.ROOT / "data" / "survey" / "control.csv"
    for row in load_points(control) if control.exists() else []:
        e, n = float(row["mE"]), float(row["mN"])
        h = float(ground(e, n)[0])
        rl = row.get(survey.get("level_column", "level")) if "datum" in survey else None
        name = f"SO{row['id']}" if row["kind"] == "setout" else row["id"]
        label = name + (f" {crs.to_model_height(float(rl), survey['datum']):.2f}" if rl else "")
        marks.append({"text": label, "at": r(frame.xyz([[e, n, h + 0.25]])[0], 2), "kind": row["kind"]})
    for f in svc_feats:
        if f["geometry"]["type"] == "Point":
            e, n = f["geometry"]["coordinates"][:2]
            p = f["properties"]
            lab = p.get("label") or (f"IL {float(p['invert_m']):.2f}" if "invert_m" in p else None)
            if not lab:
                continue
            marks.append({"text": f"drain {lab}" if "depth_m" in p else lab, "kind": "sewer",
                          "at": r(frame.xyz([[e, n, float(ground(e, n)[0]) + 0.2]])[0], 2)})
    site_features = b.site_features = features.read_all(crs.ROOT)
    for f in site_features:
        if f["properties"].get("kind") == "mark":
            e, n = f["geometry"]["coordinates"][:2]
            marks.append({"text": f["properties"]["name"], "kind": "mark",
                          "at": r(frame.xyz([[e, n, float(ground(e, n)[0]) + 0.25]])[0], 2)})
    if marks:
        layers.append({"type": "labels", "id": "marks", "group": "Survey", "visible": False,
                       "name": labels.get("marks", "Survey marks and drains" + (f" (heights {vertical})" if vertical else "")),
                       "items": marks})


def add_vegetation(b):
    """Vegetation (data/site/trees.geojson, domus.vegetation): trees, shrubs and hedges with their crowns from the LiDAR, planted beds, the lawns and the stumps, drawn with leaf cards, bark and cut-face textures written to viewer/data/."""
    frame, ground, layers, summary, tin = b.frame, b.ground, b.layers, b.summary, b.tin
    veg_feats = canopy.read()
    veg_layers, summary["vegetation"] = vegetation.layers(veg_feats, frame, ground, tin, crs.OUTPUT, **canopy.drawn_from())
    for layer in veg_layers:        # an option that draws a lawn on its own ground has the viewer hide this one
        redrawn = getattr(b, "lawns_redrawn", {}).get(layer["id"].removeprefix("veg-"))
        if redrawn and "drape" in layer:
            layer["redrawn_by"] = redrawn
    layers.extend(veg_layers)
    b.veg_feats = veg_feats


def add_features(b):
    """Small site features (data/site/features-*.geojson): fences and gates, sheds, bins and stacks, paths and paving, posts and poles, textured with the images in data/features/ (domus.features). One layer per group and material; its info lists the features in it. Features a wall option takes out (its `remove_features`) get layers of their own, which the viewer hides while that option is shown."""
    frame, ground, labels, layers, site_features = b.frame, b.ground, b.labels, b.layers, b.site_features
    removed = getattr(b, "removed_features", {})
    unknown = set(removed) - {f["properties"].get("id") for f in site_features}
    if unknown:
        print(f"options remove site features that aren't in data/site/features-*.geojson: {', '.join(sorted(unknown))}")
    merged = {}
    for group, items in features.build(site_features, ground).items():
        for m, mat, p in items:
            key = (group, mat, tuple(removed.get(p.get("id"), ())))
            merged.setdefault(key, [features.UVMesh(), {}])
            merged[key][0].extend(m)
            merged[key][1].setdefault(p.get("name", p.get("id", "")), p.get("confidence", ""))
    for k, ((group, mat, hide_with), (m, named)) in enumerate(sorted(merged.items())):
        names = list(named)
        tex = features.texture_file(mat, crs.ROOT / "data" / "features")
        extra = {}
        if tex:
            (crs.OUTPUT / f"feature-{tex}").write_bytes((crs.ROOT / "data" / "features" / tex).read_bytes())
            extra = {"uv": r(m.uv, 4), "texture": f"feature-{tex}", "repeat": True}
        layers.append({**mesh_json(m, frame, features.MATERIALS[mat][0],
                                   f"{group}, {mat.replace('-', ' ')}: {', '.join(names[:3])}"
                                   + (f" and {len(names) - 3} more" if len(names) > 3 else "")),
                       "id": f"features-{k}", "group": "Site features", **extra, **({"hide_with": list(hide_with)} if hide_with else {}),
                       "info": {"features": "; ".join(names),
                                "note": "; ".join(f"{n}: {c}" if c else n for n, c in named.items()),
                                "source": labels.get("features_source", "data/site/features-*.geojson (each feature's source and basis)")}})
    for f in site_features:
        if f["properties"].get("kind") == "line":
            p = f["properties"]
            layers.append({**lines_json([np.asarray(f["geometry"]["coordinates"])], frame, p.get("colour", "#2f2f2f"),
                                        p["name"], lambda e, n: ground(e, n) + 0.04),
                           "id": f"feature-{p['id']}", "group": "Site features",
                           "info": {k: v for k, v in p.items() if k in ("source", "basis", "note")}})


def add_points(b):
    """Point cloud from site videos (domus video export), hidden until switched on."""
    frame, labels, layers = b.frame, b.labels, b.layers
    pfile = crs.ROOT / "data" / "video" / "points.npz"
    if pfile.exists():
        pc = np.load(pfile)
        v = frame.xyz(pc["origin"] + pc["mm"] / 1000.0)
        # Viewer coordinates in 2.5 mm steps as little-endian int16, colours as bytes, both base64
        # (artifacts serve JSON but not raw binary).
        q = np.round(v / POINT_STEP).astype("<i2")
        (crs.OUTPUT / "points.json").write_text(json.dumps({
            "count": len(v), "step": POINT_STEP,
            "positions": base64.b64encode(q.tobytes()).decode("ascii"),
            "colours": base64.b64encode(pc["rgb"].astype(np.uint8).tobytes()).decode("ascii")}), encoding="utf-8")
        (crs.OUTPUT / "points.bin").unlink(missing_ok=True)
        layers.append({"type": "points", "id": "video-points", "group": "Context", "visible": False,
                       "name": f"{labels.get('points', 'Point cloud from the videos')} ({len(v):,} points)",
                       "file": "points.json", "count": len(v)})


def add_open_questions(b):
    """Open questions (data/site.toml [[video.compare]], [[video.question_marks]]): two placements of a clip as clouds side by side (domus video export writes data/video/compare.npz), and the clips' readings of the house's levels as labels, hidden until switched on."""
    cfg, frame, labels, layers = b.cfg, b.frame, b.labels, b.layers
    entries = {e["id"]: e for e in cfg.get("video", {}).get("compare", [])}
    cfile = crs.ROOT / "data" / "video" / "compare.npz"
    if entries and cfile.exists():
        cp = np.load(cfile)
        for k, cid in enumerate(str(i) for i in cp["ids"]):
            if cid not in entries:
                continue
            sel = cp["set"] == k
            q = np.round(frame.xyz(cp["origin"] + cp["mm"][sel] / 1000.0) / POINT_STEP).astype("<i2")
            (crs.OUTPUT / f"compare-{cid}.json").write_text(json.dumps({
                "count": int(sel.sum()), "step": POINT_STEP,
                "positions": base64.b64encode(q.tobytes()).decode("ascii"),
                "colours": base64.b64encode(cp["rgb"][sel].astype(np.uint8).tobytes()).decode("ascii")}), encoding="utf-8")
            layers.append({"type": "points", "id": f"compare-{cid}", "group": "Open questions", "visible": False,
                           "name": entries[cid]["name"], "file": f"compare-{cid}.json", "count": int(sel.sum())})
    readings = []
    for m in cfg.get("video", {}).get("question_marks", []):
        with open(crs.ROOT / "data" / "video" / "picks" / m["picks"], newline="", encoding="utf-8") as fh:
            row = next(x for x in csv.DictReader(fh) if x["id"] == m["id"])
        e, n, h = (float(row[k]) for k in ("E", "N", "H"))
        text = f"{m['text']}: {h:.2f}" + (f" (drawn: {m['drawn']})" if m.get("drawn") else "")
        readings.append({"text": text, "kind": "mark", "at": r(frame.xyz([[e, n, h + m.get("lift_m", 0.15)]])[0], 2)})
    if readings:
        layers.append({"type": "labels", "id": "question-readings", "group": "Open questions", "visible": False,
                       "name": labels.get("readings", "Readings from the clips"),
                       "items": readings})


def add_surface(b):
    """LiDAR surface model (trees, buildings) as context, 1 m."""
    extent, frame, dem, labels, layers, veg_feats = b.extent, b.frame, b.dem, b.labels, b.layers, b.veg_feats
    Es, Ns = terrain.grid(extent, 1.0)
    Hs = raster.dsm().sample(Es, Ns) if raster.DSM.exists() else np.full(Es.shape, np.nan)
    Hg = dem.sample(Es, Ns)
    Hs = np.where(Hs - Hg > 0.4, Hs, np.nan)
    Hs[vegetation.removed_mask(veg_feats, Es, Ns)] = np.nan   # gone since the survey
    rows, cols = Hs.shape
    verts, faces = [], []
    idx = -np.ones(Hs.shape, int)
    for i in range(rows):
        for j in range(cols):
            if np.isfinite(Hs[i, j]):
                idx[i, j] = len(verts)
                verts.append((Es[i, j], Ns[i, j], Hs[i, j]))
    for i in range(rows - 1):
        for j in range(cols - 1):
            a, b, c, d = idx[i, j], idx[i, j + 1], idx[i + 1, j + 1], idx[i + 1, j]
            if min(a, b, c) >= 0:
                faces.append((a, b, c))
            if min(a, c, d) >= 0:
                faces.append((a, c, d))
    if faces:
        layers.append({**mesh_json(mesh.Mesh(np.asarray(verts), np.asarray(faces)), frame, "#5f7d4f",
                                   labels.get("dsm", "LiDAR surface (trees and roofs), 1 m")),
                       "id": "dsm", "group": "Context", "visible": False, "opacity": 0.55})


SITE_LAYER_TYPES = ("mesh", "lines", "labels")


def site_layer(spec, frame, where):
    """One of a site's own layers, from data/layers/<name>.json (`spec`, in the site's grid coordinates), as the
    viewer takes it. A site's tool writes these when it draws something no kind in Domus does; the build only
    places them, under ids that start `site-`."""
    lid, kind = spec.get("id", ""), spec.get("type")
    if not isinstance(lid, str) or not lid.startswith("site-"):
        raise DomusError(f"{where}: a site's own layer needs an id that starts with site-, not {lid!r}")
    if kind not in SITE_LAYER_TYPES:
        raise DomusError(f"{where}: layer {lid} has type {kind!r}; a site's own layer is one of {', '.join(SITE_LAYER_TYPES)}")
    base = {"id": lid, "name": spec.get("name", lid), "group": spec.get("group", "The site's own"),
            **{k: spec[k] for k in ("visible", "info", "opacity") if k in spec}}
    try:
        if kind == "mesh":
            pts = np.asarray(spec["positions"], float).reshape(-1, 3)
            idx = [int(i) for i in spec["indices"]]
            if len(idx) % 3 or (idx and not 0 <= min(idx) <= max(idx) < len(pts)):
                raise ValueError("indices must come in threes, each a position's number")
            return {**base, "type": "mesh", "colour": spec.get("colour", "#9a9a9a"), "positions": r(frame.xyz(pts)), "indices": idx}
        if kind == "lines":
            return {**lines_json([np.asarray(line, float).reshape(-1, 3) for line in spec["lines"]], frame,
                                 spec.get("colour", "#333333"), base["name"], "z"), **base}
        items = [{"text": str(it["text"]), "at": r(frame.xyz(np.asarray([it["at"]], float).reshape(1, 3))[0], 2)}
                 for it in spec["items"]]
        return {**base, "type": "labels", "items": items}
    except (KeyError, TypeError, ValueError) as e:
        raise DomusError(f"{where}: layer {lid} isn't drawn as its type says ({e})") from e


def add_site_layers(b):
    """The site's own layers (data/layers/*.json): what its tools drew, placed in the model as they are."""
    folder = crs.ROOT / "data" / "layers"
    seen = {layer.get("id") for layer in b.layers}
    for path in sorted(folder.glob("*.json")) if folder.is_dir() else ():
        where = f"data/layers/{path.name}"
        try:
            specs = json.loads(path.read_text(encoding="utf-8"))
        except ValueError as e:
            raise DomusError(f"{where} isn't JSON: {e}") from e
        for spec in specs.get("layers", []) if isinstance(specs, dict) else []:
            layer = site_layer(spec, b.frame, where)
            if layer["id"] in seen:
                raise DomusError(f"{where}: layer {layer['id']} is in the model already")
            seen.add(layer["id"])
            b.layers.append(layer)


def add_extensions(b):
    """The layers the site's own code draws ([domus] extensions, checked as trusted when the build started)."""
    from .. import api
    from ..project import extensions

    seen = {layer.get("id") for layer in b.layers}
    for rel, sha, specs in extensions.run(b.extensions, api.open_site()):
        for spec in specs:
            layer = site_layer(spec, b.frame, f"the extension {rel}")
            if layer["id"] in seen:
                raise DomusError(f"The extension {rel}: layer {layer['id']} is in the model already")
            seen.add(layer["id"])
            b.layers.append(layer)


def add_enrich(b):
    """What would improve this model: the viewer's checklist, and the route to film as a hidden layer."""
    cfg, frame, ground = b.cfg, b.frame, b.ground
    b.enrich = None
    if not cfg.get("enrich", {}).get("enabled", True):
        return
    from ..checks import enrich

    bnd = geojson.read(crs.ROOT / "data" / "site" / "boundary.geojson")["features"]
    main_id = cfg.get("boundary", {}).get("main", bnd[0]["properties"]["id"])
    lot = next(f for f in bnd if f["properties"]["id"] == main_id)["geometry"]["coordinates"][0]
    hfc = {f["properties"]["id"]: f for f in geojson.read_optional(crs.ROOT / "data" / "site" / "house.geojson")}
    house_ring = hfc["house-walls"]["geometry"]["coordinates"][0] if "house-walls" in hfc else None
    route = enrich.walk_route(lot, house_ring, ground)
    if route:
        stops = route["stops"]
        b.layers.append({**lines_json([np.asarray(route["path"])], frame, "#ff3d9a", "Recommended video walk",
                                      lambda e, n: ground(e, n) + 0.3),
                         "id": "walk-route", "group": "Walk route", "visible": False,
                         "labels": [{"text": str(s["n"]), "at": r(frame.xyz([[s["x"], s["y"], float(ground(s["x"], s["y"])[0]) + 0.8]])[0], 2)}
                                    for s in stops]})
    b.enrich = {"sources": enrich.checklist(cfg),
                "route": route and {"length_m": route["length_m"], "stops": [{"n": s["n"], "text": s["text"]} for s in route["stops"]]}}


def site_origin(cfg, dem):
    """The viewer's origin: E, N and height. With no height given, the ground's lowest whole metre."""
    origin = list(cfg["model"]["origin"])
    if len(origin) == 2:
        origin.append(float(np.floor(np.nanmin(dem.sample(*terrain.grid(cfg["model"]["extent"], 1.0))))))
    return origin


def main(argv=None):
    """`domus build [--fresh]`: --fresh draws everything again, taking nothing from the last build."""
    if "--fresh" in (argv or []):
        os.environ["DOMUS_BUILD_CACHE"] = "0"
    from ..project import extensions

    cfg = crs.settings()
    checked = extensions.check(crs.ROOT, cfg)       # a site's own code runs only where it's trusted, or nothing is built
    extent = cfg["model"]["extent"]
    crs.OUTPUT.mkdir(parents=True, exist_ok=True)
    dem = terrain.ground()  # LiDAR ground with the curated fixes (data/site/ground-fixes.geojson)
    origin = site_origin(cfg, dem)
    wall_feats = geojson.read_optional(crs.ROOT / "data" / "site" / "walls-existing.geojson")
    b = SimpleNamespace(cfg=cfg, extent=extent, frame=Frame(origin), dem=dem, origin=origin,
                        ground=lambda e, n: dem.sample(np.atleast_1d(e), np.atleast_1d(n)),
                        labels=cfg.get("labels", {}), vertical=cfg.get("crs", {}).get("vertical", ""),
                        wall_defs=[walls.from_feature(f) for f in wall_feats], layers=[], summary={},
                        cache=buildcache.Cache(crs.ROOT, crs.OUTPUT), extensions=checked)
    add_ground(b)
    add_contours(b)
    add_boundaries(b)
    add_house(b)
    add_ifc(b)
    add_walls(b)
    add_options(b)
    add_services(b)
    add_marks(b)
    add_vegetation(b)
    add_features(b)
    add_points(b)
    add_open_questions(b)
    add_surface(b)
    add_site_layers(b)
    add_extensions(b)
    add_enrich(b)
    write_model(b)
    b.cache.prune()


def snap_targets(origin):
    """What exact drawing snaps to, in viewer metres: the boundary, the house, existing walls and survey marks."""
    e0, n0 = origin[0], origin[1]
    pt = lambda c: [round(c[0] - e0, 3), round(-(c[1] - n0), 3)]
    lines, points = [], []

    def add(rel, label, pick=lambda f: True):
        for f in geojson.read_optional(crs.ROOT / "data" / "site" / rel) or []:
            if not pick(f):
                continue
            g = f["geometry"]
            rings = g["coordinates"] if g["type"] == "Polygon" else [g["coordinates"]] if g["type"] == "LineString" else []
            for ring in rings:
                lines.append({"label": label(f) if callable(label) else label, "pts": [pt(c) for c in ring]})

    add("boundary.geojson", "Boundary")
    add("house.geojson", "House", lambda f: f["properties"].get("id") == "house-walls")
    add("walls-existing.geojson", lambda f: f["properties"].get("name", "Wall"))
    for f in geojson.read_optional(crs.ROOT / "data" / "site" / "features-survey-marks.geojson") or []:
        if f["geometry"]["type"] == "Point":
            x, z = pt(f["geometry"]["coordinates"])
            points.append({"label": f["properties"].get("name", "Mark"), "x": x, "z": z})
    return {"lines": lines, "points": points}


def write_model(b):
    cfg, summary, tin = b.cfg, b.summary, b.tin
    bnd = geojson.read(crs.ROOT / "data" / "site" / "boundary.geojson")["features"]
    main_id = cfg.get("boundary", {}).get("main", bnd[0]["properties"]["id"])
    lot_ring = np.asarray(next(f for f in bnd if f["properties"]["id"] == main_id)["geometry"]["coordinates"][0])
    site = cfg.get("site", {})
    model = {
        "format": MODEL_FORMAT,
        "title": site.get("title", "Site model"),
        "heading": site.get("heading", site.get("title", "Site model")),
        "subtitle": site.get("subtitle", ""),
        "built": datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds"),
        "commit": git_commit(),
        "crs": crs.label(),
        "vertical": b.vertical,
        "datums": crs.datums(),
        "origin": b.origin,
        "extent": b.extent,
        "textures": b.textures,
        "layers": b.layers,
        "summary": summary,
        "lot": {"area_m2": round(float(abs(np.dot(lot_ring[:-1, 0], np.roll(lot_ring[:-1, 1], -1))
                                          - np.dot(lot_ring[:-1, 1], np.roll(lot_ring[:-1, 0], -1))) / 2), 1)},
        "attribution": site.get("attribution", ""),
        "about": site.get("about", ""),
        "views": cfg.get("views", {}),
        "snap": snap_targets(b.origin),
        **({"enrich": b.enrich} if b.enrich else {}),
    }
    (crs.OUTPUT / "model.json").write_text(json.dumps(model, separators=(",", ":")), encoding="utf-8")
    (crs.OUTPUT / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    ran = {rel: sha for rel, _, sha in b.extensions}
    (crs.OUTPUT / "build.json").write_text(json.dumps({**{k: model[k] for k in ("title", "built", "commit")}, "domus": __version__,
                                                "model_format": MODEL_FORMAT, **({"extensions": ran} if ran else {})}),
                                    encoding="utf-8")
    size = (crs.OUTPUT / "model.json").stat().st_size / 1e6
    print(f"ground: {len(tin.vertices)} points, {len(tin)} triangles")
    if "house" in summary:
        print(f"house: {json.dumps(summary['house'])}")
    for w in summary["walls"]:
        print(f"wall {w['id']}: {w['length_m']} m long, exposed {w['height_min_m']}-{w['height_max_m']} m")
    print(f"wrote {crs.OUTPUT / 'model.json'} ({size:.1f} MB) and textures")


if __name__ == "__main__":
    main()
