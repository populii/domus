"""A made-up site, so the app can be tried without any real data.

    domus demo my-demo

Everything here is generated: a sloping lot somewhere in the sea off the west coast, with a small
house, a block wall, a drain, a few trees, a fence and two retaining wall options. Nothing comes
from a real place.
"""

import argparse
import json

import numpy as np
import rasterio
from rasterio.transform import from_origin

from . import newsite

# In the Tasman Sea, far off the coast: no one can mistake this for a real section.
X0, Y0 = 1500000.0, 5500000.0
LOT = [[X0, Y0 + 30], [X0 + 22, Y0 + 30], [X0 + 22, Y0], [X0, Y0], [X0, Y0 + 30]]
HOUSE = [[X0 + 5, Y0 + 15], [X0 + 17, Y0 + 15], [X0 + 17, Y0 + 6], [X0 + 5, Y0 + 6]]
WALL = [[X0 + 2, Y0 + 22], [X0 + 20, Y0 + 22]]
HEIGHT0 = 40.0
ABOUT = "A made-up site that shows what the app does. Nothing here is a real place."


def ground(x, y):
    """A slope falling north, with a gentle roll."""
    u, v = (x - X0) / 22.0, (y - Y0) / 30.0
    return HEIGHT0 + 4.0 * (1 - v) + 0.5 * np.sin(u * 3.0) + 0.25 * np.cos(v * 7.0)


TREES = [(X0 + 3.0, Y0 + 27.0, 3.0, 6.5), (X0 + 19.0, Y0 + 26.0, 2.4, 5.0), (X0 + 19.5, Y0 + 3.0, 2.0, 4.0)]


def write_tif(path, array, x0, y1, res, nodata=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    bands, h, w = array.shape
    with rasterio.open(path, "w", driver="GTiff", width=w, height=h, count=bands, dtype=array.dtype, crs="EPSG:2193",
                       transform=from_origin(x0, y1, res, res), nodata=nodata, compress="deflate") as dst:
        dst.write(array)


def write_rasters(root, ext):
    x0, y0, x1, y1 = ext[0] - 12, ext[1] - 12, ext[2] + 12, ext[3] + 12
    xs = np.arange(x0, x1, 1.0) + 0.5
    ys = y1 - np.arange(0, y1 - y0, 1.0) - 0.5
    X, Y = np.meshgrid(xs, ys)
    dem = ground(X, Y).astype("float32")
    dsm = dem.copy()
    for tx, ty, r, h in TREES:
        d = np.hypot(X - tx, Y - ty)
        dsm = np.where(d < r, np.maximum(dsm, dem + h * np.sqrt(np.clip(1 - (d / r) ** 2, 0, 1))), dsm)
    inside = (X > HOUSE[0][0]) & (X < HOUSE[1][0]) & (Y < HOUSE[0][1]) & (Y > HOUSE[2][1])
    dsm = np.where(inside, dem + 5.0, dsm).astype("float32")
    write_tif(root / "data" / "lidar" / "dem.tif", dem[None], x0, y1, 1.0, -9999.0)
    write_tif(root / "data" / "lidar" / "dsm.tif", dsm[None], x0, y1, 1.0, -9999.0)
    # The aerial: painted grass, the house's roof, the trees' crowns, the drive.
    res = 0.1
    xs = np.arange(x0, x1, res) + res / 2
    ys = y1 - np.arange(0, y1 - y0, res) - res / 2
    X, Y = np.meshgrid(xs, ys)
    rng = np.random.default_rng(7)
    rgb = np.stack([np.full(X.shape, c, "float32") for c in (112, 138, 78)])
    drive = X > X0 + 22
    rgb[:, drive] = np.array([168, 166, 160], "float32")[:, None]
    rgb[:, (X > HOUSE[0][0]) & (X < HOUSE[1][0]) & (Y < HOUSE[0][1]) & (Y > HOUSE[2][1])] = np.array([70, 72, 78], "float32")[:, None]
    for tx, ty, r, _ in TREES:
        rgb[:, np.hypot(X - tx, Y - ty) < r] = np.array([52, 88, 44], "float32")[:, None]
    rgb = (rgb + rng.normal(0, 7, rgb.shape)).clip(0, 255).astype("uint8")
    write_tif(root / "data" / "aerial" / "aerial.tif", rgb, x0, y1, res)


def fc(name, features):
    return {"type": "FeatureCollection", "name": name,
            "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:EPSG::2193"}}, "features": features}


def feat(gtype, coords, **props):
    return {"type": "Feature", "properties": props, "geometry": {"type": gtype, "coordinates": coords}}


def write_layers(root):
    site = root / "data" / "site"
    fl = float(ground(X0 + 11, Y0 + 10)) + 0.4
    walls = feat("Polygon", [[*HOUSE, HOUSE[0]]], id="house-walls", name="House", source="made up", soffit=round(fl + 2.5, 2),
                 faces=["north", "east", "south", "west"])
    door = feat("LineString", [[X0 + 10, Y0 + 15], [X0 + 11, Y0 + 15]], id="door", kind="door", face="north",
                sill_above_fl=0.0, head_above_fl=2.0)
    window = feat("LineString", [[X0 + 13, Y0 + 15], [X0 + 15, Y0 + 15]], id="window", kind="window", face="north",
                  sill_above_fl=0.9, head_above_fl=2.1)
    (site / "house.geojson").write_text(json.dumps(fc("house", [walls, door, window])), encoding="utf-8", newline="\n")
    top = [[*p, round(float(ground(*p)) + 0.7, 2)] for p in WALL]
    wall = feat("LineString", top, id="W1", name="Old block wall", material="block", thickness_m=0.2,
                retained_side="right", confidence="made up", note="A low wall across the lot.")
    (site / "walls-existing.geojson").write_text(json.dumps(fc("walls-existing", [wall])), encoding="utf-8", newline="\n")
    drain = feat("LineString", [[X0 + 17, Y0 + 11], [X0 + 21, Y0 + 11], [X0 + 21, Y0 + 32]], id="drain", name="Stormwater drain",
                 source="made up")
    (site / "services.geojson").write_text(json.dumps(fc("services", [drain])), encoding="utf-8", newline="\n")
    strip = [[X0 + 19, Y0 + 30], [X0 + 22, Y0 + 30], [X0 + 22, Y0 + 8], [X0 + 19, Y0 + 8], [X0 + 19, Y0 + 30]]
    easement = feat("Polygon", [strip], id="easement-a", kind="easement", name="Area A (made up)", intent="Easement", on_lot=True,
                    source="made up", note="A drainage easement over the drain, made up")
    (site / "title.geojson").write_text(json.dumps(fc("title", [easement])), encoding="utf-8", newline="\n")
    trees = []
    for k, (tx, ty, r, h) in enumerate(TREES):
        a = np.linspace(0, 2 * np.pi, 13)
        ring = [[round(tx + r * np.cos(t), 2), round(ty + r * np.sin(t), 2)] for t in a]
        ring[-1] = ring[0]
        trees.append(feat("Polygon", [ring], id=f"tree-{k + 1}", kind="tree", name=f"Tree {k + 1}", height_m=h, crown_base_m=1.2,
                          trunk_diameter_m=0.25, look="broadleaf", colour="#3f6b35", confidence="made up", source="made up"))
    lawn = [[X0 + 1, Y0 + 29], [X0 + 21, Y0 + 29], [X0 + 21, Y0 + 16], [X0 + 1, Y0 + 16], [X0 + 1, Y0 + 29]]
    trees.append(feat("Polygon", [lawn], id="lawn-front", kind="lawn", name="Front lawn", colour="#6f9a48", source="made up"))
    (site / "trees.geojson").write_text(json.dumps(fc("trees", trees)), encoding="utf-8", newline="\n")
    fence = feat("LineString", [[X0, Y0], [X0, Y0 + 30]], id="fence-west", kind="fence", style="paling", name="West fence", height_m=1.8,
                 material="paling-grey", frame_material="timber-grey", rails="right", post_spacing_m=2.4, source="made up")
    (site / "features-demo.geojson").write_text(json.dumps(fc("features-demo", [fence])), encoding="utf-8", newline="\n")


OPTION_A = '''name = "A. One low wall"
short = "A. One wall"
description = "A single timber wall holds a level lawn."
steps = 0
keep_existing = []

[[walls]]
id = "A1"
name = "Lawn wall"
material = "timber"
line_so = [[8.0, 2.0], [8.0, 20.0]]

[[terraces]]
id = "lawn"
name = "Lawn"
kind = "lawn"
level = {level_a}
at_so = [11.0, 11.0]
fall = "1:40"
fall_to = "north"
batter = 2.5
polygon_so = [[8.0, 2.0], [8.0, 20.0], [14.0, 20.0], [14.0, 2.0]]
'''

OPTION_B = '''name = "B. Two tiers"
short = "B. Two tiers"
description = "Two low block walls with a planted bed between them and more flat lawn above."
steps = 1
keep_existing = []

[[walls]]
id = "B1"
name = "Lower wall"
material = "block"
line_so = [[3.0, 2.0], [3.0, 20.0]]

[[walls]]
id = "B2"
name = "Upper wall"
material = "block"
line_so = [[7.0, 2.0], [7.0, 20.0]]

[[terraces]]
id = "bed"
name = "Planted bed"
kind = "planting"
level = {level_b1}
at_so = [5.0, 11.0]
batter = 2.5
polygon_so = [[3.0, 2.0], [3.0, 20.0], [7.0, 20.0], [7.0, 2.0]]

[[terraces]]
id = "lawn"
name = "Lawn"
kind = "lawn"
level = {level_b2}
at_so = [11.0, 11.0]
fall = "1:40"
fall_to = "north"
batter = 2.5
polygon_so = [[7.0, 2.0], [7.0, 20.0], [14.0, 20.0], [14.0, 2.0]]
'''


def write_options(root):
    # The frame runs south along the east boundary from the north-east corner.
    level = lambda s: round(float(ground(X0 + 11, Y0 + 30 - s)), 1)
    (root / "options" / "a-one-wall.toml").write_text(OPTION_A.format(level_a=level(11) + 0.1), encoding="utf-8", newline="\n")
    (root / "options" / "b-two-tiers.toml").write_text(OPTION_B.format(level_b1=level(5) + 0.2, level_b2=level(11) + 0.2),
                                                       encoding="utf-8", newline="\n")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="domus demo", description="Write the made-up demo site.")
    ap.add_argument("path")
    args = ap.parse_args(argv)
    root = newsite.write_project(args.path, "Demo site (made up)", LOT, "made up", ABOUT)
    ext = newsite.extent_of(LOT)
    write_rasters(root, ext)
    write_layers(root)
    write_options(root)
    fl = round(float(ground(X0 + 11, Y0 + 10)) + 0.4, 2)
    with open(root / "data" / "site.toml", "a", encoding="utf-8", newline="\n") as fh:
        fh.write(f"\n[house]\nfl_upper = {fl}\nroof_pitch_deg = 25.0\nroof_plane_at_wall = {fl + 2.8:.2f}\neaves_overhang_m = 0.4\n")
    print(f"Wrote the demo site to {root}. Next:\n  domus --site {root} build\n  domus --site {root} serve")


if __name__ == "__main__":
    main()
