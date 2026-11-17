<img src="docs/domus-logo.svg" alt="" width="120">

# Domus

**Type in a New Zealand address. Get a 3D model of the place in your browser.**

Domus grew out of a very ordinary problem: a sloping section, a tired retaining wall, and no good way to picture what could
replace it. Drawings are expensive, and it's hard to argue about a wall you can't see. It turns out New Zealand publishes a
remarkable amount of open data about its land, so Domus pulls that together into a model you can walk around, measure and
try ideas on, before you pay anyone to draw anything.

It runs on your own machine or home server. Your site's files stay in a folder you control.

> **A friendly warning:** Domus is a planning aid, not a survey. Its numbers come from 1 m LiDAR. It helps you ask better questions. An engineer, a surveyor and your council still get the last word.

## What you get

Give it an address and a few minutes, and it will:

- **Find the place:** the address, the legal parcel and the buildings around it.
- **Fetch the ground:** the newest LiDAR and sharpest recent aerial photo that LINZ has for your spot.
- **Draft what's there:** your house, with its height and roof estimated from the LiDAR, neighbouring buildings as plain
  blocks, and trees drawn from the LiDAR canopy.
- **Tell you what would help next:** a checklist of documents worth chasing, and a suggested route for filming the site.

Then, in the viewer, you can:

- Orbit, pan and zoom around the model, and drape the ground with the aerial photo, a scanned plan or plain relief.
- Measure distances, paths, areas and heights, and cut a ground profile along any line.
- Turn the ground see-through to look at drains at their depth.
- Draw walls and level areas right on the model, snapping to the boundary, the house and your own lines, with typed lengths and bearings.
- See the ground a sketched wall makes as you draw, with its cut and fill, and save the sketch as an option.
- Compare retaining wall options side by side: how they look, flat lawn gained, wall heights, and cut and fill.

## Try it

The quickest way is Docker:

```sh
docker compose -f deploy/compose.yml up -d
```

Open <http://localhost:8765>. With an empty site folder it asks for your address. Want a look first? `/demo` shows a made-up
site, which isn't a real place, so you can poke at everything without any data of your own.

> The image and this repo are private for now, so this needs access to them. Otherwise, build the image yourself with
> `docker build -t ghcr.io/populii/domus:latest .`.

Prefer no Docker? You'll need [uv](https://docs.astral.sh/uv/):

```sh
uv sync
uv run domus demo sites/demo
uv run domus --site sites/demo build
uv run domus --site sites/demo serve
```

## Two parts: Domus and your site

Domus has two parts. **Common** is the app itself: everything that works for any property, anywhere. It lives in
`src/domus`, and it's what the container runs. **Your site** is one property's project: its boundary, ground, house,
plans and wall options, plus any tools and tests written just for it.

In a copy of this repo, your site goes in [`site/`](site/README.md). It comes with a few generic examples to start
from, and git ignores everything you add there, so your property can't end up in a commit by accident. Got more than
one? Use `sites/<name>/`, which git ignores too, or keep each one wherever you like. [ARCHITECTURE.md](ARCHITECTURE.md)
explains what belongs where, and why.

## Model your own place

```sh
uv run domus create "12 Example Street, Christchurch" site
uv run domus --site site serve
```

If that address exists in more than one town, Domus asks which you meant. Adding the suburb or town skips the question.

### Get a free LINZ key first

For the real boundary and building outlines, make a free API key at [data.linz.govt.nz](https://data.linz.govt.nz) and set it
as `DOMUS_LINZ_KEY`.

Without a key Domus still works, using OpenStreetMap, with two catches: your address goes to OpenStreetMap's public servers, and
it has no parcels, so your lot is a stand-in square. Domus says so when this happens.

### Already have a boundary?

Start from your own, as GeoJSON, and add things yourself:

```sh
uv run domus new site --name "My place" --boundary lot.geojson
uv run domus --site site fetch
uv run domus --site site build
uv run domus --site site serve
```

[docs/new-site.md](docs/new-site.md) walks through it, and [docs/site-format.md](docs/site-format.md) lists every file and setting.

## Make the model better

The first model is a sketch. It gets better with everything you feed it, and **Improve this model**, in the viewer's Site panel, lists
what helps most for your site. Items tick themselves off as your files appear. Roughly in order of value:

| Add this | What it gets you | How |
|---|---|---|
| Title and survey plan | The surveyed boundary, not a guide | Edit `data/site/boundary.geojson` |
| LIM report, consent history | Drainage plans, hazards, what was consented | Keep them in `sources/raw/`, read off what you need |
| Building and site plans | True dimensions and levels | `domus prep plan-georef`, `plan-trace` |
| Drainage records | Checks on cover over pipes, and walls crossing them | Add to `data/site/services.geojson` |
| Tape and level measurements | Accurate walls and ground | `data/site/walls-existing.geojson`, `ground-fixes.geojson` |
| A video walk | Under trees, wall faces, recent changes | `domus video` |
| A phone LiDAR scan | Walls and banks in true metres, close up | `domus scan` |
| Photos | Real walls and fences in place of drawn ones | `domus prep house-textures`, `feature-textures` |
| A designer's IFC model | The building in place, with its levels | `domus prep ifc` |

### Film your site

LiDAR is great on open ground and pretty useless under a tree. A slow walk with your phone fills the gap. Show the suggested
route in the viewer, walk it, then:

```sh
domus video frames sources/raw/walk.mp4
domus video reconstruct walk --focal 1100
```

There's a bit more to it, mainly placing the clip on the grid. [docs/video.md](docs/video.md) has the whole recipe, and what to do
when a clip falls apart, which they do.

### Scan it with an iPhone

An iPhone Pro or iPad Pro has a LiDAR sensor. iOS won't save the data by itself, so use an app like Polycam or Scaniverse, export
PLY or LAS, and bring it in with `domus scan`. See [docs/phone-lidar.md](docs/phone-lidar.md). So far this has only been tested on
files written to the formats' published layouts, not a real phone export, so please tell us how you get on.

### Plans, IFC and photos

[docs/from-documents.md](docs/from-documents.md) covers pinning scanned plans to the grid, tracing vector PDFs, fitting a designer's
model and cutting textures from photos. These read their settings from `data/prep.json`, and `domus prep check` then hunts for
things floating, buried or tilted.

## Try some walls

Wall options are small text files in your site's `options/` folder. Copy `option.toml.example`, change a few numbers and rebuild:

```toml
name = "A. One low wall"
description = "A timber wall holding a level lawn."

[[walls]]
id = "L1"
name = "Lawn wall"
material = "timber"
line_so = [[2.0, 2.0], [12.0, 2.0]]    # metres along the boundary, metres in from it

[[terraces]]
id = "lawn"
name = "Lawn"
kind = "lawn"
level = 40.0
at_so = [7.0, 6.0]
polygon_so = [[2.0, 2.0], [12.0, 2.0], [12.0, 10.0], [2.0, 10.0]]
```

Domus works out the designed ground, cut and fill, wall heights and flat lawn, then flags things to check, like a footing close to
a drain or ground lowered next to the house.
[docs/options.md](docs/options.md) has the details.

## Host it at home

Domus is a single container that keeps everything in one `/site` folder, so backing up that folder backs up everything. Set
`DOMUS_SITES` and one container holds many sites, each in its own folder, with a list page and a box for new addresses.
[docs/self-hosting.md](docs/self-hosting.md) covers Docker, Unraid (there's a template in `deploy/unraid/`), upgrades and putting a
login in front.

**There's no login built in.** Anyone who can reach the port can see the model, change sketches, add sites, upload videos and start
builds, so keep it on your own network or behind a login proxy. If you reach it by a host name, list that name in
`DOMUS_ALLOWED_HOSTS`. [SECURITY.md](SECURITY.md) has more, and how to report a problem.

## What leaves your machine

Pretty much nothing, and only when you ask:

- The viewer's libraries are bundled, so looking at a model makes no outside calls.
- `domus create`, `domus fetch`, `domus marks` and `domus title` download from LINZ's public data.
- `domus create` sends the address you typed to LINZ, or to OpenStreetMap without a key.
- `domus create` and `domus points` ask OpenTopography for the point cloud tiles over the site, which tells it roughly where the site is.

## What it can't do yet

Being honest about the rough edges:

- New Zealand only, since it leans on LINZ data. Other coordinate systems are configured per site, but there's no fetching elsewhere.
- Drafted houses are simple: a hip, gable or skillion roof fitted to the LiDAR, plain walls, and neighbours as flat-topped blocks.
- It knows when your documents are there, but doesn't read them for you.
- Council data such as drains, hazards and zoning isn't fetched yet.
- Beyond the viewer and the address box, everything is a command.

## Poking around the code

| Part | Where |
|---|---|
| Address, parcel and building lookup | `src/domus/publicdata.py` |
| From an address to a model | `src/domus/create.py` |
| Fetching LINZ's LiDAR and aerials | `src/domus/linz.py` |
| The build, from site data to `model.json` and textures | `src/domus/build.py`, with `terrain`, `house`, `walls`, `vegetation`, `features` |
| Wall options: designed ground, volumes, checks | `src/domus/design.py` |
| Checklist and video walk | `src/domus/enrich.py` |
| The viewer, one page using three.js | `src/domus/viewer/` |
| The server and its small store | `src/domus/serve.py` |
| Videos, phone scans, documents | `src/domus/video/`, `scan.py`, `prep/` |

Run the checks with `uv run --all-extras pytest` and `uv run ruff check .`. [CONTRIBUTING.md](CONTRIBUTING.md) explains releases and how
to prove a change left a model alone.

## Thanks and licences

Domus stands on a lot of generous work: LINZ and its data partners (CC BY 4.0), OpenStreetMap contributors (ODbL), three.js,
three-mesh-bvh, camera-controls and three-viewport-gizmo (MIT) and COLMAP via pycolmap.

Domus itself is under the [MIT licence](LICENSE): use it, change it and share it, as long as the licence notice goes with it. The
bundled three.js, three-mesh-bvh, camera-controls and three-viewport-gizmo are MIT too, in `src/domus/viewer/vendor/`.
