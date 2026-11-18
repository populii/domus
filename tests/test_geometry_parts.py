"""The model's general geometry, on made-up shapes: house outlines and roofs, fits, lines, wall strips and canopy.

These grew in a real site's own tests and test only Domus's functions, so they live here."""

import math

import numpy as np
from shapely.geometry import Polygon

from domus.geo.georef import fit_affine, fit_rigid
from domus.geo.mesh import wall_strip
from domus.model import house, terrain, vegetation


def bumps(*specs, shape=(80, 120)):
    """A canopy of Gaussian crowns: specs are (row, col, height, width in cells)."""
    y, x = np.mgrid[0:shape[0], 0:shape[1]]
    chm = np.zeros(shape)
    for r, c, h, w in specs:
        chm = np.maximum(chm, h * np.exp(-((x - c) ** 2 + (y - r) ** 2) / (2 * w * w)))
    return chm


def test_maximal_rectangles_of_an_l_shape():
    poly = Polygon([(0, 0), (10, 0), (10, 4), (4, 4), (4, 10), (0, 10)])
    rects = sorted(house.maximal_rectangles(poly))
    assert rects == [(0.0, 0.0, 4.0, 10.0), (0.0, 0.0, 10.0, 4.0)]


def test_hip_roof_ridge_height():
    v, f = house.hip_roof((0, 0, 10, 6), z_edge=2.0, pitch=math.radians(30))
    z = np.array(v)[:, 2]
    assert abs(z.max() - (2.0 + 3 * math.tan(math.radians(30)))) < 1e-9
    assert len(f) == 6


def test_fits_recover_known_transforms():
    src = np.array([[0, 0], [10, 0], [10, 5], [0, 5]], float)
    rot = math.radians(23)
    r = np.array([[math.cos(rot), -math.sin(rot)], [math.sin(rot), math.cos(rot)]])
    dst = src @ r.T + [400000, 800000]
    got_rot, _, _, resid = fit_rigid(src, dst)
    assert abs(got_rot - rot) < 1e-9 and np.abs(resid).max() < 1e-6
    _, resid = fit_affine(src, dst)
    assert np.abs(resid).max() < 1e-6


def test_resampled_lines_keep_their_corners_and_steps():
    # A wall line with a 0.3 m step in its top over 2 cm, and a corner.
    line = np.array([[0.0, 0.0, 1.0], [1.03, 0.0, 1.0], [1.05, 0.0, 1.3], [1.05, 2.0, 1.3]])
    pts, s = terrain.resample_line(line, 0.25)
    for v in line:
        assert np.min(np.abs(pts - v).sum(axis=1)) < 1e-9
    assert np.all(np.diff(s) > 0) and np.max(np.diff(s)) <= 1.1 * 0.25


def test_wall_uv_hangs_from_the_top_edge_at_true_scale():
    # A straight 4 m wall with a 1 m batter, toe 40, top 41, 0.3 m thick: v is the distance over
    # the section from the face's top edge, u the distance along the wall.
    line = np.array([[0.0, 0.0], [2.0, 0.0], [4.0, 0.0]])
    m = wall_strip(line, 41.0, 39.85, 0.3, 1, 1.0, 40.0)
    rows = m.uv[~m.cap, 1].reshape(-1, 3)      # the face, top and back (the end caps are apart)
    slope = np.hypot(1.0, 1.0)
    assert np.allclose(rows[:, 0], [-slope - 0.15, -slope, 0.0, 0.3, 0.3 + 1.15])
    assert np.allclose(m.uv[:3, 0], [0.0, 2.0, 4.0])
    # The face's v grows with the slant distance between its vertices.
    v = m.vertices
    assert np.isclose(np.linalg.norm(v[3] - v[6]), rows[2, 0] - rows[1, 0])


def test_wall_end_caps_take_the_texture_across_the_end():
    # The caps' vertices repeat the rows' end points (no new positions) with u across the wall's end
    # and v down from its top, so the texture is not one column of the face smeared over the end.
    line = np.array([[0.0, 0.0], [2.0, 0.0], [4.0, 0.0]])
    m = wall_strip(line, 41.0, 39.85, 0.3, 1, 1.0, 40.0)
    strip, caps = m.vertices[~m.cap], m.vertices[m.cap]
    assert len(caps) == 2 * 5 and all(np.min(np.linalg.norm(strip - p, axis=1)) < 1e-12 for p in caps)
    u, v = m.uv[m.cap].T
    assert np.allclose(u[:5], [0.0, 0.0, 1.0, 1.3, 1.3]) and np.allclose(u[5:], u[:5])
    assert np.allclose(v[:5], [-1.15, -1.0, 0.0, 0.0, -1.15])
    face_rows = m.faces[~np.any(m.cap[m.faces], axis=1)]
    assert len(face_rows) == 4 * 2 * 2 and np.all(m.cap[m.faces].sum(axis=1) % 3 == 0)


def test_flood_gives_each_crown_its_own_slope():
    # A low crown beside a tall one: every cell joins the crown it can be reached from downhill.
    chm = bumps((40, 30, 3.0, 5), (40, 75, 8.0, 7))
    veg = chm > 0.5
    markers = np.zeros(chm.shape, int)
    markers[40, 30], markers[40, 75] = 1, 2
    lab = vegetation.flood(chm, markers, veg)
    assert lab[40, 30] == 1 and lab[40, 75] == 2
    assert lab[40, 36] == 1           # the low crown's own flank is not taken by the tall one
    assert np.all(lab[~veg] == 0)


def test_segment_splits_separate_crowns_and_merges_bumps():
    step = vegetation.STEP
    chm = bumps((40, 25, 6.0, 8), (40, 95, 4.0, 6))           # 17.5 m apart: two trees
    lab = vegetation.segment(chm, chm > 0.5, step)
    assert lab.max() == 2
    assert lab[40, 25] == 1 and lab[40, 95] == 2               # numbered by height
    chm = bumps((40, 55, 6.0, 8), (40, 61, 5.9, 8))            # 1.5 m apart: one crown, two bumps
    lab = vegetation.segment(chm, chm > 0.5, step)
    assert lab.max() == 1


def test_crowns_report_outline_height_and_colour():
    step = vegetation.STEP
    chm = bumps((40, 60, 7.0, 10))
    E, N = np.meshgrid(1000 + np.arange(chm.shape[1]) * step, 2000 - np.arange(chm.shape[0]) * step)
    rgb = np.zeros(chm.shape + (3,)) + [80, 110, 60]
    veg = vegetation.vegetation_mask(chm, rgb)
    lab = vegetation.segment(chm, veg, step)
    (c,) = vegetation.crowns(lab, chm, rgb, E, N, np.zeros(chm.shape), step)
    assert 6.5 < c["height_max"] <= 7.0            # the 98th percentile of the crown's cells
    assert abs(c["apex"][0] - (1000 + 60 * step)) < 0.3 and abs(c["apex"][1] - (2000 - 40 * step)) < 0.3
    assert c["colour"] == "#506e3c"
    assert vegetation.guess_kind(c) == "tree"
    # The outline holds the cells above 0.5 m: a disc of radius w * sqrt(2 ln 14) cells.
    r = 10 * np.sqrt(2 * np.log(7.0 / 0.5)) * step
    assert abs(c["outline"].area - np.pi * r * r) / (np.pi * r * r) < 0.1


def test_greenness_tells_foliage_from_roofs():
    assert vegetation.greenness(np.array([80.0, 110.0, 60.0])) > 0.1          # foliage
    assert vegetation.greenness(np.array([150.0, 155.0, 150.0])) < 0.03       # grey roof
    chm = np.full((10, 10), 5.0)
    roof = np.zeros((10, 10, 3)) + [150, 155, 150]
    assert not vegetation.vegetation_mask(chm, roof).any()
