"""An option's ground drawn from its solids (domus.options.solids.option_ground, surface and top), on the demo site: it
stands where the point rule puts the ground (scheme.Design.drawn) over the regrade area, to a millimetre, it is the
ground as it is outside it, it covers the patch the build draws it on, and a lawn laid on it lies on its terrace. Drawn
less its walls, as the build draws it, it has nothing under them and no face in theirs."""

import json
import shutil

import numpy as np
import pytest
import shapely
from shapely.geometry import LineString

from domus import options
from domus.geo.mesh import Surface
from domus.model import terrain
from domus.model import walls as walls_mod
from domus.options import design, solids


@pytest.fixture(scope="module")
def drawn(project):
    return terrain.drawn(project)


def patch_of(d):
    bb = np.array([d.garden.bounds, *[LineString(w["line"]).bounds for w in d.walls]])
    return [round(v * 2) / 2 for v in (bb[:, 0].min() - 1.5, bb[:, 1].min() - 1.5, bb[:, 2].max() + 1.5, bb[:, 3].max() + 1.5)]


@pytest.mark.parametrize("which", ["a-one-wall", "b-two-tiers"])
def test_an_options_ground_is_drawn_where_the_rule_puts_it(which, project, drawn):
    opt = next(o for o in options.load_all(project) if o["id"] == which)
    res = design.evaluate(project, opt, keep_solids=True)
    d, s = res["design"], res["solids"]
    patch = patch_of(d)
    whole = solids.option_ground(s, drawn.vertices, drawn.faces, patch, d.garden)
    v, f = solids.surface(whole, s.origin, patch)
    rng = np.random.default_rng(4)
    e = rng.uniform(patch[0] + 0.01, patch[2] - 0.01, 4000)
    n = rng.uniform(patch[1] + 0.01, patch[3] - 0.01, 4000)
    h = Surface(v, f).z(e, n)
    assert np.isfinite(h).all()                                  # it covers the patch, with no holes
    inside = shapely.contains_xy(d.garden.buffer(-0.01), e, n)
    outside = ~shapely.contains_xy(d.garden.buffer(0.01), e, n)
    assert np.abs(h[inside] - d.drawn(e[inside], n[inside])).max() < 0.001
    assert np.abs(h[outside] - Surface(drawn.vertices, drawn.faces).z(e[outside], n[outside])).max() < 1e-6
    sides = np.zeros(len(f), bool)                               # nothing on the patch's sides: only its top shows
    for axis, at in ((0, patch[0]), (0, patch[2]), (1, patch[1]), (1, patch[3])):
        sides |= np.all(np.abs(v[f][:, :, axis] - at) < 1e-6, axis=1)
    assert not sides.any()
    for t in d.terraces:                                         # a lawn laid on it lies on its terrace
        tv, tf = solids.top(whole, s.origin, t.poly.intersection(d.garden).buffer(-0.05))
        assert len(tf) and np.abs(tv[:, 2] - t.z(tv[:, 0], tv[:, 1])).max() < 0.001


def walls_cut(res, d, s, whole):
    """The cut the build takes of an option's ground for its walls and flights (build.option_layers), and its walls as
    walls_cut and surface take them (solids.standing)."""
    levels = [walls_mod.wall_levels(w["def"]) for w in res["walls"]]        # an option's walls are levelled: their toe given
    standing = [solids.standing(w["mesh"].vertices, w["mesh"].faces, pts, chain, toe, top, getattr(w["mesh"], "soil_over", ()),
                                getattr(w["mesh"], "soil_over_m", 0.0))
                for w, (pts, _, chain, toe, top) in zip(res["walls"], levels, strict=True)]
    return solids.walls_cut(standing, d.stairs, s.origin, float(whole.bounding_box()[5]) + 5.0), standing


@pytest.mark.parametrize("which", ["a-one-wall", "b-two-tiers"])
def test_drawn_less_its_walls_nothing_stands_in_them_and_no_face_lies_in_theirs(which, project, drawn):
    """Where a wall stands it covers the ground, and the rule's ground steps at its face: drawn less what the walls take
    (as the build draws it), nothing of the ground stands over a wall's toe within its drawn plan, none of its faces
    lies on a wall's outline, and everywhere else it's where it was."""
    opt = next(o for o in options.load_all(project) if o["id"] == which)
    res = design.evaluate(project, opt, keep_solids=True)
    d, s = res["design"], res["solids"]
    patch = patch_of(d)
    whole = solids.option_ground(s, drawn.vertices, drawn.faces, patch, d.garden, d.stairs)
    cut, standing = walls_cut(res, d, s, whole)
    stood = shapely.unary_union([plan for plan, _, _ in standing])
    v, f = solids.surface(whole, s.origin, patch, cut, standing)
    corners = v[f]
    n = np.cross(corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0])
    upright = np.abs(n[:, 2]) <= solids.UPRIGHT * np.linalg.norm(n, axis=1)
    mid = corners.mean(axis=1)
    assert upright.any() and (shapely.distance(stood.boundary, shapely.points(mid[upright, :2])) > 1e-4).all()
    inside = shapely.contains_xy(stood.buffer(-0.001), mid[:, 0], mid[:, 1])
    for w in res["walls"]:
        # Within a wall's drawn plan, no corner of the ground over the wall's top where it is along the wall, and none
        # over its toe by more than the cut's floor can stand: that floor is read at the corners of its outline, 5 cm
        # apart, and is flat between them, so where the toe bends it can stand a little over it, inside the wall.
        pts, _, chain, toe, top = walls_mod.wall_levels(w["def"])
        near = inside & shapely.contains_xy(solids.footprint(w["mesh"].vertices, w["mesh"].faces), mid[:, 0], mid[:, 1])
        at = corners[near].reshape(-1, 3)
        c = shapely.line_locate_point(shapely.LineString(pts[:, :2]), shapely.points(at[:, 0], at[:, 1]))
        assert near.any() and (at[:, 2] <= np.interp(c, chain, top)).all() and (at[:, 2] <= np.interp(c, chain, toe) + 0.01).all()
    rng = np.random.default_rng(5)
    e = rng.uniform(patch[0] + 0.01, patch[2] - 0.01, 4000)
    n = rng.uniform(patch[1] + 0.01, patch[3] - 0.01, 4000)
    away = ~shapely.contains_xy(stood.buffer(0.01), e, n)
    v0, f0 = solids.surface(whole, s.origin, patch)
    assert np.abs(Surface(v, f).z(e[away], n[away]) - Surface(v0, f0).z(e[away], n[away])).max() < 1e-6


@pytest.mark.parametrize("which", ["a-one-wall", "b-two-tiers"])
def test_it_stands_on_one_bottom_and_every_upright_face_drawn_shows(which, project, drawn):
    """The ground with an option stands on one flat bottom: its pieces each had their own, and where two met at
    different depths the step between them was drawn, under the ground's top. And every upright face drawn shows: by
    some corner of it, the ground's top on one side stands lower than that corner, as at the regrade area's edge. An
    upright face with the top as high as each of its corners on both sides lies inside the ground and isn't drawn. (A
    sliver under a millimetre across draws nothing at the millimetre model.json writes to.)"""
    opt = next(o for o in options.load_all(project) if o["id"] == which)
    res = design.evaluate(project, opt, keep_solids=True)
    d, s = res["design"], res["solids"]
    patch = patch_of(d)
    whole = solids.option_ground(s, drawn.vertices, drawn.faces, patch, d.garden, d.stairs)
    m = whole.to_mesh64()
    t = np.asarray(m.vert_properties, float)[:, :3][np.asarray(m.tri_verts, np.int64).reshape(-1, 3)]
    n = np.cross(t[:, 1] - t[:, 0], t[:, 2] - t[:, 0])
    down = n[:, 2] < -0.5 * np.linalg.norm(n, axis=1)                  # facing down: its bottom
    assert down.any() and np.allclose(t[down][:, :, 2], whole.bounding_box()[2])
    v, f = solids.surface(whole, s.origin, patch)
    c = v[f]
    n = np.cross(c[:, 1] - c[:, 0], c[:, 2] - c[:, 0])
    size = np.linalg.norm(n, axis=1)
    across_least = size / np.max(np.linalg.norm(c - np.roll(c, 1, axis=1), axis=2), axis=1)        # its least height
    upright = (np.abs(n[:, 2]) <= solids.UPRIGHT * size) & (across_least >= 1e-3)
    top = Surface(v, f[n[:, 2] > solids.UPRIGHT * size])
    u = c[upright]
    across = n[upright, :2] / np.linalg.norm(n[upright, :2], axis=1)[:, None] * 2e-4
    near = u + 0.01 * (u.mean(axis=1)[:, None, :] - u)
    lower = np.fmin(*[top.z((near[:, :, 0] + k * across[:, [0]]).ravel(), (near[:, :, 1] + k * across[:, [1]]).ravel()).reshape(-1, 3)
                      for k in (1, -1)])
    assert upright.any() and (near[:, :, 2] > lower + 1e-5).any(axis=1).all()


def test_inside_a_walls_body_its_floor_isnt_drawn_but_under_a_cap_and_under_the_soil_it_is(project, drawn):
    """Where a wall stands, the ground is cut down to its floor (walls_cut). Inside the wall's body, over the plan its
    mesh stands on (solids.footing) and under its head, nothing of that floor shows, and it isn't drawn (surface's
    `inside`), as for a made-up block wall holding a lawn. Beside it a made-up capped block wall: under its capping
    course, standing out from its face, the floor is the ground at its toe, seen under the cap, and where the soil
    covers its body behind the shallower cap it's the soil. Both are still drawn, as is everything away from the walls."""
    low = round(ground_at(project, 8.0, 10.0), 2)
    walls = [{"id": "P", "material": "block", "line_so": [[10.0, 4.5], [10.0, 9.5]], "retained_side": "left", "top": low + 0.9},
             {**BLOCK, "id": "C", "line_so": [[10.0, 11.0], [10.0, 16.0]], "retained_side": "left", "top": low + 0.9}]
    opt = made_up("bodies", walls=walls, terraces=[{"id": "upper", "level": low + 0.8, "at_so": [12.0, 10.0],
                                                    "polygon_so": [[10, 4], [14, 4], [14, 17], [10, 17]]}])
    res = design.evaluate(project, opt, keep_solids=True)
    d, s = res["design"], res["solids"]
    patch = patch_of(d)
    whole = solids.option_ground(s, drawn.vertices, drawn.faces, patch, d.garden, d.stairs)
    cut, standing = walls_cut(res, d, s, whole)
    bodies = []
    for w, (_, head, _) in zip(res["walls"], standing, strict=True):
        pts, _, chain, toe, _ = walls_mod.wall_levels(w["def"])
        bodies.append((solids.footing(w["mesh"].vertices, w["mesh"].faces, pts, chain, toe), head))
    v0, f0 = solids.surface(whole, s.origin, patch, cut, standing)
    v1, f1 = solids.surface(whole, s.origin, patch, cut, standing, bodies)

    def looking_up(v, f):
        c = v[f]
        n = np.cross(c[:, 1] - c[:, 0], c[:, 2] - c[:, 0])
        return c[n[:, 2] > solids.UPRIGHT * np.linalg.norm(n, axis=1)]

    def inside(mid, body, head):
        return shapely.contains_xy(body.buffer(-1e-3), mid[:, 0], mid[:, 1]) & (mid[:, 2] < head(mid[:, 0], mid[:, 1]) - 1e-3)
    up0, up1 = looking_up(v0, f0), looking_up(v1, f1)
    mid0, mid1 = up0.mean(axis=1), up1.mean(axis=1)
    (plain, plain_head), (capped, _) = bodies
    assert inside(mid0, plain, plain_head).any() and not inside(mid1, plain, plain_head).any()
    plan, _, parts = standing[1]
    buried = parts[1][0]                                       # behind the shallower cap, under the soil
    for region in (plan.difference(capped), buried):           # under the cap standing out from the face; the soil
        k0, k1 = (int(shapely.intersects(region.buffer(-1e-4), shapely.polygons(u[:, :, :2])).sum()) for u in (up0, up1))
        assert k0 and k1 == k0
    rng = np.random.default_rng(7)
    e = rng.uniform(patch[0] + 0.01, patch[2] - 0.01, 4000)
    n = rng.uniform(patch[1] + 0.01, patch[3] - 0.01, 4000)
    away = ~shapely.contains_xy(shapely.unary_union([p for p, _, _ in standing]).buffer(0.01), e, n)
    assert np.abs(Surface(v1, f1).z(e[away], n[away]) - Surface(v0, f0).z(e[away], n[away])).max() < 1e-9
    assert len(f1) < len(f0)


def test_under_a_flight_it_is_the_ramp_under_the_treads(project, drawn):
    """Under a flight of steps on a foot that slopes across it, the ground is drawn where the rule draws it, the ramp
    just under the treads (Stair.under), not the soil the flight stands on, which is carried in level from its foot
    and would come through the lowest treads."""
    en = design.frame(project).en(np.array([[8.0, 10.0]]))
    low = round(float(terrain.ground(project).sample(en[:, 0], en[:, 1])[0]), 2)
    opt = {"id": "x", "name": "x",
           "walls": [{"id": "W", "line_so": [[10.0, 6.0], [10.0, 18.0]]}],
           "terraces": [{"id": "lower", "level": low, "at_so": [8.0, 10.0], "polygon_so": [[6, 4], [10, 4], [10, 18], [6, 18]]},
                        {"id": "upper", "level": low + 0.68, "at_so": [12.0, 10.0], "polygon_so": [[10, 4], [14, 4], [14, 18], [10, 18]]}],
           "stairs": [{"id": "S", "line_so": [[8.8, 5.0], [10.0, 5.0]], "width_m": 1.4, "foot": [low + 0.28, low],
                       "head": low + 0.68, "risers": 4}]}
    res = design.evaluate(project, opt, keep_solids=True)
    d, s = res["design"], res["solids"]
    st = d.stairs[0]
    assert st.cross is not None                                  # the foot slopes across the flight
    patch = patch_of(d)
    whole = solids.option_ground(s, drawn.vertices, drawn.faces, patch, d.garden, d.stairs)
    v, f = solids.surface(whole, s.origin, patch)
    rng = np.random.default_rng(6)
    x0, y0, x1, y1 = st.poly.bounds
    e, n = rng.uniform(x0, x1, 3000), rng.uniform(y0, y1, 3000)
    under = shapely.contains_xy(st.poly.buffer(-0.01), e, n)
    e, n = e[under], n[under]
    h = Surface(v, f).z(e, n)
    assert np.abs(h - d.drawn(e, n)).max() < 0.001 and np.abs(h - st.under(e, n)).max() < 0.001
    assert (st.z(e, n) - st.under(e, n)).max() > 0.01           # where the soil stands higher, carried in from the foot


# Three made-up options the through check (checks.model.check_through) found ground coming through, on the layers the
# build draws for them (build.option_layers): a bed drawn past a capped wall's face, a wall's end post in a bed higher
# than its top, and steps whose foot is outside the regrade area.

BLOCK = {"material": "segmental", "unit_m": [0.457, 0.2], "cap_m": 0.1, "thickness_m": 0.27, "depth_m": 0.3,
         "cap_depth_m": 0.26, "cap_overhang_m": 0.025}


def made_up(oid, **parts):
    return {"id": oid, "name": oid, "short": oid, "description": "", "path": f"options/{oid}.toml", "walls": [], **parts}


def ground_at(project, s, o):
    en = design.frame(project).en(np.array([[s, o]]))
    return float(terrain.ground(project).sample(en[:, 0], en[:, 1])[0])


def through(s, opt):
    """The layers the build draws for a made-up option on site `s` (build.option_layers), written as its model to the
    site's output, and what the through check finds in them: (layers, the build's frame, findings)."""
    from domus import build
    from domus.checks import model as check

    cfg = s.settings()
    frame = build.Frame(build.site_origin(cfg, terrain.ground(s)))
    layers, _, _ = build.option_layers(s, opt, design.evaluate(s, opt, keep_solids=True), frame, cfg["model"]["extent"], cfg, [],
                                       existing_tin=terrain.drawn(s))
    model = {"origin": [frame.e0, frame.n0, frame.h0], "summary": {"options": [{"id": opt["id"], "keep_existing": []}]},
             "layers": layers}
    (s.output / "model.json").write_text(json.dumps(model), encoding="utf-8")
    findings = []
    check.check_through(s, findings)
    return layers, frame, findings


def drawn_ground(layers, oid, frame):
    """An option's ground layer as (vertices on the site's grid, faces)."""
    layer = next(x for x in layers if x["id"] == f"option-{oid}-ground")
    p = np.asarray(layer["positions"], float).reshape(-1, 3)
    return np.column_stack([p[:, 0] + frame.e0, frame.n0 - p[:, 2], p[:, 1] + frame.h0]), np.asarray(layer["indices"]).reshape(-1, 3)


def test_a_bed_drawn_past_a_capped_walls_face_stops_at_the_wall(site, project, tmp_path, at_site):
    """A block wall whose capping course stands 25 mm out from its face holds a bed flush with its top, and the bed is
    drawn 3 mm past its face, under the cap. The bed, raised a little over the ground as it's drawn, and the ground
    stop where the wall is drawn, so neither comes through the cap; under it the ground is at the toe, and behind the
    cap, over the body's top that the soil covers as it's built, the ground stays at the bed."""
    s = at_site(site, output=tmp_path)
    low = round(ground_at(project, 8.0, 10.0), 2)
    bed = round(low + 0.68, 2)
    opt = made_up("bedcap", walls=[{**BLOCK, "id": "N", "line_so": [[10.0, 6.0], [10.0, 18.0]], "top": bed}],
                  terraces=[{"id": "lower", "kind": "lawn", "level": low, "at_so": [8.0, 10.0],
                             "polygon_so": [[6, 4], [9.997, 4], [9.997, 18], [6, 18]]},
                            {"id": "bed", "kind": "planting", "level": bed, "at_so": [12.0, 10.0],
                             "polygon_so": [[9.997, 4], [14, 4], [14, 18], [9.997, 18]]}])
    layers, frame, findings = through(s, opt)
    assert findings == []
    ground = Surface(*drawn_ground(layers, "bedcap", frame))
    along = np.linspace(7.0, 17.0, 41)
    for at, level in ((9.99, low), (10.27, bed)):          # under the cap's overhang; behind the cap, over the body
        en = design.frame(project).en(np.column_stack([np.full(len(along), at), along]))
        assert np.abs(ground.z(en[:, 0], en[:, 1]) - level).max() < 0.002


def test_a_walls_end_in_a_bed_over_its_top_is_drawn_in_the_bed(site, project, tmp_path, at_site):
    """A timber wall's first end runs into a raised bed 0.1 m over its top, so the post at that end stands in the bed.
    Nothing of the ground or the beds comes through the post or the wall; the bed's edge over the wall's end is drawn,
    from the wall's top up, and none of the ground's upright faces on the wall's drawn outline stands under its top,
    where it would lie in the wall's faces."""
    s = at_site(site, output=tmp_path)
    low = round(ground_at(project, 8.0, 10.0), 2)
    bed = round(low + 0.68, 2)
    opt = made_up("raised", walls=[{"id": "T", "material": "timber", "line_so": [[10.0, 6.0], [10.0, 18.0]], "top": bed}],
                  terraces=[{"id": "high", "kind": "planting", "level": round(bed + 0.1, 2), "at_so": [10.0, 5.0],
                             "polygon_so": [[8, 4], [12, 4], [12, 6], [8, 6]]},
                            {"id": "lower", "kind": "lawn", "level": low, "at_so": [8.0, 10.0],
                             "polygon_so": [[6, 4], [10, 4], [10, 18], [6, 18]]},
                            {"id": "upper", "kind": "lawn", "level": bed, "at_so": [12.0, 10.0],
                             "polygon_so": [[10, 4], [14, 4], [14, 18], [10, 18]]}])
    layers, frame, findings = through(s, opt)
    assert findings == []
    v, f = drawn_ground(layers, "raised", frame)
    corners = v[f]
    n = np.cross(corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0])
    size = np.linalg.norm(n, axis=1)
    upright = (np.abs(n[:, 2]) <= 1e-6 * size) & (size > 2e-6)            # over a square millimetre: not a needle
    w = design.evaluate(s, opt, keep_solids=True)["walls"][0]
    pts, _, chain, toe, top = walls_mod.wall_levels(w["def"])
    plan, head, _ = solids.standing(w["mesh"].vertices, w["mesh"].faces, pts, chain, toe, top)
    mid = corners.mean(axis=1)
    on = upright & (shapely.distance(plan.boundary, shapely.points(mid[:, :2])) < 0.002)    # the layer is to the mm
    assert on.any() and (corners[on][:, :, 2].min(axis=1) >= head(mid[on, 0], mid[on, 1]) - 0.002).all()
    assert corners[on][:, :, 2].max() == pytest.approx(bed + 0.1, abs=0.002)


def test_steps_from_outside_the_regrade_area_stand_clear_of_the_ground_there(site, project, tmp_path, at_site):
    """Steps up onto a deck from the ground beyond its batter, their foot outside the regrade area (narrowed here, in
    the site's [regrade], to s from 5.4), and their lowest tread 12 mm under the ground as it is there: that ground
    stays drawn round them, but not over the ramp under their treads, so it doesn't come through them."""
    for part in ("data", "options"):
        shutil.copytree(site / part, tmp_path / "narrow" / part)
    shared = tmp_path / "narrow" / "options" / "_site.toml"
    shared.write_text(shared.read_text(encoding="utf-8").replace(
        "[regrade]\n", "[regrade]\npolygon_so = [[5.4, 0.0], [30.0, 0.0], [30.0, 22.0], [5.4, 22.0]]\n", 1), encoding="utf-8")
    (tmp_path / "out").mkdir()
    s = at_site(tmp_path / "narrow", output=tmp_path / "out")
    deck = round(ground_at(project, 6.0, 10.0) + 0.45, 2)
    foot = round((4 * (ground_at(project, 5.1, 10.0) - 0.012) - deck) / 3, 3)    # its lowest tread 12 mm under the ground at its back
    opt = made_up("outside", terraces=[{"id": "deck", "kind": "paving", "level": deck, "at_so": [7.0, 10.0], "batter": 1.0,
                                        "polygon_so": [[6, 8], [8, 8], [8, 12], [6, 12]]}],
                  stairs=[{"id": "S", "line_so": [[4.8, 10.0], [6.0, 10.0]], "width_m": 1.0, "foot": foot, "head": deck, "risers": 4}])
    d = design.evaluate(s, opt, keep_solids=True)["design"]
    assert not d.garden.covers(shapely.Point(*d.stairs[0].a))      # its foot is outside the regrade area
    _, _, findings = through(s, opt)
    assert findings == []


def ring_defects(ring):
    """A ring's corners a hair apart (an edge under 1e-6 m) and its spikes (a corner whose two edges fold back on each
    other, in a straight line): (how many of each, its shortest edge)."""
    q = np.asarray(ring.coords)[:-1, :2]
    e = np.roll(q, -1, axis=0) - q
    length = np.hypot(e[:, 0], e[:, 1])
    before = np.roll(e, 1, axis=0)
    cross = before[:, 0] * e[:, 1] - before[:, 1] * e[:, 0]
    return int((length < 1e-6).sum()), int(((np.abs(cross) < 2e-9) & ((before * e).sum(axis=1) < 0)).sum()), float(length.min())


@pytest.mark.parametrize("shape", ["round a sharp corner", "round a sharp corner, its top stepping"])
def test_a_walls_plan_with_corners_a_hair_apart_is_cut_from_the_ground(shape, project):
    """A capped block wall's plan, the union of its mesh's many triangles (solids.footprint), less the body's top the
    soil covers behind the cap, can have corners a fraction of a micrometre apart and spikes where they meet: these two
    made-up walls' plans do, level and stepped, and GEOS's constrained Delaunay refuses them once their outline has a
    corner every 5 cm, as it refused a wall on a real site. The walls' cut takes them all the same (solids.over snaps a plan to a 0.1
    micrometre grid first), over their whole plan."""
    from shapely.errors import GEOSException

    low = round(ground_at(project, 8.0, 10.0), 2)
    w = {**BLOCK, "id": "W", "line_so": [[10.0, 6.0], [10.0, 9.0], [11.0, 6.5]], "retained_side": "right", "top": low + 1.0}
    if shape.endswith("stepping"):
        w = {**w, "top": None, "tops": [low + 1.2, low + 1.0]}
        del w["top"]
    ww = design.evaluate(project, made_up("x", walls=[w]), segments=0)["walls"][0]
    m = ww["mesh"]
    pts, _, chain, toe, top = walls_mod.wall_levels(ww["def"])
    standing = solids.standing(m.vertices, m.faces, pts, chain, toe, top, getattr(m, "soil_over", ()), getattr(m, "soil_over_m", 0.0))
    plan, _, parts = standing
    refused = []
    for poly in (x for part, _ in parts for x in shapely.get_parts(part) if x.geom_type == "Polygon"):
        hairs, spikes, shortest = ring_defects(poly.exterior)
        try:
            shapely.constrained_delaunay_triangles(shapely.segmentize(poly, 0.05))
        except GEOSException:
            refused.append((hairs, spikes, shortest))
    assert refused and all(h > 10 and k >= 2 and sh < 1e-6 for h, k, sh in refused)       # the defect is there
    origin = np.asarray(plan.centroid.coords[0])
    cut = solids.walls_cut([standing], (), origin, float(np.nanmax(top)) + 5.0)
    assert abs(solids.outline(cut).area() - plan.area) < 1e-4


def test_where_geos_refuses_a_plan_it_is_ear_clipped(project, monkeypatch):
    """Where GEOS's constrained Delaunay refuses a plan even snapped, over() triangulates it by Manifold's ear clipping,
    checked to cover it once: here an L-shaped plan with a hole, GEOS made to refuse every plan. The solids between its
    heights cover the plan, hole and all, and hold its area times their height."""
    from shapely.errors import GEOSException

    def refuse(*args, **kwargs):
        raise GEOSException("IllegalStateException: Unable to find a convex corner")

    monkeypatch.setattr(shapely, "constrained_delaunay_triangles", refuse)
    plan = shapely.Polygon([(1000.0, 2000.0), (1004.0, 2000.0), (1004.0, 2001.0), (1001.0, 2001.0), (1001.0, 2003.0), (1000.0, 2003.0)],
                           [[(1000.3, 2000.3), (1000.7, 2000.3), (1000.7, 2000.7), (1000.3, 2000.7)]])
    low, high = solids.over(plan, np.array([1000.0, 2000.0]), [10.0, 10.5, 12.0])
    for piece, height in ((low, 0.5), (high, 1.5)):
        assert abs(solids.outline(piece).area() - plan.area) < 1e-6
        assert abs(piece.volume() - plan.area * height) < 1e-6


@pytest.mark.parametrize("material", ["timber", "block"])
def test_a_walls_bottom_under_a_steep_stretch_of_its_top_isnt_its_top(material, site, project, tmp_path, at_site):
    """A wall between a lower lawn and an upper one whose top drops 0.2 m over 4 cm, as a hand-drawn step can: over
    that stretch its top isn't level, and a closed wall's bottom, 0.15 m under its toe, is the highest level face there.
    The through check doesn't take it for the wall's top, with the ground over it by those 0.15 m: nothing comes
    through."""
    s = at_site(site, output=tmp_path)
    low = round(ground_at(project, 8.0, 10.0), 2)
    line = [[10.0, 6.0, low + 0.9], [10.0, 9.0, low + 0.9], [10.0, 9.04, low + 0.7], [10.0, 12.6, low + 0.7]]
    opt = made_up("steep", walls=[{"id": "T", "material": material, "line_so": line, "retained_side": "left"}],
                  terraces=[{"id": "lower", "kind": "lawn", "level": low, "at_so": [8.0, 10.0], "polygon_so": [[6, 4], [10, 4], [10, 18], [6, 18]]},
                            {"id": "upper", "kind": "lawn", "level": low + 0.68, "at_so": [12.0, 10.0], "polygon_so": [[10, 4], [14, 4], [14, 18], [10, 18]]}])
    _, _, findings = through(s, opt)
    assert findings == []


@pytest.mark.parametrize("kind", [{"material": "stone", "thickness_m": 0.4}, {"material": "gabion", "thickness_m": 0.5},
                                  {"material": "block", "thickness_m": 0.3}], ids=["stone", "gabion", "block"])
def test_a_thick_wall_in_bays_bending_at_its_step_shows_no_bottom_for_its_top(kind, site, project, tmp_path, at_site):
    """A thick wall in level bays whose line bends a fifth of a degree where its top steps, as a driveway wall restyled
    from timber can: at the step the wall's back closes over a sliver steep enough to count as upright, and under it
    the wall's bottom, 0.15 m under its toe, was the highest face that isn't upright. The through check takes as a
    wall's top only what looks up: nothing comes through."""
    import math

    s = at_site(site, output=tmp_path)
    low = round(ground_at(project, 8.0, 10.0), 2)
    a = math.radians(0.2)
    line = [[10.0, 12.0], [10.0, 8.0], [10.0 - 3.0 * math.sin(a), 8.0 - 3.0 * math.cos(a)]]
    opt = made_up("bays", walls=[{"id": "D", **kind, "line_so": line, "retained_side": "right", "tops": [low + 0.9, low + 0.68]}],
                  terraces=[{"id": "lower", "kind": "lawn", "level": low, "at_so": [8.0, 10.0], "polygon_so": [[4, 2], [10, 2], [10, 18], [4, 18]]},
                            {"id": "upper", "kind": "lawn", "level": low + 0.6, "at_so": [12.0, 10.0], "polygon_so": [[10, 2], [14, 2], [14, 18], [10, 18]]}])
    _, _, findings = through(s, opt)
    assert findings == []


@pytest.mark.parametrize("gap", [0.005, 0.02])
def test_a_flight_a_hair_short_of_a_walls_face_runs_out_to_it(gap, site, project, tmp_path, at_site):
    """Steps up from a lower lawn to an upper one, between two walls' cheeks that hold the upper lawn beside them: one
    cheek's face on the flight's side, the other's `gap` past it. 5 mm off, the flight runs out to that face, its
    treads and risers reach it, and the upper lawn isn't drawn in the gap, over the treads, but the ramp under them
    is; its other side stays on the other cheek's face. 20 mm off, past CHEEK_M, the flight is left as drawn."""
    s = at_site(site, output=tmp_path)
    low = round(ground_at(project, 8.0, 10.0), 2)
    up = round(low + 0.68, 2)
    opt = made_up("cheek", walls=[{"id": "W", "material": "block", "line_so": [[10.0, 6.0], [10.0, 18.0]]},
                                  {"id": "C", "material": "block", "line_so": [[10.0, 5.5 + gap], [11.2, 5.5 + gap]],
                                   "retained_side": "right", "top": up + 0.1},
                                  {"id": "D", "material": "block", "line_so": [[10.0, 4.5], [11.2, 4.5]], "retained_side": "left",
                                   "top": up + 0.1}],
                  terraces=[{"id": "lower", "kind": "lawn", "level": low, "at_so": [8.0, 10.0], "polygon_so": [[6, 4], [10, 4], [10, 18], [6, 18]]},
                            {"id": "upper", "kind": "lawn", "level": up, "at_so": [12.0, 10.0], "polygon_so": [[10, 4], [14, 4], [14, 18], [10, 18]]}],
                  stairs=[{"id": "S", "line_so": [[9.4, 5.0], [11.0, 5.0]], "width_m": 1.0, "foot": low, "head": up, "risers": 4}])
    st = design.evaluate(s, opt, keep_solids=True)["design"].stairs[0]
    fr = design.frame(project)
    o = fr.so(np.asarray(st.poly.exterior.coords)[:, :2])[:, 1]
    meets = gap <= 0.01                                          # scheme.CHEEK_M
    assert o.min() == pytest.approx(4.5, abs=1e-9) and o.max() == pytest.approx(5.5 + gap if meets else 5.5, abs=1e-9)
    assert st.width == pytest.approx(1.0 + gap if meets else 1.0, abs=1e-9)
    layers, frame, findings = through(s, opt)
    if not meets:
        return
    assert findings == []
    steps = next(x for x in layers if x["id"] == "option-cheek-steps-S")
    q = np.asarray(steps["positions"], float).reshape(-1, 3)
    assert fr.so(np.column_stack([q[:, 0] + frame.e0, frame.n0 - q[:, 2]]))[:, 1].max() == pytest.approx(5.5 + gap, abs=0.001)
    ground = Surface(*drawn_ground(layers, "cheek", frame))
    en = fr.en(np.column_stack([np.linspace(10.05, 10.95, 10), np.full(10, 5.5 + gap / 2)]))     # mid-way across the gap
    h = ground.z(en[:, 0], en[:, 1])
    assert np.abs(h - st.under(en[:, 0], en[:, 1])).max() < 0.002 and (h < up - 0.03).all()
