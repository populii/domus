"""Read the clipped LINZ rasters (NZTM2000) and sample them at site coordinates."""

from functools import lru_cache

import numpy as np
import rasterio
from scipy.ndimage import map_coordinates

from .crs import ROOT, settings, site_to_nztm

DEFAULTS = {"DEM": ("dem", "data/lidar/dem.tif"), "DSM": ("dsm", "data/lidar/dsm.tif"),
            "AERIAL": ("aerial", "data/aerial/aerial.tif"), "NIR": ("nir", "data/aerial/nir.tif")}


def __getattr__(name):
    # raster.DEM and so on: read when used, since a site's settings can name its own files.
    if name in DEFAULTS:
        key, default = DEFAULTS[name]
        return ROOT / settings().get("rasters", {}).get(key, default)
    raise AttributeError(name)


def shift(key):
    """How far the site moves a raster from where its file puts it, (east, north) in metres:
    [rasters.shift_m] <key> in data/site.toml. For a photo that sits off the ground model and the house."""
    east, north = settings().get("rasters", {}).get("shift_m", {}).get(key, (0.0, 0.0))
    return float(east), float(north)


class Raster:
    def __init__(self, path, shift=(0.0, 0.0)):
        with rasterio.open(path) as src:
            self.data = src.read().astype(np.float32)
            self.transform = src.transform
            self.nodata = src.nodata
        if self.nodata is not None:
            self.data[self.data == self.nodata] = np.nan
        self.inverse = ~self.transform
        self.shift = shift

    def sample(self, e, n, band=0, order=1):
        """Values at site coordinates (bilinear by default); NaN outside the clip."""
        e = np.asarray(e, float) - self.shift[0]
        n = np.asarray(n, float) - self.shift[1]
        x, y = site_to_nztm(e.ravel(), n.ravel())
        col, row = self.inverse * (np.asarray(x), np.asarray(y))
        vals = map_coordinates(self.data[band], [np.asarray(row) - 0.5, np.asarray(col) - 0.5],
                               order=order, cval=np.nan, mode="constant")
        return vals.reshape(e.shape)


@lru_cache
def dem():
    """Bare-ground model, LiDAR 1 m DEM."""
    return Raster(__getattr__("DEM"), shift("dem"))


@lru_cache
def dsm():
    """First-return surface model (roofs, trees), same survey."""
    return Raster(__getattr__("DSM"), shift("dsm"))


@lru_cache
def aerial():
    """Aerial photo, RGB."""
    return Raster(__getattr__("AERIAL"), shift("aerial"))


@lru_cache
def nir():
    """Near-infrared aerial photo: band 0 red, band 1 near-infrared. Optional."""
    return Raster(__getattr__("NIR"), shift("nir"))
