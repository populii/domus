"""The house's drawn faces: weatherboards at the site's size, counted up from the plinth, and drawn over a
face's photo when the site asks."""

import numpy as np
from PIL import Image


def spec(**extra):
    return {"kind": "wall", "fl": 3.0, "plinth_top": 1.0, "openings": [], **extra}


def lap_rows(shade):
    """The pixel rows a lap's shadow line starts on, going down the image: rows darker than the one above."""
    dark = shade < 0.7
    return np.flatnonzero(dark & ~np.r_[False, dark[:-1]])


def test_boards_default_to_150_mm_and_are_not_drawn_on_photos(site):
    from domus import house_texture

    assert house_texture.boards() == (0.15, False)


def test_boards_take_their_size_from_the_site(site, monkeypatch):
    from domus import house_texture

    monkeypatch.setattr(house_texture, "settings", lambda: {"house": {"board_m": 0.117, "boards_on_photos": True}})
    assert house_texture.boards() == (0.117, True)


def test_laps_are_a_board_apart_counted_up_from_the_datum(site):
    from domus import house_texture

    ppm, z1 = 200, 3.0
    zc = z1 - (np.arange(600) + 0.5) / ppm
    shade = house_texture.lap_shade(zc, 1.0, 0.117, ppm)
    rows = lap_rows(shade)
    heights = z1 - (rows + 1) / ppm                      # the bottom edge of the row the shadow ends on
    assert np.allclose(np.diff(rows), 0.117 * ppm, atol=1)
    below = (heights - 1.0) / 0.117
    assert np.allclose(below, np.round(below), atol=0.08), below
    # a lap's shadow is never thinner than a pixel, however small the boards are drawn
    coarse = house_texture.lap_shade(3.0 - (np.arange(60) + 0.5) / 20, 1.0, 0.117, 20)
    assert (coarse < 0.7).sum() >= 15


def test_a_face_counts_its_boards_from_the_plinth_and_says_where_they_show(site, monkeypatch):
    from domus import house_texture

    monkeypatch.setattr(house_texture, "settings", lambda: {"house": {"board_m": 0.2}})
    opening = {"kind": "window", "u0": 1.0, "u1": 2.0, "sill": 1.6, "head": 2.4}
    img, clad = house_texture.draw_face(4.0, 0.0, 3.0, spec(openings=[opening]), "north", ppm=100)
    assert img.size == (400, 300) and clad.shape == (300, 400)
    assert not clad[205:, :].any()                        # the plaster under the plinth top (1.0) is not boards
    assert not clad[70:130, 110:190].any()                # nor is the window
    assert not clad[100, :5].any() and not clad[100, -5:].any()     # nor the corner boards
    assert clad[20:50, 250:350].all()                     # the wall beside it is
    column = np.asarray(img.convert("L"), float)[:200, 300]
    rows = np.flatnonzero((column[1:-1] < column[:-2]) & (column[1:-1] <= column[2:]) & (column[1:-1] < column.mean() - 15)) + 1
    assert len(rows) >= 8 and np.allclose(np.diff(rows), 20, atol=2), rows     # a lap every 0.2 m
    assert abs((200 - rows[-1]) % 20 - 0) <= 3 or abs((200 - rows[-1]) % 20 - 20) <= 3     # the lowest lap is on the plinth's top


def test_boards_are_drawn_over_a_photo_only_where_the_boards_show(site, monkeypatch):
    from domus import house_texture

    monkeypatch.setattr(house_texture, "settings", lambda: {"house": {"board_m": 0.2, "boards_on_photos": True}})
    photo = np.full((300, 400, 3), 180, np.uint8)
    photo[::7] = 120                                      # the photo's own lines, at a spacing that is not the boards'
    clad = np.zeros((300, 400), bool)
    clad[:200, 100:300] = True
    out = np.asarray(house_texture.boards_on_photo(Image.fromarray(photo), clad, 0.0, 3.0, spec(), 100), float)
    assert (out[:, :100] == photo[:, :100]).all() and (out[200:] == photo[200:]).all()     # untouched off the boards
    column = out[:200, 200, 0]
    rows = lap_rows(column / column.max())
    assert len(rows) == 10 and np.allclose(np.diff(rows), 20, atol=1), rows               # the boards' laps, one every 0.2 m
    between = column[np.setdiff1d(np.arange(30, 180), (rows[:, None] + np.arange(-1, 4)).ravel())]
    assert between.std() < 12                             # and the photo's own lines are gone from between them
    assert abs(column.mean() - photo[:200, 200, 0].mean()) < 8     # as bright overall as the photo was
