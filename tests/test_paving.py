"""Paving under trees painted into the aerial photo (data/site/paved-under-canopy.geojson), on a made-up photo."""

import json

import numpy as np
import pytest
import shapely
from PIL import Image
from shapely.geometry import Point, box

from domus.errors import DomusError

PAVING, GRASS, CROWN = (170, 168, 162), (110, 140, 80), (52, 88, 44)
AREA = box(10.5, 4.0, 18.0, 16.0)       # round the drive under the made-up tree, and some of it in sun


def photo(E, N):
    """A made-up aerial photo: grass west of E 10 and grey paving east of it, a tree's crown 4 m across over the paving
    at (14, 10), its shade on the paving to the south-west with spots of light in it, and a little texture."""
    rgb = np.where((E < 10)[..., None], GRASS, PAVING).astype(float)
    shade = (np.hypot(E - 13.0, N - 8.0) < 2.2) & (E >= 10)
    light = 0.55 + 0.1 * (np.sin(E * 23.0) * np.cos(N * 19.0) > 0.6)
    rgb[shade] *= light[shade][:, None]
    rgb[np.hypot(E - 14.0, N - 10.0) < 2.0] = CROWN
    rgb += 4 * np.sin(E * 31.7 + N * 17.3)[..., None]
    return rgb.clip(0, 255).astype(np.uint8)


def grid(px=0.08):
    """Pixel centres over 16 m by 16 m, as the build's drapes are laid out: rows from the north."""
    return np.meshgrid(5 + (np.arange(200) + 0.5) * px, 18 - (np.arange(200) + 0.5) * px)


def test_the_paving_under_a_tree_is_painted_in_its_colour_in_sun():
    from domus import build

    E, N = grid()
    before, after = photo(E, N).astype(int), build.paint_paving(photo, E, N, AREA).astype(int)
    far = ~shapely.contains_xy(AREA.buffer(1.5), E, N)
    assert (after[far] == before[far]).all()                                        # nothing changes away from it
    under = np.hypot(E - 14.0, N - 10.0) < 1.2                                       # well inside the crown
    assert np.abs(after[under] - PAVING).max() < 8                                  # paving, in its colour in sun
    shaded = (np.hypot(E - 13.0, N - 8.0) < 1.5) & (np.hypot(E - 14.0, N - 10.0) > 2.6) & (E > 11.5)
    assert before[shaded].mean() < 0.7 * np.mean(PAVING) and after[shaded].mean() > 0.9 * np.mean(PAVING)
    sunlit = (E > 16.0) & (E < 17.0) & (N > 13.5) & (N < 15.0)
    assert np.abs(after[sunlit] - before[sunlit]).max() <= 6                        # paving in sun stays itself
    # With a picture of the paving, the painted paving takes its grain; without one, its plain colour.
    stripes = 120 + 30 * np.sin(np.arange(96) / 96 * 2 * np.pi * 6)[None, :] * np.ones((64, 1))
    grained = build.paint_paving(photo, E, N, AREA, grain=(stripes.astype(np.float32), (2.4, 1.6))).astype(int)
    assert grained[under].mean(axis=1).std() > 10 and after[under].mean(axis=1).std() < 1


def test_its_settings_change_what_counts_as_foliage_and_shade():
    from domus import build

    E, N = grid()
    none = {"foliage_green": [0.8, 0.9], "foliage_dark": [0.05, 0.01], "shade_dark": [0.2, 0.1]}
    after = build.paint_paving(photo, E, N, AREA, none).astype(int)
    assert np.abs(after - photo(E, N).astype(int)).max() <= 1                       # nothing taken for either
    with pytest.raises(DomusError, match="shade_dark = \\[0.7, 0.7\\].*two different numbers"):
        build.paint_paving(photo, E, N, AREA, {"shade_dark": [0.7, 0.7]})
    with pytest.raises(DomusError, match="foliage_dark = 0.5 .*two different numbers"):
        build.paint_paving(photo, E, N, AREA, {"foliage_dark": 0.5})


def test_an_area_with_no_paving_in_sun_says_so():
    from domus import build

    E, N = grid()
    with pytest.raises(DomusError, match="no paving in sun"):
        build.paint_paving(photo, E, N, box(5.5, 4.0, 9.5, 16.0))                  # only grass
    with pytest.raises(DomusError, match="no paving in sun"):
        build.paint_paving(photo, E, N, AREA, {"sun_green": -1.0})


def test_the_areas_and_the_paving_picture_come_from_the_site(tmp_path, at_site):
    from domus import build

    at_site(tmp_path)
    assert build.paved_under_canopy() is None and build.paving_grain() is None
    (tmp_path / "data" / "site").mkdir(parents=True)
    feats = [{"type": "Feature", "properties": {"id": f"a{k}"}, "geometry": {"type": "Polygon", "coordinates": [list(
        b.exterior.coords)]}} for k, b in enumerate((box(0, 0, 2, 2), box(5, 0, 7, 2)))]
    (tmp_path / "data" / "site" / "paved-under-canopy.geojson").write_text(
        json.dumps({"type": "FeatureCollection", "features": feats}), encoding="utf-8")
    area = build.paved_under_canopy()
    assert area.area == pytest.approx(8.0) and area.contains(Point(1, 1)) and area.contains(Point(6, 1))   # both count
    (tmp_path / "data" / "features").mkdir()
    Image.new("RGB", (48, 32), (120, 120, 120)).save(tmp_path / "data" / "features" / "paving.jpg")
    tex, size = build.paving_grain()
    assert tex.shape == (32, 48) and size == (2.4, 1.6)
