"""Rasters sampled at a site's coordinates.

Each raster is sampled on its own grid, the one its file names, so the LiDAR and photos can come on any grid: a file
that names none is taken to be on the site's, whose EPSG code it's given. A height raster whose band says it's in feet
is read in metres. This module reads no site: domus.project.rasters finds a site's rasters and reads each once.

The names it had when it read the current site as well (DEM, DSM, AERIAL, NIR, shift(), dem(), dsm(), aerial() and
nir()) still give the current site's, for site code written before. They're deprecated: domus.project.rasters gives
the same for the site it's handed.
"""

from functools import lru_cache

import numpy as np
import rasterio
from pyproj import CRS, Transformer
from scipy.ndimage import map_coordinates

FEET = {"ft": 0.3048, "foot": 0.3048, "feet": 0.3048, "international foot": 0.3048,
        "us survey foot": 1200 / 3937, "us-ft": 1200 / 3937, "ftus": 1200 / 3937, "us_survey_foot": 1200 / 3937}


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
    """The raster in the file at `path`, sampled at points on the site's grid (`epsg`, its EPSG code), moved by `shift`
    (east, north) from where its file puts it. `heights` reads a height band in feet in metres."""

    def __init__(self, path, epsg, shift=(0.0, 0.0), heights=False):
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
        self.to_own, self.to_site = _grids(epsg, self.crs)

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


# The names from before rasters were read through the site they're handed, deprecated: each gives the current
# site's, as it did.

OLD_PATHS = {"DEM": "dem", "DSM": "dsm", "AERIAL": "aerial", "NIR": "nir"}


def _old():
    # The one way from here to a site, for these names alone (see pyproject.toml's import rules).
    from ..project import rasters
    from ..project.site import current

    return rasters, current()


def __getattr__(name):
    """DEM, DSM, AERIAL and NIR, deprecated: domus.project.rasters.path(site, "dem") and so on, on the current site."""
    if name in OLD_PATHS:
        rasters, site = _old()
        return rasters.path(site, OLD_PATHS[name])
    raise AttributeError(name)


def shift(key):
    """Deprecated, domus.project.rasters.shift(site, key), on the current site."""
    rasters, site = _old()
    return rasters.shift(site, key)


def dem():
    """Deprecated, domus.project.rasters.dem(site), on the current site."""
    rasters, site = _old()
    return rasters.dem(site)


def dsm():
    """Deprecated, domus.project.rasters.dsm(site), on the current site."""
    rasters, site = _old()
    return rasters.dsm(site)


def aerial():
    """Deprecated, domus.project.rasters.aerial(site), on the current site."""
    rasters, site = _old()
    return rasters.aerial(site)


def nir():
    """Deprecated, domus.project.rasters.nir(site), on the current site."""
    rasters, site = _old()
    return rasters.nir(site)
