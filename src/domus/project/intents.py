"""Intents: small JSON objects that each say one change to a site's data, such as "move wall W1 0.5 m east" or "set
this level to 42.10" (docs/changes.md). domus.project.changes applies them to the site's files, records them and undoes
them; this module checks one, applies it to the data it changes, and works out what puts it back exactly.

An intent on a layer names it by `layer`, its file's name in data/site/ without .geojson, and its features by `id`:

    {"kind": "move", "layer": "walls-existing", "ids": ["W2", "W3"], "by": [0.12, -0.05], "basis": "..."}
        moves the named features ("ids": "*" for all of them) by [dE, dN], or [dE, dN, dH] to move their heights too
    {"kind": "coords", "layer": "house", "id": "window-3", "so": [[10.0, 4.3], [11.1, 4.3]]}
        sets a feature's vertices: "so" or "soz" in the wall options' frame, "en" or "enz" on the site's grid ("so"
        and "en" keep each vertex's own height when the feature has heights and the number of its vertices doesn't
        change; a polygon's ring is given open or closed and written closed), or "geometry", a whole GeoJSON geometry;
        with "vertex", one point sets that vertex alone
    {"kind": "level", "layer": "ground-fixes", "id": "lawn-ground", "vertex": 3, "z": 42.1}
        sets a vertex's height (counted from 0 along a line or a polygon's outer ring); without "vertex", every
        vertex's; "z" as a list gives each vertex its own
    {"kind": "props", "layer": "house", "id": "house-walls", "set": {"confidence": "measured"}, "drop": ["note"]}
        sets and drops properties
    {"kind": "add", "layer": "ground-fixes", "feature": {"type": "Feature", "properties": {"id": "fix-7"}, ...}}
        adds a feature, on the site's grid, at the end of the layer or at "at" in it; a layer that isn't there yet
        is started
    {"kind": "delete", "layer": "features-demo", "id": "old-fence"}
    {"kind": "put", "layer": "walls-existing", "id": "W1", "feature": {...}}
        sets a feature whole, as given: what an undo does to put one back as it was
    {"kind": "layer", "layer": "title", "collection": {"type": "FeatureCollection", "features": [...], ...}}
        writes a layer whole, as given: what domus marks and domus title do; a layer that isn't there yet is started

`move`, `coords` and `level` can also give a "basis": a sentence added to each feature they change's own basis.

An intent on the house kept as an IFC file, data/house.ifc, names its element or storey by its GlobalId or Domus id,
and IfcOpenShell applies it (domus.project.ifc_intents): ifc-move, ifc-rotate, ifc-storey, ifc-attribute,
ifc-property, ifc-add and ifc-delete.

An intent on a setting names its key as docs/site-format.md does:

    {"kind": "setting", "key": "house.fl_upper", "value": 45.0}
        sets a setting in data/site.toml ("file": "options/_site.toml" for the options' shared settings), keeping
        the file's comments; "drop": true takes it out instead, and "toml" says how the value is written

An intent on a wall option names it by `id`, its file's name in options/ without .toml, and writes its file whole:

    {"kind": "option", "id": "sketch-two-walls", "toml": "name = ..."}
        writes options/<id>.toml as given, which has to be TOML: what the viewer's Make it an option does; "drop": true
        takes the file away instead

Each intent's inverse, which applying it works out, puts back exactly what it changed: the features as they were, whole
and where they were in their layer, a layer or an option's file as it was, and a setting's value as the file wrote it.
"""

import copy
import json
import math
import re
import tomllib
from pathlib import Path

from ..errors import DomusError
from . import ifc_intents, schema

KINDS = ("move", "coords", "level", "props", "add", "delete", "put", "layer", "setting", "option", *ifc_intents.KINDS)
LAYER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*")
SETTINGS = {"data/site.toml": schema.SITE, "options/_site.toml": schema.OPTIONS_SITE}
COORDS = ("so", "soz", "en", "enz", "geometry")
# How deep a geometry's coordinates nest below their positions.
DEPTH = {"Point": 0, "LineString": 1, "MultiPoint": 1, "Polygon": 2, "MultiLineString": 2, "MultiPolygon": 3}
_MISSING = object()


def is_intents(value):
    """Whether `value` is an intent (a dict with its kind) or a non-empty list of them."""
    if isinstance(value, dict):
        return "kind" in value
    return isinstance(value, list) and bool(value) and all(isinstance(i, dict) and "kind" in i for i in value)


def load(value):
    """`value` as it was given, or read from the JSON file it names."""
    if not isinstance(value, (str, Path)):
        return value
    try:
        return json.loads(Path(value).read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise DomusError(f"There's no {value}") from None
    except ValueError as e:
        raise DomusError(f"{value} isn't JSON: {e}") from None


def read(value):
    """The intents in `value`: one intent, a list of them, or a JSON file holding either. Each is checked, and a copy."""
    value = load(value)
    if not is_intents(value):
        raise DomusError("A change is an intent, a JSON object with its kind (docs/changes.md), or a list of them")
    return [checked(i) for i in ([value] if isinstance(value, dict) else value)]


def _number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _position(p):
    return isinstance(p, list) and len(p) >= 2 and all(_number(v) for v in p)


def _nested(c, depth):
    """Whether `c` is positions nested `depth` deep, none of them empty."""
    if depth == 0:
        return _position(c)
    return isinstance(c, list) and bool(c) and all(_nested(x, depth - 1) for x in c)


def _geometry(g):
    return isinstance(g, dict) and g.get("type") in DEPTH and _nested(g.get("coordinates"), DEPTH[g["type"]])


def _feature_ok(f):
    return (isinstance(f, dict) and f.get("type") == "Feature" and isinstance(f.get("properties"), dict)
            and isinstance(f["properties"].get("id"), str) and f["properties"]["id"] != "" and _geometry(f.get("geometry")))


def _plain(v):
    """Whether `v` is a value a setting can take: a number, text, true or false, or a list of them."""
    if isinstance(v, list):
        return all(_plain(x) for x in v)
    return _number(v) or isinstance(v, (str, bool))


def checked(intent):
    """A copy of `intent`, its shape checked (a DomusError says what's wrong), a layer's file name taken as its name."""
    kind = intent.get("kind")
    if kind not in KINDS:
        raise DomusError(f"{kind!r} isn't a kind of change: the kinds are {', '.join(KINDS)}")
    if kind in ifc_intents.KINDS:
        return ifc_intents.checked(intent)
    i = copy.deepcopy(intent)
    what = f"a {kind} intent"
    if kind == "setting":
        i.setdefault("file", "data/site.toml")
        if i["file"] not in SETTINGS:
            raise DomusError(f"{what}: file is one of {', '.join(SETTINGS)}, not {i['file']!r}")
        if not isinstance(i.get("key"), str) or not re.fullmatch(r"[A-Za-z0-9_-]+(\.[A-Za-z0-9_-]+)*", i["key"]):
            raise DomusError(f"{what}: key names a setting, such as house.fl_upper")
        if i.get("drop"):
            if "value" in i:
                raise DomusError(f"setting {i['key']}: give a value or drop it, not both")
        elif "value" not in i or not _plain(i["value"]):
            raise DomusError(f"setting {i['key']}: give its value, a number, text, true or false or a list of them, "
                             "or drop: true")
        if "toml" in i and not isinstance(i["toml"], str):
            raise DomusError(f"setting {i['key']}: toml is the value as TOML writes it, as text")
        return i
    if kind == "option":
        if isinstance(i.get("id"), str):
            i["id"] = i["id"].removesuffix(".toml")
        if not isinstance(i.get("id"), str) or not LAYER.fullmatch(i["id"]):
            raise DomusError(f"{what}: id names the option, its file's name in options/ without .toml, such as "
                             "b-two-tiers")
        if i.get("drop"):
            if "toml" in i:
                raise DomusError(f"option {i['id']}: give its toml or drop it, not both")
            return i
        if not isinstance(i.get("toml"), str):
            raise DomusError(f"option {i['id']}: toml is the option's file, as text, or drop: true takes it away")
        try:
            tomllib.loads(i["toml"])
        except tomllib.TOMLDecodeError as e:
            raise DomusError(f"option {i['id']}: its toml isn't TOML: {e}") from None
        return i
    layer = i.get("layer")
    if isinstance(layer, str):
        layer = i["layer"] = layer.removesuffix(".geojson")
    if not isinstance(layer, str) or not LAYER.fullmatch(layer):
        raise DomusError(f"{what}: {layer!r} isn't a layer's name: give its file's name in data/site/, such as walls-existing")
    if "basis" in i and (kind not in ("move", "coords", "level") or not isinstance(i["basis"], str)):
        raise DomusError(f"{what}: basis is a sentence, for move, coords and level")
    if kind == "layer":
        fc = i.get("collection")
        if not (isinstance(fc, dict) and fc.get("type") == "FeatureCollection" and isinstance(fc.get("features"), list)
                and all(_feature_ok(f) for f in fc["features"])):
            raise DomusError(f"{what} for {layer}: collection is a GeoJSON FeatureCollection, each of its features with "
                             "an id in its properties and a geometry")
        return i
    if kind == "move":
        ids, by = i.get("ids"), i.get("by")
        if ids != "*" and not (isinstance(ids, list) and ids and all(isinstance(x, str) for x in ids)):
            raise DomusError(f"{what}: ids is a list of the features' ids, or \"*\" for all of them")
        if not (isinstance(by, list) and len(by) in (2, 3) and all(_number(v) for v in by)):
            raise DomusError(f"{what}: by is [dE, dN] or [dE, dN, dH], in metres")
        return i
    if kind == "add":
        if not _feature_ok(i.get("feature")):
            raise DomusError(f"{what}: feature is a GeoJSON feature with an id in its properties and a geometry")
        if "at" in i and not (isinstance(i["at"], int) and not isinstance(i["at"], bool) and i["at"] >= 0):
            raise DomusError(f"{what}: at is the feature's place in the layer, counted from 0")
        return i
    if not isinstance(i.get("id"), str) or not i["id"]:
        raise DomusError(f"{what}: id names the feature")
    if kind == "coords":
        given = [k for k in COORDS if k in i]
        if len(given) != 1:
            raise DomusError(f"{what} for {i['id']}: give one of {', '.join(COORDS)}")
        if given[0] == "geometry" and not _geometry(i["geometry"]):
            raise DomusError(f"{what} for {i['id']}: geometry is a GeoJSON geometry, with its coordinates")
        if given[0] != "geometry" and not (_position(i[given[0]]) or _nested(i[given[0]], 1) or _nested(i[given[0]], 2)):
            raise DomusError(f"{what} for {i['id']}: {given[0]} is a point, or a list of them")
        if "vertex" in i and (given[0] == "geometry" or not _position(i[given[0]]) or not isinstance(i["vertex"], int)
                              or isinstance(i["vertex"], bool)):
            raise DomusError(f"{what} for {i['id']}: vertex is a vertex's number, with one point for it")
    elif kind == "level":
        z = i.get("z")
        if not (_number(z) or (isinstance(z, list) and z and all(_number(v) for v in z))):
            raise DomusError(f"{what} for {i['id']}: z is a height, or a list of each vertex's")
        if "vertex" in i and (isinstance(z, list) or not isinstance(i["vertex"], int) or isinstance(i["vertex"], bool)):
            raise DomusError(f"{what} for {i['id']}: vertex is a vertex's number, with z one height")
    elif kind == "props":
        sets, drops = i.get("set", {}), i.get("drop", [])
        if not isinstance(sets, dict) or not isinstance(drops, list) or not all(isinstance(k, str) for k in drops):
            raise DomusError(f"{what} for {i['id']}: set is a dict of properties, drop a list of their names")
        if not sets and not drops:
            raise DomusError(f"{what} for {i['id']}: give properties to set or to drop")
        if "id" in drops or ("id" in sets and not (isinstance(sets["id"], str) and sets["id"])):
            raise DomusError(f"{what} for {i['id']}: a feature keeps an id")
    elif kind == "put" and not (_feature_ok(i.get("feature"))):
        raise DomusError(f"{what} for {i['id']}: feature is a GeoJSON feature with an id in its properties and a geometry")
    return i


def file_of(intent):
    """The file an intent changes, as a path relative to the site's folder (with / between its parts)."""
    if intent["kind"] in ifc_intents.KINDS:
        return ifc_intents.FILE
    if intent["kind"] == "option":
        return f"options/{intent['id']}.toml"
    return intent["file"] if intent["kind"] == "setting" else f"data/site/{intent['layer']}.geojson"


# --------------------------------------------------------------------------- a layer's features

def _first(c):
    return c if isinstance(c[0], (int, float)) else _first(c[0])


def _flat(c, out):
    if isinstance(c[0], (int, float)):
        out.append(c)
    else:
        for x in c:
            _flat(x, out)
    return out


def shape(coords, gtype):
    """Coordinates for a geometry of type `gtype`: a polygon's ring, given open or closed, written closed."""
    if gtype == "Polygon" and _nested(coords, 1):
        ring = [list(c) for c in coords]
        if ring[0] != ring[-1]:
            ring.append(list(ring[0]))
        return [ring]
    return coords


def _vertices(geom):
    c = geom["coordinates"]
    if geom["type"] == "Point":
        return [c]
    if geom["type"] == "Polygon":
        return c[0]
    return c


def _with_basis(props, basis):
    if basis and basis not in props.get("basis", ""):
        old = props.get("basis", "")
        props["basis"] = (old + " " if old else "") + basis


def _id(f):
    return (f.get("properties") or {}).get("id")


def _ids(data):
    return [_id(f) for f in data["features"]]


def _at(data, name, fid, suffix=""):
    """Where the feature `fid` is in the layer: one feature, by its own id."""
    places = [k for k, f in enumerate(data["features"]) if _id(f) == fid]
    if not places:
        raise DomusError(f"{name}: no feature {fid}{suffix}")
    if len(places) > 1:
        raise DomusError(f"{name}: {len(places)} features have the id {fid}: give each its own id first")
    return places[0]


def _put(layer, feature, before):
    """The intent that puts `feature` (named by its id now) back as `before`, whole."""
    return {"kind": "put", "layer": layer, "id": feature["properties"]["id"], "feature": copy.deepcopy(before)}


def _unique(data, name, fid, at):
    if any(k != at and _id(f) == fid for k, f in enumerate(data["features"])):
        raise DomusError(f"{name}: {fid} is already there")


def to_en(frame, so):
    """Points in the wall options' frame (s, o and any height) on the site's grid, to the millimetre."""
    import numpy as np

    a = np.asarray(so, float)
    one = a.ndim == 1
    rows = np.atleast_2d(a)
    en = frame.en(rows[:, :2])
    out = [[round(float(e), 3), round(float(n), 3)] + [round(float(z), 3) for z in row[2:]]
           for (e, n), row in zip(en, rows, strict=True)]
    return out[0] if one else out


def apply(data, intent, name, frame=None, say=print):
    """Applies a layer's intent to its data, `data` (a FeatureCollection, changed in place), saying what changes as
    `name: id: ...` lines. `frame` turns the wall options' (s, o) into the grid's, for "so" and "soz": a callable that
    gives the site's frame when it's needed. Gives the intents that put back what it changed, in the order to apply them."""
    kind, layer = intent["kind"], intent["layer"]
    feats = data["features"]
    if kind == "move":
        return _move(data, intent, name, say)
    if kind == "layer":
        before = copy.deepcopy(data)
        data.clear()
        data.update(copy.deepcopy(intent["collection"]))
        say(f"   {name}: written whole, {len(before['features'])} features -> {len(data['features'])}")
        return [{"kind": "layer", "layer": layer, "collection": before}]
    if kind == "add":
        f = copy.deepcopy(intent["feature"])
        fid = f["properties"]["id"]
        _unique(data, name, fid, None)
        at = intent.get("at", len(feats))
        if at > len(feats):
            raise DomusError(f"{name}: there's no place {at} in it for {fid}: it has {len(feats)} features")
        feats.insert(at, f)
        say(f"   {name}: {fid}: {len(_vertices(f['geometry']))} vertices, first None -> {_vertices(f['geometry'])[0]}")
        return [{"kind": "delete", "layer": layer, "id": fid}]
    fid = intent["id"]
    k = _at(data, name, fid, " to delete" if kind == "delete" else "")
    f = feats[k]
    before = copy.deepcopy(f)
    if kind == "delete":
        del feats[k]
        say(f"   {name}: {fid}: deleted")
        return [{"kind": "add", "layer": layer, "feature": before, "at": k}]
    if kind == "put":
        new = copy.deepcopy(intent["feature"])
        _unique(data, name, new["properties"]["id"], k)
        feats[k] = new
        say(f"   {name}: {fid}: set whole")
        return [_put(layer, new, before)]
    if kind == "coords":
        _coords(f, intent, name, frame, say)
    elif kind == "level":
        _level(f, intent, name, say)
    elif kind == "props":
        _props(f, intent, name, say)
        _unique(data, name, f["properties"]["id"], k)
    _with_basis(f["properties"], intent.get("basis"))
    return [_put(layer, f, before)]


def _move(data, intent, name, say):
    """Moves the named features by one vector, to the millimetre, as `domus site shift` always has."""
    de, dn, *rest = intent["by"]
    dz = float(rest[0]) if rest else 0.0
    ids = intent["ids"]

    def moved(c):
        if isinstance(c[0], (int, float)):
            return [round(c[0] + de, 3), round(c[1] + dn, 3), *([round(c[2] + dz, 3)] if len(c) > 2 else []), *c[3:]]
        return [moved(x) for x in c]

    named = _ids(data) if ids == "*" else ids
    missing = sorted(set(named) - set(_ids(data)))
    if missing:
        raise DomusError(f"{name}: no feature {missing}")
    if ids == "*" and (None in named or len(set(named)) != len(named)):
        raise DomusError(f"{name}: every feature needs an id of its own for them all to move together")
    for fid in set(named):
        _at(data, name, fid)                        # one feature each
    back = []
    for f in data["features"]:
        pid = _id(f)
        if pid not in named:
            continue
        before = copy.deepcopy(f)
        a = _first(f["geometry"]["coordinates"])
        f["geometry"]["coordinates"] = moved(f["geometry"]["coordinates"])
        b = _first(f["geometry"]["coordinates"])
        _with_basis(f["properties"], intent.get("basis"))
        say(f"   {name}: {pid}: ({a[0]:.3f}, {a[1]:.3f}) -> ({b[0]:.3f}, {b[1]:.3f})")
        back.append(_put(intent["layer"], f, before))
    return back


def _coords(f, intent, name, frame, say):
    """A feature's vertices, as `domus site edit` has always set them."""
    g = f["geometry"]
    fid = f["properties"]["id"]
    was = _vertices(g)[0] if g["coordinates"] else None
    if "geometry" in intent:
        f["geometry"] = g = copy.deepcopy(intent["geometry"])
    elif "vertex" in intent:                  # one vertex, the polygon's ring kept closed
        key = next(k for k in COORDS if k in intent)
        new = to_en(frame(), intent[key]) if key.startswith("so") else list(intent[key])
        vs, k = _ring(f, name), intent["vertex"]
        closed = g["type"] == "Polygon" and len(vs) > 1 and vs[0][:2] == vs[-1][:2]
        if not 0 <= k < len(vs) - closed:
            raise DomusError(f"{name}: {fid} has vertices 0 to {len(vs) - closed - 1}, not {k}")
        if key in ("so", "en") and len(vs[k]) > 2:
            new = new[:2] + vs[k][2:]
        say(f"   {name}: {fid}: vertex {k} {vs[k]} -> {new}")
        for v in [vs[k], *([vs[-1]] if closed and k == 0 else [])]:
            v[:] = new
        return
    else:
        key = next(k for k in COORDS if k in intent)
        new = to_en(frame(), intent[key]) if key.startswith("so") else copy.deepcopy(intent[key])
        cand = [new] if g["type"] == "Point" else new
        if key in ("so", "en") and g["coordinates"] and _nested(cand, 1):
            old = _vertices(g)
            olds = old[:-1] if g["type"] == "Polygon" and len(old) == len(cand) + 1 else old
            if len(olds) == len(cand) and all(len(o) > 2 for o in olds):
                cand = [c[:2] + list(o[2:]) for c, o in zip(cand, olds, strict=True)]
                new = cand[0] if g["type"] == "Point" else cand
        g["coordinates"] = shape(new, g["type"])
        if not _geometry(g):
            raise DomusError(f"{name}: {fid} is a {g['type']}, and those vertices don't make one")
    say(f"   {name}: {fid}: {len(_vertices(g))} vertices, first {was} -> {_vertices(g)[0]}")


def _ring(f, name):
    """A point's, a line's or a polygon's outer ring's vertices, to change in place."""
    g = f["geometry"]
    if g["type"] not in ("Point", "LineString", "Polygon"):
        raise DomusError(f"{name}: {f['properties']['id']} is a {g['type']}: one vertex is set on a point, a line or a "
                         "polygon's outer ring")
    return _vertices(g)


def _level(f, intent, name, say):
    """A vertex's height, or every vertex's."""
    g = f["geometry"]
    fid = f["properties"]["id"]
    vs = _ring(f, name)
    closed = g["type"] == "Polygon" and len(vs) > 1 and vs[0][:2] == vs[-1][:2]
    n = len(vs) - closed
    z = intent["z"]
    heights = list(z) if isinstance(z, list) else [z] * n
    if isinstance(z, list) and len(z) != n:
        raise DomusError(f"{name}: {fid} has {n} vertices, and {len(z)} heights were given")
    only = intent.get("vertex")
    if only is not None and not 0 <= only < n:
        raise DomusError(f"{name}: {fid} has vertices 0 to {n - 1}, not {only}")
    for k in range(n) if only is None else [only]:
        for v in [vs[k], *([vs[-1]] if closed and k == 0 else [])]:
            if len(v) > 2:
                v[2] = heights[k]
            else:
                v.append(heights[k])
    if only is not None:
        say(f"   {name}: {fid}: vertex {only} at {heights[only]}")
    else:
        say(f"   {name}: {fid}: {n} vertices' heights set")


def _props(f, intent, name, say):
    fid = f["properties"]["id"]
    for k, v in intent.get("set", {}).items():
        if f["properties"].get(k) != v:
            say(f"   {name}: {fid}.{k}: {str(f['properties'].get(k))[:70]!r} -> {str(v)[:110]!r}")
        f["properties"][k] = copy.deepcopy(v)
    for k in intent.get("drop", []):
        if k in f["properties"]:
            say(f"   {name}: {fid}.{k}: dropped")
            del f["properties"][k]


# --------------------------------------------------------------------------- settings

def setting_node(intent):
    """The schema's Key for a setting's intent: a DomusError for a key Domus doesn't know, or that isn't one setting."""
    node, path = SETTINGS[intent["file"]], intent["key"].split(".")
    for k, part in enumerate(path):
        if isinstance(node, schema.Table):
            node = node.keys.get(part)
        elif isinstance(node, schema.Map):
            node = node.known.get(part, node.value)
        else:
            node = None
        if node is None or (isinstance(node, schema.Key) and k < len(path) - 1) or isinstance(node, schema.Tables):
            raise DomusError(f"{intent['file']}: {intent['key']} isn't a setting Domus knows (docs/site-format.md)")
    if not isinstance(node, schema.Key):
        raise DomusError(f"{intent['file']}: {intent['key']} is a table of settings: name one of them")
    return node


def _kind_of(v):
    v = v.unwrap() if hasattr(v, "unwrap") else v
    return "bool" if isinstance(v, bool) else "number" if isinstance(v, (int, float)) else type(v).__name__


def apply_setting(doc, intent, say=print):
    """Applies a setting's intent to its file, `doc` (a tomlkit document, changed in place, its comments kept). Gives
    the intent that puts back what it changed."""
    import tomlkit

    node = setting_node(intent)
    where, key = intent["file"], intent["key"]
    *tables, last = key.split(".")
    table = doc
    for k, part in enumerate(tables):
        if part not in table or not hasattr(table[part], "keys"):
            raise DomusError(f"{where} has no [{'.'.join(tables[:k + 1])}]: add the table before changing {key}")
        table = table[part]
    old = table.get(last, _MISSING)
    if old is _MISSING:
        back = {"kind": "setting", "file": where, "key": key, "drop": True}
    else:
        back = {"kind": "setting", "file": where, "key": key, "value": old.unwrap(), "toml": old.as_string().strip()}
    if intent.get("drop"):
        if old is _MISSING:
            raise DomusError(f"{where}: {key} isn't there to drop")
        del table[last]
        say(f"   {where}: {key}: dropped")
        return [back]
    value = intent["value"]
    plain = old if old is not _MISSING else node.default if node.default not in (schema.REQUIRED, schema.COMPUTED, None) else None
    if plain is not None and _kind_of(plain) != _kind_of(value):
        raise DomusError(f"{where}: {key} is a {_kind_of(plain)}, not a {_kind_of(value)}")
    if "toml" in intent:
        item = tomlkit.parse(f"x = {intent['toml']}")["x"]
        if item.unwrap() != value:
            raise DomusError(f"{where}: {key}: its toml, {intent['toml']}, isn't its value, {value!r}")
    else:
        item = tomlkit.item(value)
    table[last] = item
    say(f"   {where}: {key}: {(old.unwrap() if old is not _MISSING else None)!r} -> {value!r}")
    return [back]


# --------------------------------------------------------------------------- saying what one does

def describe(intent):
    """One line saying what an intent does, for people."""
    kind = intent["kind"]
    if kind in ifc_intents.KINDS:
        return ifc_intents.describe(intent)
    if kind == "setting":
        return f"{intent['file']}: {intent['key']} " + ("dropped" if intent.get("drop") else f"= {intent['value']!r}")
    if kind == "option":
        return f"options/{intent['id']}.toml: " + ("taken away" if intent.get("drop") else "written whole")
    layer = intent["layer"]
    if kind == "layer":
        n = len(intent["collection"]["features"])
        return f"{layer}: written whole, {n} feature{'s' if n != 1 else ''}"
    if kind == "move":
        ids = "every feature" if intent["ids"] == "*" else ", ".join(intent["ids"])
        by = ", ".join(f"{v:+.3f}" for v in intent["by"])
        return f"{layer}: {ids} moved by ({by}) m"
    if kind == "add":
        return f"{layer}: {intent['feature']['properties']['id']} added"
    fid = intent["id"]
    if kind == "coords":
        return f"{layer}: {fid}'s " + (f"vertex {intent['vertex']}" if "vertex" in intent else "vertices") + " set"
    if kind == "level":
        z = intent["z"]
        if isinstance(z, list):
            return f"{layer}: {fid}'s {len(z)} vertices' heights set"
        return f"{layer}: {fid}'s " + (f"vertex {intent['vertex']}" if "vertex" in intent else "height") + f" at {z}"
    if kind == "props":
        parts = [f"{k} = {v[:60]!r}" if isinstance(v, str) else f"{k} = {json.dumps(v)[:60]}"
                 for k, v in intent.get("set", {}).items()]
        parts += [f"{k} dropped" for k in intent.get("drop", [])]
        return f"{layer}: {fid}: {', '.join(parts)}"
    if kind == "delete":
        return f"{layer}: {fid} deleted"
    return f"{layer}: {fid} set whole"
