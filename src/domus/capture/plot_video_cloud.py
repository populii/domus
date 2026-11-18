"""Plan view of georeferenced video point clouds over the aerial, for checking.

    domus video plot VIDEO [more tags] [--out name]

Writes data/video/checks/<name>-plan.jpg (default name: the first tag): points in their own
colour, each model's camera path in its own colour, the title boundary in yellow and the
house outline in red, with a 5 m grid labelled in grid metres.
"""


import numpy as np
from PIL import Image, ImageDraw

from ..geo import geojson
from ..project import rasters

MARGIN_M = 5.0     # shown beyond the boundary on each side
PATH_COLOURS = [(255, 0, 255), (0, 220, 255), (255, 140, 0), (120, 255, 0), (255, 255, 255), (0, 90, 255)]


def plot(site, tags, out=""):
    """`domus video plot`: placed reconstructions `tags` in plan over the aerial, as
    data/video/checks/<out>-plan.jpg (the first tag unless `out` is given). Gives that file."""
    ring = geojson.read(site.path / "data" / "site" / "boundary.geojson")["features"][0]["geometry"]["coordinates"][0]
    e0, n0 = min(q[0] for q in ring) - MARGIN_M, min(q[1] for q in ring) - MARGIN_M
    e1, n1 = max(q[0] for q in ring) + MARGIN_M, max(q[1] for q in ring) + MARGIN_M
    px = 0.03
    w, h = int((e1 - e0) / px), int((n1 - n0) / px)
    E, N = np.meshgrid(e0 + (np.arange(w) + 0.5) * px, n1 - (np.arange(h) + 0.5) * px)
    a = rasters.aerial(site)
    base = np.stack([a.sample(E, N, band=b) for b in range(3)], -1)
    img = Image.fromarray((np.nan_to_num(base, nan=200) * 0.55 + 100).clip(0, 255).astype(np.uint8))
    dr = ImageDraw.Draw(img)

    def p(e, n):
        return (e - e0) / px, (n1 - n) / px

    for k in range(int(e0), int(e1) + 1, 5):
        dr.line([p(k, n0), p(k, n1)], fill=(150, 150, 150), width=1)
        dr.text((p(k, n1)[0] + 2, 2), str(k)[-3:], fill=(0, 0, 0))
    for k in range(int(n0), int(n1) + 1, 5):
        dr.line([p(e0, k), p(e1, k)], fill=(150, 150, 150), width=1)
        dr.text((2, p(e0, k)[1] + 2), str(k)[-3:], fill=(0, 0, 0))
    for tag in tags:
        d = np.load(site.path / "data" / "video" / f"{tag}-site.npz")
        xyz, rgb = d["xyz"], d["rgb"]
        inside = (xyz[:, 0] > e0) & (xyz[:, 0] < e1) & (xyz[:, 1] > n0) & (xyz[:, 1] < n1) & (d["nview"] >= 3)
        for (x, y, _), c in zip(xyz[inside], rgb[inside]):
            dr.point(p(x, y), fill=tuple(int(v) for v in c))
    dr.line([p(*q[:2]) for q in ring], fill=(255, 220, 0), width=2)
    house = geojson.read(site.path / "data" / "site" / "house.geojson")["features"][0]["geometry"]["coordinates"][0]
    dr.line([p(*q[:2]) for q in house], fill=(230, 30, 30), width=2)
    for k, tag in enumerate(tags):
        cams = np.load(site.path / "data" / "video" / f"{tag}-site.npz")["cameras"]
        colour = PATH_COLOURS[k % len(PATH_COLOURS)]
        dr.line([p(c[0], c[1]) for c in cams], fill=colour, width=2)
        x, y = p(cams[0][0], cams[0][1])
        dr.text((x + 4, y), tag, fill=colour)
    checks = site.path / "data" / "video" / "checks"
    checks.mkdir(exist_ok=True)
    name = out or tags[0]
    img.save(checks / f"{name}-plan.jpg", quality=88)
    print(f"wrote {checks / f'{name}-plan.jpg'}")
    return checks / f"{name}-plan.jpg"
