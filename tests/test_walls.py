"""Walls with posts, such as timber post and board: where their posts stand, and an open end, which has none."""

import json

import numpy as np
import pytest

from domus import walls
from domus.errors import DomusError


def timber(line, **props):
    return walls.from_feature({"geometry": {"type": "LineString", "coordinates": line},
                               "properties": {"id": "w", "material": "timber", **props}})


def test_an_open_end_has_no_post_and_its_boards_keep_their_size():
    line = [[0, 0], [1.2, 0], [2.4, 0], [2.71, 0]]
    w = timber(line, open_ends=["end"])
    assert walls.post_positions(w) == pytest.approx([0, 1.2, 2.4])
    assert walls.texture_u(w, np.array([0, 1.2, 2.4, 2.71])) == pytest.approx([0, 1, 2, 2 + 0.31 / 1.2])
    # Without it, the last 0.31 m gets a post at its end and a whole bay squeezed into it.
    closed = timber(line)
    assert walls.post_positions(closed) == pytest.approx([0, 1.2, 2.4, 2.71])
    assert walls.texture_u(closed, np.array([2.71]))[0] == pytest.approx(3)


def test_an_open_start_counts_its_bays_from_the_first_post():
    w = timber([[0, 0], [0.3, 0], [1.5, 0], [4.9, 0]], open_ends="start")
    u = walls.texture_u(w, np.array([0, 0.3, 1.5, 4.9]))
    assert u == pytest.approx([-0.25, 0, 1, 4])
    assert walls.post_positions(w)[0] == pytest.approx(0.3)


def test_a_long_open_end_still_has_a_post_every_bay():
    assert walls.post_positions(timber([[0, 0], [1.2, 0], [4.0, 0]], open_ends=["end"])) == pytest.approx([0, 1.2, 2.4, 3.6])


def test_open_ends_are_the_start_or_the_end():
    with pytest.raises(DomusError, match="open_ends"):
        timber([[0, 0], [1, 0]], open_ends=["middle"])
    assert timber([[0, 0], [1, 0]], open_ends=["end", "start", "end"])["open_ends"] == ("start", "end")


def test_the_face_at_an_open_end_shows_board_ends_not_a_post(site):
    fc = json.loads((site / "data" / "site" / "walls-existing.geojson").read_text(encoding="utf-8"))
    feature = fc["features"][0]
    feature["properties"] = {**feature["properties"], "material": "timber", "open_ends": ["end"]}
    w = walls.from_feature(feature)
    from domus import terrain

    m, _ = walls.mesh(w, terrain.ground())                                      # its toe is read off the site's ground
    start, end = np.asarray(w["line"])[0], np.asarray(w["line"])[-1]
    cap = np.flatnonzero(m.cap)
    at_end = cap[np.hypot(*(m.vertices[cap, :2] - end).T) < np.hypot(*(m.vertices[cap, :2] - start).T)]
    at_start = np.setdiff1d(cap, at_end)
    assert np.all(np.abs(m.uv[at_start, 0]) < 0.1)                                  # the post the picture has at its edge
    assert np.all(np.abs(m.uv[at_end, 0] % 1 - 0.5) < 0.1)                         # the middle of a bay: boards


def test_a_wall_reads_no_ground_but_the_one_it_is_given(site):
    from domus import terrain

    w = {"id": "W", "line": [[0.0, 0.0], [4.0, 0.0]], "side": 1, "thickness": 0.2, "height": 1.0}
    with pytest.raises(DomusError, match="none was given"):
        walls.wall_levels(w)

    class Flat:
        def sample(self, e, n):
            return np.full(np.shape(e), 40.0)

    _, _, chain, toe, top = walls.wall_levels(w, Flat())
    assert np.allclose(toe, 40.0) and np.allclose(top, 41.0) and chain[-1] == pytest.approx(4.0)
    assert terrain.wall_levels is not walls.wall_levels                 # terrain's reads the site's own ground
