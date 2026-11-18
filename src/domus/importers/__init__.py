"""Importers bring a place in. Each writes an ordinary site folder: the boundary, the buildings round it and the
rasters, and, as data in data/site.toml, the site's grid, its heights' datum, the credit line and `[domus] importer`.
The build never calls one, so a site built once builds without the internet.

    nz      New Zealand: LINZ's addresses, parcels, buildings, LiDAR and aerial photos, with OpenStreetMap's addresses
            and buildings when there's no LINZ key (domus.importers.nz: publicdata, linz, title and points).
    osm     Anywhere else: OpenStreetMap's address and buildings, and the Copernicus ground model (domus.importers.osm).
    manual  Your own boundary and rasters: `domus new`.

`domus create ADDRESS` looks in New Zealand first and then anywhere, and `domus fetch` gets a site's rasters from
the public source for where it is.
"""

from ..errors import DomusError
from ..project.newsite import in_new_zealand
from .nz import publicdata

NAMES = ("nz", "osm", "manual")


def lookup(address):
    """What's public about an address, with `epsg`, the grid its site goes on, and `importer`: New Zealand's sources
    first, then OpenStreetMap anywhere."""
    try:
        return {"epsg": 2193, **publicdata.lookup(address), "importer": "nz"}
    except publicdata.NotFound as nz:
        try:
            found = publicdata.from_osm(address, countrycodes=None)
        except publicdata.NotFound:
            raise nz from None
    return {**found, "importer": "nz" if in_new_zealand(*found["lonlat"]) else "osm"}


def in_nz(site):
    """Whether the site is in New Zealand, where LINZ's data covers it."""
    e0, n0, e1, n1 = site.settings()["model"]["extent"]
    return in_new_zealand(*site.grid.to_lonlat((e0 + e1) / 2, (n0 + n1) / 2))


def fetch(site, say=print):
    """`domus fetch`: the site's rasters from the public source for where it is: LINZ's LiDAR and photos in New Zealand,
    the Copernicus ground model anywhere else. What it gets goes to `say`."""
    if in_nz(site):
        from .nz import linz

        return linz.main(site, say)
    from . import osm

    try:
        return osm.fetch(site, say)
    except OSError as e:
        raise DomusError(f"The Copernicus ground model didn't load ({e}). Try again shortly, or bring your own: put "
                         "the site's ground model in data/lidar/dem.tif (see docs/new-site.md).") from e
