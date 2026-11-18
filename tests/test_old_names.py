"""The names Domus replaced are gone, after warning since 2026.11.49: the modules' old names, crs's and raster's names
that read the current site, and the calls that left out the site. Each fails as anything missing does."""

import importlib
import subprocess
import sys

import pytest

# The modules' old names, each now at its place in the package's parts (ARCHITECTURE.md).
OLD_MODULES = ["buildcache", "canopy", "cli", "crs", "demo", "design", "drains", "enrich", "features", "geojson",
               "georef", "house", "house_texture", "linz", "marks", "mesh", "newsite", "points", "prep.check",
               "publicdata", "raster", "scan", "scheme", "serve", "sfm", "sfm_georef", "sketch", "terrain", "tin",
               "title", "vegetation", "video", "video.georef_sfm", "wall_looks", "walls"]
OLD_CRS = ["ROOT", "OUTPUT", "settings", "site_format", "site_epsg", "label", "site_to_nztm", "nztm_to_site",
           "site_to_lonlat", "datums", "to_model_height", "from_model_height"]
OLD_RASTER = ["DEM", "DSM", "AERIAL", "NIR", "shift", "dem", "dsm", "aerial", "nir"]


@pytest.mark.parametrize("old", OLD_MODULES)
def test_a_modules_old_name_is_no_module(old):
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(f"domus.{old}")


def test_python_m_domus_cli_runs_nothing():
    r = subprocess.run([sys.executable, "-m", "domus.cli", "--version"], capture_output=True, text=True, check=False)
    assert r.returncode != 0 and "No module named domus.cli" in r.stderr, r.stderr


def test_crs_and_raster_have_no_names_that_read_the_current_site():
    from domus.geo import crs, raster

    assert [name for name in OLD_CRS if hasattr(crs, name)] == []
    assert [name for name in OLD_RASTER if hasattr(raster, name)] == []


def test_a_part_called_without_its_site_is_an_error():
    from domus import build, options
    from domus.model import terrain
    from domus.options import design

    calls = [lambda: design.frame(), lambda: design.settings(), lambda: design.frame.cache_clear(),
             lambda: design.Design({}), lambda: design.Terrace({}, {}), lambda: terrain.ground(),
             lambda: terrain.wall_levels({}), lambda: terrain.build((0, 0, 1, 1), 0.5), lambda: options.load("a.toml"),
             lambda: build.paved_under_canopy(), lambda: build.aerial_sampler(0.0, 0.0),
             lambda: build.ground_photo_sampler(0.0, 0.0)]
    for call in calls:
        with pytest.raises(TypeError, match="'site'"):
            call()
