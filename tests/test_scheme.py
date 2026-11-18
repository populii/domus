"""A wall option worked out with no site: domus.scheme takes the frame, the ground and the rest from its caller. Its
cut and fill are soil, measured from the ground it's given."""

import numpy as np
import pytest
from shapely.geometry import Polygon

from domus import scheme
from domus.model import tin, walls


class Slope:
    """Ground rising 1 in 10 to the north from 40 m."""

    def sample(self, e, n):
        return 40.0 + 0.1 * np.asarray(n, float) + 0.0 * np.asarray(e, float)


class Flat:
    """Level ground at 40 m."""

    def sample(self, e, n):
        return np.full(np.shape(e), 40.0)


class Smeared:
    """A LiDAR's view of a wall's 0.8 m step at n 5: a slope from 40 m at n 4 to 41 m at n 6."""

    def sample(self, e, n):
        return 40.0 + 0.5 * np.clip(np.asarray(n, float) - 4.0, 0.0, 2.0) + 0.0 * np.asarray(e, float)


def on(ground, existing_walls=(), **defaults):
    """What a made-up site gives its options: s east along its south edge, o north into it, and a 10 m square to
    regrade."""
    frame = scheme.SiteFrame([0.0, 0.0], [10.0, 0.0], "left")
    garden = Polygon([(0, 0), (10, 0), (10, 10), (0, 10)])
    settings = {"defaults": {"batter": 2.0, "thickness_m": 0.2, "upstand_m": 0.1, **defaults}, "rules": {}}
    return scheme.Context(frame, settings, ground, garden, {"flat_slope": 0.05}, existing_walls)


def test_an_option_is_worked_out_on_what_it_is_given():
    # s east along the lot's south edge, o north into it; a 6 m terrace at 41 m held on its south edge by a wall.
    frame = scheme.SiteFrame([0.0, 0.0], [10.0, 0.0], "left")
    garden = Polygon([(0, 0), (10, 0), (10, 10), (0, 10)])
    site = scheme.Context(frame, {"defaults": {"batter": 2.0, "thickness_m": 0.2, "upstand_m": 0.1}, "rules": {}},
                          Slope(), garden, {"flat_slope": 0.05})
    opt = {"terraces": [{"id": "T", "level": 41.0, "polygon_so": [[2, 2], [8, 2], [8, 8], [2, 8]]}],
           "walls": [{"id": "W", "line_so": [[2, 2], [8, 2]], "retained_side": "left"}]}
    res = scheme.evaluate(opt, site, step=0.25)
    assert np.isclose(res["design"].sample(np.array([5.0]), np.array([5.0]))[0], 41.0)
    assert res["volumes"]["fill_m3"] > 18.0 and res["volumes"]["cut_m3"] < 0.5    # 36 m2 raised 0.5 m on average
    wall = res["walls"][0]
    assert np.isclose(wall["top_max"], 41.1, atol=0.01)                              # the terrace and the upstand
    assert 0.85 < wall["height_max_m"] < 1.0 and wall["flags"] == []


def wall_and_lawn(thickness):
    """A lawn at 41 m on the slope, from o 2 to past the regrade area's far side, held along o 2 by a wall `thickness`
    deep that runs on past both its sides, so no batter reaches the ground in front of it."""
    opt = {"terraces": [{"id": "T", "level": 41.0, "polygon_so": [[-3, 2], [13, 2], [13, 13], [-3, 13]]}],
           "walls": [{"id": "W", "line_so": [[-3, 2], [13, 2]], "retained_side": "left", "thickness_m": thickness}]}
    return scheme.evaluate(opt, on(Slope()), step=0.1)


def test_a_new_walls_own_body_is_not_soil():
    """The soil a wall holds back counts, and the wall itself, from its face to its back and from its toe to its top,
    doesn't: the soil under it is at its toe."""
    thick, thin = wall_and_lawn(0.4), wall_and_lawn(0.2)
    w = thick["walls"][0]["def"]
    assert np.allclose(w["toe"], 40.165) and thick["walls"][0]["top_max"] == 41.1    # the slope 0.35 m in front; the lawn
    at = (np.array([5.0, 5.0, 5.0]), np.array([2.1, 2.3, 2.5]))                   # in the wall, and just behind it
    assert np.allclose(scheme.soil_change(thick["design"], [w], [], *at), [40.165 - 40.21, 40.165 - 40.23, 41.0 - 40.25])
    # The 10 m of it in the regrade area: the lawn is filled from the slope behind it, from o 2.4 to o 10, and the slope
    # under it is cut to its toe.
    assert (thick["volumes"]["fill_m3"], thick["volumes"]["cut_m3"]) == (28.9, 0.2)
    # A wall 0.2 m thinner holds 0.2 m more of the lawn: the fill its body took, from the slope up to the lawn.
    assert thin["volumes"]["fill_m3"] - thick["volumes"]["fill_m3"] == pytest.approx(1.54, abs=0.1)


def test_paving_is_not_soil():
    """A paved terrace's level is the top of its paving, and the soil is the paving's depth under it: paving laid on
    level ground at its level cuts that much soil and fills none."""
    def paved(**more):
        return {"terraces": [{"id": "P", "kind": "paving", "level": 40.0, "polygon_so": [[2, 2], [8, 2], [8, 8], [2, 8]],
                              **more}]}

    cut = lambda opt, **defaults: scheme.evaluate(opt, on(Flat(), **defaults), step=0.1)["volumes"]
    assert cut(paved()) == {**cut(paved()), "cut_m3": 7.2, "fill_m3": 0.0, "area_m2": 36.0}     # 0.2 m deep by default
    assert cut(paved(paving_m=0.08))["cut_m3"] == 2.9                     # its own depth
    assert cut(paved(), paving_m=0.1)["cut_m3"] == 3.6                    # the options' [defaults] paving_m
    assert (cut(paved(kind="lawn"))["cut_m3"], cut(paved(kind="lawn"))["fill_m3"]) == (0.0, 0.0)


def test_an_option_is_measured_from_the_ground_with_the_existing_walls_in_it():
    """The ground as the build draws it has an existing wall's face and top in it, where a LiDAR smears the wall's
    step into a slope. An option worked out on it (tin.DrawnGround) is measured from that step, and the wall itself
    isn't soil: the soil under it is at its toe."""
    lidar = tin.Ground(Smeared())
    old = walls.from_feature({"properties": {"id": "X", "retained_side": "left", "thickness_m": 0.2, "toe_m": [40.0, 40.0]},
                              "geometry": {"type": "LineString", "coordinates": [[-5.0, 5.0, 41.0], [15.0, 5.0, 41.0]]}})
    drawn = tin.DrawnGround(tin.build((-6.0, -6.0, 16.0, 16.0), 0.5, lidar, (0.0, 0.0), [old]), lidar)
    at = (np.array([5.0, 5.0]), np.array([4.6, 5.5]))                  # in front of the wall, and behind it
    assert np.allclose(drawn.sample(*at), [40.0, 41.0]) and np.allclose(lidar.sample(*at), [40.3, 40.75])
    assert drawn.sample(np.array([30.0]), np.array([5.0]))[0] == 40.5  # off the ground drawn, the ground model's
    # A lawn at 40.5 over the step, the wall taken out: filled from 40 m to o 5.2 (the wall's toe under it too) and a
    # batter down to o 1, cut from 41 m behind it.
    lawn = {"terraces": [{"id": "T", "level": 40.5, "polygon_so": [[-3, 2], [13, 2], [13, 13], [-3, 13]]}]}
    v = scheme.evaluate(lawn, on(drawn, [old]), step=0.1)["volumes"]
    assert (v["fill_m3"], v["cut_m3"]) == (18.5, 24.0)
    v = scheme.evaluate(lawn, on(lidar, [old]), step=0.1)["volumes"]
    assert (v["fill_m3"], v["cut_m3"]) == (15.0, 22.4)                 # from the LiDAR's slope, it would be less
    # An option that keeps the wall moves no soil where it stands.
    v = scheme.evaluate({**lawn, "keep_existing": ["X"]}, on(drawn, [old]), step=0.1)["volumes"]
    assert (v["fill_m3"], v["cut_m3"]) == (17.5, 24.0)
