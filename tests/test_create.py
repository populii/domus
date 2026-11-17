"""From an address: the lookups with their answers stubbed, the house picked and fitted, the address box. Nothing here goes out."""

import http.client
import json
import threading

import numpy as np
import pytest

from domus import create, publicdata, serve

LOT = [[1000.0, 2030.0], [1022.0, 2030.0], [1022.0, 2000.0], [1000.0, 2000.0], [1000.0, 2030.0]]
HOUSE = [[1005.0, 2015.0], [1017.0, 2015.0], [1017.0, 2006.0], [1005.0, 2006.0], [1005.0, 2015.0]]
SHED = [[1002.0, 2028.0], [1005.0, 2028.0], [1005.0, 2025.0], [1002.0, 2025.0], [1002.0, 2028.0]]
NEIGHBOUR = [[1030.0, 2020.0], [1040.0, 2020.0], [1040.0, 2010.0], [1030.0, 2010.0], [1030.0, 2020.0]]


def test_linz_lookup_reads_the_three_layers(monkeypatch):
    asked = []

    def fake(key, layer, cql, count=20):
        asked.append((layer, cql))
        if layer == publicdata.ADDRESSES:
            return [{"geometry": {"type": "Point", "coordinates": [1011.0, 2010.0]}, "properties": {"full_address": "12 Example Street"}}]
        if layer == publicdata.PARCELS:
            return [{"geometry": {"type": "MultiPolygon", "coordinates": [[LOT]]}, "properties": {"appellation": "Lot 1"}}]
        return [{"geometry": {"type": "Polygon", "coordinates": [HOUSE]}}]

    monkeypatch.setattr(publicdata, "_wfs", fake)
    found = publicdata.from_linz("12 Example Street, Town", "k")
    assert found["address"] == "12 Example Street" and found["parcel"] == LOT and found["parcel_name"] == "Lot 1"
    assert found["buildings"] == [HOUSE]
    assert "12 Example Street" in asked[0][1] and "Town" not in asked[0][1]


def addresses(*names):
    return [{"geometry": {"type": "Point", "coordinates": [1011.0, 2010.0]}, "properties": {"full_address": n}} for n in names]


def test_several_places_ask_which(monkeypatch):
    monkeypatch.setattr(publicdata, "_wfs", lambda *a, **k: addresses("2 Main Road, Upton, Christchurch", "2 Main Road, Kumeu, Auckland"))
    with pytest.raises(publicdata.Ambiguous) as e:
        publicdata.from_linz("2 Main Road", "k")
    assert e.value.places == ["2 Main Road, Kumeu, Auckland", "2 Main Road, Upton, Christchurch"]


def test_the_town_settles_it_macrons_or_not(monkeypatch):
    def fake(key, layer, cql, count=20):
        if layer == publicdata.ADDRESSES:
            return addresses("2 Main Road, \u014chope, Whakatane", "2 Main Road, Kumeu, Auckland")
        return []

    monkeypatch.setattr(publicdata, "_wfs", fake)
    assert publicdata.from_linz("2 Main Road, Ohope", "k")["address"].endswith("Whakatane")


def test_the_number_must_match_from_the_start(monkeypatch):
    asked = []
    monkeypatch.setattr(publicdata, "_wfs", lambda k, layer, cql, count=20: asked.append(cql) or (addresses("2 Main Road, Upton") if layer == publicdata.ADDRESSES else []))
    publicdata.from_linz("2 Main Road", "k")
    assert asked[0].startswith("full_address_ascii ILIKE '2 Main Road,%'")


def test_osm_records_far_apart_are_different_places(monkeypatch):
    rows = [{"lon": "172.63", "lat": "-43.53", "display_name": "A"}, {"lon": "172.63001", "lat": "-43.53", "display_name": "A again"},
            {"lon": "176.0", "lat": "-39.0", "display_name": "B"}]
    monkeypatch.setattr(publicdata, "_get", lambda *a, **k: rows)
    with pytest.raises(publicdata.Ambiguous) as e:
        publicdata.from_osm("2 Main Road")
    assert e.value.places == ["A", "B"]


def test_a_choice_is_offered_on_the_page(tmp_path):
    c = serve.Creator(tmp_path)
    c.log.parent.mkdir(parents=True)
    c.log.write_text('Traceback\nWHICH: {"address": "2 Main Road", "places": ["A", "B"]}\n', encoding="utf-8")
    c.proc = type("Done", (), {"poll": lambda self: 1})()
    assert c.status() == {"state": "choose", "address": "2 Main Road", "places": ["A", "B"]}
    assert c.allowed()


def test_address_quotes_and_wildcards_are_neutralised():
    assert publicdata.normalise("12 O'Brien  St%_, Town") == "12 O''Brien St"


def test_lookup_uses_osm_only_without_a_key(monkeypatch):
    monkeypatch.setattr(publicdata, "from_linz", lambda a, k: "linz")
    monkeypatch.setattr(publicdata, "from_osm", lambda a: "osm")
    monkeypatch.setenv("DOMUS_LINZ_KEY", "k")
    assert publicdata.lookup("x") == "linz"
    monkeypatch.delenv("DOMUS_LINZ_KEY")
    assert publicdata.lookup("x") == "osm"


def test_north_first_coordinates_are_swapped():
    f = {"geometry": {"type": "Polygon", "coordinates": [[[5180000.0, 1570000.0], [5180010.0, 1570000.0]]]}}
    assert publicdata._xy_order(f)["geometry"]["coordinates"][0][0] == [1570000.0, 5180000.0]


def test_the_house_is_the_big_building_on_the_lot():
    found = {"point": [1011.0, 2010.0], "buildings": [SHED, NEIGHBOUR, HOUSE]}
    house, others = create.pick_house(found, LOT)
    assert house == HOUSE and SHED in others and NEIGHBOUR in others
    assert create.pick_house({"point": [0, 0], "buildings": [NEIGHBOUR]}, LOT)[0] is None


def test_walls_stand_inside_the_roof_outline():
    walls = create.walls_from_roof(HOUSE)
    xs, ys = zip(*walls)
    assert (min(xs), max(xs), min(ys), max(ys)) == pytest.approx((1005.4, 1016.6, 2006.4, 2014.6))
    assert len(walls) == len(HOUSE)
    sliver = [[0.0, 0.0], [10.0, 0.0], [10.0, 0.9], [0.0, 0.9], [0.0, 0.0]]
    assert create.walls_from_roof(sliver) == sliver


def test_a_roof_is_fitted_from_the_surface_model():
    ground = lambda e, n: np.full(np.shape(e), 40.0)
    surface = lambda e, n: 40.0 + 2.8 + np.minimum(np.minimum(e - 1005, 1017 - e), np.minimum(n - 2006, 2015 - n)) * 0.5
    h = create.fit_house(HOUSE, ground, surface)
    assert h["fitted"] and 15 < h["roof_pitch_deg"] < 40 and 42.0 < h["soffit"] < 43.5
    flat = create.fit_house(HOUSE, ground, lambda e, n: np.full(np.shape(e), np.nan))
    assert not flat["fitted"] and flat["soffit"] == pytest.approx(42.7)


def test_no_parcel_means_a_stand_in_square_saying_so(tmp_path):
    found = {"address": "12 Example Street, Town", "point": [1011.0, 2010.0], "parcel": None, "parcel_name": None, "buildings": [],
             "source": "OpenStreetMap contributors"}
    create.scaffold(tmp_path / "p", found)
    gj = json.loads((tmp_path / "p" / "data" / "site" / "boundary.geojson").read_text(encoding="utf-8"))
    assert "replace it" in gj["features"][0]["properties"]["source"]
    assert '"12 Example Street"' in (tmp_path / "p" / "data" / "site.toml").read_text(encoding="utf-8")


@pytest.fixture
def box(tmp_path, monkeypatch):
    started = []
    monkeypatch.setattr(serve.Creator, "start", lambda self, a: started.append(a))
    httpd = serve.make_server("127.0.0.1", 0, [tmp_path / "out"], tmp_path / "s" / "v.db", root=tmp_path / "s")
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield httpd.server_address[1], started, tmp_path / "s"
    httpd.shutdown()
    httpd.server_close()


def call(port, method, path, body=None, headers=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    c.request(method, path, body, headers or {})
    r = c.getresponse()
    return r.status, r.read()


def test_the_address_box(box):
    port, started, _ = box
    head = {"Content-Type": "application/json"}
    assert b"Build a 3D model" in call(port, "GET", "/")[1]
    assert call(port, "POST", "/api/create", json.dumps({"address": "12 Example Street"}), {"Content-Type": "text/plain"})[0] == 415
    assert call(port, "POST", "/api/create", json.dumps({"address": "--help"}), head)[0] == 400
    assert call(port, "POST", "/api/create", "{}", head)[0] == 400
    assert call(port, "POST", "/api/create", json.dumps({"address": "12 Example Street"}), head)[0] == 202
    assert started == ["12 Example Street"]


def test_a_stopped_build_can_carry_on_but_only_for_the_same_address(tmp_path, monkeypatch):
    found = {"address": "12 Example Street", "point": [1011.0, 2010.0], "parcel": None, "parcel_name": None, "buildings": [], "source": "x"}
    monkeypatch.setattr(publicdata, "lookup", lambda a: found)
    steps = []
    monkeypatch.setattr(create, "run_step", lambda root, *a: steps.append(root))
    create.main(["12 Example Street", str(tmp_path / "p")])
    monkeypatch.setattr(publicdata, "lookup", lambda a: pytest.fail("looked up again"))
    create.main(["12 Example Street", str(tmp_path / "p")])
    assert len(steps) == 2
    with pytest.raises(SystemExit, match="another address"):
        create.main(["99 Other Road", str(tmp_path / "p")])


def test_the_address_box_wont_touch_a_site_with_a_project(box):
    port, started, root = box
    (root / "data").mkdir(parents=True)
    (root / "data" / "site.toml").write_text("", encoding="utf-8")
    status, _ = call(port, "POST", "/api/create", json.dumps({"address": "12 Example Street"}), {"Content-Type": "application/json"})
    assert status == 409 and not started
    assert b"Build a 3D model" not in call(port, "GET", "/")[1]
