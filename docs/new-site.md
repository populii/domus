# Modelling a new site

## 1. Start the project

You need the lot's boundary as GeoJSON. The easiest way is [geojson.io](https://geojson.io), drawing over the
aerial, or a download of your parcel from the [LINZ Data Service](https://data.linz.govt.nz) (NZ Primary Parcels).
Either way it's a guide, not a survey: legal boundaries come from your title plan and pegs.

```sh
uv run domus new my-place --name "My place" --boundary lot.geojson
```

No boundary yet? `--centre -43.53,172.63 --size 30` puts in a stand-in square.

This writes `my-place/data/site.toml`, the boundary, and `my-place/options/_site.toml`. `[model] extent` is the
area modelled: it starts 15 m past the boundary, and you can widen it.

## 2. Fetch the ground and aerial

```sh
uv run domus --site my-place fetch
```

This finds the newest LINZ survey covering your extent, reads just your patch and records what it used in
`data/linz-sources.json`. If LINZ has no 1 m LiDAR for your area, it stops and says so. You can supply your own
GeoTIFFs at `data/lidar/dem.tif` (required), `data/lidar/dsm.tif` and `data/aerial/aerial.tif` (both optional), on
NZTM2000.

## 3. Build and look

```sh
uv run domus --site my-place build
uv run domus --site my-place serve
```

The ground, boundary, contours and any trees and roofs the LiDAR saw are there already. Everything from here on is
optional, and each file you add shows up at the next build.

## 4. Add what's on the site

The layers are GeoJSON in `data/site/`, on the site's grid (NZTM2000 unless you changed `[crs]`). QGIS reads and
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
