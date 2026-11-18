"""The made-up sites CI builds with a pull request's base and with its head, so a change to the build can be proved
without anyone's real property.

    uv run python tools/made_up_sites.py write OUT     # writes OUT/demo, OUT/richer, OUT/abroad, OUT/format1 to OUT/format3 and OUT/left
    python tools/made_up_sites.py report DIR           # what moved between DIR/base-<site> and DIR/head-<site>

The sites, all in the sea, so none is a real place:

  demo     What `domus demo` writes: a sloping lot, a small house, a block wall, a drain, trees, a fence and two
           wall options.
  richer   The demo with more of what real sites have: house parts (a porch and its steps, a deck and its
           balustrade, a chimney and a tank), levels along the drain, a sewer at an assumed depth, a right of way
           listed before the lot, and two trees over the drive beside it, with the paving under them painted into
           the aerial photo (data/site/paved-under-canopy.geojson and a paving picture).
  abroad   The demo moved onto the British National Grid (EPSG:27700), rasters and all. The same site on another grid
           builds the same model, from the site's origin: the report holds the head's build of it to the demo's
           within 1.5 mm (`twin`), and fails if it isn't.
  format1  The demo as a site in format 1 (Domus 2026.11.26 and before) with what format 2 changed: compass words
           along a frame at an angle to the grid, [front_garden], floor levels relative to each other, and a drain
           drawn from its printed levels only. Every Domus reads it as format 1 did, so it never moves. It stays as
           it is for good: each later format adds a site of its own.
  format2  The demo as a site in format 2 (Domus 2026.11.29 and before) with what format 3 changed: no [crs] and no
           credit line, which format 2 took from New Zealand. It stays as it is for good too.
  format3  The demo as a site in format 3 (Domus 2026.11.38 and before) with what format 4 changed: how sure a
           datum's offset and the upper floor's level are, in [datums.<datum>] and [house], which format 4 keeps
           under [local]. It stays as it is for good too.
  left     The demo with its options' frame run the other way, north along the lot's east boundary from its
           south-east corner, so the lot lies on the frame line's left ([frame] side = "left"). Its options are the
           demo's, with s counted from that end, so its walls and terraces stand where the demo's do.

`report` reads what CI leaves in DIR for each side (base and head) and site: the copy it built (`<side>-<site>/`,
with the model in `viewer/data/`), the build's output (`<side>-<site>.log`) and whether it built
(`<side>-<site>.status`, `ok` or `failed`). It prints Markdown, and exits 1 only when the head can't build a site
the base could, or builds the abroad site other than the demo.

    python tools/made_up_sites.py twin DEMO_OUT ABROAD_OUT   # the twin check alone, on two builds' viewer/data
"""

import argparse
import contextlib
import io
import json
import math
import re
import subprocess
import sys
import tomllib
from pathlib import Path

HERE = Path(__file__).resolve().parent
SITES = ("demo", "richer", "abroad", "format1", "format2", "format3", "left")
TWIN_M = 0.0015         # how far the abroad site's model may be from the demo's: the same site on another grid
IMAGE_MEAN, IMAGE_MAX = 0.5, 16      # and its pictures: the mean and largest difference in a colour, out of 255
# The abroad site's grid: the British National Grid, in the Celtic Sea.
ABROAD_EPSG, ABROAD_X0, ABROAD_Y0 = 27700, 100000.0, 100000.0
ABROAD_CRS = {"label": "OSGB36 / British National Grid (EPSG:27700)", "vertical": "ODN"}


def feat(gtype, coords, **props):
    return {"type": "Feature", "properties": props, "geometry": {"type": gtype, "coordinates": coords}}


def box(x0, y0, x1, y1):
    return [[[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]]


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path, value):
    path.write_text(json.dumps(value), encoding="utf-8", newline="\n")


def append(path, text):
    with open(path, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def enrich(root):
    """The demo, with house parts, drain levels, a drain at an assumed depth, a lot that isn't listed first, and trees
    over the drive beside it."""
    from domus import demo

    x0, y0 = demo.X0, demo.Y0
    site = root / "data" / "site"
    fl = tomllib.loads((root / "data" / "site.toml").read_text(encoding="utf-8"))["house"]["fl_upper"]
    # The house is 12 by 9 m, x0 + 5 to x0 + 17 east and y0 + 6 to y0 + 15 north, with its door on the north face.
    treads = [[[x0 + 9.5, y0 + 17 + 0.3 * k, round(fl - 0.05 - 0.17 * k, 2)],
               [x0 + 11.5, y0 + 17 + 0.3 * k, round(fl - 0.05 - 0.17 * k, 2)]] for k in (1, 2, 3)]
    parts = [
        feat("Polygon", box(x0 + 9, y0 + 15, x0 + 12, y0 + 17), id="porch", part="porch", floor=round(fl - 0.05, 2),
             roof_at_wall=round(fl + 2.4, 2), roof_at_edge=round(fl + 2.1, 2), walls=["west"], source="made up"),
        feat("MultiLineString", treads, id="porch-steps", part="steps", back_line=[[x0 + 9.5, y0 + 17], [x0 + 11.5, y0 + 17]],
             open_sides=["east", "west"], source="made up"),
        feat("Polygon", box(x0 + 2, y0 + 7, x0 + 5, y0 + 13), id="deck", part="block", top_below_fl_m=0.1, thickness_m=0.2,
             colour="deck", layer="deck", source="made up"),
        feat("MultiLineString", [[[x0 + 2, y0 + 7], [x0 + 2, y0 + 13], [x0 + 5, y0 + 13]]], id="deck-balustrade",
             part="balustrade", top_below_fl_m=0.1, height_m=0.9, source="made up"),
        feat("Polygon", box(x0 + 14, y0 + 8, x0 + 14.6, y0 + 8.6), id="chimney", part="chimney", stack_top=round(fl + 4.6, 2),
             source="made up"),
        feat("Point", [x0 + 18, y0 + 13.5], id="tank", part="tank", radius_m=0.5, height_m=2.0, source="made up"),
    ]
    house = read_json(site / "house.geojson")
    house["features"] += parts
    write_json(site / "house.geojson", house)

    # Levels along the stormwater drain, falling to the north, and a sewer with no levels at all.
    services = read_json(site / "services.geojson")
    for pid, xy, depth in [("drain-top", [x0 + 17, y0 + 11], 0.45), ("drain-bottom", [x0 + 21, y0 + 32], 0.9)]:
        invert = round(float(demo.ground(*xy)) - depth, 2)
        services["features"].append(feat("Point", xy, id=pid, line="drain", invert_m=invert, label=f"IL {invert:.2f}",
                                         source="made up"))
    services["features"].append(feat("LineString", [[x0 + 6, y0 + 15], [x0 + 6, y0 + 33]], id="sewer", name="Sewer",
                                     source="made up"))
    write_json(site / "services.geojson", services)

    # A right of way beside the lot, listed first, so the main lot has to be named.
    boundary = read_json(site / "boundary.geojson")
    way = feat("Polygon", box(x0 + 22, y0, x0 + 25, y0 + 30), id="right-of-way", name="Right of way (made up)",
               source="made up")
    boundary["features"].insert(0, way)
    write_json(site / "boundary.geojson", boundary)
    append(root / "data" / "site.toml", '\n[boundary]\nmain = "lot"\n\n[services]\nassumed_depth_m = { sewer = 0.7 }\n'
                                        '\n[services.notes.drain]\ntext = "Invert {lo} to {hi}, made up ({span})."\n'
                                        'confidence = "made up"\n')
    trees_over_the_drive(root)


# Two made-up trees over the drive east of the lot, which the demo's aerial photo shows as paving from x0 + 22 on: each
# tree's place east and north of x0, y0, its crown's radius and its height, in metres.
DRIVE_TREES = [(22.9, 12.0, 2.2, 7.0), (24.1, 19.5, 1.8, 6.0)]


def trees_over_the_drive(root):
    """Two trees over the drive: their crowns and shade in the aerial photo, the trees in trees.geojson, the paving
    under them to paint in (paved-under-canopy.geojson) and a picture of the paving for its grain."""
    import numpy as np
    import rasterio
    from PIL import Image

    from domus import demo

    x0, y0 = demo.X0, demo.Y0
    rng = np.random.default_rng(11)
    path = root / "data" / "aerial" / "aerial.tif"
    with rasterio.open(path) as src:
        rgb, profile, t = src.read().astype(np.float32), src.profile, src.transform
    E, N = np.meshgrid(t.c + (np.arange(rgb.shape[2]) + 0.5) * t.a, t.f + (np.arange(rgb.shape[1]) + 0.5) * t.e)
    drive = E > x0 + 22
    for dx, dy, r, _ in DRIVE_TREES:
        # The crown's shade on the drive, the sun to the north-east, with spots of light through it; then the crown.
        shade = drive & (np.hypot(E - (x0 + dx - 0.8), N - (y0 + dy - 1.2)) < 1.1 * r)
        rgb[:, shade] *= np.where(rng.random(int(shade.sum())) < 0.15, 0.85, 0.55)
        crown = np.hypot(E - (x0 + dx), N - (y0 + dy)) < r
        rgb[:, crown] = np.array([[52], [88], [44]], np.float32) + rng.normal(0, 9, (3, int(crown.sum())))
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(rgb.clip(0, 255).round().astype(np.uint8))
    site = root / "data" / "site"
    trees = read_json(site / "trees.geojson")
    for k, (dx, dy, r, h) in enumerate(DRIVE_TREES, start=1):
        ring = [[round(x0 + dx + r * math.cos(a), 2), round(y0 + dy + r * math.sin(a), 2)]
                for a in (2 * math.pi * i / 12 for i in range(12))]
        trees["features"].append(feat("Polygon", [[*ring, ring[0]]], id=f"drive-tree-{k}", kind="tree",
                                      name=f"Tree over the drive {k}", height_m=h, crown_base_m=1.8, trunk_diameter_m=0.3,
                                      look="broadleaf", colour="#3f6b35", confidence="made up", source="made up"))
    write_json(site / "trees.geojson", trees)
    area = feat("Polygon", box(x0 + 22.2, y0 + 7.0, x0 + 26.5, y0 + 24.0), id="drive-under-trees",
                name="The drive under the trees (made up)", source="made up")
    write_json(site / "paved-under-canopy.geojson", demo.fc("paved-under-canopy", [area]))
    grain = 150 + rng.normal(0, 12, (160, 240)) + np.kron(rng.normal(0, 8, (16, 24)), np.ones((10, 10)))
    (root / "data" / "features").mkdir(parents=True, exist_ok=True)
    Image.fromarray(np.repeat(grain[..., None], 3, axis=2).clip(0, 255).astype(np.uint8)).save(
        root / "data" / "features" / "paving.jpg", quality=85)


def move_abroad(root):
    """The demo moved onto the British National Grid: its layers, rasters and settings."""
    import rasterio

    from domus import demo

    dx, dy = ABROAD_X0 - demo.X0, ABROAD_Y0 - demo.Y0

    def shift(c):
        if c and isinstance(c[0], (int, float)):
            return [round(c[0] + dx, 3), round(c[1] + dy, 3), *c[2:]]
        return [shift(x) for x in c]

    for path in (root / "data" / "site").glob("*.geojson"):
        fc = read_json(path)
        fc["crs"] = {"type": "name", "properties": {"name": f"urn:ogc:def:crs:EPSG::{ABROAD_EPSG}"}}
        for f in fc["features"]:
            f["geometry"]["coordinates"] = shift(f["geometry"]["coordinates"])
        write_json(path, fc)
    for path in [*(root / "data" / "lidar").glob("*.tif"), *(root / "data" / "aerial").glob("*.tif")]:
        with rasterio.open(path) as src:
            data, profile = src.read(), src.profile
        t = profile["transform"]
        profile.update(crs=f"EPSG:{ABROAD_EPSG}", transform=rasterio.Affine(t.a, t.b, t.c + dx, t.d, t.e, t.f + dy))
        with rasterio.open(path, "w", **profile) as dst:
            dst.write(data)

    def shift_settings(path, keys):
        """Moves the [E, N] arrays that `keys` set, in a settings file as Domus writes them (one array to a line)."""
        def moved(m):
            v = json.loads(m[2])
            v = [v[0] + dx, v[1] + dy, v[2] + dx, v[3] + dy] if m[1] == "extent" else [v[0] + dx, v[1] + dy, *v[2:]]
            return f"{m[1]} = {json.dumps(v)}"
        text = re.sub(rf"^({'|'.join(keys)}) = (\[[^\]]*\])", moved, path.read_text(encoding="utf-8"), flags=re.MULTILINE)
        path.write_text(text, encoding="utf-8", newline="\n")

    shift_settings(root / "data" / "site.toml", ("extent", "origin"))
    shift_settings(root / "options" / "_site.toml", ("origin", "toward"))
    settings = root / "data" / "site.toml"
    text = settings.read_text(encoding="utf-8")
    text = re.sub(r"^epsg = \d+", f"epsg = {ABROAD_EPSG}", text, flags=re.MULTILINE)
    for key, value in ABROAD_CRS.items():
        text = re.sub(rf"^{key} = .*$", f"{key} = {json.dumps(value)}", text, count=1, flags=re.MULTILINE)
    text = text.replace('"Demo site (made up)"', '"Demo site abroad (made up)"')
    settings.write_text(text, encoding="utf-8", newline="\n")


FORMAT_1_OPTION = """name = "F. Falls named the old way"
short = "F. Old falls"
description = "Two lawns falling the ways format 1 named them, along a frame at an angle to the grid."
steps = 0
keep_existing = []

[[walls]]
id = "F1"
name = "Lawn wall"
material = "timber"
line = {wall}

[[terraces]]
id = "upper"
name = "Upper lawn"
kind = "lawn"
level = {upper}
at = {upper_at}
fall = "1:40"
fall_to = "east"
batter = 2.5
polygon = {upper_polygon}

[[terraces]]
id = "lower"
name = "Lower lawn"
kind = "lawn"
level = {lower}
at = {lower_at}
fall = "1:50"
batter = 2.5
polygon = {lower_polygon}
"""


def make_format_1(root):
    """The demo, said the way a format 1 site says it (see the module's docstring)."""
    from domus import demo

    x0, y0 = demo.X0, demo.Y0
    settings = root / "data" / "site.toml"
    text = re.sub(r"^format = .*$", "format = 1", settings.read_text(encoding="utf-8"), count=1, flags=re.MULTILINE)
    text = text.replace("[house]\n", "[house]\nfl_upper_rel = 2.6\nfl_lower_rel = 0.0\n", 1)
    settings.write_text(text + '\n[services]\nprinted_line = "drain"\n', encoding="utf-8", newline="\n")
    # The drain's printed levels, at both its ends: the only levels it has.
    site = root / "data" / "site"
    services = read_json(site / "services.geojson")
    for pid, xy, depth in [("drain-top", [x0 + 17, y0 + 11], 0.5), ("drain-bottom", [x0 + 21, y0 + 32], 0.8)]:
        invert = round(float(demo.ground(*xy)) - depth, 2)
        services["features"].append(feat("Point", xy, id=pid, line="drain", invert_m=invert, depth_m=depth,
                                         label=f"drain {depth:.2f} m", source="made up plan"))
    write_json(site / "services.geojson", services)
    # options/_site.toml: [front_garden], and a frame 20 degrees off the grid, running from the lot's north-east corner.
    shared = root / "options" / "_site.toml"
    origin, b = [x0 + 22, y0 + 30], math.radians(200)
    toward = [round(origin[0] + 20 * math.sin(b), 3), round(origin[1] + 20 * math.cos(b), 3)]
    text = shared.read_text(encoding="utf-8").replace("[regrade]", "[front_garden]")
    text = re.sub(r"^origin = .*$", f"origin = {json.dumps(origin)}", text, count=1, flags=re.MULTILINE)
    text = re.sub(r"^toward = .*$", f"toward = {json.dumps(toward)}", text, count=1, flags=re.MULTILINE)
    shared.write_text(text, encoding="utf-8", newline="\n")
    # The demo's options are drawn in the frame, which has turned; this one is drawn on the site's grid.
    for path in (root / "options").glob("*.toml"):
        if not path.name.startswith("_"):
            path.unlink()
    level = lambda x, y, up: round(float(demo.ground(x0 + x, y0 + y)) + up, 2)
    box = lambda a, b, c, d: json.dumps([[x0 + a, y0 + b], [x0 + c, y0 + b], [x0 + c, y0 + d], [x0 + a, y0 + d]])
    (root / "options" / "f-old-falls.toml").write_text(FORMAT_1_OPTION.format(
        wall=json.dumps([[x0 + 4, y0 + 18], [x0 + 18, y0 + 18]]), upper=level(11, 22, 0.1),
        upper_at=json.dumps([x0 + 11, y0 + 22]), upper_polygon=box(4, 18, 18, 27), lower=level(11, 16.5, 0.05),
        lower_at=json.dumps([x0 + 11, y0 + 16.5]), lower_polygon=box(4, 15.5, 18, 17.9)), encoding="utf-8", newline="\n")


def make_format_2(root):
    """The demo, said the way a format 2 site that leaves New Zealand to be assumed says it."""
    settings = root / "data" / "site.toml"
    text = re.sub(r"^format = .*$", "format = 2", settings.read_text(encoding="utf-8"), count=1, flags=re.MULTILINE)
    text = re.sub(r"^attribution = .*\n", "", text, count=1, flags=re.MULTILINE)
    text = re.sub(r"\n\[crs\]\n(?:\w+ = .*\n)+", "\n", text, count=1)
    settings.write_text(text, encoding="utf-8", newline="\n")


def make_format_3(root):
    """The demo, said the way a format 3 site that records how sure its levels are says it: in [datums.<datum>] and
    [house], each with a comment that has to move with it."""
    settings = root / "data" / "site.toml"
    text = re.sub(r"^format = .*$", "format = 3", settings.read_text(encoding="utf-8"), count=1, flags=re.MULTILINE)
    text = text.replace("[house]\n", "[house]\nfl_upper_uncertainty_m = 0.02          # made up\n", 1)
    text += ('\n[datums.plans]\nlabel = "The plans\' own datum (made up)"\noffset_m = 57.3\n'
             "uncertainty_m = 0.05          # made up too\n")
    settings.write_text(text, encoding="utf-8", newline="\n")


def make_left(root):
    """The demo with its options' frame run the other way along the same boundary, from where it ran towards to where
    it started, so the lot is on the frame line's left; o is as it was, and the options' s is counted from that end, so
    they stand where the demo's do."""
    shared = root / "options" / "_site.toml"
    text = shared.read_text(encoding="utf-8")
    frame = tomllib.loads(text)["frame"]
    length = math.dist(frame["origin"], frame["toward"])
    text = text.replace("(on the right looking along it)", "(on the left looking along it)")
    text = re.sub(r"^origin = .*$", f"origin = {json.dumps(frame['toward'])}", text, count=1, flags=re.MULTILINE)
    text = re.sub(r"^toward = .*$", f'toward = {json.dumps(frame["origin"])}\nside = "left"', text, count=1,
                  flags=re.MULTILINE)
    shared.write_text(text, encoding="utf-8", newline="\n")

    def turned(m):
        """An option's points in (s, o), with s counted from the other end."""
        v = json.loads(m[2])
        v = [length - v[0], *v[1:]] if isinstance(v[0], (int, float)) else [[length - p[0], *p[1:]] for p in v]
        return f"{m[1]} = {json.dumps(v)}"

    for path in (root / "options").glob("[!_]*.toml"):
        path.write_text(re.sub(r"^(\w+_so) = (\[.*\])$", turned, path.read_text(encoding="utf-8"), flags=re.MULTILINE),
                        encoding="utf-8", newline="\n")


def write(out):
    from domus import demo

    if out.exists() and any(out.iterdir()):
        raise SystemExit(f"{out} isn't empty: give a new folder")
    for name in SITES:
        with contextlib.redirect_stdout(io.StringIO()):
            demo.write(out / name)
    enrich(out / "richer")
    move_abroad(out / "abroad")
    make_format_1(out / "format1")
    make_format_2(out / "format2")
    make_format_3(out / "format3")
    make_left(out / "left")
    print(f"Wrote {', '.join(SITES)} to {out}")


def status(d, side, name):
    path = d / f"{side}-{name}.status"
    return path.read_text(encoding="utf-8").strip() if path.exists() else "not built"


def tail(path, n=15):
    return "\n".join(path.read_text(encoding="utf-8", errors="replace").splitlines()[-n:]) if path.exists() else "(no log)"


def fenced(text):
    return ["", "```text", text.strip(), "```", ""]


def decimals(x):
    """How many decimals a number was written with."""
    s = repr(float(x))
    return 0 if "e" in s else len(s.split(".")[1].rstrip("0"))


def same_number(x, y):
    """Equal within TWIN_M, or one unit apart in the last decimal they were rounded to: a value on a rounding
    boundary rounds either way, as 42.015 does to 42.01 or 42.02."""
    if isinstance(x, bool) or isinstance(y, bool) or (isinstance(x, int) and isinstance(y, int)):
        return x == y
    d = max(decimals(x), decimals(y))
    return abs(x - y) <= TWIN_M + 1e-12 or (d >= 1 and math.isclose(abs(x - y), 10.0 ** -d, rel_tol=1e-6))


def heights(positions, indices, box, step=0.05):
    """A mesh's highest surface on a grid of `step` m over `box` (x0, z0, x1, z1) in viewer metres: NaN off it."""
    import numpy as np

    P = np.asarray(positions, float).reshape(-1, 3)
    T = np.asarray(indices, int).reshape(-1, 3)
    x0, z0, x1, z1 = box
    nx, nz = int((x1 - x0) / step) + 1, int((z1 - z0) / step) + 1
    H = np.full((nz, nx), np.nan)
    for a, b, c in P[T]:
        lo = np.floor((np.minimum(np.minimum(a, b), c) - [x0, 0, z0]) / step).astype(int)
        hi = np.ceil((np.maximum(np.maximum(a, b), c) - [x0, 0, z0]) / step).astype(int)
        xs = np.arange(max(lo[0], 0), min(hi[0] + 1, nx))
        zs = np.arange(max(lo[2], 0), min(hi[2] + 1, nz))
        if not len(xs) or not len(zs):
            continue
        X, Z = np.meshgrid(x0 + xs * step, z0 + zs * step)
        den = (b[2] - c[2]) * (a[0] - c[0]) + (c[0] - b[0]) * (a[2] - c[2])
        if abs(den) < 1e-12:
            continue
        l1 = ((b[2] - c[2]) * (X - c[0]) + (c[0] - b[0]) * (Z - c[2])) / den
        l2 = ((c[2] - a[2]) * (X - c[0]) + (a[0] - c[0]) * (Z - c[2])) / den
        l3 = 1 - l1 - l2
        inside = (l1 >= -1e-9) & (l2 >= -1e-9) & (l3 >= -1e-9)
        iz, ix = np.meshgrid(zs, xs, indexing="ij")
        H[iz[inside], ix[inside]] = np.fmax(H[iz[inside], ix[inside]], (l1 * a[1] + l2 * b[1] + l3 * c[1])[inside])
    return H


def same_surface(a, b, path):
    """Two meshes with the same vertices but other triangles: the same surface within TWIN_M, where both have one."""
    import numpy as np

    pa, pb = np.asarray(a["positions"], float).reshape(-1, 3), np.asarray(b["positions"], float).reshape(-1, 3)
    if not len(pa) or not len(pb):
        return [] if len(pa) == len(pb) else [f"{path}: {len(pa)} vertices, abroad {len(pb)}"]
    lo, hi = np.minimum(pa.min(axis=0), pb.min(axis=0)), np.maximum(pa.max(axis=0), pb.max(axis=0))
    box = (lo[0] - 0.05, lo[2] - 0.05, hi[0] + 0.05, hi[2] + 0.05)
    ha, hb = heights(pa, a["indices"], box), heights(pb, b["indices"], box)
    both, one = np.isfinite(ha) & np.isfinite(hb), np.isfinite(ha) ^ np.isfinite(hb)
    worst = float(np.abs(ha - hb)[both].max()) if both.any() else 0.0
    out = []
    if worst > TWIN_M:
        out.append(f"{path}: its surface is up to {worst * 1000:.1f} mm off the demo's")
    if one.sum() > max(4, 0.002 * both.sum()):     # cells a triangle's edge falls either side of
        out.append(f"{path}: {int(one.sum())} of its 5 cm cells are covered in only one of the two")
    return out


def twin_diffs(a, b, swap, path=""):
    """What differs between the demo's model (a) and the abroad site's (b), beyond what the grid changes:
    `swap` maps the abroad site's own words (its grid's and datum's names, its title) to the demo's."""
    if isinstance(a, dict) and isinstance(b, dict):
        if "indices" in a and "positions" in a and a.get("indices") != b.get("indices"):
            yield from same_surface(a, b, path)
            a, b = ({k: v for k, v in x.items() if k != "indices"} for x in (a, b))
        for k in sorted(set(a) | set(b)):
            if k not in a or k not in b:
                yield f"{path}/{k}: only in {'the demo' if k in a else 'abroad'}"
            else:
                yield from twin_diffs(a[k], b[k], swap, f"{path}/{k}")
    elif isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            yield f"{path}: {len(a)} items, abroad {len(b)}"
        else:
            for i, (x, y) in enumerate(zip(a, b)):
                yield from twin_diffs(x, y, swap, f"{path}[{i}]")
    elif isinstance(a, (int, float)) and isinstance(b, (int, float)):
        if not same_number(a, b):
            yield f"{path}: {a} -> {b}"
    elif isinstance(a, str) and isinstance(b, str):
        for theirs, ours in swap.items():
            b = b.replace(theirs, ours)
        if a != b:
            yield f"{path}: {a[:60]!r} -> {b[:60]!r}"
    elif a != b:
        yield f"{path}: {str(a)[:60]!r} -> {str(b)[:60]!r}"


def from_origin(model, summary=None):
    """The model (or the summary it came with) with what's written in the site's own grid coordinates made
    relative to the site's origin."""
    import copy

    e0, n0 = model["origin"][:2]
    if summary is not None:
        s = copy.deepcopy(summary)
    else:
        m = copy.deepcopy(model)
        m["origin"][:2] = [0.0, 0.0]
        m["extent"] = [m["extent"][0] - e0, m["extent"][1] - n0, m["extent"][2] - e0, m["extent"][3] - n0]
        s = m.get("summary") or {}
    frame = s.get("options_site", {}).get("frame", {})
    for key in ("origin", "toward"):
        if key in frame:
            frame[key] = [frame[key][0] - e0, frame[key][1] - n0, *frame[key][2:]]
    return s if summary is not None else m


def twin(demo, abroad):
    """The abroad site's build (viewer/data) against the demo's: what differs beyond the grid, as lines."""
    import numpy as np
    from PIL import Image

    ma, mb = read_json(demo / "model.json"), read_json(abroad / "model.json")
    a, b = from_origin(ma), from_origin(mb)
    for m in (a, b):            # the checklist speaks of each country's own documents: only what it lists, and finds, count
        for source in m.get("enrich", {}).get("sources", []):
            for key in ("title", "why", "where", "how"):
                source.pop(key, None)
    swap = {b[k]: a[k] for k in ("crs", "vertical", "title") if isinstance(b.get(k), str) and b.get(k)}
    drop = ("built", "commit")
    out = [f"model.json{d}" for d in twin_diffs({k: v for k, v in a.items() if k not in drop},
                                                 {k: v for k, v in b.items() if k not in drop}, swap)]
    sa, sb = from_origin(ma, read_json(demo / "summary.json")), from_origin(mb, read_json(abroad / "summary.json"))
    out += [f"summary.json{d}" for d in twin_diffs(sa, sb, swap)]
    names = {p.name for p in demo.iterdir()} | {p.name for p in abroad.iterdir()}
    for name in sorted(names - {"model.json", "summary.json", "build.json"}):
        x, y = demo / name, abroad / name
        if not x.exists() or not y.exists():
            out.append(f"{name}: only in {'the demo' if x.exists() else 'abroad'}")
        elif x.read_bytes() == y.read_bytes():
            continue
        elif x.suffix.lower() in (".jpg", ".jpeg", ".png"):
            p, q = Image.open(x), Image.open(y)
            if p.size != q.size:
                out.append(f"{name}: {p.size[0]}x{p.size[1]} pixels, abroad {q.size[0]}x{q.size[1]}")
                continue
            d = np.abs(np.asarray(p.convert("RGB"), int) - np.asarray(q.convert("RGB"), int))
            if d.mean() > IMAGE_MEAN or d.max() > IMAGE_MAX:
                out.append(f"{name}: its colours differ by {d.mean():.2f} on average, {d.max()} at most")
        else:
            out.append(f"{name}: differs")
    return out


def report(d):
    rows, details, broke = [], [], []
    for name in SITES:
        base, head = status(d, "base", name), status(d, "head", name)
        if "not built" in (base, head):
            moved = "not built"
        elif base == head == "ok":
            built = [str(d / f"{side}-{name}" / "viewer" / "data") for side in ("base", "head")]
            r = subprocess.run([sys.executable, str(HERE / "compare_builds.py"), *built], check=False, capture_output=True,
                               text=True, encoding="utf-8")
            first = (r.stdout.strip().splitlines() or ["(no output)"])[0]
            moved = "nothing" if r.returncode == 0 else first
            if r.returncode:
                details += [f"### {name}: what moved", *fenced(r.stdout + r.stderr)]
        elif base == "ok":
            broke.append(name)
            moved = "**this pull request can't build it**"
            details += [f"### {name}: the build's last lines", *fenced(tail(d / f"head-{name}.log"))]
        elif head == "ok":
            moved = "builds now"
        else:
            moved = "fails with both"
            details += [f"### {name}: the build's last lines", *fenced(tail(d / f"head-{name}.log"))]
        rows.append(f"| {name} | {base} | {head} | {moved} |")
    if status(d, "head", "demo") == status(d, "head", "abroad") == "ok":
        off = twin(*(d / f"head-{name}" / "viewer" / "data" for name in ("demo", "abroad")))
        if off:
            broke.append("abroad")
            details += [f"### abroad against the demo: {len(off)} differences beyond the grid", *fenced("\n".join(off[:40]))]
        twin_line = ("The abroad site builds the demo's model within 1.5 mm." if not off else
                     "**The abroad site doesn't build the demo's model:** see below.")
    else:
        twin_line = "The abroad site and the demo weren't both built, so they weren't compared."
    print("\n".join(["## Made-up sites: the base against this pull request", "",
                     "| Site | Base | This pull request | What moved |", "|---|---|---|---|", *rows, "", twin_line, "",
                     *details]))
    return 1 if broke else 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="command", required=True)
    sub.add_parser("write", help="write the made-up sites").add_argument("out", type=Path)
    sub.add_parser("report", help="say what moved between the base's builds and the head's").add_argument("dir", type=Path)
    tw = sub.add_parser("twin", help="the abroad site's build against the demo's")
    tw.add_argument("demo", type=Path)
    tw.add_argument("abroad", type=Path)
    args = ap.parse_args(argv)
    if args.command == "write":
        write(args.out)
        return 0
    if args.command == "twin":
        off = twin(args.demo, args.abroad)
        print("\n".join(off) if off else "The abroad site builds the demo's model within 1.5 mm.")
        return 1 if off else 0
    return report(args.dir)


if __name__ == "__main__":
    sys.exit(main())
