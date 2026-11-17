"""The lot's record of title and the easements on and around it, from LINZ: `domus title`.

Writes data/site/title.geojson. The title's outline (kind `title`) carries its number, estate (fee simple, cross lease,
unit title and so on) and type. Each easement or covenant within EASEMENT_SEARCH_M of the lot (kind `easement`) carries
its legal description and intent; a line with no width is an easement drawn along a line. The build draws them in the
Boundaries group, and wall options flag a wall that runs into an easement. Needs DOMUS_LINZ_KEY.

LINZ's parcels are a guide, not a survey, and they say what an easement is called and where, not what it allows: that's
in the easement instrument on the title. NZ Property Titles, NZ Non-Primary Parcels and NZ Non-Primary Linear Parcels,
LINZ, CC BY 4.0.
"""

import argparse
import os

from shapely.geometry import shape

from ...geo import crs, geojson
from . import marks, publicdata

TITLES, EASEMENTS, LINEAR_EASEMENTS = "layer-50804", "layer-50782", "layer-50783"
EASEMENT_SEARCH_M = 10.0
ON_LOT_M2, ON_LOT_M = 1.0, 0.3
def title_path():
    """The lot's title and easements, data/site/title.geojson."""
    return crs.ROOT / "data" / "site" / "title.geojson"


def _to_site(coords):
    if isinstance(coords[0], (int, float)):
        return [round(v, 3) for v in crs.nztm_to_site(*coords[:2])]
    return [_to_site(c) for c in coords]


def on_site(geometry):
    return {"type": geometry["type"], "coordinates": _to_site(geometry["coordinates"])}


def title_features(rows, lot):
    """The titles that hold the lot: those covering a point inside it."""
    inside = lot.representative_point()
    out = []
    for f in rows:
        if not f.get("geometry") or not shape(f["geometry"]).contains(inside):
            continue
        p = f["properties"]
        out.append({"type": "Feature", "geometry": on_site(f["geometry"]),
                    "properties": {"id": f"linz-title-{p['title_no']}", "kind": "title", "name": f"Title {p['title_no']}",
                                   "title_no": p["title_no"], "estate": p.get("estate_description"), "type": p.get("type"),
                                   "status": p.get("status"), "issued": p.get("issue_date"), "source": "LINZ NZ Property Titles",
                                   "confidence": "LINZ's record; its outline is a guide, not a survey"}})
    return out


def easement_features(rows, lot, reach=EASEMENT_SEARCH_M):
    """The current easements and covenants touching the lot or within `reach` of it."""
    near = lot.buffer(reach)
    out, seen = [], set()
    for f in rows:
        p = f["properties"]
        if not f.get("geometry") or p["id"] in seen or not shape(f["geometry"]).intersects(near):
            continue
        seen.add(p["id"])
        g = shape(f["geometry"])
        # Parcels and the curated lot can differ by some centimetres: on the lot means a real share of it, not a sliver.
        on_lot = g.intersection(lot).area > max(ON_LOT_M2, 0.02 * g.area) if g.area else g.intersects(lot.buffer(-ON_LOT_M))
        out.append({"type": "Feature", "geometry": on_site(f["geometry"]),
                    "properties": {"id": f"linz-easement-{p['id']}", "kind": "easement", "name": p.get("appellation") or "Easement",
                                   "intent": p.get("parcel_intent"), "titles": p.get("titles"), "on_lot": bool(on_lot),
                                   "area_m2": p.get("survey_area") or p.get("calc_area"), "source": "LINZ NZ Non-Primary Parcels",
                                   "note": "Where it is, not what it allows: that's in the easement instrument on the title",
                                   "confidence": "LINZ's parcel, a guide, not a survey"}})
    return out


def fetch(key):
    """title.geojson's features, from LINZ."""
    lot = marks.lot_on_nztm()
    x, y = lot.representative_point().coords[0]
    titles = title_features(publicdata._wfs(key, TITLES, publicdata._bbox_cql(x - 1, y - 1, x + 1, y + 1)), lot)
    near = marks.around(lot, EASEMENT_SEARCH_M)
    rows = publicdata._wfs(key, EASEMENTS, near, 200) + publicdata._wfs(key, LINEAR_EASEMENTS, near, 200)
    return titles + easement_features(rows, lot)


def read():
    return [f for f in geojson.read_optional(title_path()) or []]


def easements():
    return [f for f in read() if f["properties"].get("kind") == "easement"]


def main(argv=None):
    argparse.ArgumentParser(prog="domus title", description="The lot's title and the easements around it, from LINZ.").parse_args(argv)
    key = os.environ.get("DOMUS_LINZ_KEY")
    if not key:
        raise SystemExit("This needs DOMUS_LINZ_KEY: a free key from data.linz.govt.nz.")
    feats = fetch(key)
    for f in feats:
        p = f["properties"]
        if p["kind"] == "title":
            print(f"{p['name']}: {p['estate']}, {p['type']}")
        else:
            print(f"{p['name']} ({p['intent']}){', on the lot' if p['on_lot'] else ', nearby'}")
    geojson.write(title_path(), geojson.collection(feats, "title", "The lot's title and nearby easements, from LINZ (CC BY 4.0)",
                                           epsg=crs.site_epsg()))
    print(f"{len(feats)} features -> {title_path()}. Run domus build, then look in the Boundaries group.")
    return 0
