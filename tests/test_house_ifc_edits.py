"""Editing the house kept as an IFC file through the change route (domus.project.ifc_intents): intents on its elements
by GlobalId or Domus id, applied with IfcOpenShell, recorded with the file as it was, undone byte for byte, and
proposed for a person to accept or reject, on a made-up site."""

import contextlib
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

WHY = "Measured on site with a tape."


def quiet(line):
    pass


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def model(folder):
    return json.loads((folder / "model.json").read_text(encoding="utf-8"))


def layers(folder):
    return {x["id"]: x for x in model(folder)["layers"] if x["id"].startswith("house-")}


def positions(layer):
    return np.reshape(layer["positions"], (-1, 3))


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    """The richer made-up site with its house kept as an IFC file and drawn from it, built: the site, its folder and a
    copy of its model as built before any edit."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
    import made_up_sites

    out = tmp_path_factory.mktemp("house-ifc-edits")
    with contextlib.redirect_stdout(io.StringIO()):
        made_up_sites.write(out / "sites")
    root = out / "sites" / "richer"
    s = api.open_site(root)
    s.write_house_ifc(say=quiet)
    s.edit({"kind": "setting", "key": "house.source", "value": "ifc"}, why="The house's IFC is its master.", say=quiet)
    s.build(say=quiet)
    shutil.copytree(s.output, out / "before")
    return SimpleNamespace(site=s, root=root, before=out / "before", out=out)


@pytest.fixture
def site(built, tmp_path):
    """A copy of the built site, to edit: its edits are its own."""
    root = tmp_path / "site"
    shutil.copytree(built.root, root, ignore=shutil.ignore_patterns("viewer"))
    return api.open_site(root)


def test_an_edit_moves_the_chimney_and_the_house_layers_follow(built):
    s, ifc = built.site, built.root / "data" / "house.ifc"
    before = sha(ifc)
    record = s.edit({"kind": "ifc-move", "element": "chimney", "by": [0.5, -0.25, 0.1]}, why=WHY, who="sam", say=quiet)
    (changed,) = record["files"]
    assert changed["path"] == "data/house.ifc" and changed["before"] == before and changed["after"] == sha(ifc)
    assert (built.root / changed["copy"]).read_bytes() != ifc.read_bytes() and sha(built.root / changed["copy"]) == before
    assert record["inverse"] == [{"kind": "ifc-restore", "sha": before}] and record["rebuild"] == ['the house, where [house] source = "ifc" draws it from this file']
    s.build(say=quiet)
    a, b = layers(built.before), layers(s.output)
    assert set(a) == set(b)
    moved = positions(b["house-chimney"]) - positions(a["house-chimney"])
    assert np.allclose(moved, [0.5, 0.1, 0.25], atol=0.001)          # viewer metres: x east, y up, z south
    for k in set(a) - {"house-chimney"}:
        assert a[k] == b[k], k
    # Undone, the file is byte for byte as it was, and so is the house.
    undo = s.undo(record["id"], say=quiet)
    assert sha(ifc) == before and undo["files"][0]["after"] == before
    s.build(say=quiet)
    assert layers(s.output) == a


def test_undo_restores_the_file_byte_for_byte_and_undoing_the_undo_makes_the_edit_again(site):
    ifc = site.path / "data" / "house.ifc"
    raw = ifc.read_bytes()
    edit = site.edit({"kind": "ifc-rotate", "element": "tank", "degrees": 90}, why=WHY, say=quiet)
    after = ifc.read_bytes()
    assert after != raw
    undo = site.undo(edit["id"], say=quiet)
    assert ifc.read_bytes() == raw
    site.undo(undo["id"], say=quiet)
    assert ifc.read_bytes() == after
    assert [r["kind"] for r in site.edits()][-3:] == ["ifc-rotate", "undo", "undo"]


def test_a_stale_undo_is_refused(site):
    ifc = site.path / "data" / "house.ifc"
    first = site.edit({"kind": "ifc-move", "element": "tank", "by": [0.2, 0.0]}, why=WHY, say=quiet)
    second = site.edit({"kind": "ifc-attribute", "element": "tank", "attribute": "Name", "value": "Rain tank"}, why=WHY,
                       say=quiet)
    raw = ifc.read_bytes()
    with pytest.raises(DomusError, match=rf"has changed since edit {first['id']}.*undo the edits after it first \({second['id']}\)"):
        site.undo(first["id"], say=quiet)
    assert ifc.read_bytes() == raw
    ifc.write_bytes(raw.replace(b"'Rain tank'", b"'Rain tanks'"))       # changed outside the route
    with pytest.raises(DomusError, match="changed outside this route"):
        site.undo(second["id"], say=quiet)
    # Writing the house again is an edit on the route itself, so it's the later edit to undo first.
    ifc.write_bytes(raw)
    site.write_house_ifc(force=True, say=quiet)
    again = site.edits()[-1]
    with pytest.raises(DomusError, match=rf"undo the edits after it first \({again['id']}\)"):
        site.undo(second["id"], say=quiet)
    site.undo(again["id"], say=quiet)
    assert ifc.read_bytes() == raw


def test_a_proposal_on_the_house_accepted_and_rejected(site):
    ifc = site.path / "data" / "house.ifc"
    raw = ifc.read_bytes()
    turn = site.propose({"kind": "ifc-rotate", "element": "tank", "degrees": 45}, why="The tank sits square to the fence.",
                        source="a check", say=quiet)
    rename = site.propose({"kind": "ifc-property", "element": "chimney", "pset": "Domus", "property": "source",
                           "value": "photo"}, why="From the photo.", source="a tool", say=quiet)
    assert ifc.read_bytes() == raw and turn["against"][0]["sha"] is not None
    site.reject(rename["id"], "Not from a photo.", say=quiet)
    assert ifc.read_bytes() == raw
    record = site.accept(turn["id"], say=quiet)
    assert record["basis"] == {"proposal": turn["id"], "source": "a check"} and ifc.read_bytes() != raw
    assert {p["id"]: p["state"] for p in site.proposals()} == {turn["id"]: "accepted", rename["id"]: "rejected"}
    # One made against an element that has changed since is refused, and stays open.
    stale = site.propose({"kind": "ifc-move", "element": "tank", "by": [0.1, 0.0]}, why=WHY, source="a tool", say=quiet)
    site.edit({"kind": "ifc-rotate", "element": "tank", "degrees": 10}, why=WHY, say=quiet)
    with pytest.raises(DomusError, match="has changed since proposal"):
        site.accept(stale["id"], say=quiet)
    assert site.proposals("open")[0]["id"] == stale["id"]


def read(site):
    from domus.model import house_ifc

    return {e.ref: e for e in house_ifc.read(site._project)}


def corners(element):
    return np.vstack([p[1] for p in element.pieces])


def test_each_kind_of_intent_on_the_house(site):
    from domus.model import house_ifc

    before = read(site)
    f0 = ifcopenshell.open(str(site.path / "data" / "house.ifc"))
    tank = f0.by_type("IfcTank")[0].GlobalId
    site.edit([{"kind": "ifc-rotate", "element": tank, "degrees": 90, "about": list(corners(before["tank"])[:, :2].mean(axis=0))},
               {"kind": "ifc-attribute", "element": "chimney", "attribute": "Name", "value": "Brick chimney"},
               {"kind": "ifc-property", "element": "chimney", "pset": "Domus", "property": "source", "value": "measured"},
               {"kind": "ifc-property", "element": "deck", "pset": "Domus", "property": "source", "drop": True},
               {"kind": "ifc-delete", "element": "porch"},
               {"kind": "ifc-add", "id": "shed", "class": "IfcBuildingElementProxy", "name": "Garden shed", "layer": "outdoor",
                "colour": "timber-grey", "box": {"centre": [1500002.0, 5500026.0], "size": [2.0, 1.5, 2.1], "base": 41.0,
                                                 "degrees": 30.0}},
               {"kind": "ifc-add", "id": "pad", "class": "IfcSlab", "colour": "#7f868b",
                "extrusion": {"outline": [[1500018.0, 5500003.0], [1500020.0, 5500003.0], [1500020.0, 5500005.0]],
                              "base": 44.0, "height": 0.15}}], why=WHY, say=quiet)
    after = read(site)
    f = ifcopenshell.open(str(site.path / "data" / "house.ifc"))
    import ifcopenshell.util.element as ue

    chimney = f.by_type("IfcChimney")[0]
    assert chimney.Name == "Brick chimney" and ue.get_psets(chimney)["Domus"]["source"] == "measured"
    (deck,) = [e for e in f.by_type("IfcSlab") if e.Tag == "deck"]
    assert "source" not in ue.get_psets(deck)["Domus"]
    assert "porch" not in after and "porch-west" not in after and not [e for e in f.by_type("IfcElement") if e.Tag == "porch"]
    # The tank turned a quarter about its middle: the same corners, its plan's box the same.
    a, b = corners(before["tank"]), corners(after["tank"])
    assert np.allclose(a.min(axis=0), b.min(axis=0), atol=0.002) and np.allclose(a.max(axis=0), b.max(axis=0), atol=0.002)
    # The shed: a box 2.1 m high from 41.0, on the floor's storey, in its colour and layer, its GlobalId from its id.
    shed = after["shed"]
    v = corners(shed)
    assert v[:, 2].min() == pytest.approx(41.0) and v[:, 2].max() == pytest.approx(43.1)
    assert v[:, :2].mean(axis=0) == pytest.approx([1500002.0, 5500026.0]) and shed.layer == "outdoor"
    assert {c for c, *_ in shed.pieces} == {"#8f8a83"}
    import uuid

    assert shed.global_id == ifcopenshell.guid.compress(uuid.uuid5(house_ifc.NAMESPACE, "element:shed").hex)
    pad = after["pad"]
    assert pad.layer == "outdoor" and corners(pad)[:, 2].max() == pytest.approx(44.15)
    # A storey set higher: everything on it, 0.2 m higher.
    site.edit({"kind": "ifc-storey", "storey": "floor", "elevation": site.settings()["house"]["fl_upper"] + 0.2}, why=WHY,
              say=quiet)
    raised = read(site)
    for ref, element in after.items():
        assert np.allclose(corners(raised[ref]) - corners(element), [0, 0, 0.2], atol=1e-6), ref


@pytest.mark.parametrize(("intent", "message"), [
    ({"kind": "ifc-move", "element": "nothing", "by": [1, 0]}, "has no element 'nothing'"),
    ({"kind": "ifc-move", "element": "chimney", "by": [1]}, r"by is \[dE, dN\]"),
    ({"kind": "ifc-rotate", "element": "chimney"}, "degrees is how far"),
    ({"kind": "ifc-attribute", "element": "chimney", "attribute": "GlobalId", "value": "x"}, "attribute is one of"),
    ({"kind": "ifc-property", "element": "chimney", "pset": "Domus", "property": "ref", "value": "x"}, "keeps its Domus id"),
    ({"kind": "ifc-add", "id": "chimney", "class": "IfcChimney", "box": {"centre": [0, 0], "size": [1, 1, 1], "base": 0}},
     "chimney is there already"),
    ({"kind": "ifc-add", "id": "x", "class": "IfcWall", "box": {"centre": [0, 0], "size": [1, 0, 1], "base": 0}},
     "sizes more than 0"),
    ({"kind": "ifc-storey", "storey": "attic", "elevation": 50.0}, "has no storey 'attic'"),
])
def test_an_ifc_intent_is_checked_and_nothing_is_written(site, intent, message):
    ifc = site.path / "data" / "house.ifc"
    raw, edits = ifc.read_bytes(), len(site.edits())
    with pytest.raises(DomusError, match=message):
        site.edit(intent, why=WHY, say=quiet)
    assert ifc.read_bytes() == raw and len(site.edits()) == edits


def test_the_server_lists_an_ifc_edit_with_the_others(site):
    from domus.app import serve

    record = site.edit({"kind": "ifc-attribute", "element": "tank", "attribute": "Name", "value": "Rain tank"}, why=WHY,
                       say=quiet)
    listed = serve.changes(site.path)
    assert listed["edits"][0]["id"] == record["id"] and listed["edits"][0]["summary"] == ["data/house.ifc: tank.Name = 'Rain tank'"]
    assert listed["count"] == len(site.edits())


def test_domus_site_edit_takes_ifc_intents(site, domus, capsys, tmp_path):
    spec = tmp_path / "move.json"
    spec.write_text(json.dumps({"kind": "ifc-move", "element": "chimney", "by": [0.3, 0.0]}), encoding="utf-8")
    domus("--site", site.path, "site", "edit", spec, "--why", WHY)
    assert "dry run: nothing written" in capsys.readouterr().out
    domus("--site", site.path, "site", "edit", spec, "--why", WHY, "--apply")
    out = capsys.readouterr().out
    assert "data/house.ifc: written; the file as it was is in .domus/edits/" in out and "chimney: moved by" in out
