"""LINZ's survey marks around the lot, as a site layer: `domus marks`.

Writes data/site/features-survey-marks.geojson (kind mark: labels in the viewer's hidden Survey layer, and wall options flag
footings near them) with each named boundary mark on the lot's line, and prints the nearest height marks. A height mark goes in the
layer only when you name it with --height-mark, since it's usually some way off and its position is approximate. Needs DOMUS_LINZ_KEY.

NZ Survey Boundary Marks and NZ Geodetic Vertical Marks, LINZ, CC BY 4.0.
"""

import json
import os

from shapely.geometry import Point, Polygon

from ...errors import DomusError
from ...geo import geojson
from . import publicdata

BOUNDARY_MARKS, VERTICAL_MARKS = "layer-50774", "layer-50784"
ON_LINE_M = 1.5
BOUNDARY_SEARCH_M = 60
HEIGHT_SEARCH_M = 1000
HEIGHT_MARKS_SHOWN = 5


def lot_on_nztm(site):
    """The site's lot, [boundary] main, on NZTM. Its neighbours and rights of way in boundary.geojson aren't searched."""
    lot = geojson.main_lot(geojson.read(site.path / "data" / "site" / "boundary.geojson")["features"], site.settings())
    return Polygon([site.grid.to_nztm(*p[:2]) for p in lot["geometry"]["coordinates"][0]])


def around(lot, margin):
    x0, y0, x1, y1 = lot.bounds
    return publicdata._bbox_cql(x0 - margin, y0 - margin, x1 + margin, y1 + margin)


def boundary_features(rows, lot, grid):
    """The named marks on the lot's line, on the site's grid. An unnamed or UNMK mark has nothing on the ground to
    find."""
    out = []
    for f in rows:
        p, c = f["properties"], f["geometry"]["coordinates"][:2]
        name, off = p.get("name"), lot.exterior.distance(Point(c))
        if not name or name.startswith("UNMK") or off > ON_LINE_M:
            continue
        accuracy = p.get("nominal_accuracy")
        out.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [round(v, 3) for v in grid.from_nztm(*c)]},
                    "properties": {"id": f"linz-mark-{p['id']}", "kind": "mark", "name": name, "source": "LINZ NZ Survey Boundary Marks",
                                   "basis": "LINZ's adjusted coordinate", "confidence": f"LINZ, +-{accuracy} m", "accuracy_m": accuracy,
                                   "note": f"{off:.2f} m from the model's boundary; a position to go and find, not a surveyor's check"}})
    return out


def nearest_height_marks(rows, lot):
    """(distance, feature), nearest first."""
    return sorted(((lot.distance(Point(f["geometry"]["coordinates"][:2])), f) for f in rows), key=lambda r: r[0])


def height_feature(row, grid):
    p, c = row["properties"], row["geometry"]["coordinates"][:2]
    name = p.get("current_mark_name")
    return {"type": "Feature", "geometry": {"type": "Point", "coordinates": [round(v, 3) for v in grid.from_nztm(*c)]},
            "properties": {"id": "linz-height-mark", "kind": "mark", "name": f"{name} ({p.get('normal_orthometric_height')} m)",
                           "source": "LINZ NZ Geodetic Vertical Marks", "basis": "LINZ's height; its position is approximate",
                           "confidence": f"height order {p.get('order')}", "height_datum": p.get("coordinate_system"),
                           "condition": p.get("mark_condition"), "order": p.get("order")}}


def fetch(site, height_mark=None, say=print):
    """`domus marks`: the named boundary marks on the site's lot's line, written as its features-survey-marks layer,
    with the height mark `height_mark` too when it's named. Says the nearest height marks. Gives the features written.
    Needs DOMUS_LINZ_KEY."""
    key = os.environ.get("DOMUS_LINZ_KEY")
    if not key:
        raise DomusError("This needs DOMUS_LINZ_KEY: a free key from data.linz.govt.nz.")
    lot = lot_on_nztm(site)
    feats = boundary_features(publicdata._wfs(key, BOUNDARY_MARKS, around(lot, BOUNDARY_SEARCH_M), 200), lot, site.grid)
    for f in feats:
        say(f"{f['properties']['name']}: {f['properties']['note'].split(' from')[0]} from the boundary")
    heights = nearest_height_marks(publicdata._wfs(key, VERTICAL_MARKS, around(lot, HEIGHT_SEARCH_M), 200), lot)
    if not heights:
        say(f"No height marks within {HEIGHT_SEARCH_M} m.")
    for d, f in heights[:HEIGHT_MARKS_SHOWN]:
        p = f["properties"]
        say(f"Height mark {p.get('current_mark_name')}: {p.get('normal_orthometric_height')} m, {p.get('coordinate_system')}, "
            f"{p.get('mark_condition')}, order {p.get('order')}, {d:.0f} m from the lot")
    if height_mark:
        match = [f for _, f in heights if f["properties"].get("current_mark_name") == height_mark]
        if not match:
            raise DomusError(f"No height mark called {height_mark!r} within {HEIGHT_SEARCH_M} m.")
        feats.append(height_feature(match[0], site.grid))
    out = site.path / "data" / "site" / "features-survey-marks.geojson"
    out.write_text(json.dumps({"type": "FeatureCollection", "name": "features-survey-marks",
                               "crs": {"type": "name", "properties": {"name": f"urn:ogc:def:crs:EPSG::{site.grid.epsg}"}},
                               "features": feats}, indent=1) + "\n", encoding="utf-8", newline="\n")
    say(f"{len(feats)} marks -> {out}. Run domus build, then turn on the Survey layer.")
    return feats
