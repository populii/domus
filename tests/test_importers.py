"""Importers: an address found in New Zealand or anywhere, a site's rasters from the source for where it is, and what
each writes. Nothing here goes out: the services' answers are stubbed."""

import tomllib

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from domus import importers
from domus.importers import osm
from domus.importers.nz import publicdata


def test_an_address_is_looked_for_in_new_zealand_first_then_anywhere(monkeypatch):
    monkeypatch.setattr(publicdata, "lookup", lambda a: {"address": a, "source": "LINZ"})
    assert importers.lookup("12 Example Street") == {"epsg": 2193, "address": "12 Example Street", "source": "LINZ",
                                                     "importer": "nz"}

    def not_in_nz(a):
        raise publicdata.NotFound("LINZ has no address like that")

    asked = []

    def anywhere(a, countrycodes="nz"):
        asked.append(countrycodes)
        return {"address": a, "lonlat": [-5.5, 50.1], "epsg": 32630, "source": "OpenStreetMap"}

    monkeypatch.setattr(publicdata, "lookup", not_in_nz)
    monkeypatch.setattr(publicdata, "from_osm", anywhere)
    found = importers.lookup("1 Sea Road, Penzance")
    assert asked == [None] and found["importer"] == "osm" and found["epsg"] == 32630

    def nowhere(a, countrycodes="nz"):
        raise publicdata.NotFound("OpenStreetMap has no address like that")

    monkeypatch.setattr(publicdata, "from_osm", nowhere)
    with pytest.raises(publicdata.NotFound, match="LINZ has no address"):          # New Zealand's answer, first
        importers.lookup("Nowhere at all")


def test_openstreetmap_anywhere_puts_the_place_on_its_utm_zone(monkeypatch):
    asked = []

    def answer(url, data=None, timeout=60):
        asked.append(url)
        if "nominatim" in url:
            return [{"lon": "-5.5", "lat": "50.1", "display_name": "1 Sea Road, Penzance, England"}]
        ring = [(-5.5001, 50.1001), (-5.4999, 50.1001), (-5.4999, 50.0999), (-5.5001, 50.0999), (-5.5001, 50.1001)]
        return {"elements": [{"geometry": [{"lon": x, "lat": y} for x, y in ring]}]}

    monkeypatch.setattr(publicdata, "_get", answer)
    monkeypatch.setattr(publicdata.time, "sleep", lambda s: None)
    found = publicdata.from_osm("1 Sea Road, Penzance", countrycodes=None)
    assert "countrycodes" not in asked[0] and found["epsg"] == 32630 and found["lonlat"] == [-5.5, 50.1]
    assert 300000 < found["point"][0] < 400000 and 5.5e6 < found["point"][1] < 5.6e6        # UTM zone 30 north
    (building,) = found["buildings"]
    assert 13 < max(x for x, _ in building) - min(x for x, _ in building) < 16             # 0.0002 degrees of longitude, in metres


def test_copernicus_tiles_are_named_for_their_south_west_corner():
    name = "Copernicus_DSM_COG_10_N50_00_W006_00_DEM"
    assert osm.tile(50.1, -5.5) == f"{osm.BASE}/{name}/{name}.tif"
    assert "S34_00_E151_00" in osm.tile(-33.9, 151.2) and "N00_00_E000_00" in osm.tile(0.5, 0.5)


def test_fetch_takes_the_site_s_patch_of_the_ground_model(site, tmp_path, monkeypatch, at_site, project):
    from domus.project import rasters

    west, south, east, north = osm.site_box(project)
    step = 1 / 3600                                                     # one arc-second, as GLO-30's are here
    x0, y0 = west - 10 * step, north + 10 * step
    cols, rows = int((east - west) / step) + 20, int((north - south) / step) + 20
    lon = x0 + (np.arange(cols) + 0.5) * step
    lat = y0 - (np.arange(rows) + 0.5) * step
    plane = 30 + 1000 * (lon[None, :] - west) + 2000 * (lat[:, None] - south)
    tile = tmp_path / "tile.tif"
    with rasterio.open(tile, "w", driver="GTiff", width=cols, height=rows, count=1, dtype="float32", crs="EPSG:4326",
                       transform=from_origin(x0, y0, step, step), nodata=-9999.0) as dst:
        dst.write(plane.astype("float32"), 1)
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "site.toml").write_bytes((site / "data" / "site.toml").read_bytes())     # the demo, moved
    monkeypatch.setattr(osm, "tile", lambda lat, lon: str(tile))
    moved = at_site(tmp_path)
    assert rasters.path(moved, "dem") == tmp_path / "data" / "lidar" / "dem.tif"
    osm.fetch(moved)
    with rasterio.open(tmp_path / "data" / "lidar" / "dem.tif") as src:
        assert src.crs.to_epsg() == 4326 and src.units == ("metre",)
        b, m = src.bounds, osm.MARGIN_DEG                               # the model's extent, at least, is in it
        assert b.left <= west + m and b.right >= east - m and b.bottom <= south + m and b.top >= north - m
    assert "Copernicus" in (tmp_path / "data" / "copernicus-sources.json").read_text(encoding="utf-8")


def test_fetch_asks_the_source_for_where_the_site_is(site, monkeypatch, project):
    from domus.importers.nz import linz

    asked = []
    monkeypatch.setattr(linz, "main", lambda site, say: asked.append("LINZ"))
    monkeypatch.setattr(osm, "fetch", lambda site, say: asked.append("Copernicus"))
    importers.fetch(project)                                                   # the demo is in New Zealand's waters
    monkeypatch.setattr(importers, "in_nz", lambda site: False)
    importers.fetch(project)
    assert asked == ["LINZ", "Copernicus"]


def test_a_place_abroad_is_written_with_its_own_grid_datum_and_credit(tmp_path):
    from domus import create

    found = {"address": "1 Sea Road, Penzance, England", "point": [324000.0, 5551000.0], "parcel": None,
             "parcel_name": None, "buildings": [], "source": "OpenStreetMap contributors", "epsg": 32630,
             "lonlat": [-5.5, 50.1], "importer": "osm"}
    create.scaffold(tmp_path / "p", found)
    cfg = tomllib.loads((tmp_path / "p" / "data" / "site.toml").read_text(encoding="utf-8"))
    assert cfg["domus"]["importer"] == "osm" and cfg["crs"]["epsg"] == 32630 and cfg["crs"]["vertical"] == "EGM2008"
    assert "Copernicus" in cfg["site"]["attribution"] and "Copernicus" in cfg["site"]["about"]


def test_the_checklist_speaks_of_new_zealand_only_there(site, project):
    from domus.checks import enrich

    here = {c["id"]: c for c in enrich.checklist(project)}       # the demo, off New Zealand's coast
    assert "LINZ" in here["boundary"]["where"] and here["lim"]["title"] == "LIM report"
    away = {i: (t, where) for i, t, why, where, how, pat in enrich.sources(False)}
    assert "LINZ" not in away["boundary"][1] and away["lim"][0] == "Council property report"
    assert "beforeUdig" not in away["drains"][1] and len(away) == len(enrich.SOURCES)
