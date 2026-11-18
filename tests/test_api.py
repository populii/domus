"""domus.api and domus.testing, the public API, on the made-up demo site."""

import json
import os
import shutil
import subprocess
import sys

import numpy as np
import pytest

from domus import api
from domus.errors import DomusError

# The promise: these stay, from one release to the next. Removing or renaming one needs a deprecation first.
PUBLIC = {"DomusError", "Site", "clip", "fit_affine", "linz_query", "open_site", "resample_line"}
SITE_METHODS = {"build", "check", "evaluate", "export", "frame", "grid", "ground", "house_walls", "layer", "lot",
                "options", "output", "path", "settings", "title"}


def test_the_promised_names_are_all_there():
    assert set(api.__all__) == PUBLIC
    assert all(hasattr(api, name) for name in PUBLIC)
    assert SITE_METHODS <= set(dir(api.Site)) | {"path"}


def test_a_site_reads_its_lot_house_layers_and_options(site):
    s = api.open_site()
    assert s.path == site.resolve() and api.open_site(site) is s
    assert s.title == "Demo site (made up)"
    assert s.lot().area == pytest.approx(660.0) and s.house_walls().area == pytest.approx(108.0)
    assert len(s.layer("trees")) == 4 and s.layer("no-such-layer") == []
    assert [o["id"] for o in s.options()] == ["a-one-wall", "b-two-tiers"]
    with pytest.raises(DomusError, match="layer's name"):
        s.layer("../site")


def test_its_settings_are_a_copy(site):
    s = api.open_site()
    s.settings()["site"]["title"] = "changed"
    assert s.title == "Demo site (made up)"


def test_its_grid_turns_points_and_heights(site):
    grid = api.open_site().grid
    assert grid.epsg == 2193 and grid.vertical == "NZVD2016" and grid.datums == ()
    e, n = grid.origin[:2]
    x, y = grid.to_nztm(e, n)
    assert grid.from_nztm(x, y) == pytest.approx((e, n))
    lon, lat = grid.to_lonlat(e, n)
    assert 165 < lon < 180 and -48 < lat < -34                  # off New Zealand's coast, where the demo is made up


def test_the_ground_comes_back_the_shape_it_was_asked(site):
    ground = api.open_site().ground()
    e, n = np.asarray(api.open_site().lot().exterior.coords).T
    assert np.isfinite(ground(e, n)).all() and ground(e, n).shape == e.shape
    grid = ground(e[:2, None] + np.zeros(3), n[:2, None] + np.zeros(3))
    assert grid.shape == (2, 3) and ground(e[0], n[0]).shape == ()


def test_an_option_is_evaluated_by_itself_or_its_id(site):
    s = api.open_site()
    a = s.evaluate("a-one-wall")
    assert a["volumes"]["fill_m3"] >= 0 and a["walls"]
    assert s.evaluate(s.options()[0])["volumes"] == a["volumes"]
    with pytest.raises(DomusError, match="no option"):
        s.evaluate("z-nothing")


def test_the_frame_goes_both_ways(site):
    fr = api.open_site().frame()
    so = np.array([[3.0, 2.0], [11.0, 7.5]])
    assert np.allclose(fr.so(fr.en(so)), so)


def test_the_checks_report_what_they_found(site):
    report = api.open_site().check(["sanity"])
    assert set(report["failures"]) == {"sanity"} and isinstance(report["findings"], list)
    assert json.loads((site / ".domus" / "checks" / "check_model.json").read_text(encoding="utf-8")) == report


def test_the_demos_roof_check_passes(site):
    """The demo's surface model has the roof Domus draws for its house, so the roof check, as `domus prep check roof`
    runs it, finds the house where it is."""
    report = api.open_site().check(["roof"])
    assert report["failures"] == {"roof": 0}
    assert any("best plan shift of the house E +0.00 N +0.00 m" in f["detail"] for f in report["findings"])


def test_a_folder_that_isnt_a_site_is_refused(site, tmp_path):
    with pytest.raises(DomusError, match="isn't a site project"):
        api.open_site(tmp_path)
    assert api.open_site(site) is api.open_site(site)


def _plain(model):
    return {k: v for k, v in model.items() if k not in ("built", "commit")}


def test_two_sites_in_one_process_are_read_apart(site, tmp_path):
    # A copy of the demo whose walls stand 0.2 m higher over the ground they retain: each site's option comes to its
    # own walls, whichever was read first, and the copy built in this process is the copy built in a process of its own.
    other = tmp_path / "other"
    shutil.copytree(site, other, ignore=shutil.ignore_patterns("viewer", ".domus"))
    shared = other / "options" / "_site.toml"
    shared.write_text(shared.read_text(encoding="utf-8").replace("upstand_m = 0.1", "upstand_m = 0.3"), encoding="utf-8")
    a, b = api.open_site(site), api.open_site(other)
    option = a.options()[0]["id"]
    top_a = a.evaluate(option)["walls"][0]["top_max"]
    top_b = b.evaluate(option)["walls"][0]["top_max"]
    assert top_b == pytest.approx(top_a + 0.2, abs=0.02)
    assert a.evaluate(option)["walls"][0]["top_max"] == top_a
    here = json.loads((b.build() / "model.json").read_text(encoding="utf-8"))
    r = run("from domus import build; from domus.project.site import started; build.main(started(), ['--fresh'])",
            DOMUS_SITE=str(other), DOMUS_OUTPUT=str(tmp_path / "apart"))
    assert r.returncode == 0, r.stderr
    apart = json.loads((tmp_path / "apart" / "model.json").read_text(encoding="utf-8"))
    assert _plain(here) == _plain(apart)


def run(code, **env):
    keep = {k: v for k, v in os.environ.items() if k not in ("DOMUS_SITE", "DOMUS_OUTPUT")}
    return subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, encoding="utf-8",
                          env={**keep, **env, "PYTHONUTF8": "1"}, check=False, timeout=300)


def test_a_site_opened_by_path_is_built_in_its_own_folder(site, tmp_path):
    r = run(f"from domus import api; s = api.open_site({str(site)!r}); print(s.path); print(s.output)",
            DOMUS_SITE=str(tmp_path), DOMUS_OUTPUT=str(tmp_path / "elsewhere"))
    assert r.returncode == 0, r.stderr
    assert r.stdout.split() == [str(site.resolve()), str((site / "viewer" / "data").resolve())]


def test_a_site_opens_whatever_domus_imported_first(site, tmp_path):
    r = run(f"from domus import crs, api, design; print(api.open_site({str(site)!r}).title)", DOMUS_SITE=str(tmp_path))
    assert r.returncode == 0 and r.stdout.strip() == "Demo site (made up)", r.stderr


def test_a_sites_own_tests_get_it_and_build_it_out_of_the_way(site, tmp_path):
    root = tmp_path / "place"
    shutil.copytree(site, root, ignore=shutil.ignore_patterns("viewer", "docs", ".domus"))
    (root / "tests").mkdir()
    (root / "tests" / "conftest.py").write_text(
        "from domus.testing import built, site, use_site  # noqa: F401\n\nuse_site(__file__)\n", encoding="utf-8")
    (root / "tests" / "test_it.py").write_text(
        "def test_it(site, built):\n"
        "    assert site.title == 'Demo site (made up)'\n"
        "    assert (built / 'model.json').exists() and built != site.path / 'viewer' / 'data'\n", encoding="utf-8")
    keep = {k: v for k, v in os.environ.items() if k not in ("DOMUS_SITE", "DOMUS_OUTPUT")}
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", str(root / "tests")], cwd=root,
                       capture_output=True, text=True, encoding="utf-8", env={**keep, "PYTHONUTF8": "1"}, check=False, timeout=600)
    assert r.returncode == 0 and "1 passed" in r.stdout, r.stdout + r.stderr
    assert not (root / "viewer").exists()           # the site's own model, which a server would show, is left alone


def test_the_helpers(monkeypatch):
    pts, along = api.resample_line([[0, 0, 5], [10, 0, 6]], 2.5)
    assert len(pts) == 5 and along[-1] == pytest.approx(10) and pts[:, 2][-1] == pytest.approx(6)
    a, resid = api.fit_affine([[0, 0], [10, 0], [0, 10]], [[100, 200], [110, 200], [100, 190]])
    assert np.allclose(a, [[1, 0, 100], [0, -1, 200]]) and np.allclose(resid, 0)
    from domus import publicdata

    asked = []
    monkeypatch.setattr(publicdata, "_wfs", lambda key, layer, cql, count=20: asked.append((key, layer, cql, count)) or [])
    api.linz_query(50820, bbox=(1, 2, 3, 4), key="k")
    api.linz_query("layer-50774", cql="name = 'X'", count=5, key="k")
    assert asked == [("k", "layer-50820", "(BBOX(shape,1,2,3,4,'EPSG:2193'))", 20), ("k", "layer-50774", "(name = 'X')", 5)]
    with pytest.raises(DomusError, match="bbox"):
        api.linz_query(50820, key="k")
    monkeypatch.delenv("DOMUS_LINZ_KEY", raising=False)
    with pytest.raises(DomusError, match="DOMUS_LINZ_KEY"):
        api.linz_query(50820, bbox=(1, 2, 3, 4))
