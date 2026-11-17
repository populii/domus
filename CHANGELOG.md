# Changelog

## Unreleased

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
