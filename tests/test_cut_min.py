"""No unit of a wall of a named block is cut shorter than its `cut_min_m` (walls.set_out): where a course would end in a
shorter piece, at an end of the wall or a corner, that piece and the whole unit beside it become two equal cut units,
and the courses of the other bond at that end do the same, a quarter of a unit off. The count, the drawing and
`domus prep check blocks` all follow it. On made-up walls, and on the demo site."""

import shutil

import numpy as np
import pytest

from domus.model import walls

LENGTH = 0.457
UNIT = {"unit_m": [LENGTH, 0.2], "cap_m": 0.1}


def block_wall(line, top=1.1, toe=0.2, side=1, **props):
    """A wall of 457 x 200 mm units under a 100 mm capping course along `line`, its toe and top level: four courses."""
    line = np.asarray(line, float)
    chain = np.concatenate([[0.0], np.cumsum(np.hypot(*np.diff(line, axis=0).T))])
    w = {"id": "W", "line": line, "side": side, "thickness": 0.2285, "material": "segmental",
         "properties": {**UNIT, **props}, "toe_chain": chain, "toe": np.full(len(chain), toe), "top_chain": chain,
         "top": np.full(len(chain), top)}
    walls.set_out([w])
    return w


def total(n):
    return sum(n["straight"]) + sum(n["corner"])


def test_a_course_that_would_end_in_a_short_piece_ends_in_two_equal_cut_units():
    """10.1 units long: the even courses would end in a piece 46 mm long. They end in two cut units of 0.55 of a unit
    instead, and the odd courses, which would end in 0.6 of one, in two of 0.8: their joints are a quarter of a unit
    apart. As many units are laid as before, but in each course a whole one is a cut one now, and none is short."""
    line = [[0, 0], [10.1 * LENGTH, 0]]
    w, before = block_wall(line), block_wall(line, cut_min_m=0.0)
    assert w["set_out"]["splits"] == [[pytest.approx(10.1 * LENGTH), 1, None]] and before["set_out"]["splits"] == []
    n, m = walls.unit_count(w), walls.unit_count(before)
    assert total(n) == total(m) and n["straight"] == (m["straight"][0] - 4, m["straight"][1] + 4)
    assert min(n["cut_m"]) >= 0.15 and min(m["cut_m"]) == pytest.approx(0.1 * LENGTH, abs=1e-3)
    assert sorted(set(np.round(n["cut_m"], 3))) == [round(0.55 * LENGTH, 3), round(0.8 * LENGTH, 3)]
    # The joints: halfway between the end and the joint before, for both bonds.
    assert walls.course_joints(0.0, 10.1, 0, split_hi=True)[-2:] == [9.0, pytest.approx(9.55)]
    assert walls.course_joints(0.0, 10.1, 1, split_hi=True)[-2:] == [8.5, pytest.approx(9.3)]


def test_at_a_corner_too():
    """Two outside corners with 5.1 units between them, too far off whole units to draw them a little long: the
    courses would reach the second corner 46 mm past a joint. There they end in two equal cut units."""
    run = 5.1 * LENGTH
    line = [[0, 0], [4.57, 0], [4.57, -run], [0, -run]]
    w = block_wall(line, side=-1)
    assert walls.stretches(w)[2] == [1, 1]
    corner = 4.57 + run
    assert [corner, 1, None] in [[pytest.approx(a), t, b] for a, t, b in w["set_out"]["splits"]]
    n = walls.unit_count(w)
    assert min(n["cut_m"]) >= 0.15
    assert walls.unit_count(block_wall(line, side=-1, cut_min_m=0.0))["cut_m"].count(pytest.approx(0.1 * LENGTH, abs=1e-3)) > 0


def test_a_course_only_a_step_of_the_foot_cuts_short_ends_at_a_joint():
    """Where the ground falls a course just before the end of a wall 10.5 units long, the course it uncovers runs
    less than a unit, between joints: the foot's step goes at the unit's joint, so it ends in the end's own piece, half
    a unit (a corner unit's end), not in a piece cut to where the ground falls. Each of the five courses at the end has
    a whole corner unit there, and nothing is cut."""
    end = 10.5 * LENGTH
    line = np.asarray([[0.0, 0.0], [end - 0.1, 0.0], [end, 0.0]])        # a point of its line where the ground falls
    w = {"id": "W", "line": line, "side": 1, "thickness": 0.2285, "material": "segmental", "properties": dict(UNIT),
         "toe_chain": np.array([0.0, end - 0.1, end]), "toe": np.array([0.2, 0.2, 0.0]),
         "top_chain": np.array([0.0, end]), "top": np.array([1.1, 1.1])}
    walls.set_out([w])
    n = walls.unit_count(w)
    assert w["set_out"]["splits"] == [] and n["cut_m"] == [] and n["corner"] == (4 + 5, 0)


def test_the_drawing_shows_the_two_cut_units():
    """Near the end, each course's face is drawn piece by piece: the end's unit starts at a joint of the picture, where
    the course's new joint is."""
    w = block_wall([[0, 0], [10.1 * LENGTH, 0]])
    m = walls.mesh(w)[0]
    v, uv = m.vertices, m.uv * [walls.UNIT_BLOCKS, 1]
    rate = w["set_out"]["rate"][0]
    found = set()
    for tri in np.asarray(m.faces).reshape(-1, 3)[::2]:              # each quad's first triangle
        p = v[tri]
        if np.all(np.abs(p[:, 1]) < 1e-6) and 0.2 <= p[:, 2].min() and p[:, 2].max() <= 1.0:   # the face, under the cap
            start = float(p[:, 0].min() * rate)
            tex = float(uv[tri, 0].min())
            for joint in (9.55, 9.3):                                 # the even courses' and the odd ones'
                if abs(start - joint) < 1e-6:
                    found.add(joint)
                    assert abs(tex - round(2 * tex) / 2) < 1e-6       # the picture's unit starts at a joint
    assert found == {9.55, 9.3}


def test_the_capping_course_keeps_to_the_same_minimum():
    """A length of top with 72 mm over its whole capping units: a whole one and that piece are two equal cut units, in
    the middle of the length; with a minimum of 50 mm, one cut unit of 72 mm."""
    half = round((LENGTH + 0.072) / 2, 4)
    assert np.diff(walls._lay(0.0, 3 * LENGTH + 0.072, LENGTH)[0]).round(4).tolist() == [LENGTH, half, half, LENGTH]
    sizes = np.diff(walls._lay(0.0, 3 * LENGTH + 0.072, LENGTH, least=0.05)[0])
    assert sorted(np.round(sizes, 4).tolist()) == [0.072, LENGTH, LENGTH, LENGTH]


def test_the_blocks_check_finds_no_piece_under_the_minimum(site, project, tmp_path, at_site):
    """On the demo, a wall whose courses would end 46 mm past a joint: `domus prep check blocks` reports the two equal
    cut units they end in, and no piece under 150 mm; with a minimum of 0 it reports the short piece."""
    from domus.checks import model as check
    from domus.model import terrain
    from domus.options import design

    en = design.frame(project).en(np.array([[10.0, 8.0]]))
    top = round(float(terrain.ground(project).sample(en[:, 0], en[:, 1])[0]), 2) + 1.0
    for least, expect in ((0.15, "two equal cut units"), (0.0, "46 mm long")):
        folder = tmp_path / f"min-{least}"
        shutil.copytree(site, folder, ignore=shutil.ignore_patterns("viewer", ".domus"))
        (folder / "options" / "x-short.toml").write_text(
            f'name = "X"\nshort = "X"\n\n[[walls]]\nid = "S"\nmaterial = "segmental"\nunit_m = [{LENGTH}, 0.2]\ncap_m = 0.1\n'
            f'thickness_m = 0.2285\ncut_min_m = {least}\nretained_side = "left"\ntop = {top}\n'
            f"line_so = [[10.0, 6.0], [10.0, {6.0 + 10.1 * LENGTH:.4f}]]\n", encoding="utf-8")
        findings = []
        check.check_blocks(at_site(folder), findings)
        mine = [f[3] for f in findings if f[2] == "x-short/S"]
        assert any(expect in text for text in mine), mine
        if least:
            assert not any("can be cut to" in text for text in mine), mine
