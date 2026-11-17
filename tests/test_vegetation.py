"""The vegetation mask: near-infrared where there is some, colour where there isn't."""

import numpy as np

from domus import vegetation


def test_ndvi_tells_a_green_roof_from_a_tree():
    chm = np.full((12, 24), 4.0)
    rgb = np.broadcast_to([60.0, 140.0, 60.0], (12, 24, 3))   # all of it looks like foliage in colour
    ndvi = np.full((12, 24), 0.45)                             # a tree on the left,
    ndvi[:, 8:16] = 0.03                                       # a green-painted roof in the middle,
    ndvi[:, 16:] = np.nan                                      # and no near-infrared photo on the right
    veg = vegetation.vegetation_mask(chm, rgb, ndvi=ndvi)
    assert veg[3:9, 2:6].all() and not veg[:, 9:15].any() and veg[3:9, 18:22].all()
    assert vegetation.vegetation_mask(chm, rgb)[3:9, 9:15].all()
