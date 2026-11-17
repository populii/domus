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
    assert "| abroad | failed | failed | Expected until" in out and "| richer | not built | not built | not built |" in out
