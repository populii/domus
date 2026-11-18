"""The build on the demo site: every layer it has comes out, in the viewer's frame."""

import json

import numpy as np

from domus import __version__
from domus.build import MODEL_FORMAT


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
    made = json.loads((built / "build.json").read_text(encoding="utf-8"))
    assert made["domus"] == __version__ and made["model_format"] == MODEL_FORMAT and made["title"] == m["title"]


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


def test_the_model_says_its_format(built):
    assert model(built)["format"] == MODEL_FORMAT


def test_the_model_carries_what_a_sketch_is_worked_out_with(built, project):
    """`regrade`, for the viewer to work a sketch out while it's drawn: the regrade area, each existing wall's face,
    back and toe (the soil's level under it), the options' defaults and their rules' lengths, in viewer metres. The
    ground it's worked out on is the ground layer."""
    import pytest
    from shapely.geometry import Polygon

    from domus.model import terrain
    from domus.options import design, scheme, sketch

    m = model(built)
    regrade = m["regrade"]
    e0, n0, h0 = m["origin"]
    outer, *holes = (Polygon([(e0 + x, n0 - z) for x, z in ring]) for ring in regrade["area"])
    assert outer.area - sum(h.area for h in holes) == pytest.approx(design.regrade_area(project).area, rel=1e-5)
    old = design.existing_walls(project)
    face, back, chain, toe = scheme.wall_edges(old[0], terrain.existing(project))
    wall = regrade["walls"][0]
    assert [w["id"] for w in regrade["walls"]] == [w["id"] for w in old]
    assert np.allclose([[e0 + x, n0 - z] for x, z in wall["face"]], face, atol=6e-4)
    assert np.allclose([[e0 + x, n0 - z] for x, z in wall["back"]], back, atol=6e-4)
    assert np.allclose(np.asarray(wall["toe"]) + h0, toe, atol=6e-4) and np.allclose(wall["chain"], chain, atol=6e-4)
    defaults = design.settings(project)["defaults"]
    assert regrade["defaults"] == {k: defaults[k] for k in ("upstand_m", "batter", "thickness_m", "paving_m")}
    assert regrade["rules"] == {"reach_m": scheme.BATTER_REACH_M, "held_m": scheme.HELD_M, "wall_step_m": scheme.WALL_STEP_M,
                                "toe_probe_m": scheme.TOE_PROBE_M, "back_probe_m": scheme.BACK_PROBE_M,
                                "sketch_back_m": sketch.DEFAULT_BACK_M}


def test_a_site_s_own_layers_are_placed_as_they_are():
    import pytest

    from domus.build import Frame, site_layer
    from domus.errors import DomusError

    frame = Frame((1000.0, 2000.0, 40.0))
    mesh = site_layer({"id": "site-peg", "type": "mesh", "colour": "#d23f2f", "indices": [0, 1, 2],
                       "positions": [[1001, 2002, 41], [1002, 2002, 41], [1001, 2003, 42]]}, frame, "data/layers/a.json")
    assert mesh["positions"][:3] == [1.0, 1.0, -2.0] and mesh["group"] == "The site's own" and mesh["indices"] == [0, 1, 2]
    lines = site_layer({"id": "site-line", "type": "lines", "lines": [[[1000, 2000, 40], [1010, 2000, 40]]]}, frame, "x")
    assert lines["segments"] == [0.0, 0.0, 0.0, 10.0, 0.0, 0.0] and lines["type"] == "lines"
    labels = site_layer({"id": "site-note", "type": "labels", "items": [{"text": "Peg", "at": [1000, 2001, 41.5]}]}, frame, "x")
    assert labels["items"] == [{"text": "Peg", "at": [0.0, 1.5, -1.0]}]
    for bad, says in [({"id": "peg", "type": "mesh"}, "starts with site-"), ({"id": "site-a", "type": "cube"}, "one of"),
                      ({"id": "site-a", "type": "mesh", "positions": [[0, 0, 0]], "indices": [0, 1]}, "isn't drawn")]:
        with pytest.raises(DomusError, match=says):
            site_layer(bad, frame, "data/layers/a.json")
