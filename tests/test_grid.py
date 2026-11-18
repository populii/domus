"""A site's grid (domus.geo.crs.Grid), made from its settings and reading no site; the Site's own settings, format and
grid (domus.project.site); and crs's names from before the split, which give the current site's."""

import numpy as np
import pytest
from pyproj import Transformer

from domus.errors import DomusError
from domus.geo.crs import LONLAT_EPSG, NZTM_EPSG, Datum, Grid
from domus.project import formats
from domus.project.site import Site

SETTINGS = {"crs": {"epsg": 27700, "vertical": "ODN"}, "model": {"origin": [100000.0, 100000.0]},
            "datums": {"plans": {"label": "The plans' own datum (made up)", "offset_m": 57.3}, "old": {"offset_m": -1}}}
ABROAD = """[domus]
format = 3

[crs]
epsg = 27700
vertical = "ODN"

[model]
origin = [100000.0, 100000.0]

[datums.plans]
label = "The plans' own datum (made up)"
offset_m = 2.5
"""


def test_a_grid_is_made_from_settings_alone():
    grid = Grid.from_settings(SETTINGS)
    assert grid.epsg == 27700 and grid.label == "OSGB36 / British National Grid (EPSG:27700)" and grid.vertical == "ODN"
    assert grid.origin == (100000.0, 100000.0)
    assert grid.datums == (Datum("plans", "The plans' own datum (made up)", 57.3), Datum("old", "old", -1))
    assert Grid.from_settings({"crs": {"epsg": 2193, "label": "Our grid"}}).label == "Our grid"
    assert Grid.from_settings({"crs": {"epsg": "2193"}}) == Grid(2193)


def test_it_turns_points_as_pyproj_does():
    grid = Grid.from_settings(SETTINGS)
    e, n = [100000.0, 100250.5], [100000.0, 99800.25]
    x, y = Transformer.from_crs(27700, NZTM_EPSG, always_xy=True).transform(e, n)
    assert grid.to_nztm(e, n) == (x, y)
    assert grid.from_nztm(x, y) == Transformer.from_crs(NZTM_EPSG, 27700, always_xy=True).transform(x, y)
    assert np.allclose(grid.from_nztm(x, y), (e, n))
    assert grid.to_lonlat(e, n) == Transformer.from_crs(27700, LONLAT_EPSG, always_xy=True).transform(e, n)


def test_it_turns_heights_between_its_datums():
    grid = Grid.from_settings(SETTINGS)
    assert grid.to_model_height(100.0, "plans") == 100.0 - 57.3
    assert grid.from_model_height(grid.to_model_height(100.0, "plans"), "plans") == pytest.approx(100.0)
    assert grid.datum("old").offset_m == -1
    with pytest.raises(KeyError):
        grid.to_model_height(1.0, "none")


def test_a_grid_needs_its_epsg_and_each_datum_its_offset():
    with pytest.raises(DomusError, match=r"\[crs\] epsg is missing"):
        Grid.from_settings({"crs": {}})
    with pytest.raises(DomusError, match=r"\[datums.plans\] has no offset_m"):
        Grid.from_settings({"crs": {"epsg": 2193}, "datums": {"plans": {"label": "Plans"}}})


def write_site(root, text=ABROAD):
    (root / "data").mkdir(parents=True)
    (root / "data" / "site.toml").write_text(text, encoding="utf-8")
    return root


def test_a_site_has_its_settings_format_and_grid(tmp_path):
    site = Site(write_site(tmp_path / "abroad"))
    assert site.settings_file == (tmp_path / "abroad" / "data" / "site.toml").resolve()
    s = site.settings()
    assert s["crs"]["epsg"] == 27700 and s["domus"]["format"] == formats.FORMAT and site.settings() is s
    assert site.format() == 3                                   # its file's own format, which settings() reads at this Domus's
    assert site.grid == Grid.from_settings(s) and site.grid is site.grid
    assert site.grid.datum("plans").offset_m == 2.5


def test_it_reads_its_settings_once_until_it_forgets_them(tmp_path):
    site = Site(write_site(tmp_path / "abroad"))
    s, grid = site.settings(), site.grid
    site.settings_file.write_text(ABROAD.replace("27700", "2193").replace("format = 3", "format = 4"), encoding="utf-8")
    assert site.settings() is s and site.grid is grid and site.grid.epsg == 27700
    assert site.format() == 4                                   # the file as it is now
    site.forget_settings()
    assert site.settings()["crs"]["epsg"] == 2193 and site.grid.epsg == 2193


@pytest.mark.filterwarnings("ignore::domus.errors.DomusDeprecationWarning")      # crs's names from before
def test_crs_names_from_before_give_the_current_sites(tmp_path, at_site):
    from domus import crs  # by its old name, as site code imports it

    site = at_site(write_site(tmp_path / "abroad"), tmp_path / "out")
    assert crs.ROOT == site.path and crs.OUTPUT == site.output == (tmp_path / "out").resolve()
    s = crs.settings()
    assert s is site.settings() and crs.site_format() == site.format() == 3
    assert crs.site_epsg() == 27700 and crs.label() == site.grid.label == "OSGB36 / British National Grid (EPSG:27700)"
    assert crs.datums() == [{"key": "plans", "label": "The plans' own datum (made up)", "offset_m": 2.5}]
    assert crs.to_model_height(10.0, "plans") == 7.5 and crs.from_model_height(7.5, "plans") == 10.0
    x, y = crs.site_to_nztm(100000.0, 100000.0)
    assert (x, y) == site.grid.to_nztm(100000.0, 100000.0) and crs.nztm_to_site(x, y) == site.grid.from_nztm(x, y)
    assert crs.site_to_lonlat(100000.0, 100000.0) == site.grid.to_lonlat(100000.0, 100000.0)
    crs.settings.cache_clear()
    assert crs.settings() is not s and crs.settings() == s
