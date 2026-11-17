"""LINZ's tile names, against bounds read from its own STAC items (no network here)."""

import pytest
from pyproj import Transformer

from domus import linz

TO_NZTM = Transformer.from_crs(4326, 2193, always_xy=True)


@pytest.mark.parametrize(("name", "lonlat_bbox"), [
    ("BX24_10000_0101.json", (172.5546495, -43.5070798, 172.6144406, -43.4420347)),
])
def test_tile_bounds_match_the_stac_item(name, lonlat_bbox):
    x0, y0, x1, y1 = linz.tile_bounds(name)
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    a = TO_NZTM.transform(lonlat_bbox[0], lonlat_bbox[1])
    b = TO_NZTM.transform(lonlat_bbox[2], lonlat_bbox[3])
    assert cx == pytest.approx((a[0] + b[0]) / 2, abs=5)
    assert cy == pytest.approx((a[1] + b[1]) / 2, abs=5)


def test_tile_sizes():
    a = linz.tile_bounds("BX24_1000_0244.json")
    assert a[2] - a[0] == 480 and a[3] - a[1] == 720
    b = linz.tile_bounds("BX24_10000_0101.json")
    assert b[2] - b[0] == 4800 and b[3] - b[1] == 7200


def test_tiles_covering_says_when_a_survey_stops_short():
    x0, y0, x1, _ = linz.tile_bounds("BX24_1000_0244.json")
    inside = (x0 + 10, y0 + 10, x0 + 60, y0 + 60)
    across = (x1 - 10, y0 + 10, x1 + 40, y0 + 60)
    links = ["./BX24_1000_0244.json", "./not-a-tile.json"]
    assert linz.tiles_covering(links, inside) == (["./BX24_1000_0244.json"], True)
    assert linz.tiles_covering(links, across) == (["./BX24_1000_0244.json"], False)
    assert linz.tiles_covering(["./BX24_1000_0245.json", *links], across)[1] is True


def test_newest_year_from_title():
    assert linz._year("Christchurch City LiDAR 1m DEM (2019-2020)") == 2020
    assert linz._year("Carterton 0.075m Urban Aerial Photos (2025)") == 2025
    assert linz._year("no year") == 0


def test_fetch_skips_a_survey_with_no_data_here(site, monkeypatch, tmp_path):
    monkeypatch.setattr(linz, "site_bounds", lambda: (0, 0, 10, 10))
    linz.crs.settings()             # the demo's settings, read before the site's folder moves to tmp_path
    monkeypatch.setattr(linz.crs, "ROOT", tmp_path)
    (tmp_path / "data").mkdir()
    surveys = {"ground model": [("Empty (2025)", ["e"]), ("Full (2025)", ["f"])]}
    monkeypatch.setattr(linz, "find_surveys", lambda catalog, match, bounds, recent: iter(surveys.get(next(
        s[0] for s in linz.SOURCES if s[2] is match), [])))
    monkeypatch.setattr(linz, "clip", lambda name, urls, bounds, path, bands=None: 0.0 if urls == ["e"] else 1.0)
    linz.main()
    import json
    used = json.loads((tmp_path / "data" / "linz-sources.json").read_text(encoding="utf-8"))
    assert used == {"ground model": {"title": "Full (2025)", "tiles": ["f"]}}


def test_fetch_says_linz_covers_new_zealand_only(site, monkeypatch):
    from domus import crs, linz
    from domus.errors import DomusError

    monkeypatch.setattr(crs, "site_to_lonlat", lambda e, n: (-5.5, 50.1))       # a site in Cornwall
    monkeypatch.setattr(linz, "find_surveys", lambda *a: pytest.fail("asked LINZ"))
    with pytest.raises(DomusError, match="New Zealand only"):
        linz.main()
