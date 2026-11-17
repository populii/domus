"""The site project, its coordinate systems and height datums.

A site project is a folder holding data/site.toml, data/site/*.geojson, options/ and so on. Its
path comes from the DOMUS_SITE environment variable, or the current folder.

Site data is on the grid named by [crs] epsg in data/site.toml, heights on [crs] vertical.
LINZ LiDAR and aerial photos: NZTM2000 (EPSG:2193), heights NZVD2016.
A site can list other height datums its documents use: [datums.<key>] label, offset_m, where
their level = the model's height + offset_m.
"""

import os
import sys
import tomllib
from functools import lru_cache
from pathlib import Path

from pyproj import Transformer

ROOT = Path(os.environ.get("DOMUS_SITE", ".")).resolve()
OUTPUT = Path(os.environ.get("DOMUS_OUTPUT", ROOT / "viewer" / "data")).resolve()
NZTM_EPSG = 2193


@lru_cache
def settings():
    """data/site.toml, at this Domus's format whatever format the site is in (domus.project.formats), read through
    the settings schema (domus.project.schema): a key the file leaves out reads as its default."""
    from .project import formats, schema

    site = formats.site(tomllib.loads((ROOT / "data" / "site.toml").read_text(encoding="utf-8")))
    for warning in schema.unknown(site, schema.SITE, "data/site.toml: "):
        print(f"Warning: {warning}", file=sys.stderr)
    return schema.Settings(site, schema.SITE, "data/site.toml: ")


def site_format():
    """The format the site's own files are in, as data/site.toml says (settings() has them at this Domus's)."""
    from .project import formats

    return formats.format_of(tomllib.loads((ROOT / "data" / "site.toml").read_text(encoding="utf-8")))


def site_epsg():
    return settings().get("crs", {}).get("epsg", NZTM_EPSG)


def label():
    """The site grid's name for people: [crs] label, else the grid's own name and code."""
    given = settings().get("crs", {}).get("label")
    if given:
        return given
    from pyproj import CRS

    return f"{CRS.from_epsg(site_epsg()).name} (EPSG:{site_epsg()})"


@lru_cache
def _to_nztm():
    return Transformer.from_crs(site_epsg(), NZTM_EPSG, always_xy=True)


@lru_cache
def _to_site():
    return Transformer.from_crs(NZTM_EPSG, site_epsg(), always_xy=True)


@lru_cache
def _to_lonlat():
    return Transformer.from_crs(site_epsg(), 4326, always_xy=True)


def site_to_nztm(e, n):
    return _to_nztm().transform(e, n)


def nztm_to_site(x, y):
    return _to_site().transform(x, y)


def site_to_lonlat(e, n):
    return _to_lonlat().transform(e, n)


def datums():
    """The site's other height datums: [{key, label, offset_m}], for labels and the viewer."""
    return [{"key": k, "label": d.get("label", k), "offset_m": d["offset_m"]}
            for k, d in settings().get("datums", {}).items()]


def to_model_height(level, key):
    """A level on the datum `key` as a height in the model's datum."""
    return level - settings()["datums"][key]["offset_m"]


def from_model_height(h, key):
    return h + settings()["datums"][key]["offset_m"]
