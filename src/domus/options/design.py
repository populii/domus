"""A site's wall options: options/_site.toml (their frame, defaults, rules and regrade area), read through the
settings schema, and what they're worked out on, read from the site each is handed: the ground, the house, the
existing walls, the services and the easements (context()). domus.scheme works them out and reads no site; its names
are kept here, on a site's frame and ground, for the site tools and tests that use them. Those call settings(),
frame(), Terrace and Design without a site (deprecated, and they say so), and then read the current one.

Options are drawn in the site frame of options/_site.toml: s along a reference boundary from its first corner, o in
from it, into the lot.
"""

import sys
import tomllib

import numpy as np
import shapely
from shapely.geometry import Polygon
from shapely.ops import unary_union

from ..errors import DomusError
from ..geo import geojson
from ..model import terrain
from ..project.site import cached, given
from . import scheme
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


def options_dir(site):
    """The site's wall options, options/."""
    return site.path / "options"
def options_site_path(site):
    """The options' shared settings, options/_site.toml."""
    return options_dir(site) / "_site.toml"


@cached
def settings(site):
    """options/_site.toml, at this Domus's format (domus.project.formats), read through the settings schema."""
    from ..project import formats, schema

    shared = formats.options_site(tomllib.loads(options_site_path(site).read_text(encoding="utf-8")), site.format())
    for warning in schema.unknown(shared, schema.OPTIONS_SITE, "options/_site.toml: "):
        print(f"Warning: {warning}", file=sys.stderr)
    return schema.Settings(shared, schema.OPTIONS_SITE, "options/_site.toml: ")


@cached
def frame(site):
    f = settings(site)["frame"]
    return SiteFrame(f["origin"], f["toward"], f.get("side", "right"))


@cached
def title(site):
    feats = geojson.read(site.path / "data" / "site" / "boundary.geojson")["features"]
    main = site.settings().get("boundary", {}).get("main", feats[0]["properties"]["id"])
    return Polygon(next(f for f in feats if f["properties"]["id"] == main)["geometry"]["coordinates"][0])


@cached
def house_outline(site):
    """What the house takes of the ground: its walls and every part drawn as an area (porches, blocks such as
    decks and landings) in data/site/house.geojson. A bay window hangs from the wall above the ground
    (domus.house draws it from `bottom_above_fl` up), so the ground runs on under it and it is left out."""
    feats = geojson.read_optional(site.path / "data" / "site" / "house.geojson")
    polys = [Polygon(np.asarray(f["geometry"]["coordinates"][0])[:, :2]) for f in feats
             if f["geometry"]["type"] == "Polygon" and f["properties"].get("part") != "bay"]
    return unary_union([p.buffer(0) for p in polys])


@cached
def house_retaining_walls(site):
    """Walls that retain the ground against the house, such as round a lower floor dug into a slope
    (part = "retaining-walls" in data/site/house.geojson), or None."""
    from shapely.geometry import MultiLineString

    lines = []
    for f in geojson.read_optional(site.path / "data" / "site" / "house.geojson"):
        if f["properties"].get("part") == "retaining-walls":
            g = f["geometry"]
            lines += [[c[:2] for c in part] for part in (g["coordinates"] if g["type"] == "MultiLineString" else [g["coordinates"]])]
    return MultiLineString(lines) if lines else None


def house_building(site):
    """The house itself (its walls and bay windows), for the checks on the ground beside its foundations."""
    feats = geojson.read_optional(site.path / "data" / "site" / "house.geojson")
    return unary_union([Polygon(np.asarray(f["geometry"]["coordinates"][0])[:, :2]) for f in feats
                        if f["properties"].get("id") == "house-walls" or f["properties"].get("part") == "bay"])


def regrade_settings(site):
    """options/_site.toml [regrade]: where options may regrade the ground."""
    cfg = settings(site)
    if "regrade" in cfg:
        return cfg["regrade"]
    raise DomusError(f"{options_site_path(site)} has no [regrade] section, which says where options may regrade the ground. See docs/options.md.")


@cached
def regrade_area(site):
    """Where options regrade and where flat lawn is counted: the lot, or the part of it in [regrade] polygon_so
    (a polygon in the frame) or with s under s_max, less the house and a margin round it."""
    cfg = regrade_settings(site)
    fr = frame(site)
    area = title(site)
    if "polygon_so" in cfg:
        area = area.intersection(Polygon(fr.en(np.asarray(cfg["polygon_so"], float))))
    if "s_max" in cfg:
        s_min = -1000.0
        area = area.intersection(Polygon(fr.en(np.array([[s_min, s_min], [cfg["s_max"], s_min], [cfg["s_max"], -s_min], [s_min, -s_min]]))))
    return area.difference(house_outline(site).buffer(cfg.get("house_margin_m", 0.6)))


class Terrace(scheme.Terrace):
    """scheme.Terrace in the frame of `site`."""

    def __init__(self, cfg, defaults, site=None):
        super().__init__(cfg, defaults, frame(given(site)))


class Design(scheme.Design):
    """scheme.Design on `site`: its options' defaults, regrade area and frame, and its ground as it is (the ground as
    the build draws it, terrain.existing()) unless `existing` gives another."""

    def __init__(self, opt, existing=None, site=None):
        site = given(site)
        super().__init__(opt, settings(site)["defaults"], existing or terrain.existing(site), regrade_area(site), frame(site))


def existing_walls(site):
    from ..model import walls as walls_mod

    feats = geojson.read_optional(site.path / "data" / "site" / "walls-existing.geojson")
    return [walls_mod.from_feature(f) for f in feats]


@cached
def obstacles(site):
    """Point features a wall's footing should miss: poles, posts, survey marks, lids, downpipes."""
    from ..model import features

    out = []
    for f in features.read_all(site.path):
        g = f["geometry"]
        p = f["properties"]
        if g["type"] == "Point":
            out.append((p.get("name", p["id"]).split(" (")[0].lower(), *g["coordinates"][:2]))
    return tuple(out)


@cached
def easement_shapes(site):
    """(name, intent, shape) of the easements in data/site/title.geojson (domus title)."""
    from ..importers.nz import title

    return tuple((f["properties"]["name"], f["properties"].get("intent"), shapely.geometry.shape(f["geometry"]))
                 for f in title.easements(site))


def context(site):
    """What the site gives its wall options to be worked out on (scheme.Context). The ground is today's best, as the
    build draws it: the ground fixes and the existing walls' faces and tops where there are some, the LiDAR elsewhere
    (terrain.existing())."""
    return scheme.Context(frame(site), settings(site), terrain.existing(site), regrade_area(site), regrade_settings(site),
                          existing_walls(site), house_outline(site), house_building(site), house_retaining_walls(site),
                          obstacles(site), easement_shapes(site),
                          geojson.read_optional(site.path / "data" / "site" / "services.geojson"),
                          site.settings().get("services", {}))


def evaluate(site, opt, existing=None, step=0.1):
    """Everything the viewer and the comparison table need about one option: scheme.evaluate on the site."""
    return scheme.evaluate(opt, context(site), existing, step)
