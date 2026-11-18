"""The viewer's sketch preview: app/viewer/sketch-core.js (in node) works each sketch in tests/sketch_corpus.json out on
the made-up demo's built model as its Python reference (domus.options.preview) does, and the reference measures what
the build's own rule (domus.options.scheme) does."""

import json
import subprocess
from pathlib import Path

import numpy as np
import pytest
from shapely.geometry import box

from domus.options import design, preview, scheme, sketch, solids

HERE = Path(__file__).parent
VIEWER = HERE.parent / "src" / "domus" / "app" / "viewer"
CORPUS = json.loads((HERE / "sketch_corpus.json").read_text(encoding="utf-8"))
DRAWN = [sk for sk in CORPUS["sketches"] if sk.get("preview") != "not yet"]


@pytest.fixture(scope="module")
def model(built):
    return json.loads((built / "model.json").read_text(encoding="utf-8"))


def given(model, sk):
    """The model as a sketch is worked out on: any `defaults` it gives laid over the model's."""
    return {**model, "regrade": {**model["regrade"], "defaults": {**model["regrade"]["defaults"], **sk.get("defaults", {})}}}


def in_node(built, segments, conflict_step):
    """Each corpus sketch as the viewer's copy works it out, by its name."""
    out = subprocess.run(["node", str(HERE / "sketch_preview_check.mjs"), str(VIEWER / "sketch-core.js"),
                          str(VIEWER / "vendor" / "manifold-3d" / "manifold.js"), str(built / "model.json"),
                          str(HERE / "sketch_corpus.json"), str(segments), str(conflict_step)],
                         capture_output=True, text=True, encoding="utf-8", check=False, timeout=900)
    assert out.returncode == 0, out.stderr
    return {row["name"]: row for row in map(json.loads, out.stdout.splitlines())}


@pytest.mark.parametrize(("segments", "conflict_step"), [(preview.PREVIEW_SEGMENTS, solids.CONFLICT_STEP_M),
                                                       (preview.DRAG_SEGMENTS, preview.DRAG_CONFLICT_STEP_M)],
                         ids=["as-drawn", "while-dragged"])
def test_the_preview_works_a_sketch_out_as_its_python_reference_does(built, model, segments, conflict_step):
    """Cut and fill to a few millionths of a cubic metre: the same solids, from the same model, by the same steps, as
    drawn and while dragged."""
    js = in_node(built, segments, conflict_step)
    for sk in CORPUS["sketches"]:
        row = js[sk["name"]]
        assert "error" not in row, row.get("error")
        if sk.get("preview") == "not yet":
            assert row.get("unsupported"), sk["name"]
            continue
        v = preview.sketch_solids(given(model, sk), sk["items"], segments, conflict_step)[0].volumes()
        assert row["cut_m3"] == pytest.approx(v["cut_m3"], abs=3e-6), sk["name"]
        assert row["fill_m3"] == pytest.approx(v["fill_m3"], abs=3e-6), sk["name"]


@pytest.mark.parametrize("sk", DRAWN, ids=[sk["name"] for sk in DRAWN])
def test_the_preview_measures_what_the_build_does(sk, model, project):
    """With the build's cones (solids.SEGMENTS), the preview's solids stand where its own option puts the ground, to
    2 mm, and cut and fill what the build's rule counts on the site, on a 2.5 cm grid over the regrade area (whose
    cells' edges the demo's sketches lie on, as test_solids's options do), to 0.05% of the soil moved. The rule reads
    the ground cell by cell, and where all the soil a sketch moves is in a wall's own body, a few cells wide, that
    alone can be a litre or so out: hence the floor of 2 litres."""
    s, d, walls = preview.sketch_solids(given(model, sk), sk["items"], segments=solids.SEGMENTS)
    window = solids.option_window(d, walls)
    e, n = scheme.grid_in(d.garden.intersection(box(*window)), 0.5)
    gap = np.abs(s.height(e, n) - d.sample(e, n))
    assert np.isfinite(gap).all() and gap.max() < 0.002

    ctx = design.context(project)
    defaults = {**ctx.settings["defaults"], **sk.get("defaults", {})}
    rule = scheme.Design(sketch.to_option(project, sk["items"]), defaults, ctx.ground, ctx.garden, ctx.frame)
    rule_walls = rule.wall_levels()
    e, n = scheme.grid_in(rule.garden, 0.025)
    cut, fill = (float(np.nansum(v) * 0.025 ** 2) for v in scheme.soil_moved(rule, rule_walls, ctx.existing_walls, e, n))
    v = s.volumes()
    within = max(0.0005 * (cut + fill), 0.002)
    assert v["cut_m3"] == pytest.approx(cut, abs=within) and v["fill_m3"] == pytest.approx(fill, abs=within)

