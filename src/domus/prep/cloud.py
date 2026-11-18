"""A placed cloud's ground against the model's and the LiDAR's, cell by cell, and the cloud seen from above.

    domus prep cloud CLOUD[,CLOUD...] "S0,O0 S1,O1" [--cell 1.0 [1.0]] [--pct 20] [--min 12] [--by level]
                     [--against lidar] [--grid]
    domus prep cloud-plan CLOUD[,CLOUD...] "S0,O0 S1,O1" OUT.png [--px 0.02] [--mode top|low|height] [--above H]
                          [--lines] [--grid]

A CLOUD is a placed reconstruction's points in data/video/: `<tag>-site`, its sparse points (those seen from three
cameras or more, unless --all), or `<tag>-dense`; or the path of an .npz file with `xyz` (and `rgb`). The box's two
corners are in the wall options' frame (s along its reference boundary, o in from it: options/_site.toml), or with
--grid on the site's grid (east, north).

`cloud` cuts the box into cells, --cell metres along it and across (one size for both, or two), and reads the cloud's
ground in each against the ground under it: the model's ground with the site's ground fixes, or with --against lidar
the ground model alone. It takes each point's height less the ground under it and prints the --pct percentile of those
differences in centimetres (the cloud's ground under grass and whatever stands on it, less the ground's); with
--by level it takes the cell's --pct percentile level instead, less the ground under its lowest points (those within
3 cm of that level). Each row also has the cloud's own level at the percentile and how many points the cell has;
cells with fewer than --min points are left out. The last line gives the median and quartiles over the cells, and
--csv FILE writes the table too.

`cloud-plan` draws the cloud from above over the box, each pixel the colour of the highest point in it (--mode low:
the lowest; --mode height: grey by the lowest point's height above the model's ground, black at 0 to white at 1 m),
leaving out points more than --above metres over the ground (branches over a wall). --lines draws the lot's
boundary (red), the existing walls (yellow) and the house (white).
"""

import csv
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from ..errors import DomusError
from ..geo import geojson
from ..model import house_source, terrain
from ..project import rasters


def video_dir(site):
    """The site's video work, data/video/."""
    return site.path / "data" / "video"


def read_clouds(site, names, all_points=False):
    """The points (N x 3) and their colours (N x 3, grey where a cloud has none) of the clouds `names`, together."""
    xyz, rgb = [], []
    for name in names:
        given = Path(name)
        path = given if given.suffix == ".npz" and given.exists() else video_dir(site) / (name if name.endswith(".npz") else f"{name}.npz")
        if not path.exists():
            raise DomusError(f"There's no cloud {name}: name a placed reconstruction's data/video/<tag>-site or "
                             "<tag>-dense, or give an .npz file with xyz")
        with np.load(path, allow_pickle=False) as c:
            p = np.asarray(c["xyz"], float)
            keep = c["nview"] >= 3 if "nview" in c and not all_points else np.ones(len(p), bool)
            xyz.append(p[keep])
            rgb.append(np.asarray(c["rgb"])[keep].astype(np.uint8) if "rgb" in c else np.full((int(keep.sum()), 3), 200, np.uint8))
    return np.vstack(xyz), np.vstack(rgb)


def axes(site, grid=False):
    """(to the box's coordinates, from them) for the options' frame, or the site's grid with `grid`."""
    if grid:
        return (lambda en: np.asarray(en, float)[..., :2]), (lambda uv: np.asarray(uv, float))
    from ..options import design

    fr = design.frame(site)
    return fr.so, fr.en


def box_of(corners):
    """((u0, v0), (u1, v1)) from two corners given any way round."""
    c = np.asarray(corners, float).reshape(-1, 2)
    if len(c) != 2:
        raise DomusError('Give the box as its two corners: "s,o s,o" (or "east,north east,north" with --grid)')
    lo, hi = c.min(axis=0), c.max(axis=0)
    if np.any(hi - lo <= 0):
        raise DomusError("The box's corners must differ in both directions")
    return lo, hi


def compare(site, xyz, corners, cell=1.0, pct=20.0, nmin=12, against="model", grid=False, by="points"):
    """The cloud's ground against the reference ground, cell by cell: [(u, v, points, difference, level)], u and v
    the cell's centre in the box's coordinates and `cell` its size (one, or along and across). The difference is the
    `pct` percentile of the points' heights over the reference ground (the model's, or the LiDAR's with
    against="lidar"), or with by="level" the cell's `pct` percentile level less the median of the reference ground
    under its points within 3 cm of that level; the level is the cloud's height at the percentile."""
    to_box, _ = axes(site, grid)
    (u0, v0), (u1, v1) = box_of(corners)
    uv = to_box(xyz[:, :2])
    inside = (uv[:, 0] >= u0) & (uv[:, 0] < u1) & (uv[:, 1] >= v0) & (uv[:, 1] < v1)
    xyz, uv = xyz[inside], uv[inside]
    if not len(xyz):
        return []
    if by not in ("points", "level"):
        raise DomusError(f"by = {by!r}: give points or level")
    cu, cv = (cell, cell) if np.isscalar(cell) else (float(cell[0]), float(cell[-1]))
    ref = terrain.ground(site) if against == "model" else rasters.dem(site)
    under = ref.sample(xyz[:, 0], xyz[:, 1])
    iu = np.floor((uv[:, 0] - u0) / cu).astype(np.int64)
    iv = np.floor((uv[:, 1] - v0) / cv).astype(np.int64)
    order = np.lexsort((iv, iu))
    iu, iv, under, z = iu[order], iv[order], under[order], xyz[order, 2]
    cut = np.flatnonzero((np.diff(iu) != 0) | (np.diff(iv) != 0)) + 1
    rows = []
    for a, b in zip(np.r_[0, cut], np.r_[cut, len(iu)], strict=True):
        if by == "points":
            d = z[a:b] - under[a:b]
            ok = np.isfinite(d)
            if ok.sum() < nmin:
                continue
            n, diff, level = int(ok.sum()), float(np.percentile(d[ok], pct)), float(np.percentile(z[a:b], pct))
        else:
            if b - a < nmin:
                continue
            level = float(np.percentile(z[a:b], pct))
            low = z[a:b] <= level + 0.03
            n, diff = b - a, level - float(np.median(under[a:b][low]))
        rows.append((u0 + (iu[a] + 0.5) * cu, v0 + (iv[a] + 0.5) * cv, n, diff, level))
    return rows


def plan(site, xyz, rgb, corners, out, px=0.02, mode="top", above=None, lines=False, grid=False):
    """The cloud from above over the box, written to `out`: each pixel the colour of the highest point in it (`mode`
    "low": the lowest; "height": grey by the lowest point's height over the model's ground, 0 to 1 m). Returns the
    image's size and how many points it drew."""
    to_box, _ = axes(site, grid)
    (u0, v0), (u1, v1) = box_of(corners)
    w, h = int(round((u1 - u0) / px)), int(round((v1 - v0) / px))
    uv = to_box(xyz[:, :2])
    keep = (uv[:, 0] > u0) & (uv[:, 0] < u1) & (uv[:, 1] > v0) & (uv[:, 1] < v1)
    g = None
    if above is not None or mode == "height":
        g = terrain.ground(site).sample(xyz[:, 0], xyz[:, 1])
    if above is not None:
        keep &= ~(xyz[:, 2] > g + above)
    uv, z, colour = uv[keep], xyz[keep, 2], rgb[keep]
    over = (z - g[keep]) if g is not None else None
    col = np.clip(((uv[:, 0] - u0) / px).astype(int), 0, w - 1)
    row = np.clip(((v1 - uv[:, 1]) / px).astype(int), 0, h - 1)
    order = np.argsort(z if mode == "top" else -z)        # each pixel takes the last of its points in this order
    within = order[np.argsort((row * w + col)[order], kind="stable")]
    pixel = (row * w + col)[within]
    last = within[np.r_[pixel[1:] != pixel[:-1], True]] if len(within) else within
    img = np.zeros((h, w, 3), np.uint8)
    if mode == "height":
        grey = np.clip(np.nan_to_num(over, nan=0.0) * 255, 0, 255).astype(np.uint8)
        img[row[last], col[last]] = np.repeat(grey[last, None], 3, axis=1)
    else:
        img[row[last], col[last]] = colour[last]
    im = Image.fromarray(img)
    d = ImageDraw.Draw(im)

    def at(u, v):
        return (u - u0) / px, (v1 - v) / px

    names = ("E", "N") if grid else ("s", "o")
    for u in np.arange(np.ceil(u0), u1 + 0.01, 1.0):          # a line every metre, named along the edges
        d.line([at(u, v0), at(u, v1)], fill=(110, 110, 110), width=1)
        d.text((at(u, v0)[0] + 2, h - 12), f"{names[0]} {u:.0f}", fill=(255, 255, 0))
    for v in np.arange(np.ceil(v0), v1 + 0.01, 1.0):
        d.line([at(u0, v), at(u1, v)], fill=(110, 110, 110), width=1)
        d.text((2, at(u0, v)[1] - 11), f"{names[1]} {v:.0f}", fill=(255, 255, 0))
    if lines:
        layers = site.path / "data" / "site"

        def draw(coords, colour):
            p = to_box(np.asarray(coords, float)[:, :2])
            d.line([at(a, b) for a, b in p], fill=colour, width=1)

        for f in geojson.read_optional(layers / "boundary.geojson"):
            if f["geometry"]["type"] == "Polygon":
                draw(f["geometry"]["coordinates"][0], (255, 40, 40))
        for f in geojson.read_optional(layers / "walls-existing.geojson"):
            draw(f["geometry"]["coordinates"], (255, 230, 0))
        walls = house_source.house(site).walls
        if walls is not None:
            draw(walls["geometry"]["coordinates"][0], (255, 255, 255))
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    im.save(out)
    return im.size, int(keep.sum())


def _names(clouds):
    """Clouds' names, from a list or comma-separated."""
    return clouds.split(",") if isinstance(clouds, str) else list(clouds)


def _corners(text):
    try:
        return [[float(v) for v in p.split(",")] for p in text.split()]
    except ValueError as e:
        raise DomusError(f'Give the box as its two corners, "s,o s,o": {e}') from e


def by_cell(site, clouds, box, cell=(1.0,), pct=20.0, nmin=12, by="points", against="model", all_points=False, grid=False,
            csv_file=None):
    """`domus prep cloud`: the clouds' ground against the model's or the LiDAR's, cell by cell, over `box`, printed
    as a table and written to `csv_file` too if it's given; the module's docstring says how. `clouds` are names in
    data/video (or .npz files), a list or comma-separated; `box` is two corners, a list or "s,o s,o". Gives the
    rows."""
    xyz, _ = read_clouds(site, _names(clouds), all_points)
    if len(cell) > 2:
        raise DomusError("--cell takes one size, or two: along the box and across it")
    rows = compare(site, xyz, _corners(box) if isinstance(box, str) else box, cell, pct, nmin, against, grid, by)
    names = ("E", "N") if grid else ("s", "o")
    head = [names[0], names[1], "points", "cloud - ground (cm)", "cloud level"]
    print(f"{names[0]:>9} {names[1]:>9} {'points':>7} {'cm':>6} {'level':>8}   (cloud's {pct:g}th percentile "
          f"less the {'model' if against == 'model' else 'LiDAR'}'s ground)")
    for u, v, n, d, z in rows:
        print(f"{u:9.2f} {v:9.2f} {n:7d} {100 * d:+6.0f} {z:8.2f}")
    if rows:
        d = np.array([r[3] for r in rows])
        print(f"{len(rows)} cells: median {100 * np.median(d):+.0f} cm, quartiles {100 * np.percentile(d, 25):+.0f} to "
              f"{100 * np.percentile(d, 75):+.0f} cm")
    else:
        print(f"No cell of the box has {nmin} points of the cloud or more.")
    if csv_file:
        with open(csv_file, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(head)
            w.writerows([[round(u, 3), round(v, 3), n, round(100 * d, 1), round(z, 3)] for u, v, n, d, z in rows])
    return rows


def from_above(site, clouds, box, out, px=0.02, mode="top", above=None, lines=False, all_points=False, grid=False):
    """`domus prep cloud-plan`: the clouds from above over `box`, in their own colours (`mode` top or low) or grey by
    height over the ground (height), `px` metres a pixel, into the picture `out`. Gives its size and the points
    drawn."""
    xyz, rgb = read_clouds(site, _names(clouds), all_points)
    size, n = plan(site, xyz, rgb, _corners(box) if isinstance(box, str) else box, out, px, mode, above, lines, grid)
    print(f"wrote {out} ({size[0]} x {size[1]} px, {n} points)")
    return size, n
