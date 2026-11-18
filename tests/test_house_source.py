"""The house as every part of Domus reads it (domus.model.house_source): house.geojson and [house], or, with
[house] source = "ifc", the house's IFC file, which then holds the whole of the house's description; on a made-up
site."""

import contextlib
import copy
import hashlib
import io
import json
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from domus import api
from domus.errors import DomusError

ifcopenshell = pytest.importorskip("ifcopenshell", reason="the house's IFC needs the ifc extra")


def quiet(line):
    pass


def model(folder):
    return json.loads((folder / "model.json").read_text(encoding="utf-8"))


def prepare(root):
    """The richer made-up site turned as test_house_ifc turns it, with a plinth given on one face and a retaining wall
    along another, so the house has every kind of feature."""
    from test_house_ifc import turned

    turned(root)
    path = root / "data" / "site" / "house.geojson"
    fc = json.loads(path.read_text(encoding="utf-8"))
    walls = next(f for f in fc["features"] if f["properties"]["id"] == "house-walls")
    ring = walls["geometry"]["coordinates"][0]
    names = walls["properties"].get("faces") or [f"wall-{k}" for k in range(len(ring) - 1)]
    walls["properties"]["plinth_top"] = {names[0]: 42.9}
    fc["features"].append({"type": "Feature", "properties": {"id": "lower-wall", "part": "retaining-walls",
                                                             "name": "Lower wall, east", "source": "made up"},
                           "geometry": {"type": "LineString", "coordinates": [ring[1][:2], ring[2][:2]]}})
    path.write_text(json.dumps(fc, indent=1) + "\n", encoding="utf-8", newline="\n")


@pytest.fixture(scope="module")
def pair(tmp_path_factory):
    """Two copies of the site: one drawing its house (`drawn`), one with its house written to IFC and [house] source =
    "ifc" (`ifc`), both built."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
    import made_up_sites

    out = tmp_path_factory.mktemp("house-source")
    with contextlib.redirect_stdout(io.StringIO()):
        made_up_sites.write(out / "sites")
    drawn = out / "sites" / "richer"
    prepare(drawn)
    ifc = out / "ifc"
    shutil.copytree(drawn, ifc)
    a, b = api.open_site(drawn), api.open_site(ifc)
    b.write_house_ifc(say=quiet)
    b.edit({"kind": "setting", "key": "house.source", "value": "ifc"}, why="The house's IFC is its master.", say=quiet)
    built = {}
    for name, s in (("drawn", a), ("ifc", b)):
        lines = []
        s.build(say=lines.append)
        built[name] = lines
    return SimpleNamespace(drawn=a, ifc=b, out=out, said=built)


def readers(s):
    """What each part of Domus that reads the house reads of it, on site `s` (as domus.api opens it)."""
    from domus.capture import georef_sfm
    from domus.checks import model as checks
    from domus.checks import observations
    from domus.model import canopy, house_source
    from domus.options import design
    from domus.prep import overlay, textures

    p = s._project
    h = house_source.house(p)
    m = checks.Model(p)
    built = model(s.output)
    (e0, n0), (e1, n1) = (np.asarray(h.walls["geometry"]["coordinates"][0], float)[:, :2].min(axis=0) - 5,
                          np.asarray(h.walls["geometry"]["coordinates"][0], float)[:, :2].max(axis=0) + 5)
    E, N = np.meshgrid(np.arange(e0, e1, 0.5), np.arange(n0, n1, 0.5))
    return {
        "features": h.features,
        "shape": {k: h.settings.get(k) for k in house_source.SHAPE},
        "outline": design.house_outline(p),
        "building": design.house_building(p),
        "retaining": design.house_retaining_walls(p),
        "regrade": design.regrade_area(p),
        "checks": (m.ring, [(f.id, f.spec.get("plinth_top"), f.mesh.vertices.tolist()) for f in m.hm.faces], m.house_poly),
        "roof": m.hm.roof.vertices,
        "summary": {k: v for k, v in built["summary"]["house"].items() if k != "source"},
        "route": (built.get("enrich", {}).get("route"), next((x for x in built["layers"] if x["id"] == "walk-route"), None)),
        "snap": built["snap"],
        "placing": (georef_sfm.house_planes(p), georef_sfm.eaves_height(h.settings)),
        "plinths": observations.house_plinths(p),
        "canopy": canopy.house_mask(p, E, N),
        "overlay": (overlay.Scene(p).house, overlay.Scene(p).fl),
        "textures": {k: [(f.id, f.spec.get("u0")) for f in v] for k, v in textures.faces_by_group(p).items()},
        "walls": s.house_walls(),
    }


def same(a, b, tol=0.001):
    """Equal, numbers within `tol` (a millimetre), shapes by their difference's area."""
    import shapely

    if isinstance(a, shapely.Geometry) or isinstance(b, shapely.Geometry):
        if a is None or b is None:
            return a is b
        return a.symmetric_difference(b).area < tol * tol and a.geom_type == b.geom_type
    if isinstance(a, np.ndarray) or isinstance(b, np.ndarray):
        a, b = np.asarray(a), np.asarray(b)
        return a.shape == b.shape and (a.dtype == bool and bool((a == b).all()) or np.allclose(a, b, atol=tol, rtol=0))
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(same(a[k], b[k], tol) for k in a)
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        return len(a) == len(b) and all(same(x, y, tol) for x, y in zip(a, b))
    if isinstance(a, float) or isinstance(b, float):
        return isinstance(a, (int, float)) and isinstance(b, (int, float)) and abs(a - b) <= tol
    return a == b


def test_the_ifc_holds_the_whole_house(pair):
    from domus.model import house_source

    drawn = house_source.drawn(pair.ifc._project)
    f = ifcopenshell.open(str(pair.ifc.path / "data" / "house.ifc"))
    import ifcopenshell.util.element as ue

    described = [e for e in f.by_type("IfcElement") if "feature_json" in ue.get_psets(e).get("Domus", {})]
    assert len(described) == len(drawn.features) - 1                    # every feature but the walls, whose faces they are
    openings = [x for x in drawn.features if "kind" in x["properties"]]
    found = {e.Tag: e for e in [*f.by_type("IfcDoor"), *f.by_type("IfcWindow")]}
    assert sorted(found) == sorted(x["properties"]["id"] for x in openings)
    for x in openings:
        e = found[x["properties"]["id"]]
        a, b = (np.asarray(c, float)[:2] for c in x["geometry"]["coordinates"])
        assert e.Representation is None and e.OverallWidth == pytest.approx(float(np.hypot(*(b - a))))
        assert e.Decomposes[0].RelatingObject.Tag == x["properties"]["face"]      # a part of its face
    (lower,) = [e for e in f.by_type("IfcWall") if e.Tag == "lower-wall"]
    assert lower.Representation is None and lower.ObjectType == "RETAININGWALL"
    faces = [e for e in f.by_type("IfcWall") if ue.get_psets(e)["Domus"].get("edge") is not None]
    plinths = {ue.get_psets(e)["Domus"]["face"]: ue.get_psets(e)["Domus"].get("plinth_top") for e in faces}
    walls = drawn.walls["properties"]
    assert {k: v for k, v in plinths.items() if v is not None} == walls["plinth_top"]


def test_every_reader_reads_the_ifc_house_as_the_drawn_one(pair):
    from domus.model import house_source

    assert house_source.house(pair.drawn._project).source == "drawn" and house_source.house(pair.ifc._project).source == "ifc"
    a, b = readers(pair.drawn), readers(pair.ifc)
    assert a["features"] == b["features"] and a["shape"] == b["shape"]      # the description, exactly as drawn
    for k in a:
        assert same(a[k], b[k]), k
    assert not any(line.startswith("Warning:") and "house" in line for line in pair.said["ifc"])


def test_a_site_without_the_setting_reads_house_geojson_and_house_as_they_are(pair):
    from domus.geo import geojson
    from domus.model import house_source

    p = pair.drawn._project
    h = house_source.house(p)
    assert h.features == geojson.read_optional(p.path / "data" / "site" / "house.geojson")
    assert dict.items(h.settings) == dict.items(p.settings()["house"])
    h.features[0]["properties"]["id"] = "changed"                        # the caller's own copy
    assert house_source.house(p).walls is not None


@pytest.fixture
def ifc_copy(pair, tmp_path):
    """A copy of the site whose house is its IFC, to edit."""
    root = tmp_path / "site"
    shutil.copytree(pair.ifc.path, root, ignore=shutil.ignore_patterns("viewer"))
    return api.open_site(root)


def test_the_readers_follow_an_ifc_move_of_a_wall_face(ifc_copy):
    from domus.capture import georef_sfm
    from domus.checks import model as checks
    from domus.checks import observations
    from domus.model import house, house_source
    from domus.options import design

    s, p = ifc_copy, ifc_copy._project
    before = house_source.house(p)
    ring = np.asarray(before.walls["geometry"]["coordinates"][0], float)[:-1, :2]
    names = before.walls["properties"].get("faces") or [f"wall-{k}" for k in range(len(ring))]
    k = 1
    a, b = house.outward_edges(ring)[k]
    d = (b - a) / np.hypot(*(b - a))
    out = np.array([d[1], -d[0]])                        # outward: seen from outside, the face runs left to right
    outline, planes = design.house_outline(p), georef_sfm.house_planes(p)
    s.edit({"kind": "ifc-move", "element": names[k], "by": (out * 0.5).tolist()}, why="Measured.", say=quiet)
    after = house_source.house(p)
    new = np.asarray(after.walls["geometry"]["coordinates"][0], float)[:-1, :2]
    na, nb = house.outward_edges(new)[k]
    assert float((na - a) @ out) == pytest.approx(0.5, abs=1e-6) and float((nb - b) @ out) == pytest.approx(0.5, abs=1e-6)
    moved = np.flatnonzero(np.hypot(*(new - ring).T) > 1e-6)
    assert len(moved) == 2                               # the face's two corners, its neighbours lengthened to meet it
    assert [x for x in after.features if x["properties"]["id"] != "house-walls"] == \
        [x for x in before.features if x["properties"]["id"] != "house-walls"]
    # What reads the house follows it: the outline the options keep off, the walls videos are placed on, the checks.
    grown = design.house_outline(p).difference(outline).area
    assert grown == pytest.approx(0.5 * float(np.hypot(*(b - a))), rel=0.05)
    assert float(np.hypot(*(np.asarray(georef_sfm.house_planes(p)[k][0]) - planes[k][0]))) > 0.4
    m = checks.Model(p)
    assert np.allclose(m.ring, new, rtol=0, atol=1e-9)
    face = next(f for f in m.hm.faces if f.id == names[k])
    assert float((face.mesh.vertices[0, :2] - a) @ out) == pytest.approx(0.5, abs=1e-6)
    s.build(say=quiet)
    from shapely.geometry import Polygon

    area = model(s.output)["summary"]["house"]["walls_area_m2"]
    assert area == pytest.approx(Polygon(new).area, abs=0.01) and area > Polygon(ring).area + 0.4 * float(np.hypot(*(b - a)))
    # A storey raised: the floor, the plinths and the eaves' height go with it.
    plinths = observations.house_plinths(p)
    eaves = georef_sfm.eaves_height(after.settings)
    s.edit({"kind": "ifc-storey", "storey": "floor", "elevation": after.settings["fl_upper"] + 0.2}, why="Measured.",
           say=quiet)
    raised = house_source.house(p)
    assert raised.settings["fl_upper"] == pytest.approx(after.settings["fl_upper"] + 0.2)
    assert georef_sfm.eaves_height(raised.settings) == pytest.approx(eaves + 0.2)
    given = before.walls["properties"]["plinth_top"]
    assert {f: v for f, v in observations.house_plinths(p).items() if f in given} == \
        pytest.approx({f: plinths[f] + 0.2 for f in given})


def test_an_element_added_through_the_ifc_is_a_part_of_the_house(ifc_copy):
    from domus.model import house_source
    from domus.options import design

    p = ifc_copy._project
    outline = design.house_outline(p)
    x0, y0 = outline.bounds[:2]                          # clear of everything the house takes of the ground
    ifc_copy.edit({"kind": "ifc-add", "id": "shed", "class": "IfcBuildingElementProxy", "name": "Shed",
                   "box": {"centre": [x0 - 3.0, y0 - 3.0], "size": [2.0, 1.5, 2.1], "base": 41.0}}, why="Built.", say=quiet)
    shed = house_source.house(p).by_id()["shed"]
    assert shed["properties"]["part"] == "block" and shed["properties"]["top"] == pytest.approx(43.1)
    assert design.house_outline(p).difference(outline).area == pytest.approx(3.0, abs=0.01)


def test_editing_the_drawn_house_with_the_ifc_as_master_warns(ifc_copy):
    s = ifc_copy
    said = []
    s.build(say=said.append)
    assert not [x for x in said if x.startswith("Warning:") and "house" in x]
    s.edit({"kind": "props", "layer": "house", "id": "chimney", "set": {"stack_top": 49.0}}, why="Higher.", say=quiet)
    said = []
    s.build(say=said.append)
    warned = [x for x in said if x.startswith("Warning:")]
    assert any("data/site/house.geojson has changed since the house's IFC was written" in x and "domus house ifc --force" in x
               for x in warned)
    s.edit({"kind": "setting", "key": "house.roof_pitch_deg", "value": 30.0}, why="Steeper.", say=quiet)
    said = []
    s.build(say=said.append)
    assert len([x for x in said if x.startswith("Warning:") and "house's IFC was written" in x]) == 2
    s.write_house_ifc(force=True, say=quiet)                 # written again from them: nothing to warn of
    said = []
    s.build(say=said.append)
    assert not [x for x in said if x.startswith("Warning:") and "house" in x]
    record = s.edits()[-1]
    assert record["files"][0]["drawn_from"]["data/site/house.geojson"] == hashlib.sha256(
        (s.path / "data" / "site" / "house.geojson").read_bytes()).hexdigest()


def test_a_file_without_the_description_says_how_to_write_one(ifc_copy):
    from domus.model import house_ifc, house_source

    p = ifc_copy._project
    ifc = ifc_copy.path / "data" / "house.ifc"
    import ifcopenshell.api.pset

    f = ifcopenshell.open(str(ifc))
    for b in f.by_type("IfcBuilding"):
        for pset in [rel.RelatingPropertyDefinition for rel in b.IsDefinedBy]:
            ifcopenshell.api.pset.remove_pset(f, product=b, pset=pset)
    f.write(str(ifc))
    p.cache.clear()
    with pytest.raises(DomusError, match="doesn't hold the description of the house.*domus house ifc --force"):
        house_source.house(p)
    assert house_ifc.read(p, quiet)                            # it still draws


def test_a_proposal_on_the_house_from_an_observation_says_to_change_the_ifc(ifc_copy):
    from domus.checks import observations

    r = {"id": "fl", "state": "ok", "kind": "level", "target": {"setting": "house.fl_upper"}, "at": [0.0, 0.0, 43.5],
         "source": {"picks": "walk", "mark": "sill"}}
    with pytest.raises(DomusError, match="which its IFC file keeps"):
        observations.intents_for(ifc_copy._project, copy.deepcopy(r))
