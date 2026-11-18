"""Start a site project: its settings, boundary, the options' shared settings and a .gitignore.

    domus new my-place --name "My place" --boundary lot.geojson
    domus new my-place --name "My place" --centre -43.5321,172.6362 --size 30

The boundary is a GeoJSON polygon in longitude and latitude (what LINZ Data Service and geojson.io
give you), or on another grid if its `crs` member says so. Without one, a square of --size metres
stands in until you have the real line.

A site is on a grid in metres. A boundary on one stays on it; one in longitude and latitude goes on
NZTM2000 (EPSG:2193, with NZVD2016 heights: the LINZ data's own) in New Zealand and on its UTM zone
anywhere else; one in feet goes on the UTM zone too. --epsg names another grid in metres.
"""

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

import numpy as np
from pyproj import CRS, Transformer
from pyproj.aoi import AreaOfInterest
from pyproj.database import query_utm_crs_info

from ..errors import DomusError
from . import formats
from .formats import LINZ_CREDIT, NZ_VERTICAL

TEMPLATES = Path(__file__).resolve().parents[1] / "templates"
NZTM = 2193
MARGIN_M = 15.0
LABELS = {NZTM: "NZGD2000 / New Zealand Transverse Mercator (EPSG:2193)"}     # as sites have always named it


def in_metres(crs):
    return crs.is_projected and crs.axis_info[0].unit_name in ("metre", "meter")


def metre_grid(epsg):
    """EPSG `epsg` as a CRS, if it's a grid in metres, which a site has to be on."""
    crs = CRS.from_epsg(epsg)
    if not in_metres(crs):
        raise DomusError(f"EPSG:{epsg} ({crs.name}) isn't a grid in metres. A site needs one: leave --epsg out, "
                         "and Domus picks one")
    return crs


def in_new_zealand(lon, lat):
    w, s, e, n = CRS.from_epsg(NZTM).area_of_use.bounds
    return w <= lon <= e and s <= lat <= n


def grid_for(lon, lat):
    """The grid a new site at (lon, lat) goes on: NZTM2000 in New Zealand, the LINZ data's own, and the UTM zone
    anywhere else."""
    if in_new_zealand(lon, lat):
        return NZTM
    zones = query_utm_crs_info(datum_name="WGS 84", area_of_interest=AreaOfInterest(lon, lat, lon, lat))
    if not zones:
        raise DomusError(f"There's no UTM zone at {lat:.4f}, {lon:.4f}: give the site a grid with --epsg")
    return int(zones[0].code)


def read_boundary(path, epsg=None):
    """A boundary GeoJSON's outer ring, closed, on the site's grid, and that grid's EPSG code: `epsg`, else the
    boundary's own if it's in metres, else the grid for where it is (grid_for)."""
    gj = json.loads(Path(path).read_text(encoding="utf-8"))
    feats = gj.get("features") or [gj]
    geom = feats[0].get("geometry") or feats[0]
    if geom["type"] == "MultiPolygon":
        ring = geom["coordinates"][0][0]
    elif geom["type"] == "Polygon":
        ring = geom["coordinates"][0]
    else:
        raise DomusError(f"{path}: the boundary must be a Polygon, not a {geom['type']}")
    name = gj.get("crs", {}).get("properties", {}).get("name", "")
    m = re.search(r"EPSG:+(\d+)", name)
    src = int(m.group(1)) if m else 4326
    xy = [p[:2] for p in ring]
    if epsg is None:
        if in_metres(CRS.from_epsg(src)):
            epsg = src
        else:
            lon, lat = Transformer.from_crs(src, 4326, always_xy=True).transform(*np.mean(xy, axis=0))
            epsg = grid_for(lon, lat)
    metre_grid(epsg)
    xs, ys = zip(*xy) if src == epsg else Transformer.from_crs(src, epsg, always_xy=True).transform(*zip(*xy))
    pts = [[round(x, 3), round(y, 3)] for x, y in zip(xs, ys)]
    return (pts if pts[0] == pts[-1] else [*pts, pts[0]]), epsg


def square_boundary(lat, lon, size, epsg=None):
    """A square of `size` metres round (lat, lon), on `epsg` or the grid for where it is, and that grid's code."""
    epsg = epsg or grid_for(lon, lat)
    metre_grid(epsg)
    x, y = Transformer.from_crs(4326, epsg, always_xy=True).transform(lon, lat)
    h = size / 2
    corners = [[x - h, y + h], [x + h, y + h], [x + h, y - h], [x - h, y - h]]
    pts = [[round(a, 3), round(b, 3)] for a, b in corners]
    return [*pts, pts[0]], epsg


def longest_edge(ring):
    """The ring's longest edge, directed so the lot lies on its right: the options' reference boundary."""
    p = np.asarray(ring, float)
    area2 = np.sum(p[:-1, 0] * p[1:, 1] - p[1:, 0] * p[:-1, 1])
    k = int(np.argmax(np.hypot(*np.diff(p, axis=0).T)))
    a, b = ring[k], ring[k + 1]
    return (a, b) if area2 < 0 else (b, a)    # clockwise rings keep the lot on the right already


def extent_of(ring, margin=MARGIN_M):
    p = np.asarray(ring, float)
    return [float(np.floor(p[:, 0].min() - margin)), float(np.floor(p[:, 1].min() - margin)),
            float(np.ceil(p[:, 0].max() + margin)), float(np.ceil(p[:, 1].max() + margin))]


def toml_str(s):
    return json.dumps(s, ensure_ascii=False)


def write_project(path, name, ring, note="", about="", epsg=NZTM, importer="manual", vertical=None, attribution=None):
    """A new site project at `path`, its lot `ring` on the grid EPSG `epsg`, brought in by `importer`. A site in New
    Zealand gets the LINZ data's height datum and credit line, unless `vertical` and `attribution` say otherwise; one
    elsewhere leaves them for its own data to fill in. A .gitignore lets the site be a git repository of its own: git
    leaves out what Domus makes again and keeps what a person made (templates/gitignore). A folder that has one
    already keeps it."""
    root = Path(path)
    if (root / "data" / "site.toml").exists():
        raise DomusError(f"{root} already has a site project")
    grid = metre_grid(epsg)
    (root / "data" / "site").mkdir(parents=True, exist_ok=True)
    (root / "options").mkdir(exist_ok=True)
    ext = extent_of(ring)
    origin = [round((ext[0] + ext[2]) / 2), round((ext[1] + ext[3]) / 2)]
    nz = in_new_zealand(*Transformer.from_crs(epsg, 4326, always_xy=True).transform(*origin))
    (root / "data" / "site.toml").write_text(
        (TEMPLATES / "site.toml").read_text(encoding="utf-8")
        .replace("{{name}}", toml_str(name)).replace("{{extent}}", json.dumps(ext)).replace("{{origin}}", json.dumps(origin))
        .replace("{{about}}", toml_str(about)).replace("{{format}}", str(formats.FORMAT)).replace("{{importer}}", toml_str(importer))
        .replace("{{attribution}}", toml_str(attribution if attribution is not None else LINZ_CREDIT if nz else ""))
        .replace("{{epsg}}", str(epsg)).replace("{{crs_label}}", toml_str(LABELS.get(epsg, f"{grid.name} (EPSG:{epsg})")))
        .replace("{{vertical}}", toml_str(vertical if vertical is not None else NZ_VERTICAL if nz else "")),
        encoding="utf-8", newline="\n")
    (root / "data" / "site" / "boundary.geojson").write_text(json.dumps({
        "type": "FeatureCollection", "name": "boundary",
        "crs": {"type": "name", "properties": {"name": f"urn:ogc:def:crs:EPSG::{epsg}"}},
        "features": [{"type": "Feature",
                      "properties": {"id": "lot", "name": "Boundary", "source": note or "supplied boundary"},
                      "geometry": {"type": "Polygon", "coordinates": [ring]}}]}, indent=1) + "\n",
        encoding="utf-8", newline="\n")
    a, b = longest_edge(ring)
    (root / "options" / "_site.toml").write_text(
        (TEMPLATES / "options_site.toml").read_text(encoding="utf-8")
        .replace("{{frame_origin}}", json.dumps(a)).replace("{{frame_toward}}", json.dumps(b)),
        encoding="utf-8", newline="\n")
    shutil.copy(TEMPLATES / "option.toml.example", root / "options" / "option.toml.example")
    if not (root / ".gitignore").exists():     # a folder's own stays: in a copy of Domus, site/ has one that ignores it all
        (root / ".gitignore").write_text((TEMPLATES / "gitignore").read_text(encoding="utf-8"), encoding="utf-8", newline="\n")
    return root


def main(argv=None):
    ap = argparse.ArgumentParser(prog="domus new", description="Start a site project.")
    ap.add_argument("path")
    ap.add_argument("--name", required=True, help="the site's name, shown in the viewer")
    ap.add_argument("--boundary", help="a GeoJSON polygon of the lot")
    ap.add_argument("--centre", help="latitude,longitude of the lot, when you have no boundary yet")
    ap.add_argument("--size", type=float, default=30.0, help="side of the stand-in square, metres")
    ap.add_argument("--epsg", type=int, help="the site's grid, in metres (default: the boundary's own, else one for where it is)")
    # A New Zealand latitude starts with a minus, which argparse would read as another option.
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--centre" in argv and argv.index("--centre") + 1 < len(argv):
        k = argv.index("--centre")
        argv[k:k + 2] = [f"--centre={argv[k + 1]}"]
    args = ap.parse_args(argv)
    if bool(args.boundary) == bool(args.centre):
        ap.error("give one of --boundary or --centre")
    if args.boundary:
        (ring, epsg), note = read_boundary(args.boundary, args.epsg), f"from {Path(args.boundary).name}"
    else:
        lat, lon = (float(v) for v in args.centre.split(","))
        (ring, epsg), note = square_boundary(lat, lon, args.size, args.epsg), "a stand-in square; replace it with the real boundary"
    root = write_project(args.path, args.name, ring, note, epsg=epsg)
    print(f"Started {root} on {CRS.from_epsg(epsg).name} (EPSG:{epsg}).")
    if epsg == NZTM:
        print(f"Next:\n  domus --site {root} fetch\n  domus --site {root} build\n  domus --site {root} serve")
    else:
        print(f"Next: put the site's ground model in {root / 'data' / 'lidar' / 'dem.tif'} (and its surface model and photo"
              f" if you have them: see docs/new-site.md), name its height datum in data/site.toml, then\n"
              f"  domus --site {root} build\n  domus --site {root} serve")


if __name__ == "__main__":
    main()
