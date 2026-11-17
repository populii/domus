"""Public data about a place, from an address: where it is, its parcel, the buildings around it.

LINZ holds the authoritative address, parcel and building layers for all of New Zealand, with a free API key from
https://data.linz.govt.nz in DOMUS_LINZ_KEY. Without one this falls back to OpenStreetMap, which needs no key but has no
parcels and patchier buildings, and which means the address goes to OpenStreetMap's public servers. OpenStreetMap also
finds an address anywhere else (domus.importers).

Every call here goes out to the internet, so only `domus create` and the web app's address box make them.
"""

import itertools
import json
import os
import re
import time
import unicodedata
import urllib.parse
import urllib.request

from pyproj import Transformer

from ... import __version__

LINZ_WFS = "https://data.linz.govt.nz/services;key={key}/wfs"
ADDRESSES, PARCELS, BUILDINGS = "layer-123113", "layer-50772", "layer-101290"
NOMINATIM = "https://nominatim.openstreetmap.org/search"
OVERPASS = ("https://overpass-api.de/api/interpreter", "https://overpass.private.coffee/api/interpreter",
            "https://overpass-api.de/api/interpreter")
USER_AGENT = f"domus/{__version__} (self-hosted site model; https://github.com/populii/domus)"
SAME_PLACE_M = 300.0
TO_NZTM = Transformer.from_crs(4326, 2193, always_xy=True)
TO_LONLAT = Transformer.from_crs(2193, 4326, always_xy=True)


class NotFound(Exception):
    pass


class Ambiguous(Exception):
    """An address that matches more than one place: `places` lists them, for the person to choose."""

    def __init__(self, address, places):
        super().__init__(address)
        self.address, self.places = address, places


def _get(url, data=None, timeout=60):
    req = urllib.request.Request(url, data=data, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def linz_key():
    return os.environ.get("DOMUS_LINZ_KEY", "").strip()


def _wfs(key, layer, cql, count=20):
    q = urllib.parse.urlencode({"service": "WFS", "version": "2.0.0", "request": "GetFeature", "typeNames": layer,
                                "outputFormat": "json", "srsName": "EPSG:2193", "count": count, "cql_filter": cql})
    return [_xy_order(f) for f in _get(f"{LINZ_WFS.format(key=key)}?{q}")["features"]]


def _xy_order(feature):
    """Coordinates as east, north: a service can follow the grid's official north-first order. NZTM's two ranges don't overlap."""
    def swap(c):
        if isinstance(c[0], (int, float)):
            return [c[1], c[0], *c[2:]] if c[0] > 3e6 else c
        return [swap(v) for v in c]

    g = feature.get("geometry")
    if g:
        g["coordinates"] = swap(g["coordinates"])
    return feature


def normalise(address):
    """An address in the form LINZ writes it: no commas after the road, single spaces, title case dropped."""
    a = re.sub(r"\s+", " ", re.sub(r"[%_]", " ", address).replace("'", "''")).strip()
    return a.split(",")[0].strip()


def ascii_only(text):
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()


def narrow(rows, address):
    """The matches that also have the suburb or town typed after the road's comma, if any were typed. Macrons don't matter."""
    parts = [ascii_only(p).strip().lower() for p in address.split(",")[1:] if p.strip()]
    if not parts:
        return rows
    kept = [r for r in rows if all(p in ascii_only(r["properties"]["full_address"]).lower() for p in parts)]
    return kept or rows


def ring_of(geometry):
    """The outer ring of a Polygon or the largest part of a MultiPolygon, as [[x, y], ...]."""
    if geometry["type"] == "Polygon":
        return [p[:2] for p in geometry["coordinates"][0]]
    if geometry["type"] == "MultiPolygon":
        parts = [part[0] for part in geometry["coordinates"]]
        return [p[:2] for p in max(parts, key=_area)]
    raise ValueError(geometry["type"])


def _area(ring):
    return abs(sum(a[0] * b[1] - b[0] * a[1] for a, b in itertools.pairwise(ring))) / 2


def _contains(ring, x, y):
    inside = False
    for (x0, y0), (x1, y1) in itertools.pairwise(ring):
        if (y0 > y) != (y1 > y) and x < x0 + (y - y0) * (x1 - x0) / (y1 - y0):
            inside = not inside
    return inside


def _bbox_cql(x0, y0, x1, y1):
    return f"BBOX(shape,{x0},{y0},{x1},{y1},'EPSG:2193')"


def from_linz(address, key):
    """{address, point, parcel, parcel_name, buildings, source} on NZTM, from LINZ."""
    road = normalise(address)
    rows = _wfs(key, ADDRESSES, f"full_address_ascii ILIKE '{ascii_only(road)},%'", 50)
    if not rows:
        raise NotFound(f"LINZ has no address like {address!r}. Try the number and road, such as 12 Example Street.")
    rows = narrow(rows, address)
    if len(rows) > 1:
        raise Ambiguous(address, sorted(r["properties"]["full_address"] for r in rows))
    hit = rows[0]
    x, y = hit["geometry"]["coordinates"][:2]
    near = _wfs(key, PARCELS, _bbox_cql(x - 1, y - 1, x + 1, y + 1))
    parcels = [p for p in near if p["geometry"] and _contains(ring_of(p["geometry"]), x, y)]
    parcel = min((ring_of(p["geometry"]) for p in parcels), key=_area) if parcels else None
    name = next((p["properties"].get("appellation") for p in parcels if ring_of(p["geometry"]) == parcel), None)
    if parcel:
        xs, ys = zip(*parcel)
        box = (min(xs) - 25, min(ys) - 25, max(xs) + 25, max(ys) + 25)
    else:
        box = (x - 40, y - 40, x + 40, y + 40)
    buildings = [ring_of(b["geometry"]) for b in _wfs(key, BUILDINGS, _bbox_cql(*box), 200) if b["geometry"]]
    return {"address": hit["properties"].get("full_address", address), "point": [x, y], "parcel": parcel, "parcel_name": name,
            "buildings": buildings, "source": "LINZ NZ Addresses, NZ Primary Parcels and NZ Building Outlines (CC BY 4.0)"}


def overpass_buildings(x, y, reach=50.0, epsg=2193):
    """OpenStreetMap's building outlines within `reach` metres of (x, y) on the grid `epsg`, on that grid."""
    to_lonlat = Transformer.from_crs(epsg, 4326, always_xy=True)
    to_site = Transformer.from_crs(4326, epsg, always_xy=True)
    lon0, lat0 = to_lonlat.transform(x - reach, y - reach)
    lon1, lat1 = to_lonlat.transform(x + reach, y + reach)
    q = f'[out:json][timeout:25];way["building"]({lat0},{lon0},{lat1},{lon1});out geom;'
    # Overpass's public servers are often busy: try the next, after a pause.
    for k, server in enumerate(OVERPASS):
        try:
            data = _get(server, urllib.parse.urlencode({"data": q}).encode())
            break
        except OSError:
            if k == len(OVERPASS) - 1:
                raise
            time.sleep(5)
    out = []
    for el in data.get("elements", []):
        pts = [to_site.transform(p["lon"], p["lat"]) for p in el.get("geometry", [])]
        if len(pts) >= 4 and pts[0] == pts[-1]:
            out.append([[round(a, 3), round(b, 3)] for a, b in pts])
    return out


def from_osm(address, countrycodes="nz"):
    """The same from OpenStreetMap, in New Zealand, or anywhere with `countrycodes` None: on NZTM2000 in New Zealand
    and on the UTM zone elsewhere, which `epsg` names, with the address's `lonlat`. It has no parcels, so `parcel` is
    None."""
    from ...project.newsite import grid_for

    q = {"q": address, "format": "jsonv2", "limit": 5, **({"countrycodes": countrycodes} if countrycodes else {})}
    rows = _get(f"{NOMINATIM}?{urllib.parse.urlencode(q)}")
    if not rows:
        raise NotFound(f"OpenStreetMap has no address like {address!r}.")
    lon, lat = float(rows[0]["lon"]), float(rows[0]["lat"])
    epsg = grid_for(lon, lat)
    to_site = Transformer.from_crs(4326, epsg, always_xy=True)
    points = [to_site.transform(float(r["lon"]), float(r["lat"])) for r in rows]
    x, y = points[0]
    # It can answer with several records of one place; records far apart are different places.
    far = [r["display_name"] for r, (a, b) in zip(rows, points) if (a - x) ** 2 + (b - y) ** 2 > SAME_PLACE_M ** 2]
    if far:
        raise Ambiguous(address, [rows[0]["display_name"], *far])
    time.sleep(1)    # OpenStreetMap's usage policy: one request a second
    try:
        buildings = overpass_buildings(x, y, epsg=epsg)
    except OSError as e:
        print(f"OpenStreetMap's buildings didn't load ({e}); carrying on without them")
        buildings = []
    return {"address": rows[0].get("display_name", address), "point": [round(x, 3), round(y, 3)], "parcel": None,
            "parcel_name": None, "buildings": buildings, "epsg": epsg, "lonlat": [lon, lat],
            "source": "OpenStreetMap contributors (ODbL 1.0); the lot is a stand-in, as it has no parcels"}


def lookup(address):
    """What's public about an address: from LINZ with a key, else OpenStreetMap."""
    key = linz_key()
    if key:
        return from_linz(address, key)
    print("No DOMUS_LINZ_KEY set, so using OpenStreetMap: the address goes to its servers, and the lot is a stand-in.")
    return from_osm(address)
