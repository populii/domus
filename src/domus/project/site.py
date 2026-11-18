"""A site project as a workflow holds it: its folder, where its model is built, its settings, the format its files are
in and its grid, and what Domus has read of it so far.

Every part of Domus that reads a site is handed it: the command line and the server make the Site once, from the
environment or --site, and pass it down, so one process can hold several sites, one after another or in turn, and each
is read as itself. Whatever a part keeps of what it read (the settings, the rasters, the ground) it keeps on the site
(`cached`), never in a module's own globals.
"""

import functools
import os
import sys
import tomllib
from pathlib import Path

from ..errors import DomusError
from . import formats, schema


class Site:
    """A site project: its folder (`path`), the folder its model is built into (`output`, viewer/data in the site
    unless given), its settings (`settings()`, from `settings_file`), the format its own files are in (`format()`), its
    grid (`grid`), and what Domus has read of it (`cache`, filled by the functions marked `cached`)."""

    def __init__(self, path, output=None):
        self.path = Path(path).resolve()
        self.output = Path(output).resolve() if output else self.path / "viewer" / "data"
        self.cache = {}
        self._settings = self._grid = None

    def __repr__(self):
        return f"Site({str(self.path)!r})"

    @property
    def settings_file(self):
        """Its settings' file, data/site.toml."""
        return self.path / "data" / "site.toml"

    def _toml(self):
        try:
            text = self.settings_file.read_text(encoding="utf-8")
        except FileNotFoundError:
            raise DomusError(f"{self.path} isn't a site project: it has no data/site.toml. domus new or domus create "
                             "starts one.") from None
        return tomllib.loads(text)

    def settings(self):
        """data/site.toml at this Domus's format, whatever format the site is in (domus.project.formats), read through
        the settings schema (domus.project.schema): a key the file leaves out reads as its default. It's read the first
        time it's asked for, and kept: forget_settings() has it read again."""
        if self._settings is None:
            site = formats.site(self._toml())
            for warning in schema.unknown(site, schema.SITE, "data/site.toml: "):
                print(f"Warning: {warning}", file=sys.stderr)
            self._settings = schema.Settings(site, schema.SITE, "data/site.toml: ")
        return self._settings

    def forget_settings(self):
        """Forgets the settings read so far, and the grid made from them: the next to ask reads data/site.toml again."""
        self._settings = self._grid = None

    def format(self):
        """The format the site's own files are in, as data/site.toml says now (settings() has them at this Domus's)."""
        return formats.format_of(self._toml())

    @property
    def grid(self):
        """Its grid and heights, from its settings: a domus.geo.crs.Grid, which turns points between its grid, NZTM2000
        and longitude and latitude, and heights between its datums."""
        if self._grid is None:
            # Imported here, so holding a site takes no more than the standard library.
            from ..geo.crs import Grid

            self._grid = Grid.from_settings(self.settings())
        return self._grid


_started = None


def started():
    """The site the process was started for: DOMUS_SITE (else the current folder), its model built into DOMUS_OUTPUT if
    that's set. Read the first time it's asked for, not when Domus is imported."""
    global _started
    if _started is None:
        root = Path(os.environ.get("DOMUS_SITE", ".")).resolve()
        _started = Site(root, os.environ.get("DOMUS_OUTPUT") or None)
    return _started


def start(path):
    """Makes the site in `path` the one the process was started for, as the command line's --site does: its model is
    built into its own viewer/data. Gives that Site."""
    global _started
    _started = Site(path)
    return _started


def cached(fn):
    """functools.lru_cache for fn(site), kept on the site: each site keeps its own result. fn.cache_clear(site)
    forgets that site's."""
    name = f"{fn.__module__}.{fn.__qualname__}"

    @functools.wraps(fn)
    def wrapper(site):
        key = (name, (), ())
        if key not in site.cache:
            site.cache[key] = fn(site)
        return site.cache[key]

    def cache_clear(site):
        for key in [k for k in site.cache if k[0] == name]:
            del site.cache[key]

    wrapper.cache_clear = cache_clear
    return wrapper
