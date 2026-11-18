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
    site.export("site.glb")                 # the built model for other software: glTF (.glb) or IFC (.ifc)

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
from contextlib import contextmanager
from pathlib import Path

from .errors import DomusError

__all__ = ["DomusError", "Site", "clip", "create", "demo", "fit_affine", "linz_query", "new", "open_site", "resample_line"]

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


def create(address, path=None, say=print):
    """A site project for an address, from public data, as `domus create` does: in the folder `path` (one named from
    the address unless given), with its lot, buildings, ground and photo fetched, its house and trees drafted, and its
    model built. A folder already holding this address carries on with its build. Gives the site, opened.

    `say` gets each line of progress; the fetch, draft and build run in a process of their own, which prints its own.
    An address that matches more than one place raises a DomusError whose message is `WHICH: ` and then JSON with the
    `address` and the `places` to choose from."""
    from . import create as creating

    return open_site(creating.from_address(address, path, say))


def new(path, name, boundary=None, centre=None, size=30.0, epsg=None, say=print):
    """Starts a site project, as `domus new` does: in the folder `path`, called `name`, on the lot in `boundary`, a
    GeoJSON polygon's file, or while there's no boundary yet, on a stand-in square `size` metres across at `centre`,
    (latitude, longitude). `epsg` names its grid, in metres: by default the boundary's own, else NZTM2000 in New
    Zealand and the UTM zone elsewhere. Gives the site, opened; what to do next goes to `say`."""
    from .project import newsite

    return open_site(newsite.start(path, name, boundary, centre, size, epsg, say))


def demo(path, say=print):
    """Writes the made-up demo site into the folder `path`, as `domus demo` does, and gives it, opened."""
    from .project import demo as made_up

    return open_site(made_up.write(path, say))


class Site:
    """A site project, as open_site gives it. Its methods read the site's files as they are when called, on the site's
    own grid (`[crs] epsg` in data/site.toml) with heights on its vertical datum."""

    def __init__(self, project):
        self._project = project
        self.path = project.path

    def __repr__(self):
        return f"Site({str(self.path)!r})"

    @property
    def output(self):
        """The folder its model is built into: viewer/data in the site, or DOMUS_OUTPUT for the site DOMUS_SITE names."""
        return self._project.output

    def settings(self):
        """data/site.toml, as a dict. It's a copy: changing it changes nothing."""
        return copy.deepcopy(dict(self._project.settings()))

    @property
    def title(self):
        """The site's name for people: `[site] title`, else its folder's name."""
        return self.settings().get("site", {}).get("title") or self.path.name

    @property
    def grid(self):
        """The site's grid and heights (`[crs]`, `[model] origin` and `[datums]` in data/site.toml): `epsg`, `label`,
        `vertical`, `origin` and `datums` (each with `key`, `label` and `offset_m`). to_nztm(e, n), from_nztm(x, y)
        and to_lonlat(e, n) turn points between its grid, NZTM2000 and longitude and latitude, and
        to_model_height(level, datum) and from_model_height(h, datum) heights between the model's datum and another."""
        return self._project.grid

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

        from .model import terrain
        from .project import rasters

        dem = rasters.path(self._project, "dem")
        if not dem.exists():
            raise DomusError(f"{self.path} has no ground model yet: domus fetch gets one from LINZ, or put your own "
                             f"at {dem}")
        model = terrain.ground(self._project)

        def heights(e, n):
            e, n = np.broadcast_arrays(np.asarray(e, float), np.asarray(n, float))
            return model.sample(e.ravel(), n.ravel()).reshape(e.shape)

        return heights

    def options(self):
        """The site's wall options, options/*.toml but for the files starting with "_". Each is a dict of its settings
        with `id` (its file's name), `name`, `short`, `description`, `walls`, `terraces`, `stairs` and `path`."""
        from . import options

        return options.load_all(self._project)

    def frame(self):
        """The frame the options are drawn in (options/_site.toml `[frame]`): s along a boundary, o in from it.
        frame.en(so) turns (s, o) points into the site's grid, and frame.so(en) the other way."""
        from .options import design

        return design.frame(self._project)

    def evaluate(self, option):
        """What an option comes to, as the viewer and its comparison table show it: its walls and their heights, cut and
        fill, flat lawn, and its checks. `option` is one of options(), or its id. Cut and fill are soil, measured from
        the ground as the build draws it (ground() with the existing walls' faces and tops in it): a new wall's own
        body and paving don't count."""
        from .options import design

        if isinstance(option, str):
            found = [o for o in self.options() if o["id"] == option]
            if not found:
                raise DomusError(f"{self.path} has no option {option!r} in options/")
            option = found[0]
        return design.evaluate(self._project, option)

    def build(self, fresh=False, say=print):
        """Builds the model into `output`, as domus build does, and returns that folder. `fresh` draws everything
        again, taking nothing from the last build. What it built goes to `say`."""
        from . import build

        build.main(self._project, fresh, say)
        return self.output

    def export(self, path, format=None, option=None):
        """Writes its built model for other software, as domus export does, and returns the file's path: glTF
        (`format` "glb", a binary .glb, for Blender and most 3D viewers) or IFC4 ("ifc", for architects' and builders'
        software; it needs IfcOpenShell, the ifc extra), by default the one the path's suffix says. `option`, a wall
        option's id, shows that option in place of what it replaces, as the viewer does; without it, the site as it
        is. It reads the model as last built into `output`, so build the site after changing it."""
        from . import export

        return export.write(self._project, path, format, option)

    def check(self, checks=None, tags=None, plots=False):
        """Runs the adversarial checks, as domus prep check does: `checks` names some of sanity, roof, plumb, clouds,
        through, fixes and blocks (all of them unless given), `tags` the placed reconstructions for plumb and clouds, and `plots` also writes
        JPEGs. Returns the report it writes to .domus/checks/check_model.json in the site: `failures`, how many each
        check failed, `findings`, each a dict of check, level, object and detail, and `skipped`, why each check the
        site has nothing for was skipped."""
        from .checks import model as check

        return check.run(self._project, checks or check.CHECKS, tags, plots)

    def fetch(self, say=print):
        """Gets the site's rasters from the public source for where it is, as domus fetch does: LINZ's LiDAR ground and
        surface models and aerial photo in New Zealand, the Copernicus ground model anywhere else. What it gets goes to
        `say`."""
        from . import importers

        with self._writing():
            importers.fetch(self._project, say)

    def map_canopy(self, say=print):
        """Maps candidate tree crowns from the LiDAR, as domus canopy does, for curating into data/site/trees.geojson,
        and returns the file it wrote: data/sources/canopy.geojson, or the one `[rasters] canopy` names."""
        from .model import canopy

        with self._writing():
            return canopy.map_site(self._project, say)

    def fetch_marks(self, height_mark=None, say=print):
        """Adds LINZ's survey marks around the lot, as domus marks does: the named boundary marks on its line, as the
        layer features-survey-marks, with the height mark `height_mark` (its name as `say` gets it) when one's named.
        The nearest height marks go to `say`. Returns the layer's features. It needs DOMUS_LINZ_KEY."""
        from .importers.nz import marks

        with self._writing():
            return marks.fetch(self._project, height_mark, say)

    def fetch_title(self, say=print):
        """The lot's record of title and the easements on and near it, from LINZ, as domus title does: written to
        data/site/title.geojson, and returned as its features. It needs DOMUS_LINZ_KEY."""
        from .importers.nz import title

        with self._writing():
            return title.update(self._project, say)

    def fetch_points(self, ground=False, say=print):
        """The classified LiDAR point cloud over the site, as domus points does: from LINZ (with a DOMUS_LINZ_KEY that
        can export) or OpenTopography, into data/lidar/points.laz, and with `ground` a 0.5 m ground model from its
        ground points too, data/lidar/dem-points.tif. Returns `points`, how many; `path`, the file; and
        `ground_share`, the share of the ground model's cells a ground point reached (None without `ground`). A site
        no point cloud covers raises a DomusError."""
        from .importers.nz import points

        with self._writing():
            return points.fetch(self._project, ground, say)

    def scan(self, file, tag, control=None, epsg=None, up=None, say=print):
        """Brings in a phone or tablet LiDAR scan, as domus scan does: the points in `file` (PLY, LAS, OBJ, or XYZ,
        TXT, PTS or CSV text), placed on the site's grid by `control`, a CSV of points in both (x, y and z in the scan,
        E, N and H on the grid), with `up` ("y" or "z") the scan's vertical axis taken as true; or, for a file already
        on a grid, by that grid's `epsg`. `tag` names it: letters, digits, - and _. Returns the file it wrote,
        data/video/<tag>-scan.npz, which the next `domus video export` takes in."""
        from .capture import scan

        with self._writing():
            return scan.bring_in(self._project, file, tag, control, epsg, up, say)

    def shots(self, spec, timeout=None, say=print):
        """Takes pictures of the built model as the viewer draws it, as domus shots does, from `spec`, a JSON file
        (docs/shots.md), within `timeout` seconds (ten minutes unless given). Each picture goes to `say` as it's
        written. Returns [(path, what the viewer showed)]. It needs Playwright and its Chromium (the `shots` extra)."""
        import math

        from . import shots

        timeout = shots.TIMEOUT_S if timeout is None else timeout
        if isinstance(timeout, bool) or not isinstance(timeout, int | float) or not (math.isfinite(timeout) and timeout > 0):
            raise DomusError("timeout is a number of seconds, more than 0")
        return shots.take(shots.read_spec(spec), self.output, timeout, say)

    def upgrade(self, dry_run=False, say=print):
        """Writes the site's migrations into its own files, as domus site upgrade does, so they say what this Domus
        reads, keeping the files as they were in .domus/upgrade/. With `dry_run`, it only says what would change. What
        changes, and where the files as they were are, go to `say`."""
        from .project import upgrade

        with self._writing():
            upgrade.upgrade(self.path, dry_run, say)

    def trust(self, revoke=False, say=print):
        """Lets this machine's builds run the site's extensions as they are now, as domus site trust does, or stops
        them, with `revoke`, saying which to `say`. Returns [(file, SHA-256)], with None for the SHA-256 when revoked;
        an empty list means the site declares none."""
        from .project import extensions

        done = extensions.trust(self.path, self._project.settings(), revoke)
        if not done:
            say("This site declares no extensions ([domus] extensions in data/site.toml).")
        for rel, sha in done:
            say(f"{'No longer trusted' if revoke else 'Trusted'}: {rel}" + (f" (SHA-256 {sha})" if sha else ""))
        if done and not revoke:
            say(f"Builds on this machine will run them until they change. Recorded in {extensions.trust_file()}.")
        return done

    def shift(self, spec, backup, apply=False, say=print):
        """Moves named features of the site's layers by one vector, as domus site shift does, saying so in their basis:
        `spec` is {"shift": [dE, dN], "dz", "basis", "files": {layer file: [ids] or "*"}}, or a JSON file holding it.
        Without `apply` it only says what would change; with it, each file is backed up into `backup`, a fresh folder,
        written, read back and checked."""
        from .project import edits

        with self._writing():
            edits.shift(self._project, _spec(spec), backup, apply, say)

    def edit(self, spec, backup, apply=False, say=print):
        """Changes, adds and deletes named features of the site's layers, as domus site edit does: `spec` is
        {layer file: [{"id", "so" or "en" (or "soz", "enz"), "props", "drop_props", "new", "delete"}]}, or a JSON file
        holding it. Without `apply` it only says what would change; with it, as shift does."""
        from .project import edits

        with self._writing():
            edits.edit(self._project, _spec(spec), backup, apply, say)

    def regrade(self, clouds, spec, backup, apply=False, say=print):
        """Gives ground-fix points and lines the levels a placed cloud shows, as domus site regrade does: `clouds` is
        a cloud in data/video (<tag>-dense or <tag>-site) or a list of them, and `spec` {"features", "radius",
        "percentile", "basis", ...}, or a JSON file holding it. Without `apply` it only says what would change; with
        it, as shift does."""
        from .project import edits

        with self._writing():
            edits.regrade(self._project, [clouds] if isinstance(clouds, str) else list(clouds), _spec(spec), backup, apply,
                          say)

    def _sketch(self, items):
        """For the server: what a sketch from the viewer comes to on the site, for the viewer. Not in the promise."""
        from .options import sketch

        return sketch.evaluate(self._project, items)

    def _sketch_option(self, items, name):
        """For the server: a sketch from the viewer as a wall option's file, its TOML. Not in the promise."""
        from .options import sketch

        return sketch.to_toml(sketch.to_option(self._project, items, name))

    @contextmanager
    def _writing(self):
        """Around something that writes the site's files: what Domus has read of them is forgotten after, so the next
        to ask reads them as they are."""
        try:
            yield
        finally:
            self._project.forget_settings()
            self._project.cache.clear()


def _spec(spec):
    """An edit's spec: a dict, or the path of a JSON file holding one."""
    if isinstance(spec, dict):
        return spec
    import json

    try:
        return json.loads(Path(spec).read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise DomusError(f"There's no {spec}") from None


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
