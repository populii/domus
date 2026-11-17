"""`domus title`, with LINZ's answers stubbed, and a wall option running into an easement."""

import json

import pytest
from shapely.geometry import Polygon

from domus import crs, design, marks, publicdata, title

LOT = [[1000.0, 2000.0], [1020.0, 2000.0], [1020.0, 2020.0], [1000.0, 2020.0], [1000.0, 2000.0]]
STRIP = [[1016.0, 2000.0], [1020.0, 2000.0], [1020.0, 2020.0], [1016.0, 2020.0], [1016.0, 2000.0]]
FAR = [[1100.0, 2000.0], [1104.0, 2000.0], [1104.0, 2004.0], [1100.0, 2000.0]]


def parcel(pid, ring, appellation, intent="Easement"):
    return {"geometry": {"type": "MultiPolygon", "coordinates": [[ring]]},
            "properties": {"id": pid, "appellation": appellation, "parcel_intent": intent, "titles": "CB1/1", "survey_area": 80.0}}


@pytest.fixture
def site(monkeypatch, tmp_path):
    monkeypatch.setattr(crs, "site_to_nztm", lambda e, n: (e, n))
    monkeypatch.setattr(crs, "nztm_to_site", lambda x, y: (x, y))
    monkeypatch.setattr(crs, "site_epsg", lambda: 2193)
    (tmp_path / "data" / "site").mkdir(parents=True)
    (tmp_path / "data" / "site" / "boundary.geojson").write_text(json.dumps(
        {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {}, "geometry": {"type": "Polygon", "coordinates": [LOT]}}]}))
    monkeypatch.setattr(marks, "ROOT", tmp_path)
    monkeypatch.setattr(title, "PATH", tmp_path / "data" / "site" / "title.geojson")
    return tmp_path


def test_the_title_and_the_easements_near_the_lot(site, monkeypatch, capsys):
    monkeypatch.setenv("DOMUS_LINZ_KEY", "k")

    def fake(key, layer, cql, count=20):
        if layer == title.TITLES:
            return [{"geometry": {"type": "MultiPolygon", "coordinates": [[LOT]]},
                     "properties": {"title_no": "123456", "estate_description": "Fee Simple, 1/1, Lot 1 Deposited Plan 9, 400 m2",
                                    "type": "Freehold", "status": "LIVE", "issue_date": "2015-01-01"}},
                    {"geometry": {"type": "MultiPolygon", "coordinates": [[FAR]]}, "properties": {"title_no": "999"}}]
        if layer == title.EASEMENTS:
            beside = [[1020.0, 2000.0], [1028.0, 2000.0], [1028.0, 2020.0], [1019.96, 2020.0], [1020.0, 2000.0]]    # a hair over the line
            return [parcel(7, STRIP, "Area A DP 9"), parcel(8, FAR, "Area B DP 9"), parcel(9, beside, "Area C DP 9")]
        return []

    monkeypatch.setattr(publicdata, "_wfs", fake)
    title.main([])
    feats = json.loads(title.PATH.read_text())["features"]
    assert [f["properties"]["name"] for f in feats] == ["Title 123456", "Area A DP 9", "Area C DP 9"]
    t, e, c = (f["properties"] for f in feats)
    assert not c["on_lot"]
    assert t["kind"] == "title" and t["estate"].startswith("Fee Simple")
    assert e["kind"] == "easement" and e["on_lot"] and e["intent"] == "Easement"
    assert "Area A DP 9 (Easement), on the lot" in capsys.readouterr().out


def test_a_wall_into_an_easement_is_flagged():
    strip = (("Area A DP 9", "Easement", Polygon(STRIP)),)
    across = {"line": [[1010.0, 2010.0], [1018.0, 2010.0]], "thickness": 0.2}
    clear = {"line": [[1002.0, 2010.0], [1010.0, 2010.0]], "thickness": 0.2}
    assert design.easement_checks(across, strip) == ["it runs into Area A DP 9 (easement): check the easement allows a wall there"]
    assert design.easement_checks(clear, strip) == []
