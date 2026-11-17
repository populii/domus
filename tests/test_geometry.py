"""Coordinates, the options' frame and the wall levels."""

import numpy as np
import pytest

from domus.errors import DomusError


def test_frame_round_trip(site):
    from domus import design

    fr = design.SiteFrame([100.0, 200.0], [100.0, 180.0])
    so = np.array([[3.0, 2.0], [-1.5, 7.0]])
    assert np.allclose(fr.so(fr.en(so)), so)
    # Walking south, the lot is on the right: west.
    assert np.allclose(fr.en(np.array([0.0, 1.0])), [99.0, 200.0])
    assert np.allclose(design.SiteFrame([100.0, 200.0], [100.0, 180.0], "left").en(np.array([0.0, 1.0])), [101.0, 200.0])


def test_crs_defaults_to_nztm(site):
    from domus import crs

    assert crs.site_epsg() == 2193
    assert crs.site_to_nztm(1500000.0, 5500000.0) == pytest.approx((1500000.0, 5500000.0))
    assert crs.datums() == []


def test_designed_ground_holds_its_level(site):
    from domus import design, options

    opt = next(o for o in options.load_all() if o["id"] == "a-one-wall")
    d = design.Design(opt)
    t = opt["terraces"][0]
    e, n = design.frame().en(np.array(t["at_so"]))
    assert d.sample(np.array([e]), np.array([n]))[0] == pytest.approx(t["level"], abs=0.01)


def test_new_site_reference_edge_keeps_the_lot_on_the_right():
    from domus import newsite

    cw = [[0, 10], [20, 10], [20, 0], [1, 0], [0, 10]]
    a, b = newsite.longest_edge(cw)
    assert (a, b) == ([0, 10], [20, 10])
    a, b = newsite.longest_edge(cw[::-1])
    assert (a, b) == ([0, 10], [20, 10])


def test_new_project_is_buildable_config(tmp_path):
    import tomllib

    from domus import newsite

    ring = newsite.square_boundary(-43.53, 172.63, 20)
    root = newsite.write_project(tmp_path / "p", 'A "quoted" name', ring)
    cfg = tomllib.loads((root / "data" / "site.toml").read_text(encoding="utf-8"))
    assert cfg["site"]["title"] == 'A "quoted" name'
    assert cfg["crs"]["epsg"] == 2193
    e0, n0, e1, n1 = cfg["model"]["extent"]
    assert e1 - e0 >= 50 and n1 - n0 >= 50
    assert tomllib.loads((root / "options" / "_site.toml").read_text(encoding="utf-8"))["frame"]["origin"]
    with pytest.raises(DomusError):
        newsite.write_project(tmp_path / "p", "again", ring)


def test_new_takes_a_negative_latitude(tmp_path):
    from domus import newsite

    newsite.main([str(tmp_path / "p"), "--name", "A place", "--centre", "-43.53,172.63"])
    assert (tmp_path / "p" / "data" / "site" / "boundary.geojson").exists()


def test_read_boundary_takes_lon_lat(tmp_path):
    import json

    from domus import newsite

    f = tmp_path / "lot.geojson"
    f.write_text(json.dumps({"type": "Polygon", "coordinates": [[[172.63, -43.53], [172.6302, -43.53], [172.6302, -43.5302], [172.63, -43.53]]]}), encoding="utf-8")
    ring = newsite.read_boundary(f)
    assert ring[0] == ring[-1]
    assert 1.5e6 < ring[0][0] < 1.6e6 and 5.1e6 < ring[0][1] < 5.2e6
