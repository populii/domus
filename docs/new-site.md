# Modelling a new site

## 1. Start the project

You need the lot's boundary as GeoJSON. The easiest way is [geojson.io](https://geojson.io), drawing over the
aerial, or a download of your parcel from the [LINZ Data Service](https://data.linz.govt.nz) (NZ Primary Parcels).
Either way it's a guide, not a survey: legal boundaries come from your title plan and pegs.

```sh
uv run domus new site --name "My place" --boundary lot.geojson
```

No boundary yet? `--centre -43.53,172.63 --size 30` puts in a stand-in square.

A site is on a grid in metres, and Domus picks it from the boundary. A boundary on such a grid, which its file's
`crs` member names, stays on it. One in longitude and latitude, as GeoJSON is without a `crs` member, goes on NZTM2000
in New Zealand, the LINZ data's own grid, and on its UTM zone anywhere else.
One on a grid in feet goes on the UTM zone too. `--epsg 27700`, say, puts the site on a grid of your choosing.
GeoJSON names its grid in a `crs` member, and one without is in longitude and latitude, so `domus new` refuses a
boundary with no `crs` whose numbers can't be: add its `crs`, or name the grid it's on with `--epsg`.

This writes `site/data/site.toml`, the boundary, and `site/options/_site.toml`. In a copy of the Domus repo,
git ignores everything you add to `site/`; anywhere else, use any folder you like. `[model] extent` is the
area modelled: it starts 15 m past the boundary, and you can widen it.

It writes a `.gitignore` too, as `domus create` does, so the site can be a git repository of its own. Git leaves out
what Domus makes again: the built model in `viewer/data/`; the build cache, the checks' reports, the server's logs and
the copies `domus site upgrade` keeps, in `.domus/`; the frames taken from videos and the reconstructions' working
files; and the pictures `domus video plot`, `domus prep sections` and `domus prep canopy-plot` draw. It keeps
everything you made: `data/`, `options/`, `sources/`, your notes, tools and tests, and the viewer's store,
`.domus/viewer.db`, which holds the sketches you draw and save in the viewer, with their notes, and the checklist's
ticks. Keep such a repository private, as your documents and videos in `sources/raw/` are in it. A folder that has a
`.gitignore` already keeps its own, as `site/` in a copy of the Domus repo does.

## 2. Fetch the ground and aerial

```sh
uv run domus --site site fetch
```

This finds the newest LINZ survey covering your extent, reads just your patch and records what it used in
`data/linz-sources.json`. If LINZ has no 1 m LiDAR for your area, it stops and says so.

LINZ covers New Zealand only. Elsewhere `domus fetch` gets the Copernicus ground model instead, which covers the
world's land on a 30 m grid: enough for the lie of the land, not for a wall's footing. Anywhere, you can bring your own
GeoTIFFs instead: `data/lidar/dem.tif` (the ground
model, required), `data/lidar/dsm.tif` (the surface model: roofs and trees) and `data/aerial/aerial.tif` (a photo),
both optional. They can be on any grid, as each file's own header says: Domus reads each one on its own grid. One
that names no grid is taken to be on the site's. Heights in a band that says it's in feet are read in metres. Name
the heights' datum in `[crs] vertical` in `data/site.toml` (`domus new` leaves it empty away from New Zealand), and
say where the data came from in `[site] attribution`.

## 3. Build and look

```sh
uv run domus --site site build
uv run domus --site site serve
```

The ground, boundary, contours and any trees and roofs the LiDAR saw are there already. Everything from here on is
optional, and each file you add shows up at the next build.

## 4. Add what's on the site

The layers are GeoJSON in `data/site/`, on the site's grid (`[crs] epsg` in `data/site.toml`). QGIS reads and
writes them. [site-format.md](site-format.md) lists each file and its properties, and `domus demo` writes a
small working example of all of them.

| File | What it adds |
|---|---|
| `house.geojson` | House walls, roof, openings, decks |
| `walls-existing.geojson` | Existing retaining walls, cut into the ground |
| `services.geojson` | Drains and pipes at their depth |
| `trees.geojson` | Trees, beds, lawns. `domus canopy` drafts crowns from the LiDAR to start from |
| `features-*.geojson` | Fences, sheds, paths, posts |
| `ground-fixes.geojson` | Where the LiDAR ground is wrong, such as under trees or after earthworks |

Where you know better than the LiDAR, say so in `ground-fixes.geojson`. Under trees LiDAR only interpolates, and it
can be half a metre or more out on a steep bank.

## 5. Try wall options

Copy `options/option.toml.example` to a name ending in `.toml`, change the numbers and rebuild. Each option appears
in the viewer's panel with its numbers beside the others. See [options.md](options.md).

The rules of thumb in `options/_site.toml` are placeholders, so set your own.
