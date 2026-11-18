"""A site's rasters: the files its settings name, [rasters] in data/site.toml, each read once and kept on the site,
and sampled at points on its grid (domus.geo.raster)."""

from ..geo.raster import Raster
from .site import cached

FILES = {"dem": "data/lidar/dem.tif", "dsm": "data/lidar/dsm.tif", "aerial": "data/aerial/aerial.tif",
         "nir": "data/aerial/nir.tif"}


def path(site, key):
    """The file of the site's raster `key` (dem, dsm, aerial or nir): [rasters] <key> in data/site.toml, in its folder,
    since a site's settings can name its own files."""
    return site.path / site.settings().get("rasters", {}).get(key, FILES[key])


def shift(site, key):
    """How far the site moves a raster from where its file puts it, (east, north) in metres:
    [rasters.shift_m] <key> in data/site.toml. For a photo that sits off the ground model and the house."""
    east, north = site.settings().get("rasters", {}).get("shift_m", {}).get(key, (0.0, 0.0))
    return float(east), float(north)


def read(site, key, heights=False):
    """The site's raster `key`, read now, sampled on its grid and moved as the site says."""
    return Raster(path(site, key), site.grid.epsg, shift(site, key), heights)


@cached
def dem(site):
    """The bare-ground model, the LiDAR's DEM."""
    return read(site, "dem", heights=True)


@cached
def dsm(site):
    """The first-return surface model (roofs, trees), from the same survey."""
    return read(site, "dsm", heights=True)


@cached
def aerial(site):
    """The aerial photo, RGB."""
    return read(site, "aerial")


@cached
def nir(site):
    """The near-infrared aerial photo: band 0 red, band 1 near-infrared. Optional."""
    return read(site, "nir")
