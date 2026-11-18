"""domus export and site.export: the built model as glTF (.glb) and IFC (.ifc), on the made-up sites."""

import base64
import json
import shutil
import struct
import sys
from pathlib import Path

import numpy as np
import pytest

from domus import api
from domus.errors import DomusError
from domus.export import scene

COMPONENTS = {5126: ("<f4", 4), 5125: ("<u4", 4), 5123: ("<u2", 2), 5121: ("u1", 1)}
WIDTH = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4}


def read_glb(path):
    """A .glb's JSON and binary chunk, once its header and chunks are as glTF 2.0 lays them out."""
    data = path.read_bytes()
    magic, version, length = struct.unpack_from("<4sII", data)
    assert (magic, version, length) == (b"glTF", 2, len(data))
    json_length, json_type = struct.unpack_from("<I4s", data, 12)
    assert json_type == b"JSON" and json_length % 4 == 0
    doc = json.loads(data[20:20 + json_length].decode("utf-8"))
    at = 20 + json_length
    bin_length, bin_type = struct.unpack_from("<I4s", data, at)
    assert bin_type == b"BIN\0" and bin_length % 4 == 0 and at + 8 + bin_length == len(data)
    (buffer,) = doc["buffers"]
    assert "uri" not in buffer and buffer["byteLength"] <= bin_length < buffer["byteLength"] + 4
    for view in doc["bufferViews"]:
        assert view["byteOffset"] % 4 == 0 and view["byteOffset"] + view["byteLength"] <= buffer["byteLength"]
    return doc, data[at + 8:at + 8 + bin_length]


def accessor(doc, binary, i):
    """An accessor's values, once it's inside its buffer view and its bounds are its values'."""
    a = doc["accessors"][i]
    view = doc["bufferViews"][a["bufferView"]]
    dtype, size = COMPONENTS[a["componentType"]]
    width = WIDTH[a["type"]]
    start = view["byteOffset"] + a.get("byteOffset", 0)
    assert start % size == 0 and a.get("byteOffset", 0) + a["count"] * width * size <= view["byteLength"]
    values = np.frombuffer(binary, dtype, a["count"] * width, start).reshape(a["count"], width)
    assert values.min(axis=0).tolist() == a["min"] and values.max(axis=0).tolist() == a["max"]
    return values


def vertices(doc, binary):
    """The vertices of each node with a mesh, by its layer and plant, once every primitive's attributes are as long
    as its positions, its normals unit length and its indices in range."""
    found = {}
    for node in doc["nodes"]:
        if "mesh" not in node:
            continue
        for prim in doc["meshes"][node["mesh"]]["primitives"]:
            count = doc["accessors"][prim["attributes"]["POSITION"]]["count"]
            for name, i in prim["attributes"].items():
                values = accessor(doc, binary, i)
                assert len(values) == count and np.isfinite(values).all()
                if name == "NORMAL":
                    assert np.allclose(np.linalg.norm(values, axis=1), 1, atol=1e-4)
            if "indices" in prim:
                index = accessor(doc, binary, prim["indices"]).ravel()
                assert prim["mode"] == 4 and len(index) % 3 == 0 and index.max() < count
            else:
                assert prim["mode"] == 1 and count % 2 == 0
            assert prim["material"] < len(doc["materials"])
            key = (node["extras"]["layer"], node["extras"].get("plant"))
            found[key] = found.get(key, 0) + count
    return found


def by_layer(found):
    out = {}
    for (layer, _), n in found.items():
        out[layer] = out.get(layer, 0) + n
    return out


def expected_vertices(layer):
    """How many vertices the viewer draws a layer with, worked out from the model alone: a mesh's own, two for each
    line, four for each leaf card, eighteen for each limb, a stump's 64 or a mound's 126, and 22 for each run of a
    drain."""
    kind = layer["type"]
    if kind == "mesh":
        return len(layer["positions"]) // 3
    if kind == "lines":
        return len(layer["segments"]) // 3
    if kind == "pipes":
        return (len(layer["path"]) // 3 - 1) * 22
    n = 0
    if layer.get("cards"):
        n += 4 * len(base64.b64decode(layer["cards"]["attributes"])) // 10
    if layer.get("limbs"):
        n += 18 * len(layer["limbs"]["items"])
    if layer.get("stumps"):
        n += sum(126 if it["mound"] else 64 for it in layer["stumps"]["items"])
    if layer.get("drape"):
        n += len(layer["drape"]["positions"]) // 3
    return n


def plan_area(v, faces):
    """The area a mesh covers in plan (x and z), each triangle counted once."""
    p = v[faces][:, :, [0, 2]]
    a, b = p[:, 1] - p[:, 0], p[:, 2] - p[:, 0]
    return float(np.abs(a[:, 0] * b[:, 1] - a[:, 1] * b[:, 0]).sum() / 2)


@pytest.fixture(scope="module")
def model(built):
    return json.loads((built / "model.json").read_text(encoding="utf-8"))


def test_the_site_as_it_is_is_a_glb_holding_what_the_viewer_shows(built, model, tmp_path):
    path = api.open_site().export(tmp_path / "site.glb")
    assert path == (tmp_path / "site.glb").resolve()
    doc, binary = read_glb(path)
    found = by_layer(vertices(doc, binary))
    shown = {layer["id"]: layer for layer in model["layers"] if layer["type"] != "labels"
             and layer.get("visible", True) and layer["id"] != "contours-minor" and not layer.get("option")}
    assert set(found) == set(shown)
    assert {k: expected_vertices(shown[k]) for k in found} == found
    assert {"ground", "house-roof", "wall-W1", "veg-trees", "veg-lawn-front"} <= set(found)
    assert not any(k.startswith("option-") for k in found) and "dsm" not in found
    extras = doc["asset"]["extras"]
    assert extras["crs"]["epsg"] == 2193 and extras["crs"]["vertical"] == "NZVD2016"
    assert extras["origin"] == model["origin"] and extras["option"] is None
    assert doc["asset"]["copyright"] == model["attribution"]
    # The aerial is the ground's picture, as it is, and the leaf cards are cut where their picture is clear.
    ground = next(n for n in doc["nodes"] if n.get("extras", {}).get("layer") == "ground")
    material = doc["materials"][doc["meshes"][ground["mesh"]]["primitives"][0]["material"]]
    image = doc["images"][doc["textures"][material["pbrMetallicRoughness"]["baseColorTexture"]["index"]]["source"]]
    view = doc["bufferViews"][image["bufferView"]]
    assert binary[view["byteOffset"]:view["byteOffset"] + view["byteLength"]] == (built / "aerial.jpg").read_bytes()
    assert any(m.get("alphaMode") == "MASK" and m.get("doubleSided") for m in doc["materials"])


def test_an_option_takes_the_place_of_what_it_replaces(built, model, tmp_path):
    option = "b-two-tiers"
    doc, binary = read_glb(api.open_site().export(tmp_path / "b.glb", option=option))
    found = by_layer(vertices(doc, binary))
    ours = {layer["id"] for layer in model["layers"] if layer.get("option") == option}
    assert ours <= set(found) and not any(k.startswith("option-a-") for k in found)
    assert "wall-W1" not in found and "veg-lawn-front" not in found and "veg-trees" in found
    assert doc["asset"]["extras"]["option"]["id"] == option
    # The ground is cut where the option's designed ground takes over, and only there.
    x0, z0, x1, z1 = scene.option_box(model, option)
    node = next(n for n in doc["nodes"] if n.get("extras", {}).get("layer") == "ground")
    prim = doc["meshes"][node["mesh"]]["primitives"][0]
    v = accessor(doc, binary, prim["attributes"]["POSITION"]).astype(float)
    faces = accessor(doc, binary, prim["indices"]).reshape(-1, 3)
    centre = v[faces].mean(axis=1)
    assert not ((centre[:, 0] > x0) & (centre[:, 0] < x1) & (centre[:, 2] > z0) & (centre[:, 2] < z1)).any()
    whole = next(layer for layer in model["layers"] if layer["id"] == "ground")
    full = plan_area(np.reshape(whole["positions"], (-1, 3)), np.reshape(whole["indices"], (-1, 3)))
    assert plan_area(v, faces) == pytest.approx(full - (x1 - x0) * (z1 - z0), rel=1e-4)
    contours = next(layer for layer in model["layers"] if layer["id"] == "contours-major")
    assert found["contours-major"] < expected_vertices(contours)


@pytest.fixture
def ifcopenshell():
    """IfcOpenShell, which reads the IFC back: the tests that need it are skipped without the ifc extra."""
    return pytest.importorskip("ifcopenshell", reason="reading IFC back needs the ifc extra")


def psets(element):
    import ifcopenshell.util.element as ue

    return ue.get_psets(element)


def test_an_ifc_opens_again_georeferenced_with_the_options_numbers(built, model, tmp_path, ifcopenshell):
    path = api.open_site().export(tmp_path / "a.ifc", option="a-one-wall")
    f = ifcopenshell.open(str(path))
    assert f.schema == "IFC4" and len(f.by_type("IfcProject")) == 1 and len(f.by_type("IfcSite")) == 1
    (conversion,) = f.by_type("IfcMapConversion")
    e0, n0, h0 = model["origin"]
    assert (conversion.Eastings, conversion.Northings, conversion.OrthogonalHeight) == (e0, n0, h0)
    assert (conversion.XAxisAbscissa, conversion.XAxisOrdinate, conversion.Scale) == (1.0, 0.0, 1.0)
    assert conversion.TargetCRS.Name == "EPSG:2193" and conversion.TargetCRS.VerticalDatum == "NZVD2016"
    assert conversion.TargetCRS.MapUnit.Name == "METRE" and conversion.SourceCRS.ContextType == "Model"
    assert f.by_type("IfcSite")[0].RefElevation == h0
    terrain = {e.Name: e for e in f.by_type("IfcGeographicElement") if e.PredefinedType == "TERRAIN"}
    assert set(terrain) == {"Ground (LiDAR, with walls cut in)", "A. One wall: designed ground"}
    designed = psets(terrain["A. One wall: designed ground"])["Domus"]
    summary = scene.options(model)["a-one-wall"]
    assert designed["ref"] == "a-one-wall" and designed["fill_m3"] == summary["volumes"]["fill_m3"]
    # The option's wall, with the model's numbers, and its lawn; the old wall it takes out is gone.
    (wall,) = [w for w in f.by_type("IfcWall") if w.Tag]
    info = next(layer["info"] for layer in model["layers"] if layer["id"] == "option-a-one-wall-A1")
    numbers = psets(wall)["Domus"]
    assert wall.Tag == "A1" and wall.ObjectType == "RETAININGWALL" and numbers["ref"] == "A1"
    for key in ("name", "material", "length_m", "height_min_m", "height_max_m", "top_min", "top_max", "toe_min", "toe_max"):
        assert numbers[key] == info[key], key
    assert info["toe_max"] < info["top_min"] and numbers["levels_datum"] == "NZVD2016"
    (shell,) = wall.Representation.Representations[0].Items              # a closed solid, its pieces' corners welded
    tri = np.asarray(shell.CoordIndex) - 1
    edges = np.sort(np.concatenate([tri[:, [0, 1]], tri[:, [1, 2]], tri[:, [2, 0]]]), axis=1)
    assert shell.Closed is True and (np.unique(edges, axis=0, return_counts=True)[1] == 2).all()
    import ifcopenshell.util.element as ue

    assert ue.get_material(wall).Name == "timber" and ue.get_container(wall).is_a("IfcSite")
    lawns = [e for e in f.by_type("IfcGeographicElement") if e.ObjectType == "LAWN"]
    assert any(psets(e)["Domus"].get("area_m2") == 108.0 for e in lawns)
    assert not [w for w in f.by_type("IfcWall") if w.Name == "Old block wall"]
    # The house is a building, its roof a roof; the ground's points are the model's, on the grid by the conversion.
    assert [e.is_a() for e in f.by_type("IfcRoof")] == ["IfcRoof"]
    assert ue.get_container(f.by_type("IfcRoof")[0]).is_a("IfcBuildingStorey")
    ground = terrain["Ground (LiDAR, with walls cut in)"]
    (faceset,) = ground.Representation.Representations[0].Items
    assert faceset.is_a("IfcTriangulatedFaceSet") and faceset.Closed is None          # a surface, not a solid
    local = np.asarray(faceset.Coordinates.CoordList)
    lo = np.array([e0, n0, h0]) + local.min(axis=0)
    e, n, _, _ = model["extent"]
    assert lo[0] == pytest.approx(e, abs=0.01) and lo[1] == pytest.approx(n, abs=0.01)


def test_an_ifc_of_the_site_as_it_is_keeps_its_old_wall(built, model, tmp_path, ifcopenshell):
    f = ifcopenshell.open(str(api.open_site().export(tmp_path / "site.ifc")))
    (old,) = [w for w in f.by_type("IfcWall") if w.Tag == "W1"]
    assert psets(old)["Domus"]["toe_min"] == model["summary"]["walls"][0]["toe_min"]
    assert old.Representation.Representations[0].Items[0].Closed is True                # the existing wall's a solid too
    ground = next(e for e in f.by_type("IfcGeographicElement") if e.PredefinedType == "TERRAIN")
    whole = next(layer for layer in model["layers"] if layer["id"] == "ground")
    assert len(ground.Representation.Representations[0].Items[0].Coordinates.CoordList) == len(whole["positions"]) // 3
    assert not [e for e in f.by_type("IfcElement") if (e.Name or "").startswith("A. One wall")]


def test_without_ifcopenshell_it_says_how_to_get_it(built, tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "ifcopenshell", None)
    with pytest.raises(DomusError, match=r"uv sync --extra ifc"):
        api.open_site().export(tmp_path / "site.ifc")
    assert not (tmp_path / "site.ifc").exists() and not list(tmp_path.iterdir())


def test_what_it_refuses(built, site, tmp_path):
    s = api.open_site()
    with pytest.raises(DomusError, match="no option 'z-nothing'"):
        s.export(tmp_path / "x.glb", option="z-nothing")
    with pytest.raises(DomusError, match="suffix"):
        s.export(tmp_path / "x.obj")
    with pytest.raises(DomusError, match="not 'obj'"):
        s.export(tmp_path / "x.glb", format="obj")
    assert s.export(tmp_path / "x.bin", format="glb").read_bytes()[:4] == b"glTF"
    unbuilt = tmp_path / "unbuilt"
    shutil.copytree(site, unbuilt, ignore=shutil.ignore_patterns("viewer", ".domus"))
    with pytest.raises(DomusError, match="no model yet"):
        api.open_site(unbuilt).export(tmp_path / "y.glb")


def test_the_command_writes_through_the_api(built, tmp_path, capsys):
    from domus.app import cli

    cli.main(["export", str(tmp_path / "out" / "a.glb"), "--option", "a-one-wall"])
    assert "wrote" in capsys.readouterr().out and read_glb(tmp_path / "out" / "a.glb")[0]["asset"]["extras"]["option"]
    with pytest.raises(SystemExit, match="no option"):
        cli.main(["export", str(tmp_path / "b.glb"), "--option", "nope"])


@pytest.fixture(scope="module")
def richer(tmp_path_factory):
    """The richer made-up site, built: house parts and drains at their depths, and option B with a flight of steps
    up to its lawn."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
    import made_up_sites

    out = tmp_path_factory.mktemp("export") / "sites"
    made_up_sites.write(out)
    with open(out / "richer" / "options" / "b-two-tiers.toml", "a", encoding="utf-8", newline="\n") as fh:
        fh.write('\n[[stairs]]\nid = "S1"\nname = "Steps up to the lawn"\nline_so = [[6.0, 21.0], [8.0, 21.0]]\nwidth_m = 1.0\n')
    s = api.open_site(out / "richer")
    s.build()
    return s


def test_the_richer_sites_drains_and_steps_in_a_glb(richer, tmp_path):
    model = json.loads((richer.output / "model.json").read_text(encoding="utf-8"))
    doc, binary = read_glb(richer.export(tmp_path / "richer.glb", option="b-two-tiers"))
    found = by_layer(vertices(doc, binary))
    pipes = [layer for layer in model["layers"] if layer["type"] == "pipes"]
    steps = next(layer for layer in model["layers"] if layer["id"] == "option-b-two-tiers-steps-S1")
    assert pipes and {p["id"]: found[p["id"]] for p in [*pipes, steps]} == {p["id"]: expected_vertices(p) for p in [*pipes, steps]}


def test_the_richer_sites_house_parts_drains_and_steps_in_an_ifc(richer, tmp_path, ifcopenshell):
    model = json.loads((richer.output / "model.json").read_text(encoding="utf-8"))
    pipes = [layer for layer in model["layers"] if layer["type"] == "pipes"]
    steps = next(layer for layer in model["layers"] if layer["id"] == "option-b-two-tiers-steps-S1")
    f = ifcopenshell.open(str(richer.export(tmp_path / "richer.ifc", option="b-two-tiers")))
    kinds = {e.Name: e.is_a() for e in f.by_type("IfcElement")}
    assert kinds["House: chimney"] == "IfcChimney" and kinds["House: steps"] == "IfcStair"
    assert kinds["House: walls (drawn, no photos yet)"] == "IfcWall" and kinds["House: porches"] == "IfcBuildingElementProxy"
    assert sorted(kinds[p["name"]] for p in pipes) == ["IfcPipeSegment"] * len(pipes)
    (flight,) = [s for s in f.by_type("IfcStair") if s.Tag == "S1"]
    numbers = psets(flight)["Domus"]
    assert flight.PredefinedType == "STRAIGHT_RUN_STAIR" and flight.Name == steps["name"]
    assert {k: numbers[k] for k in ("risers", "riser_m", "foot", "head")} == {k: steps["info"][k] for k in ("risers", "riser_m", "foot", "head")}


def test_leaf_cards_are_placed_as_the_viewer_places_them():
    # One card 0.5 m across at (1, 2, 3), turned a quarter about the vertical (yaw 64 of 256), standing on its foot.
    card = np.array([[100, 200, 300]], "<i2").tobytes()
    attrs = np.array([[50, 64, 0, 0, 255, 128, 0, 0, 127, 0]], np.uint8).tobytes()
    layer = {"id": "veg-x", "type": "vegetation", "atlas": "foliage.png", "atlas_grid": [4, 3], "plants": [{"name": "Shrub"}],
             "cards": {"positions": base64.b64encode(card).decode(), "attributes": base64.b64encode(attrs).decode(),
                       "runs": [[0, 5, 1, 0, 1]]}}
    (piece,) = scene.vegetation_part(layer).pieces
    v = piece.positions
    assert np.allclose(v[:, 1].min(), 2.0) and np.allclose(v[:, 1].max(), 2.5)          # its foot on the point
    assert np.allclose(v[:, 0], 1.0, atol=1e-9) and np.allclose(sorted(v[:, 2]), [2.75, 2.75, 3.25, 3.25])
    assert np.allclose(piece.colours[0], [1.0, scene.srgb_to_linear(128 / 255), 0.0])
    assert np.allclose(piece.uv[:, 0].min(), 0.25) and np.allclose(piece.uv[:, 1].max(), 1 - 1 / 3)   # sprite 5: column 1, row 1
    # Its front faces the way its normal leans (up, here), whichever way it was turned.
    a, b, c = v[piece.faces[0]]
    assert np.dot(np.cross(b - a, c - a), piece.normals[0]) >= 0


def test_the_ground_is_cut_along_the_options_edge():
    # A 4 m square of sloping ground, y = (x + z) / 4, in two triangles facing up, less a 2 by 1 m box inside it.
    v = np.array([[0.0, 0.0, 0.0], [4.0, 1.0, 0.0], [4.0, 2.0, 4.0], [0.0, 1.0, 4.0]])
    faces = np.array([[0, 2, 1], [0, 3, 2]])
    cut, f, cut_uv = scene.cut_out(v, faces, (1.0, 1.0, 3.0, 2.0), v[:, [0, 2]] / 4)
    assert plan_area(cut, f) == pytest.approx(16 - 2)
    centre = cut[f].mean(axis=1)
    assert not ((centre[:, 0] > 1) & (centre[:, 0] < 3) & (centre[:, 2] > 1) & (centre[:, 2] < 2)).any()
    assert np.allclose(cut[:, 1], (cut[:, 0] + cut[:, 2]) / 4)         # heights on the cut lie on the ground
    assert np.allclose(cut_uv, cut[:, [0, 2]] / 4)                     # and the pictures go along with them
    a, b, c = (cut[f[:, k]] for k in range(3))
    assert (np.cross(b - a, c - a)[:, 1] > 0).all()                    # every piece still faces up


def test_a_wall_manifold_wont_take_as_a_solid_isnt_marked_closed(project):
    """The IFC export marks a wall's face set Closed only where Manifold takes it as a solid (ifc.shell). A wall of
    named blocks round a sharp corner, a turn of 135 degrees, its capping course standing out, held on the inside of
    the turn, has every edge in two triangles but isn't a solid yet, so it isn't marked Closed. Round a right angle
    it is one, and it is."""
    import math
    from collections import Counter

    from domus.export import ifc
    from domus.model import terrain
    from domus.options import design

    en = design.frame(project).en(np.array([[8.0, 10.0]]))
    low = round(float(terrain.ground(project).sample(en[:, 0], en[:, 1])[0]), 2)
    for turn, solid in ((90, True), (135, False)):
        a = math.radians(turn)
        wall = {"id": "W", "material": "segmental", "unit_m": [0.457, 0.2], "cap_m": 0.1, "thickness_m": 0.27, "depth_m": 0.3,
                "cap_depth_m": 0.26, "cap_overhang_m": 0.025, "retained_side": "left", "top": low + 1.0,
                "line_so": [[10.0, 6.0], [10.0, 9.0], [10.0 + 3 * math.sin(a), 9.0 + 3 * math.cos(a)]]}
        m = design.evaluate(project, {"id": "x", "name": "x", "walls": [wall]}, segments=0)["walls"][0]["mesh"]
        _, f, closed = ifc.shell(m.vertices, m.faces)
        edges = Counter(tuple(sorted(e)) for t in f.tolist() for e in ((t[0], t[1]), (t[1], t[2]), (t[2], t[0])))
        assert set(edges.values()) == {2} and closed is solid, f"a turn of {turn} degrees"
