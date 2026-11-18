"""Wall options as closed solids (domus.options.solids), on the demo site: an option's designed ground, walls, steps and
the soil it cuts and fills, built from its own shapes with Manifold, stand where the point rule (domus.options.scheme)
puts the ground and measure what it measures."""

import itertools
import json

import numpy as np
import pytest
import shapely
from shapely.geometry import Polygon

from domus.model import walls as walls_mod
from domus.options import design, scheme, sketch, solids
from domus.project.demo import X0, Y0


@pytest.fixture(scope="module")
def ctx(project):
    return design.context(project)


def ground_at(ctx, s, o):
    en = ctx.frame.en(np.array([[s, o]]))
    return float(ctx.ground.sample(en[:, 0], en[:, 1])[0])


def option(name, project, ctx):
    """The demo's own options, and made-up ones that use the rest of the rules."""
    if name in ("a-one-wall", "b-two-tiers"):
        from domus import options

        return next(o for o in options.load_all(project) if o["id"] == name)
    low = round(ground_at(ctx, 8.0, 10.0), 2)
    wall = {"id": "W", "line_so": [[10.0, 6.0], [10.0, 18.0]]}
    upper = {"id": "upper", "level": low + 0.68, "at_so": [12.0, 10.0], "polygon_so": [[10, 4], [14, 4], [14, 18], [10, 18]]}
    if name == "steps":         # two lawns with a wall between them and steps beside its end: their batters disagree
        return {"walls": [wall], "stairs": [{"id": "S", "line_so": [[8.8, 5.0], [10.0, 5.0]], "width_m": 1.0}],
                "terraces": [{"id": "lower", "level": low, "at_so": [8.0, 10.0], "polygon_so": [[6, 4], [10, 4], [10, 18], [6, 18]]},
                             upper]}
    if name == "sloping-foot":  # steps on ground that slopes across their foot
        return {"walls": [wall], "terraces": [upper],
                "stairs": [{"id": "S", "line_so": [[8.8, 5.0], [10.0, 5.0]], "width_m": 1.0, "foot": [low - 0.1, low + 0.15]}]}
    if name == "paving-and-steep":   # a paved terrace, and a lawn falling 1:10 with no wall, past the old wall
        return {"terraces": [
            {"id": "P", "kind": "paving", "level": low + 0.3, "at_so": [5.0, 6.0], "polygon_so": [[3, 3], [7, 3], [7, 9], [3, 9]]},
            {"id": "L", "level": low + 0.5, "at_so": [11.0, 14.0], "fall": "1:10", "fall_to": 30,
             "polygon_so": [[9, 11], [13, 11], [14, 17], [9, 18]]}]}
    if name.startswith("steep-batter"):     # a terrace's batters so steep their cones reach metres below and above it
        return {"walls": [{"id": "W", "line_so": [[8.0, 2.0], [8.0, 20.0]]}],
                "terraces": [{"id": "T", "level": low + 0.6, "at_so": [11.0, 11.0], "batter": float(name.rsplit("-", 1)[1]),
                              "polygon_so": [[8, 2], [8, 20], [14, 20], [14, 2]]}]}
    if name == "own-levels":    # a lawn with its own level at each corner, its batters steep, held by a wall on one side
        return {"walls": [wall], "terraces": [{"id": "T", "batter": 1.0, "polygon_so": [
            [10, 4, low + 0.6], [14.1, 4.3, low + 0.66], [14.3, 17.7, low + 0.78], [10, 18, low + 0.7]]}]}
    if name == "built":         # a wall of named blocks that says how it's built: in a trench, with soil over its drainage
        return {"walls": [{**wall, "material": "segmental", "unit_m": [0.457, 0.2], "cap_m": 0.1, "thickness_m": 0.2285,
                           "depth_m": 0.3, "embed_m": 0.1, "embed_per_m": 0.125, "bedding_m": 0.03, "base_m": 0.15,
                           "trench_m": 0.6, "seal_m": 0.2}], "terraces": [upper]}
    if name == "built-in-courses":  # the same, its top stepped, its foot a whole course down and stepping down the slope a
        # course at a time with the ground, its cap set back and standing out (as a maker's guide gives such a wall)
        return {"walls": [{"id": "W", "line_so": [[10.0, 4.0], [10.0, 11.0], [10.0, 18.0]], "tops": [low + 0.8, low + 1.0],
                           "material": "segmental", "unit_m": [0.4, 0.2], "cap_m": 0.1, "depth_m": 0.3, "cap_depth_m": 0.27,
                           "cap_overhang_m": 0.025, "embed_courses": 1, "embed_m": 0.15, "base_m": 0.15, "trench_m": 0.6,
                           "seal_m": 0.2}], "terraces": [upper]}
    if name == "keeps-the-old-wall":
        return {"keep_existing": ["W1"], "walls": [{"id": "N", "line_so": [[12.0, 3.0], [12.0, 19.0]]}],
                "terraces": [{"id": "T", "level": ground_at(ctx, 10.0, 10.0) + 0.3, "at_so": [10.0, 10.0], "fall": "1:40",
                              "fall_to": "north", "polygon_so": [[6, 3], [12, 3], [12, 19], [6, 19]]}]}
    # a sketched wall with its own top at each corner and level ground behind it, as the viewer's sketch makes it
    item = {"id": 1, "kind": "wall", "name": "Sketched wall", "mode": "level", "retains": "right", "back": 3,
            "en": [[X0 + 4, Y0 + 20], [X0 + 14, Y0 + 20], [X0 + 16.5, Y0 + 18.2]], "tops": [42.35, 42.4, 42.45]}
    return sketch.to_option(project, [item])


def worked_out(name, project, ctx):
    d = scheme.Design(option(name, project, ctx), ctx.settings["defaults"], ctx.ground, ctx.garden, ctx.frame)
    walls = d.wall_levels()
    walls_mod.set_out(walls, ctx.ground)       # as scheme.evaluate does: a wall of named blocks steps with its courses
    return d, walls, solids.option_solids(d, walls, ctx.existing_walls)


def point_rule(d, walls, existing_walls, step=0.05):
    """Cut and fill as scheme.evaluate counts them, on a finer grid: the demo's walls and terraces lie on its cells'
    edges."""
    e, n = scheme.grid_in(d.garden, step)
    cut, fill = scheme.soil_moved(d, walls, existing_walls, e, n)
    return float(cut.sum() * step * step), float(fill.sum() * step * step)


def like_the_point_rule(d, walls, s, existing_walls):
    """The solids are closed, measure the cut and fill the point rule does to 0.05% of the soil moved, and stand within
    2 mm of the designed ground."""
    for part in [s.existing, *s.designed, *s.walls, *s.steps, *s.cut, *s.fill]:
        assert part.status() == solids.mf.Error.NoError and part.volume() > -1e-9
    cut, fill = point_rule(d, walls, existing_walls)
    v = s.volumes()
    within = 0.0005 * (cut + fill)                    # 0.05% of the soil moved
    assert v["cut_m3"] == pytest.approx(cut, abs=within) and v["fill_m3"] == pytest.approx(fill, abs=within)
    e, n = scheme.grid_in(d.garden, 0.5)
    gap = np.abs(s.height(e, n) - d.sample(e, n))
    assert np.isfinite(gap).all() and gap.max() < 0.002


@pytest.mark.parametrize("name", ["a-one-wall", "b-two-tiers", "steps", "sloping-foot", "paving-and-steep",
                                  "keeps-the-old-wall", "sketch", "steep-batter-0.5", "steep-batter-1.0", "own-levels", "built", "built-in-courses"])
def test_an_options_solids_stand_where_the_point_rule_puts_the_ground_and_measure_what_it_does(name, project, ctx):
    like_the_point_rule(*worked_out(name, project, ctx), ctx.existing_walls)


def test_beside_a_terrace_with_its_own_levels_its_batters_take_the_level_along_its_edge(project, ctx):
    """Just outside a terrace with its own level at each corner, all along it, the solids and the point rule put the
    ground in the same place: each batter starts from the terrace's level along its edge."""
    d, _, s = worked_out("own-levels", project, ctx)
    ring = np.asarray(d.terraces[0].poly.exterior.coords)[:, :2]
    pts = []
    for a, b in itertools.pairwise(ring):
        along = (b - a) / np.hypot(*(b - a))
        out = np.array([along[1], -along[0]])                  # to the right of the ring, which shapely runs clockwise
        out = out if not d.terraces[0].poly.contains(shapely.Point(*((a + b) / 2 + 0.05 * out))) else -out
        u = np.arange(0.005, np.hypot(*(b - a)), 0.01)
        pts += [a + u[:, None] * along + k * out for k in (0.03, 0.1)]
    e, n = np.vstack(pts).T
    keep = shapely.contains_xy(d.garden, e, n) & ~shapely.contains_xy(d.terraces[0].poly, e, n)
    gap = np.abs(s.height(e[keep], n[keep]) - d.sample(e[keep], n[keep]))
    assert keep.sum() > 1000 and np.isfinite(gap).all() and gap.max() < 0.002


BENDS = [(2, 22), (8, 22), (9, 21.2), (9.6, 21.7), (10.1, 22.6), (11, 22.2), (13, 22), (20, 22)]


@pytest.fixture(scope="module")
def leaning(tmp_path_factory):
    """The demo with its old wall bent through tight corners and leaning back a metre (batter_m 1.0), so the outline of
    its foot, half the lean in front of its line, folds back on itself at the corners."""
    from domus.project import demo
    from domus.project import demo as made_up
    from domus.project.site import Site

    root = tmp_path_factory.mktemp("leaning")
    demo.write(root)
    path = root / "data" / "site" / "walls-existing.geojson"
    fc = json.loads(path.read_text(encoding="utf-8"))
    wall = fc["features"][0]
    wall["geometry"]["coordinates"] = [[X0 + x, Y0 + y, round(float(made_up.ground(X0 + x, Y0 + y)) + 0.7, 2)] for x, y in BENDS]
    wall["properties"].update(batter_m=1.0, thickness_m=0.3)
    path.write_text(json.dumps(fc), encoding="utf-8", newline="\n")
    return design.context(Site(root))


def over_the_bends(name, ctx):
    """Options that take the leaning wall away under a terrace over its bends, raised or lowered, or keep it with a new
    wall across it."""
    at = ground_at(ctx, 8.0, 11.0)
    over = [[5, 6], [11, 6], [11, 16], [5, 16]]
    if name == "taken-away-under-fill":
        return {"terraces": [{"id": "T", "level": at + 0.4, "at_so": [8.0, 11.0], "polygon_so": over}]}
    if name == "taken-away-under-cut":
        return {"terraces": [{"id": "T", "level": at - 0.3, "at_so": [8.0, 11.0], "polygon_so": over}]}
    return {"keep_existing": ["W1"], "walls": [{"id": "N", "line_so": [[6.0, 12.0], [11.0, 12.0]]}],
            "terraces": [{"id": "T", "level": ground_at(ctx, 8.5, 14.0) + 0.3, "at_so": [8.5, 14.0],
                          "polygon_so": [[6, 12], [11, 12], [11, 17], [6, 17]]}]}


@pytest.mark.parametrize("name", ["taken-away-under-fill", "taken-away-under-cut", "kept-and-crossed"])
def test_an_old_walls_soil_is_at_its_toe_where_the_outline_of_its_foot_folds(name, leaning):
    face, back, _, _ = scheme.wall_edges(leaning.existing_walls[0], leaning.ground)
    assert not Polygon(np.vstack([face, back[::-1]])).is_valid
    d = scheme.Design(over_the_bends(name, leaning), leaning.settings["defaults"], leaning.ground, leaning.garden,
                      leaning.frame)
    walls = d.wall_levels()
    like_the_point_rule(d, walls, solids.option_solids(d, walls, leaning.existing_walls), leaning.existing_walls)


def test_the_walls_and_steps_are_solids_of_their_own_size(project, ctx):
    d, walls, s = worked_out("steps", project, ctx)
    res = scheme.evaluate(option("steps", project, ctx), ctx)
    w = res["walls"][0]
    assert s.walls[0].volume() == pytest.approx(w["face_area_m2"] * walls[0]["thickness"], rel=1e-3)   # a straight wall
    st = d.stairs[0]
    going = st.length / st.n
    treads = sum(going * st.width * (top - (st.foot + st.rise / st.n * i - 0.3)) for i, top in enumerate(st.tops()))
    assert sum(t.volume() for t in s.steps) == pytest.approx(treads, rel=1e-6)


def test_a_surface_that_touches_itself_at_a_corner_closes_into_a_solid():
    """Two triangles a metre up that meet only at a corner: each takes its own copy of it."""
    v = np.array([[0, 0, 1], [1, 0, 1], [1, 1, 1], [2, 1, 1], [2, 2, 1]], float)
    m = solids.close_surface(v, [[0, 1, 2], [2, 3, 4]], bottom=0.0)
    assert m.volume() == pytest.approx(1.0)


@pytest.mark.parametrize("side", [1.0, -1.0])
def test_a_strip_is_a_solid_whichever_side_its_back_is(side):
    face = np.array([[0.0, 0.0], [1.0, 0.0], [2.0, 0.0]])
    back = face + [0.0, side * 0.5]
    m = solids.strip(face, back, top=[2.0, 2.5, 3.0], bottom=1.0)
    assert m.volume() == pytest.approx(0.5 * (1.0 * 1.25 + 1.0 * 1.75))
