"""Rasters and scanned sheets read at site coordinates, and the shift a site can give one that sits off its ground."""

import json

import numpy as np
from PIL import Image


def test_a_raster_has_no_shift_unless_the_site_gives_one(site, monkeypatch):
    from domus import raster

    assert raster.shift("aerial") == (0.0, 0.0)
    monkeypatch.setattr(raster, "settings", lambda: {"rasters": {"shift_m": {"aerial": [-0.5, 0.25]}}})
    assert raster.shift("aerial") == (-0.5, 0.25)
    assert raster.shift("dem") == (0.0, 0.0)


def test_a_shifted_raster_shows_what_its_file_has_that_far_back(site):
    from domus import crs, raster

    e0, n0 = crs.settings()["model"]["origin"][:2]
    e, n = np.meshgrid(e0 + np.linspace(-6, 6, 13), n0 + np.linspace(-6, 6, 13))
    plain = raster.Raster(raster.DEM)
    moved = raster.Raster(raster.DEM, shift=(-0.5, 0.25))      # the file's content moved 0.5 m west and 0.25 m north
    assert np.allclose(moved.sample(e, n), plain.sample(e + 0.5, n - 0.25), equal_nan=True)
    assert moved.sample(e, n).shape == e.shape
    assert not np.allclose(moved.sample(e, n), plain.sample(e, n), equal_nan=True)


def test_a_scanned_sheet_can_be_shifted(site):
    from domus import build, crs

    e0, n0 = crs.settings()["model"]["origin"][:2]
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

        assert np.allclose(blot(build.scan_sampler("blot")), (0.0, 0.0), atol=0.1)
        assert np.allclose(blot(build.scan_sampler("blot", (-2.0, 3.0))), (-2.0, 3.0), atol=0.1)
    finally:
        (folder / "blot.png").unlink()
        (folder / "blot.json").unlink()
