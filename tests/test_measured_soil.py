"""An option's cut and fill are its solids' (domus.options.solids), on the demo site: what the build's table shows,
what site.evaluate gives and the soil in its materials list are the same numbers, and a sketch's numbers from the server
are its solids' with the preview's cones."""

import numpy as np
import pytest

from domus import options
from domus.options import design, preview, scheme, sketch, solids


@pytest.fixture(scope="module")
def ctx(project):
    return design.context(project)


def built_wall(project, ctx):
    """A wall of named blocks that says how it's built, holding a lawn: in its trench, with soil over its drainage."""
    en = ctx.frame.en(np.array([[8.0, 10.0]]))
    low = round(float(ctx.ground.sample(en[:, 0], en[:, 1])[0]), 2)
    return {"id": "built", "name": "Built", "short": "Built", "description": "", "path": "options/built.toml",
            "walls": [{"id": "W", "line_so": [[10.0, 6.0], [10.0, 18.0]], "material": "segmental", "unit_m": [0.457, 0.2],
                       "cap_m": 0.1, "thickness_m": 0.2285, "depth_m": 0.3, "embed_m": 0.1, "embed_per_m": 0.125,
                       "bedding_m": 0.03, "base_m": 0.15, "trench_m": 0.6, "seal_m": 0.2}],
            "terraces": [{"id": "upper", "level": low + 0.68, "at_so": [12.0, 10.0], "polygon_so": [[10, 4], [14, 4], [14, 18], [10, 18]]}]}


def measured(res, ctx, segments=solids.SEGMENTS):
    s = solids.option_solids(res["design"], [w["def"] for w in res["walls"]], ctx.existing_walls, segments=segments)
    seal = sum(p.volume() for what, part, p in s.named if what == "fill" and part.startswith("seal"))
    return s.volumes(), seal


def soil_rows(res):
    return {row["item"]: row["net"] for row in res["materials"]["rows"] if row["group"] == "Soil"}


@pytest.mark.parametrize("which", ["a-one-wall", "b-two-tiers", "built"])
def test_an_options_cut_and_fill_are_its_solids(which, project, ctx):
    """The table's cut and fill, and so the fill less the cut, are the option's solids', and the soil in its materials
    list is the same: cut, fill, fill less cut, and the soil over a wall's drainage, part of the fill."""
    opt = built_wall(project, ctx) if which == "built" else next(o for o in options.load_all(project) if o["id"] == which)
    res = design.evaluate(project, opt)
    v, seal = measured(res, ctx)
    vol = res["volumes"]
    assert (vol["cut_m3"], vol["fill_m3"]) == (round(v["cut_m3"], 1), round(v["fill_m3"], 1))
    assert vol["net_m3"] == round(v["fill_m3"] - v["cut_m3"], 1)
    rows = soil_rows(res)
    assert rows["Cut"] == pytest.approx(vol["cut_m3"], abs=0.051) and rows["Fill"] == pytest.approx(vol["fill_m3"], abs=0.051)
    assert rows["Fill less cut"] == pytest.approx(vol["net_m3"], abs=0.051)
    if which == "built":
        assert seal > 0 and rows["Low-permeability soil over the drainage aggregate"] == pytest.approx(seal, abs=0.051)
    # what isn't soil is worked out as it was: the grid still gives the area disturbed and the deepest cut and fill
    grid = scheme.evaluate(opt, ctx)["volumes"]
    assert {k: grid[k] for k in ("area_m2", "cut_max_m", "fill_max_m", "tolerance_m3")} == \
        {k: vol[k] for k in ("area_m2", "cut_max_m", "fill_max_m", "tolerance_m3")}


def test_on_the_grid_an_option_is_worked_out_as_before(project, ctx):
    """segments=0 reads the soil on the grid, as scheme.evaluate does without solids: for what needs an option worked
    out but not its soil (the build's first look at its options, the checks)."""
    opt = next(o for o in options.load_all(project) if o["id"] == "a-one-wall")
    assert design.evaluate(project, opt, segments=0)["volumes"] == scheme.evaluate(opt, ctx)["volumes"]


def test_a_sketchs_numbers_are_its_solids_with_the_previews_cones(project, ctx):
    """What the server says a sketch comes to once it settles is its solids' cut and fill, with the preview's cones
    (preview.PREVIEW_SEGMENTS), so it says what the preview showed."""
    en = ctx.frame.en(np.array([[10.0, 6.0], [10.0, 18.0]]))
    top = round(float(np.max(ctx.ground.sample(en[:, 0], en[:, 1]))) + 0.6, 2)
    items = [{"id": 1, "kind": "wall", "name": "W", "en": en.tolist(), "material": "timber", "mode": "level",
              "tops": [top, top], "top": top, "retains": "right", "back": 3.0}]
    said = sketch.evaluate(project, items)["volumes"]
    res = design.evaluate(project, sketch.to_option(project, items), step=0.25, segments=0)
    v, _ = measured(res, ctx, preview.PREVIEW_SEGMENTS)
    assert (said["cut_m3"], said["fill_m3"]) == (round(v["cut_m3"], 1), round(v["fill_m3"], 1)) and said["fill_m3"] > 0
