"""The point cloud: tile names, cropping a tile, the ground grid, and a roof fitted to building points. Nothing goes out."""

import laspy
import numpy as np
import pytest

from domus import create, points


def test_tile_names_with_and_without_a_year():
    assert points.tile_bounds("NZ20_Canterbury/CL2_BX24_2020_1000_0245.laz") == points.linz.tile_bounds("BX24_1000_0245.json")
    assert points.tile_bounds("BX24_500_012034.laz") is None or len(points.tile_bounds("BX24_500_012034.laz")) == 4
    assert points.tile_bounds("readme.txt") is None


def test_covering_needs_the_whole_extent():
    x0, y0, x1, _ = points.tile_bounds("CL2_BX24_2020_1000_0245.laz")
    keys = ["a/CL2_BX24_2020_1000_0245.laz", "a/CL2_BX24_2020_1000_0246.laz", "a/other.laz"]
    assert points.covering(keys, (x0 + 10, y0 + 10, x0 + 50, y0 + 50)) == (["a/CL2_BX24_2020_1000_0245.laz"], True)
    hits, covered = points.covering(keys, (x1 - 10, y0 + 10, x1 + 30, y0 + 50))
    assert hits == keys[:2] and covered


def write_las(path, x, y, z, cls):
    header = laspy.LasHeader(point_format=6, version="1.4")
    header.offsets, header.scales = [x.min(), y.min(), 0.0], [0.001, 0.001, 0.001]
    las = laspy.LasData(header)
    las.x, las.y, las.z, las.classification = x, y, z, cls
    las.write(path)


def test_a_tile_is_cropped_to_the_extent(tmp_path):
    rng = np.random.default_rng(1)
    x, y = rng.uniform(1000, 1100, 5000), rng.uniform(2000, 2100, 5000)
    write_las(tmp_path / "tile.laz", x, y, np.full(5000, 10.0), np.full(5000, 2, np.uint8))
    n = points.crop([(tmp_path / "tile.laz").as_uri()], (1020, 2020, 1040, 2040), tmp_path / "out.laz")
    got = points.read(tmp_path / "out.laz")
    assert n == len(got["x"]) == ((x >= 1020) & (x <= 1040) & (y >= 2020) & (y <= 2040)).sum()
    assert got["x"].min() >= 1020 and got["y"].max() <= 2040 and set(got["cls"]) == {2}


def test_the_ground_grid_takes_the_lowest_ground_point():
    pts = {"x": np.array([0.2, 0.3, 0.7, 1.2]), "y": np.array([0.8, 0.9, 0.2, 0.7]), "z": np.array([5.0, 4.0, 9.0, 6.0]),
           "cls": np.array([2, 2, 6, 2])}
    grid, _ = points.ground_grid(pts, (0.0, 0.0, 1.5, 1.0), res=0.5)
    assert grid.shape == (2, 3)
    assert grid[0, 0] == 4.0 and grid[0, 2] == 6.0 and np.isnan(grid[1, 1])    # the building point isn't ground


def test_a_gable_is_fitted_to_building_points():
    walls = [[0.0, 10.0], [12.0, 10.0], [12.0, 0.0], [0.0, 0.0], [0.0, 10.0]]
    rng = np.random.default_rng(2)
    e, n = rng.uniform(-0.4, 12.4, 4000), rng.uniform(-0.4, 10.4, 4000)
    z = 106.0 - np.tan(np.radians(30)) * np.abs(n - 5.0) + rng.normal(0, 0.03, 4000)    # ridge along east-west, 30 degrees
    ground = lambda a, b: np.full(np.shape(a), 100.0)
    flat = lambda a, b: np.full(np.shape(a), np.nan)
    h = create.fit_house(walls, ground, flat, (e, n, z))
    assert h["roof_from"] == "building points" and h["roof_form"] == "gable"
    assert h["roof_pitch_deg"] == pytest.approx(30, abs=1.5)
    assert create.fit_house(walls, ground, flat)["fitted"] is False     # no surface model and no points


def test_a_copc_tile_crops_to_plain_laz(tmp_path):
    # LINZ exports COPC: its index records can't be written back, so the crop leaves them out.
    rng = np.random.default_rng(3)
    x, y = rng.uniform(1000, 1100, 2000), rng.uniform(2000, 2100, 2000)
    header = laspy.LasHeader(point_format=6, version="1.4")
    header.offsets, header.scales = [1000.0, 2000.0, 0.0], [0.001, 0.001, 0.001]
    header.vlrs.append(laspy.VLR(user_id="copc", record_id=1000, description="a stand-in for COPC's index", record_data=b"\0" * 16))
    las = laspy.LasData(header)
    las.x, las.y, las.z, las.classification = x, y, np.full(2000, 5.0), np.full(2000, 6, np.uint8)
    las.write(tmp_path / "tile.laz")
    points.crop([(tmp_path / "tile.laz").as_uri()], (1010, 2010, 1050, 2050), tmp_path / "out.laz")
    assert not any(v.user_id == "copc" for v in laspy.read(tmp_path / "out.laz").header.vlrs)


def test_tiles_with_their_own_offsets_line_up(tmp_path):
    def tile(path, x0, offset):
        header = laspy.LasHeader(point_format=6, version="1.4")
        header.offsets, header.scales = offset, [0.001, 0.001, 0.001]
        las = laspy.LasData(header)
        las.x, las.y = np.array([x0, x0 + 1.0]), np.array([2010.0, 2011.0])
        las.z, las.classification = np.array([5.0, 6.0]), np.array([2, 2], np.uint8)
        las.write(path)

    tile(tmp_path / "a.laz", 1010.0, [1000.0, 2000.0, 0.0])
    tile(tmp_path / "b.laz", 1020.0, [1046.0, 2046.0, 3.0])       # a COPC tile is centred on itself
    points.crop([(tmp_path / n).as_uri() for n in ("a.laz", "b.laz")], (1000, 2000, 1030, 2030), tmp_path / "out.laz")
    got = points.read(tmp_path / "out.laz")
    assert sorted(np.round(got["x"], 3)) == [1010.0, 1011.0, 1020.0, 1021.0]
    assert sorted(np.round(got["y"], 3)) == [2010.0, 2010.0, 2011.0, 2011.0] and sorted(np.round(got["z"], 3)) == [5.0, 5.0, 6.0, 6.0]
