"""One route for every change to a site's data (domus.project.changes): intents applied, recorded and undone, and
proposals a person accepts or rejects, on copies of the made-up demo."""

import hashlib
import json
import shutil

import numpy as np
import pytest

from domus import api
from domus.errors import DomusError
from domus.project import changes, formats, intents

MOVE_W1 = {"kind": "move", "layer": "walls-existing", "ids": ["W1"], "by": [0.5, -0.25]}


@pytest.fixture
def demo(site, tmp_path):
    """A copy of the demo, to change: its folder."""
    root = tmp_path / "demo"
    shutil.copytree(site, root, ignore=shutil.ignore_patterns("viewer", ".domus"))
    return root


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def quiet(line):
    pass


def test_an_edit_is_recorded_and_undone_byte_for_byte(demo):
    s = api.open_site(demo)
    walls = demo / "data" / "site" / "walls-existing.geojson"
    raw = walls.read_bytes()
    before = json.loads(raw)["features"][0]["geometry"]["coordinates"]
    said = []
    record = s.edit(MOVE_W1, "Measured on site with a tape.", "sam", say=said.append)
    moved = json.loads(walls.read_bytes())["features"][0]["geometry"]["coordinates"]
    assert [[round(a - b, 3) for a, b in zip(p, q, strict=True)][:2] for p, q in zip(moved, before, strict=True)] == \
        [[0.5, -0.25]] * len(before)
    # The record: who, when in UTC, why, the intent, each file's SHA-256 before and after, its copy, and its inverse.
    path = demo / ".domus" / "edits" / "0001-move.json"
    assert json.loads(path.read_text(encoding="utf-8")) == record
    assert record["id"] == "0001" and record["who"] == "sam" and record["why"] == "Measured on site with a tape."
    assert record["when"].endswith("Z") and record["intents"] == [MOVE_W1]
    assert record["files"] == [{"path": "data/site/walls-existing.geojson", "before": hashlib.sha256(raw).hexdigest(),
                                "after": sha(walls), "copy": ".domus/edits/0001-move/data/site/walls-existing.geojson"}]
    assert (demo / record["files"][0]["copy"]).read_bytes() == raw
    assert [i["kind"] for i in record["inverse"]] == ["put"] and record["rebuild"][0].startswith("the existing walls")
    assert said[-2] == "Recorded as edit 0001 (.domus/edits/0001-move.json): domus site undo 1 undoes it."
    assert said[-1].startswith("Build the model again to see it (domus build)")
    # Undone, the file is byte for byte as it was, and the undo is an edit of its own, which can be undone in turn.
    undone = s.undo(1, who="sam")
    assert walls.read_bytes() == raw and undone["undoes"] == "0001" and undone["kind"] == "undo"
    assert undone["files"][0]["after"] == record["files"][0]["before"]
    assert [(r["id"], r["in_effect"], r.get("undone_by")) for r in s.edits()] == [("0001", False, "0002"), ("0002", True, None)]
    with pytest.raises(DomusError, match="undone already, by edit 0002"):
        s.undo(1)
    s.undo(2)
    assert sha(walls) == record["files"][0]["after"]
    assert [r["in_effect"] for r in s.edits()] == [True, False, True]


def test_a_stale_undo_is_refused(demo):
    s = api.open_site(demo)
    walls = demo / "data" / "site" / "walls-existing.geojson"
    raw = walls.read_bytes()
    s.edit(MOVE_W1, "First.", say=quiet)
    s.edit({"kind": "props", "layer": "walls-existing", "id": "W1", "set": {"confidence": "measured"}}, "Second.", say=quiet)
    with pytest.raises(DomusError, match="has changed since edit 0001.*undo the edits after it first \\(0002\\)"):
        s.undo(1, say=quiet)
    # A change made by hand, outside the route, stops an undo too.
    text = walls.read_text(encoding="utf-8")
    walls.write_text(text.replace("measured", "by hand"), encoding="utf-8", newline="")
    with pytest.raises(DomusError, match="changed outside this route"):
        s.undo(2, say=quiet)
    walls.write_text(text, encoding="utf-8", newline="")
    s.undo(2, say=quiet)
    s.undo(1, say=quiet)
    assert walls.read_bytes() == raw


def test_a_proposal_accepted_changes_the_data_and_one_rejected_doesnt(demo):
    s = api.open_site(demo)
    fixes = demo / "data" / "site" / "ground-fixes.geojson"
    point = {"type": "Feature", "properties": {"id": "fix-1", "source": "made up"},
             "geometry": {"type": "Point", "coordinates": [1500010.0, 5500020.0, 41.5]}}
    p = s.propose({"kind": "add", "layer": "ground-fixes", "feature": point}, "A level read off a clip.", "a check", say=quiet)
    assert p["id"] == "0001" and p["state"] == "open" and not fixes.exists()
    assert json.loads((demo / ".domus" / "proposals" / "0001.json").read_text(encoding="utf-8")) == p
    assert [q["id"] for q in s.proposals("open")] == ["0001"]
    record = s.accept(1, who="sam", say=quiet)
    assert record["basis"] == {"proposal": "0001", "source": "a check"} and record["why"] == "A level read off a clip."
    assert [f["properties"]["id"] for f in s.layer("ground-fixes")] == ["fix-1"]
    assert s.proposals()[0]["state"] == "accepted" and s.proposals()[0]["accepted"]["edit"] == record["id"]
    with pytest.raises(DomusError, match="accepted already, by sam"):
        s.accept(1)
    # Its undo leaves the site as it found it: the layer the edit started is gone.
    s.undo(record["id"], say=quiet)
    assert not fixes.exists()
    trees = demo / "data" / "site" / "trees.geojson"
    raw = trees.read_bytes()
    tree = s.layer("trees")[0]["properties"]["id"]
    q = s.propose({"kind": "delete", "layer": "trees", "id": tree}, "It looks cut down.", "a tool", say=quiet)
    with pytest.raises(DomusError, match="Say why"):
        s.reject(q["id"], " ")
    rejected = s.reject(q["id"], "It's still there.", "sam", say=quiet)
    assert rejected["state"] == "rejected" and rejected["rejected"]["why"] == "It's still there."
    assert trees.read_bytes() == raw and s.proposals("open") == []
    with pytest.raises(DomusError, match="rejected already"):
        s.accept(q["id"])
    # One that can't be applied is refused when it's proposed.
    with pytest.raises(DomusError, match="no feature nothing-here"):
        s.propose({"kind": "delete", "layer": "trees", "id": "nothing-here"}, "Gone.", "a tool")


@pytest.mark.parametrize("intent, message", [
    ({"kind": "paint"}, "isn't a kind of change"),
    ({"kind": "move", "layer": "../site", "ids": ["W1"], "by": [1, 0]}, "isn't a layer's name"),
    ({"kind": "move", "layer": "walls-existing", "ids": ["W9"], "by": [1, 0]}, "no feature \\['W9'\\]"),
    ({"kind": "move", "layer": "walls-existing", "ids": ["W1"], "by": [1, float("nan")]}, "by is"),
    ({"kind": "level", "layer": "walls-existing", "id": "W1", "vertex": 9, "z": 42.0}, "not 9"),
    ({"kind": "level", "layer": "walls-existing", "id": "W1", "z": [42.0]}, "heights were given"),
    ({"kind": "coords", "layer": "walls-existing", "id": "W1", "en": [1500000.0, 5500000.0]}, "don't make one"),
    ({"kind": "props", "layer": "walls-existing", "id": "W1", "drop": ["id"]}, "keeps an id"),
    ({"kind": "add", "layer": "walls-existing", "feature": {"type": "Feature", "properties": {"id": "W1"},
                                                          "geometry": {"type": "Point", "coordinates": [1, 2]}}}, "already there"),
    ({"kind": "delete", "layer": "no-such-layer", "id": "x"}, "There's no"),
    ({"kind": "setting", "key": "house.colour_of_the_door", "value": "red"}, "isn't a setting Domus knows"),
    ({"kind": "setting", "key": "house.fl_upper", "value": "high"}, "is a number, not a str"),
    ({"kind": "setting", "key": "house", "value": 1}, "is a table of settings"),
    ({"kind": "layer", "layer": "trees", "collection": {"type": "FeatureCollection", "features": [{"type": "Feature"}]}},
     "collection is a GeoJSON FeatureCollection"),
    ({"kind": "option", "id": "_site", "toml": "x = 1"}, "id names the option"),
    ({"kind": "option", "id": "a-one-wall", "toml": "name = "}, "its toml isn't TOML"),
    ({"kind": "option", "id": "no-such-option", "drop": True}, "There's no"),
])
def test_an_intent_is_checked_against_the_site_and_nothing_is_written(demo, intent, message):
    s = api.open_site(demo)
    with pytest.raises(DomusError, match=message):
        s.edit(intent, "Testing.", say=quiet)
    assert not (demo / ".domus" / "edits").exists() or not list((demo / ".domus" / "edits").glob("*.json"))


def test_an_edit_needs_a_reason_and_a_dry_run_writes_nothing(demo):
    s = api.open_site(demo)
    with pytest.raises(DomusError, match="Say why"):
        s.edit(MOVE_W1)
    walls = demo / "data" / "site" / "walls-existing.geojson"
    raw = walls.read_bytes()
    said = []
    would = s.edit(MOVE_W1, "Trying.", dry_run=True, say=said.append)
    assert said[-1] == "dry run: nothing written" and walls.read_bytes() == raw and "id" not in would
    assert s.edits() == []


def test_an_edit_in_the_frame_and_one_feature_changed_twice_comes_back_once(demo):
    s = api.open_site(demo)
    house = demo / "data" / "site" / "house.geojson"
    raw = house.read_bytes()
    window = next(f for f in s.layer("house") if f["properties"].get("kind") == "window")
    fid = window["properties"]["id"]
    so = s.frame().so([c[:2] for c in window["geometry"]["coordinates"]])
    record = s.edit([{"kind": "coords", "layer": "house", "id": fid, "so": [[a, b + 0.1] for a, b in so.tolist()]},
                     {"kind": "props", "layer": "house", "id": fid, "set": {"basis": "Measured."}}], "Moved in.", say=quiet)
    moved = next(f for f in s.layer("house") if f["properties"]["id"] == fid)
    assert np.allclose(s.frame().so([c[:2] for c in moved["geometry"]["coordinates"]]), so + [0.0, 0.1], atol=0.002)
    assert record["kind"] == "edit" and [i["kind"] for i in record["inverse"]] == ["put"]
    s.undo(record["id"], say=quiet)
    assert house.read_bytes() == raw


def test_a_setting_keeps_its_comment_and_comes_back_byte_for_byte(demo):
    s = api.open_site(demo)
    toml = demo / "data" / "site.toml"
    text = toml.read_text(encoding="utf-8").replace("[house]\n", "[house]\nfl_lower = 41.10          # from the plans\n", 1)
    toml.write_text(text, encoding="utf-8", newline="\n")
    record = s.edit({"kind": "setting", "key": "house.fl_lower", "value": 41.2}, "Measured at the door.", say=quiet)
    assert "fl_lower = 41.2          # from the plans\n" in toml.read_text(encoding="utf-8")
    assert record["inverse"] == [{"kind": "setting", "file": "data/site.toml", "key": "house.fl_lower", "value": 41.1,
                                  "toml": "41.10"}]
    assert s.settings()["house"]["fl_lower"] == 41.2
    s.undo(record["id"], say=quiet)
    assert toml.read_text(encoding="utf-8") == text
    added = s.edit({"kind": "setting", "key": "house.roof_ridge_offset_m", "value": 0.4}, "Seen from the street.", say=quiet)
    s.undo(added["id"], say=quiet)
    assert toml.read_text(encoding="utf-8") == text


def test_a_site_in_an_older_format_changes_its_layers_not_its_settings(demo):
    s = api.open_site(demo)
    toml = demo / "data" / "site.toml"
    older = toml.read_text(encoding="utf-8").replace(f"format = {formats.FORMAT}", f"format = {formats.FORMAT - 1}")
    toml.write_text(older, encoding="utf-8", newline="\n")
    with pytest.raises(DomusError, match="domus site upgrade writes it up to date first"):
        s.edit({"kind": "setting", "key": "house.fl_upper", "value": 45.0}, "From the plans.", say=quiet)
    assert s.edit(MOVE_W1, "Measured.", say=quiet)["id"] == "0001"


@pytest.mark.parametrize("text", [
    '{"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {"id": "a"}, "geometry": {"type": "Point", "coordinates": [1.0, 2.0]}}]}',
    '{"type":"FeatureCollection","features":[{"type":"Feature","properties":{"id":"a"},"geometry":{"type":"Point","coordinates":[1.0,2.0]}}]}\n',
    json.dumps({"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {"id": "a", "name": "Māra"},
                "geometry": {"type": "Point", "coordinates": [1.0, 2.0]}}]}, indent=2, ensure_ascii=False),
    json.dumps({"type": "FeatureCollection", "features": []}, indent="\t") + "\n",
])
def test_a_file_is_written_again_in_its_own_layout(text):
    assert changes.dump(json.loads(text), changes.layout(text)) == text


def test_the_older_commands_are_recorded_too(demo, tmp_path):
    s = api.open_site(demo)
    walls = demo / "data" / "site" / "walls-existing.geojson"
    raw = walls.read_bytes()
    said = []
    spec = {"shift": [0.5, -0.25], "basis": "Moved for the test.", "files": {"walls-existing.geojson": ["W1"]}}
    s.shift(spec, apply=True, say=said.append)
    assert said[1] == "walls-existing.geojson: written (1 features moved); backup " \
                      ".domus/edits/0001-move/data/site/walls-existing.geojson"
    record = s.edits()[-1]
    assert record["why"] == "Moved for the test." and record["intents"][0]["basis"] == "Moved for the test."
    s.undo(1, say=quiet)
    assert walls.read_bytes() == raw
    # The older form of edit, by position, with a folder for the copies as before.
    s.edit({"walls-existing.geojson": [{"id": "W1", "props": {"confidence": "checked"}}]}, tmp_path / "b", True, say=quiet)
    assert (tmp_path / "b" / "walls-existing.geojson").read_bytes() == raw
    assert s.edits()[-1]["files"][0]["copy"] == str((tmp_path / "b" / "walls-existing.geojson").resolve())
    assert s.edits()[-1]["why"] == "domus site edit, with no reason given"


def test_the_commands_list_undo_accept_and_reject(demo, domus, capsys):
    spec = demo.parent / "move.json"
    spec.write_text(json.dumps(MOVE_W1), encoding="utf-8")
    with pytest.raises(SystemExit, match="Say why"):
        domus("--site", demo, "site", "edit", spec, "--apply")
    domus("--site", demo, "site", "edit", spec, "--apply", "--why", "Measured.", "--who", "sam")
    capsys.readouterr()
    domus("--site", demo, "site", "edits")
    out = capsys.readouterr().out.splitlines()
    assert out[0].startswith("0001  ") and out[0].endswith("  sam  Measured.")
    assert out[1] == "      walls-existing: W1 moved by (+0.500, -0.250) m"
    domus("--site", demo, "site", "undo", "1", "--who", "sam")
    capsys.readouterr()
    domus("--site", demo, "site", "edits")
    assert "[undone by 0002]" in capsys.readouterr().out
    s = api.open_site(demo)
    s.propose({"kind": "props", "layer": "walls-existing", "id": "W1", "set": {"confidence": "measured"}}, "Measured.",
              "a check", say=quiet)
    s.propose({"kind": "props", "layer": "walls-existing", "id": "W1", "set": {"confidence": "guessed"}}, "Guessed.",
              "a tool", say=quiet)
    domus("--site", demo, "site", "proposals")
    out = capsys.readouterr().out.splitlines()
    assert out[0] == "0001  open  a check: Measured." and out[2] == "0002  open  a tool: Guessed."
    domus("--site", demo, "site", "accept", "1", "--who", "sam")
    domus("--site", demo, "site", "reject", "2", "--why", "Not a guess.", "--who", "sam")
    capsys.readouterr()
    domus("--site", demo, "site", "proposals", "--all")
    out = capsys.readouterr().out.splitlines()
    assert out[0] == "0001  accepted by sam, edit 0003  a check: Measured."
    assert out[2] == "0002  rejected by sam: Not a guess.  a tool: Guessed."
    assert s.layer("walls-existing")[0]["properties"]["confidence"] == "measured"


def test_one_change_at_a_time(demo, monkeypatch):
    s = api.open_site(demo)
    lock = demo / ".domus" / "edits" / ".lock"
    lock.parent.mkdir(parents=True)
    lock.write_text("1\n", encoding="utf-8")
    monkeypatch.setattr(changes, "LOCK_WAIT_S", 0.2)
    with pytest.raises(DomusError, match="Another change to this site is being made"):
        s.edit(MOVE_W1, "Waiting.", say=quiet)
    monkeypatch.setattr(changes, "LOCK_STALE_S", 0.0)       # one a change left behind when it stopped part way
    assert s.edit(MOVE_W1, "Not waiting.", say=quiet)["id"] == "0001" and not lock.exists()


def test_each_intent_says_what_it_does():
    assert intents.describe(MOVE_W1) == "walls-existing: W1 moved by (+0.500, -0.250) m"
    assert intents.describe({"kind": "setting", "file": "data/site.toml", "key": "house.fl_upper", "value": 45.0}) == \
        "data/site.toml: house.fl_upper = 45.0"
    assert intents.describe({"kind": "level", "layer": "ground-fixes", "id": "p", "vertex": 0, "z": 42.1}) == \
        "ground-fixes: p's vertex 0 at 42.1"
    assert intents.describe({"kind": "layer", "layer": "features-pegs", "collection": PEGS}) == \
        "features-pegs: written whole, 1 feature"
    assert intents.describe({"kind": "option", "id": "sketch-a", "toml": "x = 1"}) == "options/sketch-a.toml: written whole"
    assert intents.describe({"kind": "option", "id": "sketch-a", "drop": True}) == "options/sketch-a.toml: taken away"


def test_a_proposal_is_accepted_only_while_what_it_rests_on_holds(demo):
    s = api.open_site(demo)
    top = {"kind": "level", "layer": "walls-existing", "id": "W1", "vertex": 1, "z": 42.2}
    p = s.propose(top, "The clip reads it higher.", "a check", say=quiet)
    assert p["against"][0]["file"] == "data/site/walls-existing.geojson" and p["against"][0]["id"] == "W1"
    other = s.propose({"kind": "props", "layer": "trees", "id": "tree-1", "set": {"note": "leaning"}}, "Seen.", "a tool",
                      say=quiet)
    s.edit({"kind": "props", "layer": "walls-existing", "id": "W1", "set": {"note": "measured since"}}, "Measured.",
           say=quiet)
    with pytest.raises(DomusError, match="W1 has changed since proposal 0001 was made"):
        s.accept(p["id"], say=quiet)
    assert s.proposals("open")[0]["id"] == "0001"            # it stays open, for a person to reject
    assert s.accept(other["id"], say=quiet)["basis"]["proposal"] == "0002"


def test_one_vertex_is_set_where_it_is_and_comes_back(demo):
    s = api.open_site(demo)
    walls = demo / "data" / "site" / "walls-existing.geojson"
    raw = walls.read_bytes()
    record = s.edit({"kind": "coords", "layer": "walls-existing", "id": "W1", "vertex": 1, "en": [1500020.5, 5500022.0]},
                    "The east end, measured.", say=quiet)
    coords = s.layer("walls-existing")[0]["geometry"]["coordinates"]
    assert coords[1] == [1500020.5, 5500022.0, 42.07] and coords[0] == [1500002.0, 5500022.0, 42.0]
    assert record["summary"] == ["walls-existing: W1's vertex 1 set"]
    s.undo(record["id"], say=quiet)
    assert walls.read_bytes() == raw
    with pytest.raises(DomusError, match="not 2"):
        s.edit({"kind": "coords", "layer": "walls-existing", "id": "W1", "vertex": 2, "en": [1.0, 2.0]}, "No.", say=quiet)


PEGS = {"type": "FeatureCollection", "name": "features-pegs",
        "features": [{"type": "Feature", "properties": {"id": "peg-1", "kind": "mark", "name": "Peg 1 (made up)"},
                      "geometry": {"type": "Point", "coordinates": [1500010.0, 5500020.0]}}]}


def test_a_layer_is_written_whole_as_one_intent_and_undone(demo):
    """A whole layer written as one intent, as domus marks and domus title write theirs: undone, a layer it started is
    taken away, and one it wrote over comes back byte for byte."""
    s = api.open_site(demo)
    pegs = demo / "data" / "site" / "features-pegs.geojson"
    said = []
    record = s.edit({"kind": "layer", "layer": "features-pegs", "collection": PEGS}, "Pegs found on site.", "sam",
                    say=said.append)
    assert json.loads(pegs.read_text(encoding="utf-8")) == PEGS and pegs.read_bytes().endswith(b"]\n}\n")
    assert record["kind"] == "layer" and record["summary"] == ["features-pegs: written whole, 1 feature"]
    assert record["files"] == [{"path": "data/site/features-pegs.geojson", "before": None, "after": sha(pegs), "copy": None}]
    assert [i["kind"] for i in record["inverse"]] == ["layer"] and record["inverse"][0]["collection"]["features"] == []
    assert said[-2] == "Recorded as edit 0001 (.domus/edits/0001-layer.json): domus site undo 1 undoes it."
    trees = demo / "data" / "site" / "trees.geojson"
    raw = trees.read_bytes()
    first = {**json.loads(raw), "features": json.loads(raw)["features"][:1]}
    s.edit({"kind": "layer", "layer": "trees", "collection": first}, "Only the first tree.", say=quiet)
    assert json.loads(trees.read_text(encoding="utf-8")) == first
    assert s.edit({"kind": "layer", "layer": "trees", "collection": first}, "Again.", say=quiet) is None    # nothing to change
    s.undo(2, say=quiet)
    assert trees.read_bytes() == raw
    s.undo(1, say=quiet)
    assert not pegs.exists()
    p = s.propose({"kind": "layer", "layer": "features-pegs", "collection": PEGS}, "LINZ has a peg here.", "a tool", say=quiet)
    assert p["against"] == [{"file": "data/site/features-pegs.geojson", "whole": True, "sha": None}]
    s.edit({"kind": "layer", "layer": "features-pegs", "collection": {**PEGS, "features": []}}, "None found.", say=quiet)
    with pytest.raises(DomusError, match="^data/site/features-pegs.geojson has changed since proposal 0001 was made"):
        s.accept(p["id"], say=quiet)


def test_an_option_file_is_added_written_over_and_taken_away_as_edits(demo):
    """An option's file, options/<id>.toml, written whole as one intent, as the viewer's Make it an option adds one:
    undone, an option it added is taken away, and one it wrote over or took away comes back byte for byte."""
    s = api.open_site(demo)
    toml = 'name = "Sketched (made up)"\n\n[[walls]]\nid = "S1"\nline_so = [[2.0, 3.0], [6.0, 3.0]]\n'
    option = demo / "options" / "sketch-a.toml"
    said = []
    record = s.edit({"kind": "option", "id": "sketch-a", "toml": toml}, "Sketched in the viewer.", "sam", say=said.append)
    assert option.read_bytes() == toml.encode("utf-8") and record["kind"] == "option"
    assert record["files"] == [{"path": "options/sketch-a.toml", "before": None, "after": sha(option), "copy": None}]
    assert record["inverse"] == [{"kind": "option", "id": "sketch-a", "drop": True}]
    assert record["rebuild"] == ["the wall options"] and "sketch-a" in [o["id"] for o in s.options()]
    assert said[-2] == "Recorded as edit 0001 (.domus/edits/0001-option.json): domus site undo 1 undoes it."
    a = demo / "options" / "a-one-wall.toml"
    raw = a.read_bytes()
    said.clear()
    s.edit({"kind": "option", "id": "a-one-wall", "drop": True}, "Not this one.", say=said.append)
    kept = ".domus/edits/0002-option/options/a-one-wall.toml"
    assert not a.exists() and said[:2] == ["   options/a-one-wall.toml: taken away",
                                           f"options/a-one-wall.toml: taken away; the file as it was is in {kept}"]
    s.undo(2, say=quiet)
    assert a.read_bytes() == raw
    s.edit({"kind": "option", "id": "a-one-wall", "toml": toml}, "Over it.", say=quiet)
    s.undo(4, say=quiet)
    assert a.read_bytes() == raw
    s.undo(1, say=quiet)
    assert not option.exists() and [o["id"] for o in s.options()] == ["a-one-wall", "b-two-tiers"]
    a.write_text("name = ", encoding="utf-8")       # a file that isn't TOML is put right by hand first
    with pytest.raises(DomusError, match="a-one-wall.toml isn't TOML"):
        s.edit({"kind": "option", "id": "a-one-wall", "toml": toml}, "Over it.", say=quiet)
