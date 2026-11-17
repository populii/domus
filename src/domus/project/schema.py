"""The settings schema: every key data/site.toml and options/_site.toml can hold, with its default and a line of help.

Code reads settings through it. crs.settings() and design.settings() give a Settings, which answers a key a site
leaves out with the schema's default, so a default lives here and in no other place. A key a site writes that the
schema doesn't know gets a warning naming the nearest one it does. In the tests (DOMUS_STRICT_SETTINGS=1), reading a
key the schema doesn't declare, or giving one a default other than the schema's, is an error, so the schema can't fall
behind the code. docs/site-format.md's tables of settings are written from it (tools/settings_doc.py).
"""

import difflib
import os

REQUIRED = "required"          # no default: Domus can't do without it (or without its section)
COMPUTED = "worked out"        # a default worked out from other settings or the site's data: the help says how


class Key:
    """One setting."""

    def __init__(self, help, default=None):
        self.help, self.default = help, default


class Table:
    """A table of settings with known keys."""

    def __init__(self, help, keys):
        self.help, self.keys = help, keys


class Map:
    """A table whose keys the site names (a drain's id, a datum's), each holding a `value`; `known` keys mean
    something of their own."""

    def __init__(self, help, value, name="name", known=None):
        self.help, self.value, self.name, self.known = help, value, name, known or {}


class Tables:
    """An array of tables, [[like.this]], each a `item`."""

    def __init__(self, help, item):
        self.help, self.item = help, item


LINZ = "Ground model, surface model and aerial photo: LINZ and its data partners (CC BY 4.0)."

SITE = Table("data/site.toml", {
    "domus": Table("The site folder itself", {
        "format": Key("The site folder's format. `domus new` and `domus create` write it; every Domus reads older ones, "
                      "and `domus site upgrade` writes a site up to its own", 1),
        "requires": Key('The oldest Domus that builds the site, such as ">=2026.12.0"'),
    }),
    "site": Table("The viewer's text", {
        "title": Key("The site's name", "Site model"),
        "heading": Key("The viewer's heading: the title, unless given", COMPUTED),
        "subtitle": Key("A line under the heading", ""),
        "about": Key("A paragraph about the site", ""),
        "attribution": Key("Where the data came from, shown in the viewer", LINZ),
    }),
    "crs": Table("The site's grid and heights", {
        "epsg": Key("The grid's EPSG code", 2193),
        "label": Key("The grid's name for people: its own name and EPSG code, unless given", COMPUTED),
        "vertical": Key("The height datum's name", "NZVD2016"),
    }),
    "boundary": Table("The lot", {
        "main": Key("The id of the feature in boundary.geojson that is the lot, the first unless given; the others are "
                    "neighbours, rights of way and the like", COMPUTED),
    }),
    "model": Table("The area modelled", {
        "extent": Key("E min, N min, E max, N max on the site's grid", REQUIRED),
        "terrain_step_m": Key("The ground's grid step, in metres", REQUIRED),
        "origin": Key("The viewer's origin: E, N and optionally a height (the ground's, unless given)", REQUIRED),
    }),
    "rasters": Table("Other paths for the site's rasters", {
        "dem": Key("The ground model", "data/lidar/dem.tif"),
        "dsm": Key("The surface model", "data/lidar/dsm.tif"),
        "aerial": Key("The aerial photo", "data/aerial/aerial.tif"),
        "nir": Key("The near-infrared photo, red and near-infrared bands", "data/aerial/nir.tif"),
        "canopy": Key("The tree crowns `domus canopy` maps", "data/sources/canopy.geojson"),
        "shift_m": Map("Moves a raster from where its file puts it: `aerial = [east, north]` in metres, for a photo "
                       "that sits off the ground model and the house", Key("[east, north] in metres", [0.0, 0.0]), "raster"),
    }),
    "aerial": Table("How the aerial photo is named in the viewer", {
        "key": Key("Its texture's key", "aerial"),
        "label": Key("Its name", "Aerial photo"),
        "label_raw": Key("Its name before the paving under trees is painted in", "Aerial photo as taken"),
    }),
    "drapes": Tables("A scanned plan to lay on the ground", Table("A drape", {
        "key": Key("Its texture's key", REQUIRED),
        "label": Key("Its name in the viewer", REQUIRED),
        "georef": Key("Its georeference, in data/georef/", REQUIRED),
        "px": Key("Metres a pixel of the drape covers", 0.05),
        "shift_m": Key("[east, north]: moves it from where its georeference puts it", [0.0, 0.0]),
    })),
    "datums": Map("Another height datum your plans use, where their level = model height + offset; the viewer reads "
                  "points in it", Table("A datum", {
                      "label": Key("Its name", REQUIRED),
                      "offset_m": Key("Its level less the model's height", REQUIRED),
                      "uncertainty_m": Key("How sure the offset is, in metres: for the record"),
                  }), "datum"),
    "survey": Table("Survey marks in data/survey/control.csv", {
        "datum": Key("The datum of their levels, one of [datums]"),
        "level_column": Key("The column of their levels", "level"),
    }),
    "house": Table("The house, with house.geojson", {
        "fl_upper": Key("The floor level openings and parts are measured from", REQUIRED),
        "fl_upper_uncertainty_m": Key("How sure fl_upper is, in metres: for the record"),
        "fl_lower": Key("A lower floor's level: fl_upper, unless given", COMPUTED),
        "roof_pitch_deg": Key("The roof's pitch, in degrees", REQUIRED),
        "roof_plane_at_wall": Key("The roof plane's height over the outline (its lowest point, for a skillion or gable)", REQUIRED),
        "eaves_overhang_m": Key("How far the eaves stand out from the walls", REQUIRED),
        "roof_form": Key("hip, skillion (one plane rising towards roof_azimuth_deg) or gable (two planes, their ridge "
                         "on that bearing)", "hip"),
        "roof_azimuth_deg": Key("The bearing a skillion rises towards, or a gable's ridge runs on"),
        "roof_ridge_offset_m": Key("How far right of the outline's centre a gable's ridge runs", 0.0),
        "roof_colour": Key("The roof's colour: a palette key or hex", "roof"),
        "board_m": Key("The weatherboards' size from one lap to the next, counted up from the plastered base's top", 0.15),
        "boards_on_photos": Key("Draw the boards over the walls' photos too", False),
        "cladding": Map("face = [above the floor line, below it] in palette keys; `default` for the faces it doesn't name",
                        Key("[above, below]: the `default` entry's, unless given", COMPUTED), "face",
                        known={"default": Key("The faces it doesn't name", ["boards-beige", "boards-beige"])}),
        "trim": Table("A band at the floor line and facings round the windows below it", {
            "colour": Key("Its colour"),
            "faces": Key("The faces that have it", []),
        }),
        "layers": Map("The house's viewer layers: a name and a colour for each", Table("A layer", {
            "name": Key("Its name: the layer's own, unless given", COMPUTED),
            "colour": Key("Its colour in the layer list: the layer's own, unless given", COMPUTED),
        }), "layer"),
        "roof_name_note": Key("Words after the roof layer's name", ""),
        "roof_source": Key("Where the roof's shape came from", "the house's outline and [house] in data/site.toml"),
        "walls_source_note": Key("Words after the walls' source", ""),
        "roof_at_wall_above_fl": Key("The roof plane at the wall, above fl_upper, for placing videos on the house"),
    }),
    "services": Table("Drains (services.geojson)", {
        "assumed_depth_m": Map("A depth to draw a drain at, below the ground, when it has no levels",
                               Key("Metres to its invert"), "drain"),
        "colours": Map("A drain's colour", Key("Hex", "#2f2f2f"), "drain"),
        "lids": Key("The inspection points whose lids an option must reset where it moves the ground", []),
        "default_note": Table("What a drain's levels rest on, unless its own note says", {
            "text": Key("The note, which can use {lo}, {hi} and {span}"),
            "confidence": Key("How far to trust the levels"),
        }),
        "notes": Map("What one drain's levels rest on", Table("A note", {
            "text": Key("The note, which can use {lo}, {hi} and {span}"),
            "confidence": Key("How far to trust the levels"),
        }), "drain"),
    }),
    "ifc": Table("A designer's model as extra layers (domus prep ifc)", {
        "meta": Key("Its meta file", REQUIRED),
        "meshes": Key("Its meshes file", REQUIRED),
        "name": Key("The layers' name", REQUIRED),
        "id_prefix": Key("The prefix of their ids", REQUIRED),
        "group": Key("Their group in the layer list", REQUIRED),
        "source": Key("Where the model came from", REQUIRED),
        "note": Key("A note on it", REQUIRED),
    }),
    "labels": Table("The names of some of the viewer's layers", {
        "ground": Key("The ground", "Ground (LiDAR, with walls cut in)"),
        "marks": Key("Survey marks and drain labels: their name with the heights' datum, unless given", COMPUTED),
        "points": Key("The point cloud from the videos", "Point cloud from the videos"),
        "dsm": Key("The surface model", "LiDAR surface (trees and roofs), 1 m"),
        "lidar": Key("What the LiDAR is called in other layers' names", "LiDAR"),
        "readings": Key("Readings from the clips", "Readings from the clips"),
        "features_source": Key("The site features' source", "data/site/features-*.geojson (each feature's source and basis)"),
        "bed_floor_source": Key("The beds' floors' source", "Outlines as the beds in data/site/trees.geojson"),
    }),
    "views": Map('Extra view buttons: "Name" = { pos = [x, y, z], target = [x, y, z] } in viewer metres (x east, y up, '
                 'z south of the origin)', Table("A view", {
                     "pos": Key("Where the camera stands", REQUIRED),
                     "target": Key("What it looks at", REQUIRED),
                 }), "view"),
    "enrich": Table("The viewer's checklist and video walk", {
        "enabled": Key("Show them", True),
    }),
    "local": Map("Anything a site keeps for its own tools or records: Domus doesn't read it or warn about it",
                 Key("Anything"), "key"),
    "video": Table("Point clouds from domus video", {
        "clouds": Key("The placed clouds to show (data/video/<name>.npz)", []),
        "keep": Map("[E min, N min, E max, N max]: only this part of a cloud is trusted", Key("A box"), "cloud"),
        "compare": Tables("Two placements of a clip, shown as clouds side by side", Table("A comparison", {
            "id": Key("Its id", REQUIRED),
            "name": Key("Its name", REQUIRED),
            "clouds": Key("The clouds it moves", REQUIRED),
            "pivot": Key("[E, N] it turns about", REQUIRED),
            "dz_m": Key("How far it moves up", 0.0),
            "turn_deg": Key("How far it turns, counter-clockwise", 0.0),
        })),
        "question_marks": Tables("A clip's reading of the house's levels, as a label", Table("A question mark", {
            "id": Key("Its id", REQUIRED),
            "text": Key("Its label", REQUIRED),
            "picks": Key("The picks it reads", REQUIRED),
            "drawn": Key("What it's drawn on"),
            "lift_m": Key("How far above that it stands", 0.15),
        })),
    }),
})

OPTIONS_SITE = Table("options/_site.toml", {
    "frame": Table("The frame options are drawn in: s along a boundary, o in from it", {
        "origin": Key("Where s starts, on the site's grid", REQUIRED),
        "toward": Key("A point s runs towards", REQUIRED),
        "side": Key("The lot's side of the line, looking along it", "right"),
    }),
    "regrade": Table("Where options may regrade the ground and flat lawn is counted: the lot, less the house and a "
                     "margin round it", {
                         "house_margin_m": Key("The margin round the house", 0.6),
                         "flat_slope": Key("The slope that still counts as flat lawn (rise over run)", 0.05),
                         "s_max": Key("Only where s is under this"),
                         "polygon_so": Key("Only inside this polygon, in (s, o)"),
                     }),
    "defaults": Table("What options' walls and terraces take unless they say", {
        "upstand_m": Key("How far a wall's top stands above the ground it retains", REQUIRED),
        "batter": Key("Unretained terrace edges slope back at 1 vertical : this horizontal", REQUIRED),
        "thickness_m": Key("A wall's thickness", REQUIRED),
    }),
    "rules": Table("Checks every option is held to", {
        "drain_cover_m": Key("Least cover over a drain under lawn after a cut", 0.3),
    }),
})


STRICT = os.environ.get("DOMUS_STRICT_SETTINGS") == "1"
_UNSET = object()


class UndeclaredSetting(LookupError):
    """Code read a setting the schema doesn't declare (only in strict mode, as in the tests)."""


def _same(a, b):
    return list(a) == list(b) if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)) else a == b


class Settings(dict):
    """A table of settings as read, that knows its schema: a key the file leaves out reads as the schema's default."""

    def __init__(self, data, node, where):
        super().__init__(data)
        self.node, self.where = node, where

    def _child(self, key):
        if isinstance(self.node, Map):
            return self.node.known.get(key, self.node.value)
        child = self.node.keys.get(key) if isinstance(self.node, Table) else None
        if child is None and STRICT and isinstance(self.node, Table):
            raise UndeclaredSetting(f"{self.where}{key} isn't in the settings schema (domus.project.schema): declare it there")
        return child

    def _wrap(self, value, child, key):
        if isinstance(value, dict) and isinstance(child, (Table, Map)):
            return Settings(value, child, f"{self.where}{key}.")
        if isinstance(value, list) and isinstance(child, Tables):
            return [Settings(v, child.item, f"{self.where}{key}[].") if isinstance(v, dict) else v for v in value]
        return value

    def get(self, key, default=_UNSET):
        child = self._child(key)
        if dict.__contains__(self, key):
            return self._wrap(dict.__getitem__(self, key), child, key)
        if isinstance(child, Key):
            own = child.default
            if default is _UNSET:
                return None if own in (REQUIRED, COMPUTED) else own
            if STRICT and own not in (REQUIRED, COMPUTED) and not _same(default, own):
                raise UndeclaredSetting(f"{self.where}{key}: the code's default {default!r} isn't the schema's, {own!r}")
            return default
        if default is _UNSET:
            default = [] if isinstance(child, Tables) else {} if isinstance(child, (Table, Map)) else None
        return self._wrap(default, child, key)

    def __getitem__(self, key):
        child = self._child(key)
        if dict.__contains__(self, key):
            return self._wrap(dict.__getitem__(self, key), child, key)
        if isinstance(child, Key) and child.default not in (REQUIRED, COMPUTED, None):
            return child.default
        raise KeyError(key)


def unknown(data, node, where=""):
    """Warnings for the keys in `data` the schema (`node`) doesn't know, each naming the nearest known key."""
    out = []
    if isinstance(node, Map):
        for k, v in data.items():
            child = node.known.get(k, node.value)
            if isinstance(v, dict) and isinstance(child, (Table, Map)):
                out += unknown(v, child, f"{where}{k}.")
        return out
    if not isinstance(node, Table):
        return out
    for k, v in data.items():
        child = node.keys.get(k)
        if child is None:
            near = difflib.get_close_matches(k, node.keys, n=1, cutoff=0.8)
            out.append(f"{where}{k} isn't a setting Domus knows" + (f": did you mean {near[0]}?" if near else "."))
        elif isinstance(v, dict) and isinstance(child, (Table, Map)):
            out += unknown(v, child, f"{where}{k}.")
        elif isinstance(v, list) and isinstance(child, Tables):
            for item in v:
                if isinstance(item, dict):
                    out += unknown(item, child.item, f"{where}{k}[].")
    return out
