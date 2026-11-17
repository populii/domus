"""An example site tool: what's in a site, read through Domus.

    uv run python site/tools/example.py [SITE]

SITE is the site project's folder: the one given, else DOMUS_SITE, else this file's own site (the folder above
tools/). Copy this file before changing it; your copy stays out of git.

A site tool reads the site and writes data into it: a layer, a setting, a raster. If one turns out to be useful for
any site, give its numbers settings, test it on the made-up demo site and move it into Domus itself.
"""

import os
import sys
from pathlib import Path

SITE = Path(sys.argv[1] if len(sys.argv) > 1 else os.environ.get("DOMUS_SITE") or Path(__file__).resolve().parents[1])
# Domus reads the site's folder when its modules are imported, so name it first.
os.environ["DOMUS_SITE"] = str(SITE.resolve())
os.environ.pop("DOMUS_OUTPUT", None)

import numpy as np
import shapely
from shapely.geometry import Polygon

from domus import crs, geojson, options, raster, terrain


def main_lot(cfg):
    """The lot: the feature [boundary] main names in data/site/boundary.geojson, else the first."""
    feats = geojson.read(crs.ROOT / "data" / "site" / "boundary.geojson")["features"]
    main = cfg.get("boundary", {}).get("main", feats[0]["properties"]["id"])
    return Polygon(next(f for f in feats if f["properties"]["id"] == main)["geometry"]["coordinates"][0])


def main():
    if not (crs.ROOT / "data" / "site.toml").exists():
        sys.exit(f"{crs.ROOT} has no site project yet: see site/README.md to start one")
    cfg = crs.settings()
    lot = main_lot(cfg)
    print(f"{cfg.get('site', {}).get('title', crs.ROOT.name)}: a lot of {lot.area:.0f} m2 on {crs.label()}")
    for path in sorted((crs.ROOT / "data" / "site").glob("*.geojson")):
        count = len(geojson.read_optional(path))
        print(f"  {path.stem}: {count} feature{'' if count == 1 else 's'}")
    if raster.DEM.exists():
        e0, n0, e1, n1 = lot.bounds
        E, N = np.meshgrid(np.arange(e0, e1, 0.5), np.arange(n0, n1, 0.5))
        on_lot = shapely.contains_xy(lot, E, N)
        h = terrain.ground().sample(E[on_lot], N[on_lot])
        vertical = cfg.get("crs", {}).get("vertical", "")
        print(f"  ground on the lot: {np.nanmin(h):.2f} to {np.nanmax(h):.2f} m {vertical}".rstrip())
    else:
        print("  no ground model yet: run domus fetch, or bring your own data/lidar/dem.tif")
    for opt in options.load_all():
        print(f"  option {opt['id']}: {opt['name']}")


if __name__ == "__main__":
    main()
