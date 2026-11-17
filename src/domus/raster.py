"""Read a site's rasters and sample them at site coordinates.

Each raster is sampled on its own grid, the one its file names, so the LiDAR and photos can come on any grid: a file
that names none is taken to be on the site's. A height raster whose band says it's in feet is read in metres.
"""

from functools import lru_cache

import numpy as np
import rasterio
from pyproj import CRS, Transformer
from scipy.ndimage import map_coordinates

from .crs import ROOT, settings, site_epsg

FEET = {"ft": 0.3048, "foot": 0.3048, "feet": 0.3048, "international foot": 0.3048,
        "us survey foot": 1200 / 3937, "us-ft": 1200 / 3937, "ftus": 1200 / 3937, "us_survey_foot": 1200 / 3937}

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


@lru_cache
def _grids(site, own):
    """Site grid -> the raster's own, and back; None for each when they're the same grid."""
    if own is None or CRS.from_user_input(own) == CRS.from_epsg(site):
        return None, None
    return (Transformer.from_crs(site, own, always_xy=True), Transformer.from_crs(own, site, always_xy=True))


def metres_per_unit(units):
    """What one of a height band's units is in metres: 1 unless the file says feet."""
    return FEET.get((units or "").strip().lower(), 1.0)


class Raster:
    def __init__(self, path, shift=(0.0, 0.0), heights=False):
        with rasterio.open(path) as src:
            self.data = src.read().astype(np.float32)
            self.transform = src.transform
            self.nodata = src.nodata
            self.crs = (src.crs.to_epsg() or src.crs.to_wkt()) if src.crs else None   # its EPSG code, so the transform is the one by that code
            units = src.units
        if self.nodata is not None:
            self.data[self.data == self.nodata] = np.nan
        if heights:
            for band, unit in enumerate(units):
                if metres_per_unit(unit) != 1.0:
                    self.data[band] *= np.float32(metres_per_unit(unit))
        self.inverse = ~self.transform
        self.shift = shift
        self.to_own, self.to_site = _grids(site_epsg(), self.crs)

    def sample(self, e, n, band=0, order=1):
        """Values at site coordinates (bilinear by default); NaN outside the clip."""
        e = np.asarray(e, float) - self.shift[0]
        n = np.asarray(n, float) - self.shift[1]
        x, y = e.ravel(), n.ravel()
        if self.to_own is not None:
            x, y = self.to_own.transform(x, y)
        col, row = self.inverse @ (np.asarray(x), np.asarray(y))
        vals = map_coordinates(self.data[band], [np.asarray(row) - 0.5, np.asarray(col) - 0.5],
                               order=order, cval=np.nan, mode="constant")
        return vals.reshape(e.shape)

    def cells(self, band=0):
        """Each cell's middle on the site grid and its value: E, N and values, as arrays of the raster's shape."""
        rows, cols = self.data[band].shape
        cc, rr = np.meshgrid(np.arange(cols) + 0.5, np.arange(rows) + 0.5)
        x, y = self.transform @ (cc, rr)
        if self.to_site is not None:
            x, y = self.to_site.transform(np.asarray(x).ravel(), np.asarray(y).ravel())
            x, y = np.reshape(x, cc.shape), np.reshape(y, cc.shape)
        return np.asarray(x) + self.shift[0], np.asarray(y) + self.shift[1], self.data[band]


@lru_cache
def dem():
    """Bare-ground model, LiDAR 1 m DEM."""
    return Raster(__getattr__("DEM"), shift("dem"), heights=True)


@lru_cache
def dsm():
    """First-return surface model (roofs, trees), same survey."""
    return Raster(__getattr__("DSM"), shift("dsm"), heights=True)


@lru_cache
def aerial():
    """Aerial photo, RGB."""
    return Raster(__getattr__("AERIAL"), shift("aerial"))


@lru_cache
def nir():
    """Near-infrared aerial photo: band 0 red, band 1 near-infrared. Optional."""
    return Raster(__getattr__("NIR"), shift("nir"))
