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
| `site.ground()` | The ground model as a function: `ground(e, n)` gives heights, the shape of `e` and `n`, NaN where there's no ground. It's the LiDAR's bare ground with `ground-fixes.geojson` applied |
| `site.options()` | The wall options in `options/`, each a dict with `id`, `name`, `short`, `description`, `walls`, `terraces`, `stairs` and `path` |
| `site.frame()` | The frame the options are drawn in: `frame.en(so)` turns (s, o) points into the site's grid and `frame.so(en)` back |
| `site.evaluate(option)` | What an option comes to, as the viewer's comparison shows it: walls, cut and fill, flat lawn and checks. `option` is one of `options()`, or its id |
| `site.build()` | Builds the model, as `domus build` does, and gives the folder it's in |
| `site.output` | The folder the model is built into |
| `site.export(path, format=None, option=None)` | Writes the built model for other software, as `domus export` does, and gives the file's path: glTF (`"glb"`, a binary `.glb`) or IFC4 (`"ifc"`, which needs IfcOpenShell, the `ifc` extra), by default the one the path's suffix says. `option`, a wall option's id, shows that option in place of what it replaces, as the viewer does; without it, the site as it is. It reads the model as last built ([export.md](export.md)) |
| `site.check(checks=None, tags=None, plots=False)` | Runs the adversarial checks, as `domus prep check` does (sanity, roof, plumb, clouds, and for the wall options through, fixes and blocks), and gives their report: `failures` by check, `findings`, and `skipped`, why a check the site has nothing for was skipped. It writes it to `.domus/checks/check_model.json` |

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
