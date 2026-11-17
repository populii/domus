# From your documents and photos

`domus prep` turns plans, a designer's IFC model and photos into a site's data, and checks the result. Every step reads
what to do from `data/prep.json` in the site project. Each section below is optional, and sources belong under
`sources/raw/` in the project.

Install the extras with `uv sync --all-extras`, or use the image, which has them.

## Scanned plans

Pull page scans out of a PDF, then pin a sheet to the grid using the grid crosses printed on it:

```json
{"plans": {
  "pages": [{"pdf": "sources/raw/plans.pdf", "out": "sources/raster", "stem": "plans-p"}],
  "georef": [{"name": "C-101", "out": "c101", "source": "sources/raw/plans.pdf", "page": 1, "image": "sources/raster/plans-p1.jpeg",
              "dpi": 200, "crs": "EPSG:2193", "grid_n": [5180000, 5180025], "grid_e": [1570000, 1570025], "min_score": 0.85,
              "rough": {"n0": 5180000, "e0": 1570000, "col0": 150, "col_per_m": 40.0, "row0": 350, "row_per_m": 40.0}}]
}}
```

`rough` says about where a cross falls, for the search to start from: its pixel column is `col0 + (N - n0) * col_per_m`
and its row `row0 + (E - e0) * row_per_m`, as for a sheet with north to the right. Run `domus prep plan-pages`, then
`domus prep plan-georef`. To lay the sheet on the ground, add to `data/site.toml`:

```toml
[[drapes]]
key = "c101"
label = "Sheet C-101"
georef = "c101"
```

## Vector plans

If a PDF is true vector linework, `domus prep plan-trace` puts it on the grid as `LineString`s and text `Point`s, sorted
into layers by pen. Open the results in QGIS and copy what you need into your layers.

```json
{"plans": {"trace": [{
  "name": "site-plan", "pdf": "sources/raw/site-plan.pdf", "out": "data/sources/site-plan.geojson",
  "window": [50, 50, 800, 550], "text": "words",
  "colours": {"1.0,0.0,0.0": "new-drain"},
  "styles": {"0.0,1.42": "house-outline"},
  "fit": {"kind": "similarity", "paper": {"NW": [120.0, 80.0], "NE": [640.0, 95.0]},
          "boundary_corners": ["NW", "NE", "SE", "SW"]}
}]}}
```

- `window` is the part of the page in PDF points, leaving out title blocks.
- `styles` names layers by `grey,width` of the pen and `colours` by `r,g,b`.
- `fit` with `kind: similarity` matches named points on the sheet to the same corners of your boundary, in the boundary's
  order. `kind: rigid` keeps the printed scale (`paper_scale`), for a sheet whose size you trust, and takes its corners
  from `marks` (small circles in one pen) matched to a georeferenced scan. See `trace_one` in `src/domus/prep/plans.py`.

## An IFC model

An IFC from your designer shows as its own layers, fitted in plan to your `house-walls` outline. See the top of
`src/domus/prep/ifc.py` for the `"ifc"` section; layer names vary by designer, so list the file's first. Then add `[ifc]` to
`data/site.toml` as [site-format.md](site-format.md) describes.

## Textures from photos

Mark a quadrilateral on a photo, and it's rectified, evened out and made to tile. House walls can come from photos with
four marked points per face, or from placed video frames. The formats are at the top of `src/domus/prep/textures.py`.

```sh
domus prep feature-textures --check /tmp/check   # draws the quads on the sources, for checking
domus prep wall-textures
domus prep house-textures
```

## Checking the model

```sh
domus prep check --plots
domus prep sections walk-colmap-dense walk-colmap-site
domus prep canopy-plot
```

`check` looks for things floating above or buried in the ground, spikes and holes in the ground, a roof that doesn't fit
the LiDAR, placed clouds that are tilted or off the ground, and duplicate ids. For the wall options it looks for ground
that comes through a wall or a flight of steps, or stands over a tread's edge (`through`, in the built model), ground-fix
points inside an option's wall at or over its top (`fixes`), and block walls that aren't set out in whole units: cut
pieces at a stretch's ends or a step, and cut caps (`blocks`, with each wall's own block size). It exits 1 if a check
fails its tolerance.
It expects a site with a `house.geojson`. `sections` needs a `"sections"` entry naming a reference line, and `canopy-plot`
draws candidate tree crowns to help you write `trees.geojson`.
