"""Example checks of a site's data.

Copy this file and write the checks only your property needs, such as the lot's area from its title, or a floor
level from its plans, with a tolerance. Checks that would suit any site belong in Domus itself.
"""

import json
import tomllib

import numpy as np
import pytest
from shapely.geometry import Polygon, shape


def layer(site, name):
    path = site / "data" / "site" / f"{name}.geojson"
    return json.loads(path.read_text(encoding="utf-8"))["features"] if path.exists() else []


def main_lot(site):
    cfg = tomllib.loads((site / "data" / "site.toml").read_text(encoding="utf-8"))
    feats = layer(site, "boundary")
    main = cfg.get("boundary", {}).get("main", feats[0]["properties"]["id"])
    return Polygon(next(f for f in feats if f["properties"]["id"] == main)["geometry"]["coordinates"][0])


def test_the_lot_is_a_proper_polygon(site):
    lot = main_lot(site)
    assert lot.is_valid
    assert lot.area > 20        # m2: put your title's area here instead, with a tolerance


def test_every_feature_has_its_own_id(site):
    for path in (site / "data" / "site").glob("*.geojson"):
        ids = [f["properties"].get("id") for f in json.loads(path.read_text(encoding="utf-8"))["features"]]
        assert all(ids), f"{path.name} has a feature without an id"
        assert len(ids) == len(set(ids)), f"{path.name} has two features with the same id"


def test_the_house_stands_on_the_lot(site):
    walls = [f for f in layer(site, "house") if f["properties"].get("id") == "house-walls"]
    if not walls:
        pytest.skip("this site has no house walls in data/site/house.geojson")
    assert main_lot(site).buffer(0.5).contains(shape(walls[0]["geometry"]))


def test_the_ground_model_covers_the_lot(site):
    from domus import raster, terrain  # imported here, once conftest.py has named the site

    if not raster.DEM.exists():
        pytest.skip("no ground model yet: run domus fetch")
    lot = main_lot(site)
    e, n = np.asarray(lot.exterior.coords).T
    assert np.isfinite(terrain.ground().sample(e, n)).all()
