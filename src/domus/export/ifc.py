"""IFC4: an export's parts (domus.export.scene) as a model for architects' and builders' software, written with
IfcOpenShell (the `ifc` extra), which nothing else in an export needs.

The project holds one site, georeferenced: its map conversion takes the model's coordinates (metres east, north and
up of the model's origin) to the site's grid (an IfcProjectedCRS named by its EPSG code) and heights, with the origin
as its false easting, northing and height. On the site stand the ground (a geographic element, terrain), the
existing walls, the site's features and drains, the lawns and the house (a building, its parts typed where the model
knows what they are). With a wall option, its designed ground, walls (IfcWall), steps (IfcStair) and terraces, lawns
and beds (geographic elements) stand in for what they replace. Each is an IfcTriangulatedFaceSet in the colour the
viewer gives it, and carries a property set, Domus, with the model's numbers for it: its id (`ref`, which is its Tag
too), name and material, its levels, heights and length for a wall, its risers for steps, the option's volumes on its
designed ground. Plants, lines and labels are left out. A site whose house is kept as an IFC file
(domus.model.house_ifc) has that file's building, storeys and elements copied in as they are, in place of the
house's layers; its writer, Writer, writes that file too.
"""

import uuid

import numpy as np

from ..errors import DomusError
from .scene import hex_rgb

INSTALL = ("Writing IFC needs IfcOpenShell, Domus's ifc extra: uv sync --extra ifc in a copy of Domus, or "
           "pip install 'domus[ifc]'. The container image has it.")
PSET = "Domus"
# House parts by their layer's key (domus.build.HOUSE_LAYERS): the IFC class and predefined type each is.
HOUSE = {"walls": ("IfcWall", None), "roof": ("IfcRoof", None), "chimney": ("IfcChimney", None),
         "steps": ("IfcStair", None)}
LEVELS = ("top_min", "top_max", "toe_min", "toe_max", "foot", "head")


def ifcopenshell_modules():
    """IfcOpenShell and the parts of its API used here, or a DomusError saying how to install it."""
    try:
        import ifcopenshell
        import ifcopenshell.api.aggregate
        import ifcopenshell.api.context
        import ifcopenshell.api.geometry
        import ifcopenshell.api.georeference
        import ifcopenshell.api.material
        import ifcopenshell.api.project
        import ifcopenshell.api.pset
        import ifcopenshell.api.root
        import ifcopenshell.api.spatial
        import ifcopenshell.api.style
        import ifcopenshell.api.unit
        import ifcopenshell.guid
        import ifcopenshell.util.geolocation
        import ifcopenshell.util.shape_builder
    except ImportError as e:
        raise DomusError(INSTALL) from e
    return ifcopenshell


def kind(part, model):
    """What `part` is in IFC: (class, predefined type, where it stands: "site" or "house"), or None for what IFC
    leaves out (plants, lines)."""
    layer = part.layer
    lid, option = layer["id"], layer.get("option")
    if layer["type"] == "lines" or layer.get("cards") or layer.get("limbs") or layer.get("stumps"):
        return None
    if lid == "ground" or layer.get("role") == "ground":
        return "IfcGeographicElement", "TERRAIN", "site"
    if option:
        rest = lid.removeprefix(f"option-{option}-")
        if rest.startswith("steps-"):
            return "IfcStair", "STRAIGHT_RUN_STAIR", "site"
        if rest.startswith("terrace-"):
            terrace = terraces(model, option).get(rest.removeprefix("terrace-"), {})
            return "IfcGeographicElement", (terrace.get("kind") or "terrace").upper(), "site"
        if rest.startswith("lawn-"):
            return "IfcGeographicElement", "LAWN", "site"
        if layer["type"] == "mesh" and not layer.get("role"):
            return "IfcWall", "RETAININGWALL", "site"
        return None
    if lid.startswith("house-"):
        key = lid.removeprefix("house-")
        ifc_class, predefined = HOUSE.get("roof" if key.startswith("roof") else key, ("IfcBuildingElementProxy", None))
        return ifc_class, predefined, "house"
    if lid.startswith("wall-"):
        return "IfcWall", None, "site"
    if lid.startswith("pipe-"):
        return "IfcPipeSegment", None, "site"
    if layer["type"] == "vegetation":         # a lawn, or the soil under the beds, drawn on the ground
        return "IfcGeographicElement", "PLANTING" if lid == "veg-bed-floor" else "LAWN", "site"
    if layer["type"] in ("mesh", "pipes"):
        return "IfcBuildingElementProxy", None, "site"
    return None


def terraces(model, option):
    """The option's terraces, lawns and beds, by id, as its summary has them."""
    found = next((o for o in model.get("summary", {}).get("options", []) if o["id"] == option), {})
    return {t["id"]: t for t in found.get("terraces", [])}


def option_numbers(o):
    """What an option comes to, as properties of its designed ground: the comparison's numbers."""
    flat = o.get("flat", {})
    flags = [f"{w['id']}: {f}" for w in o.get("walls", []) for f in w.get("flags", [])]
    flags += [f"{s['id']}: {f}" for s in o.get("stairs", []) for f in s.get("flags", [])]
    flags += [f"Ground: {e['flag']}" for e in o.get("edge", [])] + [f"Drains: {d['flag']}" for d in o.get("drains", [])]
    volumes = {"regraded_area_m2" if k == "area_m2" else k: v for k, v in o.get("volumes", {}).items()}
    return {"id": o["id"], "name": o.get("name"), "short": o.get("short"), "description": o.get("description"),
            **volumes, "flat_before_m2": flat.get("before_m2"), "flat_after_m2": flat.get("after_m2"),
            "flat_gain_m2": flat.get("gain_m2"), "removed_existing_m": o.get("removed_existing_m"),
            "keep_existing": ", ".join(o.get("keep_existing", [])) or "none", "steps": o.get("steps"),
            "checks": "; ".join(flags) or "none"}


def properties(part, model, option):
    """The Domus property set of `part`: the layer it comes from and what the model has to say about it. The id the
    model gives a wall, a flight of steps, a terrace or an option is `ref`, as IfcOpenShell's own reading of a property
    set (ifcopenshell.util.element.get_psets) puts the set's own number under `id`."""
    layer = part.layer
    lid = layer["id"]
    props = {"layer": lid}
    if lid == f"option-{option}-ground":
        found = next((o for o in model.get("summary", {}).get("options", []) if o["id"] == option), None)
        props.update(option_numbers(found) if found else {})
    elif option and lid.startswith(f"option-{option}-terrace-"):
        props.update(terraces(model, option).get(lid.removeprefix(f"option-{option}-terrace-"), {}))
    props.update(part.info)
    if part.plant:
        props["plant"] = part.plant
    if any(k in props for k in LEVELS) and model.get("vertical"):
        props["levels_datum"] = model["vertical"]
    if "id" in props:
        props = {("ref" if k == "id" else k): v for k, v in props.items()}
    return props


def measure(f, key, value):
    """A property's value as IFC types it: lengths and levels, areas and volumes in the project's metres, counts as
    whole numbers, and text as labels or, when long, as text."""
    if isinstance(value, bool):
        return f.create_entity("IfcBoolean", value)
    if isinstance(value, int):
        return f.create_entity("IfcInteger", value)
    if isinstance(value, float):
        if not np.isfinite(value):
            return None
        if key.endswith("_m3"):
            return f.create_entity("IfcVolumeMeasure", value)
        if key.endswith("_m2"):
            return f.create_entity("IfcAreaMeasure", value)
        if key.endswith("_m") or key in LEVELS:
            return f.create_entity("IfcLengthMeasure", value)
        return f.create_entity("IfcReal", value)
    if isinstance(value, (list, tuple)):
        value = ", ".join(str(v) for v in value)
    if value is None or isinstance(value, dict):
        return None
    text = str(value)
    return f.create_entity("IfcText" if len(text) > 255 else "IfcLabel", text)


def shell(v, faces, tol=1e-6):
    """A mesh's corners welded where its pieces meet (a wall's pieces each have corners of their own, for its picture),
    less any triangle that closes up: (positions, triangles, and whether it's a closed solid). It's one only where
    Manifold takes it as one, with a volume: every edge in two triangles isn't enough, as the two can face the same
    way."""
    import manifold3d as mf

    v = np.asarray(v, float)
    key = np.round((v - v.min(axis=0)) / tol).astype(np.int64)
    _, first, inverse = np.unique(key, axis=0, return_index=True, return_inverse=True)
    f = inverse.reshape(-1)[np.asarray(faces, np.int64).reshape(-1, 3)]
    f = f[(f[:, 0] != f[:, 1]) & (f[:, 1] != f[:, 2]) & (f[:, 2] != f[:, 0])]
    p = v[first]
    if not len(f):
        return p, f, False
    solid = mf.Manifold(mf.Mesh64(vert_properties=np.ascontiguousarray(p - p.mean(axis=0)), tri_verts=np.ascontiguousarray(f, np.uint64)))
    return p, f, solid.status() == mf.Error.NoError and solid.volume() > 0.0


def local(positions):
    """Viewer metres (x east, y up, z south) as the IFC model's coordinates: east, north and up of the origin."""
    p = np.asarray(positions, float)
    return np.column_stack([p[:, 0], -p[:, 2], p[:, 1]])


class Writer:
    """An IFC4 file being written with IfcOpenShell (`f`): its project, its units in metres, its body context (`body`)
    and one site (`site`), georeferenced as the export's are. Every GlobalId it gives comes from a key under the uuid5
    namespace `space`, so the same things written again get the same ids; `keyed` holds the STEP ids of those given
    one so far."""

    def __init__(self, name, title, georef, lonlat, space):
        ifcopenshell = self.ifc = ifcopenshell_modules()
        api, util = ifcopenshell.api, ifcopenshell.util
        self.space, self.keyed = space, set()
        E0, N0, H0 = (float(v) for v in georef["origin"])
        f = self.f = api.project.create_file(version="IFC4")
        f.header.file_name.name = name
        f.header.file_name.originating_system = f"Domus {georef['domus']}"
        f.header.file_name.authorization = ""
        f.header.file_description.description = ("ViewDefinition [ReferenceView_V1.2]",)
        self.project = self.same_id(api.root.create_entity(f, ifc_class="IfcProject", name=title), "project")
        metre = api.unit.add_si_unit(f, unit_type="LENGTHUNIT")
        api.unit.assign_unit(f, units=[metre, api.unit.add_si_unit(f, unit_type="AREAUNIT"),
                                       api.unit.add_si_unit(f, unit_type="VOLUMEUNIT"),
                                       api.unit.add_si_unit(f, unit_type="PLANEANGLEUNIT")])
        model3d = api.context.add_context(f, context_type="Model")
        self.body = api.context.add_context(f, context_type="Model", context_identifier="Body", target_view="MODEL_VIEW",
                                            parent=model3d)
        api.georeference.add_georeferencing(f, ifc_class="IfcMapConversion", name=f"EPSG:{georef['crs']['epsg']}")
        api.georeference.edit_georeferencing(
            f, projected_crs={"Name": f"EPSG:{georef['crs']['epsg']}", "Description": georef["crs"].get("name"),
                              "VerticalDatum": georef["crs"].get("vertical") or None, "MapUnit": metre},
            coordinate_operation={"Eastings": E0, "Northings": N0, "OrthogonalHeight": H0, "XAxisAbscissa": 1.0,
                                  "XAxisOrdinate": 0.0, "Scale": 1.0})
        self.site = site = self.same_id(api.root.create_entity(f, ifc_class="IfcSite", name=title), "site")
        site.RefLatitude = util.geolocation.dd2dms(lonlat[1], use_us=True)
        site.RefLongitude = util.geolocation.dd2dms(lonlat[0], use_us=True)
        site.RefElevation = H0
        api.aggregate.assign_object(f, products=[site], relating_object=self.project)
        api.geometry.edit_object_placement(f, product=site)
        self.builder = util.shape_builder.ShapeBuilder(f)
        self.styles, self.materials = {}, {}

    def same_id(self, element, key):
        """`element` with the GlobalId `key` gives it: the same thing in the same file gets the same id each time."""
        element.GlobalId = self.ifc.guid.compress(uuid.uuid5(self.space, key).hex)
        self.keyed.add(element.id())
        return element

    def spatial(self, ifc_class, name, key, parent, matrix=None):
        """A building or a storey, `ifc_class`, in `parent`, placed by `matrix` (the identity unless given)."""
        api = self.ifc.api
        element = self.same_id(api.root.create_entity(self.f, ifc_class=ifc_class, name=name), key)
        api.aggregate.assign_object(self.f, products=[element], relating_object=parent)
        api.geometry.edit_object_placement(self.f, product=element, matrix=matrix)
        return element

    def style(self, name, colour):
        """The surface style `name`, shaded in `colour` ("#rrggbb"): made the first time it's asked for."""
        if name not in self.styles:
            api = self.ifc.api
            style = api.style.add_style(self.f, name=name)
            rgb = hex_rgb(colour)
            api.style.add_surface_style(self.f, style=style, ifc_class="IfcSurfaceStyleShading", attributes={
                "SurfaceColour": {"Name": None, "Red": rgb[0], "Green": rgb[1], "Blue": rgb[2]}, "Transparency": 0.0})
            self.styles[name] = style
        return self.styles[name]

    def element(self, ifc_class, predefined, name, key, items, props, container=None, parent=None, matrix=None):
        """An element of `ifc_class` (with its `predefined` type, which IfcOpenShell makes USERDEFINED with it as the
        object type where the class has no such type), its GlobalId from `key`: contained in `container` or a part of
        `parent`, placed by `matrix` (4 by 4 in the model's coordinates, the identity unless given), its body
        `items`, each (positions in its own coordinates, triangles, style name, colour, and optionally whether it bounds
        a solid, which marks it Closed) a triangulated face set in its style, and its Domus property set `props`, whose
        `ref` is its Tag too and whose `material` names its material."""
        api, f = self.ifc.api, self.f
        element = self.same_id(api.root.create_entity(f, ifc_class=ifc_class, predefined_type=predefined, name=name), key)
        if container is not None:
            api.spatial.assign_container(f, products=[element], relating_structure=container)
        else:
            api.aggregate.assign_object(f, products=[element], relating_object=parent)
        api.geometry.edit_object_placement(f, product=element, matrix=matrix)
        if items:
            facesets = [self.builder.triangulated_face_set(np.asarray(item[0], float).tolist(), np.asarray(item[1]).tolist())
                        for item in items]
            for item, faceset in zip(items, facesets, strict=True):
                if len(item) > 4 and item[4]:
                    faceset.Closed = True
            rep = f.create_entity("IfcShapeRepresentation", ContextOfItems=self.body, RepresentationIdentifier="Body",
                                  RepresentationType="Tessellation", Items=facesets)
            api.geometry.assign_representation(f, product=element, representation=rep)
            api.style.assign_representation_styles(f, shape_representation=rep,
                                                   styles=[self.style(item[2], item[3]) for item in items])
        if props.get("ref") is not None:
            element.Tag = str(props["ref"])
        self.properties(element, props)
        if isinstance(props.get("material"), str) and props["material"]:
            material = props["material"]
            if material not in self.materials:
                self.materials[material] = api.material.add_material(f, name=material)
            api.material.assign_material(f, products=[element], material=self.materials[material])
        return element

    def properties(self, element, props):
        """`element`'s Domus property set: `props`, each typed as measure() types it."""
        api, f = self.ifc.api, self.f
        typed = {k: m for k, val in props.items() if (m := measure(f, k, val)) is not None}
        pset = api.pset.add_pset(f, product=element, name=PSET)
        api.pset.edit_pset(f, pset=pset, properties=typed)
        return pset


def write(parts, model, path, georef, lonlat, name=None, house=None):
    """Writes `parts` (domus.export.scene.parts) as an IFC4 file at `path`, which its header names `name` (the path's
    own name unless given). `georef` holds the site's grid, heights and origin, and `lonlat` the origin's longitude
    and latitude, for the site's reference point. `house`, the path of the site's house kept as an IFC file
    (domus.model.house_ifc), takes the place of the model's house: its building, storeys and elements are copied in
    as they are."""
    title = model.get("title") or "Site model"
    E0, N0, H0 = (float(v) for v in georef["origin"])
    space = uuid.uuid5(uuid.NAMESPACE_URL, f"domus:{georef['crs']['epsg']}:{E0:.3f}:{N0:.3f}:{H0:.3f}")
    w = Writer(name or path.name, title, georef, lonlat, space)
    shown = georef.get("option")
    w.site.Description = f"With {shown['name']}" if shown else "As it is"
    storey = None
    if house is not None:
        from ..model import house_ifc

        house_ifc.copy_into(w, house)
        parts = [p for p in parts if not ((k := kind(p, model)) and k[2] == "house")]
    elif any((k := kind(p, model)) and k[2] == "house" for p in parts):
        building = w.spatial("IfcBuilding", "House", "house", w.site)
        building.ElevationOfRefHeight = H0
        floor = model.get("summary", {}).get("house", {}).get("fl_upper")
        storey = w.spatial("IfcBuildingStorey", "Floor", "storey", building)
        storey.Elevation = round(float(floor) - H0, 3) if floor is not None else 0.0
    for part in parts:
        what = kind(part, model)
        if what is None:
            continue
        v, faces = part.mesh()
        if not len(faces):
            continue
        ifc_class, predefined, where = what
        closed = False
        if ifc_class in ("IfcWall", "IfcStair") and where == "site":       # walls and steps the build draws: solids
            v, faces, closed = shell(v, faces)
        w.element(ifc_class, predefined, part.name, f"layer:{part.id}", [(local(v), faces, part.colour, part.colour, closed)],
                  properties(part, model, shown and shown["id"]), container=storey if where == "house" else w.site)
    w.f.write(str(path))
