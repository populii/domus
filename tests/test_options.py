"""Wall options beyond walls and terraces, on the demo site: steps, an option restyled, a painted
picture for every wall material, fill against the house, walls in level bays and timber posts, walls of a
named block, steps of sleepers, the ground's edges at walls, steps and the house, steps on a foot that slopes
across them, the lawns an option leaves alone, walls of named blocks set out in whole blocks, and the ground options
are worked out on."""

import json
import re

import numpy as np
import pytest


def demo():
    """The demo site, as the `project` fixture has it: the one the tests' process was started for (tests/conftest.py)."""
    from domus.project.site import started

    return started()


def ground_at(s, o):
    from domus.model import terrain
    from domus.options import design

    en = design.frame(demo()).en(np.array([[s, o]]))
    return float(terrain.ground(demo()).sample(en[:, 0], en[:, 1])[0])


@pytest.fixture
def two_lawns(site):
    """A lower and an upper lawn 0.68 m apart with a wall between them, and steps beside the wall's end."""
    low = round(ground_at(8.0, 10.0), 2)
    return low, {
        "walls": [{"id": "W", "line_so": [[10.0, 6.0], [10.0, 18.0]]}],
        "terraces": [{"id": "lower", "level": low, "at_so": [8.0, 10.0], "polygon_so": [[6, 4], [10, 4], [10, 18], [6, 18]]},
                     {"id": "upper", "level": low + 0.68, "at_so": [12.0, 10.0],
                      "polygon_so": [[10, 4], [14, 4], [14, 18], [10, 18]]}],
        "stairs": [{"id": "S", "name": "Steps", "line_so": [[8.8, 5.0], [10.0, 5.0]], "width_m": 1.0}],
    }


def test_steps_take_their_levels_from_the_lawns_they_join(two_lawns, project):
    from domus.options import design

    low, opt = two_lawns
    d = design.Design(opt, site=project)
    st = d.stairs[0]
    assert st.foot == pytest.approx(low, abs=0.01) and st.head == pytest.approx(low + 0.68, abs=0.01)
    assert st.n == 4                                    # 0.68 m in risers of about 0.17 m
    mid = design.frame(project).en(np.array([[9.4, 5.0]]))     # the ground under the flight is a ramp just below the treads
    assert float(d.sample(mid[:, 0], mid[:, 1])[0]) == pytest.approx(low + 0.34 - 0.03, abs=0.01)
    res = design.evaluate(project, opt)
    s = res["stairs"][0]
    assert (s["risers"], s["riser_m"], s["going_m"], s["flags"]) == (4, 0.17, 0.3, [])
    z = s["mesh"].vertices[:, 2]
    assert z.max() == pytest.approx(st.head) and z.min() >= st.foot - 0.31


def test_steps_drawn_from_the_top_come_out_the_same(two_lawns, project):
    from domus.options import design

    low, opt = two_lawns
    opt["stairs"][0]["line_so"] = opt["stairs"][0]["line_so"][::-1]
    st = design.Design(opt, site=project).stairs[0]
    assert st.foot == pytest.approx(low, abs=0.01) and st.rise == pytest.approx(0.68, abs=0.02)
    assert design.frame(project).so(st.a)[0] == pytest.approx(8.8)     # its foot is the lower end


def test_a_short_flight_is_flagged_as_steep(two_lawns, project):
    from domus.options import design

    _, opt = two_lawns
    opt["stairs"][0]["line_so"] = [[9.4, 5.0], [10.0, 5.0]]
    flags = design.evaluate(project, opt)["stairs"][0]["flags"]
    assert len(flags) == 1 and "steep" in flags[0]


def test_a_lawn_edge_under_the_steps_gets_no_batter(two_lawns, project):
    """The upper lawn's edge is free where the wall stops short of it; the flight there holds it instead."""
    from domus.options import design

    _, opt = two_lawns
    d = design.Design(opt, site=project)
    upper_free = d.free[1]
    flight = d.stairs[0].poly
    assert not upper_free.intersects(flight.buffer(-0.05))


def test_an_option_can_be_another_one_restyled(tmp_path):
    from domus import options

    (tmp_path / "base.toml").write_text('''name = "Base"
keep_existing = ["W1"]
[[walls]]
id = "A"
material = "timber"
line_so = [[0, 0], [1, 0]]
[[walls]]
id = "B"
material = "timber"
line_so = [[0, 1], [1, 1]]
[[terraces]]
id = "lawn"
level = 1.0
polygon_so = [[0, 0], [1, 0], [1, 1]]
''', encoding="utf-8")
    variant = {"name": "In gabion", "based_on": "base",
               "restyle": [{"material": "gabion", "thickness_m": 0.5}, {"walls": ["B"], "material": "stone"}]}
    out = options.restyled(variant, tmp_path / "variant.toml")
    assert out["name"] == "In gabion" and out["keep_existing"] == ["W1"] and len(out["terraces"]) == 1
    assert [(w["id"], w["material"], w["thickness_m"]) for w in out["walls"]] == [("A", "gabion", 0.5), ("B", "stone", 0.5)]
    assert "based_on" not in out and "restyle" not in out
    (tmp_path / "third.toml").write_text('based_on = "variant"\n', encoding="utf-8")
    (tmp_path / "variant.toml").write_text('based_on = "base"\n', encoding="utf-8")
    with pytest.raises(ValueError, match="itself based on"):
        options.restyled({"based_on": "variant"}, tmp_path / "third.toml")


def test_every_wall_material_has_a_painted_picture(site, tmp_path):
    from PIL import Image

    from domus.model import wall_looks, walls

    for material, (image, size_m, _) in walls.MATERIAL_TEXTURES.items():
        path = wall_looks.paint(material, tmp_path / image, size_m)
        w, h = Image.open(path).size
        assert (w, h) == (round(size_m[0] * wall_looks.PPM), round(size_m[1] * wall_looks.PPM)), material
    assert set(walls.MATERIAL_TEXTURES) == set(walls.MATERIAL_COLOURS)
    assert walls.texture_image("gabion", tmp_path) == "wall-gabion.jpg" and (tmp_path / "wall-gabion.jpg").exists()
    assert walls.texture_image("gabion", tmp_path, painted=True) == "wall-gabion.jpg"       # the demo has no photo to pass over
    assert walls.texture_image("glass", tmp_path) is None


def test_a_wall_with_no_photo_is_drawn_with_its_painted_picture(built):
    layers = {x.get("id"): x for x in json.loads((built / "model.json").read_text(encoding="utf-8"))["layers"]}
    assert layers["wall-W1"]["texture"] == "wall-block.jpg" and (built / "wall-block.jpg").exists()


def test_fill_against_the_house_is_reported_as_that(site, project):
    """A bed raised against the house's north wall: the house holds it, so it's the cladding to mind, not a wall to add."""
    from domus.options import design

    level = round(ground_at(13.5, 11.0) + 0.4, 2)
    opt = {"terraces": [{"id": "bed", "kind": "planting", "level": level, "at_so": [13.5, 11.0],
                         "polygon_so": [[12.5, 7.0], [15.5, 7.0], [15.5, 15.0], [12.5, 15.0]]}]}
    flags = [e["flag"] for e in design.evaluate(project, opt)["edge"]]
    assert any("against the house" in f for f in flags)
    assert not any("needs a wall" in f for f in flags)


def test_an_option_can_take_a_site_feature_out(tmp_path):
    """Built on its own copy of the demo, so the shared one's layers stay as the other tests expect."""
    import os
    import subprocess
    import sys

    root = tmp_path / "demo"
    env = {k: v for k, v in os.environ.items() if k not in ("DOMUS_OUTPUT", "DOMUS_SITE")}
    subprocess.run([sys.executable, "-m", "domus.app.cli", "demo", str(root)], check=True, env=env, capture_output=True)
    option = root / "options" / "a-one-wall.toml"
    option.write_text(option.read_text(encoding="utf-8").replace(
        "keep_existing = []", 'keep_existing = []\nremove_features = ["fence-west", "no-such-thing"]'), encoding="utf-8")
    out = subprocess.run([sys.executable, "-m", "domus.app.cli", "--site", str(root), "build"], check=True, env=env,
                         capture_output=True, text=True)
    assert "no-such-thing" in out.stdout
    layers = json.loads((root / "viewer" / "data" / "model.json").read_text(encoding="utf-8"))["layers"]
    gone = [x for x in layers if x.get("hide_with")]
    assert gone and all(x["hide_with"] == ["a-one-wall"] and "West fence" in x["info"]["features"] for x in gone)


def stepped_wall(low):
    return {"walls": [{"id": "W", "material": "timber", "line_so": [[10.0, 6.0], [10.0, 9.0], [10.0, 12.6]],
                       "retained_side": "left", "tops": [low + 0.9, low + 0.6]}]}


def test_a_wall_can_step_in_level_bays(site, project):
    from domus.options import design

    low = round(ground_at(8.0, 10.0), 2)
    w = design.evaluate(project, stepped_wall(low))["walls"][0]
    assert (w["top_min"], w["top_max"]) == (pytest.approx(low + 0.6), pytest.approx(low + 0.9))
    v = w["mesh"].vertices
    so = design.frame(project).so(v[:, :2])
    boards = np.abs(so[:, 0] - 10.0) < 1e-6                         # the face of the boards, not the posts
    for o0, o1, levels in ((6.5, 8.5, {0.9}), (9.5, 12.0, {0.6}), (9.0, 9.0, {0.9, 0.6})):   # level along each bay, both at the step
        here = boards & (so[:, 1] > o0 - 1e-6) & (so[:, 1] < o1 + 1e-6)
        assert {round(float(z) - low, 2) for z in v[here, 2]} & {0.9, 0.6} == levels
    with pytest.raises(ValueError, match="needs 2 of them"):
        design.Design({"walls": [{**stepped_wall(low)["walls"][0], "tops": [low]}]}, site=project)


def test_a_timber_wall_has_its_posts_and_a_block_wall_none(site, project):
    from domus.model import walls
    from domus.options import design

    low = round(ground_at(8.0, 10.0), 2)
    w = design.Design(stepped_wall(low), site=project).wall_levels()[0]
    assert walls.post_positions(w) == pytest.approx([0, 1.5, 3.0, 4.2, 5.4, 6.6])     # ends, the step, and between
    m, _ = walls.mesh(w)
    so = design.frame(project).so(m.vertices[:, :2])
    assert so[:, 0].min() == pytest.approx(10.0 - walls.POST_PROUD_M)       # the posts stand out from the boards
    assert m.vertices[:, 2].max() == pytest.approx(low + 0.9 + walls.POST_PROUD_M)
    assert len(m.uv) == len(m.vertices) == len(m.cap)
    block, _ = walls.mesh({**w, "material": "block"})
    assert design.frame(project).so(block.vertices[:, :2])[:, 0].min() == pytest.approx(10.0)


def test_the_ground_behind_a_wall_is_the_ground_it_holds(site, project):
    """A wall that stands above the ground behind it, as a level bay does over falling ground, leaves that
    ground where it is."""
    from scipy.interpolate import LinearNDInterpolator

    from domus.model import terrain
    from domus.options import design

    low = round(ground_at(8.0, 10.0), 2)
    opt = {"walls": [{"id": "W", "material": "block", "line_so": [[10.0, 6.0], [10.0, 12.0]], "retained_side": "left",
                      "top": low + 1.5}]}
    d = design.Design(opt, site=project)
    fr = design.frame(project)
    e, n = fr.en(np.array([[10.0, 6.0], [10.0, 12.0]])).T
    tin = terrain.build([e.min() - 3, n.min() - 3, e.max() + 3, n.max() + 3], 0.5, d.wall_levels(), surface=d.sample, site=project)
    behind = fr.en(np.array([[10.4, 9.0]]))                         # just behind its back
    z = float(LinearNDInterpolator(tin.vertices[:, :2], tin.vertices[:, 2])(behind)[0])
    assert z == pytest.approx(float(d.sample(behind[:, 0], behind[:, 1])[0]), abs=0.1)
    assert z < low + 1.0


def test_a_wall_can_name_its_block(site, tmp_path, project):
    """A wall that names its block is drawn in it at its true size: one picture of the capping course, the
    courses under it and the cap's top, as long as four blocks, in the block's own colour."""
    from PIL import Image

    from domus.model import wall_looks, walls
    from domus.options import design

    low = round(ground_at(8.0, 10.0), 2)
    # No shortest cut (cut_min_m 0): its courses run on to its end as the picture does (tests/test_cut_min.py has the rule)
    cfg = {"id": "W", "material": "segmental", "unit_m": [0.457, 0.2], "cap_m": 0.1, "colour": "#7f868b", "cut_min_m": 0.0,
           "line_so": [[10.0, 6.0], [10.0, 12.0]], "retained_side": "left", "top": low + 0.9}
    w = design.Design({"walls": [cfg]}, site=project).wall_levels()[0]
    assert walls.units(w) == (0.457, 0.2, 0.1, "#7f868b")
    (tu, tv), bays = walls.texture_size(w)
    assert tu == pytest.approx(4 * 0.457) and tv == pytest.approx(0.1 + 16 * 0.2 + walls.UNIT_TOP_M) and not bays
    name = walls.wall_image(w, tmp_path)
    assert name.startswith("wall-units-") and walls.wall_image(w, tmp_path) == name
    img = np.asarray(Image.open(tmp_path / name).convert("RGB")).astype(float)
    assert img.shape[:2] == (round(tv * wall_looks.PPM), round(tu * wall_looks.PPM))
    assert np.abs(img.mean(axis=(0, 1)) - [0x7f, 0x86, 0x8b]).max() < 25
    joint = round(0.1 * wall_looks.PPM)                               # the bed joint under the cap is darker than the cap's face
    assert img[joint:joint + 2].mean() < 0.75 * img[5:joint - 3].mean()
    m, _ = walls.mesh(w)
    courses = m.uv[:, 1] * tv < -0.1 - 1e-6                           # under the capping course, which has its own units
    assert m.uv[courses, 0].max() == pytest.approx(6.0 / tu) and len(m.uv) == len(m.vertices)
    (start, end, joints), = w["set_out"]["caps"]                      # thirteen capping units reach along its 6 m
    assert (start, end, len(joints)) == (0.0, pytest.approx(6.0), 14) and np.diff(joints) == pytest.approx(6.0 / 13)
    plain = design.Design({"walls": [{k: v for k, v in cfg.items() if k not in ("unit_m", "cap_m", "colour")}]}, site=project).wall_levels()[0]
    assert walls.units(plain) is None and walls.wall_image(plain, tmp_path) == "wall-segmental.jpg"


def test_a_restyle_to_another_material_drops_the_block_and_steps_restyle_too(tmp_path):
    from domus import options

    (tmp_path / "base.toml").write_text('''name = "Base"
[[walls]]
id = "A"
material = "segmental"
unit_m = [0.457, 0.2]
cap_m = 0.1
colour = "#7f868b"
line_so = [[0, 0], [1, 0]]
[[stairs]]
id = "S"
line_so = [[0, 0], [1, 0]]
[[stairs]]
id = "T"
line_so = [[0, 2], [1, 2]]
''', encoding="utf-8")
    out = options.restyled({"based_on": "base", "restyle": [{"material": "gabion"}],
                            "restyle_stairs": [{"material": "sleepers"}, {"stairs": ["T"], "material": "concrete"}]},
                           tmp_path / "variant.toml")
    assert out["walls"][0]["material"] == "gabion" and not {"unit_m", "cap_m", "colour"} & set(out["walls"][0])
    assert [(s["id"], s["material"]) for s in out["stairs"]] == [("S", "sleepers"), ("T", "concrete")]
    assert "restyle_stairs" not in out
    same = options.restyled({"based_on": "base", "restyle": [{"thickness_m": 0.4}]}, tmp_path / "variant.toml")
    assert same["walls"][0]["unit_m"] == [0.457, 0.2] and "material" not in same["stairs"][0]
    built = (tmp_path / "base.toml").read_text(encoding="utf-8").replace("cap_m = 0.1\n", "cap_m = 0.1\ndepth_m = 0.3\n"
                                                                        "embed_m = 0.1\nbase_m = 0.15\n")
    (tmp_path / "base.toml").write_text(built, encoding="utf-8")
    same = options.restyled({"based_on": "base", "restyle": [{"thickness_m": 0.4}]}, tmp_path / "variant.toml")
    assert {"depth_m", "embed_m", "base_m"} <= set(same["walls"][0])
    out = options.restyled({"based_on": "base", "restyle": [{"material": "gabion"}]}, tmp_path / "variant.toml")
    assert not set(options.CONSTRUCTION) & set(out["walls"][0])            # how the block wall was built goes too


def test_steps_can_be_railway_sleepers(two_lawns, project):
    """A flight of sleepers is two sleepers to a tread, each with faces of its own, and carries the picture's uv."""
    from domus.options import design

    low, opt = two_lawns
    s = design.evaluate(project, {**opt, "stairs": [{**opt["stairs"][0], "material": "sleepers"}]})["stairs"][0]
    m = s["mesh"]
    assert (s["material"], s["risers"]) == ("sleepers", 4) and design.STEP_LOOKS["sleepers"][1][0] == "steps-sleepers.jpg"
    assert m.uv.shape == (len(m.vertices), 2) and len(m.vertices) == 4 * (4 * 7) + 4       # 7 faces a tread, and the back
    assert m.vertices[:, 2].max() == pytest.approx(low + 0.68, abs=0.01)
    tops = m.vertices[m.faces][np.ptp(m.vertices[m.faces][:, :, 2], axis=1) < 1e-9]            # the level faces
    assert len(tops) == 4 * 2 * 2 and sorted({round(float(z) - low, 2) for z in tops[:, 0, 2]}) == [0.17, 0.34, 0.51, 0.68]


def test_a_walls_back_does_not_fold_at_a_corner():
    """Offset to the inside of a corner, the points near it go to the mitre; to the outside, the corner goes out to it."""
    from domus.geo.mesh import offset_behind

    line = np.array([[0, 0], [1, 0], [1.9, 0], [2, 0], [2, 0.1], [2, 1], [2, 2]], float)      # east, then north
    assert np.allclose(offset_behind(line, 0.3, 1), [[0, 0.3], [1, 0.3], [1.7, 0.3], [1.7, 0.3], [1.7, 0.3], [1.7, 1], [1.7, 2]])
    assert np.allclose(offset_behind(line, 0.3, -1), [[0, -0.3], [1, -0.3], [1.9, -0.3], [2.3, -0.3], [2.3, 0.1], [2.3, 1], [2.3, 2]])
    step = np.array([[0, 0], [1, 0], [1, 0], [2, 0]], float)                                   # a point given twice, as at a step in the top
    assert np.allclose(offset_behind(step, 0.3, 1), [[0, 0.3], [1, 0.3], [1, 0.3], [2, 0.3]])


def test_the_ground_has_a_clean_edge_at_steps_and_at_a_building(site, project):
    """Round a flight of steps the ground is the ground beside it up to the line and the ground under it from
    there; at a building it runs level to the wall, and meets what is under the building just inside it."""
    import shapely
    from scipy.interpolate import LinearNDInterpolator
    from shapely.geometry import box

    from domus.model import terrain
    from domus.options import design

    e, n = design.frame(project).en(np.array([[8.0, 10.0]]))[0]
    pit = box(e - 1, n - 1, e + 1, n + 1)

    def surface(E, N):
        return np.where(shapely.contains_xy(pit, E, N), 10.0, 11.0)

    def at(tin, de):                # read about the mesh's own middle, as it was triangulated
        mid = tin.vertices[:, :2].mean(axis=0)
        return float(LinearNDInterpolator(tin.vertices[:, :2] - mid, tin.vertices[:, 2])(e + de - mid[0], n + 0.13 - mid[1]))

    extent = [e - 4, n - 4, e + 4, n + 4]
    smeared = terrain.build(extent, 0.5, surface=surface, site=project)
    assert 10.05 < at(smeared, 0.9) < 10.95                         # without an edge the step is spread over a grid cell
    steps = terrain.build(extent, 0.5, surface=surface, edges=[(pit, False)], site=project)
    assert at(steps, 0.95) == pytest.approx(10.0) and at(steps, 1.05) == pytest.approx(11.0)
    house = terrain.build(extent, 0.5, surface=surface, edges=[(pit, True)], site=project)
    assert at(house, 1.05) == pytest.approx(11.0) and at(house, 0.99) == pytest.approx(11.0) and at(house, 0.8) == pytest.approx(10.0)


def test_the_ground_runs_on_under_a_bay_window(monkeypatch, project):
    """A bay window hangs from the wall, so it takes no ground: an option regrades under it."""
    from shapely.geometry import box

    from domus.options import design

    def ring(*bounds):
        return [list(box(*bounds).exterior.coords)]

    feats = [{"geometry": {"type": "Polygon", "coordinates": ring(0, 0, 10, 8)}, "properties": {"id": "house-walls"}},
             {"geometry": {"type": "Polygon", "coordinates": ring(2, 8, 4, 8.6)}, "properties": {"id": "bay", "part": "bay", "bottom_above_fl": -0.2}}]
    monkeypatch.setattr(design.geojson, "read_optional", lambda path: feats)
    design.house_outline.cache_clear(project)
    try:
        assert design.house_outline(project).area == pytest.approx(80.0)
        assert design.house_building(project).area == pytest.approx(81.2)
    finally:
        design.house_outline.cache_clear(project)


def sloping_foot(two_lawns):
    """The two lawns' flight, 1.4 m wide, on ground that rises 0.28 m across its foot, left to right or right to
    left: (the lower lawn's level, the option, the flight's `foot`, and whether the frame's `o` is to the left)."""
    from domus.options import design

    low, opt = two_lawns
    en = design.frame(demo()).en(np.array([[0.0, 0.0], [1.0, 0.0], [0.0, 1.0]]))
    along, inward = en[1] - en[0], en[2] - en[0]
    o_is_left = along[0] * inward[1] - along[1] * inward[0] > 0      # facing up the flight, which runs along s
    stair = {**opt["stairs"][0], "width_m": 1.4, "foot": [low + 0.28, low], "head": low + 0.68, "risers": 4}
    return low, opt, stair, o_is_left


def test_a_flight_can_stand_on_ground_that_slopes_across_its_foot(two_lawns, project):
    """`foot` with two levels, the ground at the left and right ends of the foot line: the treads stay level, the
    foot line straight and every tread runs the full width. The lowest tread is level with the ground at the high
    end, and the risers above it are equal."""
    from domus.options import design

    low, opt, stair, _ = sloping_foot(two_lawns)
    d = design.Design({**opt, "stairs": [stair]}, site=project)
    st = d.stairs[0]
    assert (st.foot, st.n, st.cross) == (low, 4, (low + 0.28, low))
    tops = [low + 0.28 + (0.68 - 0.28) * i / 3 for i in range(4)]
    assert st.tops() == pytest.approx(tops) and st.riser == pytest.approx(0.4 / 3)
    m = st.mesh()
    assert len(m.vertices) == 4 * 8                 # four treads, each one block the full width
    across = (m.vertices[:, :2] - st.a) @ np.array([-st.along[1], st.along[0]])
    for top in tops:
        on = np.isclose(m.vertices[:, 2], top)
        assert on.sum() == 4 and across[on].min() == pytest.approx(-0.7) and across[on].max() == pytest.approx(0.7)
    side = np.array([-st.along[1], st.along[0]])    # to the left, facing up the flight

    def at(chain, across):
        p = st.a + st.along * chain + side * across
        return np.array([p[0]]), np.array([p[1]])

    # The ground it stands on: 0.5 m left of centre the foot's ground (0.24 m up) runs in level, then the ramp takes over.
    assert float(d.sample(*at(0.1, 0.5))[0]) == pytest.approx(low + 0.24 - 0.03)
    assert float(d.sample(*at(1.1, 0.5))[0]) == pytest.approx(low + 0.68 * 1.1 / 1.2 - 0.03)
    assert float(d.sample(*at(0.1, -0.6))[0]) == pytest.approx(low + 0.68 * 0.1 / 1.2 - 0.03)
    # What is drawn under the treads is the ramp alone, which is below every one of them.
    assert float(d.drawn(*at(0.1, 0.5))[0]) == pytest.approx(low + 0.68 * 0.1 / 1.2 - 0.03)
    s = design.evaluate(project, {**opt, "stairs": [stair]})["stairs"][0]
    assert (s["risers"], s["riser_m"], s["foot"], s["foot_rise_m"]) == (4, 0.133, low, 0.28) and "tread_widths_m" not in s
    m = design.evaluate(project, {**opt, "stairs": [{**stair, "material": "sleepers"}]})["stairs"][0]["mesh"]
    quads = 4 * 2 * 3 + 4 + 1                                # sleepers' tops and ends, risers, the back
    assert m.uv.shape == (len(m.vertices), 2) and len(m.vertices) == 4 * quads
    # Two equal levels are a level foot, and a foot above its head is a mistake.
    plain = design.Design({**opt, "stairs": [{**stair, "foot": low}]}, site=project).stairs[0]
    level = design.Design({**opt, "stairs": [{**stair, "foot": [low, low]}]}, site=project).stairs[0]
    assert level.cross is None and np.array_equal(level.mesh().vertices, plain.mesh().vertices)
    with pytest.raises(ValueError, match="starts at the foot"):
        design.Design({**opt, "stairs": [{**stair, "foot": [low + 0.9, low + 0.8]}]}, site=project)


def test_a_wall_beside_steps_on_a_sloping_foot_is_measured_at_its_own_face(two_lawns, project):
    """A wall up the high side of such a flight stands on the ground under the flight at the wall, not on the
    lower ground 0.35 m out across the flight, where a wall's foot is otherwise read."""
    from domus.options import design

    low, opt, stair, o_is_left = sloping_foot(two_lawns)
    high_side = 5.7 if o_is_left else 4.3           # the flight's centre line is o = 5.0, and its left is the high end
    cheek = {"id": "C", "line_so": [[8.8, high_side], [10.0, high_side]], "top": low + 1.0, "retained_side": "left"}
    d = design.Design({**opt, "walls": [*opt["walls"], cheek], "stairs": [stair]}, site=project)
    w = next(x for x in d.wall_levels() if x["id"] == "C")
    assert w["toe"][1] == pytest.approx(low + 0.28 - 0.03, abs=0.005)        # the foot line's ground at the wall, run in level
    out = w["samples"][1] - w["normals"][1] * 0.35
    assert float(d.sample(out[:1], out[1:])[0]) == pytest.approx(low + 0.21 - 0.03, abs=0.005)   # and 0.35 m out


def test_an_option_keeps_the_lawns_it_leaves_alone(built, project):
    """A lawn that is there stays drawn where an option leaves it alone, on the option's ground, and its own layer
    names the options that redraw it. It is drawn as its grass without its bare patches, as the beds, their plants
    and the stumps are not drawn with an option: the soil under a clump of weeds would show as a bare spot. A new
    lawn beside it is drawn as the same grass."""
    from shapely.geometry import Polygon
    from shapely.ops import unary_union

    from domus import options
    from domus.model import canopy, vegetation
    from domus.options import design

    model = json.loads((built / "model.json").read_text(encoding="utf-8"))
    by_id = {x["id"]: x for x in model["layers"]}
    lawn = vegetation.lawns(canopy.read(project))[0]
    assert by_id[f"veg-{lawn['id']}"]["redrawn_by"] == [o["id"] for o in model["summary"]["options"]]
    assert "hide_with" not in by_id[f"veg-{lawn['id']}"]            # that is for a feature an option takes out
    e0, n0 = model["origin"][:2]

    def drawn(layer):                                               # its triangles in plan (viewer x east, z south)
        p = np.asarray(layer["drape"]["positions"], float).reshape(-1, 3)
        tri = p[np.asarray(layer["drape"]["indices"]).reshape(-1, 3)]
        return unary_union([Polygon(np.column_stack([t[:, 0] + e0, n0 - t[:, 2]])) for t in tri])

    defs = {o["id"]: o for o in options.load_all(project)}
    for o in model["summary"]["options"]:
        kept = by_id[f"option-{o['id']}-lawn-{lawn['id']}"]
        assert (kept["type"], kept["role"], kept["option"], kept["visible"]) == ("vegetation", "skin", o["id"], False)
        assert kept["drape"]["texture"] == f"{lawn['id']}-plain.jpg" and o["lawns_kept"] == [lawn["id"]]
        d = design.Design(defs[o["id"]], site=project)
        taken = unary_union([t.poly for t in d.terraces if t.kind != "ground"] + [st.poly for st in d.stairs])
        area = drawn(kept)
        assert area.area > 5 and lawn["poly"].buffer(0.01).contains(area) and area.intersection(taken).area < 0.01
        # the option's own lawn is beside the lawn that is there, so it is that lawn's grass
        new = by_id[f"option-{o['id']}-terrace-lawn"]
        assert (new["type"], new["role"], new["drape"]["texture"]) == ("vegetation", "skin", f"{lawn['id']}-plain.jpg")
        assert o["lawn_pictures"] == [lawn["id"]] and drawn(new).intersection(area).area < 0.01
    assert (built / f"{lawn['id']}-plain.jpg").exists()


BLOCK = {"material": "segmental", "unit_m": [0.457, 0.2], "cap_m": 0.1, "thickness_m": 0.27}


def cornered(first, second, last=1.0):
    """A wall of named blocks that runs `first` metres, turns a corner, runs `second` metres, turns the same way
    again and runs `last` metres more, with the side given that makes both of them outside corners."""
    from domus.model import walls
    from domus.options import design

    line = [[10.0, 6.0], [10.0, 6.0 + first], [10.0 + second, 6.0 + first], [10.0 + second, 6.0 + first - last]]
    for side in ("left", "right"):
        w = design.Design({"walls": [{**BLOCK, "id": "W", "line_so": line, "retained_side": side,
                                      "top": round(ground_at(8.0, 10.0), 2) + 1.3}]}, site=demo()).wall_levels()[0]
        if walls.stretches(w)[2] == [1, 1]:
            return w
    raise AssertionError("neither side makes them outside corners")


def test_a_block_wall_has_a_corner_block_at_each_outside_corner(site):
    """The bond of a wall of named blocks starts at its corners: a joint at the first outside corner, the next
    face half a block on from it, and whole blocks from one corner to the next where they fit."""
    from domus.model import walls

    block = 0.457
    w = cornered(3 * block, 4.5 * block)
    assert walls.set_out([w]) == {"W": []}
    so = w["set_out"]
    assert so["cuts"] == pytest.approx([0, 3 * block, 7.5 * block, 7.5 * block + 1.0])
    assert so["rate"] == pytest.approx([1 / block] * 3)
    assert so["u0"] == pytest.approx([-3.0, 0.5, 5.5])                # 4.5 blocks on from the half: a joint at the second corner
    m, _ = walls.mesh(w)
    assert len(m.uv) == len(m.vertices) and not m.cap.any()
    # Half a block's width over: blocks are drawn up to 1% long to reach the corner, and no further.
    near = cornered(3 * block, 4.5 * block + 0.01)
    assert walls.set_out([near]) == {"W": []}
    assert 1 / near["set_out"]["rate"][1] == pytest.approx(block + 0.01 / 4.5) and near["set_out"]["u0"][2] == pytest.approx(5.5)
    far = cornered(3 * block, 4.5 * block + 0.049)
    notes = walls.set_out([far])["W"]
    assert len(notes) == 1 and "49 mm off a joint" in notes[0] and "180 mm the other way" in notes[0]
    assert far["set_out"]["rate"] == pytest.approx([1 / block] * 3)
    # A corner the face turns in at has no corner block: the blocks of the two faces butt there, turn about from
    # course to course, so the next face starts half a block on round it too.
    inside = {**w, "side": -w["side"]}
    inside.pop("set_out")
    assert walls.stretches(inside)[2] == [-1, -1]
    assert walls.set_out([inside]) == {"W": []}
    assert inside["set_out"]["u0"] == pytest.approx([0.0, 3.5, 8.5])


def test_a_block_walls_blocks_butt_turn_about_in_an_inside_corner(site, project):
    """Between an outside corner and an inside one the bond runs on half a block round each: whole blocks reach
    both where the stretches are whole numbers of half blocks. Where one is not, the outside corner keeps its
    joint, the cut block stands in the inside corner, and the wall says so."""
    from domus.model import walls
    from domus.options import design

    block = 0.457

    def stepped(third):
        """Runs 3 blocks, turns, 4.5 blocks, turns back, `third` blocks, turns as at first, and 1 m more."""
        a, b, c = 3 * block, 4.5 * block, third * block
        line = [[10.0, 6.0], [10.0, 6.0 + a], [10.0 + b, 6.0 + a], [10.0 + b, 6.0 + a + c], [11.0 + b, 6.0 + a + c]]
        for side in ("left", "right"):
            w = design.Design({"walls": [{**BLOCK, "id": "W", "line_so": line, "retained_side": side,
                                          "top": round(ground_at(8.0, 10.0), 2) + 1.3}]}, site=project).wall_levels()[0]
            if walls.stretches(w)[2] == [1, -1, 1]:
                return w
        raise AssertionError("neither side makes the first corner an outside one")

    w = stepped(3.5)
    assert walls.set_out([w]) == {"W": []}
    assert w["set_out"]["u0"] == pytest.approx([-3.0, 0.5, 5.5, 9.5])      # a whole block at 5.0 one side of the inside corner, half the other
    odd = stepped(3.25)
    notes = walls.set_out([odd])["W"]
    assert len(notes) == 1 and "a block is cut in the inside corner 3.43 m along it" in notes[0]
    assert "move a corner 114 mm, or 114 mm the other way" in notes[0]
    assert odd["set_out"]["u0"] == pytest.approx([-3.0, 0.5, -3.25, 0.5])    # the joint is kept at the outside corner


def test_capping_units_are_whole_from_each_corner_step_and_end(site, project):
    """A capping course is laid in units as long as the blocks: whole ones in from both ends of each length of
    top, the one cut to fit in the middle, and none of them a sliver. At an outside corner the unit of one face
    runs through to the corner and the next face's butts against it, its depth round the corner. The capping
    course and the top are drawn with those joints."""
    from domus.model import walls
    from domus.options import design

    block = 0.457
    joints, count = walls._lay(1.0, 1.0 + 4 * block, block)
    assert count == 0 and np.diff(joints) == pytest.approx([block] * 4)
    joints, count = walls._lay(0.0, 3.7 * block, block)                       # one cut, in the middle
    assert count == 1 and np.diff(joints) == pytest.approx([block, block, 0.7 * block, block])
    joints, count = walls._lay(0.0, 4.25 * block, block)                      # too short a piece: two share it with a whole one
    assert count == pytest.approx(1.2) and np.diff(joints) == pytest.approx([block, block, 0.625 * block, 0.625 * block, block])
    w = cornered(3 * block, 4 * block)
    walls.set_out([w])
    (a0, a1, first), (_, b1, second), (_, c1, third) = w["set_out"]["caps"]
    assert (a0, a1, b1, c1) == pytest.approx((0.0, 3 * block, 7 * block, 7 * block + 1.0))
    assert first == pytest.approx([0, block, 2 * block, 3 * block])           # three whole units, the last through to the corner
    assert second[0] == pytest.approx(3 * block + 0.27) and second[-1] == pytest.approx(7 * block)
    assert np.diff(second) == pytest.approx([block, block, 4 * block - 0.27 - 3 * block, block])
    assert third[0] == pytest.approx(7 * block + 0.27) and min(np.diff(third)) >= walls.UNIT_CUT_MIN_M
    m, _ = walls.mesh(w)
    so = design.frame(project).so(m.vertices[:, :2])
    edge = (np.abs(m.vertices[:, 2] - m.vertices[:, 2].max()) < 1e-9) & (np.abs(m.uv[:, 1]) < 1e-9) & (np.abs(so[:, 0] - 10.0) < 1e-6)
    inner = edge & (so[:, 1] > 6.01) & (so[:, 1] < 6.0 + 3 * block - 0.01)   # the front edge of the top, between the first face's ends
    units = m.uv[inner, 0] * walls.UNIT_BLOCKS - 0.25                         # the picture's cap has its joints a quarter along
    assert {1.0, 2.0} <= {round(float(u), 6) for u in units}                  # a point of the top at each joint
    assert so[inner, 1] - 6.0 == pytest.approx(units * block)                 # and one unit to a block's length along it


def test_a_bed_is_drawn_up_to_the_bed_beside_it(site):
    """A bed is drawn a little inside its outline, but right up to a wall and to another bed or lawn of the
    scheme that lies against it: no strip of bare ground shows between the two."""
    from shapely.geometry import box
    from shapely.ops import unary_union

    from domus import build

    a, b, none = box(0, 0, 2, 2), box(2, 0, 4, 2), unary_union([])
    assert build.skin_area(a, none, none).bounds == pytest.approx((0.03, 0.03, 1.97, 1.97))
    both = unary_union([build.skin_area(a, none, b), build.skin_area(b, none, a)])
    assert both.contains(box(1.9, 0.1, 2.1, 1.9)) and both.bounds == pytest.approx((0.03, 0.0, 3.97, 2.0))
    assert build.skin_area(a, box(-0.3, 0, 0, 2), none).bounds[0] == pytest.approx(0.0)


def meeting(drop, low):
    """A wall of named blocks with a corner, and a lower one that carries on along its first face: `drop` lower."""
    return {"walls": [{**BLOCK, "id": "A", "line_so": [[10.0, 6.0], [10.0, 9.0], [12.0, 9.0]], "top": low + 1.3},
                      {**BLOCK, "id": "B", "line_so": [[10.0, 8.95], [10.0, 12.0]], "top": low + 1.3 - drop}]}


def test_block_walls_that_meet_share_their_courses_and_their_bond(site, project):
    """Two walls of the same block that stand against each other are set out as one: the same courses, the bond
    carried on from the one into the other, and the face they share drawn once. A top that is not a whole number
    of courses from the other's is reported."""
    from domus.options import design

    low = round(ground_at(8.0, 10.0), 2)
    side = "left" if cornered(1.0, 1.0)["side"] == 1 else "right"     # the side that makes A's corner an outside one
    opt = meeting(0.6, low)
    for w in opt["walls"]:
        w["retained_side"] = side
    res = design.evaluate(project, opt)
    a, b = (w["def"]["set_out"] for w in res["walls"])
    assert not a["notes"] and not b["notes"] and not any("course" in f for w in res["walls"] for f in w["flags"])
    assert a["datum"] == pytest.approx(low + 1.2) and b["datum"] == pytest.approx(low + 1.2)      # three courses over B's own
    assert a["u0"][0] + a["rate"][0] * 3.0 == pytest.approx(0.0, abs=1e-6)                        # a joint at A's corner
    assert b["u0"][0] + b["rate"][0] * 0.05 == pytest.approx(0.0, abs=1e-6) and b["rate"][0] == pytest.approx(a["rate"][0])
    assert b["hidden"] == [pytest.approx((0.0, 0.05), abs=1e-6)] and a["hidden"] == []
    m = res["walls"][1]["mesh"]
    so = design.frame(project).so(m.vertices[:, :2])
    on_face = (np.abs(so[:, 0] - 10.0) < 1e-6)[m.faces].all(axis=1)                               # the triangles of B's face
    assert so[m.faces[on_face], 1].min() == pytest.approx(9.0)                                    # which starts where A's ends
    assert so[:, 1].min() == pytest.approx(8.95)                                                  # while its top runs on into A
    # two and a half courses lower: B is still set out on A's courses, and says what is cut
    odd = meeting(0.5, low)
    for w in odd["walls"]:
        w["retained_side"] = side
    flags = design.evaluate(project, odd)["walls"][1]["flags"]
    assert any("100 mm off the courses of A" in f and "100 mm higher or 100 mm lower" in f for f in flags)
    # a wall on its own keeps its own courses, whatever its top
    alone = {"walls": [odd["walls"][1], {**odd["walls"][0], "line_so": [[10.0, 20.0], [10.0, 23.0], [12.0, 23.0]]}]}
    lone = design.evaluate(project, alone)["walls"][0]
    assert lone["def"]["set_out"]["datum"] == pytest.approx(low + 0.7) and not any("courses" in f for f in lone["flags"])


def test_a_block_walls_courses_stay_level_where_its_top_steps(site, project):
    """Under a top that steps the courses run through, each bay has its own capping course, and the end of the
    higher bay shows across the wall. A step that is not a whole number of courses is reported."""
    from domus.model import walls
    from domus.options import design

    low = round(ground_at(8.0, 10.0), 2)
    cfg = {**BLOCK, "id": "W", "line_so": [[10.0, 6.0], [10.0, 9.0], [10.0, 12.6]], "retained_side": "left",
           "tops": [low + 1.1, low + 0.7]}
    w = design.evaluate(project, {"walls": [cfg]})["walls"][0]
    assert not any("course" in f for f in w["flags"])
    (_, tv), _ = walls.texture_size(w["def"])
    m = w["mesh"]
    so = design.frame(project).so(m.vertices[:, :2])
    down = np.round(m.uv[:, 1] * tv, 3)                               # metres down the picture: the cap is its first 0.1
    face = np.abs(so[:, 0] - 10.0) < 1e-6

    def at(level, o0, o1):
        here = face & (np.abs(m.vertices[:, 2] - level) < 1e-6) & (so[:, 1] > o0 - 1e-6) & (so[:, 1] < o1 + 1e-6)
        return {float(x) for x in down[here]}

    assert at(low + 1.1, 6.0, 9.0) == {0.0} and at(low + 0.7, 9.01, 12.6) == {0.0}                # each bay's cap at its own top
    assert at(low + 1.0, 6.0, 9.0) == {-0.1}                                                      # the higher bay's first course
    assert at(low + 0.6, 9.01, 12.6) == {-0.1, -0.5}            # the lower bay's cap ends, on the higher bay's third course
    assert at(low + 0.7, 9.0, 9.0) == {0.0, -0.4}               # and at the step, the lower cap's top meets the end of the higher bay
    across = (np.abs(so[:, 1] - 9.0) < 1e-6) & (so[:, 0] > 10.0 + 1e-6)                           # the end of the higher bay
    assert m.vertices[across, 2].max() == pytest.approx(low + 1.1) and so[across, 0].max() == pytest.approx(10.27)
    odd = design.evaluate(project, {"walls": [{**cfg, "tops": [low + 1.1, low + 0.8]}]})["walls"][0]
    assert any("1 of its 2 top levels are not a whole number of 200 mm courses" in f for f in odd["flags"])


def test_the_ground_keeps_its_level_up_to_a_walls_face_and_back(site, project):
    """The step in the ground at a wall is inside the wall: just in front of its face the ground is the ground at
    its foot, and just behind its back the ground it holds."""
    from scipy.interpolate import LinearNDInterpolator

    from domus.model import terrain
    from domus.options import design

    low = round(ground_at(8.0, 10.0), 2)
    opt = {"walls": [{"id": "W", "material": "block", "line_so": [[10.0, 6.0], [10.0, 12.0]], "retained_side": "left",
                      "thickness_m": 0.3}],
           "terraces": [{"id": "lower", "level": low, "at_so": [8.0, 9.0], "polygon_so": [[6, 6], [10, 6], [10, 12], [6, 12]]},
                        {"id": "upper", "level": low + 0.8, "at_so": [12.0, 9.0], "polygon_so": [[10, 6], [14, 6], [14, 12], [10, 12]]}]}
    d = design.Design(opt, site=project)
    w = d.wall_levels()[0]
    fr = design.frame(project)
    e, n = fr.en(np.array([[10.0, 6.0], [10.0, 12.0]])).T
    tin = terrain.build([e.min() - 3, n.min() - 3, e.max() + 3, n.max() + 3], 0.5, [w], surface=d.sample, site=project)
    mid = tin.vertices[:, :2].mean(axis=0)
    ground = LinearNDInterpolator(tin.vertices[:, :2] - mid, tin.vertices[:, 2])
    behind = 1 if w["side"] == 1 else -1                              # which way along s the wall's body lies
    front, back = (fr.en(np.array([[10.0 + behind * x, 9.0]]))[0] - mid for x in (-0.01, 0.31))
    assert float(ground(*front)) == pytest.approx(low, abs=0.005)
    assert float(ground(*back)) == pytest.approx(low + 0.8, abs=0.005)


def test_capping_units_end_at_a_corner_where_the_top_changes_level():
    """A corner with the top at two levels is the end of the higher wall and the start of the lower one: the
    capping units of each end there, and none runs round it."""
    from domus.model import walls

    block = 0.457
    chain = np.array([0.0, 3 * block, 3 * block, 5 * block])
    level = walls._caps(chain, np.array([1.0, 1.0, 1.0, 1.0]), [0.0, 3 * block, 5 * block], [1], block, 0.2285)
    assert level[1][2][0] == pytest.approx(3 * block + 0.2285)                # one level: the second face's butts round the corner
    stepped = walls._caps(chain, np.array([1.0, 1.0, 0.8, 0.8]), [0.0, 3 * block, 5 * block], [1], block, 0.2285)
    assert [np.diff(j) == pytest.approx([block] * n) for (_, _, j), n in zip(stepped, (3, 2), strict=True)] == [True, True]


def test_a_step_of_one_course_is_capped_as_a_stair():
    """Two capping units make a course: at a step of one course the higher capping course runs half a unit on
    past the step, over two capping units stacked beside the end of the higher course."""
    from domus.model import walls

    block = 0.457
    chain = np.array([0.0, 2.5 * block, 2.5 * block, 5 * block])
    top = np.array([1.0, 1.0, 0.8, 0.8])
    plain = walls._caps(chain, top, [0.0, 5 * block], [], block, 0.2285)
    assert plain[0][2][-1] == pytest.approx(2.5 * block)
    stair = walls._caps(chain, top, [0.0, 5 * block], [], block, 0.2285, lap=0.2)
    assert np.diff(stair[0][2]) == pytest.approx([block] * 3) and stair[1][2][0] == pytest.approx(2.5 * block)


def test_options_are_worked_out_on_the_ground_the_build_draws(built, project):
    """The wall options are designed on and measured from the ground as the build draws it, with the ground fixes and
    the existing walls' faces and tops in it: just behind the demo's old wall, the wall's top, a third of a metre
    above the LiDAR there."""
    from domus.geo import mesh
    from domus.model import terrain, walls
    from domus.options import design

    ground = design.context(project).ground
    assert ground is terrain.existing(project) and ground.surface is terrain.drawn(project)
    drawn = next(x for x in json.loads((built / "model.json").read_text(encoding="utf-8"))["layers"] if x["id"] == "ground")
    assert len(drawn["indices"]) == terrain.drawn(project).faces.size      # the triangles the model shows
    old = design.existing_walls(project)[0]
    pts, _, _, _, top = walls.wall_levels(old, terrain.ground(project))
    k = len(pts) // 2
    behind = mesh.offset_behind(pts, old["thickness"] + 0.01, old["side"])[k]
    here = ground.sample(behind[:1], behind[1:])[0]
    assert here == pytest.approx(top[k], abs=0.03)
    assert here > terrain.ground(project).sample(behind[:1], behind[1:])[0] + 0.3


def test_a_paved_terrace_is_drawn_as_paving(site, at_site, tmp_path):
    """A terrace of kind "paving" is drawn with the pavers' picture, in the colour the viewer sketches paving in, where
    a planted bed is drawn with the bed's."""
    from PIL import Image

    from domus import build
    from domus.model import terrain, wall_looks
    from domus.options import design

    s = at_site(site, tmp_path)
    low = round(ground_at(10.0, 10.0), 2)
    opt = {"id": "paved", "name": "Paved", "short": "P", "description": "", "path": "options/paved.toml", "walls": [],
           "terraces": [{"id": "patio", "kind": "paving", "level": low, "polygon_so": [[9, 6], [11, 6], [11, 12], [9, 12]]},
                        {"id": "bed", "kind": "planting", "level": low, "polygon_so": [[11, 6], [12, 6], [12, 12], [11, 12]]}]}
    cfg = s.settings()
    frame = build.Frame(build.site_origin(cfg, terrain.ground(s)))
    layers, _, _ = build.option_layers(s, opt, design.evaluate(s, opt), frame, cfg["model"]["extent"], cfg, [])
    skins = {x["id"]: x for x in layers if x.get("role") == "skin"}
    patio, bed = skins["option-paved-terrace-patio"], skins["option-paved-terrace-bed"]
    assert (patio["texture"], patio["colour"], patio["tint"]) == ("pavers.jpg", "#b9b2a5", None) and bed["texture"] == "bed.jpg"
    pavers = np.asarray(Image.open(tmp_path / "pavers.jpg").convert("RGB"), float)
    assert pavers.shape == (512, 512, 3)                                     # 2 m of pavers, at the painted pictures' scale
    assert np.allclose(pavers.reshape(-1, 3).mean(axis=0), wall_looks.PAVER_RGB, atol=8)    # the sketch's paving, on average
    from domus.app import serve

    sketched = re.search(r'AREA_KINDS = \{[^}]*path: "(#[0-9a-f]{6})"', (serve.VIEWER / "index.html").read_text(encoding="utf-8"))
    assert sketched[1] == patio["colour"] == "#" + "".join(f"{v:02x}" for v in wall_looks.PAVER_RGB)    # the viewer's sketch of a path


def test_a_block_wall_is_drawn_as_deep_as_its_units_with_its_foot_in_the_ground(site, project):
    """A wall of a named block can say how deep its units are (depth_m) and its capping units (cap_depth_m), how far
    its capping course stands out from its face (cap_overhang_m), and how far its first course goes into the ground at
    its toe: it's drawn that deep, its capping course standing out that far, and its foot on the course at or below
    that depth, so it steps down with the ground a course at a time. thickness_m still sets it out, so its blocks and
    capping units are laid as they were."""
    from domus.model import walls
    from domus.options import design

    low = round(ground_at(8.0, 10.0), 2)
    plain = {**BLOCK, "id": "W", "line_so": [[10.0, 6.0], [10.0, 12.0]], "retained_side": "left", "top": low + 1.0}
    built = {**plain, "depth_m": 0.3, "cap_depth_m": 0.26, "cap_overhang_m": 0.025, "embed_m": 0.1, "embed_per_m": 0.125}
    a, b = (design.Design({"walls": [cfg]}, site=project).wall_levels()[0] for cfg in (plain, built))
    walls.set_out([a])
    walls.set_out([b])
    datum = a["set_out"]["datum"]
    assert a["set_out"]["caps"] == b["set_out"]["caps"] and datum == b["set_out"]["datum"]
    sunk = np.maximum(0.1, 0.125 * (b["top"] - b["toe"]))
    on_course = datum - np.ceil((datum - (b["toe"] - sunk)) / 0.2 - 1e-9) * 0.2     # the course at or below that
    assert np.all(on_course <= b["toe"] - sunk + 1e-9) and np.all(on_course > b["toe"] - sunk - 0.2)
    fr = design.frame(project)
    for w, body, cap, out, foot in ((a, 0.27, 0.27, 0.0, a["toe"] - 0.15), (b, 0.3, 0.26, 0.025, on_course)):
        m, info = walls.mesh(w)
        off = fr.so(m.vertices[:, :2])[:, 0] - 10.0
        back = off * np.sign(off[np.abs(off).argmax()])                       # how far behind its face
        top = np.abs(m.vertices[:, 2] - m.vertices[:, 2].max()) < 1e-9
        assert back.max() == pytest.approx(body) and back.min() == pytest.approx(-out, abs=1e-9)
        assert back[top].min() == pytest.approx(-out, abs=1e-9) and back[top].max() == pytest.approx(cap - out)
        assert m.vertices[:, 2].min() == pytest.approx(foot.min())
        assert info["height_max_m"] == pytest.approx((w["top"] - w["toe"]).max())    # what shows is the same
    # One whole course buried, where that's more than the rule
    deep = design.Design({"walls": [{**built, "embed_courses": 1}]}, site=project).wall_levels()[0]
    walls.set_out([deep])
    assert deep["embedment"] == pytest.approx(np.maximum(sunk, 0.2))
    assert walls.mesh(deep)[0].vertices[:, 2].min() <= (deep["toe"] - 0.2).min() + 1e-9
