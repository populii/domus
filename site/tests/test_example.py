"""Example checks of a site's data, through Domus's public API (domus.api).

Copy this file and write the checks only your property needs, such as the lot's area from its title, or a floor
level from its plans, with a tolerance. Checks that would suit any site belong in Domus itself.
"""

import numpy as np
import pytest

from domus.api import DomusError


def test_the_lot_is_a_proper_polygon(site):
    lot = site.lot()
    assert lot.is_valid
    assert lot.area > 20        # m2: put your title's area here instead, with a tolerance


def test_every_feature_has_its_own_id(site):
    for path in sorted((site.path / "data" / "site").glob("*.geojson")):
        ids = [f["properties"].get("id") for f in site.layer(path.stem)]
        assert all(ids), f"{path.name} has a feature without an id"
        assert len(ids) == len(set(ids)), f"{path.name} has two features with the same id"


def test_the_house_stands_on_the_lot(site):
    walls = site.house_walls()
    if walls is None:
        pytest.skip("this site has no house walls in data/site/house.geojson")
    assert site.lot().buffer(0.5).contains(walls)


def test_the_ground_model_covers_the_lot(site):
    try:
        ground = site.ground()
    except DomusError as e:
        pytest.skip(str(e))
    e, n = np.asarray(site.lot().exterior.coords).T
    assert np.isfinite(ground(e, n)).all()
