# The public API

Site tools, a site's own tests and agents read a site through `domus.api`, and test it with `domus.testing`. The
examples in [`site/`](../site/README.md) use both.

```python
from domus import api

site = api.open_site("site")
print(site.title, round(site.lot().area), "m2")
for option in site.options():
    print(option["name"], site.evaluate(option)["volumes"]["cut_m3"])
site.export("site.glb")         # the built model for other software, glTF or IFC; option= an option's id shows it
```

## The promise

What `domus.api` and `domus.testing` offer keeps working from one release to the next. Changing any of it takes a
deprecation warning in an earlier release, and a "For site projects" note in [CHANGELOG.md](../CHANGELOG.md). The rest
of the `domus` package is internal: it can change in any release, so a site tool that reaches into it may need
changing with it.

## Opening a site

`api.open_site(path=None)` gives the site project in a folder: the one given, else the one `DOMUS_SITE` names, else
the current folder. A folder without `data/site.toml` raises a `DomusError`.

Three functions start one, as the commands of the same names do, and give it opened:

| | What it does |
|---|---|
| `api.create(address, path=None)` | A site project for an address, from public data: its lot, buildings, ground and photo fetched, its house and trees drafted, and its model built, in `path` (a folder named from the address unless given). A folder already holding this address carries on with its build. An address that matches more than one place raises a `DomusError` whose message is `WHICH: ` and then JSON with the `places` to choose from |
| `api.new(path, name, boundary=None, centre=None, size=30.0, epsg=None)` | A site project called `name` on the lot in `boundary`, a GeoJSON polygon's file, or while there's no boundary yet, on a stand-in square `size` metres across at `centre`, (latitude, longitude). `epsg` names its grid, in metres: by default the boundary's own, else NZTM2000 in New Zealand and the UTM zone elsewhere |
| `api.demo(path)` | The made-up demo site |

A process can open several sites. Each `Site` reads its own folder, and what Domus reads of one (its settings, its
rasters, its ground) is kept apart from the others, so a tool can open two sites and compare them. Opening the same
folder twice gives the same `Site`. A site opened by a path other than `DOMUS_SITE`'s is built into its own
`viewer/data`, as `domus --site` does.

## A site

Everything is on the site's own grid (`[crs] epsg` in `data/site.toml`), with heights on its vertical datum.

| | What it gives |
|---|---|
| `site.path` | The site project's folder |
| `site.title` | `[site] title`, else the folder's name |
| `site.settings()` | `data/site.toml` as a dict, a copy |
| `site.grid` | The site's grid and heights: `epsg`, `label`, `vertical`, `origin` (`[model] origin`) and `datums`, its other height datums (each with `key`, `label` and `offset_m`). `grid.to_nztm(e, n)`, `grid.from_nztm(x, y)` and `grid.to_lonlat(e, n)` turn points between the site's grid, NZTM2000 and longitude and latitude; `grid.to_model_height(level, datum)` and `grid.from_model_height(h, datum)` turn heights between the model's datum and another |
| `site.lot()` | The lot as a shapely Polygon in plan: `boundary.geojson`'s feature that `[boundary] main` names, else its first |
| `site.house_walls()` | The house's walls (`house-walls` in `house.geojson`) as a shapely Polygon in plan, or None |
| `site.layer(name)` | The features of `data/site/<name>.geojson`, such as `"trees"`: a list of GeoJSON features, empty if there's no such layer |
| `site.ground()` | The ground as a function: `ground(e, n)` gives heights, the shape of `e` and `n`, NaN where there's no ground. It's the ground as the build draws it, the one the wall options are designed on and the drains are drawn below: the LiDAR's bare ground with `ground-fixes.geojson` applied and the existing walls' faces and tops cut in, over the model's extent, and the LiDAR with the fixes beyond it |
| `site.options()` | The wall options in `options/`, each a dict with `id`, `name`, `short`, `description`, `walls`, `terraces`, `stairs` and `path` |
| `site.frame()` | The frame the options are drawn in: `frame.en(so)` turns (s, o) points into the site's grid and `frame.so(en)` back |
| `site.evaluate(option)` | What an option comes to, as the viewer's comparison shows it: walls, cut and fill, flat lawn and checks, and what it takes to build (`materials`, [options.md](options.md#what-an-option-takes-its-materials-list)). `option` is one of `options()`, or its id. Cut and fill are soil, measured from the ground as the build draws it, `ground()`. A new wall's own body and paving don't count ([options.md](options.md#what-the-build-works-out)) |
| `site.build(fresh=False)` | Builds the model, as `domus build` does, and gives the folder it's in. `fresh` draws everything again, taking nothing from the last build |
| `site.output` | The folder the model is built into |
| `site.export(path, format=None, option=None)` | Writes the built model for other software, as `domus export` does, and gives the file's path: glTF (`"glb"`, a binary `.glb`) or IFC4 (`"ifc"`, which needs IfcOpenShell, the `ifc` extra), by default the one the path's suffix says. `option`, a wall option's id, shows that option in place of what it replaces, as the viewer does; without it, the site as it is. It reads the model as last built ([export.md](export.md)) |
| `site.check(checks=None, tags=None, plots=False)` | Runs the adversarial checks, as `domus prep check` does (sanity, roof, plumb, clouds, and for the wall options through, fixes and blocks), and gives their report: `failures` by check, `findings`, and `skipped`, why a check the site has nothing for was skipped. It writes it to `.domus/checks/check_model.json` |
| `site.shots(spec, timeout=None)` | Takes pictures of the built model as the viewer draws it, as `domus shots` does, from `spec`, a JSON file ([shots.md](shots.md)), within `timeout` seconds (ten minutes unless given), and gives each picture's path and what the viewer showed. It needs the `shots` extra |

## Changing a site

Each of these does what the command does, and writes the site's files; the site reads them afresh afterwards.

| | What it does |
|---|---|
| `site.fetch()` | Gets the site's rasters from the public source for where it is, as `domus fetch` does: LINZ's ground and surface models and aerial photo in New Zealand, the Copernicus ground model elsewhere |
| `site.map_canopy()` | Maps candidate tree crowns from the LiDAR, as `domus canopy` does, for curating into `trees.geojson`, and gives the file it wrote |
| `site.fetch_marks(height_mark=None)` | LINZ's survey marks around the lot, as `domus marks` does: the named boundary marks on its line as the `features-survey-marks` layer, with the height mark `height_mark` when one's named. Gives the layer's features. It needs `DOMUS_LINZ_KEY` |
| `site.fetch_title()` | The lot's title and the easements on and near it, from LINZ, as `domus title` does, into `data/site/title.geojson`, and gives its features. It needs `DOMUS_LINZ_KEY` |
| `site.fetch_points(ground=False)` | The classified LiDAR point cloud over the site, as `domus points` does, into `data/lidar/points.laz`, and with `ground` a 0.5 m ground model from its ground points too. Gives `points`, `path` and `ground_share`. A site no point cloud covers raises a `DomusError` |
| `site.scan(file, tag, control=None, epsg=None, up=None)` | Brings in a phone or tablet LiDAR scan, as `domus scan` does, placed on the grid by `control`, a CSV of points in both (with `up`, `"y"` or `"z"`, the scan's vertical axis taken as true), or by `epsg` for a file already on a grid. Gives the file it wrote, `data/video/<tag>-scan.npz` ([phone-lidar.md](phone-lidar.md)) |
| `site.upgrade(dry_run=False)` | Writes the site's migrations into its own files, as `domus site upgrade` does, keeping the files as they were in `.domus/upgrade/`; with `dry_run`, only says what would change |
| `site.trust(revoke=False)` | Lets this machine's builds run the site's extensions as they are now, as `domus site trust` does, or stops them, with `revoke`. Gives each file and its SHA-256 |
| `site.shift(spec, backup, apply=False)` | Moves named features of the site's layers by one vector, as `domus site shift` does. `spec` is a dict or a JSON file; without `apply` it only says what would change, and with it each file is backed up into `backup`, a fresh folder, written, read back and checked |
| `site.edit(spec, backup, apply=False)` | Changes, adds and deletes named features of the site's layers, as `domus site edit` does, the same way |
| `site.regrade(clouds, spec, backup, apply=False)` | Gives ground-fix points and lines the levels a placed cloud (or a list of them) shows, as `domus site regrade` does, the same way |

[AGENTS.md](../AGENTS.md) (section 8.6) gives the specs of `shift`, `edit` and `regrade`.

## What they say as they go

Each function that does what a command does takes `say`, which gets each line the command prints as it goes:
`print` unless given. Give your own to keep the lines, or `say=lambda line: None` for none. `api.create` runs its
fetch, draft and build in a process of their own, which prints its own, and a few notices from deeper in Domus, such
as a house part a build can't draw, are printed as they were.

## Provisional: the video and prep steps

`site.video` runs the steps of `domus video` ([video.md](video.md)): `site.video.place("walk-colmap")` places a
reconstruction as `domus video place walk-colmap` does. `site.prep` runs those of `domus prep`
([from-documents.md](from-documents.md)) the same way: `site.prep.profile("0,2 4,2", offsets=[0, 1])`. They're
**provisional**, as [PEP 411](https://peps.python.org/pep-0411/) means it: public and documented, but a step may change
from one release to the next without a deprecation until they're declared stable. The promise above doesn't cover them
yet.

Each step is a method named after it: `auto`, `frames`, `reconstruct`, `reconstruct_chain`, `two_lens`, `merge`,
`split`, `link`, `link_picks`, `place`, `pick`, `dense`, `export`, `plot`, `sheet`, `crop` and `picks_edit`. Its
arguments are the command's, named after its options, such as `site.video.reconstruct("walk", extra=["walk-2"],
focal=650)`: a list stands for a comma-separated option, `range_` and `as_` for `--range` and `--as`, and
`loops=False` for `--no-loops`. Each takes `say`, which gets the lines the step prints. Paths are as the command takes
them, from the current folder (but `auto`'s video, which is in the site's folder unless it's a full path), and `sheet`,
`crop` and `picks_edit` work on the files they're given.

The prep steps are `plan_pages`, `plan_georef`, `plan_trace`, `ifc`, `feature_textures`, `wall_textures`,
`house_textures`, `boards`, `sections`, `profile`, `cloud`, `cloud_plan`, `face_fit`, `frame_points`, `overlay` and
`canopy_plot`, their arguments named the same way (`from_`, `min_` and `all_` for `--from`, `--min` and `--all`); a
box or a line's points can be pairs or the command's text. `domus prep check` is `site.check()`, above. Most read
what to do from `data/prep.json`, and `profile`, `cloud` and `cloud_plan` give what they found.

## Helpers

| | What it does |
|---|---|
| `api.resample_line(line, spacing)` | Points about every `spacing` metres along a polyline and at its corners, and their distances along it |
| `api.fit_affine(pix, world)` | The least-squares affine from image points (column, row) to ground points (E, N), and its residuals |
| `api.linz_query(layer, cql=None, bbox=None, count=20)` | Features of a LINZ Data Service layer on NZTM, by its number (`50820`) or name (`"layer-50820"`), within a box on NZTM, a CQL filter or both. It uses `DOMUS_LINZ_KEY` |
| `api.clip(name, urls, bounds, out_path, bands=None)` | The window covering `bounds` from cloud-optimised GeoTIFFs, such as LINZ's survey tiles, as one file |

## A site's own layers, drawn in its builds

A site's tool can write layers as data, in `data/layers/*.json` ([model-format.md](model-format.md)). To have them
drawn at build time instead, declare the tool as an extension in `data/site.toml`:

```toml
[domus]
extensions = ["tools/pegs.py"]
```

It defines `layers(site)`, which gets the `Site` above and returns layers as `data/layers/*.json` holds them:

```python
def layers(site):
    e0, n0 = site.settings()["model"]["origin"][:2]
    return [{"id": "site-peg", "type": "labels", "name": "Pegs", "items": [{"text": "Peg", "at": [e0, n0, 45.0]}]}]
```

Running a site's code is trusting it, so a build runs it only once it's trusted on that machine, as it is:
`domus site trust` shows each file's SHA-256 and records it, and a file changed since is untrusted again. A build
whose extensions aren't all trusted stops and says so. The container image trusts none (`DOMUS_NO_EXTENSIONS=1`),
and the server never imports a site's code; `build.json` records what ran.

## A site's own tests

In the site's `tests/conftest.py`:

```python
from domus.testing import built, site, use_site  # noqa: F401 - pytest finds the fixtures here

use_site(__file__)
```

`use_site` names the site: `DOMUS_SITE` if it's set, else the folder above `tests/`. A test then asks for `site`, the
site as `open_site` gives it, or `built`, the folder of its model. `built` builds the site once for the whole run,
into a temporary folder, so the model a server shows is left alone. Tests are skipped while the folder has no site
project.

## Errors

A problem a person can put right, such as a missing setting or a file in a format Domus can't read, raises a
`DomusError` (`api.DomusError`) whose message says what. Domus's own functions raise it rather than ending the
program, so a site tool can catch it. The command line shows just the message.
