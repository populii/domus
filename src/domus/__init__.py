"""Site model: survey data in; terrain, buildings, walls and retaining wall options out.

The package's parts (see ARCHITECTURE.md): geo/ (grids, rasters, GeoJSON, meshes, fits), model/ (the ground, house,
walls, features, plants, drains), options/ (the wall options), build/, checks/, capture/ (video and scans), prep/
(from documents), importers/ (places into site folders), project/ (the site folder: its Site, settings, formats and
edits), and at the top the command line, the server, the public API (api, testing) and the viewer.

Modules that moved into those parts in 2026.11.38 can still be imported by their old names (domus.terrain,
domus.video.georef_sfm, ...): each old name is the module itself, so site tools written before keep working.
"""

import importlib
import importlib.abc
import importlib.util
import sys

__version__ = "2026.11.38"

MOVED = {
    "buildcache": "build.cache",
    "canopy": "model.canopy",
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
        module = importlib.import_module(self._new(spec.name))
        self._real = getattr(self, "_real", {})
        self._real[spec.name] = module.__spec__         # the import system sets the alias's spec on it: put it back
        return module

    def exec_module(self, module):
        real = self._real.pop(module.__spec__.name, None)
        if real is not None:
            module.__spec__ = real


if not any(isinstance(f, _OldNames) for f in sys.meta_path):
    sys.meta_path.insert(0, _OldNames())
