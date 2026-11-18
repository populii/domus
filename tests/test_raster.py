"""Rasters and scanned sheets read at site coordinates, and the shift a site can give one that sits off its ground."""

import json

import numpy as np
from PIL import Image


def test_a_raster_has_no_shift_unless_the_site_gives_one(site, monkeypatch, project):
    from domus.project import rasters

    assert rasters.shift(project, "aerial") == (0.0, 0.0)
    monkeypatch.setattr(project, "settings", lambda: {"rasters": {"shift_m": {"aerial": [-0.5, 0.25]}}})
    assert rasters.shift(project, "aerial") == (-0.5, 0.25)
    assert rasters.shift(project, "dem") == (0.0, 0.0)


def test_a_shifted_raster_shows_what_its_file_has_that_far_back(site, project):
    from domus.geo import raster
    from domus.project import rasters

    e0, n0 = project.settings()["model"]["origin"][:2]
    e, n = np.meshgrid(e0 + np.linspace(-6, 6, 13), n0 + np.linspace(-6, 6, 13))
    plain = raster.Raster(rasters.path(project, "dem"), project.grid.epsg)
    moved = raster.Raster(rasters.path(project, "dem"), project.grid.epsg, shift=(-0.5, 0.25))      # the file's content moved 0.5 m west and 0.25 m north
    assert np.allclose(moved.sample(e, n), plain.sample(e + 0.5, n - 0.25), equal_nan=True)
    assert moved.sample(e, n).shape == e.shape
    assert not np.allclose(moved.sample(e, n), plain.sample(e, n), equal_nan=True)


def test_a_scanned_sheet_can_be_shifted(site, project):
    from domus import build

    e0, n0 = project.settings()["model"]["origin"][:2]
    scan = np.full((200, 200), 255, np.uint8)
    scan[95:105, 95:105] = 0                                   # one blot of ink in the middle of the sheet
    folder = site / "data" / "georef"
    folder.mkdir(parents=True, exist_ok=True)
    Image.fromarray(scan).save(folder / "blot.png")
    # 10 pixels to the metre, north up, the sheet's middle at the model's origin
    a = [[0.1, 0.0, e0 - 10.0], [0.0, -0.1, n0 + 10.0]]
    (folder / "blot.json").write_text(json.dumps({"A": a, "image": "data/georef/blot.png"}), encoding="utf-8")
    try:
        e, n = np.meshgrid(e0 + np.arange(-8, 8, 0.1), n0 + np.arange(8, -8, -0.1))

        def blot(sampler):
            rgb = sampler(e, n).astype(float)
            ink = rgb[..., 0] - rgb[..., 1]                    # the linework is drawn in red
            w = np.clip(ink - ink.max() / 2, 0, None)
            return float((w * e).sum() / w.sum()) - e0, float((w * n).sum() / w.sum()) - n0

        assert np.allclose(blot(build.scan_sampler(project, "blot")), (0.0, 0.0), atol=0.1)
        assert np.allclose(blot(build.scan_sampler(project, "blot", (-2.0, 3.0))), (-2.0, 3.0), atol=0.1)
    finally:
        (folder / "blot.png").unlink()
        (folder / "blot.json").unlink()


def write_raster(path, data, transform, crs=None, units=None):
    import rasterio

    with rasterio.open(path, "w", driver="GTiff", width=data.shape[1], height=data.shape[0], count=1, dtype="float32",
                       crs=crs, transform=transform) as dst:
        dst.write(data.astype("float32"), 1)
        if units:
            dst.units = (units,)


def test_a_raster_on_another_grid_is_read_on_its_own(site, tmp_path, project):
    """A raster in longitude and latitude, its values a plane in degrees: sampled at site points it gives the
    plane at those points' longitude and latitude."""
    from pyproj import Transformer
    from rasterio.transform import from_origin

    from domus.geo import raster

    e0, n0 = project.settings()["model"]["origin"][:2]
    lon0, lat0 = Transformer.from_crs(project.grid.epsg, 4326, always_xy=True).transform(e0, n0)
    step = 1e-5                                                         # about a metre
    cols, rows = np.meshgrid(np.arange(60) + 0.5, np.arange(60) + 0.5)
    west, north = lon0 - 30 * step, lat0 + 30 * step
    lon, lat = west + cols * step, north - rows * step
    write_raster(tmp_path / "plane.tif", 1000 * (lon - lon0) + 2000 * (lat - lat0) + 40, from_origin(west, north, step, step), "EPSG:4326")
    r = raster.Raster(tmp_path / "plane.tif", project.grid.epsg)
    e, n = np.meshgrid(e0 + np.linspace(-15, 15, 7), n0 + np.linspace(-15, 15, 7))
    lo, la = Transformer.from_crs(project.grid.epsg, 4326, always_xy=True).transform(e, n)
    assert np.allclose(r.sample(e, n), 1000 * (lo - lon0) + 2000 * (la - lat0) + 40, atol=1e-4)
    E, N, values = r.cells()                                            # each cell's middle, on the site's grid
    lo, la = Transformer.from_crs(project.grid.epsg, 4326, always_xy=True).transform(E, N)
    assert values.shape == lon.shape and np.allclose(lo, lon, atol=1e-9) and np.allclose(la, lat, atol=1e-9)


def test_a_raster_that_names_no_grid_is_on_the_sites(site, tmp_path, project):
    import rasterio

    from domus.geo import raster
    from domus.project import rasters

    with rasterio.open(rasters.path(project, "dem")) as src:
        write_raster(tmp_path / "bare.tif", src.read(1), src.transform)
    e0, n0 = project.settings()["model"]["origin"][:2]
    e, n = np.meshgrid(e0 + np.linspace(-6, 6, 13), n0 + np.linspace(-6, 6, 13))
    epsg = project.grid.epsg
    assert np.allclose(raster.Raster(tmp_path / "bare.tif", epsg).sample(e, n),
                       raster.Raster(rasters.path(project, "dem"), epsg).sample(e, n), equal_nan=True)


def test_heights_in_feet_are_read_in_metres(site, tmp_path, project):
    import rasterio

    from domus.geo import raster
    from domus.project import rasters

    with rasterio.open(rasters.path(project, "dem")) as src:
        write_raster(tmp_path / "feet.tif", src.read(1) / 0.3048, src.transform, src.crs, units="ft")
    e0, n0 = project.settings()["model"]["origin"][:2]
    e, n = np.meshgrid(e0 + np.linspace(-6, 6, 13), n0 + np.linspace(-6, 6, 13))
    epsg = project.grid.epsg
    metres = raster.Raster(rasters.path(project, "dem"), epsg).sample(e, n)
    assert np.allclose(raster.Raster(tmp_path / "feet.tif", epsg, heights=True).sample(e, n), metres, atol=1e-3, equal_nan=True)
    assert not np.allclose(raster.Raster(tmp_path / "feet.tif", epsg).sample(e, n), metres, atol=1, equal_nan=True)  # a photo's bands aren't heights
    assert raster.metres_per_unit("US survey foot") == 1200 / 3937 and raster.metres_per_unit(None) == 1.0
