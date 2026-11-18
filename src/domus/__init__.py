"""Site model: survey data in; terrain, buildings, walls and retaining wall options out.

The package's parts (see ARCHITECTURE.md): geo/ (grids, rasters, GeoJSON, meshes, fits), model/ (the ground, house,
walls, features, plants, drains), options/ (the wall options), build/, checks/, capture/ (video and scans), prep/
(from documents), importers/ (places into site folders), project/ (the site folder: its Site, settings, formats and
edits), the public API (api, testing), and on top of them all app/ (the command line, the server and the viewer).

Modules that moved into those parts can still be imported by their old names (domus.terrain,
domus.video.georef_sfm, domus.cli, ...): each old name is the module itself, so site tools written before keep
working. Each is deprecated, and says so where it's imported (a DeprecationWarning naming the module's name now), and
`python -m domus.cli` still runs the command line. The old names go in a later release.
"""

import importlib
import importlib.abc
import importlib.util
import sys

from .errors import deprecated

__version__ = "2026.11.62"

MOVED = {
    "buildcache": "build.cache",
    "canopy": "model.canopy",
    "cli": "app.cli",
    "crs": "geo.crs",
    "demo": "project.demo",
    "design": "options.design",
    "drains": "model.drains",
    "enrich": "checks.enrich",
    "features": "model.features",
    "geojson": "geo.geojson",
    "georef": "geo.georef",
    "house": "model.house",
    "house_texture": "model.house_texture",
    "linz": "importers.nz.linz",
    "marks": "importers.nz.marks",
    "mesh": "geo.mesh",
    "newsite": "project.newsite",
    "points": "importers.nz.points",
    "prep.check": "checks.model",
    "publicdata": "importers.nz.publicdata",
    "raster": "geo.raster",
    "scan": "capture.scan",
    "scheme": "options.scheme",
    "serve": "app.serve",
    "sfm": "capture.sfm",
    "sfm_georef": "capture.sfm_georef",
    "sketch": "options.sketch",
    "terrain": "model.terrain",
    "tin": "model.tin",
    "title": "importers.nz.title",
    "vegetation": "model.vegetation",
    "video": "capture",
    "wall_looks": "model.wall_looks",
    "walls": "model.walls",
}


class _OldNames(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    """domus.<old name> is the module at its new name: the same object, so patching one patches the other."""

    def _new(self, name):
        rest = name[len("domus."):]
        if rest in MOVED:
            return "domus." + MOVED[rest]
        if rest.startswith("video."):
            return "domus.capture." + rest[len("video."):]
        return None

    def find_spec(self, name, path, target=None):
        if name.startswith("domus.") and self._new(name):
            return importlib.util.spec_from_loader(name, self)
        return None

    def create_module(self, spec):
        deprecated(f"{spec.name} is {self._new(spec.name)} now: import it by that name")
        module = importlib.import_module(self._new(spec.name))
        self._real = getattr(self, "_real", {})
        self._real[spec.name] = module.__spec__         # the import system sets the alias's spec on it: put it back
        return module

    def exec_module(self, module):
        real = self._real.pop(module.__spec__.name, None)
        if real is not None:
            module.__spec__ = real

    def get_code(self, name):
        """`python -m domus.<old name>`: the module at its new name, run as __main__."""
        new = self._new(name)
        said = f"python -m {name} is python -m {new} now"
        lines = ["import runpy", "import warnings", "from domus.errors import DomusDeprecationWarning",
                 f"warnings.warn({said!r}, DomusDeprecationWarning)",
                 f"runpy.run_module({new!r}, run_name='__main__', alter_sys=True)"]
        return compile("\n".join(lines) + "\n", name, "exec")


if not any(isinstance(f, _OldNames) for f in sys.meta_path):
    sys.meta_path.insert(0, _OldNames())
