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

    ring, epsg = newsite.square_boundary(-43.53, 172.63, 20)
    root = newsite.write_project(tmp_path / "p", 'A "quoted" name', ring, epsg=epsg)
    cfg = tomllib.loads((root / "data" / "site.toml").read_text(encoding="utf-8"))
    assert cfg["site"]["title"] == 'A "quoted" name'
    assert cfg["crs"] == {"epsg": 2193, "label": "NZGD2000 / New Zealand Transverse Mercator (EPSG:2193)", "vertical": "NZVD2016"}
    assert "LINZ" in cfg["site"]["attribution"]
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
    ring, epsg = newsite.read_boundary(f)
    assert ring[0] == ring[-1] and epsg == 2193
    assert 1.5e6 < ring[0][0] < 1.6e6 and 5.1e6 < ring[0][1] < 5.2e6


def boundary(path, ring, epsg=None):
    import json

    gj = {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {},
                                                     "geometry": {"type": "Polygon", "coordinates": [ring]}}]}
    if epsg:
        gj["crs"] = {"type": "name", "properties": {"name": f"urn:ogc:def:crs:EPSG::{epsg}"}}
    path.write_text(json.dumps(gj), encoding="utf-8")
    return path


def test_a_site_abroad_goes_on_a_grid_in_metres(tmp_path):
    import tomllib

    from domus import newsite

    # Longitude and latitude in Cornwall: its UTM zone. A grid in metres: kept. A grid in feet: the UTM zone.
    lonlat = [[-5.5, 50.1], [-5.4998, 50.1], [-5.4998, 50.1002], [-5.5, 50.1]]
    assert newsite.read_boundary(boundary(tmp_path / "a.geojson", lonlat))[1] == 32630
    bng = [[100000.0, 100000.0], [100020.0, 100000.0], [100020.0, 100030.0], [100000.0, 100000.0]]
    ring, epsg = newsite.read_boundary(boundary(tmp_path / "b.geojson", bng, 27700))
    assert epsg == 27700 and ring == [*bng]
    feet = [[6000000.0, 2100000.0], [6000060.0, 2100000.0], [6000060.0, 2100090.0], [6000000.0, 2100000.0]]
    ring, epsg = newsite.read_boundary(boundary(tmp_path / "c.geojson", feet, 2227))      # California zone 3, US feet
    assert epsg == 32610 and abs((ring[1][0] - ring[0][0]) - 60 * 1200 / 3937) < 0.05
    # --epsg names the grid; one in degrees won't do.
    assert newsite.read_boundary(tmp_path / "b.geojson", 32630)[1] == 32630
    with pytest.raises(DomusError, match="isn't a grid in metres"):
        newsite.read_boundary(tmp_path / "b.geojson", 4326)
    # Away from New Zealand the height datum and credit line are the site's own to fill in.
    newsite.main([str(tmp_path / "p"), "--name", "Abroad", "--boundary", str(tmp_path / "b.geojson")])
    cfg = tomllib.loads((tmp_path / "p" / "data" / "site.toml").read_text(encoding="utf-8"))
    assert cfg["crs"] == {"epsg": 27700, "label": "OSGB36 / British National Grid (EPSG:27700)", "vertical": ""}
    assert cfg["site"]["attribution"] == ""


def test_a_grid_cell_is_cut_along_its_corners_closer_in_height():
    from domus.terrain import cut_cells

    # One cell, its corners (row, column): 0 (0, 0), 1 (0, 1), 2 (1, 0), 3 (1, 1). Corner 3 stands high, so the cut
    # runs between 1 and 2, whichever way Delaunay took it; a point off the grid (4) leaves its triangles alone.
    cell = np.array([[0, 0], [0, 1], [1, 0], [1, 1], [-1, -1]])
    h = np.array([0.0, 0.1, 0.05, 1.0, 0.0])
    for faces in ([[0, 1, 3], [0, 3, 2]], [[0, 1, 2], [1, 3, 2]]):
        out = cut_cells(np.array(faces), cell, h)
        assert {frozenset(f) for f in out.tolist()} == {frozenset((0, 1, 2)), frozenset((1, 2, 3))}
    with_off = np.array([[0, 1, 4], [1, 3, 4], [3, 2, 4], [2, 0, 4]])
    assert (cut_cells(with_off, cell, h) == with_off).all()
