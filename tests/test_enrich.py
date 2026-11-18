"""The checklist and the walk route."""

import json

import numpy as np
from shapely.geometry import LineString, Polygon

LOT = [[0.0, 30.0], [22.0, 30.0], [22.0, 0.0], [0.0, 0.0], [0.0, 30.0]]
HOUSE = [[5.0, 15.0], [17.0, 15.0], [17.0, 6.0], [5.0, 6.0], [5.0, 15.0]]


def test_the_model_carries_a_checklist_in_order_with_what_the_site_has(built):
    e = json.loads((built / "model.json").read_text(encoding="utf-8"))["enrich"]
    ids = [s["id"] for s in e["sources"]]
    assert ids[:2] == ["boundary", "lim"] and {"consents", "plans", "video", "scan"} <= set(ids)
    by_id = {s["id"]: s for s in e["sources"]}
    assert by_id["drains"]["found"] and by_id["measure"]["found"]       # the demo has these layers
    assert not by_id["lim"]["found"] and not by_id["scan"]["found"]
    assert all(s["why"] and s["where"] and s["how"] for s in e["sources"])


def test_a_document_in_sources_counts_as_found(built, site, monkeypatch, project):
    from domus.checks import enrich

    raw = site / "sources" / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    (raw / "LIM-2024.pdf").write_bytes(b"x")
    try:
        assert next(s for s in enrich.checklist(project) if s["id"] == "lim")["found"]
    finally:
        (raw / "LIM-2024.pdf").unlink()


def test_the_route_stays_on_the_lot_and_goes_round_the_house(site):
    from domus.checks import enrich

    ground = lambda e, n: np.zeros(np.shape(e))
    r = enrich.walk_route(LOT, HOUSE, ground)
    assert Polygon(LOT).buffer(0.01).contains(LineString(r["path"]))
    assert LineString(r["path"]).distance(Polygon(HOUSE)) > 1.5
    assert [s["n"] for s in r["stops"]][:2] == [1, 2]
    assert r["length_m"] > 2 * (22 + 30) - 15


def test_a_steep_spot_gets_its_own_stop(site):
    from domus.checks import enrich

    steep = lambda e, n: np.where(np.asarray(n) < 10, np.asarray(n) * 1.5, 15.0)
    r = enrich.walk_route(LOT, None, steep)
    assert any("steep" in s["text"] for s in r["stops"])
    assert Polygon(LOT).buffer(0.01).contains(LineString(r["path"]))


def test_no_stop_is_on_the_house(site, built):
    from domus.checks import enrich

    steep = lambda e, n: np.asarray(n) * 1.5
    r = enrich.walk_route(LOT, HOUSE, steep)
    for s in r["stops"]:
        assert not Polygon(HOUSE).buffer(0.9).contains(Polygon([(s["x"], s["y"]), (s["x"] + 0.01, s["y"]), (s["x"], s["y"] + 0.01)]))
    assert LineString(r["path"]).distance(Polygon(HOUSE)) > 0.9


def test_it_can_be_left_out(built, site, project):
    from domus import build

    toml = site / "data" / "site.toml"
    was = toml.read_text(encoding="utf-8")
    toml.write_text(was + "\n[enrich]\nenabled = false\n", encoding="utf-8")
    project.forget_settings()
    try:
        build.main(project)
        m = json.loads((built / "model.json").read_text(encoding="utf-8"))
        assert "enrich" not in m and not [x for x in m["layers"] if x.get("id") == "walk-route"]
    finally:
        toml.write_text(was, encoding="utf-8")
        project.forget_settings()
        build.main(project)
