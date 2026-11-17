# Site project format

```
data/site.toml            settings (below)
data/site/*.geojson       curated layers; only boundary.geojson is required
data/lidar/dem.tif        ground model on NZTM2000 (required)
data/lidar/dsm.tif        surface model (optional)
data/lidar/points.laz     classified point cloud from `domus points` (optional; drafted roofs are fitted to it)
data/lidar/dem-points.tif 0.5 m ground from its ground points (`domus points --ground`; used only if [rasters] dem names it)
data/aerial/aerial.tif    aerial photo (optional)
data/aerial/nir.tif       near-infrared photo, red and near-infrared bands (optional)
data/survey/control.csv   survey marks (optional)
data/georef/<sheet>.json  georeferenced scanned plans, for [[drapes]] (optional)
data/features/, data/walls/, data/house/   textures (optional; plain colours without them)
options/_site.toml        what wall options share
options/*.toml            one per option
viewer/data/              the built model (set DOMUS_OUTPUT to move it)
.domus/viewer.db      saved sketches (set DOMUS_DB to move it)
```

## data/site.toml

Required: `[model] extent` (E min, N min, E max, N max), `terrain_step_m`, `origin` (E, N, and optionally a height).
Everything else has a default.

| Section | Keys |
|---|---|
| `[site]` | `title`, `heading`, `subtitle`, `about`, `attribution`: the viewer's text |
| `[crs]` | `epsg` (default 2193), `label`, `vertical` |
| `[boundary]` | `main`: the id of the feature in `boundary.geojson` that is the lot, default the first |
| `[rasters]` | `dem`, `dsm`, `aerial`, `nir` (near-infrared photo: red and near-infrared bands), `canopy`: other paths for those files. `[rasters.shift_m]` moves one from where its file puts it, `aerial = [east, north]` in metres (or `nir`, `dem`, `dsm`): for a photo that sits off the ground model and the house |
| `[aerial]` | `key`, `label`, `label_raw`: how the photo is named in the viewer |
| `[[drapes]]` | `key`, `label`, `georef`, `px`: a scanned plan to lay on the ground; `shift_m = [east, north]` moves it from where its georeference puts it |
| `[datums.<key>]` | `label`, `offset_m`: another height datum your plans use, where their level = model height + offset. The viewer reads points in it |
| `[survey]` | `datum`, `level_column`: the datum and column of levels in `control.csv` |
| `[house]` | `fl_upper`, `roof_pitch_deg`, `roof_plane_at_wall`, `eaves_overhang_m`, required with `house.geojson`; `roof_form`, `hip` by default: `skillion` is one plane rising towards the bearing `roof_azimuth_deg`, and `gable` is two planes with their ridge on that bearing, through the outline's centre or `roof_ridge_offset_m` to the right of it. For both, `roof_plane_at_wall` is the roof's height over the outline's lowest point; `[house.cladding]`; `board_m`, the weatherboards' size from one lap to the next (0.15 unless given): they are drawn that size, counted up from the top of the plastered base, so they can be counted as a ruler up the wall; `boards_on_photos = true` draws them over the walls' photos too; `[house.cladding]` face = [above the floor line, below it] in palette keys, and `default` for the faces it doesn't name; `[house.trim]` `colour` and `faces`: a band at the floor line and facings round the windows below it on those faces; `roof_colour`; `[house.layers]` (see House parts) |
| `[services]` | `assumed_depth_m` (id = metres), `colours` (id = hex), `lids`, `printed_line`, `default_note`, `[services.notes]` |
| `[ifc]` | A designer's model as extra layers: `meta`, `meshes`, `name`, `id_prefix`, `group`, `source`, `note` |
| `[labels]` | Names of the layers: `ground`, `marks`, `points`, `dsm`, `lidar`, `readings`, `features_source`, `bed_floor_source` |
| `[views]` | Extra view buttons: `"Name" = { pos = [x, y, z], target = [x, y, z] }` in viewer metres (x east, y up, z south of the origin) |
| `[enrich]` | `enabled = false` leaves out the viewer's checklist and video walk |
| `[video]` | Point clouds from `domus video` |

## Layers

Every feature has an `id` and usually a `name`, `source` and short `confidence`, shown when you click it. Docstrings
at the top of each module give the full detail: `features.py` for fences, sheds and so on, `vegetation.py` for
plants, `house.py` for the house.

- **`boundary.geojson`**: Polygon per boundary. The main one is drawn and used for the lot's area.
- **`house.geojson`**: A Polygon with id `house-walls` and a `soffit` height and optional `faces` names, one per edge.
  LineStrings with `kind` (`door`, `window`), `face`, and `sill_above_fl` and `head_above_fl` (or `sill` and `head`)
  are its openings. Its other parts each say what they are in `part`, and go in the viewer layer of that kind unless
  they name one in `layer`. Colours are palette keys (`house_texture.PALETTE`) or hex such as `"#7f868b"`. See
  [House parts](#house-parts).
- **`walls-existing.geojson`**: LineString along each wall's face, with a top height as each point's third
  coordinate or a `height_m`. `retained_side` is `left` or `right` looking along the line; `material`, `thickness_m`.
- **`services.geojson`**: LineStrings for drains. A Point with `line` (a drain's id) and `invert_m` gives a level
  along it. Without any, a drain's id in `[services] assumed_depth_m` draws it at that depth.
- **`title.geojson`**: From `domus title`: the lot's title (`kind` `title`, with `title_no`, `estate`, `type`) and the
  easements on or near it (`kind` `easement`, with `intent` and `on_lot`). Wall options flag a wall that runs into an easement.
- **`ground-fixes.geojson`**: Polygons where the LiDAR isn't used, and 3D lines and points that stand in for it.
- **`paved-under-canopy.geojson`**: A Polygon of paving the aerial sees through trees or deep shade. It's painted
  in as paving, using `data/features/paving.jpg`.

## House parts

Each is a feature in `house.geojson` with a `part`, in addition to its `id`. Heights are in the model's datum; the
`*_above_fl` and `top_below_fl_m` ones are measured from `[house] fl_upper`.

| `part` | Geometry | Keys |
|---|---|---|
| `bay` | Polygon, two corners on the wall | `bottom_above_fl`, `top_above_fl`, `hood_top_above_fl`; `hood_overhang_m` (0.08), `faces` (the faces' name, default its id), `skirt_colour`, `trim_colour` |
| `porch` | Rectangle on the house's grid, one side against the walls | `floor`, `roof_at_wall`, `roof_at_edge` (its lean-to roof falls away from the house), `walls` (the sides it has walls on: `north`, `east`, `south` or `west`); `faces`, `landing_colour`, `trim_colour`, `roof_colour`. Open corners get posts and open sides a header |
| `steps` | MultiLineString, one nosing line per tread, with the tread's height as each point's third coordinate | `back_line` (two points: each tread runs back to this line); `base` (else `sink_m`, 0.3, below the ground), `open_sides` (the sides finished in `side_colour`, as compass directions), `colour` |
| `block` | Polygon | A flat-topped solid such as a deck, landing, step, planter or a unit on the ground: `top`, `top_below_fl_m` or `height_m` (above the lowest ground under it), and `base`, `thickness_m` or `sink_m` (0.05, below the ground); `colour`; `pit = true` for one sunk below the ground on purpose, such as a stairwell, which the checks then leave alone |
| `wall` | LineString of two points, its top heights as their third coordinates | `thickness_m`; `base` (else 0.1 below the ground), `colour` |
| `balustrade` | MultiLineString | `height_m`, `top` or `top_below_fl_m` (the deck it stands on); `colour` |
| `chimney` | Polygon | `stack_top`; `pot_top` and `cowl_top` add a flue pot and cowl; `colour` |
| `tank` | Point | `radius_m`, `top` or `height_m`; `colour` |
| `retaining-walls` | (Multi)LineString | Not drawn: walls that hold the ground against the house, such as round a lower floor, which wall options check against |

The layers are `bay`, `porch`, `steps`, `outdoor` (blocks, walls and balustrades), `chimney` and `fixtures` (tanks).
`[house.layers.<key>]` with `name` and `colour` names any layer, including ones a part makes up with `layer`.
