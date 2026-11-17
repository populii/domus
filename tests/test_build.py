"""The build on the demo site: every layer it has comes out, in the viewer's frame."""

import json

import numpy as np


def model(built):
    return json.loads((built / "model.json").read_text(encoding="utf-8"))


def test_build_writes_what_the_viewer_loads(built):
    m = model(built)
    assert m["title"] == "Demo site (made up)"
    assert next(iter(m["textures"])) == "aerial"
    for t in m["textures"].values():
        assert (built / t["file"]).exists()
    for layer in m["layers"]:
        tex = layer.get("texture")
        if tex:
            assert (built / tex).exists(), layer["id"]
    assert (built / "build.json").exists()


def test_layers_cover_the_demo(built):
    ids = {x.get("id") for x in model(built)["layers"]}
    assert {"ground", "lot", "house-walls", "house-roof", "wall-W1", "drain", "dsm", "easement-a"} <= ids


def test_origin_height_comes_from_the_ground(built):
    assert len(model(built)["origin"]) == 3
    assert model(built)["origin"][2] <= 45


def test_ground_mesh_is_near_the_origin(built):
    m = model(built)
    ground = next(x for x in m["layers"] if x["id"] == "ground")
    p = np.reshape(ground["positions"], (-1, 3))
    span = max(m["extent"][2] - m["extent"][0], m["extent"][3] - m["extent"][1])
    assert np.abs(p[:, [0, 2]]).max() <= span
    assert p[:, 1].min() >= -1 and p[:, 1].max() < 15


def test_options_are_compared(built):
    opts = model(built)["summary"]["options"]
    assert [o["id"] for o in opts] == ["a-one-wall", "b-two-tiers"]
    for o in opts:
        assert o["flat"]["gain_m2"] > 0
        assert o["walls"]
