"""Intents on the house kept as an IFC file, data/house.ifc (domus.model.house_ifc): the change route's
(domus.project.changes) for the house's elements, applied with IfcOpenShell's API and the file written whole.

An element is named by its GlobalId or its Domus id (`ref` in its Domus property set, which is its Tag too), and a
storey the same way (a house written by Domus has `floor`, or `lower` and `upper`), or by its name:

    {"kind": "ifc-move", "element": "chimney", "by": [0.5, 0.0, 0.0]}
        moves it by [dE, dN] or [dE, dN, dH], metres on the site's grid; its parts move with it
    {"kind": "ifc-rotate", "element": "tank", "degrees": 90.0}
        turns it anticlockwise, seen from above, about the upright through its own origin, or through `about`, [E, N]
    {"kind": "ifc-storey", "storey": "floor", "elevation": 43.5}
        sets a storey's elevation, a height on the site's datum; what stands on it moves with it
    {"kind": "ifc-attribute", "element": "chimney", "attribute": "Name", "value": "Brick chimney"}
        sets one of its attributes: Name, Description, ObjectType or Tag (null clears it)
    {"kind": "ifc-property", "element": "chimney", "pset": "Domus", "property": "source", "value": "measured"}
        sets a value in one of its property sets, which is made if it isn't there; "drop": true takes it out
    {"kind": "ifc-add", "id": "shed", "class": "IfcBuildingElementProxy", "name": "Garden shed", "layer": "outdoor",
     "colour": "timber-grey", "box": {"centre": [E, N], "size": [2.0, 1.5, 2.1], "base": 41.2, "degrees": 0.0}}
        adds an element made from a box (its centre in plan, its size along, across and up, its base's height and how
        far it's turned), or from an outline extruded up ("extrusion": {"outline": [[E, N], ...], "base": H,
        "height": h}), in its colour (a palette key, one of the site's own colours, or hex), in the house layer it
        names (its class's unless given), on the highest storey whose floor is at or below its base; its GlobalId is a
        uuid5 of its id under the house's namespace, as the house's own elements' are
    {"kind": "ifc-delete", "element": "tank"}
        deletes it, and its parts
    {"kind": "ifc-write"}
        writes the file whole, the house as house.geojson and [house] draw it today: what domus house ifc does

IfcOpenShell writes the file whole, so the route keeps the file as it was before each edit, and an edit's inverse puts
that copy back, byte for byte ({"kind": "ifc-restore", "sha": its SHA-256}, or "sha": null to take away a file the
edit started): an undo does that only while the file is still as the edit left it. What's written is checked against the file as it was: every element and storey the intents
didn't name, nor move with one they did, is as it was, and an element moved once moved by the vector asked.
"""

import copy
import hashlib
import json
import math
import re

import numpy as np

from ..errors import DomusError

FILE = "data/house.ifc"
KINDS = ("ifc-move", "ifc-rotate", "ifc-storey", "ifc-attribute", "ifc-property", "ifc-add", "ifc-delete", "ifc-write",
         "ifc-restore")
ATTRIBUTES = ("Name", "Description", "ObjectType", "Tag")
GUID = re.compile(r"[0-9A-Za-z_$]{22}")
ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")
MOVED_M = 0.0011            # how far off the vector asked a moved element may land, as a layer's moves are held to


def _number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _numbers(v, sizes):
    return isinstance(v, list) and len(v) in sizes and all(_number(x) for x in v)


def checked(intent):
    """A copy of an IFC intent, its shape checked: a DomusError says what's wrong."""
    i = copy.deepcopy(intent)
    kind = i["kind"]
    what = f"an {kind} intent"
    if kind == "ifc-write":
        return i
    if kind == "ifc-restore":
        if not (i.get("sha") is None or (isinstance(i["sha"], str) and re.fullmatch(r"[0-9a-f]{64}", i["sha"]))):
            raise DomusError(f"{what}: sha is the SHA-256 of the file to put back, or null to take away one an edit "
                             "started")
        i.setdefault("sha", None)
        return i
    if kind == "ifc-storey":
        if not isinstance(i.get("storey"), str) or not i["storey"]:
            raise DomusError(f"{what}: storey names it, by its GlobalId, its Domus id or its name")
        if not _number(i.get("elevation")):
            raise DomusError(f"{what} for {i['storey']}: elevation is a height on the site's datum, in metres")
        return i
    if kind == "ifc-add":
        if not isinstance(i.get("id"), str) or not ID.fullmatch(i["id"]):
            raise DomusError(f"{what}: id is the new element's own: letters, digits, '.', '_' and '-'")
        if not isinstance(i.get("class"), str) or not re.fullmatch(r"Ifc[A-Za-z]+", i["class"]):
            raise DomusError(f"{what} for {i['id']}: class is an IFC class of element, such as IfcBuildingElementProxy")
        for key in ("name", "layer", "colour", "source", "predefined"):
            if key in i and not (isinstance(i[key], str) and i[key]):
                raise DomusError(f"{what} for {i['id']}: {key} is text")
        shapes = [k for k in ("box", "extrusion") if k in i]
        if len(shapes) != 1:
            raise DomusError(f"{what} for {i['id']}: give a box or an extrusion")
        s = i[shapes[0]]
        if shapes[0] == "box":
            if not (isinstance(s, dict) and _numbers(s.get("centre"), (2,)) and _numbers(s.get("size"), (3,))
                    and all(v > 0 for v in s["size"]) and _number(s.get("base")) and _number(s.get("degrees", 0.0))):
                raise DomusError(f"{what} for {i['id']}: a box is {{\"centre\": [E, N], \"size\": [along, across, up], "
                                 "\"base\": its height, \"degrees\": how far it's turned}, its sizes more than 0")
        elif not (isinstance(s, dict) and isinstance(s.get("outline"), list) and len(s["outline"]) >= 3
                  and all(_numbers(p, (2,)) for p in s["outline"]) and _number(s.get("base"))
                  and _number(s.get("height")) and s["height"] > 0):
            raise DomusError(f"{what} for {i['id']}: an extrusion is {{\"outline\": [[E, N], ...] (three points or more), "
                             "\"base\": its height, \"height\": how far up it goes, more than 0}")
        return i
    if not isinstance(i.get("element"), str) or not i["element"]:
        raise DomusError(f"{what}: element names it, by its GlobalId or its Domus id")
    name = i["element"]
    if kind == "ifc-move" and not _numbers(i.get("by"), (2, 3)):
        raise DomusError(f"{what} for {name}: by is [dE, dN] or [dE, dN, dH], in metres")
    if kind == "ifc-rotate":
        if not _number(i.get("degrees")):
            raise DomusError(f"{what} for {name}: degrees is how far to turn it, anticlockwise seen from above")
        if "about" in i and not _numbers(i["about"], (2,)):
            raise DomusError(f"{what} for {name}: about is [E, N], the point it turns about")
    if kind == "ifc-attribute":
        if i.get("attribute") not in ATTRIBUTES:
            raise DomusError(f"{what} for {name}: attribute is one of {', '.join(ATTRIBUTES)}")
        if "value" not in i or not (i["value"] is None or isinstance(i["value"], str)):
            raise DomusError(f"{what} for {name}: value is text, or null to clear it")
    if kind == "ifc-property":
        if not all(isinstance(i.get(k), str) and i[k] for k in ("pset", "property")):
            raise DomusError(f"{what} for {name}: pset and property name the value")
        if i.get("drop"):
            if "value" in i:
                raise DomusError(f"{what} for {name}: give a value or drop it, not both")
        elif not (_number(i.get("value")) or isinstance(i.get("value"), (str, bool))):
            raise DomusError(f"{what} for {name}: value is a number, text, or true or false; or drop: true")
        if i["pset"] == "Domus" and i["property"] == "ref":
            raise DomusError(f"{what} for {name}: an element keeps its Domus id (Domus.ref)")
    return i


def describe(intent):
    """One line saying what an IFC intent does, for people."""
    kind = intent["kind"]
    if kind == "ifc-restore":
        return f"{FILE}: put back as it was" if intent["sha"] else f"{FILE}: taken away, as it wasn't there"
    if kind == "ifc-write":
        return f"{FILE}: written whole, the house as house.geojson and [house] draw it"
    if kind == "ifc-storey":
        return f"{FILE}: storey {intent['storey']} at {intent['elevation']}"
    if kind == "ifc-add":
        return f"{FILE}: {intent['id']} added, an {intent['class']} from {'a box' if 'box' in intent else 'an extrusion'}"
    name = intent["element"]
    if kind == "ifc-move":
        return f"{FILE}: {name} moved by ({', '.join(f'{v:+.3f}' for v in intent['by'])}) m"
    if kind == "ifc-rotate":
        return f"{FILE}: {name} turned {intent['degrees']:+g} degrees"
    if kind == "ifc-attribute":
        return f"{FILE}: {name}.{intent['attribute']} = {intent['value']!r}"
    if kind == "ifc-property":
        return f"{FILE}: {name} {intent['pset']}.{intent['property']} " + (
            "dropped" if intent.get("drop") else f"= {json.dumps(intent['value'])[:60]}")
    return f"{FILE}: {name} deleted"


# --------------------------------------------------------------------------- the file

def parse(raw):
    """The IFC file whose text is `raw` (bytes), as IfcOpenShell holds it, or a DomusError saying what's wrong."""
    from ..model import house_ifc

    ifcopenshell = house_ifc.modules()
    try:
        return ifcopenshell.file.from_string(raw.decode("utf-8"))
    except Exception as e:        # noqa: BLE001 - IfcOpenShell says what it couldn't read in many ways
        raise DomusError(f"{FILE} can't be read as IFC ({type(e).__name__}: {e}): nothing written") from None


def text(f):
    """The file as IfcOpenShell writes it, whole: its text, as bytes."""
    return f.to_string().encode("utf-8")


def _ref(element):
    """An element's Domus id: `ref` in its Domus property set, else its Tag."""
    import ifcopenshell.util.element as ue

    return ue.get_psets(element).get("Domus", {}).get("ref", getattr(element, "Tag", None))


def find(f, name, what="element"):
    """The element, or the storey (`what`), that `name` names: by its GlobalId, or its Domus id (`ref` in its Domus
    property set, else its Tag); a storey by its name too. A DomusError when none is, or more than one."""
    kind = "IfcBuildingStorey" if what == "storey" else "IfcElement"
    if GUID.fullmatch(name):
        try:
            found = f.by_guid(name)
        except RuntimeError:
            found = None
        if found is not None and found.is_a(kind):
            return found
    pool = f.by_type(kind)
    found = [e for e in pool if _ref(e) == name] or ([e for e in pool if e.Name == name] if what == "storey" else [])
    if not found:
        raise DomusError(f"{FILE} has no {what} {name!r}: name one by its GlobalId or its Domus id")
    if len(found) > 1:
        raise DomusError(f"{FILE}: {len(found)} {what}s are called {name!r}: name it by its GlobalId")
    return found[0]


def parts_of(element):
    """An element and everything that's a part of it, all the way down."""
    out = [element]
    for rel in getattr(element, "IsDecomposedBy", None) or ():
        for part in rel.RelatedObjects:
            out += parts_of(part)
    return out


def on_storey(storey):
    """What stands on a storey, each with its parts."""
    out = []
    for rel in getattr(storey, "ContainsElements", None) or ():
        for element in rel.RelatedElements:
            out += parts_of(element)
    return out


def to_grid(f):
    """The file's map conversion: the 4 by 4 matrix from its model's coordinates (in its own units) to the site's grid."""
    import ifcopenshell.util.geolocation as geo

    return geo.auto_local2global(f, np.eye(4))


def placed(element):
    """Where an element (or a storey) is placed: its 4 by 4 matrix in the model's coordinates, in the file's units."""
    import ifcopenshell.util.placement as up

    return up.get_local_placement(element.ObjectPlacement)


def place(f, element, matrix):
    """Places `element` by `matrix` (the model's coordinates, in the file's units), its parts and what's placed in it
    going with it."""
    import ifcopenshell.api.geometry

    ifcopenshell.api.geometry.edit_object_placement(f, product=element, matrix=matrix, is_si=False,
                                                    should_transform_children=True)


def turn(degrees, about):
    """The 4 by 4 matrix that turns points `degrees` anticlockwise, seen from above, about the upright through
    `about` (its first two values)."""
    a = math.radians(degrees)
    m = np.eye(4)
    m[:2, :2] = [[math.cos(a), -math.sin(a)], [math.sin(a), math.cos(a)]]
    p = np.asarray(about, float)[:2]
    m[:2, 3] = p - m[:2, :2] @ p
    return m


def storey_for(f, height):
    """The highest storey whose floor (its placement's height) is at or below `height`, in the model's heights; else
    the lowest."""
    floors = sorted(((float(placed(s)[2, 3]), s.id(), s) for s in f.by_type("IfcBuildingStorey")), key=lambda x: x[:2])
    if not floors:
        raise DomusError(f"{FILE} has no storey to stand a new element on")
    on = [s for level, _, s in floors if level <= height + 1e-6]
    return on[-1] if on else floors[0][2]


def apply(f, intent, site, say=print):
    """Applies an IFC intent to `f`, the house's file as IfcOpenShell holds it, with IfcOpenShell's API, saying what
    changes. Gives the GlobalIds of what it changed, moved, added or deleted, and of what moves with it."""
    import ifcopenshell.api.attribute
    import ifcopenshell.api.pset
    import ifcopenshell.api.root
    import ifcopenshell.util.element as ue

    kind = intent["kind"]
    back = np.linalg.inv(to_grid(f))
    if kind == "ifc-storey":
        storey = find(f, intent["storey"], "storey")
        level = float((back @ [0.0, 0.0, intent["elevation"], 1.0])[2])
        m = placed(storey)
        dz = level - m[2, 3]
        moving = on_storey(storey)
        m[2, 3] = level
        place(f, storey, m)
        before = storey.Elevation
        storey.Elevation = round(level, 6)
        say(f"   {FILE}: storey {intent['storey']}: elevation {before} -> {storey.Elevation} in the model's heights, "
            f"{len(moving)} elements on it moved {dz:+.3f} m")
        return {storey.GlobalId, *(e.GlobalId for e in moving)}
    if kind == "ifc-add":
        return add(f, intent, site, back, say)
    element = find(f, intent["element"])
    name = intent["element"]
    if kind == "ifc-move":
        de, dn, *rest = intent["by"]
        m = placed(element)
        m[:3, 3] += back[:3, :3] @ np.array([de, dn, rest[0] if rest else 0.0])
        place(f, element, m)
        say(f"   {FILE}: {name}: moved by ({', '.join(f'{v:+.3f}' for v in intent['by'])}) m")
        return {e.GlobalId for e in parts_of(element)}
    if kind == "ifc-rotate":
        m = placed(element)
        about = (back @ [*intent["about"], 0.0, 1.0])[:3] if "about" in intent else m[:3, 3]
        place(f, element, turn(intent["degrees"], about) @ m)
        say(f"   {FILE}: {name}: turned {intent['degrees']:+g} degrees")
        return {e.GlobalId for e in parts_of(element)}
    if kind == "ifc-attribute":
        attribute = intent["attribute"]
        before = getattr(element, attribute, None)
        try:
            ifcopenshell.api.attribute.edit_attributes(f, product=element, attributes={attribute: intent["value"]})
        except Exception as e:        # noqa: BLE001 - IfcOpenShell refuses a value its schema doesn't take
            raise DomusError(f"{FILE}: {name}'s {attribute} can't be {intent['value']!r} ({e})") from None
        say(f"   {FILE}: {name}.{attribute}: {before!r} -> {getattr(element, attribute)!r}")
        return {element.GlobalId}
    if kind == "ifc-property":
        pset_name, prop = intent["pset"], intent["property"]
        sets = {d.Name: d for rel in getattr(element, "IsDefinedBy", None) or () if rel.is_a("IfcRelDefinesByProperties")
                for d in [rel.RelatingPropertyDefinition] if d.is_a("IfcPropertySet")}
        have = ue.get_psets(element).get(pset_name, {})
        if intent.get("drop"):
            if pset_name not in sets or prop not in have:
                raise DomusError(f"{FILE}: {name} has no {pset_name}.{prop} to drop")
            ifcopenshell.api.pset.edit_pset(f, pset=sets[pset_name], properties={prop: None})
            say(f"   {FILE}: {name} {pset_name}.{prop}: dropped")
        else:
            from ..export import ifc

            pset = sets.get(pset_name) or ifcopenshell.api.pset.add_pset(f, product=element, name=pset_name)
            ifcopenshell.api.pset.edit_pset(f, pset=pset, properties={prop: ifc.measure(f, prop, intent["value"])})
            say(f"   {FILE}: {name} {pset_name}.{prop}: {have.get(prop)!r} -> {intent['value']!r}")
        return {element.GlobalId}
    gone = parts_of(element)
    ids = {e.GlobalId for e in gone}
    for e in reversed(gone):
        ifcopenshell.api.root.remove_product(f, product=e)
    say(f"   {FILE}: {name}: deleted" + (f", with its {len(gone) - 1} parts" if len(gone) > 1 else ""))
    return ids


def add(f, intent, site, back, say):
    """apply()'s ifc-add: an element made from a box or an extrusion, `back` taking the site's grid to the model's
    coordinates."""
    import ifcopenshell.api.geometry
    import ifcopenshell.api.pset
    import ifcopenshell.api.root
    import ifcopenshell.api.spatial
    import ifcopenshell.api.style
    import ifcopenshell.util.shape_builder

    from ..export import ifc
    from ..model import house_ifc, house_texture

    new = intent["id"]
    gid = house_ifc.global_id(f"element:{new}")
    try:
        clash = f.by_guid(gid)
    except RuntimeError:
        clash = None
    if clash is not None or any(_ref(e) == new for e in f.by_type("IfcElement")):
        raise DomusError(f"{FILE}: {new} is there already")
    try:
        element = ifcopenshell.api.root.create_entity(f, ifc_class=intent["class"], predefined_type=intent.get("predefined"),
                                                      name=intent.get("name", new))
    except Exception as e:        # noqa: BLE001 - IfcOpenShell refuses a class its schema doesn't have
        raise DomusError(f"{FILE}: {new} can't be an {intent['class']} ({e})") from None
    if not element.is_a("IfcElement") or element.is_a("IfcFeatureElement"):
        raise DomusError(f"{FILE}: {new} would be an {intent['class']}, which isn't an element a house is built of")
    element.GlobalId, element.Tag = gid, new
    shape = intent.get("box") or intent["extrusion"]
    base = float((back @ [0.0, 0.0, shape["base"], 1.0])[2])
    if "box" in intent:
        along, across, height = shape["size"]
        x, y = (back @ [*shape["centre"], 0.0, 1.0])[:2]
        theta = math.radians(shape.get("degrees", 0.0))
        outline = [(-along / 2, -across / 2), (along / 2, -across / 2), (along / 2, across / 2), (-along / 2, across / 2)]
    else:
        pts = np.array([(back @ [*p, 0.0, 1.0])[:2] for p in shape["outline"]])
        if len(pts) > 3 and np.allclose(pts[0], pts[-1]):
            pts = pts[:-1]
        x, y = pts.mean(axis=0)
        theta, height = 0.0, shape["height"]
        outline = [tuple(p) for p in pts - (x, y)]
        if sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(outline, outline[1:] + outline[:1])) < 0:
            outline = outline[::-1]                 # anticlockwise, as IFC's profiles go
    storey = storey_for(f, base)
    ifcopenshell.api.spatial.assign_container(f, products=[element], relating_structure=storey)
    ifcopenshell.api.geometry.edit_object_placement(f, product=element, matrix=house_ifc.frame_matrix(float(x), float(y), theta,
                                                                                                    base))
    body = next((c for c in f.by_type("IfcGeometricRepresentationSubContext") if c.ContextIdentifier == "Body"), None)
    if body is None:
        raise DomusError(f"{FILE} has no body context (Model, Body) for a new element's shape")
    builder = ifcopenshell.util.shape_builder.ShapeBuilder(f)
    solid = builder.extrude(builder.polyline([list(p) for p in outline], closed=True), magnitude=float(height))
    rep = builder.get_representation(body, [solid])
    ifcopenshell.api.geometry.assign_representation(f, product=element, representation=rep)
    key = intent.get("colour", "concrete")
    looks = house_texture.Looks(site.settings().get("house", {}))
    colour = "#" + "".join(f"{v:02x}" for v in looks.rgb(key))
    style = next((s for s in f.by_type("IfcSurfaceStyle") if s.Name == key), None)
    if style is None:
        rgb = [int(colour[k:k + 2], 16) / 255 for k in (1, 3, 5)]
        style = ifcopenshell.api.style.add_style(f, name=key)
        ifcopenshell.api.style.add_surface_style(f, style=style, ifc_class="IfcSurfaceStyleShading", attributes={
            "SurfaceColour": {"Name": None, "Red": rgb[0], "Green": rgb[1], "Blue": rgb[2]}, "Transparency": 0.0})
    ifcopenshell.api.style.assign_representation_styles(f, shape_representation=rep, styles=[style])
    props = {"ref": new, "layer": intent.get("layer") or house_ifc.layer_of(element, {}), "part": "added",
             **({"source": intent["source"]} if intent.get("source") else {})}
    pset = ifcopenshell.api.pset.add_pset(f, product=element, name="Domus")
    ifcopenshell.api.pset.edit_pset(f, pset=pset, properties={k: ifc.measure(f, k, v) for k, v in props.items()})
    say(f"   {FILE}: {new}: added, an {element.is_a()} on storey {storey.Name}")
    return {element.GlobalId}


# --------------------------------------------------------------------------- checking what's written

def state(f):
    """What each element and storey of `f` is, by GlobalId, as a SHA-256 of its class, attributes, placement (to the
    tenth of a millimetre), property sets and body, so two files can be compared element by element."""
    import ifcopenshell.util.element as ue

    out = {}
    for e in [*f.by_type("IfcElement"), *f.by_type("IfcSpatialStructureElement")]:
        body = [r.get_info(recursive=True, include_identifier=False) for r in
                (e.Representation.Representations if getattr(e, "Representation", None) else ())]
        psets = {k: {p: v for p, v in d.items() if p != "id"} for k, d in ue.get_psets(e).items()}
        info = {"class": e.is_a(), "attributes": {k: getattr(e, k, None) for k in (*ATTRIBUTES, "PredefinedType", "Elevation")},
                "placement": np.round(placed(e), 4).tolist(), "psets": psets, "body": body}
        out[e.GlobalId] = hashlib.sha256(json.dumps(info, sort_keys=True, default=str).encode()).hexdigest()
    return out


def check(before, written, named, intents):
    """Raises a DomusError unless the file as written (`written`, its bytes) says what was asked of it and nothing else
    changed: every element and storey not `named` (the GlobalIds apply gave) is as it was in `before`, and each
    element moved once by the intents moved by the vector asked."""
    a, b = parse(before), parse(written)
    sa, sb = state(a), state(b)
    for gid in sorted(set(sa) | set(sb)):
        if gid not in named and sa.get(gid) != sb.get(gid):
            raise DomusError(f"{FILE}: {gid} changed though it was not named: not written")
    grid_a, grid_b = to_grid(a), to_grid(b)
    once = [i for i in intents if i["kind"] == "ifc-move"
            and sum(x.get("element") == i["element"] for x in intents if x["kind"] != "ifc-add") == 1]
    for i in once:
        try:
            element = find(a, i["element"])
            moved = b.by_guid(element.GlobalId)
        except (DomusError, RuntimeError):
            continue
        de, dn, *rest = i["by"]
        shift = (grid_b @ placed(moved))[:3, 3] - (grid_a @ placed(element))[:3, 3]
        if np.abs(shift - [de, dn, rest[0] if rest else 0.0]).max() > MOVED_M:
            raise DomusError(f"{FILE}: {i['element']} did not move as meant: not written")


def against(f, intents):
    """What IFC intents rest on, in `f` as it is now (None where there's no file): a SHA-256 of each element and storey
    they name (None where it isn't there), so a proposal is accepted only while they're as they were."""
    have = state(f) if f is not None else {}
    out = []
    for i in intents:
        if i["kind"] in ("ifc-restore", "ifc-write"):
            continue
        name, what = ((i["id"], "element") if i["kind"] == "ifc-add" else (i["storey"], "storey") if i["kind"] == "ifc-storey"
                      else (i["element"], "element"))
        try:
            gid = find(f, name, what).GlobalId if f is not None else None
        except DomusError:
            gid = None
        out.append({"file": FILE, what: name, "sha": have.get(gid) if gid else None})
    return out
