"""A site's wall options: options/_site.toml (their frame, defaults, rules and regrade area), read through the
settings schema, and what they're worked out on, read from the site: the ground, the house, the existing walls, the
services and the easements (context()). domus.scheme works them out and reads no site; its names are kept here, on
this site's frame and ground, for the site tools and tests that use them.

Options are drawn in the site frame of options/_site.toml: s along a reference boundary from its first corner, o in
from it, into the lot.
"""

import sys
import tomllib
from functools import lru_cache

import numpy as np
import shapely
from shapely.geometry import Polygon
from shapely.ops import unary_union

from . import crs, geojson, scheme, terrain
from .crs import ROOT
from .errors import DomusError
from .scheme import (  # noqa: F401 (the names site tools and tests use, as they were here)
    LIDAR_TOLERANCE_M,
    STAIR_PROUD_M,
    STAIR_RISER_M,
    STAIR_STEEP,
    STEP_LOOKS,
    SiteFrame,
    Stair,
    compass,
    drain_checks,
    easement_checks,
    flat_area,
    grid_in,
    option_wall,
    retained,
    wall_checks,
)

OPTIONS = ROOT / "options"
SITE = OPTIONS / "_site.toml"


@lru_cache
def settings():
    """options/_site.toml, at this Domus's format (domus.project.formats), read through the settings schema."""
    from .project import formats, schema

    shared = formats.options_site(tomllib.loads(SITE.read_text(encoding="utf-8")), crs.site_format())
    for warning in schema.unknown(shared, schema.OPTIONS_SITE, "options/_site.toml: "):
        print(f"Warning: {warning}", file=sys.stderr)
    return schema.Settings(shared, schema.OPTIONS_SITE, "options/_site.toml: ")


@lru_cache
def frame():
    f = settings()["frame"]
    return SiteFrame(f["origin"], f["toward"], f.get("side", "right"))


@lru_cache
def title():
    feats = geojson.read(ROOT / "data" / "site" / "boundary.geojson")["features"]
    main = crs.settings().get("boundary", {}).get("main", feats[0]["properties"]["id"])
    return Polygon(next(f for f in feats if f["properties"]["id"] == main)["geometry"]["coordinates"][0])


@lru_cache
def house_outline():
    """What the house takes of the ground: its walls and every part drawn as an area (porches, blocks such as
    decks and landings) in data/site/house.geojson. A bay window hangs from the wall above the ground
    (domus.house draws it from `bottom_above_fl` up), so the ground runs on under it and it is left out."""
    feats = geojson.read_optional(ROOT / "data" / "site" / "house.geojson")
    polys = [Polygon(np.asarray(f["geometry"]["coordinates"][0])[:, :2]) for f in feats
             if f["geometry"]["type"] == "Polygon" and f["properties"].get("part") != "bay"]
    return unary_union([p.buffer(0) for p in polys])


@lru_cache
def house_retaining_walls():
    """Walls that retain the ground against the house, such as round a lower floor dug into a slope
    (part = "retaining-walls" in data/site/house.geojson), or None."""
    from shapely.geometry import MultiLineString

    lines = []
    for f in geojson.read_optional(ROOT / "data" / "site" / "house.geojson"):
        if f["properties"].get("part") == "retaining-walls":
            g = f["geometry"]
            lines += [[c[:2] for c in part] for part in (g["coordinates"] if g["type"] == "MultiLineString" else [g["coordinates"]])]
    return MultiLineString(lines) if lines else None


def house_building():
    """The house itself (its walls and bay windows), for the checks on the ground beside its foundations."""
    feats = geojson.read_optional(ROOT / "data" / "site" / "house.geojson")
    return unary_union([Polygon(np.asarray(f["geometry"]["coordinates"][0])[:, :2]) for f in feats
                        if f["properties"].get("id") == "house-walls" or f["properties"].get("part") == "bay"])


def regrade_settings():
    """options/_site.toml [regrade]: where options may regrade the ground."""
    cfg = settings()
    if "regrade" in cfg:
        return cfg["regrade"]
    raise DomusError(f"{SITE} has no [regrade] section, which says where options may regrade the ground. See docs/options.md.")


@lru_cache
def regrade_area():
    """Where options regrade and where flat lawn is counted: the lot, or the part of it in [regrade] polygon_so
    (a polygon in the frame) or with s under s_max, less the house and a margin round it."""
    cfg = regrade_settings()
    fr = frame()
    area = title()
    if "polygon_so" in cfg:
        area = area.intersection(Polygon(fr.en(np.asarray(cfg["polygon_so"], float))))
    if "s_max" in cfg:
        s_min = -1000.0
        area = area.intersection(Polygon(fr.en(np.array([[s_min, s_min], [cfg["s_max"], s_min], [cfg["s_max"], -s_min], [s_min, -s_min]]))))
    return area.difference(house_outline().buffer(cfg.get("house_margin_m", 0.6)))


class Terrace(scheme.Terrace):
    """scheme.Terrace in this site's frame."""

    def __init__(self, cfg, defaults):
        super().__init__(cfg, defaults, frame())


class Design(scheme.Design):
    """scheme.Design on this site: its options' defaults, regrade area and frame, and its ground as it is unless
    `existing` gives another."""

    def __init__(self, opt, existing=None):
        super().__init__(opt, settings()["defaults"], existing or terrain.ground(), regrade_area(), frame())


def existing_walls():
    from . import walls as walls_mod

    feats = geojson.read_optional(ROOT / "data" / "site" / "walls-existing.geojson")
    return [walls_mod.from_feature(f) for f in feats]


@lru_cache
def obstacles():
    """Point features a wall's footing should miss: poles, posts, survey marks, lids, downpipes."""
    from . import features

    out = []
    for f in features.read_all(ROOT):
        g = f["geometry"]
        p = f["properties"]
        if g["type"] == "Point":
            out.append((p.get("name", p["id"]).split(" (")[0].lower(), *g["coordinates"][:2]))
    return tuple(out)


@lru_cache
def easement_shapes():
    """(name, intent, shape) of the easements in data/site/title.geojson (domus title)."""
    from . import title

    return tuple((f["properties"]["name"], f["properties"].get("intent"), shapely.geometry.shape(f["geometry"]))
                 for f in title.easements())


def context():
    """What this site gives its wall options to be worked out on (scheme.Context)."""
    return scheme.Context(frame(), settings(), terrain.ground(), regrade_area(), regrade_settings(), existing_walls(),
                          house_outline(), house_building(), house_retaining_walls(), obstacles(), easement_shapes(),
                          geojson.read_optional(ROOT / "data" / "site" / "services.geojson"),
                          crs.settings().get("services", {}))


def evaluate(opt, existing=None, step=0.1):
    """Everything the viewer and the comparison table need about one option: scheme.evaluate on this site."""
    return scheme.evaluate(opt, context(), existing, step)
