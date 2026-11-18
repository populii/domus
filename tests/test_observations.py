"""Observations: readings kept as data apart from the model, each set against the element it checks by its id at every
build, and accepted into the model through the change route; on copies of the made-up demo, with made-up picks."""

import json
import shutil
import sys
import tomllib
from pathlib import Path

import numpy as np
import pytest

from domus import api
from domus.errors import DomusError
from domus.project import formats, observations, schema
from domus.project import site as sites

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import made_up_sites

X0, Y0 = 1500000.0, 5500000.0
# Made-up marks, as `domus video pick` writes a clip's solved picks: the old wall's east end 5 cm over its top, the door
# sill 6 cm over the floor, a point on the lawn, the house's north-west corner a few centimetres off, and its plinth.
MARKS = [("w1-east-top", X0 + 20.0, Y0 + 22.0, 42.12), ("sill", X0 + 10.5, Y0 + 15.0, 43.45),
         ("lawn-1", X0 + 8.0, Y0 + 25.0, 42.6), ("corner", X0 + 5.03, Y0 + 15.02, 44.0),
         ("plinth-n", X0 + 12.0, Y0 + 15.05, 42.9)]
OBSERVATIONS = [
    {"id": "w1-top", "kind": "level", "name": "W1's top, east end", "source": {"picks": "walk", "mark": "w1-east-top"},
     "target": {"layer": "walls-existing", "id": "W1", "what": "top", "vertex": 1}, "uncertainty_m": 0.03},
    {"id": "sill", "kind": "level", "name": "Door sill", "source": {"picks": "walk", "mark": "sill"},
     "target": {"setting": "house.fl_upper"}, "uncertainty_m": 0.02},
    {"id": "lawn", "kind": "level", "name": "Lawn", "source": {"picks": "walk", "mark": "lawn-1"},
     "target": {"what": "ground"}, "uncertainty_m": 0.05},
    {"id": "corner", "kind": "point", "name": "House corner", "source": {"picks": "walk", "mark": "corner"},
     "target": {"layer": "house", "id": "house-walls", "what": "position", "vertex": 0}, "uncertainty_m": 0.05},
    {"id": "plinth", "kind": "level", "name": "North plinth", "source": {"picks": "walk", "mark": "plinth-n"},
     "target": {"layer": "house", "id": "house-walls", "what": "plinth", "face": "north"}},
    {"id": "loose", "kind": "level", "name": "A step", "source": {"picks": "walk", "mark": "sill"}, "note": "drawn: 43.40"},
    {"id": "gone", "kind": "level", "name": "Old wall", "source": {"picks": "walk", "mark": "sill"},
     "target": {"layer": "walls-existing", "id": "W9", "what": "top"}},
    {"id": "unread", "kind": "level", "name": "Unread", "source": {"picks": "walk", "mark": "nothing"}}]


def write_picks(root, name, marks):
    folder = root / "data" / "video" / "picks"
    folder.mkdir(parents=True, exist_ok=True)
    rows = "".join(f"{i},{e},{n},{h},{name}-colmap,2,2,0.8,12.0,,made up\n" for i, e, n, h in marks)
    (folder / f"{name}-site.csv").write_text("id,E,N,H,model,used,marks,reproj_max_px,angle_deg,dropped,desc\n" + rows,
                                             encoding="utf-8", newline="")


@pytest.fixture
def observed(site, tmp_path):
    """A copy of the demo with a clip's made-up picks and the observations above."""
    root = tmp_path / "demo"
    shutil.copytree(site, root, ignore=shutil.ignore_patterns("viewer", ".domus"))
    write_picks(root, "walk", MARKS)
    (root / "data" / "observations.json").write_text(json.dumps({"observations": OBSERVATIONS}, indent=1), encoding="utf-8")
    return root


def quiet(line):
    pass


def by_id(s):
    return {r["id"]: r for r in s.observations()}


def test_each_reads_its_source_and_its_target_as_they_are_now(observed):
    s = api.open_site(observed)
    found = by_id(s)
    w1 = found["w1-top"]
    assert (w1["measured"], w1["expected"], w1["difference"], w1["state"]) == (42.12, 42.07, 0.05, "near")
    assert found["sill"]["expected"] == 43.39 and found["sill"]["state"] == "off"          # 6 cm, twice 2 cm and more
    assert found["lawn"]["expected"] == pytest.approx(float(s.ground()(X0 + 8.0, Y0 + 25.0)), abs=1e-6)
    assert found["corner"]["offset"] == [0.03, 0.02] and found["corner"]["state"] == "ok"
    assert found["plinth"]["state"] == "compared" and "uncertainty_m" not in found["plinth"]
    assert found["loose"]["state"] == "reading" and found["loose"]["measured"] == 43.45
    assert (found["gone"]["state"], found["gone"]["why"]) == ("lost target", "walls-existing has no W9")
    assert (found["unread"]["state"], found["unread"]["why"]) == ("no reading", "data/video/picks/walk-site.csv has no mark nothing")
    # Solved again, the clip reads the wall's top at the model's: the reading follows the picks.
    write_picks(observed, "walk", [("w1-east-top", X0 + 20.0, Y0 + 22.0, 42.07), *MARKS[1:]])
    assert by_id(s)["w1-top"]["state"] == "ok" and by_id(s)["w1-top"]["difference"] == 0.0
    # The model changed, through the route: what the observation expects follows it.
    s.edit({"kind": "setting", "key": "house.fl_upper", "value": 43.44}, "Measured at the door.", say=quiet)
    assert by_id(s)["sill"]["expected"] == 43.44 and by_id(s)["sill"]["state"] == "ok"


def test_the_build_draws_them_and_says_which_lost_their_targets(observed):
    s = api.open_site(observed)
    said = []
    s.build(say=said.append)
    assert "observation gone: lost target: walls-existing has no W9" in said
    assert "observation unread: no reading: data/video/picks/walk-site.csv has no mark nothing" in said
    model = json.loads((s.output / "model.json").read_text(encoding="utf-8"))
    layer = next(x for x in model["layers"] if x["id"] == "observations")
    assert layer["group"] == "Observations" and layer["type"] == "labels" and not layer["visible"]
    items = {it["text"].split(":")[0]: it for it in layer["items"]}
    assert items["W1's top, east end"]["text"] == "W1's top, east end: 42.12 (model 42.07, +0.05 m, ±0.03)"
    assert [items[k].get("tone") for k in ("W1's top, east end", "Door sill", "House corner", "North plinth", "Old wall")] == \
        ["near", "off", "ok", None, "lost"]
    assert items["A step"]["text"] == "A step: 43.45 (drawn: 43.40)" and "Unread" not in items
    report = {r["id"]: r for r in json.loads((s.output / "summary.json").read_text(encoding="utf-8"))["observations"]}
    assert report["w1-top"] == {"id": "w1-top", "kind": "level", "name": "W1's top, east end", "state": "near",
                                "measured": 42.12, "expected": 42.07, "difference": 0.05, "uncertainty_m": 0.03,
                                "target": OBSERVATIONS[0]["target"], "source": OBSERVATIONS[0]["source"]}
    assert len(report) == len(OBSERVATIONS)


def test_a_proposal_accepted_changes_the_model_and_its_undo_puts_it_back(observed):
    s = api.open_site(observed)
    walls = observed / "data" / "site" / "walls-existing.geojson"
    raw = walls.read_bytes()
    p = s.propose_observation("w1-top", say=quiet)
    assert p["source"] == "observation w1-top" and p["intents"] == [
        {"kind": "level", "layer": "walls-existing", "id": "W1", "vertex": 1, "z": 42.12,
         "basis": "Read off walk (w1-east-top), observation w1-top."}]
    record = s.accept(p["id"], say=quiet)
    assert s.layer("walls-existing")[0]["geometry"]["coordinates"][1][2] == 42.12
    assert by_id(s)["w1-top"]["difference"] == 0.0 and record["basis"]["source"] == "observation w1-top"
    s.undo(record["id"], say=quiet)
    assert walls.read_bytes() == raw and by_id(s)["w1-top"]["difference"] == 0.05
    # The others' targets: a setting, a ground-fix point under the reading, a vertex's place, the plinth on a face.
    for oid in ("sill", "lawn", "corner", "plinth"):
        s.accept(s.propose_observation(oid, say=quiet)["id"], say=quiet)
    after = by_id(s)
    assert s.settings()["house"]["fl_upper"] == 43.45 and after["sill"]["difference"] == 0.0
    fix = next(f for f in s.layer("ground-fixes") if f["properties"]["id"] == "observation-lawn")
    assert fix["geometry"]["coordinates"] == [X0 + 8.0, Y0 + 25.0, 42.6] and fix["properties"]["confidence"] == "within 0.05 m"
    assert after["lawn"]["difference"] == pytest.approx(0.0, abs=0.02)        # the ground drawn through the new fix
    walls_ring = next(f for f in s.layer("house") if f["properties"]["id"] == "house-walls")["geometry"]["coordinates"][0]
    assert walls_ring[0][:2] == [X0 + 5.03, Y0 + 15.02] and walls_ring[-1][:2] == [X0 + 5.03, Y0 + 15.02]
    assert after["corner"]["difference"] == 0.0
    assert next(f for f in s.layer("house") if f["properties"]["id"] == "house-walls")["properties"]["plinth_top"] == {"north": 42.9}
    assert after["plinth"]["difference"] == 0.0
    for oid, message in (("loose", "has no target to set"), ("gone", "can't be proposed: walls-existing has no W9"),
                         ("nothing", "no observation nothing")):
        with pytest.raises(DomusError, match=message):
            s.propose_observation(oid, say=quiet)


def test_a_target_renamed_or_removed_is_reported(observed):
    s = api.open_site(observed)
    s.edit({"kind": "props", "layer": "walls-existing", "id": "W1", "set": {"id": "W1a"}}, "Renamed.", say=quiet)
    assert (by_id(s)["w1-top"]["state"], by_id(s)["w1-top"]["why"]) == ("lost target", "walls-existing has no W1")
    s.edit({"kind": "delete", "layer": "walls-existing", "id": "W1a"}, "Knocked down.", say=quiet)
    said = []
    s.build(say=said.append)
    assert "observation w1-top: lost target: walls-existing has no W1" in said


def test_the_check_lists_them(observed, site):
    report = api.open_site(observed).check(["observations"])
    levels = {f["object"]: f["level"] for f in report["findings"]}
    assert levels == {"w1-top": "warn", "sill": "fail", "lawn": "fail", "corner": "info", "plinth": "info", "loose": "info",
                      "gone": "warn", "unread": "warn"}
    assert report["failures"] == {"observations": 2}
    assert api.open_site(site).check(["observations"])["skipped"] == {
        "observations": "the site has no observations (data/observations.json)"}


@pytest.mark.parametrize("o, message", [
    ({"id": "a b", "kind": "level", "source": {"picks": "walk", "mark": "m"}}, "has an id"),
    ({"id": "a", "kind": "angle", "source": {"picks": "walk", "mark": "m"}}, "kind is one of"),
    ({"id": "a", "kind": "level", "source": {"picks": "walk"}}, "a mark in a clip's picks"),
    ({"id": "a", "kind": "level", "source": {"picks": "walk", "mark": "m"}, "target": {"what": "position"}}, "one of top, level"),
    ({"id": "a", "kind": "level", "source": {"picks": "walk", "mark": "m"}, "target": {"setting": "house.colour"}}, "isn't a setting"),
    ({"id": "a", "kind": "level", "source": {"picks": "walk", "mark": "m"}, "target": {"layer": "house", "what": "top"}},
     "names a layer and an element"),
    ({"id": "a", "kind": "level", "source": {"picks": "walk", "mark": "m"}, "uncertainty_m": -1}, "how sure"),
    ({"id": "a", "kind": "placement", "source": {"clouds": ["walk-site"], "turn_deg": 2.0}}, "gives the pivot"),
    ({"id": "a", "kind": "placement", "source": {"clouds": ["walk-site"]}, "target": {"what": "ground"}}, "has no target"),
])
def test_an_observation_says_what_is_wrong_with_it(o, message):
    with pytest.raises(DomusError, match=message):
        observations.checked(o, "data/observations.json")


@pytest.fixture(scope="module")
def made_up(tmp_path_factory):
    out = tmp_path_factory.mktemp("observed") / "sites"
    made_up_sites.write(out)
    return out


def test_a_format_4_sites_readings_and_comparisons_are_observations(made_up, tmp_path, capsys):
    """[[video.question_marks]] and [[video.compare]] read as observations in memory, and upgraded into
    data/observations.json, which then reads the same; a mark read off two clips gives each its picks' name."""
    four = made_up / "format4"
    raw = tomllib.loads((four / "data" / "site.toml").read_text(encoding="utf-8"))
    assert formats.format_of(raw) == 4 and len(raw["video"]["question_marks"]) == 3 and len(raw["video"]["compare"]) == 2
    assert "video" not in formats.site(raw) and schema.unknown(formats.site(raw), schema.SITE) == []
    s = api.open_site(four)
    read = observations.read(sites.Site(four))
    assert [(o["id"], o["kind"]) for o in read] == [("sill-walk", "level"), ("sill-yard", "level"), ("w1-east", "level"),
                                                    ("walk-up", "placement"), ("walk-turned", "placement")]
    assert read[0] == {"id": "sill-walk", "kind": "level", "name": "Door sill, the walk (made up)",
                       "source": {"picks": "walk", "mark": "sill"}, "target": None, "note": "drawn: upper floor 43.39",
                       "lift_m": 0.2}
    assert read[3]["source"] == {"clouds": ["walk-colmap-site"], "pivot": [X0 + 11, Y0 + 22], "dz_m": 0.3}
    assert [r["measured"] for r in s.observations()[:3]] == [43.452, 43.41, 42.118]
    copy = tmp_path / "four"
    shutil.copytree(four, copy, ignore=shutil.ignore_patterns("viewer", ".domus"))
    s.build(say=quiet)
    before = json.loads((s.output / "model.json").read_text(encoding="utf-8"))
    upgraded = api.open_site(copy)
    upgraded.upgrade(say=quiet)
    text = (copy / "data" / "site.toml").read_text(encoding="utf-8")
    assert formats.format_of(tomllib.loads(text)) == formats.FORMAT and "video" not in tomllib.loads(text)
    assert json.loads((copy / "data" / "observations.json").read_text(encoding="utf-8")) == {"observations": read}
    assert observations.read(sites.Site(copy)) == read
    upgraded.build(say=quiet)
    after = json.loads((upgraded.output / "model.json").read_text(encoding="utf-8"))
    group = lambda m: [x for x in m["layers"] if x.get("group") == "Observations"]
    assert group(after) == group(before) and [x["id"] for x in group(after)] == ["observation-walk-up", "observations"]
    assert after["summary"]["observations"] == before["summary"]["observations"]


def test_the_export_writes_each_placements_cloud(observed):
    """domus video export moves a placement's clouds as it says, into data/video/compare.npz."""
    s = api.open_site(observed)
    video = observed / "data" / "video"
    xyz = np.array([[X0 + 8 + 0.1 * k, Y0 + 24 + 0.05 * k, 42.0] for k in range(50)])
    np.savez_compressed(video / "lawn-scan.npz", xyz=xyz, rgb=np.full((50, 3), 120, np.uint8), nview=np.full(50, 3, np.uint8))
    data = json.loads((observed / "data" / "observations.json").read_text(encoding="utf-8"))
    data["observations"].append({"id": "lawn-up", "kind": "placement", "name": "The lawn's scan, 0.25 m up",
                                 "source": {"clouds": ["lawn-scan"], "dz_m": 0.25}})
    (observed / "data" / "observations.json").write_text(json.dumps(data), encoding="utf-8")
    s.video.export(say=quiet)
    cp = np.load(video / "compare.npz")
    assert [str(i) for i in cp["ids"]] == ["lawn-up"]
    heights = cp["origin"][2] + cp["mm"][:, 2] / 1000.0
    assert len(heights) == 50 and np.allclose(heights, 42.25, atol=0.002)


def test_the_commands_check_and_propose_them(observed, domus, capsys):
    assert domus("--site", observed, "prep", "check", "observations") == 1     # two are off by more than twice theirs
    out = capsys.readouterr().out
    assert "observations fail      sill" in out and "observations warn      gone" in out
    domus("--site", observed, "site", "propose", "w1-top", "corner")
    out = capsys.readouterr().out.splitlines()
    assert out[0].startswith("Proposed as 0001 (.domus/proposals/0001.json): walls-existing: W1's vertex 1 at 42.12.")
    assert out[1].startswith("Proposed as 0002 (.domus/proposals/0002.json): house: house-walls's vertex 0 set.")
    with pytest.raises(SystemExit, match="has no target to set"):
        domus("--site", observed, "site", "propose", "loose")


def test_a_walls_toe_is_read_as_the_build_takes_it_and_set_as_its_own(observed):
    """An existing wall's toe: the ground in front of its face until it says its own (`toe_m`), which accepting an
    observation of it gives it."""
    from domus.model import terrain, walls

    s = api.open_site(observed)
    w1 = s.layer("walls-existing")[0]
    toe = terrain.wall_levels(walls.from_feature(w1), site=sites.Site(observed))[3]
    write_picks(observed, "walk", [*MARKS, ("w1-west-toe", X0 + 2.0, Y0 + 21.6, round(float(toe[0]) - 0.04, 3))])
    data = json.loads((observed / "data" / "observations.json").read_text(encoding="utf-8"))
    data["observations"].append({"id": "w1-toe", "kind": "level", "name": "W1's toe, west end", "uncertainty_m": 0.02,
                                 "source": {"picks": "walk", "mark": "w1-west-toe"},
                                 "target": {"layer": "walls-existing", "id": "W1", "what": "toe", "vertex": 0}})
    (observed / "data" / "observations.json").write_text(json.dumps(data), encoding="utf-8")
    r = by_id(s)["w1-toe"]
    assert r["expected"] == pytest.approx(float(toe[0])) and r["difference"] == -0.04 and r["state"] == "near"
    p = s.propose_observation("w1-toe", say=quiet)
    toes = p["intents"][0]["set"]["toe_m"]
    assert p["intents"][0]["kind"] == "props" and len(toes) == 2 and toes[0] == round(float(toe[0]) - 0.04, 3)
    s.accept(p["id"], say=quiet)
    assert s.layer("walls-existing")[0]["properties"]["toe_m"] == toes and by_id(s)["w1-toe"]["difference"] == 0.0
