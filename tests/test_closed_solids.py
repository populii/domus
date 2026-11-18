"""Walls and steps drawn as closed solids, as Manifold takes them (its check of a valid solid), with the looks and the
picture coordinates they had, on the demo site: plain walls (timber in level bays with its posts and with an open end,
block, battered, and held on either side), walls of a named block (straight, with a capping course standing out or deeper than the body, round a
corner, round a sharp corner, stepped with their stacked capping units, stepped two courses, stepped where the line
turns, along an arc with a stepped top, and two that meet along a face), and steps (of blocks and of sleepers, on a
level foot and on one that slopes across them)."""

import math

import manifold3d as mf
import numpy as np
import pytest

from domus import options
from domus.model import terrain
from domus.options import design

BLOCK = {"material": "segmental", "unit_m": [0.457, 0.2], "cap_m": 0.1, "thickness_m": 0.27}
OUT = {"depth_m": 0.3, "cap_depth_m": 0.26, "cap_overhang_m": 0.025}     # a capping course standing out, shallower
DEEP = {"depth_m": 0.27, "cap_depth_m": 0.33}                           # a capping course deeper than the body


def low_at(project):
    en = design.frame(project).en(np.array([[8.0, 10.0]]))
    return round(float(terrain.ground(project).sample(en[:, 0], en[:, 1])[0]), 2)


def cases(low):
    straight = {**BLOCK, "id": "W", "line_so": [[10.0, 6.0], [10.0, 12.0]], "retained_side": "left", "top": low + 1.0}
    stepped = {**BLOCK, "id": "W", "line_so": [[10.0, 6.0], [10.0, 9.0], [10.0, 12.6]], "retained_side": "left",
               "tops": [low + 1.2, low + 1.0]}
    meeting = [{**BLOCK, "id": "A", "line_so": [[10.0, 6.0], [10.0, 9.0], [12.0, 9.0]], "top": low + 1.3, "retained_side": "left"},
               {**BLOCK, "id": "B", "line_so": [[10.0, 8.95], [10.0, 12.0]], "top": low + 0.7, "retained_side": "left"}]
    lawns = {"terraces": [{"id": "lower", "level": low, "at_so": [8.0, 10.0], "polygon_so": [[6, 4], [10, 4], [10, 18], [6, 18]]},
                          {"id": "upper", "level": low + 0.68, "at_so": [12.0, 10.0], "polygon_so": [[10, 4], [14, 4], [14, 18], [10, 18]]}],
             "walls": [{"id": "W", "line_so": [[10.0, 6.0], [10.0, 18.0]]}]}
    flight = {"id": "S", "name": "Steps", "line_so": [[8.8, 5.0], [10.0, 5.0]], "width_m": 1.0}
    sloping = {**flight, "width_m": 1.4, "foot": [low + 0.28, low], "head": low + 0.68, "risers": 4}

    def turning(deg):
        """The stepped wall, its line turning `deg` degrees where its top steps."""
        a = math.radians(deg)
        return {**stepped, "line_so": [[10.0, 6.0], [10.0, 9.0], [10.0 + 3.6 * math.sin(a), 9.0 + 3.6 * math.cos(a)]]}
    arc = [[10.0 + 3 * math.sin(a), 6.0 + 3 * (1 - math.cos(a))] for a in np.linspace(0, math.pi / 2, 13)]
    return {
        "timber in level bays": {"walls": [{"id": "W", "material": "timber", "line_so": [[10.0, 6.0], [10.0, 9.0], [10.0, 12.6]],
                                            "retained_side": "left", "tops": [low + 0.9, low + 0.6]}]},
        "timber with an open end": {"walls": [{"id": "W", "material": "timber", "line_so": [[10.0, 6.0], [10.0, 12.0]],
                                               "retained_side": "left", "top": low + 0.8, "open_ends": ["start"]}]},
        "block, battered": {"walls": [{"id": "W", "material": "block", "line_so": [[10.0, 6.0], [10.0, 12.0]],
                                       "retained_side": "left", "top": low + 1.0, "batter": 0.2}]},
        "block, held on the right": {"walls": [{"id": "W", "material": "block", "line_so": [[10.0, 6.0], [10.0, 12.0]],
                                                "retained_side": "right", "top": low + 1.0}]},
        "timber in level bays, held on the right": {"walls": [{"id": "W", "material": "timber", "retained_side": "right",
                                                               "line_so": [[10.0, 6.0], [10.0, 9.0], [10.0, 12.6]],
                                                               "tops": [low + 0.9, low + 0.6]}]},
        "named blocks": {"walls": [straight]},
        "named blocks, a cap standing out": {"walls": [{**straight, **OUT}]},
        "named blocks, a deeper cap": {"walls": [{**straight, **DEEP}]},
        "named blocks round a corner": {"walls": [{**straight, "line_so": [[10.0, 6.0], [10.0, 9.0], [12.0, 9.0]]}]},
        "named blocks, stepped": {"walls": [stepped]},
        "named blocks, stepped, a cap standing out": {"walls": [{**stepped, **OUT}]},
        "named blocks, stepped, a deeper cap": {"walls": [{**stepped, **DEEP}]},
        "named blocks, stepped two courses, a cap standing out": {"walls": [{**stepped, **OUT, "tops": [low + 1.4, low + 1.0]}]},
        "named blocks, stepped where the line turns a little": {"walls": [turning(7.5)]},
        "named blocks, stepped where the line turns, a cap standing out": {"walls": [{**turning(30), **OUT}]},
        "named blocks, stepped at a corner, a deeper cap": {"walls": [{**turning(90), **DEEP}]},
        "named blocks, stepped at a sharp corner, a cap standing out": {"walls": [{**turning(150), **OUT}]},
        "named blocks round a sharp corner, a height over the toe, a cap standing out": {"walls": [
            {**{k: v for k, v in turning(150).items() if k != "tops"}, **OUT, "height_m": 1.0}]},
        "named blocks along an arc, stepping, a cap standing out": {"walls": [{**stepped, **OUT, "line_so": arc,
                                                                                "tops": [low + 1.2 - 0.2 * (i % 2) for i in range(12)]}]},
        "named blocks meeting along a face": {"walls": meeting},
        "named blocks meeting, caps standing out": {"walls": [{**w, **OUT} for w in meeting]},
        "steps of blocks": {**lawns, "stairs": [flight]},
        "steps of sleepers": {**lawns, "stairs": [{**flight, "material": "sleepers"}]},
        "steps on a sloping foot": {**lawns, "stairs": [sloping]},
        "sleepers on a sloping foot": {**lawns, "stairs": [{**sloping, "material": "sleepers"}]},
    }


def solid(m):
    """A mesh with its corners welded to a micrometre, as Manifold takes it: (its status, its volume)."""
    v = np.asarray(m.vertices, float)
    key = np.round((v - v.min(axis=0)) / 1e-6).astype(np.int64)
    _, first, inverse = np.unique(key, axis=0, return_index=True, return_inverse=True)
    f = inverse.reshape(-1)[np.asarray(m.faces, np.int64).reshape(-1, 3)]
    f = f[(f[:, 0] != f[:, 1]) & (f[:, 1] != f[:, 2]) & (f[:, 2] != f[:, 0])]
    p = v[first]
    s = mf.Manifold(mf.Mesh64(vert_properties=np.ascontiguousarray(p - p.mean(axis=0)), tri_verts=np.ascontiguousarray(f, np.uint64)))
    return s.status(), s.volume()


@pytest.mark.parametrize("which", list(cases(0.0)))
def test_walls_and_steps_are_closed_solids(which, project):
    """Each wall and each flight of steps an option draws is a closed solid, its outside out: Manifold takes it with
    no error and it has a volume. Every corner keeps its picture's coordinates."""
    opt = {"id": "x", "name": which, **cases(low_at(project))[which]}
    res = design.evaluate(project, opt, segments=0)
    meshes = [(f"wall {w['id']}", w["mesh"]) for w in res["walls"]] + [(f"steps {s['id']}", s["mesh"]) for s in res["stairs"]]
    assert meshes
    for name, m in meshes:
        status, volume = solid(m)
        assert status == mf.Error.NoError and volume > 0.1, f"{which}: {name}: {status}, {volume:.4f} m3"
        if getattr(m, "uv", None) is not None:
            assert len(m.uv) == len(m.vertices) and np.isfinite(m.uv).all()


@pytest.mark.parametrize("turn", [120, 135, 150, 165])
def test_named_blocks_round_a_sharp_corner_are_closed_solids(turn, project):
    """A wall of named blocks round a sharp corner, its 3 m legs turning `turn` degrees, its top level, is a closed
    solid: plain, with a capping course standing out and with one deeper than the body, held on either side of its
    line. At 120 degrees, held on the outside of the turn, some of its pieces had their three corners in a line,
    which, counted, gave an edge a third triangle and left two alone. Held on the inside of a sharper turn, its back's
    and its capping course's corners stopped at twice their depth from the face's, short of where the two legs' backs
    meet, and with a capping course standing out or deeper than the body its pieces crossed there, two of them facing
    the same way across an edge."""
    low = low_at(project)
    a = math.radians(turn)
    line = [[10.0, 6.0], [10.0, 9.0], [10.0 + 3 * math.sin(a), 9.0 + 3 * math.cos(a)]]
    for name, kind in (("plain", BLOCK), ("a cap standing out", {**BLOCK, **OUT}), ("a deeper cap", {**BLOCK, **DEEP})):
        for side in ("left", "right"):
            w = {**kind, "id": "W", "line_so": line, "retained_side": side, "top": low + 1.0}
            m = design.evaluate(project, {"id": "x", "name": "x", "walls": [w]}, segments=0)["walls"][0]["mesh"]
            status, volume = solid(m)
            assert status == mf.Error.NoError and volume > 0.1, f"{name}, held {side}: {status}, {volume:.4f} m3"


def test_the_demo_options_walls_are_closed_solids(project):
    """The walls of the demo's options, as they're built, are closed solids too."""
    for opt in options.load_all(project):
        for w in design.evaluate(project, opt, segments=0)["walls"]:
            status, volume = solid(w["mesh"])
            assert status == mf.Error.NoError and volume > 0.1, f"{opt['id']}: wall {w['id']}: {status}"


def test_an_edge_is_cut_at_the_corners_on_it():
    """A unit box whose +x side is two pieces, a lower and an upper half: its front and back sides' edges along x = 1
    run past the halves' shared corners. Joined, each such edge is cut there, so the box is closed edge to edge; a cut
    triangle keeps its turn, and each new corner's texture coordinates are read along its edge."""
    from domus.geo.mesh import join_t_junctions

    def quad(a, b, c, d):
        return [a, b, c, d]

    sides = [quad((0, 0, 0), (0, 1, 0), (1, 1, 0), (1, 0, 0)),          # bottom, out = -z
             quad((0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1)),          # top, out = +z
             quad((0, 0, 0), (1, 0, 0), (1, 0, 1), (0, 0, 1)),          # front, out = -y
             quad((0, 1, 0), (0, 1, 1), (1, 1, 1), (1, 1, 0)),          # back, out = +y
             quad((0, 0, 0), (0, 0, 1), (0, 1, 1), (0, 1, 0)),          # -x
             quad((1, 0, 0), (1, 1, 0), (1, 1, 0.5), (1, 0, 0.5)),      # +x, lower half
             quad((1, 0, 0.5), (1, 1, 0.5), (1, 1, 1), (1, 0, 1))]      # +x, upper half
    v = np.array([p for s in sides for p in s], float)
    f = np.array([(k, k + 1, k + 2) for k in range(0, len(v), 4)] + [(k, k + 2, k + 3) for k in range(0, len(v), 4)])
    uv = v[:, [0, 2]] * 10.0                                             # a picture laid on x and z
    from types import SimpleNamespace

    assert solid(SimpleNamespace(vertices=v, faces=f))[0] != mf.Error.NoError        # open where the halves meet
    v2, f2, uv2, parent = join_t_junctions(v, f, uv)
    status, volume = solid(SimpleNamespace(vertices=v2, faces=f2))
    assert status == mf.Error.NoError and volume == pytest.approx(1.0)
    assert len(f2) == len(f) + 2 and set(parent) == set(range(len(f)))                # front and back each cut once
    new = np.arange(len(v), len(v2))
    assert sorted(tuple(float(x) for x in p) for p in v2[new]) == [(1.0, 0.0, 0.5), (1.0, 1.0, 0.5)]   # the halves' corners
    assert np.allclose(uv2[new], v2[new][:, [0, 2]] * 10.0)                           # read along the edge
