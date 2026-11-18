"""The house kept as an IFC file, data/house.ifc: the master of the house's shape when [house] source = "ifc".

`write` writes the house as house.geojson and [house] draw it today (domus.model.house) to an IFC4 file, with
IfcOpenShell and the export's writer (domus.export.ifc), as an edit on the change route (domus.project.changes), so
writing it again is recorded and undone as any edit is; `draft` gives the file's text. The file holds one site,
georeferenced as an export is, with a building on it and a storey at each of the house's floor levels: fl_upper, and
fl_lower where it's another level. Each element stands on the highest storey whose floor is at or below its lowest
point, or on the lowest. The elements are:

    each wall face        IfcWall, placed at the face's left end seen from outside, its x axis along the face
    the eaves             IfcCovering (EAVES): the fascia and the soffit
    the roof              IfcRoof: HIP_ROOF, SHED_ROOF (a skillion) or GABLE_ROOF
    each part             by its kind: IfcStair (steps), IfcSlab (block), IfcWall (wall), IfcRailing (balustrade),
                          IfcChimney, IfcTank, and IfcBuildingElementProxy (BAY, PORCH) for a bay window or a porch,
                          whose wall faces are IfcWalls of their own, parts of it

Each element's body is its triangles as the house draws them, each piece an IfcTriangulatedFaceSet in a style named for
its colour (the palette's key, or the colour's hex) and shaded in it. Its property set, Domus, says its id (`ref`, which
is its Tag too), the viewer layer it's drawn in (`layer`), what it is (`part`), the feature of house.geojson it comes
from (`feature`) and that feature's `source`, and a wall face's own id (`face`). Its GlobalId is a uuid5 of its id under
a fixed namespace, as are the project's, the site's, the building's and the storeys', and every relationship's comes
from what it relates, so writing the house again gives the same ids throughout.

`read` reads the file back for the build: each element triangulated by IfcOpenShell's geometry in world coordinates
(the model's), put on the site's grid by the file's own map conversion, in the colours of its styles, and in its own
coordinates too, which lay a wall face's picture and the roof's tiles on it wherever it's been moved. `copy_into`
copies the house's building, storeys and elements into another file being written, as domus export does.

Everything here needs IfcOpenShell (the ifc extra), which it imports only when it's used: a site whose house is drawn
builds without it.
"""

import hashlib
import itertools
import math
import uuid
from dataclasses import dataclass, field

import numpy as np

from .. import __version__
from ..errors import DomusError
from ..geo import geojson
from . import house, house_texture

FILE = "data/house.ifc"
SOURCES = ("drawn", "ifc")
# The house's GlobalIds: uuid5s of each thing's key under this namespace, the same for every site and every write.
NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "https://github.com/populii/domus/house.ifc")
REQUIRED = ("fl_upper", "roof_pitch_deg", "roof_plane_at_wall", "eaves_overhang_m")
WHY = "The house written to IFC as house.geojson and [house] draw it."     # the reason recorded unless one's given
ROOFS = {"hip": "HIP_ROOF", "skillion": "SHED_ROOF", "gable": "GABLE_ROOF"}
# Each kind of part (house.PARTS) as IFC has it: its class and predefined type.
CLASSES = {"bay": ("IfcBuildingElementProxy", "BAY"), "porch": ("IfcBuildingElementProxy", "PORCH"),
           "steps": ("IfcStair", "STRAIGHT_RUN_STAIR"), "block": ("IfcSlab", None), "wall": ("IfcWall", None),
           "balustrade": ("IfcRailing", "BALUSTRADE"), "chimney": ("IfcChimney", None), "tank": ("IfcTank", "STORAGE")}
# The house layer an element without one of its own is drawn in, by its IFC class (or a proxy's object type).
LAYERS = {"IfcWall": "walls", "IfcCovering": "eaves", "IfcRoof": "roof", "IfcStair": "steps", "IfcStairFlight": "steps",
          "IfcSlab": "outdoor", "IfcRailing": "outdoor", "IfcChimney": "chimney", "IfcTank": "fixtures", "BAY": "bay",
          "PORCH": "porch"}


def path(site):
    """The house's IFC file in the site's folder: data/house.ifc."""
    return site.path / FILE


def global_id(key):
    """The GlobalId `key` gives under the house's namespace: a uuid5, compressed as IFC writes it."""
    return modules().guid.compress(uuid.uuid5(NAMESPACE, key).hex)


def source(site):
    """Where the site's build draws its house from, as [house] source says: "drawn" or "ifc"."""
    found = site.settings().get("house", {}).get("source", "drawn")
    if found not in SOURCES:
        raise DomusError(f"[house] source in data/site.toml is {found!r}: give drawn (house.geojson and [house]) or ifc "
                         f"({FILE})")
    return found


def digest(site):
    """What the build cache keys on for the house's IFC: {file: SHA-256} while the site draws its house from one
    that's there, else None."""
    p = path(site)
    if source(site) != "ifc" or not p.is_file():
        return None
    return {FILE: hashlib.sha256(p.read_bytes()).hexdigest()}


def modules():
    """IfcOpenShell, with the parts of it used here, or a DomusError saying how to install it."""
    from ..export import ifc

    ifcopenshell = ifc.ifcopenshell_modules()
    try:
        import ifcopenshell.api.project
        import ifcopenshell.geom
        import ifcopenshell.util.element
        import ifcopenshell.util.placement
        import ifcopenshell.util.unit
    except ImportError as e:
        raise DomusError(ifc.INSTALL) from e
    return ifcopenshell


def open_file(p):
    """The IFC file at `p`, or a DomusError saying what to do about one that can't be read."""
    ifcopenshell = modules()
    try:
        return ifcopenshell.open(str(p))
    except Exception as e:        # noqa: BLE001 - IfcOpenShell says what it couldn't read in many ways
        raise DomusError(f"{FILE} can't be read as IFC ({type(e).__name__}: {e}). domus house ifc --force writes it again "
                         "from house.geojson and [house], or [house] source = \"drawn\" draws the house from them") from None


# --------------------------------------------------------------------------- writing

def frame_matrix(x, y, theta, z=0.0):
    """A placement in the model's coordinates: its origin at (x, y, z) and its x axis turned `theta` from east."""
    c, s = math.cos(theta), math.sin(theta)
    m = np.eye(4)
    m[:2, :2] = [[c, -s], [s, c]]
    m[:3, 3] = (x, y, z)
    return m


def to_local(m, points):
    """Points in the model's coordinates as the placement `m` has them."""
    p = np.asarray(points, float)
    return (np.linalg.inv(m) @ np.column_stack([p, np.ones(len(p))]).T).T[:, :3]


def face_colour(looks, face):
    """The colour a wall face is shaded in where its picture isn't laid on it: its cladding's above the floor line,
    where its group's cladding starts."""
    zone = looks.cladding(face.spec.get("group", face.id))[0][1]
    return house_texture.material(zone)[1]


def hex_of(looks, key):
    return "#" + "".join(f"{v:02x}" for v in looks.rgb(key))


@dataclass
class Plan:
    """An element to write: what it is, its Domus property set, where it's placed and its pieces, each (positions on
    the site's grid, triangles, colour key), and the faces that are parts of it."""

    ifc_class: str
    predefined: str | None
    name: str
    props: dict
    matrix: np.ndarray
    pieces: list
    faces: list = field(default_factory=list)

    @property
    def lowest(self):
        """Its lowest point, its faces' among them."""
        return min([float(np.min(v[:, 2])) for v, _, _ in self.pieces] + [x.lowest for x in self.faces])


def plans(hm, features, looks, origin, cfg):
    """The elements the house `hm` (house.build's) is written as, in the order the build draws them."""
    e0, n0, _ = origin
    walls_id = "house-walls"
    walls_p = next(f["properties"] for f in features if f["properties"].get("id") == walls_id)
    fr = hm.frame
    house_m = frame_matrix(fr.origin[0] - e0, fr.origin[1] - n0, fr.theta)

    def face_plan(face, part, source_):
        v = face.mesh.vertices
        d = v[1, :2] - v[0, :2]
        theta = math.atan2(d[1], d[0]) if np.hypot(*d) > 1e-9 else fr.theta
        props = {"ref": face.id, "layer": face.spec.get("layer", "walls"), "part": "face", "feature": part,
                 "face": face.id, "source": source_}
        colour = face_colour(looks, face)
        return Plan("IfcWall", None, f"Wall {face.id}", props, frame_matrix(v[0, 0] - e0, v[0, 1] - n0, theta),
                    [(v, face.mesh.faces, colour)])

    out = [face_plan(f, walls_id, walls_p.get("source")) for f in hm.faces if f.part == walls_id]
    eaves = [(m.vertices, m.faces, key) for part, layer, m, key in hm.pieces if part == walls_id and layer == "eaves"]
    out.append(Plan("IfcCovering", "EAVES", "Eaves, fascia and soffit", {"ref": "eaves", "layer": "eaves", "part": "eaves",
                    "feature": walls_id, "source": walls_p.get("source")}, house_m, eaves))
    out.append(Plan("IfcRoof", ROOFS[cfg.get("roof_form", "hip")], "Roof", {"ref": "roof", "layer": "roof", "part": "roof",
                    "feature": walls_id, "source": walls_p.get("source")}, house_m,
                    [(hm.roof.vertices, hm.roof.faces, looks.roof_colour())]))
    for f in features:
        p = f["properties"]
        kind, pid = p.get("part"), p.get("id")
        if house.PARTS.get(kind) is None or pid == walls_id:
            continue
        pieces = [(m.vertices, m.faces, key) for part, _, m, key in hm.pieces if part == pid and len(m.faces)]
        faces = [face_plan(x, pid, p.get("source")) for x in hm.faces if x.part == pid]
        if not pieces and not faces:
            continue
        ifc_class, predefined = CLASSES[kind]
        every = np.vstack([v for v, _, _ in pieces] + [x.pieces[0][0] for x in faces])
        local = fr.local(every[:, :2])
        middle = fr.site((local.min(axis=0) + local.max(axis=0)) / 2)
        matrix = frame_matrix(middle[0] - e0, middle[1] - n0, fr.theta, float(every[:, 2].min()) - origin[2])
        out.append(Plan(ifc_class, predefined, p.get("name") or pid, {"ref": pid, "layer": p.get("layer", house.PARTS[kind]),
                        "part": kind, "feature": pid, "source": p.get("source")}, matrix, pieces, faces))
    refs = [x.props["ref"] for x in out] + [y.props["ref"] for x in out for y in x.faces]
    twice = sorted({r for r in refs if refs.count(r) > 1})
    if twice:
        raise DomusError(f"house.geojson: {', '.join(twice)} names more than one of the house's faces and parts, which an "
                         "IFC element knows by its id: give each its own")
    return out


def storeys(cfg):
    """The house's storeys: (Domus id, name, the setting it's at, its level), lowest first."""
    upper = float(cfg["fl_upper"])
    lower = float(cfg.get("fl_lower", upper))
    if abs(upper - lower) < 0.001:
        return [("floor", "Floor", "house.fl_upper", upper)]
    found = [("lower", "Lower floor", "house.fl_lower", lower), ("upper", "Upper floor", "house.fl_upper", upper)]
    return sorted(found, key=lambda s: s[3])


def settle_ids(w):
    """Gives every relationship and property set in `w`'s file a GlobalId from what it relates, under the house's
    namespace: IfcOpenShell makes them new each time, and the house's are the same each write."""
    counts = {}
    for entity in sorted(w.f.by_type("IfcRoot"), key=lambda e: e.id()):
        if entity.id() in w.keyed:
            continue
        cls = entity.is_a()
        if cls == "IfcRelAggregates":
            key = f"aggregates:{entity.RelatingObject.GlobalId}"
        elif cls == "IfcRelContainedInSpatialStructure":
            key = f"contains:{entity.RelatingStructure.GlobalId}"
        elif cls == "IfcRelDefinesByProperties":
            key = f"defines:{entity.RelatedObjects[0].GlobalId}:{entity.RelatingPropertyDefinition.Name}"
        elif entity.is_a("IfcPropertySetDefinition") and getattr(entity, "DefinesOccurrence", None):
            key = f"pset:{entity.DefinesOccurrence[0].RelatedObjects[0].GlobalId}:{entity.Name}"
        else:
            counts[cls] = counts.get(cls, 0) + 1
            key = f"{cls}:{counts[cls]}"
        w.same_id(entity, key)


def write(site, force=False, say=print, why=None, who=None):
    """Writes the site's house, as house.geojson and [house] draw it today, to data/house.ifc (`domus house ifc`), and
    gives the file's path. It's an edit on the change route (domus.project.changes): recorded in .domus/edits/ with
    `who`, when and `why` (a reason of its own unless given), the file as it was kept beside the record and both
    SHA-256s, so `domus site undo` puts the file back as it was, or takes away one it started. A file that's there
    already is written again only with `force`. Says what it wrote to `say`."""
    from ..project import changes

    target = path(site)
    if target.exists() and not force:
        raise DomusError(f"{FILE} is there already: domus house ifc --force writes it again from house.geojson and "
                         "[house], as an edit domus site undo can undo")
    changes.apply(site, [{"kind": "ifc-write"}], why or WHY, who, say=say)
    if source(site) != "ifc":
        say('[house] source = "ifc" in data/site.toml has the build draw the house from it.')
    return target


def draft(site, say=print):
    """The house, as house.geojson and [house] draw it today, as the text of an IFC4 file (bytes): what `write` puts
    in data/house.ifc. Says what it holds to `say`."""
    from ..build import site_origin
    from ..export import ifc
    from . import terrain

    features = geojson.read_optional(site.path / "data" / "site" / "house.geojson")
    if not any(f["properties"].get("id") == "house-walls" for f in features):
        raise DomusError("This site has no house to write: data/site/house.geojson has no house-walls")
    cfg = site.settings()
    hcfg = cfg.get("house", {})
    missing = [k for k in REQUIRED if k not in hcfg]
    if missing:
        raise DomusError(f"[house] in data/site.toml needs {', '.join(missing)} to draw the house")
    dem = terrain.ground(site)
    hm = house.build(features, hcfg["fl_upper"], hcfg["roof_pitch_deg"], hcfg["roof_plane_at_wall"],
                     hcfg["eaves_overhang_m"], lambda e, n: dem.sample(np.atleast_1d(e), np.atleast_1d(n)),
                     **house.roof_options(hcfg))
    origin = [float(v) for v in site_origin(cfg, dem)]
    e0, n0, h0 = origin
    looks = house_texture.Looks(hcfg, site.path / "data" / "house")
    todo = plans(hm, features, looks, origin, hcfg)
    title = cfg.get("site", {}).get("title", "Site model")
    georef = {"origin": origin, "domus": __version__,
              "crs": {"epsg": site.grid.epsg, "name": site.grid.label, "vertical": cfg.get("crs", {}).get("vertical") or None}}
    w = ifc.Writer(path(site).name, title, georef, site.grid.to_lonlat(e0, n0), NAMESPACE)
    building = w.spatial("IfcBuilding", "House", "building", w.site)
    building.ElevationOfRefHeight = h0
    floors = []
    for ref, name, setting, level in storeys(hcfg):
        s = w.spatial("IfcBuildingStorey", name, f"storey:{ref}", building, frame_matrix(0.0, 0.0, 0.0, level - h0))
        s.Elevation = round(level - h0, 6)
        w.properties(s, {"ref": ref, "setting": setting, "level": level})
        floors.append((level, s))

    def storey_of(lowest):
        on = [s for level, s in floors if level <= lowest + 1e-6]
        return on[-1] if on else floors[0][1]

    def items(plan):
        return [(to_local(plan.matrix, v - origin), t, key, hex_of(looks, key)) for v, t, key in plan.pieces]

    count = 0
    for plan in todo:
        props = {k: v for k, v in plan.props.items() if v is not None}
        element = w.element(plan.ifc_class, plan.predefined, plan.name, f"element:{props['ref']}", items(plan), props,
                            container=storey_of(plan.lowest), matrix=plan.matrix)
        count += 1
        for face in plan.faces:
            fprops = {k: v for k, v in face.props.items() if v is not None}
            w.element(face.ifc_class, face.predefined, face.name, f"element:{fprops['ref']}", items(face), fprops,
                      parent=element, matrix=face.matrix)
            count += 1
    settle_ids(w)
    say(f"   {FILE}: the house as house.geojson and [house] draw it, {count} elements on {len(floors)} "
        f"{'storey' if len(floors) == 1 else 'storeys'}")
    return w.f.to_string().encode("utf-8")


# --------------------------------------------------------------------------- reading

@dataclass
class Element:
    """One element of the house's IFC as the build draws it: its place in the file (`step`), GlobalId, IFC class,
    Domus id (`ref`), the house layer it's drawn in, the wall face it carries (`face`) and its `source`, and its
    pieces, each (colour "#rrggbb", positions on the site's grid (n, 3), triangles (m, 3), positions in its own
    coordinates (n, 3)). `datum` is the height on the site's datum that its own coordinates' heights are counted from
    as the file was written: its origin's."""

    step: int
    global_id: str
    ifc_class: str
    ref: str | None
    layer: str
    face: str | None
    source: str | None
    pieces: list
    datum: float = 0.0


def grid_of(f, site):
    """The file's map conversion as a function from the model's coordinates (metres, as IfcOpenShell's geometry gives
    them) to the site's grid and heights, or a DomusError for a file that isn't on the site's grid."""
    ifcopenshell = modules()
    conversions = f.by_type("IfcMapConversion")
    if not conversions:
        raise DomusError(f"{FILE} has no map conversion (IfcMapConversion), which puts it on the site's grid: domus house "
                         "ifc writes one that has")
    crs = conversions[0].TargetCRS
    name = (getattr(crs, "Name", None) or "").upper().replace("::", ":")
    if name != f"EPSG:{site.grid.epsg}":
        raise DomusError(f"{FILE} is on {name or 'a grid it does not name'}, and the site on EPSG:{site.grid.epsg}")
    scale = ifcopenshell.util.unit.calculate_unit_scale(f)
    to_map = ifcopenshell.util.geolocation.auto_local2global(f, np.eye(4))

    def convert(points):
        p = np.asarray(points, float) / scale
        return (to_map @ np.column_stack([p, np.ones(len(p))]).T).T[:, :3]

    return convert


def drawn_elements(f):
    """The elements of the file that have a body to draw: not openings and the like, which only cut others."""
    return [e for e in f.by_type("IfcElement")
            if e.Representation is not None and not e.is_a("IfcFeatureElement") and not e.is_a("IfcVirtualElement")]


def layer_of(element, props):
    """The house layer an element is drawn in: its Domus property set's, else its IFC class's."""
    if isinstance(props.get("layer"), str) and props["layer"]:
        return props["layer"]
    if element.is_a("IfcBuildingElementProxy"):
        return LAYERS.get(str(element.ObjectType or "").upper(), "outdoor")
    return next((layer for cls, layer in LAYERS.items() if cls.startswith("Ifc") and element.is_a(cls)), "outdoor")


def read(site, say=print):
    """The house's elements, from data/house.ifc, each triangulated by IfcOpenShell's geometry: [Element], in the
    order the file holds them. A file that isn't there or can't be read is a DomusError saying how to make it, and an
    element IfcOpenShell can make no body of is named to `say` and left out."""
    p = path(site)
    if not p.is_file():
        raise DomusError(f"[house] source = \"ifc\" in data/site.toml, but there's no {FILE}: domus house ifc writes it "
                         "from house.geojson and [house], or source = \"drawn\" draws the house from them")
    ifcopenshell = modules()
    f = open_file(p)
    to_grid = grid_of(f, site)
    datum = float(to_grid(np.zeros((1, 3)))[0, 2])
    scale = ifcopenshell.util.unit.calculate_unit_scale(f)
    elements = drawn_elements(f)
    shapes = {}
    if elements:
        settings = ifcopenshell.geom.settings()
        settings.set("use-world-coords", True)
        settings.set("weld-vertices", False)
        it = ifcopenshell.geom.iterator(settings, f, 1, include=elements)
        if it.initialize():
            while True:
                shape = it.get()
                g = shape.geometry
                colours = ["#" + "".join(f"{round(min(max(c, 0.0), 1.0) * 255):02x}" for c in m.diffuse.components[:3])
                           for m in g.materials]
                shapes[shape.id] = (np.asarray(g.verts, float).reshape(-1, 3), np.asarray(g.faces, np.int64).reshape(-1, 3),
                                    np.asarray(g.material_ids, np.int64), colours)
                if not it.next():
                    break
    lost = [e for e in elements if e.id() not in shapes]
    if lost:
        say(f"{FILE}: IfcOpenShell made no body of {', '.join(str(e.Tag or e.Name or e.GlobalId) for e in lost)}, so "
            f"{'it is' if len(lost) == 1 else 'they are'} left out of the house")
    out = []
    for element in sorted(elements, key=lambda e: e.id()):
        if element.id() not in shapes:
            continue
        v, faces, mids, colours = shapes[element.id()]
        props = ifcopenshell.util.element.get_psets(element).get("Domus", {})
        m = ifcopenshell.util.placement.get_local_placement(element.ObjectPlacement)
        m[:3, 3] *= scale
        local = to_local(m, v)
        grid = to_grid(v)
        pieces = []
        starts = [0, *np.flatnonzero(np.diff(mids)) + 1, len(faces)] if len(faces) else []
        for a, b in itertools.pairwise(starts):
            used, back = np.unique(faces[a:b].ravel(), return_inverse=True)
            colour = colours[mids[a]] if 0 <= mids[a] < len(colours) else "#888888"
            pieces.append((colour, grid[used], back.reshape(-1, 3), local[used]))
        ref = props.get("ref", element.Tag)
        out.append(Element(element.id(), element.GlobalId, element.is_a(), None if ref is None else str(ref),
                           layer_of(element, props), props.get("face"), props.get("source"), pieces, datum))
    return out


# --------------------------------------------------------------------------- into an export

def copy_into(w, p):
    """Copies the house in the IFC file at `p` into the file `w` (domus.export.ifc.Writer) is writing, as it is: its
    buildings into w's site, its storeys into them and its elements into those, each with its GlobalId, body, styles
    and property sets, where it stands by the two files' map conversions."""
    ifcopenshell = modules()
    api = ifcopenshell.api
    source_file = open_file(p)
    reuse, copies = {}, {}

    def copy(entity):
        new = api.project.append_asset(w.f, library=source_file, element=entity, reuse_identities=reuse)
        copies[entity.id()] = new
        return new

    def parent_of(entity):
        rel = (getattr(entity, "Decomposes", None) or getattr(entity, "ContainedInStructure", None) or [None])[0]
        if rel is None:
            return None, None
        return (rel.RelatingObject, "part") if rel.is_a("IfcRelAggregates") else (rel.RelatingStructure, "in")

    for building in source_file.by_type("IfcBuilding"):
        api.aggregate.assign_object(w.f, products=[copy(building)], relating_object=w.site)
    for storey in source_file.by_type("IfcBuildingStorey"):
        above, _ = parent_of(storey)
        target = copies.get(above.id()) if above is not None else None
        api.aggregate.assign_object(w.f, products=[copy(storey)], relating_object=target or w.site)
    pending = sorted(source_file.by_type("IfcElement"), key=lambda e: e.id())
    while pending:
        later = []
        for element in pending:
            above, how = parent_of(element)
            if above is not None and above.id() not in copies and above.is_a("IfcElement"):
                later.append(element)              # a part of an element not copied yet
                continue
            new = copy(element)
            target = copies.get(above.id()) if above is not None else None
            if how == "part" and target is not None:
                api.aggregate.assign_object(w.f, products=[new], relating_object=target)
            else:
                api.spatial.assign_container(w.f, products=[new], relating_structure=target or w.site)
        if len(later) == len(pending):
            raise DomusError(f"{FILE}: elements that are parts of each other in a ring can't be copied")
        pending = later
