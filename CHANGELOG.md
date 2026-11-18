# Changelog

## Unreleased

- **For site projects:** every command is a call into the public API, so a site's tools can do what the commands do.
  `api.create()`, `api.new()` and `api.demo()` start a site; a site's `fetch()`, `map_canopy()`, `fetch_marks()`,
  `fetch_title()`, `fetch_points()`, `build(fresh=False)`, `scan()`, `shots()`, `upgrade()`, `trust()`, `shift()`,
  `edit()` and `regrade()` do what `domus fetch`, `canopy`, `marks`, `title`, `points`, `build`, `scan`, `shots` and
  `site ...` do ([docs/api.md](docs/api.md)). Each takes `say`, which gets the lines the command prints as it goes.
  The commands work as they did, with the same options, output and exit codes.
- Every command and step prints its usage with `--help` and does nothing else: `domus build`, `fetch`, `canopy` and
  `video export` ran (and `fetch` downloaded), `video merge`, `split`, `link`, `link-picks` and `pick` failed on it, and
  `prep canopy-plot` took it as the name of the picture to write. A command run where there's no site project says
  so in a line, where `build`, `fetch` and `canopy` printed a traceback, and one given an argument it doesn't take
  says so, where `build`, `fetch` and `canopy` went on without it. `domus --help` lists the commands, and
  `domus video --help` and `domus prep --help` the steps.
- **For site projects:** every wall option's cut and fill change once, because they now mean soil, measured from
  today's best ground. The options are designed on and measured from the ground as the build draws it: the ground
  fixes from measurements and placed walks, and the existing walls' faces and tops, with the LiDAR only where
  nothing better is known. Until now they were worked out on the LiDAR with the fixes and without the walls, which
  smears a wall's step into a slope. So where batters meet the ground, the flat lawn before, the cover over the
  drains and the step at the regrade area's edge are worked out on that ground too, and the fixes and walls a site
  measures move its options' numbers. Cut and fill count soil only: the soil a new wall holds back counts, but the
  wall itself, from its face to its back and from its toe to its top, doesn't, nor does an existing wall, nor does
  paving. A terrace of `kind = "paving"` (what a path sketched in the viewer becomes) has its level at the paving's
  top and the soil `paving_m` under it: 0.2 m, unless the terrace or `[defaults] paving_m` in `options/_site.toml`
  says otherwise. The range on cut and fill is worked out as before. The viewer colours the ground by the same cut
  and fill, and says under its comparison what they mean ([docs/options.md](docs/options.md#what-the-build-works-out)).

## 2026.11.44

- **For site projects:** on a site whose options' frame has the lot on its left (`[frame] side = "left"`), the viewer's
  s and o (the corner readout while dragging, the sketch editor's corners and the s and o saved with a sketch) had o
  the wrong way round: the viewer took the lot to be on the frame line's right. It now reads the frame's side, as the
  options do. Sketches saved on such a site before this have o's sign flipped in their `so`; their points on the grid
  (`en`), which **Make it an option** uses, were right. `tools/made_up_sites.py` has a site like that, `left`, which
  CI builds with the others.
- **For site projects:** `domus video place` works without `[house] roof_at_wall_above_fl`, which neither `domus
  create` nor `domus demo` writes. It stopped with a `KeyError` without it; now it takes the eaves' height from
  `fl_upper` plus `roof_at_wall_above_fl` where a site gives it, else from `roof_plane_at_wall`, the same height, which
  every house has. A site that set `roof_at_wall_above_fl` to `roof_plane_at_wall` less `fl_upper` only to place a
  video can drop it ([docs/site-format.md](docs/site-format.md)).
- The README drops its small 3D model, which GitHub's viewer drew as a little blue patch on a wire floor, and the
  README's and AGENTS.md's flowcharts of many steps read top to bottom, so GitHub doesn't shrink them.
- `domus demo` writes a surface model (`data/lidar/dsm.tif`) with the roof Domus draws for the demo's house, its hip,
  pitch, eaves and overhang, where it had the house as a flat block 5 m above the ground. So the demo passes its own
  roof check, `domus prep check roof`, as the made-up sites in `tools/made_up_sites.py`, written from it, do too, and
  the viewer's LiDAR surface layer shows the roof. AGENTS.md's worked example of a failing roof check says what the
  pattern means instead of quoting the demo.

## 2026.11.43

- **For site projects:** the command line, the server and the viewer are Domus's app, in `domus.app`:
  `domus.app.cli`, `domus.app.serve` and `domus/app/viewer/`, which nothing else in Domus imports. The `domus`
  command, the container and the server work as they did. The old names `domus.cli` and `domus.serve` are the same
  modules and say they're deprecated (a DeprecationWarning), and `python -m domus.cli` still runs the command line; a
  site's tools use `domus.api` rather than either. The container's icon is at the viewer's new place.

## 2026.11.42

- **For site projects:** every part of Domus is handed the site it works on, where it read the current one, so
  nothing can mix one site up with another in a process that holds several. The command line and the server read
  `DOMUS_SITE` (or `--site`) once and hand the site down, and a job in a process of its own gets the site's folder.
  A build's extensions get the site being built: they got the one `DOMUS_SITE` names, another for a site opened by
  its path. A site's tools that call Domus's internals without a site, as `design.frame()`, `design.settings()`,
  `design.Design(option)`, `design.Terrace`, `terrain.ground()`, `terrain.wall_levels()`, `terrain.build()`,
  `options.load()`, `raster.dem()`, `raster.dsm()`, `raster.DEM`, `build.paved_under_canopy()`,
  `build.aerial_sampler()` and `build.ground_photo_sampler()` do, keep working on the current site, and are
  deprecated: each takes the site now (`site=`, or first). Domus's other internals take it as their first argument,
  and the public API needs none of this.
- The README, AGENTS.md and CONTRIBUTING.md are rewritten. The README tells the story of how a model comes about,
  with diagrams and pictures of the made-up sites and a small 3D model to turn; AGENTS.md is an agent's step-by-step
  manual, from setting a site up to the video walk (the first thing to reach for), fitting, adapting a site and the
  public API; CONTRIBUTING.md is the contributor's guide.

## 2026.11.41

- **For site projects:** a site's grid is a value of its own. `site.grid` in `domus.api` gives the site's grid and
  heights: its EPSG code, name and vertical datum, its origin and its other height datums, with the sums that turn
  points between its grid, NZTM2000 and longitude and latitude, and heights between the model's datum and another
  ([docs/api.md](docs/api.md)). Inside Domus, `domus.geo.crs` is now the grid maths, a `Grid` made from a site's
  settings that reads no site, and the site's folder, output, settings and format are its Site's. crs's names
  that read the current site (`crs.ROOT`, `crs.OUTPUT`, `crs.settings()`, `crs.site_format()`, `crs.site_epsg()`,
  `crs.label()`, `crs.site_to_nztm()`, `crs.nztm_to_site()`, `crs.site_to_lonlat()`, `crs.datums()`,
  `crs.to_model_height()` and `crs.from_model_height()`) work as they did, and are deprecated: they'll warn, and later
  go. A site's tools and tests can use `api.open_site()`'s `path`, `output`, `settings()` and `grid` instead.

## 2026.11.40

- **For site projects:** `domus export PATH`, and `site.export(path, format=None, option=None)` in the public API,
  write the built model for other software. A `.glb` is glTF, for Blender and most 3D viewers: what the viewer shows,
  in its colours and pictures, with the site's grid and origin in its extras so it can be placed again. An `.ifc` is
  IFC4, for architects' and builders' software, through IfcOpenShell (the `ifc` extra): the ground, the house, the
  walls, lawns, features and drains on a site georeferenced to the site's grid, each with a property set of the
  model's numbers. `--option ID` shows a wall option in place of what it replaces, as the viewer does, with its walls
  as IfcWall and its steps as IfcStair in an IFC. docs/export.md says what each holds.
- A wall option's walls carry their toe levels, `toe_min` and `toe_max`, in the model and in `site.evaluate`, as the
  existing walls do.
- The direction gizmo's handles are all one size, W was smaller, and the two facing away keep most of their colour,
  where they were faded to half, so W and S read as clearly as N and E. Domus's copy of three-viewport-gizmo takes a
  `fade` setting for that.

## 2026.11.39

- **For site projects:** how sure a datum's offset and the upper floor's level are, `[datums.<datum>] uncertainty_m`
  and `[house] fl_upper_uncertainty_m`, aren't Domus settings any more: nothing in Domus read them. A site keeps them
  as its own notes under `[local]`, by the same names: `[local.datums.<datum>] uncertainty_m` and `[local.house]
  fl_upper_uncertainty_m`. Sites are in format 4 now. One in format 3 or before that gives them reads with them there,
  with no warning, and `domus site upgrade` moves them there in its file, comments and all. A site's tool that read
  them from `[datums]` or `[house]` reads them from `[local]` now.
- `domus prep boards` reads the weatherboards off a wall's rectified photo (`data/house/faces.json`, from
  `domus prep house-textures`): how far apart their lap lines are up the wall, which is `[house] board_m`, and the
  heights of the lap lines, band by band along the face and as the face's median. Which part of the face it reads, in
  bands how wide, between which heights and over what range of spacings are its arguments. It grew in a real site's own
  tool, and on that site's walls finds the spacing the tool found, face by face, wherever the boards read clearly, so a
  site with its own copy can drop it.
- `domus prep check through` leaves out a wall's or a flight's top where it lies inside another of the option's walls
  or flights, under that one's top, as where a wall's end tucks into a thicker wall at a corner. Nothing shows there,
  but the option's ground inside the thicker wall, which rises from its face to the ground it holds, was reported as
  coming through the tucked end. Ground that comes through just beside the thicker wall is still found.
- `domus shots SPEC.json` takes pictures of the viewer: for each shot in a spec, the site as it is or a wall option,
  from a view button or a camera placed in viewer metres, in the wall options' frame or on the site's grid, with the
  layer groups it names switched off. It serves the model itself for the run, drives the viewer in Playwright's
  Chromium, an optional extra (`uv sync --extra shots`, then `uv run playwright install chromium`), and leaves nothing
  running afterwards; the whole run has a time limit (`--timeout`). The pictures are PNGs with no metadata.
  [docs/shots.md](docs/shots.md) describes the spec, with examples on the made-up demo site. It grew from a real
  site's own scripts. The container image leaves the extra out.
- The viewer's `siteViewer.drawn()` waits until every picture and point cloud the page has asked for has arrived, any
  camera move has finished and the model has been drawn since, for scripts that take pictures of it.
- `domus new` and `domus create` write a `.gitignore` in a new site, so the site can be a git repository of its own.
  Git leaves out what Domus makes again: the built model, the build cache, the checks' reports, the server's logs, the
  copies `domus site upgrade` keeps, the frames taken from videos and the reconstructions' working files. It keeps
  everything a person made, the sketches saved in the viewer (`.domus/viewer.db`) among it. A folder that has a
  `.gitignore` already keeps its own, as `site/` in a copy of Domus does. docs/new-site.md lists what's left out.
- **For site projects:** the paving painted into the aerial photo under trees, `data/site/paved-under-canopy.geojson`,
  is documented in [docs/site-format.md](docs/site-format.md#layers), and what it takes in the photo for paving in sun,
  for foliage and for shade, and how far it smooths and fades them, are settings: `[aerial.paving]` in
  `data/site.toml`. Their defaults are the numbers it always used, so a site builds as before. The file can hold more
  than one polygon, where only its first counted; a site without a picture of its paving (`data/features/paving.jpg`)
  gets the paving's colour without its grain, where its build stopped; and an area where the photo sees none of its
  paving in sun says so.
- The viewer's direction gizmo takes the page's theme: north and south in the accent teal, east and west in amber, up in
  the ink, on the panel's ground, and it changes with the light or dark theme. Its letters are in the display face,
  bolder, and fill most of each circle: Domus's copy of three-viewport-gizmo takes a `font.fill` setting for that.

## 2026.11.38

- **For site projects:** Domus's package is laid out by what each part does: `geo/` (grids, rasters, GeoJSON, meshes,
  fits), `model/` (the ground, house, walls, features, plants, drains), `options/` (the wall options), `build/`,
  `checks/`, `capture/` (videos and scans), `importers/nz/`, `prep/` and `project/` (ARCHITECTURE.md has the table).
  The old import paths, such as `domus.terrain` and `domus.video.georef_sfm`, still work and give the same modules, so
  a site's tools written before keep working.

## 2026.11.37

- **For site projects:** a process can open several sites. `api.open_site` no longer refuses a second one: each `Site`
  reads its own folder, and what Domus reads of a site (its settings, rasters, ground and options) is kept with that
  site, so a tool can open two and compare them. Domus reads `DOMUS_SITE` the first time it needs the site, not when
  its modules are imported.

## 2026.11.36

- **For site projects:** `options/_site.toml` takes a `[local]` table too, as `data/site.toml` does: a site's own notes
  in the wall options' frame, which Domus doesn't read or warn about.

## 2026.11.35

- **For site projects:** `domus prep check` and `site.check()` write their report to the site's `.domus/checks/` (it was `docs/checks/adversarial/`), beside the build cache and out of the site's own documents. A check the site has nothing for is skipped with a note saying why, in the report's `skipped` and in what the command prints: the roof without a house or a surface model, plumb and clouds without placed videos, and through, fixes and blocks without wall options. A site with no house couldn't be checked at all before.
- A site with wall options and no house builds. Working out its options stopped with an error.
- `domus prep cloud` reads a placed video's cloud against the ground, cell by cell over a box in the wall options'
  frame or on the site's grid: the model's ground with its fixes, or the LiDAR's alone. `domus prep cloud-plan` draws
  the cloud from above in its own colours. Both grew in a real site's own tools, and reproduce them cell for cell and
  pixel for pixel.
- `domus prep face-fit` fits a wall's vertical faces in a placed cloud and compares them with the model's lines, and
  `domus prep frame-points` reads where a placed frame's pixels fall on a wall's plane or a level. They also grew in a
  real site's tools, and print what those did, line for line.
- `domus prep overlay` draws the model's parts over frames of a placed clip, through the clip's own cameras: the
  house, the existing walls, the features, the ground fixes, the boundary, the plants and a wall option's walls, each
  only where the camera sees it past the house.
- `domus video two-lens` maps a clip filmed on two lenses again, with a camera for each, from the workspace
  `domus video reconstruct` left: a phone that switches from its main lens to its wide one part way through a take
  breaks up a reconstruction made with one camera. It grew in a real site's tool, and writes what that wrote.
- `domus video sheet` writes contact sheets of a clip's frames, `domus video crop` a piece of a photo at full size, and
  `domus video picks-edit` sets and drops marks in a picks file behind a backup. They match a real site's own tools
  byte for byte.
- **For site projects:** `domus site shift`, `domus site edit` and `domus site regrade` change a site's curated layers
  (move named features by one vector, set features' vertices and properties, give ground fixes a placed cloud's levels)
  behind a backup, a read-back and a check that only what was named changed. They grew in a real site's own tools and
  write what those wrote, byte for byte.

## 2026.11.34

- **For site projects:** the parts of the model that draw the house's looks, the walls and the small features read no site of their own now, and the import rules hold them to it: the build gives them the ground, the settings and the folders of the site's pictures. A site's own code that calls them gives them too. `walls.mesh(wall, ground)` and `walls.set_out(walls, ground)` take the ground (`terrain.ground()`), `walls.texture_file` and `features.texture_file` the folder of the site's pictures, `features.read_all` the site's folder, and the house's drawing a `house_texture.Looks`. `terrain.wall_levels(wall)` reads the site's ground as before, and the models built are unchanged.

## 2026.11.33

- **For site projects:** `domus prep check` checks the wall options too, with three rules that grew in a real site's own tools: `through` finds ground that comes through an option's walls and flights of steps, or stands over a tread's edge, in the built model; `fixes` finds ground-fix points kept inside an option's wall at or above its top, which show through it; and `blocks` finds block walls not set out in whole units, with a cut piece at a stretch's end or a step, or a cut cap, from each wall's own block size. A site that carries its own copies of these checks can drop them.
- `domus prep profile` reads the existing ground along a line and either side of it: a table of levels at stations along a line in the wall options' frame (or with `--grid`, on the site's grid), at the offsets you ask for, and with `--csv`, a file of them too.

## 2026.11.32

- **For site projects:** a site's own code can draw in its builds. `[domus] extensions = ["tools/pegs.py"]` names Python files that define `layers(site)`, which gets the site through `domus.api` and returns layers of the site's own. A build runs them only on a machine that trusts them as they are: `domus site trust` records each file's SHA-256 (`--revoke` drops them), a file changed since is untrusted again, and a build whose extensions aren't all trusted stops and says so. The container image trusts none, the server never imports a site's code, and `build.json` records what ran.
- **For site projects:** a house's looks are the site's choices. `[house.palette]` adds colours of the site's own, by name. A cladding in `[house.cladding]` is weatherboards in a colour, as before, or `plaster:`, `brick:` or `panel:` before a colour. `[house] roof` draws the roof as `tiles`, `metal` (long-run iron) or a `membrane`. A colour Domus doesn't know is refused with a message saying how to name one, and the defaults are as they were.
- `domus prep check` reads the ground in the triangles the build drew. Since 2026.11.29 it cut the ground its old way to find a point's triangle, missed about half of them on a lot, and reported holes in the ground that aren't there.

## 2026.11.31

- **Faster rebuilds:** a build takes from the last one what hasn't changed: the ground's pictures (the aerial photo, the scanned sheets, the relief) and each wall option's layers, each kept with a key made of everything it's built from, the Domus code included. Saving a sketch as an option, or changing one option, rebuilds that option rather than the whole model. The cache is the site's `.domus/build-cache/`, and a cached build is byte for byte the build it saves. `domus build --fresh` takes nothing from it, and a site folder Domus can't write to builds without it.
- **For site projects:** a site's tool can add layers of its own as data, in `data/layers/<name>.json`: meshes, lines and labels on the site's grid, under ids that start `site-`. The build places them in the model as they are. [docs/model-format.md](docs/model-format.md) documents what the build writes and the viewer reads, and `model.json` now says its format, 1.

## 2026.11.30

- **Addresses anywhere:** `domus create` and the address box look for an address in New Zealand first, then anywhere in OpenStreetMap. A place outside New Zealand goes on its UTM zone, with OpenStreetMap's buildings round it and its ground from the Copernicus ground model (GLO-30), which covers the world's land on a 30 m grid: the lie of the land, much rougher than LINZ's LiDAR, and no aerial photo. `domus fetch` gets a site's rasters from the source for where it is: LINZ in New Zealand, Copernicus elsewhere.
- Importers bring a place in (`domus.importers`): New Zealand's, anywhere's, and your own files (`domus new`). Each writes an ordinary site folder, with `[domus] importer` saying which, and the build never calls one.
- **For site projects:** sites are in format 3. `[crs] epsg`, `[crs] vertical` and `[site] attribution` have no New Zealand defaults any more: a site in format 2 or before that leaves them out gets NZTM2000, NZVD2016 and LINZ's credit line, as it always did, and `domus site upgrade` writes them in. A format 3 site without `[crs] epsg` is refused with a message saying what to add.
- The "Improve this model" checklist names New Zealand's documents and services (a LIM, LINZ's title search, beforeUdig) only for a site in New Zealand, and says what to look for anywhere else.
- `domus create ADDRESS PATH` builds into PATH's own `viewer/data`, as `--site` does. In the container it used to build into the mounted site's model, whatever PATH was.

## 2026.11.29

- **Sites anywhere:** Domus reads each raster on its own grid, the one its file names, so a site's ground model, surface model and photos can come on any grid. A file that names none is taken to be on the site's, and heights in a band that says it's in feet are read in metres. Domus used to take every raster to be on NZTM2000.
- `domus new` puts a site on a grid in metres. A boundary on one stays on it; one in longitude and latitude goes on NZTM2000 in New Zealand and on its UTM zone anywhere else; one on a grid in feet goes on the UTM zone. `--epsg` names another grid in metres. Away from New Zealand it leaves the height datum and the credit line for the site's own data to fill in, and `domus fetch` says that LINZ covers New Zealand only and what to bring instead. A site can leave its height datum unnamed: the viewer then calls its heights the model's.
- The same site on any grid builds the same model, as the build now works from the site's origin. The ground is triangulated about it, and each whole grid cell is cut along the diagonal whose corners are closer in height, so a bank is drawn down its slope. A cell's corners lie on a circle, so it could be cut either way, and which way used to depend on where the site sat on its grid. A site built before has some cells cut the other way now, its points unchanged: on the made-up sites the ground between points moves by up to 6 mm, and an option's designed ground by up to 8.5 cm in cells it bends sharply across, and on steep banks the middle of a cell can move by tens of centimetres. CI builds the made-up demo moved onto the British National Grid and holds its model to the demo's within 1.5 mm.
- A new lawn or bed in an option has its grass or soil tiled from the viewer's origin. It was tiled from the grid's own, millions of tiles away, where the viewer's numbers can't place a texture within a tile, which smeared it.
- A lawn's picture comes out the same size whatever the arithmetic's last digits are.

## 2026.11.28

- **For site projects:** every setting Domus reads from `data/site.toml` and `options/_site.toml` is declared in one place, with its default and a line on what it is, and [docs/site-format.md](docs/site-format.md#settings) lists them all from there. A key Domus doesn't know gets a warning naming the nearest one it does, such as `house.boards_m isn't a setting Domus knows: did you mean board_m?`, where it used to do nothing without a word. Settings only your own tools read go under `[local]`, which Domus leaves alone.
- `build.json` says which Domus built the model, and the model's format. A model an older Domus built is built again the first time its site is opened, one build at a time, and the old model shows until the new one is ready, so there's no `domus build` to run after an upgrade. If that build fails, the old model stays, `.domus/build.log` says why, and the server tries again only when it's restarted.
- CI checks the package's import rules (`uv run lint-imports`, set in `pyproject.toml`): the server imports only the standard library, the model's pure parts (house, mesh, wall looks, drains) import nothing that reads a site, and nothing imports the command line or the server. The tests run with `DOMUS_STRICT_SETTINGS=1`, under which reading a setting the schema doesn't declare, or with another default than the schema's, is an error.

## 2026.11.27

- **For site projects:** sites have a format, now 2: `[domus] format` in `data/site.toml`, which `domus new` and `domus create` write. Domus reads an older site as it is, migrating it in memory, and always will, so nothing has to be run in a container. `domus site upgrade` writes the migration into the site's files, keeping their comments, after copying them to `.domus/upgrade/`. `[domus] requires`, such as `">=2026.12.0"`, names the oldest Domus that builds a site, and a site in a newer format than Domus reads is refused with a message saying so.
- **For site projects:** in format 2 a compass word in an option means the site grid's north, as it already did in sketches and house parts, and a lawn's `fall_to` can be a bearing in degrees too. In format 1 the words ran along the options' frame; an older site's are read that way still, and `domus site upgrade` writes them as words or bearings on the grid. `[front_garden]` in `options/_site.toml` is `[regrade]`, and `[house] fl_lower` is written down rather than worked out from `fl_upper_rel` and `fl_lower_rel`.
- A drain's levels are worked out once, for the pipe the viewer draws and the options' checks alike: the levels from one document (their `source`) count over their own stretch, and the higher counts where documents overlap. `[services] printed_line` has gone. A drain's `follow_up` in `services.geojson` says what's known to be wrong with its levels: the viewer shows it on the pipe and in the profile tool, and every option's check of that drain repeats it. A level point without a `label` shows its invert instead of stopping the build.
- A wall with posts, such as timber post and board, can end without one: `open_ends` (`start`, `end`) in `walls-existing.geojson` names an end where its boards run on, as behind another wall. The boards there keep their true size from the last post, and the wall's end shows the ends of its boards. Such an end used to get a post that isn't there, and a whole bay squeezed into its last stretch.

## 2026.11.26

- The image runs on Python 3.14 (was 3.13), and so does a checkout's default `uv` environment. 3.13 still works.
- The viewer runs on three.js r187 (was r170), with three-mesh-bvh 0.9.18. Shadows use PCF filtering, as soft PCF is gone from three.
- The camera is driven by camera-controls instead of three's OrbitControls. Dragging, panning along the ground, the wheel, pinching, the navigation pad, the keys, double-click and saved views work as before, and drags and zooms ease to a stop.
- A view gizmo at the top right shows north, east, south and up: click a side to look from it, or drag it to turn the view.
- A selected sketch wall or level area has a ring to turn it about its middle in plan, in 5° steps (Alt for any angle), with undo.
- In a copy of this repo, `site/` is where one property's project lives. Git ignores everything you add to it, so a property's data, settings, options, tools and tests can't be published by accident. It comes with a README, notes for agents, an example tool and example tests to copy. `sites/<name>/` is ignored too, for more than one property. [ARCHITECTURE.md](ARCHITECTURE.md) says what belongs in Domus and what belongs in a site.
- `domus new` and `domus create` write the site folder's format, `[domus] format = 1`, in `data/site.toml`, so later versions of Domus can tell how to read it. Sites without it are read as before.
- The options' shared settings no longer suggest `driveway_so` for a surcharge check: Domus has no such check, and the setting did nothing.
- The server sends big text files gzipped to a browser that takes them: the demo's 2.1 MB `model.json` goes as 0.5 MB, and the viewer's three.js as a fifth of its size. Each file is compressed once, and again only when it changes.
- Every text file is read and written as UTF-8, so a name with a macron reads the same on Windows as on Linux and macOS.
- CI builds three made-up sites with a pull request's base and with its head, and says what moved (`tools/made_up_sites.py`), so a change to the build can be proved without a real site.
- **For site projects:** `domus.api` is Domus's public API, for site tools, a site's own tests and agents: `open_site`, and a site's `lot()`, `house_walls()`, `layer()`, `ground()`, `options()`, `frame()`, `evaluate()`, `build()` and `check()`, with `resample_line`, `fit_affine`, `linz_query` and `clip`. `domus.testing` gives a site's tests `site` and `built`, which builds the model out of the way of the one a server shows. What they offer changes only after a deprecation in an earlier release; the rest of the package can change in any release. [docs/api.md](docs/api.md) has it all, and the examples in `site/` use it.
- **For site projects:** Domus's functions raise `DomusError` (`domus.api.DomusError`) for something a person can put right, such as a missing setting or a scan in a format it can't read, instead of ending the program. The command line shows the same messages as before. A site tool that caught `SystemExit` from one of them catches `DomusError` instead.
- `domus marks` and `domus title` search round the lot `[boundary] main` names, as the build does. They took the first feature in `boundary.geojson`, which could be a neighbour or a right of way.
- A sketch that can't be worked out or saved because of one of the site's settings says which. The sketch worker's own errors go to the server's log instead of being thrown away.

## 2026.11.25

- The house's parts are general. Each feature in `house.geojson` besides the walls and openings says what it is in `part`: `bay`, `porch`, `steps`, `block` (decks, landings, steps, planters, units on the ground), `wall`, `balustrade`, `chimney`, `tank` or `retaining-walls`, with its sides, heights, colours and viewer layer in its own properties ([docs/site-format.md](docs/site-format.md#house-parts)). A porch can stand on any side of a house with walls on whichever sides it has, and steps can face any way. **A site made before this needs its parts given a `part`**; the build names any that have none, and leaves them out.
- `[house.trim]` sets a band at the floor line and facings round the lower windows on the faces it names, `[house.cladding] default` the cladding of faces not named, `roof_colour` the roof's colour, and `[house.layers]` the names and colours of the house's layers. Colours can be hex.
- Wall options regrade `[regrade]` in `options/_site.toml`: the whole lot unless `s_max` or `polygon_so` narrows it. `[front_garden]`, its old name, still works.
- The model's grid label comes from the site's own grid when `[crs] label` isn't set, instead of always saying NZTM.
- Licence changed from AGPL-3.0-or-later to MIT.
- PDF plans are read with pdfplumber (MIT) instead of PyMuPDF (AGPL), so the image has no AGPL code in it. `plan-pages` and `plan-trace` give the same page images and linework as before; label positions can move by up to about 4 cm on the ground, and text at an angle is read as whole words as before.

## 2026.11.24

- Where a capped block wall's top steps one course along a straight stretch, and two capping units make a course, the step is capped as a stair: two capping units stacked beside the end of the higher course, with the higher capping course lapping half a unit over them. The end of the higher course used to show above the lower capping course.

## 2026.11.23

- A flight on ground that slopes across its foot (`foot` with two levels) has every tread the full width. Its lowest tread is level with the ground at the high end of the foot line, so it is flush there and stands the ground's whole rise above it at the low end, and the risers above it are equal. The lowest tread used to run only part of the way across, with the tread above carried out to the foot line beside it, which read as half steps.
- Where a block wall's top changes level at a corner, the capping units of the two walls each end there, as at a step. One used to be run round the corner as if both were at one level.

## 2026.11.22

- A block wall's capping course is laid in capping units, as long as its blocks: whole ones in from both ends of each length of top, between corners, steps and ends. At a corner the unit of one face runs through to the far side and the other face's butts against it. Where whole units do not reach, the one cut to fit is in the middle of the length, or two share it so that neither is a sliver. The capping course used to repeat a quarter of a block off the blocks' joints, which left short pieces of cap at corners, at steps in the top and at a wall's ends.
- A bed or a lawn of an option is drawn right up to another of its beds or lawns that lies against it. Each was drawn a little inside its own outline, so a strip of bare ground showed between two beds that meet.

## 2026.11.21

- In an inside corner of a wall of a named block (`unit_m`), the blocks of the two faces butt turn about from course to course: one face shows a whole block at the corner and the other half of one, as a wall built of whole blocks does. The pattern used to run straight on round an inside corner, which put two whole blocks or two half blocks against each other in every course. Where whole blocks cannot reach from an inside corner to the outside corner beyond it, that corner keeps its joint, the cut block stands in the inside corner, and the wall's checks say how far to move a corner.

## 2026.11.20

- Weatherboards are drawn at the site's own size and counted up from the top of the plastered base, where the lowest one sits: `[house] board_m` gives the distance from one lap to the next (0.15 m unless given). They used to be 0.15 m everywhere and counted from the floor line, so they could not be used to check a height. A lap's shadow line is at least a pixel thick, so it stays a line.
- `[house] boards_on_photos = true` draws the boards over the walls' photos too. Where the boards show, the photo's own lap lines are smoothed away and the laps drawn in their place, with the photo's colours and light kept. Windows, doors, facings, corner boards and the plastered base are left as the photo has them. The boards can then be counted anywhere on the house, and a photo that was squeezed or stretched when it was rectified shows up against them.
- A raster can be moved from where its file puts it: `[rasters.shift_m]` with `aerial = [east, north]` in metres (or `nir`, `dem`, `dsm`), and `shift_m` on a `[[drapes]]` sheet. For an aerial photo or a surveyor's sheet that sits a little off the ground model and the house.

## 2026.11.19

- A wall of a named block (`unit_m`) is set out as it would be built. Its courses are level on one grid of levels: they run through where its top steps and where it stands against another wall of the same block, and each top has its own capping course. They used to hang from each wall's own top, so two walls whose tops were not a whole number of courses apart missed each other at a corner. At each outside corner stands a corner block, a whole block's length on one face and half a block's on the other, turn about from course to course. The pattern used to run round the corner unbroken, which cut the corner blocks to odd lengths. A wall that carries on along another's face takes up its bond.
- A block wall's checks say what does not fit: a top that is off the courses of the wall it stands against, a top that steps by a part of a course, and a corner the blocks reach off a joint, with how far to move it.
- The ground keeps its level up to the foot of a wall's face and up to its back, and lawns and beds run right up to the wall. The step in the ground used to start 2 cm outside the wall on each side, which showed as a thin strip of bare ground along its foot.
- A lawn that an option leaves alone is drawn as its grass without the bare patches it has today, as a new lawn beside it already was. Beds and their plants are not drawn with an option, so the soil under a clump of weeds showed as a bare spot in the lawn.

## 2026.11.18

- A flight of steps can stand on ground that slopes across its foot: `foot = [left, right]` gives the ground at the two ends of the foot line. The treads stay level and the foot line straight. The lowest tread runs in from the low end as far as it stands clear of the ground, and the tread above is carried out to the foot line from there. A wall up the side of such a flight is measured from the ground under it at the wall's own face.
- An option keeps the lawns it leaves alone: a lawn that is there stays drawn as it is, on the option's ground, where the option does not build on it. It used to disappear with the option shown, which left the bare aerial photo. A new lawn beside it is drawn as the same grass. The viewer no longer names a lawn's layer itself: the lawn's layer lists the options that redraw it (`redrawn_by`).

## 2026.11.17

- A wall can name the block it is built of: `unit_m` (one block's face), `cap_m` (a capping course) and `colour`. It is drawn in those blocks at their true size, split-faced and in running bond, with the cap along its top.
- Steps can be railway sleepers: `material = "sleepers"` draws a flight as two timber sleepers to a tread. `[[restyle_stairs]]` restyles the steps of an option based on another, as `[[restyle]]` does its walls, and a restyle that changes a wall's material drops the block the base named.
- The ground no longer cuts through an option's walls and steps. A wall keeps its thickness round a corner, and its back no longer folds on the inside of one. The ground has a clean edge round a flight of steps and runs level to the house's walls. Lawns and beds stop at the walls and steps that stand in them, and contours are not drawn across them.
- The ground mesh is triangulated about its own middle. In whole grid coordinates, points a few centimetres apart could be lost.
- An option regrades under a bay window, which hangs from the wall above the ground. It used to leave a pit there.
- A terrace of kind `ground` regrades without a lawn or a bed drawn over it, for making the ground good round steps.

## 2026.11.16

- A wall's top can be level in bays that step: `tops` gives a level for each stretch of its line, and the top drops straight down between them. One wall now does what took a wall per bay.
- Timber walls have their posts: one at every point of the wall's line and between them where the boards' picture repeats, standing out from the boards and a little above them.
- Behind an option's wall the ground is the ground it holds. A wall standing above it, as a level bay does over falling ground, no longer pulls the ground up to its top.
- An option's new beds are drawn planted, as bark mulch with clumps of low plants, in place of bare soil and leaves.
- The viewer has a button for each wall option over the model, so any scheme is one click from any other whatever panel is open, and `[` and `]` step through them.

## 2026.11.15

- Wall options can have steps: a `[[stairs]]` entry is a line and a width, and the flight takes its levels from the ground at each end. It's drawn in the model, its risers and goings are in the option's card, and steep ones are flagged.
- An option can take site features out (`remove_features`), such as old steps a scheme replaces: they are hidden while it is shown.
- An option can be another one restyled (`based_on` and `[[restyle]]`), to compare the same scheme in timber, block, gabion or stone without copying it.
- Every wall material has a picture: segmental block, gabion, crib and concrete are painted by the build, and so are timber, block and stone on a site with no photo of its own.
- Ground filled against the house is reported as that, not as an edge needing a wall, and the check now covers a house standing clear inside the lot. An existing wall an option keeps no longer counts as an unheld edge.
- Drafted houses are the right size. LINZ's building outlines trace the roof's edge, so the walls now stand the eaves' 0.4 m inside the outline instead of on it, with the roof another 0.4 m beyond.
- `domus fetch` also gets LINZ's near-infrared photo where there is one, and the canopy map uses it: living leaves show up in near-infrared, so green roofs and paint no longer count as trees. Without one it goes by colour as before.
- `domus points` gets the classified LiDAR point cloud over the site: from LINZ's export API with a key that has the exports scopes, else from OpenTopography's public copies, which lag LINZ. `domus create` fits the drafted roof to its building points, and `--ground` writes a 0.5 m ground model from its ground points for a site to choose.
- `domus fetch` skips a survey whose tiles reach the site but hold no data there, and takes the next. Some sites near a survey's edge got an empty ground model and a build that failed.
- `domus title` adds the lot's title (number, estate, type) and the easements and covenants on or near it, from LINZ. They're drawn in the Boundaries group, and wall options flag a wall that runs into an easement. `domus create` runs it when there's a LINZ key.

## 2026.11.14

- The Domus logo is the browser tab's icon and the container's icon in Unraid.

## 2026.11.13

- 2026.11.12 published no image because its build failed. This release carries its changes, and images now come with a software bill of materials.

## 2026.11.12

- Safer on an open network: the server answers only to IP addresses, localhost and names in the new `DOMUS_ALLOWED_HOSTS`, so **if you reach Domus by a host name, add it there**. It sends a content security policy, treats every name and note as text, refuses uploads that would nearly fill the disk, and caps sites at `DOMUS_MAX_SITES`.
- Video caches are read without running anything in them.
- `domus video plot` works on any site: it frames the site's boundary.
- The README and docs catch up with the app: many sites, sketches as options, roof forms, no cost rates.
- A `SECURITY.md`, Actions pinned to exact commits, CodeQL, and images with a software bill of materials, signed once the repo is public.

## 2026.11.11

- The viewer no longer shows a hint over the model when no tool is in hand.

## 2026.11.10

- The image is published as `ghcr.io/populii/domus`.

## 2026.11.9

- The measuring tools moved into the Measure panel and the drawing tools into Design. The prompt over the model names the tool in hand, with Done to put it down.

## 2026.11.8

- A new layout for the viewer. The tools sit in one bar over the model, and a rail opens one panel at a time (View, Measure, Design, Site) in place of the single long list. Clicking the model shows a point's details on a card over it. Layer groups fold and have a switch for the whole group. The title block runs along the foot, and the keys are under Help, or `?`. On phones the rail sits at the bottom and the model starts at full size.

## 2026.11.7

- Removed cost estimates and consent flags. They weren't based on real data. Options compare how a scheme looks and its cut and fill.
- Walls that shape the ground: say which side a sketched wall retains and how far back, and the viewer shows the ground it makes, with cut and fill as you go. **Make it an option** saves the sketch as a wall option and builds it.

## 2026.11.6

- Exact drawing: walls, areas and measurements snap to the boundary, house, existing walls, survey marks and your own sketch; line up with the site's directions; lock a direction with Shift; and take a typed length, or a length and bearing. Corners can be set by number, and the sketch has undo and redo.

## 2026.11.5

- Gable roofs: `roof_form = "gable"`, with the ridge's bearing. `domus create` picks a gable or skillion when it clearly fits the LiDAR better than a hip.

## 2026.11.4

- The site list and address pages take the viewer's look, in light and dark, and the viewer has an All sites link back to the list.

## 2026.11.3

- Skillion roofs: `roof_form = "skillion"` in `[house]`, and `domus create` picks one when a single plane clearly fits the LiDAR better than a hip.

## 2026.11.2

- One container can hold many sites: set `DOMUS_SITES`, and `/` lists them with an address box for a new one. A one-site container works as before.

## 2026.11.1

- `domus marks`: LINZ's survey marks on the lot's line as a site layer, and the nearest height marks.
- `--site` now moves a build's output too, so trying something on a copy inside the container can't overwrite the live model.
- Addresses with macrons no longer crash `domus` on a Windows console.
- Labels say what a public-data model really has: other buildings aren't "sheds", and walls and trees no longer mention photos it doesn't have.
- `AGENTS.md`, from a test run on a real address: working in a container, layer names, older surveys and near-infrared, and reading a roof check when the roof isn't a hip.

## 2026.11.0

- `domus video auto`, started by the scan page: from a scan to the model with no hand work, roughly placed using AR positions.
- Calendar versions from here on: YEAR.MONTH.COUNT.

## 0.5.0

- Guided scanning at `/scan`: target dots, route, speed and blur warnings and upload from a phone, with position tracking through WebXR on Android.
- Licence changed from MIT to AGPL-3.0-or-later.

## 0.4.1

- When an address matches more than one place, Domus asks which, instead of taking the first. Matches the whole number and road, so 2 Main Road no longer finds 12 Main Road.

## 0.4.0

- The viewer's "Improve this model" panel: a checklist of further sources in order of value, and a recommended video walk.
- `domus create`, and an address box in the web app: a model from an address and public data alone.
- `domus prep`: plans onto the grid, IFC models, textures from photos, model checks and cross-sections, all driven by `data/prep.json`.

## 0.3.0

- Renamed from sitemodel to Domus: the command, package, image and `DOMUS_*` settings, and `.domus/` for saved sketches.
- Phone LiDAR scans: `domus scan` reads PLY, LAS, OBJ and text exports and places them on the grid.

## 0.2.0

- Video reconstruction is built in: `domus video`, in the image too.

## 0.1.0

- First version as an app: any New Zealand site from a boundary and LINZ's open LiDAR and aerial data.
- `domus` command: `new`, `demo`, `fetch`, `canopy`, `build`, `serve`.
- Viewer with its libraries bundled, so it makes no outside calls; sketches saved on the server.
- Container image, Compose file and Unraid template.
