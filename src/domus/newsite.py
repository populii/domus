"""Start a site project: its settings, boundary and the options' shared settings.

    domus new my-place --name "My place" --boundary lot.geojson
    domus new my-place --name "My place" --centre -43.5321,172.6362 --size 30

The boundary is a GeoJSON polygon in longitude and latitude (what LINZ Data Service and geojson.io
give you), or on another grid if its `crs` member says so. Without one, a square of --size metres
stands in until you have the real line. New sites use NZTM2000 (EPSG:2193) and NZVD2016 heights,
the LINZ data's own.
"""

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

import numpy as np
from pyproj import Transformer

TEMPLATES = Path(__file__).resolve().parent / "templates"
NZTM = 2193
MARGIN_M = 15.0


def read_boundary(path):
    """A boundary GeoJSON's outer ring on NZTM, closed."""
    gj = json.loads(Path(path).read_text(encoding="utf-8"))
    feats = gj.get("features") or [gj]
    geom = feats[0].get("geometry") or feats[0]
    if geom["type"] == "MultiPolygon":
        ring = geom["coordinates"][0][0]
    elif geom["type"] == "Polygon":
        ring = geom["coordinates"][0]
    else:
        raise SystemExit(f"{path}: the boundary must be a Polygon, not a {geom['type']}")
    name = gj.get("crs", {}).get("properties", {}).get("name", "")
    m = re.search(r"EPSG:+(\d+)", name)
    src = int(m.group(1)) if m else 4326
    xs, ys = Transformer.from_crs(src, NZTM, always_xy=True).transform(*zip(*[p[:2] for p in ring]))
    pts = [[round(x, 3), round(y, 3)] for x, y in zip(xs, ys)]
    return pts if pts[0] == pts[-1] else [*pts, pts[0]]


def square_boundary(lat, lon, size):
    x, y = Transformer.from_crs(4326, NZTM, always_xy=True).transform(lon, lat)
    h = size / 2
    corners = [[x - h, y + h], [x + h, y + h], [x + h, y - h], [x - h, y - h]]
    pts = [[round(a, 3), round(b, 3)] for a, b in corners]
    return [*pts, pts[0]]


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


def write_project(path, name, ring, note="", about=""):
    root = Path(path)
    if (root / "data" / "site.toml").exists():
        raise SystemExit(f"{root} already has a site project")
    (root / "data" / "site").mkdir(parents=True, exist_ok=True)
    (root / "options").mkdir(exist_ok=True)
    ext = extent_of(ring)
    origin = [round((ext[0] + ext[2]) / 2), round((ext[1] + ext[3]) / 2)]
    (root / "data" / "site.toml").write_text(
        (TEMPLATES / "site.toml").read_text(encoding="utf-8")
        .replace("{{name}}", toml_str(name)).replace("{{extent}}", json.dumps(ext)).replace("{{origin}}", json.dumps(origin))
        .replace("{{about}}", toml_str(about)),
        encoding="utf-8", newline="\n")
    (root / "data" / "site" / "boundary.geojson").write_text(json.dumps({
        "type": "FeatureCollection", "name": "boundary",
        "crs": {"type": "name", "properties": {"name": f"urn:ogc:def:crs:EPSG::{NZTM}"}},
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
    return root


def main(argv=None):
    ap = argparse.ArgumentParser(prog="domus new", description="Start a site project.")
    ap.add_argument("path")
    ap.add_argument("--name", required=True, help="the site's name, shown in the viewer")
    ap.add_argument("--boundary", help="a GeoJSON polygon of the lot")
    ap.add_argument("--centre", help="latitude,longitude of the lot, when you have no boundary yet")
    ap.add_argument("--size", type=float, default=30.0, help="side of the stand-in square, metres")
    # A New Zealand latitude starts with a minus, which argparse would read as another option.
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--centre" in argv and argv.index("--centre") + 1 < len(argv):
        k = argv.index("--centre")
        argv[k:k + 2] = [f"--centre={argv[k + 1]}"]
    args = ap.parse_args(argv)
    if bool(args.boundary) == bool(args.centre):
        ap.error("give one of --boundary or --centre")
    if args.boundary:
        ring, note = read_boundary(args.boundary), f"from {Path(args.boundary).name}"
    else:
        lat, lon = (float(v) for v in args.centre.split(","))
        ring, note = square_boundary(lat, lon, args.size), "a stand-in square; replace it with the real boundary"
    root = write_project(args.path, args.name, ring, note)
    print(f"Started {root}. Next:\n  domus --site {root} fetch\n  domus --site {root} build\n"
          f"  domus --site {root} serve")


if __name__ == "__main__":
    main()
