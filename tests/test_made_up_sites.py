"""tools/made_up_sites.py: the sites CI builds with a pull request's base and its head, and its report on them."""

import json
import sys
import tomllib
from pathlib import Path

import pytest
import rasterio

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import made_up_sites


@pytest.fixture(scope="module")
def sites(tmp_path_factory):
    out = tmp_path_factory.mktemp("made-up") / "sites"
    made_up_sites.write(out)
    return out


def settings(root):
    return tomllib.loads((root / "data" / "site.toml").read_text(encoding="utf-8"))


def layer(root, name):
    return json.loads((root / "data" / "site" / f"{name}.geojson").read_text(encoding="utf-8"))


def test_the_richer_site_has_what_the_demo_lacks(sites):
    root = sites / "richer"
    assert [f["properties"]["id"] for f in layer(root, "boundary")["features"]] == ["right-of-way", "lot"]
    assert settings(root)["boundary"]["main"] == "lot"
    parts = {f["properties"].get("part") for f in layer(root, "house")["features"]}
    assert {"porch", "steps", "block", "balustrade", "chimney", "tank"} <= parts
    levels = [f for f in layer(root, "services")["features"] if "invert_m" in f["properties"]]
    assert len(levels) == 2 and levels[0]["properties"]["invert_m"] > levels[1]["properties"]["invert_m"]
    paved = tomllib.loads((root / "options" / "c-paved-terrace.toml").read_text(encoding="utf-8"))
    assert [t["kind"] for t in paved["terraces"]] == ["paving", "lawn"]
    blocks = tomllib.loads((root / "options" / "d-block-tiers.toml").read_text(encoding="utf-8"))["walls"]
    assert all(w["depth_m"] > w["thickness_m"] and w["trench_m"] > w["depth_m"] for w in blocks)
    fixes = layer(root, "ground-fixes")["features"]
    assert [f["geometry"]["type"] for f in fixes] == ["Polygon", "LineString", "MultiPoint"]
    assert len(fixes[2]["geometry"]["coordinates"]) == 64 and all(f["properties"]["source"] == "made up" for f in fixes)
    assert [f["properties"]["id"] for f in layer(root, "features-demo")["features"] if f["properties"].get("kind") == "pad"] == \
        ["path-south"]


def test_the_richer_site_paints_in_the_drive_under_its_trees(sites, at_site):
    import numpy as np
    from shapely.geometry import shape

    from domus import build
    from domus.project import demo

    root = sites / "richer"
    richer = at_site(root)
    area = build.paved_under_canopy(richer)
    trees = [shape(f["geometry"]) for f in layer(root, "trees")["features"] if f["properties"]["id"].startswith("drive-tree")]
    assert len(trees) == 2 and all(area.intersects(t) for t in trees) and build.paving_grain(richer) is not None
    x0, y0 = demo.X0, demo.Y0
    E, N = np.meshgrid(x0 + 20 + (np.arange(125) + 0.5) * 0.08, y0 + 26 - (np.arange(250) + 0.5) * 0.08)
    raw, painted = build.aerial_sampler(E, N, site=richer).astype(int), build.ground_photo_sampler(E, N, site=richer).astype(int)
    green = lambda rgb: (rgb[:, 1] - (rgb[:, 0] + rgb[:, 2]) / 2) / rgb.mean(axis=1)
    crown = (np.hypot(E - (x0 + 22.9), N - (y0 + 12.0)) < 1.4) & (E > x0 + 23.0)        # inside the area and the crown
    assert green(raw[crown]).mean() > 0.4 and np.abs(green(painted[crown])).mean() < 0.06
    far = E > x0 + 27.8                                                                   # over 1.2 m east of the area
    assert (painted[far] == raw[far]).all()


def test_the_abroad_site_is_the_demo_on_another_grid(sites):
    root, demo = sites / "abroad", sites / "demo"
    assert settings(root)["crs"]["epsg"] == 27700
    a, b = settings(demo)["model"]["extent"], settings(root)["model"]["extent"]
    assert [b[2] - b[0], b[3] - b[1]] == pytest.approx([a[2] - a[0], a[3] - a[1]]) and b[0] < 200000
    for name in ("boundary", "house", "trees"):
        assert layer(root, name)["crs"]["properties"]["name"].endswith("EPSG::27700")
    with rasterio.open(root / "data" / "lidar" / "dem.tif") as dem, rasterio.open(demo / "data" / "lidar" / "dem.tif") as nz:
        assert dem.crs.to_epsg() == 27700 and (dem.read() == nz.read()).all()
        assert dem.bounds.left - b[0] == pytest.approx(nz.bounds.left - a[0])


def test_the_format_2_site_leaves_new_zealand_to_be_assumed(sites):
    s, demo = settings(sites / "format2"), settings(sites / "demo")
    assert s["domus"]["format"] == 2 and "crs" not in s and "attribution" not in s["site"]
    assert {k: v for k, v in s.items() if k not in ("domus", "crs", "site")} == {k: v for k, v in demo.items() if k not in ("domus", "crs", "site")}


def test_the_format_3_site_says_how_sure_its_levels_are_where_format_3_did(sites):
    s, demo = settings(sites / "format3"), settings(sites / "demo")
    assert s["domus"]["format"] == 3 and "local" not in s
    assert s["house"]["fl_upper_uncertainty_m"] == 0.02 and s["datums"]["plans"]["uncertainty_m"] == 0.05
    assert {k: v for k, v in s["house"].items() if k != "fl_upper_uncertainty_m"} == demo["house"]
    assert {k: v for k, v in s.items() if k not in ("domus", "datums", "house")} == \
        {k: v for k, v in demo.items() if k not in ("domus", "datums", "house")}


def test_the_left_site_draws_the_demos_options_with_the_lot_on_its_frames_left(sites):
    import numpy as np

    from domus.options.scheme import SiteFrame

    def frame(root):
        f = tomllib.loads((root / "options" / "_site.toml").read_text(encoding="utf-8"))["frame"]
        return f, SiteFrame(f["origin"], f["toward"], f.get("side", "right"))

    (d, demo), (f, left) = frame(sites / "demo"), frame(sites / "left")
    assert f["side"] == "left" and "side" not in d and (f["origin"], f["toward"]) == (d["toward"], d["origin"])
    for name in ("a-one-wall", "b-two-tiers"):
        a, b = (tomllib.loads((root / "options" / f"{name}.toml").read_text(encoding="utf-8"))
                for root in (sites / "demo", sites / "left"))
        assert len(a["walls"]) == len(b["walls"]) and len(a["terraces"]) == len(b["terraces"])
        for x, y in [*zip(a["walls"], b["walls"]), *zip(a["terraces"], b["terraces"])]:
            for key in ("line_so", "polygon_so", "at_so"):
                if key in x:
                    assert np.allclose(left.en(np.array(y[key], float)), demo.en(np.array(x[key], float))), (name, key)


def test_it_wont_write_over_a_folder_with_something_in_it(tmp_path):
    (tmp_path / "keep.txt").write_text("mine", encoding="utf-8")
    with pytest.raises(SystemExit, match="isn't empty"):
        made_up_sites.write(tmp_path)


def test_the_report_fails_only_when_the_head_breaks_a_build(tmp_path, capsys):
    for side, name, state in [("base", "demo", "ok"), ("head", "demo", "failed"), ("base", "abroad", "failed"),
                              ("head", "abroad", "failed")]:
        (tmp_path / f"{side}-{name}.status").write_text(state, encoding="utf-8")
    (tmp_path / "head-demo.log").write_text("Traceback\nValueError: something broke\n", encoding="utf-8")
    assert made_up_sites.report(tmp_path) == 1
    out = capsys.readouterr().out
    assert "| demo | ok | failed | **this pull request can't build it** |" in out and "something broke" in out
    assert "| abroad | failed | failed | fails with both |" in out and "| richer | not built | not built | not built |" in out
    assert "weren't both built, so they weren't compared" in out


def twin_build(root, e0, n0, top=1.0, diagonal=0, title="Demo site (made up)", crs="NZTM", vertical="NZVD2016",
               level=42.01, picture=None):
    """A small build of the same made-up site, with its origin at (e0, n0) on its grid: a 1 m square of ground whose
    far corner stands `top` high, cut along one diagonal or the other."""
    root.mkdir(parents=True)
    faces = [0, 1, 2, 0, 2, 3] if diagonal == 0 else [0, 1, 3, 1, 2, 3]
    model = {"title": title, "crs": crs, "vertical": vertical, "origin": [e0, n0, 40.0],
             "extent": [e0 - 10, n0 - 10, e0 + 10, n0 + 10], "built": "now", "commit": None,
             "layers": [{"id": "ground", "name": f"Ground ({vertical})", "positions": [0, 0, 0, 1, 0, 0, 1, top, -1, 0, 0, -1],
                         "indices": faces, "info": {"top_min": level}}],
             "summary": {"options_site": {"frame": {"origin": [e0 + 1, n0 + 2], "toward": [e0 + 5, n0 + 2]}}}}
    (root / "model.json").write_text(json.dumps(model), encoding="utf-8")
    (root / "summary.json").write_text(json.dumps(model["summary"]), encoding="utf-8")
    (root / "build.json").write_text("{}", encoding="utf-8")
    from PIL import Image

    Image.new("RGB", (8, 8), picture or (120, 140, 90)).save(root / "relief.jpg")
    return root


def test_the_same_site_on_another_grid_is_the_twin(tmp_path):
    demo = twin_build(tmp_path / "demo", 1500000.0, 5500000.0)
    abroad = twin_build(tmp_path / "abroad", 100000.0, 100000.0, title="Demo site abroad (made up)", crs="BNG",
                        vertical="ODN", level=42.02)          # 42.015 rounds either way
    assert made_up_sites.twin(demo, abroad) == []


def test_the_twin_check_finds_what_the_grid_doesnt_explain(tmp_path):
    demo = twin_build(tmp_path / "demo", 1500000.0, 5500000.0)
    # A surface cut along the other diagonal, where the ground isn't flat: 0.5 m off in the middle.
    off = made_up_sites.twin(demo, twin_build(tmp_path / "a", 100000.0, 100000.0, diagonal=1))
    assert any("surface is up to" in line for line in off)
    # The same flat ground cut either way is the same surface.
    flat = twin_build(tmp_path / "flat", 1500000.0, 5500000.0, top=0.0)
    assert made_up_sites.twin(flat, twin_build(tmp_path / "b", 100000.0, 100000.0, top=0.0, diagonal=1)) == []
    # A corner 5 mm higher, a level two rounding steps off, and a picture of something else.
    assert made_up_sites.twin(demo, twin_build(tmp_path / "c", 100000.0, 100000.0, top=1.005)) != []
    assert made_up_sites.twin(demo, twin_build(tmp_path / "d", 100000.0, 100000.0, level=42.03)) != []
    assert made_up_sites.twin(demo, twin_build(tmp_path / "e", 100000.0, 100000.0, picture=(200, 30, 30))) != []


def test_a_surface_triangulated_its_own_way_is_compared_as_a_surface(tmp_path):
    """The same flat square drawn as two triangles, and as four about a corner of its own in its middle, as a solid's
    top can be drawn from coordinates a hair apart: the same surface. With that middle corner 5 mm up, it isn't."""
    def fan(root, rise):
        model = json.loads((root / "model.json").read_text(encoding="utf-8"))
        model["layers"][0]["positions"] = [0, 0, 0, 1, 0, 0, 1, 0, -1, 0, 0, -1, 0.5, rise, -0.5]
        model["layers"][0]["indices"] = [0, 1, 4, 1, 2, 4, 2, 3, 4, 3, 0, 4]
        (root / "model.json").write_text(json.dumps(model), encoding="utf-8")
        return root

    flat = twin_build(tmp_path / "flat", 1500000.0, 5500000.0, top=0.0)
    assert made_up_sites.twin(flat, fan(twin_build(tmp_path / "a", 100000.0, 100000.0, top=0.0), 0.0)) == []
    off = made_up_sites.twin(flat, fan(twin_build(tmp_path / "b", 100000.0, 100000.0, top=0.0), 0.005))
    assert any("surface is up to" in line for line in off)


def test_the_report_holds_the_abroad_site_to_the_demo(tmp_path, capsys):
    for name in ("demo", "abroad"):
        (tmp_path / f"base-{name}.status").write_text("failed" if name == "abroad" else "ok", encoding="utf-8")
        (tmp_path / f"head-{name}.status").write_text("ok", encoding="utf-8")
    twin_build(tmp_path / "head-demo" / "viewer" / "data", 1500000.0, 5500000.0)
    twin_build(tmp_path / "head-abroad" / "viewer" / "data", 100000.0, 100000.0, top=1.2)
    twin_build(tmp_path / "base-demo" / "viewer" / "data", 1500000.0, 5500000.0)
    assert made_up_sites.report(tmp_path) == 1
    out = capsys.readouterr().out
    assert "| abroad | failed | ok | builds now |" in out and "doesn't build the demo's model" in out


def test_the_format_4_site_keeps_its_readings_where_format_4_did(sites):
    s, demo = settings(sites / "format4"), settings(sites / "demo")
    assert s["domus"]["format"] == 4
    assert [(m["id"], m["picks"]) for m in s["video"]["question_marks"]] == [
        ("sill", "walk-site.csv"), ("sill", "yard-site.csv"), ("w1-east", "walk-site.csv")]
    assert [c["id"] for c in s["video"]["compare"]] == ["walk-up", "walk-turned"]
    assert {k: v for k, v in s.items() if k not in ("domus", "video")} == {k: v for k, v in demo.items() if k != "domus"}
    picks = sites / "format4" / "data" / "video" / "picks"
    assert sorted(p.name for p in picks.iterdir()) == ["walk-site.csv", "yard-site.csv"]
    assert (sites / "format4" / "data" / "video" / "compare.npz").exists()
