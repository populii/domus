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

`boards` reads the weatherboards off the walls' rectified photos (`data/house/faces.json`): how far apart their lap
lines are up the wall, which is `[house] board_m`, and the lap lines' heights. `domus prep boards north` reads the north
face in bands 0.5 m wide along it (`--band WIDTH`, and `--along U0 U1` for part of the face), over the photo's whole
height (`--heights Z0 Z1` for less), trying spacings from 80 to 250 mm (`--spacing MIN MAX`, every 0.2 mm: `--step`).
It prints each band's spacing, how clear its lap lines are (their contrast) and their heights, then the face's spacing,
the median of its bands'. `--above HEIGHT` adds the first lap line at or above a height, such as the top of the
plastered base, where the model counts the boards from. A band across a window or a door reads less clearly, or
wrongly, so go by the median. A spacing found at an end of the range tried is marked: the boards' may lie beyond it.

## Checking the model

```sh
domus prep check --plots
domus prep sections walk-colmap-dense walk-colmap-site
domus prep canopy-plot
```

`profile` reads the ground along a line and either side of it, a table of levels: `domus prep profile "8,0.4 8,3"
--offsets -0.5 0 0.5` for a line in the wall options' frame, `--grid` for one on the site's grid.

`cloud` reads a placed video's cloud against the ground, cell by cell over a box: `domus prep cloud walk-dense
"0,-3 20,6" --cell 1 0.5` prints each cell's cloud ground less the model's, from each point's height over the ground
(`--by level`: from the cell's own level), and `--against lidar` reads it against the LiDAR's ground model alone, to
tell a ground fix from the LiDAR. `cloud-plan` draws a cloud from above in its own colours over the same kind of box,
to lay beside the aerial photo: `domus prep cloud-plan walk-dense "0,-3 20,6" plan.png`, with `--lines` for the
boundary, the walls and the house.

Two steps help fit the site's parts to a placed video by hand. `face-fit` fits vertical faces in a placed cloud,
each named in a JSON file with its box in the wall options' frame, the axis it runs along and, to compare, the model's
line for it: `domus prep face-fit walk-dense faces.json` prints where each face is at both ends of its box, and how far
the model's line is from it. `frame-points` reads where pixels of a placed frame fall on a wall's plane or on a level,
for picking a line off a photo: `domus prep frame-points walk levels.json` prints each pixel's place in the frame and its
height. `domus prep face-fit --help` and `frame-points --help` describe their files.

`overlay` draws the model's parts over frames of a placed clip, through the clip's own cameras, to check each one's
place against what was filmed: `domus prep overlay walk-colmap checks/overlay t012.500 t045.250` draws the house's
walls, corners and windows, the existing walls' tops and feet, the features, the ground fixes, the boundary and its
marks and the plants, each only where the camera sees it past the house (`--only` picks some, `--option NAME` adds a
wall option's walls).

`check` looks for things floating above or buried in the ground, spikes and holes in the ground, a roof that doesn't fit
the LiDAR, placed clouds that are tilted or off the ground, and duplicate ids. For the wall options it looks for ground
that comes through a wall or a flight of steps, or stands over a tread's edge (`through`, in the built model), ground-fix
points inside an option's wall at or over its top (`fixes`), and block walls that aren't set out in whole units: cut
pieces at a stretch's ends or a step, and cut caps (`blocks`, with each wall's own block size). It exits 1 if a check
fails its tolerance, and writes its report to `.domus/checks/check_model.json`. A check the site has nothing for is
skipped with a note saying why: the roof without a house or a surface model, plumb and clouds without placed videos, and
the option checks without wall options. `sections` needs a `"sections"` entry naming a reference line, and `canopy-plot`
draws candidate tree crowns to help you write `trees.geojson`.
