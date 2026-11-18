"""Wall options as closed solids (domus.options.solids), on the demo site: an option's designed ground, walls, steps and
the soil it cuts and fills, built from its own shapes with Manifold, stand where the point rule (domus.options.scheme)
puts the ground and measure what it measures."""

import numpy as np
import pytest

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
    return d, walls, solids.option_solids(d, walls, ctx.existing_walls)


def point_rule(d, walls, existing_walls, step=0.05):
    """Cut and fill as scheme.evaluate counts them, on a finer grid: the demo's walls and terraces lie on its cells'
    edges."""
    e, n = scheme.grid_in(d.garden, step)
    dz = scheme.soil_change(d, walls, existing_walls, e, n)
    return float(-dz[dz < 0].sum() * step * step), float(dz[dz > 0].sum() * step * step)


@pytest.mark.parametrize("name", ["a-one-wall", "b-two-tiers", "steps", "sloping-foot", "paving-and-steep",
                                  "keeps-the-old-wall", "sketch"])
def test_an_options_solids_stand_where_the_point_rule_puts_the_ground_and_measure_what_it_does(name, project, ctx):
    d, walls, s = worked_out(name, project, ctx)
    for part in [s.existing, *s.designed, *s.walls, *s.steps, *s.cut, *s.fill]:
        assert part.status() == solids.mf.Error.NoError and part.volume() > -1e-9
    cut, fill = point_rule(d, walls, ctx.existing_walls)
    v = s.volumes()
    within = 0.0005 * (cut + fill)                    # 0.05% of the soil moved
    assert v["cut_m3"] == pytest.approx(cut, abs=within) and v["fill_m3"] == pytest.approx(fill, abs=within)
    e, n = scheme.grid_in(d.garden, 0.5)
    gap = np.abs(s.height(e, n) - d.sample(e, n))
    assert np.isfinite(gap).all() and gap.max() < 0.002


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
