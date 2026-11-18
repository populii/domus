"""The site's house as every part of Domus reads it: house.geojson's features and [house]'s settings, from where
[house] source says they are.

`house(site)` gives a House: its `source` ("drawn" or "ifc"), its `features` (house.geojson's: the walls, the feature
with id "house-walls", their openings and the house's other parts, as docs/site-format.md describes them) and its
`settings` ([house]). With the house drawn, the default, they are house.geojson and [house] as they are. With
[house] source = "ifc", the house's IFC file (data/house.ifc, domus.model.house_ifc) gives them: the walls' outline
from its wall faces where they stand now, the floor levels from its storeys, each face's id and plinth, the openings,
the house's parts and the roof's numbers from their elements, each read through where its element stands now
(house_ifc.description). [house]'s settings that say how the house looks, rather than its shape (SHAPE), are
[house]'s either way, and house.geojson isn't read.

Every part of Domus that reads the house reads it here: the build (its house layers, its numbers in summary.json, the
walk route and what the viewer snaps to), the wall options' regrade area and house margin, the model's checks, the
observations' plinths, placing videos, the overlays, the house's textures and the canopy's map.

`drawn(site)` is the house as house.geojson and [house] draw it, whatever the source: what domus house ifc writes to
the IFC. `fingerprint(site)` is what that write records of them, and `drift(site)` says, for the build to warn, when
they've changed since, while the IFC is the master.
"""

import copy
import hashlib
import json
from dataclasses import dataclass

from ..geo import geojson

FILE = "data/site/house.geojson"
WALLS = "house-walls"
# [house]'s settings that give the house's shape: the IFC holds them while it's the house's master. The rest of
# [house] says how the house looks, and where it's drawn from.
SHAPE = ("fl_upper", "fl_lower", "roof_pitch_deg", "roof_plane_at_wall", "eaves_overhang_m", "roof_form",
         "roof_azimuth_deg", "roof_ridge_offset_m", "roof_at_wall_above_fl")
SETTINGS = "data/site.toml [house]"         # how a write's record names [house]'s shape settings


@dataclass
class House:
    """The house as its source gives it: `source` ("drawn" or "ifc"), `features` (house.geojson's, a list of GeoJSON
    features) and `settings` ([house], read through the settings schema)."""

    source: str
    features: list
    settings: dict

    @property
    def walls(self):
        """The walls: the feature with id "house-walls", or None for a site with no house."""
        return next((f for f in self.features if f["properties"].get("id") == WALLS), None)

    def by_id(self):
        """The features by their ids."""
        return {f["properties"].get("id"): f for f in self.features}

    def parts(self, *kinds):
        """The house's parts of the `kinds` given (each a feature's `part`, such as "porch")."""
        return [f for f in self.features if f["properties"].get("part") in kinds]

    def model(self, ground):
        """The house drawn (domus.model.house.build): its faces, solids and roof, `ground(e, n)` giving the heights of
        the ground under it. A site with no house has none: a DomusError."""
        from ..errors import DomusError
        from . import house

        if self.walls is None:
            raise DomusError("This site has no house: house-walls in data/site/house.geojson")
        h = self.settings
        return house.build(self.features, h["fl_upper"], h["roof_pitch_deg"], h["roof_plane_at_wall"],
                           h["eaves_overhang_m"], ground, **house.roof_options(h))


def house(site):
    """The site's house, as [house] source says to read it now: a House, its features the caller's own to change.
    house.geojson and [house] are read as they are at each call; the IFC's description is read once for each state of
    the file (by its SHA-256), and kept on the site."""
    from ..project import schema
    from . import house_ifc

    given = site.settings().get("house")
    if house_ifc.source(site) != "ifc":
        return House("drawn", geojson.read_optional(site.path / FILE), given)
    features, shape = _description(site)
    merged = {k: v for k, v in dict.items(given) if k not in SHAPE}
    merged.update(shape)
    return House("ifc", copy.deepcopy(features), schema.Settings(merged, given.node, given.where))


def _description(site):
    """house_ifc.description(site), kept on the site for the file as it is: a file changed since is read again."""
    from . import house_ifc

    p = house_ifc.path(site)
    name = f"{__name__}.description"
    key = (name, hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None, ())
    if key not in site.cache:
        for old in [k for k in site.cache if k[0] == name]:
            del site.cache[old]
        site.cache[key] = house_ifc.description(site)
    return site.cache[key]


def drawn(site):
    """The house as house.geojson and [house] draw it, whatever [house] source says: what domus house ifc writes."""
    return House("drawn", geojson.read_optional(site.path / FILE), site.settings().get("house"))


def shape_settings(site):
    """[house]'s settings that give the house's shape, as data/site.toml gives them (left out where it doesn't)."""
    given = site.settings().get("house")
    return {k: dict.__getitem__(given, k) for k in SHAPE if dict.__contains__(given, k)}


def fingerprint(site):
    """What the house's IFC is written from, as the write's record keeps it: house.geojson's SHA-256 (None without
    one) and that of [house]'s shape settings."""
    p = site.path / FILE
    return {FILE: hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None,
            SETTINGS: hashlib.sha256(json.dumps(shape_settings(site), sort_keys=True).encode("utf-8")).hexdigest()}


def drift(site):
    """Warnings, while the IFC is the house's master ([house] source = "ifc"), for house.geojson and [house]'s shape
    settings changed since the IFC was last written from them (by the latest write still in effect that recorded them):
    the build doesn't read them, so a change there is one the model doesn't show."""
    from ..project import changes
    from . import house_ifc

    if house_ifc.source(site) != "ifc":
        return []
    written = None
    for r in changes.records(site):
        for f in r["files"]:
            if r["in_effect"] and f["path"] == house_ifc.FILE and f.get("drawn_from"):
                written = f["drawn_from"]
    if written is None:
        return []
    now = fingerprint(site)
    out = []
    if FILE in written and now[FILE] != written[FILE]:
        out.append(f"Warning: {FILE} has changed since the house's IFC was written, and with [house] source = \"ifc\" "
                   "the build doesn't read it: edit the house through the IFC (domus site edit), or write the IFC again "
                   "(domus house ifc --force)")
    if SETTINGS in written and now[SETTINGS] != written[SETTINGS]:
        out.append("Warning: [house]'s settings of the house's shape in data/site.toml (its floor levels and the roof's "
                   "numbers) have changed since the house's IFC was written, and with [house] source = \"ifc\" the build "
                   "takes them from the IFC: edit the house through the IFC (domus site edit), or write the IFC again "
                   "(domus house ifc --force)")
    return out
