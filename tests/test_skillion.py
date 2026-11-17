"""Skillion roofs: the plane, the walls that meet it, and telling one from a hip in the LiDAR."""

import math

import numpy as np
import pytest

from domus import create, house
from domus.prep import check

RING = [[1005.0, 2015.0], [1017.0, 2015.0], [1017.0, 2006.0], [1005.0, 2006.0], [1005.0, 2015.0]]
ground = lambda e, n: np.full(np.shape(e), 40.0)


def feats():
    return [{"geometry": {"type": "Polygon", "coordinates": [RING]},
             "properties": {"id": "house-walls", "soffit": 42.8, "source": "test"}}]


def rising_east(e, n):
    return 43.0 + (np.asarray(e) - 1005.0) * math.tan(math.radians(7.0))


def test_the_roof_is_one_plane_rising_towards_the_bearing():
    m = house.build(feats(), 40.3, 7.0, 43.0, 0.4, ground, form="skillion", azimuth_deg=90.0)
    v = m.roof.vertices
    assert np.allclose(v[:, 2], rising_east(v[:, 0], v[:, 1]), atol=1e-6)
    assert m.info["roof_form"] == "skillion" and m.info["ridge_max"] > m.info["roof_eave_edge"]


def test_the_walls_follow_it_and_the_soffit_stays_under_it():
    m = house.build(feats(), 40.3, 7.0, 43.0, 0.4, ground, form="skillion", azimuth_deg=90.0)
    east = 43.0 + 12.0 * math.tan(math.radians(7.0)) - 0.2
    tops = {round(f.z1, 3) for f in m.faces}
    assert round(east, 3) in tops and 42.8 in tops
    for mesh, key in m.solids["eaves"]:
        if key == "soffit":
            v = mesh.vertices
            assert np.allclose(v[:, 2], rising_east(v[:, 0], v[:, 1]) - 0.2, atol=1e-6)


def test_the_roof_faces_up_whichever_way_the_outline_runs():
    for ring in (RING, RING[::-1]):
        f = feats()
        f[0]["geometry"]["coordinates"] = [ring]
        roof = house.build(f, 40.3, 7.0, 43.0, 0.4, ground, form="skillion", azimuth_deg=200.0).roof
        for a, b, c in np.asarray(roof.faces):
            v0, v1, v2 = roof.vertices[[a, b, c]]
            assert np.cross(v1 - v0, v2 - v0)[2] > 0


def test_the_check_reads_the_same_plane():
    fr = house.HouseFrame(RING)
    surf = house.roof_surface(fr, fr.local(np.array(RING[:-1])), {"roof_pitch_deg": 7.0, "roof_plane_at_wall": 43.0, "eaves_overhang_m": 0.4, "roof_form": "skillion", "roof_azimuth_deg": 90.0})
    e, n = np.array([1010.0, 1100.0]), np.array([2010.0, 2010.0])
    loc = fr.local(np.column_stack([e, n]))
    z = surf(loc[:, 0], loc[:, 1])
    assert z[0] == pytest.approx(rising_east(1010.0, 2010.0)) and z[1] == -np.inf


def test_a_hip_is_still_the_default():
    m = house.build(feats(), 40.3, 25.0, 43.0, 0.4, ground)
    assert "roof_form" not in m.info and m.info["roof_rectangles"] >= 1


def test_the_fit_finds_a_skillion_and_its_bearing():
    h = create.fit_house(RING, ground, rising_east)
    assert h["roof_form"] == "skillion"
    assert h["roof_azimuth_deg"] == pytest.approx(90.0, abs=0.5) and h["roof_pitch_deg"] == pytest.approx(7.0, abs=0.2)
    assert h["roof_plane_at_wall"] == pytest.approx(43.0, abs=0.05) and h["soffit"] < h["roof_plane_at_wall"]


def test_the_fit_ignores_a_tree_over_the_roof():
    def with_tree(e, n):
        return np.where((np.asarray(e) > 1014) & (np.asarray(n) > 2012), 49.0, rising_east(e, n))

    h = create.fit_house(RING, ground, with_tree)
    assert h["roof_form"] == "skillion" and h["roof_pitch_deg"] == pytest.approx(7.0, abs=0.3)


def test_the_fit_keeps_a_hip_a_hip():
    def hip(e, n):
        return 42.8 + np.minimum(np.minimum(np.asarray(e) - 1005, 1017 - np.asarray(e)), np.minimum(np.asarray(n) - 2006, 2015 - np.asarray(n))) * 0.5

    assert "roof_form" not in create.fit_house(RING, ground, hip)


def test_a_low_or_steep_plane_isnt_taken_for_a_skillion():
    assert "roof_form" not in create.fit_house(RING, ground, lambda e, n: 41.0 + (np.asarray(e) - 1005.0) * 0.02)
    assert "roof_form" not in create.fit_house(RING, ground, lambda e, n: 43.0 + (np.asarray(e) - 1005.0) * 1.0)


def test_the_check_judges_a_skillion_by_its_fit_not_a_plan_shift():
    assert check.roof_failure("skillion", (0.5, -0.8), 0.09) is None
    assert "off the model" in check.roof_failure("skillion", (0.0, 0.0), 0.4)
    assert "moved" in check.roof_failure("hip", (0.8, 0.8), 0.09)
    assert check.roof_failure("hip", (0.0, 0.0), 0.4) is None


def ridge_north(e, n):
    """A gable with its ridge along x = 1011, 30 degrees."""
    return 45.0 - np.abs(np.asarray(e) - 1011.0) * math.tan(math.radians(30.0))


def test_a_gable_is_two_planes_meeting_at_the_ridge():
    # roof_plane_at_wall is at the outline's lowest point, 6 m off the ridge
    plane = 45.0 - 6.0 * math.tan(math.radians(30.0))
    m = house.build(feats(), 40.3, 30.0, plane, 0.4, ground, form="gable", azimuth_deg=0.0)
    v = m.roof.vertices
    assert np.allclose(v[:, 2], ridge_north(v[:, 0], v[:, 1]), atol=1e-6)
    assert m.info["ridge_max"] == pytest.approx(45.0, abs=1e-3)
    assert m.info["roof_form"] == "gable"
    for a, b, c in np.asarray(m.roof.faces):
        v0, v1, v2 = v[[a, b, c]]
        assert np.cross(v1 - v0, v2 - v0)[2] > 0


def test_the_gable_ends_reach_up_to_the_ridge_and_the_sides_stay_at_the_eaves():
    plane = 45.0 - 6.0 * math.tan(math.radians(30.0))
    m = house.build(feats(), 40.3, 30.0, plane, 0.4, ground, form="gable", azimuth_deg=0.0)
    gap = plane - 42.8
    ends = [f for f in m.faces if f.width == pytest.approx(12.0)]
    sides = [f for f in m.faces if f.width == pytest.approx(9.0)]
    assert len(ends) == 2 and all(f.z1 == pytest.approx(45.0 - gap) for f in ends)
    assert all(f.z1 == pytest.approx(42.8) for f in sides)
    # every wall top sits under the roof by the build-up, the peak included
    for f in ends:
        tops = f.mesh.vertices[f.mesh.vertices[:, 2] > 41.0]
        assert np.allclose(tops[:, 2], ridge_north(tops[:, 0], tops[:, 1]) - gap, atol=1e-6)


def test_a_ridge_can_sit_off_centre():
    fr = house.HouseFrame(RING)
    surf = house.roof_surface(fr, fr.local(np.array(RING[:-1])), {"roof_pitch_deg": 30.0, "roof_plane_at_wall": 42.0, "eaves_overhang_m": 0.4,
                                                                 "roof_form": "gable", "roof_azimuth_deg": 0.0, "roof_ridge_offset_m": 1.0})
    loc = fr.local(np.array([[1012.0, 2010.0], [1011.0, 2010.0], [1013.0, 2010.0]]))
    z = surf(loc[:, 0], loc[:, 1])
    assert z[0] > z[1] and z[0] > z[2] and z[1] == pytest.approx(z[2])


def test_the_fit_finds_a_gable_and_its_ridge():
    h = create.fit_house(RING, ground, lambda e, n: ridge_north(e, n) + 1.0)     # eaves far enough above the ground to be a house
    assert h["roof_form"] == "gable"
    assert h["roof_azimuth_deg"] % 180 == pytest.approx(0.0, abs=0.5) or h["roof_azimuth_deg"] % 180 == pytest.approx(180.0, abs=0.5)
    assert h["roof_pitch_deg"] == pytest.approx(30.0, abs=1.0) and abs(h["roof_ridge_offset_m"]) < 0.3


def test_the_fit_finds_a_ridge_along_the_other_walls_off_centre():
    h = create.fit_house(RING, ground, lambda e, n: 45.0 - np.abs(np.asarray(n) - 2011.5) * math.tan(math.radians(25.0)))
    assert h["roof_form"] == "gable" and h["roof_azimuth_deg"] == pytest.approx(90.0, abs=0.5)
    assert abs(h["roof_ridge_offset_m"]) == pytest.approx(1.0, abs=0.3)


def test_a_steep_tree_covered_or_unknown_form_is_handled():
    with pytest.raises(ValueError, match="roof_form"):
        house.build(feats(), 40.3, 30.0, 43.0, 0.4, ground, form="dome", azimuth_deg=0.0)
    assert "moved" not in (check.roof_failure("gable", (0.8, 0.8), 0.1) or "")
    assert "off the model" in check.roof_failure("gable", (0.0, 0.0), 0.4)
