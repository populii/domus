"""The names Domus is replacing, and the calls that leave out the site: each still works as it did, and says it's
deprecated (a DomusDeprecationWarning) at the line that used it, naming what replaces it. They go in a later release,
which the changelog will say."""

import importlib
import sys

import pytest
import rasterio

import domus
from domus.errors import DomusDeprecationWarning

ABROAD = """[domus]
format = 3

[crs]
epsg = 27700

[model]
origin = [100000.0, 100000.0]

[datums.plans]
label = "The plans' own datum (made up)"
offset_m = 2.5
"""
CRS_SAYS = ("domus.geo.crs.{old} is deprecated: use the site's {new} (domus.project.site.Site; a site that "
            "domus.api.open_site() opens has its path, output, settings() and grid too)")
RASTER_SAYS = "domus.geo.raster.{old} is deprecated: use domus.project.rasters.{new}, handing it the site (domus.project.site.Site)"
NO_SITE_SAYS = ("{part}() was called without its site, which is deprecated: hand it the Site it works on "
                "(domus.project.site.Site), first or as site=")


def said(got):
    """What the warnings `got` said, each of them put at a line of this file (the one that used the old name), none at
    Domus's own."""
    assert got, "nothing was said"
    assert {w.filename for w in got} == {__file__}, [(w.filename, w.lineno) for w in got]
    return [str(w.message) for w in got]


@pytest.mark.parametrize("old", [*domus.MOVED, "video.georef_sfm"])
def test_an_old_module_name_says_its_new_one_and_is_that_module(old, monkeypatch):
    new = "domus." + domus.MOVED.get(old, "capture." + old.removeprefix("video."))
    monkeypatch.delitem(sys.modules, f"domus.{old}", raising=False)        # imported afresh, as site code first does
    with pytest.warns(DomusDeprecationWarning) as got:
        module = importlib.import_module(f"domus.{old}")
    assert f"domus.{old} is {new} now: import it by that name" in said(got)
    assert module is importlib.import_module(new) and module.__spec__.name == new


def test_an_import_statement_is_the_line_it_says(monkeypatch):
    monkeypatch.delitem(sys.modules, "domus.walls", raising=False)
    monkeypatch.delattr(domus, "walls", raising=False)
    with pytest.warns(DomusDeprecationWarning) as got:
        from domus import walls  # noqa: F401
    assert said(got) == ["domus.walls is domus.model.walls now: import it by that name"]
    assert got[0].lineno == test_an_import_statement_is_the_line_it_says.__code__.co_firstlineno + 4


def test_crs_names_from_before_say_what_replaces_them(tmp_path, at_site):
    from domus.geo import crs

    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "site.toml").write_text(ABROAD, encoding="utf-8")
    site = at_site(tmp_path, tmp_path / "out")
    grid, (x, y) = site.grid, site.grid.to_nztm(100000.0, 100000.0)
    uses = [    # crs's name, what replaces it on the Site, a use of the name, and what the Site gives
        ("ROOT", "path", lambda: crs.ROOT, site.path),
        ("OUTPUT", "output", lambda: crs.OUTPUT, site.output),
        ("settings()", "settings()", lambda: crs.settings(), site.settings()),
        ("site_format()", "format()", lambda: crs.site_format(), site.format()),
        ("site_epsg()", "grid.epsg", lambda: crs.site_epsg(), grid.epsg),
        ("label()", "grid.label", lambda: crs.label(), grid.label),
        ("site_to_nztm()", "grid.to_nztm()", lambda: crs.site_to_nztm(100000.0, 100000.0), (x, y)),
        ("nztm_to_site()", "grid.from_nztm()", lambda: crs.nztm_to_site(x, y), grid.from_nztm(x, y)),
        ("site_to_lonlat()", "grid.to_lonlat()", lambda: crs.site_to_lonlat(100000.0, 100000.0), grid.to_lonlat(100000.0, 100000.0)),
        ("datums()", "grid.datums", lambda: crs.datums(), [{"key": "plans", "label": "The plans' own datum (made up)", "offset_m": 2.5}]),
        ("to_model_height()", "grid.to_model_height()", lambda: crs.to_model_height(10.0, "plans"), 7.5),
        ("from_model_height()", "grid.from_model_height()", lambda: crs.from_model_height(7.5, "plans"), 10.0),
    ]
    for old, new, use, value in uses:
        with pytest.warns(DomusDeprecationWarning) as got:
            assert use() == value, old
        assert said(got) == [CRS_SAYS.format(old=old, new=new)]
    s = site.settings()
    with pytest.warns(DomusDeprecationWarning) as got:
        crs.settings.cache_clear()
    assert said(got) == [CRS_SAYS.format(old="settings.cache_clear()", new="forget_settings()")]
    assert site.settings() is not s and site.settings() == s


def test_raster_names_from_before_say_what_replaces_them(project):
    from domus.geo import raster
    from domus.project import rasters

    uses = [    # raster's name, what replaces it in domus.project.rasters, a use of the name, and what that gives
        ("DEM", 'path(site, "dem")', lambda: raster.DEM, rasters.path(project, "dem")),
        ("DSM", 'path(site, "dsm")', lambda: raster.DSM, rasters.path(project, "dsm")),
        ("AERIAL", 'path(site, "aerial")', lambda: raster.AERIAL, rasters.path(project, "aerial")),
        ("NIR", 'path(site, "nir")', lambda: raster.NIR, rasters.path(project, "nir")),
        ("shift()", "shift(site, key)", lambda: raster.shift("aerial"), rasters.shift(project, "aerial")),
        ("dem()", "dem(site)", lambda: raster.dem(), rasters.dem(project)),
        ("dsm()", "dsm(site)", lambda: raster.dsm(), rasters.dsm(project)),
        ("aerial()", "aerial(site)", lambda: raster.aerial(), rasters.aerial(project)),
    ]
    for old, new, use, value in uses:
        with pytest.warns(DomusDeprecationWarning) as got:
            assert use() == value, old
        assert said(got) == [RASTER_SAYS.format(old=old, new=new)]
    with pytest.warns(DomusDeprecationWarning) as got, pytest.raises(rasterio.errors.RasterioIOError):
        raster.nir()                                    # the demo has none: it says so, as rasters.nir(site) does
    assert said(got) == [RASTER_SAYS.format(old="nir()", new="nir(site)")]


def test_a_part_called_without_its_site_says_to_hand_it_one(project, monkeypatch):
    """Site code from before calls some of Domus's parts without their site: each reads the current site, as it did,
    and says to hand it the site. (In the tests' strict mode it's an error.)"""
    from domus import build
    from domus.options import design
    from domus.project import site as sites

    monkeypatch.setattr(sites, "STRICT", False)
    terrace = {"id": "t", "polygon_so": [[1.0, 1.0], [6.0, 1.0], [6.0, 4.0], [1.0, 4.0]], "at_so": [1.0, 1.0], "level": 10.0}
    uses = [    # the part, a call without its site, and what it gives handed the site
        ("domus.options.design.frame", lambda: design.frame(), design.frame(project)),
        ("domus.build.paved_under_canopy", lambda: build.paved_under_canopy(), build.paved_under_canopy(project)),
        ("domus.options.design.Terrace", lambda: design.Terrace(terrace, {"batter": 1.0}).poly,
         design.Terrace(terrace, {"batter": 1.0}, site=project).poly),
    ]
    for part, use, value in uses:
        with pytest.warns(DomusDeprecationWarning) as got:
            assert use() == value, part
        assert said(got) == [NO_SITE_SAYS.format(part=part)]
    with pytest.warns(DomusDeprecationWarning) as got:
        design.frame.cache_clear()
    assert said(got) == [NO_SITE_SAYS.format(part="domus.options.design.frame.cache_clear")]
    assert design.frame(project) is not None
