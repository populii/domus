"""Small GeoJSON helpers. Site data is stored on the site's grid ([crs] epsg in data/site.toml).

GeoJSON (RFC 7946) assumes WGS84 lon/lat; these files carry the legacy `crs` member naming the
site's grid instead, which GDAL/QGIS honour, so an engineer can drop them into GIS.
Heights (third coordinate) are metres on the site's vertical datum unless a feature's properties
say otherwise.
"""

import json
from pathlib import Path


def feature(geometry_type, coordinates, **properties):
    return {
        "type": "Feature",
        "properties": properties,
        "geometry": {"type": geometry_type, "coordinates": coordinates},
    }


def collection(features, name, description="", *, epsg):
    """A layer of `features` on the grid EPSG `epsg` (the site's, which the caller gives)."""
    fc = {"type": "FeatureCollection", "name": name,
          "crs": {"type": "name", "properties": {"name": f"urn:ogc:def:crs:EPSG::{epsg}"}}}
    if description:
        fc["description"] = description
    fc["features"] = features
    return fc


def round_coords(obj, nd=3):
    if isinstance(obj, float):
        return round(obj, nd)
    if isinstance(obj, (list, tuple)):
        return [round_coords(v, nd) for v in obj]
    return obj


def write(path, fc):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    for f in fc["features"]:
        f["geometry"]["coordinates"] = round_coords(f["geometry"]["coordinates"])
    path.write_text(json.dumps(fc, indent=1) + "\n", encoding="utf-8")


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def read_optional(path):
    """The features of a site layer, or none if the site does not have that layer."""
    path = Path(path)
    return read(path)["features"] if path.exists() else []


def main_lot(features, settings):
    """The lot among boundary.geojson's `features`: the one `[boundary] main` in data/site.toml (`settings`) names,
    else the first. The others are neighbours, rights of way and the like."""
    from .errors import DomusError

    main = settings.get("boundary", {}).get("main")
    if main is None:
        return features[0]
    found = [f for f in features if f["properties"].get("id") == main]
    if not found:
        raise DomusError(f"[boundary] main in data/site.toml is {main!r}, but no feature in boundary.geojson has that id")
    return found[0]
