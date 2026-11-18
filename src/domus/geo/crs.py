"""A site's grid and height datums, and the transforms between its grid, NZTM2000 and longitude and latitude.

A site's data is on the grid its data/site.toml names, [crs] epsg, with heights on [crs] vertical. A site can list
other height datums its documents use, [datums.<key>] label and offset_m, where their level = the model's height +
offset_m. A Grid holds all of that and the viewer's origin, [model] origin, and does the sums. It's made from a
site's settings (Grid.from_settings) and reads no site itself: a Site (domus.project.site) has its own, `site.grid`.
Rasters are read on their own grids (domus.geo.raster).

The names this module had when it read the current site as well (ROOT, OUTPUT, settings(), site_format(),
site_epsg(), label(), site_to_nztm(), nztm_to_site(), site_to_lonlat(), datums(), to_model_height() and
from_model_height()) still give the current site's (domus.project.site.current()), for site code written before.
They're deprecated, and say so where they're used: a Site's path, output, settings(), format() and grid give the same.
"""

from dataclasses import dataclass
from functools import lru_cache

from pyproj import CRS, Transformer

from ..errors import DomusError, deprecated

NZTM_EPSG = 2193
LONLAT_EPSG = 4326


@dataclass(frozen=True)
class Datum:
    """Another height datum a site's documents use, `key` in [datums]: their level = the model's height + offset_m."""

    key: str
    label: str
    offset_m: float


@dataclass(frozen=True)
class Grid:
    """A site's grid and heights: the grid's EPSG code (`epsg`, a grid in metres) and its name for people (`label`),
    the height datum's name (`vertical`, "" for none named), the viewer's origin as the site gives it (`origin`: E, N
    and maybe a height, or None) and the other height datums its documents use (`datums`). Its methods turn points
    between the grid, NZTM2000 and longitude and latitude, and heights between the datums."""

    epsg: int
    label: str = ""
    vertical: str = ""
    origin: tuple | None = None
    datums: tuple[Datum, ...] = ()

    def __post_init__(self):
        if not self.label:              # the grid's own name and code, unless the site gives one
            object.__setattr__(self, "label", f"{_name(self.epsg)} (EPSG:{self.epsg})")

    @classmethod
    def from_settings(cls, settings):
        """The grid a site's settings give (data/site.toml as read): [crs] epsg, label and vertical, [model] origin
        and [datums]. Since site format 3 the grid has no default, so a site that doesn't name it is refused."""
        given = settings.get("crs", {})
        epsg = given.get("epsg")
        if epsg is None:
            raise DomusError("data/site.toml: [crs] epsg is missing. Give the site's grid as its EPSG code, such as 2193 "
                             "for NZTM2000 or 27700 for the British National Grid")
        datums = []
        for key, d in settings.get("datums", {}).items():
            if "offset_m" not in d:
                raise DomusError(f"data/site.toml: [datums.{key}] has no offset_m, its level less the model's height")
            datums.append(Datum(key, d.get("label", key), d["offset_m"]))
        origin = settings.get("model", {}).get("origin")
        return cls(int(epsg), given.get("label") or "", given.get("vertical", ""),
                   tuple(origin) if origin is not None else None, tuple(datums))

    def to_nztm(self, e, n):
        """Points on this grid on NZTM2000 (EPSG:2193), as (x, y)."""
        return _transformer(self.epsg, NZTM_EPSG).transform(e, n)

    def from_nztm(self, x, y):
        """Points on NZTM2000 on this grid, as (e, n)."""
        return _transformer(NZTM_EPSG, self.epsg).transform(x, y)

    def to_lonlat(self, e, n):
        """Points on this grid as longitude and latitude (WGS 84, EPSG:4326), as (lon, lat)."""
        return _transformer(self.epsg, LONLAT_EPSG).transform(e, n)

    def datum(self, key):
        """The other height datum `key`: a KeyError if the site has no such datum."""
        for d in self.datums:
            if d.key == key:
                return d
        raise KeyError(key)

    def to_model_height(self, level, key):
        """A level on the datum `key` as a height on the model's datum."""
        return level - self.datum(key).offset_m

    def from_model_height(self, h, key):
        """A height on the model's datum as a level on the datum `key`."""
        return h + self.datum(key).offset_m


@lru_cache
def _name(epsg):
    return CRS.from_epsg(epsg).name


@lru_cache
def _transformer(source, target):
    return Transformer.from_crs(source, target, always_xy=True)


# The names from before the grid was split from the site, deprecated: each gives the current site's, as it did. A
# Site (domus.project.site) has them all: its path, output, settings(), format() and grid.

def _old(name, new):
    """Says that crs.`name` is deprecated, naming what's `new`."""
    deprecated(f"domus.geo.crs.{name} is deprecated: use the site's {new} (domus.project.site.Site; a site that "
               "domus.api.open_site() opens has its path, output, settings() and grid too)")


def _site():
    # The one way from here to a site, for these names alone (see pyproject.toml's import rules).
    from ..project.site import current

    return current()


def __getattr__(name):
    """ROOT and OUTPUT, deprecated: the current site's folder (Site.path), and the folder its model is built into
    (Site.output)."""
    if name == "ROOT":
        _old("ROOT", "path")
        return _site().path
    if name == "OUTPUT":
        _old("OUTPUT", "output")
        return _site().output
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def settings():
    """Deprecated, Site.settings(): the current site's settings. settings.cache_clear() has them read again."""
    _old("settings()", "settings()")
    return _site().settings()


def _forget():
    _old("settings.cache_clear()", "forget_settings()")
    _site().forget_settings()


settings.cache_clear = _forget


def site_format():
    """Deprecated, Site.format(): the format the current site's own files are in."""
    _old("site_format()", "format()")
    return _site().format()


_settings = settings


def _grid(name, new):
    # Made from crs.settings at each call, as these read it before, so code that replaces crs.settings (tests did)
    # still has them read its own. It's `name` that's said to be deprecated, not settings() as well.
    _old(name, new)
    return Grid.from_settings(_site().settings() if settings is _settings else settings())


def site_epsg():
    """Deprecated, Site.grid.epsg: the current site's grid, [crs] epsg."""
    return _grid("site_epsg()", "grid.epsg").epsg


def label():
    """Deprecated, Site.grid.label: the current site's grid's name for people."""
    return _grid("label()", "grid.label").label


def site_to_nztm(e, n):
    """Deprecated, Site.grid.to_nztm(e, n), on the current site."""
    return _grid("site_to_nztm()", "grid.to_nztm()").to_nztm(e, n)


def nztm_to_site(x, y):
    """Deprecated, Site.grid.from_nztm(x, y), on the current site."""
    return _grid("nztm_to_site()", "grid.from_nztm()").from_nztm(x, y)


def site_to_lonlat(e, n):
    """Deprecated, Site.grid.to_lonlat(e, n), on the current site."""
    return _grid("site_to_lonlat()", "grid.to_lonlat()").to_lonlat(e, n)


def datums():
    """Deprecated, Site.grid.datums: the current site's other height datums, as [{key, label, offset_m}]."""
    return [{"key": d.key, "label": d.label, "offset_m": d.offset_m} for d in _grid("datums()", "grid.datums").datums]


def to_model_height(level, key):
    """Deprecated, Site.grid.to_model_height(level, key), on the current site."""
    return _grid("to_model_height()", "grid.to_model_height()").to_model_height(level, key)


def from_model_height(h, key):
    """Deprecated, Site.grid.from_model_height(h, key), on the current site."""
    return _grid("from_model_height()", "grid.from_model_height()").from_model_height(h, key)
