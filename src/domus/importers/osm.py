"""Anywhere: OpenStreetMap's address and buildings (domus.publicdata), and the Copernicus ground model.

The Copernicus DEM, GLO-30, covers the world's land on a grid of one arc-second, about 30 m, from the TanDEM-X radar
survey. Its open copy on AWS needs no key, and Domus reads just the site's patch of it, as it does LINZ's LiDAR. It's
a surface model, so buildings and trees are in it, but at 30 m a house is mostly smoothed away: it gives the lie of
the land, a first model and not a survey. Its heights are on EGM2008. Rasters are read on their own grid, so the patch
keeps the tiles' longitude and latitude.
"""

import json
import math

import numpy as np
import rasterio
from rasterio.errors import RasterioIOError
from rasterio.merge import merge

from ..errors import DomusError
from ..project import rasters

BASE = "https://copernicus-dem-30m.s3.amazonaws.com"
VERTICAL = "EGM2008"
CREDIT = ("Ground model: Copernicus DEM GLO-30, (c) DLR e.V. 2010-2014 and (c) Airbus Defence and Space GmbH 2014-2018, "
          "provided under COPERNICUS by the European Union and ESA. Buildings: OpenStreetMap contributors (ODbL 1.0).")
MARGIN_DEG = 0.002      # about 200 m past the site's extent: the build samples past its edge, and a 30 m grid needs room


def tile(lat, lon):
    """The GLO-30 tile with (lat, lon) in it, named for its south-west corner in whole degrees."""
    s, w = math.floor(lat), math.floor(lon)
    name = f"Copernicus_DSM_COG_10_{'N' if s >= 0 else 'S'}{abs(s):02d}_00_{'E' if w >= 0 else 'W'}{abs(w):03d}_00_DEM"
    return f"{BASE}/{name}/{name}.tif"


def site_box(site):
    """The site's model's extent in longitude and latitude, and a margin: (west, south, east, north)."""
    e0, n0, e1, n1 = site.settings()["model"]["extent"]
    lon, lat = site.grid.to_lonlat([e0, e1, e0, e1], [n0, n0, n1, n1])
    return min(lon) - MARGIN_DEG, min(lat) - MARGIN_DEG, max(lon) + MARGIN_DEG, max(lat) + MARGIN_DEG


def fetch(site, say=print):
    """data/lidar/dem.tif: the site's patch of the Copernicus ground model, from the tiles it falls on, saying what it
    gets to `say`."""
    west, south, east, north = site_box(site)
    urls = sorted({tile(lat, lon) for lat in (south, north) for lon in (west, east)})
    sources, used = [], []
    try:
        for url in urls:
            try:
                sources.append(rasterio.open(url))
                used.append(url)
            except RasterioIOError:
                say(f"  no Copernicus tile at {url.rsplit('/', 1)[-1]} (the sea, most likely)")
        if not sources:
            raise DomusError("The Copernicus ground model has no tile here. Put the site's own ground model in "
                             "data/lidar/dem.tif (see docs/new-site.md).")
        data, transform = merge(sources, bounds=(west, south, east, north), nodata=-9999.0)
        profile = {"driver": "GTiff", "width": data.shape[2], "height": data.shape[1], "count": 1, "dtype": "float32",
                   "crs": sources[0].crs, "transform": transform, "nodata": -9999.0, "compress": "deflate", "predictor": 3}
    finally:
        for src in sources:
            src.close()
    if not np.isfinite(data[0]).any() or (data[0] == -9999.0).all():
        raise DomusError("The Copernicus ground model has no heights over this site.")
    out = rasters.path(site, "dem")
    out.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(out, "w", **profile) as dst:
        dst.write(data[:1].astype(np.float32))
        dst.units = ("metre",)
    record = site.path / "data" / "copernicus-sources.json"
    record.write_text(json.dumps({"tiles": used, "credit": CREDIT}, indent=2) + "\n", encoding="utf-8", newline="\n")
    say(f"  ground model: Copernicus DEM GLO-30, {data.shape[2]}x{data.shape[1]} cells -> {out}")
    say(f"recorded the tiles used in {record}")
