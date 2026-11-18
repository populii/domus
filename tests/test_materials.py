"""An option's materials list (domus.options.materials): the units of a wall of named blocks counted course by course
from its set-out (walls.unit_count), what its trench takes, and the rest, net and with the site's allowance."""

import numpy as np
import pytest

from domus import walls
from domus.options import materials

UNIT = {"unit_m": [0.457, 0.2], "cap_m": 0.1}


def block_wall(line, top, toe, side=1, **more):
    """A wall of 457 x 200 mm units under a 100 mm capping course along `line`, its toe and top level, set out on
    half a block's thickness."""
    line = np.asarray(line, float)
    chain = np.concatenate([[0.0], np.cumsum(np.hypot(*np.diff(line, axis=0).T))])
    w = {"id": "W", "line": line, "side": side, "thickness": 0.2285, "material": "segmental", "properties": dict(UNIT),
         "toe_chain": chain, "toe": np.full(len(chain), toe), "top_chain": chain, "top": np.full(len(chain), top), **more}
    walls.set_out([w])
    return w


def test_a_straight_wall_of_whole_units_has_a_corner_unit_at_each_end_of_each_course():
    """Ten blocks long, four courses from its toe to under its cap: the even courses have a joint at each end, so
    their end units are corner units, and the odd ones end in half a block, a corner unit's end. The face by area is
    40 units, and 42 are laid: the odd courses' half blocks at each end."""
    w = block_wall([[0, 0], [4.57, 0]], top=1.1, toe=0.2)
    n = walls.unit_count(w)
    assert n["straight"] == (8 + 9 + 8 + 9, 0) and n["corner"] == (8, 0)
    assert n["caps"] == (10, 0) and n["top"] == 10 and n["cut_m"] == []


def test_its_first_course_goes_down_a_whole_course_when_it_says_so():
    w = block_wall([[0, 0], [4.57, 0]], top=1.1, toe=0.2, embedment=np.full(2, 0.2))
    n = walls.unit_count(w)
    assert sum(n["straight"]) + sum(n["corner"]) == 42 + 8 + 2      # a fifth course, of ten units: 8 straight, 2 corner


def test_a_wall_that_doesnt_fit_its_length_has_its_end_units_cut():
    """10.4 blocks long: each course's last unit is cut, 0.4 or 0.9 of a block."""
    w = block_wall([[0, 0], [10.4 * 0.457, 0]], top=1.1, toe=0.2)
    n = walls.unit_count(w)
    assert n["corner"] == (4, 4) and sorted(set(np.round(n["cut_m"], 3))) == [round(0.4 * 0.457, 3), round(0.9 * 0.457, 3)]


def test_an_outside_corner_takes_one_corner_unit_a_course():
    """Two stretches of ten and five blocks round an outside corner, set out so whole blocks reach it: each course has
    one corner unit there, which shows its length on one face and its end on the other, and one at each end."""
    w = block_wall([[0, 0], [4.57, 0], [4.57, -2.285]], top=1.1, toe=0.2, side=-1)
    assert walls.stretches(w)[2] == [1]
    n = walls.unit_count(w)
    assert n["corner"][0] == 4 * 3 and n["corner"][1] == 0 and n["cut_m"] == []


def test_the_list_counts_what_the_trench_and_the_units_take(project):
    """A wall that says how it's built and what it takes: its base, bedding, aggregate and seal along its trench, its
    pins, cores, pipe and outlets, with the site's allowance on what's ordered and the weight where a density's given."""
    from test_options import BLOCK, ground_at

    from domus.options import design

    low = round(ground_at(8.0, 10.0), 2)
    built = {**BLOCK, "id": "W", "line_so": [[10.0, 6.0], [10.0, 12.0]], "retained_side": "left", "top": low + 1.0,
             "depth_m": 0.3, "embed_m": 0.1, "bedding_m": 0.03, "base_m": 0.15, "trench_m": 0.6, "seal_m": 0.2,
             "pins_per_unit": 2, "pins_per_box": 40, "core_void_m3": 0.01, "pipe_m": 0.1, "outlet_spacing_m": 4.0,
             "geotextile": True}
    res = design.evaluate(project, {"id": "built", "walls": [built], "terraces": []})
    rows = {r["item"]: r for r in res["materials"]["rows"]}
    w = res["walls"][0]["def"]
    count = walls.unit_count(w)
    units = sum(count["straight"]) + sum(count["corner"])
    assert rows["Units in all"]["net"] == units
    assert rows["Pins"]["net"] == 2 * (units - count["top"]) and rows["Boxes of pins"]["net"] == -(-2 * (units - count["top"]) // 40)
    assert rows["Drainage aggregate in the units' cores"]["net"] == pytest.approx(0.01 * units, abs=0.005)
    assert rows["Base course, compacted"]["net"] == pytest.approx(0.6 * 0.15 * 6.0, abs=0.005)
    assert rows["Bedding"]["net"] == pytest.approx(0.6 * 0.03 * 6.0, abs=0.005)
    assert rows["Low-permeability soil over the drainage aggregate"]["net"] == pytest.approx(0.3 * 0.2 * 6.0, abs=0.005)
    first = walls.foot(w, w["toe_chain"], w["toe"], clear=0.0)
    behind = 0.3 * np.trapezoid(w["retained"] - 0.2 - first, w["toe_chain"])
    assert rows["Drainage aggregate behind the units"]["net"] == pytest.approx(behind, abs=0.005)
    assert rows["Perforated drainage pipe, 100 mm, in a filter sock"]["net"] == pytest.approx(6.0)
    assert rows["Pipe outlets"]["net"] == 2
    assert all(r["order"] == r["net"] for r in rows.values())          # no allowance unless the site gives one
    assert {"Cut", "Fill", "Fill less cut"} <= set(rows)


def test_the_allowance_is_added_to_what_is_ordered_and_not_to_the_soil():
    """A site's allowance goes on every quantity to order, rounded up for a count, and a density gives a weight."""
    class Design:
        stairs, terraces = [], []

    volumes = {"cut_m3": 2.0, "fill_m3": 5.0, "net_m3": 3.0}
    w = block_wall([[0, 0], [4.57, 0]], top=1.1, toe=0.2)
    w.update(depth=0.3, trench=0.6, base=0.15, bedding=0.0, seal=0.0, retained=np.full(2, 1.0), embedment=None)
    out = materials.listed([{"id": "W", "def": w, "length_m": 4.57, "face_area_m2": 4.1}], Design(), volumes,
                           {"materials": {"allowance_pct": 10, "density_t_m3": {"base": 2.0}}})
    rows = {r["item"]: r for r in out["rows"]}
    assert out["allowance_pct"] == 10
    assert rows["Units in all"]["net"] == 42 and rows["Units in all"]["order"] == 47      # 46.2, rounded up
    base = rows["Base course, compacted"]
    assert base["net"] == pytest.approx(0.6 * 0.15 * 4.57, abs=0.005) and base["order"] == pytest.approx(base["net"] * 1.1, abs=0.01)
    assert base["tonnes"] == pytest.approx(base["order"] * 2.0, abs=0.01)
    assert rows["Cut"]["order"] == 2.0 and rows["Fill less cut"]["order"] == 3.0


def test_steps_and_paving_and_timber_posts():
    class Stair:
        material, n, width, length, foot, rise = "sleepers", 4, 1.2, 1.2, 40.0, 0.6

        def tops(self):
            return [40.15, 40.3, 40.45, 40.6]

    class Terrace:
        kind, properties = "paving", {"paving_bedding_m": 0.03}

        class poly:
            area = 10.0

    class Design:
        stairs, terraces = [Stair()], [Terrace()]

    timber = {"id": "T", "def": {"material": "timber", "line": np.array([[0, 0], [5.0, 0], [5.0, 2.0]]), "post_spacing_m": 1.2,
                                 "trench": 0.2, "depth": 0.2, "base": 0.0, "bedding": 0.0, "seal": 0.0, "properties": {},
                                 "toe_chain": np.array([0.0, 7.0]), "toe": np.zeros(2), "top_chain": np.array([0.0, 7.0]),
                                 "top": np.ones(2), "retained": np.ones(2)},
              "length_m": 7.0, "face_area_m2": 7.0}
    out = materials.listed([timber], Design(), {"cut_m3": 0.0, "fill_m3": 0.0, "net_m3": 0.0},
                           {"materials": {"sleeper_m": 2.4}, "defaults": {"paving_base_m": 0.1}})
    rows = {r["item"]: r for r in out["rows"]}
    assert rows["Timber walls, posts"]["net"] == 5 + 2 + 1         # 5 m in five bays, 2 m in two, and the last post
    assert rows["Sleepers for the steps"]["net"] == pytest.approx(2 * 4 * 1.2) and rows["Sleepers"]["net"] == 8
    assert rows["Pavers"]["net"] == 10.0 and rows["Bedding sand under the pavers"]["net"] == pytest.approx(0.3)
    assert rows["Basecourse under the paving"]["net"] == pytest.approx(1.0)
