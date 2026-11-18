<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/images/domus-logo-dark.svg">
    <img src="docs/domus-logo.svg" alt="" width="112">
  </picture>
</p>

<h1 align="center">Domus</h1>

<p align="center">
  <strong>Type in an address. Get a 3D model of the place in your browser.</strong><br>
  Then try retaining walls on it, and see what each one does to the ground, before anyone draws anything.
</p>

<p align="center">
  <a href="#try-it">Try it</a> ·
  <a href="#screenshots">Screenshots</a> ·
  <a href="#how-a-model-comes-about">How it works</a> ·
  <a href="#what-you-can-do-with-it">What it does</a> ·
  <a href="AGENTS.md">For agents</a> ·
  <a href="CONTRIBUTING.md">Contributing</a> ·
  <a href="#the-docs">The docs</a>
</p>

![A made-up site in the viewer: a small house on a slope, a lawn held up by two low block walls with a planted bed between them, trees and a fence](docs/images/hero.png)

<p align="center"><sub>The made-up demo site with scheme B: two low block walls and a planted bed. Every picture of a site in this repo is of a made-up one. None is a real place.</sub></p>

Domus grew out of a very ordinary problem: a sloping section, a tired retaining wall, and no good way to picture what
could replace it. Drawings cost money, and it's hard to argue about a wall you can't see. It turns out New Zealand
publishes a remarkable amount of open data about its land, so Domus pulls that together into a model you can walk
around, measure and try ideas on. Then it keeps getting better as you add what you know: a walk round the place with
your phone, a few tape measurements, the plans in the council's file.

It runs on your own machine or home server, and your site's files stay in a folder you control.

## What it's good for

- **Seeing your place before anyone draws anything.** The ground, the house, the trees and the walls you have, in 3D,
  from public data in a few minutes, and better with every walk, measurement and document you add.
- **Comparing retaining wall schemes.** Sketch one on the model, or write it as a small text file, and Domus works out
  the ground it makes, how high each wall stands, how much soil moves, how much flat lawn you gain, and what it takes to
  build.
- **Knowing what to ask.** It flags a wall in an easement, a drain left with too little cover, a footing beside a post or
  a lid, ground lowered beside the house or raised against it, steps that are too steep, and more.
- **Talking to the people who'll do the work.** Pictures of each scheme from the same spots, and the model as glTF or
  IFC for a designer's software.

> [!WARNING]
> **Domus is a planning aid, not a survey.** Its numbers come from public LiDAR, your phone and your tape measure. It
> helps you ask better questions. An engineer, a surveyor and your council still get the last word, and Domus doesn't
> price a job or say what needs consent.

## Screenshots

<table>
<tr>
<td colspan="2"><img src="docs/images/screenshot-site.png" alt="The richer made-up site from above the drive: a house with a porch and steps, a chimney and a water tank, an old block wall across the lawn, trees along the drive, and a fence"></td>
</tr>
<tr>
<td colspan="2" align="center"><sub>The richer made-up site as it is: the house with its porch, chimney and tank, an old block wall across the lawn, trees along the drive, and a fence.</sub></td>
</tr>
<tr>
<td width="50%" valign="top"><img src="docs/images/screenshot-cut-and-fill.png" alt="Scheme B on the made-up demo with its ground coloured by cut and fill: the lawn and the bed behind its two block walls in blue where soil is brought in"></td>
<td width="50%" valign="top"><img src="docs/images/screenshot-block-walls.png" alt="Scheme D on the richer made-up site: two retaining walls of a named block, laid course by course under capping units, with a planted bed between them and the house behind"></td>
</tr>
<tr>
<td align="center"><sub>Scheme B on the demo, its ground coloured by its cut and fill: blue where soil is brought in, stronger the deeper.</sub></td>
<td align="center"><sub>Scheme D on the richer site: two walls of a named block, laid course by course under their capping units.</sub></td>
</tr>
</table>

## How a model comes about

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/images/story-dark.svg">
  <img src="docs/images/story-light.svg" alt="Eight steps: an address, public data, a phone walk, measurements, the model, schemes side by side, cut and fill, and checks.">
</picture>

A model starts rough and gets better with everything you feed it. Here's the whole journey, one step at a time.

### 1. An address

`domus create "12 Example Street, Christchurch"` finds the place: the address, the legal parcel and the buildings round
it. It starts a **site folder** for the property, an ordinary folder of text files and pictures you can read, edit and
back up. If the address could be in more than one town, Domus asks which you meant.

<details>
<summary><b>What <code>domus create</code> does, step by step</b></summary>
<br>

```mermaid
flowchart TD
  A([An address]) --> B{Found in New Zealand's<br/>data, with a LINZ key?}
  B -- yes --> C[LINZ: the parcel and<br/>the buildings]
  B -- no --> D[OpenStreetMap: the address<br/>and buildings, a stand-in lot]
  C --> E[(A site folder)]
  D --> E
  E --> F[The ground, surface<br/>and photo]
  F --> G[New Zealand: the title,<br/>easements and point cloud]
  G --> H[Drafts: the house,<br/>neighbours and trees]
  H --> I[domus build]
  I --> J([The model in<br/>your browser])
```

- In New Zealand, the ground, the surface and the aerial photo come from LINZ's open LiDAR and imagery. Elsewhere the
  ground comes from the Copernicus 30 m ground model, and there's no photo.
- With a LINZ key, Domus also adds the lot's title and the easements near it, and in New Zealand it tries for the
  LiDAR's point cloud, to fit the roof better.
- The house is drafted from its outline, with its height and roof fitted to the LiDAR (a hip, a gable or a skillion).
  Neighbouring buildings become plain blocks, and trees come from the LiDAR's canopy and the photo.
- Stopped part way? Run the same command again and it carries on.

</details>

### 2. Public data

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/images/public-data-dark.svg">
  <img src="docs/images/public-data-light.svg" alt="Four layers stacked like slabs: an aerial photo, a surface model of roofs and trees, the bare ground with contours, and the parcel with the buildings' outlines.">
</picture>

New Zealand is lucky here. LINZ publishes 1 m LiDAR for much of the country: a **ground model** (the bare earth), a
**surface model** (the tops of roofs and trees), and sharp recent **aerial photos**, all free. Domus takes the newest of
each over your site, just the patch it needs, and notes what it used and how old it is.

Anywhere else, the start is rougher: OpenStreetMap's buildings, a 30 m ground model, and a square stand-in for the lot
until you draw the real one. Bring your own LiDAR and photos, on any grid, and the model is as good as New Zealand's.

### 3. A phone walk

LiDAR is great on open ground and pretty useless under a tree. It also can't know about anything built or dug since the
survey. A slow walk round the property with your phone fills those gaps, and it's the single most useful thing you can
do yourself, today, for free.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/images/video-walk-dark.svg">
  <img src="docs/images/video-walk-light.svg" alt="Left: a plan of a lot with a suggested walking route round the edge, to the trees and a steep bank, then round the house. Right: five steps from filming to a point cloud in the model.">
</picture>

Domus suggests a route for your site: round the lot's edge, a detour to each place the LiDAR sees worst (under the trees,
along steep banks), then a loop round the house. Show it on the model, walk it, and Domus turns the video into a cloud
of 3D points on your site's grid, to measure against and fit the model to.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/images/viewer-walk-dark.webp">
  <img src="docs/images/viewer-walk-light.webp" alt="The viewer's Site panel: a checklist of sources that would improve the model, and the recommended video walk drawn on the model in pink.">
</picture>

<p align="center"><sub>The viewer's <b>Improve this model</b> panel, with the suggested walk drawn on the made-up demo.</sub></p>

Open your site on your phone, over https, and the same panel has a **Scan with this phone** button: a guided page that
films the walk and uploads it for you. [docs/video.md](docs/video.md) has the whole recipe, including what to do when a clip falls
apart, which they do.

### 4. Measurements

A tape and a level are still the most accurate tools you own. A handful of measured heights, on the walls and the
ground that matter, ties everything else down. They go in the site's layers as plain GeoJSON, with a note of where each
number came from and how far to trust it.

You can edit those files by hand, in a text editor or QGIS. Or let Domus make the change, with `domus site edit`, an
agent or a tool of your own: then it's checked, recorded with who made it, when and why, and can be undone. When a
check or an agent finds something that should change, it proposes it, and you accept or reject it, on the command line
or in the viewer's **Site** tab. Nothing changes itself. [docs/changes.md](docs/changes.md) has the details.

### 5. The model

Everything comes together in one page in your browser: the ground with the photo draped over it, the house, existing
walls, drains at their depth, fences, trees, lawns and beds, and the point clouds from your walks. Every part says where
it came from and how sure it is, so you always know what you're looking at.

![The house on the richer made-up site: a porch with steps and a lean-to roof, a door and window, a chimney, a water tank, a deck with a balustrade, and an old block wall in front](docs/images/house-parts.png)

<p align="center"><sub>The house on a richer made-up site: a porch with its steps, a chimney, a tank, a deck with a balustrade, and an old block wall.</sub></p>

The house can also be kept as an IFC file, the format designers' software opens. `domus house ifc` writes it, and with
`[house] source = "ifc"` it becomes the house's one source: the model, the schemes and the checks all read the house
from it, and it changes the same recorded, undoable way as everything else.
[docs/site-format.md](docs/site-format.md#the-house-as-an-ifc-file) says how.

### 6. Schemes side by side

Now the fun part. A retaining wall scheme is a small text file: where each wall goes, what it's made of, and the level
of each lawn or bed it holds. Or just sketch one on the model in the viewer and press **Make it an option**. Domus works
out the ground each scheme makes, and you flick between them.

| The ground now | A. One low wall | B. Two tiers |
|:---:|:---:|:---:|
| ![The made-up demo as it is, with an old block wall across the lawn](docs/images/compare-now.png) | ![Scheme A: one timber wall holding a level lawn](docs/images/compare-a.png) | ![Scheme B: two block walls with a planted bed between them](docs/images/compare-b.png) |

<details>
<summary><b>What a scheme looks like as a file</b></summary>
<br>

Scheme A on the made-up demo, `options/a-one-wall.toml` in its site folder. Positions are metres along one boundary
of the lot (`s`) and in from it (`o`), so you can read them off a tape:

```toml
name = "A. One low wall"
short = "A. One wall"
description = "A single timber wall holds a level lawn."
steps = 0
keep_existing = []

[[walls]]
id = "A1"
name = "Lawn wall"
material = "timber"
line_so = [[8.0, 2.0], [8.0, 20.0]]

[[terraces]]
id = "lawn"
name = "Lawn"
kind = "lawn"
level = 42.0
at_so = [11.0, 11.0]
fall = "1:40"
fall_to = "north"
batter = 2.5
polygon_so = [[8.0, 2.0], [8.0, 20.0], [14.0, 20.0], [14.0, 2.0]]
```

Walls can be timber, block, segmental, gabion, crib, stone or concrete, and a named block is laid out course by course
at its real size. A wall can also say how it's built, the way its maker's guide does: how deep it is, how far it goes
into the ground, its base course and bedding, and the drainage behind it. Domus draws it that way and lists what each
scheme takes to build, to order from: blocks and capping units, aggregate, pipe, sleepers, paving and soil, with no
prices. A scheme can be based on another and restyled, to compare the same layout in different walls.
[docs/options.md](docs/options.md) has all of it.

To take a scheme into other software, `domus export option-a.glb --option a-one-wall` writes it as glTF for Blender and
3D viewers, and `.ifc` as IFC for a designer's software, with each wall's numbers, every wall and flight of steps a
closed solid. [docs/export.md](docs/export.md) says what each holds.

</details>

### 7. Cut and fill

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/images/cut-and-fill-dark.svg">
  <img src="docs/images/cut-and-fill-light.svg" alt="A cross-section of sloping ground: a level lawn cut into the high side and filled on the low side, held by a retaining wall, with a batter where no wall holds it.">
</picture>

For every scheme, Domus works out how much soil moves (cut and fill, with a range from the LiDAR's tolerance), how
high each wall stands, and how much flat lawn you gain. Tick **Colour the ground by cut and fill** in the viewer to
see where, as in the [screenshot](#screenshots) of scheme B. It measures from the best ground there is today, as the
model draws it: your measurements and placed walks where you have them, the existing walls' faces and tops, and the
LiDAR only where nothing better is known. Only soil counts, measured as solid shapes of what's dug out and brought in:
a new wall itself and paving aren't soil, so they don't count.

Here's how the made-up demo's two schemes come out, as its build works them out:

| | The ground now | A. One low wall | B. Two tiers |
|---|---:|---:|---:|
| Flat lawn | 5 m² | 91 m² | 164 m² |
| Walls | one old wall, 18 m | one timber wall, 18 m | two block walls, 36 m |
| Highest wall face | 0.75 m | 0.70 m | 0.85 m |
| Cut / fill | | 0.9 / 23.4 m³ (±18) | 0.2 / 75.5 m³ (±38) |
| Things to check | | 1 | 3 |

### 8. Checks

Domus flags what to look at before anyone digs: a wall running into an easement, the cover left over a drain after a
cut, a wall crossing a drain, a footing close to a post, pole, survey mark or inspection lid, ground lowered next to the
house or raised against it, steps that are too steep, and regrading that ends in a step at the edge of the area you let
it change. On the demo, both schemes run into a made-up easement, and scheme B's regrading meets the edge of its area
with an 18 cm step.

For each scheme it also checks how the model draws it: ground showing through a wall or a step, a measured ground
level that lands inside a new wall, and block walls that need blocks cut to fit.

It checks the model itself too: things floating or buried, spikes and holes in the ground, a roof that doesn't fit the
LiDAR, and clouds that are tilted or off the ground. Readings you take off your videos, such as the top of a wall or a
door sill, can be kept as observations: every build sets each one against the part of the model it checks and shows
it in green, amber or red, and `domus site propose` turns one into a proposed change to the model, for you to accept or
reject. A check that fails is a question to answer, not a box to tick.

## What you can do with it

<table>
<tr>
<td width="50%" valign="top">

**Look around**

- Orbit, pan and zoom, or jump to the overview, a plan and views you save.
- Drape the ground with the aerial photo, a scanned plan or plain relief.
- Turn the ground see-through to look at drains at their depth.
- Exaggerate the vertical scale to read gentle slopes.

</td>
<td width="50%" valign="top">

**Measure**

- Distances, paths, areas and heights.
- A ground profile along any line you draw.
- Each layer's note says how far to trust a measurement on it.
- Go to a coordinate, and read a point's grid position and height.

</td>
</tr>
<tr>
<td valign="top">

**Design**

- Sketch walls and level areas right on the model.
- Snap to the boundary, the house, existing walls and survey marks; type a length, or `3.2@45` for a bearing too.
- See the ground a sketch makes as you draw and drag it, worked out as solids, with its cut and fill, and save it as
  an option.
- Flick between schemes with <kbd>[</kbd> and <kbd>]</kbd>.

</td>
<td valign="top">

**Share and keep**

- `domus shots` saves pictures of each scheme from the views you choose, its cut and fill coloured if you like.
- `domus export` writes the site, or a scheme, as glTF for Blender and 3D viewers, or as IFC for a designer's software,
  with each wall's numbers.
- Changes made through Domus are recorded, with who, when and why, and can be undone.
- Sketches save as you go, with your notes, and copy out as text.
- A site folder can be a git repository of its own: `domus create` writes a `.gitignore` that leaves out what Domus makes again.
- Back up one folder and you've backed up everything.

</td>
</tr>
</table>

## Try it

### The demo, in two minutes

The quickest way is Docker:

```sh
docker compose -f deploy/compose.yml up -d
```

Open <http://localhost:8765>. With an empty site folder it asks for your address. Want a look first? `/demo` shows the
made-up site, so you can poke at everything without any data of your own.

The image comes from `ghcr.io/populii/domus`. To build it from your copy of the repo instead, run
`docker build -t ghcr.io/populii/domus:latest .` first.

Prefer no Docker? You'll need [uv](https://docs.astral.sh/uv/), which sorts out Python for you:

```sh
uv sync
uv run domus demo sites/demo
uv run domus --site sites/demo build
uv run domus --site sites/demo serve
```

Then open <http://127.0.0.1:8765>. The demo builds in well under a minute.

### Your own place

```sh
uv run domus create "12 Example Street, Christchurch" site
uv run domus --site site serve
```

That's it: a few minutes later you're walking round a model of your place. In a copy of this repo, `site/` is the place
for it, because git ignores everything you add there.

<details>
<summary><b>Get a free LINZ key first (New Zealand)</b></summary>
<br>

For the real boundary and building outlines, make a free API key at [data.linz.govt.nz](https://data.linz.govt.nz) and
set it as `DOMUS_LINZ_KEY`.

Without a key Domus still works, with two catches: it looks your address up in OpenStreetMap instead, so the address
goes to OpenStreetMap's public servers, and OpenStreetMap has no parcels, so your lot starts as a 24 m square round the
address. Domus says so when this happens. The LiDAR and photo still come from LINZ.

</details>

<details>
<summary><b>Outside New Zealand</b></summary>
<br>

Domus finds the address and the buildings round it in OpenStreetMap, puts the site on its UTM zone, and fetches the
ground from the Copernicus ground model, which covers the world's land at 30 m. That gives you the lie of the land
rather than the detail LINZ's 1 m LiDAR does, and the lot is a stand-in square until you draw the real one. Bring your
own ground model and photo if you have them: [docs/new-site.md](docs/new-site.md) says how.

</details>

<details>
<summary><b>Already have a boundary?</b></summary>
<br>

Start from your own, as GeoJSON, and add things yourself:

```sh
uv run domus new site --name "My place" --boundary lot.geojson
uv run domus --site site fetch
uv run domus --site site build
uv run domus --site site serve
```

[docs/new-site.md](docs/new-site.md) walks through it, and [docs/site-format.md](docs/site-format.md) lists every file
and setting.

</details>

### Host it at home

Domus is a single container that keeps everything in one `/site` folder: the site's files, the built model and the
sketches you save, so backing up that folder backs up everything. Set `DOMUS_SITES` and one container holds many sites,
each in its own folder, with a list page and a box for new addresses. To upgrade, pull the new image and start the
container again: a model an older Domus built is built again the first time its site is opened.
[docs/self-hosting.md](docs/self-hosting.md) covers Docker, Unraid (there's a template in `deploy/unraid/`), upgrades
and putting a login in front.

> [!IMPORTANT]
> **There's no login built in.** Anyone who can reach the port can see the model, change sketches, add sites, upload
> videos and start builds, so keep it on your own network or behind a login proxy. If you reach it by a host name, list
> that name in `DOMUS_ALLOWED_HOSTS`. [SECURITY.md](SECURITY.md) has more, and how to report a problem.

## Make the model better

The first model is a sketch. The viewer's **Improve this model** panel, in the Site tab, lists what would help for your
site and ticks things off as their files appear. Here they are, with the two you can do yourself today first:

| Add this | What it gets you | How |
|---|---|---|
| A video walk | Under trees, wall faces, anything changed since the survey | `domus video`: [docs/video.md](docs/video.md) |
| Tape and level measurements | Accurate walls and ground | `walls-existing.geojson`, `ground-fixes.geojson` |
| Title and survey plan | The surveyed boundary, not a guide | `boundary.geojson` |
| LIM report, consent history | Drainage plans, hazards, what was consented | Keep them in `sources/raw/` and read off what you need |
| Building and site plans | True dimensions and levels | `domus prep plan-georef`, `plan-trace` |
| Drainage records | Cover over pipes, and walls crossing them | `services.geojson` |
| A phone LiDAR scan | Walls and banks in true metres, close up | `domus scan`: [docs/phone-lidar.md](docs/phone-lidar.md) |
| Photos | Real walls and fences in place of drawn ones | `domus prep house-textures`, `feature-textures` |
| A designer's IFC model | The building in place, with its levels | `domus prep ifc` |

The documents often take days and a fee. The walk and the tape measure you can do this afternoon, so start there.
[docs/from-documents.md](docs/from-documents.md) covers plans, IFC models and photos.

## Two parts: Domus and your site

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/images/common-and-site-dark.svg">
  <img src="docs/images/common-and-site-light.svg" alt="Two cards. Common, Domus itself: the commands, the viewer, the public API and the made-up sites. Your site, one property's private folder: settings, layers, rasters, wall schemes, sources, its own tools and tests, and notes.">
</picture>

Domus has two parts. **Common** is the app itself: everything that works for any property, anywhere. It's what the
container runs. **Your site** is one property's project: its boundary, ground, house, plans and wall schemes, plus any
tools and tests written just for it.

In a copy of this repo, your site goes in [`site/`](site/README.md). It comes with a few generic examples to start from,
and git ignores everything you add there, so your property can't end up in a commit by accident. Got more than one? Use
`sites/<name>/`, which git ignores too, or keep each one wherever you like. Your own tools and tests read your site
through Domus's public API, [`domus.api`](docs/api.md), which keeps working from one release to the next.

When a tool you wrote for your site turns out to be useful for anyone's, it can move into common.
[ARCHITECTURE.md](ARCHITECTURE.md) says what belongs where, and why.

## Working with an agent

Domus is built to be driven by an AI agent as much as by hand. [AGENTS.md](AGENTS.md) is the agent's manual: how to set
a site up from nothing, which sources to reach for first (the video walk above all), how to turn a walk into placed
clouds and measurements, how to fit the model to the evidence, how to adapt a site to its own situation, how to check
the result, and the privacy rules for working on a real property. It has the agent make its changes the recorded,
undoable way, and propose the ones you should decide. Point your agent at AGENTS.md and at this README, and ask it to
model your place.

## What leaves your machine

Pretty much nothing, and only when you ask:

- The viewer's libraries are bundled, so looking at a model makes no outside calls.
- `domus create`, `domus fetch`, `domus marks` and `domus title` download from LINZ's public data.
- `domus create` sends the address you typed to LINZ, or to OpenStreetMap without a key or outside New Zealand.
- Outside New Zealand, `domus create` and `domus fetch` read the Copernicus ground model's open copy on AWS, which tells
  AWS roughly where the site is.
- `domus create` and `domus points` ask LINZ (with a key that can export) or OpenTopography for the point cloud tiles
  over the site, which tells them roughly where the site is.

## What it can't do yet

Being honest about the rough edges:

- Outside New Zealand the public data is much rougher: a 30 m ground model, OpenStreetMap's buildings, no parcels and no
  aerial photo. Your own LiDAR and photos, on any grid, make the model as good as New Zealand's.
- Drafted houses are simple: a hip, gable or skillion roof fitted to the LiDAR, plain walls, and neighbours as
  flat-topped blocks. You add the porch, the deck and the rest.
- Placing a video walk accurately is still hand work: marking points in its frames and holding them to places you
  know. The phone's guided scan places a walk only roughly, to about a metre, and it and phone LiDAR scans have had
  little use on real phones so far.
- It knows when your documents are there, but doesn't read them for you.
- Council data such as drains, hazards and zoning isn't fetched yet.
- It doesn't estimate cost or say what needs consent.
- Beyond the viewer and the address box, everything is a command.

## The docs

| Doc | What it covers |
|---|---|
| [docs/new-site.md](docs/new-site.md) | Starting a site from your own boundary and rasters |
| [docs/video.md](docs/video.md) | From a phone video to a placed point cloud |
| [docs/phone-lidar.md](docs/phone-lidar.md) | Bringing in an iPhone or iPad LiDAR scan |
| [docs/from-documents.md](docs/from-documents.md) | Plans, IFC models and photos into the model, and checking it |
| [docs/options.md](docs/options.md) | Retaining wall schemes, in full |
| [docs/shots.md](docs/shots.md) | Pictures of the viewer, from a spec |
| [docs/export.md](docs/export.md) | The model as glTF or IFC, for other software |
| [docs/site-format.md](docs/site-format.md) | Every file and setting in a site folder |
| [docs/model-format.md](docs/model-format.md) | What a build writes, for the viewer and your own tools |
| [docs/changes.md](docs/changes.md) | Every change to a site's data, recorded and undoable, and the changes proposed for you to accept |
| [docs/observations.md](docs/observations.md) | Readings from the videos, set against the model at every build |
| [docs/api.md](docs/api.md) | The public API for site tools and tests |
| [docs/self-hosting.md](docs/self-hosting.md) | Docker, Unraid, many sites, upgrades and logins |
| [AGENTS.md](AGENTS.md) | The guide for AI agents building a model or changing Domus |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Setting up, tests, the rules, and how a release goes |
| [ARCHITECTURE.md](ARCHITECTURE.md) | How Domus is put together |
| [CHANGELOG.md](CHANGELOG.md) | What changed in each release |

## Contributing

Pull requests are welcome. [CONTRIBUTING.md](CONTRIBUTING.md) explains the setup, the tests, how to prove a change left
the model alone, and the one rule that matters most: no real property ever goes in this repo. Everyone taking part
follows the [code of conduct](CODE_OF_CONDUCT.md).

## Thanks and licences

Domus stands on a lot of generous work: LINZ and its data partners (CC BY 4.0), OpenStreetMap contributors (ODbL), the
Copernicus DEM (DLR e.V. and Airbus Defence and Space GmbH, provided under Copernicus by the European Union and ESA),
three.js, three-mesh-bvh, camera-controls and three-viewport-gizmo (MIT), COLMAP via pycolmap, and Manifold via
manifold3d (Apache-2.0), which carries Clipper2 (Boost Software License 1.0), nanobind (BSD 3-Clause) and oneTBB
(Apache-2.0).

Domus itself is under the [MIT licence](LICENSE): use it, change it and share it, as long as the licence notice goes
with it. The bundled three.js, three-mesh-bvh, camera-controls and three-viewport-gizmo are MIT too, and Manifold's
WebAssembly (manifold-3d) is Apache-2.0, all in the viewer's `vendor` folder with their licences.
