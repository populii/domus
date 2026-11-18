"""Triangles for points with a height each (domus.geo.mesh.delaunay_by_height): Delaunay's, worked out about the first
point, but where four corners lie within a few millimetres of one circle, so that Delaunay could cut them either way,
along the diagonal whose ends are closer in height."""

import numpy as np
import pytest
from scipy.spatial import ConvexHull, Delaunay

from domus.geo.mesh import CIRCLE_NEAR_M, delaunay_by_height

AT = np.array([1500000.0, 5500000.0])       # on a grid as large as New Zealand's


def cuts(tris):
    return {frozenset(map(int, t)) for t in tris}


def area(points, tris):
    p = np.asarray(points, float)[:, :2] - points[0][:2]
    a, b, c = p[tris[:, 0]], p[tris[:, 1]], p[tris[:, 2]]
    return 0.5 * ((b[:, 0] - a[:, 0]) * (c[:, 1] - a[:, 1]) - (b[:, 1] - a[:, 1]) * (c[:, 0] - a[:, 0]))


def test_away_from_circles_they_are_delaunays_about_the_first_point():
    """Corners at random, where no four lie near one circle: Delaunay's triangles, every corner among them, counter-
    clockwise, filling the corners' hull, on a grid as large as New Zealand's."""
    rng = np.random.default_rng(2)
    for k in (3, 4, 6, 10, 30):
        for size in (3.0, 20.0):
            pts = AT + rng.uniform(0.0, size, (k, 2))
            z = rng.uniform(40.0, 43.0, k)
            tris = delaunay_by_height(pts, z)
            assert cuts(tris) == cuts(Delaunay(pts - pts[0]).simplices)
            assert set(np.unique(tris)) == set(range(k))
            a = area(pts, tris)
            assert (a > 0).all() and a.sum() == pytest.approx(ConvexHull(pts - pts[0]).volume, rel=1e-9)


RECT = [[0.0, 0.0], [4.0, 0.0], [4.0, 1.5], [0.0, 1.5]]


@pytest.mark.parametrize(("z", "diagonal"), [([42.8, 42.8, 42.83, 42.8], {1, 3}),     # 1-3 closer in height
                                            ([42.8, 42.83, 42.8, 42.8], {0, 2}),     # 0-2 closer
                                            ([42.8, 42.9, 42.8, 42.9], {0, 2}),      # as close: from corner 0
                                            ([42.8, 42.8, 42.8, 42.8], {0, 2})])     # level: from corner 0
def test_four_corners_on_one_circle_are_cut_along_the_diagonal_closer_in_height(z, diagonal):
    pts = AT + np.array(RECT)
    tris = delaunay_by_height(pts, z)
    assert len(tris) == 2 and all(diagonal <= set(map(int, t)) for t in tris)


def test_a_corner_off_the_circle_by_less_than_the_tolerance_counts_as_on_it():
    """To the millimetre corners are given in, a near-rectangle is a rectangle: cut by height up to CIRCLE_NEAR_M off
    the circle, by Delaunay beyond it."""
    for off, by_height in ((0.0009, True), (CIRCLE_NEAR_M * 0.8, True), (CIRCLE_NEAR_M * 3, False)):
        for inward in (True, False):
            q = np.array(RECT)
            q[2, 0] += -off if inward else off      # corner 2 moved along the long side: in, or out of, the circle
            pts = AT + q
            z = [42.8, 42.8, 42.83, 42.8] if inward else [42.8, 42.83, 42.8, 42.8]
            tris = delaunay_by_height(pts, z)
            want = cuts(Delaunay(pts - pts[0]).simplices)
            if by_height:
                closer = {1, 3} if inward else {0, 2}
                assert all(closer <= set(map(int, t)) for t in tris), (off, inward)
            else:
                assert cuts(tris) == want, (off, inward)


def test_among_cuts_again_only_pairs_with_one_of_those_corners():
    """`among` limits the cutting by height to the pairs of triangles with one of its points among their corners."""
    pts = AT + np.array([*RECT, [10.0, 0.0], [14.0, 0.0], [14.0, 1.5], [10.0, 1.5]])
    z = [42.8, 42.8, 42.83, 42.8, 42.8, 42.8, 42.83, 42.8]          # in each rectangle, the diagonal from its corner 1
    every = delaunay_by_height(pts, z)
    some = delaunay_by_height(pts, z, among=[5])
    none = delaunay_by_height(pts, z, among=[])
    first = cuts(Delaunay(pts - pts[0]).simplices)
    assert {1, 3} in [frozenset(t) & {1, 3} for t in cuts(every)] and {5, 7} in [frozenset(t) & {5, 7} for t in cuts(every)]
    assert cuts(none) == first
    left = {t for t in cuts(some) if max(t) <= 3}
    right = {t for t in cuts(some) if min(t) >= 4}
    assert left == {t for t in first if max(t) <= 3}                 # the first rectangle as Delaunay cut it
    assert all({5, 7} <= set(t) for t in right if len(t & {4, 5, 6, 7}) == 3)


def test_a_grid_is_cut_cell_by_cell_down_its_slopes():
    """A grid's every cell has its corners on one circle: each is cut along the diagonal whose ends are closer in
    height, as the ground model cuts its cells."""
    rng = np.random.default_rng(5)
    e, n = np.meshgrid(np.arange(6) * 0.5, np.arange(5) * 0.5)
    pts = AT + np.column_stack([e.ravel(), n.ravel()])
    z = 40.0 + rng.uniform(0.0, 1.0, len(pts))
    tris = delaunay_by_height(pts, z)
    have = cuts(tris)
    for j in range(4):
        for i in range(5):
            a, b, c, d = j * 6 + i, j * 6 + i + 1, (j + 1) * 6 + i + 1, (j + 1) * 6 + i     # the cell's corners in turn
            if abs(z[a] - z[c]) < abs(z[b] - z[d]) - 1e-9:
                want = (a, c)
            elif abs(z[b] - z[d]) < abs(z[a] - z[c]) - 1e-9:
                want = (b, d)
            else:
                want = (a, c) if min(a, c) < min(b, d) else (b, d)
            assert any(set(want) <= t and len(t & {a, b, c, d}) == 3 for t in have), (i, j)


def test_given_triangles_are_cut_again_and_the_rest_left_as_they_were():
    """Given its triangles (a Delaunay of the caller's own, as the ground model's about its origin), it cuts those again
    by height, and leaves each one it doesn't cut as it was given, in its place and its turn, so with nothing to cut
    nothing moves."""
    pts = AT + np.array([*RECT, [10.0, 0.0], [14.0, 0.0], [14.0, 1.5], [10.0, 1.5]])
    mine = Delaunay(pts - pts[0]).simplices[:, [0, 2, 1]]                 # clockwise, as some callers keep them
    z = np.full(8, 42.8)
    for a, b, c in ((0, 1, 2), (4, 5, 6)):          # in each rectangle, a corner raised so the diagonal it hasn't is closer
        has_ac = any({a, c} <= set(t) for t in mine.tolist())
        z[a if has_ac else b] += 0.03
    same = delaunay_by_height(pts, z, triangles=mine, among=[])
    assert (same == mine).all()
    cut = delaunay_by_height(pts, z, triangles=mine)
    assert cuts(cut) == cuts(delaunay_by_height(pts, z)) != cuts(mine)
    kept = (cut == mine).all(axis=1)
    assert (area(pts, cut[~kept]) > 0).all()                              # what it cuts, it gives counter-clockwise
    only = delaunay_by_height(pts, z, triangles=mine, among=[6])
    left = [k for k, t in enumerate(mine.tolist()) if max(t) <= 3]
    right = [k for k, t in enumerate(mine.tolist()) if min(t) >= 4]
    assert (only[left] == mine[left]).all() and not (only[right] == mine[right]).all()
