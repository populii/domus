"""A plan of the LiDAR's candidate tree crowns over the aerial, for curating data/site/trees.geojson.

    domus canopy
    domus prep canopy-plot [out.jpg]
"""

import sys

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from .. import crs, geojson, raster, vegetation
from ..crs import ROOT


def main():
    extent = crs.settings()["model"]["extent"]
    cands, _ = vegetation.map_canopy(extent, vegetation.removed_areas(vegetation.read()))
    out = sys.argv[1] if len(sys.argv) > 1 else "data/sources/canopy.jpg"
    plot(extent, cands, ROOT / out)
    print(f"wrote {ROOT / out}")


def plot(extent, cands, path, px=0.075):
    e0, n0, e1, n1 = extent
    w, h = int(round((e1 - e0) / px)), int(round((n1 - n0) / px))
    E, N = np.meshgrid(e0 + (np.arange(w) + 0.5) * px, n1 - (np.arange(h) + 0.5) * px)
    a = raster.aerial()
    img = Image.fromarray(np.nan_to_num(np.stack([a.sample(E, N, band=b) for b in range(3)], -1), nan=200)
                          .clip(0, 255).astype(np.uint8))
    d = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("arial.ttf", 13)
    except OSError:
        font = ImageFont.load_default()

    def P(e, n):
        return (e - e0) / px, (n1 - n) / px

    lot = geojson.read(ROOT / "data" / "site" / "boundary.geojson")["features"][0]
    d.line([P(*p[:2]) for p in lot["geometry"]["coordinates"][0]], fill=(255, 220, 0), width=2)
    colours = {"tree": (255, 80, 200), "shrub": (120, 255, 120), "hedge": (90, 200, 255), "removed": (255, 60, 40)}
    for c in cands:
        kind = "removed" if c.get("removed") else vegetation.guess_kind(c)
        d.line([P(x, y) for x, y in c["outline"].exterior.coords], fill=colours[kind], width=2)
        x, y = P(*c["apex"])
        d.text((x - 8, y - 8), f"{c['segment']}", fill=(255, 255, 255), font=font, stroke_width=2,
               stroke_fill=(0, 0, 0))
    d.text((8, h - 22), "Candidate crowns from the LiDAR: pink tree, green shrub, blue hedge, red gone since; "
           "yellow: title boundary", fill=(255, 255, 255), font=font, stroke_width=2, stroke_fill=(0, 0, 0))
    img.save(path, quality=82, optimize=True)


