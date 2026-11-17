"""A wall option worked out with no site: domus.scheme takes the frame, the ground and the rest from its caller."""

import numpy as np
from shapely.geometry import Polygon

from domus import scheme


class Slope:
    """Ground rising 1 in 10 to the north from 40 m."""

    def sample(self, e, n):
        return 40.0 + 0.1 * np.asarray(n, float) + 0.0 * np.asarray(e, float)


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
