"""The viewer's sketch preview: app/viewer/sketch-core.js (in node) works each sketch in tests/sketch_corpus.json out on
the made-up demo's built model as its Python reference (domus.options.preview) does, and the reference measures what
the build's own rule (domus.options.scheme) does."""

import json
import subprocess
from pathlib import Path

import numpy as np
import pytest
import shapely
from shapely.geometry import MultiPoint, box

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


def levels_on(tris, pts, z, e, n):
    """Levels at points (e, n), linear on triangles (each three corners of `pts` by index, with their levels `z`)."""
    out = np.full(len(e), np.nan)
    for a, b, c in tris:
        (ax, ay), (bx, by), (cx, cy) = pts[a], pts[b], pts[c]
        den = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy)
        u = ((by - cy) * (e - cx) + (cx - bx) * (n - cy)) / den
        v = ((cy - ay) * (e - cx) + (ax - cx) * (n - cy)) / den
        inside = (u >= -1e-9) & (v >= -1e-9) & (1.0 - u - v >= -1e-9)
        out[inside] = (u * z[a] + v * z[b] + (1.0 - u - v) * z[c])[inside]
    return out


def test_the_preview_levels_a_terrace_as_the_build_does(tmp_path):
    """A terrace with its own level at each corner is linear on its corners' triangles, which scheme.Terrace has from
    geo.mesh.delaunay_by_height: Delaunay's, worked out about its first corner, but where four corners lie within a few
    millimetres of one circle, cut along the diagonal whose ends are closer in height. The viewer's copy finds the same
    triangles, so the same levels: on corners scattered at random on a site's grid, across 3 to 60 m, and where four
    lie on one circle, level, on one plane or on none. Where five or more lie on one circle, which way the build cuts
    them depends on how Qhull first did, so the copy finds none and the preview says it can't show that terrace."""
    rng = np.random.default_rng(7)
    sets = []
    for k in (3, 4, 5, 6, 8, 12, 20, 30):
        for size in (3.0, 8.0, 20.0, 60.0):
            for _ in range(3):
                at = np.array([1500000.0, 5500000.0]) + rng.uniform(-3000.0, 3000.0, 2)
                sets.append({"pts": (at + rng.uniform(0.0, size, (k, 2))).tolist(), "z": rng.uniform(40.0, 43.0, k).tolist()})
    square = [[1500000.0, 5500000.0], [1500004.0, 5500000.0], [1500004.0, 5500004.0], [1500000.0, 5500004.0], [1500002.0, 5500009.0]]
    near = [[1500000.0, 5500000.0], [1500004.0, 5500000.0], [1500003.9991, 5500004.0], [1500000.0, 5500004.0], [1500002.0, 5500009.0]]
    sets += [{"pts": square, "z": [41.0, 41.0, 41.0, 41.0, 41.5]},       # four on one circle, level
             {"pts": square, "z": [41.0, 41.2, 41.2, 41.0, 41.5]},       # four on one circle, on one plane
             {"pts": square, "z": [41.0, 41.2, 41.0, 41.2, 41.5]},       # four on one circle, on no one plane: as close
             {"pts": square, "z": [41.0, 41.0, 41.3, 41.0, 41.5]},       # and 1-3 closer in height
             {"pts": near, "z": [41.0, 41.0, 41.3, 41.0, 41.5]}]         # a corner 0.9 mm in from the circle
    ring = np.radians([90, 162, 234, 306, 18])
    five = {"pts": (np.array([1500000.0, 5500000.0]) + 3.0 * np.column_stack([np.cos(ring), np.sin(ring)])).tolist(),
            "z": [41.0, 41.4, 41.1, 41.3, 41.2]}                          # five on one circle, on no one plane
    (tmp_path / "sets.json").write_text(json.dumps([*sets, five]), encoding="utf-8")
    out = subprocess.run(["node", str(HERE / "delaunay_check.mjs"), str(VIEWER / "sketch-core.js"), str(tmp_path / "sets.json")],
                         capture_output=True, text=True, encoding="utf-8", check=False, timeout=300)
    assert out.returncode == 0, out.stderr
    found = [json.loads(line) for line in out.stdout.splitlines()]
    assert len(found) == len(sets) + 1
    for s, tris in zip(sets, found, strict=False):
        assert tris is not None
        pts = np.asarray(s["pts"])
        terrace = scheme.Terrace({"id": "t", "polygon": [[*p, z] for p, z in zip(pts.tolist(), s["z"], strict=True)]},
                                 {"batter": 1.0}, None)
        assert {frozenset(t) for t in tris} == {frozenset(map(int, t)) for t in terrace.triangles}
        x0, y0 = pts.min(axis=0)
        x1, y1 = pts.max(axis=0)
        e, n = (v.ravel() for v in np.meshgrid(np.linspace(x0, x1, 60), np.linspace(y0, y1, 60)))
        inside = shapely.contains_xy(MultiPoint(pts).convex_hull.buffer(-1e-6), e, n)
        ours = levels_on(tris, pts - pts[0], s["z"], e - pts[0][0], n - pts[0][1])
        assert np.isfinite(ours[inside]).all()
        assert np.nanmax(np.abs(ours[inside] - terrace.z(e[inside], n[inside]))) < 1e-9
    assert found[-1] is None


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

