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


def test_designed_ground_holds_its_level(site, project):
    from domus import design, options

    opt = next(o for o in options.load_all(project) if o["id"] == "a-one-wall")
    d = design.Design(opt, site=project)
    t = opt["terraces"][0]
    e, n = design.frame(project).en(np.array(t["at_so"]))
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


def test_new_takes_a_negative_latitude(tmp_path, domus):
    domus("new", tmp_path / "p", "--name", "A place", "--centre", "-43.53,172.63")
    assert (tmp_path / "p" / "data" / "site" / "boundary.geojson").exists()


def test_a_new_site_can_be_a_git_repository_of_its_own(tmp_path, domus):
    """`domus new` writes a .gitignore: git leaves out what Domus makes again (the built model, the build cache, the
    checks' reports, the server's logs, upgrade backups, video frames, the reconstructions' working files, pictures of
    checks, picks backups) and keeps what a person made, the viewer's saved sketches and the records of edits among it.
    Asked of git itself, with no configuration of this machine's."""
    import os
    import shutil
    import subprocess

    if not shutil.which("git"):
        pytest.skip("git isn't installed")
    root = tmp_path / "p"
    domus("new", root, "--name", "A place", "--centre", "-43.53,172.63")
    made = ["viewer/data/model.json", ".domus/build-cache/ground-k1/entry.json", ".domus/checks/check_model.json",
            ".domus/build.log", ".domus/upgrade/format-2-20260101-000000/data/site.toml", "sources/raw/frames6/walk/t000.167.jpg",
            "data/video/cache/colmap/walk/database.db", "data/video/checks/walk-plan.jpg", "data/sources/canopy.jpg",
            "data/video/picks/walk.json.bak-1", "tools/__pycache__/pegs.cpython-314.pyc", ".pytest_cache/README.md"]
    kept = ["data/site.toml", "data/site/boundary.geojson", "data/site/house.geojson", "data/lidar/dem.tif",
            "data/video/walk-site.npz", "data/video/picks/walk.json", "options/_site.toml", "options/a-terrace.toml",
            "sources/raw/walk.mp4", "sources/raw/plan.pdf", ".domus/viewer.db", ".domus/edits/move-the-shed.json",
            "AGENTS.md", "notes.md", "tools/pegs.py", "tests/test_site.py", ".gitignore"]
    for rel in made + kept:
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).touch()
    (tmp_path / "none").touch()
    env = {**os.environ, "GIT_CONFIG_GLOBAL": str(tmp_path / "none"), "GIT_CONFIG_NOSYSTEM": "1"}
    git = ["git", "-c", f"core.excludesFile={tmp_path / 'none'}"]
    subprocess.run([*git, "init", "-q"], cwd=root, env=env, check=True, capture_output=True)
    out = subprocess.run([*git, "check-ignore", *made, *kept], cwd=root, env=env, capture_output=True, text=True,
                         encoding="utf-8", check=False)
    assert sorted(out.stdout.split()) == sorted(made), out.stderr


def test_a_folder_keeps_its_own_gitignore(tmp_path):
    """site/ in a copy of Domus has a .gitignore that keeps everything added to it out of the repo: a site started there
    leaves it as it is."""
    from domus import newsite

    ring, epsg = newsite.square_boundary(-43.53, 172.63, 20)
    (tmp_path / "site").mkdir()
    (tmp_path / "site" / ".gitignore").write_text("/*\n!/README.md\n", encoding="utf-8")
    newsite.write_project(tmp_path / "site", "A place", ring, epsg=epsg)
    assert (tmp_path / "site" / ".gitignore").read_text(encoding="utf-8") == "/*\n!/README.md\n"


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


def test_a_site_abroad_goes_on_a_grid_in_metres(tmp_path, domus):
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
    domus("new", tmp_path / "p", "--name", "Abroad", "--boundary", tmp_path / "b.geojson")
    cfg = tomllib.loads((tmp_path / "p" / "data" / "site.toml").read_text(encoding="utf-8"))
    assert cfg["crs"] == {"epsg": 27700, "label": "OSGB36 / British National Grid (EPSG:27700)", "vertical": ""}
    assert cfg["site"]["attribution"] == ""


def test_a_grid_cell_is_cut_along_its_corners_closer_in_height():
    from domus.model.terrain import cut_cells

    # One cell, its corners (row, column): 0 (0, 0), 1 (0, 1), 2 (1, 0), 3 (1, 1). Corner 3 stands high, so the cut
    # runs between 1 and 2, whichever way Delaunay took it; a point off the grid (4) leaves its triangles alone.
    cell = np.array([[0, 0], [0, 1], [1, 0], [1, 1], [-1, -1]])
    h = np.array([0.0, 0.1, 0.05, 1.0, 0.0])
    for faces in ([[0, 1, 3], [0, 3, 2]], [[0, 1, 2], [1, 3, 2]]):
        out = cut_cells(np.array(faces), cell, h)
        assert {frozenset(f) for f in out.tolist()} == {frozenset((0, 1, 2)), frozenset((1, 2, 3))}
    with_off = np.array([[0, 1, 4], [1, 3, 4], [3, 2, 4], [2, 0, 4]])
    assert (cut_cells(with_off, cell, h) == with_off).all()


def test_the_ground_is_triangulated_from_what_it_is_given():
    from domus import tin

    # tin reads no site: the ground model (a plane rising 1 in 10 to the north), a wall across it and the model's
    # origin come from the caller, and the same ground far along a grid, about an origin moved with it, gives the
    # same triangles.
    class Plane:
        def __init__(self, n0):
            self.n0 = n0

        def sample(self, e, n):
            return 40.0 + 0.1 * (np.asarray(n, float) - self.n0)

    built = []
    for e0, n0 in ((0.0, 0.0), (612345.0, 5412345.0)):
        wall = {"line": np.array([[e0 + 2.0, n0 + 4.0], [e0 + 8.0, n0 + 4.0]]), "side": 1, "thickness": 0.2,
                "batter": 0.0, "toe": [40.2, 40.2], "toe_chain": [0.0, 6.0], "top": [41.0, 41.0], "top_chain": [0.0, 6.0]}
        m = tin.build((e0, n0, e0 + 10.0, n0 + 8.0), 0.5, tin.Ground(Plane(n0)), (e0, n0), [wall])
        built.append((m.vertices - [e0, n0, 0.0], m.faces))
    (v0, f0), (v1, f1) = built
    assert np.allclose(v0, v1, atol=1e-6) and np.array_equal(f0, f1)
    assert np.isclose(v0[:, 2], 40.2).sum() >= 20 and np.isclose(v0[:, 2], 41.0).sum() >= 20    # the wall's toe and top


def test_a_surface_is_read_in_its_own_triangles():
    from domus.geo.mesh import surface_z

    # A 1 m square cut along one diagonal, its far corner 1 m high, and a small roof over one corner.
    v = np.array([[0, 0, 0], [1, 0, 0], [1, 1, 1], [0, 1, 0], [0, 0, 2], [0.2, 0, 2], [0, 0.2, 2]], float)
    f = np.array([[0, 1, 2], [0, 2, 3], [4, 5, 6]])
    e, n = np.array([0.75, 0.25, 0.5, 0.05, 1.5]), np.array([0.25, 0.75, 0.5, 0.05, 0.5])
    z = surface_z(v, f, e, n)
    assert np.allclose(z[:3], [0.25, 0.25, 0.5]) and z[3] == 2.0 and np.isnan(z[4])     # the highest; none off it
    cut_the_other_way = surface_z(v, np.array([[0, 1, 3], [1, 2, 3]]), e[:2], n[:2])
    assert np.allclose(cut_the_other_way, [0.0, 0.0])                                      # it reads the cut it's given
    assert surface_z(v, f, np.zeros((2, 2)) + 0.5, np.zeros((2, 2)) + 0.5).shape == (2, 2)
