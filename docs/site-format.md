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
data/house.ifc            the house as an IFC file, its master when [house] source = "ifc" (optional; `domus house ifc`)
data/layers/*.json        the site's own layers, drawn by its tools (optional; docs/model-format.md)
data/observations.json    readings from videos, each checking an element of the model by its id (optional; docs/observations.md)
options/_site.toml        what wall options share
options/*.toml            one per option
viewer/data/              the built model (set DOMUS_OUTPUT to move it; docs/model-format.md)
.domus/viewer.db          saved sketches (set DOMUS_DB to move it)
.domus/build-cache/       what the next build can reuse
.domus/edits/             each change made to the site's data, and the files as they were (docs/changes.md)
.domus/proposals/         changes proposed for a person to accept or reject (docs/changes.md)
.gitignore                what git leaves out of the site: what Domus makes again (`domus new` writes it)
```

## Settings

`data/site.toml` holds a site's settings, and `options/_site.toml` what its wall options share. Required:
`[model] extent`, `terrain_step_m` and `origin`; everything else has a default or can be left out. Domus reads
settings through its schema (`domus.project.schema`), which these tables are written from: a key a site leaves out
reads as its default, and a key Domus doesn't know gets a warning naming the nearest one it does. Keep anything
only your own tools read under `[local]`. So do notes on how sure a datum's offset and `[house] fl_upper` are:
`[local.datums.<datum>] uncertainty_m` and `[local.house] fl_upper_uncertainty_m`. A site in format 3 or before that
gives them in `[datums.<datum>]` and `[house]` reads with them there, and `domus site upgrade` moves them. A clip's
readings and two placements of a clip side by side are observations, in `data/observations.json`
([observations.md](observations.md)): a site in format 4 or before that gives them as `[[video.question_marks]]` and
`[[video.compare]]` reads with them there, and `domus site upgrade` moves them into that file.

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
| `[aerial]` | | How the aerial photo is named in the viewer, and how paving under trees is painted into it |
| `aerial.key` | `"aerial"` | Its texture's key |
| `aerial.label` | `"Aerial photo"` | Its name |
| `aerial.label_raw` | `"Aerial photo as taken"` | Its name before the paving under trees is painted in |
| `[aerial.paving]` | | Painting in the paving the photo sees through trees or deep shade, in data/site/paved-under-canopy.geojson |
| `aerial.paving.grey` | `0.18` | A pixel is grey if its colours spread by less than this share of its brightest |
| `aerial.paving.sun_green` | `0.04` | The paving in sun: the grey pixels in the area less green than this (green less the mean of red and blue, over the brightness) |
| `aerial.paving.sun_percentile` | `75` | Its sunlit brightness: this percentile of theirs |
| `aerial.paving.sun_share` | `0.85` | Its sunlit colour: the median of those pixels at least this share of that brightness |
| `aerial.paving.foliage_green` | `[0.02, 0.1]` | Foliage: how green a pixel is where it starts to count as foliage, and where it counts fully |
| `aerial.paving.foliage_dark` | `[0.6, 0.4]` | Foliage too: how dark a pixel that isn't grey is, as a share of the sunlit brightness, where it starts to count and where it counts fully |
| `aerial.paving.foliage_blur_m` | `0.15` | How far foliage is softened, in metres |
| `aerial.paving.shade_dark` | `[0.95, 0.7]` | Shade: how dark the paving is, as a share of the sunlit brightness, where it starts to be lifted to it and where it's lifted fully |
| `aerial.paving.shade_blur_m` | `0.4` | How far the brightness is smoothed to find shade, in metres |
| `aerial.paving.shade_detail` | `0.5` | How much of the photo's detail paving lifted out of shade keeps (the rest is the dapple of the crowns' light) |
| `aerial.paving.feather_m` | `0.25` | How far the painting fades out at the area's edges, in metres |
| `[[drapes]]` | | A scanned plan to lay on the ground |
| `drapes[].key` | required | Its texture's key |
| `drapes[].label` | required | Its name in the viewer |
| `drapes[].georef` | required | Its georeference, in data/georef/ |
| `drapes[].px` | `0.05` | Metres a pixel of the drape covers |
| `drapes[].shift_m` | `[0.0, 0.0]` | [east, north]: moves it from where its georeference puts it |
| `[datums.<datum>]` | | Another height datum your plans use, where their level = model height + offset; the viewer reads points in it |
| `datums.<datum>.label` | required | Its name |
| `datums.<datum>.offset_m` | required | Its level less the model's height |
| `[survey]` | | Survey marks in data/survey/control.csv |
| `survey.datum` |  | The datum of their levels, one of [datums] |
| `survey.level_column` | `"level"` | The column of their levels |
| `[house]` | | The house, with house.geojson |
| `house.fl_upper` | required | The floor level openings and parts are measured from |
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
| `house.roof_at_wall_above_fl` | worked out | The roof plane at the wall, above fl_upper, for placing videos on the house: optional, roof_plane_at_wall less fl_upper, unless given |
| `house.source` | `"drawn"` | Where the build draws the house from: drawn, from house.geojson and these settings, or ifc, from data/house.ifc, which domus house ifc writes from them |
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
| `labels.readings` | `"Readings from the clips"` | The observations' labels (data/observations.json) |
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
| `defaults.paving_m` | `0.2` | How deep a paved terrace's paving is, from its top (the terrace's level) to the soil under it: the pavers, their bedding and any basecourse, which cut and fill don't count |
| `defaults.depth_m` | worked out | A wall's body, from its face to its back, drawn and not soil, where thickness_m is what its set-out and corners take: thickness_m, unless given |
| `defaults.cap_depth_m` | worked out | Its capping course's depth, from its front to its back: depth_m, unless given |
| `defaults.cap_overhang_m` | `0.0` | How far its capping course stands out in front of its face |
| `defaults.embed_m` | `0.0` | How far its first course goes below the ground at its toe, at least |
| `defaults.embed_per_m` | `0.0` | And for each metre the wall stands above the ground at its toe, this much, where that's more |
| `defaults.embed_courses` | `0` | And for a wall of named blocks, this many whole courses, where that's more |
| `defaults.bedding_m` | `0.0` | A bedding layer under its first course |
| `defaults.base_m` | `0.0` | A base course under the bedding, compacted |
| `defaults.trench_m` | worked out | Its trench's width from its face, filled from the bottom of the base course with what's brought in (the base, the bedding, the wall and any drainage aggregate behind it): depth_m, unless given |
| `defaults.seal_m` | `0.0` | Soil over the drainage aggregate behind it, at the top of its trench under the finished ground: soil brought back in, counted as fill |
| `defaults.cut_min_m` | `0.15` | The shortest piece a named unit (unit_m) is cut to: where a course would end in a shorter one, it and the whole unit beside it become two equal cut units, and so would a capping course's |
| `defaults.pins_per_unit` |  | Pins for each unit under another course, in a wall of named blocks |
| `defaults.pins_per_box` |  | Pins in a box, to count the boxes |
| `defaults.core_void_m3` |  | The voids in each unit, filled with drainage aggregate |
| `defaults.pipe_m` |  | A perforated drainage pipe along a wall's foot, this wide |
| `defaults.outlet_spacing_m` | `15.0` | An outlet from that pipe for each this much of it, or part of it |
| `defaults.geotextile` | `false` | Filter fabric over a wall's face, its length by its height |
| `defaults.post_spacing_m` |  | A timber wall's posts this far apart, with one at each end |
| `defaults.paving_bedding_m` | `0.0` | The bedding sand under a paved terrace's pavers, a part of paving_m |
| `defaults.paving_base_m` | `0.0` | The basecourse under that bedding, a part of paving_m |
| `[materials]` | | What the options' materials lists take besides what they build (docs/options.md) |
| `materials.allowance_pct` | `0.0` | What's added to each quantity to order, in per cent: the list gives the net beside it |
| `materials.density_t_m3.<volume>` |  | A volume's density in tonnes a cubic metre, which gives its weight: base, bedding, drainage, paving_bedding or paving_base |
| `materials.sleeper_m` |  | A sleeper's length, to count the sleepers a flight of sleeper steps takes |
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
  `[services] assumed_depth_m` draws it at that depth, below the ground as the build draws it (with the existing walls'
  faces and tops in it). A drain's `follow_up` says what's known to be wrong with its
  levels: the viewer shows it on the pipe, and the options' checks of that drain repeat it.
- **`title.geojson`**: From `domus title`: the lot's title (`kind` `title`, with `title_no`, `estate`, `type`) and the
  easements on or near it (`kind` `easement`, with `intent` and `on_lot`). Wall options flag a wall that runs into an easement.
- **`ground-fixes.geojson`**: Polygons where the LiDAR isn't used, and 3D lines and points that stand in for it.
  Where four points lie on one circle, as a regular pattern's do, the ground between them is cut along the pair
  closer in height. Two fix points within 1 mm of each other in plan, from any features (a line's own points too),
  are one place, where the later in the file is kept; where their heights are more than 5 mm apart, the build warns
  and `domus prep check sanity` reports it, naming both features and the two heights.
- **`paved-under-canopy.geojson`**: Polygons round paving that trees hang over, such as a drive under a tree's crown.
  An aerial photo, taken from above in sun, shows the crowns and their shade there, which the model would lay on the
  ground under its own trees. Inside the polygons the build paints the paving in: what the photo shows as foliage takes
  the paving's own colour in sun, read from the photo where it sees the same paving in sun, with the grain of
  `data/features/paving.jpg` if the site has one, and paving in shade is lifted to that colour, keeping some of the
  photo's dapple. The viewer keeps the photo as taken as well (`[aerial] label_raw`). Draw one wherever crowns or deep
  shade hide paving in the photo: round the paving, not the crowns, which lean in the photo and are found by their
  colour, and take in some of that paving in sun. Lawns and beds under trees stay out of it. `[aerial.paving]` in
  `data/site.toml` holds what's taken for paving in sun, foliage and shade, for a photo that needs other numbers.

### Changing the layers

The layers are curated by hand, and every change to them, or to the settings, goes one way, so it can be checked and
undone ([changes.md](changes.md)): it's said as an intent, such as "move wall W1 0.5 m east"; each file it changes is
copied, written to a temporary file in its own layout, read back and checked to have changed only as asked, and only
then put in place; and it's recorded in `.domus/edits/` with who, when and why, so `domus site undo` can put it back.
Each command says what it would change and writes nothing until you add `--apply`.

- `domus site edit SPEC.json [BACKUP]` applies intents (changes.md), or a spec in its older form: named features'
  vertices (in the wall options' frame or on the grid) and properties set, features added and deleted.
- `domus site shift SPEC.json [BACKUP]` moves named features by one vector in plan (and their heights, if you like),
  adding a sentence to each one's `basis`: for a set of features placed from a survey that sits off the rest.
- `domus site regrade CLOUD SPEC.json [BACKUP]` gives ground-fix points and lines the levels a placed video's cloud
  shows, vertex by vertex; one the cloud doesn't see well moves with the read ones near it.

`BACKUP`, a fresh folder, holds the copies of the files as they were; without it they're kept beside the edit's
record. The docstring of `src/domus/project/edits.py` describes the older specs. A check or a tool that finds
something to change proposes it in `.domus/proposals/`, for a person to accept or reject (`domus site proposals`,
`accept` and `reject`).

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

## The house as an IFC file

`domus house ifc` (`site.write_house_ifc()`, which needs the `ifc` extra) writes the house as `house.geojson` and
`[house]` draw it today to `data/house.ifc`, an IFC4 file written with IfcOpenShell. With `[house] source = "ifc"` in
`data/site.toml`, that file is the house's one source: the build draws the house from it, and every part of Domus that
reads the house reads it there, so what a designer's software or IfcOpenShell changes in it is what the model shows.
Without the setting the file changes nothing. Writing it is an edit on the change route
([changes.md](changes.md#the-houses-ifc)), recorded with who, when and why, the file as it was kept, and undone with
`domus site undo`; `--force` writes it again over one that's there. Its elements change through that route too:
moved, turned, a storey raised, attributes and property values set, elements added and deleted, each undoable.

The file holds one site, georeferenced as `domus export` does it (an IfcMapConversion from the model's origin to the
site's grid), a building on it, and a storey at each of the house's floor levels: `fl_upper`, and `fl_lower` where
it's another level. Each element stands on the highest storey whose floor is at or below its lowest point.

| Element | IFC |
|---|---|
| Each wall face of `house-walls` | `IfcWall`, placed at the face's left end seen from outside, its x axis along the face |
| The eaves: fascia and soffit | `IfcCovering`, `EAVES` |
| The roof | `IfcRoof`: `HIP_ROOF`, `SHED_ROOF` (a skillion) or `GABLE_ROOF` |
| `steps`, `block`, `wall`, `balustrade` | `IfcStair` (`STRAIGHT_RUN_STAIR`), `IfcSlab`, `IfcWall`, `IfcRailing` (`BALUSTRADE`) |
| `chimney`, `tank` | `IfcChimney`, `IfcTank` (`STORAGE`) |
| `bay`, `porch` | `IfcBuildingElementProxy` (`BAY`, `PORCH`), with its wall faces as `IfcWall`s that are its parts |
| Each opening (a feature with a `kind`) | `IfcDoor` or `IfcWindow`, with no body of its own (the face's picture draws it): a part of the wall face it's on, placed at its left end at its sill, with its `OverallWidth` and `OverallHeight` |
| `retaining-walls`, and any other feature that draws nothing | An element with no body: an `IfcWall` (`RETAININGWALL`), or of its part's class |

Each element's body is its triangles as the house draws them, each piece an `IfcTriangulatedFaceSet` in a surface
style named for its colour (its palette key, or its hex) and shaded in it. Its property set, `Domus`, holds its id
(`ref`, which is its `Tag` too: a part's id in `house.geojson`, a face's own id, `roof` or `eaves`), the layer it's
drawn in (`layer`), what it is (`part`), the feature of `house.geojson` it comes from (`feature`) and that feature's
`source`, and on a wall face, the face it is (`face`). Its GlobalId is a uuid5 of its id under a fixed namespace, as
are the project's, the site's, the building's and the storeys', and every relationship's comes from what it relates,
so writing the house again gives the same ids throughout.

The file holds the whole of the house's description too, each piece with the element it belongs to, so nothing is read
from `house.geojson`, or from `[house]`'s settings of the house's shape, while the file is the source:

- On every element, in `Domus`: where it stood when the file was written (`placed`: east, north, height, and the angle
  of its x axis from east in radians) and, for a feature of `house.geojson`, that feature as written (`feature_json`)
  and its place in the file (`index`). On a wall face of `house-walls`, the side of the outline it is (`edge`) and its
  `plinth_top`, where one is given.
- On the building, in `Domus`: the walls, `house-walls`, as written (`walls`, and `walls_index`), and `[house]`'s
  settings of the house's shape as `data/site.toml` gave them (`house`): `fl_upper`, `fl_lower`, `roof_pitch_deg`,
  `roof_plane_at_wall`, `eaves_overhang_m`, `roof_form`, `roof_azimuth_deg`, `roof_ridge_offset_m` and
  `roof_at_wall_above_fl`.

Read from the file, the house is that description as its elements stand now. Each feature moves and turns with its
element: its positions, its heights on the datum (`sill`, `head`, `floor`, `roof_at_wall`, `roof_at_edge`, `base`,
`top`, `stack_top`, `pot_top` and `cowl_top`) and a flight's `back_line`; the sides a porch's walls or a flight's open
sides are on (`walls`, `open_sides`) turn with it, to the nearest compass direction; and its heights over the floor
(`*_above_fl`, `top_below_fl_m`) go with it against `fl_upper`. The walls' outline takes each side from where its wall
face stands and each corner from where its two sides' lines meet, so a face moved out takes its neighbours' ends with
it; the faces' ids and plinths go with the faces, and the soffit with the eaves. The floor levels are the storeys',
`roof_plane_at_wall` and `roof_at_wall_above_fl` rise with the roof, and a skillion's or a gable's `roof_azimuth_deg`
turns with it. An element added through the route (`ifc-add`) is a `block` of the house: its outline, `base` and
`top`. `[house]`'s other settings, how the house looks, are still `[house]`'s.

So the build draws the house's layers from the file: each element in the house layer its `layer` names, or, without
one, the layer its IFC class says (`IfcWall` the walls, `IfcCovering` the eaves, `IfcStair` the steps, `IfcSlab` and
`IfcRailing` the outdoor parts, a proxy's `BAY` or `PORCH` its own); IfcOpenShell triangulates it in the model's
coordinates, and the file's own map conversion puts it on the site's grid. Each piece takes its style's colour. A wall
that still carries the id of one of the house's faces (`face`) keeps that face's picture, its photo where one covers it
(`data/house/`) and its drawn boards and windows elsewhere, laid on it by the wall's own coordinates, so the picture
moves with the wall; any other wall takes its colour. The roof's tiles lie as the drawn roof's do, by the house's roof
form, in the roof's own coordinates. Everything else that reads the house reads it from the file too: the house's
numbers in `summary.json` (which names the file, `source`), the walk route and what the viewer snaps to, the wall
options' regrade area, house margin and checks, `domus prep check`, the observations' plinths and floor levels,
placing videos (the walls and the eaves' height), the overlays, the house's textures and the canopy's map.

What the description doesn't follow: an element's body changed in other software (it follows the element's placement,
not its triangles); a wall face deleted (the outline keeps that side where it was written); a feature's height over
the ground (`height_m`, `sink_m`) when its element is raised; a hipped roof turned, or the roof moved in plan (its shape
follows the walls); and an element other software adds, which is drawn but isn't one of the house's parts. A file
written before Domus kept the description in it, or by other software, stops the build and says to write it again
(`domus house ifc --force`), as a missing file, or one IfcOpenShell can't read, says how to make it.

`domus house ifc` records `house.geojson`'s SHA-256 and that of `[house]`'s settings of the house's shape in its
edit's record. While the file is the house's source, a build whose `house.geojson` or `[house]` no longer matches that
record warns that the build doesn't read it: edit the house through the IFC (`domus site edit`), or write the IFC
again (`domus house ifc --force`).
