"""Domus's public API: what site tools, a site's own tests and agents use.

    from domus import api

    site = api.open_site("site")            # a site project's folder: None means DOMUS_SITE, else the current folder
    print(site.title, round(site.lot().area), "m2")
    for tree in site.layer("trees"):
        print(tree["properties"]["id"])
    ground = site.ground()
    print(ground([e], [n]))                 # heights at points on the site's grid
    for option in site.options():
        print(option["name"], site.evaluate(option)["volumes"]["cut_m3"])

**The promise.** What this module offers keeps working from one release to the next. Changing any of it takes a
deprecation warning in an earlier release and a "For site projects" note in CHANGELOG.md. The rest of the `domus`
package is internal, and can change in any release.

**Several sites in one process.** Each Site reads its own folder, and what Domus reads of one is kept apart from
the others, so a tool can open two sites and compare them. A problem a person can put right, such as a missing
setting, comes as a DomusError whose message says what.
"""

import copy
import os
import re
from pathlib import Path

from .errors import DomusError

__all__ = ["DomusError", "Site", "clip", "fit_affine", "linz_query", "open_site", "resample_line"]

LAYER_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*")
_opened = {}


def open_site(path=None):
    """The site project in the folder `path`: None means the one DOMUS_SITE names, else the current folder. A folder that
    isn't a site project raises a DomusError.

    A process can open several sites; opening one twice gives the same Site. A site opened by a path other than
    DOMUS_SITE's is built into its own viewer/data, as `domus --site` does, whatever DOMUS_OUTPUT says."""
    from .project import site as sites

    root = Path(path if path is not None else os.environ.get("DOMUS_SITE") or ".").resolve()
    if not (root / "data" / "site.toml").exists():
        raise DomusError(f"{root} isn't a site project: it has no data/site.toml. domus new or domus create starts one.")
    if root not in _opened:
        started = sites.started()
        _opened[root] = Site(started if started.path == root else sites.Site(root))
    return _opened[root]


class Site:
    """A site project, as open_site gives it. Its methods read the site's files as they are when called, on the site's
    own grid (`[crs] epsg` in data/site.toml) with heights on its vertical datum."""

    def __init__(self, project):
        self._project = project
        self.path = project.path

    def __repr__(self):
        return f"Site({str(self.path)!r})"

    def _use(self):
        from .project.site import use

        return use(self._project)

    @property
    def output(self):
        """The folder its model is built into: viewer/data in the site, or DOMUS_OUTPUT for the site DOMUS_SITE names."""
        return self._project.output

    def settings(self):
        """data/site.toml, as a dict. It's a copy: changing it changes nothing."""
        from .geo import crs

        with self._use():
            return copy.deepcopy(dict(crs.settings()))

    @property
    def title(self):
        """The site's name for people: `[site] title`, else its folder's name."""
        return self.settings().get("site", {}).get("title") or self.path.name

    def layer(self, name):
        """The features of data/site/<name>.geojson, such as "trees" or "walls-existing": a list of GeoJSON features,
        empty if the site has no such layer."""
        from .geo import geojson

        if not isinstance(name, str) or not LAYER_NAME.fullmatch(name):
            raise DomusError(f"{name!r} isn't a layer's name: give the file's name without .geojson, such as trees")
        return geojson.read_optional(self.path / "data" / "site" / f"{name}.geojson")

    def lot(self):
        """The lot, a shapely Polygon in plan: boundary.geojson's feature that `[boundary] main` names, else its first."""
        import shapely
        from shapely.geometry import shape

        from .geo import geojson

        boundary = self.layer("boundary")
        if not boundary:
            raise DomusError(f"{self.path} has no boundary yet: data/site/boundary.geojson")
        return shapely.force_2d(shape(geojson.main_lot(boundary, self.settings())["geometry"]))

    def house_walls(self):
        """The house's walls, a shapely Polygon in plan (the "house-walls" feature in house.geojson), or None if the
        site has no house."""
        import shapely
        from shapely.geometry import shape

        walls = [f for f in self.layer("house") if f["properties"].get("id") == "house-walls"]
        return shapely.force_2d(shape(walls[0]["geometry"])) if walls else None

    def ground(self):
        """The ground model, as a function: ground(e, n) gives the heights at points on the site's grid, an array the
        shape of e and n. It's the LiDAR's bare ground (data/lidar/dem.tif, or the file `[rasters] dem` names) with
        ground-fixes.geojson applied, and NaN where that has no ground."""
        import numpy as np

        from .geo import raster
        from .model import terrain

        with self._use():
            if not raster.DEM.exists():
                raise DomusError(f"{self.path} has no ground model yet: domus fetch gets one from LINZ, or put your own "
                                 f"at {raster.DEM}")
            model = terrain.ground()

        def heights(e, n):
            e, n = np.broadcast_arrays(np.asarray(e, float), np.asarray(n, float))
            return model.sample(e.ravel(), n.ravel()).reshape(e.shape)

        return heights

    def options(self):
        """The site's wall options, options/*.toml but for the files starting with "_". Each is a dict of its settings
        with `id` (its file's name), `name`, `short`, `description`, `walls`, `terraces`, `stairs` and `path`."""
        from . import options

        with self._use():
            return options.load_all()

    def frame(self):
        """The frame the options are drawn in (options/_site.toml `[frame]`): s along a boundary, o in from it.
        frame.en(so) turns (s, o) points into the site's grid, and frame.so(en) the other way."""
        from .options import design

        with self._use():
            return design.frame()

    def evaluate(self, option):
        """What an option comes to, as the viewer and its comparison table show it: its walls and their heights, cut and
        fill, flat lawn, and its checks. `option` is one of options(), or its id."""
        from .options import design

        if isinstance(option, str):
            found = [o for o in self.options() if o["id"] == option]
            if not found:
                raise DomusError(f"{self.path} has no option {option!r} in options/")
            option = found[0]
        with self._use():
            return design.evaluate(option)

    def build(self):
        """Builds the model into `output`, as domus build does, and returns that folder."""
        from . import build

        with self._use():
            build.main()
        return self.output

    def check(self, checks=None, tags=None, plots=False):
        """Runs the adversarial checks, as domus prep check does: `checks` names some of sanity, roof, plumb, clouds,
        through, fixes and blocks (all of them unless given), `tags` the placed reconstructions for plumb and clouds, and `plots` also writes
        JPEGs. Returns the report it writes to .domus/checks/check_model.json in the site: `failures`, how many each
        check failed, `findings`, each a dict of check, level, object and detail, and `skipped`, why each check the
        site has nothing for was skipped."""
        from .prep import check

        with self._use():
            return check.run(checks or check.CHECKS, tags, plots)


def resample_line(line, spacing):
    """Points about every `spacing` metres along a polyline and at each of its own vertices, and their distances along
    it: (points, distances). Its points keep any third coordinate."""
    from .geo.mesh import resample_line as resample

    return resample(line, spacing)


def fit_affine(pix, world):
    """The least-squares affine transform from points on an image (column, row) to points on the ground (E, N):
    (A, residuals), where world = A @ [column, row, 1] and A is 2 by 3."""
    from .geo.georef import fit_affine as fit

    return fit(pix, world)


def linz_query(layer, cql=None, bbox=None, count=20, key=None):
    """Features of a LINZ Data Service layer, on NZTM2000 (EPSG:2193) with east before north. `layer` is the layer's
    number, such as 50820, or its name, "layer-50820". Give a `bbox` (E min, N min, E max, N max on NZTM), a `cql`
    filter, or both; `count` caps how many come back. The key is DOMUS_LINZ_KEY's unless given."""
    from .importers.nz import publicdata

    key = key or publicdata.linz_key()
    if not key:
        raise DomusError("This needs DOMUS_LINZ_KEY: a free key from data.linz.govt.nz.")
    if cql is None and bbox is None:
        raise DomusError("Give linz_query a bbox, a cql filter or both, rather than asking for a whole layer.")
    name = str(layer) if str(layer).startswith("layer-") else f"layer-{layer}"
    filters = [f for f in (cql, publicdata._bbox_cql(*bbox) if bbox is not None else None) if f]
    return publicdata._wfs(key, name, " AND ".join(f"({f})" for f in filters), count)


def clip(name, urls, bounds, out_path, bands=None):
    """Reads the window covering `bounds` (E min, N min, E max, N max, on the rasters' grid) from each cloud-optimised
    GeoTIFF in `urls`, such as the LINZ survey tiles in data/linz-sources.json, and writes their mosaic to `out_path`
    on the first one's grid. `bands` (counted from 0) keeps just those, losslessly; otherwise a photo keeps its first
    three, as a JPEG. `name` labels it in what's printed."""
    from .importers.nz import linz

    return linz.clip(name, urls, bounds, out_path, bands)
