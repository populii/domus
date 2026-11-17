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


def test_plants_are_drawn_from_what_they_are_given():
    # vegetation reads no site: a tree whose top is read off the LiDAR needs the surface model it's given, and the
    # viewer calls the LiDAR what it's told.
    import pytest
    from shapely.geometry import Polygon

    from domus.errors import DomusError

    ring = [[0.0, 0.0], [4.0, 0.0], [4.0, 4.0], [0.0, 4.0], [0.0, 0.0]]
    f = {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [ring]},
         "properties": {"id": "t", "kind": "tree", "top": "lidar", "height_m": 5.0}}
    flat = lambda e, n: np.zeros(np.shape(e))
    at = (np.array([1.0]), np.array([1.0]), np.array([0.0]))
    with pytest.raises(DomusError, match="surface model"):
        vegetation.Plant(f, flat, Polygon(ring)).top(*at)

    class Surface:
        def sample(self, e, n):
            return np.full(np.shape(e), 4.0)

    plant = vegetation.Plant(f, flat, Polygon(ring), surface=Surface(), lidar="the 2025 LiDAR")
    assert plant.top(*at)[0] == 4.0 and "(the 2025 LiDAR)" in plant.info()["note"]
