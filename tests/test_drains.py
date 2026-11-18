"""Drain levels, worked out once for the pipes and the options' checks (domus.drains), and a drain flagged to follow up."""

from types import SimpleNamespace

import numpy as np
import pytest

from domus import drains

LINE = [[0.0, 0.0], [20.0, 0.0]]
flat = lambda e, n: np.full(np.shape(np.atleast_1d(e)), 10.0)


def point(x, invert, line="d", source="", **props):
    return {"type": "Feature", "geometry": {"type": "Point", "coordinates": [x, 0.0]},
            "properties": {"id": f"p{x}-{source}", "line": line, "invert_m": invert, "source": source, **props}}


def drain(**props):
    return {"type": "Feature", "geometry": {"type": "LineString", "coordinates": LINE},
            "properties": {"id": "d", "name": "Drain", **props}}


def at(found, x):
    _, chain, invert, _ = found
    return float(np.interp(x, chain, invert))


def test_one_document_runs_straight_between_its_levels_and_keeps_them_past_its_ends():
    found = drains.levels(LINE, drains.level_points([point(5, 9.0), point(15, 8.0)])["d"], flat)
    assert at(found, 5) == pytest.approx(9.0) and at(found, 10) == pytest.approx(8.5) and at(found, 15) == pytest.approx(8.0)
    assert at(found, 0) == pytest.approx(9.0) and at(found, 20) == pytest.approx(8.0)
    assert found[3] == [(5.0, 9.0), (15.0, 8.0)]


def test_past_its_ends_a_drain_keeps_its_depth_where_the_ground_falls_away():
    falling = lambda e, n: 10.0 - 0.2 * np.asarray(e, float)            # the ground drops 0.2 m a metre
    found = drains.levels(LINE, drains.level_points([point(5, 8.0), point(10, 7.5)])["d"], falling)
    assert at(found, 20) == pytest.approx(10.0 - 0.2 * 20 - (10.0 - 0.2 * 10 - 7.5))


def test_where_documents_overlap_the_higher_level_counts():
    pts = [point(2, 9.0, source="plan"), point(12, 8.0, source="plan"), point(8, 8.0, source="model"), point(18, 7.0, source="model")]
    found = drains.levels(LINE, drains.level_points(pts)["d"], flat)
    assert at(found, 2) == pytest.approx(9.0)                          # the plan alone
    assert at(found, 10) == pytest.approx(8.2)                         # the plan (8.2) is higher than the model (7.8)
    assert at(found, 15) == pytest.approx(7.3)                         # the model alone
    assert len(found[3]) == 4


def test_single_points_from_different_documents_join_straight():
    found = drains.levels(LINE, drains.level_points([point(4, 9.0, source="a"), point(14, 8.0, source="b")])["d"], flat)
    assert at(found, 9) == pytest.approx(8.5)


def test_an_assumed_depth_and_no_levels_at_all():
    _, _, invert, known = drains.levels(LINE, [], flat, assumed_depth=0.6)
    assert np.allclose(invert, 9.4) and known == []
    assert drains.levels(LINE, [], flat) is None


def test_a_flagged_drain_says_so_on_its_pipe_and_in_every_check_of_it(site, project):
    from domus import build, design

    features = [drain(follow_up="the as-laid plan will show where it really is"), point(2, 9.2), point(18, 9.0)]
    pipe = build.pipe_layers(project, features, build.Frame(np.zeros(3)), flat, {})[0]
    assert pipe["info"]["follow_up"] == "the as-laid plan will show where it really is"
    cut = SimpleNamespace(existing=SimpleNamespace(sample=flat), sample=lambda e, n: np.full(np.shape(e), 9.6),
                          walls=[{"id": "W1", "line": [[10.0, -1.0], [10.0, 1.0]]}])
    checks = design.drain_checks(cut, features, {}, {"drain_cover_m": 0.3})
    assert len(checks) == 2 and all(c["follow_up"] == features[0]["properties"]["follow_up"] for c in checks)
    assert all("its levels are to be followed up" in c["flag"] for c in checks)


def test_a_level_without_a_label_shows_its_invert(site, project):
    from domus import build

    b = SimpleNamespace(site=project, cfg={}, frame=build.Frame(np.zeros(3)), ground=flat, drawn_ground=flat, labels={}, layers=[],
                        vertical="NZVD2016", svc_feats=[drain(), point(5, 9.25), point(15, 9.0, label="IL 9.00 (plan)")])
    build.add_marks(b)
    texts = [m["text"] for m in next(x for x in b.layers if x["id"] == "marks")["items"]]
    assert "IL 9.25" in texts and "IL 9.00 (plan)" in texts


def test_drains_are_drawn_on_the_ground_the_build_draws(tmp_path):
    """A drain's line, its pipe at an assumed depth and its labels take the ground as the build draws it (the existing
    walls cut in), the one the wall options' checks of it use, not the LiDAR's alone."""
    import json

    from domus import build

    (tmp_path / "data" / "site").mkdir(parents=True)
    features = [drain(), point(5, 9.25)]
    (tmp_path / "data" / "site" / "services.geojson").write_text(
        json.dumps({"type": "FeatureCollection", "features": features}), encoding="utf-8")
    site = SimpleNamespace(path=tmp_path, settings=lambda: {"services": {"assumed_depth_m": {"d": 0.6}}})
    drawn = lambda e, n: np.full(np.shape(np.atleast_1d(e)), 10.5)       # a wall's top, say, where the LiDAR has 10
    b = SimpleNamespace(site=site, cfg={}, frame=build.Frame(np.zeros(3)), ground=flat, drawn_ground=drawn, labels={},
                        layers=[], vertical="")
    build.add_services(b)
    line = next(x for x in b.layers if x["id"] == "d")
    assert np.allclose(np.asarray(line["segments"]).reshape(-1, 3)[:, 1], 10.56)    # viewer y is the height
    build.add_marks(b)
    assert next(x for x in b.layers if x["id"] == "marks")["items"][0]["at"][1] == pytest.approx(10.7)
    b.layers = []
    features[1]["properties"].pop("line")                                  # no levels: drawn at its assumed depth
    (tmp_path / "data" / "site" / "services.geojson").write_text(
        json.dumps({"type": "FeatureCollection", "features": features}), encoding="utf-8")
    build.add_services(b)
    pipe = next(x for x in b.layers if x["type"] == "pipes")
    assert np.allclose(np.asarray(pipe["path"]).reshape(-1, 3)[:, 1], 10.5 - 0.6 + build.PIPE_RADIUS)
