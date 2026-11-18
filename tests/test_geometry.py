"""Coordinates, the options' frame and the wall levels."""

import itertools
import json

import numpy as np
import pytest
from shapely.geometry import Polygon

from domus.errors import DomusError


def test_frame_round_trip(site):
    from domus.options import design

    fr = design.SiteFrame([100.0, 200.0], [100.0, 180.0])
    so = np.array([[3.0, 2.0], [-1.5, 7.0]])
    assert np.allclose(fr.so(fr.en(so)), so)
    # Walking south, the lot is on the right: west.
    assert np.allclose(fr.en(np.array([0.0, 1.0])), [99.0, 200.0])
    assert np.allclose(design.SiteFrame([100.0, 200.0], [100.0, 180.0], "left").en(np.array([0.0, 1.0])), [101.0, 200.0])


def test_crs_defaults_to_nztm(project):
    assert project.grid.epsg == 2193
    assert project.grid.to_nztm(1500000.0, 5500000.0) == pytest.approx((1500000.0, 5500000.0))
    assert project.grid.datums == ()


def test_designed_ground_holds_its_level(site, project):
    from domus import options
    from domus.options import design

    opt = next(o for o in options.load_all(project) if o["id"] == "a-one-wall")
    d = design.Design(opt, site=project)
    t = opt["terraces"][0]
    e, n = design.frame(project).en(np.array(t["at_so"]))
    assert d.sample(np.array([e]), np.array([n]))[0] == pytest.approx(t["level"], abs=0.01)


def test_new_site_reference_edge_keeps_the_lot_on_the_right():
    from domus.project import newsite

    cw = [[0, 10], [20, 10], [20, 0], [1, 0], [0, 10]]
    a, b = newsite.longest_edge(cw)
    assert (a, b) == ([0, 10], [20, 10])
    a, b = newsite.longest_edge(cw[::-1])
    assert (a, b) == ([0, 10], [20, 10])


def test_new_project_is_buildable_config(tmp_path):
    import tomllib

    from domus.project import newsite

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
    from domus.project import newsite

    ring, epsg = newsite.square_boundary(-43.53, 172.63, 20)
    (tmp_path / "site").mkdir()
    (tmp_path / "site" / ".gitignore").write_text("/*\n!/README.md\n", encoding="utf-8")
    newsite.write_project(tmp_path / "site", "A place", ring, epsg=epsg)
    assert (tmp_path / "site" / ".gitignore").read_text(encoding="utf-8") == "/*\n!/README.md\n"


def test_read_boundary_takes_lon_lat(tmp_path):
    import json

    from domus.project import newsite

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

    from domus.project import newsite

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


def lattice_fixes(tmp_path, e0, n0):
    """A made-up 6 x 5 m fix area with a regular pattern of points 0.5 m apart, as a cloud's low points taken cell by
    cell would be, on a made-up ground that isn't flat: (areas, points), as read_fixes gives them, and each point's
    place in the pattern (column, row)."""
    from domus.model import tin

    cols, rows = np.meshgrid(np.arange(12), np.arange(10))
    cols, rows = cols.ravel(), rows.ravel()
    x, y = 10.25 + 0.5 * cols, 20.25 + 0.5 * rows
    z = 41.0 + 0.1 * (y - 20) + 0.06 * np.sin(1.3 * cols) * np.cos(0.9 * rows)
    area = [[e0 + 10, n0 + 20], [e0 + 16, n0 + 20], [e0 + 16, n0 + 25], [e0 + 10, n0 + 25], [e0 + 10, n0 + 20]]
    layer = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"id": "fix-area"}, "geometry": {"type": "Polygon", "coordinates": [area]}},
        {"type": "Feature", "properties": {"id": "fix-levels"}, "geometry": {
            "type": "MultiPoint", "coordinates": [[e0 + a, n0 + b, c] for a, b, c in zip(x, y, z)]}}]}
    path = tmp_path / "ground-fixes.geojson"
    path.write_text(json.dumps(layer), encoding="utf-8")
    return (*tin.read_fixes(path), np.column_stack([cols, rows]))


def cut_along_closer(faces, first, place, heights):
    """Whether each cell of the pattern (its points `first` onwards in the triangles' numbering, at `place`) that the
    triangles hold as two is cut along its diagonal whose ends are closer in height, or where both are as close, the
    one from the point given first: (cells held, cells cut so)."""
    edges = {frozenset(e) for f in np.asarray(faces).tolist() for e in itertools.combinations(f, 2)}
    at = {tuple(p): first + k for k, p in enumerate(place.tolist())}
    held = right = 0
    for (c, r), p00 in at.items():
        p10, p01, p11 = at.get((c + 1, r)), at.get((c, r + 1)), at.get((c + 1, r + 1))
        if None in (p10, p01, p11):
            continue
        rising, falling = frozenset((p00, p11)), frozenset((p10, p01))
        if (rising in edges) == (falling in edges):
            continue
        held += 1
        gap = {d: abs(heights[min(d)] - heights[max(d)]) for d in (rising, falling)}
        closer = (min((rising, falling), key=min) if abs(gap[rising] - gap[falling]) <= 1e-9
                  else min((rising, falling), key=gap.get))
        right += (rising if rising in edges else falling) == closer
    return held, right


def test_a_regular_pattern_of_ground_fixes_is_cut_by_height_wherever_it_lies(tmp_path):
    from domus.model import tin

    # Each cell of a regular pattern of fix points has its four corners on one circle, so Delaunay could cut it either
    # way and the arithmetic's rounding would choose. Every cell is cut along its corners closer in height, and so the
    # ground is the same whether the pattern lies on whole metres of the grid or between them.
    class Wavy:
        def __init__(self, e0, n0):
            self.e0, self.n0 = e0, n0

        def sample(self, e, n):
            return 40.0 + 0.1 * (np.asarray(n, float) - self.n0) + 0.3 * np.sin((np.asarray(e, float) - self.e0) / 3.0)

    x, y = (a.ravel() for a in np.meshgrid(np.arange(10.05, 16.0, 0.1), np.arange(20.05, 25.0, 0.1)))
    heights = []
    for e0, n0 in ((1500000.0, 5500000.0), (1500000.37, 5500000.81)):
        areas, fixes, place = lattice_fixes(tmp_path, e0, n0)
        ground = tin.Ground(Wavy(e0, n0), areas, fixes)
        t = ground.triangles
        held, right = cut_along_closer(t.faces, len(t.vertices) - len(fixes), place, t.vertices[:, 2])
        assert held == 11 * 9 and right == held
        heights.append(ground.sample(e0 + x, n0 + y))
    assert np.abs(heights[0] - heights[1]).max() < 1e-6


def test_the_drawn_ground_cuts_a_regular_pattern_of_fixes_by_height(tmp_path):
    from domus.model import tin

    # The ground as the build draws it holds the fix points as they're given, cut where four lie on one circle as
    # Ground cuts them: along the pair closer in height.
    class Slope:
        def sample(self, e, n):
            return 40.0 + 0.1 * (np.asarray(n, float) - 5500000.0)

    e0, n0 = 1500000.0, 5500000.0
    areas, fixes, place = lattice_fixes(tmp_path, e0, n0)
    m = tin.build((e0 + 8, n0 + 18, e0 + 18, n0 + 27), 2.0, tin.Ground(Slope(), areas, fixes), (e0, n0))
    held, right = cut_along_closer(m.faces, len(m.vertices) - len(fixes), place, m.vertices[:, 2])
    assert held > 50 and right == held


def test_the_ground_is_triangulated_from_what_it_is_given():
    from domus.model import tin

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


def test_ground_fixes_far_along_a_grid_keep_every_point(tmp_path):
    from domus.model import tin

    # A made-up 6 x 5 m fix area on a made-up ground model: a 3D line, resampled every 0.25 m as read_fixes does, and
    # three points, the last on the line's middle corner (one place given twice, kept once). At coordinates in the
    # millions, as a New Zealand site's are, every point is a corner of the fixed ground's triangles, the ground at
    # each fix point is its level, and the triangles are those the same fixes make near the grid's start.
    class Wavy:
        def __init__(self, e0, n0):
            self.e0, self.n0 = e0, n0

        def sample(self, e, n):
            return 40.0 + 0.1 * (np.asarray(n, float) - self.n0) + 0.3 * np.sin((np.asarray(e, float) - self.e0) / 3.0)

    made = []
    for e0, n0 in ((0.0, 0.0), (1500000.0, 5500000.0)):
        def at(e, n, *z, e0=e0, n0=n0):
            return [e0 + e, n0 + n, *z]

        layer = {"type": "FeatureCollection", "features": [
            {"type": "Feature", "properties": {"id": "fix-area"}, "geometry": {
                "type": "Polygon", "coordinates": [[at(10, 20), at(16, 20), at(16, 25), at(10, 25), at(10, 20)]]}},
            {"type": "Feature", "properties": {"id": "fix-line"}, "geometry": {
                "type": "LineString", "coordinates": [at(10.4, 20.3, 41.6), at(13.1, 22.2, 42.3), at(15.6, 24.7, 42.0)]}},
            {"type": "Feature", "properties": {"id": "fix-points"}, "geometry": {
                "type": "MultiPoint", "coordinates": [at(11.2, 24.1, 42.9), at(14.7, 21.05, 41.75), at(13.1, 22.2, 42.3)]}}]}
        path = tmp_path / "ground-fixes.geojson"
        path.write_text(json.dumps(layer), encoding="utf-8")
        areas, fixes = tin.read_fixes(path)
        ground = tin.Ground(Wavy(e0, n0), areas, fixes)
        assert np.abs(ground.sample(fixes[:, 0], fixes[:, 1]) - fixes[:, 2]).max() < 0.001
        xy, faces = ground.triangles.vertices[:, :2], ground.triangles.faces
        assert len(np.setdiff1d(np.arange(len(xy)), faces)) == 0
        assert len(np.unique(fixes[:, :2], axis=0)) == len(fixes)                  # the place given twice, once
        made.append({frozenset(s) for s in faces.tolist()})
    assert made[0] == made[1]


def test_two_ground_fixes_at_one_place_keep_the_later(tmp_path):
    from domus.geo.mesh import surface_z
    from domus.model import tin

    # A made-up fix area at the demo's coordinates: a line whose levels step at one of its corners, 40 mm up; a point
    # on that corner 30 mm higher still; the line's end given again at its height; and a point given twice, 0.5 mm
    # apart and 2 mm apart in height. At each place only the later is kept, and the fixed ground and the ground the
    # build draws take it alike; the two that disagree by more than 5 mm are named, with their heights.
    e0, n0 = 1500000.0, 5500000.0

    def at(e, n, *z):
        return [e0 + e, n0 + n, *z]

    layer = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"id": "fix-area"}, "geometry": {
            "type": "Polygon", "coordinates": [[at(10, 20), at(16, 20), at(16, 25), at(10, 25), at(10, 20)]]}},
        {"type": "Feature", "properties": {"id": "fix-line"}, "geometry": {"type": "LineString", "coordinates": [
            at(10.4, 20.3, 41.6), at(13.1, 22.2, 42.3), at(13.1, 22.2, 42.34), at(15.6, 24.7, 42.0)]}},
        {"type": "Feature", "properties": {"id": "fix-point"}, "geometry": {"type": "Point", "coordinates": at(13.1, 22.2, 42.37)}},
        {"type": "Feature", "properties": {"id": "fix-again"}, "geometry": {"type": "MultiPoint", "coordinates": [
            at(15.6, 24.7, 42.0), at(11.2, 24.1, 42.9), at(11.2005, 24.1, 42.902)]}}]}
    path = tmp_path / "ground-fixes.geojson"
    path.write_text(json.dumps(layer), encoding="utf-8")
    clashes = []
    areas, fixes = tin.read_fixes(path, clashes)
    assert clashes == [("fix-line", 42.3, "fix-line", 42.34, 1), ("fix-line", 42.34, "fix-point", 42.37, 1)]
    assert "fix-line and fix-point give one place two heights, 42.340 and 42.370 m" in tin.clash_text(clashes[1])
    for (e, n), h in (((13.1, 22.2), 42.37), ((15.6, 24.7), 42.0), ((11.2005, 24.1), 42.902)):
        here = np.hypot(fixes[:, 0] - (e0 + e), fixes[:, 1] - (n0 + n)) < 0.001
        assert here.sum() == 1 and fixes[here, 2][0] == pytest.approx(h)

    class Slope:
        def sample(self, e, n):
            return 40.0 + 0.1 * (np.asarray(n, float) - n0)

    ground = tin.Ground(Slope(), areas, fixes)
    drawn = tin.build((e0 + 8, n0 + 18, e0 + 18, n0 + 27), 0.5, ground, (e0, n0))
    e, n = np.array([e0 + 13.1]), np.array([n0 + 22.2])
    assert ground.sample(e, n)[0] == pytest.approx(42.37) and surface_z(drawn.vertices, drawn.faces, e, n)[0] == pytest.approx(42.37)


def test_a_pad_far_along_a_grid_is_drawn_through_all_its_points():
    from domus.geo.mesh import cross2
    from domus.model import features

    # A made-up L-shaped pad, draped on the ground, at the demo's coordinates: every point it's drawn through is a
    # corner of its triangles, and they cover it, with none that has no area along its straight edges.
    e0, n0 = 1500000.0, 5500000.0
    ring = [[e0 + e, n0 + n] for e, n in ((2.13, 3.07), (9.71, 3.9), (9.4, 6.62), (5.2, 6.1), (4.7, 10.3), (1.8, 9.95), (2.13, 3.07))]
    pad = {"type": "Feature", "properties": {"id": "pad-a", "kind": "pad"}, "geometry": {"type": "Polygon", "coordinates": [ring]}}
    m = features.pad(pad, lambda e, n: 40.0 + 0.1 * (np.asarray(n, float) - n0))["concrete"]
    assert len(np.unique(m.faces)) == len(m.vertices)
    v = m.vertices[m.faces]
    area = 0.5 * np.abs(cross2(v[:, 1, :2] - v[:, 0, :2], v[:, 2, :2] - v[:, 0, :2]))
    assert area.sum() == pytest.approx(Polygon(ring).area, abs=0.01) and area.min() > 1e-6


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
