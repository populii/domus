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
deprecation warning in an earlier release and a "For site projects" note in CHANGELOG.md. Three parts are provisional
until they're declared stable, and docs/api.md says why: the video and prep steps (`site.video`, `site.prep`), the
observations (`observations()`, `propose_observation()`) and the house kept as an IFC file (`house()`,
`write_house_ifc()`). The rest of the `domus` package is internal, and can change in any release.

**Several sites in one process.** Each Site reads its own folder, and what Domus reads of one is kept apart from
the others, so a tool can open two sites and compare them. A problem a person can put right, such as a missing
setting, comes as a DomusError whose message says what.
"""

import copy
import os
import re
from contextlib import contextmanager, redirect_stdout
from pathlib import Path

from .errors import DomusError

__all__ = ["DomusError", "Site", "clip", "create", "demo", "fit_affine", "linz_query", "new", "open_site", "resample_line"]

LAYER_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*")
_opened = {}
_GIVEN = object()          # edit()'s backup when it isn't given by name: the older edit(spec, backup, apply) by position


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
    Zealand and the UTM zone elsewhere. A boundary with no crs member whose numbers can't be longitude and latitude,
    or whose crs is null, is on `epsg`, and refused without it. Gives the site, opened; what to do next goes to `say`."""
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

    def house(self):
        """The house, as `[house] source` says to read it (provisional): its `source` ("drawn": house.geojson and
        [house]; "ifc": data/house.ifc), `features` (house.geojson's, as that source gives them: the walls, their
        openings and the house's parts), `settings` ([house], its shape as that source gives it) and `walls` (the
        house-walls feature, or None). Every part of Domus that reads the house reads it this way."""
        from .model import house_source

        return house_source.house(self._project)

    def house_walls(self):
        """The house's walls, a shapely Polygon in plan (the "house-walls" feature, as house() gives it), or None if the
        site has no house."""
        import shapely
        from shapely.geometry import shape

        walls = self.house().walls
        return shapely.force_2d(shape(walls["geometry"])) if walls is not None else None

    def ground(self):
        """The ground, as a function: ground(e, n) gives the heights at points on the site's grid, an array the shape of
        e and n. It's the ground as the build draws it, the one the wall options are designed on and the drains are
        drawn below: the LiDAR's bare ground (data/lidar/dem.tif, or the file `[rasters] dem` names) with
        ground-fixes.geojson applied and the existing walls' faces and tops cut in (walls-existing.geojson), over the
        model's extent. Beyond it, the LiDAR with the fixes; NaN where that has no ground."""
        import numpy as np

        from .model import terrain
        from .project import rasters

        rasters.need_ground(self._project)
        model = terrain.existing(self._project)

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
        fill, flat lawn, its checks, and what it takes to build (`materials`, docs/options.md). `option` is one of
        options(), or its id. Cut and fill are soil, measured as solids from the ground as the build draws it,
        ground(): a new wall's own body and paving don't count."""
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
        through, fixes, blocks and observations (all of them unless given), `tags` the placed reconstructions for plumb and clouds, and `plots` also writes
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

    def fetch_marks(self, height_mark=None, say=print, why=None, who=None):
        """Adds LINZ's survey marks around the lot, as domus marks does: the named boundary marks on its line, as the
        layer features-survey-marks, with the height mark `height_mark` (its name as `say` gets it) when one's named.
        The layer is written as an edit on the change route, recorded with `why` (a reason of its own unless given) and
        `who`, and undone with undo(). The nearest height marks go to `say`. Returns the layer's features. It needs
        DOMUS_LINZ_KEY."""
        from .importers.nz import marks

        with self._writing():
            return marks.fetch(self._project, height_mark, say, why, who)

    def fetch_title(self, say=print, why=None, who=None):
        """The lot's record of title and the easements on and near it, from LINZ, as domus title does: written to
        data/site/title.geojson as an edit on the change route, recorded with `why` (a reason of its own unless given)
        and `who`, and undone with undo(); and returned as its features. It needs DOMUS_LINZ_KEY."""
        from .importers.nz import title

        with self._writing():
            return title.update(self._project, say, why, who)

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

    def shift(self, spec, backup=None, apply=False, say=print, why=None, who=None):
        """Moves named features of the site's layers by one vector, as domus site shift does, saying so in their basis:
        `spec` is {"shift": [dE, dN], "dz", "basis", "files": {layer file: [ids] or "*"}}, or a JSON file holding it.
        Without `apply` it only says what would change; with it, it's an edit as edit() makes one, `why` its reason
        (the spec's basis unless given) and each file's copy as it was in `backup`, a fresh folder, if it's given."""
        from .project import edits

        with self._writing():
            return edits.shift(self._project, _spec(spec), backup, apply, say, why, who)

    def edit(self, intent, why=None, who=None, say=print, *, dry_run=False, backup=_GIVEN, apply=None):
        """Changes the site's data the one way every change goes (docs/changes.md), and gives the edit's record.
        `intent` says what to change, such as {"kind": "move", "layer": "walls-existing", "ids": ["W1"], "by": [0.5,
        0]}: an intent, a list of them, or a JSON file holding either. It's checked against the site, each file it
        changes is written the safe way, all of them or none, and it's recorded in .domus/edits/ with `why`, which it
        needs, `who` (this computer's user unless given), each file's SHA-256 before and after, and what puts it back,
        which undo() applies. `dry_run` only says what would change.

        The older form, edit(spec, backup=None, apply=False), with a spec as domus site edit has always taken it
        ({layer file: [{"id", "so" or "en" (or "soz", "enz"), "props", "drop_props", "new", "delete"}]}), works as it
        did: without `apply` it only says what would change, and with it the change is recorded the same way."""
        from .project import changes, edits
        from .project import intents as kinds

        loaded = kinds.load(intent)
        if kinds.is_intents(loaded):
            if apply is not None:
                dry_run = not apply
            with self._writing():
                return changes.apply(self._project, kinds.read(loaded), why, who, dry_run=dry_run, say=say,
                                     backup=None if backup is _GIVEN else backup)
        if not isinstance(loaded, dict):
            raise DomusError("An edit is an intent or a list of them (docs/changes.md), or a spec as domus site edit takes it")
        if backup is _GIVEN:            # edit(spec, backup, apply=False), by position, as it always was
            backup, why, apply, who = why, None, bool(who) if apply is None else apply, None
        with self._writing():
            return edits.edit(self._project, loaded, backup, bool(apply), say, why, who)

    def regrade(self, clouds, spec, backup=None, apply=False, say=print, why=None, who=None):
        """Gives ground-fix points and lines the levels a placed cloud shows, as domus site regrade does: `clouds` is
        a cloud in data/video (<tag>-dense or <tag>-site) or a list of them, and `spec` {"features", "radius",
        "percentile", "basis", ...}, or a JSON file holding it. Without `apply` it only says what would change; with
        it, as shift does."""
        from .project import edits

        with self._writing():
            return edits.regrade(self._project, [clouds] if isinstance(clouds, str) else list(clouds), _spec(spec), backup,
                                 apply, say, why, who)

    def write_house_ifc(self, force=False, say=print, why=None, who=None):
        """Writes the site's house, as house.geojson and [house] draw it today, to data/house.ifc, an IFC4 file, as
        domus house ifc does, and gives the file's path: a building with a storey at each floor level and an element
        for each wall face and part, each in its colours and with a Domus property set, and the same GlobalIds each
        time. It needs IfcOpenShell (the ifc extra). It's an edit on the change route, recorded with `why` and `who`
        and undone with undo(); a file that's there already is written again only with `force`. With [house] source =
        "ifc", the build draws the house from it (docs/site-format.md). Provisional (docs/api.md)."""
        from .model import house_ifc

        with self._writing():
            return house_ifc.write(self._project, force, say, why, who)

    def undo(self, edit, who=None, why=None, say=print):
        """Undoes an edit, by its number as edits() gives it: what puts it back is applied, but only while each file it
        changed is still as it left it, and the undo is recorded as an edit of its own, which can be undone in turn.
        Gives that record."""
        from .project import changes

        with self._writing():
            return changes.undo(self._project, edit, who, why, say)

    def edits(self):
        """The edits made to the site's data, oldest first: each record as .domus/edits/ holds it (`id`, `kind`, `who`,
        `when`, `why`, `intents`, `summary`, `files` with each one's SHA-256 before and after, `inverse`, `rebuild`),
        with `in_effect`, and `undone_by` once an undo has undone it."""
        from .project import changes

        return changes.records(self._project)

    def propose(self, intents, why, source, say=print):
        """Proposes a change for a person to accept or reject, as a check or a tool does when it finds something to
        put right: `intents` as edit() takes them, `why` why, and `source` what proposed it. It's checked against the
        site now; nothing changes until it's accepted. Gives the proposal (.domus/proposals/<id>.json)."""
        from .project import changes
        from .project import intents as kinds

        return changes.propose(self._project, kinds.read(intents), why, source, say)

    def observations(self):
        """The site's observations (data/observations.json, docs/observations.md), each as read now: what it measured
        from its source (`measured`, and `at`, where), what the model says of its target (`expected`), the
        `difference`, and its `state` (ok, near or off against its `uncertainty_m`; compared, when it gives none;
        reading, with no target; lost target; no reading; placement). Provisional (docs/api.md)."""
        from .checks import observations

        return observations.evaluate(self._project)

    def propose_observation(self, observation, say=print):
        """Proposes setting an observation's target to what it measured, as domus site propose does, for a person to
        accept or reject. Gives the proposal. Provisional (docs/api.md)."""
        from .checks import observations

        return observations.propose(self._project, observation, say)

    def proposals(self, state=None):
        """The proposed changes, oldest first: each with `id`, `made`, `source`, `why`, `intents`, `summary` and its
        `state`, open, accepted or rejected (only those in `state`, if it's given)."""
        from .project import changes

        return changes.proposals(self._project, state)

    def accept(self, proposal, who=None, say=print):
        """Accepts a proposed change, by its id: its intents applied as edit() applies them, with the proposal as their
        basis. Gives the edit's record."""
        from .project import changes

        with self._writing():
            return changes.accept(self._project, proposal, who, say)

    def reject(self, proposal, why, who=None, say=print):
        """Rejects a proposed change, by its id, recording `why` and `who`; the site's data is left as it is. Gives the
        proposal."""
        from .project import changes

        return changes.reject(self._project, proposal, why, who, say)

    def _sketch(self, items):
        """For the server: what a sketch from the viewer comes to on the site, for the viewer. Not in the promise."""
        from .options import sketch

        return sketch.evaluate(self._project, items)

    def _sketch_option(self, items, name):
        """For the server: a sketch from the viewer as a wall option's file, its TOML; not one with an outline that
        crosses itself (sketch.crossings), which the build would reshape. Not in the promise."""
        from .options import sketch

        crossed = sketch.crossings(items)
        if crossed:
            raise DomusError(" ".join(crossed))
        return sketch.to_toml(sketch.to_option(self._project, items, name))

    def _save_sketch(self, items, name, who=None):
        """For the server: a sketch from the viewer saved as a wall option, options/sketch-<name>.toml (never over an
        earlier one), as an edit on the change route recorded with `who`, so undo() takes it away again. Gives its
        `file`, in the site's folder, and its `edit`'s number. Not in the promise."""
        from .options import sketch
        from .project import changes, intents

        toml = self._sketch_option(items, name)
        option = sketch.option_id(self._project, name)
        with self._writing():
            record = changes.apply(self._project, intents.read({"kind": "option", "id": option, "toml": toml}),
                                   f"Made an option from the sketch {name!r} in the viewer.", who, say=lambda line: None)
        return {"file": f"options/{option}.toml", "edit": record["id"]}

    def _writing(self):
        return _writes(self._project)

    @property
    def video(self):
        """The site's video steps, as `domus video <step>` runs them (Video, provisional)."""
        return Video(self._project)

    @property
    def prep(self):
        """The site's prep steps, as `domus prep <step>` runs them (Prep, provisional)."""
        return Prep(self._project)


class Video:
    """A site's video steps, as `domus video <step>` runs them (docs/video.md): from its videos to point clouds on its
    grid. `site.video` gives them.

    **Provisional**, as PEP 411 means it: public and documented, but a step may change from one release to the next
    without a deprecation until the steps are declared stable. Each step prints what it does as it goes, and `say`
    gets each line (print unless given). Paths are taken as the command takes them, from the current folder (but
    `auto`'s video, which is in the site's folder unless it's a full path). `sheet`, `crop` and `picks_edit` work on
    the files they're given, whichever site they're asked through."""

    def __init__(self, project):
        self._project = project

    def __repr__(self):
        return f"Video({str(self._project.path)!r})"

    def auto(self, video, say=print):
        """From a scan to the model, with no hand work, as domus video auto does: the video's frames, a reconstruction,
        and with the phone's AR positions beside it (<clip>-poses.json), the cloud on the site's grid and a build."""
        from .capture import auto

        with _saying(say), _writes(self._project):
            auto.run(self._project, video)

    def frames(self, video, fps=6.0, say=print):
        """The sharpest frame of each 1/`fps` seconds of `video`, in sources/raw/frames6/<clip>/, as domus video frames
        writes them. Returns that folder."""
        from .capture import extract_frames

        with _saying(say), _writes(self._project):
            return extract_frames.extract(self._project, video, fps)

    def reconstruct(self, clip, step=1, extra=(), extra_step=3, focal=1100.0, tag="", threads=8, overlap=12, retrieve=5,
                    max_features=8192, first_octave=0, mapper="global", min_matches=30, seed=0, fresh=False, say=print):
        """Reconstructs a clip's frames with COLMAP, as domus video reconstruct does: every `step`-th, and every
        `extra_step`-th of the `extra` clips (a list), into data/video/<tag>-sfm.npz (<clip>-colmap unless given). The
        other arguments are the command's options of the same names."""
        from .capture import colmap_sfm

        with _saying(say), _writes(self._project):
            colmap_sfm.reconstruct(self._project, clip, step, extra, extra_step, focal, tag, threads, overlap, retrieve,
                                   max_features, first_octave, mapper, min_matches, seed, fresh)

    def reconstruct_chain(self, clip, step=1, window=6, focal=1100.0, k1=0.0, fix_intrinsics=False, extra=(), extra_step=3,
                          range_="", tag="", loops=True, loop_candidates=12, workers=None, say=print):
        """Reconstructs a clip with the built-in chain of views, as domus video reconstruct-chain does. `range_` is
        its --range, "first:last" frame of the clip; `loops` False is --no-loops; `workers` is all but four of the
        machine's processors unless given. The other arguments are the command's options of the same names."""
        from .capture import video_sfm

        with _saying(say), _writes(self._project):
            video_sfm.reconstruct(self._project, clip, step, window, focal, k1, fix_intrinsics, extra, extra_step, range_, tag,
                                  loops, loop_candidates, workers)

    def two_lens(self, clip, work_tag, t_main, t_wide, f_main, f_wide, stage="all", mapper="global", threads=8,
                 min_matches=30, every=None, keep=None, ranges=None, name="", model=None, as_=None, say=print):
        """Maps a clip filmed on two lenses again, with a camera for each, from the workspace `domus video reconstruct`
        left in data/video/cache/colmap/<work_tag>, as domus video two-lens does. `as_` is its --as; the other
        arguments are the command's of the same names."""
        from .capture import two_lens

        with _saying(say), _writes(self._project):
            two_lens.map_lenses(self._project, clip, work_tag, t_main, t_wide, f_main, f_wide, stage, mapper, threads,
                                min_matches, every, keep, ranges, name, model, as_)

    def merge(self, clip, tag_a, tag_b, out=None, say=print):
        """Joins reconstructions `tag_a` and `tag_b` of `clip`, which share frames, in `tag_a`'s frame, as domus video
        merge does, into data/video/<out>-sfm.npz (<clip>-joined unless given)."""
        from .capture import merge_sfm

        with _saying(say), _writes(self._project):
            merge_sfm.merge(self._project, clip, tag_a, tag_b, out)

    def split(self, tag, new, t0, t1, say=print):
        """Cuts the frames from `t0` to `t1` seconds out of reconstruction `tag` as one of their own, `new`, as domus
        video split does. Returns its summary."""
        from .capture import split_sfm

        with _saying(say), _writes(self._project):
            return split_sfm.split(self._project, tag, new, t0, t1)

    def link(self, tag_a, tag_b, say=print):
        """What carries reconstruction `tag_b` into `tag_a`'s frame, from matching their frames, as domus video link
        does: data/video/<tag_b>-to-<tag_a>.json. Returns it, or None when too few points match."""
        from .capture import link_sfm

        with _saying(say), _writes(self._project):
            return link_sfm.link(self._project, tag_a, tag_b)

    def link_picks(self, tag_a, picks_a, tag_b, picks_b, say=print):
        """The same as link, from the points with the same id in the picks files `picks_a` and `picks_b`, as domus
        video link-picks does. Returns it, or None with fewer than three shared points."""
        from .capture import link_picks

        with _saying(say), _writes(self._project):
            return link_picks.link(self._project, tag_a, picks_a, tag_b, picks_b)

    def place(self, tag, via=None, say=print):
        """Places reconstruction `tag` on the site's grid, as domus video place does: by its control file, or `via` a
        placed reconstruction it's linked to. Writes data/video/<tag>-georef.json and <tag>-site.npz, and returns what
        the -georef.json holds."""
        from .capture import georef_sfm

        with _saying(say), _writes(self._project):
            return georef_sfm.place(self._project, tag, via)

    def pick(self, picks, tags=(), say=print):
        """Puts the points marked in the picks file `picks` on the site's grid, as domus video pick does, each with
        the first of the placed reconstructions `tags` that registered two of its frames. Returns the CSV it wrote."""
        from .capture import triangulate_picks

        with _saying(say), _writes(self._project):
            return triangulate_picks.pick(self._project, picks, tags)

    def dense(self, tag, every=2, scale=0.5, say=print):
        """A dense point cloud from placed reconstruction `tag`, as domus video dense does: stereo on pairs of every
        `every`-th frame, scaled by `scale`. Returns the file it wrote, data/video/<tag>-dense.npz."""
        from .capture import dense_sfm

        with _saying(say), _writes(self._project):
            return dense_sfm.dense(self._project, tag, every, scale)

    def export(self, say=print):
        """Combines the clouds `[video] clouds` lists, and the LiDAR scans, into data/video/points.npz for the build,
        as domus video export does."""
        from .capture import export_cloud

        with _saying(say), _writes(self._project):
            export_cloud.export(self._project)

    def plot(self, tags, out="", say=print):
        """Placed reconstructions `tags` in plan over the aerial, for checking, as domus video plot does. Returns the
        picture, data/video/checks/<out>-plan.jpg (the first tag unless `out` is given)."""
        from .capture import plot_video_cloud

        with _saying(say):
            return plot_video_cloud.plot(self._project, list(tags), out)

    def sheet(self, frames, prefix, t0, t1, step, cols=6, width=480, rows=3, registered=None, say=print):
        """Contact sheets of the frames in the folder `frames` from `t0` to `t1` seconds, at least `step` seconds
        apart, as domus video sheet makes them: <prefix>-1.jpg and on. Returns [(file, width, height, first frame,
        last frame)]."""
        from .capture import sheet

        with _saying(say):
            return sheet.make(frames, prefix, t0, t1, step, cols, width, rows, registered)

    def crop(self, photo, out, side, box, zoom=1.0, rotate=0.0, say=print):
        """A piece of `photo` at full size, as domus video crop cuts it: `box` (X0, Y0, X1, Y1) read on a copy with a
        long side of `side` pixels, turned `rotate` degrees anticlockwise and scaled by `zoom`, into `out`. Returns its
        size and the box it took from the full-size photo."""
        from .capture import crop

        with _saying(say):
            return crop.piece(photo, out, side, box, zoom, rotate)

    def picks_edit(self, picks, edits, say=print):
        """Adds, moves or drops marks in the picks file `picks`, as `edits` (a dict, or a JSON file holding one) says,
        as domus video picks-edit does, keeping the old file beside it. Returns that backup."""
        from .capture import picks_edit

        with _saying(say):
            return picks_edit.edit(picks, edits)


class Prep:
    """A site's prep steps, as `domus prep <step>` runs them (docs/from-documents.md): from its documents and photos to
    its data. `site.prep` gives them; `domus prep check` is `site.check()`.

    **Provisional**, as PEP 411 means it: public and documented, but a step may change from one release to the next
    without a deprecation until the steps are declared stable. Most steps read what to do from data/prep.json. Each
    prints what it does as it goes, and `say` gets each line (print unless given). Paths are taken as the command takes
    them, from the current folder (but `canopy_plot`'s picture, which is in the site's folder)."""

    def __init__(self, project):
        self._project = project

    def __repr__(self):
        return f"Prep({str(self._project.path)!r})"

    def _run(self, say, step, *args, **kwargs):
        with _saying(say), _writes(self._project):
            return step(self._project, *args, **kwargs)

    def plan_pages(self, say=print):
        """Pulls the page scans out of a PDF, as domus prep plan-pages does and "plans" in data/prep.json says."""
        from .prep import plans

        return self._run(say, plans.pages)

    def plan_georef(self, say=print):
        """Pins scanned sheets to the grid by their grid crosses, as domus prep plan-georef does."""
        from .prep import plans

        return self._run(say, plans.georef)

    def plan_trace(self, say=print):
        """Traces a vector PDF's linework onto the grid, as domus prep plan-trace does."""
        from .prep import plans

        return self._run(say, plans.trace)

    def ifc(self, say=print):
        """A designer's IFC model of the house on the grid, as domus prep ifc does and "ifc" in data/prep.json says. It
        needs IfcOpenShell (the `ifc` extra)."""
        from .prep import ifc

        return self._run(say, ifc.place)

    def feature_textures(self, check=None, say=print):
        """Cuts fence, shed and paving textures from photos, as domus prep feature-textures does; `check` is a folder
        for the sources with their quads drawn on."""
        from .prep import textures

        return self._run(say, textures.feature_textures, check)

    def wall_textures(self, check=None, say=print):
        """Cuts wall textures from photos, as domus prep wall-textures does; `check` is a folder for the frames with
        their quads drawn on."""
        from .prep import textures

        return self._run(say, textures.wall_textures, check)

    def house_textures(self, groups=(), say=print):
        """Rectifies the house's walls from photos and placed video, as domus prep house-textures does: the sources in
        `groups`, else all of them."""
        from .prep import textures

        return self._run(say, textures.house_textures, groups)

    def boards(self, faces=(), along=None, band=0.5, heights=None, spacing=(0.08, 0.25), step=0.0002, above=None,
               say=print):
        """The weatherboards' spacing and lap lines' heights on rectified wall photos, as domus prep boards finds them:
        on `faces` (all of them unless named), with the command's options of the same names."""
        from .prep import boards

        return self._run(say, boards.measure_faces, faces, along, band, heights, spacing, step, above)

    def sections(self, clouds, step=1.0, from_=-6.0, to=16.0, say=print):
        """Cross-sections through the placed clouds `clouds`, as domus prep sections draws them; `from_` is its
        --from."""
        from .prep import sections

        return self._run(say, sections.draw, clouds, step, from_, to)

    def profile(self, points, offsets=(0.0,), step=0.5, grid=False, csv=None, say=print):
        """The ground along a line and either side of it, as domus prep profile gives it: `points` are (s, o) pairs (or
        east and north on the grid, with `grid`), or the text "s,o s,o ...". Returns the table's rows; `csv` also
        writes them to that file."""
        from .prep import profile

        return self._run(say, profile.table, points, offsets, step, grid, csv)

    def cloud(self, clouds, box, cell=(1.0,), pct=20.0, min_=12, by="points", against="model", all_=False, grid=False,
              csv=None, say=print):
        """A placed cloud's ground against the model's or the LiDAR's, cell by cell, as domus prep cloud gives it:
        `clouds` a list or comma-separated, `box` two corners or "s,o s,o"; `min_` and `all_` are its --min and --all.
        Returns the rows; `csv` also writes them to that file."""
        from .prep import cloud

        return self._run(say, cloud.by_cell, clouds, box, cell, pct, min_, by, against, all_, grid, csv)

    def cloud_plan(self, clouds, box, out, px=0.02, mode="top", above=None, lines=False, all_=False, grid=False,
                   say=print):
        """A placed cloud from above, as domus prep cloud-plan draws it, into the picture `out`. Returns its size and
        the points drawn."""
        from .prep import cloud

        return self._run(say, cloud.from_above, clouds, box, out, px, mode, above, lines, all_, grid)

    def face_fit(self, cloud, spec, all_=False, say=print):
        """Fits vertical faces in a placed cloud, as domus prep face-fit does: `spec` is a dict of name: {box, along,
        model}, or a JSON file holding one."""
        from .prep import fitting

        return self._run(say, fitting.fit_faces, cloud, spec, all_)

    def frame_points(self, tag, spec, georef=None, say=print):
        """Where a placed frame's pixels meet a wall's plane or a level, as domus prep frame-points finds it: `spec` is
        a list of {frame, plane, points}, or a JSON file holding one."""
        from .prep import fitting

        return self._run(say, fitting.frame_points, tag, spec, georef)

    def overlay(self, tag, out, frames, frames_dir=None, georef=None, only=None, option=None, see_through=False, suffix="",
                say=print):
        """The model's parts drawn over frames of a placed clip, as domus prep overlay draws them, into the folder
        `out`; `only` is a list or comma-separated."""
        from .prep import overlay

        return self._run(say, overlay.draw_frames, tag, out, frames, frames_dir, georef, only, option, see_through, suffix)

    def canopy_plot(self, out="data/sources/canopy.jpg", say=print):
        """A plan of the candidate tree crowns over the aerial, as domus prep canopy-plot draws it, into `out` in the
        site's folder."""
        from .prep import canopy_plot

        return self._run(say, canopy_plot.draw, out)


class _Lines:
    """What's printed, a line at a time, to `say`."""

    def __init__(self, say):
        self.say, self.part = say, ""

    def write(self, text):
        self.part += text
        *lines, self.part = self.part.split("\n")
        for line in lines:
            self.say(line)
        return len(text)

    def flush(self):
        pass


@contextmanager
def _saying(say):
    """While a step runs, what it prints goes to `say`, a line at a time; print leaves it as it is."""
    if say is print:
        yield
        return
    lines = _Lines(say)
    try:
        with redirect_stdout(lines):
            yield
    finally:
        if lines.part:
            say(lines.part)


@contextmanager
def _writes(project):
    """Around something that writes a site's files: what Domus has read of them is forgotten after, so the next to
    ask reads them as they are."""
    try:
        yield
    finally:
        project.forget_settings()
        project.cache.clear()


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
