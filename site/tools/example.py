"""An example site tool: what's in a site, read through Domus's public API (domus.api).

    uv run python site/tools/example.py [SITE]

SITE is the site project's folder: the one given, else DOMUS_SITE, else this file's own site (the folder above
tools/). Copy this file before changing it; your copy stays out of git.

A site tool reads the site and writes data into it: a layer, a setting, a raster. If one turns out to be useful for
any site, give its numbers settings, test it on the made-up sites and move it into Domus itself.
"""

import os
import sys
from pathlib import Path

import numpy as np
import shapely

from domus import api


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    folder = argv[0] if argv else os.environ.get("DOMUS_SITE") or Path(__file__).resolve().parents[1]
    try:
        site = api.open_site(folder)
    except api.DomusError as e:
        sys.exit(f"{e} See site/README.md to start one.")
    lot, grid = site.lot(), site.settings().get("crs", {})
    print(f"{site.title}: a lot of {lot.area:.0f} m2 on {grid.get('label') or 'EPSG:' + str(grid.get('epsg', 2193))}")
    for path in sorted((site.path / "data" / "site").glob("*.geojson")):
        count = len(site.layer(path.stem))
        print(f"  {path.stem}: {count} feature{'' if count == 1 else 's'}")
    try:
        ground = site.ground()
    except api.DomusError:
        print("  no ground model yet: run domus fetch, or bring your own data/lidar/dem.tif")
    else:
        e0, n0, e1, n1 = lot.bounds
        E, N = np.meshgrid(np.arange(e0, e1, 0.5), np.arange(n0, n1, 0.5))
        on_lot = shapely.contains_xy(lot, E, N)
        h = ground(E[on_lot], N[on_lot])
        print(f"  ground on the lot: {np.nanmin(h):.2f} to {np.nanmax(h):.2f} m {grid.get('vertical', '')}".rstrip())
    for option in site.options():
        print(f"  option {option['id']}: {option['name']}")


if __name__ == "__main__":
    main()
