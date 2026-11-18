# Site project format

```
data/site.toml            settings (below)
data/site/*.geojson       curated layers; only boundary.geojson is required
data/lidar/dem.tif        ground model, on any grid its file names (required)
data/lidar/dsm.tif        surface model (optional)
data/lidar/points.laz     classified point cloud from `domus points` (optional; drafted roofs are fitted to it)
data/lidar/dem-points.tif 0.5 m ground from its ground points (`domus points --ground`; used only if [rasters] dem names it)
data/aerial/aerial.tif    aerial photo (optional)
data/aerial/nir.tif       near-infrared photo, red and near-infrared bands (optional)
data/survey/control.csv   survey marks (optional)
data/georef/<sheet>.json  georeferenced scanned plans, for [[drapes]] (optional)
data/features/, data/walls/, data/house/   textures (optional; plain colours without them)
data/layers/*.json        the site's own layers, drawn by its tools (optional; docs/model-format.md)
options/_site.toml        what wall options share
options/*.toml            one per option
viewer/data/              the built model (set DOMUS_OUTPUT to move it; docs/model-format.md)
.domus/viewer.db          saved sketches (set DOMUS_DB to move it)
.domus/build-cache/       what the next build can reuse
.gitignore                what git leaves out of the site: what Domus makes again (`domus new` writes it)
```

## Settings

`data/site.toml` holds a site's settings, and `options/_site.toml` what its wall options share. Required:
`[model] extent`, `terrain_step_m` and `origin`; everything else has a default or can be left out. Domus reads
settings through its schema (`domus.project.schema`), which these tables are written from: a key a site leaves out
reads as its default, and a key Domus doesn't know gets a warning naming the nearest one it does. Keep anything
only your own tools read under `[local]`.

<!-- settings: start (tools/settings_doc.py writes this part) -->
### data/site.toml

| Key | Default | What it is |
|---|---|---|
| `[domus]` | | The site folder itself |
| `domus.format` | `1` | The site folder's format. `domus new` and `domus create` write it; every Domus reads older ones, and `domus site upgrade` writes a site up to its own |
| `domus.requires` |  | The oldest Domus that builds the site, such as ">=2026.12.0" |
| `domus.extensions` | `[]` | Python files of the site's own that its builds run, each with layers(site), once trusted on this machine with `domus site trust` |
| `domus.importer` |  | Where the place came in from: nz (LINZ's data), osm (OpenStreetMap's and the Copernicus ground model) or manual (its own boundary and rasters) |
| `[site]` | | The viewer's text |
| `site.title` | `"Site model"` | The site's name |
| `site.heading` | worked out | The viewer's heading: the title, unless given |
| `site.subtitle` | `""` | A line under the heading |
| `site.about` | `""` | A paragraph about the site |
| `site.attribution` | `""` | Where the data came from, shown in the viewer |
| `[crs]` | | The site's grid and heights |
| `crs.epsg` | required | The grid's EPSG code, in metres, such as 2193 for NZTM2000 |
| `crs.label` | worked out | The grid's name for people: its own name and EPSG code, unless given |
| `crs.vertical` | `""` | The height datum's name, as the viewer shows it: none named unless given |
| `[boundary]` | | The lot |
| `boundary.main` | worked out | The id of the feature in boundary.geojson that is the lot, the first unless given; the others are neighbours, rights of way and the like |
| `[model]` | | The area modelled |
| `model.extent` | required | E min, N min, E max, N max on the site's grid |
| `model.terrain_step_m` | required | The ground's grid step, in metres |
| `model.origin` | required | The viewer's origin: E, N and optionally a height (the ground's, unless given) |
| `[rasters]` | | Other paths for the site's rasters |
| `rasters.dem` | `"data/lidar/dem.tif"` | The ground model |
| `rasters.dsm` | `"data/lidar/dsm.tif"` | The surface model |
| `rasters.aerial` | `"data/aerial/aerial.tif"` | The aerial photo |
| `rasters.nir` | `"data/aerial/nir.tif"` | The near-infrared photo, red and near-infrared bands |
| `rasters.canopy` | `"data/sources/canopy.geojson"` | The tree crowns `domus canopy` maps |
| `rasters.shift_m.<raster>` | `[0.0, 0.0]` | Moves a raster from where its file puts it: `aerial = [east, north]` in metres, for a photo that sits off the ground model and the house |
| `[aerial]` | | How the aerial photo is named in the viewer |
| `aerial.key` | `"aerial"` | Its texture's key |
| `aerial.label` | `"Aerial photo"` | Its name |
| `aerial.label_raw` | `"Aerial photo as taken"` | Its name before the paving under trees is painted in |
| `[[drapes]]` | | A scanned plan to lay on the ground |
| `drapes[].key` | required | Its texture's key |
| `drapes[].label` | required | Its name in the viewer |
| `drapes[].georef` | required | Its georeference, in data/georef/ |
| `drapes[].px` | `0.05` | Metres a pixel of the drape covers |
| `drapes[].shift_m` | `[0.0, 0.0]` | [east, north]: moves it from where its georeference puts it |
| `[datums.<datum>]` | | Another height datum your plans use, where their level = model height + offset; the viewer reads points in it |
| `datums.<datum>.label` | required | Its name |
| `datums.<datum>.offset_m` | required | Its level less the model's height |
| `datums.<datum>.uncertainty_m` |  | How sure the offset is, in metres: for the record |
| `[survey]` | | Survey marks in data/survey/control.csv |
| `survey.datum` |  | The datum of their levels, one of [datums] |
| `survey.level_column` | `"level"` | The column of their levels |
| `[house]` | | The house, with house.geojson |
| `house.fl_upper` | required | The floor level openings and parts are measured from |
| `house.fl_upper_uncertainty_m` |  | How sure fl_upper is, in metres: for the record |
| `house.fl_lower` | worked out | A lower floor's level: fl_upper, unless given |
| `house.roof_pitch_deg` | required | The roof's pitch, in degrees |
| `house.roof_plane_at_wall` | required | The roof plane's height over the outline (its lowest point, for a skillion or gable) |
| `house.eaves_overhang_m` | required | How far the eaves stand out from the walls |
| `house.roof_form` | `"hip"` | hip, skillion (one plane rising towards roof_azimuth_deg) or gable (two planes, their ridge on that bearing) |
| `house.roof_azimuth_deg` |  | The bearing a skillion rises towards, or a gable's ridge runs on |
| `house.roof_ridge_offset_m` | `0.0` | How far right of the outline's centre a gable's ridge runs |
| `house.roof` | `"tiles"` | How the roof is drawn: tiles, metal (long-run iron) or membrane |
| `house.roof_colour` | `"roof"` | The roof's colour: a palette key, one of the site's own colours or hex |
| `house.palette.<colour>` |  | The site's own colours, by name, for the cladding, trim and roof: name = "#rrggbb" |
| `house.board_m` | `0.15` | The weatherboards' size from one lap to the next, counted up from the plastered base's top |
| `house.boards_on_photos` | `false` | Draw the boards over the walls' photos too |
| `house.cladding.<face>` | worked out | face = [above the floor line, below it]: a colour is weatherboards in it, and plaster:, brick: or panel: before a colour names another cladding; `default` for the faces it doesn't name; `default`: The faces it doesn't name (`["boards-beige", "boards-beige"]`) |
| `[house.trim]` | | A band at the floor line and facings round the windows below it |
| `house.trim.colour` |  | Its colour |
| `house.trim.faces` | `[]` | The faces that have it |
| `[house.layers.<layer>]` | | The house's viewer layers: a name and a colour for each |
| `house.layers.<layer>.name` | worked out | Its name: the layer's own, unless given |
| `house.layers.<layer>.colour` | worked out | Its colour in the layer list: the layer's own, unless given |
| `house.roof_name_note` | `""` | Words after the roof layer's name |
| `house.roof_source` | `"the house's outline and [house] in data/site.toml"` | Where the roof's shape came from |
| `house.walls_source_note` | `""` | Words after the walls' source |
| `house.roof_at_wall_above_fl` |  | The roof plane at the wall, above fl_upper, for placing videos on the house |
| `[services]` | | Drains (services.geojson) |
| `services.assumed_depth_m.<drain>` |  | A depth to draw a drain at, below the ground, when it has no levels |
| `services.colours.<drain>` | `"#2f2f2f"` | A drain's colour |
| `services.lids` | `[]` | The inspection points whose lids an option must reset where it moves the ground |
| `[services.default_note]` | | What a drain's levels rest on, unless its own note says |
| `services.default_note.text` |  | The note, which can use {lo}, {hi} and {span} |
| `services.default_note.confidence` |  | How far to trust the levels |
| `[services.notes.<drain>]` | | What one drain's levels rest on |
| `services.notes.<drain>.text` |  | The note, which can use {lo}, {hi} and {span} |
| `services.notes.<drain>.confidence` |  | How far to trust the levels |
| `[ifc]` | | A designer's model as extra layers (domus prep ifc) |
| `ifc.meta` | required | Its meta file |
| `ifc.meshes` | required | Its meshes file |
| `ifc.name` | required | The layers' name |
| `ifc.id_prefix` | required | The prefix of their ids |
| `ifc.group` | required | Their group in the layer list |
| `ifc.source` | required | Where the model came from |
| `ifc.note` | required | A note on it |
| `[labels]` | | The names of some of the viewer's layers |
| `labels.ground` | `"Ground (LiDAR, with walls cut in)"` | The ground |
| `labels.marks` | worked out | Survey marks and drain labels: their name with the heights' datum, unless given |
| `labels.points` | `"Point cloud from the videos"` | The point cloud from the videos |
| `labels.dsm` | `"LiDAR surface (trees and roofs), 1 m"` | The surface model |
| `labels.lidar` | `"LiDAR"` | What the LiDAR is called in other layers' names |
| `labels.readings` | `"Readings from the clips"` | Readings from the clips |
| `labels.features_source` | `"data/site/features-*.geojson (each feature's source and basis)"` | The site features' source |
| `labels.bed_floor_source` | `"Outlines as the beds in data/site/trees.geojson"` | The beds' floors' source |
| `[views.<view>]` | | Extra view buttons: "Name" = { pos = [x, y, z], target = [x, y, z] } in viewer metres (x east, y up, z south of the origin) |
| `views.<view>.pos` | required | Where the camera stands |
| `views.<view>.target` | required | What it looks at |
| `[enrich]` | | The viewer's checklist and video walk |
| `enrich.enabled` | `true` | Show them |
| `local.<key>` |  | Anything a site keeps for its own tools or records: Domus doesn't read it or warn about it |
| `[video]` | | Point clouds from domus video |
| `video.clouds` | `[]` | The placed clouds to show (data/video/<name>.npz) |
| `video.keep.<cloud>` |  | [E min, N min, E max, N max]: only this part of a cloud is trusted |
| `[[video.compare]]` | | Two placements of a clip, shown as clouds side by side |
| `video.compare[].id` | required | Its id |
| `video.compare[].name` | required | Its name |
| `video.compare[].clouds` | required | The clouds it moves |
| `video.compare[].pivot` | required | [E, N] it turns about |
| `video.compare[].dz_m` | `0.0` | How far it moves up |
| `video.compare[].turn_deg` | `0.0` | How far it turns, counter-clockwise |
| `[[video.question_marks]]` | | A clip's reading of the house's levels, as a label |
| `video.question_marks[].id` | required | Its id |
| `video.question_marks[].text` | required | Its label |
| `video.question_marks[].picks` | required | The picks it reads |
| `video.question_marks[].drawn` |  | What it's drawn on |
| `video.question_marks[].lift_m` | `0.15` | How far above that it stands |

### options/_site.toml

| Key | Default | What it is |
|---|---|---|
| `[frame]` | | The frame options are drawn in: s along a boundary, o in from it |
| `frame.origin` | required | Where s starts, on the site's grid |
| `frame.toward` | required | A point s runs towards |
| `frame.side` | `"right"` | The lot's side of the line, looking along it |
| `[regrade]` | | Where options may regrade the ground and flat lawn is counted: the lot, less the house and a margin round it |
| `regrade.house_margin_m` | `0.6` | The margin round the house |
| `regrade.flat_slope` | `0.05` | The slope that still counts as flat lawn (rise over run) |
| `regrade.s_max` |  | Only where s is under this |
| `regrade.polygon_so` |  | Only inside this polygon, in (s, o) |
| `[defaults]` | | What options' walls and terraces take unless they say |
| `defaults.upstand_m` | required | How far a wall's top stands above the ground it retains |
| `defaults.batter` | required | Unretained terrace edges slope back at 1 vertical : this horizontal |
| `defaults.thickness_m` | required | A wall's thickness |
| `[rules]` | | Checks every option is held to |
| `rules.drain_cover_m` | `0.3` | Least cover over a drain under lawn after a cut |
| `local.<key>` |  | Anything a site keeps for its own tools or records: Domus doesn't read it or warn about it |

<!-- settings: end -->

## Layers

Every feature has an `id` and usually a `name`, `source` and short `confidence`, shown when you click it. Docstrings
at the top of each module give the full detail: `model/features.py` for fences, sheds and so on,
`model/vegetation.py` for plants, `model/house.py` for the house.

- **`boundary.geojson`**: Polygon per boundary. The main one is drawn and used for the lot's area.
- **`house.geojson`**: A Polygon with id `house-walls` and a `soffit` height and optional `faces` names, one per edge.
  LineStrings with `kind` (`door`, `window`), `face`, and `sill_above_fl` and `head_above_fl` (or `sill` and `head`)
  are its openings. Its other parts each say what they are in `part`, and go in the viewer layer of that kind unless
  they name one in `layer`. Colours are palette keys (`house_texture.PALETTE`) or hex such as `"#7f868b"`. See
  [House parts](#house-parts).
- **`walls-existing.geojson`**: LineString along each wall's face, with a top height as each point's third
  coordinate or a `height_m`. `retained_side` is `left` or `right` looking along the line; `material`, `thickness_m`.
  A wall with posts, such as `timber`, has one at every point of its line. `open_ends` (`start`, `end`) names an end
  with none, where its boards run on, as behind another wall.
- **`services.geojson`**: LineStrings for drains. A Point with `line` (a drain's id) and `invert_m` gives a level
  along it, and its `label`, if any, shows by it. The points from one document (their `source`) give its levels over
  their own stretch; where documents overlap, the higher level is used. Without any, a drain's id in
  `[services] assumed_depth_m` draws it at that depth. A drain's `follow_up` says what's known to be wrong with its
  levels: the viewer shows it on the pipe, and the options' checks of that drain repeat it.
- **`title.geojson`**: From `domus title`: the lot's title (`kind` `title`, with `title_no`, `estate`, `type`) and the
  easements on or near it (`kind` `easement`, with `intent` and `on_lot`). Wall options flag a wall that runs into an easement.
- **`ground-fixes.geojson`**: Polygons where the LiDAR isn't used, and 3D lines and points that stand in for it.
- **`paved-under-canopy.geojson`**: A Polygon of paving the aerial sees through trees or deep shade. It's painted
  in as paving, using `data/features/paving.jpg`.

### Changing the layers

The layers are curated by hand, and three commands change them in ways you can check and undo. Each says what it
would change and writes nothing until you add `--apply`; then it copies each file it changes into a folder you name
(a fresh one each time), writes the new file, reads it back and checks that only what was named changed, and only then
puts it in place, keeping the file's own layout.

- `domus site shift SPEC.json BACKUP` moves named features by one vector in plan (and their heights, if you like),
  adding a sentence to each one's `basis`: for a set of features placed from a survey that sits off the rest.
- `domus site edit SPEC.json BACKUP` sets named features' vertices (in the wall options' frame or on the grid) and
  properties, adds features and deletes them.
- `domus site regrade CLOUD SPEC.json BACKUP` gives ground-fix points and lines the levels a placed video's cloud
  shows, vertex by vertex; one the cloud doesn't see well moves with the read ones near it.

The docstring of `src/domus/project/edits.py` describes each SPEC.json.

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
