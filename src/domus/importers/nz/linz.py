"""Fetch a site's ground model, surface model, aerial photo and near-infrared photo from LINZ's open data.

LINZ publishes these as Cloud Optimised GeoTIFFs in public AWS buckets (CC BY 4.0), each survey a
STAC collection. This finds the newest survey covering the site and reads only the internal blocks
it needs, over HTTP range requests. Outputs keep LINZ's own grid (NZTM2000, EPSG:2193; heights
NZVD2016) so nothing is resampled here; the build samples them in the site frame.

    domus fetch
"""

import json
import re
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urljoin

import numpy as np
import rasterio
from pyproj import Transformer
from rasterio.windows import Window, from_bounds

from ...errors import DomusError
from ...geo import crs
from ...project import rasters

ELEVATION = "https://nz-elevation.s3.ap-southeast-2.amazonaws.com/catalog.json"
IMAGERY = "https://nz-imagery.s3.ap-southeast-2.amazonaws.com/catalog.json"

# LINZ's tiles are named on the NZ Topo50 sheets: BX24_1000_0244 is sheet BX24 at 1:1000, row 02,
# column 44. Sheets are 24 x 36 km in NZTM, with no I or O in their letters.
SHEET_LETTERS = "ABCDEFGHJKLMNPQRSTUVWXYZ"
SHEET_W, SHEET_H = 24000.0, 36000.0
SHEET_X0, SHEET_Y0 = 988000.0, 6810000.0
TILE_RE = re.compile(r"([A-Z]{2})(\d\d)_(\d+)_(\d\d)(\d\d)\.json$")
COVERAGE_RE = re.compile(r"\((\d{4})(?:-(\d{4}))?\)\s*$")

GDAL_ENV = {
    "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
    "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".tiff",
    "GDAL_HTTP_MAX_RETRY": "3",
}


def get_json(url):
    with urllib.request.urlopen(url, timeout=60) as r:
        return json.load(r)


def tile_bounds(name):
    """NZTM bounds of a LINZ tile from its name, such as BX24_1000_0244.json."""
    m = TILE_RE.search(name)
    if not m:
        return None
    letters, col, scale, r, c = m.groups()
    n = 50000 // int(scale)
    w, h = SHEET_W / n, SHEET_H / n
    rows = SHEET_LETTERS.index(letters[0]) * len(SHEET_LETTERS) + SHEET_LETTERS.index(letters[1])
    x0 = SHEET_X0 + int(col) * SHEET_W + (int(c) - 1) * w
    y1 = SHEET_Y0 - rows * SHEET_H - (int(r) - 1) * h
    return x0, y1 - h, x0 + w, y1


def tiles_covering(links, bounds):
    """(tile names touching `bounds`, whether they cover it all) from a collection's item links."""
    x0, y0, x1, y1 = bounds
    hits = []
    for name in links:
        b = tile_bounds(name)
        if b and b[0] < x1 and b[2] > x0 and b[1] < y1 and b[3] > y0:
            hits.append((name, b))
    if not hits:
        return [], False
    # Every corner and the middle fall in a tile: enough at a site's size against tiles of 480 m or more.
    probes = [(x, y) for x in (x0, (x0 + x1) / 2, x1) for y in (y0, (y0 + y1) / 2, y1)]
    covered = all(any(b[0] <= x <= b[2] and b[1] <= y <= b[3] for _, b in hits) for x, y in probes)
    return [n for n, _ in hits], covered


def _year(title):
    m = COVERAGE_RE.search(title or "")
    return int(m.group(2) or m.group(1)) if m else 0


def _gsd(collection):
    """A collection's ground sample distance in metres, from its `gsd` or its title; large if it gives neither."""
    if collection.get("gsd"):
        return float(collection["gsd"])
    m = re.search(r"(\d+(?:\.\d+)?)m\b", collection.get("title", ""))
    return float(m.group(1)) if m else 1e9


def find_surveys(catalog_url, match, bounds, recent_years=0):
    """Collections in a catalog whose tiles cover `bounds`, best first, as (title, [tile URLs]): the newest, or with
    `recent_years` the sharpest of those within that many years of the newest. Lazily, so a caller can stop at the
    first whose tiles hold data here: a survey's tiles can reach past the area it flew, empty."""
    catalog = get_json(catalog_url)
    children = [link for link in catalog["links"] if link["rel"] == "child" and match(link["href"])]
    by_year = {}
    for link in children:
        by_year.setdefault(_year(link.get("title")), []).append(link)
    window, newest = [], None
    with ThreadPoolExecutor(8) as pool:
        for year in sorted(by_year, reverse=True):
            if newest is not None and year < newest - recent_years and window:
                yield from (f[2:] for f in sorted(window, key=lambda f: (f[1], -f[0])))
                window = []
            urls = [urljoin(catalog_url, link["href"]) for link in by_year[year]]
            for url, coll in zip(urls, pool.map(get_json, urls)):
                items = [link["href"] for link in coll["links"] if link["rel"] == "item"]
                names, covered = tiles_covering(items, bounds)
                if covered:
                    newest = year if newest is None else newest
                    window.append((year, _gsd(coll) if recent_years else 0, coll["title"],
                                   [urljoin(url, n).replace(".json", ".tiff") for n in names]))
    yield from (f[2:] for f in sorted(window, key=lambda f: (f[1], -f[0])))


def find_survey(catalog_url, match, bounds, recent_years=0):
    """The best of find_surveys: (title, [tile URLs]), or None."""
    return next(find_surveys(catalog_url, match, bounds, recent_years), None)


def clip(name, urls, bounds, out_path, bands=None, say=print):
    """Read the window covering `bounds` from each COG and mosaic them on the first one's grid. `bands` (0-based) keeps
    just those, losslessly; otherwise a photo keeps its first three as a JPEG. What it wrote, labelled `name`, goes to
    `say`."""
    with rasterio.Env(**GDAL_ENV):
        with rasterio.open(urls[0]) as ref:
            res = ref.res
            win = from_bounds(*bounds, transform=ref.transform).round_offsets().round_lengths()
            transform = ref.window_transform(win)
            profile = ref.profile.copy()
            nodata = ref.nodata
        height, width = int(win.height), int(win.width)
        out = np.full((profile["count"], height, width), nodata if nodata is not None else 0,
                      dtype=profile["dtype"])
        filled = np.zeros((height, width), bool)
        for url in urls:
            with rasterio.open(url) as src:
                assert src.res == res and src.crs.to_epsg() == crs.NZTM_EPSG, (url, src.res, src.crs)
                col_off = int(round((transform.c - src.transform.c) / res[0]))
                row_off = int(round((src.transform.f - transform.f) / res[1]))
                # Intersect the wanted window with this file's extent.
                c0, r0 = max(col_off, 0), max(row_off, 0)
                c1 = min(col_off + width, src.width)
                r1 = min(row_off + height, src.height)
                if c1 <= c0 or r1 <= r0:
                    continue
                data = src.read(window=Window(c0, r0, c1 - c0, r1 - r0))
                dr, dc = r0 - row_off, c0 - col_off
                region = (slice(dr, dr + (r1 - r0)), slice(dc, dc + (c1 - c0)))
                take = ~filled[region]
                out[(slice(None),) + region][:, take] = data[:, take]
                filled[region] |= True
    if not filled.all():
        say(f"  warning: {name} has {np.count_nonzero(~filled)} cells outside the source tiles")
    has_data = filled & (out[0] != nodata) if nodata is not None else filled
    profile.update(driver="GTiff", width=width, height=height, transform=transform,
                   tiled=False, blockxsize=None, blockysize=None)
    for key in ("blockxsize", "blockysize", "tiled", "interleave"):
        profile.pop(key, None)
    if bands is not None:
        out = out[list(bands)]
        profile.update(count=len(bands), compress="deflate", photometric="minisblack")
    elif profile["dtype"] == "uint8":
        profile.update(compress="jpeg", jpeg_quality=92, photometric="ycbcr")
        out = out[:3]
        profile.update(count=3)
    else:
        profile.update(compress="deflate", predictor=3)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(out_path, "w", **profile) as dst:
        dst.write(out)
    say(f"  {name}: {width}x{height} px at {res[0]} m -> {out_path}")
    return float(has_data.mean())


def site_bounds(site, margin=12.0):
    """The site's model's extent in NZTM with a margin, so the build can sample just past its edge. LINZ covers New
    Zealand only: a site elsewhere is told to bring its own rasters."""
    from ...project.newsite import in_new_zealand

    to_nztm = Transformer.from_crs(site.grid.epsg, crs.NZTM_EPSG, always_xy=True)
    e0, n0, e1, n1 = site.settings()["model"]["extent"]
    if not in_new_zealand(*site.grid.to_lonlat((e0 + e1) / 2, (n0 + n1) / 2)):
        raise DomusError("domus fetch gets the ground model and photos from LINZ, which covers New Zealand only. A site "
                         "elsewhere brings its own, on any grid: its ground model in data/lidar/dem.tif, and its surface "
                         "model and photo if it has them (see docs/new-site.md).")
    xs, ys = to_nztm.transform([e0, e1, e0, e1], [n0, n0, n1, n1])
    return min(xs) - margin, min(ys) - margin, max(xs) + margin, max(ys) + margin


MIN_DATA_SHARE = 0.9    # of the clip that has to hold data: less, and the survey stops short here though its tiles don't

SHARP = re.compile(r"_0\.(0\d+|1\d*|2\d*|3)m/")

# name, catalog, which collections, the site's raster (domus.project.rasters), required, years to look back for a
# sharper one, bands kept (None: as the photo). The near-infrared photo keeps red and near-infrared, for the
# vegetation's NDVI.
SOURCES = (
    ("ground model", ELEVATION, lambda h: "/dem_1m/" in h, "dem", True, 0, None),
    ("surface model", ELEVATION, lambda h: "/dsm_1m/" in h, "dsm", False, 0, None),
    ("aerial photo", IMAGERY, lambda h: "/rgb/2193/" in h and SHARP.search(h), "aerial", False, 2, None),
    ("near-infrared photo", IMAGERY, lambda h: "/rgbnir/2193/" in h and SHARP.search(h), "nir", False, 2, (0, 3)),
)


def main(site, say=print):
    """`domus fetch` for a site in New Zealand: its rasters from LINZ, saying what it gets to `say`."""
    bounds = site_bounds(site)
    say(f"NZTM2000 bounds: {[round(v, 2) for v in bounds]}")
    used = {}
    for name, catalog, match, raster_key, required, recent, bands in SOURCES:
        tried = False
        for title, urls in find_surveys(catalog, match, bounds, recent):
            tried = True
            say(f"  {name}: {title}, {len(urls)} tile(s)")
            if clip(name, urls, bounds, rasters.path(site, raster_key), bands, say=say) >= MIN_DATA_SHARE:
                used[name] = {"title": title, "tiles": urls}
                break
            say(f"  {name}: {title} has no data over most of the site, trying the next")
        else:
            if required:
                raise DomusError(f"LINZ has no {name} covering this site. A site can bring its own: see docs/new-site.md.")
            say(f"  {name}: LINZ has none covering this site, carrying on without it")
            if tried:      # the last clip tried is empty: don't leave it for the build
                rasters.path(site, raster_key).unlink(missing_ok=True)
    record = site.path / "data" / "linz-sources.json"
    record.write_text(json.dumps(used, indent=2) + "\n", encoding="utf-8")
    say(f"recorded the surveys used in {record}")


if __name__ == "__main__":
    from ...project.site import started

    main(started())       # run by itself, on the site the environment names
