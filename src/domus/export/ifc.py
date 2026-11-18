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
designed ground. Plants, lines and labels are left out.
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


def local(positions):
    """Viewer metres (x east, y up, z south) as the IFC model's coordinates: east, north and up of the origin."""
    p = np.asarray(positions, float)
    return np.column_stack([p[:, 0], -p[:, 2], p[:, 1]])


def write(parts, model, path, georef, lonlat, name=None):
    """Writes `parts` (domus.export.scene.parts) as an IFC4 file at `path`, which its header names `name` (the path's
    own name unless given). `georef` holds the site's grid, heights and origin, and `lonlat` the origin's longitude
    and latitude, for the site's reference point."""
    ifcopenshell = ifcopenshell_modules()
    api, util = ifcopenshell.api, ifcopenshell.util
    title = model.get("title") or "Site model"
    E0, N0, H0 = (float(v) for v in georef["origin"])
    space = uuid.uuid5(uuid.NAMESPACE_URL, f"domus:{georef['crs']['epsg']}:{E0:.3f}:{N0:.3f}:{H0:.3f}")

    def same_id(element, key):          # the same thing in the same site gets the same GlobalId each export
        element.GlobalId = ifcopenshell.guid.compress(uuid.uuid5(space, key).hex)
        return element

    f = api.project.create_file(version="IFC4")
    f.header.file_name.name = name or path.name
    f.header.file_name.originating_system = f"Domus {georef['domus']}"
    f.header.file_name.authorization = ""
    f.header.file_description.description = ("ViewDefinition [ReferenceView_V1.2]",)
    project = same_id(api.root.create_entity(f, ifc_class="IfcProject", name=title), "project")
    metre = api.unit.add_si_unit(f, unit_type="LENGTHUNIT")
    api.unit.assign_unit(f, units=[metre, api.unit.add_si_unit(f, unit_type="AREAUNIT"),
                                   api.unit.add_si_unit(f, unit_type="VOLUMEUNIT"),
                                   api.unit.add_si_unit(f, unit_type="PLANEANGLEUNIT")])
    model3d = api.context.add_context(f, context_type="Model")
    body = api.context.add_context(f, context_type="Model", context_identifier="Body", target_view="MODEL_VIEW",
                                   parent=model3d)
    api.georeference.add_georeferencing(f, ifc_class="IfcMapConversion", name=f"EPSG:{georef['crs']['epsg']}")
    api.georeference.edit_georeferencing(
        f, projected_crs={"Name": f"EPSG:{georef['crs']['epsg']}", "Description": georef["crs"].get("name"),
                          "VerticalDatum": georef["crs"].get("vertical") or None, "MapUnit": metre},
        coordinate_operation={"Eastings": E0, "Northings": N0, "OrthogonalHeight": H0, "XAxisAbscissa": 1.0,
                              "XAxisOrdinate": 0.0, "Scale": 1.0})

    shown = georef.get("option")
    site = same_id(api.root.create_entity(f, ifc_class="IfcSite", name=title), "site")
    site.Description = f"With {shown['name']}" if shown else "As it is"
    site.RefLatitude = util.geolocation.dd2dms(lonlat[1], use_us=True)
    site.RefLongitude = util.geolocation.dd2dms(lonlat[0], use_us=True)
    site.RefElevation = H0
    api.aggregate.assign_object(f, products=[site], relating_object=project)
    api.geometry.edit_object_placement(f, product=site)
    storey = None
    if any((k := kind(p, model)) and k[2] == "house" for p in parts):
        house = same_id(api.root.create_entity(f, ifc_class="IfcBuilding", name="House"), "house")
        house.ElevationOfRefHeight = H0
        api.aggregate.assign_object(f, products=[house], relating_object=site)
        api.geometry.edit_object_placement(f, product=house)
        floor = model.get("summary", {}).get("house", {}).get("fl_upper")
        storey = same_id(api.root.create_entity(f, ifc_class="IfcBuildingStorey", name="Floor"), "storey")
        storey.Elevation = round(float(floor) - H0, 3) if floor is not None else 0.0
        api.aggregate.assign_object(f, products=[storey], relating_object=house)
        api.geometry.edit_object_placement(f, product=storey)

    builder = util.shape_builder.ShapeBuilder(f)
    styles, materials = {}, {}
    for part in parts:
        what = kind(part, model)
        if what is None:
            continue
        v, faces = part.mesh()
        if not len(faces):
            continue
        ifc_class, predefined, where = what
        element = same_id(api.root.create_entity(f, ifc_class=ifc_class, predefined_type=predefined, name=part.name),
                          f"layer:{part.id}")
        api.spatial.assign_container(f, products=[element], relating_structure=storey if where == "house" else site)
        api.geometry.edit_object_placement(f, product=element)
        faceset = builder.triangulated_face_set(local(v).tolist(), faces.tolist())
        rep = f.create_entity("IfcShapeRepresentation", ContextOfItems=body, RepresentationIdentifier="Body",
                              RepresentationType="Tessellation", Items=[faceset])
        api.geometry.assign_representation(f, product=element, representation=rep)
        if part.colour not in styles:
            style = api.style.add_style(f, name=part.colour)
            rgb = hex_rgb(part.colour)
            api.style.add_surface_style(f, style=style, ifc_class="IfcSurfaceStyleShading", attributes={
                "SurfaceColour": {"Name": None, "Red": rgb[0], "Green": rgb[1], "Blue": rgb[2]}, "Transparency": 0.0})
            styles[part.colour] = style
        api.style.assign_representation_styles(f, shape_representation=rep, styles=[styles[part.colour]])
        props = properties(part, model, shown and shown["id"])
        if props.get("ref") is not None:
            element.Tag = str(props["ref"])
        typed = {k: m for k, val in props.items() if (m := measure(f, k, val)) is not None}
        pset = api.pset.add_pset(f, product=element, name=PSET)
        api.pset.edit_pset(f, pset=pset, properties=typed)
        if isinstance(props.get("material"), str) and props["material"]:
            name = props["material"]
            if name not in materials:
                materials[name] = api.material.add_material(f, name=name)
            api.material.assign_material(f, products=[element], material=materials[name])
    f.write(str(path))
